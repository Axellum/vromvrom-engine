"""
core/langfuse_bridge.py — Axe 1 — Bridge Langfuse pour l'observabilité LLM avancée.

Singleton qui envoie les traces (sessions, agents, appels LLM) vers une instance
Langfuse self-hosted. Si les variables d'environnement ne sont pas configurées,
toutes les méthodes sont des no-op silencieux (mode dégradé).

Variables d'environnement requises (optionnelles) :
    LANGFUSE_PUBLIC_KEY — Clé publique du projet Langfuse
    LANGFUSE_SECRET_KEY — Clé secrète du projet Langfuse
    LANGFUSE_HOST       — URL du serveur Langfuse (ex: http://${LM_STUDIO_HOST:-192.168.1.x}:3000)
"""

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


class LangfuseBridge:
    """
    Bridge singleton vers Langfuse.
    
    Architecture de tracing :
        Trace (= 1 session/exécution)
          └─ Span (= 1 agent)
               └─ Generation (= 1 appel LLM)
    
    Utilisation :
        bridge = LangfuseBridge.get_instance()
        trace = bridge.start_trace(session_id, "Objectif de la tâche")
        span = bridge.start_span(trace, "planner")
        bridge.log_generation(span, "gemini-3.5-flash", 120, 340, 0.0001, 2.3)
        bridge.end_span(span, "success")
        bridge.end_trace(trace)
    """

    _instance = None

    @classmethod
    def get_instance(cls) -> 'LangfuseBridge':
        """Retourne l'instance singleton du bridge."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        """Initialise le client Langfuse si les clés sont disponibles."""
        self._client = None
        self._enabled = False
        self._active_traces: dict[str, Any] = {}   # session_id → trace
        self._active_spans: dict[str, Any] = {}     # agent_name → span

        # Tentative d'initialisation
        public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
        secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
        host = os.environ.get("LANGFUSE_HOST", "http://${LM_STUDIO_HOST:-192.168.1.x}:3000")

        if not public_key or not secret_key:
            logger.info(
                "[LANGFUSE] Variables LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY non définies. "
                "Bridge désactivé (mode no-op). Pour activer, ajoutez les clés dans .env"
            )
            return

        try:
            from langfuse import Langfuse
            self._client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=host
            )
            self._enabled = True
            logger.info(f"[LANGFUSE] ✅ Bridge activé. Serveur : {host}")
        except ImportError:
            logger.warning(
                "[LANGFUSE] Package 'langfuse' non installé. "
                "Installez-le avec : pip install langfuse"
            )
        except Exception as e:
            logger.error(f"[LANGFUSE] Erreur lors de l'initialisation : {e}")

    @property
    def enabled(self) -> bool:
        """True si le bridge est actif et connecté à Langfuse."""
        return self._enabled

    # ─── Traces (= sessions d'exécution) ───

    def start_trace(self, session_id: str, objective: str, user_id: str = "moteur-ia") -> Any | None:
        """
        Démarre une trace Langfuse pour une session d'exécution.
        
        Args:
            session_id: Identifiant unique de la session (ex: UUID)
            objective: Objectif de la tâche en langage naturel
            user_id: Identifiant de l'utilisateur
            
        Returns:
            Objet trace Langfuse (ou None si désactivé)
        """
        if not self._enabled:
            return None

        try:
            trace = self._client.trace(
                name=f"session-{session_id[:8]}",
                session_id=session_id,
                user_id=user_id,
                input={"objective": objective},
                metadata={"engine_version": "5.4"}
            )
            self._active_traces[session_id] = trace
            logger.debug(f"[LANGFUSE] Trace démarrée pour session {session_id[:8]}")
            return trace
        except Exception as e:
            logger.warning(f"[LANGFUSE] Erreur start_trace : {e}")
            return None

    def end_trace(self, session_id: str, status: str = "success", output: str = None):
        """
        Termine une trace Langfuse.
        
        Args:
            session_id: Identifiant de la session
            status: 'success' ou 'error'
            output: Résultat final de l'exécution
        """
        if not self._enabled:
            return

        trace = self._active_traces.pop(session_id, None)
        if trace:
            try:
                trace.update(
                    output=output or status,
                    metadata={"final_status": status}
                )
                logger.debug(f"[LANGFUSE] Trace terminée pour session {session_id[:8]} ({status})")
            except Exception as e:
                logger.warning(f"[LANGFUSE] Erreur end_trace : {e}")

    # ─── Spans (= agents) ───

    def start_span(self, session_id: str, agent_name: str) -> Any | None:
        """
        Démarre un span Langfuse pour un agent au sein d'une trace.
        
        Args:
            session_id: Identifiant de la session (pour retrouver la trace parente)
            agent_name: Nom technique de l'agent (planner, executor, etc.)
            
        Returns:
            Objet span Langfuse (ou None si désactivé)
        """
        if not self._enabled:
            return None

        trace = self._active_traces.get(session_id)
        if not trace:
            logger.debug(f"[LANGFUSE] Pas de trace active pour session {session_id[:8]} — span ignoré")
            return None

        try:
            span = trace.span(
                name=agent_name,
                input={"agent": agent_name, "start_time": time.time()},
                metadata={"agent_type": agent_name}
            )
            # Clé composite pour supporter le même agent appelé plusieurs fois
            span_key = f"{session_id}:{agent_name}"
            self._active_spans[span_key] = {
                "span": span,
                "start_time": time.time()
            }
            logger.debug(f"[LANGFUSE] Span démarré : {agent_name}")
            return span
        except Exception as e:
            logger.warning(f"[LANGFUSE] Erreur start_span : {e}")
            return None

    def end_span(self, session_id: str, agent_name: str, status: str = "success", output: str = None):
        """
        Termine un span Langfuse pour un agent.
        
        Args:
            session_id: Identifiant de la session
            agent_name: Nom technique de l'agent
            status: 'success' ou 'error'
            output: Résultat de l'agent
        """
        if not self._enabled:
            return

        span_key = f"{session_id}:{agent_name}"
        span_data = self._active_spans.pop(span_key, None)
        if span_data:
            try:
                duration = time.time() - span_data["start_time"]
                span_data["span"].update(
                    output=output or status,
                    metadata={
                        "status": status,
                        "duration_seconds": round(duration, 3)
                    }
                )
                span_data["span"].end()
                logger.debug(f"[LANGFUSE] Span terminé : {agent_name} ({status}, {duration:.2f}s)")
            except Exception as e:
                logger.warning(f"[LANGFUSE] Erreur end_span : {e}")

    # ─── Generations (= appels LLM) ───

    def log_generation(
        self,
        session_id: str,
        agent_name: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float = 0.0,
        latency_s: float = None,
        input_text: str = None,
        output_text: str = None
    ):
        """
        Enregistre un appel LLM dans Langfuse comme une Generation.
        
        Args:
            session_id: Identifiant de la session
            agent_name: Agent qui a fait l'appel (pour rattacher au bon span)
            model: Nom du modèle LLM utilisé
            prompt_tokens: Nombre de tokens en entrée
            completion_tokens: Nombre de tokens en sortie
            cost_usd: Coût estimé ou réel en USD
            latency_s: Latence de l'appel en secondes
            input_text: Prompt envoyé (optionnel, pour debug)
            output_text: Réponse reçue (optionnel, pour debug)
        """
        if not self._enabled:
            return

        # Chercher le span parent de cet agent
        span_key = f"{session_id}:{agent_name}"
        span_data = self._active_spans.get(span_key)

        # Si pas de span, chercher la trace directement
        parent = None
        if span_data:
            parent = span_data["span"]
        else:
            parent = self._active_traces.get(session_id)

        if not parent:
            # Pas de contexte Langfuse actif — enregistrement ignoré silencieusement
            return

        try:
            parent.generation(
                name=f"llm-{model}",
                model=model,
                input=input_text[:500] if input_text else None,  # Tronquer pour éviter la surcharge
                output=output_text[:500] if output_text else None,
                usage={
                    "input": prompt_tokens,
                    "output": completion_tokens,
                    "total": prompt_tokens + completion_tokens
                },
                metadata={
                    "cost_usd": round(cost_usd, 8),
                    "latency_s": round(latency_s, 3) if latency_s else None
                }
            )
            logger.debug(
                f"[LANGFUSE] Generation enregistrée : {model} "
                f"({prompt_tokens}in/{completion_tokens}out, ${cost_usd:.6f})"
            )
        except Exception as e:
            logger.warning(f"[LANGFUSE] Erreur log_generation : {e}")

    # ─── Flush (envoi des données en attente) ───

    def flush(self):
        """
        Force l'envoi des données en attente vers Langfuse, SANS jamais bloquer.

        [#T272] Le client Langfuse est synchrone et son `flush()` n'a pas de
        borne de temps : quand le réseau ne répond pas, l'appel attend — et toute
        la fin de session avec lui, puisque `Engine._finalize_session()` appelle
        `end_trace()` puis `flush()` à CHAQUE session.

        Mesuré le 11/08 par capture de pile `faulthandler` : la suite de tests
        est passée de 16 secondes à plus de 10 MINUTES sur un seul test, bloquée
        exactement ici. En test c'était un garde-fou réseau ; en production ce
        sera une coupure Internet ou une panne du service. Le `try/except` ne
        protégeait de rien — une ATTENTE n'est pas une exception.
        """
        self._appel_borne(lambda: self._client.flush(), "flush")

    def shutdown(self):
        """Ferme proprement le client Langfuse (borné, cf. `flush`)."""
        if not (self._enabled and self._client):
            return

        def _fermeture():
            self._client.flush()
            self._client.shutdown()

        if self._appel_borne(_fermeture, "shutdown"):
            logger.info("[LANGFUSE] Client arrêté proprement.")

    def _appel_borne(self, action, nom: str) -> bool:
        """
        [#T272] Exécute `action` dans un thread et n'attend que `_timeout_flush()`.

        Au-delà, on journalise et on rend la main : le thread est `daemon`, il ne
        retiendra pas le processus. **De la télémétrie perdue ne vaut pas une
        session bloquée** — c'est tout l'arbitrage, et c'est pourquoi le délai
        par défaut est court.

        Returns:
            True si l'action s'est terminée dans les temps, False sinon.
        """
        import threading

        if not (self._enabled and self._client):
            return False

        erreur: list[Exception] = []

        def _executer():
            try:
                action()
            except Exception as e:  # noqa: BLE001 — remontée au thread appelant
                erreur.append(e)

        thread = threading.Thread(target=_executer, name=f"langfuse-{nom}", daemon=True)
        thread.start()
        thread.join(timeout=self._timeout_flush())

        if thread.is_alive():
            logger.warning(
                f"[LANGFUSE] {nom} abandonné après {self._timeout_flush()}s "
                f"(serveur injoignable ?) — la session continue sans attendre."
            )
            return False
        if erreur:
            logger.warning(f"[LANGFUSE] Erreur {nom} : {erreur[0]}")
            return False
        logger.debug(f"[LANGFUSE] {nom} effectué.")
        return True

    @staticmethod
    def _timeout_flush() -> float:
        """
        [#T272] Délai maximal accordé à un envoi Langfuse.

        Court par défaut (5 s) et c'est délibéré : ce sont des traces
        d'observabilité, pas de la donnée métier. Les perdre est un désagrément ;
        retarder la fin de chaque session est un défaut de production.
        Surchargeable par `MOTEUR_LANGFUSE_FLUSH_TIMEOUT_S`.
        """
        brut = os.environ.get("MOTEUR_LANGFUSE_FLUSH_TIMEOUT_S")
        if brut:
            try:
                return max(0.1, float(brut))
            except ValueError:
                logger.warning(
                    f"[LANGFUSE] MOTEUR_LANGFUSE_FLUSH_TIMEOUT_S illisible ({brut!r}) — défaut retenu."
                )
        return 5.0
