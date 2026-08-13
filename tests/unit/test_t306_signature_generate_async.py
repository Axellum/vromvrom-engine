"""
tests/unit/test_t306_signature_generate_async.py — Verrouillage de la signature
commune de `generate_async` (#T306).

Verdict de l'inventaire #T306 : les cinq implémentations de `generate_async` sont
déjà homogènes sur la forme `(self, system_prompt, user_prompt, **kwargs)`, et les
providers qui ne la surchargent pas héritent de la même signature via
`LLMProvider.generate_async` (core/llm/providers/base.py:80). Aucun écart à
aligner — ce test fige la signature commune pour empêcher une divergence future
(ordre des paramètres, kwargs disparu, méthode devenue synchrone).
"""

import importlib
import inspect

import pytest

CLASSES = [
    # Implémentations directes
    "core.llm.providers.base:LLMProvider",
    "core.llm.providers.deepseek:ClaudeInstructionsWrapper",
    "core.llm.providers.deepseek:FallbackProvider",
    "core.anthropic_native_provider:AnthropicNativeProvider",
    "core.openai_compat_provider:OpenAICompatibleProvider",
    # Héritiers de la base (ne surchargent pas generate_async)
    "core.llm.providers.deepseek:LMStudioProvider",
    "core.llm.providers.deepseek:OllamaDeckProvider",
    "core.llm.providers.deepseek:ClaudeCLIProvider",
    "core.llm.providers.gemini:GeminiProvider",
    "core.llm.providers.gemini:GeminiCLIProvider",
    "core.gemini_native:GeminiNativeProvider",
]


def _charger_classes():
    """Importe les classes — skip celles dont le module dépend de libs absentes."""
    resultats = []
    for spec in CLASSES:
        module_path, _, nom = spec.partition(":")
        try:
            module = importlib.import_module(module_path)
            resultats.append((nom, getattr(module, nom)))
        except Exception as exc:  # import_optionnel (genai, etc.) : classe hors test
            resultats.append((nom, exc))
    return resultats


@pytest.fixture(scope="module")
def classes_importables():
    return [c for c in _charger_classes() if isinstance(c[1], type)]


def test_toutes_les_classes_ont_bien_une_generate_async(classes_importables):
    """Les classes non importables sont documentées, pas oubliées."""
    # Aucune assertion bloquante : les modules optionnels sont attendus hors CI.
    assert len(classes_importables) >= len(CLASSES) - 2


@pytest.mark.parametrize("nom,cls", _charger_classes(), ids=lambda v: str(v)[:40])
def test_signature_commune_generate_async(nom, cls):
    """La signature commune est `(self, system_prompt, user_prompt, **kwargs)`,
    méthode async, deux premiers paramètres obligatoires sans défaut."""
    if not isinstance(cls, type):
        pytest.skip(f"Module non importable ici ({type(cls).__name__}) : {cls}")

    assert hasattr(cls, "generate_async"), f"{nom} n'a pas generate_async"
    methode = cls.generate_async

    assert inspect.iscoroutinefunction(methode), (
        f"{nom}.generate_async doit être async (contrat D5)"
    )

    parametres = list(inspect.signature(methode).parameters.values())
    noms = [p.name for p in parametres]

    assert noms[:3] == ["self", "system_prompt", "user_prompt"], (
        f"{nom}.generate_async : paramètres {noms[:3]} — attendu "
        f"self, system_prompt, user_prompt, **kwargs"
    )
    assert parametres[1].default is inspect.Parameter.empty, (
        f"{nom}.generate_async : system_prompt doit être obligatoire"
    )
    assert parametres[2].default is inspect.Parameter.empty, (
        f"{nom}.generate_async : user_prompt doit être obligatoire"
    )
    assert parametres[-1].kind == inspect.Parameter.VAR_KEYWORD, (
        f"{nom}.generate_async : doit accepter **kwargs (session_id, agent_name, "
        f"temperature, max_tokens…) — un kwargs avalé sans transmission ferait "
        f"disparaître le rattachement de session (#T296/#T299/#T308)"
    )
