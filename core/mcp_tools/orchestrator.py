"""
core/mcp_tools/orchestrator.py — Outils MCP LLM/routing (#T124).

8 outils : run_tab5_agent, query_deepseek, get_engine_status,
get_routing_recommendation, get_routing_matrix, delegate_complex_reasoning,
query_llm_direct, delegate_to_gateway. Extrait de l'ex-mcp_server.py
monolithique. Tous les outils qui touchent LLMGateway/FallbackProvider
(et donc le registre CircuitBreaker, en memoire pure) restent groupes ici,
dans le meme process que le reste du serveur MCP - aucune fragmentation
d'etat entre process.
"""
import logging

from agents.antigravity_agent import AntigravityAgent
from agents.executor import ExecutorAgent
from agents.planner import PlannerAgent
from core.engine import Engine
from core.llm_gateway import LLMGateway
from core.mcp_app import mcp
from core.router import Router
from memory.context_manager import ContextManager
from tools.registry_setup import register_base_tools, register_extended_tools
from tools.tool_registry import ToolRegistry

logger = logging.getLogger("mcp_server.orchestrator")


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

    # [#T213] Enregistrement factorisé (partagé avec core/factory.py et
    # services/pipeline_service.py) : le serveur MCP gagne Git Safety et les
    # familles étendues Workspace/Cloud/Imagen, absentes ici avant (divergence
    # silencieuse des 3 sites de registry).
    register_base_tools(registry)
    register_extended_tools(registry)

    # [P0-audit-2026-07-09] config.json racine (source unique, cf. core/factory.py)
    # au lieu d'un core/mcp_tools/config.json qui n'a jamais existé sur disque : ce
    # fallback codé en dur tournait en permanence déconnecté de la config réelle
    # (modifier la config via l'IHM ne changeait pas les modèles utilisés ici).
    from core.llm_gateway import load_config
    config = load_config()

    from agents.ha_agent import HACommandAgent

    executor = ExecutorAgent(llm_gateway=gateway, tool_registry=registry, provider_name=config.get("executor_model", "automatique"))
    planner = PlannerAgent(llm_gateway=gateway, provider_name=config.get("planner_model", "fort"))
    antigravity_agent = AntigravityAgent(llm_gateway=gateway, provider_name=config.get("antigravity_model", "fort"))
    ha_agent = HACommandAgent(llm_gateway=gateway, tool_registry=registry, provider_name=config.get("executor_model", "automatique"))

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
        # [P0-audit-2026-07-09] Ancien hasattr(agent, "_provider_name") (avec
        # underscore) : PlannerAgent stocke l'attribut SANS underscore
        # (self.provider_name, agents/planner.py) -> ce hasattr était toujours
        # faux, l'override de modèle demandé ici n'avait jamais d'effet.
        for agent in engine.agents.values():
            if agent.name == "planner" and hasattr(agent, "provider_name"):
                agent.provider_name = planner_tier
                logger.info(f"[DELEGATION] Modèle du planificateur configuré sur : {planner_tier}")

    initial_payload, starting_agent = await router.analyze_request(task, session_id=session_id)
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

    Modèles disponibles et tarification dynamique (cf. core/pricing.py) :
      - deepseek-chat (alias deepseek-v4-flash) : Tarifs dynamiques avec pic/hors-pic
      - deepseek-reasoner (R1) : Tarifs dynamiques avec pic/hors-pic (Chain of Thought)
      - deepseek-v4-pro : Tarifs dynamiques avec pic/hors-pic

    Les coûts d'inférence en heure de pic sont doublés à partir du 15 juillet 2026.
    Référez-vous à core/pricing.py pour le calcul exact des coûts en temps réel.

    Args:
        prompt: Le prompt pour DeepSeek.
        model: Modèle DeepSeek (défaut: deepseek-chat).
        system_prompt: Prompt système optionnel.
        temperature: Température de génération (défaut: 0.7).
    """
    gateway = get_gateway()

    try:
        # Chercher la clé du provider DeepSeek correspondant
        resolved = model if model in gateway.providers else None
        if not resolved:
            # Essayer les alias courants
            alias_map = {
                "deepseek-chat": "deepseek-chat",
                "deepseek-reasoner": "deepseek-reasoner",
                "deepseek-v4-flash": "deepseek-chat",
                "deepseek-v4-pro": "deepseek-v4-pro",
                "r1": "deepseek-reasoner",
            }
            candidate = alias_map.get(model.lower(), model)
            if candidate in gateway.providers:
                resolved = candidate

        if not resolved:
            available = [k for k in gateway.providers if "deepseek" in k.lower()]
            return f"❌ Modèle '{model}' non trouvé. Providers DeepSeek disponibles : {available}"

        # [#T212] get_provider() (et non gateway.providers[...]) : l'appel passe
        # par le wrapping Circuit Breaker commun au lieu de le contourner.
        provider = gateway.get_provider(resolved)

        # generate_async() attend (system_prompt, user_prompt, **kwargs)
        # [#T123] Cohérence post-migration D5 : les autres outils utilisent
        # generate_async() (httpx natif pour les providers OpenAI-compat), pas
        # asyncio.to_thread(generate, ...) qui force un thread bloquant.
        sys_prompt = system_prompt if system_prompt else "Tu es un assistant expert."
        result = await provider.generate_async(
            sys_prompt, prompt, temperature=temperature
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
            status_parts.append("\n### KeyPool Gemini")
            status_parts.append(f"  🔑 Clés Free Tier : {pool_stats.get('available', '?')}/{pool_stats.get('total', '?')} disponibles")
            status_parts.append(f"  💳 Clé payante : {'✅' if pool_stats.get('has_paid') else '❌'}")
        except Exception:
            pass  # KeyPool optionnel

        # Providers configurés
        status_parts.append("\n### Providers Configurés")
        for name, provider in gateway.providers.items():
            provider_type = type(provider).__name__
            status_parts.append(f"  - **{name}** → {provider_type}")

        return "\n".join(status_parts)
    except Exception as e:
        return f"❌ Erreur status : {e}"

# ═══════════════════════════════════════════════════════
# Helpers de routage partagés (recommandation + délégation, #T40)
# ═══════════════════════════════════════════════════════

# Paniers de modèles du catalogue dynamique par contrainte budgétaire.
# [#T246] Deux axes distincts du catalogue, à ne pas mélanger :
#   - la colonne `tier` (économie : local/free/subscription/paid) ;
#   - la colonne `routing_tier` (capacité : leger/moyen/fort).
# Le panier "free" filtre sur `tier` : aucun modèle n'a routing_tier 'free'/'local',
# le filtrer sur routing_tier le vidait structurellement (les outils MCP répondaient
# « catalogue free vide » alors qu'il est plein).
_BUDGET_BASKETS = {
    "free":  {"catalog_tiers": ["local", "free"]},
    "cheap": {"routing_tiers": ["leger", "moyen"]},
    "best":  {"routing_tiers": ["fort"]},
}


def _describe_basket(effective_budget: str) -> str:
    """Description lisible d'un panier pour l'en-tête de recommandation."""
    basket = _BUDGET_BASKETS.get(effective_budget, _BUDGET_BASKETS["cheap"])
    if "catalog_tiers" in basket:
        return "tier catalogue : " + ", ".join(basket["catalog_tiers"])
    return "routing_tier : " + ", ".join(basket["routing_tiers"])


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
    [#T246] Le panier "free" filtre sur la colonne `tier` (économie), les paniers
    "cheap"/"best" sur `routing_tier` (capacité) — voir `_BUDGET_BASKETS`.
    """
    from core.models_db import get_model_cost, get_models_for_catalog_tiers, get_models_for_tier
    basket = _BUDGET_BASKETS.get(effective_budget, _BUDGET_BASKETS["cheap"])
    if "catalog_tiers" in basket:
        modeles = get_models_for_catalog_tiers(basket["catalog_tiers"])
    else:
        modeles = []
        for tier in basket["routing_tiers"]:
            modeles.extend(get_models_for_tier(tier))
    # get_models_for_tier trie déjà par cascade_priority ; en concaténant plusieurs
    # tiers (ou en cas de doublon), on réordonne et déduplique par sécurité.
    modeles.sort(key=lambda m: m.get("cascade_priority") or 999)
    picked, seen = [], set()
    for m in modeles:
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

    [#T212] Le provider retourné vient de gateway.get_provider() (cascade d'un
    seul élément) : l'appel passe par le wrapping Circuit Breaker commun au
    lieu de le contourner via un accès brut à gateway.providers.

    Retourne (clé_résolue, provider) ou (model_id, None) si introuvable.
    """
    resolved_key = None
    if model_id in gateway.providers:
        resolved_key = model_id
    else:
        for key in gateway.providers:
            if model_id.lower() in key.lower():
                resolved_key = key
                break
    if resolved_key is None:
        return model_id, None
    return resolved_key, gateway.get_provider(resolved_key)



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
        budget_constraint: Contrainte budgétaire (défaut: "auto"). Options: "free" (tier catalogue local/free), "cheap" (routing_tier leger/moyen), "best" (routing_tier fort), "auto" (choix selon le type de tâche).
    """
    try:
        router = get_router()

        # Analyser la requête
        payload, agent_name = await router.analyze_request(task_description)

        metadata = payload.metadata or {}
        routing_type = metadata.get("routing_type", "standard")

        # Construire la recommandation
        lines = [f"🎯 **Recommandation de routage** pour :\n> \"{task_description[:100]}...\"\n"]

        lines.append("### Analyse")
        lines.append(f"  - **Type de tâche** : {routing_type}")
        lines.append(f"  - **Agent recommandé** : {agent_name}")

        if metadata.get("multi_intent"):
            lines.append(f"  - **Multi-intent** : {len(metadata.get('sub_intents', []))} sous-tâches détectées")

        # Sélection des modèles dans le catalogue dynamique selon le budget → tiers
        # (logique partagée avec delegate_to_gateway, #T40).
        import asyncio

        effective_budget = _effective_budget(budget_constraint, routing_type)

        picked = await asyncio.to_thread(_catalog_models_for_budget, effective_budget)

        header = f"\n### Modèles recommandés (budget: {budget_constraint}"
        if budget_constraint == "auto":
            header += f" → {effective_budget}"
        header += f", {_describe_basket(effective_budget)})"
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
        from core.models_db import get_model_cost, get_routing_rules

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
        budget_constraint: "auto" (choix selon le type de tâche, défaut), "free" (tier catalogue
            local/free uniquement), "cheap" (routing_tier léger/moyen), "best" (routing_tier fort).
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
