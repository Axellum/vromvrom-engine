"""
tests/unit/test_sync_db_to_markdown.py — Garde-fous #T307 de la synchronisation
Markdown de fin de session.

Trois invariants, testés dans l'ordre d'importance :

1. **Jamais d'écrasement** (garde-fou n°1, celui qui décide de l'acceptation) :
   un fichier préexistant au contenu arbitraire reste intact après exécution,
   le script écrit dans un nouveau fichier (suffixe numérique).
2. **`--dry-run` n'écrit rien du tout**.
3. **Base temporaire uniquement** (motif `override_db_path`,
   cf. tests/unit/test_billing_history_schema.py:29-34) — jamais
   `moteur_runtime.db` de prod dans les tests.
"""

import os
from datetime import datetime

import pytest

from core import runtime_db
from tools.sync_db_to_markdown import sync_db_to_markdown

# Objectif court et stable : le slug du dossier de sortie doit être déterministe.
_OBJECTIF_DEMO = "Audit HA"
_SUJET_DEMO = "audit_ha"


def _base_temporaire(tmp_path) -> str:
    """Crée une base runtime isolée avec une session de démo, retourne son chemin."""
    db = str(tmp_path / "test_runtime.db")
    runtime_db.override_db_path(db)
    runtime_db.get_connection().close()  # déclenche _init_schema

    with runtime_db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO sessions
                (session_id, objective, status, started_at, ended_at, duration_ms,
                 starting_agent, agents_invoked, task_count, last_activity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "session_test_001",
                _OBJECTIF_DEMO,
                "success",
                1000.0,
                1300.0,
                300000.0,
                "planner",
                '["planner", "executor", "reviewer"]',
                4,
                1300.0,
            ),
        )
        conn.execute(
            """
            INSERT INTO token_usage
                (session_id, timestamp, model, prompt_tokens, completion_tokens,
                 total_tokens, cost_usd, channel, agent_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("session_test_001", 1200.0, "deepseek-chat", 500, 200, 700, 0.0021,
             "deepseek", "executor"),
        )
        conn.commit()
    return db


def test_fichier_preexistant_intact_et_ajout_seul(tmp_path):
    """GARDE-FOU N°1 : un fichier préexistant au contenu arbitraire est intact
    après exécution — l'écriture se fait en AJOUT (nouveau fichier suffixé)."""
    db = _base_temporaire(tmp_path)
    out = tmp_path / "historique"
    out.mkdir()
    # Fichier préexistant au contenu arbitraire (comme un résumé écrit à la main)
    # dans le dossier exact que le script ciblera (date du jour + slug de l'objectif).
    dossier_cible = out / f"{datetime.now().strftime('%Y-%m-%d')}_{_SUJET_DEMO}"
    dossier_cible.mkdir()
    preexistant = dossier_cible / "resume_session.md"
    contenu_humain = "# Résumé écrit à la main par Axel\n\nNe jamais écraser ce texte."
    preexistant.write_text(contenu_humain, encoding="utf-8")

    resultat = sync_db_to_markdown(
        session_id="session_test_001", db_path=db, out_dir=str(out)
    )

    assert resultat["ecrit"] is True
    assert preexistant.read_text(encoding="utf-8") == contenu_humain, (
        "le fichier préexistant a été modifié — écriture en AJOUT exigée"
    )
    # Un NOUVEAU fichier a été créé (suffixe numérique), pas d'écrasement.
    nouveaux = [
        p for p in preexistant.parent.iterdir()
        if p.name != "resume_session.md"
    ]
    assert len(nouveaux) == 1, f"attendu 1 nouveau fichier, trouvé {[p.name for p in nouveaux]}"
    contenu_nouveau = nouveaux[0].read_text(encoding="utf-8")
    assert "session_test_001" in contenu_nouveau
    assert "Audit HA" in contenu_nouveau


def test_dry_run_ne_ecrit_rien_du_tout(tmp_path):
    """GARDE-FOU : --dry-run affiche le diff et ne crée AUCUN fichier."""
    db = _base_temporaire(tmp_path)
    out = tmp_path / "historique_dry"
    out.mkdir()

    resultat = sync_db_to_markdown(
        session_id="session_test_001", dry_run=True, db_path=db, out_dir=str(out)
    )

    assert resultat["ecrit"] is False
    assert "DRY-RUN" in resultat["diff"]
    assert "session_test_001" in resultat["diff"]
    assert list(out.rglob("*")) == [], (
        "dry-run ne doit créer ni dossier ni fichier"
    )


def test_session_introuvable_refuse_decrire(tmp_path):
    """Mode défensif : une session introuvable lève et n'écrit rien."""
    db = _base_temporaire(tmp_path)
    out = tmp_path / "historique_vide"
    out.mkdir()

    with pytest.raises(ValueError, match="Aucune session 'inexistante'"):
        sync_db_to_markdown(
            session_id="inexistante", db_path=db, out_dir=str(out)
        )

    assert list(out.rglob("*")) == []


def test_resume_complet_contient_les_sections(tmp_path):
    """Le résumé contient les sections attendues (consommation, routage, événements)."""
    db = _base_temporaire(tmp_path)
    out = tmp_path / "historique_contenu"
    out.mkdir()

    resultat = sync_db_to_markdown(
        session_id="session_test_001", db_path=db, out_dir=str(out)
    )

    chemin = resultat["chemin"]
    assert os.path.exists(chemin)
    contenu = open(chemin, encoding="utf-8").read()
    for section in (
        "# 📝 Résumé de Session",
        "## 💰 Consommation LLM",
        "| deepseek-chat | 1 | 700 | 0.0021 |",
        "## 🧭 Décisions de routage",
        "## 📡 Événements notables",
        "## 🧩 Tâches DAG",
        "ne remplace pas le récit humain de `04_Projets/`",
    ):
        assert section in contenu, f"section manquante : {section}"


async def test_declencheur_router_fin_de_session(monkeypatch):
    """#T307 : `analyze_request` déclenche la sync en arrière-plan quand la session
    est connue, et ne déclenche RIEN sans session (pas de résumé « au hasard »)."""
    import asyncio
    from unittest.mock import MagicMock

    from core.router import Router

    appels = []

    def _fake_sync(session_id=None, **kwargs):
        appels.append(session_id)
        return {"ecrit": False, "chemin": None, "diff": "fake"}

    monkeypatch.setattr(
        "tools.sync_db_to_markdown.sync_db_to_markdown", _fake_sync
    )

    routeur = Router.__new__(Router)
    routeur.default_agent = "planner"
    routeur.rag_engine = None
    routeur.llm_gateway = None
    routeur.config = {}
    routeur.context_loader = MagicMock()
    routeur.context_loader.load_all.return_value = None
    routeur.context_loader.reload_if_stale.return_value = None
    routeur.context_loader.get_context_for_categories.return_value = ""
    routeur.episode_store = MagicMock()
    del routeur.episode_store.query_relevant_episodes_async
    routeur.episode_store.query_relevant_episodes.return_value = ""
    routeur.fact_store = MagicMock()
    del routeur.fact_store.get_facts_for_context_async
    routeur.fact_store.get_facts_for_context.return_value = ""
    routeur.categories = {}

    # Avec session : la sync est déclenchée en tâche de fond.
    await routeur.analyze_request("FIN DE SESSION — sauvegarde", session_id="s_t307")
    await asyncio.sleep(0.05)  # laisser la tâche de fond s'exécuter
    assert appels == ["s_t307"], f"sync attendue avec s_t307, reçu {appels}"

    # Sans session : aucun déclenchement (garde anti-résumé au hasard).
    await routeur.analyze_request("FIN DE SESSION — sauvegarde")
    await asyncio.sleep(0.05)
    assert appels == ["s_t307"], "sans session_id, la sync ne doit pas partir"
