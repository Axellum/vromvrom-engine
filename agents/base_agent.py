"""
agents/base_agent.py — Interface abstraite commune à tous les agents du moteur.

Un seul contrat : `invoke(payload: TaskPayload) -> StateUpdate`. Tous les agents
métier (executor, planner, reviewer, ha_agent, dreamer, antigravity, tool_maker,
prompt_engineer) en héritent directement ou via `ExecutorAgent`.

[#T296] Chaque `invoke()` est automatiquement encadré par la pose du nom de
l'agent dans une ContextVar (`core/agent_trace.py`), lue plus bas par
`token_tracker.record_usage()` pour renseigner `token_usage.agent_name`.
L'enveloppe est posée ici, sur la classe de base, plutôt que sur les quinze
call-sites de `.invoke()` répartis dans `dag_runner`, `swarm_dispatcher`,
`engine`, `healing` et `review_loop` : un agent qui oublierait de le faire
redeviendrait invisible dans la comptabilité, et c'est exactement le défaut
qu'on corrige.
"""
import functools
import logging
from abc import ABC, abstractmethod

from core.agent_trace import poser_agent_courant, restaurer_agent_courant
from core.state import StateUpdate, TaskPayload

logger = logging.getLogger("base_agent")


class BaseAgent(ABC):
    """
    Classe abstraite fondamentale (Single Responsibility Principle).
    Définit l'interface standard pour tous les agents du moteur.
    """

    def __init__(self, name: str, system_prompt: str):
        """
        Initialise l'agent avec son nom unique et son prompt système (règles métier).
        """
        self.name = name
        self.system_prompt = system_prompt

    def __init_subclass__(cls, **kwargs):
        """
        [#T296] Enveloppe l'`invoke()` de chaque sous-classe pour tracer l'agent courant.

        Seules les classes qui définissent leur PROPRE `invoke` sont enveloppées :
        une sous-classe qui hérite de celui de son parent (cas des agents dérivés
        d'`ExecutorAgent`) réutilise l'enveloppe déjà posée, sans double pose.
        """
        super().__init_subclass__(**kwargs)

        fonction = cls.__dict__.get("invoke")
        if fonction is None or getattr(fonction, "_trace_agent_posee", False):
            return

        @functools.wraps(fonction)
        async def invoke_trace(self, payload, *args, **kwargs_appel):
            jeton = poser_agent_courant(getattr(self, "name", cls.__name__))
            # [#T298] Heartbeat de vivacité : chaque étape d'agent réelle prouve
            # que la session est encore vivante, donc le nettoyage des zombies ne
            # doit pas y toucher. Posé ici, sur la classe de base, comme le trace
            # #T296 : tous les call-sites (engine, dag_runner, healing,
            # review_loop, swarm_dispatcher) passent par cette enveloppe — aucun
            # chemin d'exécution ne peut l'oublier. Non bloquant par conception.
            try:
                _sid = (payload.metadata or {}).get("session_id")
                if _sid:
                    from core.session_history import record_session_activity
                    record_session_activity(_sid)
            except Exception:
                pass
            # [#T422] Trace pas-à-pas. La table `agent_steps` existait depuis
            # l'origine — schéma, index, purge de rétention — et comptait
            # 0 ligne sur la production au 19/08 : rien ne l'écrivait. Pour
            # reconstituer la campagne autonome des 18–19/08, il a fallu croiser
            # les branches Git, les rapports de cycle et le journal systemd.
            # Posée ici pour la même raison que #T296 et #T298 : les quinze
            # call-sites de `.invoke()` passent tous par cette enveloppe.
            # Écriture hors boucle d'événements (`to_thread`) et best effort —
            # une trace qui ferait échouer l'agent qu'elle observe serait pire
            # que pas de trace.
            import time as _time
            _debut = _time.time()
            _resultat = None
            try:
                _resultat = await fonction(self, payload, *args, **kwargs_appel)
                return _resultat
            finally:
                restaurer_agent_courant(jeton)
                try:
                    _sid_trace = (payload.metadata or {}).get("session_id")
                    if _sid_trace:
                        import asyncio as _asyncio

                        from core.agent_steps import enregistrer_etape
                        await _asyncio.to_thread(
                            enregistrer_etape,
                            session_id=_sid_trace,
                            agent_name=getattr(self, "name", cls.__name__),
                            objectif=getattr(payload, "task_objective", ""),
                            task_id=getattr(payload, "task_id", None),
                            statut=getattr(_resultat, "status", "interrompu"),
                            erreur=getattr(_resultat, "error_message", None),
                            extrait_resultat=getattr(_resultat, "result_data", None),
                            debut=_debut,
                        )
                except Exception as _err_trace:
                    logger.debug(f"[BASE_AGENT] Trace d'étape non posée : {_err_trace}")

        invoke_trace._trace_agent_posee = True
        cls.invoke = invoke_trace

    @abstractmethod
    async def invoke(self, payload: TaskPayload) -> StateUpdate:
        """
        Méthode principale d'exécution.
        Prend un Payload (contexte isolé) et retourne un Update (delta d'état).
        Doit être implémentée par chaque agent spécialisé.
        """
        pass
