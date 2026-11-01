"""
tests/unit/test_task_discovery_agent.py — Test unitaire pour TaskDiscoveryAgent
"""

import pytest
from pathlib import Path
from agents.task_discovery_agent import TaskDiscoveryAgent, cle_dedup_todo


def test_evaluate_complexity():
    agent = TaskDiscoveryAgent()
    assert agent.evaluate_complexity("Refactor de l'architecture systemd et securite", "core") == "high"
    assert agent.evaluate_complexity("Mettre a jour la documentation et les commentaires", "docs") == "low"
    assert agent.evaluate_complexity("Ajouter un bouton d'action", "ihm") == "medium"


def test_select_llm_tier():
    agent = TaskDiscoveryAgent()
    tier_low = agent.select_llm_tier("low")
    assert tier_low["tier"] == "local_free"
    assert "ajean_pc" in tier_low["recommended_models"]

    tier_high = agent.select_llm_tier("high")
    assert tier_high["tier"] == "supervisor_pro"
    assert "claude-sonnet-4-6" in tier_high["recommended_models"]


def test_prepare_task_payload():
    agent = TaskDiscoveryAgent()
    task = {
        "id": "T999",
        "zone": "core/test.py",
        "prio": "P2",
        "title": "Titre test",
        "description": "Exemple de description pour test",
        "complexity": "low",
        "source": "unit_test"
    }
    payload = agent.prepare_task_payload(task)
    assert payload["task_id"] == "T999"
    assert "Contexte d'exécution" in payload["prompt"]
    assert payload["tier_info"]["tier"] == "local_free"


def test_payload_contrat_quatre_ingredients():
    """Le prompt de délégation n'est plus un préambule générique : il porte
    les quatre ingrédients (périmètre, objectif, validation, contrat de
    sortie) et le marqueur de dédup."""
    agent = TaskDiscoveryAgent()
    task = {
        "id": "T998",
        "zone": "core/vocal_host.py",
        "prio": "P2",
        "title": "Titre test",
        "description": "Brancher le scorer",
        "complexity": "medium",
        "source": "unit_test",
        "dedup_key": "board:T998",
    }
    prompt = agent.prepare_task_payload(task)["prompt"]
    for section in ("1. PÉRIMÈTRE", "2. OBJECTIF", "3. VALIDATION OBLIGATOIRE",
                    "4. CONTRAT DE SORTIE"):
        assert section in prompt
    assert "[dedup:board:T998]" in prompt
    assert "core/vocal_host.py" in prompt


def test_cle_dedup_todo_stable_sans_numero_de_ligne():
    """Même TODO déplacé de trois lignes = même clé (casse/espaces normalisés)."""
    assert (cle_dedup_todo("core/budget_guard.py", "Brancher le ProviderScorer")
            == cle_dedup_todo("core/budget_guard.py", "brancher   le providerscorer"))
    assert (cle_dedup_todo("core/a.py", "X")
            != cle_dedup_todo("core/b.py", "X"))


def test_scan_board_exclut_les_decisions_humaines(tmp_path):
    """Une tâche marquée « DÉCISION AXEL » ne doit jamais être déléguée."""
    board = tmp_path / "docs" / "travail"
    board.mkdir(parents=True)
    (board / "TACHES.md").write_text(
        "- ⏳ #T500 — vocal — **P2** — Réparer le marqueur xyz. — Claude —\n"
        "- ⏳ #T501 — config — **P3** — **DÉCISION AXEL** — Choix du jeton. — Axel —\n",
        encoding="utf-8")

    taches = TaskDiscoveryAgent(tmp_path).scan_board_tasks()

    assert [t["id"] for t in taches] == ["T500"]
    assert taches[0]["dedup_key"] == "board:T500"


def test_scan_board_exclut_les_analyses(tmp_path):
    """« À ANALYSER » n'est pas du code : le cycle autonome ne doit pas l'injecter."""
    board = tmp_path / "docs" / "travail"
    board.mkdir(parents=True)
    (board / "TACHES.md").write_text(
        "- ⏳ #T500 — vocal — **P2** — Réparer le marqueur xyz. — Claude —\n"
        "- ⏳ #T421 — tab5/firmware — **P1** — **À ANALYSER** — Montée ESPHome. — Claude —\n",
        encoding="utf-8")

    taches = TaskDiscoveryAgent(tmp_path).scan_board_tasks()

    assert [t["id"] for t in taches] == ["T500"]


def test_scan_code_todos_cle_sans_ligne(tmp_path):
    (tmp_path / "exemple.py").write_text(
        "x = 1\n# TODO: Brancher ProviderScorer sur DreamCoder\ny = 2\n",
        encoding="utf-8")

    todos = TaskDiscoveryAgent(tmp_path).scan_code_todos()

    assert len(todos) == 1
    # La clé ne contient PAS le numéro de ligne.
    assert todos[0]["dedup_key"] == "todo:exemple.py:brancher providerscorer sur dreamcoder"
    assert ":2" not in todos[0]["title"]


def test_scan_code_todos_exclut_le_bruit(tmp_path):
    """Le code tiers embarqué, les brouillons et les worktrees ne sont pas
    du travail à déléguer : ils ne doivent pas produire de tâches."""
    for dossier in ("scratch", ".claude/worktrees/x", "tools/llama.cpp", "core"):
        (tmp_path / dossier).mkdir(parents=True, exist_ok=True)
        (tmp_path / dossier / "fichier.py").write_text(
            "# " + "TODO: quelque chose à faire\n", encoding="utf-8")

    todos = TaskDiscoveryAgent(tmp_path).scan_code_todos()

    assert [t["zone"] for t in todos] == ["core/fichier.py"]


def test_scan_code_todos_chemins_posix(tmp_path):
    """La clé doit être identique quel que soit l'OS : séparateurs POSIX."""
    (tmp_path / "sous" / "dossier.py").parent.mkdir(parents=True)
    (tmp_path / "sous" / "dossier.py").write_text(
        "# TODO: normaliser\n", encoding="utf-8")

    (todo,) = TaskDiscoveryAgent(tmp_path).scan_code_todos()

    assert todo["dedup_key"] == "todo:sous/dossier.py:normaliser"


def test_scan_code_todos_exclut_venv_et_site_packages(tmp_path):
    for dossier in ("venv", "site-packages", ".tox", "core"):
        (tmp_path / dossier).mkdir(parents=True, exist_ok=True)
        (tmp_path / dossier / "fichier.py").write_text(
            "# TODO: dépendance à mettre à jour\n", encoding="utf-8")

    todos = TaskDiscoveryAgent(tmp_path).scan_code_todos()

    assert [t["zone"] for t in todos] == ["core/fichier.py"]


def test_scan_code_todos_porte_une_validation_mecanique(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "budget_guard.py").write_text(
        "# TODO: brancher le scorer\n", encoding="utf-8")

    todos = TaskDiscoveryAgent(tmp_path).scan_code_todos()
    assert len(todos) == 1
    assert todos[0]["validation"]["fichier_test"] == "tests/unit/test_budget_guard.py"
    prompt = TaskDiscoveryAgent(tmp_path).prepare_task_payload(todos[0])["prompt"]
    assert "tests/unit/test_budget_guard.py" in prompt


def test_scan_board_ignore_une_tache_deja_cochee(tmp_path):
    board = tmp_path / "docs" / "travail"
    board.mkdir(parents=True)
    (board / "TACHES.md").write_text(
        "- ⏳ #T414 — dreamcoder/decouverte — **P1** — Filtrer les faux positifs.\n"
        "| ✅ **#T414 / #T416 déjà dans git** | Filtre worktrees.\n",
        encoding="utf-8")

    taches = TaskDiscoveryAgent(tmp_path).scan_board_tasks()
    assert taches == []
