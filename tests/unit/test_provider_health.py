"""test_provider_health.py — #T352, sonde d'accessibilité d'hôte du fast-path.

Journal de PROD du 17/08 : `ollama_pc` (192.168.1.10, simplement éteint) coûtait
2,56 s de connect timeout + backoff avant bascule, à CHAQUE phrase, parce que le
disjoncteur n'ouvre qu'après trois échecs. La sonde doit écarter l'hôte muet
avant le premier essai.

Aucun appel réseau réel ici : `_sonder` (la vraie ouverture TCP) est mockée, et
la sonde de cascade (`_fast_path_hote_muet`) est contrôlée par mock.
"""
import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.llm import provider_health
from core.llm.provider_health import (
    TTL_SUCCES_S,
    base_url_provider,
    hote_et_port,
    hote_joignable,
)
from core.llm.providers.deepseek import FallbackProvider
from core.llm_gateway import LLMGateway
from services import pipeline_service

# ──────────────────────────────────────────────────────────────────
# hote_et_port
# ──────────────────────────────────────────────────────────────────

class TestHoteEtPort:
    def test_ip_et_port_explicites(self):
        assert hote_et_port("http://192.168.1.10:11434/v1/chat/completions") == (
            "192.168.1.10", 11434,
        )

    def test_sans_port_retourne_none(self):
        assert hote_et_port("http://192.168.1.10/v1") is None

    def test_url_illisible_retourne_none(self):
        assert hote_et_port("::pas-une-url::") is None


# ──────────────────────────────────────────────────────────────────
# base_url_provider — déballage des wrappers / cascades
# ──────────────────────────────────────────────────────────────────

class TestBaseUrlProvider:
    def test_provider_brut(self):
        class _P:
            base_url = "http://192.168.1.10:11434/v1"
        assert base_url_provider(_P()) == "http://192.168.1.10:11434/v1"

    def test_fallback_provider_deballe_son_premier_candidat(self):
        class _P:
            base_url = "http://192.168.1.10:11434/v1"
        cascade = FallbackProvider([("ollama_pc", _P())])
        assert base_url_provider(cascade) == "http://192.168.1.10:11434/v1"

    def test_wrapper_decorateur_deballe(self):
        class _P:
            base_url = "http://127.0.0.1:1337/v1"
        class _Wrapper:
            def __init__(self, p):
                self.provider = p
        assert base_url_provider(_Wrapper(_P())) == "http://127.0.0.1:1337/v1"

    def test_aucun_base_url_retourne_none(self):
        class _SansUrl:
            pass
        assert base_url_provider(_SansUrl()) is None


# ──────────────────────────────────────────────────────────────────
# hote_joignable — cache + verrou + TTL
# ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _vider_cache_health():
    provider_health._cache.clear()
    provider_health._verrous.clear()
    yield
    provider_health._cache.clear()
    provider_health._verrous.clear()


class TestHoteJoignable:
    @patch("core.llm.provider_health._sonder", new_callable=AsyncMock)
    async def test_deux_appels_rapproches_ne_lancent_qu_une_sonde(self, mock_sonder):
        """Le cache par (hôte, port) évite de resonder un hôte déjà testé."""
        mock_sonder.return_value = True
        assert await hote_joignable("http://192.168.1.10:11434/v1")
        assert await hote_joignable("http://192.168.1.10:11434/v1")
        assert mock_sonder.await_count == 1

    @patch("core.llm.provider_health._sonder", new_callable=AsyncMock)
    async def test_une_seule_sonde_en_vol_par_hote(self, mock_sonder):
        """Dix requêtes simultanées ne lancent pas dix sondes (verrou par clé)."""
        mock_sonder.return_value = True
        url = "http://192.168.1.10:11434/v1"
        resultats = await asyncio.gather(*[hote_joignable(url) for _ in range(10)])
        assert all(resultats)
        assert mock_sonder.await_count == 1

    @patch("core.llm.provider_health._sonder", new_callable=AsyncMock)
    async def test_ttl_echac_court_permet_de_ressonder(self, mock_sonder):
        """Un échec est mémorisé court (60 s) : le PC rétabli est repris vite."""
        mock_sonder.return_value = False
        url = "http://192.168.1.10:11434/v1"
        assert await hote_joignable(url) is False
        # TTL échec court : on expire le cache pour simuler son retour.
        cle = ("192.168.1.10", 11434)
        provider_health._cache[cle] = (0.0, False)  # expiration passée
        mock_sonder.return_value = True
        assert await hote_joignable(url) is True
        assert mock_sonder.await_count == 2

    @patch("core.llm.provider_health._sonder", new_callable=AsyncMock)
    async def test_succes_ttl_long(self, mock_sonder):
        """Un succès est mémorisé 300 s : pas de resonde pendant longtemps."""
        mock_sonder.return_value = True
        url = "http://192.168.1.10:11434/v1"
        await hote_joignable(url)
        cle = ("192.168.1.10", 11434)
        expiration, resultat = provider_health._cache[cle]
        assert resultat is True
        assert expiration - time.monotonic() > TTL_SUCCES_S * 0.9

    @patch("core.llm.provider_health._sonder", new_callable=AsyncMock)
    async def test_exception_de_sonde_devient_false(self, mock_sonder):
        """Toute exception (socket, DNS, timeout) = « on ne sait pas » = False."""
        mock_sonder.side_effect = OSError("connexion refusée")
        assert await hote_joignable("http://192.168.1.10:11434/v1") is False

    async def test_hote_illisible_est_considere_joignable(self):
        """Doute (URL illisible) → on ne refuse jamais : True, sans sonde."""
        with patch("core.llm.provider_health._sonder", new_callable=AsyncMock) as mock_sonder:
            assert await hote_joignable("http://pas-de-port/v1") is True
            mock_sonder.assert_not_awaited()


# ──────────────────────────────────────────────────────────────────
# _fast_path_hote_muet — le filtre lui-même
# ──────────────────────────────────────────────────────────────────

class TestFastPathHoteMuet:
    def _gateway(self):
        g = Mock()
        g._est_hote_local = Mock(side_effect=lambda h: LLMGateway._est_hote_local(h))
        return g

    def _provider(self, base_url):
        class _P:
            def __init__(self, url):
                self.base_url = url
        return _P(base_url)

    @patch("core.llm.provider_health.hote_joignable", new_callable=AsyncMock)
    async def test_hote_local_muet_retourne_true(self, mock_joignable):
        mock_joignable.return_value = False  # ne décroche pas
        g = self._gateway()
        p = self._provider("http://192.168.1.10:11434/v1")
        assert await pipeline_service._fast_path_hote_muet(g, "ollama_pc", p) is True
        mock_joignable.assert_awaited_once()

    @patch("core.llm.provider_health.hote_joignable", new_callable=AsyncMock)
    async def test_hote_local_qui_decroche_retourne_false(self, mock_joignable):
        mock_joignable.return_value = True
        g = self._gateway()
        p = self._provider("http://192.168.1.10:11434/v1")
        assert await pipeline_service._fast_path_hote_muet(g, "ollama_pc", p) is False

    @patch("core.llm.provider_health.hote_joignable", new_callable=AsyncMock)
    async def test_provider_cloud_jamais_sondé(self, mock_joignable):
        """Un hôte cloud ne doit JAMAIS être écarté par une sonde TCP."""
        g = self._gateway()
        p = self._provider("https://api.deepseek.com/v1/chat/completions")
        assert await pipeline_service._fast_path_hote_muet(g, "deepseek-chat", p) is False
        mock_joignable.assert_not_awaited()

    @patch("core.llm.provider_health.hote_joignable", new_callable=AsyncMock)
    async def test_exception_de_sonde_ne_refuse_pas(self, mock_joignable):
        """Le doute ne doit pas devenir un refus : exception → on tente."""
        mock_joignable.side_effect = OSError("DNS en panne")
        g = self._gateway()
        p = self._provider("http://192.168.1.10:11434/v1")
        assert await pipeline_service._fast_path_hote_muet(g, "ollama_pc", p) is False


# ──────────────────────────────────────────────────────────────────
# Cascade fast-path — run_fast_path avec gateway mocké
# ──────────────────────────────────────────────────────────────────

class _ProviderCascade:
    """Provider factice : base_url + generate compteur d'appels."""

    def __init__(self, base_url, reponse):
        self.base_url = base_url
        self.reponse = reponse
        self.appels = 0

    def generate(self, system_prompt, user_prompt, **kwargs):
        self.appels += 1
        return self.reponse


class _GatewayCascade:
    """Gateway factice : get_provider + vrai critère _est_hote_local."""

    def __init__(self, providers):
        self._providers = providers

    def get_provider(self, name):
        if name not in self._providers:
            raise ValueError(f"Provider LLM inconnu : {name}")
        return self._providers[name]

    @staticmethod
    def _est_hote_local(host):
        return LLMGateway._est_hote_local(host)


class TestCascadeFastPath:
    def _gateway_avec(self, providers):
        return _GatewayCascade(providers)

    async def _run(self, gateway, noms_providers):
        token_tracker = Mock()
        token_tracker.init_session = Mock()
        with (
            patch("core.agent_trace.poser_agent_courant"),
            patch.object(pipeline_service, "_persist_fast_path_async", new_callable=AsyncMock),
            patch.object(pipeline_service, "FAST_PATH_PROVIDERS", noms_providers),
        ):
            return await pipeline_service.run_fast_path(
                user_prompt="bonjour",
                session_id="test-session",
                gateway=gateway,
                token_tracker=token_tracker,
                fast_path_cache={},
            )

    @patch.object(pipeline_service, "_fast_path_hote_muet", new_callable=AsyncMock)
    async def test_hote_muet_saute_et_le_suivant_repond(self, mock_sonde):
        """Hôte muet → provider sauté, le suivant est appelé."""
        local_muet = _ProviderCascade("http://192.168.1.10:11434/v1", "muet")
        local_ok = _ProviderCascade("http://192.168.1.10:11434/v1", "ok")
        mock_sonde.side_effect = [True]  # le premier est muet → sauté
        gateway = self._gateway_avec({"ollama_pc": local_muet, "autre_local": local_ok})

        reponse = await self._run(gateway, ["ollama_pc", "autre_local"])

        assert reponse == "ok"
        assert local_muet.appels == 0, "le provider muet ne doit pas être tenté"
        assert local_ok.appels == 1

    @patch.object(pipeline_service, "_fast_path_hote_muet", new_callable=AsyncMock)
    async def test_hote_qui_decroche_est_tente(self, mock_sonde):
        """Hôte qui décroche → provider tenté normalement."""
        local_ok = _ProviderCascade("http://192.168.1.10:11434/v1", "ok")
        mock_sonde.side_effect = [False]  # décroche
        gateway = self._gateway_avec({"ollama_pc": local_ok})

        reponse = await self._run(gateway, ["ollama_pc"])

        assert reponse == "ok"
        assert local_ok.appels == 1

    @patch.object(pipeline_service, "_fast_path_hote_muet", new_callable=AsyncMock)
    async def test_tous_injoignables_le_dernier_est_tente(self, mock_sonde):
        """Ne jamais vider la cascade : le dernier est tenté même si muet."""
        local_a = _ProviderCascade("http://192.168.1.10:11434/v1", "a")
        local_b = _ProviderCascade("http://192.168.1.10:11434/v1", "b")
        # Le premier est muet (sauté) ; le dernier n'est jamais sondé.
        mock_sonde.side_effect = [True]
        gateway = self._gateway_avec({"a": local_a, "b": local_b})

        reponse = await self._run(gateway, ["a", "b"])

        assert reponse == "b"
        assert local_a.appels == 0
        assert local_b.appels == 1
        assert mock_sonde.await_count == 1, "le dernier candidat ne doit pas être sondé"

    @patch.object(pipeline_service, "_fast_path_hote_muet", new_callable=AsyncMock)
    async def test_exception_de_sonde_ne_bloque_pas_la_cascade(self, mock_sonde):
        """Une exception dans la sonde ne doit pas tuer la cascade."""
        local_ok = _ProviderCascade("http://192.168.1.10:11434/v1", "ok")
        # La sonde lève (comportement de _fast_path_hote_muet après exception :
        # elle retourne False → on tente). Ici on simule le résultat final False.
        mock_sonde.side_effect = [False]
        gateway = self._gateway_avec({"ollama_pc": local_ok})

        reponse = await self._run(gateway, ["ollama_pc"])

        assert reponse == "ok"
        assert local_ok.appels == 1

    @patch.object(pipeline_service, "_fast_path_hote_muet", new_callable=AsyncMock)
    async def test_provider_cloud_de_la_cascade_n_est_pas_sonde(self, mock_sonde):
        """Le filtre ne sonde jamais un cloud : cascade intacte en tête."""
        cloud = _ProviderCascade("https://api.cerebras.ai/v1/chat/completions", "cloud")
        # Le premier (cloud) doit être tenté (jamais sondé → filtre False) ; le
        # second est le dernier (jamais sondé non plus).
        mock_sonde.side_effect = [False]
        gateway = self._gateway_avec({"gpt-oss-120b": cloud, "ollama_pc": cloud})

        reponse = await self._run(gateway, ["gpt-oss-120b", "ollama_pc"])

        assert reponse == "cloud"
        assert mock_sonde.await_count == 1
