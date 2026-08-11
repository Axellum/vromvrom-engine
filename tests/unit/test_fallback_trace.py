"""Tests de la traçabilité du modèle qui a répondu dans FallbackProvider (#T288).

FallbackProvider choisit le modèle au moment de l'appel (parcours des candidats
jusqu'au premier qui aboutit). Ces tests vérifient que l'appelant peut lire,
après son appel et dans le même contexte, le nom du candidat gagnant :

- cascade dont le 1er candidat échoue → le 2ᵉ modèle est exposé, pas le 1er ;
- exécution CONCURRENTE (asyncio.gather) → chaque tâche lit LE SIEN ;
- appel servi par le cache sémantique → aucun modèle n'est prétendu ;
- variantes structurées (sync + async) et échec total → trace vierge.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.llm.circuit_breaker import CircuitBreaker
from core.llm.fallback_trace import lire_modele_repondu, marquer_modele_repondu
from core.llm.providers.deepseek import FallbackProvider


def _make_provider(response="ok", fail=False):
    """Provider factice, sync + async, comme dans test_fallback_provider_async.py."""
    p = MagicMock()
    if fail:
        p.generate = MagicMock(side_effect=RuntimeError("boom"))
        p.generate_async = AsyncMock(side_effect=RuntimeError("boom"))
        p.generate_structured = MagicMock(side_effect=RuntimeError("boom"))
        p.generate_structured_async = AsyncMock(side_effect=RuntimeError("boom"))
    else:
        p.generate = MagicMock(return_value=response)
        p.generate_async = AsyncMock(return_value=response)
        p.generate_structured = MagicMock(return_value={"result": response})
        p.generate_structured_async = AsyncMock(return_value={"result": response})
    return p


@pytest.fixture(autouse=True)
def _trace_vierge_et_circuits_fermes():
    """Chaque test repart d'une trace vierge et de circuits fermés."""
    marquer_modele_repondu(None)
    CircuitBreaker._registry.clear()
    yield
    marquer_modele_repondu(None)
    CircuitBreaker._registry.clear()


# ── Bascule de cascade : le modèle exposé est le GAGNANT, pas le premier ─────

def test_generate_sync_expose_le_modele_apres_bascule():
    """1er candidat en échec → l'appelant lit le nom du 2ᵉ, pas « tier-moyen »."""
    p1 = _make_provider(fail=True)
    p2 = _make_provider("Réponse du modèle de secours, assez longue pour le seuil.")
    fb = FallbackProvider([("modele-echec", p1), ("modele-secours", p2)])

    res = fb.generate("sys", "user", use_semantic_cache=False)

    assert "secours" in res
    assert lire_modele_repondu() == "modele-secours"


@pytest.mark.asyncio
async def test_generate_async_expose_le_modele_apres_bascule():
    p1 = _make_provider(fail=True)
    p2 = _make_provider("Réponse du modèle de secours, assez longue pour le seuil.")
    fb = FallbackProvider([("modele-echec", p1), ("modele-secours", p2)])

    res = await fb.generate_async("sys", "user", use_semantic_cache=False)

    assert "secours" in res
    assert lire_modele_repondu() == "modele-secours"


# ── CONCURRENCE : le test qui donne sa valeur à la PR ────────────────────────

@pytest.mark.asyncio
async def test_concurrence_chaque_tache_lit_son_modele():
    """Deux cascades concurrentes (asyncio.gather) : chacun lit LE SIEN.

    Avec un attribut d'instance (contre-exemple #T288), les deux tâches
    liraient le dernier modèle écrit — au moins une assertion échouerait. La
    ContextVar, copiée par tâche au moment de sa création, garantit
    l'isolation : c'est exactement le mode d'exécution du DAG
    (asyncio.create_task par nœud, core/dag_runner.py).
    """
    fb_alpha = FallbackProvider([
        ("modele-alpha", _make_provider("Réponse du modèle alpha, assez longue.")),
    ])
    fb_beta = FallbackProvider([
        ("modele-beta", _make_provider("Réponse du modèle beta, assez longue.")),
    ])

    async def appeler(fb: FallbackProvider, attendu: str) -> tuple[bool, str | None]:
        res = await fb.generate_async("sys", "user", use_semantic_cache=False)
        return attendu in res, lire_modele_repondu()

    (ok_alpha, modele_alpha), (ok_beta, modele_beta) = await asyncio.gather(
        appeler(fb_alpha, "alpha"),
        appeler(fb_beta, "beta"),
    )

    assert ok_alpha and ok_beta
    assert modele_alpha == "modele-alpha"
    assert modele_beta == "modele-beta"


# ── Cache sémantique : aucun modèle ne peut être prétendu ────────────────────

class _FauxCache:
    """Cache sémantique factice (hit systématique), sans ChromaDB ni réseau."""

    def __init__(self, reponse: str):
        self._reponse = reponse

    @property
    def enabled(self) -> bool:
        return True

    def get(self, prompt: str) -> str:
        return self._reponse

    def put(self, prompt: str, response: str, model: str | None = None) -> None:
        pass


def test_cache_semantique_ne_pretend_pas_qu_un_modele_a_repondu(monkeypatch):
    """Un hit du cache n'expose AUCUN modèle, même après un appel réel précédent."""
    import core.semantic_cache as sc_mod

    # 1er appel réel : le modèle qui répond est bien enregistré…
    reel = _make_provider("Réponse du modèle réel, suffisamment longue pour le seuil.")
    fb_reel = FallbackProvider([("modele-reel", reel)])
    fb_reel.generate("sys", "question", use_semantic_cache=False)
    assert lire_modele_repondu() == "modele-reel"

    # …mais le 2ᵉ appel, servi par le cache sémantique, ne doit PAS réexposer
    # ce modèle : la trace repart de zéro à chaque appel (#T288).
    cache = _FauxCache("Réponse servie par le cache, aucun modèle n'a répondu.")
    monkeypatch.setattr(sc_mod, "get_semantic_cache", lambda: cache)
    jamais_appele = _make_provider("ce provider ne doit jamais servir")
    fb_cache = FallbackProvider([("modele-jamais-appele", jamais_appele)])
    fb_cache.generate("sys", "question")

    assert jamais_appele.generate.call_count == 0  # le cache a bien servi l'appel
    assert lire_modele_repondu() is None


# ── Variantes structurées et échec total ─────────────────────────────────────

def test_generate_structured_expose_le_modele():
    fb = FallbackProvider([("modele-structure", _make_provider("structuré"))])

    res = fb.generate_structured("sys", "user", schema={})

    assert res == {"result": "structuré"}
    assert lire_modele_repondu() == "modele-structure"


@pytest.mark.asyncio
async def test_generate_structured_async_expose_le_modele():
    fb = FallbackProvider([("modele-structure-async", _make_provider("structuré"))])

    res = await fb.generate_structured_async("sys", "user", schema={})

    assert res == {"result": "structuré"}
    assert lire_modele_repondu() == "modele-structure-async"


def test_echec_total_laisse_la_trace_vierge():
    """Aucun candidat ne répond : la trace reste None, sans résidu d'appel passé."""
    fb = FallbackProvider([("modele-mort", _make_provider(fail=True))])

    with pytest.raises(RuntimeError):
        fb.generate("sys", "user", use_semantic_cache=False)

    assert lire_modele_repondu() is None
