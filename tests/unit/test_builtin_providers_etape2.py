"""
tests/unit/test_builtin_providers_etape2.py — Conversion des providers du seed (#T231, étape 2).

Points verrouillés :
1. **Fidélité au seed** : chaque modèle déclaré par un plugin reprend EXACTEMENT les
   valeurs du seed (l'enregistrement doit être un no-op sur le catalogue existant) —
   vérifié par relecture AST de seed_models_db.py, pas de copie à la main.
2. **Curation cohérente** : les ids déclarés sont uniques globalement, et les
   api_model_id résolvent les alias réels (dashscope/, deepinfra/, -paid…).
3. **Invariant transport** : une clé absente → CredentialsManquantes (le provider
   est écarté, jamais écrit).
4. **Branchement démarrage** : enregistrer_provider_plugins_once() est idempotent,
   non bloquant et désactivable par MOTEUR_PROVIDER_PLUGINS.

Aucun accès réseau : les transports ne sont jamais appelés.
"""
import ast
import os
import sys
import types
from pathlib import Path

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if not (getattr(sys.modules.get("tools"), "__file__", "") or "").startswith(_REPO):
    _m = types.ModuleType("tools")
    _m.__path__ = [os.path.join(_REPO, "tools")]
    sys.modules["tools"] = _m

from core.llm.builtin_providers import BUILTIN_PROVIDER_PLUGINS  # noqa: E402
from core.llm.provider_plugin import CredentialsManquantes  # noqa: E402

# ── Données du seed (relecture AST — pas de recopie) ─────────────────────────

def _seed_data():
    src = (Path(_REPO) / "seed_models_db.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("seed_providers", "seed_models"):
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for cible in stmt.targets:
                        if isinstance(cible, ast.Name) and cible.id in ("providers", "models"):
                            out[cible.id] = ast.literal_eval(stmt.value)
    return out


_SEED = _seed_data()
_SEED_PROVIDERS = {p["id"]: p for p in _SEED["providers"]}
_SEED_MODELS = {m["id"]: m for m in _SEED["models"]}

# Champs comparés : ce que l'enregistrement écrit (en_colonnes) et que le seed porte.
_CHAMPS_MODELE = (
    "display_name", "tier", "routing_tier", "context_input", "context_output",
    "cost_input_per_m", "cost_output_per_m", "speciality", "recommended_use",
)
_CHAMPS_BOOL = ("supports_tools", "supports_vision", "supports_json_mode", "supports_streaming")
_CHAMPS_PROVIDER = ("name", "type", "api_endpoint", "confidentiality", "cascade_priority")


# ── Fidélité au seed (no-op garanti) ─────────────────────────────────────────


def test_nombre_de_plugins_et_ids_providers_uniques():
    ids = [p.descriptor.id for p in BUILTIN_PROVIDER_PLUGINS]
    assert len(ids) == len(set(ids)) == 18
    # Les 16 convertis + le pilote de l'étape 1 + AJEAN (declaration #AJEAN).
    assert "mistral" in ids


def test_descripteurs_providers_conformes_au_seed():
    """Les colonnes providers déclarées = celles du seed, pour chaque plugin."""
    for plugin in BUILTIN_PROVIDER_PLUGINS:
        d = plugin.descriptor
        seed_p = _SEED_PROVIDERS.get(d.id)
        if seed_p is None:
            continue  # modèle ajouté par curation (aucun seed à comparer)
        for champ in _CHAMPS_PROVIDER:
            assert getattr(d, champ) == seed_p.get(champ), (
                f"provider '{d.id}' : {champ} divergé ({getattr(d, champ)} != {seed_p.get(champ)})"
            )


def test_modeles_declares_conformes_au_seed():
    """Pour chaque modèle déclaré issu du seed, toutes les colonnes écrites coïncident."""
    for plugin in BUILTIN_PROVIDER_PLUGINS:
        for spec in plugin.descriptor.models:
            seed_m = _SEED_MODELS.get(spec.id)
            if seed_m is None:
                continue  # ajouté par curation (domotique-qwen7b:q4, MiniMax-M2.1, MiniMax-M2.7…)
            for champ in _CHAMPS_MODELE:
                assert getattr(spec, champ) == seed_m.get(champ), (
                    f"{spec.id} : {champ} divergé ({getattr(spec, champ)} != {seed_m.get(champ)})"
                )
            for champ in _CHAMPS_BOOL:
                assert int(getattr(spec, champ)) == int(seed_m.get(champ, 0)), (
                    f"{spec.id} : {champ} divergé ({getattr(spec, champ)} != {seed_m.get(champ)})"
                )


def test_ids_modeles_globaux_uniques():
    ids = [spec.id for p in BUILTIN_PROVIDER_PLUGINS for spec in p.descriptor.models]
    assert len(ids) == len(set(ids)), "deux plugins déclarent le même id de modèle"


# ── Curation : api_model_id résout les alias réels ───────────────────────────


@pytest.mark.parametrize("plugin_id, modele, attendu", [
    ("dashscope", "dashscope/qwen3-coder-next", "qwen3-coder-next"),
    ("dashscope", "dashscope/MiniMax-M2.5", "MiniMax-M2.5"),
    ("deepinfra", "deepinfra/deepseek-r1", "deepseek-ai/DeepSeek-R1"),
    ("deepinfra", "deepinfra/llama-3.3-70b-instruct", "meta-llama/Llama-3.3-70B-Instruct"),
    ("cerebras", "gemma-4-31b-cerebras", "gemma-4-31b"),
    ("deepseek", "deepseek-v4-flash", "deepseek-chat"),
    ("deepseek", "deepseek-v4-pro", "deepseek-chat"),
    ("gemini_paid", "gemini-3.1-pro-customtools-paid", "gemini-3.1-pro-preview-customtools"),
    ("minimax", "minimax-m3", "MiniMax-M3"),
])
def test_api_model_id_resout_les_alias(plugin_id, modele, attendu):
    plugin = next(p for p in BUILTIN_PROVIDER_PLUGINS if p.descriptor.id == plugin_id)
    spec = next(s for s in plugin.descriptor.models if s.id == modele)
    assert spec.nom_api == attendu


def test_modeles_sans_alias_gardent_leur_id():
    plugin = next(p for p in BUILTIN_PROVIDER_PLUGINS if p.descriptor.id == "mistral")
    for spec in plugin.descriptor.models:
        assert spec.nom_api == spec.id


# ── Invariant transport : sans clé, on écarte, on ne construit pas ───────────


@pytest.mark.parametrize("plugin_id, env_var", [
    ("deepseek", "DEEPSEEK_API_KEY"),
    ("zhipu", "ZHIPU_API_KEY"),
    ("dashscope", "DASHSCOPE_API_KEY"),
    ("anthropic_native", "ANTHROPIC_API_KEY"),
    ("gemini_paid", "GEMINI_PAYANT_API_KEY"),
    ("gemini_free", "GEMINI_API_KEY"),
    ("openrouter", "OPENROUTER_API_KEY"),
    ("xai", "XAI_API_KEY"),
    ("deepinfra", "DEEPINFRA_API_KEY"),
    ("minimax", "MINIMAX_API_KEY"),
    ("cohere", "COHERE_API_KEY"),
    ("cerebras", "CEREBRAS_API_KEY"),
])
def test_cle_absente_leve_credentials_manquantes(plugin_id, env_var):
    plugin = next(p for p in BUILTIN_PROVIDER_PLUGINS if p.descriptor.id == plugin_id)
    with pytest.raises(CredentialsManquantes):
        plugin.build({}, model=plugin.descriptor.models[0].id)


def test_ollama_local_se_construit_sans_cle():
    plugin = next(p for p in BUILTIN_PROVIDER_PLUGINS if p.descriptor.id == "ollama_local")
    t = plugin.build({}, model="qwen2.5-coder:7b")
    assert t.model == "qwen2.5-coder:7b"


# ── Branchement au démarrage (factory) ───────────────────────────────────────


def test_enregistrement_une_fois_idempotent(monkeypatch):
    from core import models_db
    from core.llm import provider_registration

    ecrits = []
    monkeypatch.setattr(models_db, "upsert_provider", lambda pid, **kw: ecrits.append(("p", pid)) or True)
    monkeypatch.setattr(models_db, "upsert_model", lambda mid, **kw: ecrits.append(("m", mid)) or True)
    provider_registration._ENREGISTREMENT_FAIT = False

    r1 = provider_registration.enregistrer_provider_plugins_once({})
    r2 = provider_registration.enregistrer_provider_plugins_once({})

    assert r1 is not None and r1.providers_enregistres  # local/ollama au minimum
    assert r2 is None  # idempotent : la seconde passe ne fait rien
    assert ecrits  # l'upsert a bien été exercé


def test_desactivation_par_moteur_provider_plugins(monkeypatch):
    from core.llm import provider_registration

    monkeypatch.setenv("MOTEUR_PROVIDER_PLUGINS", "0")
    provider_registration._ENREGISTREMENT_FAIT = False
    assert provider_registration.enregistrer_provider_plugins_once({}) is None


def test_factory_appelle_lenregistrement():
    """create_engine() déclenche bien la passe d'enregistrement, après le gateway.

    L'appel réel de create_engine est lourd (gateway, agents) ; on vérifie la
    séquence sur le code source : l'enregistrement arrive après LLMGateway().
    """
    import inspect

    import core.factory as factory

    src = inspect.getsource(factory.create_engine)
    assert "enregistrer_provider_plugins_once()" in src
    assert src.index("enregistrer_provider_plugins_once()") > src.index("LLMGateway()")
