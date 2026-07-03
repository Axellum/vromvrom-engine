/* ============================================================
   APP.JS — Point d'entrée, routage onglets, initialisation
   ============================================================ */

const TABS = [
    { id: 'chat',         label: '💬 Chat',                render: renderChat },
    { id: 'dashboard',    label: '📊 Dashboard',           render: renderDashboard },
    { id: 'metrics',      label: '⚡ Métriques & Elo',     render: renderMetricsTab },
    { id: 'execution',    label: '▶️ Exécution',            render: renderExecution },
    { id: 'agents',       label: '🤖 Agents & Workflows',  render: renderAgents },
    { id: 'models-group', label: '⚙️ Modèles & Tarifs',    render: renderModelsGroup },
    { id: 'supervision',  label: '🔄 Supervision 24/7',     render: renderSupervision },
    { id: 'data-group',   label: '📂 Données & Contexte',  render: renderDataGroup },
    { id: 'tokens',       label: '💰 Tokens & Budget',      render: renderTokens },
    { id: 'apis-quotas',  label: '📡 APIs & Quotas',        render: renderApisQuotas },
];

let activeTab = 'chat';

/**
 * Initialisation de l'application au chargement de la page
 */
document.addEventListener("DOMContentLoaded", async () => {
    // Initialisation dynamique du catalogue de modèles (Zone 6)
    if (typeof initDynamicModelCatalog === "function") {
        await initDynamicModelCatalog().catch(err => console.error(err));
    }

    // Rendu des boutons de navigation
    renderNavTabs();

    // Activer l'onglet par défaut (depuis le hash de l'URL ou 'chat')
    const hash = window.location.hash.substring(1);
    const tabExists = TABS.some(t => t.id === hash);
    if (tabExists) {
        switchTab(hash);
    } else {
        switchTab('chat');
    }

    // Écouter les changements de hash (navigation historique du navigateur)
    window.addEventListener('hashchange', () => {
        const h = window.location.hash.substring(1);
        const exists = TABS.some(t => t.id === h);
        if (exists && activeTab !== h) {
            switchTab(h);
        }
    });

    // Connexion SSE
    setupSSE();

    // Rafraîchissement périodique des données en temps réel
    setInterval(refreshRealTimeData, 5000);
});

/**
 * Rendu des boutons de navigation
 */
function renderNavTabs() {
    const nav = document.getElementById('nav-tabs');
    if (!nav) return;

    nav.innerHTML = TABS.map(tab => `
        <button class="tab-btn ${tab.id === activeTab ? 'active' : ''}"
                data-tab="${tab.id}"
                onclick="switchTab('${tab.id}')">
            ${tab.label}
        </button>
    `).join('');
}

/**
 * Change l'onglet actif et rend son contenu
 */
function switchTab(tabId) {
    activeTab = tabId;

    // Mettre à jour les boutons de nav
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-tab') === tabId);
    });

    // Rendre le contenu
    const container = document.getElementById('tab-content');
    if (!container) return;

    const tab = TABS.find(t => t.id === tabId);
    if (tab && tab.render) {
        container.innerHTML = '';
        const pane = document.createElement('div');
        pane.className = 'tab-pane active';
        container.appendChild(pane);
        tab.render(pane);
    }

    // Mettre à jour le hash d'URL sans provoquer de scroll ni de rechargement
    if (window.location.hash.substring(1) !== tabId) {
        window.history.replaceState(null, null, `#${tabId}`);
    }
}

/**
 * Rafraîchissement périodique des données temps réel (toutes les 5s)
 */
async function refreshRealTimeData() {
    // Si le flux SSE est actif et connecté, on évite le polling REST toutes les 5s
    if (sseSource && sseSource.readyState === EventSource.OPEN) {
        return;
    }
    try {
        // Mise à jour des tokens (KPIs) — utiliser combined_total si dispo
        const tokens = await fetchTokens();
        if (tokens && tokens.total) {
            const kpiTokens = document.getElementById('kpi-tokens');
            const kpiCost = document.getElementById('kpi-cost');
            if (kpiTokens) {
                kpiTokens.textContent = tokens.combined_total
                    ? formatGaugeValue(tokens.combined_total.grand_total, '')
                    : formatGaugeValue(tokens.total.total_tokens, '');
            }
            if (kpiCost) {
                const totalCost = tokens.combined_total?.grand_cost_usd || tokens.total.estimated_cost_usd;
                kpiCost.textContent = `${totalCost.toFixed(4)} $`;
            }
        }
        if (tokens && tokens.real_billing) {
            const rb = tokens.real_billing;
            const dsEl = document.getElementById('kpi-deepseek-balance');
            if (dsEl && rb.deepseek_balance_usd != null) {
                dsEl.textContent = `DeepSeek: ${rb.deepseek_balance_usd.toFixed(2)} $`;
            }
        }

        // Mise à jour des jauges de quotas
        try {
            const quotas = await fetchQuotasSliding();
            if (typeof updateDashboardGauges === 'function') updateDashboardGauges(quotas);
            if (typeof updateQuotaGauges === 'function') updateQuotaGauges(quotas);
            if (typeof updatePricingQuotaGauges === 'function') updatePricingQuotaGauges(quotas);
        } catch { /* endpoint optionnel */ }

    } catch (err) {
        // Silencieux pour ne pas spammer la console
    }
}
