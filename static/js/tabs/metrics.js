/* ============================================================
   METRICS.JS — Onglet de Métriques de Performance & Elo
   Intégré au système de navigation dynamique (app.js)
   ============================================================ */

let metricsTokensChart = null;
let metricsCostsChart = null;
let metricsRoutingChart = null;

// Configuration partagée Chart.js (thème sombre)
const metricsChartDefaults = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
        legend: { labels: { color: '#9ca3af', font: { family: 'Inter, sans-serif' } } }
    },
    scales: {
        x: { ticks: { color: '#6b7280', font: { family: 'Inter, sans-serif', size: 10 } }, grid: { color: 'rgba(255,255,255,0.03)' } },
        y: { ticks: { color: '#6b7280', font: { family: 'Inter, sans-serif', size: 10 } }, grid: { color: 'rgba(255,255,255,0.03)' } }
    }
};

/**
 * Point d'entrée de l'onglet : Rendu HTML de base
 */
function renderMetricsTab(container) {
    // Détruire proprement les anciens graphiques pour éviter les fuites mémoire
    destroyMetricsCharts();

    container.innerHTML = `
        <div class="metrics-dashboard">
            <!-- Header -->
            <div class="metrics-header">
                <h2>⚡ Métriques de Performance &amp; Elo</h2>
                <div class="metrics-actions">
                    <select class="period-select" id="metricsPeriodSelect" onchange="loadMetricsAll()">
                        <option value="1h">Dernière heure</option>
                        <option value="6h">6 heures</option>
                        <option value="24h" selected>24 heures</option>
                        <option value="7d">7 jours</option>
                        <option value="30d">30 jours</option>
                    </select>
                    <button class="refresh-btn" onclick="loadMetricsAll()">⟳ Rafraîchir</button>
                </div>
            </div>

            <!-- KPI Cards -->
            <div class="metrics-kpi-grid">
                <div class="metrics-kpi-card">
                    <div class="kpi-label">Tokens Totaux</div>
                    <div class="kpi-value" id="metricsKpiTokens">—</div>
                    <div class="kpi-trend" id="metricsKpiTokensTrend"></div>
                </div>
                <div class="metrics-kpi-card">
                    <div class="kpi-label">Sessions</div>
                    <div class="kpi-value" id="metricsKpiSessions">—</div>
                    <div class="kpi-trend" id="metricsKpiSessionsTrend"></div>
                </div>
                <div class="metrics-kpi-card">
                    <div class="kpi-label">Coût Total (USD)</div>
                    <div class="kpi-value" id="metricsKpiCost">—</div>
                    <div class="kpi-trend" id="metricsKpiCostTrend"></div>
                </div>
                <div class="metrics-kpi-card">
                    <div class="kpi-label">Projection 7j</div>
                    <div class="kpi-value" id="metricsKpiForecast">—</div>
                    <div class="kpi-trend" id="metricsKpiForecastTrend"></div>
                </div>
            </div>

            <!-- Charts -->
            <div class="metrics-charts-grid">
                <div class="metrics-chart-card">
                    <h3><span class="icon" style="background:rgba(129,140,248,0.15);color:var(--accent-primary);">📊</span> Tokens par heure</h3>
                    <div style="position:relative;height:250px;"><canvas id="metricsTokensCanvas"></canvas></div>
                </div>
                <div class="metrics-chart-card">
                    <h3><span class="icon" style="background:rgba(16,185,129,0.15);color:var(--success);">💰</span> Coûts par heure (USD)</h3>
                    <div style="position:relative;height:250px;"><canvas id="metricsCostsCanvas"></canvas></div>
                </div>
            </div>

            <!-- Bottom Grid : Elo + Routing -->
            <div class="metrics-bottom-grid">
                <!-- Elo Leaderboard -->
                <div class="metrics-table-card">
                    <h3>🏆 Classement Elo par Domaine</h3>
                    <div id="metricsEloContent">
                        <div class="metrics-loading"><div class="spinner"></div>Chargement des scores Elo...</div>
                    </div>
                </div>

                <!-- Routing Stats -->
                <div class="metrics-chart-card">
                    <h3><span class="icon" style="background:rgba(167,139,250,0.15);color:var(--accent-secondary);">🔀</span> Répartition du Routage</h3>
                    <div style="position:relative;height:250px;"><canvas id="metricsRoutingCanvas"></canvas></div>
                </div>
            </div>

            <!-- Heatmap Elo (Modèles ✕ Domaines) -->
            <div class="metrics-table-card">
                <h3>🗺️ Heatmap des Performances Elo (Modèles ✕ Domaines)</h3>
                <div id="metricsEloHeatmap" style="margin-top: 1rem; overflow-x: auto;">
                    <div class="metrics-loading"><div class="spinner"></div>Chargement de la heatmap...</div>
                </div>
            </div>
        </div>
    `;

    // Charger les données
    loadMetricsAll();
}

/**
 * Nettoie les instances de graphiques pour éviter les doublons/conflits sur les canvas
 */
function destroyMetricsCharts() {
    if (metricsTokensChart) { metricsTokensChart.destroy(); metricsTokensChart = null; }
    if (metricsCostsChart) { metricsCostsChart.destroy(); metricsCostsChart = null; }
    if (metricsRoutingChart) { metricsRoutingChart.destroy(); metricsRoutingChart = null; }
}

/**
 * Chargement principal des API en parallèle
 */
async function loadMetricsAll() {
    const select = document.getElementById('metricsPeriodSelect');
    const period = select ? select.value : '24h';
    
    try {
        const [telemetry, elo] = await Promise.all([
            fetch(`/api/metrics/telemetry?period=${period}`).then(r => r.ok ? r.json() : null).catch(() => null),
            fetch('/api/metrics/elo').then(r => r.ok ? r.json() : null).catch(() => null),
        ]);

        if (telemetry) {
            updateMetricsKPIs(telemetry.kpis, telemetry.budget_forecast);
            updateMetricsTokensChart(telemetry.time_series);
            updateMetricsCostsChart(telemetry.time_series);
            updateMetricsRoutingChart(telemetry.routing_stats);
        }

        if (elo) {
            updateMetricsEloTable(elo);
            updateMetricsEloHeatmap(elo);
        }
    } catch (err) {
        console.error('[METRICS TAB] Erreur de chargement:', err);
    }
}

/**
 * Formatage abrégé des nombres
 */
function metricsFmt(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return String(n);
}

/**
 * Conversion du score Elo en badge et icône
 */
function metricsEloRank(score) {
    if (score >= 1600) return ['expert', '⭐'];
    if (score >= 1500) return ['competent', '🟢'];
    if (score >= 1400) return ['novice', '🟡'];
    return ['unreliable', '🔴'];
}

/**
 * Mise à jour des KPI cards
 */
function updateMetricsKPIs(kpis, forecast) {
    const kpiTokens = document.getElementById('metricsKpiTokens');
    const kpiSessions = document.getElementById('metricsKpiSessions');
    const kpiCost = document.getElementById('metricsKpiCost');
    const kpiForecast = document.getElementById('metricsKpiForecast');

    if (kpiTokens && kpis) {
        kpiTokens.textContent = metricsFmt(kpis.total_tokens || 0);
    }
    if (kpiSessions && kpis) {
        kpiSessions.textContent = kpis.total_sessions || 0;
    }
    if (kpiCost && kpis) {
        kpiCost.textContent = '$' + (kpis.total_cost_usd || 0).toFixed(4);
    }
    if (kpiForecast && forecast) {
        kpiForecast.textContent = '$' + (forecast.projected_7d || 0).toFixed(2);
        
        const forecastTrend = document.getElementById('metricsKpiForecastTrend');
        if (forecastTrend) {
            forecastTrend.textContent = `Moy. jour: $${(forecast.avg_daily_cost || 0).toFixed(4)} — Proj. 30j: $${(forecast.projected_30d || 0).toFixed(2)}`;
        }
    }
}

/**
 * Graphique : Tokens/heure
 */
function updateMetricsTokensChart(ts) {
    const canvas = document.getElementById('metricsTokensCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (metricsTokensChart) metricsTokensChart.destroy();
    
    const labels = (ts?.labels || []).map(l => l.split(' ')[1] || l);
    metricsTokensChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: 'Tokens',
                data: ts?.tokens || [],
                backgroundColor: 'rgba(129, 140, 248, 0.4)',
                borderColor: '#818cf8',
                borderWidth: 1,
                borderRadius: 4,
            }]
        },
        options: { ...metricsChartDefaults }
    });
}

/**
 * Graphique : Coûts/heure
 */
function updateMetricsCostsChart(ts) {
    const canvas = document.getElementById('metricsCostsCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (metricsCostsChart) metricsCostsChart.destroy();

    const labels = (ts?.labels || []).map(l => l.split(' ')[1] || l);
    metricsCostsChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Coût USD',
                data: ts?.costs || [],
                borderColor: '#10b981',
                backgroundColor: 'rgba(16, 185, 129, 0.1)',
                fill: true,
                tension: 0.4,
                pointRadius: 2,
            }]
        },
        options: { ...metricsChartDefaults }
    });
}

/**
 * Graphique : Répartition du Routage
 */
function updateMetricsRoutingChart(routing) {
    const canvas = document.getElementById('metricsRoutingCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (metricsRoutingChart) metricsRoutingChart.destroy();

    const cats = routing?.categories || [];
    const labels = cats.map(c => c.dominant_category || 'inconnu');
    const data = cats.map(c => c.count || 0);
    const colors = ['#818cf8', '#a78bfa', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#3b82f6', '#06b6d4'];

    metricsRoutingChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels,
            datasets: [{
                data,
                backgroundColor: colors.slice(0, data.length),
                borderWidth: 0,
                hoverOffset: 8,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '65%',
            plugins: {
                legend: {
                    position: 'right',
                    labels: { color: '#9ca3af', font: { family: 'Inter, sans-serif', size: 11 }, padding: 12 }
                }
            }
        }
    });
}

/**
 * Génération du tableau classique Elo par domaine
 */
function updateMetricsEloTable(eloData) {
    const container = document.getElementById('metricsEloContent');
    if (!container) return;
    const domains = eloData?.domains || [];
    const leaderboards = eloData?.leaderboards || {};

    if (domains.length === 0) {
        container.innerHTML = `<div class="metrics-loading" style="padding:2rem"><p style="color:var(--text-muted)">Aucun score Elo enregistré.<br>Les scores apparaîtront après quelques exécutions du moteur.</p></div>`;
        return;
    }

    let html = '';
    for (const domain of domains) {
        const lb = leaderboards[domain] || [];
        if (lb.length === 0) continue;

        html += `<h4 style="margin:1rem 0 0.5rem;color:var(--accent-secondary);font-size:0.85rem;text-transform:capitalize">${domain.replace('_', ' ')}</h4>`;
        html += `<table><thead><tr><th>#</th><th>Modèle</th><th>Elo</th><th>W/L</th><th>Win%</th><th>Rang</th></tr></thead><tbody>`;

        lb.forEach((entry, i) => {
            const [rankClass, rankIcon] = metricsEloRank(entry.elo);
            html += `<tr>
                <td style="color:var(--text-muted)">${i + 1}</td>
                <td style="font-weight:500">${entry.model}</td>
                <td><strong>${entry.elo}</strong></td>
                <td>${entry.wins}/${entry.losses}</td>
                <td>${entry.win_rate}%</td>
                <td><span class="elo-badge elo-${rankClass}">${rankIcon} ${rankClass}</span></td>
            </tr>`;
        });

        html += '</tbody></table>';
    }

    container.innerHTML = html;
}

/**
 * Génération de la Heatmap Elo interactive
 */
function updateMetricsEloHeatmap(eloData) {
    const container = document.getElementById('metricsEloHeatmap');
    if (!container) return;
    const scores = eloData?.scores || {};
    const domains = eloData?.domains || [];
    
    // Extraire et trier tous les modèles
    const models = Object.keys(scores).sort();

    if (models.length === 0 || domains.length === 0) {
        container.innerHTML = `<div class="metrics-loading" style="padding:2rem"><p style="color:var(--text-muted)">Aucun score disponible pour générer la heatmap.</p></div>`;
        return;
    }

    let html = '<table><thead><tr><th>Modèle</th>';
    domains.forEach(d => {
        html += `<th style="text-align:center;text-transform:capitalize;">${d.replace('_', ' ')}</th>`;
    });
    html += '</tr></thead><tbody>';

    models.forEach(model => {
        html += `<tr><td style="font-weight:600;white-space:nowrap;">${model}</td>`;
        domains.forEach(domain => {
            const entry = scores[model]?.[domain];
            if (entry) {
                const elo = Math.round(entry.elo);
                const matches = entry.matches || 0;
                const [rankClass, rankIcon] = metricsEloRank(elo);
                html += `<td style="text-align:center;" class="heatmap-cell heatmap-${rankClass}" title="Matches: ${matches} | Win rate: ${entry.win_rate}% | Latence moy: ${entry.avg_latency_ms ? entry.avg_latency_ms + 'ms' : '–'}">
                    <span class="elo-badge elo-${rankClass}" style="background:transparent;padding:0;font-size:0.85rem;"><strong>${elo}</strong></span>
                    <div style="font-size:0.6rem;color:var(--text-muted);margin-top:2px;">${matches} match${matches > 1 ? 'es' : ''}</div>
                </td>`;
            } else {
                html += `<td style="text-align:center;color:var(--text-muted);opacity:0.3;background:rgba(255,255,255,0.01)">–</td>`;
            }
        });
        html += '</tr>';
    });

    html += '</tbody></table>';
    container.innerHTML = html;
}
