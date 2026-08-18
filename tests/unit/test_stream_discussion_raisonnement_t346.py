"""test_stream_discussion_raisonnement_t346.py — #T346, canal de raisonnement.

Le raisonnement des modèles qui en produisent (`deepseek-reasoner` et son
`delta.reasoning_content`, les compatibles à champ `reasoning`) était ignoré à
la lecture du chunk SSE : aucun canal de raisonnement n'existait dans le
moteur. Ce test couvre la traversée provider → gateway → pipeline → route SSE.

Garde-fou central : le raisonnement ne doit JAMAIS partir au TTS ni se
retrouver dans la réponse rendue (ou mise en cache) — un bloc de pensée lu à
voix haute serait une régression visible immédiatement dans le salon.

Aucun appel réseau réel ici : les providers sont des doubles, la sonde d'hôte
est mockée et `inject_project_context=False` évite tout appel RAG/BDD.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from services import pipeline_service

# ──────────────────────────────────────────────────────────────────
# Niveau provider : generate_stream sépare raisonnement et tokens
# ──────────────────────────────────────────────────────────────────

def _reponse_sse(lignes: list[str]):
    """Double de réponse HTTP streamée : iter_lines + statut 200."""
    reponse = MagicMock()
    reponse.status_code = 200
    reponse.iter_lines.return_value = iter(lignes)
    return reponse


def _chunk(delta: dict) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]})


class TestGenerateStreamCanalRaisonnement:
    def _provider(self):
        from core.openai_compat_provider import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            provider_name="Test", base_url="http://unitaire.invalid/v1",
            api_key="cle-factice", model="deepseek-reasoner",
        )

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    def test_le_raisonnement_est_yielde_separement_des_tokens(self, mock_session):
        """delta.reasoning_content → canal dédié, jamais confondu avec token."""
        mock_session.return_value.post.return_value = _reponse_sse([
            _chunk({"reasoning_content": "Je cher"}),
            _chunk({"reasoning_content": "che."}),
            _chunk({"content": "Bonjour"}),
            _chunk({"content": " tout le monde."}),
            "data: [DONE]",
        ])

        chunks = list(self._provider().generate_stream("s", "u"))

        raisonnements = [c for c in chunks if c.get("reasoning")]
        tokens = [c for c in chunks if c.get("token")]
        assert "".join(c["reasoning"] for c in raisonnements) == "Je cherche."
        assert "".join(c["token"] for c in tokens) == "Bonjour tout le monde."
        # Jamais les deux canaux dans un même chunk yieldé.
        assert not any(c.get("reasoning") and c.get("token") for c in chunks)
        # Le chunk de clôture garde exactement sa forme existante.
        assert chunks[-1] == {"token": "", "done": True, "usage": None}

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    def test_le_champ_reasoning_est_aussi_reconnu(self, mock_session):
        """Variante `delta.reasoning` (autres compatibles OpenAI)."""
        mock_session.return_value.post.return_value = _reponse_sse([
            _chunk({"reasoning": "Réfléchissons."}),
            _chunk({"content": "Fait."}),
            "data: [DONE]",
        ])

        chunks = list(self._provider().generate_stream("s", "u"))

        assert chunks[0] == {"reasoning": "Réfléchissons.", "done": False, "usage": None}
        assert chunks[1] == {"token": "Fait.", "done": False, "usage": None}

    @patch("core.openai_compat_provider.SharedHTTPPool.get_session")
    def test_sans_raisonnement_le_flux_est_inchange(self, mock_session):
        """Un provider qui ne raisonne pas yield exactement ce qu'avant."""
        mock_session.return_value.post.return_value = _reponse_sse([
            _chunk({"content": "Salut"}),
            "data: [DONE]",
        ])

        chunks = list(self._provider().generate_stream("s", "u"))

        assert chunks == [
            {"token": "Salut", "done": False, "usage": None},
            {"token": "", "done": True, "usage": None},
        ]


def test_le_wrapper_laisse_passer_le_canal_raisonnement():
    """ClaudeInstructionsWrapper (yield from) ne doit pas fondre les canaux."""
    from core.llm.providers.deepseek import ClaudeInstructionsWrapper

    class _ProviderRaisonneur:
        def generate_stream(self, system_prompt, user_prompt, **kwargs):
            yield {"reasoning": "hm", "done": False, "usage": None}
            yield {"token": "ok", "done": False, "usage": None}
            yield {"token": "", "done": True, "usage": None}

    chunks = list(ClaudeInstructionsWrapper(_ProviderRaisonneur()).generate_stream("s", "u"))

    assert chunks[0] == {"reasoning": "hm", "done": False, "usage": None}
    assert chunks[1] == {"token": "ok", "done": False, "usage": None}


# ──────────────────────────────────────────────────────────────────
# Niveau SSE : événements thinking distincts, TTS jamais contaminé
# ──────────────────────────────────────────────────────────────────

class _ProviderRaisonneur:
    """Provider factice : raisonne d'abord, répond ensuite."""

    def __init__(self):
        self.base_url = "https://api.deepseek.com/chat/completions"

    def generate_stream(self, system_prompt, user_prompt, **kwargs):
        yield {"reasoning": "Analysons la demande", "done": False, "usage": None}
        yield {"reasoning": " : c'est une salutation.", "done": False, "usage": None}
        yield {"token": "Bonjour", "done": False, "usage": None}
        yield {"token": " tout le monde.", "done": False, "usage": None}
        yield {"token": "", "done": True, "usage": None}


class _ProviderSansRaisonnement:
    """Provider factice : aucun champ raisonnement, flux historique."""

    def __init__(self):
        self.base_url = "https://api.deepseek.com/chat/completions"

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


async def _drainer(gateway, provider_name, cache):
    """Draine le générateur SSE et renvoie la liste des événements parsés."""
    evenements = []
    with (
        patch.object(
            pipeline_service, "_fast_path_hote_muet", AsyncMock(return_value=False)
        ),
        patch.object(pipeline_service, "FAST_PATH_PROVIDERS", [provider_name]),
        patch.object(
            pipeline_service, "_persist_fast_path_async", new_callable=AsyncMock
        ),
    ):
        gen = pipeline_service.stream_discussion_fast_path_sse(
            user_prompt="bonjour",
            session_id="test-raisonnement",
            gateway=gateway,
            fast_path_cache=cache,
            inject_project_context=False,
        )
        async for ligne in gen:
            if ligne.startswith("data:"):
                evenements.append(json.loads(ligne[5:].strip()))
    return evenements


class TestSseCanalRaisonnement:
    def test_le_raisonnement_part_dans_des_evenements_distincts(self):
        """LE test central : raisonnement et texte dans des événements séparés,
        et le texte final (TTS) ne contient pas une ligne de raisonnement."""
        cache: dict = {}
        gateway = _GatewayFactice({"raisonneur": _ProviderRaisonneur()})

        evenements = asyncio.run(_drainer(gateway, "raisonneur", cache))
        types = [e["type"] for e in evenements]

        # Les fragments de pensée arrivent sur le canal thinking, dans l'ordre.
        pensee = "".join(e["text"] for e in evenements if e["type"] == "thinking")
        assert pensee == "Analysons la demande : c'est une salutation."

        # thinking_done porte la durée et précède le premier token.
        assert "thinking_done" in types
        assert evenements[types.index("thinking_done")]["durationMs"] >= 0
        assert types.index("thinking_done") < types.index("token")

        # GARDE-FOU : aucun fragment de raisonnement dans ce qui est rendu,
        # parlé (sentence) ou mis en cache.
        rendu = "".join(
            e.get("text", "") + e.get("response", "")
            for e in evenements
            if e["type"] in ("token", "sentence", "done")
        )
        assert "Analysons" not in rendu
        assert "salutation" not in rendu
        assert all("Analysons" not in v for v in cache.values())

        # La réponse reste intacte pour le TTS.
        done = evenements[types.index("done")]
        assert done["response"] == "Bonjour tout le monde."
        assert done["agents_used"] == ["discussion_chat"]

    def test_sans_raisonnement_le_flux_est_identique_evenement_par_evenement(self):
        """Un provider qui ne raisonne pas produit exactement le flux d'avant :
        un client qui ignore les nouveaux événements ne voit aucun changement."""
        cache: dict = {}
        gateway = _GatewayFactice({"muet": _ProviderSansRaisonnement()})

        evenements = asyncio.run(_drainer(gateway, "muet", cache))

        assert evenements == [
            {"type": "token", "text": "Bonjour."},
            {"type": "sentence", "text": "Bonjour."},
            {
                "type": "done",
                "response": "Bonjour.",
                "agents_used": ["discussion_chat"],
            },
        ]
