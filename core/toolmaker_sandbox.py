"""
core/toolmaker_sandbox.py — Validation distante du code candidat ToolMaker (#T207).

Remplace l'exécution locale `subprocess.run` (RCE : le code généré par le LLM
tournait avec les droits du process moteur sur le Deck) par un runner GitHub
Actions jetable :

1. Création d'une branche éphémère `toolmaker/validate-<id>` via l'API Git Data
   (blob → tree → commit → ref : un seul événement push, donc un seul run) ;
2. Le workflow `.github/workflows/toolmaker_validate.yml` exécute le candidat
   dans une VM GitHub isolée (aucun accès réseau au LAN Deck/HA, GITHUB_TOKEN
   du job sans aucune permission) et échoue si `SANDBOX_OK` n'apparaît pas ;
3. Le verdict (conclusion du run) est interrogé par l'API REST Actions en
   polling asyncio ;
4. La branche éphémère est supprimée dans tous les cas (finally).

Choix SYNCHRONE (le flux attend le verdict, ~30 s à 2 min) plutôt qu'asynchrone
avec notification après coup : le ToolMaker n'est pas sur un chemin interactif
(déclenché par le SkillStore en tâche de fond), son `invoke()` attendait déjà
une boucle ReAct multi-appels LLM du même ordre de durée, et un mode asynchrone
exigerait de persister un état « en attente de validation » plus un mécanisme
de notification/reprise — de la machinerie sans bénéfice ici. Le polling est
fait avec `await asyncio.sleep`, il ne bloque jamais l'event loop.

Sécurité (fail-closed) : sans PAT dédié (`MOTEUR_TOOLMAKER_PAT`), la validation
ÉCHOUE — il n'y a aucun repli vers une exécution locale. Le PAT doit être un
fine-grained token limité à CE dépôt (Contents: read/write, Actions: read),
distinct de `GITHUB_TOKEN` (clé LLM GitHub Models dans ce projet) et de tout
PAT existant plus large (cf. audit config Antigravity).
"""

import asyncio
import logging
import os
import time
import uuid

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

# Chemin du candidat dans la branche éphémère — doit correspondre au workflow.
CANDIDATE_PATH = ".toolmaker_sandbox/candidate_full.py"


def _get_pat() -> str | None:
    """PAT dédié à la sandbox distante (fine-grained, scope minimal, ce dépôt)."""
    return os.environ.get("MOTEUR_TOOLMAKER_PAT") or None


def _get_repo() -> str:
    """Dépôt cible au format owner/repo."""
    return os.environ.get("MOTEUR_TOOLMAKER_REPO", "Axellum/moteur_agents")


async def _gh(client, method: str, path: str, **kwargs):
    """Appel REST GitHub : lève httpx.HTTPStatusError si statut >= 400."""
    response = await client.request(method, f"{GITHUB_API}{path}", **kwargs)
    response.raise_for_status()
    return response


async def validate_candidate_remote(
    full_code: str,
    timeout_s: float | None = None,
    poll_interval_s: float = 6.0,
    _client=None,
) -> dict:
    """
    Pousse le code candidat sur une branche éphémère, attend le verdict du
    workflow `ToolMaker Validate`, puis supprime la branche.

    Args:
        full_code: Code candidat complet (outil + bloc de test SANDBOX_OK).
        timeout_s: Délai global d'attente du verdict (défaut : env
                   MOTEUR_TOOLMAKER_TIMEOUT_S ou 300 s).
        poll_interval_s: Intervalle de polling de l'API Actions.
        _client: Client httpx injectable (tests).

    Returns:
        {"passed": bool, "error": str | None, "run_url": str | None}
    """
    pat = _get_pat()
    if not pat:
        # Fail-closed : jamais de repli vers une exécution locale.
        return {
            "passed": False,
            "error": (
                "MOTEUR_TOOLMAKER_PAT absent du .env — validation distante "
                "impossible, outil rejeté (aucun repli local par sécurité). "
                "Créer un fine-grained PAT limité à ce dépôt "
                "(Contents: read/write, Actions: read)."
            ),
            "run_url": None,
        }

    if timeout_s is None:
        try:
            timeout_s = float(os.environ.get("MOTEUR_TOOLMAKER_TIMEOUT_S", "300"))
        except ValueError:
            timeout_s = 300.0

    repo = _get_repo()
    branch = f"toolmaker/validate-{uuid.uuid4().hex[:12]}"

    import httpx

    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    owns_client = _client is None
    client = _client or httpx.AsyncClient(headers=headers, timeout=30.0)
    branch_created = False
    try:
        # 1. Branche de base (défaut du dépôt) et son commit/tree.
        repo_info = (await _gh(client, "GET", f"/repos/{repo}")).json()
        default_branch = repo_info.get("default_branch", "master")
        base_ref = (
            await _gh(client, "GET", f"/repos/{repo}/git/ref/heads/{default_branch}")
        ).json()
        base_sha = base_ref["object"]["sha"]
        base_commit = (
            await _gh(client, "GET", f"/repos/{repo}/git/commits/{base_sha}")
        ).json()
        base_tree_sha = base_commit["tree"]["sha"]

        # 2. blob → tree → commit → ref : un seul push, donc un seul run.
        blob = (
            await _gh(
                client, "POST", f"/repos/{repo}/git/blobs",
                json={"content": full_code, "encoding": "utf-8"},
            )
        ).json()
        tree = (
            await _gh(
                client, "POST", f"/repos/{repo}/git/trees",
                json={
                    "base_tree": base_tree_sha,
                    "tree": [{
                        "path": CANDIDATE_PATH,
                        "mode": "100644",
                        "type": "blob",
                        "sha": blob["sha"],
                    }],
                },
            )
        ).json()
        commit = (
            await _gh(
                client, "POST", f"/repos/{repo}/git/commits",
                json={
                    "message": f"toolmaker: candidat a valider ({branch})",
                    "tree": tree["sha"],
                    "parents": [base_sha],
                },
            )
        ).json()
        await _gh(
            client, "POST", f"/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
        )
        branch_created = True
        logger.info(f"[TOOLMAKER-SANDBOX] Branche éphémère poussée : {branch}")

        # 3. Polling du run déclenché par le push (seul workflow sur ces branches).
        deadline = time.monotonic() + timeout_s
        run_url = None
        while time.monotonic() < deadline:
            runs = (
                await _gh(
                    client, "GET",
                    f"/repos/{repo}/actions/runs",
                    params={"branch": branch, "event": "push", "per_page": 5},
                )
            ).json().get("workflow_runs", [])
            if runs:
                run = runs[0]
                run_url = run.get("html_url")
                if run.get("status") == "completed":
                    conclusion = run.get("conclusion")
                    if conclusion == "success":
                        logger.info(
                            f"[TOOLMAKER-SANDBOX] ✅ Verdict runner : SANDBOX_OK ({run_url})"
                        )
                        return {"passed": True, "error": None, "run_url": run_url}
                    return {
                        "passed": False,
                        "error": f"Runner GitHub Actions : conclusion '{conclusion}'",
                        "run_url": run_url,
                    }
            await asyncio.sleep(poll_interval_s)

        return {
            "passed": False,
            "error": f"Timeout {timeout_s:.0f}s : verdict du runner non obtenu",
            "run_url": run_url,
        }

    except Exception as e:
        # Toute erreur API = rejet (fail-closed), jamais d'exécution locale.
        logger.warning(f"[TOOLMAKER-SANDBOX] Erreur API GitHub : {e}")
        return {"passed": False, "error": f"Erreur API GitHub : {e}", "run_url": None}
    finally:
        if branch_created:
            try:
                await _gh(client, "DELETE", f"/repos/{repo}/git/refs/heads/{branch}")
                logger.info(f"[TOOLMAKER-SANDBOX] Branche éphémère supprimée : {branch}")
            except Exception as e:
                logger.warning(
                    f"[TOOLMAKER-SANDBOX] Échec de suppression de {branch} : {e} "
                    "(à nettoyer manuellement)"
                )
        if owns_client:
            await client.aclose()
