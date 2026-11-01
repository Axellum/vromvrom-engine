"""
test_app_state.py — Tests unitaires du singleton AppState (core/app_state.py).

Vérifie notamment le correctif #T344 : un échec survenu pendant qu'une autre
requête tourne ne doit PAS être masqué par le succès final de la dernière
requête. `_refresh_aggregate_locked` doit mémoriser le dernier statut terminal
et le réappliquer quand le système redevient idle.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.app_state import AppState


@pytest.fixture
def state():
    """Fixture : un AppState fraîchement initialisé (singleton réinitialisé)."""
    s = AppState()
    s._initialized = False
    s.initialize()
    return s


class TestRefreshAggregateLocked:
    """Tests du correctif #T344 : pas de masquage d'échec."""

    def test_etat_idle_initial(self, state):
        """Au démarrage, l'état agrégé est 'idle'."""
        assert state.execution_state["status"] == "idle"
        assert state.execution_state["error_message"] is None

    def test_retour_premature_en_running_conserve_etat(self, state):
        """Quand une requête est active, l'état reste 'running'."""
        state.active_requests = 1
        state._refresh_aggregate_locked(status="success")
        assert state.execution_state["status"] == "running"

    def test_echec_pendant_requete_active_est_memorise(self, state):
        """
        Scénario du bug #T344 :
        1. Une requête B est encore active (active_requests=1)
        2. La tâche A se termine en erreur → _refresh_aggregate_locked("error", msg)
        3. Le statut terminal 'error' et le message doivent être mémorisés
        """
        state.active_requests = 1
        state._refresh_aggregate_locked(status="error", error_message="Échec de la tâche A")

        # L'état agrégé reste 'running' (système encore actif)...
        assert state.execution_state["status"] == "running"
        # ...mais l'échec est mémorisé pour être réappliqué plus tard.
        assert state._last_terminal_status == "error"
        assert state._last_terminal_error == "Échec de la tâche A"

    def test_echec_non_masque_par_succes_final(self, state):
        """
        Scénario complet du bug #T344 :
        1. Tâche A échoue pendant que la requête B tourne
        2. La requête B se termine avec succès
        3. L'état final doit refléter l'échec de A, PAS le succès de B
        """
        # Étape 1 : la requête B démarre
        state.active_requests = 1
        state.execution_state["status"] = "running"

        # Étape 2 : la tâche A se termine en erreur
        state._refresh_aggregate_locked(status="error", error_message="Échec de A")

        # Étape 3 : la requête B se termine (succès par défaut)
        state.active_requests = 0
        state._refresh_aggregate_locked(status="success")

        # L'échec de A doit être visible, pas masqué par le succès de B
        assert state.execution_state["status"] == "error"
        assert state.execution_state["error_message"] == "Échec de A"

    def test_succes_memorise_ne_masque_pas_un_echec_final(self, state):
        """
        Symétrique du cas précédent — le piège de la première version.

        Si l'on mémorise AUSSI les succès, un "success" enregistré pendant
        l'activité écrase l'"error" du dernier appelant : le masquage revient,
        simplement dans l'autre sens. Seul un échec doit être mémorisé.

        1. La requête B se termine en succès pendant que A tourne
        2. A se termine en erreur, plus rien en vol
        3. L'état final doit être l'erreur de A
        """
        state.active_requests = 1
        state._refresh_aggregate_locked(status="success")

        state.active_requests = 0
        state._refresh_aggregate_locked(status="error", error_message="Échec de A")

        assert state.execution_state["status"] == "error"
        assert state.execution_state["error_message"] == "Échec de A"

    def test_succes_final_sans_echec_precedent(self, state):
        """Sans échec antérieur, un succès final s'applique normalement."""
        state.active_requests = 1
        state._refresh_aggregate_locked(status="success")
        state.active_requests = 0
        state._refresh_aggregate_locked(status="success")

        assert state.execution_state["status"] == "success"
        assert state.execution_state["error_message"] is None

    def test_memoire_reinitialisee_apres_application(self, state):
        """
        Après application du dernier statut terminal, la mémoire est
        réinitialisée pour le prochain cycle.
        """
        state.active_requests = 1
        state._refresh_aggregate_locked(status="error", error_message="Échec")
        state.active_requests = 0
        state._refresh_aggregate_locked(status="success")

        # La mémoire doit être vide pour le prochain cycle
        assert state._last_terminal_status == "idle"
        assert state._last_terminal_error is None

    def test_clear_executions_reinitialise_memoire(self, state):
        """
        Un arrêt global (clear_executions) doit repartir d'une page blanche,
        sans resurgir un échec antérieur mémorisé.
        """
        # Simuler un échec mémorisé
        state.active_requests = 1
        state._refresh_aggregate_locked(status="error", error_message="Échec")

        # Arrêt global
        import asyncio
        asyncio.run(state.clear_executions(status="error", error_message="Arrêt manuel"))

        assert state._last_terminal_status == "idle"
        assert state._last_terminal_error is None
        assert state.execution_state["status"] == "error"
        assert state.execution_state["error_message"] == "Arrêt manuel"

    def test_echec_avec_registre_execution_actif(self, state):
        """
        Même scénario mais avec une exécution dans le registre (pas seulement
        une requête en vol) : l'échec doit aussi être mémorisé.
        """
        state.execution_registry["session_B"] = {"status": "running"}
        state._refresh_aggregate_locked(status="error", error_message="Échec de A")

        assert state.execution_state["status"] == "running"
        assert state._last_terminal_status == "error"
        assert state._last_terminal_error == "Échec de A"

        # La session B se termine
        state.execution_registry.pop("session_B", None)
        state._refresh_aggregate_locked(status="success")

        assert state.execution_state["status"] == "error"
        assert state.execution_state["error_message"] == "Échec de A"
