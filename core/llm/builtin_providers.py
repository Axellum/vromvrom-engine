"""
core/llm/builtin_providers.py — Plugins provider « internes » (#T231).

Les ~18 providers historiques ne sont pas des plugins tiers : ils vivent dans le
dépôt. Ils sont donc déclarés ici et chargés SANS `exec_module`, donc sans dépendre
de `MOTEUR_ENABLE_PLUGINS` (ce garde-fou de `core/plugin_registry.py:132` protège de
l'exécution de code arbitraire venu du dossier `plugins/` ; du code déjà présent dans
le dépôt ne pose pas ce risque).

**Étape 2 : les providers du seed sont convertis** (16 + Mistral, le pilote de
l'étape 1). Les valeurs des descripteurs sont recopiées de `seed_models_db.py` —
l'enregistrement est un upsert seul, jamais destructif. La CURATION est écrite dans
chaque descripteur (`notes`) et justifiée par les trois signaux mesurés le 10/08 :
inventaire live des API (#T243), historique d'appels (`token_usage`), câblage réel
du gateway (`core/llm_gateway.py`). Règle appliquée : est déclaré ce qui est câblé
et/ou appelé ; le stock « offert mais ni câblé ni appelé » n'est pas reconduit en
dormant (il se réajoute ici le jour où un besoin le justifie, ex. #T245).

Non convertis, assumé :
  - `cloud_apis` (TTS/STT/Vision/Translation) — APIs spécialisées hors chat, aucun
    transport LLM à construire ; reste sur le chemin historique ;
  - `anthropic_gcp`, `flux` — absents du seed (ajouts directs du catalogue
    local), et pour ces derniers aucun transport n'existe dans le gateway.
  - `github` — RETIRÉ le 11/08/2026 (#T280) : service fermé le 30/07/2026.
"""

from __future__ import annotations

import os
from typing import Any

from core.llm.provider_plugin import (
    AuthSpec,
    CredentialsManquantes,
    ModelSpec,
    ProviderDescriptor,
    ProviderPlugin,
    verifier_interface_transport,
)

# Modèle réellement chargé par AJEAN (llama.cpp / llama-server). Constante unique,
# partagée avec le registre core/openai_compat_provider.py — ne pas dupliquer ici.
from core.openai_compat_provider import AJEAN_DEFAULT_MODEL

_NOTES_EXPERIMENT = "Plan Experiment (Gratuit)"


def _binaire_cli_present(noms: tuple[str, ...]) -> bool:
    """Détection PATH d'un binaire CLI (Antigravity/Claude).

    Les chemins AppData Windows du gateway (`llm_gateway`/`budget_guard`) ne sont
    jamais présents sous Linux ; la recherche PATH suffit et couvre les deux
    plateformes (le BudgetGuard fait de même depuis #T248).
    """
    import shutil
    return any(shutil.which(nom) for nom in noms)


class _PluginOpenAICompat(ProviderPlugin):
    """Base des plugins à transport OpenAI-compatible.

    Recopie la construction du gateway (`llm_gateway.py::_make_compat`) : base_url
    et en-têtes tirés du registre `OPENAI_COMPAT_PROVIDERS` (jamais recopiés en
    dur), surcharge d'endpoint DashScope via `DASHSCOPE_BASE_URL` incluse.
    """

    def _build_compat(self, registre_id: str, spec: ModelSpec, cle: str) -> Any:
        from core.openai_compat_provider import OPENAI_COMPAT_PROVIDERS, OpenAICompatibleProvider

        config = OPENAI_COMPAT_PROVIDERS.get(registre_id, {})
        base_url = config.get("base_url", "")
        if registre_id == "dashscope":
            override = os.environ.get("DASHSCOPE_BASE_URL", "").rstrip("/")
            if override:
                base_url = (
                    override
                    if override.endswith("/chat/completions")
                    else f"{override}/chat/completions"
                )
        classe = OpenAICompatibleProvider
        if registre_id == "minimax":
            from core.openai_compat_provider import MiniMaxProvider
            classe = MiniMaxProvider
        transport = classe(
            provider_name=registre_id.capitalize(),
            base_url=base_url,
            api_key=cle,
            # `nom_api` et pas `id` : le catalogue peut porter un alias désambiguïsé,
            # l'API veut son propre nom (cf. le 404 de `gemma-4-31b-cerebras`).
            model=spec.nom_api,
            extra_headers=config.get("extra_headers"),
        )
        verifier_interface_transport(transport)
        return transport


class MistralPlugin(ProviderPlugin):
    """Provider Mistral AI — transport OpenAI-compatible, auth par clé d'API."""

    _DESCRIPTEUR = ProviderDescriptor(
        id="mistral",
        name="Mistral AI API",
        # Vocabulaire du catalogue, pas du transport : `models_db.py:1121` filtre
        # sur `type IN ('pay_as_you_go', 'free')`. Voir l'avertissement de
        # provider_plugin.ProviderDescriptor.
        type="pay_as_you_go",
        transport="openai_compat",
        api_endpoint="https://api.mistral.ai/v1/chat/completions",
        auth=AuthSpec(
            kind="api_key",
            env_var="MISTRAL_API_KEY",
            instructions=(
                "Créer une clé d'API sur console.mistral.ai (La Plateforme → API Keys), "
                "puis la poser dans MISTRAL_API_KEY du .env du moteur."
            ),
            connect_label="Enregistrer la clé Mistral",
            doc_url="https://docs.mistral.ai/getting-started/quickstart/",
        ),
        confidentiality="training",
        cascade_priority=3.8,
        notes="Plan Experiment (Gratuit). Excellent en français. Tokenizer optimisé.",
        models=(
            ModelSpec(
                id="mistral-large-latest",
                display_name="Mistral Large (latest)",
                tier="free",
                routing_tier="moyen",
                context_input=128000,
                context_output=8192,
                cost_input_per_m=0.0,
                cost_output_per_m=0.0,
                supports_tools=True,
                supports_vision=True,
                supports_json_mode=True,
                supports_streaming=True,
                speciality="raisonnement",
                recommended_use="Raisonnement fort, RAG en français, Tool Calling",
                notes=_NOTES_EXPERIMENT,
            ),
            ModelSpec(
                id="codestral-latest",
                display_name="Codestral (latest)",
                tier="free",
                routing_tier="moyen",
                context_input=32000,
                context_output=8192,
                cost_input_per_m=0.0,
                cost_output_per_m=0.0,
                supports_tools=True,
                supports_json_mode=True,
                supports_streaming=True,
                speciality="code",
                recommended_use="Génération de code et FIM",
                notes=_NOTES_EXPERIMENT,
            ),
            ModelSpec(
                id="open-mistral-nemo",
                display_name="Mistral Nemo (latest)",
                tier="free",
                context_input=128000,
                context_output=8192,
                cost_input_per_m=0.0,
                cost_output_per_m=0.0,
                supports_tools=True,
                supports_json_mode=True,
                supports_streaming=True,
                speciality="agents_lowcost",
                recommended_use="Worker rapide en français",
                notes=_NOTES_EXPERIMENT,
            ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    def build(self, credentials: dict[str, str], model: str | None = None) -> Any:
        """Construit le transport OpenAI-compatible pour un modèle Mistral.

        Import local de `OpenAICompatibleProvider` : le module importe httpx et met en
        place un pool partagé au chargement, on ne le paie que si un transport est
        réellement construit (lire un descripteur reste gratuit et sans effet de bord).
        """
        env_var = self.descriptor.auth.env_var or ""
        cle = (credentials.get(env_var) or "").strip()
        if not cle:
            raise CredentialsManquantes(
                f"{env_var} absente — provider 'mistral' non construit. "
                f"Ajoutez {env_var}=... dans moteur_agents/.env"
            )

        par_id = {m.id: m for m in self.descriptor.models}
        modele = model or self.descriptor.models[0].id
        if modele not in par_id:
            raise CredentialsManquantes(
                f"Modèle '{modele}' non déclaré par le provider 'mistral' "
                f"(déclarés : {', '.join(sorted(par_id))})."
            )

        from core.openai_compat_provider import OpenAICompatibleProvider

        transport = OpenAICompatibleProvider(
            provider_name="Mistral",
            base_url=self.descriptor.api_endpoint or "",
            api_key=cle,
            # `nom_api` et pas `id` : le catalogue peut porter un alias désambiguïsé,
            # l'API veut son propre nom (cf. le 404 de `gemma-4-31b-cerebras`).
            model=par_id[modele].nom_api,
        )
        verifier_interface_transport(transport)
        return transport


class LMStudioLocalPlugin(ProviderPlugin):
    """Provider LM Studio (Local) — étape 2 (#T231)."""

    _DESCRIPTEUR = ProviderDescriptor(
        id='local',
        name='LM Studio (Local)',
        type='local',
        transport='local',
        api_endpoint='http://192.168.1.10:1234/v1/chat/completions',
        auth=AuthSpec(kind='none', instructions='Aucune clé : serveur LM Studio sur le LAN (192.168.1.10:1234).'),
        confidentiality='total',
        cascade_priority=1.0,
        notes='GPU locale (16GB VRAM), 9 modèles chargés. Coût zéro, confidentialité totale. Curation étape 2 : Zoo local LM Studio déclaré tel quel (9 modèles) : le contenu est dynamique par nature, la découverte live relève de discover_models. Signal : gemma-4-31b = 17 appels, seul modèle local réellement consommé.',
        models=(
        ModelSpec(
            id='qwen2.5-14b-instruct-1m',
            display_name='Qwen 2.5 14B Instruct 1M',
            tier='local',
            context_input=1000000,
            context_output=32768,
            supports_tools=True,
            supports_json_mode=True,
            speciality='agents_locaux',
            recommended_use='Tier Léger — agents locaux, RAG confidentiel',
        ),
        ModelSpec(
            id='qwen3-coder-next',
            display_name='Qwen 3 Coder Next',
            tier='local',
            routing_tier='leger',
            context_input=131072,
            context_output=32768,
            supports_tools=True,
            speciality='code',
            recommended_use='Génération de code local',
        ),
        ModelSpec(
            id='qwen2.5-coder-32b',
            display_name='Qwen 2.5 Coder 32B',
            tier='local',
            context_input=131072,
            context_output=32768,
            supports_tools=True,
            speciality='code',
            recommended_use='Refactoring lourd confidentiel',
        ),
        ModelSpec(
            id='gemma-4-31b',
            display_name='Google Gemma 4 31B',
            tier='local',
            routing_tier='leger',
            context_input=131072,
            context_output=32768,
            speciality='polyvalent',
            recommended_use='Modèle Google local dense',
        ),
        ModelSpec(
            id='gemma-4-26b-a4b',
            display_name='Google Gemma 4 26B MoE (A4B)',
            tier='local',
            context_input=131072,
            context_output=32768,
            speciality='polyvalent_rapide',
            recommended_use='MoE rapide local — classification',
        ),
        ModelSpec(
            id='gemma-4-e4b',
            display_name='Google Gemma 4 E4B',
            tier='local',
            context_input=131072,
            context_output=32768,
            speciality='ultra_rapide',
            recommended_use='Le plus rapide localement (65 tok/s)',
        ),
        ModelSpec(
            id='qwen3.6-35b-a3b',
            display_name='Qwen 3.6 35B MoE (A3B)',
            tier='local',
            context_input=131072,
            context_output=32768,
            speciality='polyvalent',
            recommended_use='MoE local Qwen (thinking mode)',
        ),
        ModelSpec(
            id='deepseek-coder-v2-lite',
            display_name='DeepSeek Coder V2 Lite',
            tier='local',
            routing_tier='leger',
            context_input=131072,
            context_output=16384,
            speciality='code_rapide',
            recommended_use='Code léger rapide',
        ),
        ModelSpec(
            id='nomic-embed-text-v1.5',
            display_name='Nomic Embed Text V1.5',
            tier='local',
            context_input=8192,
            speciality='embeddings',
            recommended_use='Embeddings airgapped pour RAG',
        ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    def build(self, credentials, model=None):
        from core.llm.providers.deepseek import LMStudioProvider
        transport = LMStudioProvider()
        verifier_interface_transport(transport)
        return transport



class OllamaLocalPlugin(_PluginOpenAICompat):
    """Provider Ollama (Local PC) — étape 2 (#T231)."""

    _DESCRIPTEUR = ProviderDescriptor(
        id='ollama_local',
        name='Ollama (Local PC)',
        type='local',
        transport='openai_compat',
        api_endpoint='http://127.0.0.1:11434/v1/chat/completions',
        auth=AuthSpec(kind='none', instructions='Aucune clé : démon Ollama local (127.0.0.1:11434).'),
        confidentiality='total',
        cascade_priority=1.1,
        notes="Ollama local sur GPU locale. Inférence ultra-rapide. Curation étape 2 : 2 modèles du seed + le fine-tune domotique câblé mais absent du catalogue. ollama_pc (LAN 192.168.1.10) reste hors descripteur : c'est un accès distant au MÊME service, pas un provider distinct.",
        models=(
        ModelSpec(
            id='qwen2.5-coder:7b',
            display_name='Qwen 2.5 Coder 7B (Ollama)',
            tier='local',
            context_input=131072,
            context_output=8192,
            supports_tools=True,
            supports_json_mode=True,
            speciality='code',
            recommended_use='Génération de code rapide sur GPU RTX',
        ),
        ModelSpec(
            id='deepseek-r1:8b',
            display_name='DeepSeek R1 8B (Ollama)',
            tier='local',
            context_input=131072,
            context_output=8192,
            speciality='raisonnement',
            recommended_use='Raisonnement logique avec thinking local',
        ),
        ModelSpec(
            id='domotique-qwen7b:q4',
            display_name='Domotique Qwen 7B (fine-tune q4)',
            tier='local',
            context_input=32768,
            context_output=4096,
            speciality='domotique',
            recommended_use='Fast path vocal/domotique — fine-tune QLoRA du projet',
            notes='Câblé dans le gateway (clé principale du provider) mais absent du seed — déclaré ici pour aligner catalogue et gateway.',
        ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    def build(self, credentials, model=None):
        # Pas de clé : Ollama local n'authentifie pas (le gateway passe "ollama").
        par_id = {m.id: m for m in self.descriptor.models}
        modele = model or self.descriptor.models[0].id
        if modele not in par_id:
            raise CredentialsManquantes(f"Modèle '{modele}' non déclaré par 'ollama_local'.")
        return self._build_compat('ollama_local', par_id[modele], 'ollama')



class AjeanPlugin(_PluginOpenAICompat):
    """Provider AJEAN (llama.cpp / llama-server, port 8080).

    Déclaration purement déclarative : AJEAN n'est PAS encore mis en tête de cascade
    (FAST_PATH_PROVIDERS intact, `services/pipeline_service.py` non modifié). L'hôte
    n'écoute pas encore sur le LAN — action d'Axel (basculer llama-server sur 0.0.0.0 +
    ouvrir le pare-feu 8080). D'ici là, `ajean_pc` échoue en connect timeout et la
    cascade bascule en silence sur le cloud.
    """

    _DESCRIPTEUR = ProviderDescriptor(
        id='ajean_pc',
        name='AJEAN (llama.cpp PC via LAN)',
        type='local',
        transport='openai_compat',
        api_endpoint='http://192.168.1.10:8080/v1/chat/completions',
        auth=AuthSpec(kind='none', instructions='Aucune clé : llama.cpp n\'authentifie pas (API OpenAI-compatible, port 8080).'),
        confidentiality='total',
        cascade_priority=1.1,
        notes="AJEAN = llama.cpp (llama-server, build b10451) sur le PC d'Axel, Qwen2.5-14B-Instruct-1M-Q4_K_M. Déclaré par IP LAN (192.168.1.10), jamais par nom DNS, pour rester dans la famille locale (#T337, _est_hote_local). ajean_deck (127.0.0.1) est l'accès loopback au MÊME service, pour l'instance Deck à venir — pas un provider distinct, d'où l'absence de descripteur propre (même logique qu'ollama_local/ollama_pc). NON branché en cascade tant que l'hôte n'écoute pas sur le LAN (action d'Axel : --host 0.0.0.0 + pare-feu 8080).",
        models=(
        ModelSpec(
            id=AJEAN_DEFAULT_MODEL,
            display_name='Qwen 2.5 14B Instruct 1M (AJEAN)',
            tier='local',
            context_input=32768,
            context_output=8192,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='domotique',
            recommended_use='Inférence locale llama.cpp — contexte 1M, Q4_K_M sur GPU',
        ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    def build(self, credentials, model=None):
        # Pas de clé : llama.cpp n'authentifie pas (le gateway passe "ajean").
        par_id = {m.id: m for m in self.descriptor.models}
        modele = model or self.descriptor.models[0].id
        if modele not in par_id:
            raise CredentialsManquantes(f"Modèle '{modele}' non déclaré par 'ajean_pc'.")
        return self._build_compat('ajean_pc', par_id[modele], 'ajean')



class GeminiFreePlugin(ProviderPlugin):
    """Provider Gemini Free Tier (AI Studio) — étape 2 (#T231)."""

    _DESCRIPTEUR = ProviderDescriptor(
        id='gemini_free',
        name='Gemini Free Tier (AI Studio)',
        type='free',
        transport='native',
        api_endpoint='https://generativelanguage.googleapis.com/v1beta',
        auth=AuthSpec(
            kind='api_key',
            env_var='GEMINI_API_KEY',
            instructions="Clé AI Studio gratuite ; jusqu'à 6 clés rotatives (GEMINI_API_KEY_2..6) via KeyPool.",
        ),
        confidentiality='training',
        cascade_priority=2.0,
        notes='5 clés Free Tier rotatives via KeyPool. Cache implicite gratuit. Curation étape 2 : Seuls les 3 modèles câblés par le gateway (GeminiNativeProvider + KeyPool). flash-lite = 60 appels, 3.5-flash = 13 — les entrées image/preview/doublons -free du catalogue local ne sont ni câblées ni appelées.',
        models=(
        ModelSpec(
            id='gemini-3.5-flash',
            display_name='Gemini 3.5 Flash',
            tier='free',
            routing_tier='moyen',
            context_input=1048576,
            context_output=65536,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='agents_rapides',
            recommended_use='Tier Léger/Moyen — le plus rapide, gratuit, cache implicite',
        ),
        ModelSpec(
            id='gemini-3.1-flash-lite',
            display_name='Gemini 3.1 Flash Lite',
            tier='free',
            routing_tier='leger',
            context_input=1048576,
            context_output=65536,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='ultra_rapide',
            recommended_use='Tâches simples ultra-rapides',
        ),
        ModelSpec(
            id='gemini-2.5-flash',
            display_name='Gemini 2.5 Flash',
            tier='free',
            context_input=1048576,
            context_output=65536,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='raisonnement',
            recommended_use='Raisonnement Tier Léger — backup de 3.5 Flash',
        ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    # Drapeaux par modèle, recopiés de llm_gateway.py (natif payant : grounding
    # débloqué ; natif gratuit : cache explicite désactivé + KeyPool).
    _GRATUIT = True

    def build(self, credentials, model=None):
        cle = (credentials.get('GEMINI_API_KEY') or '').strip()
        if not cle:
            raise CredentialsManquantes(
                "GEMINI_API_KEY absente — provider 'gemini_free' non construit.")
        par_id = {m.id: m for m in self.descriptor.models}
        modele = model or self.descriptor.models[0].id
        if modele not in par_id:
            raise CredentialsManquantes(f"Modèle '{modele}' non déclaré par 'gemini_free'.")
        nom_api = par_id[modele].nom_api
        try:
            from core.gemini_native import GeminiNativeProvider
        except ImportError:
            GeminiNativeProvider = None
        if GeminiNativeProvider:
            if self._GRATUIT:
                transport = GeminiNativeProvider(
                    api_key=cle, model=nom_api,
                    search_grounding_available=False,
                    enable_explicit_cache=False,  # cache explicite = payant uniquement
                    use_key_pool=True,
                )
            else:
                # Payant : grounding débloqué partout sauf TTS ; cache explicite
                # désactivé sur TTS et 3.1-flash-lite (comportement gateway).
                transport = GeminiNativeProvider(
                    api_key=cle, model=nom_api,
                    search_grounding_available=(nom_api != 'gemini-2.0-flash-tts'),
                    enable_explicit_cache=(nom_api not in ('gemini-2.0-flash-tts', 'gemini-3.1-flash-lite')),
                )
        else:
            from core.llm.providers.gemini import GeminiProvider
            transport = GeminiProvider(api_key=cle, model=nom_api)
        verifier_interface_transport(transport)
        return transport



class GeminiPaidPlugin(ProviderPlugin):
    """Provider Gemini API (GCP Payant) — étape 2 (#T231)."""

    _DESCRIPTEUR = ProviderDescriptor(
        id='gemini_paid',
        name='Gemini API (GCP Payant)',
        type='pay_as_you_go',
        transport='native',
        api_endpoint='https://generativelanguage.googleapis.com/v1beta',
        auth=AuthSpec(
            kind='api_key',
            env_var='GEMINI_PAYANT_API_KEY',
            instructions='Clé GCP payante — Search Grounding débloqué, facturation EUR.',
        ),
        confidentiality='none',
        cascade_priority=5.0,
        notes="Tarifs GCP en EUR. Search Grounding débloqué. Context Caching -90%. Curation étape 2 : Les 9 modèles du seed, tous câblés par le gateway (grounding débloqué sur la clé GCP). Les ids -paid sont des alias de désambiguïsation ; api_model_id porte le nom réel de l'API.",
        models=(
        ModelSpec(
            id='gemini-3.5-flash-paid',
            api_model_id='gemini-3.5-flash',
            display_name='Gemini 3.5 Flash (GCP)',
            tier='paid',
            routing_tier='moyen',
            context_input=1048576,
            context_output=65536,
            cost_input_per_m=1.282575,
            cost_output_per_m=7.69545,
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='agents_grounding',
            recommended_use='API payante avec Search Grounding débloqué',
        ),
        ModelSpec(
            id='gemini-2.5-pro-paid',
            api_model_id='gemini-2.5-pro',
            display_name='Gemini 2.5 Pro (GCP)',
            tier='paid',
            context_input=2097152,
            context_output=65536,
            cost_input_per_m=1.068812,
            cost_output_per_m=8.5505,
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='raisonnement_pro',
            recommended_use='Raisonnement avancé GCP — context 2M',
        ),
        ModelSpec(
            id='gemini-2.5-flash-paid',
            api_model_id='gemini-2.5-flash',
            display_name='Gemini 2.5 Flash (GCP)',
            tier='paid',
            context_input=1048576,
            context_output=65536,
            cost_input_per_m=0.256515,
            cost_output_per_m=2.137625,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='economique',
            recommended_use='Backup payant économique',
        ),
        ModelSpec(
            id='gemini-2.0-flash-tts-paid',
            api_model_id='gemini-2.0-flash-tts',
            display_name='Gemini 2.0 Flash TTS (GCP)',
            tier='paid',
            context_input=1048576,
            context_output=65536,
            speciality='tts',
            recommended_use='Synthèse vocale native (TTS multimodal)',
        ),
        ModelSpec(
            id='gemini-3.1-flash-lite-paid',
            api_model_id='gemini-3.1-flash-lite',
            display_name='Gemini 3.1 Flash Lite (GCP)',
            tier='paid',
            context_input=1048576,
            context_output=65536,
            cost_input_per_m=0.213762,
            cost_output_per_m=1.282575,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='ultra_economique',
            recommended_use='Le moins cher en GCP payant',
        ),
        ModelSpec(
            id='gemini-3-pro-short-paid',
            api_model_id='gemini-3-pro-preview',
            display_name='Gemini 3 Pro Short (GCP)',
            tier='paid',
            context_input=2097152,
            context_output=65536,
            cost_input_per_m=1.7101,
            cost_output_per_m=10.2606,
            supports_tools=True,
            supports_streaming=True,
            speciality='raisonnement_pro',
            recommended_use='Alias court → 3-pro-preview',
            notes='Alias court',
        ),
        ModelSpec(
            id='gemini-3.1-pro-preview-paid',
            api_model_id='gemini-3.1-pro-preview',
            display_name='Gemini 3.1 Pro Preview (GCP)',
            tier='paid',
            routing_tier='fort',
            context_input=2097152,
            context_output=65536,
            cost_input_per_m=1.7101,
            cost_output_per_m=10.2606,
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='raisonnement_pro',
            recommended_use='Pro avec Grounding + context 2M',
        ),
        ModelSpec(
            id='gemini-3.1-pro-customtools-paid',
            api_model_id='gemini-3.1-pro-preview-customtools',
            display_name='Gemini 3.1 Pro Custom Tools (GCP)',
            tier='paid',
            routing_tier='fort',
            context_input=2097152,
            context_output=65536,
            cost_input_per_m=1.7101,
            cost_output_per_m=10.2606,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='tool_use',
            recommended_use='Pro optimisé Tool Use',
        ),
        ModelSpec(
            id='gemini-3-pro-preview-paid',
            api_model_id='gemini-3-pro-preview',
            display_name='Gemini 3 Pro Preview (GCP)',
            tier='paid',
            routing_tier='fort',
            context_input=2097152,
            context_output=65536,
            cost_input_per_m=1.7101,
            cost_output_per_m=10.2606,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='raisonnement_pro',
            recommended_use='Pro preview GCP',
        ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    # Drapeaux par modèle, recopiés de llm_gateway.py (natif payant : grounding
    # débloqué ; natif gratuit : cache explicite désactivé + KeyPool).
    _GRATUIT = False

    def build(self, credentials, model=None):
        cle = (credentials.get('GEMINI_PAYANT_API_KEY') or '').strip()
        if not cle:
            raise CredentialsManquantes(
                "GEMINI_PAYANT_API_KEY absente — provider 'gemini_paid' non construit.")
        par_id = {m.id: m for m in self.descriptor.models}
        modele = model or self.descriptor.models[0].id
        if modele not in par_id:
            raise CredentialsManquantes(f"Modèle '{modele}' non déclaré par 'gemini_paid'.")
        nom_api = par_id[modele].nom_api
        try:
            from core.gemini_native import GeminiNativeProvider
        except ImportError:
            GeminiNativeProvider = None
        if GeminiNativeProvider:
            if self._GRATUIT:
                transport = GeminiNativeProvider(
                    api_key=cle, model=nom_api,
                    search_grounding_available=False,
                    enable_explicit_cache=False,  # cache explicite = payant uniquement
                    use_key_pool=True,
                )
            else:
                # Payant : grounding débloqué partout sauf TTS ; cache explicite
                # désactivé sur TTS et 3.1-flash-lite (comportement gateway).
                transport = GeminiNativeProvider(
                    api_key=cle, model=nom_api,
                    search_grounding_available=(nom_api != 'gemini-2.0-flash-tts'),
                    enable_explicit_cache=(nom_api not in ('gemini-2.0-flash-tts', 'gemini-3.1-flash-lite')),
                )
        else:
            from core.llm.providers.gemini import GeminiProvider
            transport = GeminiProvider(api_key=cle, model=nom_api)
        verifier_interface_transport(transport)
        return transport



class GeminiCLIPluginInterne(ProviderPlugin):
    """Provider Gemini Advanced (CLI Antigravity) — étape 2 (#T231)."""

    _DESCRIPTEUR = ProviderDescriptor(
        id='gemini_cli',
        name='Gemini Advanced (CLI Antigravity)',
        type='subscription',
        transport='cli',
        api_endpoint='antigravity-ide.cmd',
        auth=AuthSpec(kind='cli', instructions='Binaire Antigravity IDE requis (abonnement Google One AI Pro).'),
        confidentiality='none',
        cascade_priority=3.0,
        notes='Inclus dans Google One AI Pro (19.99$/mois). Fenêtre 1M-2M tokens. Curation étape 2 : Les 3 modèles du seed, tous câblés (CLI Antigravity, abonnement).',
        models=(
        ModelSpec(
            id='gemini-cli',
            display_name='Gemini CLI (défaut)',
            tier='subscription',
            context_input=1048576,
            context_output=65536,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            speciality='agents_cli',
            recommended_use='CLI Antigravity IDE — mode agent par défaut',
        ),
        ModelSpec(
            id='gemini-3.5-flash-high-cli',
            display_name='Gemini 3.5 Flash (CLI High)',
            tier='subscription',
            routing_tier='fort',
            context_input=1048576,
            context_output=65536,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_vision=True,
            supports_streaming=True,
            speciality='agents_cli',
            recommended_use='CLI Antigravity IDE — mode high compute',
            notes='Amorti ~0.20$/M tokens',
        ),
        ModelSpec(
            id='gemini-3.5-flash-medium-cli',
            display_name='Gemini 3.5 Flash (CLI Medium)',
            tier='subscription',
            routing_tier='moyen',
            context_input=1048576,
            context_output=65536,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_streaming=True,
            speciality='agents_cli',
            recommended_use='CLI Antigravity IDE — mode medium compute',
        ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    def build(self, credentials, model=None):
        if not _binaire_cli_present((
            "antigravity-ide", "antigravity-ide.cmd", "antigravity", "antigravity.exe",
        )):
            raise CredentialsManquantes(
                "Binaire Antigravity IDE absent de cet hôte — 'gemini_cli' non construit.")
        par_id = {m.id: m for m in self.descriptor.models}
        modele = model or self.descriptor.models[0].id
        if modele not in par_id:
            raise CredentialsManquantes(f"Modèle '{modele}' non déclaré par 'gemini_cli'.")
        from core.llm.providers.gemini import GeminiCLIProvider
        if modele == 'gemini-cli':
            transport = GeminiCLIProvider()
        else:
            transport = GeminiCLIProvider(mode='chat', model_name=modele)
        verifier_interface_transport(transport)
        return transport



class ClaudeCLIPluginInterne(ProviderPlugin):
    """Provider Anthropic (CLI Claude Pro) — étape 2 (#T231)."""

    _DESCRIPTEUR = ProviderDescriptor(
        id='claude_cli',
        name='Anthropic (CLI Claude Pro)',
        type='subscription',
        transport='cli',
        api_endpoint='claude.cmd',
        auth=AuthSpec(kind='cli', instructions='Binaire `claude` requis (abonnement Claude Pro/Max).'),
        confidentiality='none',
        cascade_priority=3.5,
        notes='Inclus dans Claude Pro (20$/mois). SWE-bench 87.6%. Curation étape 2 : Les 9 modèles du seed, tous câblés (aliases legacy inclus). Les appels CLI sont comptés par cli_token_collector, pas par id de modèle — le signal « appels » est aveugle ici, on garde tout ce qui est câblé.',
        models=(
        ModelSpec(
            id='claude-opus-4-8',
            display_name='Claude Opus 4.8',
            tier='subscription',
            routing_tier='fort',
            context_input=200000,
            context_output=32768,
            cost_input_per_m=5.0,
            cost_output_per_m=25.0,
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='architecture',
            recommended_use='Tier Fort — le plus puissant en agentic coding (depuis 2026-05-28)',
            notes='Tarifs référence API, inclus dans le forfait Pro',
        ),
        ModelSpec(
            id='claude-opus-4-7',
            display_name='Claude Opus 4.7',
            tier='subscription',
            routing_tier='fort',
            context_input=200000,
            context_output=32768,
            cost_input_per_m=5.0,
            cost_output_per_m=25.0,
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='architecture',
            recommended_use='Génération stable précédente',
            notes='Tarifs référence API, inclus dans le forfait Pro',
        ),
        ModelSpec(
            id='claude-opus-4-5',
            display_name='Claude Opus 4.5',
            tier='subscription',
            context_input=200000,
            context_output=32768,
            cost_input_per_m=5.0,
            cost_output_per_m=25.0,
            supports_tools=True,
            supports_vision=True,
            speciality='architecture',
            recommended_use='Génération stable précédente',
        ),
        ModelSpec(
            id='claude-opus-4-0',
            display_name='Claude Opus 4.0 (alias → 4.8)',
            tier='subscription',
            context_input=200000,
            context_output=32768,
            speciality='architecture',
            recommended_use='Alias legacy → redirige vers opus-4-8',
            notes='Alias legacy',
        ),
        ModelSpec(
            id='claude-sonnet-4-6',
            display_name='Claude Sonnet 4.6',
            tier='subscription',
            routing_tier='fort',
            context_input=200000,
            context_output=32768,
            cost_input_per_m=3.0,
            cost_output_per_m=15.0,
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='pair_programming',
            recommended_use='Défaut CLI — bon équilibre rapidité/qualité',
        ),
        ModelSpec(
            id='claude-sonnet-4-5',
            display_name='Claude Sonnet 4.5',
            tier='subscription',
            context_input=200000,
            context_output=32768,
            cost_input_per_m=3.0,
            cost_output_per_m=15.0,
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='pair_programming',
            recommended_use='Génération précédente stable',
        ),
        ModelSpec(
            id='claude-haiku-4-5',
            display_name='Claude Haiku 4.5',
            tier='subscription',
            context_input=200000,
            context_output=32768,
            cost_input_per_m=1.0,
            cost_output_per_m=5.0,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='rapide',
            recommended_use='Tâches légères rapides via CLI',
        ),
        ModelSpec(
            id='claude-sonnet-4.6-thinking-cli',
            display_name='Claude Sonnet 4.6 Thinking CLI (legacy)',
            tier='subscription',
            routing_tier='moyen',
            context_input=200000,
            context_output=32768,
            speciality='pair_programming',
            recommended_use='Alias legacy → sonnet-4-6',
            notes='Alias legacy',
        ),
        ModelSpec(
            id='claude-opus-4.6-thinking-cli',
            display_name='Claude Opus 4.6 Thinking CLI (legacy)',
            tier='subscription',
            routing_tier='fort',
            context_input=200000,
            context_output=32768,
            speciality='architecture',
            recommended_use='Alias legacy → opus-4-8',
            notes='Alias legacy',
        ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    def build(self, credentials, model=None):
        if not _binaire_cli_present(("claude", "claude.cmd")):
            raise CredentialsManquantes(
                "Binaire `claude` absent de cet hôte — 'claude_cli' non construit.")
        par_id = {m.id: m for m in self.descriptor.models}
        modele = model or self.descriptor.models[0].id
        if modele not in par_id:
            raise CredentialsManquantes(f"Modèle '{modele}' non déclaré par 'claude_cli'.")
        from core.llm.providers.deepseek import _make_claude
        # Les alias legacy du catalogue pointent vers le modèle réel côté CLI
        # (même comportement que le gateway, llm_gateway.py:484-485).
        cibles = {
            'claude-sonnet-4.6-thinking-cli': 'claude-sonnet-4-6',
            'claude-opus-4.6-thinking-cli': 'claude-opus-4-8',
            'claude-opus-4-0': 'claude-opus-4-8',
        }
        transport = _make_claude(cibles.get(modele, par_id[modele].nom_api))
        verifier_interface_transport(transport)
        return transport



class AnthropicNativePlugin(ProviderPlugin):
    """Provider Anthropic API — étape 2 (#T231)."""

    _DESCRIPTEUR = ProviderDescriptor(
        id='anthropic_native',
        name='Anthropic API',
        type='pay_as_you_go',
        transport='native',
        api_endpoint='https://api.anthropic.com/v1/messages',
        auth=AuthSpec(
            kind='api_key',
            env_var='ANTHROPIC_API_KEY',
            instructions='Clé API Anthropic directe — indépendante des quotas du CLI Claude Pro.',
        ),
        confidentiality='none',
        cascade_priority=3.4,
        notes='API native. 13/08 Axel : crédit à sec → hors cascade (EXCLUSIONS_CASCADE_DEFAUT), clé conservée. claude-fable-5 déjà hors tiers (D-4). Accès explicite inchangé.',
        models=(
        ModelSpec(
            id='claude-sonnet-5',
            display_name='Claude Sonnet 5 (API directe)',
            tier='moyen',
            context_input=1000000,
            context_output=128000,
            cost_input_per_m=2.0,
            cost_output_per_m=10.0,
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='pair_programming',
            recommended_use='Meilleur rapport qualité/prix Anthropic — proche du niveau Opus en code/agentique',
            notes="Tarif d'intro 2.0$/10.0$ par M tokens jusqu'au 31/08/2026 (puis 3.0$/15.0$). Thinking adaptatif actif par défaut.",
        ),
        ModelSpec(
            id='claude-fable-5',
            display_name='Claude Fable 5 (API directe)',
            tier='fort',
            context_input=1000000,
            context_output=128000,
            cost_input_per_m=10.0,
            cost_output_per_m=50.0,
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='raisonnement_extreme',
            recommended_use="Modèle le plus capable d'Anthropic — raisonnement long-horizon, tâches agentiques les plus dures",
            notes='Thinking toujours actif. Nécessite rétention de données ≥30 jours (indisponible en ZDR). Tarif nettement supérieur à Opus.',
        ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    def build(self, credentials, model=None):
        cle = (credentials.get('ANTHROPIC_API_KEY') or '').strip()
        if not cle:
            raise CredentialsManquantes(
                "ANTHROPIC_API_KEY absente — provider 'anthropic_native' non construit.")
        par_id = {m.id: m for m in self.descriptor.models}
        modele = model or self.descriptor.models[0].id
        if modele not in par_id:
            raise CredentialsManquantes(f"Modèle '{modele}' non déclaré par 'anthropic_native'.")
        from core.anthropic_native_provider import AnthropicNativeProvider
        transport = AnthropicNativeProvider(api_key=cle, model=par_id[modele].nom_api)
        verifier_interface_transport(transport)
        return transport



class DeepSeekPlugin(_PluginOpenAICompat):
    """Provider DeepSeek API — étape 2 (#T231)."""

    _DESCRIPTEUR = ProviderDescriptor(
        id='deepseek',
        name='DeepSeek API',
        type='pay_as_you_go',
        transport='openai_compat',
        api_endpoint='https://api.deepseek.com/chat/completions',
        auth=AuthSpec(
            kind='api_key',
            env_var='DEEPSEEK_API_KEY',
            instructions='Clé API DeepSeek (solde prépayé).',
        ),
        confidentiality='training',
        cascade_priority=4.0,
        notes='Ratio coût/intelligence imbattable. V4-Flash à $0.14/$0.28/M. Curation étape 2 : Les 4 modèles du seed, tous câblés ; deepseek-chat = 37 appels (4e modèle le plus appelé en prod). v4-flash/v4-pro passent par le même endpoint deepseek-chat côté transport (comportement du gateway recopié, llm_gateway.py:225-226) — api_model_id le porte.',
        models=(
        ModelSpec(
            id='deepseek-chat',
            display_name='DeepSeek Chat (alias V4 Flash)',
            tier='paid',
            routing_tier='leger',
            context_input=1000000,
            context_output=65536,
            cost_input_per_m=0.14,
            cost_output_per_m=0.28,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='agents_lowcost',
            recommended_use='Alias legacy → redirige vers V4 Flash',
            notes='Alias legacy',
        ),
        ModelSpec(
            id='deepseek-reasoner',
            display_name='DeepSeek Reasoner (legacy R1)',
            tier='paid',
            routing_tier='fort',
            context_input=1000000,
            context_output=65536,
            cost_input_per_m=0.55,
            cost_output_per_m=2.19,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='raisonnement',
            recommended_use='Legacy R1 — migration vers V4 recommandée',
            notes='Legacy R1',
        ),
        ModelSpec(
            id='deepseek-v4-flash',
            api_model_id='deepseek-chat',
            display_name='DeepSeek V4 Flash',
            tier='paid',
            routing_tier='moyen',
            context_input=1000000,
            context_output=65536,
            cost_input_per_m=0.14,
            cost_output_per_m=0.28,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='agents_lowcost',
            recommended_use='Tâches de routine à très bas coût',
        ),
        ModelSpec(
            id='deepseek-v4-pro',
            api_model_id='deepseek-chat',
            display_name='DeepSeek V4 Pro',
            tier='paid',
            routing_tier='fort',
            context_input=1000000,
            context_output=65536,
            cost_input_per_m=0.435,
            cost_output_per_m=0.87,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='raisonnement',
            recommended_use='Planification multi-étapes — tarif permanent (ex-promo)',
        ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    def build(self, credentials, model=None):
        cle = (credentials.get('DEEPSEEK_API_KEY') or '').strip()
        if not cle:
            raise CredentialsManquantes(
                "DEEPSEEK_API_KEY absente — provider 'deepseek' non construit.")
        par_id = {m.id: m for m in self.descriptor.models}
        modele = model or self.descriptor.models[0].id
        if modele not in par_id:
            raise CredentialsManquantes(f"Modèle '{modele}' non déclaré par 'deepseek'.")
        return self._build_compat('deepseek', par_id[modele], cle)



class CoherePlugin(_PluginOpenAICompat):
    """Provider Cohere API — étape 2 (#T231)."""

    _DESCRIPTEUR = ProviderDescriptor(
        id='cohere',
        name='Cohere API',
        type='pay_as_you_go',
        transport='openai_compat',
        api_endpoint='https://api.cohere.com/compatibility/v1/chat/completions',
        auth=AuthSpec(
            kind='api_key',
            env_var='COHERE_API_KEY',
            instructions='Trial Key gratuite (10 RPM).',
        ),
        confidentiality='training',
        cascade_priority=3.9,
        notes='Trial Key (Gratuit, 10 RPM). Champion du RAG et du Tool Calling. Curation étape 2 : Les 2 modèles gratuits câblés. Les 4 command-a payants ne sont PAS déclarés : compte Trial Key, gel explicite #T183 (Axel ne passe pas Cohere en payant) — les déclarer recréerait du « au catalogue mais inutilisable ».',
        models=(
        ModelSpec(
            id='command-r-08-2024',
            display_name='Cohere Command R (08-2024)',
            tier='free',
            context_input=128000,
            context_output=4096,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='agents_rapides',
            recommended_use='RAG rapide, agents',
            notes='Trial Key (Gratuit)',
        ),
        ModelSpec(
            id='command-r-plus-08-2024',
            display_name='Cohere Command R+ (08-2024)',
            tier='free',
            context_input=128000,
            context_output=4096,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='raisonnement',
            recommended_use='RAG avancé, Tool Calling, planifications',
            notes='Trial Key (Gratuit)',
        ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    def build(self, credentials, model=None):
        cle = (credentials.get('COHERE_API_KEY') or '').strip()
        if not cle:
            raise CredentialsManquantes(
                "COHERE_API_KEY absente — provider 'cohere' non construit.")
        par_id = {m.id: m for m in self.descriptor.models}
        modele = model or self.descriptor.models[0].id
        if modele not in par_id:
            raise CredentialsManquantes(f"Modèle '{modele}' non déclaré par 'cohere'.")
        return self._build_compat('cohere', par_id[modele], cle)



class CerebrasPlugin(_PluginOpenAICompat):
    """Provider Cerebras API — étape 2 (#T231)."""

    _DESCRIPTEUR = ProviderDescriptor(
        id='cerebras',
        name='Cerebras API',
        type='pay_as_you_go',
        transport='openai_compat',
        api_endpoint='https://api.cerebras.ai/v1/chat/completions',
        auth=AuthSpec(
            kind='api_key',
            env_var='CEREBRAS_API_KEY',
            instructions='Clé gratuite pour gpt-oss-120b ; CEREBRAS_PAYANT_API_KEY optionnelle pour gemma/zai (la clé free sert de secours).',
        ),
        confidentiality='none',
        cascade_priority=2.3,
        notes="Clé payante. Inférence Wafer Scale ultra-rapide (Cerebras Paid). Curation étape 2 : Les 3 modèles du seed, tous câblés. gpt-oss-120b = 8 appels (tête du fast path vocal #T206). gemma-4-31b-cerebras : l'alias du catalogue renvoyait HTTP 404 chez Cerebras (vérifié 10/08 par appel réel) — api_model_id rétablit le vrai nom.",
        models=(
        ModelSpec(
            id='gpt-oss-120b',
            display_name='GPT OSS 120B (Cerebras)',
            tier='paid',
            routing_tier='fort',
            context_input=8192,
            context_output=4096,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='raisonnement',
            recommended_use='Modèle géant 120B ultra-rapide (Cerebras)',
            notes='Clé Payante (Cerebras)',
        ),
        ModelSpec(
            id='gemma-4-31b-cerebras',
            api_model_id='gemma-4-31b',
            display_name='Gemma 4 31B (Cerebras)',
            tier='paid',
            routing_tier='moyen',
            context_input=8192,
            context_output=4096,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='ultra_rapide',
            recommended_use='Google Gemma 4 31B ultra-rapide (Cerebras Wafer Scale)',
            notes='Clé Payante (Cerebras)',
        ),
        ModelSpec(
            id='zai-glm-4.7',
            display_name='Zai GLM 4.7 (Cerebras)',
            tier='paid',
            context_input=8192,
            context_output=4096,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='raisonnement',
            recommended_use='Raisonnement polyvalent ultra-rapide (Cerebras)',
            notes='Clé Payante (Cerebras)',
        ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    def build(self, credentials, model=None):
        # Double clé : gpt-oss-120b (rotation BudgetGuard) consomme la clé
        # gratuite ; gemma/zai préfèrent la clé payante, free en secours.
        cle_free = (credentials.get('CEREBRAS_API_KEY') or '').strip()
        cle_payante = (credentials.get('CEREBRAS_PAYANT_API_KEY') or '').strip()
        par_id = {m.id: m for m in self.descriptor.models}
        modele = model or self.descriptor.models[0].id
        if modele not in par_id:
            raise CredentialsManquantes(f"Modèle '{modele}' non déclaré par 'cerebras'.")
        if modele == 'gpt-oss-120b':
            cle = cle_free or cle_payante
        else:
            cle = cle_payante or cle_free
        if not cle:
            raise CredentialsManquantes(
                "CEREBRAS_API_KEY / CEREBRAS_PAYANT_API_KEY absentes — 'cerebras' non construit.")
        return self._build_compat('cerebras', par_id[modele], cle)



class OpenRouterPlugin(_PluginOpenAICompat):
    """Provider OpenRouter API — étape 2 (#T231)."""

    _DESCRIPTEUR = ProviderDescriptor(
        id='openrouter',
        name='OpenRouter API',
        type='pay_as_you_go',
        transport='openai_compat',
        api_endpoint='https://openrouter.ai/api/v1/chat/completions',
        auth=AuthSpec(
            kind='api_key',
            env_var='OPENROUTER_API_KEY',
            instructions='Clé OpenRouter (crédits).',
        ),
        confidentiality='none',
        cascade_priority=2.4,
        notes='13/08 Axel : un seul modèle câblé, openrouter/auto. Les slugs Llama :free répondent 404 (unavailable for free). Les autres entrées catalogue restent offertes, non câblées.',
        models=(
        ModelSpec(
            id='openrouter/auto',
            display_name='OpenRouter Auto',
            tier='paid',
            routing_tier='moyen',
            context_input=131072,
            context_output=4096,
            cost_input_per_m=0.15,
            cost_output_per_m=0.60,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='raisonnement',
            recommended_use='Routeur OpenRouter — choisit un modèle vivant, plus de slug :free mort',
            notes='Décision Axel 13/08. Auto facture le modèle routé (pas gratuit). Coûts catalogue = ordre de grandeur, solde OR = source d\'autorité.',
        ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    def build(self, credentials, model=None):
        cle = (credentials.get('OPENROUTER_API_KEY') or '').strip()
        if not cle:
            raise CredentialsManquantes(
                "OPENROUTER_API_KEY absente — provider 'openrouter' non construit.")
        par_id = {m.id: m for m in self.descriptor.models}
        modele = model or self.descriptor.models[0].id
        if modele not in par_id:
            raise CredentialsManquantes(f"Modèle '{modele}' non déclaré par 'openrouter'.")
        return self._build_compat('openrouter', par_id[modele], cle)



class XAIPlugin(_PluginOpenAICompat):
    """Provider xAI API (Grok) — étape 2 (#T231)."""

    _DESCRIPTEUR = ProviderDescriptor(
        id='xai',
        name='xAI API (Grok)',
        type='pay_as_you_go',
        transport='openai_compat',
        api_endpoint='https://api.x.ai/v1/chat/completions',
        auth=AuthSpec(
            kind='api_key',
            env_var='XAI_API_KEY',
            instructions="Clé xAI (Grok). La gestion d'équipe utilise XAI_MANAGEMENT_API_KEY.",
        ),
        confidentiality='none',
        cascade_priority=4.2,
        notes="Modèles Grok-2 et Grok-Beta. Excellentes capacités de raisonnement. Curation étape 2 : Les 5 modèles câblés par le gateway. grok-imagine-image (génération d'images, hors chat) n'est ni câblé ni appelé — non déclaré.",
        models=(
        ModelSpec(
            id='grok-4.3',
            display_name='Grok 4.3',
            tier='paid',
            context_input=200000,
            context_output=4096,
            cost_input_per_m=1.25,
            cost_output_per_m=2.5,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='raisonnement',
            recommended_use='Modèle phare xAI avec raisonnement intégré',
        ),
        ModelSpec(
            id='grok-4.20-0309-non-reasoning',
            display_name='Grok 4.20 (Non-Reasoning)',
            tier='paid',
            context_input=200000,
            context_output=4096,
            cost_input_per_m=1.25,
            cost_output_per_m=2.5,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='polyvalent',
            recommended_use='Modèle rapide non-raisonnant xAI',
        ),
        ModelSpec(
            id='grok-4.20-0309-reasoning',
            display_name='Grok 4.20 (Reasoning)',
            tier='paid',
            context_input=200000,
            context_output=4096,
            cost_input_per_m=1.25,
            cost_output_per_m=2.5,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='raisonnement',
            recommended_use='Modèle avec raisonnement avancé xAI',
        ),
        ModelSpec(
            id='grok-4.20-multi-agent-0309',
            display_name='Grok 4.20 Multi-Agent',
            tier='paid',
            context_input=200000,
            context_output=4096,
            cost_input_per_m=1.25,
            cost_output_per_m=2.5,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='agents',
            recommended_use='Modèle xAI optimisé pour le multi-agent',
        ),
        ModelSpec(
            id='grok-build-0.1',
            display_name='Grok Build 0.1',
            tier='paid',
            context_input=200000,
            context_output=4096,
            cost_input_per_m=1.0,
            cost_output_per_m=2.0,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='code',
            recommended_use='Modèle xAI spécialisé développement et build',
        ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    def build(self, credentials, model=None):
        cle = (credentials.get('XAI_API_KEY') or '').strip()
        if not cle:
            raise CredentialsManquantes(
                "XAI_API_KEY absente — provider 'xai' non construit.")
        par_id = {m.id: m for m in self.descriptor.models}
        modele = model or self.descriptor.models[0].id
        if modele not in par_id:
            raise CredentialsManquantes(f"Modèle '{modele}' non déclaré par 'xai'.")
        return self._build_compat('xai', par_id[modele], cle)



class DeepInfraPlugin(_PluginOpenAICompat):
    """Provider DeepInfra API — étape 2 (#T231)."""

    _DESCRIPTEUR = ProviderDescriptor(
        id='deepinfra',
        name='DeepInfra API',
        type='pay_as_you_go',
        transport='openai_compat',
        api_endpoint='https://api.deepinfra.com/v1/openai/chat/completions',
        auth=AuthSpec(
            kind='api_key',
            env_var='DEEPINFRA_API_KEY',
            instructions='Clé DeepInfra (prépayé).',
        ),
        confidentiality='none',
        cascade_priority=4.1,
        notes='Hébergeur de modèles open-weights à bas coût. Llama 3.3, Qwen 2.5 et DeepSeek R1. Curation étape 2 : Les 3 modèles du seed, tous câblés. Les ids du catalogue ne correspondaient à AUCUN nom réel chez DeepInfra (constat #T243) — api_model_id porte les vrais noms, recopiés du gateway.',
        models=(
        ModelSpec(
            id='deepinfra/deepseek-r1',
            api_model_id='deepseek-ai/DeepSeek-R1',
            display_name='DeepSeek R1 671B (DeepInfra)',
            tier='paid',
            context_input=163840,
            context_output=8192,
            cost_input_per_m=0.55,
            cost_output_per_m=2.19,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='raisonnement',
            recommended_use='Routage alternatif Tier Fort — raisonnement profond avec balises thinking',
        ),
        ModelSpec(
            id='deepinfra/llama-3.3-70b-instruct',
            api_model_id='meta-llama/Llama-3.3-70B-Instruct',
            display_name='Llama 3.3 70B Instruct (DeepInfra)',
            tier='paid',
            context_input=131072,
            context_output=4096,
            cost_input_per_m=0.23,
            cost_output_per_m=0.23,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='raisonnement',
            recommended_use='Routage alternatif Tier Moyen/Fort — excellent rapport qualité/prix',
        ),
        ModelSpec(
            id='deepinfra/qwen-2.5-72b-instruct',
            api_model_id='Qwen/Qwen2.5-72B-Instruct',
            display_name='Qwen 2.5 72B Instruct (DeepInfra)',
            tier='paid',
            context_input=131072,
            context_output=4096,
            cost_input_per_m=0.35,
            cost_output_per_m=0.35,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='code',
            recommended_use='Routage alternatif Tier Moyen/Fort — excellent pour le code et le français',
        ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    def build(self, credentials, model=None):
        cle = (credentials.get('DEEPINFRA_API_KEY') or '').strip()
        if not cle:
            raise CredentialsManquantes(
                "DEEPINFRA_API_KEY absente — provider 'deepinfra' non construit.")
        par_id = {m.id: m for m in self.descriptor.models}
        modele = model or self.descriptor.models[0].id
        if modele not in par_id:
            raise CredentialsManquantes(f"Modèle '{modele}' non déclaré par 'deepinfra'.")
        return self._build_compat('deepinfra', par_id[modele], cle)



class MiniMaxPlugin(_PluginOpenAICompat):
    """Provider MiniMax API — étape 2 (#T231)."""

    _DESCRIPTEUR = ProviderDescriptor(
        id='minimax',
        name='MiniMax API',
        type='pay_as_you_go',
        transport='openai_compat',
        api_endpoint='https://api.minimax.chat/v1/text/chatcompletions',
        auth=AuthSpec(
            kind='api_key',
            env_var='MINIMAX_API_KEY',
            instructions='Clé MiniMax (endpoint international api.minimax.io).',
        ),
        confidentiality='none',
        cascade_priority=4.5,
        notes="Modèle d'écriture et de dialogue, performant en multilingue. Curation étape 2 : Les 4 modèles du seed (le M3 y est en minuscule — le vrai nom API est MiniMax-M3, api_model_id le porte) + 4 modèles câblés dans le gateway et présents au catalogue local mais absents du seed : M2.7 (20 appels, le plus consommé de la famille), M2.7-highspeed, M2.5, M2.1. Le doublon catalogue local MiniMax-M3 (majuscule, 0 appel) n'est pas reconduit.",
        models=(
        ModelSpec(
            id='minimax-m3',
            api_model_id='MiniMax-M3',
            display_name='MiniMax M3',
            tier='paid',
            routing_tier='fort',
            context_input=64000,
            context_output=4096,
            cost_input_per_m=1.2,
            cost_output_per_m=1.2,
            supports_tools=True,
            speciality='polyvalent',
            recommended_use="Modèle d'écriture créative et multilingue",
        ),
        ModelSpec(
            id='MiniMax-M2',
            display_name='MiniMax M2',
            tier='paid',
            routing_tier='leger',
            context_input=64000,
            context_output=4096,
            cost_input_per_m=0.15,
            cost_output_per_m=0.6,
            supports_tools=True,
            speciality='polyvalent',
            recommended_use='Tier léger, câblé dans core/llm_gateway.py mais absent du catalogue avant migration routing_tier (07/07/2026)',
        ),
        ModelSpec(
            id='MiniMax-M2.1-highspeed',
            display_name='MiniMax M2.1 (Highspeed)',
            tier='paid',
            routing_tier='leger',
            context_input=64000,
            context_output=4096,
            cost_input_per_m=0.3,
            cost_output_per_m=1.2,
            supports_tools=True,
            speciality='polyvalent_rapide',
            recommended_use='Tier léger rapide, câblé dans core/llm_gateway.py mais absent du catalogue avant migration routing_tier (07/07/2026)',
        ),
        ModelSpec(
            id='MiniMax-M2.5-highspeed',
            display_name='MiniMax M2.5 (Highspeed)',
            tier='paid',
            routing_tier='moyen',
            context_input=64000,
            context_output=4096,
            cost_input_per_m=0.3,
            cost_output_per_m=1.2,
            supports_tools=True,
            speciality='polyvalent_rapide',
            recommended_use='Tier moyen rapide, câblé dans core/llm_gateway.py mais absent du catalogue avant migration routing_tier (07/07/2026)',
        ),
        ModelSpec(
            id='MiniMax-M2.7',
            display_name='MiniMax M2.7',
            tier='fort',
            routing_tier='fort',
            context_input=204800,
            context_output=16384,
            cost_input_per_m=0.3,
            cost_output_per_m=1.2,
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='polyvalent',
            recommended_use='Nouveau flagship MiniMax avec contexte 1M tokens',
            notes='MiniMax — 204K contexte, agentic+coding. $0.30/$1.20/M. Successeur M2.5',
        ),
        ModelSpec(
            id='MiniMax-M2.7-highspeed',
            display_name='MiniMax M2.7 Highspeed',
            tier='moyen',
            context_input=204800,
            context_output=16384,
            cost_input_per_m=0.6,
            cost_output_per_m=2.4,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='agents_rapides',
            recommended_use='Version rapide de M2.7 pour agents temps réel',
            notes='MiniMax Highspeed — 2x plus rapide, 2x plus cher. $0.60/$2.40/M. Agents temps réel',
        ),
        ModelSpec(
            id='MiniMax-M2.5',
            display_name='MiniMax M2.5',
            tier='moyen',
            context_input=204800,
            context_output=16384,
            cost_input_per_m=0.15,
            cost_output_per_m=1.2,
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='polyvalent',
            recommended_use='Version stable précédente MiniMax',
            notes='MiniMax — Modèle stable précédent, $0.15/$1.20/M. Bon rapport qualité/coût',
        ),
        ModelSpec(
            id='MiniMax-M2.1',
            display_name='MiniMax M2.1',
            tier='paid',
            routing_tier='leger',
            context_input=204800,
            context_output=32768,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='agents_rapides',
            recommended_use='Modèle léger applicatif (déc 2025)',
            notes='Câblé dans le gateway (llm_gateway.py, gamme validée en direct 2026-06-16) mais absent du catalogue — déclaré pour alignement.',
        ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    def build(self, credentials, model=None):
        cle = (credentials.get('MINIMAX_API_KEY') or '').strip()
        if not cle:
            raise CredentialsManquantes(
                "MINIMAX_API_KEY absente — provider 'minimax' non construit.")
        par_id = {m.id: m for m in self.descriptor.models}
        modele = model or self.descriptor.models[0].id
        if modele not in par_id:
            raise CredentialsManquantes(f"Modèle '{modele}' non déclaré par 'minimax'.")
        return self._build_compat('minimax', par_id[modele], cle)



class ZhipuPlugin(_PluginOpenAICompat):
    """Provider Zhipu AI (Z.ai) — étape 2 (#T231)."""

    _DESCRIPTEUR = ProviderDescriptor(
        id='zhipu',
        name='Zhipu AI (Z.ai)',
        type='pay_as_you_go',
        transport='openai_compat',
        api_endpoint='https://open.bigmodel.cn/api/paas/v4/chat/completions',
        auth=AuthSpec(
            kind='api_key',
            env_var='ZHIPU_API_KEY',
            instructions='Clé Zhipu AI (Z.ai).',
        ),
        confidentiality='training',
        cascade_priority=3.7,
        notes='Famille GLM — excellent en code/agentique et en chinois. ZHIPU_API_KEY active. Curation étape 2 : Les 8 modèles du seed, tous câblés. glm-5.2 = 20 appels, glm-5-turbo = 15 — les seuls réellement consommés ; le reste est conservé car câblé (replis de cascade).',
        models=(
        ModelSpec(
            id='glm-5.2',
            display_name='GLM-5.2 (Flagship)',
            tier='fort',
            context_input=1000000,
            context_output=8192,
            cost_input_per_m=1.1,
            cost_output_per_m=3.86,
            supports_tools=True,
            speciality='code',
            recommended_use='Modèle flagship MoE de 744B paramètres, optimisé pour les longs contextes et le codage.',
            notes='Contexte de 1M tokens, licence MIT open-source, excellentes performances en agentique complexe.',
        ),
        ModelSpec(
            id='glm-5.1',
            display_name='GLM-5.1',
            tier='fort',
            context_input=200000,
            context_output=8192,
            cost_input_per_m=1.0,
            cost_output_per_m=3.2,
            supports_tools=True,
            speciality='code',
            recommended_use='Version intermédiaire entre GLM-5 et GLM-5.2 — alternative si budget serré vs 5.2',
            notes='⚠️ Prix à vérifier — sources divergentes au 03/07/2026 ($0.95-1.40 input / $3.15-4.40 output).',
        ),
        ModelSpec(
            id='glm-5',
            display_name='GLM-5',
            tier='fort',
            context_input=200000,
            context_output=8192,
            cost_input_per_m=1.0,
            cost_output_per_m=3.2,
            supports_tools=True,
            speciality='code',
            recommended_use='Première génération de la série 5 — base agentique avancée',
            notes='Dépassé par glm-5.1/glm-5.2. Prix à confirmer (sources divergentes au 03/07/2026: $1.00-1.40 input).',
        ),
        ModelSpec(
            id='glm-5-turbo',
            display_name='GLM-5 Turbo',
            tier='moyen',
            context_input=1000000,
            context_output=8192,
            cost_input_per_m=0.96,
            cost_output_per_m=3.58,
            supports_tools=True,
            speciality='code',
            recommended_use='Modèle optimisé en vitesse et fluidité pour les agents autonomes multi-étapes.',
            notes='Rapport coût/performance imbattable, latence minimale, haute cadence.',
        ),
        ModelSpec(
            id='glm-4.7',
            display_name='GLM-4.7',
            tier='moyen',
            context_input=200000,
            context_output=4096,
            cost_input_per_m=0.55,
            cost_output_per_m=2.2,
            supports_tools=True,
            speciality='code',
            recommended_use='Modèle équilibré et économique, robuste pour le code et les résumés longs.',
            notes='Très abordable, grand historique de stabilité.',
        ),
        ModelSpec(
            id='glm-4.6',
            display_name='GLM-4.6',
            tier='moyen',
            context_input=200000,
            context_output=4096,
            cost_input_per_m=0.55,
            cost_output_per_m=2.2,
            supports_tools=True,
            speciality='code',
            recommended_use='Alternative mature à GLM-4.7 — contexte étendu vs 4.5',
        ),
        ModelSpec(
            id='glm-4.5',
            display_name='GLM-4.5',
            tier='moyen',
            context_input=128000,
            context_output=4096,
            cost_input_per_m=0.28,
            cost_output_per_m=1.1,
            supports_tools=True,
            speciality='code',
            recommended_use='Ancienne version stable des modèles GLM 4.5.',
            notes='Idéal comme base économique pour des tâches simples.',
        ),
        ModelSpec(
            id='glm-4.5-air',
            display_name='GLM-4.5 Air',
            tier='leger',
            context_input=128000,
            context_output=4096,
            cost_input_per_m=0.2,
            cost_output_per_m=1.1,
            supports_tools=True,
            speciality='agents_lowcost',
            recommended_use='Le moins cher de la gamme GLM — volumes élevés, tâches simples',
        ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    def build(self, credentials, model=None):
        cle = (credentials.get('ZHIPU_API_KEY') or '').strip()
        if not cle:
            raise CredentialsManquantes(
                "ZHIPU_API_KEY absente — provider 'zhipu' non construit.")
        par_id = {m.id: m for m in self.descriptor.models}
        modele = model or self.descriptor.models[0].id
        if modele not in par_id:
            raise CredentialsManquantes(f"Modèle '{modele}' non déclaré par 'zhipu'.")
        return self._build_compat('zhipu', par_id[modele], cle)



class DashScopePlugin(_PluginOpenAICompat):
    """Provider Alibaba DashScope Coding Plan — étape 2 (#T231)."""

    _DESCRIPTEUR = ProviderDescriptor(
        id='dashscope',
        name='Alibaba DashScope Coding Plan',
        type='subscription',
        transport='openai_compat',
        api_endpoint='https://coding-intl.dashscope.aliyuncs.com/v1/chat/completions',
        auth=AuthSpec(
            kind='api_key',
            env_var='DASHSCOPE_API_KEY',
            instructions='Clé Coding Plan sk-sp-* ; alias BAILIAN_CODING_PLAN_API_KEY accepté, endpoint surchargable via DASHSCOPE_BASE_URL.',
        ),
        confidentiality='training',
        cascade_priority=2.5,
        notes="D-8 Axel 13/08 : désactivé (401 + CGU backend). Catalogue conservé, routing_tier retiré, gateway n'instancie plus. Clé DASHSCOPE_API_KEY gardée. Réactiver = DASHSCOPE_ACTIF=True dans llm_gateway.py.",
        models=(
        ModelSpec(
            id='dashscope/qwen3.7-plus',
            api_model_id='qwen3.7-plus',
            display_name='Qwen3.7 Plus (Coding Plan)',
            tier='subscription',
            context_input=1000000,
            context_output=65536,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='code',
            recommended_use='Flagship Qwen Coding Plan — vision + thinking, contexte 1M',
            notes='Lite amorti (~¥40/mois). ID API exact: qwen3.7-plus',
        ),
        ModelSpec(
            id='dashscope/qwen3.6-plus',
            api_model_id='qwen3.6-plus',
            display_name='Qwen3.6 Plus (Coding Plan)',
            tier='subscription',
            context_input=1000000,
            context_output=65536,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='code',
            recommended_use='Qwen Coding Plan — vision + thinking',
            notes='ID API exact: qwen3.6-plus',
        ),
        ModelSpec(
            id='dashscope/qwen3.5-plus',
            api_model_id='qwen3.5-plus',
            display_name='Qwen3.5 Plus (Coding Plan)',
            tier='subscription',
            context_input=1000000,
            context_output=65536,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='code',
            recommended_use='Qwen Coding Plan polyvalent — vision',
            notes='ID API exact: qwen3.5-plus',
        ),
        ModelSpec(
            id='dashscope/qwen3-max-2026-01-23',
            api_model_id='qwen3-max-2026-01-23',
            display_name='Qwen3 Max 2026-01-23 (Coding Plan)',
            tier='subscription',
            context_input=262144,
            context_output=65536,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='raisonnement',
            recommended_use='Qwen Max snapshot Coding Plan',
            notes='ID API exact: qwen3-max-2026-01-23',
        ),
        ModelSpec(
            id='dashscope/qwen3-coder-next',
            api_model_id='qwen3-coder-next',
            display_name='Qwen3 Coder Next (Coding Plan)',
            tier='subscription',
            context_input=262144,
            context_output=65536,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='code',
            recommended_use='Défaut moteur Coding Plan — génération de code (pas de thinking)',
            notes='Thinking non supporté. ID API exact: qwen3-coder-next',
        ),
        ModelSpec(
            id='dashscope/qwen3-coder-plus',
            api_model_id='qwen3-coder-plus',
            display_name='Qwen3 Coder Plus (Coding Plan)',
            tier='subscription',
            context_input=1000000,
            context_output=65536,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='code',
            recommended_use='Code + contexte 1M (pas de thinking)',
            notes='Thinking non supporté. ID API exact: qwen3-coder-plus',
        ),
        ModelSpec(
            id='dashscope/kimi-k2.5',
            api_model_id='kimi-k2.5',
            display_name='Kimi K2.5 (Coding Plan)',
            tier='subscription',
            context_input=262144,
            context_output=65536,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='code',
            recommended_use='Moonshot Kimi via Coding Plan — vision + agentique',
            notes='ID API exact: kimi-k2.5',
        ),
        ModelSpec(
            id='dashscope/glm-5',
            api_model_id='glm-5',
            display_name='GLM-5 (Coding Plan)',
            tier='subscription',
            context_input=202752,
            context_output=32768,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='code',
            recommended_use='GLM-5 via forfait Alibaba (distinct de Zhipu payant)',
            notes='ID API exact: glm-5 — ne pas confondre avec provider zhipu',
        ),
        ModelSpec(
            id='dashscope/glm-4.7',
            api_model_id='glm-4.7',
            display_name='GLM-4.7 (Coding Plan)',
            tier='subscription',
            context_input=202752,
            context_output=32768,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='code',
            recommended_use='GLM-4.7 via forfait Alibaba',
            notes='ID API exact: glm-4.7',
        ),
        ModelSpec(
            id='dashscope/MiniMax-M2.5',
            api_model_id='MiniMax-M2.5',
            display_name='MiniMax M2.5 (Coding Plan)',
            tier='subscription',
            context_input=196608,
            context_output=32768,
            cost_input_per_m=0.0,
            cost_output_per_m=0.0,
            supports_tools=True,
            supports_json_mode=True,
            supports_streaming=True,
            speciality='polyvalent',
            recommended_use='MiniMax via Coding Plan (distinct de MINIMAX_API_KEY)',
            notes='ID API exact: MiniMax-M2.5 (casse significative)',
        ),
        ),
    )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._DESCRIPTEUR

    def build(self, credentials, model=None):
        cle = ((credentials.get('DASHSCOPE_API_KEY')
                or credentials.get('BAILIAN_CODING_PLAN_API_KEY')) or '').strip()
        if not cle:
            raise CredentialsManquantes(
                "DASHSCOPE_API_KEY absente — provider 'dashscope' non construit.")
        par_id = {m.id: m for m in self.descriptor.models}
        modele = model or self.descriptor.models[0].id
        if modele not in par_id:
            raise CredentialsManquantes(f"Modèle '{modele}' non déclaré par 'dashscope'.")
        return self._build_compat('dashscope', par_id[modele], cle)



# Plugins internes convertis au contrat. `cloud_apis` reste volontairement hors
# plugins (APIs spécialisées sans transport chat) ; `anthropic_gcp`/`flux`
# ne sont pas dans le seed (voir docstring), `github` a été retiré (#T280).
# L'enregistrement de cette liste au démarrage est upsert seul : rien n'est
# retiré du catalogue existant.
BUILTIN_PROVIDER_PLUGINS: tuple[ProviderPlugin, ...] = (
    MistralPlugin(),
    LMStudioLocalPlugin(),
    OllamaLocalPlugin(),
    AjeanPlugin(),
    GeminiFreePlugin(),
    GeminiPaidPlugin(),
    GeminiCLIPluginInterne(),
    ClaudeCLIPluginInterne(),
    AnthropicNativePlugin(),
    DeepSeekPlugin(),
    CoherePlugin(),
    CerebrasPlugin(),
    OpenRouterPlugin(),
    XAIPlugin(),
    DeepInfraPlugin(),
    MiniMaxPlugin(),
    ZhipuPlugin(),
    DashScopePlugin(),
)
