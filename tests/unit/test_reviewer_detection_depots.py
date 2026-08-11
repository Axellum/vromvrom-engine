"""
tests/unit/test_reviewer_detection_depots.py — Détection des dépôts (#T276).

Chaîne mesurée en production le 11/08, de la cause à l'effet :

1. Le Deck porte trois dossiers `_pre_codedeploy_*` (sauvegardes prises avant
   déploiement) à côté du vrai dépôt `moteur_agents`.
2. Le Reviewer les ramassait comme des espaces de travail, constatait qu'ils
   n'étaient pas informatifs (aucun remote, commit unique) et levait
   `GitDetectionError` — pour TOUS les dépôts, y compris le vrai.
3. Donc « auto-approbation à 10/10 bloquée, analyse forcée via LLM » à chaque
   revue : un appel LLM payé systématiquement.
4. Et sur une tâche qui ne modifie aucun code, le LLM n'avait rien à relire :
   verdict `critical`, score 0.0/10, « L'agent exécuteur n'a fourni aucun code
   […] à relire ».
5. La tâche de vérification échouait, et `_finalize_git` annulait un travail
   pourtant réussi (`Rollback effectué`).

La règle rétablie : un dépôt non informatif est IGNORÉ, pas condamnant. On ne
refuse de conclure que s'il ne reste aucun dépôt exploitable — ce qui préserve
l'intention de #T209.
"""

import subprocess

import pytest

from agents.reviewer import GitDetectionError, ReviewerAgent


def _depot(chemin, avec_remote: bool, fichier: str = "module.py", contenu: str = "x = 1\n"):
    """Crée un dépôt Git local. `avec_remote` le rend « informatif »."""
    chemin.mkdir(parents=True, exist_ok=True)
    def run(*args):
        return subprocess.run(args, cwd=str(chemin), capture_output=True, check=False)

    run("git", "init", "-q")
    run("git", "config", "user.email", "test@local")
    run("git", "config", "user.name", "Test")
    (chemin / fichier).write_text(contenu, encoding="utf-8")
    run("git", "add", ".")
    run("git", "commit", "-q", "-m", "commit initial")
    if avec_remote:
        # Aucun accès réseau : `git remote add` est purement local.
        run("git", "remote", "add", "origin", "https://example.invalid/depot.git")
    return chemin


@pytest.fixture
def reviewer():
    from core.llm_gateway import LLMGateway
    return ReviewerAgent(llm_gateway=LLMGateway(), provider_name="moyen")


# ── Reconnaissance des dossiers d'archive ────────────────────────────────────

@pytest.mark.parametrize("nom", [
    "_pre_codedeploy_1783842691",   # les trois vus sur le Deck
    "_pre_codedeploy_20260722_185833",
    "backups_prod",
    "moteur_agents_old",
])
def test_dossiers_d_archive_ecartes(nom):
    assert ReviewerAgent._est_dossier_workspace(nom) is False


@pytest.mark.parametrize("nom", ["moteur_agents", "00ProjetTab", "ServeurHA", "contexte_ia"])
def test_vrais_workspaces_conserves(nom):
    assert ReviewerAgent._est_dossier_workspace(nom) is True


# ── Détection des modifications ──────────────────────────────────────────────

def test_depot_parasite_ne_condamne_plus_le_vrai_depot(reviewer, tmp_path):
    """
    Le cas de production : un dépôt sain, une sauvegarde figée à côté.

    Avant #T276, la sauvegarde faisait lever GitDetectionError et bloquait
    l'auto-approbation. Désormais elle est ignorée.
    """
    _depot(tmp_path / "moteur_agents", avec_remote=True)
    _depot(tmp_path / "sauvegarde_figee", avec_remote=False)

    # Ne lève pas, et ne trouve aucune modification (les deux dépôts sont propres).
    assert reviewer._has_modified_files(racine=str(tmp_path)) is False


def test_modification_reelle_toujours_detectee(reviewer, tmp_path):
    """Contre-épreuve : le correctif n'aveugle pas la détection."""
    depot = _depot(tmp_path / "moteur_agents", avec_remote=True)
    (depot / "module.py").write_text("x = 2  # modifié\n", encoding="utf-8")

    assert reviewer._has_modified_files(racine=str(tmp_path)) is True


def test_uniquement_des_depots_parasites_refuse_de_conclure(reviewer, tmp_path):
    """
    Intention de #T209 préservée : sans dépôt fiable, on ne conclut pas.

    Le nom ne ressemble pas à une archive, donc le dossier est bien exploré —
    c'est son absence de remote et son commit unique qui le disqualifient.
    """
    _depot(tmp_path / "copie_deployee", avec_remote=False)

    with pytest.raises(GitDetectionError, match="Aucun dépôt Git informatif"):
        reviewer._has_modified_files(racine=str(tmp_path))


def test_aucun_depot_du_tout(reviewer, tmp_path):
    (tmp_path / "un_dossier_ordinaire").mkdir()

    with pytest.raises(GitDetectionError, match="Aucun dépôt Git détecté"):
        reviewer._has_modified_files(racine=str(tmp_path))


def test_dossier_archive_meme_avec_git_est_ignore(reviewer, tmp_path):
    """Une sauvegarde nommée `_pre_codedeploy_*` n'est même pas explorée."""
    _depot(tmp_path / "_pre_codedeploy_1783842691", avec_remote=True, contenu="y = 9\n")

    # Seule archive présente → aucun workspace retenu.
    with pytest.raises(GitDetectionError, match="Aucun dépôt Git détecté"):
        reviewer._has_modified_files(racine=str(tmp_path))


def test_fichier_non_code_ne_declenche_pas_de_revue(reviewer, tmp_path):
    """
    Le scénario exact de la vérification : un `.txt` écrit, rien à relire.

    C'est le cas qui doit rendre `False` pour que le court-circuit
    d'auto-approbation s'applique et qu'aucun LLM ne soit appelé.
    """
    depot = _depot(tmp_path / "moteur_agents", avec_remote=True)
    (depot / "preuve.txt").write_text("OK-T274\n", encoding="utf-8")

    assert reviewer._has_modified_files(racine=str(tmp_path)) is False
