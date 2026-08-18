"""test_stream_discussion_sonde.py — #T352, sonde d'hôte sur le chemin streamé.

Le correctif porte sur `stream_discussion_fast_path_sse`, le générateur SSE qui
sert `/api/execute/stream` en mode chat — soit 166 des 169 échanges vocaux des 30
derniers jours (98 % du trafic). La sonde `_fast_path_hote_muet` (livrée par
#T352) ne couvrait que le chemin bufferisé `run_fast_path` ; ce test vérifie
qu'elle est désormais appliquée à la cascade streamée avec les MÊMES garde-fous.

Aucun appel réseau réel ici : la sonde (`_fast_path_hote_muet`) est mockée et
`inject_project_context=False` évite tout appel RAG/BDD.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

from services import pipeline_service


class _ProviderStream:
    """Provider factice : base_url + generate_stream compteur d'appels."""

    def __init__(self, base_url, reponse):
        self.base_url = base_url
        self.reponse = reponse
        self.appels = 0

    def generate_stream(self, system_prompt, user_prompt, **kwargs):
        self.appels += 1
        yield {"token": self.reponse, "done": False}
        yield {"token": "", "done": True}


class _GatewayStream:
    """Gateway factice : get_provider + vrai critère _est_hote_local."""

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


def _gateway_avec(providers):
    return _GatewayStream(providers)


async def _collect(gateway, noms_providers, mock_sonde):
    """Draine le générateur et renvoie la liste des lignes SSE."""
    lignes = []
    with (
        patch.object(pipeline_service, "_fast_path_hote_muet", mock_sonde),
        patch.object(pipeline_service, "FAST_PATH_PROVIDERS", noms_providers),
        patch.object(pipeline_service, "_persist_fast_path_async", new_callable=AsyncMock),
    ):
        gen = pipeline_service.stream_discussion_fast_path_sse(
            user_prompt="bonjour",
            session_id="test-session",
            gateway=gateway,
            fast_path_cache={},
            inject_project_context=False,
        )
        async for ligne in gen:
            lignes.append(ligne)
    return lignes


def _texte_done(lignes):
    """Extrait le champ 'response' du dernier événement de type 'done'."""
    reponse = None
    for ligne in lignes:
        if ligne.startswith("data:"):
            evt = json.loads(ligne[5:].strip())
            if evt.get("type") == "done":
                reponse = evt.get("response")
    return reponse


class TestStreamDiscussionSonde:
    def test_hote_muet_saute_et_le_suivant_repond(self):
        """Cascade [ollama_pc, autre] : hôte muet → ollama_pc jamais streamé,
        la réponse vient de `autre`, et la sonde a bien été consultée."""
        local_muet = _ProviderStream("http://192.168.1.10:11434/v1", "MUET")
        autre = _ProviderStream("http://192.168.1.10:11434/v1", "AUTRE")
        gateway = _gateway_avec({"ollama_pc": local_muet, "autre": autre})
        mock_sonde = AsyncMock(side_effect=[True])

        lignes = asyncio.run(_collect(gateway, ["ollama_pc", "autre"], mock_sonde))

        assert _texte_done(lignes) == "AUTRE"
        assert local_muet.appels == 0, "le provider muet ne doit jamais être streamé"
        assert autre.appels == 1
        mock_sonde.assert_awaited_once()

    def test_tous_muets_le_dernier_est_quand_meme_streamed(self):
        """Tous les candidats muets → la cascade n'est pas vidée : le dernier
        est tenté quand même (mieux vaut payer 2 s que ne rien répondre)."""
        a = _ProviderStream("http://192.168.1.10:11434/v1", "A")
        b = _ProviderStream("http://192.168.1.10:11434/v1", "B")
        gateway = _gateway_avec({"a": a, "b": b})
        mock_sonde = AsyncMock(side_effect=[True])

        lignes = asyncio.run(_collect(gateway, ["a", "b"], mock_sonde))

        assert _texte_done(lignes) == "B"
        assert a.appels == 0
        assert b.appels == 1
        assert mock_sonde.await_count == 1, "le dernier candidat ne doit pas être sondé"

    def test_hote_qui_decroche_est_streamed(self):
        """Hôte qui décroche → le provider est streamé normalement (sonde False)."""
        ok = _ProviderStream("http://192.168.1.10:11434/v1", "OK")
        autre = _ProviderStream("http://192.168.1.10:11434/v1", "AUTRE")
        gateway = _gateway_avec({"ollama_pc": ok, "autre": autre})
        mock_sonde = AsyncMock(side_effect=[False])

        lignes = asyncio.run(_collect(gateway, ["ollama_pc", "autre"], mock_sonde))

        assert _texte_done(lignes) == "OK"
        assert ok.appels == 1
        assert autre.appels == 0
        mock_sonde.assert_awaited_once()

    def test_provider_cloud_muet_tcp_n_est_pas_ecarte(self):
        """Un provider cloud muet au niveau TCP n'est pas écarté : la sonde ne
        le concerne pas (elle ne s'applique qu'aux hôtes locaux)."""
        cloud = _ProviderStream("https://api.deepseek.com/v1/chat/completions", "CLOUD")
        autre = _ProviderStream("http://192.168.1.10:11434/v1", "AUTRE")
        gateway = _gateway_avec({"deepseek-chat": cloud, "autre": autre})
        # La sonde mockée renvoie False (le filtre ne juge pas le cloud muet).
        mock_sonde = AsyncMock(side_effect=[False])

        lignes = asyncio.run(_collect(gateway, ["deepseek-chat", "autre"], mock_sonde))

        assert _texte_done(lignes) == "CLOUD"
        assert cloud.appels == 1
        assert autre.appels == 0
        mock_sonde.assert_awaited_once()

    def test_sonde_qui_leve_le_provider_est_tente(self):
        """Une sonde qui lève → « on ne sait pas » → le provider est tenté comme
        avant (le doute ne devient pas un refus).

        `_fast_path_hote_muet` attrape toute exception interne (socket, DNS) et
        renvoie False ; on simule donc son résultat réel après une levée de sonde.
        """
        ok = _ProviderStream("http://192.168.1.10:11434/v1", "OK")
        autre = _ProviderStream("http://192.168.1.10:11434/v1", "AUTRE")
        gateway = _gateway_avec({"ollama_pc": ok, "autre": autre})
        mock_sonde = AsyncMock(side_effect=[False])

        lignes = asyncio.run(_collect(gateway, ["ollama_pc", "autre"], mock_sonde))

        assert _texte_done(lignes) == "OK"
        assert ok.appels == 1
        assert autre.appels == 0
        mock_sonde.assert_awaited_once()
