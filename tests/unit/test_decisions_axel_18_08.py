"""
tests/unit/test_decisions_axel_18_08.py — Deux décisions tranchées le 18/08.

#T315 — Seuil d'approbation humaine relevé à `high`.
Le seuil était codé en dur sur `low` : tout plan classé `medium` (un libellé
contenant « écrire », « créer », « exécuter ») réclamait un feu vert. Sur la
campagne mesurée, 3 demandes sur 8 sont mortes faute de réponse — dont un `ls`
de `docs/`, qui ne modifie rien. Un garde-fou qu'on apprend à cliquer sans lire
ne garde plus rien.

#T335 — Les modèles CLI sortent du routage là où leurs binaires n'existent pas.
`MOTEUR_DISABLE_CLI_PROVIDERS` ne couvrait que le round-robin de DreamCoder ; le
chat et le vocal continuaient de les élire dans les tiers, puis d'échouer.
Mesuré sur le Deck : `gemini-cli-abo` meurt sur `FileNotFoundError:
'antigravity-ide'`, `claude-cli-abo` rend « Credit balance is too low ». Les
`*-cli` sont présents dans les QUATRE tiers, `leger` compris.
"""

import os
from unittest.mock import patch

import pytest

from core.engine import HITL_SEUIL_APPROBATION_DEFAUT, Engine
from core.llm_gateway import _est_modele_cli


def _moteur(session_id="chat_abc"):
    return Engine(session_id=session_id)


# ── #T315 — le seuil d'approbation ──────────────────────────────────────────

def test_le_seuil_par_defaut_est_high():
    assert HITL_SEUIL_APPROBATION_DEFAUT == "high"


def test_un_plan_medium_ne_reclame_plus_de_feu_vert():
    """Le cas qui a tué 3 demandes sur 8 : « écrire » dans un libellé de tâche."""
    eng = _moteur()
    with patch("core.llm_gateway.load_config", return_value={}):
        raison = eng._hitl_bypass_reason("chat_abc", "medium")
    assert raison is not None
    assert "seuil" in raison


def test_un_plan_high_reclame_toujours_un_feu_vert():
    """write_file reste au-dessus du seuil : l'approbation est demandée."""
    eng = _moteur()
    with patch("core.llm_gateway.load_config", return_value={}):
        assert eng._hitl_bypass_reason("chat_abc", "high") is None


def test_un_plan_critical_reclame_toujours_un_feu_vert():
    """run_terminal_command n'est jamais silencieux sur une session interactive."""
    eng = _moteur()
    with patch("core.llm_gateway.load_config", return_value={}):
        assert eng._hitl_bypass_reason("chat_abc", "critical") is None


def test_le_risque_faible_passe_toujours():
    eng = _moteur()
    with patch("core.llm_gateway.load_config", return_value={}):
        assert eng._hitl_bypass_reason("chat_abc", "low") is not None


def test_le_seuil_est_reglable_par_config():
    """Remettre `medium` restaure le comportement d'avant la décision."""
    eng = _moteur()
    with patch("core.llm_gateway.load_config",
               return_value={"hitl": {"seuil_approbation": "medium"}}):
        assert eng._hitl_bypass_reason("chat_abc", "medium") is None


def test_un_seuil_invalide_retombe_sur_le_defaut():
    """
    Une faute de frappe dans config.json ne doit ni ouvrir tout, ni tout fermer :
    elle retombe sur `high` et le journalise.
    """
    eng = _moteur()
    with patch("core.llm_gateway.load_config",
               return_value={"hitl": {"seuil_approbation": "haut"}}):
        assert eng._hitl_bypass_reason("chat_abc", "medium") is not None   # < high
        assert eng._hitl_bypass_reason("chat_abc", "high") is None         # >= high


def test_la_session_autonome_reste_prioritaire_sur_le_seuil():
    """Le bypass des sessions de nuit ne dépend pas du seuil (#T386)."""
    eng = _moteur("task_42")
    with patch("core.llm_gateway.load_config",
               return_value={"hitl": {"seuil_approbation": "low"}}):
        assert eng._hitl_bypass_reason("task_42", "critical") is not None


# ── #T335 — les modèles CLI hors du routage ─────────────────────────────────

@pytest.mark.parametrize("modele", [
    "gemini-3.5-flash-high-cli",
    "claude-opus-4.6-thinking-cli",
    "claude-sonnet-4.6-thinking-cli",
])
def test_modele_cli_reconnu(modele):
    assert _est_modele_cli(modele) is True


@pytest.mark.parametrize("modele", [
    "deepseek-chat",
    "open-mistral-nemo",
    "gemini-3.5-flash-free",
    "clip-model",          # se termine par « cli » sans être un CLI
])
def test_modele_non_cli(modele):
    assert _est_modele_cli(modele) is False


def test_la_desactivation_lit_la_meme_variable_que_budget_guard():
    """
    Une seule définition de « les CLI sont hors jeu ici » : le routage par tier
    et le round-robin de DreamCoder doivent s'accorder, sinon l'un écarte ce que
    l'autre élit.
    """
    from core.budget_guard import _cli_providers_disabled
    from core.llm_gateway import _cli_desactives

    with patch.dict(os.environ, {"MOTEUR_DISABLE_CLI_PROVIDERS": "1"}):
        assert _cli_desactives() is True
        assert _cli_providers_disabled() is True

    with patch.dict(os.environ, {"MOTEUR_DISABLE_CLI_PROVIDERS": "0"}):
        assert _cli_desactives() is False
        assert _cli_providers_disabled() is False
