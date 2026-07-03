/* ============================================================
   APIS-QUOTAS.JS — Page 7 : APIs, Soldes & Quotas en détail
   Refonte V9 : tableau de risques, barres de consommation,
   providers avec statut détaillé, timeline de blocage.
   ============================================================ */

function renderApisQuotas(container) {
    container.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:var(--space-xl);">

        <!-- ═══ 1. Statut des Providers & Clés API ═══ -->
        <div class="glass-panel">
            <div class="section-title">
                <span>Statut des Providers & Clés API</span>
                <button class="btn btn--ghost btn--sm" onclick="refreshApisStatus()">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>
                    Rafraîchir
                </button>
            </div>
            <div class="grid grid-4" id="api-status-cards">
                <div class="empty-state"><div class="empty-state__icon">📡</div><div>Chargement...</div></div>
            </div>
        </div>

        <!-- ═══ 2. Quotas & Forfaits — Consommation en temps réel ═══ -->
        <div class="glass-panel">
            <div class="section-title">
                <span>⚡ Quotas & Forfaits — Consommation en temps réel</span>
                <div style="display:flex;gap:var(--space-sm);align-items:center;">
                    <span class="subtitle" id="quotas-last-update">Calcul en cours...</span>
                    <button class="btn btn--warning btn--sm" onclick="triggerRefreshClaude()" id="btn-refresh-claude-usage" title="Force le refresh Claude /usage">
                        💻 Sync Claude
                    </button>
                </div>
            </div>

            <!-- Alertes de blocage imminents -->
            <div id="quota-alerts" style="margin-bottom:var(--space-lg);"></div>

            <!-- Tableau des jauges par forfait -->
            <div style="display:flex;flex-direction:column;gap:var(--space-lg);" id="quota-detail-container">
                <div class="empty-state" style="padding:var(--space-xl);">
                    <div class="empty-state__icon">⏳</div>
                    <div>Chargement des quotas...</div>
                </div>
            </div>
        </div>

        <!-- ═══ 3. Bilans Financiers ═══ -->
        <div class="grid grid-3" style="gap:var(--space-xl);">

            <!-- Claude Pro (abonnement) -->
            <div class="glass-panel" style="display:flex;flex-direction:column;gap:var(--space-md);">
                <div style="display:flex;align-items:center;gap:var(--space-sm);">
                    <span style="font-size:1.5rem;">🤖</span>
                    <div>
                        <div style="font-weight:700;color:var(--accent-primary);">Claude Pro</div>
                        <div style="font-size:0.7rem;color:var(--text-muted);">Abonnement · 20 $/mois</div>
                    </div>
                </div>
                <div id="claude-billing-detail" style="font-size:0.78rem;display:flex;flex-direction:column;gap:var(--space-xs);">
                    <div style="color:var(--text-muted);">Chargement...</div>
                </div>
            </div>

            <!-- Gemini Advanced / Antigravity IDE -->
            <div class="glass-panel" style="display:flex;flex-direction:column;gap:var(--space-md);">
                <div style="display:flex;align-items:center;gap:var(--space-sm);">
                    <span style="font-size:1.5rem;">💻</span>
                    <div>
                        <div style="font-weight:700;color:var(--accent-secondary);">Gemini Advanced + IDE</div>
                        <div style="font-size:0.7rem;color:var(--text-muted);">Abonnement · 19.99 $/mois</div>
                    </div>
                </div>
                <div id="gemini-billing-detail" style="font-size:0.78rem;display:flex;flex-direction:column;gap:var(--space-xs);">
                    <div style="color:var(--text-muted);">Chargement...</div>
                </div>
            </div>

            <!-- DeepSeek & GCP $$$ -->
            <div class="glass-panel" style="display:flex;flex-direction:column;gap:var(--space-md);">
                <div style="display:flex;align-items:center;gap:var(--space-sm);">
                    <span style="font-size:1.5rem;">🐋</span>
                    <div>
                        <div style="font-weight:700;color:var(--color-deepseek);">DeepSeek API</div>
                        <div style="font-size:0.7rem;color:var(--text-muted);">Prépayé · Solde restant</div>
                    </div>
                </div>
                <div id="deepseek-billing-detail" style="font-size:0.78rem;display:flex;flex-direction:column;gap:var(--space-xs);">
                    <div style="color:var(--text-muted);">Chargement...</div>
                </div>
                <!-- GCP billing séparé -->
                <div style="border-top:1px solid var(--border-color);padding-top:var(--space-sm);margin-top:var(--space-xs);">
                    <div style="font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">Gemini API GCP (Payant)</div>
                    <div id="gcp-billing-detail" style="font-size:0.78rem;color:var(--text-secondary);">Chargement...</div>
                    <div style="display:flex;gap:var(--space-sm);margin-top:var(--space-sm);">
                        <button id="btn-sync-billing" class="btn btn--ghost btn--xs" onclick="triggerSyncBilling()" style="font-size:0.7rem;">
                            Synchro GCP
                        </button>
                    </div>
                </div>
            </div>
        </div>

        <!-- ═══ 4. Tendances Historiques (BDD SQLite) ═══ -->
        <div class="glass-panel">
            <div class="section-title">
                <span>📈 Tendances Historiques (30 jours)</span>
                <span class="subtitle">Données SQLite · Rétention 30 jours</span>
            </div>
            <div class="grid grid-3" id="trend-sparklines-container" style="gap:var(--space-xl);">
                <div class="glass-panel glass-panel--compact" style="display:flex;flex-direction:column;gap:var(--space-sm);">
                    <div style="font-size:0.72rem;color:var(--text-muted);text-transform:uppercase;">Gemini Flash — RPD (24h)</div>
                    <div id="spark-flash-rpd"></div>
                    <div style="font-size:0.65rem;color:var(--text-muted);" id="spark-flash-rpd-info">Chargement...</div>
                </div>
                <div class="glass-panel glass-panel--compact" style="display:flex;flex-direction:column;gap:var(--space-sm);">
                    <div style="font-size:0.72rem;color:var(--text-muted);text-transform:uppercase;">Solde DeepSeek ($)</div>
                    <div id="spark-deepseek-balance"></div>
                    <div style="font-size:0.65rem;color:var(--text-muted);" id="spark-ds-info">Chargement...</div>
                </div>
                <div class="glass-panel glass-panel--compact" style="display:flex;flex-direction:column;gap:var(--space-sm);">
                    <div style="font-size:0.72rem;color:var(--text-muted);text-transform:uppercase;">Coût Moteur ($)</div>
                    <div id="spark-moteur-cost"></div>
                    <div style="font-size:0.65rem;color:var(--text-muted);" id="spark-moteur-info">Chargement...</div>
                </div>
            </div>
        </div>

        </div><!-- fin container principal -->

        <!-- Modal Chromium -->
        <div id="scraper-login-modal" class="modal-overlay">
            <div class="glass-panel modal-content" style="border-color:rgba(var(--warning-rgb),0.3);">
                <div style="font-size:2.5rem;">🔒</div>
                <h3 style="font-family:var(--font-display);font-weight:700;color:var(--warning);font-size:1.2rem;">Connexion requise à Google Cloud</h3>
                <p style="font-size:0.85rem;color:var(--text-secondary);line-height:1.5;">
                    Une fenêtre Chrome s'est ouverte. Connectez-vous à votre compte Google pour synchroniser la facturation GCP.
                </p>
                <button class="btn btn--warning" onclick="triggerRelaunchChrome()" style="width:100%;">🚀 Relancer Chrome en mode Débogage</button>
                <button class="btn btn--ghost" onclick="closeScraperModal()" style="width:100%;">Annuler</button>
            </div>
        </div>
    `;

    loadApisData();
}

// ═══════════════════════════════════════════════════════════════
// CHARGEMENT PRINCIPAL
// ═══════════════════════════════════════════════════════════════

async function loadApisData() {
    try {
        const [apiData, tokensData, quotasData, claudeRTData] = await Promise.allSettled([
            fetchApisStatus(),
            fetchTokens(),
            fetchQuotasSliding(),
            fetch('/api/quotas/claude-realtime').then(r => r.ok ? r.json() : null)
        ]);

        const apis      = apiData.status === 'fulfilled'      ? apiData.value      : null;
        const tokens    = tokensData.status === 'fulfilled'   ? tokensData.value   : null;
        const quotas    = quotasData.status === 'fulfilled'   ? quotasData.value   : null;
        const claudeRT  = claudeRTData.status === 'fulfilled' ? claudeRTData.value : null;

        // 1. Status Cards
        if (apis) renderApiStatusCards(apis);

        // 2. Quotas détaillés (avec données Claude.ai si disponibles)
        renderDetailedQuotas(quotas, tokens, apis, claudeRT);

        // 3. Bilans financiers
        renderFinancialBilans(tokens, apis);

        // 4. Sparklines de tendance
        try { await loadTrendSparklines(); } catch {}

    } catch (err) {
        console.error('[APIs] Erreur:', err);
    }
}

// ═══════════════════════════════════════════════════════════════
// QUOTAS DÉTAILLÉS — Refonte V9
// ═══════════════════════════════════════════════════════════════

function renderDetailedQuotas(quotas, tokens, apis, claudeRT) {
    const container = document.getElementById('quota-detail-container');
    const alertsEl  = document.getElementById('quota-alerts');
    const updateEl  = document.getElementById('quotas-last-update');
    if (!container) return;

    const now = new Date();
    if (updateEl) updateEl.textContent = `Mis à jour : ${now.toLocaleTimeString('fr-FR')}`;

    // ── Données moteur (SQLite token_usage) ──
    const flashRPM  = quotas?.gemini_free_flash_rpm || 0;
    const flashTPM  = quotas?.gemini_free_flash_tpm || 0;
    const flashRPD  = quotas?.gemini_free_flash_rpd || 0;
    const proRPM    = quotas?.gemini_free_pro_rpm || 0;
    const proRPD    = quotas?.gemini_free_pro_rpd || 0;
    const geminiTPH = quotas?.gemini_cli_tph || 0;
    const geminiTPM = quotas?.gemini_cli_tpm || 0;
    const dsBalance = tokens?.real_billing?.deepseek_balance_usd ?? null;

    // ── Données Claude.ai Pro (scraping manuel ou endpoint /api/quotas/claude-realtime) ──
    // claudeRT contient : session_pct, session_reset_mins, weekly_pct, weekly_reset_mins,
    //                     routines_used, routines_max, source, last_sync
    const cHasRT         = claudeRT && (claudeRT.session_pct != null || claudeRT.weekly_pct != null);
    const cSessionPct    = claudeRT?.session_pct ?? null;      // % session Pro (fenêtre ~5h)
    const cSessionReset  = claudeRT?.session_reset_mins ?? null; // minutes avant reset
    const cWeeklyPct     = claudeRT?.weekly_pct ?? null;       // % limite hebdomadaire
    const cWeeklyReset   = claudeRT?.weekly_reset_mins ?? null;
    const cRoutinesUsed  = claudeRT?.routines_used ?? null;
    const cRoutinesMax   = claudeRT?.routines_max ?? 5;
    const cSource        = claudeRT?.source ?? 'unavailable';
    const cLastSync      = claudeRT?.last_sync ? new Date(claudeRT.last_sync).toLocaleTimeString('fr-FR') : '—';

    // Timestamps de reset génériques
    const nextMidnight = new Date(now);
    nextMidnight.setDate(nextMidnight.getDate() + 1);
    nextMidnight.setHours(0, 0, 0, 0);
    const midnightH = Math.ceil((nextMidnight - now) / 3600000);
    const midnightM = Math.ceil((nextMidnight - now) / 60000) % 60;
    const nextMonth = new Date(now.getFullYear(), now.getMonth() + 1, 1);
    const monthDays = Math.ceil((nextMonth - now) / 86400000);
    const monthH    = Math.ceil((nextMonth - now) / 3600000) % 24;

    // ── Helpers pour afficher les valeurs Claude.ai ──
    const fmtPct = (v) => v != null ? `${v}%` : '–';
    const fmtReset = (mins) => mins != null
        ? `dans ${Math.floor(mins/60)}h ${mins % 60}min`
        : 'inconnu';

    // ── Définition des sections de quotas ──
    // SECTION CLAUDE : données réelles depuis claude.ai, pas depuis SQLite
    const claudeNote = cHasRT
        ? `Source: ${cSource} · Sync: ${cLastSync}`
        : `⚠️ Données non disponibles — cliquez "Sync Claude" pour saisir les valeurs depuis claude.ai/settings/usage`;

    const sections = [
        {
            id: 'claude-pro',
            title: '🤖 Claude.ai Pro — Session & Limites',
            color: 'var(--accent-primary)',
            bg: 'rgba(129,140,248,0.04)',
            border: 'rgba(129,140,248,0.2)',
            note: claudeNote,
            // Rendu spécial pour Claude (pourcentages, pas des tokens absolus)
            isClaudeRealtime: true,
            claudeData: { cSessionPct, cSessionReset, cWeeklyPct, cWeeklyReset, cRoutinesUsed, cRoutinesMax, cHasRT },
            rows: [
                // Lignes utilisées seulement si données disponibles
                ...(!cHasRT ? [] : [
                    {
                        label: 'Session actuelle (Pro)',
                        value: cSessionPct,
                        max: 100,
                        unit: '%',
                        reset: cSessionReset != null ? `Reset ${fmtReset(cSessionReset)}` : 'Fenêtre glissante ~5h',
                        warning: 'Si dépassé → pause forcée pendant la session',
                        critical: 0.75,
                        tip: 'Limite de la session Pro en cours — se recharge après quelques heures'
                    },
                    {
                        label: 'Limite hebdomadaire (tous modèles)',
                        value: cWeeklyPct,
                        max: 100,
                        unit: '%',
                        reset: cWeeklyReset != null ? `Reset ${fmtReset(cWeeklyReset)}` : 'Réinitialisation hebdomadaire',
                        warning: 'Si dépassé → accès dégradé sur Claude.ai',
                        critical: 0.8,
                        tip: 'Quota hebdomadaire global tous modèles confondus'
                    },
                    {
                        label: 'Routines quotidiennes',
                        value: cRoutinesUsed,
                        max: cRoutinesMax,
                        unit: '',
                        reset: 'Reset chaque jour',
                        warning: 'Si dépassé → plus de routines automatiques aujourd\'hui',
                        critical: 0.8,
                        tip: `${cRoutinesUsed ?? 0}/${cRoutinesMax} exécutions de routines utilisées`
                    }
                ])
            ]
        },
        {
            id: 'gemini-advanced',
            title: '💻 Gemini CLI + Antigravity IDE — 19.99$/mois',
            color: 'var(--accent-secondary)',
            bg: 'rgba(167,139,250,0.04)',
            border: 'rgba(167,139,250,0.2)',
            note: 'Quota partagé entre l\'IDE Antigravity et Gemini CLI',
            rows: [
                {
                    label: 'Tokens / Heure (fenêtre glissante)',
                    value: geminiTPH,
                    max: 4000000,
                    reset: 'Glissant · se vide en continu',
                    warning: 'Si dépassé → requêtes rejetées temporairement',
                    critical: 0.75,
                    tip: '~4M tokens/h — Flash est beaucoup moins coûteux que Pro'
                },
                {
                    label: 'Tokens / Mois',
                    value: geminiTPM,
                    max: 100000000,
                    reset: `Reset dans ${monthDays}j ${monthH}h (1er du mois)`,
                    warning: 'Si dépassé → bascule sur crédits Google One',
                    critical: 0.85,
                    tip: '100M tokens/mois — très large mais Flash en consomme peu'
                }
            ]
        },
        {
            id: 'gemini-free-flash',
            title: '🆓 Gemini Free Flash — Clé API gratuite',
            color: 'var(--success)',
            bg: 'rgba(16,185,129,0.03)',
            border: 'rgba(16,185,129,0.15)',
            note: 'Quotas stricts mais suffisants pour les tâches légères et embeddings',
            rows: [
                {
                    label: 'Requêtes / Minute (RPM)',
                    value: flashRPM,
                    max: 15,
                    reset: 'Reset continu (chaque minute)',
                    warning: 'Si dépassé → erreur 429, retry automatique après 1 min',
                    critical: 0.6,
                    tip: '15 req/min — le moteur gère automatiquement le retry'
                },
                {
                    label: 'Tokens / Minute (TPM)',
                    value: flashTPM,
                    max: 1000000,
                    reset: 'Reset continu',
                    warning: 'Si dépassé → throttle temporaire',
                    critical: 0.7,
                    tip: '1M tokens/min — rarement atteint sur les tâches légères'
                },
                {
                    label: 'Requêtes / Jour (RPD)',
                    value: flashRPD,
                    max: 1500,
                    reset: `Minuit dans ${midnightH}h ${midnightM}min`,
                    warning: 'Si dépassé → bloqué jusqu\'à minuit UTC',
                    critical: 0.8,
                    tip: '1500 req/jour — à surveiller en cas d\'usage intensif des embeddings'
                }
            ]
        },
        {
            id: 'gemini-free-pro',
            title: '🔬 Gemini Free Pro — Clé API gratuite',
            color: 'var(--warning)',
            bg: 'rgba(245,158,11,0.03)',
            border: 'rgba(245,158,11,0.15)',
            note: 'Très limité — réservé aux tâches de raisonnement ponctuelles',
            rows: [
                {
                    label: 'Requêtes / Minute (RPM)',
                    value: proRPM,
                    max: 2,
                    reset: 'Reset continu',
                    warning: 'Si dépassé → erreur 429 immédiate',
                    critical: 0.5,
                    tip: '2 req/min seulement — le moteur préfère Claude CLI pour le raisonnement'
                },
                {
                    label: 'Requêtes / Jour (RPD)',
                    value: proRPD,
                    max: 50,
                    reset: `Minuit dans ${midnightH}h ${midnightM}min`,
                    warning: 'Si dépassé → bloqué jusqu\'à minuit UTC',
                    critical: 0.8,
                    tip: '50 req/jour — à utiliser avec parcimonie'
                }
            ]
        }
    ];

    // ── Génération du HTML ──
    const alerts = [];

    let html = '';
    sections.forEach(section => {
        html += `
            <div style="border:1px solid ${section.border};border-radius:var(--radius-lg);padding:var(--space-lg);background:${section.bg};">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:var(--space-md);">
                    <div>
                        <div style="font-weight:700;color:${section.color};font-size:0.9rem;">${section.title}</div>
                        <div style="font-size:0.7rem;color:var(--text-muted);margin-top:2px;">${section.note}</div>
                    </div>
                </div>
                <div style="display:flex;flex-direction:column;gap:var(--space-md);">
        `;

        // ─ Cas spécial : aucune donnée disponible (Claude avant première saisie) ─
        if (section.rows.length === 0) {
            html += `
                <div style="display:flex;align-items:center;gap:var(--space-md);
                            padding:var(--space-md);background:rgba(255,255,255,0.02);
                            border-radius:var(--radius-md);border:1px dashed rgba(255,255,255,0.1);">
                    <span style="font-size:1.8rem;">🔒</span>
                    <div>
                        <div style="font-size:0.8rem;color:var(--text-secondary);margin-bottom:4px;">
                            Données non encore saisies
                        </div>
                        <div style="font-size:0.7rem;color:var(--text-muted);margin-bottom:8px;">
                            Claude.ai bloque le scraping automatique. Consultez
                            <strong>claude.ai/settings/usage</strong> puis saisissez les valeurs.
                        </div>
                        <button class="btn btn--warning btn--sm" onclick="triggerRefreshClaude()">
                            📋 Saisir les données maintenant
                        </button>
                    </div>
                </div>
            `;
        }

        section.rows.forEach(row => {
            const pct = Math.min(100, row.value > 0 ? (row.value / row.max) * 100 : 0);


            const isCritical = pct >= row.critical * 100;
            const isWarning  = pct >= row.critical * 100 * 0.6;
            const isEmpty    = row.value === 0;

            let barColor = section.color;
            let riskLabel = '';
            let riskStyle = '';

            if (isCritical) {
                barColor = 'var(--error)';
                riskLabel = `⛔ BLOQUAGE IMMINENT — ${row.warning}`;
                riskStyle = 'color:var(--error);';
                alerts.push({ label: `${section.title} · ${row.label}`, pct, warning: row.warning, reset: row.reset });
            } else if (isWarning) {
                barColor = 'var(--warning)';
                riskLabel = `⚠️ ${row.warning}`;
                riskStyle = 'color:var(--warning);';
            }

            // Format valeur
            const isLarge = row.max >= 1000000;
            const valStr  = formatGaugeValue(row.value, '');
            const maxStr  = formatGaugeValue(row.max, '');

            html += `
                <div style="position:relative;"
                     title="${row.tip}&#10;Consommation : ${valStr} / ${maxStr}&#10;${row.reset}&#10;${row.warning}">
                    <!-- Header de la jauge -->
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                        <div style="display:flex;align-items:center;gap:6px;">
                            <span style="font-size:0.78rem;color:var(--text-secondary);">${row.label}</span>
                            <span class="tooltip-wrap" style="cursor:help;">
                                <span style="font-size:0.6rem;color:var(--text-muted);background:rgba(255,255,255,0.04);padding:1px 4px;border-radius:3px;">?</span>
                                <div class="tooltip" style="width:260px;left:0;transform:translateX(0);">
                                    <div class="tooltip__title">${row.label}</div>
                                    <div class="tooltip__desc">${row.tip}</div>
                                    <div class="tooltip__usage">${row.warning}</div>
                                </div>
                            </span>
                        </div>
                        <div style="display:flex;align-items:center;gap:6px;">
                            ${isCritical ? `<span style="font-size:0.62rem;font-weight:700;color:var(--error);background:rgba(239,68,68,0.1);padding:1px 5px;border-radius:3px;">CRITIQUE</span>` : ''}
                            <span style="font-size:0.72rem;font-family:var(--font-mono);color:${isEmpty ? 'var(--text-muted)' : 'var(--text-primary)'};">
                                ${valStr} / ${maxStr}
                                ${isEmpty ? '<span style="color:var(--success);margin-left:3px;">✓ Libre</span>' : `<span style="color:var(--text-muted);"> (${pct.toFixed(1)}%)</span>`}
                            </span>
                        </div>
                    </div>
                    <!-- Barre de progression -->
                    <div style="height:8px;background:rgba(255,255,255,0.05);border-radius:4px;overflow:hidden;position:relative;">
                        <div style="height:100%;width:${Math.max(0, pct).toFixed(1)}%;background:${barColor};border-radius:4px;transition:width 0.6s ease;${isCritical ? 'animation:pulseGlow 1.5s infinite;' : ''}"></div>
                        ${row.critical < 1 ? `
                        <div style="position:absolute;top:0;height:100%;width:1px;background:rgba(245,158,11,0.5);left:${row.critical * 100}%;" title="Seuil d'alerte ${row.critical * 100}%"></div>
                        ` : ''}
                    </div>
                    <!-- Footer -->
                    <div style="display:flex;justify-content:space-between;margin-top:3px;font-size:0.62rem;color:var(--text-muted);">
                        <span>${row.reset}</span>
                        ${riskLabel ? `<span style="${riskStyle}font-weight:600;">${riskLabel}</span>` : `<span style="color:var(--success);">✓ OK</span>`}
                    </div>
                </div>
            `;
        });

        html += `</div></div>`;  // fin rows + section
    });

    container.innerHTML = html;

    // ── Alertes de blocage ──
    if (alertsEl) {
        if (alerts.length > 0) {
            alertsEl.innerHTML = `
                <div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.3);border-radius:var(--radius-md);padding:var(--space-md) var(--space-lg);">
                    <div style="font-weight:700;color:var(--error);margin-bottom:var(--space-sm);">⚠️ ${alerts.length} quota(s) critique(s)</div>
                    ${alerts.map(a => `
                        <div style="font-size:0.78rem;color:var(--text-secondary);margin-bottom:2px;">
                            <strong>${a.label}</strong> — ${a.pct.toFixed(1)}% · ${a.warning}<br>
                            <span style="color:var(--text-muted);font-size:0.7rem;">${a.reset}</span>
                        </div>
                    `).join('')}
                </div>
            `;
        } else {
            alertsEl.innerHTML = `
                <div style="background:rgba(16,185,129,0.05);border:1px solid rgba(16,185,129,0.2);border-radius:var(--radius-md);padding:var(--space-sm) var(--space-lg);font-size:0.78rem;color:var(--success);">
                    ✅ Tous les quotas sont dans les limites normales — aucun risque de blocage imminent
                </div>
            `;
        }
    }
}

// ═══════════════════════════════════════════════════════════════
// BILANS FINANCIERS
// ═══════════════════════════════════════════════════════════════

function renderFinancialBilans(tokens, apis) {
    const rb = tokens?.real_billing;
    const ct = tokens?.combined_total;
    const bySource = ct?.by_source || [];

    // ── Claude Pro ──
    const claudeEl = document.getElementById('claude-billing-detail');
    if (claudeEl) {
        const claudeData = bySource.find(s => s.source === 'claude_cli') || {};
        const ideData    = bySource.find(s => s.source === 'antigravity_ide') || {};
        const totalCost  = (claudeData.cost_usd || 0) + (ideData.cost_usd || 0);
        const totalToks  = (claudeData.tokens || 0) + (ideData.tokens || 0);
        const totalSess  = (claudeData.sessions || 0) + (ideData.sessions || 0);
        claudeEl.innerHTML = `
            <div style="display:flex;justify-content:space-between;">
                <span style="color:var(--text-muted);">Coût total (prorata) :</span>
                <strong style="color:var(--accent-primary);">${totalCost.toFixed(2)} $</strong>
            </div>
            <div style="display:flex;justify-content:space-between;">
                <span style="color:var(--text-muted);">Sessions CLI Claude :</span>
                <strong style="color:var(--text-primary);">${claudeData.sessions || 0}</strong>
            </div>
            <div style="display:flex;justify-content:space-between;">
                <span style="color:var(--text-muted);">Sessions Antigravity IDE :</span>
                <strong style="color:var(--text-primary);">${ideData.sessions || 0}</strong>
            </div>
            <div style="display:flex;justify-content:space-between;">
                <span style="color:var(--text-muted);">Tokens totaux :</span>
                <strong style="color:var(--text-primary);">${formatGaugeValue(totalToks, '')}</strong>
            </div>
            <div style="margin-top:var(--space-xs);padding:var(--space-xs) var(--space-sm);background:rgba(129,140,248,0.06);border-radius:var(--radius-sm);font-size:0.68rem;color:var(--text-muted);line-height:1.4;">
                Abonnement 20$/mois · Prorata 0.57$/M tokens · 35M/mois inclus
            </div>
            ${rb?.claude_summary_text ? `<div style="font-size:0.7rem;color:var(--accent-primary);font-style:italic;">${rb.claude_summary_text}</div>` : ''}
        `;
    }

    // ── Gemini Advanced ──
    const geminiEl = document.getElementById('gemini-billing-detail');
    if (geminiEl) {
        const antiGravData = bySource.find(s => s.source === 'antigravity_ide') || {};
        geminiEl.innerHTML = `
            <div style="display:flex;justify-content:space-between;">
                <span style="color:var(--text-muted);">Sessions IDE :</span>
                <strong style="color:var(--text-primary);">${antiGravData.sessions || 0}</strong>
            </div>
            <div style="display:flex;justify-content:space-between;">
                <span style="color:var(--text-muted);">Tokens IDE :</span>
                <strong style="color:var(--text-primary);">${formatGaugeValue(antiGravData.tokens || 0, '')}</strong>
            </div>
            <div style="display:flex;justify-content:space-between;">
                <span style="color:var(--text-muted);">Coût prorata :</span>
                <strong style="color:var(--accent-secondary);">${(antiGravData.cost_usd || 0).toFixed(2)} $</strong>
            </div>
            <div style="margin-top:var(--space-xs);padding:var(--space-xs) var(--space-sm);background:rgba(167,139,250,0.06);border-radius:var(--radius-sm);font-size:0.68rem;color:var(--text-muted);line-height:1.4;">
                Abonnement 19.99$/mois · Quota horaire 4M tok · 100M/mois inclus
            </div>
            ${apis?.antigravity?.credits != null ? `
                <div style="display:flex;justify-content:space-between;margin-top:var(--space-xs);">
                    <span style="color:var(--text-muted);">Crédits IDE restants :</span>
                    <strong style="color:${apis.antigravity.credits < 10 ? 'var(--error)' : 'var(--success)'};">${apis.antigravity.credits.toFixed(2)} $</strong>
                </div>
            ` : ''}
        `;
    }

    // ── DeepSeek ──
    const dsEl = document.getElementById('deepseek-billing-detail');
    if (dsEl && rb) {
        const balance = rb.deepseek_balance_usd ?? null;
        const consumed = balance != null ? Math.max(0, 20 - balance) : null;
        dsEl.innerHTML = `
            <div style="display:flex;justify-content:space-between;">
                <span style="color:var(--text-muted);">Solde restant :</span>
                <strong style="color:${balance != null && balance < 5 ? 'var(--error)' : 'var(--color-deepseek)'};">${balance != null ? balance.toFixed(2) + ' $' : '–'}</strong>
            </div>
            ${consumed != null ? `
                <div style="display:flex;justify-content:space-between;">
                    <span style="color:var(--text-muted);">Consommé :</span>
                    <strong style="color:var(--text-primary);">${consumed.toFixed(2)} $</strong>
                </div>
                <div style="height:6px;background:rgba(255,255,255,0.05);border-radius:3px;overflow:hidden;margin-top:4px;">
                    <div style="height:100%;width:${Math.min(100, (consumed/20)*100).toFixed(1)}%;background:var(--color-deepseek);border-radius:3px;"></div>
                </div>
                <div style="font-size:0.68rem;color:var(--text-muted);margin-top:3px;">${consumed.toFixed(2)} / 20 $ · ${Math.round((consumed/20)*100)}% consommé</div>
            ` : ''}
            ${rb.deepseek_last_sync ? `<div style="font-size:0.65rem;color:var(--text-muted);">Sync : ${new Date(rb.deepseek_last_sync).toLocaleString('fr-FR')}</div>` : ''}
            ${balance != null && balance < 3 ? `
                <div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.2);border-radius:var(--radius-sm);padding:4px 8px;font-size:0.7rem;color:var(--error);margin-top:var(--space-xs);">
                    ⚠️ Solde faible — Recharger sur platform.deepseek.com
                </div>
            ` : ''}
        `;
    }

    // ── GCP Billing ──
    const gcpEl = document.getElementById('gcp-billing-detail');
    if (gcpEl && rb) {
        const gcpCost = rb.gemini_gcp_cost_usd;
        gcpEl.innerHTML = gcpCost != null
            ? `<span style="color:${gcpCost > 1 ? 'var(--warning)' : 'var(--success)'};">${gcpCost.toFixed(4)} $</span><span style="color:var(--text-muted);font-size:0.7rem;"> ce mois-ci</span>`
            : `<span style="color:var(--text-muted);font-style:italic;">Non synchronisé (Chrome requis)</span>`;
    }
}

// ═══════════════════════════════════════════════════════════════
// STATUS CARDS (Providers)
// ═══════════════════════════════════════════════════════════════

function renderApiStatusCards(data) {
    const container = document.getElementById('api-status-cards');
    if (!container || !data) return;

    const cards = [
        {
            title: 'Gemini Free (AI Studio)',
            icon: '🆓',
            configured: data.gemini?.configured,
            active: data.gemini?.configured,
            key: data.gemini?.obfuscated_key || '–',
            project: data.gemini?.project_name || '–',
            extra: 'Clé API gratuite · RPM 15 · RPD 1500'
        },
        {
            title: 'Gemini Paid (GCP)',
            icon: '💳',
            configured: data.gemini?.configured,
            active: data.gemini?.active,
            key: data.gemini?.obfuscated_key || '–',
            project: data.gemini?.project_name || '–',
            extra: 'Facturation EUR · Sans quota · Usage minime'
        },
        {
            title: 'DeepSeek API',
            icon: '🐋',
            configured: data.deepseek?.configured,
            active: data.deepseek?.active,
            key: data.deepseek?.obfuscated_key || '–',
            project: '–',
            extra: `Solde prépayé · V4 disponible · Très compétitif`
        },
        {
            title: 'Antigravity IDE',
            icon: '💻',
            configured: data.antigravity?.connected !== undefined,
            active: data.antigravity?.connected === true,
            key: data.antigravity?.email && data.antigravity.email !== 'Inconnu' ? data.antigravity.email : 'Non connecté',
            project: data.antigravity?.user && data.antigravity.user !== 'Non connecté' ? data.antigravity.user : '–',
            extra: data.antigravity?.connected
                ? `Plan : ${data.antigravity.plan || 'Google AI Ultra'} · ${data.antigravity.credits !== undefined ? data.antigravity.credits.toFixed(2) : '0.00'} $ crédits`
                : (data.antigravity?.error ? `Erreur : ${data.antigravity.error}` : 'Session inactive ou non détectée')
        }
    ];

    container.innerHTML = cards.map(c => {
        const statusColor = c.active ? 'var(--success)' : (c.configured ? 'var(--warning)' : 'var(--error)');
        const statusText  = c.active ? 'Actif' : (c.configured ? 'Configuré' : 'Inactif');
        const dotClass    = c.active ? 'success' : (c.configured ? 'warning' : 'error');

        return `
            <div class="glass-panel glass-panel--compact" style="display:flex;flex-direction:column;gap:var(--space-md);">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div style="display:flex;align-items:center;gap:var(--space-sm);">
                        <span style="font-size:1.3rem;">${c.icon}</span>
                        <span style="font-weight:700;font-size:0.9rem;">${c.title}</span>
                    </div>
                    <div class="status-badge" style="border-color:rgba(0,0,0,0.1);color:${statusColor};">
                        <span class="status-dot ${dotClass}"></span>
                        <span>${statusText}</span>
                    </div>
                </div>
                <div style="font-size:0.75rem;color:var(--text-muted);display:flex;flex-direction:column;gap:0.2rem;">
                    <div>Clé / Session : <span style="font-family:var(--font-mono);color:var(--text-secondary);">${c.key}</span></div>
                    ${c.project !== '–' ? `<div>Utilisateur : <span style="color:var(--text-secondary);">${c.project}</span></div>` : ''}
                    <div style="color:var(--text-muted);font-style:italic;font-size:0.68rem;margin-top:0.15rem;">${c.extra || ''}</div>
                </div>
            </div>
        `;
    }).join('');
}

// ═══════════════════════════════════════════════════════════════
// SPARKLINES DE TENDANCE
// ═══════════════════════════════════════════════════════════════

let _trendSparklines = {};

async function loadTrendSparklines() {
    if (typeof createSparkline !== 'function') return;

    // 1. Gemini Free Flash RPD (24h)
    try {
        const rpd = await fetchQuotasHistory(24, 'gemini_free_flash', 'rpd');
        const container = document.getElementById('spark-flash-rpd');
        const info = document.getElementById('spark-flash-rpd-info');
        if (container && rpd.data) {
            container.innerHTML = '';
            if (_trendSparklines['flash-rpd']) _trendSparklines['flash-rpd'].destroy();
            _trendSparklines['flash-rpd'] = createSparkline(container, {
                width: 220, height: 36, color: 'hsl(120, 70%, 55%)', unit: '', showDots: true
            });
            _trendSparklines['flash-rpd'].update(rpd.data);
            if (info) info.textContent = `${rpd.data.length} points · max 1500 RPD`;
        }
    } catch (e) { console.warn('[Sparkline] flash-rpd:', e); }

    // 2. Solde DeepSeek (30j)
    try {
        const ds = await fetchBillingHistory(30, 'deepseek');
        const container = document.getElementById('spark-deepseek-balance');
        const info = document.getElementById('spark-ds-info');
        if (container && ds.data) {
            container.innerHTML = '';
            if (_trendSparklines['ds-balance']) _trendSparklines['ds-balance'].destroy();
            _trendSparklines['ds-balance'] = createSparkline(container, {
                width: 220, height: 36, color: 'hsl(200, 80%, 55%)', unit: '$', showDots: true
            });
            _trendSparklines['ds-balance'].update(ds.data);
            if (info) {
                const first = ds.data[0]?.value;
                const last  = ds.data[ds.data.length - 1]?.value;
                const delta = first && last ? (last - first).toFixed(3) : '–';
                info.textContent = `${ds.data.length} points · Δ${delta} $`;
            }
        }
    } catch (e) { console.warn('[Sparkline] ds-balance:', e); }

    // 3. Coût moteur (placeholder depuis combined_total)
    try {
        const container = document.getElementById('spark-moteur-cost');
        const info = document.getElementById('spark-moteur-info');
        if (container) {
            const tokens = await fetchTokens();
            const ct = tokens?.combined_total;
            if (ct && info) {
                info.textContent = `Moteur: ${ct.moteur_cost_usd?.toFixed(4) || '0'} $ · CLI: ${ct.cli_cost_estimated_usd?.toFixed(2) || '0'} $ (abo)`;
            }
        }
    } catch {}
}

// ═══════════════════════════════════════════════════════════════
// ACTIONS (refresh, billing sync, chrome)
// ═══════════════════════════════════════════════════════════════

async function refreshApisStatus() {
    showToast('info', 'Rafraîchissement des statuts APIs...');
    await loadApisData();
    showToast('success', 'Statuts APIs rafraîchis.');
}

async function triggerSyncBilling() {
    const btn = document.getElementById('btn-sync-billing');
    if (btn) btn.disabled = true;
    try {
        await syncBilling();
        showToast('success', 'Synchronisation de la facturation réussie !');
        loadApisData();
    } catch (err) {
        if (err.message && err.message.includes('Chrome')) {
            document.getElementById('scraper-login-modal').classList.add('visible');
        } else {
            showToast('error', 'Erreur de synchro : ' + err.message);
        }
    } finally {
        if (btn) btn.disabled = false;
    }
}

function closeScraperModal() {
    document.getElementById('scraper-login-modal')?.classList.remove('visible');
}

async function triggerRelaunchChrome() {
    try {
        await launchChromeDebug();
        showToast('success', 'Chrome relancé en mode Débogage !');
        closeScraperModal();
    } catch (err) {
        showToast('error', 'Erreur Chrome : ' + err.message);
    }
}

/**
 * triggerRefreshClaude — Ouvre un modal de saisie manuelle.
 * Claude.ai bloque le scraping automatique (Cloudflare) → l'utilisateur
 * saisit les valeurs vues sur claude.ai/settings/usage.
 * Les données sont persistées via /api/quotas/claude-update.
 */
async function triggerRefreshClaude() {
    // Supprimer le modal existant si présent
    const existingModal = document.getElementById('claude-manual-modal');
    if (existingModal) existingModal.remove();

    // Récupérer les valeurs actuelles (si déjà saisies)
    let current = {};
    try {
        const r = await fetch('/api/quotas/claude-realtime');
        if (r.ok) current = await r.json();
    } catch {}

    const modal = document.createElement('div');
    modal.id = 'claude-manual-modal';
    modal.style.cssText = `
        position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;
        background:rgba(0,0,0,0.65);backdrop-filter:blur(6px);
    `;
    modal.innerHTML = `
        <div class="glass-panel" style="max-width:480px;width:90%;padding:var(--space-xl);
             border:1px solid rgba(129,140,248,0.35);position:relative;">
            <button onclick="document.getElementById('claude-manual-modal').remove()"
                    style="position:absolute;top:var(--space-md);right:var(--space-md);
                           background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:1.2rem;">✕</button>

            <div style="display:flex;align-items:center;gap:var(--space-md);margin-bottom:var(--space-lg);">
                <span style="font-size:2rem;">🤖</span>
                <div>
                    <div style="font-weight:700;font-size:1.05rem;color:var(--accent-primary);">
                        Saisie manuelle — Claude.ai Pro
                    </div>
                    <div style="font-size:0.72rem;color:var(--text-muted);">
                        Rendez-vous sur <strong>claude.ai/settings/usage</strong>, puis saisissez les valeurs affichées.
                    </div>
                </div>
            </div>

            <!-- Session actuelle -->
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--space-md);margin-bottom:var(--space-md);">
                <div class="form-group">
                    <label class="form-label">Session actuelle (%)</label>
                    <input type="number" id="cm-session-pct" min="0" max="100" placeholder="ex: 31"
                           value="${current.session_pct ?? ''}" style="width:100%;">
                    <div style="font-size:0.65rem;color:var(--text-muted);margin-top:2px;">
                        Barre "Session actuelle" sur la page
                    </div>
                </div>
                <div class="form-group">
                    <label class="form-label">Reset session (h)</label>
                    <input type="number" id="cm-session-reset-h" min="0" max="10" placeholder="ex: 4"
                           value="${current.session_reset_mins ? Math.floor(current.session_reset_mins/60) : ''}" style="width:100%;">
                </div>
            </div>

            <!-- Limite hebdomadaire -->
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--space-md);margin-bottom:var(--space-md);">
                <div class="form-group">
                    <label class="form-label">Limite hebdo. (%)</label>
                    <input type="number" id="cm-weekly-pct" min="0" max="100" placeholder="ex: 3"
                           value="${current.weekly_pct ?? ''}" style="width:100%;">
                    <div style="font-size:0.65rem;color:var(--text-muted);margin-top:2px;">
                        Barre "Tous les modèles" (hebdo.)
                    </div>
                </div>
                <div class="form-group">
                    <label class="form-label">Reset hebdo (h)</label>
                    <input type="number" id="cm-weekly-reset-h" min="0" max="200" placeholder="ex: 5"
                           value="${current.weekly_reset_mins ? Math.floor(current.weekly_reset_mins/60) : ''}" style="width:100%;">
                </div>
            </div>

            <!-- Routines -->
            <div class="form-group" style="margin-bottom:var(--space-lg);">
                <label class="form-label">Routines quotidiennes utilisées (ex: 0)</label>
                <input type="number" id="cm-routines" min="0" max="5" placeholder="0"
                       value="${current.routines_used ?? ''}" style="width:100%;">
            </div>

            <div style="display:flex;gap:var(--space-sm);justify-content:flex-end;">
                <button class="btn btn--ghost" onclick="document.getElementById('claude-manual-modal').remove()">
                    Annuler
                </button>
                <button class="btn btn--primary" onclick="submitClaudeManual()">
                    💾 Enregistrer
                </button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    // Focus sur le premier champ
    setTimeout(() => document.getElementById('cm-session-pct')?.focus(), 50);
}

async function submitClaudeManual() {
    const sessionPct  = parseInt(document.getElementById('cm-session-pct')?.value);
    const sessionH    = parseInt(document.getElementById('cm-session-reset-h')?.value);
    const weeklyPct   = parseInt(document.getElementById('cm-weekly-pct')?.value);
    const weeklyH     = parseInt(document.getElementById('cm-weekly-reset-h')?.value);
    const routines    = parseInt(document.getElementById('cm-routines')?.value);

    const payload = {};
    if (!isNaN(sessionPct))  payload.session_pct         = sessionPct;
    if (!isNaN(sessionH))    payload.session_reset_mins  = sessionH * 60;
    if (!isNaN(weeklyPct))   payload.weekly_pct          = weeklyPct;
    if (!isNaN(weeklyH))     payload.weekly_reset_mins   = weeklyH * 60;
    if (!isNaN(routines))    payload.routines_used       = routines;

    try {
        const r = await fetch('/api/quotas/claude-update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!r.ok) throw new Error(await r.text());

        document.getElementById('claude-manual-modal')?.remove();
        showToast('success', `Claude.ai Pro mis à jour : Session ${sessionPct ?? '?'}%, Hebdo ${weeklyPct ?? '?'}%`);

        // Recharger l'affichage
        await loadApisData();
    } catch (err) {
        showToast('error', 'Erreur sauvegarde : ' + err.message);
    }
}

