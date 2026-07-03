/* ============================================================
   TIERS-CONFIG.JS — Configuration des Tiers & Rôles des Agents
   Sources de données : /api/models (BDD), /api/config (JSON)
   Interagit avec : model-tooltip.js (AVAILABLE_MODELS, RECOMMENDED_BY_TIER)
   ============================================================ */

let currentTiersConfig = {};
let _allModelsFromDB = [];  // Cache des modèles chargés depuis la BDD

function renderTiersConfig(container) {
    container.innerHTML = `
        <!-- ═══ Rôles des Agents (sélection du modèle actif par rôle) ═══ -->
        <div class="glass-panel">
            <div class="section-title">
                <span>⚙️ Rôles des Agents — Modèle Actif par Fonction</span>
                <span class="subtitle" id="tiers-roles-save-status" style="color:var(--success);"></span>
            </div>
            <div style="font-size:0.78rem;color:var(--text-muted);margin-bottom:var(--space-md);padding:var(--space-sm) var(--space-md);background:rgba(255,255,255,0.02);border-radius:var(--radius-md);border-left:3px solid var(--accent-primary);">
                Le moteur peut utiliser soit un <strong>nom de tier</strong> (leger/moyen/fort/automatique)
                soit un <strong>identifiant de modèle précis</strong> pour chaque rôle.
            </div>
            <div class="grid grid-4" style="gap:var(--space-xl);">
                <!-- Planner / Orchestrateur -->
                <div class="form-group">
                    <label class="form-label" for="select-planner">
                        🧠 Planner / Orchestrateur
                        <span style="font-size:0.7rem;color:var(--text-muted);font-weight:400;"> — analyse &amp; planification</span>
                    </label>
                    <div class="select-wrap">
                        <select id="select-planner" onchange="onTierRoleChange()">
                            <optgroup label="— Par Tier —">
                                <option value="leger">Tier Léger (Routine / Local)</option>
                                <option value="moyen">Tier Moyen (Intermédiaire)</option>
                                <option value="fort">Tier Fort (Raisonnement / Claude)</option>
                                <option value="automatique">Tier Automatique (Tous modèles)</option>
                            </optgroup>
                            <optgroup label="— Modèle Précis —" id="optgroup-planner-models">
                                <option value="claude-opus-4-8">Claude Opus 4.8 🥇 (SOTA 2026)</option>
                                <option value="claude-opus-4-7">Claude Opus 4.7</option>
                                <option value="claude-sonnet-4-6">Claude Sonnet 4.6</option>
                                <option value="gemini-3.5-flash-high-cli">Gemini 3.5 Flash (CLI)</option>
                                <option value="gemini-3.5-flash">Gemini 3.5 Flash (Gratuit)</option>
                                <option value="deepseek-v4-pro">DeepSeek V4 Pro</option>
                            </optgroup>
                        </select>
                    </div>
                    <div style="font-size:0.68rem;color:var(--text-muted);margin-top:4px;">
                        Actuel : <strong id="planner-model-detail" style="color:var(--accent-primary);">–</strong>
                    </div>
                </div>

                <!-- Executor / Outils -->
                <div class="form-group">
                    <label class="form-label" for="select-executor">
                        🔧 Executor / Outils
                        <span style="font-size:0.7rem;color:var(--text-muted);font-weight:400;"> — exécution &amp; outils</span>
                    </label>
                    <div class="select-wrap">
                        <select id="select-executor" onchange="onTierRoleChange()">
                            <optgroup label="— Par Tier —">
                                <option value="leger">Tier Léger (Routine / Local)</option>
                                <option value="moyen">Tier Moyen (Intermédiaire)</option>
                                <option value="fort">Tier Fort (Raisonnement / Claude)</option>
                                <option value="automatique">Tier Automatique (Tous modèles)</option>
                            </optgroup>
                            <optgroup label="— Modèle Précis —" id="optgroup-executor-models">
                                <option value="claude-opus-4-8">Claude Opus 4.8 🥇 (SOTA 2026)</option>
                                <option value="claude-sonnet-4-6">Claude Sonnet 4.6</option>
                                <option value="claude-haiku-4-5">Claude Haiku 4.5 (rapide)</option>
                                <option value="gemini-3.5-flash-high-cli">Gemini 3.5 Flash (CLI)</option>
                                <option value="gemini-3.5-flash">Gemini 3.5 Flash (Gratuit)</option>
                                <option value="deepseek-v4-flash">DeepSeek V4 Flash</option>
                            </optgroup>
                        </select>
                    </div>
                    <div style="font-size:0.68rem;color:var(--text-muted);margin-top:4px;">
                        Actuel : <strong id="executor-model-detail" style="color:var(--success);">–</strong>
                    </div>
                </div>

                <!-- Expert / Antigravity -->
                <div class="form-group">
                    <label class="form-label" for="select-expert">
                        ⭐ Expert / Antigravity
                        <span style="font-size:0.7rem;color:var(--text-muted);font-weight:400;"> — tâches complexes</span>
                    </label>
                    <div class="select-wrap">
                        <select id="select-expert" onchange="onTierRoleChange()">
                            <optgroup label="— Par Tier —">
                                <option value="leger">Tier Léger (Routine / Local)</option>
                                <option value="moyen">Tier Moyen (Intermédiaire)</option>
                                <option value="fort">Tier Fort (Raisonnement / Claude)</option>
                                <option value="automatique">Tier Automatique (Tous modèles)</option>
                            </optgroup>
                            <optgroup label="— Modèle Précis —" id="optgroup-expert-models">
                                <option value="claude-opus-4-8">Claude Opus 4.8 🥇 (SOTA 2026)</option>
                                <option value="claude-opus-4-7">Claude Opus 4.7</option>
                                <option value="claude-sonnet-4-6">Claude Sonnet 4.6</option>
                                <option value="gemini-3.1-pro-preview-paid">Gemini 3.1 Pro Preview</option>
                                <option value="deepseek-v4-pro">DeepSeek V4 Pro</option>
                            </optgroup>
                        </select>
                    </div>
                    <div style="font-size:0.68rem;color:var(--text-muted);margin-top:4px;">
                        Actuel : <strong id="expert-model-detail" style="color:var(--accent-secondary);">–</strong>
                    </div>
                </div>

                <!-- HA Agent -->
                <div class="form-group">
                    <label class="form-label" for="select-ha">
                        🏠 HA Agent
                        <span style="font-size:0.7rem;color:var(--text-muted);font-weight:400;"> — domotique</span>
                    </label>
                    <div class="select-wrap">
                        <select id="select-ha" onchange="onTierRoleChange()">
                            <optgroup label="— Par Tier —">
                                <option value="leger">Tier Léger (Routine / Local)</option>
                                <option value="moyen">Tier Moyen (Intermédiaire)</option>
                                <option value="fort">Tier Fort (Raisonnement / Claude)</option>
                                <option value="automatique">Tier Automatique (Tous modèles)</option>
                            </optgroup>
                            <optgroup label="— Modèle Précis —">
                                <option value="gemini-3.5-flash">Gemini 3.5 Flash (Gratuit)</option>
                                <option value="gemini-3.5-flash-high-cli">Gemini 3.5 Flash (CLI)</option>
                                <option value="claude-sonnet-4-6">Claude Sonnet 4.6</option>
                                <option value="deepseek-v4-flash">DeepSeek V4 Flash</option>
                            </optgroup>
                        </select>
                    </div>
                    <div style="font-size:0.68rem;color:var(--text-muted);margin-top:4px;">
                        Actuel : <strong id="ha-model-detail" style="color:var(--warning);">–</strong>
                    </div>
                </div>
            </div>
        </div>

        <!-- ═══ Sélection Multi-Modèles par Tier ═══ -->
        <div class="glass-panel">
            <div class="section-title">
                <span>📋 Modèles disponibles par Tier</span>
                <span class="subtitle" id="tiers-models-count">Chargement de la BDD...</span>
            </div>
            <div style="font-size:0.78rem;color:var(--text-muted);margin-bottom:var(--space-md);padding:var(--space-sm) var(--space-md);background:rgba(255,255,255,0.02);border-radius:var(--radius-md);border-left:3px solid var(--success);">
                Cochez les modèles autorisés dans chaque tier. Les modèles
                <span style="background:rgba(129,140,248,0.15);padding:1px 5px;border-radius:3px;font-size:0.7rem;font-weight:700;">CONSEILLÉ</span>
                sont recommandés selon l'usage courant.
            </div>
            <div class="grid grid-4" style="gap:var(--space-xl);">

                <!-- Léger -->
                <div style="border:1px solid rgba(129,140,248,0.25);border-radius:var(--radius-lg);padding:var(--space-xl);background:rgba(129,140,248,0.03);">
                    <div style="font-weight:700;font-size:1rem;color:var(--accent-primary);margin-bottom:0.2rem;">⚡ Tier Léger</div>
                    <div style="font-size:0.72rem;color:var(--text-muted);margin-bottom:var(--space-md);">Tâches simples, locales, haute fréquence.</div>
                    <div id="checkboxes-leger" style="display:flex;flex-direction:column;gap:0.3rem;max-height:420px;overflow-y:auto;"></div>
                </div>

                <!-- Moyen -->
                <div style="border:1px solid rgba(16,185,129,0.25);border-radius:var(--radius-lg);padding:var(--space-xl);background:rgba(16,185,129,0.03);">
                    <div style="font-weight:700;font-size:1rem;color:var(--success);margin-bottom:0.2rem;">🔹 Tier Moyen</div>
                    <div style="font-size:0.72rem;color:var(--text-muted);margin-bottom:var(--space-md);">Analyse standard, tâches quotidiennes.</div>
                    <div id="checkboxes-moyen" style="display:flex;flex-direction:column;gap:0.3rem;max-height:420px;overflow-y:auto;"></div>
                </div>

                <!-- Fort -->
                <div style="border:1px solid rgba(167,139,250,0.25);border-radius:var(--radius-lg);padding:var(--space-xl);background:rgba(167,139,250,0.03);">
                    <div style="font-weight:700;font-size:1rem;color:var(--accent-secondary);margin-bottom:0.2rem;">🔥 Tier Fort</div>
                    <div style="font-size:0.72rem;color:var(--text-muted);margin-bottom:var(--space-md);">Raisonnement logique, codage expert.</div>
                    <div id="checkboxes-fort" style="display:flex;flex-direction:column;gap:0.3rem;max-height:420px;overflow-y:auto;"></div>
                </div>

                <!-- Automatique -->
                <div style="border:1px solid rgba(245,158,11,0.25);border-radius:var(--radius-lg);padding:var(--space-xl);background:rgba(245,158,11,0.03);">
                    <div style="font-weight:700;font-size:1rem;color:var(--warning);margin-bottom:0.2rem;">🤖 Tier Automatique</div>
                    <div style="font-size:0.72rem;color:var(--text-muted);margin-bottom:var(--space-md);">Sélection dynamique selon la tâche.</div>
                    <div id="checkboxes-automatique" style="display:flex;flex-direction:column;gap:0.3rem;max-height:420px;overflow-y:auto;"></div>
                </div>

            </div>
        </div>
    `;

    loadTiersConfig();
}

/**
 * Charge la config actuelle et les modèles disponibles depuis la BDD
 */
async function loadTiersConfig() {
    try {
        // Chargement parallèle : config + modèles BDD
        const [config, dbData] = await Promise.all([
            fetchConfig(),
            fetchModelsDB().catch(() => null)
        ]);

        // Mettre à jour AVAILABLE_MODELS dynamiquement depuis la BDD
        if (dbData && dbData.models && dbData.models.length > 0) {
            _allModelsFromDB = dbData.models;
            _refreshAvailableModelsFromDB(dbData.models);
        }

        if (config) {
            // Peupler les selects (tiers ou modèles précis)
            _setSelectValue('select-planner', config.planner_model);
            _setSelectValue('select-executor', config.executor_model);
            _setSelectValue('select-expert',   config.antigravity_model);
            _setSelectValue('select-ha',       config.ha_model);

            // Afficher les détails de chaque rôle
            _updateRoleDetail('planner-model-detail', config.planner_model);
            _updateRoleDetail('executor-model-detail', config.executor_model);
            _updateRoleDetail('expert-model-detail',   config.antigravity_model);
            _updateRoleDetail('ha-model-detail',       config.ha_model);

            // Peupler les checkboxes depuis la config.tiers
            currentTiersConfig = config.tiers || {};
            renderTierCheckboxes();

            // Mettre à jour les badges header
            const bp = document.getElementById('badge-planner');
            const be = document.getElementById('badge-executor');
            const bx = document.getElementById('badge-expert');
            if (bp) bp.innerText = config.planner_model || '–';
            if (be) be.innerText = config.executor_model || '–';
            if (bx) bx.innerText = config.antigravity_model || '–';
        }

        // Stats d'affichage
        const countEl = document.getElementById('tiers-models-count');
        if (countEl) {
            countEl.textContent = `${AVAILABLE_MODELS.length} modèles disponibles · BDD synchronisée`;
        }

    } catch (err) {
        console.error('[Tiers] Erreur de chargement:', err);
    }
}

/**
 * Met à jour la liste AVAILABLE_MODELS globale avec les modèles frais de la BDD
 * Préserve l'ordre par catégorie (abonnements en tête)
 */
function _refreshAvailableModelsFromDB(dbModels) {
    // Regroupement par catégorie depuis les données BDD
    const categoryOrder = [
        'claude_cli', 'gemini_cli',
        'gemini_free', 'local',
        'deepseek', 'cerebras',
        'openrouter', 'gemini_paid', 'mistral', 'cohere'
    ];

    const categoryLabels = {
        claude_cli:   '[ABO CLI] Claude',
        gemini_cli:   '[ABO CLI] Gemini',
        gemini_free:  '[GRATUIT API] Gemini',
        local:        '[LOCAL] LM Studio',
        deepseek:     '[PAYANT] DeepSeek',
        cerebras:     '[GRATUIT] Cerebras',
        openrouter:   '[GRATUIT] OpenRouter',
        gemini_paid:  '[PAYANT GCP] Gemini',
        mistral:      '[GRATUIT] Mistral',
        cohere:       '[GRATUIT] Cohere',
    };

    // Vider et reconstruire AVAILABLE_MODELS depuis la BDD (source de vérité)
    AVAILABLE_MODELS.length = 0;
    const grouped = {};
    dbModels.forEach(m => {
        if (!grouped[m.provider_id]) grouped[m.provider_id] = [];
        grouped[m.provider_id].push(m);
    });

    categoryOrder.forEach(prov => {
        const models = grouped[prov] || [];
        models.forEach(m => {
            const prefix = categoryLabels[prov] || `[${prov}]`;
            // Mise en valeur des modèles spéciaux
            let marker = '';
            if (m.id === 'claude-opus-4-8') marker = ' 🥇 (SOTA 2026)';
            else if (m.id.includes('claude-opus-4-7')) marker = ' ⭐';
            AVAILABLE_MODELS.push({
                id: m.id,
                name: `${prefix} ${m.display_name || m.id}${marker}`
            });
        });
    });
}

/**
 * Définit la valeur d'un select — accepte un tier (leger/moyen/fort/automatique)
 * OU un ID de modèle précis. Ajoute l'option dynamiquement si elle n'existe pas.
 */
function _setSelectValue(selectId, value) {
    const sel = document.getElementById(selectId);
    if (!sel || !value) return;

    // Chercher dans les options existantes
    const existing = Array.from(sel.options).find(o => o.value === value);
    if (existing) {
        sel.value = value;
        return;
    }

    // Ajouter l'option manquante dans le groupe "Modèle Précis"
    const optgroupId = `optgroup-${selectId.replace('select-', '')}-models`;
    const og = document.getElementById(optgroupId)
        || sel.querySelector('optgroup:last-child')
        || sel;
    const opt = document.createElement('option');
    opt.value = value;
    // Chercher le display_name dans la BDD
    const dbModel = _allModelsFromDB.find(m => m.id === value);
    opt.text = dbModel ? dbModel.display_name : value;
    opt.selected = true;
    og.appendChild(opt);
    sel.value = value;
}

/**
 * Met à jour le libellé de détail sous chaque select de rôle
 */
function _updateRoleDetail(elementId, value) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const dbModel = _allModelsFromDB.find(m => m.id === value);
    if (dbModel) {
        el.textContent = dbModel.display_name;
    } else if (value) {
        // Si c'est un tier, afficher la liste des modèles associés
        const tierModels = currentTiersConfig[value] || [];
        if (tierModels.length > 0) {
            el.textContent = `Tier ${value} (${tierModels.length} modèles)`;
        } else {
            el.textContent = value;
        }
    } else {
        el.textContent = '–';
    }
}

/**
 * Rend les checkboxes de tous les tiers avec groupement par catégorie
 */
function renderTierCheckboxes() {
    const tiers = ['leger', 'moyen', 'fort', 'automatique'];
    tiers.forEach(tier => {
        const container = document.getElementById(`checkboxes-${tier}`);
        if (!container) return;

        const checkedModels = currentTiersConfig[tier] || [];
        const recommended = RECOMMENDED_BY_TIER[tier] || [];

        // Trier : recommandés d'abord, puis alphabétique dans chaque groupe
        const sortedModels = [...AVAILABLE_MODELS].sort((a, b) => {
            const aRec = recommended.includes(a.id);
            const bRec = recommended.includes(b.id);
            if (aRec && !bRec) return -1;
            if (!aRec && bRec) return 1;
            return 0;
        });

        let html = '';
        let lastCategory = null;

        sortedModels.forEach(model => {
            const isChecked = checkedModels.includes(model.id) ? 'checked' : '';
            const isRec = recommended.includes(model.id);

            // Séparateur de catégorie si la préfixe change
            const cat = model.name.match(/^\[([^\]]+)\]/)?.[1] || '';
            if (cat !== lastCategory) {
                lastCategory = cat;
                html += `<div style="font-size:0.6rem;font-weight:700;color:var(--text-muted);letter-spacing:0.05em;margin-top:${html ? 'var(--space-md)' : '0'};margin-bottom:2px;padding-bottom:2px;border-bottom:1px solid var(--border-color);">${cat}</div>`;
            }

            let itemStyle = '';
            let badgeHtml = '';
            if (isRec) {
                const color = TIER_COLORS[tier];
                const bg = TIER_BG_COLORS[tier];
                itemStyle = `border:1px solid ${color};background:${bg};`;
                badgeHtml = `<span style="font-size:0.55rem;background:${color};color:#0b0d13;padding:1px 4px;border-radius:2px;white-space:nowrap;font-weight:700;margin-left:auto;flex-shrink:0;">CONSEILLÉ</span>`;
            }

            // Nom sans le préfixe de catégorie (plus lisible dans le checkbox)
            const cleanName = model.name.replace(/^\[[^\]]+\]\s*/, '');

            html += `
                <label class="checkbox-item" style="${itemStyle}cursor:pointer;">
                    <input type="checkbox" data-tier="${tier}" data-model="${model.id}" ${isChecked} onchange="onTierCheckboxChange()">
                    <div class="tooltip-wrap" style="flex:1;display:flex;align-items:center;min-width:0;gap:var(--space-xs);">
                        <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.78rem;${isRec ? 'color:var(--text-primary);font-weight:600;' : 'color:var(--text-secondary);'}">${cleanName}</span>
                        ${typeof getModelTooltipOnly === 'function' ? getModelTooltipOnly(model.id) : ''}
                    </div>
                    ${badgeHtml}
                </label>
            `;
        });
        container.innerHTML = html;
    });
}

/**
 * Appelé à chaque changement de checkbox — met à jour currentTiersConfig
 */
function onTierCheckboxChange() {
    const tiers = ['leger', 'moyen', 'fort', 'automatique'];
    tiers.forEach(tier => {
        currentTiersConfig[tier] = [];
        document.querySelectorAll(`input[data-tier="${tier}"]:checked`).forEach(cb => {
            currentTiersConfig[tier].push(cb.getAttribute('data-model'));
        });
    });
    onTierRoleChange();
}

/**
 * Sauvegarde la config dès qu'un rôle ou une checkbox change
 */
async function onTierRoleChange() {
    const config = {
        planner_model:     document.getElementById('select-planner')?.value,
        executor_model:    document.getElementById('select-executor')?.value,
        antigravity_model: document.getElementById('select-expert')?.value,
        ha_model:          document.getElementById('select-ha')?.value,
        tiers: currentTiersConfig
    };

    try {
        await saveConfig(config);

        // Mise à jour des détails
        _updateRoleDetail('planner-model-detail', config.planner_model);
        _updateRoleDetail('executor-model-detail', config.executor_model);
        _updateRoleDetail('expert-model-detail',   config.antigravity_model);
        _updateRoleDetail('ha-model-detail',       config.ha_model);

        // Mise à jour des badges header
        const bp = document.getElementById('badge-planner');
        const be = document.getElementById('badge-executor');
        const bx = document.getElementById('badge-expert');
        if (bp) bp.innerText = config.planner_model;
        if (be) be.innerText = config.executor_model;
        if (bx) bx.innerText = config.antigravity_model;

        // Notification discrète de sauvegarde
        const status = document.getElementById('tiers-roles-save-status');
        if (status) {
            status.textContent = '✅ Sauvegardé';
            setTimeout(() => { status.textContent = ''; }, 2000);
        }
    } catch (err) {
        console.error('[Tiers] Erreur sauvegarde:', err);
        const status = document.getElementById('tiers-roles-save-status');
        if (status) status.textContent = '❌ Erreur de sauvegarde';
    }
}
