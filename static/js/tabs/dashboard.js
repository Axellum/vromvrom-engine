/* ============================================================
   DASHBOARD.JS — Page 1 : Vue d'ensemble (KPIs + Moteur + Quotas)
   Refonte V9 : KPIs coûts réels, graphe 30j par jour/semaine,
   section "Risques de blocage" lisible avec timeline.
   ============================================================ */

function renderDashboard(container) {
    container.innerHTML = `
        <!-- ═══ KPIs (4 tuiles) ═══ -->
        <div class="grid grid-4" style="gap:var(--space-xl);margin-bottom:var(--space-xl);">

            <!-- KPI 1 : Tokens 30j -->
            <div class="glass-panel card-metric" id="kpi-card-tokens" title="Tokens consommés sur les 30 derniers jours (moteur + CLI)">
                <div class="card-metric__label">
                    <span>Tokens · 30 jours</span>
                    <span style="font-size:0.65rem;color:var(--text-muted);">Moteur + CLI</span>
                </div>
                <div class="card-metric__value" id="kpi-tokens">–</div>
                <div class="card-metric__sub">
                    <span>Moteur : <strong id="kpi-moteur-tokens">–</strong></span>
                    <span>CLI : <strong id="kpi-cli-tokens">–</strong></span>
                </div>
            </div>

            <!-- KPI 2 : Coûts 30 jours -->
            <div class="glass-panel card-metric cost" id="kpi-card-cost"
                 style="cursor:pointer;position:relative;"
                 title="Coût réel (abonnements + APIs) vs Coût estimé si pay-per-use pur. Survol pour détail.">
                <div class="card-metric__label">
                    <span>Coûts · 30 jours</span>
                    <span style="font-size:0.65rem;color:var(--text-muted);">Réel vs Estimé</span>
                </div>
                <div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:2px;">
                    <div>
                        <div style="font-size:0.6rem;color:var(--text-muted);text-transform:uppercase;">Réel</div>
                        <div class="card-metric__value" id="kpi-cost" style="color:var(--success);font-size:1.6rem;">–</div>
                    </div>
                    <div style="text-align:right;">
                        <div style="font-size:0.6rem;color:var(--text-muted);text-transform:uppercase;">Est. Global</div>
                        <div class="card-metric__value" id="kpi-cost-estimated" style="color:var(--accent-secondary);font-size:1.4rem;">–</div>
                    </div>
                </div>
                <div class="card-metric__sub" id="kpi-cost-sub" style="margin-top:2px;">
                    <span id="kpi-cost-abo" style="color:var(--accent-primary);">–</span>
                    <span id="kpi-cost-api" style="color:var(--color-deepseek);">–</span>
                </div>
                <!-- Tooltip de détail au survol -->
                <div id="kpi-cost-tooltip" style="
                    display:none;position:absolute;top:100%;left:0;right:0;
                    background:rgba(11,13,19,0.97);border:1px solid rgba(129,140,248,0.3);
                    border-radius:10px;padding:0.8rem;z-index:200;font-size:0.75rem;
                    box-shadow:0 12px 28px rgba(0,0,0,0.7);margin-top:6px;">
                    <div id="kpi-cost-breakdown"></div>
                </div>
            </div>

            <!-- KPI 3 : Solde DeepSeek -->
            <div class="glass-panel card-metric" id="kpi-card-deepseek"
                 style="position:relative;cursor:pointer;"
                 title="Solde prépayé DeepSeek API restant. Clic pour forcer la synchro.">
                <div class="card-metric__label">
                    <span>Solde DeepSeek</span>
                    <span style="font-size:0.65rem;color:var(--color-deepseek);">Prépayé restant</span>
                </div>
                <div class="card-metric__value" id="kpi-deepseek" style="color:var(--color-deepseek);">–</div>
                <div class="card-metric__sub">
                    <span id="kpi-deepseek-used">–</span>
                    <span id="kpi-deepseek-sync" style="font-size:0.65rem;color:var(--text-muted);">–</span>
                </div>
            </div>

            <!-- KPI 4 : Sessions -->
            <div class="glass-panel card-metric sessions" id="kpi-card-sessions"
                 title="Sessions enregistrées dans la BDD (moteur + CLI Antigravity)">
                <div class="card-metric__label">
                    <span>Sessions</span>
                    <span style="font-size:0.65rem;color:var(--text-muted);">Moteur + IDE</span>
                </div>
                <div class="card-metric__value" id="kpi-sessions">–</div>
                <div class="card-metric__sub">
                    <span>Moteur : <strong id="kpi-sessions-moteur">–</strong></span>
                    <span>IDE : <strong id="kpi-sessions-ide">–</strong></span>
                </div>
            </div>
        </div>

        <!-- ═══ Ligne médiane : Pipeline + Quotas & Risques ═══ -->
        <div class="grid grid-12" style="gap:var(--space-xl);">

            <!-- Pipeline du Moteur -->
            <div class="glass-panel span-7" style="display:flex;flex-direction:column;gap:var(--space-lg);">
                <div class="section-title">
                    <span>Pipeline du Moteur</span>
                    <div class="flex items-center gap-sm">
                        <span class="status-dot" id="dash-engine-dot"></span>
                        <span style="font-size:0.8rem;color:var(--text-secondary);" id="dash-engine-text">Inactif</span>
                    </div>
                </div>
                <div id="engine-pipeline-viz" style="display:flex;flex-direction:column;gap:var(--space-md);">
                    ${renderPipelineFlowchart()}
                </div>
                <!-- Statut d'exécution live du Moteur -->
                <div id="dash-engine-live-status" style="display:none;background:rgba(255,255,255,0.02);border:1px solid var(--border-color);border-radius:var(--radius-md);padding:var(--space-md);margin-top:var(--space-md);">
                    <div style="display:flex;justify-content:space-between;margin-bottom:var(--space-xs);align-items:center;">
                        <span style="font-size:0.75rem;color:var(--text-muted);">Phase courante :</span>
                        <strong id="dash-live-phase" style="font-size:0.8rem;color:var(--accent-primary);">–</strong>
                    </div>
                    <div style="display:flex;justify-content:space-between;margin-bottom:var(--space-xs);align-items:center;">
                        <span style="font-size:0.75rem;color:var(--text-muted);">Agent Actif :</span>
                        <strong id="dash-live-agent" style="font-size:0.8rem;color:var(--accent-secondary);">–</strong>
                    </div>
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <span style="font-size:0.75rem;color:var(--text-muted);">Budget Session :</span>
                        <strong id="dash-live-budget" style="font-size:0.8rem;color:var(--success);">–</strong>
                    </div>
                </div>
            </div>

            <!-- Quotas & Risques de Blocage (temps réel) -->
            <div class="glass-panel span-5" style="display:flex;flex-direction:column;gap:var(--space-md);">
                <div class="section-title">
                    <span>Quotas & Risques</span>
                    <div style="display:flex;gap:var(--space-sm);align-items:center;">
                        <span class="subtitle" id="dash-quotas-status">Temps réel</span>
                        <button class="btn btn--ghost btn--xs" onclick="loadDashboardData()" style="padding:2px 6px;">↺</button>
                    </div>
                </div>
                <div id="dash-quota-risks" style="display:flex;flex-direction:column;gap:var(--space-md);">
                    <div class="empty-state" style="padding:var(--space-md);">
                        <div class="empty-state__icon">⏳</div>
                        <div>Chargement des quotas...</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- ═══ Graphe Coût Réel 30j ═══ -->
        <div class="glass-panel" style="display:flex;flex-direction:column;gap:var(--space-lg);margin-top:var(--space-xl);">
            <div class="section-title">
                <span>📈 Coût Réel — 30 derniers jours</span>
                <span class="subtitle" id="dash-chart-subtitle">Par jour · Survol = détail</span>
            </div>
            <div id="dash-cost-chart" style="position:relative;width:100%;height:160px;">
                <canvas id="dash-cost-canvas" style="width:100%;height:160px;"></canvas>
                <div id="dash-chart-tooltip" style="
                    display:none;position:absolute;background:rgba(11,13,19,0.95);
                    border:1px solid rgba(129,140,248,0.3);border-radius:8px;padding:0.5rem 0.75rem;
                    font-size:0.72rem;pointer-events:none;z-index:100;white-space:nowrap;"></div>
            </div>
            <!-- Légende -->
            <div style="display:flex;gap:var(--space-xl);flex-wrap:wrap;font-size:0.72rem;color:var(--text-muted);">
                <span style="display:flex;align-items:center;gap:4px;"><span style="width:10px;height:3px;background:var(--accent-primary);display:inline-block;border-radius:2px;"></span> Claude (abo)</span>
                <span style="display:flex;align-items:center;gap:4px;"><span style="width:10px;height:3px;background:var(--accent-secondary);display:inline-block;border-radius:2px;"></span> Gemini (abo)</span>
                <span style="display:flex;align-items:center;gap:4px;"><span style="width:10px;height:3px;background:var(--color-deepseek);display:inline-block;border-radius:2px;"></span> DeepSeek (API)</span>
                <span style="display:flex;align-items:center;gap:4px;"><span style="width:10px;height:3px;background:var(--success);display:inline-block;border-radius:2px;"></span> Gemini (API gratuit)</span>
            </div>
        </div>

        <!-- ═══ Ligne basse : Widget Elo + Dernières Sessions ═══ -->
        <div class="grid grid-12" style="gap:var(--space-xl);margin-top:var(--space-xl);">

            <!-- Widget Top 5 Routage Elo -->
            <div class="glass-panel span-4" style="display:flex;flex-direction:column;gap:var(--space-lg);">
                <div class="section-title">
                    <span>🏆 Top Routage Elo</span>
                    <span class="subtitle" id="dash-elo-count">–</span>
                </div>
                <div id="elo-ranking-list" style="display:flex;flex-direction:column;gap:var(--space-sm);">
                    <div class="empty-state" style="padding:var(--space-lg);">
                        <div class="empty-state__icon">📊</div>
                        <div>Chargement...</div>
                    </div>
                </div>
                <div style="border-top:1px solid var(--border-color);padding-top:var(--space-md);font-size:0.75rem;color:var(--text-muted);">
                    <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
                        <span>Décisions totales :</span>
                        <strong id="dash-routing-total">–</strong>
                    </div>
                    <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
                        <span>Fast-path utilisé :</span>
                        <strong id="dash-routing-fastpath" style="color:var(--success);">–</strong>
                    </div>
                    <div style="display:flex;justify-content:space-between;">
                        <span>LLM classifier :</span>
                        <strong id="dash-routing-llm" style="color:var(--accent-secondary);">–</strong>
                    </div>
                </div>
            </div>

            <!-- Dernières Sessions -->
            <div class="glass-panel span-8">
                <div class="section-title">
                    <span>Dernières Sessions</span>
                    <span class="subtitle" id="dash-sessions-count">–</span>
                </div>
                <div style="overflow-x:auto;">
                    <table class="data-table" id="dash-sessions-table">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>Objectif</th>
                                <th style="text-align:right;">Tokens</th>
                                <th style="text-align:right;">Coût réel</th>
                                <th style="text-align:right;">Canal</th>
                            </tr>
                        </thead>
                        <tbody id="dash-sessions-body">
                            <tr><td colspan="5" class="empty-state" style="padding:var(--space-xl);">Chargement...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>

        </div><!-- fin grille basse -->
    `;

    // Hover sur KPI coût
    const kpiCostCard = document.getElementById('kpi-card-cost');
    const kpiCostTooltip = document.getElementById('kpi-cost-tooltip');
    if (kpiCostCard && kpiCostTooltip) {
        kpiCostCard.addEventListener('mouseenter', () => { kpiCostTooltip.style.display = 'block'; });
        kpiCostCard.addEventListener('mouseleave', () => { kpiCostTooltip.style.display = 'none'; });
    }

    loadDashboardData();
}

function renderPipelineFlowchart() {
    return `
        <div id="pipeline-graph" style="position:relative;width:100%;height:160px;">
            <svg width="100%" height="160" viewBox="0 0 720 160" xmlns="http://www.w3.org/2000/svg" style="overflow:visible;">
                <defs>
                    <linearGradient id="flow-grad" x1="0%" y1="0%" x2="100%" y2="0%">
                        <stop offset="0%" style="stop-color:rgba(129,140,248,0.1)"/>
                        <stop offset="50%" style="stop-color:rgba(129,140,248,0.6)"/>
                        <stop offset="100%" style="stop-color:rgba(129,140,248,0.1)"/>
                    </linearGradient>
                </defs>
                <line x1="110" y1="55" x2="175" y2="55" stroke="rgba(129,140,248,0.2)" stroke-width="2" stroke-dasharray="4,4" class="pipe-line" id="pipe-1-2"/>
                <line x1="290" y1="55" x2="345" y2="55" stroke="rgba(245,158,11,0.2)" stroke-width="2" stroke-dasharray="4,4" class="pipe-line" id="pipe-2-3"/>
                <line x1="445" y1="55" x2="505" y2="55" stroke="rgba(16,185,129,0.2)" stroke-width="2" stroke-dasharray="4,4" class="pipe-line" id="pipe-3-4"/>
                <line x1="610" y1="55" x2="660" y2="55" stroke="rgba(59,130,246,0.2)" stroke-width="2" stroke-dasharray="4,4" class="pipe-line" id="pipe-4-5"/>
                <line x1="555" y1="75" x2="555" y2="115" stroke="rgba(217,70,239,0.2)" stroke-width="2" stroke-dasharray="3,3" class="pipe-line" id="pipe-4-r"/>

                <circle r="3" fill="var(--accent-primary)" opacity="0">
                    <animate attributeName="opacity" values="0;0.9;0" dur="2.5s" repeatCount="indefinite" begin="0s"/>
                    <animateMotion dur="2.5s" repeatCount="indefinite" begin="0s">
                        <mpath href="#flow-path-full"/>
                    </animateMotion>
                </circle>
                <path id="flow-path-full" d="M 55 55 L 175 55 L 290 55 L 345 55 L 445 55 L 505 55 L 610 55 L 680 55" fill="none" stroke="none"/>

                <g id="node-request" class="pipeline-node" data-agent="request">
                    <rect x="15" y="30" width="95" height="50" rx="10" fill="rgba(129,140,248,0.08)" stroke="rgba(129,140,248,0.3)" stroke-width="1.5"/>
                    <text x="62" y="53" text-anchor="middle" fill="var(--text-primary)" font-size="11" font-weight="600" font-family="Inter, sans-serif">📝 Requête</text>
                    <text x="62" y="70" text-anchor="middle" fill="var(--text-muted)" font-size="8" font-family="Inter, sans-serif">Objectif</text>
                </g>
                <g id="node-planner" class="pipeline-node" data-agent="planner">
                    <rect x="175" y="30" width="115" height="50" rx="10" fill="rgba(167,139,250,0.08)" stroke="rgba(167,139,250,0.3)" stroke-width="1.5"/>
                    <text x="232" y="53" text-anchor="middle" fill="var(--text-primary)" font-size="11" font-weight="600" font-family="Inter, sans-serif">🧠 Planner</text>
                    <text x="232" y="70" text-anchor="middle" fill="var(--text-muted)" font-size="8" font-family="Inter, sans-serif" id="node-planner-model">Plan &amp; DAG</text>
                </g>
                <g id="node-router" class="pipeline-node" data-agent="router">
                    <rect x="345" y="30" width="100" height="50" rx="10" fill="rgba(245,158,11,0.08)" stroke="rgba(245,158,11,0.3)" stroke-width="1.5"/>
                    <text x="395" y="53" text-anchor="middle" fill="var(--text-primary)" font-size="11" font-weight="600" font-family="Inter, sans-serif">⚙️ Router</text>
                    <text x="395" y="70" text-anchor="middle" fill="var(--text-muted)" font-size="8" font-family="Inter, sans-serif">Sélection LLM</text>
                </g>
                <g id="node-executor" class="pipeline-node" data-agent="executor">
                    <rect x="505" y="30" width="105" height="50" rx="10" fill="rgba(16,185,129,0.08)" stroke="rgba(16,185,129,0.3)" stroke-width="1.5"/>
                    <text x="557" y="53" text-anchor="middle" fill="var(--text-primary)" font-size="11" font-weight="600" font-family="Inter, sans-serif">🔧 Executor</text>
                    <text x="557" y="70" text-anchor="middle" fill="var(--text-muted)" font-size="8" font-family="Inter, sans-serif" id="node-executor-model">ReAct Loop</text>
                </g>
                <g id="node-result" class="pipeline-node" data-agent="result">
                    <rect x="660" y="30" width="55" height="50" rx="10" fill="rgba(59,130,246,0.08)" stroke="rgba(59,130,246,0.3)" stroke-width="1.5"/>
                    <text x="687" y="58" text-anchor="middle" fill="var(--text-primary)" font-size="15">✅</text>
                </g>
                <g id="node-reviewer" class="pipeline-node" data-agent="reviewer">
                    <rect x="490" y="115" width="130" height="40" rx="8" fill="rgba(217,70,239,0.06)" stroke="rgba(217,70,239,0.25)" stroke-width="1" stroke-dasharray="4,2"/>
                    <text x="555" y="138" text-anchor="middle" fill="rgba(217,70,239,0.8)" font-size="10" font-weight="600" font-family="Inter, sans-serif">🔍 Reviewer V5.2</text>
                </g>
            </svg>
        </div>
        <div style="font-size:0.78rem;color:var(--text-secondary);line-height:1.45;max-width:700px;">
            Le moteur sélectionne automatiquement le modèle le plus adapté.
            Le <strong style="color:rgba(217,70,239,0.8);">Reviewer</strong> valide automatiquement les résultats post-DAG.
        </div>
    `;
}

function highlightPipelineNode(agentName) {
    document.querySelectorAll('.pipeline-node rect').forEach(rect => {
        rect.style.filter = '';
        rect.style.strokeWidth = '';
    });
    const nodeMap = {
        'planner': 'node-planner',
        'router': 'node-router',
        'executor': 'node-executor',
        'reviewer': 'node-reviewer',
        'result': 'node-result'
    };
    const nodeId = nodeMap[agentName];
    if (nodeId) {
        const node = document.getElementById(nodeId);
        if (node) {
            const rect = node.querySelector('rect');
            if (rect) {
                rect.style.filter = 'drop-shadow(0 0 12px currentColor)';
                rect.style.strokeWidth = '2.5';
            }
        }
    }
}

// ═══════════════════════════════════════════════════════════════
// CHARGEMENT DES DONNÉES
// ═══════════════════════════════════════════════════════════════

async function loadDashboardData() {
    loadEloRanking().catch(() => {});

    try {
        const [tokens, quotas, claudeRTRes] = await Promise.allSettled([
            fetchTokens(),
            fetchQuotasSliding(),
            fetch('/api/quotas/claude-realtime').then(r => r.ok ? r.json() : null)
        ]);

        const tk = tokens.status === 'fulfilled' ? tokens.value : null;
        const qt = quotas.status === 'fulfilled' ? quotas.value : null;
        const claudeRT = claudeRTRes.status === 'fulfilled' ? claudeRTRes.value : null;

        // ── KPI Tokens ──
        if (tk && tk.combined_total) {
            const ct = tk.combined_total;
            document.getElementById('kpi-tokens').textContent = formatGaugeValue(ct.grand_total || 0, '');
            document.getElementById('kpi-moteur-tokens').textContent = formatGaugeValue(ct.moteur_tokens || 0, '');
            document.getElementById('kpi-cli-tokens').textContent = formatGaugeValue(ct.cli_tokens || 0, '');
        } else if (tk && tk.total) {
            document.getElementById('kpi-tokens').textContent = formatGaugeValue(tk.total.total_tokens || 0, '');
            document.getElementById('kpi-moteur-tokens').textContent = formatGaugeValue(tk.total.prompt_tokens || 0, '');
            document.getElementById('kpi-cli-tokens').textContent = '–';
        }

        // ── KPI Coûts (source : combined_total et real_billing) ──
        if (tk && tk.combined_total) {
            const ct = tk.combined_total;
            // Détail par canal
            const bySource = ct.by_source || [];
            const ideSource = bySource.find(s => s.source === 'antigravity_ide');
            const claudeSource = bySource.find(s => s.source === 'claude_cli');
            const aboTotal = (ideSource?.cost_usd || 0) + (claudeSource?.cost_usd || 0);
            const apiPayant = tk.real_billing?.deepseek_balance_usd != null
                ? (20 - tk.real_billing.deepseek_balance_usd) : 0;
            const gcpCost = tk.real_billing?.gemini_gcp_cost_usd || 0;

            // Coût réel total = abonnements (prorata) + API payantes directes
            const totalRealCost = aboTotal + apiPayant + gcpCost;
            document.getElementById('kpi-cost').textContent = `${totalRealCost.toFixed(2)} $`;

            // Coût estimé total = estimation moteur + estimation CLI/IDE
            const totalEstimatedCost = (ct.moteur_cost_usd || 0) + (ct.cli_cost_estimated_usd || 0);
            const estEl = document.getElementById('kpi-cost-estimated');
            if (estEl) {
                estEl.textContent = `${totalEstimatedCost.toFixed(3)} $`;
            }

            document.getElementById('kpi-cost-abo').textContent =
                `Abo: ${aboTotal.toFixed(2)} $`;
            document.getElementById('kpi-cost-api').textContent =
                `API: ${(apiPayant + gcpCost).toFixed(3)} $`;

            // Détail tooltip
            const breakdown = document.getElementById('kpi-cost-breakdown');
            if (breakdown) {
                breakdown.innerHTML = `
                    <div style="font-weight:700;color:var(--accent-secondary);margin-bottom:0.4rem;border-bottom:1px solid var(--border-color);padding-bottom:0.25rem;">Détail des coûts réels</div>
                    ${bySource.map(s => `
                        <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
                             <span style="color:var(--text-muted);">${s.source === 'antigravity_ide' ? '💻 Antigravity IDE' : s.source === 'claude_cli' ? '🤖 Claude Code CLI' : s.source}</span>
                            <strong style="color:var(--text-primary);">${(s.cost_usd || 0).toFixed(2)} $</strong>
                        </div>
                        <div style="font-size:0.65rem;color:var(--text-muted);margin-bottom:4px;margin-left:4px;">${s.sessions} sessions · ${formatGaugeValue(s.tokens || 0, '')} tokens</div>
                    `).join('')}
                    ${tk.real_billing?.deepseek_balance_usd != null ? `
                        <div style="display:flex;justify-content:space-between;margin-bottom:3px;margin-top:4px;border-top:1px solid var(--border-color);padding-top:4px;">
                            <span style="color:var(--text-muted);">🐋 DeepSeek (prépayé)</span>
                            <strong style="color:var(--color-deepseek);">${apiPayant.toFixed(2)} $ consommés</strong>
                        </div>
                    ` : ''}
                    ${tk.real_billing?.gemini_gcp_cost_usd != null && tk.real_billing.gemini_gcp_cost_usd > 0 ? `
                        <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
                            <span style="color:var(--text-muted);">☁️ Gemini API GCP</span>
                            <strong style="color:var(--success);">${tk.real_billing.gemini_gcp_cost_usd.toFixed(4)} $</strong>
                        </div>
                    ` : ''}
                    <div style="margin-top:6px;padding-top:4px;border-top:1px solid var(--border-color);font-size:0.65rem;color:var(--text-muted);line-height:1.4;">
                        Abonnements : prorata 0.57$/M tokens · Gemini CLI 19.99$/mois amortis
                    </div>
                `;
            }
        }

        // ── KPI DeepSeek ──
        if (tk && tk.real_billing) {
            const rb = tk.real_billing;
            if (rb.deepseek_balance_usd != null) {
                const bal = rb.deepseek_balance_usd;
                const consumed = Math.max(0, 20 - bal);
                document.getElementById('kpi-deepseek').textContent = `${bal.toFixed(2)} $`;
                document.getElementById('kpi-deepseek-used').textContent = `Consommé : ${consumed.toFixed(2)} $`;
                document.getElementById('kpi-deepseek-sync').textContent =
                    rb.deepseek_last_sync ? 'Sync : ' + new Date(rb.deepseek_last_sync).toLocaleTimeString('fr-FR') : 'Sync: –';
            }
        }

        // ── KPI Sessions ──
        if (tk && tk.combined_total && tk.combined_total.by_source) {
            const bySource = tk.combined_total.by_source;
            const ideSessions = bySource.find(s => s.source === 'antigravity_ide')?.sessions || 0;
            const cliSessions = bySource.find(s => s.source === 'claude_cli')?.sessions || 0;
            const moteurSessions = Object.keys(tk.sessions || {}).length;
            const totalSess = ideSessions + cliSessions + moteurSessions;
            document.getElementById('kpi-sessions').textContent = totalSess;
            document.getElementById('kpi-sessions-moteur').textContent = moteurSessions;
            document.getElementById('kpi-sessions-ide').textContent = ideSessions + cliSessions;
        }

        // ── Graphe 30j ──
        if (tk && tk.history && tk.history.length > 0) {
            renderCostChart30Days(tk.history);
        }

        // ── Widget Quotas & Risques ──
        if (qt) {
            renderQuotaRisks(qt, tk, claudeRT);
        }

        // ── Dernières Sessions ──
        await loadDashboardSessions(tk);

        // ── Config badges header ──
        try {
            const config = await fetchConfig();
            if (config) {
                const bp = document.getElementById('badge-planner');
                const be = document.getElementById('badge-executor');
                const bx = document.getElementById('badge-expert');
                if (bp) bp.innerText = config.planner_model || '–';
                if (be) be.innerText = config.executor_model || '–';
                if (bx) bx.innerText = config.antigravity_model || '–';
            }
        } catch {}

    } catch (err) {
        console.error('[Dashboard] Erreur de chargement:', err);
    }
}

// ═══════════════════════════════════════════════════════════════
// GRAPHE COÛT 30 JOURS (Canvas inline)
// ═══════════════════════════════════════════════════════════════

function renderCostChart30Days(history) {
    const canvas = document.getElementById('dash-cost-canvas');
    if (!canvas) return;

    const now = new Date();
    const days30 = 30;

    // Regrouper les transactions par jour et par modèle/canal
    const dayMap = {};
    for (let i = 0; i < days30; i++) {
        const d = new Date(now);
        d.setDate(d.getDate() - (days30 - 1 - i));
        const key = d.toISOString().slice(0, 10);
        dayMap[key] = { claude: 0, gemini: 0, deepseek: 0, gratuit: 0, total: 0, date: new Date(d) };
    }

    // Remplir avec les données d'historique
    history.forEach(h => {
        const key = h.timestamp.slice(0, 10);
        if (!dayMap[key]) return;
        const cost = h.cost_usd || 0;
        const model = (h.model || '').toLowerCase();
        if (model.startsWith('claude')) {
            dayMap[key].claude += cost;
        } else if (model.includes('gemini') && cost > 0 && cost < 0.01) {
            dayMap[key].gratuit += cost;
        } else if (model.includes('gemini')) {
            dayMap[key].gemini += cost;
        } else if (model.includes('deepseek')) {
            dayMap[key].deepseek += cost;
        }
        dayMap[key].total += cost;
    });

    const days = Object.values(dayMap);
    const maxCost = Math.max(...days.map(d => d.total), 0.001);

    // Calcul des totaux par semaine (pour le tooltip au survol)
    const weekTotals = [0, 0, 0, 0];
    days.forEach((d, i) => {
        const w = Math.floor(i / 7);
        if (w < 4) weekTotals[w] += d.total;
    });

    const W = canvas.offsetWidth || 800;
    const H = 150;
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext('2d');

    const pad = { left: 40, right: 12, top: 10, bottom: 28 };
    const chartW = W - pad.left - pad.right;
    const chartH = H - pad.top - pad.bottom;
    const barW = Math.max(2, (chartW / days30) - 2);

    ctx.clearRect(0, 0, W, H);

    // Grille horizontale
    ctx.strokeStyle = 'rgba(255,255,255,0.04)';
    ctx.lineWidth = 1;
    [0.25, 0.5, 0.75, 1.0].forEach(r => {
        const y = pad.top + chartH * (1 - r);
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(W - pad.right, y);
        ctx.stroke();
        // Labels Y
        ctx.fillStyle = 'rgba(255,255,255,0.25)';
        ctx.font = '9px Inter, sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText(`${(maxCost * r).toFixed(3)}$`, pad.left - 3, y + 3);
    });

    // Barres empilées par jour
    const colors = {
        claude:   'rgba(129,140,248,0.85)',
        gemini:   'rgba(167,139,250,0.7)',
        deepseek: 'rgba(14,165,233,0.85)',
        gratuit:  'rgba(16,185,129,0.5)'
    };

    days.forEach((d, i) => {
        const x = pad.left + i * (chartW / days30) + 1;
        const totalH = (d.total / maxCost) * chartH;
        let stackY = pad.top + chartH;

        ['gratuit', 'gemini', 'deepseek', 'claude'].forEach(key => {
            const val = d[key];
            if (val <= 0) return;
            const h = (val / maxCost) * chartH;
            stackY -= h;
            ctx.fillStyle = colors[key];
            ctx.fillRect(x, stackY, barW, h);
        });

        // Label axe X (lun/dimanche uniquement)
        const dow = d.date.getDay();
        if (dow === 1 || i === 0 || i === days30 - 1) {
            ctx.fillStyle = 'rgba(255,255,255,0.3)';
            ctx.font = '8px Inter, sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(
                d.date.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' }),
                x + barW / 2,
                H - 4
            );
        }
    });

    // Interaction survol — tooltip
    const tooltip = document.getElementById('dash-chart-tooltip');
    const subtitle = document.getElementById('dash-chart-subtitle');
    const totalAll = days.reduce((s, d) => s + d.total, 0);

    if (subtitle) subtitle.textContent =
        `30j total : ${totalAll.toFixed(3)} $ · Semaine 1: ${weekTotals[0].toFixed(3)} $ · S2: ${weekTotals[1].toFixed(3)} $ · S3: ${weekTotals[2].toFixed(3)} $ · S4: ${weekTotals[3].toFixed(3)} $`;

    canvas.onmousemove = function(e) {
        const rect = canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const colW = chartW / days30;
        const idx = Math.floor((mx - pad.left) / colW);
        if (idx >= 0 && idx < days30 && tooltip) {
            const d = days[idx];
            const weekIdx = Math.floor(idx / 7);
            tooltip.style.display = 'block';
            tooltip.style.left = Math.min(mx - 40, W - 200) + 'px';
            tooltip.style.top = '0px';
            tooltip.innerHTML = `
                <div style="font-weight:700;color:var(--accent-secondary);margin-bottom:4px;">
                    ${d.date.toLocaleDateString('fr-FR', { weekday:'short', day:'2-digit', month:'long' })}
                </div>
                <div style="color:var(--text-muted);font-size:0.65rem;margin-bottom:4px;">Semaine ${weekIdx + 1} · Total S${weekIdx + 1}: <strong style="color:var(--text-primary);">${weekTotals[weekIdx].toFixed(3)} $</strong></div>
                ${d.claude > 0 ? `<div style="color:rgba(129,140,248,0.9);">Claude (abo) : ${d.claude.toFixed(4)} $</div>` : ''}
                ${d.gemini > 0 ? `<div style="color:rgba(167,139,250,0.9);">Gemini (abo) : ${d.gemini.toFixed(4)} $</div>` : ''}
                ${d.deepseek > 0 ? `<div style="color:rgba(14,165,233,0.9);">DeepSeek API : ${d.deepseek.toFixed(4)} $</div>` : ''}
                ${d.gratuit > 0 ? `<div style="color:rgba(16,185,129,0.8);">Gemini gratuit : ${d.gratuit.toFixed(4)} $</div>` : ''}
                <div style="border-top:1px solid var(--border-color);margin-top:4px;padding-top:4px;font-weight:700;color:var(--text-primary);">Total : ${d.total.toFixed(4)} $</div>
            `;
        }
    };
    canvas.onmouseleave = function() {
        if (tooltip) tooltip.style.display = 'none';
    };
}

// ═══════════════════════════════════════════════════════════════
// SECTION QUOTAS & RISQUES DE BLOCAGE
// ═══════════════════════════════════════════════════════════════

/**
 * Calcule et affiche les risques de blocage des quotas
 * avec un timing estimé de prochain dépassement
 */
function renderQuotaRisks(quotas, tokens, claudeRT) {
    const container = document.getElementById('dash-quota-risks');
    if (!container) return;

    const now = new Date();

    // ── Données réelles Claude.ai Pro si disponibles ──
    const cHasRT         = claudeRT && (claudeRT.session_pct != null || claudeRT.weekly_pct != null);
    const cSessionPct    = claudeRT?.session_pct ?? null;
    const cSessionReset  = claudeRT?.session_reset_mins ?? null;
    const cWeeklyPct     = claudeRT?.weekly_pct ?? null;
    const cWeeklyReset   = claudeRT?.weekly_reset_mins ?? null;

    const claudeLimits = [];
    if (cHasRT) {
        if (cSessionPct != null) {
            claudeLimits.push({
                label: 'Session',
                value: cSessionPct,
                max: 100,
                unit: '%',
                rechargeType: 'minute_reset',
                rechargeMins: cSessionReset,
                critical: 0.75,
                desc: 'Limite de la session Claude Pro en cours'
            });
        }
        if (cWeeklyPct != null) {
            claudeLimits.push({
                label: 'Hebdo',
                value: cWeeklyPct,
                max: 100,
                unit: '%',
                rechargeType: 'minute_reset',
                rechargeMins: cWeeklyReset,
                critical: 0.8,
                desc: 'Limite hebdomadaire Claude Pro globale'
            });
        }
    } else {
        // Fallback historique si pas de données réelles (moteur uniquement)
        claudeLimits.push({
            label: 'Fenêtre 1h',
            value: quotas.claude_cli_tph || 0,
            max: 1500000,
            unit: 'tok',
            rechargeType: 'glissant',
            rechargeH: 1,
            critical: 0.8,
            desc: '1.5M tokens/heure (glissant moteur)'
        });
        claudeLimits.push({
            label: 'Mensuel',
            value: quotas.claude_cli_tpm || 0,
            max: 35000000,
            unit: 'tok',
            rechargeType: 'mensuel',
            rechargeDay: 1,
            critical: 0.85,
            desc: '35M tokens/mois (moteur) — reset le 1er'
        });
    }

    // Définir les forfaits avec leurs limites réelles
    const plans = [
        {
            id: 'claude-pro',
            label: cHasRT ? 'Claude Pro (Réel)' : 'Claude Code CLI',
            icon: '🤖',
            color: 'var(--accent-primary)',
            limits: claudeLimits
        },
        {
            id: 'gemini-cli',
            label: 'Gemini CLI (Abo)',
            icon: '💻',
            color: 'var(--accent-secondary)',
            limits: [
                {
                    label: 'Fenêtre 1h',
                    value: quotas.gemini_cli_tph || 0,
                    max: 4000000,
                    unit: 'tok',
                    rechargeType: 'glissant',
                    rechargeH: 1,
                    critical: 0.8,
                    desc: '4M tokens/heure (glissant)'
                },
                {
                    label: 'Mensuel',
                    value: quotas.gemini_cli_tpm || 0,
                    max: 100000000,
                    unit: 'tok',
                    rechargeType: 'mensuel',
                    rechargeDay: 1,
                    critical: 0.9,
                    desc: '100M tokens/mois — reset le 1er'
                }
            ]
        },
        {
            id: 'gemini-free-flash',
            label: 'Gemini Free Flash',
            icon: '🆓',
            color: 'var(--success)',
            limits: [
                {
                    label: 'RPM',
                    value: quotas.gemini_free_flash_rpm || 0,
                    max: 15,
                    unit: 'req/min',
                    rechargeType: 'continu',
                    critical: 0.7,
                    desc: '15 requêtes/minute (reset continu)'
                },
                {
                    label: 'RPJ',
                    value: quotas.gemini_free_flash_rpd || 0,
                    max: 1500,
                    unit: 'req',
                    rechargeType: 'quotidien',
                    critical: 0.8,
                    desc: '1500 requêtes/jour — reset à minuit'
                }
            ]
        },
        {
            id: 'deepseek-balance',
            label: 'DeepSeek Solde',
            icon: '🐋',
            color: 'var(--color-deepseek)',
            limits: [
                {
                    label: 'Solde',
                    value: tokens?.real_billing?.deepseek_balance_usd != null
                        ? Math.max(0, 20 - tokens.real_billing.deepseek_balance_usd)
                        : 0,
                    max: 20,
                    unit: '$',
                    rechargeType: 'manuel',
                    critical: 0.8,
                    desc: 'Crédit prépayé · rechargement manuel requis'
                }
            ]
        }
    ];

    // Calcul de la prochaine reset pour mensuel
    function getNextMonthly() {
        const next = new Date(now.getFullYear(), now.getMonth() + 1, 1);
        const diffMs = next - now;
        const diffDays = Math.floor(diffMs / 86400000);
        const diffH = Math.floor((diffMs % 86400000) / 3600000);
        return `dans ${diffDays}j ${diffH}h`;
    }

    // Calcul ETA de saturation (extrapolation linéaire)
    function estimateETA(value, max, rechargeH) {
        if (value === 0) return null;
        const remaining = max - value;
        if (remaining <= 0) return '⚠️ Bloqué maintenant';
        const rate = value; // tokens dans la fenêtre actuelle
        if (rate === 0) return null;
        const hoursLeft = (remaining / rate) * (rechargeH || 1);
        if (hoursLeft < 0.1) return '< 6 min';
        if (hoursLeft < 1) return `< ${Math.ceil(hoursLeft * 60)} min`;
        return `~${hoursLeft.toFixed(1)}h`;
    }

    let html = '';

    plans.forEach(plan => {
        plan.limits.forEach((limit, idx) => {
            const pct = Math.min(100, (limit.value / limit.max) * 100);
            const isCritical = pct >= limit.critical * 100;
            const isWarning = pct >= limit.critical * 100 * 0.6;
            const isEmpty = limit.value === 0;

            let statusColor = 'var(--success)';
            let statusLabel = 'OK';
            if (isCritical) { statusColor = 'var(--error)'; statusLabel = 'CRITIQUE'; }
            else if (isWarning) { statusColor = 'var(--warning)'; statusLabel = 'Attention'; }
            else if (isEmpty) { statusColor = 'var(--text-muted)'; statusLabel = 'Libre'; }

            // Timing de recharge
            let rechargeStr = '';
            if (limit.rechargeType === 'mensuel') {
                rechargeStr = `Reset ${getNextMonthly()}`;
            } else if (limit.rechargeType === 'quotidien') {
                const midnight = new Date(now);
                midnight.setDate(midnight.getDate() + 1);
                midnight.setHours(0, 0, 0, 0);
                const diffH = Math.ceil((midnight - now) / 3600000);
                rechargeStr = `Minuit dans ${diffH}h`;
            } else if (limit.rechargeType === 'minute_reset') {
                rechargeStr = limit.rechargeMins != null
                    ? `Reset dans ${Math.floor(limit.rechargeMins / 60)}h ${limit.rechargeMins % 60}min`
                    : 'Reset fenêtre Pro';
            } else if (limit.rechargeType === 'glissant') {
                rechargeStr = 'Fenêtre glissante 1h';
            } else if (limit.rechargeType === 'continu') {
                rechargeStr = 'Reset continu';
            } else if (limit.rechargeType === 'manuel') {
                rechargeStr = tokens?.real_billing?.deepseek_balance_usd != null
                    ? `Solde restant : ${tokens.real_billing.deepseek_balance_usd.toFixed(2)} $`
                    : 'Rechargement manuel';
            }

            // ETA blocage
            const eta = limit.rechargeType === 'glissant'
                ? estimateETA(limit.value, limit.max, limit.rechargeH) : null;

            // Format de la valeur
            const valStr = limit.unit === '$'
                ? `${limit.value.toFixed(2)} / ${limit.max} ${limit.unit}`
                : `${formatGaugeValue(limit.value, '')} / ${formatGaugeValue(limit.max, '')} ${limit.unit}`;

            html += `
                <div class="quota-risk-row ${isCritical ? 'quota-risk-row--critical' : isWarning ? 'quota-risk-row--warning' : ''}"
                     style="padding:var(--space-sm) var(--space-md);border-radius:var(--radius-md);background:rgba(255,255,255,0.02);border:1px solid ${isCritical ? 'rgba(239,68,68,0.25)' : isWarning ? 'rgba(245,158,11,0.15)' : 'var(--border-color)'};cursor:pointer;transition:background 0.15s;"
                     onmouseenter="this.style.background='rgba(255,255,255,0.04)'"
                     onmouseleave="this.style.background='rgba(255,255,255,0.02)'"
                     title="${limit.desc}&#10;${rechargeStr}${eta ? '&#10;Blocage estimé : ' + eta : ''}">
                    <!-- En-tête ligne -->
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;">
                        <div style="display:flex;align-items:center;gap:6px;min-width:0;">
                            <span style="font-size:0.85rem;">${idx === 0 ? plan.icon : '  '}</span>
                            <span style="font-size:0.72rem;font-weight:600;color:${plan.color};white-space:nowrap;">${idx === 0 ? plan.label : ''}</span>
                            <span style="font-size:0.65rem;color:var(--text-muted);background:rgba(255,255,255,0.04);padding:1px 5px;border-radius:3px;">${limit.label}</span>
                        </div>
                        <div style="display:flex;align-items:center;gap:6px;flex-shrink:0;">
                            ${eta && !isCritical ? `<span style="font-size:0.62rem;color:var(--warning);background:rgba(245,158,11,0.08);padding:1px 5px;border-radius:3px;">⏱ ${eta}</span>` : ''}
                            ${isCritical ? `<span style="font-size:0.62rem;font-weight:700;color:var(--error);background:rgba(239,68,68,0.1);padding:1px 5px;border-radius:3px;">⛔ ${statusLabel}</span>` : ''}
                            <span style="font-size:0.65rem;font-family:var(--font-mono);color:var(--text-secondary);">${valStr}</span>
                        </div>
                    </div>
                    <!-- Barre de progression -->
                    <div style="height:5px;background:rgba(255,255,255,0.05);border-radius:3px;overflow:hidden;">
                        <div style="height:100%;width:${pct.toFixed(1)}%;background:${statusColor};border-radius:3px;transition:width 0.5s ease;${isCritical ? 'animation:pulseGlow 1.5s infinite;' : ''}"></div>
                    </div>
                    <!-- Info recharge -->
                    <div style="font-size:0.62rem;color:var(--text-muted);margin-top:3px;">${rechargeStr}${isEmpty ? ' · Aucune consommation' : ` · ${pct.toFixed(1)}% utilisé`}</div>
                </div>
            `;
        });
    });

    container.innerHTML = html;

    // Statut global
    const statusEl = document.getElementById('dash-quotas-status');
    const hasCritical = plans.some(p => p.limits.some(l => (l.value / l.max) >= l.critical));
    if (statusEl) {
        statusEl.textContent = hasCritical ? '⚠️ Quota critique' : '✅ Quotas OK';
        statusEl.style.color = hasCritical ? 'var(--warning)' : 'var(--success)';
    }
}

// ═══════════════════════════════════════════════════════════════
// SESSIONS TABLE
// ═══════════════════════════════════════════════════════════════

async function loadDashboardSessions(tokens) {
    try {
        let allSessions = [];
        if (tokens && tokens.sessions) {
            allSessions = Object.entries(tokens.sessions)
                .map(([id, s]) => ({ id, channel: 'moteur', ...s }));
        }
        try {
            const ideData = await fetchIdeConversations(10);
            if (ideData && ideData.conversations) {
                const existingIds = new Set(allSessions.map(s => s.id));
                ideData.conversations.forEach(c => {
                    if (!existingIds.has(c.conversation_id)) {
                        allSessions.push({
                            id: c.conversation_id,
                            channel: c.source,
                            timestamp: c.timestamp,
                            objective: c.objective,
                            total_tokens: c.total_tokens,
                            estimated_cost_usd: c.estimated_cost_usd || 0,
                            is_subscription: c.is_subscription,
                            models: c.models || {},
                        });
                    }
                });
            }
        } catch { }

        allSessions.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
        const top5 = allSessions.slice(0, 5);

        const tbody = document.getElementById('dash-sessions-body');
        if (tbody) {
            document.getElementById('dash-sessions-count').textContent = `${allSessions.length} session(s)`;
            if (top5.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" class="empty-state">Aucune session enregistrée.</td></tr>';
            } else {
                tbody.innerHTML = top5.map(s => {
                    const d = new Date(s.timestamp);
                    const dateStr = d.toLocaleDateString('fr-FR') + ' ' + d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
                    const cost = s.estimated_cost_usd || 0;

                    // Label de coût : si abonnement, afficher "abo" clairement
                    const costStr = s.is_subscription
                        ? `<span style="color:var(--accent-primary);font-size:0.7rem;">~${cost.toFixed(4)} $<br><span style="font-size:0.6rem;color:var(--text-muted);">(prorata abo)</span></span>`
                        : `<span style="color:var(--success);">${cost.toFixed(4)} $</span>`;

                    // Badge canal
                    const channelBadge = s.channel === 'moteur'
                        ? `<span style="font-size:0.55rem;background:rgba(16,185,129,0.12);color:var(--success);padding:1px 4px;border-radius:3px;">MOTEUR</span>`
                        : s.channel === 'antigravity_ide' || s.channel === 'claude_cli'
                            ? `<span style="font-size:0.55rem;background:rgba(129,140,248,0.12);color:var(--accent-primary);padding:1px 4px;border-radius:3px;">IDE</span>`
                            : `<span style="font-size:0.55rem;background:rgba(255,255,255,0.05);color:var(--text-muted);padding:1px 4px;border-radius:3px;">${s.channel}</span>`;

                    return `
                        <tr>
                            <td style="white-space:nowrap;color:var(--text-secondary);font-size:0.78rem;">${dateStr}</td>
                            <td style="max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.8rem;">${s.objective || 'Non spécifié'}</td>
                            <td style="text-align:right;font-family:var(--font-mono);font-size:0.78rem;">${formatGaugeValue(s.total_tokens || 0, '')}</td>
                            <td style="text-align:right;font-family:var(--font-mono);">${costStr}</td>
                            <td style="text-align:right;">${channelBadge}</td>
                        </tr>
                    `;
                }).join('');
            }
        }
    } catch (err) {
        console.warn('[Dashboard] Sessions:', err);
    }
}

// ═══════════════════════════════════════════════════════════════
// ELO RANKING
// ═══════════════════════════════════════════════════════════════

async function loadEloRanking() {
    try {
        const [eloData, routingData] = await Promise.allSettled([
            typeof fetchMetricsElo === 'function' ? fetchMetricsElo() : null,
            typeof fetchMetricsRouting === 'function' ? fetchMetricsRouting() : null,
        ]);

        const elo = eloData?.status === 'fulfilled' ? eloData.value : null;
        const rankingList = document.getElementById('elo-ranking-list');
        if (rankingList && elo) {
            const domains = Object.keys(elo.leaderboards || {});
            const firstDomain = domains[0] || 'general';
            const scores = elo.leaderboards?.[firstDomain] || [];
            const countEl = document.getElementById('dash-elo-count');

            if (scores.length > 0) {
                if (countEl) countEl.textContent = `${scores.length} modèles`;
                const top5 = scores.slice(0, 5);
                const maxScore = Math.max(...top5.map(s => s.elo || 1500));
                rankingList.innerHTML = top5.map((s, i) => {
                    const name = s.model || 'Inconnu';
                    const score = Math.round(s.elo || 1500);
                    const pct = Math.round(score / maxScore * 100);
                    const winRate = s.win_rate != null ? `${Math.round(s.win_rate)}%` : '–';
                    const medals = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣'];
                    const colors = ['var(--accent-primary)', 'var(--accent-secondary)', 'var(--success)', 'var(--text-secondary)', 'var(--text-muted)'];
                    return `
                        <div style="display:flex;align-items:center;gap:var(--space-sm);">
                            <span style="width:1.2rem;text-align:center;font-size:0.85rem;">${medals[i]}</span>
                            <div style="flex:1;min-width:0;">
                                <div style="font-size:0.78rem;font-weight:600;display:flex;justify-content:space-between;">
                                    <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${name}</span>
                                    <span style="font-size:0.68rem;color:var(--text-muted);margin-left:4px;">${winRate}</span>
                                </div>
                                <div style="height:4px;background:var(--bg-tertiary);border-radius:2px;margin-top:3px;">
                                    <div style="height:4px;width:${pct}%;background:${colors[i]};border-radius:2px;transition:width 0.6s ease;"></div>
                                </div>
                            </div>
                            <span style="font-size:0.72rem;font-family:var(--font-mono);color:${colors[i]};min-width:2.8rem;text-align:right;">${score}</span>
                        </div>
                    `;
                }).join('');
            } else {
                if (countEl) countEl.textContent = 'Pas de données';
                rankingList.innerHTML = `
                    <div style="text-align:center;padding:var(--space-lg);color:var(--text-muted);font-size:0.78rem;">
                        <div style="font-size:1.4rem;margin-bottom:var(--space-xs);">📊</div>
                        Données Elo insuffisantes<br>
                        <small>Déclencher des tâches pour alimenter le scoring</small>
                    </div>`;
            }
        }

        const routing = routingData?.status === 'fulfilled' ? routingData.value : null;
        if (routing) {
            const totalEl    = document.getElementById('dash-routing-total');
            const fastpathEl = document.getElementById('dash-routing-fastpath');
            const llmEl      = document.getElementById('dash-routing-llm');
            if (totalEl)    totalEl.textContent    = routing.total_decisions ?? '–';
            if (fastpathEl) fastpathEl.textContent = routing.fast_path_count != null
                ? `${routing.fast_path_count} (${Math.round(routing.fast_path_count / (routing.total_decisions || 1) * 100)}%)`
                : '–';
            if (llmEl) llmEl.textContent = routing.llm_classifier_count != null
                ? `${routing.llm_classifier_count} (${Math.round(routing.llm_classifier_count / (routing.total_decisions || 1) * 100)}%)`
                : '–';
        }

    } catch (err) {
        console.error('[Dashboard] Erreur chargement Elo:', err);
    }
}

/**
 * Met à jour la barre de statut d'exécution live du Dashboard.
 * Appelé depuis handleSSEEvent dans sse.js.
 */
function updateDashboardLiveStatus(state, status, activeAgent) {
    const liveStatusPanel = document.getElementById('dash-engine-live-status');
    if (!liveStatusPanel) return;

    if (status === 'running' && state) {
        liveStatusPanel.style.display = 'block';
        
        // Mettre à jour la phase
        const phaseEl = document.getElementById('dash-live-phase');
        if (phaseEl) {
            const phaseLabels = {
                'init': 'Initialisation 🏁',
                'planning': 'Planification (Planner) 🧠',
                'executing': 'Exécution (DAG Runner) ⚙️',
                'reviewing': 'Revue post-DAG (Reviewer) 🔍',
                'healing': 'Auto-correction (Self-Healing) 🩹',
                'waiting_approval': 'Attente validation humaine ⏳',
                'completed': 'Terminé ✅',
                'failed': 'Échec ❌'
            };
            phaseEl.textContent = phaseLabels[state.current_phase] || state.current_phase || 'Exécution';
        }

        // Mettre à jour l'agent actif
        const agentEl = document.getElementById('dash-live-agent');
        if (agentEl) {
            agentEl.textContent = activeAgent ? activeAgent.toUpperCase() : 'En attente...';
        }

        // Mettre à jour le budget/tokens de session
        const budgetEl = document.getElementById('dash-live-budget');
        if (budgetEl && state.workflow_metadata) {
            const tokens = state.workflow_metadata.total_tokens || 0;
            const cost = state.workflow_metadata.total_cost_usd || 0;
            budgetEl.textContent = `${tokens.toLocaleString()} tokens (~${cost.toFixed(4)} $)`;
        }
    } else {
        liveStatusPanel.style.display = 'none';
    }
}
