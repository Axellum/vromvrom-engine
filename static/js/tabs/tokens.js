/* ============================================================
   TOKENS.JS — Page 5 : Suivi Tokens + Historique Complet
   ============================================================ */

const SESSIONS_PER_PAGE = 15;
let sessionsCurrentPage = 1;
let sessionsAllData = [];
let sessionsFilterText = '';

function renderTokens(container) {
    container.innerHTML = `
        <!-- Bandeau résumé financier -->
        <div class="glass-panel" style="margin-bottom:var(--space-xl);background:rgba(129,140,248,0.04);border-color:rgba(129,140,248,0.15);" id="tokens-financial-summary">
            <div class="section-title" style="margin-bottom:var(--space-md);">Résumé Financier Global</div>
            <div class="grid grid-4" style="gap:var(--space-lg);">
                <div style="text-align:center;">
                    <div style="font-size:0.68rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;">Tokens Totaux</div>
                    <div style="font-size:1.5rem;font-weight:800;font-family:var(--font-mono);color:var(--text-primary);" id="tfin-tokens">–</div>
                    <div style="font-size:0.65rem;color:var(--text-muted);">Moteur + CLI + IDE</div>
                </div>
                <div style="text-align:center;">
                    <div style="font-size:0.68rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;">Coût Réel</div>
                    <div style="font-size:1.5rem;font-weight:800;font-family:var(--font-mono);color:var(--accent-primary);" id="tfin-real-cost">–</div>
                    <div style="font-size:0.65rem;color:var(--text-muted);">Abos + API payantes</div>
                </div>
                <div style="text-align:center;">
                    <div style="font-size:0.68rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;">Coût Estimé</div>
                    <div style="font-size:1.5rem;font-weight:800;font-family:var(--font-mono);color:var(--success);" id="tfin-est-cost">–</div>
                    <div style="font-size:0.65rem;color:var(--text-muted);">Si pay-per-use global</div>
                </div>
                <div style="text-align:center;">
                    <div style="font-size:0.68rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;">Solde DeepSeek</div>
                    <div style="font-size:1.5rem;font-weight:800;font-family:var(--font-mono);color:var(--color-deepseek);" id="tfin-deepseek">–</div>
                    <div style="font-size:0.65rem;color:var(--text-muted);">Crédit prépayé restant</div>
                </div>
            </div>
        </div>

        <!-- OhMyToken Bead Board -->
        <div class="glass-panel" style="margin-bottom:var(--space-xl); display:flex; flex-direction:column; gap:var(--space-md); background:rgba(0,0,0,0.1); border-color:rgba(129,140,248,0.1);">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:var(--space-sm);">
                <div style="display:flex; align-items:center; gap:var(--space-sm);">
                    <span style="font-size:1.2rem;">🎨</span>
                    <div style="display:flex; flex-direction:column;">
                        <span class="section-title" style="margin-bottom:0; font-size:1rem; font-family:var(--font-display); text-transform:none;">OhMyToken Bead Board</span>
                        <span style="font-size:0.65rem; color:var(--text-muted);">Mosaïque rétro 3D de la consommation de tokens</span>
                    </div>
                </div>
                <div style="display:flex; gap:var(--space-md); align-items:center; flex-wrap:wrap;">
                    <div style="font-size:0.75rem; color:var(--accent-primary); font-family:var(--font-mono); font-weight:600;" id="omt-bead-value">1 perle = – tokens</div>
                    <div style="display:flex; gap:var(--space-xs); align-items:center;">
                        <span style="font-size:0.68rem; color:var(--text-muted);">Mode:</span>
                        <select id="omt-display-mode" style="background:rgba(18,22,33,0.9); border:1px solid var(--border-color); border-radius:var(--radius-sm); color:var(--text-primary); font-size:0.72rem; padding:3px 8px; font-family:var(--font-body); outline:none; cursor:pointer;" onchange="drawBeadBoard()">
                            <option value="chronological">Mosaïque Temporelle (Séquentiel)</option>
                            <option value="grouped">Groupé par Modèle</option>
                            <option value="spiral">Spirale Rétro</option>
                        </select>
                    </div>
                    <button class="btn btn--ghost btn--xs" onclick="initBeadBoardAnimation()" style="padding:4px 8px; font-size:0.72rem; border-color:var(--border-color); border-radius:var(--radius-sm);">✨ Animer</button>
                </div>
            </div>
            
            <div style="position:relative; width:100%; display:flex; justify-content:center; align-items:center; background:rgba(11,13,19,0.8); border-radius:var(--radius-lg); padding:var(--space-lg); overflow:hidden; border:1px solid rgba(255,255,255,0.02);">
                <canvas id="omt-canvas" style="display:block; max-width:100%; height:auto; background:#0f172a; border-radius:var(--radius-sm); border:1px solid rgba(255,255,255,0.03); image-rendering:pixelated;"></canvas>
            </div>
            
            <!-- Légende et métriques de la planche -->
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:var(--space-md); font-size:0.7rem; color:var(--text-muted); border-top:1px solid var(--border-color); padding-top:var(--space-md); margin-top:var(--space-xs);">
                <div style="display:flex; gap:var(--space-lg); flex-wrap:wrap; align-items:center;">
                    <span style="display:flex; align-items:center; gap:6px;">
                        <span style="width:9px; height:9px; background:var(--color-claude); border-radius:50%; display:inline-block; border:1px solid rgba(255,255,255,0.15); box-shadow:0 1px 3px rgba(0,0,0,0.5);"></span>
                        Claude Pro/CLI (<strong style="color:var(--text-primary);" id="omt-count-claude">0</strong> perles)
                    </span>
                    <span style="display:flex; align-items:center; gap:6px;">
                        <span style="width:9px; height:9px; background:var(--color-gemini); border-radius:50%; display:inline-block; border:1px solid rgba(255,255,255,0.15); box-shadow:0 1px 3px rgba(0,0,0,0.5);"></span>
                        Gemini Pro/Adv (<strong style="color:var(--text-primary);" id="omt-count-gemini">0</strong> perles)
                    </span>
                    <span style="display:flex; align-items:center; gap:6px;">
                        <span style="width:9px; height:9px; background:var(--color-deepseek); border-radius:50%; display:inline-block; border:1px solid rgba(255,255,255,0.15); box-shadow:0 1px 3px rgba(0,0,0,0.5);"></span>
                        DeepSeek API (<strong style="color:var(--text-primary);" id="omt-count-deepseek">0</strong> perles)
                    </span>
                    <span style="display:flex; align-items:center; gap:6px;">
                        <span style="width:9px; height:9px; background:var(--color-local); border-radius:50%; display:inline-block; border:1px solid rgba(255,255,255,0.15); box-shadow:0 1px 3px rgba(0,0,0,0.5);"></span>
                        Local / Gratuit (<strong style="color:var(--text-primary);" id="omt-count-local">0</strong> perles)
                    </span>
                    <span style="display:flex; align-items:center; gap:6px;">
                        <span style="width:9px; height:9px; background:#1e293b; border-radius:50%; display:inline-block; border:1px solid rgba(255,255,255,0.05);"></span>
                        Picot Vide
                    </span>
                </div>
                <div id="omt-board-stats" style="font-family:var(--font-mono); font-size:0.68rem; color:var(--text-secondary); background:rgba(255,255,255,0.03); padding:2px 8px; border-radius:var(--radius-sm); border:1px solid var(--border-color);">
                    Planche : 0 / 640 perles
                </div>
            </div>
        </div>

        <div class="grid grid-12">
            <!-- Ventilation par modèle -->
            <div class="glass-panel span-5" style="display:flex;flex-direction:column;gap:var(--space-lg);">
                <div class="section-title">
                    <span>Consommation par Modèle</span>
                    <span style="font-size:0.68rem;color:var(--text-muted);">Coût estimé = tarif pay-per-use</span>
                </div>
                <div id="models-breakdown" style="display:flex;flex-direction:column;gap:var(--space-md);">
                    <div class="empty-state"><div class="empty-state__icon">📊</div><div>Chargement...</div></div>
                </div>
            </div>

            <!-- Historique complet des Sessions -->
            <div class="glass-panel span-7" style="display:flex;flex-direction:column;gap:var(--space-lg);">
                <div class="section-title">
                    <span>Historique des Sessions & Conversations</span>
                    <span class="subtitle" id="sessions-total-badge">–</span>
                </div>
                
                <!-- KPI combiné moteur + CLI -->
                <div id="cli-combined-kpi" style="display:none;background:rgba(var(--accent-primary-rgb),0.06);border:1px solid var(--border-color);border-radius:var(--radius-md);padding:0.6rem 1rem;margin-bottom:var(--space-sm);">
                    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:var(--space-sm);">
                        <div style="font-size:0.78rem;color:var(--text-secondary);">
                            <strong style="color:var(--accent-primary);">Total Combiné :</strong>
                            <span id="cli-grand-total" style="font-family:var(--font-mono);font-weight:700;color:var(--text-primary);">–</span>
                            <span style="color:var(--text-muted);margin-left:0.5rem;">(Moteur: <span id="cli-moteur-total">–</span> + CLI: <span id="cli-cli-total">–</span>)</span>
                        </div>
                        <span id="cli-scan-status" style="font-size:0.68rem;color:var(--text-muted);"></span>
                    </div>
                </div>

                <!-- Barre de recherche + filtre -->
                <div style="display:flex;gap:var(--space-sm);align-items:center;flex-wrap:wrap;">
                    <input type="text" id="sessions-search" placeholder="🔍 Rechercher un objectif..."
                        style="flex:1;min-width:180px;background:rgba(var(--accent-primary-rgb),0.06);border:1px solid var(--border-color);border-radius:var(--radius-md);padding:0.45rem 0.75rem;font-size:0.8rem;color:var(--text-primary);font-family:var(--font-body);"
                        oninput="filterSessions(this.value)" />
                    <button class="btn btn--ghost btn--xs" onclick="scanCliTokens()" id="btn-scan-cli" title="Scanner les tokens Antigravity IDE + Claude CLI">🔄 Scanner CLI</button>
                    <button class="btn btn--ghost btn--xs" onclick="exportSessionsCSV()" title="Exporter CSV">📤 CSV</button>
                </div>

                <!-- Tableau -->
                <div style="overflow-x:auto;max-height:520px;">
                    <table class="data-table" id="sessions-table">
                        <thead>
                            <tr>
                                <th style="cursor:pointer;" onclick="sortSessions('date')">Date ↕</th>
                                <th>Objectif de la Session</th>
                                <th style="text-align:right;cursor:pointer;" onclick="sortSessions('tokens')">Tokens ↕</th>
                                <th style="text-align:right;cursor:pointer;" onclick="sortSessions('cost')">Coût ↕</th>
                                <th style="text-align:right;">Modèles</th>
                            </tr>
                        </thead>
                        <tbody id="sessions-table-body">
                            <tr><td colspan="5" class="empty-state">Chargement...</td></tr>
                        </tbody>
                    </table>
                </div>

                <!-- Pagination -->
                <div style="display:flex;justify-content:space-between;align-items:center;font-size:0.78rem;color:var(--text-muted);">
                    <span id="sessions-page-info">–</span>
                    <div style="display:flex;gap:var(--space-xs);">
                        <button class="btn btn--ghost btn--xs" id="sessions-prev" onclick="sessionsChangePage(-1)" disabled>◀ Préc.</button>
                        <button class="btn btn--ghost btn--xs" id="sessions-next" onclick="sessionsChangePage(1)" disabled>Suiv. ▶</button>
                    </div>
                </div>
            </div>

            <!-- Détails session cliquable -->
            <div id="session-details-card" class="glass-panel span-12" style="display:none;border-color:rgba(var(--accent-secondary-rgb),0.4);background:rgba(18,22,33,0.95);">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--space-md);border-bottom:1px solid var(--border-color);padding-bottom:var(--space-sm);">
                    <span style="font-weight:700;color:var(--accent-secondary);font-family:var(--font-display);font-size:1.1rem;" id="detail-sess-title">Détails de la Session</span>
                    <button class="btn btn--ghost btn--xs" onclick="document.getElementById('session-details-card').style.display='none'">Fermer</button>
                </div>
                <div style="font-size:0.9rem;color:var(--text-primary);font-weight:500;margin-bottom:var(--space-md);" id="detail-sess-objective"></div>
                
                <!-- KPIs rapides de la session -->
                <div class="grid grid-4" style="margin-bottom:var(--space-md);" id="detail-sess-kpis"></div>
                
                <!-- Ventilation par modèle -->
                <div class="grid grid-3" id="detail-sess-models"></div>
            </div>
        </div>
    `;

    loadTokensData();
}

let sessionsSortKey = 'date';
let sessionsSortAsc = false;

async function loadTokensData() {
    try {
        const data = await fetchTokens();
        if (!data) return;

        // ── Bandeau résumé financier ──
        updateFinancialSummary(data);

        // La ventilation par modele est appelee APRES chargement des sessions IDE
        // pour inclure les modeles CLI dans le graphique (voir plus bas)

        // Stocker toutes les sessions moteur
        sessionsAllData = [];
        if (data.sessions) {
            sessionsAllData = Object.entries(data.sessions)
                .map(([id, s]) => ({ id, channel: 'moteur', ...s }));
        }

        // Injecter les sessions CLI si disponibles (cache mémoire)
        if (data.cli_sessions) {
            const cli = data.cli_sessions;
            (cli.antigravity || []).forEach(s => {
                sessionsAllData.push({ ...s, id: s.session_id, channel: 'antigravity_ide' });
            });
            (cli.claude || []).forEach(s => {
                sessionsAllData.push({ ...s, id: s.session_id, channel: 'claude_cli' });
            });

            // Mettre à jour le KPI combiné
            const kpiBox = document.getElementById('cli-combined-kpi');
            if (kpiBox && data.combined_total) {
                kpiBox.style.display = 'block';
                document.getElementById('cli-grand-total').textContent = formatGaugeValue(data.combined_total.grand_total, '');
                document.getElementById('cli-moteur-total').textContent = formatGaugeValue(data.combined_total.moteur_tokens, '');
                document.getElementById('cli-cli-total').textContent = formatGaugeValue(data.combined_total.cli_tokens, '');
                document.getElementById('cli-scan-status').textContent = `Dernier scan : ${cli.last_scan ? new Date(cli.last_scan).toLocaleTimeString('fr-FR') : '–'}`;
            }
        }

        // Charger les conversations IDE depuis la BDD (persistées au démarrage)
        // Appels parallèles via Promise.all
        try {
            const [ideData, ideStats] = await Promise.all([
                fetchIdeConversations(200),
                fetchIdeConversationsStats()
            ]);
            if (ideData && ideData.conversations && ideData.conversations.length > 0) {
                const existingIds = new Set(sessionsAllData.map(s => s.conversation_id || s.id));
                let addedFromDb = 0;
                ideData.conversations.forEach(conv => {
                    if (!existingIds.has(conv.conversation_id)) {
                        sessionsAllData.push({
                            id: conv.conversation_id,
                            conversation_id: conv.conversation_id,
                            channel: conv.source,
                            timestamp: conv.timestamp,
                            last_activity: conv.last_activity,
                            objective: conv.objective,
                            prompt_tokens: conv.prompt_tokens,
                            completion_tokens: conv.completion_tokens,
                            total_tokens: conv.total_tokens,
                            estimated_cost_usd: conv.estimated_cost_usd,
                            is_subscription: conv.is_subscription,
                            models: conv.models || {},
                            user_messages: conv.user_messages,
                            model_responses: conv.model_responses,
                            estimation_method: conv.estimation_method,
                        });
                        addedFromDb++;
                    }
                });
                
                // Mettre à jour le KPI si pas déjà affiché par le CLI cache
                const kpiBox = document.getElementById('cli-combined-kpi');
                if (kpiBox && !data.cli_sessions && ideStats) {
                    kpiBox.style.display = 'block';
                    const globalData = data.global || data.total || {};
                    const moteurTokens = globalData.total_tokens || 0;
                    document.getElementById('cli-grand-total').textContent = formatGaugeValue(moteurTokens + (ideStats.total_tokens || 0), '');
                    document.getElementById('cli-moteur-total').textContent = formatGaugeValue(moteurTokens, '');
                    document.getElementById('cli-cli-total').textContent = formatGaugeValue(ideStats.total_tokens || 0, '');
                    document.getElementById('cli-scan-status').textContent = `${ideStats.total_conversations || 0} conv. IDE (BDD)`;
                }
            }
        } catch (ideErr) {
            console.warn("[Tokens] Conversations IDE BDD indisponibles:", ideErr.message);
        }

        // Trier par date décroissante
        sessionsAllData.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
        sessionsCurrentPage = 1;
        renderSessionsPage();

        // Ventilation par modèle APRES chargement de toutes les sessions
        // Maintenant sessionsAllData contient moteur + CLI + IDE
        renderModelsBreakdown(data);

        // [OhMyToken] Initialisation et animation de la planche de perles
        initBeadBoardAnimation();

    } catch (err) {
        console.error("[Tokens] Erreur:", err);
    }
}

/**
 * Devine le canal d'un modèle non présent dans le catalogue.
 * Utilisé pour les modèles IDE (gemini-2.5-pro, claude-3.5-sonnet, etc.)
 */
function _guessChannel(modelName) {
    const m = modelName.toLowerCase();
    if (m.includes('claude')) return 'cli-claude-pro';
    if (m.includes('gemini') && (m.includes('cli') || m.includes('adv'))) return 'cli-gemini-adv';
    if (m.includes('gemini')) return 'cli-gemini-adv';  // Les modèles IDE Gemini = abonnement
    if (m.includes('deepseek')) return 'paid-deepseek';
    if (m.includes('local') || m.includes('lmstudio')) return 'local';
    return 'unknown';
}

function renderModelsBreakdown(data) {
    const breakdown = document.getElementById('models-breakdown');
    if (!breakdown) return;

    // Agrégation des modèles : JSON moteur + sessions IDE
    const mergedModels = {};
    
    // 1. Modèles du JSON moteur (source primaire)
    if (data.models) {
        Object.entries(data.models).forEach(([name, m]) => {
            mergedModels[name] = { ...m };
        });
    }
    
    // 2. Modèles des sessions IDE (source BDD via sessionsAllData)
    if (sessionsAllData && sessionsAllData.length > 0) {
        sessionsAllData.forEach(s => {
            if (s.models && typeof s.models === 'object') {
                Object.entries(s.models).forEach(([modelName, modelData]) => {
                    // Normaliser le nom de modèle (enlever les préfixes longs)
                    const name = modelName.split('/').pop();
                    if (!mergedModels[name]) {
                        mergedModels[name] = {
                            prompt_tokens: 0,
                            completion_tokens: 0,
                            total_tokens: 0,
                            estimated_cost_usd: 0.0,
                        };
                    }
                    // Agréger (les données CLI sont des estimations)
                    const tokens = modelData.total_tokens || modelData.tokens || 0;
                    if (tokens > 0) {
                        mergedModels[name].total_tokens += tokens;
                        mergedModels[name].prompt_tokens += modelData.prompt_tokens || Math.round(tokens * 0.4);
                        mergedModels[name].completion_tokens += modelData.completion_tokens || Math.round(tokens * 0.6);
                        mergedModels[name].estimated_cost_usd += modelData.estimated_cost_usd || modelData.cost_usd || 0;
                    }
                });
            }
        });
    }

    const totalTokens = Object.values(mergedModels).reduce((sum, m) => sum + (m.total_tokens || 0), 0) || 1;
    const models = Object.entries(mergedModels)
        .sort(([, a], [, b]) => b.total_tokens - a.total_tokens);

    if (models.length === 0) {
        breakdown.innerHTML = '<div class="empty-state"><div class="empty-state__icon">📊</div><div>Aucune consommation.</div></div>';
        return;
    }

    // Grouper les modèles par canal d'accès
    const channelGroups = {};
    const channelMeta = {
        'local':            { icon: '🖥️', label: 'Local', costLabel: 'GRATUIT', color: 'var(--color-local)' },
        'free-api':         { icon: '🆓', label: 'Gemini Free (API)', costLabel: 'GRATUIT', color: 'var(--success)' },
        'cli-claude-pro':   { icon: '💻', label: 'CLI Claude Pro', costLabel: '~0.57 $/M amorti', color: 'var(--accent-primary)' },
        'cli-gemini-adv':   { icon: '💻', label: 'CLI Gemini Adv.', costLabel: '~0.20 $/M amorti', color: 'var(--accent-secondary)' },
        'paid-deepseek':    { icon: '🐋', label: 'DeepSeek API', costLabel: '0.14–0.87 $/M', color: 'var(--color-deepseek)' },
        'paid-gcp':         { icon: '💳', label: 'GCP Payant', costLabel: '1.28–10.26 €/M', color: 'var(--warning)' },
        'media':            { icon: '🎨', label: 'Génération Médias', costLabel: 'Par unité', color: 'var(--accent-primary)' },
        'unknown':          { icon: '❓', label: 'Autre', costLabel: '—', color: 'var(--text-muted)' }
    };

    models.forEach(([name, m]) => {
        const entry = getModelEntry(name);
        const channel = entry?.channel || _guessChannel(name);
        if (!channelGroups[channel]) channelGroups[channel] = { models: [], totalTokens: 0, totalCost: 0 };
        channelGroups[channel].models.push({ name, ...m });
        channelGroups[channel].totalTokens += m.total_tokens;
        channelGroups[channel].totalCost += m.estimated_cost_usd;
    });

    // Trier les canaux par volume de tokens
    const sortedChannels = Object.entries(channelGroups)
        .sort(([, a], [, b]) => b.totalTokens - a.totalTokens);

    breakdown.innerHTML = sortedChannels.map(([channelKey, group]) => {
        const meta = channelMeta[channelKey] || channelMeta['unknown'];
        const channelPct = ((group.totalTokens / totalTokens) * 100).toFixed(1);

        const modelsHtml = group.models.map(m => {
            const pct = ((m.total_tokens / totalTokens) * 100).toFixed(1);
            const barColor = getModelBarColor(m.name);
            return `
                <div style="display:flex;flex-direction:column;gap:var(--space-xs);padding-left:var(--space-md);">
                    <div style="display:flex;justify-content:space-between;align-items:baseline;font-size:0.78rem;">
                        <span style="display:flex;align-items:center;gap:var(--space-sm);">
                            ${getModelBadgeHtml(m.name)}
                        </span>
                        <span style="font-family:var(--font-mono);font-size:0.72rem;color:var(--text-secondary);">
                            ${formatGaugeValue(m.total_tokens, '')} (${pct}%)
                        </span>
                    </div>
                    <div class="progress-bar">
                        <div class="progress-bar__fill" style="width:${pct}%;background:${barColor};"></div>
                    </div>
                    <div style="display:flex;justify-content:space-between;font-size:0.62rem;color:var(--text-muted);">
                        <span>In: ${formatGaugeValue(m.prompt_tokens, '')}</span>
                        <span>Out: ${formatGaugeValue(m.completion_tokens, '')}</span>
                        <span style="color:var(--success);">${m.estimated_cost_usd.toFixed(4)} $</span>
                    </div>
                </div>
            `;
        }).join('');

        return `
            <div style="border:1px solid var(--border-color);border-radius:var(--radius-lg);padding:var(--space-lg);background:rgba(255,255,255,0.01);display:flex;flex-direction:column;gap:var(--space-md);">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div style="display:flex;align-items:center;gap:var(--space-sm);">
                        <span style="font-size:1.1rem;">${meta.icon}</span>
                        <span style="font-weight:700;font-size:0.88rem;color:${meta.color};">${meta.label}</span>
                        <span style="font-size:0.65rem;color:var(--text-muted);">(${meta.costLabel})</span>
                    </div>
                    <div style="display:flex;align-items:baseline;gap:var(--space-sm);font-family:var(--font-mono);font-size:0.78rem;">
                        <span style="font-weight:700;color:var(--text-primary);">${formatGaugeValue(group.totalTokens, '')}</span>
                        <span style="font-size:0.65rem;color:var(--text-muted);">${channelPct}%</span>
                        <span style="color:var(--success);font-size:0.72rem;">${group.totalCost.toFixed(4)} $</span>
                    </div>
                </div>
                ${modelsHtml}
            </div>
        `;
    }).join('');
}

/**
 * Met à jour le bandeau résumé financier en tête de l'onglet Tokens
 */
function updateFinancialSummary(data) {
    const ct = data?.combined_total;
    const rb = data?.real_billing;
    const globalData = data?.global || data?.total;

    const tfinTokens = document.getElementById('tfin-tokens');
    const tfinReal   = document.getElementById('tfin-real-cost');
    const tfinEst    = document.getElementById('tfin-est-cost');
    const tfinDS     = document.getElementById('tfin-deepseek');

    if (tfinTokens && ct) {
        tfinTokens.textContent = formatGaugeValue(ct.grand_total || 0, '');
    } else if (tfinTokens && globalData) {
        tfinTokens.textContent = formatGaugeValue(globalData.total_tokens || 0, '');
    }

    if (tfinReal && ct) {
        // Coût réel = abonnements (prorata) + API payantes directes (DeepSeek + GCP)
        const bySource = ct.by_source || [];
        const ideSource = bySource.find(s => s.source === 'antigravity_ide');
        const claudeSource = bySource.find(s => s.source === 'claude_cli');
        const aboTotal = (ideSource?.cost_usd || 0) + (claudeSource?.cost_usd || 0);
        const apiPayant = rb?.deepseek_balance_usd != null
            ? (20 - rb.deepseek_balance_usd) : 0;
        const gcpCost = rb?.gemini_gcp_cost_usd || 0;
        const totalRealCost = aboTotal + apiPayant + gcpCost;
        tfinReal.textContent = `${totalRealCost.toFixed(2)} $`;
    } else if (tfinReal && rb) {
        // Fallback si pas de combined_total mais real_billing disponible
        const apiPayant = rb.deepseek_balance_usd != null
            ? (20 - rb.deepseek_balance_usd) : 0;
        const gcpCost = rb.gemini_gcp_cost_usd || 0;
        const totalRealCost = apiPayant + gcpCost;
        tfinReal.textContent = `${totalRealCost.toFixed(2)} $`;
    } else if (tfinReal) {
        tfinReal.textContent = '–';
    }

    if (tfinEst) {
        if (ct) {
            // Coût estimé total = estimation moteur + estimation CLI/IDE
            const totalEstimatedCost = (ct.moteur_cost_usd || 0) + (ct.cli_cost_estimated_usd || 0);
            tfinEst.textContent = `${totalEstimatedCost.toFixed(4)} $`;
        } else if (globalData) {
            tfinEst.textContent = `${(globalData.estimated_cost_usd || 0).toFixed(4)} $`;
        } else {
            tfinEst.textContent = '–';
        }
    }

    if (tfinDS && rb) {
        tfinDS.textContent = rb.deepseek_balance_usd != null
            ? `${rb.deepseek_balance_usd.toFixed(2)} $` : '–';
    }
}

function getFilteredSessions() {
    let filtered = sessionsAllData;
    if (sessionsFilterText) {
        const q = sessionsFilterText.toLowerCase();
        filtered = filtered.filter(s =>
            (s.objective || '').toLowerCase().includes(q) ||
            (s.id || '').toLowerCase().includes(q)
        );
    }
    return filtered;
}

function renderSessionsPage() {
    const filtered = getFilteredSessions();
    const totalPages = Math.max(1, Math.ceil(filtered.length / SESSIONS_PER_PAGE));
    sessionsCurrentPage = Math.min(sessionsCurrentPage, totalPages);

    const start = (sessionsCurrentPage - 1) * SESSIONS_PER_PAGE;
    const page = filtered.slice(start, start + SESSIONS_PER_PAGE);

    // Badge total
    const badge = document.getElementById('sessions-total-badge');
    if (badge) badge.textContent = `${filtered.length} session(s)${sessionsFilterText ? ' filtrée(s)' : ''}`;

    // Tableau
    const tbody = document.getElementById('sessions-table-body');
    if (!tbody) return;

    if (page.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="empty-state" style="padding:var(--space-xl);">Aucune session trouvée.</td></tr>`;
    } else {
        tbody.innerHTML = page.map(s => {
            const d = new Date(s.timestamp);
            const dateStr = d.toLocaleDateString('fr-FR') + ' ' + d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
            const models = Object.keys(s.models || {}).map(m => m.split('/').pop()).join(', ') || '–';
            // Badge de canal
            let channelBadge = '';
            if (s.channel === 'antigravity_ide') {
                channelBadge = '<span style="background:rgba(129,140,248,0.15);color:var(--accent-primary);font-size:0.6rem;padding:1px 5px;border-radius:3px;margin-left:4px;font-weight:600;">IDE</span>';
            } else if (s.channel === 'claude_cli') {
                channelBadge = '<span style="background:rgba(251,146,60,0.15);color:var(--warning);font-size:0.6rem;padding:1px 5px;border-radius:3px;margin-left:4px;font-weight:600;">CLI</span>';
            } else {
                channelBadge = '<span style="background:rgba(16,185,129,0.15);color:var(--success);font-size:0.6rem;padding:1px 5px;border-radius:3px;margin-left:4px;font-weight:600;">MOT</span>';
            }
            const subLabel = s.is_subscription ? ' <span style="font-size:0.55rem;color:var(--text-muted);">(abo)</span>' : '';
            const costDisplay = s.is_subscription
                ? `<span style="color:var(--text-muted);font-style:italic;">≈ ${(s.estimated_cost_usd || 0).toFixed(4)} $</span> <span style="font-size:0.55rem;color:var(--accent-secondary);">inclus</span>`
                : `${(s.estimated_cost_usd || 0).toFixed(4)} $`;
            return `
                <tr class="clickable" onclick="showSessionDetails('${s.id}', ${JSON.stringify(s).replace(/'/g, '&#39;').replace(/"/g, '&quot;')})">
                    <td style="white-space:nowrap;color:var(--text-secondary);font-size:0.78rem;">${dateStr}${channelBadge}</td>
                    <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${(s.objective || '').replace(/"/g, '&quot;')}">${s.objective || '<em style="color:var(--text-muted);">Non spécifié</em>'}</td>
                    <td style="text-align:right;font-family:var(--font-mono);font-size:0.78rem;">${formatGaugeValue(s.total_tokens, '')}${subLabel}</td>
                    <td style="text-align:right;font-family:var(--font-mono);font-size:0.78rem;">${costDisplay}</td>
                    <td style="text-align:right;font-size:0.68rem;color:var(--text-muted);max-width:140px;overflow:hidden;text-overflow:ellipsis;">${models}</td>
                </tr>
            `;
        }).join('');
    }

    // Pagination
    const pageInfo = document.getElementById('sessions-page-info');
    if (pageInfo) pageInfo.textContent = `Page ${sessionsCurrentPage} / ${totalPages}`;

    const prevBtn = document.getElementById('sessions-prev');
    const nextBtn = document.getElementById('sessions-next');
    if (prevBtn) prevBtn.disabled = sessionsCurrentPage <= 1;
    if (nextBtn) nextBtn.disabled = sessionsCurrentPage >= totalPages;
}

function sessionsChangePage(delta) {
    sessionsCurrentPage += delta;
    renderSessionsPage();
}

function filterSessions(text) {
    sessionsFilterText = text;
    sessionsCurrentPage = 1;
    renderSessionsPage();
}

function sortSessions(key) {
    if (sessionsSortKey === key) {
        sessionsSortAsc = !sessionsSortAsc;
    } else {
        sessionsSortKey = key;
        sessionsSortAsc = key === 'date' ? false : true;
    }

    sessionsAllData.sort((a, b) => {
        let va, vb;
        if (key === 'date') { va = new Date(a.timestamp); vb = new Date(b.timestamp); }
        else if (key === 'tokens') { va = a.total_tokens; vb = b.total_tokens; }
        else if (key === 'cost') { va = a.estimated_cost_usd; vb = b.estimated_cost_usd; }
        return sessionsSortAsc ? va - vb : vb - va;
    });

    sessionsCurrentPage = 1;
    renderSessionsPage();
}

function showSessionDetails(sessionId, sessionData) {
    const card = document.getElementById('session-details-card');
    if (!card) return;
    card.style.display = 'block';
    card.scrollIntoView({ behavior: 'smooth', block: 'start' });

    document.getElementById('detail-sess-title').textContent = `Session : ${sessionId}`;
    document.getElementById('detail-sess-objective').textContent = sessionData.objective || 'Non spécifié';

    // KPIs de la session
    const kpis = document.getElementById('detail-sess-kpis');
    if (kpis) {
        const d = new Date(sessionData.timestamp);
        const modelCount = Object.keys(sessionData.models || {}).length;
        kpis.innerHTML = `
            <div class="glass-panel glass-panel--compact" style="text-align:center;">
                <div style="font-size:0.68rem;color:var(--text-muted);margin-bottom:2px;">Date</div>
                <div style="font-weight:700;font-size:0.9rem;">${d.toLocaleDateString('fr-FR')}</div>
                <div style="font-size:0.7rem;color:var(--text-secondary);">${d.toLocaleTimeString('fr-FR')}</div>
            </div>
            <div class="glass-panel glass-panel--compact" style="text-align:center;">
                <div style="font-size:0.68rem;color:var(--text-muted);margin-bottom:2px;">Tokens Total</div>
                <div style="font-weight:700;font-size:0.9rem;color:var(--accent-primary);">${formatGaugeValue(sessionData.total_tokens, '')}</div>
            </div>
            <div class="glass-panel glass-panel--compact" style="text-align:center;">
                <div style="font-size:0.68rem;color:var(--text-muted);margin-bottom:2px;">Coût</div>
                <div style="font-weight:700;font-size:0.9rem;color:var(--success);">${sessionData.estimated_cost_usd.toFixed(4)} $</div>
            </div>
            <div class="glass-panel glass-panel--compact" style="text-align:center;">
                <div style="font-size:0.68rem;color:var(--text-muted);margin-bottom:2px;">Modèles</div>
                <div style="font-weight:700;font-size:0.9rem;">${modelCount}</div>
            </div>
        `;
    }

    // Ventilation par modèle
    const modelsContainer = document.getElementById('detail-sess-models');
    if (modelsContainer && sessionData.models) {
        modelsContainer.innerHTML = Object.entries(sessionData.models).map(([name, m]) => `
            <div class="glass-panel glass-panel--compact" style="display:flex;flex-direction:column;gap:var(--space-sm);">
                <div style="font-weight:700;font-size:0.85rem;">${getModelBadgeHtml(name)}</div>
                <div style="font-size:0.75rem;color:var(--text-secondary);">
                    <div>Prompt: <strong>${formatGaugeValue(m.prompt_tokens, '')}</strong></div>
                    <div>Completion: <strong>${formatGaugeValue(m.completion_tokens, '')}</strong></div>
                    <div>Total: <strong>${formatGaugeValue(m.total_tokens, '')}</strong></div>
                    <div style="color:var(--success);margin-top:var(--space-xs);">Coût: <strong>${m.estimated_cost_usd.toFixed(4)} $</strong></div>
                </div>
            </div>
        `).join('');
    }
}

function exportSessionsCSV() {
    const filtered = getFilteredSessions();
    if (filtered.length === 0) {
        showToast('warning', 'Aucune session à exporter.');
        return;
    }

    let csv = 'Date,Session ID,Objectif,Tokens,Coût USD,Modèles\n';
    filtered.forEach(s => {
        const d = new Date(s.timestamp).toISOString();
        const obj = (s.objective || '').replace(/"/g, '""');
        const models = Object.keys(s.models || {}).join(' | ');
        csv += `"${d}","${s.id}","${obj}",${s.total_tokens},${s.estimated_cost_usd.toFixed(6)},"${models}"\n`;
    });

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `sessions_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    showToast('success', `${filtered.length} sessions exportées en CSV.`);
}

function getModelBarColor(modelName) {
    if (modelName.includes('deepseek')) return 'var(--color-deepseek)';
    if (modelName.includes('claude')) return 'var(--color-claude)';
    if (modelName.includes('local')) return 'var(--color-local)';
    return 'var(--color-gemini)';
}

/* ─── Scan CLI Tokens ─── */
async function scanCliTokens() {
    const btn = document.getElementById('btn-scan-cli');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ Scan...';
    }
    try {
        const result = await collectCliTokens();
        showToast('success', `Scan CLI terminé : ${result.antigravity_count} Antigravity + ${result.claude_count} Claude = ${formatGaugeValue(result.total_cli_tokens, '')} tokens`);
        // Recharger les données avec les sessions CLI
        await loadTokensData();
    } catch (err) {
        showToast('error', 'Erreur scan CLI : ' + err.message);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '🔄 Scanner CLI';
        }
    }
}

/* ============================================================
   OHMYTOKEN BEAD BOARD — LOGIQUE ET RENDU
   ============================================================ */

let omtAnimationTimer = null;
let omtAnimationIndex = 0;

const OMT_PALETTE = {
    'claude': {
        base: '#d97757',
        light: '#e7a087',
        dark: '#b75232'
    },
    'gemini': {
        base: '#4285f4',
        light: '#7dabf8',
        dark: '#125be2'
    },
    'deepseek': {
        base: '#00d4aa',
        light: '#47ffd6',
        dark: '#009e7e'
    },
    'local': {
        base: '#a3e635',
        light: '#c2f170',
        dark: '#7bae1a'
    },
    'other': {
        base: '#6b7280',
        light: '#9ca3af',
        dark: '#4b5563'
    }
};

function drawPeg(ctx, cx, cy) {
    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, 2.5, 0, Math.PI * 2);
    ctx.fillStyle = '#1e293b'; // Picot gris-bleu foncé
    ctx.fill();
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
    ctx.lineWidth = 0.5;
    ctx.stroke();
    ctx.restore();
}

function drawBead(ctx, cx, cy, modelType, size = 8) {
    const palette = OMT_PALETTE[modelType] || OMT_PALETTE['other'];
    
    ctx.save();
    
    // Ombre portée sous la perle pour effet 3D
    ctx.shadowColor = 'rgba(0, 0, 0, 0.45)';
    ctx.shadowBlur = 4;
    ctx.shadowOffsetX = 1.5;
    ctx.shadowOffsetY = 2;
    
    // Corps de la perle
    ctx.beginPath();
    ctx.arc(cx, cy, size, 0, Math.PI * 2);
    
    // Dégradé radial pour simuler le volume (brillance plastique)
    const grad = ctx.createRadialGradient(cx - size * 0.25, cy - size * 0.25, size * 0.05, cx, cy, size);
    grad.addColorStop(0, '#ffffff'); // Reflet blanc au sommet
    grad.addColorStop(0.2, palette.light); // Zone claire éclairée
    grad.addColorStop(0.65, palette.base); // Couleur de base
    grad.addColorStop(1, palette.dark); // Bordure ombragée
    
    ctx.fillStyle = grad;
    ctx.fill();
    
    // Désactiver l'ombre pour le trou central
    ctx.shadowColor = 'transparent';
    ctx.shadowBlur = 0;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 0;
    
    // Trou central de la perle
    ctx.beginPath();
    ctx.arc(cx, cy, size * 0.35, 0, Math.PI * 2);
    ctx.fillStyle = '#0f172a'; // Fond de la plaque (picot/sombre)
    ctx.fill();
    
    // Bordure fine du trou central pour donner du relief
    ctx.strokeStyle = 'rgba(0, 0, 0, 0.35)';
    ctx.lineWidth = 0.75;
    ctx.stroke();
    
    // Bordure fine extérieure pour séparer les perles
    ctx.beginPath();
    ctx.arc(cx, cy, size, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(0, 0, 0, 0.2)';
    ctx.lineWidth = 0.5;
    ctx.stroke();
    
    ctx.restore();
}

function getSpiralCoordinates(cols, rows) {
    const coords = [];
    let x = Math.floor(cols / 2);
    let y = Math.floor(rows / 2);
    
    let dx = 1;
    let dy = 0;
    let segmentLength = 1;
    let segmentPassed = 0;
    let turns = 0;
    
    const visited = Array(rows).fill().map(() => Array(cols).fill(false));
    
    for (let i = 0; i < cols * rows; i++) {
        if (x >= 0 && x < cols && y >= 0 && y < rows) {
            coords.push({ r: y, c: x });
            visited[y][x] = true;
        }
        
        x += dx;
        y += dy;
        segmentPassed++;
        
        if (segmentPassed === segmentLength) {
            segmentPassed = 0;
            // Tourner sens horaire
            const temp = dx;
            dx = -dy;
            dy = temp;
            
            turns++;
            if (turns % 2 === 0) {
                segmentLength++;
            }
        }
    }
    
    // Compléter avec les cases non visitées (coins)
    for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
            if (!visited[r][c]) {
                coords.push({ r, c });
            }
        }
    }
    
    return coords;
}

function getBeadsForDisplay(mode, maxBeads) {
    if (!sessionsAllData || sessionsAllData.length === 0) {
        return [];
    }
    
    let totalTokens = 0;
    const modelTokens = {
        claude: 0,
        gemini: 0,
        deepseek: 0,
        local: 0
    };
    
    // Trier chronologiquement pour le mode séquentiel
    const sortedSessions = [...sessionsAllData].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    const rawBeadsSequence = [];
    
    sortedSessions.forEach(s => {
        if (!s.models) return;
        Object.entries(s.models).forEach(([modelName, mData]) => {
            const tokens = mData.total_tokens || mData.tokens || 0;
            if (tokens <= 0) return;
            
            let category = 'local';
            const m = modelName.toLowerCase();
            if (m.includes('claude')) category = 'claude';
            else if (m.includes('gemini')) category = 'gemini';
            else if (m.includes('deepseek')) category = 'deepseek';
            else if (m.includes('local') || m.includes('lmstudio')) category = 'local';
            
            modelTokens[category] += tokens;
            totalTokens += tokens;
            
            rawBeadsSequence.push({ category, tokens });
        });
    });
    
    if (totalTokens === 0) return [];
    
    // Arrondir la valeur de token par perle pour avoir une belle granularité
    const rawVal = totalTokens / maxBeads;
    let tokenPerBead = 1000;
    if (rawVal > 100000) tokenPerBead = 100000;
    else if (rawVal > 50000) tokenPerBead = 50000;
    else if (rawVal > 25000) tokenPerBead = 25000;
    else if (rawVal > 10000) tokenPerBead = 10000;
    else if (rawVal > 5000) tokenPerBead = 5000;
    else if (rawVal > 2000) tokenPerBead = 2000;
    else if (rawVal > 1000) tokenPerBead = 1000;
    else if (rawVal > 500) tokenPerBead = 500;
    else if (rawVal > 100) tokenPerBead = 100;
    else tokenPerBead = 50;
    
    const valEl = document.getElementById('omt-bead-value');
    if (valEl) {
        valEl.textContent = `1 perle = ${formatGaugeValue(tokenPerBead, '')} tokens`;
    }
    
    let beads = [];
    
    if (mode === 'chronological' || mode === 'spiral') {
        let accumulator = { claude: 0, gemini: 0, deepseek: 0, local: 0 };
        rawBeadsSequence.forEach(item => {
            accumulator[item.category] += item.tokens;
            while (accumulator[item.category] >= tokenPerBead) {
                beads.push(item.category);
                accumulator[item.category] -= tokenPerBead;
            }
        });
        
        // Ajouter un résidu si significatif (> 50% de la perle)
        Object.entries(accumulator).forEach(([cat, val]) => {
            if (val >= tokenPerBead * 0.5 && beads.length < maxBeads) {
                beads.push(cat);
            }
        });
    } else if (mode === 'grouped') {
        Object.entries(modelTokens).forEach(([cat, tokens]) => {
            const count = Math.round(tokens / tokenPerBead);
            for (let i = 0; i < count; i++) {
                beads.push(cat);
            }
        });
    }
    
    if (beads.length > maxBeads) {
        beads = beads.slice(-maxBeads); // Conserver les plus récentes
    }
    
    return beads;
}

function drawBeadBoard() {
    // Si une animation est en cours, on l'arrête pour éviter le clignotement
    if (omtAnimationTimer) {
        clearInterval(omtAnimationTimer);
        omtAnimationTimer = null;
    }

    const canvas = document.getElementById('omt-canvas');
    if (!canvas) return;
    
    const ctx = canvas.getContext('2d');
    const cols = 40;
    const rows = 16;
    const cellSize = 20;
    
    if (canvas.width !== cols * cellSize) canvas.width = cols * cellSize;
    if (canvas.height !== rows * cellSize) canvas.height = rows * cellSize;
    
    // Fond sombre de la plaque
    ctx.fillStyle = '#0f172a';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    
    const modeSelect = document.getElementById('omt-display-mode');
    const mode = modeSelect ? modeSelect.value : 'chronological';
    const beads = getBeadsForDisplay(mode, cols * rows);
    
    let drawCoords = [];
    if (mode === 'spiral') {
        drawCoords = getSpiralCoordinates(cols, rows);
    } else {
        for (let r = 0; r < rows; r++) {
            for (let c = 0; c < cols; c++) {
                drawCoords.push({ r, c });
            }
        }
    }
    
    let countClaude = 0;
    let countGemini = 0;
    let countDeepseek = 0;
    let countLocal = 0;
    
    for (let i = 0; i < cols * rows; i++) {
        const coord = drawCoords[i];
        const cx = coord.c * cellSize + cellSize / 2;
        const cy = coord.r * cellSize + cellSize / 2;
        
        if (i < beads.length) {
            const type = beads[i];
            drawBead(ctx, cx, cy, type, 8);
            
            if (type === 'claude') countClaude++;
            else if (type === 'gemini') countGemini++;
            else if (type === 'deepseek') countDeepseek++;
            else if (type === 'local') countLocal++;
        } else {
            drawPeg(ctx, cx, cy);
        }
    }
    
    // Mettre à jour la légende
    const elClaude = document.getElementById('omt-count-claude');
    const elGemini = document.getElementById('omt-count-gemini');
    const elDeepseek = document.getElementById('omt-count-deepseek');
    const elLocal = document.getElementById('omt-count-local');
    const elStats = document.getElementById('omt-board-stats');
    
    if (elClaude) elClaude.textContent = countClaude;
    if (elGemini) elGemini.textContent = countGemini;
    if (elDeepseek) elDeepseek.textContent = countDeepseek;
    if (elLocal) elLocal.textContent = countLocal;
    
    if (elStats) {
        elStats.textContent = `Planche : ${beads.length} / ${cols * rows} perles`;
    }
}

function initBeadBoardAnimation() {
    if (omtAnimationTimer) {
        clearInterval(omtAnimationTimer);
    }
    
    omtAnimationIndex = 0;
    const canvas = document.getElementById('omt-canvas');
    if (!canvas) return;
    
    const ctx = canvas.getContext('2d');
    const cols = 40;
    const rows = 16;
    const cellSize = 20;
    
    if (canvas.width !== cols * cellSize) canvas.width = cols * cellSize;
    if (canvas.height !== rows * cellSize) canvas.height = rows * cellSize;
    
    const modeSelect = document.getElementById('omt-display-mode');
    const mode = modeSelect ? modeSelect.value : 'chronological';
    const beads = getBeadsForDisplay(mode, cols * rows);
    
    let drawCoords = [];
    if (mode === 'spiral') {
        drawCoords = getSpiralCoordinates(cols, rows);
    } else {
        for (let r = 0; r < rows; r++) {
            for (let c = 0; c < cols; c++) {
                drawCoords.push({ r, c });
            }
        }
    }
    
    // Remplir d'abord toute la plaque avec des picots vides
    ctx.fillStyle = '#0f172a';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    for (let i = 0; i < cols * rows; i++) {
        const coord = drawCoords[i];
        const cx = coord.c * cellSize + cellSize / 2;
        const cy = coord.r * cellSize + cellSize / 2;
        drawPeg(ctx, cx, cy);
    }
    
    // Animer le dépôt de perles
    omtAnimationTimer = setInterval(() => {
        if (omtAnimationIndex >= beads.length) {
            clearInterval(omtAnimationTimer);
            omtAnimationTimer = null;
            return;
        }
        
        const type = beads[omtAnimationIndex];
        const coord = drawCoords[omtAnimationIndex];
        const cx = coord.c * cellSize + cellSize / 2;
        const cy = coord.r * cellSize + cellSize / 2;
        
        drawBead(ctx, cx, cy, type, 8);
        updateAnimationLegend(beads.slice(0, omtAnimationIndex + 1), cols * rows);
        
        omtAnimationIndex++;
    }, 6); // Rendu très rapide pour les grands volumes
}

function updateAnimationLegend(visibleBeads, totalSlots) {
    let countClaude = 0;
    let countGemini = 0;
    let countDeepseek = 0;
    let countLocal = 0;
    
    visibleBeads.forEach(type => {
        if (type === 'claude') countClaude++;
        else if (type === 'gemini') countGemini++;
        else if (type === 'deepseek') countDeepseek++;
        else if (type === 'local') countLocal++;
    });
    
    const elClaude = document.getElementById('omt-count-claude');
    const elGemini = document.getElementById('omt-count-gemini');
    const elDeepseek = document.getElementById('omt-count-deepseek');
    const elLocal = document.getElementById('omt-count-local');
    const elStats = document.getElementById('omt-board-stats');
    
    if (elClaude) elClaude.textContent = countClaude;
    if (elGemini) elGemini.textContent = countGemini;
    if (elDeepseek) elDeepseek.textContent = countDeepseek;
    if (elLocal) elLocal.textContent = countLocal;
    
    if (elStats) {
        elStats.textContent = `Planche : ${visibleBeads.length} / ${totalSlots} perles`;
    }
}

