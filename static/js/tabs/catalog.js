/* ============================================================
   CATALOG.JS — Page 8 : Catalogue interactif des modèles
   Carousel canaux, cartes modèles, modale détail, matrice
   ============================================================ */

let catalogFilter = 'all'; // Filtre actif par canal

async function renderCatalog(container) {
    // Recharger dynamiquement les données depuis models_registry.db (SQLite)
    if (typeof initDynamicModelCatalog === "function") {
        await initDynamicModelCatalog().catch(err => console.error(err));
    }

    container.innerHTML = `
        <!-- Carousel des Canaux d'Accès -->
        <div class="glass-panel" style="display:flex;flex-direction:column;gap:var(--space-lg);">
            <div class="section-title">
                <span>Canaux d'Accès & Méthodes de Facturation</span>
                <span class="subtitle">Glissez horizontalement pour découvrir</span>
            </div>
            <div class="channel-carousel" id="channel-carousel"></div>
        </div>

        <!-- Filtres + Grille Modèles -->
        <div class="glass-panel" style="display:flex;flex-direction:column;gap:var(--space-lg);">
            <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:var(--space-md);">
                <div class="section-title" style="margin-bottom:0;">Fiches Modèles</div>
                <div class="catalog-filters" id="catalog-filters"></div>
            </div>
            <div class="model-cards-grid" id="model-cards-grid"></div>
        </div>

        <!-- Matrice de Comparaison -->
        <div class="glass-panel" style="display:flex;flex-direction:column;gap:var(--space-lg);">
            <div class="section-title" style="color:var(--accent-secondary);">
                <span>Matrice de Comparaison Rapide</span>
                <span class="subtitle">Cliquez sur les en-têtes pour trier</span>
            </div>
            <div style="overflow-x:auto;">
                <table class="comparison-matrix" id="comparison-matrix"></table>
            </div>
        </div>

        <!-- Modale Détail (overlay global) -->
        <div class="model-modal-overlay" id="model-modal-overlay" onclick="closeModelModal(event)">
            <div class="model-modal" id="model-modal-content" onclick="event.stopPropagation()"></div>
        </div>
    `;

    renderChannelCarousel();
    renderCatalogFilters();
    renderModelCards();
    renderComparisonMatrix();
}

// ─── Carousel des canaux ──────────────────────────────────────
function renderChannelCarousel() {
    const container = document.getElementById('channel-carousel');
    if (!container) return;

    const channelData = [
        { key: 'local', models: ['local'], quota: 'Illimité (VRAM)', extra: 'Airgapped · RTX 5070 Ti' },
        { key: 'free-api', models: getModelsForChannel('free-api'), quota: 'Flash: 15 RPM · 1500 RPD\nPro: 2 RPM · 50 RPD', extra: '⚠️ Données entraînement' },
        { key: 'cli-gemini-adv', models: getModelsForChannel('cli-gemini-adv'), quota: '~4M tokens/h\n~100M tokens/mois', extra: '🔒 Confidentiel' },
        { key: 'cli-claude-pro', models: getModelsForChannel('cli-claude-pro'), quota: '~1.5M tokens/h\n~35M tokens/mois', extra: '🔒 Confidentiel' },
        { key: 'paid-deepseek', models: getModelsForChannel('paid-deepseek'), quota: 'Sans limite', extra: 'Solde prépayé · Non confidentiel' },
        { key: 'paid-gcp', models: getModelsForChannel('paid-gcp'), quota: 'Sans limite', extra: '🔒 Confidentiel · Tarifs EUR' },
        { key: 'media', models: getModelsForChannel('media'), quota: 'Par unité', extra: 'Images, Vidéos, Recherche' }
    ];

    container.innerHTML = channelData.map(ch => {
        const info = CHANNELS[ch.key];
        if (!info) return '';
        const isFree = info.costTag === 'GRATUIT';
        const costStyle = isFree
            ? 'background:rgba(16,185,129,0.12);color:var(--success);'
            : 'background:rgba(255,255,255,0.04);color:var(--text-secondary);';

        return `
            <div class="channel-slide" style="--channel-color:${info.color};" onclick="filterByChannel('${ch.key}')">
                <div class="channel-slide__icon">${info.icon}</div>
                <div class="channel-slide__title">${info.label}</div>
                <div class="channel-slide__cost" style="${costStyle}">${info.costTag}</div>
                <div class="channel-slide__meta">
                    ${ch.quota.split('\n').map(l => `<span>${l}</span>`).join('')}
                    <span style="margin-top:0.15rem;">${ch.extra}</span>
                </div>
                <div class="channel-slide__models">
                    ${ch.models.length} modèle${ch.models.length > 1 ? 's' : ''} disponible${ch.models.length > 1 ? 's' : ''}
                </div>
            </div>
        `;
    }).join('');
}

function getModelsForChannel(channelKey) {
    const models = [];
    for (const [id, entry] of Object.entries(MODEL_CATALOG)) {
        if (entry && entry.channel === channelKey) models.push(id);
    }
    return models;
}

// ─── Filtres ──────────────────────────────────────────────────
function renderCatalogFilters() {
    const container = document.getElementById('catalog-filters');
    if (!container) return;

    const filters = [
        { key: 'all', label: 'Tous' },
        { key: 'local', label: '🖥️ Local' },
        { key: 'free-api', label: '🆓 Gratuit' },
        { key: 'cli-gemini-adv', label: '💻 Gemini Adv.' },
        { key: 'cli-claude-pro', label: '💻 Claude Pro' },
        { key: 'paid-deepseek', label: '🐋 DeepSeek' },
        { key: 'paid-gcp', label: '💳 GCP Payant' },
        { key: 'media', label: '🎨 Médias' }
    ];

    container.innerHTML = filters.map(f =>
        `<span class="filter-chip ${f.key === catalogFilter ? 'active' : ''}" onclick="filterByChannel('${f.key}')">${f.label}</span>`
    ).join('');
}

function filterByChannel(channelKey) {
    catalogFilter = channelKey;
    renderCatalogFilters();
    renderModelCards();
}

// ─── Cartes modèles ──────────────────────────────────────────
function renderModelCards() {
    const container = document.getElementById('model-cards-grid');
    if (!container) return;

    // Collecter les modèles non-null, filtrés
    const entries = [];
    for (const [id, entry] of Object.entries(MODEL_CATALOG)) {
        if (!entry) continue;
        if (catalogFilter !== 'all' && entry.channel !== catalogFilter) continue;
        entries.push({ id, ...entry });
    }

    // Tri : gratuit d'abord, puis abo, puis payant
    const channelOrder = { 'local': 0, 'free-api': 1, 'cli-gemini-adv': 2, 'cli-claude-pro': 3, 'paid-deepseek': 4, 'paid-gcp': 5, 'media': 6 };
    entries.sort((a, b) => (channelOrder[a.channel] || 9) - (channelOrder[b.channel] || 9));

    if (entries.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="empty-state__icon">🔍</div><div>Aucun modèle pour ce filtre.</div></div>';
        return;
    }

    container.innerHTML = entries.map(entry => {
        const chInfo = CHANNELS[entry.channel] || {};
        const accentColor = chInfo.color || 'var(--accent-primary)';

        // Benchmark principal (le meilleur score)
        let mainBenchHtml = '';
        const benchKeys = Object.keys(entry.benchmarks || {});
        if (benchKeys.length > 0) {
            mainBenchHtml = benchKeys.slice(0, 2).map(key => {
                const bm = entry.benchmarks[key];
                if (!bm || bm.score == null) return '';
                const pct = bm.unit === '%' ? bm.score : Math.min(100, (bm.score / 4000) * 100);
                const color = pct >= 90 ? 'var(--success)' : pct >= 70 ? 'var(--warning)' : 'var(--error)';
                return `
                    <div class="model-card__bench-item">
                        <span style="width:70px;color:var(--text-muted);flex-shrink:0;">${key}</span>
                        <div class="model-card__bench-bar">
                            <div class="model-card__bench-fill" style="width:${pct}%;background:${color};"></div>
                        </div>
                        <span style="font-weight:700;color:${color};min-width:48px;text-align:right;">${bm.score}${bm.unit} ${bm.rank || ''}</span>
                    </div>
                `;
            }).join('');
        }

        // Coût
        const p = entry.pricing || {};
        let costLabel = '';
        if (p.type === 'local' || p.type === 'free') costLabel = '<span style="color:var(--success);font-weight:800;">GRATUIT</span>';
        else if (p.type === 'subscription') costLabel = `<span style="color:var(--accent-primary);">~${(p.amortizedPerM || 0).toFixed(2)} $/M</span>`;
        else costLabel = `<span>${p.inputPerM || 0} ${p.currency || '$'}/M in</span>`;

        // Perf
        const perfHtml = entry.perf
            ? `<span style="color:var(--text-muted);font-size:0.65rem;">${entry.perf.ttft || ''} · ${entry.perf.throughput || ''}</span>`
            : '';

        return `
            <div class="model-card" style="--card-accent:${accentColor};" onclick="openModelModal('${entry.id}')"
                 onmousemove="this.style.setProperty('--mouse-x', (event.offsetX/this.offsetWidth*100)+'%'); this.style.setProperty('--mouse-y', (event.offsetY/this.offsetHeight*100)+'%');">
                <div class="model-card__header">
                    <div>
                        <div style="display:flex;align-items:center;gap:var(--space-sm);">
                            <span class="model-card__icon">${entry.icon}</span>
                            <div>
                                <div class="model-card__name">${entry.title}</div>
                                <div class="model-card__family">${entry.family}</div>
                            </div>
                        </div>
                    </div>
                    ${getChannelBadgeHtml(entry.channel)}
                </div>
                <div style="font-size:0.75rem;color:var(--text-secondary);line-height:1.4;">${entry.desc || ''}</div>
                ${mainBenchHtml ? `<div class="model-card__bench">${mainBenchHtml}</div>` : ''}
                <div class="model-card__footer">
                    <div class="model-card__cost">${costLabel}</div>
                    ${perfHtml}
                </div>
            </div>
        `;
    }).join('');
}

// ─── Modale détail ────────────────────────────────────────────
function openModelModal(modelId) {
    const entry = getModelEntry(modelId);
    if (!entry) return;

    const overlay = document.getElementById('model-modal-overlay');
    const content = document.getElementById('model-modal-content');
    if (!overlay || !content) return;

    const chInfo = CHANNELS[entry.channel] || {};
    const p = entry.pricing || {};

    // Benchmarks section
    let benchHtml = '';
    if (entry.benchmarks && Object.keys(entry.benchmarks).length > 0) {
        benchHtml = `
            <div class="model-modal__section">
                <div class="model-modal__section-title">📊 Benchmarks & Performances</div>
                <div class="model-modal__benchmarks">
                    ${Object.entries(entry.benchmarks).map(([name, bm]) => {
                        if (!bm || bm.score == null) return '';
                        const pct = bm.unit === '%' ? bm.score : Math.min(100, (bm.score / 4000) * 100);
                        const color = pct >= 90 ? 'var(--success)' : pct >= 70 ? 'var(--warning)' : 'var(--error)';
                        return `
                            <div class="modal-bench-row">
                                <span class="modal-bench-label">${name}</span>
                                <div class="modal-bench-bar-wrap">
                                    <div class="modal-bench-bar-fill" style="width:${pct}%;background:${color};"></div>
                                </div>
                                <span class="modal-bench-value" style="color:${color};">${bm.score}${bm.unit} ${bm.rank || ''}</span>
                            </div>
                            ${bm.desc ? `<div style="font-size:0.65rem;color:var(--text-muted);margin-left:114px;margin-top:-0.2rem;">${bm.desc}</div>` : ''}
                        `;
                    }).join('')}
                </div>
            </div>
        `;
    }

    // Performance section
    let perfHtml = '';
    if (entry.perf) {
        const ctxM = entry.perf.contextWindow ? (entry.perf.contextWindow / 1000000).toFixed(1).replace('.0','') + 'M' : '–';
        const effM = entry.perf.effectiveContext ? (entry.perf.effectiveContext / 1000000).toFixed(1).replace('.0','') + 'M' : '–';
        perfHtml = `
            <div class="model-modal__section">
                <div class="model-modal__section-title">⚡ Performances d'Inférence</div>
                <div class="model-modal__pricing-grid">
                    <div class="pricing-cell">
                        <span class="pricing-cell__label">TTFT</span>
                        <span class="pricing-cell__value" style="color:var(--accent-primary);">${entry.perf.ttft || '–'}</span>
                    </div>
                    <div class="pricing-cell">
                        <span class="pricing-cell__label">Débit</span>
                        <span class="pricing-cell__value" style="color:var(--accent-secondary);">${entry.perf.throughput || '–'}</span>
                    </div>
                    <div class="pricing-cell">
                        <span class="pricing-cell__label">Contexte</span>
                        <span class="pricing-cell__value">${ctxM}</span>
                    </div>
                    <div class="pricing-cell">
                        <span class="pricing-cell__label">Effectif</span>
                        <span class="pricing-cell__value" style="color:${ctxM === effM ? 'var(--success)' : 'var(--warning)'};">${effM}</span>
                    </div>
                </div>
            </div>
        `;
    }

    // Pricing section
    let pricingHtml = '';
    let costDisplay = '';
    if (p.type === 'local' || p.type === 'free') {
        costDisplay = '<span style="color:var(--success);font-size:1.4rem;font-weight:800;">GRATUIT</span>';
    } else if (p.type === 'subscription') {
        costDisplay = `<span style="color:var(--accent-primary);font-size:1.1rem;font-weight:700;">Forfait · ~${(p.amortizedPerM || 0).toFixed(2)} $/M amorti</span>`;
    } else {
        costDisplay = `<span style="font-family:var(--font-mono);font-size:1.1rem;">${p.inputPerM} ${p.currency}/M in · ${p.outputPerM} ${p.currency}/M out</span>`;
    }

    pricingHtml = `
        <div class="model-modal__section">
            <div class="model-modal__section-title">💰 Tarification</div>
            <div style="display:flex;flex-direction:column;gap:var(--space-sm);">
                <div>${costDisplay}</div>
                ${p.note ? `<div style="font-size:0.75rem;color:var(--text-muted);">${p.note}</div>` : ''}
            </div>
        </div>
    `;

    // Strengths / Weaknesses
    let tagsHtml = '';
    if ((entry.strengths && entry.strengths.length) || (entry.weaknesses && entry.weaknesses.length)) {
        tagsHtml = `
            <div class="model-modal__section">
                <div class="model-modal__section-title">🎯 Forces & Faiblesses</div>
                <div class="model-modal__tags">
                    ${(entry.strengths || []).map(s => `<span class="tag-strength">✅ ${s}</span>`).join('')}
                    ${(entry.weaknesses || []).map(w => `<span class="tag-weakness">⚠️ ${w}</span>`).join('')}
                </div>
                ${entry.bestFor ? `<div style="font-size:0.78rem;color:var(--text-secondary);margin-top:var(--space-sm);"><strong style="color:var(--success);">Idéal pour :</strong> ${entry.bestFor}</div>` : ''}
                ${entry.hallucination_risk ? `<div style="font-size:0.72rem;color:var(--text-muted);margin-top:0.2rem;">Risque d'hallucination : <strong>${entry.hallucination_risk}</strong></div>` : ''}
            </div>
        `;
    }

    // Profil cognitif
    let cogHtml = '';
    if (entry.cognitiveProfile) {
        cogHtml = `
            <div class="model-modal__section">
                <div class="model-modal__section-title">🧠 Profil Cognitif</div>
                <div style="font-size:0.82rem;color:var(--text-secondary);line-height:1.6;background:rgba(255,255,255,0.02);border:1px solid var(--border-color);border-radius:var(--radius-md);padding:var(--space-lg);">
                    ${entry.cognitiveProfile}
                </div>
            </div>
        `;
    }

    content.innerHTML = `
        <button class="model-modal__close" onclick="closeModelModal()">&times;</button>
        <div class="model-modal__header">
            <span class="model-modal__icon">${entry.icon}</span>
            <div class="model-modal__title-block">
                <div class="model-modal__title">${entry.title}</div>
                <div class="model-modal__subtitle">
                    ${getChannelBadgeHtml(entry.channel)}
                    <span style="margin-left:var(--space-sm);font-size:0.75rem;color:var(--text-muted);">
                        ${entry.confidential ? '🔒 Confidentiel' : '⚠️ Non confidentiel'}
                    </span>
                </div>
            </div>
        </div>
        ${benchHtml}
        ${perfHtml}
        ${pricingHtml}
        ${tagsHtml}
        ${cogHtml}
    `;

    overlay.classList.add('visible');

    // Animer les barres de benchmarks après ouverture
    requestAnimationFrame(() => {
        content.querySelectorAll('.modal-bench-bar-fill').forEach(bar => {
            bar.style.width = bar.style.width; // trigger reflow
        });
    });
}

function closeModelModal(event) {
    if (event && event.target !== event.currentTarget) return;
    const overlay = document.getElementById('model-modal-overlay');
    if (overlay) overlay.classList.remove('visible');
}

// Fermer avec Escape
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModelModal();
});

// ─── Matrice de comparaison ───────────────────────────────────
let matrixSortKey = 'channel';
let matrixSortAsc = true;

function renderComparisonMatrix() {
    const table = document.getElementById('comparison-matrix');
    if (!table) return;

    // Collecter les données
    const rows = [];
    for (const [id, entry] of Object.entries(MODEL_CATALOG)) {
        if (!entry) continue;
        const p = entry.pricing || {};
        const perf = entry.perf || {};

        // Coût effectif pour le tri
        let effectiveCost = 0;
        if (p.type === 'subscription') effectiveCost = p.amortizedPerM || 0;
        else if (p.type === 'payg') effectiveCost = (p.inputPerM || 0) + (p.outputPerM || 0);

        // Benchmark principal
        let mainBench = null;
        let mainBenchScore = 0;
        for (const [name, bm] of Object.entries(entry.benchmarks || {})) {
            if (bm && bm.score != null && bm.unit === '%') {
                if (bm.score > mainBenchScore) {
                    mainBench = { name, ...bm };
                    mainBenchScore = bm.score;
                }
            }
        }

        rows.push({
            id, entry,
            name: entry.title,
            channel: entry.channel,
            channelLabel: (CHANNELS[entry.channel] || {}).label || '',
            channelOrder: ['local','free-api','cli-gemini-adv','cli-claude-pro','paid-deepseek','paid-gcp','media'].indexOf(entry.channel),
            context: perf.contextWindow || 0,
            effectiveCtx: perf.effectiveContext || 0,
            ttft: perf.ttft || '–',
            throughput: perf.throughput || '–',
            costIn: p.inputPerM || 0,
            costOut: p.outputPerM || 0,
            effectiveCost,
            currency: p.currency || 'USD',
            mainBench,
            confidential: entry.confidential
        });
    }

    // Tri
    rows.sort((a, b) => {
        let va, vb;
        switch (matrixSortKey) {
            case 'name': va = a.name; vb = b.name; break;
            case 'channel': va = a.channelOrder; vb = b.channelOrder; break;
            case 'context': va = a.context; vb = b.context; break;
            case 'cost': va = a.effectiveCost; vb = b.effectiveCost; break;
            case 'bench': va = a.mainBench?.score || 0; vb = b.mainBench?.score || 0; break;
            default: va = a.channelOrder; vb = b.channelOrder;
        }
        if (typeof va === 'string') return matrixSortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
        return matrixSortAsc ? va - vb : vb - va;
    });

    // Trouver le meilleur rapport qualité/prix (bench le plus haut / coût le plus bas)
    let bestRatioId = null;
    let bestRatio = -1;
    rows.forEach(r => {
        if (r.mainBench && r.effectiveCost >= 0) {
            const ratio = r.mainBench.score / (r.effectiveCost + 0.01); // +0.01 pour éviter div/0
            if (ratio > bestRatio) { bestRatio = ratio; bestRatioId = r.id; }
        }
    });

    const sortIcon = (key) => matrixSortKey === key ? (matrixSortAsc ? ' ▲' : ' ▼') : '';

    table.innerHTML = `
        <thead>
            <tr>
                <th onclick="sortMatrix('name')">Modèle${sortIcon('name')}</th>
                <th onclick="sortMatrix('channel')">Canal${sortIcon('channel')}</th>
                <th onclick="sortMatrix('context')" style="text-align:right;">Contexte${sortIcon('context')}</th>
                <th>TTFT</th>
                <th>Débit</th>
                <th onclick="sortMatrix('cost')" style="text-align:right;">Coût In/M${sortIcon('cost')}</th>
                <th style="text-align:right;">Coût Out/M</th>
                <th onclick="sortMatrix('bench')" style="text-align:right;">Benchmark${sortIcon('bench')}</th>
                <th>🔒</th>
            </tr>
        </thead>
        <tbody>
            ${rows.map(r => {
                const isBest = r.id === bestRatioId;
                const fmtCtx = r.context >= 1000000 ? (r.context / 1000000).toFixed(0) + 'M' : r.context >= 1000 ? (r.context / 1000).toFixed(0) + 'k' : r.context;
                const costColor = r.effectiveCost === 0 ? 'var(--success)' : r.effectiveCost < 1 ? 'var(--accent-primary)' : 'var(--warning)';
                const benchHtml = r.mainBench
                    ? `<span style="font-weight:700;color:var(--success);">${r.mainBench.score}${r.mainBench.unit}</span> <span style="font-size:0.6rem;color:var(--text-muted);">${r.mainBench.name}</span>`
                    : '<span style="color:var(--text-muted);">–</span>';

                return `
                    <tr class="${isBest ? 'best-value' : ''}" onclick="openModelModal('${r.id}')" title="${isBest ? '⭐ Meilleur rapport qualité/prix' : ''}">
                        <td>
                            <div style="display:flex;align-items:center;gap:0.3rem;">
                                <span style="font-size:0.9rem;">${r.entry.icon}</span>
                                <span style="font-weight:600;">${r.name}</span>
                                ${isBest ? '<span style="font-size:0.6rem;background:var(--success);color:#0b0d13;padding:0.05rem 0.3rem;border-radius:3px;font-weight:700;">MEILLEUR</span>' : ''}
                            </div>
                        </td>
                        <td>${getChannelBadgeHtml(r.channel)}</td>
                        <td style="text-align:right;font-family:var(--font-mono);">${fmtCtx}</td>
                        <td style="font-size:0.68rem;">${r.ttft}</td>
                        <td style="font-size:0.68rem;">${r.throughput}</td>
                        <td style="text-align:right;font-family:var(--font-mono);color:${costColor};">${r.costIn === 0 ? 'GRATUIT' : r.costIn.toFixed(2) + ' ' + r.currency}</td>
                        <td style="text-align:right;font-family:var(--font-mono);color:${costColor};">${r.costOut === 0 ? '–' : r.costOut.toFixed(2) + ' ' + r.currency}</td>
                        <td style="text-align:right;">${benchHtml}</td>
                        <td style="text-align:center;">${r.confidential ? '🔒' : '⚠️'}</td>
                    </tr>
                `;
            }).join('')}
        </tbody>
    `;
}

function sortMatrix(key) {
    if (matrixSortKey === key) matrixSortAsc = !matrixSortAsc;
    else { matrixSortKey = key; matrixSortAsc = true; }
    renderComparisonMatrix();
}

/* ============================================================
   Logique fusionnée de models-group.js
   ============================================================ */

let activeModelsSubTab = 'catalog';

function renderModelsGroup(container) {
    container.innerHTML = `
        <!-- ═══ Panneau de synthèse Modèles & Tarifs ═══ -->
        <div class="glass-panel" id="models-summary-panel"
             style="background:linear-gradient(135deg,rgba(139,92,246,0.08),rgba(59,130,246,0.05));
                    border-color:rgba(139,92,246,0.2);margin-bottom:0;">
            <div class="section-title" style="margin-bottom:var(--space-md);">
                <span>⚙️ Registre des Modèles &amp; Providers</span>
                <span class="subtitle" id="models-last-refresh">Chargement...</span>
            </div>

            <!-- KPIs rapides depuis models_registry.db -->
            <div class="grid grid-4" style="gap:var(--space-lg);margin-bottom:var(--space-md);">

                <div class="glass-panel glass-panel--compact" style="text-align:center;">
                    <div style="font-size:1.4rem;margin-bottom:var(--space-xs);">🤖</div>
                    <div style="font-weight:700;font-size:1.4rem;color:var(--accent-primary);" id="models-kpi-total">–</div>
                    <div style="font-size:0.7rem;color:var(--text-muted);">Modèles en catalogue</div>
                </div>

                <div class="glass-panel glass-panel--compact" style="text-align:center;">
                    <div style="font-size:1.4rem;margin-bottom:var(--space-xs);">🏭</div>
                    <div style="font-weight:700;font-size:1.4rem;color:var(--accent-secondary);" id="models-kpi-providers">–</div>
                    <div style="font-size:0.7rem;color:var(--text-muted);">Providers configurés</div>
                </div>

                <div class="glass-panel glass-panel--compact" style="text-align:center;">
                    <div style="font-size:1.4rem;margin-bottom:var(--space-xs);">🔑</div>
                    <div style="font-weight:700;font-size:1.4rem;color:var(--success);" id="models-kpi-keys">–</div>
                    <div style="font-size:0.7rem;color:var(--text-muted);">Clés API actives</div>
                </div>

                <div class="glass-panel glass-panel--compact" style="text-align:center;">
                    <div style="font-size:1.4rem;margin-bottom:var(--space-xs);">📐</div>
                    <div style="font-weight:700;font-size:1.4rem;color:var(--warning);" id="models-kpi-rules">–</div>
                    <div style="font-size:0.7rem;color:var(--text-muted);">Règles de routage</div>
                </div>

            </div>

            <!-- Modèles actifs (tiers courants) -->
            <div id="models-active-tier-bar"
                 style="padding:var(--space-sm) var(--space-md);
                        background:rgba(255,255,255,0.02);border-radius:var(--radius-md);
                        border:1px solid var(--border-color);display:flex;justify-content:space-around;
                        align-items:center;flex-wrap:wrap;gap:var(--space-md);font-size:0.78rem;">
                <div style="display:flex;align-items:center;gap:var(--space-sm);">
                    <span style="color:var(--text-muted);">🧠 Planner :</span>
                    <strong style="color:var(--accent-secondary);" id="models-active-planner">–</strong>
                </div>
                <div style="display:flex;align-items:center;gap:var(--space-sm);">
                    <span style="color:var(--text-muted);">🔧 Executor :</span>
                    <strong style="color:var(--success);" id="models-active-executor">–</strong>
                </div>
                <div style="display:flex;align-items:center;gap:var(--space-sm);">
                    <span style="color:var(--text-muted);">⭐ Expert :</span>
                    <strong style="color:var(--accent-primary);" id="models-active-expert">–</strong>
                </div>
                <div style="display:flex;align-items:center;gap:var(--space-sm);">
                    <span style="color:var(--text-muted);">🏠 HA :</span>
                    <strong style="color:var(--warning);" id="models-active-ha">–</strong>
                </div>
                <button class="btn btn--ghost btn--xs" onclick="loadModelsSummary()">🔄 Actualiser</button>
            </div>
        </div>

        <!-- ═══ Sous-onglets ═══ -->
        <div class="subnav-tabs" data-group="models">
            <button class="subtab-btn ${activeModelsSubTab === 'catalog' ? 'active' : ''}"
                    data-subtab="catalog"
                    data-group="models"
                    onclick="switchModelsSubTab('catalog')">📖 Catalogue</button>
            <button class="subtab-btn ${activeModelsSubTab === 'tiers' ? 'active' : ''}"
                    data-subtab="tiers"
                    data-group="models"
                    onclick="switchModelsSubTab('tiers')">⚙️ Configuration des Tiers</button>
            <button class="subtab-btn ${activeModelsSubTab === 'pricing' ? 'active' : ''}"
                    data-subtab="pricing"
                    data-group="models"
                    onclick="switchModelsSubTab('pricing')">💳 Tarifs &amp; SQLite BDD</button>
        </div>
        <div id="models-subtab-content"></div>
    `;

    // Charger les données en arrière-plan
    loadModelsSummary();

    // Rendre l'onglet correspondant
    switchModelsSubTab(activeModelsSubTab);
}

function switchModelsSubTab(subtabId) {
    activeModelsSubTab = subtabId;

    document.querySelectorAll('.subtab-btn[data-group="models"]').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-subtab') === subtabId);
    });

    const contentDiv = document.getElementById('models-subtab-content');
    if (!contentDiv) return;
    contentDiv.innerHTML = '';

    if (subtabId === 'catalog') {
        renderCatalog(contentDiv);
    } else if (subtabId === 'tiers') {
        renderTiersConfig(contentDiv);
    } else if (subtabId === 'pricing') {
        renderPricing(contentDiv);
    }
}

async function loadModelsSummary() {
    try {
        const [statsRes, configRes] = await Promise.allSettled([
            fetchModelsStats(),
            fetchConfig(),
        ]);

        const stats = statsRes.status === 'fulfilled' ? statsRes.value : null;
        if (stats) {
            const totalEl     = document.getElementById('models-kpi-total');
            const providersEl = document.getElementById('models-kpi-providers');
            const keysEl      = document.getElementById('models-kpi-keys');
            const rulesEl     = document.getElementById('models-kpi-rules');

            if (totalEl)     totalEl.textContent     = stats.models         ?? stats.active_models     ?? '–';
            if (providersEl) providersEl.textContent = stats.providers       ?? '–';
            if (keysEl)      keysEl.textContent      = stats.api_keys        ?? '–';
            if (rulesEl)     rulesEl.textContent     = stats.routing_rules   ?? '–';
        }

        const config = configRes.status === 'fulfilled' ? configRes.value : null;
        if (config) {
            const plannerEl  = document.getElementById('models-active-planner');
            const executorEl = document.getElementById('models-active-executor');
            const expertEl   = document.getElementById('models-active-expert');
            const haEl       = document.getElementById('models-active-ha');

            if (plannerEl)  plannerEl.textContent  = config.planner_model     || '–';
            if (executorEl) executorEl.textContent = config.executor_model    || '–';
            if (expertEl)   expertEl.textContent   = config.antigravity_model || '–';
            if (haEl)       haEl.textContent       = config.ha_model          || '–';
        }

        const refresh = document.getElementById('models-last-refresh');
        if (refresh) {
            refresh.textContent = `Rafraîchi à ${new Date().toLocaleTimeString('fr-FR')}`;
        }

    } catch (err) {
        console.error('[MODELS SUMMARY] Erreur:', err);
    }
}
