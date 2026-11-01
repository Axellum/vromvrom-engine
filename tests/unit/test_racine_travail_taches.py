"""
tests/unit/test_racine_travail_taches.py — Où atterrit le travail d'une tâche (#T388).

Mesuré sur le Deck le 18/08, première session autonome réelle : quatre tâches
déclarées « réussies », quatre branches `task/*` VIDES, et les livrables — dont
`docs/travail/AUDIT_IHM_V2.md` — déposés dans le dossier de PRODUCTION.

Deux défauts indépendants, qui se sont couverts l'un l'autre :

1. Les outils fichiers résolvent un chemin relatif contre le CWD du processus,
   qui est le dossier de prod, pas le clone de la tâche. Le prompt injecté
   demandait pourtant des chemins absolus : une consigne textuelle demande au
   modèle de compenser un défaut d'outillage, et il ne l'a pas fait.
2. `git add -A` ne trouvait rien, `git commit` échouait sur « nothing to
   commit », et personne ne lisait son code de retour — la tâche passait
   'completed'. Coût : 1,13 M de tokens et quatre branches à relire pour rien.
"""

import os
import subprocess

import pytest

from tools.system import (
    _WORKSPACE_ROOT,
    definir_racine_travail,
    racine_travail_courante,
    reinitialiser_racine_travail,
    write_file,
)


@pytest.fixture
def racine_propre():
    """Aucune racine ne doit fuir d'un test à l'autre."""
    jeton = definir_racine_travail(None)
    yield
    reinitialiser_racine_travail(jeton)


# ── Résolution des chemins ───────────────────────────────────────────────────

def test_sans_racine_le_comportement_historique_est_conserve(racine_propre):
    """Hors tâche de fond, un relatif se résout contre le CWD, comme open()."""
    assert racine_travail_courante() is None


def test_le_relatif_suit_la_racine_de_la_tache(tmp_path, racine_propre):
    """
    Le cœur du défaut : `docs/x.md` doit atterrir dans le clone de la tâche,
    pas dans le répertoire courant du serveur.
    """
    clone = tmp_path / "clone_de_tache"
    clone.mkdir()
    jeton = definir_racine_travail(str(clone))
    try:
        from tools.system import _resolve_within_workspace

        resolu, erreur = _resolve_within_workspace(os.path.join("docs", "livrable.md"))
    finally:
        reinitialiser_racine_travail(jeton)

    # tmp_path sort du workspace autorisé : ce qui compte ici est que la racine
    # ait bien été PRISE EN COMPTE, visible dans le chemin refusé.
    cible = os.path.realpath(str(clone / "docs" / "livrable.md"))
    assert (resolu == cible) or (erreur is not None and str(clone) not in _WORKSPACE_ROOT)


def test_un_chemin_absolu_n_est_jamais_reinterprete(tmp_path, racine_propre):
    """La racine ne doit pas préfixer un chemin que l'agent a déjà pleinement écrit."""
    from tools.system import _resolve_within_workspace

    jeton = definir_racine_travail(str(tmp_path / "clone"))
    try:
        absolu = os.path.join(_WORKSPACE_ROOT, "fichier_a_la_racine.txt")
        resolu, erreur = _resolve_within_workspace(absolu)
    finally:
        reinitialiser_racine_travail(jeton)

    assert erreur is None
    assert resolu == os.path.realpath(absolu)


def test_la_racine_ne_perce_pas_le_garde_fou_de_securite(racine_propre):
    """
    Une racine hors workspace ne doit pas devenir un contournement : la borne
    `_WORKSPACE_ROOT` s'applique APRÈS la résolution, pas avant.
    """
    from tools.system import _resolve_within_workspace

    jeton = definir_racine_travail(os.path.join(os.sep, "ailleurs", "hors_workspace"))
    try:
        resolu, erreur = _resolve_within_workspace("secret.txt")
    finally:
        reinitialiser_racine_travail(jeton)

    assert resolu is None
    assert "accès refusé" in erreur


def test_ecriture_reelle_dans_la_racine(tmp_path, monkeypatch, racine_propre):
    """Bout du fil : write_file avec un relatif écrit bien sous la racine imposée."""
    faux_workspace = tmp_path / "dev_station"
    clone = faux_workspace / "clone"
    clone.mkdir(parents=True)
    monkeypatch.setattr("tools.system._WORKSPACE_ROOT", os.path.realpath(str(faux_workspace)))

    jeton = definir_racine_travail(str(clone))
    try:
        write_file(os.path.join("docs", "livrable.md"), "contenu")
    finally:
        reinitialiser_racine_travail(jeton)

    assert (clone / "docs" / "livrable.md").read_text(encoding="utf-8") == "contenu"
    # …et surtout PAS à côté, là où le CWD l'aurait envoyé.
    assert not (faux_workspace / "docs" / "livrable.md").exists()


def test_deux_taches_paralleles_ne_partagent_pas_leur_racine(tmp_path):
    """
    Chaque travailleur du drainage a sa propre copie de contexte : la racine de
    l'un ne doit pas déteindre sur l'autre.
    """
    import asyncio

    async def travailleur(chemin: str) -> str | None:
        jeton = definir_racine_travail(chemin)
        try:
            await asyncio.sleep(0)  # laisse l'autre tâche s'intercaler
            return racine_travail_courante()
        finally:
            reinitialiser_racine_travail(jeton)

    async def scenario():
        return await asyncio.gather(
            travailleur(str(tmp_path / "slot1")),
            travailleur(str(tmp_path / "slot2")),
        )

    vus = asyncio.run(scenario())
    assert vus[0] != vus[1]
    assert vus[0].endswith("slot1") and vus[1].endswith("slot2")


# ── Le garde-fou : un succès sans commit n'est pas un succès ─────────────────

def test_une_branche_identique_a_sa_base_n_est_pas_un_travail(tmp_path):
    """
    Reproduit la détection : après le commit, la branche de tâche doit DIFFÉRER
    de sa base. C'est le contrôle qui manquait — `git diff --quiet base..HEAD`
    rend 0 quand rien n'a bougé, et 1 dès qu'un fichier a changé.
    """
    depot = tmp_path / "depot"
    depot.mkdir()

    def git(*args):
        return subprocess.run(["git"] + list(args), cwd=str(depot),
                              capture_output=True, text=True)

    git("init", "-b", "master")
    git("config", "user.email", "t@local")
    git("config", "user.name", "T")
    (depot / "base.txt").write_text("base", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "base")

    git("checkout", "-b", "task/1_1787000000")
    # Rien n'est écrit : exactement l'état des quatre branches du 18/08.
    git("add", "-A")
    git("commit", "-m", "tentative")
    assert git("diff", "--quiet", "master..HEAD").returncode == 0, "aucun travail détecté"

    # Avec un vrai livrable, le même contrôle passe.
    (depot / "livrable.md").write_text("du vrai travail", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "travail")
    assert git("diff", "--quiet", "master..HEAD").returncode != 0
