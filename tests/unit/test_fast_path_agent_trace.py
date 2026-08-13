"""
tests/unit/test_fast_path_agent_trace.py — L'étiquette d'agent survit au passage
dans le thread du provider (#T301).

#T296 a branché `token_usage.agent_name` sur une ContextVar posée par
`BaseAgent.invoke()`. Vérifié en exerçant la prod le 11/08, deux trous restaient :

1. le fast-path (`casual_chat`) n'est PAS un agent — il appelle le gateway
   directement — donc rien ne posait l'étiquette, alors que la route se déclare
   elle-même `agents_used = ["fast_path"]`. C'est le chemin dominant du moteur
   (6 appelants, dont `core/vocal_jobs.py` et `core/vocal_host.py`) ;
2. même une fois l'étiquette posée, elle n'atteignait pas `record_usage()` :
   l'appel provider passait par `loop.run_in_executor()`, qui **ne propage pas**
   les ContextVar, contrairement à `asyncio.to_thread()`.

Le second point est le piège : les tests de #T296 appelaient `record_usage()`
dans la même tâche et passaient au vert, pendant que la colonne restait NULL en
production. Ce test-ci lit la ContextVar DEPUIS LE THREAD du provider, comme le
vrai code.
"""
import asyncio

import pytest

from core.agent_trace import lire_agent_courant
from services.pipeline_service import run_fast_path


class _ProviderTemoin:
    """Provider factice : enregistre ce que la ContextVar vaut dans SON thread."""

    def __init__(self):
        self.agent_vu_dans_le_thread = "<jamais appelé>"
        self.thread_est_different = None

    def generate(self, system_prompt, user_prompt, session_id=None):
        import threading

        self.agent_vu_dans_le_thread = lire_agent_courant()
        self.thread_est_different = threading.current_thread() is not threading.main_thread()
        return "réponse factice"


class _GatewayTemoin:
    def __init__(self, provider):
        self._provider = provider

    def get_provider(self, nom):
        return self._provider

    def get_provider_for_tier(self, tier, config):
        return None, self._provider


class _TokenTrackerTemoin:
    def init_session(self, session_id, objective):
        pass


@pytest.mark.asyncio
async def test_l_etiquette_fast_path_atteint_le_thread_du_provider():
    """Sans `to_thread`, l'étiquette se perd et `agent_name` reste NULL en base."""
    provider = _ProviderTemoin()

    reponse = await run_fast_path(
        user_prompt="bonjour",
        session_id="s_t301",
        gateway=_GatewayTemoin(provider),
        token_tracker=_TokenTrackerTemoin(),
        fast_path_cache={},
    )

    assert reponse == "réponse factice", reponse
    assert provider.thread_est_different, (
        "le provider doit bien s'exécuter hors du thread principal — "
        "sinon ce test ne prouve rien sur la propagation du contexte"
    )
    assert provider.agent_vu_dans_le_thread == "fast_path", (
        f"étiquette perdue au passage du thread : {provider.agent_vu_dans_le_thread!r}. "
        "C'est le symptôme de `run_in_executor` (qui ne propage pas les ContextVar) "
        "à la place de `asyncio.to_thread`."
    )


@pytest.mark.asyncio
async def test_le_pont_de_thread_choisi_propage_bien_le_contexte():
    """Garde-fou : documente la différence des deux passerelles, mesurée le 11/08.

    Si un jour `to_thread` cessait de propager, ou si quelqu'un revenait à
    `run_in_executor` par souci d'uniformité, ce test dirait pourquoi c'est faux.
    """
    from core.agent_trace import poser_agent_courant

    poser_agent_courant("sentinelle")

    def _lire():
        return lire_agent_courant()

    via_to_thread = await asyncio.to_thread(_lire)
    via_executor = await asyncio.get_running_loop().run_in_executor(None, _lire)

    assert via_to_thread == "sentinelle", "asyncio.to_thread doit propager le contexte"
    assert via_executor is None, (
        "run_in_executor ne propage pas le contexte — si ce comportement change, "
        "le commentaire de services/pipeline_service.py doit être mis à jour"
    )
