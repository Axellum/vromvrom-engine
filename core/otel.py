"""
core/otel.py — Tracer OpenTelemetry pour les appels LLM.

Fournit un tracer OTLP configuré depuis les variables d'environnement :
    OTEL_EXPORTER_OTLP_ENDPOINT  — URL du collecteur (Langfuse, Phoenix, Jaeger…)
    OTEL_EXPORTER_OTLP_HEADERS   — Headers HTTP (ex : "Authorization=Bearer xxx")
    OTEL_SERVICE_NAME             — Nom du service (défaut : "moteur-agents")

Si les variables ne sont pas définies ou si le SDK n'est pas installé, toutes
les opérations de tracing sont des no-op silencieux.

Conventions de nommage des attributs (GenAI OTel SemConv + custom llm.*) :
    gen_ai.system            — famille du provider (deepseek, gemini, openai…)
    gen_ai.request.model     — identifiant du modèle
    gen_ai.usage.input_tokens  — tokens d'entrée
    gen_ai.usage.output_tokens — tokens de sortie
    llm.latency_ms           — durée de l'appel en ms
    llm.fallback_index       — rang dans la cascade (0 = primaire)
    llm.fallback_triggered   — True si rang > 0
    llm.cache_hit            — True si réponse issue du cache sémantique
    llm.cb_state             — état du circuit breaker avant l'appel
    llm.error                — classe d'exception en cas d'échec
"""

import logging
import os
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Initialisation du tracer ─────────────────────────────────────────────────

_tracer = None
_setup_done = False


def _setup_tracer():
    """Configure le provider OTLP si les variables d'environnement sont présentes."""
    global _tracer, _setup_done
    if _setup_done:
        return _tracer
    _setup_done = True

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    service_name = os.getenv("OTEL_SERVICE_NAME", "moteur-agents")

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider()

        if os.getenv("OTEL_EXPORTER_GCP", "").lower() == "true":
            try:
                from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
                exporter = CloudTraceSpanExporter()
                provider.add_span_processor(BatchSpanProcessor(exporter))
                logger.info(f"[OTEL] Tracing GCP natif actif (service: {service_name})")
            except ImportError:
                logger.error("[OTEL] opentelemetry-exporter-gcp-trace absent — no-op.")

        elif endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                headers_raw = os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")
                headers = {}
                for part in headers_raw.split(","):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        headers[k.strip()] = v.strip()
                exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)
                provider.add_span_processor(BatchSpanProcessor(exporter))
                logger.info(f"[OTEL] Tracing OTLP agnostique actif → {endpoint} (service: {service_name})")
            except ImportError:
                logger.debug("[OTEL] opentelemetry-exporter-otlp-proto-http absent — no-op.")
        else:
            logger.debug("[OTEL] OTEL_EXPORTER_OTLP_ENDPOINT non défini — tracing no-op.")

        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name, schema_url="https://opentelemetry.io/schemas/1.24.0")

    except ImportError:
        logger.debug("[OTEL] opentelemetry-sdk absent — tracing no-op.")
        _tracer = None

    return _tracer


def get_tracer():
    """Retourne le tracer OTel (no-op si non configuré)."""
    return _setup_tracer()


# ─── Contexte de span ─────────────────────────────────────────────────────────

@contextmanager
def llm_span(
    model_name: str,
    provider_system: Optional[str] = None,
    fallback_index: int = 0,
    cache_hit: bool = False,
    cb_state: str = "CLOSED",
):
    """
    Context manager créant un span OTel pour un appel LLM.

    Usage :
        with llm_span("gemini-2.5-flash", provider_system="gemini") as span:
            result = await provider.generate_async(...)
            set_span_tokens(span, input_tokens=120, output_tokens=340)
    """
    tracer = get_tracer()
    if tracer is None:
        # No-op : yield un objet factice avec les méthodes attendues
        yield _NoOpSpan()
        return

    from opentelemetry import trace as _trace
    from opentelemetry.trace import StatusCode

    with tracer.start_as_current_span("llm.call") as span:
        span.set_attribute("gen_ai.request.model", model_name)
        if provider_system:
            span.set_attribute("gen_ai.system", provider_system)
        span.set_attribute("llm.fallback_index", fallback_index)
        span.set_attribute("llm.fallback_triggered", fallback_index > 0)
        span.set_attribute("llm.cache_hit", cache_hit)
        span.set_attribute("llm.cb_state", cb_state)
        try:
            yield span
        except Exception as exc:
            span.set_attribute("llm.error", type(exc).__name__)
            span.set_status(StatusCode.ERROR, str(exc))
            raise


def set_span_tokens(span, input_tokens: int = 0, output_tokens: int = 0, latency_ms: float = 0.0):
    """Enrichit un span OTel avec les métriques de tokens et la latence."""
    if span is None or isinstance(span, _NoOpSpan):
        return
    try:
        if input_tokens:
            span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
        if output_tokens:
            span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
        if latency_ms:
            span.set_attribute("llm.latency_ms", round(latency_ms, 1))
    except Exception:
        pass


# ─── No-op span ───────────────────────────────────────────────────────────────

class _NoOpSpan:
    """Span factice retourné quand OTel n'est pas configuré."""
    def set_attribute(self, key, value):
        pass
    def set_status(self, status, description=""):
        pass
    def record_exception(self, exc):
        pass
