"""
tests/unit/test_router_contexte_chat.py — Contexte projet léger sur le chat rapide (#T173).

Le problème que ces tests verrouillent, vérifié le 11/08 : le chat rapide
(casual_chat, chemin le plus utilisé — 865 échanges vocaux sur vocal_audit_log)
partait SANS aucun contexte projet, là où les autres catégories en reçoivent.

Règle posée : casual_chat reçoit un contexte LÉGER (profil utilisateur d'Axel,
catégorie "core" du ContextLoader) plafonné explicitement — la latence est LE
critère de ce chemin (p50 1,6 s / p90 3,6 s) — et désactivable par le
kill-switch MOTEUR_CHAT_CONTEXTE_PROJET=0 (comportement d'avant, identique).
"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.router import (
    CASUAL_CHAT_CONTEXT_CATEGORIES,
    CASUAL_CHAT_MAX_CONTEXT_CHARS,
    CATEGORY_TO_CONTEXT,
    Router,
    contexte_projet_chat_rapide_active,
)
from memory.context_loader import ContextLoader


def _routeur_mocke() -> Router:
    """Router nu : ContextLoader et compresseur mockés, mémoires vides."""
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

    # Compresseur laissant passer le contexte structuré tel quel, pour mesurer la borne
    routeur.context_compressor = MagicMock()
    routeur.context_compressor.compress = MagicMock(
        side_effect=lambda contextes: contextes.get("context_loader", "")
    )
    del routeur.context_compressor.compress_async

    routeur.categories = {
        "casual_chat": {"keywords": ["bonjour", "salut", "hello"], "weight": 1.0},
        "home_assistant": {"keywords": ["lumiere", "clim"], "weight": 1.5},
        "code_generation": {"keywords": ["code", "python"], "weight": 1.2},
        "database": {"keywords": ["sqlite", "sql"], "weight": 1.4},
        "files": {"keywords": ["fichier"], "weight": 1.0},
        "analysis": {"keywords": ["analyse", "audit"], "weight": 1.2},
        "sysadmin": {"keywords": ["ssh", "deck"], "weight": 1.5},
        "deck_edge": {"keywords": ["deck_ollama"], "weight": 1.0},
    }
    routeur._ha_commands = []
    return routeur


def _contexte_injecte(routeur: Router) -> str:
    """Le contexte 3-Layers réellement transmis au compresseur (donc injecté au prompt)."""
    return routeur.context_compressor.compress.call_args.args[0]["context_loader"]


class TestContexteProjetChatRapide:
    """Le chat rapide reçoit désormais un contexte projet léger et borné."""

    async def test_casual_chat_recoit_contexte_borne(self):
        """Contexte non vide, borné par le plafond déclaré."""
        routeur = _routeur_mocke()
        # Source volontairement plus longue que le plafond : le router doit la borner
        routeur.context_loader.get_context_for_categories.return_value = (
            "Profil utilisateur : Axel, domotique multi-agents, projets Tab5/HA/moteur. " + "x" * 9_000
        )

        payload, agent = await routeur.analyze_request("Bonjour, comment vas-tu ?")

        assert payload.metadata["dominant_category"] == "casual_chat"
        assert agent == "executor"  # Court-circuit conservé : la latence est LE critère
        # Le loader est appelé avec la catégorie légère et le plafond NOMINATIF
        routeur.context_loader.get_context_for_categories.assert_called_once_with(
            CASUAL_CHAT_CONTEXT_CATEGORIES, max_chars=CASUAL_CHAT_MAX_CONTEXT_CHARS
        )
        injecte = _contexte_injecte(routeur)
        assert 0 < len(injecte) <= CASUAL_CHAT_MAX_CONTEXT_CHARS
        assert "Profil utilisateur" in injecte
        assert injecte in payload.relevant_context

    async def test_plafond_respecte_source_enorme(self):
        """Même avec une source énorme, le contexte injecté reste ≤ au plafond."""
        routeur = _routeur_mocke()
        # Source 25x plus grosse que le plafond : la garantie en dur doit trancher
        routeur.context_loader.get_context_for_categories.return_value = "z" * 100_000

        payload, _agent = await routeur.analyze_request("Salut, tu connais mes projets ?")

        injecte = _contexte_injecte(routeur)
        assert len(injecte) <= CASUAL_CHAT_MAX_CONTEXT_CHARS
        assert len(injecte) == CASUAL_CHAT_MAX_CONTEXT_CHARS  # Tronqué exactement au plafond
        assert injecte in payload.relevant_context

    async def test_kill_switch_zero_contexte_vide(self, monkeypatch):
        """MOTEUR_CHAT_CONTEXTE_PROJET=0 → aucun contexte, comportement d'avant identique."""
        monkeypatch.setenv("MOTEUR_CHAT_CONTEXTE_PROJET", "0")
        routeur = _routeur_mocke()

        payload, _agent = await routeur.analyze_request("Bonjour !")

        # Le loader n'est jamais sollicité et rien n'est injecté
        routeur.context_loader.get_context_for_categories.assert_not_called()
        assert _contexte_injecte(routeur) == ""
        # Métadonnées strictement identiques à l'ancien comportement
        assert payload.metadata["context_categories"] == []

    def test_kill_switch_valeurs(self, monkeypatch):
        """Défaut actif ; '0'/'false'/'off' désactivent l'injection."""
        monkeypatch.delenv("MOTEUR_CHAT_CONTEXTE_PROJET", raising=False)
        assert contexte_projet_chat_rapide_active() is True
        for valeur in ("0", "false", "off"):
            monkeypatch.setenv("MOTEUR_CHAT_CONTEXTE_PROJET", valeur)
            assert contexte_projet_chat_rapide_active() is False

    async def test_rag_toujours_exclu_du_chat_rapide(self):
        """Inchangé : le RAG ne pollue pas le chat rapide (latence)."""
        routeur = _routeur_mocke()

        appele = {"nb": 0}

        async def _faux_query(*_args, **_kwargs):
            appele["nb"] += 1
            return "résultat RAG"

        routeur.rag_engine = MagicMock()
        routeur.rag_engine.query_async = _faux_query

        await routeur.analyze_request("Salut, ça va ?")

        # La garde existante (dominant_category != "casual_chat") reste en place
        assert appele["nb"] == 0


class TestContexteChatBoutEnBout:
    """Bout-en-bout avec le VRAI ContextLoader : troncature du loader + borne du router."""

    async def test_contexte_reel_du_loader_borne(self, tmp_path):
        # Fabriquer un contexte_ia temporaire avec un fichier "core" énorme
        dossier_core = tmp_path / "01_Core"
        dossier_core.mkdir(parents=True)
        (dossier_core / "rules_global.md").write_text(
            "Profil utilisateur d'Axel : domotique, Tab5, Home Assistant, moteur multi-agents.\n"
            + "y" * 50_000,
            encoding="utf-8",
        )

        routeur = _routeur_mocke()
        routeur.context_loader = ContextLoader(contexte_ia_path=str(tmp_path))
        routeur.context_loader.load_all()

        payload, _agent = await routeur.analyze_request("Salut, tu connais mes projets ?")

        injecte = _contexte_injecte(routeur)
        assert 0 < len(injecte) <= CASUAL_CHAT_MAX_CONTEXT_CHARS
        assert "Profil utilisateur" in injecte
        assert injecte in payload.relevant_context


class TestMappingContexteInchange:
    """Le mapping des AUTRES catégories reste strictement identique."""

    def test_autres_categories_inchangees(self):
        mapping_avant = {
            "home_assistant": ["home_assistant"],
            "code_generation": ["code_generation"],
            "database": ["home_assistant"],
            "analysis": ["analysis"],
            "files": [],
            "sysadmin": [],
            "deck_edge": [],
        }
        for categorie, attendu in mapping_avant.items():
            assert CATEGORY_TO_CONTEXT[categorie] == attendu, f"Mapping modifié pour {categorie}"
        # Seule évolution : casual_chat pointe vers le profil utilisateur léger
        assert CATEGORY_TO_CONTEXT["casual_chat"] == CASUAL_CHAT_CONTEXT_CATEGORIES == ["core"]
