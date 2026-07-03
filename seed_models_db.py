"""
seed_models_db.py — Peuplement initial de models_registry.db.

Script idempotent (INSERT OR REPLACE) qui peuple la BDD depuis :
  - Les données de pricing_strategy.json
  - Les informations hardcodées du LLMGateway (providers, modèles)
  - Les benchmarks de la documentation technique
  - Les clés API du .env

Usage :
    python seed_models_db.py
    # → Crée/peuple models_registry.db dans le dossier moteur_agents/
"""

import os
import sys

# Ajouter le dossier racine du moteur au PATH
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from core.models_db import (
    upsert_provider, upsert_model, upsert_api_key,
    upsert_benchmark, upsert_routing_rule, upsert_subscription,
    upsert_access_channel, update_quota_realtime,
    get_db_stats,
)


def seed_providers():
    """Insère les 7 providers d'accès LLM."""
    print("── Providers ──")

    providers = [
        {
            "id": "local",
            "name": "LM Studio (Local)",
            "type": "local",
            "api_endpoint": "http://${LMSTUDIO_HOST:-localhost}:1234/v1/chat/completions",
            "auth_method": "local",
            "confidentiality": "total",
            "cascade_priority": 1.0,
            "notes": "RTX 5070 Ti (16GB VRAM), 9 modèles chargés. Coût zéro, confidentialité totale.",
        },
        {
            "id": "gemini_free",
            "name": "Gemini Free Tier (AI Studio)",
            "type": "free",
            "api_endpoint": "https://generativelanguage.googleapis.com/v1beta",
            "auth_method": "api_key",
            "confidentiality": "training",
            "cascade_priority": 2.0,
            "notes": "5 clés Free Tier rotatives via KeyPool. Cache implicite gratuit.",
        },
        {
            "id": "gemini_cli",
            "name": "Gemini Advanced (CLI Antigravity)",
            "type": "subscription",
            "api_endpoint": "antigravity-ide.cmd",
            "auth_method": "cli",
            "confidentiality": "none",
            "cascade_priority": 3.0,
            "notes": "Inclus dans Google One AI Pro (19.99$/mois). Fenêtre 1M-2M tokens.",
        },
        {
            "id": "claude_cli",
            "name": "Anthropic (CLI Claude Pro)",
            "type": "subscription",
            "api_endpoint": "claude.cmd",
            "auth_method": "cli",
            "confidentiality": "none",
            "cascade_priority": 3.5,
            "notes": "Inclus dans Claude Pro (20$/mois). SWE-bench 87.6%.",
        },
        {
            "id": "deepseek",
            "name": "DeepSeek API",
            "type": "pay_as_you_go",
            "api_endpoint": "https://api.deepseek.com/chat/completions",
            "auth_method": "api_key",
            "confidentiality": "training",
            "cascade_priority": 4.0,
            "notes": "Ratio coût/intelligence imbattable. V4-Flash à $0.14/$0.28/M.",
        },
        {
            "id": "gemini_paid",
            "name": "Gemini API (GCP Payant)",
            "type": "pay_as_you_go",
            "api_endpoint": "https://generativelanguage.googleapis.com/v1beta",
            "auth_method": "api_key",
            "confidentiality": "none",
            "cascade_priority": 5.0,
            "notes": "Tarifs GCP en EUR. Search Grounding débloqué. Context Caching -90%.",
        },
        {
            "id": "mistral",
            "name": "Mistral AI API",
            "type": "pay_as_you_go",
            "api_endpoint": "https://api.mistral.ai/v1/chat/completions",
            "auth_method": "api_key",
            "confidentiality": "training",
            "cascade_priority": 3.8,
            "notes": "Plan Experiment (Gratuit). Excellent en français. Tokenizer optimisé.",
        },
        {
            "id": "cohere",
            "name": "Cohere API",
            "type": "pay_as_you_go",
            "api_endpoint": "https://api.cohere.com/compatibility/v1/chat/completions",
            "auth_method": "api_key",
            "confidentiality": "training",
            "cascade_priority": 3.9,
            "notes": "Trial Key (Gratuit, 10 RPM). Champion du RAG et du Tool Calling.",
        },

        {
            "id": "cerebras",
            "name": "Cerebras API",
            "type": "pay_as_you_go",
            "api_endpoint": "https://api.cerebras.ai/v1/chat/completions",
            "auth_method": "api_key",
            "confidentiality": "none",
            "cascade_priority": 2.3,
            "notes": "Plan Gratuit (1M tokens/jour). Vitesse exceptionnelle.",
        },
        {
            "id": "openrouter",
            "name": "OpenRouter API",
            "type": "pay_as_you_go",
            "api_endpoint": "https://openrouter.ai/api/v1/chat/completions",
            "auth_method": "api_key",
            "confidentiality": "none",
            "cascade_priority": 2.4,
            "notes": "Modèles gratuits et payants. Alternatives en cas de pannes de tiers.",
        },
        {
            "id": "xai",
            "name": "xAI API (Grok)",
            "type": "pay_as_you_go",
            "api_endpoint": "https://api.x.ai/v1/chat/completions",
            "auth_method": "api_key",
            "confidentiality": "none",
            "cascade_priority": 4.2,
            "notes": "Modèles Grok-2 et Grok-Beta. Excellentes capacités de raisonnement.",
        },
        {
            "id": "deepinfra",
            "name": "DeepInfra API",
            "type": "pay_as_you_go",
            "api_endpoint": "https://api.deepinfra.com/v1/openai/chat/completions",
            "auth_method": "api_key",
            "confidentiality": "none",
            "cascade_priority": 4.1,
            "notes": "Hébergeur de modèles open-weights à bas coût. Llama 3.3, Qwen 2.5 et DeepSeek R1.",
        },
        {
            "id": "cloud_apis",
            "name": "Google Cloud APIs (TTS, STT, Vision, Translation)",
            "type": "pay_as_you_go",
            "api_endpoint": "https://texttospeech.googleapis.com",
            "auth_method": "api_key",
            "confidentiality": "none",
            "cascade_priority": 6.0,
            "notes": "APIs spécialisées hors LLM. Utilisées par les outils du moteur.",
        },
        {
            "id": "minimax",
            "name": "MiniMax API",
            "type": "pay_as_you_go",
            "api_endpoint": "https://api.minimax.chat/v1/text/chatcompletions",
            "auth_method": "api_key",
            "confidentiality": "none",
            "cascade_priority": 4.5,
            "notes": "Modèle d'écriture et de dialogue, performant en multilingue.",
        },
        {
            "id": "flux",
            "name": "Black Forest Labs (Flux)",
            "type": "pay_as_you_go",
            "api_endpoint": "https://api.replicate.com/v1",
            "auth_method": "api_key",
            "confidentiality": "none",
            "cascade_priority": 5.5,
            "notes": "Génération d'images SOTA 2026 via Replicate API.",
        },
        {
            "id": "ollama_local",
            "name": "Ollama (Local PC)",
            "type": "local",
            "api_endpoint": "http://127.0.0.1:11434/v1/chat/completions",
            "auth_method": "local",
            "confidentiality": "total",
            "cascade_priority": 1.1,
            "notes": "Ollama local sur RTX 5070 Ti. Inférence ultra-rapide.",
        },
        {
            "id": "anthropic_native",
            "name": "Anthropic API",
            "type": "pay_as_you_go",
            "api_endpoint": "https://api.anthropic.com/v1/messages",
            "auth_method": "api_key",
            "confidentiality": "none",
            "cascade_priority": 3.4,
            "notes": "API native avec accès aux Batchs. ANTHROPIC_API_KEY active — indépendante des quotas Claude Pro CLI.",
        },
        {
            "id": "zhipu",
            "name": "Zhipu AI (Z.ai)",
            "type": "pay_as_you_go",
            "api_endpoint": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            "auth_method": "api_key",
            "confidentiality": "training",
            "cascade_priority": 3.7,
            "notes": "Famille GLM — excellent en code/agentique et en chinois. ZHIPU_API_KEY active.",
        },
    ]


    for p in providers:
        ok = upsert_provider(p["id"], **p)
        print(f"  {'✅' if ok else '❌'} {p['id']} — {p['name']}")

    return len(providers)


def seed_models():
    """Insère les modèles LLM depuis les données connues."""
    print("\n── Modèles ──")

    models = [
        # ═══ LOCAL ═══
        {"id": "qwen2.5-coder:7b", "provider_id": "ollama_local", "display_name": "Qwen 2.5 Coder 7B (Ollama)", "tier": "local", "context_input": 131072, "context_output": 8192, "throughput_tps": 75.0, "supports_tools": 1, "supports_json_mode": 1, "speciality": "code", "recommended_use": "Génération de code rapide sur GPU RTX"},
        {"id": "deepseek-r1:8b", "provider_id": "ollama_local", "display_name": "DeepSeek R1 8B (Ollama)", "tier": "local", "context_input": 131072, "context_output": 8192, "throughput_tps": 50.0, "supports_thinking": 1, "speciality": "raisonnement", "recommended_use": "Raisonnement logique avec thinking local"},
        {"id": "qwen2.5-14b-instruct-1m", "provider_id": "local", "display_name": "Qwen 2.5 14B Instruct 1M", "tier": "local", "context_input": 1000000, "context_output": 32768, "throughput_tps": 35.0, "supports_tools": 1, "supports_json_mode": 1, "speciality": "agents_locaux", "recommended_use": "Tier Léger — agents locaux, RAG confidentiel"},
        {"id": "qwen3-coder-next", "provider_id": "local", "display_name": "Qwen 3 Coder Next", "tier": "local", "context_input": 131072, "context_output": 32768, "throughput_tps": 28.0, "supports_tools": 1, "speciality": "code", "recommended_use": "Génération de code local"},
        {"id": "qwen2.5-coder-32b", "provider_id": "local", "display_name": "Qwen 2.5 Coder 32B", "tier": "local", "context_input": 131072, "context_output": 32768, "throughput_tps": 18.0, "supports_tools": 1, "speciality": "code", "recommended_use": "Refactoring lourd confidentiel"},
        {"id": "gemma-4-31b", "provider_id": "local", "display_name": "Google Gemma 4 31B", "tier": "local", "context_input": 131072, "context_output": 32768, "throughput_tps": 15.0, "speciality": "polyvalent", "recommended_use": "Modèle Google local dense"},
        {"id": "gemma-4-26b-a4b", "provider_id": "local", "display_name": "Google Gemma 4 26B MoE (A4B)", "tier": "local", "context_input": 131072, "context_output": 32768, "throughput_tps": 40.0, "speciality": "polyvalent_rapide", "recommended_use": "MoE rapide local — classification"},
        {"id": "gemma-4-e4b", "provider_id": "local", "display_name": "Google Gemma 4 E4B", "tier": "local", "context_input": 131072, "context_output": 32768, "throughput_tps": 65.0, "speciality": "ultra_rapide", "recommended_use": "Le plus rapide localement (65 tok/s)"},
        {"id": "qwen3.6-35b-a3b", "provider_id": "local", "display_name": "Qwen 3.6 35B MoE (A3B)", "tier": "local", "context_input": 131072, "context_output": 32768, "throughput_tps": 24.0, "speciality": "polyvalent", "recommended_use": "MoE local Qwen (thinking mode)"},
        {"id": "deepseek-coder-v2-lite", "provider_id": "local", "display_name": "DeepSeek Coder V2 Lite", "tier": "local", "context_input": 131072, "context_output": 16384, "throughput_tps": 50.0, "speciality": "code_rapide", "recommended_use": "Code léger rapide"},
        {"id": "nomic-embed-text-v1.5", "provider_id": "local", "display_name": "Nomic Embed Text V1.5", "tier": "local", "context_input": 8192, "speciality": "embeddings", "recommended_use": "Embeddings airgapped pour RAG"},

        # ═══ GEMINI FREE ═══
        {"id": "gemini-3.5-flash", "provider_id": "gemini_free", "display_name": "Gemini 3.5 Flash", "tier": "free", "context_input": 1048576, "context_output": 65536, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "ttft_ms": 622, "throughput_tps": 100.0, "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "agents_rapides", "recommended_use": "Tier Léger/Moyen — le plus rapide, gratuit, cache implicite", "last_tested": "2026-06-15"},
        {"id": "gemini-3.1-pro-preview", "provider_id": "gemini_free", "display_name": "Gemini 3.1 Pro Preview", "tier": "free", "context_input": 2097152, "context_output": 65536, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "ttft_ms": 2000, "throughput_tps": 40.0, "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "raisonnement_pro", "recommended_use": "Raisonnement avancé (gratuit si quota disponible)", "last_tested": "2026-06-15"},
        {"id": "gemini-3.1-flash-lite", "provider_id": "gemini_free", "display_name": "Gemini 3.1 Flash Lite", "tier": "free", "context_input": 1048576, "context_output": 65536, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "ttft_ms": 400, "throughput_tps": 150.0, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "ultra_rapide", "recommended_use": "Tâches simples ultra-rapides", "last_tested": "2026-06-15"},
        {"id": "gemini-2.5-flash", "provider_id": "gemini_free", "display_name": "Gemini 2.5 Flash", "tier": "free", "context_input": 1048576, "context_output": 65536, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "ttft_ms": 550, "throughput_tps": 80.0, "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "raisonnement", "recommended_use": "Raisonnement Tier Léger — backup de 3.5 Flash", "last_tested": "2026-06-15"},

        # ═══ GEMINI CLI (Abonnement) ═══
        {"id": "gemini-3.5-flash-high-cli", "provider_id": "gemini_cli", "display_name": "Gemini 3.5 Flash (CLI High)", "tier": "subscription", "context_input": 1048576, "context_output": 65536, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "supports_streaming": 1, "speciality": "agents_cli", "recommended_use": "CLI Antigravity IDE — mode high compute", "notes": "Amorti ~0.20$/M tokens"},
        {"id": "gemini-3.5-flash-medium-cli", "provider_id": "gemini_cli", "display_name": "Gemini 3.5 Flash (CLI Medium)", "tier": "subscription", "context_input": 1048576, "context_output": 65536, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_tools": 1, "supports_streaming": 1, "speciality": "agents_cli", "recommended_use": "CLI Antigravity IDE — mode medium compute"},
        {"id": "gemini-cli", "provider_id": "gemini_cli", "display_name": "Gemini CLI (défaut)", "tier": "subscription", "context_input": 1048576, "context_output": 65536, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_tools": 1, "speciality": "agents_cli", "recommended_use": "CLI Antigravity IDE — mode agent par défaut"},

        # ═══ CLAUDE CLI (Abonnement) ═══
        {"id": "claude-opus-4-8", "provider_id": "claude_cli", "display_name": "Claude Opus 4.8", "tier": "subscription", "context_input": 200000, "context_output": 32768, "cost_input_per_m": 5.0, "cost_output_per_m": 25.0, "ttft_ms": 6000, "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "architecture", "recommended_use": "Tier Fort — le plus puissant en agentic coding (depuis 2026-05-28)", "last_tested": "2026-05-29", "notes": "Tarifs référence API, inclus dans le forfait Pro"},
        {"id": "claude-opus-4-7", "provider_id": "claude_cli", "display_name": "Claude Opus 4.7", "tier": "subscription", "context_input": 200000, "context_output": 32768, "cost_input_per_m": 5.0, "cost_output_per_m": 25.0, "ttft_ms": 6000, "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "architecture", "recommended_use": "Génération stable précédente", "last_tested": "2026-05-26", "notes": "Tarifs référence API, inclus dans le forfait Pro"},
        {"id": "claude-sonnet-4-6", "provider_id": "claude_cli", "display_name": "Claude Sonnet 4.6", "tier": "subscription", "context_input": 200000, "context_output": 32768, "cost_input_per_m": 3.0, "cost_output_per_m": 15.0, "ttft_ms": 3000, "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "pair_programming", "recommended_use": "Défaut CLI — bon équilibre rapidité/qualité", "last_tested": "2026-05-26"},
        {"id": "claude-haiku-4-5", "provider_id": "claude_cli", "display_name": "Claude Haiku 4.5", "tier": "subscription", "context_input": 200000, "context_output": 32768, "cost_input_per_m": 1.0, "cost_output_per_m": 5.0, "ttft_ms": 1500, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "rapide", "recommended_use": "Tâches légères rapides via CLI", "last_tested": "2026-05-26"},
        {"id": "claude-opus-4-5", "provider_id": "claude_cli", "display_name": "Claude Opus 4.5", "tier": "subscription", "context_input": 200000, "context_output": 32768, "cost_input_per_m": 5.0, "cost_output_per_m": 25.0, "ttft_ms": 6000, "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "speciality": "architecture", "recommended_use": "Génération stable précédente"},
        # Alias Claude CLI legacy
        {"id": "claude-opus-4-0", "provider_id": "claude_cli", "display_name": "Claude Opus 4.0 (alias → 4.8)", "tier": "subscription", "context_input": 200000, "context_output": 32768, "speciality": "architecture", "recommended_use": "Alias legacy → redirige vers opus-4-8", "notes": "Alias legacy"},
        {"id": "claude-sonnet-4-5", "provider_id": "claude_cli", "display_name": "Claude Sonnet 4.5", "tier": "subscription", "context_input": 200000, "context_output": 32768, "cost_input_per_m": 3.0, "cost_output_per_m": 15.0, "ttft_ms": 3000, "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "pair_programming", "recommended_use": "Génération précédente stable", "last_tested": "2026-05-26"},
        {"id": "claude-sonnet-4.6-thinking-cli", "provider_id": "claude_cli", "display_name": "Claude Sonnet 4.6 Thinking CLI (legacy)", "tier": "subscription", "context_input": 200000, "context_output": 32768, "speciality": "pair_programming", "recommended_use": "Alias legacy → sonnet-4-6", "notes": "Alias legacy"},
        {"id": "claude-opus-4.6-thinking-cli", "provider_id": "claude_cli", "display_name": "Claude Opus 4.6 Thinking CLI (legacy)", "tier": "subscription", "context_input": 200000, "context_output": 32768, "speciality": "architecture", "recommended_use": "Alias legacy → opus-4-8", "notes": "Alias legacy"},

        # ═══ MISTRAL (Free Experiment) ═══
        {"id": "mistral-large-latest", "provider_id": "mistral", "display_name": "Mistral Large (latest)", "tier": "free", "context_input": 128000, "context_output": 8192, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "raisonnement", "recommended_use": "Raisonnement fort, RAG en français, Tool Calling", "notes": "Plan Experiment (Gratuit)"},
        {"id": "codestral-latest", "provider_id": "mistral", "display_name": "Codestral (latest)", "tier": "free", "context_input": 32000, "context_output": 8192, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "code", "recommended_use": "Génération de code et FIM", "notes": "Plan Experiment (Gratuit)"},
        {"id": "open-mistral-nemo", "provider_id": "mistral", "display_name": "Mistral Nemo (latest)", "tier": "free", "context_input": 128000, "context_output": 8192, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "agents_lowcost", "recommended_use": "Worker rapide en français", "notes": "Plan Experiment (Gratuit)"},

        # ═══ COHERE (Free Trial) ═══
        {"id": "command-r-plus-08-2024", "provider_id": "cohere", "display_name": "Cohere Command R+ (08-2024)", "tier": "free", "context_input": 128000, "context_output": 4096, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "raisonnement", "recommended_use": "RAG avancé, Tool Calling, planifications", "notes": "Trial Key (Gratuit)"},
        {"id": "command-r-08-2024", "provider_id": "cohere", "display_name": "Cohere Command R (08-2024)", "tier": "free", "context_input": 128000, "context_output": 4096, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "agents_rapides", "recommended_use": "RAG rapide, agents", "notes": "Trial Key (Gratuit)"},



        # ═══ CEREBRAS API ═══
        {"id": "gpt-oss-120b", "provider_id": "cerebras", "display_name": "GPT OSS 120B (Cerebras)", "tier": "free", "context_input": 8192, "context_output": 4096, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "raisonnement", "recommended_use": "Modèle géant 120B ultra-rapide (Cerebras)", "notes": "Plan Gratuit (Cerebras)"},
        {"id": "zai-glm-4.7", "provider_id": "cerebras", "display_name": "Zai GLM 4.7 (Cerebras)", "tier": "free", "context_input": 8192, "context_output": 4096, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "raisonnement", "recommended_use": "Raisonnement polyvalent ultra-rapide (Cerebras)", "notes": "Plan Gratuit (Cerebras)"},

        # ═══ OPENROUTER API ═══
        {"id": "meta-llama/llama-3.3-70b-instruct:free", "provider_id": "openrouter", "display_name": "Llama 3.3 70B Instruct (Free via OpenRouter)", "tier": "free", "context_input": 131072, "context_output": 4096, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "raisonnement", "recommended_use": "Raisonnement fort gratuit via OpenRouter", "notes": "Plan Gratuit (OpenRouter)"},
        {"id": "meta-llama/llama-3.2-3b-instruct:free", "provider_id": "openrouter", "display_name": "Llama 3.2 3B Instruct (Free via OpenRouter)", "tier": "free", "context_input": 131072, "context_output": 4096, "cost_input_per_m": 0.0, "cost_output_per_m": 0.0, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "ultra_rapide", "recommended_use": "Tâches rapides gratuites via OpenRouter", "notes": "Plan Gratuit (OpenRouter)"},
        {"id": "anthropic/claude-3.5-sonnet", "provider_id": "openrouter", "display_name": "Claude 3.5 Sonnet (OpenRouter)", "tier": "paid", "context_input": 200000, "context_output": 8192, "cost_input_per_m": 3.0, "cost_output_per_m": 15.0, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "code", "recommended_use": "Raisonnement, code, agentique via OpenRouter", "notes": "Payant"},
        {"id": "anthropic/claude-sonnet-5", "provider_id": "openrouter", "display_name": "Claude 5 Sonnet (OpenRouter)", "tier": "paid", "context_input": 200000, "context_output": 8192, "cost_input_per_m": 2.0, "cost_output_per_m": 10.0, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "code", "recommended_use": "Raisonnement, code, agentique via OpenRouter", "notes": "Payant (Nouveau Claude 5)"},
        {"id": "openai/gpt-5.2", "provider_id": "openrouter", "display_name": "GPT-5.2 (OpenRouter)", "tier": "fort", "context_input": 400000, "context_output": 128000, "cost_input_per_m": 1.75, "cost_output_per_m": 14.0, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "raisonnement", "recommended_use": "Seul accès à la famille OpenAI GPT-5.x du catalogue — agentique/contexte long", "notes": "GPT-5.5 existe aussi sur OpenRouter (plus récent) mais pricing non confirmé au 03/07/2026"},
        {"id": "moonshotai/kimi-k2.7-code", "provider_id": "openrouter", "display_name": "Kimi K2.7 Code (OpenRouter)", "tier": "fort", "context_input": 262144, "context_output": 262144, "cost_input_per_m": 0.74, "cost_output_per_m": 3.50, "supports_tools": 1, "supports_thinking": 1, "supports_vision": 1, "supports_streaming": 1, "speciality": "code", "recommended_use": "Meilleur Kimi (Moonshot AI) actuel — MoE 1T/32B actifs, orienté code multi-tours", "notes": "Sorti 12/06/2026. Alternative moins chère : moonshotai/kimi-k2-thinking (~$0.60/$2.50)"},
        {"id": "qwen/qwen3.7-max", "provider_id": "openrouter", "display_name": "Qwen 3.7 Max (OpenRouter)", "tier": "fort", "context_input": 1000000, "context_output": 32768, "cost_input_per_m": 1.25, "cost_output_per_m": 3.75, "supports_tools": 1, "supports_thinking": 1, "supports_streaming": 1, "speciality": "raisonnement", "recommended_use": "Dernier flagship Qwen (Alibaba) — contexte 1M, alternative chinoise à Kimi/GLM"},

        # ═══ ANTHROPIC API (accès direct, hors abonnement Claude Pro CLI) ═══
        {"id": "claude-sonnet-5", "provider_id": "anthropic_native", "display_name": "Claude Sonnet 5 (API directe)", "tier": "moyen", "context_input": 1000000, "context_output": 128000, "cost_input_per_m": 2.0, "cost_output_per_m": 10.0, "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "pair_programming", "recommended_use": "Meilleur rapport qualité/prix Anthropic — proche du niveau Opus en code/agentique", "notes": "Tarif d'intro 2.0$/10.0$ par M tokens jusqu'au 31/08/2026 (puis 3.0$/15.0$). Thinking adaptatif actif par défaut."},
        {"id": "claude-fable-5", "provider_id": "anthropic_native", "display_name": "Claude Fable 5 (API directe)", "tier": "fort", "context_input": 1000000, "context_output": 128000, "cost_input_per_m": 10.0, "cost_output_per_m": 50.0, "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "raisonnement_extreme", "recommended_use": "Modèle le plus capable d'Anthropic — raisonnement long-horizon, tâches agentiques les plus dures", "notes": "Thinking toujours actif. Nécessite rétention de données ≥30 jours (indisponible en ZDR). Tarif nettement supérieur à Opus."},
        # NB : claude-opus-4-8 et claude-haiku-4-5 restent provider_id="claude_cli" (modèle canonique
        # déjà défini plus haut) — l'accès direct via ANTHROPIC_API_KEY est ajouté comme access_channel
        # supplémentaire dans seed_access_channels(), PAS comme une nouvelle ligne "models" (id = PK global).

        # ═══ ZHIPU AI / Z.ai — famille GLM ═══
        {"id": "glm-5.2", "provider_id": "zhipu", "display_name": "GLM-5.2 (Flagship)", "tier": "fort", "context_input": 1000000, "context_output": 8192, "cost_input_per_m": 1.1, "cost_output_per_m": 3.86, "supports_tools": 1, "supports_thinking": 1, "speciality": "code", "recommended_use": "Modèle flagship MoE de 744B paramètres, optimisé pour les longs contextes et le codage.", "notes": "Contexte de 1M tokens, licence MIT open-source, excellentes performances en agentique complexe."},
        {"id": "glm-5-turbo", "provider_id": "zhipu", "display_name": "GLM-5 Turbo", "tier": "moyen", "context_input": 1000000, "context_output": 8192, "cost_input_per_m": 0.96, "cost_output_per_m": 3.58, "supports_tools": 1, "speciality": "code", "recommended_use": "Modèle optimisé en vitesse et fluidité pour les agents autonomes multi-étapes.", "notes": "Rapport coût/performance imbattable, latence minimale, haute cadence."},
        {"id": "glm-5", "provider_id": "zhipu", "display_name": "GLM-5", "tier": "fort", "context_input": 200000, "context_output": 8192, "cost_input_per_m": 1.0, "cost_output_per_m": 3.2, "supports_tools": 1, "supports_thinking": 1, "speciality": "code", "recommended_use": "Première génération de la série 5 — base agentique avancée", "notes": "Dépassé par glm-5.1/glm-5.2. Prix à confirmer (sources divergentes au 03/07/2026: $1.00-1.40 input)."},
        {"id": "glm-5.1", "provider_id": "zhipu", "display_name": "GLM-5.1", "tier": "fort", "context_input": 200000, "context_output": 8192, "cost_input_per_m": 1.0, "cost_output_per_m": 3.2, "supports_tools": 1, "supports_thinking": 1, "speciality": "code", "recommended_use": "Version intermédiaire entre GLM-5 et GLM-5.2 — alternative si budget serré vs 5.2", "notes": "⚠️ Prix à vérifier — sources divergentes au 03/07/2026 ($0.95-1.40 input / $3.15-4.40 output)."},
        {"id": "glm-4.7", "provider_id": "zhipu", "display_name": "GLM-4.7", "tier": "moyen", "context_input": 200000, "context_output": 4096, "cost_input_per_m": 0.55, "cost_output_per_m": 2.2, "supports_tools": 1, "speciality": "code", "recommended_use": "Modèle équilibré et économique, robuste pour le code et les résumés longs.", "notes": "Très abordable, grand historique de stabilité."},
        {"id": "glm-4.6", "provider_id": "zhipu", "display_name": "GLM-4.6", "tier": "moyen", "context_input": 200000, "context_output": 4096, "cost_input_per_m": 0.55, "cost_output_per_m": 2.2, "supports_tools": 1, "speciality": "code", "recommended_use": "Alternative mature à GLM-4.7 — contexte étendu vs 4.5"},
        {"id": "glm-4.5", "provider_id": "zhipu", "display_name": "GLM-4.5", "tier": "moyen", "context_input": 128000, "context_output": 4096, "cost_input_per_m": 0.28, "cost_output_per_m": 1.1, "supports_tools": 1, "speciality": "code", "recommended_use": "Ancienne version stable des modèles GLM 4.5.", "notes": "Idéal comme base économique pour des tâches simples."},
        {"id": "glm-4.5-air", "provider_id": "zhipu", "display_name": "GLM-4.5 Air", "tier": "leger", "context_input": 128000, "context_output": 4096, "cost_input_per_m": 0.20, "cost_output_per_m": 1.10, "supports_tools": 1, "speciality": "agents_lowcost", "recommended_use": "Le moins cher de la gamme GLM — volumes élevés, tâches simples"},

        # ═══ DEEPINFRA (Pay-as-you-go) ═══
        {"id": "deepinfra/llama-3.3-70b-instruct", "provider_id": "deepinfra", "display_name": "Llama 3.3 70B Instruct (DeepInfra)", "tier": "paid", "context_input": 131072, "context_output": 4096, "cost_input_per_m": 0.23, "cost_output_per_m": 0.23, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "raisonnement", "recommended_use": "Routage alternatif Tier Moyen/Fort — excellent rapport qualité/prix"},
        {"id": "deepinfra/qwen-2.5-72b-instruct", "provider_id": "deepinfra", "display_name": "Qwen 2.5 72B Instruct (DeepInfra)", "tier": "paid", "context_input": 131072, "context_output": 4096, "cost_input_per_m": 0.35, "cost_output_per_m": 0.35, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "code", "recommended_use": "Routage alternatif Tier Moyen/Fort — excellent pour le code et le français"},
        {"id": "deepinfra/deepseek-r1", "provider_id": "deepinfra", "display_name": "DeepSeek R1 671B (DeepInfra)", "tier": "paid", "context_input": 163840, "context_output": 8192, "cost_input_per_m": 0.55, "cost_output_per_m": 2.19, "supports_thinking": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "raisonnement", "recommended_use": "Routage alternatif Tier Fort — raisonnement profond avec balises thinking"},

        # ═══ DEEPSEEK (Pay-as-you-go) ═══
        {"id": "deepseek-v4-flash", "provider_id": "deepseek", "display_name": "DeepSeek V4 Flash", "tier": "paid", "context_input": 1000000, "context_output": 65536, "cost_input_per_m": 0.14, "cost_output_per_m": 0.28, "cost_cached_per_m": 0.003, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "agents_lowcost", "recommended_use": "Tâches de routine à très bas coût"},
        {"id": "deepseek-v4-pro", "provider_id": "deepseek", "display_name": "DeepSeek V4 Pro", "tier": "paid", "context_input": 1000000, "context_output": 65536, "cost_input_per_m": 0.435, "cost_output_per_m": 0.87, "cost_cached_per_m": 0.004, "supports_thinking": 1, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "raisonnement", "recommended_use": "Planification multi-étapes — tarif permanent (ex-promo)"},
        {"id": "deepseek-chat", "provider_id": "deepseek", "display_name": "DeepSeek Chat (alias V4 Flash)", "tier": "paid", "context_input": 1000000, "context_output": 65536, "cost_input_per_m": 0.14, "cost_output_per_m": 0.28, "cost_cached_per_m": 0.003, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "agents_lowcost", "recommended_use": "Alias legacy → redirige vers V4 Flash", "notes": "Alias legacy"},
        {"id": "deepseek-reasoner", "provider_id": "deepseek", "display_name": "DeepSeek Reasoner (legacy R1)", "tier": "paid", "context_input": 1000000, "context_output": 65536, "cost_input_per_m": 0.55, "cost_output_per_m": 2.19, "cost_cached_per_m": 0.14, "supports_thinking": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "raisonnement", "recommended_use": "Legacy R1 — migration vers V4 recommandée", "notes": "Legacy R1"},

        # ═══ xAI Grok (Pay-as-you-go) ═══
        {"id": "grok-4.3", "provider_id": "xai", "display_name": "Grok 4.3", "tier": "paid", "context_input": 200000, "context_output": 4096, "cost_input_per_m": 1.25, "cost_output_per_m": 2.50, "supports_thinking": 1, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "raisonnement", "recommended_use": "Modèle phare xAI avec raisonnement intégré", "last_tested": "2026-06-01"},
        {"id": "grok-4.20-0309-non-reasoning", "provider_id": "xai", "display_name": "Grok 4.20 (Non-Reasoning)", "tier": "paid", "context_input": 200000, "context_output": 4096, "cost_input_per_m": 1.25, "cost_output_per_m": 2.50, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "polyvalent", "recommended_use": "Modèle rapide non-raisonnant xAI", "last_tested": "2026-06-01"},
        {"id": "grok-4.20-0309-reasoning", "provider_id": "xai", "display_name": "Grok 4.20 (Reasoning)", "tier": "paid", "context_input": 200000, "context_output": 4096, "cost_input_per_m": 1.25, "cost_output_per_m": 2.50, "supports_thinking": 1, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "raisonnement", "recommended_use": "Modèle avec raisonnement avancé xAI", "last_tested": "2026-06-01"},
        {"id": "grok-4.20-multi-agent-0309", "provider_id": "xai", "display_name": "Grok 4.20 Multi-Agent", "tier": "paid", "context_input": 200000, "context_output": 4096, "cost_input_per_m": 1.25, "cost_output_per_m": 2.50, "supports_thinking": 1, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "agents", "recommended_use": "Modèle xAI optimisé pour le multi-agent", "last_tested": "2026-06-01"},
        {"id": "grok-build-0.1", "provider_id": "xai", "display_name": "Grok Build 0.1", "tier": "paid", "context_input": 200000, "context_output": 4096, "cost_input_per_m": 1.00, "cost_output_per_m": 2.00, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "code", "recommended_use": "Modèle xAI spécialisé développement et build", "last_tested": "2026-06-01"},

        # ═══ GEMINI PAID (GCP) ═══
        {"id": "gemini-3.5-flash-paid", "provider_id": "gemini_paid", "display_name": "Gemini 3.5 Flash (GCP)", "tier": "paid", "context_input": 1048576, "context_output": 65536, "cost_input_per_m": 1.282575, "cost_output_per_m": 7.69545, "cost_cached_per_m": 0.128257, "currency": "EUR", "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "supports_search_grounding": 1, "speciality": "agents_grounding", "recommended_use": "API payante avec Search Grounding débloqué", "last_tested": "2026-05-26"},
        {"id": "gemini-2.5-pro-paid", "provider_id": "gemini_paid", "display_name": "Gemini 2.5 Pro (GCP)", "tier": "paid", "context_input": 2097152, "context_output": 65536, "cost_input_per_m": 1.068812, "cost_output_per_m": 8.5505, "cost_cached_per_m": 0.106881, "currency": "EUR", "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "supports_search_grounding": 1, "speciality": "raisonnement_pro", "recommended_use": "Raisonnement avancé GCP — context 2M", "last_tested": "2026-05-26"},
        {"id": "gemini-2.5-flash-paid", "provider_id": "gemini_paid", "display_name": "Gemini 2.5 Flash (GCP)", "tier": "paid", "context_input": 1048576, "context_output": 65536, "cost_input_per_m": 0.256515, "cost_output_per_m": 2.137625, "cost_cached_per_m": 0.025651, "currency": "EUR", "supports_thinking": 1, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "supports_search_grounding": 1, "speciality": "economique", "recommended_use": "Backup payant économique", "last_tested": "2026-05-26"},
        {"id": "gemini-3.1-flash-lite-paid", "provider_id": "gemini_paid", "display_name": "Gemini 3.1 Flash Lite (GCP)", "tier": "paid", "context_input": 1048576, "context_output": 65536, "cost_input_per_m": 0.213762, "cost_output_per_m": 1.282575, "cost_cached_per_m": 0.021376, "currency": "EUR", "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "speciality": "ultra_economique", "recommended_use": "Le moins cher en GCP payant"},
        {"id": "gemini-3.1-pro-preview-paid", "provider_id": "gemini_paid", "display_name": "Gemini 3.1 Pro Preview (GCP)", "tier": "paid", "context_input": 2097152, "context_output": 65536, "cost_input_per_m": 1.7101, "cost_output_per_m": 10.2606, "currency": "EUR", "supports_thinking": 1, "supports_tools": 1, "supports_vision": 1, "supports_json_mode": 1, "supports_streaming": 1, "supports_search_grounding": 1, "speciality": "raisonnement_pro", "recommended_use": "Pro avec Grounding + context 2M", "last_tested": "2026-05-26"},
        {"id": "gemini-3-pro-preview-paid", "provider_id": "gemini_paid", "display_name": "Gemini 3 Pro Preview (GCP)", "tier": "paid", "context_input": 2097152, "context_output": 65536, "cost_input_per_m": 1.7101, "cost_output_per_m": 10.2606, "currency": "EUR", "supports_thinking": 1, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "supports_search_grounding": 1, "speciality": "raisonnement_pro", "recommended_use": "Pro preview GCP", "last_tested": "2026-05-26"},
        {"id": "gemini-3.1-pro-customtools-paid", "provider_id": "gemini_paid", "display_name": "Gemini 3.1 Pro Custom Tools (GCP)", "tier": "paid", "context_input": 2097152, "context_output": 65536, "cost_input_per_m": 1.7101, "cost_output_per_m": 10.2606, "currency": "EUR", "supports_thinking": 1, "supports_tools": 1, "supports_json_mode": 1, "supports_streaming": 1, "supports_search_grounding": 1, "speciality": "tool_use", "recommended_use": "Pro optimisé Tool Use", "last_tested": "2026-05-26"},
        {"id": "gemini-2.0-flash-tts-paid", "provider_id": "gemini_paid", "display_name": "Gemini 2.0 Flash TTS (GCP)", "tier": "paid", "context_input": 1048576, "context_output": 65536, "supports_audio": 1, "speciality": "tts", "recommended_use": "Synthèse vocale native (TTS multimodal)"},
        {"id": "gemini-3-pro-short-paid", "provider_id": "gemini_paid", "display_name": "Gemini 3 Pro Short (GCP)", "tier": "paid", "context_input": 2097152, "context_output": 65536, "cost_input_per_m": 1.7101, "cost_output_per_m": 10.2606, "currency": "EUR", "supports_thinking": 1, "supports_tools": 1, "supports_streaming": 1, "supports_search_grounding": 1, "speciality": "raisonnement_pro", "recommended_use": "Alias court → 3-pro-preview", "notes": "Alias court"},

        # ═══ CLOUD APIs (hors LLM) ═══
        {"id": "cloud-tts", "provider_id": "cloud_apis", "display_name": "Cloud Text-to-Speech", "tier": "paid", "speciality": "tts", "recommended_use": "Synthèse vocale Google Cloud"},
        {"id": "cloud-stt", "provider_id": "cloud_apis", "display_name": "Cloud Speech-to-Text", "tier": "paid", "speciality": "stt", "recommended_use": "Reconnaissance vocale Google Cloud"},
        {"id": "cloud-vision", "provider_id": "cloud_apis", "display_name": "Cloud Vision API", "tier": "paid", "speciality": "vision", "recommended_use": "Analyse d'images Google Cloud"},
        {"id": "cloud-translation", "provider_id": "cloud_apis", "display_name": "Cloud Translation API", "tier": "paid", "speciality": "traduction", "recommended_use": "Traduction multilingue Google Cloud"},
        
        # ═══ MiniMax, Imagen, Veo, Flux (Spécialisés) ═══
        {"id": "minimax-m3", "provider_id": "minimax", "display_name": "MiniMax M3", "tier": "paid", "context_input": 64000, "context_output": 4096, "cost_input_per_m": 1.20, "cost_output_per_m": 1.20, "supports_tools": 1, "speciality": "polyvalent", "recommended_use": "Modèle d'écriture créative et multilingue"},
        {"id": "imagen-4", "provider_id": "cloud_apis", "display_name": "Imagen 4", "tier": "paid", "speciality": "images", "recommended_use": "Génération d'images photoréalistes Google"},
        {"id": "veo-3", "provider_id": "cloud_apis", "display_name": "Veo 3", "tier": "paid", "speciality": "videos", "recommended_use": "Génération de vidéos cinématiques Google"},
        {"id": "flux-1.1-pro", "provider_id": "flux", "display_name": "Flux 1.1 Pro", "tier": "paid", "speciality": "images", "recommended_use": "Génération d'images ultra-précises (BFL)"},
    ]

    count = 0
    for m in models:
        mid = m.pop("id")
        ok = upsert_model(mid, **m)
        if ok:
            count += 1
        print(f"  {'✅' if ok else '❌'} {mid}")

    return count


def seed_api_keys():
    """Insère les clés API connues (sans les valeurs sensibles)."""
    print("\n── Clés API ──")

    keys = [
        {"id": "GEMINI_API_KEY", "provider_id": "gemini_free", "env_var": "GEMINI_API_KEY", "project_name": "moteur-ia-free", "key_type": "free", "quota_rpm": 15, "quota_rpd": 500, "quota_tpm": 250000, "last_tested": "2026-05-26"},
        {"id": "GEMINI_API_KEY_2", "provider_id": "gemini_free", "env_var": "GEMINI_API_KEY_2", "project_name": "moteur-ia-backup-1", "key_type": "free", "quota_rpm": 15, "quota_rpd": 500, "quota_tpm": 250000, "last_tested": "2026-05-26"},
        {"id": "GEMINI_API_KEY_3", "provider_id": "gemini_free", "env_var": "GEMINI_API_KEY_3", "project_name": "moteur-ia-backup-2", "key_type": "free", "quota_rpm": 15, "quota_rpd": 500, "quota_tpm": 250000, "last_tested": "2026-05-26"},
        {"id": "GEMINI_API_KEY_4", "provider_id": "gemini_free", "env_var": "GEMINI_API_KEY_4", "project_name": "moteur-ia-backup-3", "key_type": "free", "quota_rpm": 15, "quota_rpd": 500, "quota_tpm": 250000, "last_tested": "2026-05-26"},
        {"id": "GEMINI_API_KEY_5", "provider_id": "gemini_free", "env_var": "GEMINI_API_KEY_5", "project_name": "moteur-ia-backup-4", "key_type": "free", "quota_rpm": 15, "quota_rpd": 500, "quota_tpm": 250000, "last_tested": "2026-05-26"},
        {"id": "GEMINI_PAYANT_API_KEY", "provider_id": "gemini_paid", "env_var": "GEMINI_PAYANT_API_KEY", "project_name": "ha-delta (GCP)", "key_type": "paid", "quota_rpm": 500, "quota_rpd": 10000, "quota_tpm": 4000000, "last_tested": "2026-05-26"},
        {"id": "DEEPSEEK_API_KEY", "provider_id": "deepseek", "env_var": "DEEPSEEK_API_KEY", "project_name": "DeepSeek Prepaid", "key_type": "paid", "quota_rpm": 60, "quota_rpd": None, "quota_tpm": 1000000, "last_tested": "2026-05-26"},
        {"id": "MISTRAL_API_KEY", "provider_id": "mistral", "env_var": "MISTRAL_API_KEY", "project_name": "tab5-engine Mistra", "key_type": "free", "quota_rpm": 30, "quota_rpd": None, "quota_tpm": None, "last_tested": "2026-05-28"},
        {"id": "COHERE_API_KEY", "provider_id": "cohere", "env_var": "COHERE_API_KEY", "project_name": "Cohere Trial", "key_type": "free", "quota_rpm": 10, "quota_rpd": None, "quota_tpm": None, "last_tested": "2026-05-28"},

        {"id": "CEREBRAS_API_KEY", "provider_id": "cerebras", "env_var": "CEREBRAS_API_KEY", "project_name": "Cerebras Free Tier", "key_type": "free", "quota_rpm": 30, "quota_rpd": 14400, "quota_tpm": None, "last_tested": "2026-05-28"},
        {"id": "OPENROUTER_API_KEY", "provider_id": "openrouter", "env_var": "OPENROUTER_API_KEY", "project_name": "OpenRouter Free", "key_type": "free", "quota_rpm": 20, "quota_rpd": None, "quota_tpm": None, "last_tested": "2026-05-28"},
        {"id": "XAI_API_KEY", "provider_id": "xai", "env_var": "XAI_API_KEY", "project_name": "tab5-engine", "key_type": "paid", "quota_rpm": 60, "quota_rpd": None, "quota_tpm": 1000000, "last_tested": "2026-06-01"},
        {"id": "DEEPINFRA_API_KEY", "provider_id": "deepinfra", "env_var": "DEEPINFRA_API_KEY", "project_name": "DeepInfra Paid", "key_type": "paid", "quota_rpm": 100, "quota_rpd": None, "quota_tpm": None, "last_tested": "2026-06-04"},
        {"id": "CLOUD_API_KEY", "provider_id": "cloud_apis", "env_var": "CLOUD_API_KEY", "project_name": "ha-delta (Cloud APIs)", "key_type": "paid", "last_tested": "2026-05-26"},
        {"id": "MINIMAX_API_KEY", "provider_id": "minimax", "env_var": "MINIMAX_API_KEY", "project_name": "MiniMax Pro", "key_type": "paid", "last_tested": "2026-06-03"},
        {"id": "REPLICATE_API_KEY", "provider_id": "flux", "env_var": "REPLICATE_API_KEY", "project_name": "Replicate Flux", "key_type": "paid", "last_tested": "2026-06-03"},
        {"id": "ANTHROPIC_API_KEY", "provider_id": "anthropic_native", "env_var": "ANTHROPIC_API_KEY", "project_name": "Anthropic API (accès direct)", "key_type": "paid"},
        {"id": "ZHIPU_API_KEY", "provider_id": "zhipu", "env_var": "ZHIPU_API_KEY", "project_name": "Zhipu AI Developer Account", "key_type": "paid", "quota_rpm": 100, "quota_tpm": 1000000},
    ]


    count = 0
    for k in keys:
        kid = k.pop("id")
        # Vérifier si la clé existe dans le .env
        env_val = os.environ.get(k["env_var"])
        k["status"] = "active" if env_val else "missing"
        ok = upsert_api_key(kid, **k)
        if ok:
            count += 1
        status_icon = "✅" if env_val else "⚠️"
        print(f"  {status_icon} {kid} ({k['env_var']}) — {'trouvée' if env_val else 'ABSENTE du .env'}")

    return count


def seed_benchmarks():
    """Insère les benchmarks connus pour les modèles principaux."""
    print("\n── Benchmarks ──")

    benchmarks = [
        # Claude Opus 4.8
        ("claude-opus-4-8", "SWE-bench", 88.2, "%"),
        ("claude-opus-4-8", "OSWorld", 94.5, "%"),
        ("claude-opus-4-8", "GPQA Diamond", 80.5, "%"),
        ("claude-opus-4-8", "ARC-AGI-2", 22.0, "%"),
        # Claude Opus 4.7
        ("claude-opus-4-7", "SWE-bench", 87.6, "%"),
        ("claude-opus-4-7", "OSWorld", 94.0, "%"),
        ("claude-opus-4-7", "GPQA Diamond", 79.4, "%"),
        ("claude-opus-4-7", "ARC-AGI-2", 21.2, "%"),
        # Claude Sonnet 4.6
        ("claude-sonnet-4-6", "SWE-bench", 72.7, "%"),
        ("claude-sonnet-4-6", "GPQA Diamond", 73.1, "%"),
        # Gemini 3.5 Flash
        ("gemini-3.5-flash", "MMLU Pro", 78.3, "%"),
        ("gemini-3.5-flash", "GPQA Diamond", 65.0, "%"),
        ("gemini-3.5-flash", "ARC-AGI-2", 18.4, "%"),
        # DeepSeek V4 Pro
        ("deepseek-v4-pro", "SWE-bench", 71.4, "%"),
        ("deepseek-v4-pro", "GPQA Diamond", 72.5, "%"),
        ("deepseek-v4-pro", "ARC-AGI-2", 15.0, "%"),
        # DeepSeek V4 Flash
        ("deepseek-v4-flash", "MMLU Pro", 72.0, "%"),
        # Zhipu GLM
        ("glm-5.2", "SWE-bench Pro", 62.1, "%"),
        ("glm-5.2", "Terminal-Bench 2.1", 81.0, "%"),
        ("glm-4.7", "SWE-bench", 73.8, "%"),
        ("glm-4.5-air", "SWE-bench Verified", 57.6, "%"),
        # OpenRouter — compléments Kimi K2.7
        ("moonshotai/kimi-k2.7-code", "SWE-bench Pro", 58.6, "%"),
        ("moonshotai/kimi-k2.7-code", "SWE-bench Verified", 80.2, "%"),
    ]

    count = 0
    for model_id, bench, score, unit in benchmarks:
        ok = upsert_benchmark(model_id, bench, score, unit)
        if ok:
            count += 1
    print(f"  ✅ {count} benchmarks insérés")
    return count


def seed_subscriptions():
    """Insère les abonnements."""
    print("\n── Abonnements ──")

    subs = [
        {
            "id": "claude_pro",
            "name": "Claude Pro",
            "cost_monthly_usd": 20.0,
            "rolling_window_hours": 5,
            "hourly_token_limit": 1500000,
            "monthly_token_limit": 35000000,
            "estimated_messages_limit": 45,
            "models": ["claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5", "claude-opus-4-5", "claude-sonnet-4-5"],
            "advantages": "Modèle de développement de pointe. SWE-bench 88.2%, OSWorld 94.5%.",
            "recommended_use": "Tâches complexes d'écriture et de refactoring de code (Tier Fort).",
        },
        {
            "id": "gemini_advanced",
            "name": "Gemini Advanced (Google One AI Pro)",
            "cost_monthly_usd": 19.99,
            "rolling_window_hours": 5,
            "hourly_token_limit": 4000000,
            "monthly_token_limit": 100000000,
            "estimated_messages_limit": 50,
            "models": ["gemini-3.5-flash-high-cli", "gemini-3.5-flash-medium-cli", "gemini-cli"],
            "advantages": "Immense fenêtre (1M-2M), Antigravity IDE inclus, très rapide.",
            "recommended_use": "Lecture gros fichiers, analyse globale, Tier Fort/Moyen.",
        },
    ]

    count = 0
    for s in subs:
        sid = s.pop("id")
        ok = upsert_subscription(sid, **s)
        if ok:
            count += 1
        print(f"  {'✅' if ok else '❌'} {sid} — {s['name']}")

    return count


def seed_routing_rules():
    """Insère les règles de routage par type de tâche."""
    print("\n── Règles de routage ──")

    rules = [
        {"task_type": "architecture", "recommended_model": "claude-opus-4-8", "provider_id": "claude_cli", "justification": "SWE-bench 88.2%, OSWorld 94.5%", "effective_cost": "0.57 $/M amorti"},
        {"task_type": "pair_programming", "recommended_model": "claude-sonnet-4-6", "provider_id": "claude_cli", "justification": "Bon équilibre rapidité/qualité en code", "effective_cost": "0.57 $/M amorti"},
        {"task_type": "code_rapide", "recommended_model": "gemini-3.5-flash", "provider_id": "gemini_free", "justification": "Gratuit + 100 tok/s + thinking", "effective_cost": "0.00 $/M"},
        {"task_type": "domotique_simple", "recommended_model": "gemini-3.5-flash", "provider_id": "gemini_free", "justification": "Latence minimale, gratuit", "effective_cost": "0.00 $/M"},
        {"task_type": "planification", "recommended_model": "deepseek-v4-pro", "provider_id": "deepseek", "justification": "Raisonnement long pas cher ($0.435/$0.87)", "effective_cost": "0.435 $/M"},
        {"task_type": "routine_batch", "recommended_model": "deepseek-v4-flash", "provider_id": "deepseek", "justification": "Ultra low-cost ($0.14/$0.28)", "effective_cost": "0.14 $/M"},
        {"task_type": "recherche_web", "recommended_model": "gemini-3.5-flash-paid", "provider_id": "gemini_paid", "justification": "Search Grounding débloqué", "effective_cost": "1.28 EUR/M"},
        {"task_type": "confidentiel", "recommended_model": "qwen2.5-14b-instruct-1m", "provider_id": "local", "justification": "100% local, confidentialité totale", "effective_cost": "0.00 $/M"},
        {"task_type": "embeddings", "recommended_model": "nomic-embed-text-v1.5", "provider_id": "local", "justification": "Embeddings airgapped", "effective_cost": "0.00 $/M"},
    ]

    count = 0
    for r in rules:
        tt = r.pop("task_type")
        ok = upsert_routing_rule(tt, **r)
        if ok:
            count += 1
    print(f"  ✅ {count} règles de routage insérées")
    return count


def seed_access_channels():
    """Peuple la table access_channels avec toutes les combinaisons clé×modèle."""
    print("\n── Canaux d'accès ──")

    # Clés Free Tier Gemini → 4 modèles chacune
    free_keys = ["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4", "GEMINI_API_KEY_5"]
    free_models = [
        ("gemini-3.5-flash", "gemini-3.5-flash-free", 622, 100.0),
        ("gemini-3.1-flash-lite", "gemini-3.1-flash-lite-free", 400, 150.0),
        ("gemini-2.5-flash", "gemini-2.5-flash-free", 550, 80.0),
        ("gemini-3.1-pro-preview", "gemini-3.1-pro-preview-free", 2000, 40.0),
    ]
    count = 0
    for key_id in free_keys:
        for model_id, alias, ttft, tps in free_models:
            ok = upsert_access_channel(
                model_id, key_id,
                access_method="api_rest", provider_alias=alias,
                speed_tier="ultra_fast", latency_ttft_ms=ttft, throughput_tps=tps,
                is_default=1 if key_id == "GEMINI_API_KEY" else 0,
            )
            if ok: count += 1

    # Clé Paid Gemini → 8+ modèles
    paid_models = [
        ("gemini-3.5-flash", "gemini-3.5-flash-paid", 622, 100.0, "Search Grounding"),
        ("gemini-2.5-pro-paid", "gemini-2.5-pro-paid", 2000, 40.0, "Context 2M + Grounding"),
        ("gemini-2.5-flash", "gemini-2.5-flash-paid", 550, 80.0, None),
        ("gemini-3.1-flash-lite", "gemini-3.1-flash-lite-paid", 400, 150.0, None),
        ("gemini-3.5-flash-paid", "gemini-3.5-flash-paid", 622, 100.0, "Search Grounding débloqué"),
        ("gemini-3.1-pro-preview-paid", "gemini-3.1-pro-preview-paid", 2000, 40.0, "Pro + Grounding"),
        ("gemini-3-pro-preview-paid", "gemini-3-pro-preview-paid", 2000, 40.0, None),
        ("gemini-3.1-pro-customtools-paid", "gemini-3.1-pro-customtools-paid", 2000, 40.0, "Tool Use optimisé"),
        ("gemini-3-pro-short-paid", "gemini-3-pro-short-paid", 2000, 40.0, "Alias court"),
        ("gemini-2.0-flash-tts-paid", "gemini-2.0-flash-tts-paid", 1000, None, "TTS multimodal"),
    ]
    for model_id, alias, ttft, tps, notes in paid_models:
        ok = upsert_access_channel(
            model_id, "GEMINI_PAYANT_API_KEY",
            access_method="api_rest", provider_alias=alias,
            speed_tier="fast", latency_ttft_ms=ttft, throughput_tps=tps,
            notes=notes,
        )
        if ok: count += 1

    # DeepSeek → 4 modèles
    ds_models = [
        ("deepseek-v4-flash", "deepseek-v4-flash", 1500, 60.0),
        ("deepseek-v4-pro", "deepseek-v4-pro", 3000, 30.0),
        ("deepseek-chat", "deepseek-chat", 1500, 60.0),
        ("deepseek-reasoner", "deepseek-reasoner", 4000, 20.0),
    ]
    for model_id, alias, ttft, tps in ds_models:
        ok = upsert_access_channel(
            model_id, "DEEPSEEK_API_KEY",
            access_method="api_rest", provider_alias=alias,
            speed_tier="fast", latency_ttft_ms=ttft, throughput_tps=tps,
            is_default=1,
        )
        if ok: count += 1

    # Mistral API → 3 modèles
    mistral_models = [
        ("mistral-large-latest", "mistral-large-latest", 1000, 40.0),
        ("codestral-latest", "codestral-latest", 800, 50.0),
        ("open-mistral-nemo", "open-mistral-nemo", 600, 60.0),
    ]
    for model_id, alias, ttft, tps in mistral_models:
        ok = upsert_access_channel(
            model_id, "MISTRAL_API_KEY",
            access_method="api_rest", provider_alias=alias,
            speed_tier="fast", latency_ttft_ms=ttft, throughput_tps=tps,
            is_default=1,
        )
        if ok: count += 1

    # Cohere API → 2 modèles
    cohere_models = [
        ("command-r-plus-08-2024", "command-r-plus-08-2024", 1200, 35.0),
        ("command-r-08-2024", "command-r-08-2024", 800, 45.0),
    ]
    for model_id, alias, ttft, tps in cohere_models:
        ok = upsert_access_channel(
            model_id, "COHERE_API_KEY",
            access_method="api_rest", provider_alias=alias,
            speed_tier="medium", latency_ttft_ms=ttft, throughput_tps=tps,
            is_default=1,
        )
        if ok: count += 1



    # Cerebras API → 2 modèles
    cerebras_models = [
        ("gpt-oss-120b", "gpt-oss-120b", 150, 450.0),
        ("zai-glm-4.7", "zai-glm-4.7", 100, 500.0),
    ]
    for model_id, alias, ttft, tps in cerebras_models:
        ok = upsert_access_channel(
            model_id, "CEREBRAS_API_KEY",
            access_method="api_rest", provider_alias=alias,
            speed_tier="ultra_fast", latency_ttft_ms=ttft, throughput_tps=tps,
            is_default=1,
        )
        if ok: count += 1

    # OpenRouter API
    openrouter_models = [
        ("meta-llama/llama-3.3-70b-instruct:free", "meta-llama/llama-3.3-70b-instruct:free", 800, 50.0),
        ("meta-llama/llama-3.2-3b-instruct:free", "meta-llama/llama-3.2-3b-instruct:free", 500, 80.0),
        ("anthropic/claude-3.5-sonnet", "anthropic/claude-3.5-sonnet", 800, 60.0),
        ("anthropic/claude-sonnet-5", "anthropic/claude-sonnet-5", 800, 60.0),
        ("openai/gpt-5.2", "openai/gpt-5.2", 1200, 60.0),
        ("moonshotai/kimi-k2.7-code", "moonshotai/kimi-k2.7-code", 1500, 50.0),
        ("qwen/qwen3.7-max", "qwen/qwen3.7-max", 1500, 45.0),
    ]
    for model_id, alias, ttft, tps in openrouter_models:
        ok = upsert_access_channel(
            model_id, "OPENROUTER_API_KEY",
            access_method="api_rest", provider_alias=alias,
            speed_tier="fast", latency_ttft_ms=ttft, throughput_tps=tps,
            is_default=1,
        )
        if ok: count += 1

    # Anthropic API directe (ANTHROPIC_API_KEY) — modèles anthropic_native + accès
    # direct alternatif aux modèles claude_cli (bypass des quotas d'abonnement Claude Pro)
    anthropic_direct_models = [
        "claude-sonnet-5", "claude-fable-5", "claude-opus-4-8", "claude-haiku-4-5",
    ]
    for model_id in anthropic_direct_models:
        ok = upsert_access_channel(
            model_id, "ANTHROPIC_API_KEY",
            access_method="api_rest", provider_alias=model_id,
            speed_tier="fast", is_default=1,
            notes="API directe payante — indépendante des quotas de l'abonnement Claude Pro CLI",
        )
        if ok: count += 1

    # Zhipu API (GLM)
    zhipu_models = ["glm-5.2", "glm-5-turbo", "glm-5", "glm-5.1", "glm-4.7", "glm-4.6", "glm-4.5", "glm-4.5-air"]
    for model_id in zhipu_models:
        ok = upsert_access_channel(
            model_id, "ZHIPU_API_KEY",
            access_method="api_rest", provider_alias=model_id,
            speed_tier="fast", is_default=1,
        )
        if ok: count += 1

    # xAI API → 5 modèles
    xai_models = [
        ("grok-4.3", "grok-4.3", 2000, 45.0),
        ("grok-4.20-0309-non-reasoning", "grok-4.20-0309-non-reasoning", 1500, 60.0),
        ("grok-4.20-0309-reasoning", "grok-4.20-0309-reasoning", 2500, 35.0),
        ("grok-4.20-multi-agent-0309", "grok-4.20-multi-agent-0309", 2500, 35.0),
        ("grok-build-0.1", "grok-build-0.1", 1800, 40.0),
    ]
    for model_id, alias, ttft, tps in xai_models:
        ok = upsert_access_channel(
            model_id, "XAI_API_KEY",
            access_method="api_rest", provider_alias=alias,
            speed_tier="fast", latency_ttft_ms=ttft, throughput_tps=tps,
            is_default=1,
        )
        if ok: count += 1

    # DeepInfra API → 3 modèles
    deepinfra_models = [
        ("deepinfra/llama-3.3-70b-instruct", "deepinfra/llama-3.3-70b-instruct", 800, 100.0),
        ("deepinfra/qwen-2.5-72b-instruct", "deepinfra/qwen-2.5-72b-instruct", 700, 110.0),
        ("deepinfra/deepseek-r1", "deepinfra/deepseek-r1", 3500, 30.0),
    ]
    for model_id, alias, ttft, tps in deepinfra_models:
        ok = upsert_access_channel(
            model_id, "DEEPINFRA_API_KEY",
            access_method="api_rest", provider_alias=alias,
            speed_tier="fast", latency_ttft_ms=ttft, throughput_tps=tps,
            is_default=1,
        )
        if ok: count += 1

    # Claude CLI (pas de clé API, accès via CLI)

    claude_models = [
        ("claude-opus-4-8", "claude-opus-4-8", 6000, 25.0),
        ("claude-opus-4-7", "claude-opus-4-7", 6000, 25.0),
        ("claude-sonnet-4-6", "claude-sonnet-4-6", 3000, 40.0),
        ("claude-haiku-4-5", "claude-haiku-4-5", 1500, 80.0),
        ("claude-opus-4-5", "claude-opus-4-5", 6000, 25.0),
        ("claude-sonnet-4-5", "claude-sonnet-4-5", 3000, 40.0),
        ("claude-opus-4-0", "claude-opus-4-8", 6000, 25.0),
    ]
    for model_id, alias, ttft, tps in claude_models:
        ok = upsert_access_channel(
            model_id, None,
            access_method="cli", provider_alias=alias,
            speed_tier="medium", latency_ttft_ms=ttft, throughput_tps=tps,
            is_default=1, notes="Via claude.cmd (Claude Pro)",
        )
        if ok: count += 1

    # Gemini CLI (pas de clé API, accès via CLI Antigravity)
    gemini_cli_models = [
        ("gemini-3.5-flash-high-cli", "gemini-3.5-flash-high-cli", 2000, 60.0),
        ("gemini-3.5-flash-medium-cli", "gemini-3.5-flash-medium-cli", 2000, 60.0),
        ("gemini-cli", "gemini-cli", 2000, 60.0),
    ]
    for model_id, alias, ttft, tps in gemini_cli_models:
        ok = upsert_access_channel(
            model_id, None,
            access_method="cli", provider_alias=alias,
            speed_tier="fast", latency_ttft_ms=ttft, throughput_tps=tps,
            is_default=1, notes="Via antigravity-ide.cmd (Gemini Advanced)",
        )
        if ok: count += 1

    # Local (pas de clé, accès via LM Studio)
    local_models = [
        "qwen2.5-14b-instruct-1m", "qwen3-coder-next", "qwen2.5-coder-32b",
        "gemma-4-31b", "gemma-4-26b-a4b", "gemma-4-e4b",
        "qwen3.6-35b-a3b", "deepseek-coder-v2-lite", "nomic-embed-text-v1.5",
    ]
    for model_id in local_models:
        ok = upsert_access_channel(
            model_id, None,
            access_method="local", provider_alias=model_id,
            speed_tier="fast", is_default=1,
            notes="LM Studio local (RTX 5070 Ti)",
        )
        if ok: count += 1

    # Ollama Local (pas de clé, accès via Ollama local)
    ollama_models = [
        ("qwen2.5-coder:7b", "qwen2.5-coder:7b", 75.0),
        ("deepseek-r1:8b", "deepseek-r1:8b", 50.0),
    ]
    for model_id, alias, tps in ollama_models:
        ok = upsert_access_channel(
            model_id, None,
            access_method="api_rest", provider_alias=alias,
            speed_tier="fast", latency_ttft_ms=500, throughput_tps=tps,
            is_default=1, notes="Ollama local (RTX 5070 Ti)",
        )
        if ok: count += 1

    # Cloud APIs (clé CLOUD_API_KEY)
    for model_id in ["cloud-tts", "cloud-stt", "cloud-vision", "cloud-translation"]:
        ok = upsert_access_channel(
            model_id, "CLOUD_API_KEY",
            access_method="api_rest", provider_alias=model_id,
            speed_tier="fast", is_default=1,
        )
        if ok: count += 1

    # MiniMax, Imagen, Veo, Flux Channels
    ok = upsert_access_channel("minimax-m3", "MINIMAX_API_KEY", access_method="api_rest", speed_tier="fast", is_default=1)
    if ok: count += 1
    ok = upsert_access_channel("imagen-4", "CLOUD_API_KEY", access_method="api_rest", speed_tier="fast", is_default=1)
    if ok: count += 1
    ok = upsert_access_channel("veo-3", "CLOUD_API_KEY", access_method="api_rest", speed_tier="medium", is_default=1)
    if ok: count += 1
    ok = upsert_access_channel("flux-1.1-pro", "REPLICATE_API_KEY", access_method="api_rest", speed_tier="fast", is_default=1)
    if ok: count += 1

    print(f"  ✅ {count} canaux d'accès insérés")
    return count


def seed_quota_realtime():
    """Initialise la table quota_realtime avec les limites par clé API."""
    print("\n── Quotas temps réel (init) ──")


    quotas = [
        # 5 clés Gemini Free (15 RPM, 500 RPD, 250K TPM chacune)
        {"api_key_id": "GEMINI_API_KEY", "limit_rpm": 15, "limit_rpd": 500, "limit_tpm": 250000, "external_status": "ok"},
        {"api_key_id": "GEMINI_API_KEY_2", "limit_rpm": 15, "limit_rpd": 500, "limit_tpm": 250000, "external_status": "ok"},
        {"api_key_id": "GEMINI_API_KEY_3", "limit_rpm": 15, "limit_rpd": 500, "limit_tpm": 250000, "external_status": "ok"},
        {"api_key_id": "GEMINI_API_KEY_4", "limit_rpm": 15, "limit_rpd": 500, "limit_tpm": 250000, "external_status": "ok"},
        {"api_key_id": "GEMINI_API_KEY_5", "limit_rpm": 15, "limit_rpd": 500, "limit_tpm": 250000, "external_status": "ok"},
        # Clé Gemini Paid (500 RPM, 10K RPD, 4M TPM)
        {"api_key_id": "GEMINI_PAYANT_API_KEY", "limit_rpm": 500, "limit_rpd": 10000, "limit_tpm": 4000000, "external_status": "ok"},
        # DeepSeek (60 RPM, 1M TPM, balance prépayée)
        {"api_key_id": "DEEPSEEK_API_KEY", "limit_rpm": 60, "limit_tpm": 1000000, "external_status": "unknown"},
        # Mistral API (Experiment)
        {"api_key_id": "MISTRAL_API_KEY", "limit_rpm": 30, "limit_rpd": None, "limit_tpm": None, "external_status": "ok"},
        # Cohere API (Trial)
        {"api_key_id": "COHERE_API_KEY", "limit_rpm": 10, "limit_rpd": None, "limit_tpm": None, "external_status": "ok"},

        # Cerebras API (Free)
        {"api_key_id": "CEREBRAS_API_KEY", "limit_rpm": 30, "limit_rpd": 14400, "limit_tpm": None, "external_status": "ok"},
        # OpenRouter API (Free)
        {"api_key_id": "OPENROUTER_API_KEY", "limit_rpm": 20, "limit_rpd": None, "limit_tpm": None, "external_status": "ok"},
        # xAI API (Grok)
        {"api_key_id": "XAI_API_KEY", "limit_rpm": 60, "limit_tpm": 1000000, "external_status": "ok"},
        # DeepInfra API
        {"api_key_id": "DEEPINFRA_API_KEY", "limit_rpm": 100, "limit_tpm": None, "external_status": "ok"},
        # Cloud APIs (pas de limites glissantes connues)
        {"api_key_id": "CLOUD_API_KEY", "external_status": "ok"},
        # MiniMax & Replicate FCM
        {"api_key_id": "MINIMAX_API_KEY", "external_status": "ok"},
        {"api_key_id": "REPLICATE_API_KEY", "external_status": "ok"},
    ]

    count = 0
    for q in quotas:
        ok = update_quota_realtime(**q, source="seed")
        if ok: count += 1

    print(f"  ✅ {count} quotas initialisés")
    return count


# ══════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("═" * 60)
    print("  Seed models_registry.db — V7.4")
    print("═" * 60)

    # Garde-fou V12 pour ne pas écraser une base de données enrichie (ex: 91 modèles)
    import sys
    force = "--force" in sys.argv
    try:
        stats = get_db_stats()
        current_models = stats.get('models', 0)
        if current_models > 40 and not force:
            print(f"\n❌ ATTENTION : La base de données contient actuellement {current_models} modèles.")
            print("Le script de seed statique ne contient que ~32 modèles d'origine et risque d'écraser")
            print("vos enrichissements récents (comme l'audit à 91 modèles).")
            print("Si vous souhaitez réellement réinitialiser la base de données, exécutez :")
            print("  python seed_models_db.py --force")
            print("\nExécution annulée par sécurité.")
            sys.exit(1)
    except Exception as e:
        # Si la base n'existe pas ou est vide, on procède normalement
        pass

    n_providers = seed_providers()
    n_models = seed_models()
    n_keys = seed_api_keys()
    n_benchmarks = seed_benchmarks()
    n_subs = seed_subscriptions()
    n_rules = seed_routing_rules()
    n_channels = seed_access_channels()
    n_quotas = seed_quota_realtime()

    print("\n" + "═" * 60)
    print("  BILAN")
    print("═" * 60)

    stats = get_db_stats()
    print(f"  Providers       : {stats.get('providers', 0)}")
    print(f"  Modèles         : {stats.get('models', 0)} (actifs: {stats.get('active_models', 0)})")
    print(f"  Clés API        : {stats.get('api_keys', 0)}")
    print(f"  Benchmarks      : {stats.get('benchmarks', 0)}")
    print(f"  Abonnements     : {stats.get('subscriptions', 0)}")
    print(f"  Routing Rules   : {stats.get('routing_rules', 0)}")
    print(f"  Access Channels : {stats.get('access_channels', 0)}")
    print(f"  Quota Realtime  : {stats.get('quota_realtime', 0)}")
    print(f"  Taille BDD      : {stats.get('db_size_kb', 0)} KB")
    print(f"  Chemin          : {stats.get('db_path', '?')}")
    print("\n✅ Seed terminé avec succès.")
