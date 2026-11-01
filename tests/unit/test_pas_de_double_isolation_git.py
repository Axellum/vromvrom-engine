"""
tests/unit/test_pas_de_double_isolation_git.py — Une seule couche d'isolation Git (#T391).

L'Engine crée une branche éphémère `agent/run_<session>_<ts>` pour isoler le
travail d'une session, et la finalise en fin de course : merge si succès,
**rollback destructif** si échec. `success` tombe à False dès qu'un outil
rapporte une erreur — un `ls` sur un chemin inexistant suffit.

Or une tâche DreamCoder est DÉJÀ isolée par son appelant : branche
`task/<id>_<ts>` dans un clone dédié, commit et rollback gérés par
`agents/dreamer_agent.py`. La seconde branche ne protégeait donc rien, et
détruisait le travail avant que l'appelant ne puisse le commiter.

Mesuré le 18/08, tâche 16 :
    [T273-AUDIT] Outil à risque exécuté : write_file
    [GIT SAFETY] Échec détecté. Annulation des changements sur 'agent/run_task_16_…'

DreamCoder trouvait ensuite une branche vide et concluait « aucun fichier n'a
été écrit » — diagnostic faux : ils avaient été écrits, puis supprimés.
"""

from unittest.mock import patch

from core.engine import GIT_SESSIONS_DEJA_ISOLEES, Engine


def test_une_tache_dreamcoder_ne_recoit_pas_de_seconde_branche():
    """Le cœur du correctif : aucune branche éphémère, donc aucun rollback."""
    eng = Engine(session_id="task_16")

    with patch("tools.git_safety.git_prepare_agent_branch") as prepare:
        assert eng._prepare_git_branch() is None
        prepare.assert_not_called()


def test_une_session_de_chat_garde_son_isolation():
    """
    Les sessions interactives n'ont pas d'appelant qui les isole : elles
    continuent de recevoir leur branche éphémère, comportement inchangé.
    """
    eng = Engine(session_id="chat_abc")

    with patch("tools.git_safety.git_prepare_agent_branch",
               return_value="agent/run_chat_abc_1787000000") as prepare:
        assert eng._prepare_git_branch() == "agent/run_chat_abc_1787000000"
        prepare.assert_called_once()


def test_les_sessions_autonomes_non_dreamcoder_gardent_leur_isolation():
    """
    `daemon_`/`dreamer_` tournent sans appelant qui gère Git : elles gardent la
    protection. Seul `task_` est concerné.
    """
    for session in ("daemon_001", "dreamer_x", "routine_2"):
        eng = Engine(session_id=session)
        with patch("tools.git_safety.git_prepare_agent_branch",
                   return_value=f"agent/run_{session}_1") as prepare:
            assert eng._prepare_git_branch() is not None
            prepare.assert_called_once()


def test_le_prefixe_ne_deborde_pas():
    """
    Garde-fou : la dispense s'évalue sur un PRÉFIXE. Une session interactive
    dont l'identifiant contient « task » garde son isolation — sinon il
    suffirait de nommer sa session pour perdre la protection.
    """
    eng = Engine(session_id="chat_task_1")

    with patch("tools.git_safety.git_prepare_agent_branch",
               return_value="agent/run_chat_task_1_1") as prepare:
        assert eng._prepare_git_branch() is not None
        prepare.assert_called_once()


def test_la_liste_est_explicite():
    """Le seul cas dispensé aujourd'hui est DreamCoder, et il est nommé."""
    assert GIT_SESSIONS_DEJA_ISOLEES == ("task_",)
