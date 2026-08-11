"""
tests/unit/test_router_garde_hitl.py — Garde HITL sur les court-circuits (#T273).

Le problème que ces tests verrouillent, mesuré en production le 11/08 : le
Router court-circuitait les catégories `files`/`database`/`sysadmin` droit vers
l'Executor, qui écrivait des fichiers et lançait des commandes shell **sans
jamais croiser le point d'approbation humaine** — celui-ci n'est posé que sur
le chemin DAG (`_check_hitl_before_dag`). Un simple « écris le fichier … » a
produit `run_terminal_command : echo … > …` avec zéro demande d'approbation.

Règle posée : une requête qui demande d'AGIR repart vers le Planner (donc vers
un plan approuvable) ; une requête qui LIT garde le raccourci rapide.
"""

import pytest

from core.router import Router, requete_demande_une_action


@pytest.fixture
def routeur():
    """Router nu : seule la couche de résolution d'agent est exercée."""
    return Router()


# ── Détection d'action ───────────────────────────────────────────────────────

@pytest.mark.parametrize("prompt", [
    "Ecris le fichier /tmp/preuve.txt avec la ligne OK",          # le cas réel du 11/08
    "écris le fichier /tmp/preuve.txt",                            # avec accents
    "Supprime les logs de la semaine dernière",
    "Crée un dossier de sauvegarde",
    "Exécute la commande de nettoyage",
    "Lance le script de déploiement",
    "modifie la configuration du serveur",
    "rm -rf le dossier temporaire",
])
def test_actions_detectees(prompt):
    assert requete_demande_une_action(prompt) is True


@pytest.mark.parametrize("prompt", [
    "Lis le fichier de configuration et résume-le",
    "Affiche les 10 dernières lignes du journal",
    "Combien de fichiers dans ce dossier ?",
    "Cherche la définition de la classe Router",
])
def test_lectures_non_detectees(prompt):
    assert requete_demande_une_action(prompt) is False


# ── Résolution d'agent ───────────────────────────────────────────────────────

@pytest.mark.parametrize("categorie", ["files", "database", "sysadmin"])
def test_action_ne_court_circuite_plus(routeur, categorie):
    """Une action part au Planner : c'est le seul chemin qui passe par le HITL."""
    agent, routing_type, _tier, meta = routeur._resolve_target_agent(
        "Ecris le fichier /tmp/preuve.txt avec la ligne OK", categorie, is_complex=False
    )
    assert agent == "planner"
    assert routing_type == "planner_pour_approbation"
    assert meta["hitl_garde_routage"] is True


@pytest.mark.parametrize("categorie,routing_attendu", [
    ("files", "executor_direct"),
    ("database", "executor_direct"),
    ("sysadmin", "sysadmin_direct"),
])
def test_lecture_garde_le_raccourci(routeur, categorie, routing_attendu):
    """La lecture ne peut rien casser : elle conserve le chemin rapide."""
    agent, routing_type, _tier, _meta = routeur._resolve_target_agent(
        "Lis le fichier de configuration et résume-le", categorie, is_complex=False
    )
    assert agent == "executor"
    assert routing_type == routing_attendu


def test_kill_switch_restaure_le_raccourci(routeur, monkeypatch):
    """`MOTEUR_ROUTER_GARDE_HITL=0` rend le comportement d'avant #T273."""
    monkeypatch.setenv("MOTEUR_ROUTER_GARDE_HITL", "0")
    agent, routing_type, _tier, _meta = routeur._resolve_target_agent(
        "Ecris le fichier /tmp/preuve.txt", "files", is_complex=False
    )
    assert agent == "executor"
    assert routing_type == "executor_direct"


def test_categories_non_concernees_intactes(routeur):
    """Le vocal et la domotique ne sont pas touchés : leur latence est critique."""
    agent, routing_type, tier, _meta = routeur._resolve_target_agent(
        "Raconte-moi une blague", "casual_chat", is_complex=False
    )
    assert (agent, routing_type, tier) == ("executor", "casual_chat", "leger")


def test_requete_complexe_va_toujours_au_planner(routeur):
    """Inchangé : une requête complexe ne court-circuite jamais."""
    agent, routing_type, _tier, _meta = routeur._resolve_target_agent(
        "Lis les fichiers", "files", is_complex=True
    )
    assert agent == "planner"
    assert routing_type == "default"


# ── Trace d'audit des outils à risque ────────────────────────────────────────

@pytest.mark.asyncio
async def test_outil_a_risque_journalise(caplog):
    """
    Chaque exécution d'outil à risque laisse une trace greppable.

    Sans elle, impossible de mesurer ce qui s'exécute encore hors approbation —
    et donc impossible de savoir quand la surface est réellement couverte.
    """
    import logging

    from tools.tool_registry import OUTILS_A_RISQUE, ToolRegistry

    def _faux_terminal(commande: str = "") -> str:
        return "ok"

    registry = ToolRegistry()
    registry.register("run_terminal_command", _faux_terminal, "Faux outil de test")

    assert "run_terminal_command" in OUTILS_A_RISQUE
    with caplog.at_level(logging.INFO, logger="tools.tool_registry"):
        await registry.execute("run_terminal_command", {"commande": "echo test"})

    assert any("[T273-AUDIT]" in m and "run_terminal_command" in m for m in caplog.messages)


@pytest.mark.asyncio
async def test_outil_sans_risque_non_journalise(caplog):
    """Pas de bruit sur les outils de lecture — un journal saturé est un journal ignoré."""
    import logging

    from tools.tool_registry import ToolRegistry

    def _faux_lecture(chemin: str = "") -> str:
        return "contenu"

    registry = ToolRegistry()
    registry.register("read_file", _faux_lecture, "Faux outil de test")

    with caplog.at_level(logging.INFO, logger="tools.tool_registry"):
        await registry.execute("read_file", {"chemin": "fichier_inexistant.txt"})

    assert not any("[T273-AUDIT]" in m for m in caplog.messages)
