/* ============================================================
   PRICING.JS — Tarifs & Édition Directe SQLite (models_registry.db)
   ============================================================ */

let currentEditingModelId = null;

function renderPricing(container) {
    container.innerHTML = `
        <!-- En-tête stratégique -->
        <div class="glass-panel" style="display:flex;flex-direction:column;gap:var(--space-lg);">
            <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:var(--space-md);">
                <div class="section-title" style="margin-bottom:0;">Configuration & Tarifs des Modèles (SQLite)</div>
            </div>
            <p style="font-size:0.88rem;color:var(--text-secondary);line-height:1.5;margin:0;">
                Les tarifs et priorités de cascade sont persistés en direct dans la base de données <strong>models_registry.db</strong>.
                Le PlannerAgent utilise ces métriques pour équilibrer la qualité et les coûts en temps réel.
            </p>
        </div>

        <!-- Tableau Comparatif et Édition -->
        <div class="glass-panel" style="display:flex;flex-direction:column;gap:var(--space-lg);">
            <div class="section-title" style="color:var(--accent-secondary);margin-bottom:0;">Registre des Modèles & Édition des Tarifs</div>
            <div style="overflow-x:auto;" id="pricing-comparison-table">Chargement de la base SQLite...</div>
        </div>

        <!-- Jauges de Quotas & Forfaits en Temps Réel -->
        <div class="glass-panel" style="display:flex;flex-direction:column;gap:var(--space-xl);">
            <div class="section-title" style="margin-bottom:0;">
                <span>Quotas & Forfaits en Temps Réel</span>
                <span class="subtitle">Mise à jour toutes les 5s</span>
            </div>
            <div class="grid grid-2" id="pricing-quota-gauges"></div>
        </div>

        <!-- Modale d'Édition de Modèle SQLite -->
        <div id="edit-model-modal" class="modal-overlay">
            <div class="glass-panel modal-content" style="max-width:560px; text-align:left;">
                <h3 style="font-family:var(--font-display);font-weight:700;font-size:1.15rem;margin-bottom:var(--space-lg);display:flex;align-items:center;gap:var(--space-sm);">
                    <span style="font-size:1.5rem;">✏️</span> Éditer le Modèle <span id="edit-model-title-id" style="font-family:var(--font-mono);color:var(--accent-primary);"></span>
                </h3>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--space-md);">
                    <div class="form-group">
                        <label class="form-label" for="edit-model-display-name">Nom d'affichage</label>
                        <input type="text" id="edit-model-display-name">
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="edit-model-status">Statut</label>
                        <div class="select-wrap">
                            <select id="edit-model-status">
                                <option value="active">Active (Disponible)</option>
                                <option value="inactive">Inactive (Désactivé)</option>
                            </select>
                        </div>
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="edit-model-tier">Tier</label>
                        <div class="select-wrap">
                            <select id="edit-model-tier">
                                <option value="leger">Léger (Casual/Routine)</option>
                                <option value="moyen">Moyen (Standard)</option>
                                <option value="fort">Fort (Raisonnement/Dev)</option>
                                <option value="automatique">Automatique (Cascade)</option>
                            </select>
                        </div>
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="edit-model-currency">Devise</label>
                        <input type="text" id="edit-model-currency" value="USD">
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="edit-model-cost-input">Coût Input ($/M tokens)</label>
                        <input type="text" id="edit-model-cost-input">
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="edit-model-cost-output">Coût Output ($/M tokens)</label>
                        <input type="text" id="edit-model-cost-output">
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="edit-model-context-input">Limite Contexte Input</label>
                        <input type="number" id="edit-model-context-input">
                    </div>
                    <div class="form-group" style="grid-column: span 2;">
                        <label class="form-label" for="edit-model-notes">Notes / Observations</label>
                        <textarea id="edit-model-notes" rows="2"></textarea>
                    </div>
                </div>
                <div style="display:flex;gap:var(--space-sm);margin-top:var(--space-xl);justify-content:flex-end;">
                    <button class="btn btn--ghost" onclick="closeEditModelModal()">Annuler</button>
                    <button class="btn btn--success" onclick="submitEditModel()">Sauvegarder en BDD</button>
                </div>
            </div>
        </div>
    `;

    loadPricingData();
    initPricingQuotaGauges();
}

/**
 * Charge les modèles SQLite et peuple le tableau
 */
async function loadPricingData() {
    const tableContainer = document.getElementById('pricing-comparison-table');
    if (!tableContainer) return;

    try {
        const [providers, dbData] = await Promise.all([
            fetchProviders(),
            fetchModelsDB()
        ]);
        const models = dbData?.models || [];

        const iconMap = { local: '🖥️', gemini_free: '🆓', gemini_cli: '💻', claude_cli: '💻', deepseek: '🐋', gemini_paid: '💳', cloud_apis: '☁️' };
        
        let rowsHtml = '';
        models.forEach(m => {
            const provider = providers.find(p => p.id === m.provider_id) || { name: m.provider_id };
            const statusClass = m.status === 'active' ? 'color:var(--success);font-weight:600;' : 'color:var(--text-muted);';
            const costIn = m.cost_input_per_m != null ? m.cost_input_per_m.toFixed(2) : '0.00';
            const costOut = m.cost_output_per_m != null ? m.cost_output_per_m.toFixed(2) : '0.00';
            const costHtml = `${costIn} / ${costOut} ${m.currency}`;

            rowsHtml += `
                <tr>
                    <td style="font-weight:600;">${iconMap[m.provider_id] || '📦'} ${provider.name}</td>
                    <td style="font-family:var(--font-mono);font-size:0.75rem;">${m.id}</td>
                    <td><strong>${m.display_name || m.id}</strong></td>
                    <td style="font-family:var(--font-mono);text-align:right;">${m.context_input ? (m.context_input/1000).toFixed(0) + 'k' : '—'}</td>
                    <td style="${statusClass}">${m.status === 'active' ? 'Actif' : 'Inactif'}</td>
                    <td style="font-family:var(--font-mono);text-align:right;font-weight:700;">${costHtml}</td>
                    <td style="text-align:center;">
                        <button class="btn btn--ghost btn--xs" onclick="openEditModelModal('${m.id}')">✏️ Éditer</button>
                    </td>
                </tr>
            `;
        });

        tableContainer.innerHTML = `
            <table class="data-table">
                <thead>
                    <tr>
                        <th>Fournisseur</th>
                        <th>ID Modèle</th>
                        <th>Nom Affichage</th>
                        <th style="text-align:right;">Contexte</th>
                        <th>Statut</th>
                        <th style="text-align:right;">Tarif (In/Out /M)</th>
                        <th style="text-align:center;">Action</th>
                    </tr>
                </thead>
                <tbody>${rowsHtml}</tbody>
            </table>
        `;
    } catch (err) {
        console.error("[Pricing] Erreur de chargement BDD:", err);
        tableContainer.innerHTML = `
            <div class="empty-state">
                <div class="empty-state__icon">⚠️</div>
                <div>Impossible de charger les données SQLite : ${err.message}</div>
            </div>`;
    }
}

/**
 * Ouvre la modale de modification d'un modèle
 */
async function openEditModelModal(modelId) {
    currentEditingModelId = modelId;
    document.getElementById('edit-model-title-id').textContent = modelId;

    try {
        const res = await fetch(`/api/models/${modelId}`);
        if (!res.ok) throw new Error(await res.text());
        const model = await res.json();

        // Remplir les inputs
        document.getElementById('edit-model-display-name').value = model.display_name || '';
        document.getElementById('edit-model-status').value = model.status || 'active';
        document.getElementById('edit-model-tier').value = model.tier || 'leger';
        document.getElementById('edit-model-currency').value = model.currency || 'USD';
        document.getElementById('edit-model-cost-input').value = model.cost_input_per_m ?? '0.0';
        document.getElementById('edit-model-cost-output').value = model.cost_output_per_m ?? '0.0';
        document.getElementById('edit-model-context-input').value = model.context_input ?? '128000';
        document.getElementById('edit-model-notes').value = model.notes || '';

        document.getElementById('edit-model-modal').classList.add('visible');
    } catch (err) {
        showToast('error', `Erreur de chargement du modèle : ${err.message}`);
    }
}

function closeEditModelModal() {
    document.getElementById('edit-model-modal').classList.remove('visible');
    currentEditingModelId = null;
}

/**
 * Soumet les modifications à l'API modèles
 */
async function submitEditModel() {
    if (!currentEditingModelId) return;

    // Récupérer le model pour avoir son provider_id d'origine
    let providerId = 'unknown';
    try {
        const res = await fetch(`/api/models/${currentEditingModelId}`);
        if (res.ok) {
            const m = await res.json();
            providerId = m.provider_id || 'unknown';
        }
    } catch {}

    const payload = {
        id: currentEditingModelId,
        provider_id: providerId,
        display_name: document.getElementById('edit-model-display-name').value.trim(),
        status: document.getElementById('edit-model-status').value,
        tier: document.getElementById('edit-model-tier').value,
        currency: document.getElementById('edit-model-currency').value.trim(),
        cost_input_per_m: parseFloat(document.getElementById('edit-model-cost-input').value) || 0.0,
        cost_output_per_m: parseFloat(document.getElementById('edit-model-cost-output').value) || 0.0,
        context_input: parseInt(document.getElementById('edit-model-context-input').value) || 128000,
        notes: document.getElementById('edit-model-notes').value.trim()
    };

    try {
        const res = await fetch('/api/models/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!res.ok) throw new Error(await res.text());
        
        showToast('success', `Modèle "${currentEditingModelId}" mis à jour en base SQLite.`);
        closeEditModelModal();
        loadPricingData(); // Recharger le tableau

        // Recharger le catalogue pour refléter les changements
        if (typeof initDynamicModelCatalog === 'function') {
            initDynamicModelCatalog();
        }
    } catch (err) {
        showToast('error', 'Erreur de sauvegarde : ' + err.message);
    }
}

/* ============================================================
   JAUGES DE QUOTAS — Délégation à apis-quotas.js V9
   ============================================================ */

function initPricingQuotaGauges() {
    const container = document.getElementById('pricing-quota-gauges');
    if (!container) return;

    // Injecter le rendu enrichi depuis apis-quotas.js
    container.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:var(--space-lg);width:100%;" id="pricing-quota-detail">
            <div class="empty-state" style="padding:var(--space-xl);">
                <div class="empty-state__icon">⏳</div>
                <div>Chargement des quotas en temps réel...</div>
            </div>
        </div>
        <div id="pricing-quota-alerts" style="width:100%;"></div>
    `;

    // Déléguer à la logique V9 de apis-quotas.js
    Promise.all([fetchQuotasSliding(), fetchTokens()])
        .then(([quotas, tokens]) => {
            const detailEl = document.getElementById('pricing-quota-detail');
            const alertsEl = document.getElementById('pricing-quota-alerts');
            if (detailEl && typeof renderDetailedQuotas === 'function') {
                // Créer des conteneurs temporaires pointant vers les bons IDs
                const tempContainer = { innerHTML: '' };
                // Appel direct avec les éléments existants
                const origDetail = document.getElementById('quota-detail-container');
                const origAlerts = document.getElementById('quota-alerts');

                // On crée une div temporaire pour recevoir le rendu
                const tempDiv = document.createElement('div');
                tempDiv.id = 'quota-detail-container';
                tempDiv.style.display = 'none';
                document.body.appendChild(tempDiv);

                const tempAlerts = document.createElement('div');
                tempAlerts.id = 'quota-alerts';
                tempAlerts.style.display = 'none';
                document.body.appendChild(tempAlerts);

                renderDetailedQuotas(quotas, tokens, null);

                // Récupérer le HTML généré et l'injecter
                detailEl.innerHTML = tempDiv.innerHTML;
                if (alertsEl) alertsEl.innerHTML = tempAlerts.innerHTML;

                // Nettoyer les éléments temporaires
                tempDiv.remove();
                tempAlerts.remove();
            }
        })
        .catch(err => console.warn('[Pricing] Quotas:', err));
}

function updatePricingQuotaGauges(quotas) {
    // Re-render si la section est visible (délégation)
    const detail = document.getElementById('pricing-quota-detail');
    if (detail && detail.children.length > 0 && typeof renderDetailedQuotas === 'function') {
        fetchTokens().then(tokens => {
            renderDetailedQuotas(quotas, tokens, null);
        }).catch(() => {});
    }
}


