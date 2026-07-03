"""
tests/unit/test_minimax_provider.py — Tests de la sous-classe MiniMaxProvider (Phase 2, D3).

Vérifie que le filtrage des balises <think>...</think> fonctionne en sous-classe
(sans monkey-patch), pour les réponses texte ET structurées (tool_calls).
"""

from unittest import mock

from core.openai_compat_provider import MiniMaxProvider


def _provider():
    return MiniMaxProvider(
        provider_name="Minimax",
        base_url="https://api.minimax.io/v1/chat/completions",
        api_key="test-key",
        model="MiniMax-M3",
    )


def test_strip_think_static():
    assert MiniMaxProvider.strip_think("<think>raisonnement</think>Réponse") == "Réponse"
    assert MiniMaxProvider.strip_think("Pas de think") == "Pas de think"
    assert MiniMaxProvider.strip_think({"k": "v"}) == {"k": "v"}  # non-str inchangé


def test_generate_strips_think_from_text():
    p = _provider()
    with mock.patch(
        "core.openai_compat_provider.OpenAICompatibleProvider.generate",
        return_value="<think>je réfléchis\nsur plusieurs lignes</think>Voici la réponse",
    ):
        out = p.generate("sys", "user")
    assert out == "Voici la réponse"


def test_generate_strips_think_in_dict_content():
    p = _provider()
    with mock.patch(
        "core.openai_compat_provider.OpenAICompatibleProvider.generate",
        return_value={"content": "<think>x</think>texte", "tool_calls": [{"id": "1"}]},
    ):
        out = p.generate("sys", "user")
    assert out["content"] == "texte"
    assert out["tool_calls"] == [{"id": "1"}]


def test_is_subclass_not_monkeypatched():
    """[D3] generate est une vraie méthode de classe, pas un attribut d'instance."""
    p = _provider()
    assert "generate" not in p.__dict__  # pas de monkey-patch sur l'instance
    assert type(p).generate is MiniMaxProvider.generate
