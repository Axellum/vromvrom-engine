"""
mcp_server.py — Serveur MCP du Moteur Agents pour Antigravity IDE.

Expose 17 outils (dans l'ordre du fichier) :
  1.  run_tab5_agent            : Pipeline complet multi-agents (tâches complexes)
  2.  query_deepseek            : Appel direct DeepSeek (bypass moteur)
  3.  get_engine_status         : État complet du moteur (quotas, CB, sessions)
  4.  get_models_catalog        : Catalogue des modèles depuis models_registry.db
  5.  search_ha_entities        : Recherche d'entités Home Assistant
  6.  rag_search                : Recherche sémantique vectorielle (ChromaDB, espace Gemini)
  7.  query_token_usage         : Statistiques d'usage de tokens
  8.  get_routing_recommendation: Recommandation de modèle pour une tâche précise
  9.  get_routing_matrix        : Matrice de routage coût/avantage (routing_rules + coût live)
  10. validate_config_format    : Linter YAML / Jinja2 / ESPHome
  11. delegate_complex_reasoning: Délégation planifiée au moteur
  12. query_llm_direct          : Appel direct à tout provider du LLMGateway
  13. list_available_models     : Inventaire des modèles + état circuit breakers
  14. execute_ha_action         : Commande HA directe (service REST)
  15. query_runtime             : Lecture SQL read-only de moteur_runtime.db
  16. search_memory             : Recherche plein-texte de memory.db (faits/épisodes/graphe)
  17. delegate_to_gateway       : Délégation auto au gateway (routage coût/avantage + exécution, #T40)

@version 3.5.0 — 17 outils ; délégation auto au gateway (delegate_to_gateway, #T40) ; cœur pipeline mutualisé (_run_engine_pipeline) entre run_tab5_agent et delegate_complex_reasoning (sans changement d'API)
"""

from mcp.server.fastmcp import FastMCP
from core.engine import Engine
from core.router import Router
from core.llm_gateway import LLMGateway
from tools.tool_registry import ToolRegistry
from agents.executor import ExecutorAgent
from agents.planner import PlannerAgent
from agents.antigravity_agent import AntigravityAgent
from tools.system import read_file, write_file, validate_config_yaml
from tools.terminal import run_terminal_command
from tools.api import call_api
from memory.context_manager import ContextManager
import logging
import os
import json
from dotenv import load_dotenv

# Résolution absolue du fichier .env pour éviter les problèmes de CWD (répertoire de travail) de l'IDE
dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(dotenv_path=dotenv_path)

# Désactiver les logs bruyants qui pourraient perturber stdio (FastMCP gère ça en partie, mais prudence)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger("mcp_server")

# Initialisation du serveur FastMCP
mcp = FastMCP("Tab5 Engine")

# Singleton LLMGateway pour les outils légers (pas besoin du moteur complet)
_gateway = None

def get_gateway():
    """Retourne le singleton LLMGateway (initialisé au premier appel)."""
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway


# [P1-2.1] Singleton Router câblé pour les outils MCP légers (gateway + config),
# évite un Router nu (slow-path LLM et Elo morts).
_router = None

def get_router():
    """Retourne un Router singleton câblé (gateway + config)."""
    global _router
    if _router is None:
        from core.llm_gateway import load_config
        _router = Router(default_agent="planner", llm_gateway=get_gateway(), config=load_config())
    return _router


def setup_engine(session_id: str = "mcp_session"):
    """Prépare les composants du moteur pour l'exécution complète."""
    gateway = get_gateway()
    registry = ToolRegistry()
    context_manager = ContextManager(llm_gateway=gateway)
    
    # Enregistrement des outils
    registry.register("read_file", read_file, "Lit le contenu d'un fichier texte local.")
    registry.register("write_file", write_file, "Crée ou modifie un fichier texte local.")
    registry.register("run_terminal_command", run_terminal_command, "Exécute une commande système sur la machine hôte.")
    registry.register("call_api", call_api, "Effectue une requête HTTP (GET/POST) vers une API distante.")
    registry.register("validate_config_yaml", validate_config_yaml, "Valide la syntaxe et les dépendances d'un fichier YAML ESPHome.")

    
    # Initialisation des agents selon la stratégie de ventilation et config.json
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    config = {
        "planner_model": "deepseek-reasoner",
        "executor_model": "deepseek-chat",
        "antigravity_model": "gemini"
    }
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config.update(json.load(f))
        except Exception as e:
            logging.error(f"Erreur chargement config.json: {e}")

    from agents.ha_agent import HACommandAgent

    executor = ExecutorAgent(llm_gateway=gateway, tool_registry=registry, provider_name=config["executor_model"])
    planner = PlannerAgent(llm_gateway=gateway, provider_name=config["planner_model"])
    antigravity_agent = AntigravityAgent(llm_gateway=gateway, provider_name=config["antigravity_model"])
    ha_agent = HACommandAgent(llm_gateway=gateway, tool_registry=registry, provider_name=config["executor_model"])
    
    # Assemblage
    engine = Engine(session_id=session_id, context_manager=context_manager)
    engine.register_agent(executor)
    engine.register_agent(planner)
    engine.register_agent(antigravity_agent)
    engine.register_agent(ha_agent)

    # [P1-2.1] Router câblé (gateway + config) au lieu d'un Router nu : active le
    # slow-path LLM de classification et le classement Elo piloté par la config.
    router = Router(default_agent="planner", llm_gateway=gateway, config=config)

    return engine, router


async def _run_engine_pipeline(task: str, session_id: str, planner_tier: str = None):
    """Cœur d'exécution partagé par run_tab5_agent et delegate_complex_reasoning.

    Prépare le moteur, surcharge éventuellement le tier du planner, analyse la
    requête et exécute le pipeline. Retourne le `final_state` ; le formatage et la
    gestion d'erreur restent propres à chaque outil (sorties inchangées).
    """
    engine, router = setup_engine(session_id=session_id)

    if planner_tier:
        # L'attribut est 'agents' (public), pas '_agents' (privé)
        for agent in engine.agents.values():
            if agent.name == "planner" and hasattr(agent, "_provider_name"):
                agent._provider_name = planner_tier
                logger.info(f"[DELEGATION] Modèle du planificateur configuré sur : {planner_tier}")

    initial_payload, starting_agent = await router.analyze_request(task)
    return await engine.run(initial_payload, starting_agent)


# ═══════════════════════════════════════════════════════
# Outil 1 — Pipeline complet (existant)
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def run_tab5_agent(user_request: str) -> str:
    """
    Lance le tab5-engine Tab5 pour accomplir une tâche complexe.
    Utilise cet outil lorsque tu as besoin que des sous-agents autonomes réfléchissent, écrivent du code ou fassent des recherches avancées.

    Args:
        user_request: La demande détaillée pour les agents (ex: "Analyse le fichier X et propose une correction", "Crée un script python pour faire Y").
    """
    try:
        final_state = await _run_engine_pipeline(user_request, session_id="mcp_default_session")

        result_summary = []
        result_summary.append("=== Trace de l'exécution ===")
        for idx, update in enumerate(final_state.history):
            result_summary.append(f"[{idx+1}] Agent '{update.agent_name}' ({update.status}) : {str(update.result_data)[:300]}...")
        
        result_summary.append("\n=== Résultat Final ===")
        if final_state.history:
            last_result = final_state.history[-1].result_data
            result_summary.append(str(last_result))
        else:
            result_summary.append("Aucun résultat généré.")
            
        return "\n".join(result_summary)
    except Exception as e:
        return f"Erreur lors de l'exécution du moteur Tab5 : {str(e)}"


# ═══════════════════════════════════════════════════════
# Outil 2 — Appel direct DeepSeek (bypass moteur)
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def query_deepseek(
    prompt: str,
    model: str = "deepseek-chat",
    system_prompt: str = "",
    temperature: float = 0.7
) -> str:
    """
    Appel direct à un modèle DeepSeek (bypass le pipeline complet du moteur).
    Idéal pour du raisonnement low-cost ou des tâches algorithmiques.
    
    Modèles disponibles :
      - deepseek-chat (alias deepseek-v4-flash) : $0.14/$0.28 par M tokens — ultra économique
      - deepseek-reasoner (R1) : $0.55/$2.19 — Chain of Thought, champion algorithmique
      - deepseek-v4-pro : $0.435/$0.87 — raisonnement avancé
    
    Solde compte : ~$19.95 USD.
    
    Args:
        prompt: Le prompt pour DeepSeek.
        model: Modèle DeepSeek (défaut: deepseek-chat).
        system_prompt: Prompt système optionnel.
        temperature: Température de génération (défaut: 0.7).
    """
    gateway = get_gateway()
    
    try:
        # Chercher le provider DeepSeek correspondant
        provider = gateway.providers.get(model)
        if not provider:
            # Essayer les alias courants
            alias_map = {
                "deepseek-chat": "deepseek-chat",
                "deepseek-reasoner": "deepseek-reasoner",
                "deepseek-v4-flash": "deepseek-chat",
                "deepseek-v4-pro": "deepseek-v4-pro",
                "r1": "deepseek-reasoner",
            }
            resolved = alias_map.get(model.lower(), model)
            provider = gateway.providers.get(resolved)
        
        if not provider:
            available = [k for k in gateway.providers if "deepseek" in k.lower()]
            return f"❌ Modèle '{model}' non trouvé. Providers DeepSeek disponibles : {available}"
        
        # generate() attend (system_prompt, user_prompt, **kwargs)
        # Avant le fix : un seul argument → TypeError 'missing positional argument'
        import asyncio
        sys_prompt = system_prompt if system_prompt else "Tu es un assistant expert."
        result = await asyncio.to_thread(
            provider.generate, sys_prompt, prompt, temperature=temperature
        )
        
        return f"{result}\n\n---\n📊 Modèle: {model} | DeepSeek API directe"
    except Exception as e:
        return f"❌ Erreur DeepSeek ({model}): {e}"


# ═══════════════════════════════════════════════════════
# Outil 3 — État complet du moteur
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def get_engine_status() -> str:
    """
    Retourne l'état complet du tab5-engine : quotas KeyPool, 
    état des Circuit Breakers, sessions récentes, solde DeepSeek,
    et statistiques globales de tokens.
    
    Ne lance aucune inférence — lecture pure des données internes.
    """
    gateway = get_gateway()
    
    try:
        status_parts = ["🔧 **État du Moteur Agents**\n"]
        
        # Circuit Breakers
        try:
            cb_status = gateway.get_circuit_breakers_status()
            status_parts.append("### Circuit Breakers")
            # Itérer sur la clé 'providers' (dict de dicts) et non
            # sur cb_status racine qui contient aussi 'total_providers' (int)
            providers_cb = cb_status.get("providers", {})
            for name, info in providers_cb.items():
                cb_info = info.get("circuit_breaker", {}) if isinstance(info, dict) else {}
                state_icon = "🟢" if cb_info.get("state", "").upper() == "CLOSED" else "🔴"
                status_parts.append(f"  {state_icon} **{name}** : {cb_info.get('state', '?')} | Échecs: {cb_info.get('failure_count', 0)}")
            status_parts.append(f"  📊 Total : {cb_status.get('total_providers', 0)} providers, {cb_status.get('total_circuit_breakers', 0)} CB actifs")
        except Exception as e:
            status_parts.append(f"⚠️ Circuit Breakers non disponibles : {e}")
        
        # Token Tracker
        try:
            from core.token_tracker import get_global_summary
            summary = get_global_summary()
            status_parts.append("\n### Consommation de Tokens")
            status_parts.append(f"  📊 Coût total estimé : **${summary.get('total_cost_usd', 0):.4f}**")
            status_parts.append(f"  📊 Tokens totaux : {summary.get('total_tokens', 0):,}")
            if summary.get("by_model"):
                status_parts.append("  Par modèle :")
                for m, data in summary["by_model"].items():
                    status_parts.append(f"    - {m}: {data.get('total_tokens', 0):,} tokens | ${data.get('cost_usd', 0):.4f}")
        except Exception as e:
            status_parts.append(f"⚠️ Token Tracker non disponible : {e}")
        
        # KeyPool (si disponible via le gateway)
        try:
            from core.key_pool import GeminiKeyPool
            pool = GeminiKeyPool()
            pool_stats = pool.get_stats()
            status_parts.append(f"\n### KeyPool Gemini")
            status_parts.append(f"  🔑 Clés Free Tier : {pool_stats.get('available', '?')}/{pool_stats.get('total', '?')} disponibles")
            status_parts.append(f"  💳 Clé payante : {'✅' if pool_stats.get('has_paid') else '❌'}")
        except Exception:
            pass  # KeyPool optionnel
        
        # Providers configurés
        status_parts.append(f"\n### Providers Configurés")
        for name, provider in gateway.providers.items():
            provider_type = type(provider).__name__
            status_parts.append(f"  - **{name}** → {provider_type}")
        
        return "\n".join(status_parts)
    except Exception as e:
        return f"❌ Erreur status : {e}"


# ═══════════════════════════════════════════════════════
# Outil 4 — Catalogue des modèles depuis SQLite
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def get_models_catalog(
    filter_provider: str = "",
    filter_capability: str = ""
) -> str:
    """
    Catalogue complet des modèles (~100, dynamique) depuis models_registry.db (SQLite).
    
    Retourne les modèles avec tarifs, benchmarks, statut et spécialité.
    Filtrable par fournisseur ou par capacité.
    
    Args:
        filter_provider: Filtrer par fournisseur (optionnel). Ex: "lmstudio", "gemini_free", "anthropic", "deepseek".
        filter_capability: Filtrer par capacité (optionnel). Ex: "thinking", "tool_use", "vision", "code".
    """
    try:
        from core.models_db import get_all_models

        models = get_all_models()
        
        if not models:
            return "❌ Aucun modèle trouvé dans models_registry.db. Exécutez seed_models_db.py."
        
        # Filtrage
        if filter_provider:
            models = [m for m in models if filter_provider.lower() in (m.get("provider_id", "") or "").lower()]
        if filter_capability:
            models = [m for m in models if filter_capability.lower() in (m.get("capabilities", "") or "").lower()]
        
        # Formatage
        lines = [f"📚 **{len(models)} modèle(s)** dans le catalogue\n"]
        
        # Grouper par provider
        by_provider = {}
        for m in models:
            pid = m.get("provider_id", "inconnu")
            by_provider.setdefault(pid, []).append(m)
        
        for provider, provider_models in by_provider.items():
            lines.append(f"\n### {provider} ({len(provider_models)} modèles)")
            for m in provider_models:
                cost_in = m.get("input_cost_per_m", 0) or 0
                cost_out = m.get("output_cost_per_m", 0) or 0
                status = "✅" if m.get("status") == "active" else "❌"
                ctx = m.get("context_window", 0) or 0
                ctx_str = f"{ctx:,}" if ctx else "?"
                
                cost_str = f"${cost_in:.2f}/${cost_out:.2f}" if (cost_in + cost_out) > 0 else "GRATUIT"
                
                lines.append(f"  {status} **{m.get('model_id', '?')}** | Ctx: {ctx_str} | Coût: {cost_str} | {m.get('specialty', '')}")
        
        return "\n".join(lines)
    except ImportError:
        return "❌ Module core/models_db.py non trouvé. Le catalogue SQLite n'est pas encore initialisé."
    except Exception as e:
        return f"❌ Erreur catalogue : {e}"


# ═══════════════════════════════════════════════════════
# Outil 5 — Recherche d'entités Home Assistant
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def search_ha_entities(
    query: str,
    domain: str = ""
) -> str:
    """
    Recherche d'entités Home Assistant par nom ou domaine.
    
    Interroge directement l'API HA pour trouver des capteurs, lumières,
    interrupteurs ou tout autre type d'entité.
    
    Args:
        query: Terme de recherche dans le nom des entités (ex: "température", "salon", "volet").
        domain: Filtrer par domaine HA (optionnel). Ex: "sensor", "light", "switch", "climate", "binary_sensor".
    """
    try:
        import requests
        
        # Récupérer les entités HA via l'API
        ha_url = os.environ.get("HA_URL", "http://${HA_HOST:-192.168.1.x}:8123")
        ha_token = os.environ.get("HA_TOKEN", "")
        
        if not ha_token:
            return "❌ Variable HA_TOKEN non configurée dans .env. Impossible de contacter Home Assistant."
        
        response = requests.get(
            f"{ha_url}/api/states",
            headers={"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"},
            timeout=10
        )
        
        if response.status_code != 200:
            return f"❌ HA API erreur {response.status_code}: {response.text[:200]}"
        
        entities = response.json()
        
        # Filtrage par domaine
        if domain:
            entities = [e for e in entities if e.get("entity_id", "").startswith(f"{domain}.")]
        
        # Filtrage par query (recherche dans entity_id et friendly_name)
        query_lower = query.lower()
        matched = [
            e for e in entities 
            if query_lower in e.get("entity_id", "").lower()
            or query_lower in e.get("attributes", {}).get("friendly_name", "").lower()
        ]
        
        if not matched:
            return f"🔍 Aucune entité trouvée pour '{query}'" + (f" dans le domaine '{domain}'" if domain else "")
        
        # Limiter à 30 résultats
        matched = matched[:30]
        
        lines = [f"🏠 **{len(matched)} entité(s)** trouvée(s) pour \"{query}\"" + (f" (domaine: {domain})" if domain else "") + "\n"]
        for e in matched:
            eid = e.get("entity_id", "?")
            name = e.get("attributes", {}).get("friendly_name", "")
            state = e.get("state", "?")
            unit = e.get("attributes", {}).get("unit_of_measurement", "")
            unit_str = f" {unit}" if unit else ""
            lines.append(f"  - `{eid}` → **{name}** = `{state}{unit_str}`")
        
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Erreur recherche HA : {e}"


# ═══════════════════════════════════════════════════════
# Outil 5bis — Recherche sémantique RAG (mémoire partagée)
# ═══════════════════════════════════════════════════════

# Singleton paresseux de l'EmbeddingStore (l'init du client ChromaDB est coûteuse).
_rag_store = None


def _get_rag_store():
    """Instancie (une seule fois) l'EmbeddingStore de recherche vectorielle."""
    global _rag_store
    if _rag_store is None:
        from memory.embeddings import EmbeddingStore
        _rag_store = EmbeddingStore()
    return _rag_store


@mcp.tool()
async def rag_search(
    query: str,
    top_n: int = 5
) -> str:
    """
    Recherche sémantique dans la mémoire vectorielle du moteur (ChromaDB, espace Gemini).

    Interroge la base de connaissances indexée depuis `contexte_ia/` (architecture,
    règles, faits vérifiés, leçons apprises…). Permet à l'IDE et à Claude de partager
    la même mémoire sémantique que le moteur. Lecture seule.

    Args:
        query: Requête en langage naturel (ex: "règles GPIO ESP32-P4", "architecture du routeur LLM").
        top_n: Nombre de sections à retourner (défaut 5).
    """
    try:
        import asyncio

        if not query or not query.strip():
            return "❌ Requête vide."

        backend = os.environ.get("RAG_BACKEND", "LOCAL").upper()
        
        if backend == "VERTEX":
            # [T92] Adapter GCP Vertex AI (Réversibilité)
            project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
            data_store = os.environ.get("VERTEX_DATA_STORE_ID", "")
            if not project_id or not data_store:
                return "⚠️ RAG Vertex non configuré (manque GOOGLE_CLOUD_PROJECT ou VERTEX_DATA_STORE_ID). Repassez RAG_BACKEND=LOCAL."
            
            try:
                from google.cloud import discoveryengine
                client = discoveryengine.SearchServiceClient()
                serving_config = client.serving_config_path(
                    project=project_id,
                    location="global",
                    data_store=data_store,
                    serving_config="default_config",
                )
                request = discoveryengine.SearchRequest(
                    serving_config=serving_config,
                    query=query,
                    page_size=top_n,
                )
                response = await asyncio.to_thread(client.search, request)
                
                results = []
                for res in response.results:
                    doc = res.document
                    struct_data = doc.struct_data
                    title = struct_data.get("title", "") if struct_data else doc.id
                    content = struct_data.get("content", "") if struct_data else "Contenu non structuré"
                    results.append({"source": "VertexAI", "title": title, "score": 1.0, "content": content})
            except ImportError:
                return "❌ Package google-cloud-discoveryengine manquant. Installez-le ou repassez RAG_BACKEND=LOCAL."
            except Exception as e:
                logger.error(f"[RAG] Erreur Vertex AI: {e}")
                return f"❌ Erreur lors de l'appel à Vertex AI Search: {e}"
        else:
            # Backend Local par défaut (ChromaDB)
            store = _get_rag_store()
            if not getattr(store, "_available", False):
                return (
                    "⚠️ RAG vectoriel indisponible (collection ChromaDB non initialisée — "
                    "vérifier la clé GEMINI_API_KEY et l'indexation `index_documents()`)."
                )
            results = await asyncio.to_thread(store.query_similar, query, top_n)
        if not results:
            return f"🔍 Aucun résultat RAG pour « {query} »."

        lines = [f"🧠 **{len(results)} section(s)** pour « {query} » :\n"]
        for r in results:
            src = r.get("source", "inconnu")
            title = r.get("title", "")
            score = r.get("score", 0.0)
            content = (r.get("content", "") or "").strip()
            if len(content) > 600:
                content = content[:600] + " […]"
            header = f"  ### {title or '(sans titre)'} — `{src}` (score {score})"
            lines.append(header)
            lines.append(f"  {content}\n")

        return "\n".join(lines)
    except Exception as e:
        return f"❌ Erreur recherche RAG : {e}"


# ═══════════════════════════════════════════════════════
# Outil 6 — Statistiques de tokens
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def query_token_usage(
    period: str = "today",
    group_by: str = "model"
) -> str:
    """
    Statistiques d'usage de tokens depuis la base SQLite du moteur.
    
    Permet de connaître la consommation par modèle, par canal d'accès,
    ou par période.
    
    Args:
        period: Période d'analyse (défaut: "today"). Options: "today", "week", "month", "all".
        group_by: Regroupement (défaut: "model"). Options: "model", "channel", "session".
    """
    import asyncio
    from core.runtime_db import get_connection

    # Colonne de regroupement (liste blanche → pas d'injection possible).
    group_col = {"model": "model", "channel": "channel", "session": "session_id"}.get(group_by, "model")

    # Filtre de période. ATTENTION : token_usage.timestamp est un REAL epoch Unix.
    # date(timestamp) le lirait comme un jour julien (→ NULL) et la comparaison à un
    # datetime texte échouerait (REAL < TEXT en SQLite) : on convertit explicitement.
    if period == "today":
        period_filter = "AND date(timestamp, 'unixepoch', 'localtime') = date('now', 'localtime')"
    elif period == "week":
        period_filter = "AND timestamp >= CAST(strftime('%s', 'now', '-7 days') AS INTEGER)"
    elif period == "month":
        period_filter = "AND timestamp >= CAST(strftime('%s', 'now', '-30 days') AS INTEGER)"
    else:  # "all" = pas de filtre
        period_filter = ""

    limit_clause = "LIMIT 20" if group_by == "session" else ""
    query = f"""
        SELECT {group_col},
               SUM(prompt_tokens)     AS total_input,
               SUM(completion_tokens) AS total_output,
               SUM(cost_usd)          AS total_cost,
               COUNT(*)               AS calls
        FROM token_usage
        WHERE 1=1 {period_filter}
        GROUP BY {group_col}
        ORDER BY total_cost DESC
        {limit_clause}
    """

    def _run():
        conn = get_connection()
        try:
            conn.execute("PRAGMA query_only=ON")  # lecture seule (cohérent avec query_runtime)
            return conn.execute(query).fetchall()
        finally:
            conn.close()

    try:
        rows = await asyncio.to_thread(_run)

        if not rows:
            return f"📊 Aucune donnée de tokens pour la période '{period}'."

        lines = [f"📊 **Usage tokens** (période: {period}, groupé par: {group_by})\n"]
        total_cost = 0.0
        total_tokens = 0
        for row in rows:
            name = row[0] or "inconnu"
            inp = row[1] or 0
            out = row[2] or 0
            cost = row[3] or 0.0
            calls = row[4] or 0
            total = inp + out
            total_cost += cost
            total_tokens += total
            lines.append(f"  - **{name}** : {total:,} tokens ({inp:,} in + {out:,} out) | ${cost:.4f} | {calls} appels")

        lines.append(f"\n**Total** : {total_tokens:,} tokens | **${total_cost:.4f}**")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Erreur token usage : {e}"


# ═══════════════════════════════════════════════════════
# Helpers de routage partagés (recommandation + délégation, #T40)
# ═══════════════════════════════════════════════════════

# Paniers de tiers du catalogue dynamique par contrainte budgétaire.
_BUDGET_TIERS = {
    "free":  ["local", "free"],
    "cheap": ["leger", "moyen"],
    "best":  ["pro", "fort"],
}


def _effective_budget(budget_constraint: str, routing_type: str) -> str:
    """Résout le budget effectif ('auto' → panier selon le type de tâche détecté)."""
    if budget_constraint != "auto":
        return budget_constraint
    if routing_type in ("complex_dev", "architecture", "code_generation", "analysis"):
        return "best"
    if routing_type in ("casual_chat", "simple", "home_assistant"):
        return "free"
    return "cheap"


def _catalog_models_for_budget(effective_budget: str) -> list:
    """
    Retourne les modèles actifs du catalogue dynamique pour un budget donné,
    ordonnés par cascade_priority (le plus prioritaire d'abord), dédupliqués.

    Source unique partagée par `get_routing_recommendation` et `delegate_to_gateway`.
    """
    from core.models_db import get_models_for_tier, get_model_cost
    tiers = _BUDGET_TIERS.get(effective_budget, _BUDGET_TIERS["cheap"])
    picked, seen = [], set()
    for tier in tiers:
        # get_models_for_tier trie déjà par cascade_priority (le plus prioritaire d'abord).
        for m in get_models_for_tier(tier):
            mid = m.get("id")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            cost = get_model_cost(mid) or {}
            ci, co = cost.get("cost_input_per_m"), cost.get("cost_output_per_m")
            cur = cost.get("currency", "USD")
            cost_str = "0$ (local/gratuit)" if (ci is None and co is None) else f"{ci or 0:.2f}/{co or 0:.2f} {cur}/M"
            picked.append({
                "id": mid, "tier": m.get("tier"), "cost": cost_str,
                "is_free": (ci is None and co is None) or (not ci and not co),
                "speciality": m.get("speciality") or "",
                "use": m.get("recommended_use") or "",
            })
    return picked


def _resolve_gateway_provider(gateway, model_id: str):
    """
    Résout un id de modèle vers un provider du LLMGateway : exact d'abord,
    puis recherche partielle (ex: 'deepseek' → 'deepseek-chat'). Partagé par
    query_llm_direct et delegate_to_gateway.

    Retourne (clé_résolue, provider) ou (model_id, None) si introuvable.
    """
    provider = gateway.providers.get(model_id)
    if provider:
        return model_id, provider
    for key in gateway.providers:
        if model_id.lower() in key.lower():
            return key, gateway.providers[key]
    return model_id, None


# ═══════════════════════════════════════════════════════
# Outil 7 — Recommandation de routage
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def get_routing_recommendation(
    task_description: str,
    budget_constraint: str = "auto"
) -> str:
    """
    Recommande le meilleur modèle et canal pour une tâche donnée.

    Utilise le Router V7 pour analyser la tâche, puis sélectionne les modèles
    optimaux **dans le catalogue dynamique** (`models_registry.db`) selon le tier
    correspondant au budget (coûts live, plus de liste codée en dur). Complète
    `get_routing_matrix` (table de référence complète) en ciblant une tâche précise.

    Args:
        task_description: Description de la tâche à accomplir.
        budget_constraint: Contrainte budgétaire (défaut: "auto"). Options: "free" (tiers local/free), "cheap" (tiers leger/moyen), "best" (tiers pro/fort), "auto" (choix selon le type de tâche).
    """
    try:
        router = get_router()

        # Analyser la requête
        payload, agent_name = await router.analyze_request(task_description)
        
        metadata = payload.metadata or {}
        routing_type = metadata.get("routing_type", "standard")
        
        # Construire la recommandation
        lines = [f"🎯 **Recommandation de routage** pour :\n> \"{task_description[:100]}...\"\n"]
        
        lines.append(f"### Analyse")
        lines.append(f"  - **Type de tâche** : {routing_type}")
        lines.append(f"  - **Agent recommandé** : {agent_name}")
        
        if metadata.get("multi_intent"):
            lines.append(f"  - **Multi-intent** : {len(metadata.get('sub_intents', []))} sous-tâches détectées")
        
        # Sélection des modèles dans le catalogue dynamique selon le budget → tiers
        # (logique partagée avec delegate_to_gateway, #T40).
        import asyncio

        effective_budget = _effective_budget(budget_constraint, routing_type)
        tiers = _BUDGET_TIERS.get(effective_budget, _BUDGET_TIERS["cheap"])

        picked = await asyncio.to_thread(_catalog_models_for_budget, effective_budget)

        header = f"\n### Modèles recommandés (budget: {budget_constraint}"
        if budget_constraint == "auto":
            header += f" → {effective_budget}"
        header += f", tiers: {', '.join(tiers)})"
        lines.append(header)

        if not picked:
            lines.append("  (aucun modèle actif dans le catalogue pour ces tiers)")
        else:
            for m in picked[:5]:
                spec = f" — {m['speciality']}" if m["speciality"] else ""
                use = f" · {m['use']}" if m["use"] else ""
                lines.append(f"  🏆 **{m['id']}** ({m['tier']}, {m['cost']}){spec}{use}")

        return "\n".join(lines)
    except Exception as e:
        return f"❌ Erreur routage : {e}"


# ═══════════════════════════════════════════════════════
# Outil 15 — Matrice de routage coût/avantage (catalogue dynamique)
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def get_routing_matrix(
    task_type: str = "",
    include_live_cost: bool = True,
) -> str:
    """
    Expose la **matrice de routage coût/avantage** du moteur : quel LLM utiliser pour
    quel type de tâche, avec sa justification et son coût effectif.

    Source = table `routing_rules` du catalogue dynamique `models_registry.db` (règles
    curées task_type → modèle), enrichie du tarif live du catalogue (`models.cost_*`).
    Contrairement à `get_routing_recommendation` (analyse d'une requête précise), cet
    outil donne la **table de référence complète** partagée par les 3 outils (moteur,
    Antigravity IDE, Claude) pour déléguer/router à coût optimal. Lecture seule.

    Args:
        task_type: Filtre optionnel sur le type de tâche (sous-chaîne, ex: "code", "gratuit",
                   "raisonnement", "rapide"). Vide → toute la matrice.
        include_live_cost: Si vrai, ajoute le tarif catalogue actuel ($/M in·out) du modèle.
    """
    import asyncio

    def _run() -> str:
        from core.models_db import get_routing_rules, get_model_cost

        rules = get_routing_rules()
        if not rules:
            return "❌ Aucune règle de routage dans models_registry.db (table routing_rules vide)."

        if task_type.strip():
            tt = task_type.strip().lower()
            rules = [r for r in rules if tt in (r.get("task_type", "") or "").lower()]
            if not rules:
                return f"🔍 Aucune règle de routage ne correspond à « {task_type} »."

        lines = [
            f"🧭 **Matrice de routage** ({len(rules)} règle(s)"
            + (f", filtre « {task_type} »" if task_type.strip() else "")
            + ") — source `models_registry.db`\n"
        ]
        for r in rules:
            tt = r.get("task_type", "?")
            model = r.get("recommended_model", "?")
            provider = r.get("provider_id", "?")
            justif = (r.get("justification", "") or "").strip()
            eff_cost = r.get("effective_cost", "") or ""

            cost_str = eff_cost
            if include_live_cost:
                c = get_model_cost(model) or {}
                ci, co = c.get("cost_input_per_m"), c.get("cost_output_per_m")
                cur = c.get("currency", "USD")
                if ci is None and co is None:
                    live = "local/gratuit"
                else:
                    live = f"{ci or 0:.2f}/{co or 0:.2f} {cur}/M"
                cost_str = f"{eff_cost} (catalogue: {live})" if eff_cost else f"catalogue: {live}"

            lines.append(f"### `{tt}`")
            lines.append(f"  - **Modèle** : `{model}` ({provider})")
            lines.append(f"  - **Coût** : {cost_str}")
            if justif:
                lines.append(f"  - **Pourquoi** : {justif}")
            lines.append("")

        lines.append(
            "_Pour router une requête précise, utiliser `get_routing_recommendation`. "
            "Pour déléguer au moteur : endpoint OpenAI-compat `/v1/chat/completions`._"
        )
        return "\n".join(lines)

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        return f"❌ Erreur get_routing_matrix : {e}"


# ═══════════════════════════════════════════════════════
# Outil 8 — Validation de configuration (linter)
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def validate_config_format(file_path: str) -> str:
    """
    Valide localement la syntaxe d'un fichier de configuration (YAML, Jinja2, ESPHome).
    Détecte les erreurs de syntaxe YAML, les expressions Jinja2 mal formées,
    et exécute 'esphome config' sur les configurations ESPHome pour identifier
    les erreurs sémantiques de configuration hardware (DAC, I2C, GPIO) et de C++.
    
    Args:
        file_path: Chemin absolu ou relatif vers le fichier à valider.
    """
    from tools.linter import ConfigurationLinter
    
    linter = ConfigurationLinter()
    workspace_root = os.path.dirname(os.path.abspath(__file__))
    
    # Si le chemin est relatif, le résoudre par rapport à la racine du serveur
    if not os.path.isabs(file_path):
        resolved_path = os.path.abspath(os.path.join(workspace_root, file_path))
    else:
        resolved_path = os.path.abspath(file_path)
        
    # SEC-1 : Sécurité - Path Traversal
    # Vérifier que le chemin commence bien par workspace_root
    if not resolved_path.startswith(os.path.join(workspace_root, "")):
        return "❌ Erreur de sécurité : Accès refusé (Path Traversal détecté)."
        
    try:
        import asyncio
        # Exécuter la validation dans un thread séparé si elle contient des appels bloquants
        result = await asyncio.to_thread(linter.validate_file, resolved_path)
        
        if result["valid"]:
            return f"✅ CONFIGURATION VALIDE : {result['message']} (type: {result['type']})"
        else:
            return f"❌ CONFIGURATION INVALIDE ({result['type']}) :\n\n{result['error']}"
    except Exception as e:
        return f"❌ Erreur lors de la validation du fichier : {e}"


# ═══════════════════════════════════════════════════════
# Outil 9 — Routage cascade (Délégation au moteur)
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def delegate_complex_reasoning(
    task_description: str,
    model_tier: str = "deepseek-reasoner"
) -> str:
    """
    Délègue une tâche de développement, de debugging ou de refactoring complexe
    au tab5-engine local Tab5. Le moteur va planifier, exécuter et valider
    la résolution de la tâche en autonomie.
    
    Args:
        task_description: Description exhaustive de la tâche à accomplir (fichiers concernés, modifications souhaitées).
        model_tier: Modèle à utiliser pour l'agent planificateur (ex: "deepseek-reasoner", "deepseek-chat", "gemini").
    """
    try:
        logger.info(f"[DELEGATION] Lancement de la tâche déléguée : '{task_description[:100]}...'")
        final_state = await _run_engine_pipeline(
            task_description, session_id="mcp_delegated_session", planner_tier=model_tier
        )

        result_parts = [f"🏆 **Résolution de la tâche déléguée terminée** (planificateur: {model_tier})\n"]
        
        if final_state.history:
            last_result = final_state.history[-1].result_data
            result_parts.append(str(last_result))
        else:
            result_parts.append("Aucun résultat n'a été produit par le moteur.")
            
        return "\n".join(result_parts)
    except Exception as e:
        return f"❌ Erreur lors de la délégation au moteur : {e}"



# ═══════════════════════════════════════════════════════
# Outil 10 — Appel LLM direct (multi-provider, sans pipeline)
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def query_llm_direct(
    prompt: str,
    model: str = "auto",
    system_prompt: str = "",
    temperature: float = 0.0,
    max_tokens: int = 4096,
) -> str:
    """
    Appel direct à N'IMPORTE QUEL modèle du LLMGateway (bypass pipeline).
    Supporte tous les providers : DeepSeek, Gemini, Claude, LM Studio, Mistral, Grok, etc.

    Utilise 'auto' pour le routage automatique (recommandé pour économiser).
    Utilise un nom de modèle exact pour forcer un provider spécifique.

    Modèles disponibles (extrait) :
      - "auto"              : Routage dynamique (circuit breaker + fallback)
      - "deepseek-chat"     : $0.14/$0.28 par M tokens — ultra économique
      - "deepseek-reasoner" : R1, $0.55/$2.19 — Chain of Thought
      - "deepseek-v4-pro"   : $0.435/$0.87 — raisonnement avancé
      - "local"             : LM Studio RTX 5070Ti (0$, 50 tok/s, confidentiel)
      - "claude-sonnet-4-6" : Inclus Claude Pro, pair programming
      - "claude-opus-4-8"   : Inclus Claude Pro, architecture
      - "gemini-2.5-flash"  : Free Tier (0$, 80 tok/s, raisonnement)
      - "grok-4.3"          : xAI, $1.25/$2.50 par M tokens
      - "mistral-large-latest" : Free Tier Mistral, excellent en français

    Args:
        prompt: Le prompt utilisateur.
        model: Modèle cible (défaut: "auto").
        system_prompt: Prompt système optionnel.
        temperature: Température de génération (0.0 = déterministe).
        max_tokens: Nombre max de tokens en sortie (défaut: 4096).
    """
    gateway = get_gateway()

    # Résolution du modèle
    resolved_model = model
    if model == "auto":
        # Priorité : DeepSeek > Gemini > Local
        for candidate in ["deepseek-chat", "gemini-2.5-flash", "local"]:
            if candidate in gateway.providers:
                resolved_model = candidate
                break

    resolved_model, provider = _resolve_gateway_provider(gateway, resolved_model)

    if not provider:
        available = list(gateway.providers.keys())
        return (
            f"❌ Modèle '{model}' non trouvé dans le LLMGateway.\n"
            f"Providers disponibles ({len(available)}) : {', '.join(sorted(available)[:20])}..."
        )

    try:
        sys = system_prompt if system_prompt else "Tu es un assistant expert. Réponds en français."
        kwargs = {"temperature": temperature, "max_tokens": max_tokens}

        result = await provider.generate_async(sys, prompt, **kwargs)

        model_label = resolved_model if resolved_model != model else model
        tag = f" (résolu depuis '{model}')" if resolved_model != model else ""
        return f"{result}\n\n---\n📊 Modèle utilisé : **{model_label}**{tag}"

    except Exception as e:
        return f"❌ Erreur avec le modèle '{resolved_model}' : {e}"


# ═══════════════════════════════════════════════════════
# Outil 17 — Délégation automatique au gateway (routage coût/avantage, #T40 partie 2/2)
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def delegate_to_gateway(
    prompt: str,
    budget_constraint: str = "auto",
    system_prompt: str = "",
    temperature: float = 0.0,
    max_tokens: int = 4096,
) -> str:
    """
    Délègue une tâche au **gateway moteur** en choisissant AUTOMATIQUEMENT le modèle
    le moins cher éligible (Ollama-first → free → payant), au lieu de consommer les
    tokens cloud de l'IDE/Claude. Combine la décision de routage (matrice coût/avantage
    `get_routing_matrix`) et l'exécution en un seul appel.

    Concrètement : analyse la tâche (Router V7) → sélectionne le panier de tiers du
    catalogue dynamique selon le budget → exécute le premier modèle disponible dans le
    LLMGateway, avec **fallback en cascade** sur le candidat suivant en cas d'échec.

    À utiliser pour toutes les tâches « éligibles » (résumé, extraction, reformulation,
    parsing, Q/R simple, tâches HA) que le moteur peut traiter à moindre coût. Pour
    forcer un modèle précis, utiliser `query_llm_direct`.

    Args:
        prompt: Le prompt / la tâche à déléguer.
        budget_constraint: "auto" (choix selon le type de tâche, défaut), "free" (local/free
            uniquement), "cheap" (léger/moyen), "best" (pro/fort).
        system_prompt: Prompt système optionnel.
        temperature: Température de génération (0.0 = déterministe).
        max_tokens: Nombre max de tokens en sortie.
    """
    import asyncio

    try:
        gateway = get_gateway()
        router = get_router()

        # 1. Analyse de la tâche pour déterminer le type de routage.
        try:
            payload, _agent = await router.analyze_request(prompt)
            routing_type = (payload.metadata or {}).get("routing_type", "standard")
        except Exception:
            routing_type = "standard"

        # 2. Budget effectif + candidats du catalogue (ordonnés par cascade_priority).
        effective_budget = _effective_budget(budget_constraint, routing_type)
        candidates = await asyncio.to_thread(_catalog_models_for_budget, effective_budget)

        if not candidates:
            return (
                f"❌ Aucun modèle actif dans le catalogue pour le budget '{effective_budget}'. "
                f"Utiliser `query_llm_direct` avec un modèle explicite."
            )

        # 3. Exécution en cascade : premier candidat résolu dans le gateway qui répond.
        sys = system_prompt if system_prompt else "Tu es un assistant expert. Réponds en français."
        kwargs = {"temperature": temperature, "max_tokens": max_tokens}

        tried, last_error = [], None
        for cand in candidates:
            resolved, provider = _resolve_gateway_provider(gateway, cand["id"])
            if not provider:
                continue  # candidat du catalogue sans provider câblé → suivant
            tried.append(resolved)
            try:
                result = await provider.generate_async(sys, prompt, **kwargs)
            except Exception as e:
                last_error = f"{resolved}: {e}"
                continue  # fallback cascade sur le candidat suivant

            cost_note = "0$ (local/gratuit)" if cand["is_free"] else cand["cost"]
            fallback_note = ""
            if len(tried) > 1:
                fallback_note = f" (après échec de : {', '.join(tried[:-1])})"
            return (
                f"{result}\n\n---\n"
                f"📊 Délégué au gateway · modèle **{resolved}** "
                f"(tier {cand['tier']}, {cost_note}, budget {budget_constraint}"
                f"{' → ' + effective_budget if budget_constraint == 'auto' else ''}, "
                f"tâche '{routing_type}'){fallback_note}"
            )

        # Aucun candidat n'a abouti.
        if not tried:
            avail = ', '.join(sorted(gateway.providers.keys())[:15])
            return (
                f"❌ Aucun des {len(candidates)} modèles éligibles ({effective_budget}) "
                f"n'est câblé dans le LLMGateway. Providers dispo : {avail}…"
            )
        return f"❌ Tous les candidats éligibles ont échoué. Dernière erreur — {last_error}"

    except Exception as e:
        return f"❌ Erreur délégation gateway : {e}"


# ═══════════════════════════════════════════════════════
# Outil 11 — Liste complète des modèles disponibles
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def list_available_models(
    filter_term: str = "",
    show_status: bool = True,
) -> str:
    """
    Liste tous les modèles disponibles dans le LLMGateway V11 avec leur statut.
    Indique quels providers ont leurs clés API configurées et lesquels sont accessibles.

    Utile avant d'appeler query_llm_direct pour savoir exactement quels modèles
    sont disponibles dans ta session actuelle.

    Args:
        filter_term: Filtre sur le nom (optionnel). Ex: "deepseek", "claude", "gemini", "local".
        show_status: Afficher l'état des circuit breakers (défaut: True).
    """
    gateway = get_gateway()

    lines = [f"📋 **Modèles disponibles — LLMGateway V11** ({len(gateway.providers)} total)\n"]

    # Grouper par type de provider
    groups = {
        "DeepSeek": [],
        "Gemini": [],
        "Claude": [],
        "LM Studio / Ollama": [],
        "Mistral": [],
        "Grok (xAI)": [],
        "Zhipu AI (Z.ai)": [],
        "Autres": [],
    }

    for name in sorted(gateway.providers.keys()):
        if filter_term and filter_term.lower() not in name.lower():
            continue

        provider_type = type(gateway.providers[name]).__name__

        # État Circuit Breaker
        cb_icon = "🟢"
        if show_status:
            try:
                from core.llm.circuit_breaker import CircuitBreaker
                cb = CircuitBreaker.get_or_create(name)
                cb_icon = "🔴" if cb.is_open() else "🟢"
            except Exception:
                cb_icon = "⚪"

        entry = f"  {cb_icon} `{name}` ({provider_type})"

        # Classification
        nl = name.lower()
        if "deepseek" in nl:
            groups["DeepSeek"].append(entry)
        elif "gemini" in nl or "antigravity" in nl:
            groups["Gemini"].append(entry)
        elif "claude" in nl:
            groups["Claude"].append(entry)
        elif "local" in nl or "lmstudio" in nl or "deck" in nl or "ollama" in nl:
            groups["LM Studio / Ollama"].append(entry)
        elif "mistral" in nl or "codestral" in nl or "nemo" in nl:
            groups["Mistral"].append(entry)
        elif "grok" in nl or "xai" in nl:
            groups["Grok (xAI)"].append(entry)
        elif "zhipu" in nl or "z-ai" in nl or "glm" in nl:
            groups["Zhipu AI (Z.ai)"].append(entry)
        else:
            groups["Autres"].append(entry)

    for group_name, entries in groups.items():
        if entries:
            lines.append(f"\n### {group_name} ({len(entries)})")
            lines.extend(entries)

    if show_status:
        lines.append("\n> 🟢 Circuit fermé (disponible) | 🔴 Circuit ouvert (temporairement indisponible)")

    lines.append(f"\n💡 **Conseil** : Utilise `query_llm_direct(prompt, model='deepseek-chat')` pour un appel direct.")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# Outil 12 — Exécution directe de commande Home Assistant
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def execute_ha_action(
    entity_id: str,
    service: str,
    service_data: str = "{}",
) -> str:
    """
    Exécute une action (service) directement sur Home Assistant via l'API REST.

    Contourne le pipeline du moteur pour les commandes domotiques simples et déterministes.
    Idéal pour allumer/éteindre une lumière, régler un thermostat, déclencher une scène.

    Exemples d'appel :
        execute_ha_action("light.salon", "light.turn_on", '{"brightness": 200}')
        execute_ha_action("climate.chambre", "climate.set_temperature", '{"temperature": 21}')
        execute_ha_action("scene.cinema", "scene.turn_on")
        execute_ha_action("switch.prise_bureau", "switch.toggle")

    Args:
        entity_id: Identifiant complet de l'entité HA (ex: "light.salon_principal").
        service: Service HA à appeler (ex: "light.turn_on", "climate.set_temperature").
        service_data: JSON string des données du service (optionnel). Défaut: "{}".
    """
    import requests
    import json as _json

    ha_url = os.environ.get("HA_URL", os.environ.get("HASS_URL", "http://${HA_HOST:-192.168.1.x}:8123"))
    ha_token = os.environ.get("HA_TOKEN", os.environ.get("HASS_TOKEN", ""))

    if not ha_token:
        return "❌ Variable HA_TOKEN (ou HASS_TOKEN) non configurée dans .env."

    # Parser le domaine depuis le service (ex: "light.turn_on" → domain="light", svc="turn_on")
    if "." in service:
        domain, svc_name = service.split(".", 1)
    else:
        # Déduire le domaine depuis entity_id
        domain = entity_id.split(".")[0] if "." in entity_id else service
        svc_name = service

    # [P0-1.6] Valider les identifiants avant de les injecter dans l'URL de l'API HA
    # (anti-traversée de chemin / injection de segments via entrée LLM non maîtrisée).
    from core.validation import (
        is_valid_ha_entity_id, is_valid_ha_domain, is_valid_ha_service_name,
        validate_service_data,
    )
    if not is_valid_ha_entity_id(entity_id):
        return f"❌ entity_id invalide : {entity_id!r} (attendu : domaine.objet, ex: light.salon)."
    if not is_valid_ha_domain(domain):
        return f"❌ domaine HA invalide : {domain!r}."
    if not is_valid_ha_service_name(svc_name):
        return f"❌ service HA invalide : {svc_name!r}."

    # Parser service_data
    try:
        svc_data = _json.loads(service_data) if service_data.strip() != "{}" else {}
    except _json.JSONDecodeError:
        return f"❌ service_data invalide (JSON attendu) : {service_data}"

    # Valider service_data contre les injections de templates Jinja2 (SSTI) et clés invalides
    try:
        validate_service_data(svc_data)
    except ValueError as val_err:
        return f"❌ service_data invalide : {str(val_err)}"

    # Ajouter entity_id dans les données si pas déjà présent
    if "entity_id" not in svc_data:
        svc_data["entity_id"] = entity_id

    endpoint = f"{ha_url}/api/services/{domain}/{svc_name}"

    try:
        resp = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {ha_token}",
                "Content-Type": "application/json",
            },
            json=svc_data,
            timeout=10,
        )

        if resp.status_code in (200, 201):
            # HA retourne une liste d'états modifiés
            try:
                changed = resp.json()
                changed_ids = [e.get("entity_id", "?") for e in changed] if isinstance(changed, list) else []
                changed_str = ", ".join(changed_ids) if changed_ids else entity_id
            except Exception:
                changed_str = entity_id

            return (
                f"✅ **Commande exécutée avec succès**\n"
                f"  - Service : `{domain}.{svc_name}`\n"
                f"  - Entité : `{entity_id}`\n"
                f"  - Données : `{svc_data}`\n"
                f"  - Entités modifiées : `{changed_str}`"
            )
        else:
            return (
                f"❌ Erreur HA API ({resp.status_code}) :\n"
                f"  Endpoint : {endpoint}\n"
                f"  Réponse : {resp.text[:300]}"
            )

    except requests.exceptions.ConnectionError:
        return f"❌ Impossible de contacter Home Assistant à {ha_url}. Vérifier le réseau ou l'URL."
    except Exception as e:
        return f"❌ Erreur lors de l'appel HA : {e}"


# ═══════════════════════════════════════════════════════
# Outil 13 — Lecture relationnelle du runtime (moteur_runtime.db)
# ═══════════════════════════════════════════════════════

# Préfixes SQL autorisés (lecture seule). Tout le reste est rejeté en amont,
# en plus du garde-fou PRAGMA query_only=ON au niveau de la connexion SQLite.
_RUNTIME_READ_PREFIXES = ("select", "with")


@mcp.tool()
async def query_runtime(
    sql: str = "",
    limit: int = 100,
) -> str:
    """
    Exécute une requête SQL **en lecture seule** sur la base relationnelle unifiée du
    moteur (`moteur_runtime.db`) : sessions, usage de tokens, scores Elo, décisions de
    routage, tâches DAG, étapes ReAct, workers Swarm, etc.

    Outil de lecture uniforme partagé par les 3 outils (moteur, Antigravity IDE, Claude)
    pour interroger l'état réel du moteur sans dupliquer d'accès BD. Double garde-fou :
    seules les requêtes `SELECT`/`WITH` mono-instruction passent, et la connexion est
    ouverte en `PRAGMA query_only=ON` (toute écriture échoue au niveau SQLite).

    Args:
        sql: Requête SELECT/WITH. Laissé vide → liste les tables et leur nombre de lignes
             (découverte du schéma).
        limit: Nombre maximum de lignes retournées (défaut 100, plafonné à 1000).

    Exemples :
        query_runtime()  # découverte des tables
        query_runtime("SELECT model, SUM(total_tokens) t FROM token_usage GROUP BY model ORDER BY t DESC")
        query_runtime("SELECT model_name, domain, elo_score FROM model_elo_scores ORDER BY elo_score DESC", 20)
    """
    import asyncio
    from core.runtime_db import get_connection, get_db_path

    limit = max(1, min(int(limit or 100), 1000))

    def _run() -> str:
        conn = get_connection()
        try:
            # Garde-fou fort : lecture seule au niveau de la connexion SQLite.
            conn.execute("PRAGMA query_only=ON")

            # Mode découverte : aucune requête → inventaire des tables + comptes.
            if not sql or not sql.strip():
                tables = [
                    r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                    ).fetchall()
                ]
                lines = [f"🗃️ **{len(tables)} table(s)** dans `{os.path.basename(get_db_path())}` :\n"]
                for t in tables:
                    try:
                        n = conn.execute(f"SELECT COUNT(*) FROM \"{t}\"").fetchone()[0]
                    except Exception:
                        n = "?"
                    lines.append(f"  - `{t}` — {n} ligne(s)")
                lines.append("\nUtiliser `query_runtime(\"SELECT ... FROM <table>\")` pour interroger.")
                return "\n".join(lines)

            clean = sql.strip().rstrip(";").strip()

            # Garde-fou 1 : une seule instruction (pas de SQL empilé).
            if ";" in clean:
                return "❌ Une seule instruction SQL autorisée (point-virgule interne détecté)."

            # Garde-fou 2 : préfixe lecture seule.
            if not clean.lower().startswith(_RUNTIME_READ_PREFIXES):
                return "❌ Lecture seule : seules les requêtes `SELECT` ou `WITH` sont autorisées."

            cursor = conn.execute(clean)
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(limit)
            # Détecte une troncature : si on a atteint le plafond, reste-t-il une ligne ?
            truncated = len(rows) == limit and cursor.fetchone() is not None

            if not rows:
                return "📭 Aucune ligne retournée."

            # Formatage en tableau Markdown compact.
            out = [f"📊 **{len(rows)} ligne(s)**" + (f" (tronqué à {limit})" if truncated else "") + " :\n"]
            out.append("| " + " | ".join(cols) + " |")
            out.append("| " + " | ".join("---" for _ in cols) + " |")
            for row in rows:
                cells = []
                for v in row:
                    s = "" if v is None else str(v)
                    if len(s) > 80:
                        s = s[:80] + "…"
                    cells.append(s.replace("|", "\\|").replace("\n", " "))
                out.append("| " + " | ".join(cells) + " |")
            return "\n".join(out)
        finally:
            conn.close()

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        return f"❌ Erreur query_runtime : {e}"


# ═══════════════════════════════════════════════════════
# Outil 14 — Recherche dans la mémoire active (memory.db)
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def search_memory(
    query: str,
    scope: str = "all",
    limit: int = 10,
) -> str:
    """
    Recherche **plein-texte** dans la mémoire active du moteur (`memory.db`) : faits
    vérifiés / leçons apprises, épisodes (résumés de sessions) et graphe de connaissances.

    Complète `rag_search` (sémantique vectoriel ChromaDB) par une recherche lexicale
    rapide (FTS5/LIKE) sur la base relationnelle. Lecture seule. Partagé par les 3 outils.

    Args:
        query: Termes à rechercher (ex: "GPIO ESP32-P4", "cascade Ollama", "zigbee migration").
        scope: Périmètre — "facts", "episodes", "graph" ou "all" (défaut).
        limit: Nombre maximum de résultats par périmètre (défaut 10).
    """
    import asyncio

    if not query or not query.strip():
        return "❌ Requête vide."

    scope = (scope or "all").strip().lower()
    if scope not in ("facts", "episodes", "graph", "all"):
        return f"❌ scope invalide : {scope!r} (attendu : facts, episodes, graph, all)."

    limit = max(1, min(int(limit or 10), 50))

    def _run() -> str:
        from memory.memory_db import MemoryDB

        db = MemoryDB.get_instance()
        lines = []

        if scope in ("facts", "all"):
            facts = db.search_facts(query, limit=limit)
            lines.append(f"### 📌 Faits/leçons ({len(facts)})")
            for f in facts:
                cat = f.get("category", "?")
                title = f.get("title", "(sans titre)")
                content = (f.get("content", "") or "").strip().replace("\n", " ")
                if len(content) > 240:
                    content = content[:240] + " […]"
                src = f.get("source_file", "")
                lines.append(f"- **[{cat}] {title}**" + (f" — `{src}`" if src else ""))
                if content:
                    lines.append(f"  {content}")
            if not facts:
                lines.append("  (aucun)")

        if scope in ("episodes", "all"):
            eps = db.search_episodes(query, limit=limit)
            lines.append(f"\n### 🗓️ Épisodes ({len(eps)})")
            for e in eps:
                date = e.get("session_date", "?")
                summary = (e.get("summary", "") or "").strip().replace("\n", " ")
                if len(summary) > 240:
                    summary = summary[:240] + " […]"
                folder = e.get("session_folder", "")
                lines.append(f"- **{date}**" + (f" — `{folder}`" if folder else ""))
                if summary:
                    lines.append(f"  {summary}")
            if not eps:
                lines.append("  (aucun)")

        if scope in ("graph", "all"):
            graph = db.search_graph(query, limit=limit)
            entities = graph.get("entities", [])
            relations = graph.get("relations", [])
            lines.append(f"\n### 🕸️ Graphe ({len(entities)} entité(s), {len(relations)} relation(s))")
            for ent in entities:
                obs = ent.get("observations", [])
                obs_str = "; ".join(obs[:3]) if isinstance(obs, list) else str(obs)
                if len(obs_str) > 240:
                    obs_str = obs_str[:240] + " […]"
                lines.append(f"- **{ent.get('name')}** ({ent.get('entity_type')})")
                if obs_str:
                    lines.append(f"  {obs_str}")
            for rel in relations:
                lines.append(
                    f"  ↔ `{rel.get('from_entity')}` —{rel.get('relation_type')}→ `{rel.get('to_entity')}`"
                )
            if not entities and not relations:
                lines.append("  (aucun)")

        header = f"🧠 **Recherche mémoire** « {query} » (scope: {scope})\n"
        return header + "\n".join(lines)

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        return f"❌ Erreur search_memory : {e}"


if __name__ == "__main__":
    # Lancement du serveur MCP
    mcp.run()
