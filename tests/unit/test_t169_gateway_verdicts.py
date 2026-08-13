"""
tests/unit/test_t169_gateway_verdicts.py — Verdicts #T169 : résolution modèle → provider
et timeouts de la passerelle LLM.

Deux symptômes rapportés pendant la génération des audits Tab5, non reproduits à ce
jour. Ce fichier fige par des tests le comportement réel, symptôme par symptôme.

SYMPTÔME A — « zai-glm-4.7 routé vers Cerebras »
Verdict établi : comportement CONFORME. `zai-glm-4.7` est un modèle Cerebras par
design — descripteur CerebrasPlugin (core/llm/builtin_providers.py:1249), câblage
gateway (core/llm_gateway.py:267), seed (seed_models_db.py:293), usage documenté
(agents/dreamer_agent.py:528-531 : « escaladé vers zai-glm-4.7 — GLM/Z.ai hébergé
gratuitement via la clé Cerebras — décision Axel 07/07 »). `glm-4.7` est un modèle
Zhipu DISTINCT (ZhipuPlugin builtin_providers.py:1774, gateway:358, seed:316) ;
`dashscope/glm-4.7` un troisième (préfixé, gateway:385, seed:332). La résolution se
fait par clé exacte (`self.providers.get(name.lower())`, llm_gateway.py:661) : ni
préfixe rogné, ni correspondance partielle, ni dernier gagnant. Aucune collision
d'identifiants entre plugins ni entre plugin et seed.

SYMPTÔME B — « timeout 120 s »
Verdict établi : comportement CONFORME. Le read de 120 s est le DÉFAUT de la famille
`openai_compat` (core/llm_timeouts.py:24), pas un héritage du réglage local
`lmstudio` ((2, 120), llm_timeouts.py:25, utilisé uniquement par LMStudioProvider,
core/llm/providers/deepseek.py:206). Le seul timeout court est `ollama_pc` (2, 15),
explicite (core/llm_gateway.py:430-436). Aucun provider distant n'utilise la famille
`lmstudio`.
"""


import pytest

from core.llm.builtin_providers import BUILTIN_PROVIDER_PLUGINS
from core.llm.providers.deepseek import LMStudioProvider
from core.llm_gateway import LLMGateway
from core.llm_timeouts import DEFAULT_TIMEOUTS, get_timeout

# ── SYMPTÔME A : résolution nom de modèle → provider ──────────────────────────


@pytest.fixture()
def gateway_cles_factices(monkeypatch):
    """Gateway instancié avec des clés factices — aucune requête réseau (construction seule)."""
    for cle in (
        "CEREBRAS_API_KEY",
        "CEREBRAS_PAYANT_API_KEY",
        "ZHIPU_API_KEY",
        "DASHSCOPE_API_KEY",
        "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY",
        "MISTRAL_API_KEY",
        "COHERE_API_KEY",
        "OPENROUTER_API_KEY",
        "XAI_API_KEY",
        "MINIMAX_API_KEY",
        "DEEPINFRA_API_KEY",
    ):
        monkeypatch.setenv(cle, f"cle-factice-{cle.lower()}")
    return LLMGateway()


def test_resolution_par_cle_exacte_des_trois_glm(gateway_cles_factices):
    """#T169-A : les trois identifiants voisins sont résolus par clé exacte vers
    TROIS providers distincts — aucun préfixe rogné, aucun dernier gagnant."""
    providers = gateway_cles_factices.providers

    zai = providers["zai-glm-4.7"]
    assert "cerebras.ai" in zai.base_url, (
        "zai-glm-4.7 doit être câblé chez Cerebras (design documenté) "
        f"— reçu : {zai.base_url}"
    )
    assert zai.model == "zai-glm-4.7"

    glm = providers["glm-4.7"]
    assert "bigmodel.cn" in glm.base_url, (
        "glm-4.7 doit être câblé chez Zhipu — reçu : {glm.base_url}"
    )
    assert glm.model == "glm-4.7"

    dash = providers["dashscope/glm-4.7"]
    assert "dashscope.aliyuncs.com" in dash.base_url, (
        "dashscope/glm-4.7 doit être câblé chez DashScope — reçu : {dash.base_url}"
    )

    # Les trois objets sont distincts : aucune collision d'entrée de registre.
    assert zai is not glm and glm is not dash and zai is not dash


def test_aucun_id_modele_partage_entre_plugins():
    """#T169-A : deux plugins ne déclarent jamais le même id — sinon le dernier
    enregistré écraserait le premier dans le catalogue (upsert par id)."""
    ids_par_plugin: dict[str, set[str]] = {}
    for plugin in BUILTIN_PROVIDER_PLUGINS:
        pid = plugin.descriptor.id
        for spec in plugin.descriptor.models:
            ids_par_plugin.setdefault(spec.id, set()).add(pid)
    collisions = {mid: pids for mid, pids in ids_par_plugin.items() if len(pids) > 1}
    assert collisions == {}, (
        "Identifiants de modèles partagés entre plugins (collision catalogue) : "
        f"{collisions}"
    )


def test_les_ids_dashscope_sont_prefixes():
    """#T169-A : la règle de préfixation `dashscope/...` (llm_gateway.py:366-367)
    protège les modèles DashScope des collisions avec zhipu/local/minimax."""
    dashscope = next(p for p in BUILTIN_PROVIDER_PLUGINS if p.descriptor.id == "dashscope")
    ids = [m.id for m in dashscope.descriptor.models]
    assert all(mid.startswith("dashscope/") for mid in ids), (
        f"IDs DashScope non préfixés (collision possible avec un autre provider) : "
        f"{[mid for mid in ids if not mid.startswith('dashscope/')]}"
    )
    assert "dashscope/glm-4.7" in ids  # le jumeau préfixé de glm-4.7 existe bien


# ── SYMPTÔME B : timeouts ─────────────────────────────────────────────────────


def test_le_timeout_lmstudio_est_local_et_delibere():
    """#T169-B : la famille lmstudio garde (2, 120) — connect court réseau local,
    read long pour les gros modèles locaux (défaut centralisé, llm_timeouts.py:25)."""
    assert get_timeout("lmstudio") == (2.0, 120.0)
    assert DEFAULT_TIMEOUTS["lmstudio"] == (2.0, 120.0)


def test_le_timeout_120s_des_distants_est_le_defaut_openai_compat():
    """#T169-B : le read de 120 s des providers distants est le DÉFAUT de la famille
    openai_compat (llm_timeouts.py:24), PAS un héritage du réglage local lmstudio :
    les deux familles ont des connect timeouts différents et des entrées distinctes."""
    assert get_timeout("openai_compat") == (5.0, 120.0)
    assert get_timeout("openai_compat") != get_timeout("lmstudio")


def test_ollama_pc_garde_son_timeout_court_explicite(gateway_cles_factices):
    """#T169-B : ollama_pc (LAN, cold start 30s+) garde le timeout dédié (2, 15)
    documenté llm_gateway.py:430-436 — aucun retour silencieux à 120 s."""
    ollama_pc = gateway_cles_factices.providers["ollama_pc"]
    assert ollama_pc.timeout == (2.0, 15.0)


def test_aucun_provider_httpx_n_utilise_la_famille_lmstudio(gateway_cles_factices):
    """#T169-B : la famille lmstudio n'alimente que LMStudioProvider (local).
    Aucun autre provider instancié ne porte le tuple lmstudio (2, 120) — les
    distants portent le défaut de leur famille (openai_compat (5, 120),
    anthropic (5, 180)), jamais le réglage local connect-court."""
    for nom, provider in gateway_cles_factices.providers.items():
        if nom == "local":
            continue  # LMStudioProvider — le seul légitime porteur de (2, 120)
        timeout = getattr(provider, "timeout", None)
        if timeout is None:
            continue  # providers natifs (CLI Gemini/Claude, deck_ollama…)
        assert timeout != (2.0, 120.0), (
            f"{nom} porte le tuple lmstudio (2.0, 120.0) : héritage du réglage "
            f"local par un provider qui n'est pas LMStudioProvider — à corriger."
        )


def test_lmstudio_provider_utilise_bien_la_famille_lmstudio():
    """#T169-B : preuve que le 120 s « local » vit bien chez LMStudioProvider."""
    import inspect

    src = inspect.getsource(LMStudioProvider.generate)
    assert 'get_timeout("lmstudio")' in src
