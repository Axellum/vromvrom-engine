"""test_stream_discussion_outils_t346.py — #T346, outils sur le chemin streamé.

Le mode chat streamé (98 % du trafic) appelait le provider en direct, SANS
aucun outil : ceux-ci ne vivaient que sur le chemin non streamé
(`run_vocal_tool_loop`). Ce test couvre la réutilisation de cette boucle sur
le chemin streamé — pas de seconde boucle d'outils — et les événements de
cycle de vie attendus par `ToolCallDisplay` (appel → résultat ou erreur, avec
durée).

Aucun appel réseau réel ici : les providers sont des doubles, le dispatch
d'outils est mocké, la sonde d'hôte aussi, et `inject_project_context=False`
évite tout appel RAG/BDD.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from core.vocal_tools import run_vocal_tool_loop
from services import pipeline_service

_APPEL_OUTIL = {
    "role": "assistant",
    "content": None,
    "tool_calls": [{
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "ha_get_state",
            "arguments": '{"entity_id": "sensor.salon_temperature"}',
        },
    }],
}

_REPONSE_FINALE = {
    "role": "assistant",
    "content": "Il fait 21,5 degrés dans le salon.",
}


class OpenAICompatibleProvider:
    """Double factice : le nom de classe déclenche
    `provider_supports_openai_tools`, comme le vrai provider factorisé."""

    def __init__(self, reponses):
        self.base_url = "https://api.cerebras.ai/v1/chat/completions"
        self.reponses = list(reponses)
        self.appels: list[list[dict]] = []

    def generate(self, system_prompt, user_prompt, **kwargs):
        self.appels.append([dict(m) for m in kwargs.get("messages") or []])
        return self.reponses.pop(0)

    def generate_stream(self, system_prompt, user_prompt, **kwargs):
        # Ne doit PAS être emprunté quand la boucle d'outils répond.
        yield {"token": "FLUX_DIRECT", "done": False, "usage": None}
        yield {"token": "", "done": True, "usage": None}


class _ProviderSansOutils:
    """Double factice NON détecté compatible outils (nom de classe neutre)."""

    def __init__(self):
        self.base_url = "https://generativelanguage.googleapis.com/v1"

    def generate_stream(self, system_prompt, user_prompt, **kwargs):
        yield {"token": "Bonjour.", "done": False, "usage": None}
        yield {"token": "", "done": True, "usage": None}


class _GatewayFactice:
    def __init__(self, providers):
        self._providers = providers

    def get_provider(self, name):
        if name not in self._providers:
            raise ValueError(f"Provider LLM inconnu : {name}")
        return self._providers[name]

    @staticmethod
    def _est_hote_local(host):
        from core.llm_gateway import LLMGateway

        return LLMGateway._est_hote_local(host)


def _patch_communs():
    return (
        patch.object(
            pipeline_service, "_fast_path_hote_muet", AsyncMock(return_value=False)
        ),
        patch.object(pipeline_service, "FAST_PATH_PROVIDERS", ["candidat"]),
        patch.object(
            pipeline_service, "_persist_fast_path_async", new_callable=AsyncMock
        ),
    )


async def _drainer(gateway, session_id):
    evenements = []
    p1, p2, p3 = _patch_communs()
    with p1, p2, p3:
        gen = pipeline_service.stream_discussion_fast_path_sse(
            user_prompt="Quelle température dans le salon ?",
            session_id=session_id,
            gateway=gateway,
            fast_path_cache={},
            inject_project_context=False,
        )
        async for ligne in gen:
            if ligne.startswith("data:"):
                evenements.append(json.loads(ligne[5:].strip()))
    return evenements


# ──────────────────────────────────────────────────────────────────
# Cycle de vie des outils en streaming (contrat ToolCallDisplay)
# ──────────────────────────────────────────────────────────────────

class TestOutilsEnStreaming:
    def test_appel_d_outil_produit_les_evenements_de_cycle_de_vie(self, monkeypatch):
        """tool_call → tool_result (succès, durée) → le résultat de l'outil
        atteint la réponse finale."""
        async def _faux_outil(nom, args, session_id=None):
            return "21.5 °C"

        monkeypatch.setattr("core.vocal_tools.dispatch_vocal_tool", _faux_outil)
        provider = OpenAICompatibleProvider([_APPEL_OUTIL, _REPONSE_FINALE])
        gateway = _GatewayFactice({"candidat": provider})

        evenements = asyncio.run(_drainer(gateway, "test-outils-ok"))
        types = [e["type"] for e in evenements]

        # Annonce AVANT exécution, résultat APRÈS, les deux avant la réponse.
        assert types.index("tool_call") < types.index("tool_result") < types.index("done")

        appel = evenements[types.index("tool_call")]
        assert appel["id"] == "call_1"
        assert appel["toolName"] == "ha_get_state"
        assert appel["args"] == {"entity_id": "sensor.salon_temperature"}

        resultat = evenements[types.index("tool_result")]
        assert resultat["id"] == "call_1"
        assert resultat["status"] == "success"
        assert resultat["resultData"] == "21.5 °C"
        assert resultat["errorMessage"] is None
        assert resultat["executionTimeMs"] >= 0

        # Le résultat de l'outil a bien atteint le tour de synthèse : le second
        # appel LLM porte le message tool, et la réponse finale le synthétise.
        messages_synthese = provider.appels[-1]
        assert any(
            m.get("role") == "tool" and m.get("content") == "21.5 °C"
            for m in messages_synthese
        )
        done = evenements[types.index("done")]
        assert done["response"] == "Il fait 21,5 degrés dans le salon."
        assert done["agents_used"] == ["discussion_chat"]
        # La réponse outillée passe par token + sentence pour les clients TTS.
        assert "token" in types and "sentence" in types

    def test_outil_en_erreur_produit_un_evenement_d_erreur_sans_flux_mort(self, monkeypatch):
        """Un outil qui échoue → événement d'erreur, et la réponse reste
        rendue (le flux ne meurt pas)."""
        async def _outil_en_erreur(nom, args, session_id=None):
            return "Erreur HA HTTP 500"

        monkeypatch.setattr("core.vocal_tools.dispatch_vocal_tool", _outil_en_erreur)
        reponse_repli = {"role": "assistant", "content": "Le capteur ne répond pas."}
        provider = OpenAICompatibleProvider([_APPEL_OUTIL, reponse_repli])
        gateway = _GatewayFactice({"candidat": provider})

        evenements = asyncio.run(_drainer(gateway, "test-outils-ko"))
        types = [e["type"] for e in evenements]

        resultat = evenements[types.index("tool_result")]
        assert resultat["status"] == "error"
        assert resultat["errorMessage"] == "Erreur HA HTTP 500"
        assert resultat["resultData"] is None

        # Pas de flux mort : une réponse est rendue, pas d'événement error SSE.
        assert "error" not in types
        done = evenements[types.index("done")]
        assert done["response"] == "Le capteur ne répond pas."

    def test_provider_sans_outils_ne_paie_aucun_aller_retour(self, monkeypatch):
        """Latence : un provider qui ne supporte pas les outils garde le flux
        historique — la boucle d'outils n'est JAMAIS invoquée."""
        boucle = AsyncMock(return_value="ne devrait jamais servir")
        monkeypatch.setattr("core.vocal_tools.run_vocal_tool_loop", boucle)
        gateway = _GatewayFactice({"candidat": _ProviderSansOutils()})

        evenements = asyncio.run(_drainer(gateway, "test-sans-outils"))

        boucle.assert_not_awaited()
        assert evenements == [
            {"type": "token", "text": "Bonjour."},
            {"type": "sentence", "text": "Bonjour."},
            {
                "type": "done",
                "response": "Bonjour.",
                "agents_used": ["discussion_chat"],
            },
        ]


# ──────────────────────────────────────────────────────────────────
# Annulation vocale : un appel d'outil en cours ne survit pas à l'abort
# ──────────────────────────────────────────────────────────────────

def test_abort_pendant_la_boucle_d_outils(monkeypatch):
    """Un abort vocal pendant un appel d'outil coupe le flux : événement
    `aborted`, jamais de `done`."""
    from core.vocal_abort import abort_vocal_streams

    async def _outil_long(nom, args, session_id=None):
        await asyncio.sleep(5)
        return "21.5 °C"

    monkeypatch.setattr("core.vocal_tools.dispatch_vocal_tool", _outil_long)

    async def _scenario():
        provider = OpenAICompatibleProvider([_APPEL_OUTIL, _REPONSE_FINALE])
        gateway = _GatewayFactice({"candidat": provider})
        evenements = []
        p1, p2, p3 = _patch_communs()
        with p1, p2, p3:
            gen = pipeline_service.stream_discussion_fast_path_sse(
                user_prompt="Quelle température dans le salon ?",
                session_id="test-abort-outils",
                gateway=gateway,
                fast_path_cache={},
                inject_project_context=False,
            )

            async def _drainer():
                async for ligne in gen:
                    if ligne.startswith("data:"):
                        evenements.append(json.loads(ligne[5:].strip()))

            tache = asyncio.create_task(_drainer())
            # Attendre que l'appel d'outil soit annoncé, puis abort (barge-in).
            for _ in range(400):
                if any(e.get("type") == "tool_call" for e in evenements):
                    break
                await asyncio.sleep(0.01)
            abort_vocal_streams(session_id="test-abort-outils")
            await asyncio.wait_for(tache, timeout=5)
        return evenements

    evenements = asyncio.run(_scenario())
    types = [e["type"] for e in evenements]

    assert "tool_call" in types, "l'appel d'outil doit avoir été annoncé"
    assert types[-1] == "aborted"
    assert "done" not in types


# ──────────────────────────────────────────────────────────────────
# Chemin non streamé inchangé : run_vocal_tool_loop historique
# ──────────────────────────────────────────────────────────────────

class TestBoucleOutilsNonStreamee:
    """L'adaptation (on_event) ne doit rien changer à l'appelant historique
    sans callback — les tests existants passent par ailleurs sans modification."""

    @pytest.mark.asyncio
    async def test_sans_callback_comportement_identique(self, monkeypatch):
        monkeypatch.setattr(
            "core.vocal_tools.provider_supports_openai_tools", lambda _p: True
        )

        async def _faux_outil(nom, args, session_id=None):
            return "21.5 °C"

        monkeypatch.setattr("core.vocal_tools.dispatch_vocal_tool", _faux_outil)
        provider = OpenAICompatibleProvider([_APPEL_OUTIL, _REPONSE_FINALE])

        reponse = await run_vocal_tool_loop(
            provider,
            system_prompt="Tu es l'assistant vocal.",
            user_prompt="Quelle température dans le salon ?",
            session_id="test-non-streame",
        )

        assert reponse == "Il fait 21,5 degrés dans le salon."

    @pytest.mark.asyncio
    async def test_avec_callback_evenements_emis_et_resultat_identique(self, monkeypatch):
        monkeypatch.setattr(
            "core.vocal_tools.provider_supports_openai_tools", lambda _p: True
        )

        async def _faux_outil(nom, args, session_id=None):
            return "21.5 °C"

        monkeypatch.setattr("core.vocal_tools.dispatch_vocal_tool", _faux_outil)
        provider = OpenAICompatibleProvider([_APPEL_OUTIL, _REPONSE_FINALE])
        recoit: list[dict] = []

        async def _collecteur(evenement):
            recoit.append(evenement)

        reponse = await run_vocal_tool_loop(
            provider,
            system_prompt="Tu es l'assistant vocal.",
            user_prompt="Quelle température dans le salon ?",
            session_id="test-avec-callback",
            on_event=_collecteur,
        )

        assert reponse == "Il fait 21,5 degrés dans le salon."
        assert [e["type"] for e in recoit] == ["tool_call", "tool_result"]

    @pytest.mark.asyncio
    async def test_callback_defaillant_ne_casse_pas_la_boucle(self, monkeypatch):
        """Une défaillance du callback d'événements ne doit JAMAIS casser la
        boucle d'outils (le flux SSE est un observateur, pas un acteur)."""
        monkeypatch.setattr(
            "core.vocal_tools.provider_supports_openai_tools", lambda _p: True
        )

        async def _faux_outil(nom, args, session_id=None):
            return "21.5 °C"

        monkeypatch.setattr("core.vocal_tools.dispatch_vocal_tool", _faux_outil)
        provider = OpenAICompatibleProvider([_APPEL_OUTIL, _REPONSE_FINALE])

        async def _collecteur_casse(_evenement):
            raise RuntimeError("client SSE défaillant")

        reponse = await run_vocal_tool_loop(
            provider,
            system_prompt="Tu es l'assistant vocal.",
            user_prompt="Quelle température dans le salon ?",
            session_id="test-callback-casse",
            on_event=_collecteur_casse,
        )

        assert reponse == "Il fait 21,5 degrés dans le salon."
