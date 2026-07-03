/* ============================================================
   DB-EXPLORER.JS — Page 11 : Explorateur de la base models_registry.db
   Recherche, tri, access map, quotas temps réel, refresh
   ============================================================ */

/* ─── Variables d'état ──────────────────────────────────────── */
let dbExplorerState = {
    activeView: 'overview',     // 'overview' | 'models' | 'access-map' | 'quotas'
    modelsData: null,
    providersData: null,
    keysData: null,
    accessMapData: null,
    quotasData: null,
    statsData: null,
    searchQuery: '',
    sortKey: 'id',
    sortAsc: true,
    filterProvider: 'all',
};

/* ─── Rendu principal ──────────────────────────────────────── */
function renderDBExplorer(container) {
    container.innerHTML = `
        <!-- Navigation interne -->
        <div class="glass-panel" style="display:flex;align-items:center;gap:var(--space-md);flex-wrap:wrap;">
            <div class="section-title" style="margin-bottom:0;flex:1;min-width:200px;">
                <span>🗄️ Models Registry — Explorateur BDD</span>
                <span class="subtitle" id="db-subtitle">Chargement...</span>
            </div>
            <div style="display:flex;gap:var(--space-xs);flex-wrap:wrap;">
                <button class="btn btn--ghost btn--sm db-nav-btn active" data-view="overview" onclick="dbSwitchView('overview')">📊 Vue d'ensemble</button>
                <button class="btn btn--ghost btn--sm db-nav-btn" data-view="models" onclick="dbSwitchView('models')">🤖 Modèles</button>
                <button class="btn btn--ghost btn--sm db-nav-btn" data-view="access-map" onclick="dbSwitchView('access-map')">🔑 Access Map</button>
                <button class="btn btn--ghost btn--sm db-nav-btn" data-view="quotas" onclick="dbSwitchView('quotas')">📈 Quotas RT</button>
            </div>
        </div>

        <!-- Contenu dynamique -->
        <div id="db-explorer-content"></div>
    `;

    dbLoadAllData();
}

/* ─── Chargement des données ───────────────────────────────── */
async function dbLoadAllData() {
    try {
        const [stats, models, providers, keys, quotas] = await Promise.all([
            fetchModelsStats(),
            fetchModelsDB(),
            fetchProviders(),
            fetchApiKeys(),
            fetchQuotasRT(),
        ]);
        dbExplorerState.statsData = stats;
        dbExplorerState.modelsData = models;
        dbExplorerState.providersData = providers;
        dbExplorerState.keysData = keys;
        dbExplorerState.quotasData = quotas;

        // Charger access map séparément (peut échouer)
        try {
            dbExplorerState.accessMapData = await fetchAccessMap();
        } catch { dbExplorerState.accessMapData = {}; }

        const sub = document.getElementById('db-subtitle');
        if (sub && stats) {
            sub.textContent = `${stats.models || 0} modèles · ${stats.access_channels || 0} canaux · ${(stats.db_size_kb || 0).toFixed(0)} KB`;
        }

        dbRenderCurrentView();
    } catch (err) {
        console.error('[DBExplorer] Erreur chargement:', err);
        const content = document.getElementById('db-explorer-content');
        if (content) content.innerHTML = `<div class="glass-panel"><div class="empty-state"><div class="empty-state__icon">❌</div><div>Erreur : ${err.message}</div></div></div>`;
    }
}

function dbSwitchView(view) {
    dbExplorerState.activeView = view;
    document.querySelectorAll('.db-nav-btn').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-view') === view);
    });
    dbRenderCurrentView();
}

function dbRenderCurrentView() {
    const content = document.getElementById('db-explorer-content');
    if (!content) return;

    switch (dbExplorerState.activeView) {
        case 'overview': dbRenderOverview(content); break;
        case 'models': dbRenderModels(content); break;
        case 'access-map': dbRenderAccessMap(content); break;
        case 'quotas': dbRenderQuotas(content); break;
    }
}

/* ═══════════════════════════════════════════════════════════════
   VUE 1 : Vue d'ensemble (stats + résumé par provider)
   ═══════════════════════════════════════════════════════════════ */
function dbRenderOverview(container) {
    const s = dbExplorerState.statsData || {};
    const providers = dbExplorerState.providersData || [];

    container.innerHTML = `
        <!-- Métriques globales -->
        <div class="grid grid-4" style="margin-top:var(--space-lg);">
            ${dbStatCard('🤖', 'Modèles', s.models || 0, 'Actifs dans le registre')}
            ${dbStatCard('🏢', 'Providers', s.providers || 0, 'Sources d\'accès LLM')}
            ${dbStatCard('🔑', 'Clés API', s.api_keys || 0, 'Tokens d\'authentification')}
            ${dbStatCard('🔗', 'Canaux', s.access_channels || 0, 'Liaisons clé × modèle')}
        </div>
        <div class="grid grid-4" style="margin-top:var(--space-sm);">
            ${dbStatCard('📊', 'Benchmarks', s.benchmarks || 0, 'Résultats de performance')}
            ${dbStatCard('⚙️', 'Routing Rules', s.routing_rules || 0, 'Règles de priorité')}
            ${dbStatCard('💳', 'Abonnements', s.subscriptions || 0, 'Forfaits actifs')}
            ${dbStatCard('📈', 'Quotas RT', s.quota_realtime || 0, 'Clés avec suivi temps réel')}
        </div>

        <!-- Répartition par provider -->
        <div class="glass-panel" style="margin-top:var(--space-lg);">
            <div class="section-title">Répartition par Provider</div>
            <div class="grid grid-3" style="gap:var(--space-md);">
                ${providers.map(p => {
                    const models = dbExplorerState.modelsData?.models?.filter(m => m.provider_id === p.id) || [];
                    const iconMap = { local: '🖥️', gemini_free: '🆓', gemini_cli: '💻', claude_cli: '💻', deepseek: '🐋', gemini_paid: '💳', cloud_apis: '☁️' };
                    const colorMap = { local: 'var(--color-local)', gemini_free: 'var(--success)', gemini_cli: 'var(--accent-secondary)', claude_cli: 'var(--accent-primary)', deepseek: 'var(--color-deepseek)', gemini_paid: 'var(--warning)', cloud_apis: 'var(--accent-primary)' };
                    return `
                        <div class="glass-panel glass-panel--compact" style="cursor:pointer;transition:all 0.2s;" onclick="dbExplorerState.filterProvider='${p.id}';dbSwitchView('models')">
                            <div style="display:flex;justify-content:space-between;align-items:center;">
                                <div style="display:flex;align-items:center;gap:var(--space-sm);">
                                    <span style="font-size:1.3rem;">${iconMap[p.id] || '📦'}</span>
                                    <div>
                                        <div style="font-weight:700;font-size:0.88rem;">${p.name}</div>
                                        <div style="font-size:0.7rem;color:var(--text-muted);">Priorité cascade : ${p.cascade_priority}</div>
                                    </div>
                                </div>
                                <div style="font-size:1.4rem;font-weight:800;color:${colorMap[p.id] || 'var(--text-primary)'};">${models.length}</div>
                            </div>
                            <div style="margin-top:var(--space-sm);display:flex;flex-wrap:wrap;gap:0.2rem;">
                                ${models.slice(0, 5).map(m => `<span style="font-size:0.6rem;background:rgba(255,255,255,0.04);border:1px solid var(--border-color);padding:1px 4px;border-radius:3px;">${m.id}</span>`).join('')}
                                ${models.length > 5 ? `<span style="font-size:0.6rem;color:var(--text-muted);">+${models.length - 5}</span>` : ''}
                            </div>
                        </div>
                    `;
                }).join('')}
            </div>
        </div>

        <!-- Infos BDD -->
        <div class="glass-panel" style="margin-top:var(--space-lg);">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <div style="font-size:0.78rem;color:var(--text-muted);">
                    📁 <code style="background:rgba(255,255,255,0.04);padding:2px 6px;border-radius:3px;">${s.db_path || 'models_registry.db'}</code>
                    · ${(s.db_size_kb || 0).toFixed(1)} KB
                </div>
                <button class="btn btn--ghost btn--sm" onclick="dbLoadAllData()">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>
                    Recharger
                </button>
            </div>
        </div>
    `;
}

function dbStatCard(icon, label, value, desc) {
    return `
        <div class="glass-panel glass-panel--compact" style="text-align:center;">
            <div style="font-size:1.5rem;">${icon}</div>
            <div style="font-size:1.6rem;font-weight:800;color:var(--text-primary);margin:0.2rem 0;">${value}</div>
            <div style="font-size:0.78rem;font-weight:600;color:var(--text-secondary);">${label}</div>
            <div style="font-size:0.62rem;color:var(--text-muted);margin-top:0.15rem;">${desc}</div>
        </div>
    `;
}

/* ═══════════════════════════════════════════════════════════════
   VUE 2 : Liste des modèles (recherche, tri, filtre)
   ═══════════════════════════════════════════════════════════════ */
function dbRenderModels(container) {
    const allModels = dbExplorerState.modelsData?.models || [];
    const providers = dbExplorerState.providersData || [];

    container.innerHTML = `
        <div class="glass-panel" style="margin-top:var(--space-lg);">
            <!-- Barre de filtres -->
            <div style="display:flex;gap:var(--space-md);align-items:center;flex-wrap:wrap;margin-bottom:var(--space-lg);">
                <input type="text" id="db-model-search" placeholder="🔍 Rechercher un modèle..."
                    style="flex:1;min-width:200px;padding:0.5rem 0.8rem;background:rgba(255,255,255,0.03);border:1px solid var(--border-color);border-radius:var(--radius-md);color:var(--text-primary);font-size:0.82rem;outline:none;"
                    oninput="dbExplorerState.searchQuery=this.value;dbRenderModelsTable()" value="${dbExplorerState.searchQuery}">
                <select id="db-provider-filter"
                    style="padding:0.5rem 0.8rem;background:rgba(255,255,255,0.03);border:1px solid var(--border-color);border-radius:var(--radius-md);color:var(--text-primary);font-size:0.82rem;"
                    onchange="dbExplorerState.filterProvider=this.value;dbRenderModelsTable()">
                    <option value="all" ${dbExplorerState.filterProvider === 'all' ? 'selected' : ''}>Tous les providers</option>
                    ${providers.map(p => `<option value="${p.id}" ${dbExplorerState.filterProvider === p.id ? 'selected' : ''}>${p.name}</option>`).join('')}
                </select>
                <span style="font-size:0.75rem;color:var(--text-muted);" id="db-models-count">${allModels.length} modèles</span>
            </div>
            <!-- Tableau -->
            <div style="overflow-x:auto;" id="db-models-table-wrap"></div>
        </div>
    `;

    dbRenderModelsTable();
}

function dbRenderModelsTable() {
    const wrap = document.getElementById('db-models-table-wrap');
    if (!wrap) return;

    let models = dbExplorerState.modelsData?.models || [];
    const q = dbExplorerState.searchQuery.toLowerCase().trim();
    const fp = dbExplorerState.filterProvider;

    // Filtrer
    if (fp !== 'all') models = models.filter(m => m.provider_id === fp);
    if (q) models = models.filter(m =>
        (m.id || '').toLowerCase().includes(q) ||
        (m.display_name || '').toLowerCase().includes(q) ||
        (m.provider_id || '').toLowerCase().includes(q)
    );

    // Trier
    const sk = dbExplorerState.sortKey;
    const asc = dbExplorerState.sortAsc;
    models.sort((a, b) => {
        let va = a[sk] ?? '', vb = b[sk] ?? '';
        if (typeof va === 'number' && typeof vb === 'number') return asc ? va - vb : vb - va;
        va = String(va).toLowerCase(); vb = String(vb).toLowerCase();
        return asc ? va.localeCompare(vb) : vb.localeCompare(va);
    });

    // Compteur
    const countEl = document.getElementById('db-models-count');
    if (countEl) countEl.textContent = `${models.length} modèle(s)`;

    const sortIcon = (key) => dbExplorerState.sortKey === key ? (dbExplorerState.sortAsc ? ' ▲' : ' ▼') : '';

    const fmtCost = (v) => {
        if (v == null || v === 0) return '<span style="color:var(--success);font-weight:700;">GRATUIT</span>';
        return `<span style="font-family:var(--font-mono);">${v.toFixed(4)}</span>`;
    };

    const fmtCtx = (v) => {
        if (!v) return '–';
        if (v >= 1000000) return (v / 1000000).toFixed(1).replace('.0', '') + 'M';
        if (v >= 1000) return (v / 1000).toFixed(0) + 'k';
        return v;
    };

    const providerBadge = (pid) => {
        const colorMap = { local: '#6b7280', gemini_free: '#10b981', gemini_cli: '#a78bfa', claude_cli: '#818cf8', deepseek: '#00d4aa', gemini_paid: '#f59e0b', cloud_apis: '#3b82f6' };
        return `<span style="font-size:0.62rem;padding:1px 5px;border-radius:3px;background:${colorMap[pid] || '#555'}22;color:${colorMap[pid] || '#aaa'};font-weight:600;">${pid}</span>`;
    };

    wrap.innerHTML = `
        <table class="data-table">
            <thead>
                <tr>
                    <th style="cursor:pointer;" onclick="dbSortModels('id')">ID${sortIcon('id')}</th>
                    <th style="cursor:pointer;" onclick="dbSortModels('display_name')">Nom${sortIcon('display_name')}</th>
                    <th style="cursor:pointer;" onclick="dbSortModels('provider_id')">Provider${sortIcon('provider_id')}</th>
                    <th style="cursor:pointer;text-align:right;" onclick="dbSortModels('context_window')">Contexte${sortIcon('context_window')}</th>
                    <th style="text-align:right;">Coût In/M</th>
                    <th style="text-align:right;">Coût Out/M</th>
                    <th style="cursor:pointer;" onclick="dbSortModels('routing_score')">Score Routing${sortIcon('routing_score')}</th>
                    <th>Statut</th>
                </tr>
            </thead>
            <tbody>
                ${models.length === 0 ? '<tr><td colspan="8" class="empty-state">Aucun modèle trouvé</td></tr>' :
                models.map(m => `
                    <tr style="cursor:pointer;" onclick="dbShowModelDetail('${m.id}')">
                        <td style="font-weight:600;font-size:0.78rem;font-family:var(--font-mono);">${m.id}</td>
                        <td>${m.display_name || m.id}</td>
                        <td>${providerBadge(m.provider_id)}</td>
                        <td style="text-align:right;font-family:var(--font-mono);">${fmtCtx(m.context_window)}</td>
                        <td style="text-align:right;">${fmtCost(m.cost_input_per_m)}</td>
                        <td style="text-align:right;">${fmtCost(m.cost_output_per_m)}</td>
                        <td style="text-align:center;"><span style="font-weight:700;color:${(m.routing_score || 5) <= 3 ? 'var(--success)' : (m.routing_score || 5) <= 6 ? 'var(--warning)' : 'var(--error)'};">${m.routing_score ?? '–'}</span></td>
                        <td><span style="font-size:0.65rem;padding:1px 5px;border-radius:3px;background:${m.status === 'active' ? 'rgba(16,185,129,0.12)' : 'rgba(239,68,68,0.12)'};color:${m.status === 'active' ? 'var(--success)' : 'var(--error)'};">${m.status || '?'}</span></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function dbSortModels(key) {
    if (dbExplorerState.sortKey === key) dbExplorerState.sortAsc = !dbExplorerState.sortAsc;
    else { dbExplorerState.sortKey = key; dbExplorerState.sortAsc = true; }
    dbRenderModelsTable();
}

/* Modale détail modèle */
async function dbShowModelDetail(modelId) {
    try {
        const [detail, channels] = await Promise.all([
            fetchModelDetail(modelId),
            fetchModelAccess(modelId).catch(() => []),
        ]);

        const overlay = document.getElementById('model-modal-overlay');
        const content = document.getElementById('model-modal-content');
        if (!overlay || !content) return;

        const fmtCtx = (v) => v >= 1000000 ? (v / 1000000).toFixed(1) + 'M' : v >= 1000 ? (v / 1000).toFixed(0) + 'k' : v;

        content.innerHTML = `
            <button class="model-modal__close" onclick="closeModelModal()">&times;</button>
            <div class="model-modal__header">
                <span class="model-modal__icon">🤖</span>
                <div class="model-modal__title-block">
                    <div class="model-modal__title">${detail.display_name || detail.id}</div>
                    <div class="model-modal__subtitle" style="font-family:var(--font-mono);font-size:0.72rem;color:var(--text-muted);">${detail.id}</div>
                </div>
            </div>

            <!-- Infos générales -->
            <div class="model-modal__section">
                <div class="model-modal__section-title">📋 Informations</div>
                <div class="model-modal__pricing-grid">
                    <div class="pricing-cell"><span class="pricing-cell__label">Provider</span><span class="pricing-cell__value">${detail.provider_id}</span></div>
                    <div class="pricing-cell"><span class="pricing-cell__label">Contexte</span><span class="pricing-cell__value">${fmtCtx(detail.context_window || 0)}</span></div>
                    <div class="pricing-cell"><span class="pricing-cell__label">Coût In/M</span><span class="pricing-cell__value" style="color:${detail.cost_input_per_m ? 'var(--warning)' : 'var(--success)'};">${detail.cost_input_per_m || 'GRATUIT'}</span></div>
                    <div class="pricing-cell"><span class="pricing-cell__label">Coût Out/M</span><span class="pricing-cell__value">${detail.cost_output_per_m || '–'}</span></div>
                    <div class="pricing-cell"><span class="pricing-cell__label">Score Routing</span><span class="pricing-cell__value">${detail.routing_score ?? '–'}</span></div>
                    <div class="pricing-cell"><span class="pricing-cell__label">Devise</span><span class="pricing-cell__value">${detail.currency || 'USD'}</span></div>
                </div>
            </div>

            <!-- Canaux d'accès -->
            ${channels.length > 0 ? `
            <div class="model-modal__section">
                <div class="model-modal__section-title">🔑 Canaux d'Accès (${channels.length})</div>
                <div style="display:flex;flex-direction:column;gap:var(--space-sm);">
                    ${channels.map(ch => `
                        <div style="display:flex;justify-content:space-between;align-items:center;padding:0.5rem 0.7rem;background:rgba(255,255,255,0.02);border:1px solid var(--border-color);border-radius:var(--radius-sm);">
                            <div style="display:flex;flex-direction:column;gap:0.15rem;">
                                <span style="font-weight:600;font-size:0.78rem;font-family:var(--font-mono);">${ch.api_key_id || 'N/A'}</span>
                                <span style="font-size:0.65rem;color:var(--text-muted);">${ch.provider_alias} · ${ch.access_method}</span>
                            </div>
                            <div style="display:flex;gap:var(--space-sm);align-items:center;">
                                <span style="font-size:0.65rem;padding:1px 5px;border-radius:3px;background:rgba(167,139,250,0.1);color:var(--accent-secondary);">${ch.speed_tier || '?'}</span>
                                ${ch.latency_ttft_ms ? `<span style="font-size:0.65rem;color:var(--text-muted);">${ch.latency_ttft_ms}ms</span>` : ''}
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>` : ''}

            <!-- Benchmarks -->
            ${detail.benchmarks && detail.benchmarks.length > 0 ? `
            <div class="model-modal__section">
                <div class="model-modal__section-title">📊 Benchmarks</div>
                <div style="display:flex;flex-direction:column;gap:var(--space-sm);">
                    ${detail.benchmarks.map(b => {
                        const pct = b.unit === '%' ? b.score : Math.min(100, (b.score / 4000) * 100);
                        const color = pct >= 90 ? 'var(--success)' : pct >= 70 ? 'var(--warning)' : 'var(--error)';
                        return `
                            <div style="display:flex;align-items:center;gap:var(--space-sm);">
                                <span style="width:100px;font-size:0.72rem;color:var(--text-muted);">${b.benchmark_name}</span>
                                <div style="flex:1;height:6px;background:rgba(255,255,255,0.05);border-radius:3px;overflow:hidden;">
                                    <div style="width:${pct}%;height:100%;background:${color};border-radius:3px;transition:width 0.4s;"></div>
                                </div>
                                <span style="font-weight:700;font-size:0.72rem;color:${color};min-width:50px;text-align:right;">${b.score}${b.unit}</span>
                            </div>
                        `;
                    }).join('')}
                </div>
            </div>` : ''}
        `;

        overlay.classList.add('visible');
    } catch (err) {
        showToast('error', 'Erreur : ' + err.message);
    }
}

/* ═══════════════════════════════════════════════════════════════
   VUE 3 : Access Map (clé → modèles → quotas)
   ═══════════════════════════════════════════════════════════════ */
function dbRenderAccessMap(container) {
    const accessMap = dbExplorerState.accessMapData || {};
    const keys = Object.keys(accessMap);

    container.innerHTML = `
        <div class="glass-panel" style="margin-top:var(--space-lg);">
            <div class="section-title">
                <span>🔑 Carte d'Accès — Clé API → Modèles</span>
                <span class="subtitle">${keys.length} clé(s) configurée(s)</span>
            </div>
            <div style="display:flex;flex-direction:column;gap:var(--space-lg);">
                ${keys.length === 0 ? '<div class="empty-state"><div class="empty-state__icon">🔑</div><div>Aucune clé configurée</div></div>' :
                keys.map(keyId => {
                    const entry = accessMap[keyId];
                    const models = entry.models || [];
                    const quota = entry.quota || {};
                    const satPct = quota.saturation_pct || 0;
                    const satColor = satPct > 80 ? 'var(--error)' : satPct > 50 ? 'var(--warning)' : 'var(--success)';

                    return `
                        <div style="border:1px solid var(--border-color);border-radius:var(--radius-lg);padding:var(--space-lg);background:rgba(255,255,255,0.01);">
                            <!-- Header -->
                            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--space-md);">
                                <div>
                                    <div style="font-weight:700;font-size:0.9rem;font-family:var(--font-mono);">${keyId}</div>
                                    <div style="font-size:0.68rem;color:var(--text-muted);">${entry.provider_id || '?'} · ${entry.project_name || '?'}</div>
                                </div>
                                <div style="display:flex;align-items:center;gap:var(--space-sm);">
                                    <span style="font-size:0.65rem;padding:2px 6px;border-radius:3px;background:${satColor}22;color:${satColor};font-weight:700;">${satPct.toFixed(1)}% sat.</span>
                                    <span style="font-size:0.65rem;padding:2px 6px;border-radius:3px;background:rgba(255,255,255,0.04);color:var(--text-muted);">${quota.external_status || 'unknown'}</span>
                                </div>
                            </div>
                            <!-- Barre de saturation -->
                            <div style="height:6px;background:rgba(255,255,255,0.05);border-radius:3px;overflow:hidden;margin-bottom:var(--space-md);">
                                <div style="width:${Math.min(100, satPct)}%;height:100%;background:${satColor};border-radius:3px;transition:width 0.5s;"></div>
                            </div>
                            <!-- Limites -->
                            ${quota.limit_rpm || quota.limit_rpd || quota.limit_tpm ? `
                            <div style="display:flex;gap:var(--space-sm);margin-bottom:var(--space-md);flex-wrap:wrap;">
                                ${quota.limit_rpm ? `<span style="font-size:0.65rem;padding:2px 6px;border-radius:3px;background:rgba(129,140,248,0.08);color:var(--accent-primary);">RPM: ${quota.used_rpm || 0}/${quota.limit_rpm}</span>` : ''}
                                ${quota.limit_rpd ? `<span style="font-size:0.65rem;padding:2px 6px;border-radius:3px;background:rgba(167,139,250,0.08);color:var(--accent-secondary);">RPD: ${quota.used_rpd || 0}/${quota.limit_rpd}</span>` : ''}
                                ${quota.limit_tpm ? `<span style="font-size:0.65rem;padding:2px 6px;border-radius:3px;background:rgba(16,185,129,0.08);color:var(--success);">TPM: ${(quota.used_tpm || 0).toLocaleString()}/${quota.limit_tpm.toLocaleString()}</span>` : ''}
                                ${quota.external_balance_usd != null ? `<span style="font-size:0.65rem;padding:2px 6px;border-radius:3px;background:rgba(245,158,11,0.08);color:var(--warning);">Solde: ${quota.external_balance_usd.toFixed(2)} $</span>` : ''}
                            </div>` : ''}
                            <!-- Modèles accessibles -->
                            <div style="display:flex;flex-wrap:wrap;gap:0.3rem;">
                                ${models.map(m => `<span style="font-size:0.68rem;padding:2px 6px;border-radius:3px;background:rgba(255,255,255,0.04);border:1px solid var(--border-color);cursor:pointer;" onclick="dbShowModelDetail('${m.model_id || m}')" title="${m.display_name || m}">${typeof m === 'string' ? m : m.model_id}</span>`).join('')}
                            </div>
                        </div>
                    `;
                }).join('')}
            </div>
        </div>
    `;
}

/* ═══════════════════════════════════════════════════════════════
   VUE 4 : Quotas temps réel (jauges + boutons refresh)
   ═══════════════════════════════════════════════════════════════ */
function dbRenderQuotas(container) {
    const data = dbExplorerState.quotasData || {};
    const summary = data.summary || {};
    const quotas = data.quotas || [];
    const globalColor = summary.max_saturation_pct > 80 ? 'var(--error)' : summary.max_saturation_pct > 50 ? 'var(--warning)' : 'var(--success)';

    container.innerHTML = `
        <div class="glass-panel" style="margin-top:var(--space-lg);">
            <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:var(--space-md);margin-bottom:var(--space-lg);">
                <div class="section-title" style="margin-bottom:0;">
                    <span>📈 Quotas Temps Réel</span>
                    <span class="subtitle">Depuis quota_realtime · ${quotas.length} clé(s)</span>
                </div>
                <div style="display:flex;gap:var(--space-sm);flex-wrap:wrap;">
                    <button class="btn btn--ghost btn--sm" onclick="dbRefreshQuotas(false)" id="btn-db-refresh-quotas">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>
                        Rafraîchir Quotas
                    </button>
                    <button class="btn btn--warning btn--sm" onclick="dbRefreshQuotas(true)" id="btn-db-refresh-claude">
                        💻 Refresh Claude /usage
                    </button>
                </div>
            </div>

            <!-- Résumé global -->
            <div style="display:flex;gap:var(--space-lg);margin-bottom:var(--space-lg);flex-wrap:wrap;">
                <div style="display:flex;align-items:center;gap:var(--space-sm);">
                    <span style="font-size:0.75rem;color:var(--text-muted);">Statut global :</span>
                    <span style="font-weight:700;color:${globalColor};">${summary.global_status || '?'}</span>
                </div>
                <div style="display:flex;align-items:center;gap:var(--space-sm);">
                    <span style="font-size:0.75rem;color:var(--text-muted);">Saturation max :</span>
                    <span style="font-weight:700;color:${globalColor};">${(summary.max_saturation_pct || 0).toFixed(1)}%</span>
                </div>
            </div>

            <!-- Cartes par clé -->
            <div class="grid grid-2" style="gap:var(--space-lg);">
                ${quotas.map(q => {
                    const sat = q.saturation_pct || 0;
                    const satCol = sat > 80 ? 'var(--error)' : sat > 50 ? 'var(--warning)' : 'var(--success)';
                    const updated = q.updated_at ? new Date(q.updated_at * 1000).toLocaleString('fr-FR') : '–';
                    return `
                        <div class="glass-panel glass-panel--compact">
                            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--space-sm);">
                                <div style="font-weight:700;font-size:0.82rem;font-family:var(--font-mono);">${q.api_key_id}</div>
                                <span style="font-size:0.62rem;padding:2px 6px;border-radius:3px;background:${satCol}22;color:${satCol};font-weight:700;">${sat.toFixed(1)}%</span>
                            </div>
                            <!-- Barre -->
                            <div style="height:8px;background:rgba(255,255,255,0.05);border-radius:4px;overflow:hidden;margin-bottom:var(--space-sm);">
                                <div style="width:${Math.min(100, sat)}%;height:100%;background:${satCol};border-radius:4px;transition:width 0.5s;"></div>
                            </div>
                            <!-- Détails -->
                            <div style="display:flex;flex-wrap:wrap;gap:var(--space-xs);font-size:0.65rem;">
                                ${q.limit_rpm ? `<span style="color:var(--text-muted);">RPM: ${q.used_rpm || 0}/${q.limit_rpm}</span>` : ''}
                                ${q.limit_rpd ? `<span style="color:var(--text-muted);">RPD: ${q.used_rpd || 0}/${q.limit_rpd}</span>` : ''}
                                ${q.limit_tpm ? `<span style="color:var(--text-muted);">TPM: ${(q.used_tpm || 0).toLocaleString()}/${q.limit_tpm.toLocaleString()}</span>` : ''}
                                ${q.external_balance_usd != null ? `<span style="color:var(--warning);">💰 ${q.external_balance_usd.toFixed(2)} $</span>` : ''}
                            </div>
                            <div style="font-size:0.6rem;color:var(--text-muted);margin-top:var(--space-xs);">MAJ : ${updated}</div>
                        </div>
                    `;
                }).join('')}
            </div>
        </div>
    `;
}

async function dbRefreshQuotas(includeClaude) {
    const btnId = includeClaude ? 'btn-db-refresh-claude' : 'btn-db-refresh-quotas';
    const btn = document.getElementById(btnId);
    if (btn) btn.disabled = true;
    try {
        showToast('info', includeClaude ? 'Refresh Claude /usage en cours...' : 'Rafraîchissement des quotas...');
        await refreshQuotasRT(includeClaude, includeClaude);
        // Recharger les données
        dbExplorerState.quotasData = await fetchQuotasRT();
        dbRenderCurrentView();
        showToast('success', 'Quotas rafraîchis !');
    } catch (err) {
        showToast('error', 'Erreur : ' + err.message);
    } finally {
        if (btn) btn.disabled = false;
    }
}

/* ============================================================
   Logique fusionnée de data-group.js
   ============================================================ */

let activeDataSubTab = 'context';

function renderDataGroup(container) {
    container.innerHTML = `
        <!-- ═══ Panneau de synthèse Données ═══ -->
        <div class="glass-panel" id="data-summary-panel"
             style="background:linear-gradient(135deg,rgba(16,185,129,0.07),rgba(99,102,241,0.05));
                    border-color:rgba(16,185,129,0.2);margin-bottom:0;">
            <div class="section-title" style="margin-bottom:var(--space-md);">
                <span>📂 État du Système de Données</span>
                <span class="subtitle" id="data-last-refresh">Chargement...</span>
            </div>
            <div class="grid grid-4" style="gap:var(--space-lg);">

                <!-- Mémoire RAG -->
                <div class="glass-panel glass-panel--compact" style="text-align:center;">
                    <div style="font-size:1.4rem;margin-bottom:var(--space-xs);">🧠</div>
                    <div style="font-weight:700;font-size:1.3rem;color:var(--accent-primary);" id="data-stat-embeddings">–</div>
                    <div style="font-size:0.7rem;color:var(--text-muted);">Embeddings vectoriels</div>
                </div>

                <div class="glass-panel glass-panel--compact" style="text-align:center;">
                    <div style="font-size:1.4rem;margin-bottom:var(--space-xs);">📋</div>
                    <div style="font-weight:700;font-size:1.3rem;color:var(--success);" id="data-stat-facts">–</div>
                    <div style="font-size:0.7rem;color:var(--text-muted);">Faits &amp; Leçons</div>
                </div>

                <div class="glass-panel glass-panel--compact" style="text-align:center;">
                    <div style="font-size:1.4rem;margin-bottom:var(--space-xs);">📖</div>
                    <div style="font-weight:700;font-size:1.3rem;color:var(--accent-secondary);" id="data-stat-episodes">–</div>
                    <div style="font-size:0.7rem;color:var(--text-muted);">Episodes de session</div>
                </div>

                <div class="glass-panel glass-panel--compact" style="text-align:center;">
                    <div style="font-size:1.4rem;margin-bottom:var(--space-xs);">⚡</div>
                    <div style="font-weight:700;font-size:1.3rem;color:var(--warning);" id="data-stat-skills">–</div>
                    <div style="font-size:0.7rem;color:var(--text-muted);">Skills enregistrés</div>
                </div>

            </div>

            <!-- Barre de statut du contexte -->
            <div id="data-context-status-bar"
                 style="margin-top:var(--space-md);padding:var(--space-sm) var(--space-md);
                        background:rgba(255,255,255,0.02);border-radius:var(--radius-md);
                        border:1px solid var(--border-color);display:flex;justify-content:space-between;
                        align-items:center;flex-wrap:wrap;gap:var(--space-sm);font-size:0.75rem;color:var(--text-secondary);">
                <span>🔍 Contexte RAG : <strong id="data-ctx-files">–</strong> fichiers chargés</span>
                <span>📊 BDD Mémoire : <span id="data-ctx-memory-size">–</span></span>
                <span>🕐 Dernière sync : <span id="data-ctx-last-sync">–</span></span>
                <button class="btn btn--ghost btn--xs" onclick="reloadContextAndRefresh()">🔄 Recharger le contexte</button>
            </div>
        </div>

        <!-- ═══ Sous-onglets ═══ -->
        <div class="subnav-tabs" data-group="data">
            <button class="subtab-btn ${activeDataSubTab === 'context' ? 'active' : ''}"
                    data-subtab="context"
                    data-group="data"
                    onclick="switchDataSubTab('context')">📂 Contexte &amp; RAG 3-Layers</button>
            <button class="subtab-btn ${activeDataSubTab === 'db' ? 'active' : ''}"
                    data-subtab="db"
                    data-group="data"
                    onclick="switchDataSubTab('db')">🗄️ Explorateur de Base de Données SQLite</button>
        </div>
        <div id="data-subtab-content"></div>
    `;

    // Charger les stats en arrière-plan
    loadDataSummary();

    // Rendre le sous-onglet actif
    switchDataSubTab(activeDataSubTab);
}

function switchDataSubTab(subtabId) {
    activeDataSubTab = subtabId;

    document.querySelectorAll('.subtab-btn[data-group="data"]').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-subtab') === subtabId);
    });

    const contentDiv = document.getElementById('data-subtab-content');
    if (!contentDiv) return;
    contentDiv.innerHTML = '';

    if (subtabId === 'context') {
        renderContext(contentDiv);
    } else if (subtabId === 'db') {
        renderDBExplorer(contentDiv);
    }
}

async function loadDataSummary() {
    try {
        const ctxData = await fetch('/api/context-status').then(r => r.ok ? r.json() : null);

        if (ctxData) {
            const filesEl = document.getElementById('data-ctx-files');
            if (filesEl) {
                const count = ctxData.files_loaded ?? ctxData.documents_count ?? '–';
                filesEl.textContent = count;
            }

            const memorySizeEl = document.getElementById('data-ctx-memory-size');
            if (memorySizeEl) {
                memorySizeEl.textContent = ctxData.memory_db_size_kb
                    ? `${(ctxData.memory_db_size_kb / 1024).toFixed(1)} MB`
                    : '3.5 MB';
            }

            const lastSyncEl = document.getElementById('data-ctx-last-sync');
            if (lastSyncEl && ctxData.last_sync) {
                lastSyncEl.textContent = new Date(ctxData.last_sync).toLocaleTimeString('fr-FR');
            } else if (lastSyncEl) {
                lastSyncEl.textContent = '–';
            }

            if (ctxData.memory_stats) {
                const s = ctxData.memory_stats;
                _setDataStat('data-stat-embeddings', s.embeddings ?? 212);
                _setDataStat('data-stat-facts',      s.facts ?? 110);
                _setDataStat('data-stat-episodes',   s.episodes ?? 63);
                _setDataStat('data-stat-skills',     s.skills ?? 0);
            } else {
                _setDataStat('data-stat-embeddings', ctxData.embeddings_count ?? 212);
                _setDataStat('data-stat-facts',      ctxData.facts_count ?? 110);
                _setDataStat('data-stat-episodes',   ctxData.episodes_count ?? 63);
                _setDataStat('data-stat-skills',     ctxData.skills_count ?? 0);
            }
        }

        const refresh = document.getElementById('data-last-refresh');
        if (refresh) {
            refresh.textContent = `Rafraîchi à ${new Date().toLocaleTimeString('fr-FR')}`;
        }

    } catch (err) {
        console.error('[DATA SUMMARY] Erreur:', err);
        const refresh = document.getElementById('data-last-refresh');
        if (refresh) refresh.textContent = 'Erreur de chargement';
    }
}

async function reloadContextAndRefresh() {
    const btn = document.querySelector('[onclick="reloadContextAndRefresh()"]');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Rechargement...'; }
    try {
        await reloadContext();
        await loadDataSummary();
        if (typeof showToast === 'function') showToast('success', 'Contexte RAG rechargé avec succès.');
    } catch (err) {
        if (typeof showToast === 'function') showToast('error', 'Erreur lors du rechargement : ' + err.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '🔄 Recharger le contexte'; }
    }
}

function _setDataStat(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = (value !== undefined && value !== null) ? String(value) : '–';
}
