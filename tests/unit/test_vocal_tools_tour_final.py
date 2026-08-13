"""
tests/unit/test_vocal_tools_tour_final.py — Le tour de synthèse de la boucle
d'outils vocaux n'envoie plus d'appel d'outil orphelin (#T310), et les erreurs
HTTP des providers disent enfin pourquoi (#T309).

Mesuré le 11/08 en exerçant le vrai chemin vocal : au tour limite,
`run_vocal_tool_loop` réinjectait le message assistant porteur de `tool_calls`
puis un message `user`, sans les messages `tool` correspondants. Cerebras répond :

    HTTP 400 — "An assistant message with 'tool_calls' must be followed by tool
    messages responding to each 'tool_call_id'."

La synthèse échouait donc systématiquement dès que le modèle redemandait un outil
au dernier tour, et l'échec ouvrait le circuit breaker de `gpt-oss-120b` —
premier provider du fast-path vocal, donc du chemin le plus emprunté du moteur.

Aucun appel réseau ici : le provider est un double qui enregistre ce qu'on lui envoie.
"""
import pytest

from core.vocal_tools import run_vocal_tool_loop


class _ProviderQuiRedemandeToujoursUnOutil:
    """Force le plafond de tours : renvoie un tool_call à chaque appel outillé."""

    def __init__(self):
        self.appels: list[list[dict]] = []

    def generate(self, system_prompt, user_prompt, **kwargs):
        messages = kwargs.get("messages") or []
        self.appels.append([dict(m) for m in messages])
        # Le tour de synthèse n'a pas d'outils : on rend du texte.
        if not kwargs.get("tools"):
            return {"role": "assistant", "content": "Il fait 21,5 °C dans le salon."}
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"call_{len(self.appels)}",
                "type": "function",
                "function": {"name": "ha_list", "arguments": '{"query":"salon"}'},
            }],
        }


def _appels_orphelins(messages: list[dict]) -> list[str]:
    """Ids de tool_call annoncés par un assistant sans message `tool` en réponse."""
    annonces, repondus = [], set()
    for message in messages:
        if message.get("role") == "assistant":
            for appel in message.get("tool_calls") or []:
                annonces.append(appel.get("id"))
        elif message.get("role") == "tool":
            repondus.add(message.get("tool_call_id"))
    return [identifiant for identifiant in annonces if identifiant not in repondus]


@pytest.mark.asyncio
async def test_le_tour_de_synthese_n_envoie_aucun_appel_d_outil_orphelin(monkeypatch):
    """C'est la condition exacte que l'API refuse en 400."""
    monkeypatch.setattr(
        "core.vocal_tools.provider_supports_openai_tools", lambda _p: True
    )
    async def _faux_outil(nom, args, session_id=None):
        return "sensor.salon = 21.5"

    monkeypatch.setattr("core.vocal_tools.dispatch_vocal_tool", _faux_outil)

    provider = _ProviderQuiRedemandeToujoursUnOutil()
    reponse = await run_vocal_tool_loop(
        provider,
        system_prompt="Tu es l'assistant vocal.",
        user_prompt="Quelle température dans le salon ?",
        session_id="s_t310",
        temperature=0.0,
    )

    assert reponse == "Il fait 21,5 °C dans le salon.", reponse
    assert len(provider.appels) >= 2, "le plafond de tours n'a pas été atteint"

    orphelins = _appels_orphelins(provider.appels[-1])
    assert not orphelins, (
        f"appels d'outils sans réponse dans le tour de synthèse : {orphelins} — "
        "c'est exactement ce que l'API rejette en HTTP 400"
    )


class TestCorpsDesErreursHttp:
    """#T309 : une erreur d'API doit dire pourquoi, pas seulement son code."""

    class _Reponse:
        def __init__(self, code, texte):
            self.status_code, self.text = code, texte

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"{self.status_code} Client Error")

    def test_le_corps_est_journalise_sur_4xx(self, caplog):
        from core.openai_compat_provider import lever_pour_statut

        reponse = self._Reponse(400, '{"message":"wrong_api_format","param":"messages"}')
        with caplog.at_level("ERROR"):
            with pytest.raises(RuntimeError):
                lever_pour_statut(reponse, provider="Cerebras", modele="gpt-oss-120b")

        assert "wrong_api_format" in caplog.text, "le corps de l'API doit être journalisé"
        assert "Cerebras" in caplog.text and "gpt-oss-120b" in caplog.text

    def test_transparent_sur_2xx(self):
        from core.openai_compat_provider import lever_pour_statut

        lever_pour_statut(self._Reponse(200, "ok"), provider="X", modele="y")

    def test_corps_tronque(self, caplog):
        from core.openai_compat_provider import lever_pour_statut

        reponse = self._Reponse(400, "x" * 5000)
        with caplog.at_level("ERROR"):
            with pytest.raises(RuntimeError):
                lever_pour_statut(reponse, provider="X", modele="y")
        assert len(caplog.text) < 1500, "un corps énorme ne doit pas inonder le journal"
