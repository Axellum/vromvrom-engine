"""
core/mcp_tools/memory.py - Outils MCP memoire/RAG (#T124).

6 outils : get_models_catalog, rag_search, query_token_usage,
list_available_models, query_runtime, search_memory. Extrait de
l'ex-mcp_server.py monolithique.
"""
import logging
import os

from core.mcp_app import mcp
from core.mcp_tools.orchestrator import get_gateway

logger = logging.getLogger("mcp_server.memory")


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
                # [T122] borne l'appel réseau Vertex — sinon freeze infini si le service ne répond pas.
                response = await asyncio.wait_for(asyncio.to_thread(client.search, request), timeout=15.0)

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
            # [T122] borne l'appel ChromaDB — sinon freeze infini si le service ne répond pas.
            results = await asyncio.wait_for(asyncio.to_thread(store.query_similar, query, top_n), timeout=15.0)
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
    except asyncio.TimeoutError:
        return "❌ Timeout (15s) lors de la recherche RAG — le service (ChromaDB/Vertex) ne répond pas."
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
