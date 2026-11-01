"""
tests/unit/test_zone_est_une_etiquette.py — une zone de board n'est pas un chemin (#T418).

Mesuré le 19/08 à 19h11, cycle autonome sur la PRODUCTION : trois tâches
d'affilée, **1,7 M de tokens à elles trois**, ont lu l'étiquette de zone de leur
titre comme un chemin de fichier.

    T415  « Zone : runtime/llm »            -> livre le fichier `runtime/llm`,
                                               une ligne : la sortie d'un `find`
    T414  « Zone : dreamcoder/decouverte »  -> crée core/dreamcoder/decouverte/
                                               task_discovery_agent.py, doublon
                                               écrit de zéro du vrai agent, et
                                               son test posé dans core/
    T416  « Zone : dreamcoder/metrics »     -> même motif

Les trois ont été déclarées `completed` : elles écrivaient bien des fichiers,
donc la porte « la branche diffère de master » (#T388) était satisfaite. Le
board affichait #T414/#T415/#T416 comme faites alors que le vrai code n'avait
pas été touché.

Deux bouts à tenir, et ces tests figent les deux :
  - la CAUSE — le contrat de délégation doit dire qu'une zone est une étiquette ;
  - l'EFFET — une porte de sortie doit refuser les livrables mal placés, parce
    qu'une consigne seule ne suffit pas (on l'a mesuré : le contrat exigeait
    déjà « exécute pytest », et trois tâches ont livré des tests rouges).
"""

import pytest

import tools.git_safety as git_safety
from agents.dreamer_agent import _livrables_hors_convention
from agents.task_discovery_agent import TaskDiscoveryAgent

_SAUT = chr(10)


# ── La cause : le contrat de délégation ─────────────────────────────────────

@pytest.fixture
def contrat():
    """Prompt produit pour une tâche de board dont la zone ressemble à un chemin."""
    agent = TaskDiscoveryAgent()
    tache = {
        "id": "T415",
        "source": "board_md",
        "zone": "runtime/llm",
        "prio": "P1",
        "title": "[T415] (runtime/llm) Câblage AJEAN",
        "description": "Câblage et validation de complétion locale AJEAN PC.",
        "complexity": "high",
        "dedup_key": "board:T415",
    }
    return agent.prepare_task_payload(tache)["prompt"]


def test_le_contrat_dit_que_la_zone_est_une_etiquette(contrat):
    """Le mot doit être dans le prompt, pas seulement dans nos intentions."""
    assert "ÉTIQUETTE" in contrat
    assert "PAS un chemin de fichier" in contrat


def test_le_contrat_interdit_de_creer_un_dossier_du_nom_de_la_zone(contrat):
    assert "Ne crée NI dossier NI fichier portant ce nom" in contrat


def test_le_contrat_impose_de_localiser_l_existant_avant_d_ecrire(contrat):
    """C'est la contre-mesure au « j'invente un module neuf »."""
    assert "LOCALISER" in contrat
    assert "n'invente pas un module neuf" in contrat


def test_le_contrat_porte_les_invariants_d_arborescence(contrat):
    assert "tests/unit/" in contrat
    assert "porte une extension" in contrat


def test_le_titre_n_accole_plus_la_zone_par_un_tiret():
    """
    « [T415] runtime/llm - Câblage… » se lit comme un chemin suivi d'une
    description. Les parenthèses en font une étiquette.
    """
    agent = TaskDiscoveryAgent()
    taches = agent.scan_board_tasks()
    for t in taches:
        assert f"] {t['zone']} - " not in t["title"], (
            f"titre encore ambigu : {t['title']}")


# ── L'effet : la porte de sortie ────────────────────────────────────────────

def _diff(monkeypatch, *chemins):
    monkeypatch.setattr(
        git_safety, "_run_git", lambda *a, **k: (0, _SAUT.join(chemins), ""))


def test_un_fichier_sans_extension_est_refuse(monkeypatch):
    """Le cas T415, à la lettre."""
    _diff(monkeypatch, "runtime/llm")

    motifs = _livrables_hors_convention("/depot", "master")

    assert len(motifs) == 1
    assert "runtime/llm" in motifs[0]
    assert "extension" in motifs[0]


def test_un_test_dans_un_paquet_de_production_est_refuse(monkeypatch):
    """Le cas T414 : le test livré dans core/ au lieu de tests/."""
    _diff(monkeypatch, "core/dreamcoder/decouverte/test_task_discovery_agent.py")

    motifs = _livrables_hors_convention("/depot", "master")

    assert len(motifs) == 1
    assert "tests/" in motifs[0]


def test_une_livraison_conforme_passe(monkeypatch):
    """Aucun faux positif sur une branche normale."""
    _diff(
        monkeypatch,
        "core/app_state.py",
        "tests/unit/test_app_state.py",
        "docs/travail/TACHES.md",
        "ihm-v2/src/components/kanban/TaskKanbanBoard.tsx",
        ".gitignore",
    )

    assert _livrables_hors_convention("/depot", "master") == []


def test_les_tests_hors_paquets_de_production_restent_permis(monkeypatch):
    """
    `scripts/` et `tools/` hébergent déjà des `test_*.py` (7 au 19/08) : la
    porte ne doit pas condamner une convention en place.
    """
    _diff(monkeypatch, "scripts/test_campaign_deep.py", "tools/test_vertex_rag.py")

    assert _livrables_hors_convention("/depot", "master") == []


def test_les_fichiers_caches_echappent_a_la_regle_d_extension(monkeypatch):
    """
    `.gitignore`, `.dockerignore`, `.pre-commit-config.yaml` : nommés par
    convention, pas par extension. Les condamner serait un faux positif.
    """
    _diff(monkeypatch, ".gitignore", ".dockerignore", ".env.example")

    assert _livrables_hors_convention("/depot", "master") == []


def test_ce_que_la_porte_n_attrape_PAS(monkeypatch):
    """
    Limite assumée, écrite ici pour que personne ne croie la porte plus large
    qu'elle n'est.

    T416 a livré `core/dreamcoder/metrics/persistance_tokens.py` + son test au
    bon endroit : arborescence irréprochable. Son défaut — créer un module neuf
    au lieu de corriger le chemin `except` de `dreamer_agent.py` où les
    `tokens_used` sont perdus — n'est pas mécaniquement détectable sans faux
    positifs. C'est le contrat de délégation qui doit l'empêcher, pas la porte.
    """
    _diff(
        monkeypatch,
        "core/dreamcoder/metrics/__init__.py",
        "core/dreamcoder/metrics/persistance_tokens.py",
        "tests/unit/test_persistance_tokens_metrics.py",
    )

    assert _livrables_hors_convention("/depot", "master") == []


def test_plusieurs_motifs_sont_tous_remontes(monkeypatch):
    _diff(
        monkeypatch,
        "runtime/llm",
        "core/dreamcoder/metrics/test_persistance.py",
        "core/app_state.py",
    )

    assert len(_livrables_hors_convention("/depot", "master")) == 2


def test_un_script_python_a_la_racine_est_refuse(monkeypatch):
    """Vague du 23/08 : extract_git_files.py / get_git_file.py livrés à la racine."""
    _diff(monkeypatch, "extract_git_files.py", "core/app_state.py")

    motifs = _livrables_hors_convention("/depot", "master")
    assert len(motifs) == 1
    assert "extract_git_files.py" in motifs[0]
    assert "racine" in motifs[0]
