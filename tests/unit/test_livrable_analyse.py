"""
[#T424] Une tâche d'analyse doit produire un fichier, sinon elle ne produit rien.

Le harnais exige une branche non vide — c'est ce qui empêche le moteur de
déclarer « terminé » sur du vent. Les entrées de board « **À ANALYSER** »
étaient donc condamnées : l'agent raisonnait, répondait en texte, et le cycle
constatait une branche vide. Mesuré le 21/08 sur les tâches 49, 50 et 51.

La réponse n'est pas d'ouvrir la porte du harnais, mais de donner un livrable à
l'analyse.
"""

import os

import pytest

from agents.dreamer_agent import (
    _est_une_tache_d_analyse,
    _nom_de_rapport,
    consigne_de_livrable,
)

DEPOT = os.path.join("H:", os.sep, "AuxFilsDesIdees", "moteur_agents_dc")


@pytest.mark.parametrize(
    "texte",
    [
        "[T421] (tab5/firmware) **À ANALYSER** — Montée ESPHome 2026.8.0",
        "[T428] (dev/tooling) **A ANALYSER** — Boucle de flash accélérée",  # sans accent
        "Étude de faisabilité du portage LVGL",
        "Audit de la cascade de providers",
    ],
)
def test_une_tache_d_analyse_est_reconnue(texte):
    assert _est_une_tache_d_analyse(texte)


@pytest.mark.parametrize(
    "texte",
    [
        "Corriger l'indentation PEP 8 dans core/antigravity_helper.py",
        "TODO dans tests/unit/test_task_discovery_agent.py — normaliser les clés",
        "Ajouter un test de non-régression sur le circuit breaker",
    ],
)
def test_une_tache_de_code_n_en_est_pas_une(texte):
    """Le faux positif coûterait cher : un correctif rendu en rapport Markdown."""
    assert not _est_une_tache_d_analyse(texte)


def test_aucune_consigne_pour_une_tache_de_code():
    tache = {"id": 42, "title": "Corriger l'indentation PEP 8", "description": "core/x.py"}
    assert consigne_de_livrable(tache, DEPOT) == ""


def test_la_consigne_nomme_un_chemin_absolu_dans_le_depot_de_travail():
    tache = {
        "id": 49,
        "title": "[T421] (tab5/firmware) **À ANALYSER** — Montée ESPHome 2026.8.0",
        "description": "Vérifier la version ESPHome utilisée.",
    }
    consigne = consigne_de_livrable(tache, DEPOT)

    assert "LIVRABLE ATTENDU" in consigne
    assert "write_file" in consigne
    # Le chemin doit être absolu SOUS le dépôt de travail : un chemin relatif
    # se résoudrait contre le CWD du serveur, pas contre le clone (#T388).
    assert DEPOT in consigne
    assert "docs" in consigne and "analyses" in consigne
    assert "tache_49_" in consigne


def test_le_nom_du_rapport_est_stable_et_sans_accents():
    nom = _nom_de_rapport(49, "[T421] (tab5/firmware) **À ANALYSER** — Montée ESPHome")
    assert nom == _nom_de_rapport(49, "[T421] (tab5/firmware) **À ANALYSER** — Montée ESPHome")
    assert nom.startswith("docs/analyses/tache_49_")
    assert nom.endswith(".md")
    assert all(ord(c) < 128 for c in nom), "un chemin accentué complique tout en aval"


def test_un_titre_vide_donne_quand_meme_un_nom_utilisable():
    assert _nom_de_rapport(7, "") == "docs/analyses/tache_7_analyse.md"


def test_la_consigne_interdit_d_inventer():
    """Le risque d'une tâche d'analyse, c'est le rapport plausible et faux."""
    tache = {"id": 50, "title": "**À ANALYSER** — Wake word runtime", "description": ""}
    consigne = consigne_de_livrable(tache, DEPOT)
    assert "N'invente aucun chiffre" in consigne
    assert "PAS pu vérifier" in consigne


def test_analyse_sans_rapport_dans_docs_analyses_est_refusee():
    """[#T436] `resultat_recherche.txt` à la racine ne valide plus une analyse."""
    from agents.dreamer_agent import _livrable_insuffisant

    titre = "[T421] (tab5/firmware) **À ANALYSER** — Montée ESPHome"
    assert _livrable_insuffisant(titre, ["resultat_recherche.txt"])
    assert _livrable_insuffisant(titre, ["search_discovery_key.py"])
    assert not _livrable_insuffisant(
        titre, ["docs/analyses/tache_63_esphome.md"]
    )


def test_code_avec_seulement_du_jetable_est_refuse():
    from agents.dreamer_agent import _livrable_insuffisant

    titre = "Persister tokens_used sur les échecs DreamCoder"
    assert _livrable_insuffisant(titre, ["search_backlog_tasks.py"])
    assert not _livrable_insuffisant(
        titre, ["core/backlog_db.py", "tests/unit/test_backlog_db.py"]
    )


def test_un_py_a_la_racine_est_jetable():
    from agents.dreamer_agent import _livrable_insuffisant

    titre = "Purger seulement les branches task/* vides"
    assert _livrable_insuffisant(titre, ["extract_git_files.py", "get_git_file.py"])


def test_titre_qui_cite_un_fichier_absent_du_diff():
    from agents.dreamer_agent import _titre_incoherent_avec_diff

    assert _titre_incoherent_avec_diff(
        "Corriger agents/task_discovery_agent.py",
        ["extract_git_files.py"],
    )
    assert not _titre_incoherent_avec_diff(
        "Corriger agents/task_discovery_agent.py",
        ["agents/task_discovery_agent.py"],
    )


def test_agent_qui_dit_rien_a_faire():
    from agents.dreamer_agent import _agent_affirme_rien_a_faire

    assert _agent_affirme_rien_a_faire("Aucune modification nécessaire, critère atteint.")
    assert not _agent_affirme_rien_a_faire("J'ai modifié core/dag_runner.py.")


def test_tache_board_deja_cochee_est_detectee():
    from agents.dreamer_agent import _id_tache_board_deja_livree

    board = "| ✅ **#T414 / #T416 déjà dans git** | Filtre worktrees.\n"
    assert _id_tache_board_deja_livree("[MOTEUR] #T414 — filtrer", board) == "T414"
    assert _id_tache_board_deja_livree("[MOTEUR] #T417 — purger", board) is None


def test_livrable_insuffisant_conserve_la_branche():
    from agents.dreamer_agent import _faut_conserver_la_branche

    assert _faut_conserver_la_branche(
        "Le pipeline s'est déclaré terminé mais le livrable est insuffisant : "
        "tâche d'analyse sans rapport dans docs/analyses/*.md"
    )
    assert _faut_conserver_la_branche(
        "le titre de la tâche cite agents/foo.py mais le diff ne le contient pas"
    )
    assert not _faut_conserver_la_branche(
        "Le pipeline s'est déclaré terminé mais la branche task/50 est identique à master"
    )
    assert not _faut_conserver_la_branche(
        "AUCUN agent d'exécution n'a tourné (agents : aucun)"
    )
    assert not _faut_conserver_la_branche("429 Too Many Requests")


def test_consigne_de_correction_vide_sans_retry():
    from agents.dreamer_agent import _consigne_de_correction

    assert _consigne_de_correction({"retries": 0, "error_message": "livrable insuffisant"}) == ""
    assert _consigne_de_correction({"retries": 1, "error_message": ""}) == ""


def test_consigne_de_correction_restitue_le_refus():
    from agents.dreamer_agent import _consigne_de_correction

    texte = _consigne_de_correction({
        "retries": 1,
        "error_message": "livrable est insuffisant : docs/analyses/*.md",
    })
    assert "ne recommence PAS" in texte
    assert "docs/analyses/*.md" in texte


def test_reprendre_branche_critique_checkout_local(tmp_path):
    import subprocess

    from agents.dreamer_agent import _reprendre_branche_critique

    repo = tmp_path / "clone"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "master"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "task/64_1"], cwd=repo, check=True, capture_output=True)
    (repo / "docs").mkdir()
    (repo / "docs" / "note.md").write_text("brouillon", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "brouillon"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "master"], cwd=repo, check=True, capture_output=True)

    assert _reprendre_branche_critique(str(repo), "task/64_1")
    head = subprocess.check_output(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, text=True
    ).strip()
    assert head == "task/64_1"
    assert (repo / "docs" / "note.md").read_text(encoding="utf-8") == "brouillon"

