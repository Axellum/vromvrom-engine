"""
Tests T81 — Instrumentation OpenTelemetry des appels LLM.

Vérifie que :
  1. llm_span crée bien un span avec les attributs attendus
  2. set_span_tokens enrichit le span correctement
  3. Les échecs marquent le span ERROR avec llm.error
  4. FallbackProvider émet un span par tentative (via in-memory SDK)
"""
import asyncio
import pytest

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from core.otel import llm_span, set_span_tokens, _NoOpSpan


# ─── Fixture : exporter en mémoire ───────────────────────────────────────────

@pytest.fixture()
def otel_exporter(monkeypatch):
    """Configure un TracerProvider en mémoire et renvoie l'exporter."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    import core.otel as otel_module
    real_tracer = provider.get_tracer("test-tracer")
    monkeypatch.setattr(otel_module, "_tracer", real_tracer)
    monkeypatch.setattr(otel_module, "_setup_done", True)

    yield exporter

    exporter.clear()
    trace.set_tracer_provider(TracerProvider())


# ─── Tests ────────────────────────────────────────────────────────────────────

def test_llm_span_creates_span_with_attributes(otel_exporter):
    """llm_span doit créer un span 'llm.call' avec les attributs de base."""
    with llm_span(
        model_name="deepseek-chat",
        provider_system="deepseek",
        fallback_index=0,
        cb_state="CLOSED",
    ) as span:
        set_span_tokens(span, input_tokens=50, output_tokens=120, latency_ms=350.5)

    spans = otel_exporter.get_finished_spans()
    assert len(spans) == 1

    s = spans[0]
    assert s.name == "llm.call"
    assert s.attributes.get("gen_ai.request.model") == "deepseek-chat"
    assert s.attributes.get("gen_ai.system") == "deepseek"
    assert s.attributes.get("llm.fallback_index") == 0
    assert s.attributes.get("llm.fallback_triggered") is False
    assert s.attributes.get("llm.cb_state") == "CLOSED"
    assert s.attributes.get("gen_ai.usage.input_tokens") == 50
    assert s.attributes.get("gen_ai.usage.output_tokens") == 120
    assert s.attributes.get("llm.latency_ms") == 350.5


def test_llm_span_fallback_index_sets_triggered(otel_exporter):
    """Un fallback_index > 0 doit marquer llm.fallback_triggered = True."""
    with llm_span(model_name="gemini-flash", fallback_index=2) as span:
        set_span_tokens(span, latency_ms=100.0)

    spans = otel_exporter.get_finished_spans()
    s = spans[0]
    assert s.attributes.get("llm.fallback_index") == 2
    assert s.attributes.get("llm.fallback_triggered") is True


def test_llm_span_records_error_on_exception(otel_exporter):
    """Une exception dans le contexte doit marquer le span ERROR avec llm.error."""
    from opentelemetry.trace import StatusCode

    with pytest.raises(RuntimeError):
        with llm_span(model_name="ollama-local") as span:
            raise RuntimeError("Connexion refusée")

    spans = otel_exporter.get_finished_spans()
    s = spans[0]
    assert s.attributes.get("llm.error") == "RuntimeError"
    assert s.status.status_code == StatusCode.ERROR


def test_set_span_tokens_noop_on_noop_span():
    """set_span_tokens ne doit pas planter sur un _NoOpSpan."""
    noop = _NoOpSpan()
    set_span_tokens(noop, input_tokens=10, output_tokens=20, latency_ms=50.0)  # pas d'exception


@pytest.mark.asyncio
async def test_fallback_provider_emits_span_per_attempt(otel_exporter):
    """FallbackProvider doit créer un span OTel pour chaque tentative de provider."""
    from unittest.mock import AsyncMock
    from core.llm.providers.deepseek import FallbackProvider
    from core.llm.circuit_breaker import CircuitBreaker

    # Nettoyer le registre pour ce test
    CircuitBreaker._registry.pop("model-primary", None)
    CircuitBreaker._registry.pop("model-fallback", None)

    mock_primary = AsyncMock()
    mock_primary.generate_async.side_effect = RuntimeError("Provider down")

    mock_fallback = AsyncMock()
    mock_fallback.generate_async.return_value = "Réponse de secours — provider de fallback opérationnel."

    provider = FallbackProvider([
        ("model-primary", mock_primary),
        ("model-fallback", mock_fallback),
    ])

    result = await provider.generate_async("sys", "user", use_semantic_cache=False)
    assert "fallback" in result.lower()

    spans = otel_exporter.get_finished_spans()
    # Au moins 2 spans : un pour la tentative primaire (échec) + un pour le fallback
    assert len(spans) >= 2

    models = [s.attributes.get("gen_ai.request.model") for s in spans]
    assert "model-primary" in models
    assert "model-fallback" in models

    # Le span fallback doit avoir fallback_triggered = True
    fallback_span = next(s for s in spans if s.attributes.get("gen_ai.request.model") == "model-fallback")
    assert fallback_span.attributes.get("llm.fallback_triggered") is True
