/* ============================================================
   API.JS — Fonctions fetch centralisées pour le HMI V2
   Toutes les communications avec le backend FastAPI
   ============================================================ */

const API_BASE = `${window.location.origin}/api`;

/**
 * Wrapper fetch générique avec gestion d'erreur
 */
async function apiFetch(path, options = {}) {
    try {
        const res = await fetch(`${API_BASE}${path}`, {
            headers: { "Content-Type": "application/json", ...options.headers },
            ...options
        });
        if (!res.ok) {
            const errData = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(errData.detail || `Erreur HTTP ${res.status}`);
        }
        return await res.json();
    } catch (err) {
        console.error(`[API] Erreur sur ${path}:`, err);
        throw err;
    }
}

/* ─── Tokens & Consommation ─── */
async function fetchTokens() {
    return apiFetch("/tokens");
}

/* ─── Configuration des tiers ─── */
async function fetchConfig() {
    return apiFetch("/config");
}

async function saveConfig(config) {
    return apiFetch("/config", {
        method: "POST",
        body: JSON.stringify(config)
    });
}

/* ─── Pricing / Stratégies ─── */
async function fetchPricing() {
    return apiFetch("/pricing");
}

async function savePricing(pricingData) {
    return apiFetch("/pricing", {
        method: "POST",
        body: JSON.stringify(pricingData)
    });
}

async function autoUpdatePricing() {
    return apiFetch("/pricing/auto-update", { method: "POST" });
}

/* ─── APIs Status (clés, providers) ─── */
async function fetchApisStatus() {
    return apiFetch("/apis-status");
}

/* ─── Quotas glissants (legacy token_tracker) ─── */
async function fetchQuotasSliding() {
    return apiFetch("/quotas/sliding");
}

/* ─── Models Registry BDD ─── */
async function fetchModelsDB(provider = null) {
    const path = provider ? `/models?provider=${provider}` : "/models";
    return apiFetch(path);
}

async function fetchModelsStats() {
    return apiFetch("/models/stats");
}

async function fetchModelDetail(id) {
    return apiFetch(`/models/${encodeURIComponent(id)}`);
}

async function fetchProviders() {
    return apiFetch("/providers");
}

async function fetchApiKeys() {
    return apiFetch("/keys");
}

async function fetchAccessMap() {
    return apiFetch("/access-map");
}

async function fetchModelAccess(modelId) {
    return apiFetch(`/access-map/${encodeURIComponent(modelId)}`);
}

/* ─── Quotas temps réel (BDD quota_realtime) ─── */
async function fetchQuotasRT() {
    return apiFetch("/quotas");
}

async function refreshQuotasRT(includeClaude = false, forceClaude = false) {
    let path = "/quotas/refresh";
    const params = [];
    if (includeClaude) params.push("include_claude=true");
    if (forceClaude) params.push("force_claude=true");
    if (params.length) path += "?" + params.join("&");
    return apiFetch(path, { method: "POST" });
}

/* ─── Statut du moteur ─── */
async function fetchStatus() {
    return apiFetch("/status");
}

/* ─── Exécution du moteur ─── */
async function runEngine(objective) {
    return apiFetch("/run", {
        method: "POST",
        body: JSON.stringify({ objective })
    });
}

/* ─── Billing Scraper ─── */
async function syncBilling() {
    return apiFetch("/billing/sync", { method: "POST" });
}

async function launchChromeDebug() {
    return apiFetch("/billing/launch-chrome", { method: "POST" });
}

/* ─── Agents (nouveaux endpoints) ─── */
async function fetchAgents() {
    try {
        return await apiFetch("/agents");
    } catch {
        // Fallback si le endpoint n'existe pas encore
        return { agents: [] };
    }
}

async function saveAgentConfig(agentName, config) {
    try {
        return await apiFetch(`/agents/${agentName}/config`, {
            method: "POST",
            body: JSON.stringify(config)
        });
    } catch {
        return { status: "endpoint_not_ready" };
    }
}

/* ─── Workflows (nouveau endpoint) ─── */
async function fetchWorkflows() {
    try {
        return await apiFetch("/workflows");
    } catch {
        return { workflows: [] };
    }
}

async function saveWorkflow(workflowData) {
    try {
        return await apiFetch("/workflows", {
            method: "POST",
            body: JSON.stringify(workflowData)
        });
    } catch {
        return { status: "endpoint_not_ready" };
    }
}

async function fetchWorkflowsList() {
    try {
        return await apiFetch("/workflows/list");
    } catch {
        return { workflows: ["Default"] };
    }
}

async function loadWorkflowByName(name) {
    return apiFetch(`/workflows/load/${name}`);
}

async function saveWorkflowByName(name, workflowData) {
    return apiFetch(`/workflows/save/${name}`, {
        method: "POST",
        body: JSON.stringify(workflowData)
    });
}

async function deleteWorkflow(name) {
    return apiFetch(`/workflows/${name}`, {
        method: "DELETE"
    });
}

/* ─── Circuit Breakers ─── */
async function fetchCircuitBreakers() {
    try {
        return await apiFetch("/circuit-breakers");
    } catch {
        return {};
    }
}

/* ─── Détection des modèles disponibles ─── */
async function detectModels() {
    try {
        return await apiFetch("/models/detect");
    } catch {
        return { models: [] };
    }
}

/* ─── Collecteur CLI (Antigravity IDE + Claude Code) ─── */
async function collectCliTokens() {
    return apiFetch("/collect-cli-tokens", { method: "POST" });
}

/* ─── Context Loader (3-Layers) ─── */
async function fetchContextStatus() {
    return apiFetch("/context-status");
}

async function reloadContext() {
    return apiFetch("/context-reload", { method: "POST" });
}

async function ingestHAEntities() {
    return apiFetch("/context-ha-ingest", { method: "POST" });
}

/* ─── Conversations IDE (BDD SQLite) ─── */
async function fetchIdeConversations(limit = 200, source = null) {
    let path = `/ide-conversations?limit=${limit}`;
    if (source) path += `&source=${source}`;
    return apiFetch(path);
}

async function fetchIdeConversationsStats() {
    return apiFetch("/ide-conversations/stats");
}

/* ─── Historique Quotas & Billing (BDD SQLite) ─── */
async function fetchQuotasHistory(hours = 24, channel = null, metric = null) {
    let path = `/quotas/history?hours=${hours}`;
    if (channel) path += `&channel=${channel}`;
    if (metric) path += `&metric=${metric}`;
    return apiFetch(path);
}

async function fetchBillingHistory(days = 30, provider = null) {
    let path = `/billing/history?days=${days}`;
    if (provider) path += `&provider=${provider}`;
    return apiFetch(path);
}

/* ─── Métriques Avancées (routes api/routes/metrics.py) ─── */

/**
 * Récupère les métriques de télémétrie du moteur (latences, erreurs, etc.)
 */
async function fetchMetricsTelemetry() {
    try {
        return await apiFetch("/metrics/telemetry");
    } catch {
        return null;
    }
}

/**
 * Récupère les scores Elo des modèles (routing_metrics.db)
 * Retourne la liste des modèles classés par performance
 */
async function fetchMetricsElo() {
    try {
        return await apiFetch("/metrics/elo");
    } catch {
        return { scores: [] };
    }
}

/**
 * Récupère les statistiques de routage (routing_metrics.db)
 * Nombre de décisions, fast_path, catégories dominantes
 */
async function fetchMetricsRouting() {
    try {
        return await apiFetch("/metrics/routing");
    } catch {
        return null;
    }
}

/**
 * Récupère les métriques d'agents (durée, succès, erreurs par agent)
 */
async function fetchMetricsAgents() {
    try {
        return await apiFetch("/metrics/agents");
    } catch {
        return null;
    }
}

/* ─── Sessions Historique (BDD session_history.db) ─── */

/**
 * Récupère l'historique des sessions du moteur (table sessions)
 */
async function fetchSessionsHistory(limit = 50) {
    try {
        return await apiFetch(`/sessions?limit=${limit}`);
    } catch {
        return { sessions: [] };
    }
}

/**
 * Récupère les statistiques globales des sessions (coût total, durée moyenne, etc.)
 */
async function fetchSessionsStats() {
    try {
        return await apiFetch("/sessions/stats");
    } catch {
        return null;
    }
}

/**
 * Récupère le détail d'une session spécifique
 */
async function fetchSessionDetail(sessionId) {
    return apiFetch(`/sessions/${encodeURIComponent(sessionId)}`);
}
