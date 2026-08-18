"""
tests/unit/test_dreamcoder_push_branche.py — Sortie du travail DreamCoder.

Une tâche réussie produisait un commit qui ne quittait jamais le Deck. Le push
de la branche 'task/*' est désormais tenté à chaque succès, mais en best effort :
le Deck refuse le push de son dépôt de PROD (`no-push://…`, garde-fou du 07/08)
et sa clé de déploiement GitHub est en lecture seule. Ces tests figent le
contrat : on pousse quand on peut, on rend une raison lisible quand on ne peut
pas, et on ne casse JAMAIS une tâche réussie sur un problème de transport.
"""

import subprocess
from unittest.mock import patch

import pytest

from agents.dreamer_agent import _pousser_branche_tache


def _git(args, cwd):
    """Exécute un git de préparation de fixture (échoue bruyamment si KO)."""
    subprocess.run(["git"] + args, cwd=cwd, check=True,
                   capture_output=True, text=True)


class _AiguillagePush:
    """
    Intercepte UNIQUEMENT `git push`, laisse passer le reste.

    `agents.dreamer_agent.subprocess` EST le module subprocess : patcher son
    `run` patcherait aussi celui de `tools.git_safety`, donc la lecture du
    remote par `_run_git` — et le test ne vérifierait plus le vrai chemin.
    """

    def __init__(self, reponse=None, exception=None):
        self._reponse, self._exception = reponse, exception
        self._vrai_run = subprocess.run
        self.appels_push = []

    def __call__(self, args, *a, **kw):
        if isinstance(args, list) and "push" in args:
            self.appels_push.append({"args": args, "kwargs": kw})
            if self._exception is not None:
                raise self._exception
            return self._reponse
        return self._vrai_run(args, *a, **kw)


@pytest.fixture
def depot_avec_origin(tmp_path):
    """
    Un vrai clone relié à un vrai `origin` (dépôt bare local), avec une branche
    de tâche portant un commit. Permet de vérifier le push pour de bon, sans
    réseau ni mock du transport.
    """
    bare = tmp_path / "origin.git"
    _git(["init", "--bare", "-b", "master", str(bare)], cwd=str(tmp_path))

    clone = tmp_path / "clone"
    _git(["clone", str(bare), str(clone)], cwd=str(tmp_path))
    _git(["config", "user.email", "test@local"], cwd=str(clone))
    _git(["config", "user.name", "Test"], cwd=str(clone))

    (clone / "fichier.txt").write_text("base", encoding="utf-8")
    _git(["add", "-A"], cwd=str(clone))
    _git(["commit", "-m", "base"], cwd=str(clone))
    _git(["push", "origin", "master"], cwd=str(clone))

    _git(["checkout", "-b", "task/42_1787000000"], cwd=str(clone))
    (clone / "fichier.txt").write_text("travail de l'agent", encoding="utf-8")
    _git(["add", "-A"], cwd=str(clone))
    _git(["commit", "-m", "feat: travail de la tache 42"], cwd=str(clone))

    return {"clone": str(clone), "bare": str(bare), "branche": "task/42_1787000000"}


# ── Le chemin qui marche : la branche sort vraiment ──────────────────────────

def test_push_reel_publie_la_branche_sur_origin(depot_avec_origin):
    """Un push qui aboutit crée bien la ref côté origin — vérifié, pas supposé."""
    res = _pousser_branche_tache(depot_avec_origin["clone"], depot_avec_origin["branche"])

    assert res["pushed"] is True
    assert res["raison"] == "ok"

    refs = subprocess.run(["git", "branch", "--list"], cwd=depot_avec_origin["bare"],
                          capture_output=True, text=True).stdout
    assert depot_avec_origin["branche"] in refs


def test_push_pose_le_suivi_amont(depot_avec_origin):
    """--set-upstream : la branche récoltée sait d'où elle vient."""
    _pousser_branche_tache(depot_avec_origin["clone"], depot_avec_origin["branche"])

    amont = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=depot_avec_origin["clone"], capture_output=True, text=True).stdout.strip()
    assert amont == f"origin/{depot_avec_origin['branche']}"


# ── Les refus : une raison lisible, jamais une exception ─────────────────────

def test_remote_no_push_du_deck_est_reconnu_sans_tenter(depot_avec_origin):
    """
    Le dépôt de prod du Deck porte `no-push://le-deck-est-une-cible-de-deploiement`.
    On veut une raison nommée ET aucune tentative réseau derrière.
    """
    _git(["remote", "set-url", "--push", "origin",
          "no-push://le-deck-est-une-cible-de-deploiement"], cwd=depot_avec_origin["clone"])

    aiguillage = _AiguillagePush(reponse=subprocess.CompletedProcess([], 0, "", ""))
    with patch("agents.dreamer_agent.subprocess.run", aiguillage):
        res = _pousser_branche_tache(depot_avec_origin["clone"], depot_avec_origin["branche"])

    assert res["pushed"] is False
    assert res["raison"] == "remote_no_push"
    assert aiguillage.appels_push == []  # aucune tentative réseau derrière


def test_remote_absent(tmp_path):
    """Un dépôt sans origin ne doit pas partir en exception."""
    depot = tmp_path / "sans_origin"
    depot.mkdir()
    _git(["init", "-b", "master", "."], cwd=str(depot))

    res = _pousser_branche_tache(str(depot), "task/1_1787000000")
    assert res["pushed"] is False
    assert res["raison"] == "remote_absent"


def test_push_refuse_rend_la_raison_du_serveur(depot_avec_origin):
    """
    Cas réel attendu sur le Deck : clé de déploiement en lecture seule.
    Le message du serveur doit remonter (tronqué) pour être diagnosticable.
    """
    echec = subprocess.CompletedProcess(
        args=[], returncode=128, stdout="",
        stderr="ERROR: The key you are authenticating with has not been granted write access.")

    with patch("agents.dreamer_agent.subprocess.run", _AiguillagePush(reponse=echec)):
        res = _pousser_branche_tache(depot_avec_origin["clone"], depot_avec_origin["branche"])

    assert res["pushed"] is False
    assert res["raison"] == "refuse"
    assert "write access" in res["detail"]


def test_timeout_ne_mange_pas_la_fenetre_du_cycle(depot_avec_origin):
    """
    Le push vit dans le budget `dreamcoder_max_cycle_minutes`, partagé par
    toutes les tâches : un push qui pend doit être coupé, pas attendu.
    """
    expiration = subprocess.TimeoutExpired(cmd="git push", timeout=90.0)
    with patch("agents.dreamer_agent.subprocess.run", _AiguillagePush(exception=expiration)):
        res = _pousser_branche_tache(depot_avec_origin["clone"], depot_avec_origin["branche"])

    assert res["pushed"] is False
    assert res["raison"] == "timeout"


def test_erreur_inattendue_est_absorbee(depot_avec_origin):
    """Aucune exception ne remonte : une tâche réussie le reste."""
    with patch("agents.dreamer_agent.subprocess.run",
               _AiguillagePush(exception=OSError("git introuvable"))):
        res = _pousser_branche_tache(depot_avec_origin["clone"], depot_avec_origin["branche"])

    assert res["pushed"] is False
    assert res["raison"] == "erreur"


# ── Le garde-fou qui évite le service figé ───────────────────────────────────

def test_git_terminal_prompt_desactive(depot_avec_origin):
    """
    Le clone dédié du Deck a un `origin` HTTPS sans identifiants. Sans
    GIT_TERMINAL_PROMPT=0, git attend une saisie qui ne viendra jamais dans un
    service systemd — et le cycle entier reste bloqué là.
    """
    ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    aiguillage = _AiguillagePush(reponse=ok)

    with patch("agents.dreamer_agent.subprocess.run", aiguillage):
        _pousser_branche_tache(depot_avec_origin["clone"], depot_avec_origin["branche"])

    assert len(aiguillage.appels_push) == 1
    appel = aiguillage.appels_push[0]
    assert appel["kwargs"]["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert appel["kwargs"]["timeout"] == 90.0
    assert appel["args"] == [
        "git", "push", "--set-upstream", "origin", depot_avec_origin["branche"]]
