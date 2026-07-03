/* ============================================================
   CONTEXT.JS — Onglet "📂 Contexte" du HMI V2
   Affiche l'état du ContextLoader 3-Layers avec :
   1. Bandeau KPIs (fichiers, taille, catégories, timestamp)
   2. Grille de catégories avec LED badges et fichiers
   3. Tableau détaillé triable avec preview
   ============================================================ */

// ─── Constantes de design par catégorie ───
const CATEGORY_THEME = {
    core:            { icon: '🧠', label: 'Core',            color: '#818cf8' },
    esphome:         { icon: '⚡', label: 'ESPHome',         color: '#a3e635' },
    home_assistant:  { icon: '🏠', label: 'Home Assistant',  color: '#3b82f6' },
    moteur:          { icon: '⚙️', label: 'Moteur Agents',   color: '#a78bfa' },
    analysis:        { icon: '📊', label: 'Analyse',         color: '#f59e0b' },
    code_generation: { icon: '💻', label: 'Code Generation', color: '#10b981' },
    hardware:        { icon: '🔧', label: 'Hardware',        color: '#d97757' },
};

// ─── État local ───
let _contextData = null;
let _sortColumn = 'path';
let _sortAsc = true;

/**
 * Point d'entrée du rendu de l'onglet Contexte
 * @param {HTMLElement} container — le panneau onglet
 */
async function renderContext(container) {
    container.innerHTML = '<div class="context-loading" style="text-align:center;padding:3rem;color:var(--text-muted);">Chargement du contexte 3-Layers...</div>';

    try {
        _contextData = await fetchContextStatus();
    } catch (err) {
        container.innerHTML = `<div style="color:var(--error);padding:2rem;">❌ Erreur de chargement : ${err.message}</div>`;
        return;
    }

    container.innerHTML = '';
    _renderKPIBanner(container);
    _renderCategoriesGrid(container);
    _renderDetailTable(container);
}


/* ════════════════════════════════════════════════════════════════
   SECTION 1 — Bandeau KPIs
   ════════════════════════════════════════════════════════════════ */
function _renderKPIBanner(container) {
    const d = _contextData;
    const totalKo = (d.total_size_bytes / 1024).toFixed(1);
    const catsActive = d.categories_map ? Object.keys(d.categories_map).length : 0;
    const lastReload = d.timestamp
        ? new Date(d.timestamp * 1000).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
        : '—';

    const kpiRow = document.createElement('div');
    kpiRow.className = 'context-kpi-row';
    kpiRow.innerHTML = `
        <div class="context-kpi-card">
            <span class="kpi-label">Fichiers chargés</span>
            <span class="kpi-value">${d.documents_count}</span>
            <span class="kpi-sub">${d.loaded ? '✅ Indexés en mémoire' : '⏳ Non chargés'}</span>
        </div>
        <div class="context-kpi-card">
            <span class="kpi-label">Taille totale</span>
            <span class="kpi-value">${totalKo} Ko</span>
            <span class="kpi-sub">Limite : ${(d.max_context_chars || 12000).toLocaleString()} chars/injection</span>
        </div>
        <div class="context-kpi-card">
            <span class="kpi-label">Catégories</span>
            <span class="kpi-value">${catsActive}</span>
            <span class="kpi-sub">Domaines de connaissance</span>
        </div>
        <div class="context-kpi-card">
            <span class="kpi-label">Dernier chargement</span>
            <span class="kpi-value" id="ctx-last-reload">${lastReload}</span>
            <span class="kpi-sub">${d.base_path || ''}</span>
        </div>
    `;
    container.appendChild(kpiRow);
}


/* ════════════════════════════════════════════════════════════════
   SECTION 2 — Grille de catégories
   ════════════════════════════════════════════════════════════════ */
function _renderCategoriesGrid(container) {
    const d = _contextData;
    const catMap = d.categories_map || {};

    // Titre de section
    const title = document.createElement('div');
    title.className = 'context-section-title';
    title.innerHTML = '<span class="section-icon">📁</span> Catégories de contexte';
    container.appendChild(title);

    const grid = document.createElement('div');
    grid.className = 'context-categories-grid';

    // Calculer la taille max pour la barre de progression relative
    const catSizes = {};
    for (const [cat, files] of Object.entries(catMap)) {
        catSizes[cat] = files.reduce((total, filePath) => {
            const doc = d.documents.find(doc => doc.path === filePath);
            return total + (doc ? doc.size_bytes : 0);
        }, 0);
    }
    const maxCatSize = Math.max(...Object.values(catSizes), 1);

    for (const [cat, files] of Object.entries(catMap)) {
        const theme = CATEGORY_THEME[cat] || { icon: '📄', label: cat, color: '#818cf8' };
        const loadedInCat = files.filter(fp => d.documents.some(doc => doc.path === fp));
        const allLoaded = loadedInCat.length === files.length;
        const noneLoaded = loadedInCat.length === 0;

        // Déterminer la couleur LED
        let ledClass = 'led-green';
        if (noneLoaded) ledClass = 'led-red';
        else if (!allLoaded) ledClass = 'led-yellow';

        const card = document.createElement('div');
        card.className = 'context-category-card';
        card.style.setProperty('--cat-color', theme.color);

        // Header
        let html = `
            <div class="category-card-header">
                <div class="cat-name-group">
                    <span class="cat-icon">${theme.icon}</span>
                    <span class="cat-name">${theme.label}</span>
                </div>
                <span class="led-badge ${ledClass}" title="${loadedInCat.length}/${files.length} fichiers chargés"></span>
            </div>
        `;

        // Liste des fichiers
        html += '<ul class="category-files-list">';
        for (const filePath of files) {
            const doc = d.documents.find(doc => doc.path === filePath);
            const basename = filePath.split('/').pop();
            if (doc) {
                const sizeStr = doc.size_bytes >= 1024
                    ? `${(doc.size_bytes / 1024).toFixed(1)} Ko`
                    : `${doc.size_bytes} o`;
                html += `
                    <li class="category-file-item">
                        <span class="file-name" title="${filePath}">${basename}</span>
                        <span class="file-status">
                            <span class="file-size">${sizeStr}</span>
                            <span class="file-status-icon">✅</span>
                        </span>
                    </li>
                `;
            } else {
                html += `
                    <li class="category-file-item">
                        <span class="file-name" title="${filePath}" style="color:var(--error);">${basename}</span>
                        <span class="file-status">
                            <span class="file-size">—</span>
                            <span class="file-status-icon">❌</span>
                        </span>
                    </li>
                `;
            }
        }
        html += '</ul>';

        // Barre de progression relative
        const pct = (catSizes[cat] / maxCatSize * 100).toFixed(1);
        html += `
            <div class="category-progress-bar">
                <div class="category-progress-fill" style="width:${pct}%"></div>
            </div>
        `;

        card.innerHTML = html;
        grid.appendChild(card);
    }

    container.appendChild(grid);
}


/* ════════════════════════════════════════════════════════════════
   SECTION 3 — Tableau détaillé triable
   ════════════════════════════════════════════════════════════════ */
function _renderDetailTable(container) {
    const d = _contextData;

    // Titre + toolbar
    const wrapper = document.createElement('div');
    wrapper.className = 'context-table-wrapper';

    const toolbar = document.createElement('div');
    toolbar.className = 'context-table-toolbar';
    toolbar.innerHTML = `
        <span class="toolbar-title">📋 Détail des fichiers indexés (${d.documents.length})</span>
        <div style="display: flex; gap: 0.5rem;">
            <button class="btn-reload" id="btn-ha-ingest" onclick="_handleHAIngest()">
                <span class="reload-icon">🏠</span> Ingestion Entités HA
            </button>
            <button class="btn-reload" id="btn-ctx-reload" onclick="_handleContextReload()">
                <span class="reload-icon">🔄</span> Recharger
            </button>
        </div>
    `;
    wrapper.appendChild(toolbar);

    // Table
    const tableContainer = document.createElement('div');
    tableContainer.style.overflowX = 'auto';
    tableContainer.innerHTML = _buildTableHtml(d.documents);
    wrapper.appendChild(tableContainer);

    container.appendChild(wrapper);
}

function _buildTableHtml(docs) {
    // Tri
    const sorted = [...docs].sort((a, b) => {
        let valA, valB;
        switch (_sortColumn) {
            case 'path': valA = a.path; valB = b.path; break;
            case 'size': valA = a.size_bytes; valB = b.size_bytes; break;
            case 'modified': valA = a.last_modified; valB = b.last_modified; break;
            default: valA = a.path; valB = b.path;
        }
        if (typeof valA === 'string') {
            return _sortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
        }
        return _sortAsc ? valA - valB : valB - valA;
    });

    const arrow = (col) => {
        if (_sortColumn !== col) return '<span class="sort-arrow">↕</span>';
        return `<span class="sort-arrow active">${_sortAsc ? '↑' : '↓'}</span>`;
    };

    let html = `
        <table class="context-detail-table">
            <thead>
                <tr>
                    <th onclick="_sortContextTable('path')">Chemin ${arrow('path')}</th>
                    <th onclick="_sortContextTable('categories')">Catégories</th>
                    <th onclick="_sortContextTable('size')">Taille ${arrow('size')}</th>
                    <th onclick="_sortContextTable('modified')">Modifié ${arrow('modified')}</th>
                    <th>Aperçu</th>
                </tr>
            </thead>
            <tbody>
    `;

    for (const doc of sorted) {
        const sizeStr = doc.size_bytes >= 1024
            ? `${(doc.size_bytes / 1024).toFixed(1)} Ko`
            : `${doc.size_bytes} o`;
        const modifiedStr = new Date(doc.last_modified * 1000)
            .toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
        const catBadges = (doc.categories || [])
            .map(c => `<span class="cat-badge" data-cat="${c}">${c}</span>`)
            .join('');
        const preview = (doc.preview || '').split('\n')[0] || '—';

        html += `
            <tr>
                <td class="cell-path">${doc.path}</td>
                <td>${catBadges}</td>
                <td style="font-family:var(--font-mono);font-size:0.72rem;">${sizeStr}</td>
                <td style="font-family:var(--font-mono);font-size:0.72rem;color:var(--text-muted);">${modifiedStr}</td>
                <td class="cell-preview" title="${(doc.preview || '').replace(/"/g, '&quot;')}">${preview}</td>
            </tr>
        `;
    }

    html += '</tbody></table>';
    return html;
}


/* ─── Handlers ─── */

/**
 * Tri du tableau par colonne (toggle asc/desc)
 */
function _sortContextTable(column) {
    if (_sortColumn === column) {
        _sortAsc = !_sortAsc;
    } else {
        _sortColumn = column;
        _sortAsc = true;
    }
    // Re-render uniquement le tableau
    const wrapper = document.querySelector('.context-table-wrapper');
    if (wrapper && _contextData) {
        const tableContainer = wrapper.querySelector('div[style*="overflow"]');
        if (tableContainer) {
            tableContainer.innerHTML = _buildTableHtml(_contextData.documents);
        }
    }
}

/**
 * Rechargement forcé du contexte via l'API
 */
async function _handleContextReload() {
    const btn = document.getElementById('btn-ctx-reload');
    if (btn) btn.classList.add('loading');

    try {
        _contextData = await reloadContext();
        // Mettre à jour le timestamp KPI
        const el = document.getElementById('ctx-last-reload');
        if (el && _contextData.timestamp) {
            el.textContent = new Date(_contextData.timestamp * 1000)
                .toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        }
        // Re-render complet de l'onglet
        const tabContent = document.getElementById('tab-content');
        if (tabContent) {
            const pane = tabContent.querySelector('.tab-pane');
            if (pane) {
                pane.innerHTML = '';
                _renderKPIBanner(pane);
                _renderCategoriesGrid(pane);
                _renderDetailTable(pane);
            }
        }
        // Toast de confirmation (si disponible)
        if (typeof showToast === 'function') {
            showToast('Contexte 3-Layers rechargé avec succès !', 'success');
        }
    } catch (err) {
        if (typeof showToast === 'function') {
            showToast(`Erreur de rechargement : ${err.message}`, 'error');
        }
    } finally {
        if (btn) btn.classList.remove('loading');
    }
}

/**
 * Ingestion des entités Home Assistant dans memory.db
 */
async function _handleHAIngest() {
    const btn = document.getElementById('btn-ha-ingest');
    if (btn) {
        btn.classList.add('loading');
        btn.innerHTML = '<span class="reload-icon">⏳</span> Ingestion...';
    }

    try {
        const res = await ingestHAEntities();
        if (res && res.status === "success") {
            if (typeof showToast === 'function') {
                showToast(res.message, 'success');
            }
        } else {
            throw new Error(res ? res.message : "Erreur inconnue");
        }
    } catch (err) {
        if (typeof showToast === 'function') {
            showToast(`Erreur d'ingestion : ${err.message}`, 'error');
        }
    } finally {
        if (btn) {
            btn.classList.remove('loading');
            btn.innerHTML = '<span class="reload-icon">🏠</span> Ingestion Entités HA';
        }
    }
}
