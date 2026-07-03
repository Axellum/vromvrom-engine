/* ============================================================
   PERSISTENT.JS — Onglet « 🔄 Agents 24/7 »
   Configuration et monitoring des agents persistants :
   - Démon Sentinelle (tick loop)
   - autoDream (consolidation mémoire nocturne)
   - Routines de développement (placeholder)
   ============================================================ */

// État local des agents persistants
let persistentConfig = {};
let daemonStatus = {};
let daemonLogs = [];
let dreamerStatus = {};

/**
 * Rendu principal de l'onglet Agents Persistants 24/7
 */
function renderPersistent(container) {
    container.innerHTML = `
        <!-- En-tête de section -->
        <div class="glass-panel" style="background:linear-gradient(135deg, rgba(99,102,241,0.08), rgba(168,85,247,0.06)); border-color:rgba(99,102,241,0.2);">
            <div style="display:flex;align-items:center;gap:var(--space-lg);margin-bottom:var(--space-md);">
                <div style="font-size:2rem;">🔄</div>
                <div>
                    <div class="section-title" style="margin:0;">Agents Persistants 24/7</div>
                    <div style="font-size:0.85rem;color:var(--text-muted);margin-top:2px;">
                        Services de fond autonomes — surveillance, consolidation mémoire, routines
                    </div>
                </div>
            </div>
        </div>

        <!-- Section 1 : Démon Sentinelle -->
        <div class="glass-panel" id="daemon-section">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-lg);">
                <div style="display:flex;align-items:center;gap:var(--space-md);">
                    <div style="font-size:1.4rem;">🛡️</div>
                    <div>
                        <div style="font-weight:700;font-size:1.1rem;">Démon Sentinelle</div>
                        <div style="font-size:0.78rem;color:var(--text-muted);">Tick loop — surveillance Git, HA, Calendrier, Mémoire</div>
                    </div>
                </div>
                <div style="display:flex;align-items:center;gap:var(--space-lg);">
                    <span id="daemon-status-badge" class="status-badge" style="font-size:0.75rem;">⏳ Chargement...</span>
                    <label class="toggle-switch" title="Activer/Désactiver le démon">
                        <input type="checkbox" id="daemon-toggle" onchange="onDaemonToggle(this.checked)">
                        <span class="toggle-slider"></span>
                    </label>
                </div>
            </div>

            <div class="grid grid-3" style="gap:var(--space-xl);">
                <!-- Fréquence -->
                <div class="form-group">
                    <label class="form-label" for="daemon-interval">Fréquence (minutes)</label>
                    <div style="display:flex;align-items:center;gap:var(--space-md);">
                        <input type="range" id="daemon-interval" min="1" max="60" value="10"
                               style="flex:1;accent-color:var(--accent-primary);"
                               oninput="document.getElementById('daemon-interval-value').textContent = this.value + ' min'">
                        <span id="daemon-interval-value" style="font-weight:600;min-width:50px;text-align:right;">10 min</span>
                    </div>
                </div>
                <!-- Modèle -->
                <div class="form-group">
                    <label class="form-label" for="daemon-model">Modèle / Tier</label>
                    <div class="select-wrap">
                        <select id="daemon-model">
                            <option value="leger">Tier Léger (Gemini Flash Free)</option>
                            <option value="moyen">Tier Moyen (Intermédiaire)</option>
                            <option value="fort">Tier Fort (Claude Pro)</option>
                            <option value="automatique">Tier Automatique</option>
                        </select>
                    </div>
                </div>
                <!-- Métriques -->
                <div class="form-group">
                    <label class="form-label">Métriques</label>
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--space-sm);font-size:0.82rem;">
                        <div>Cycles totaux : <strong id="daemon-total-cycles">0</strong></div>
                        <div>Erreurs : <strong id="daemon-errors" style="color:var(--error);">0</strong></div>
                        <div>Dernier cycle : <strong id="daemon-last-cycle">—</strong></div>
                        <div>Durée : <strong id="daemon-last-duration">—</strong></div>
                    </div>
                </div>
            </div>

            <!-- Anomalies détectées -->
            <div id="daemon-anomalies" style="margin-top:var(--space-lg);display:none;">
                <div style="font-weight:600;font-size:0.9rem;color:var(--warning);margin-bottom:var(--space-sm);">⚠️ Anomalies détectées</div>
                <div id="daemon-anomalies-list" style="font-size:0.82rem;"></div>
            </div>

            <!-- Mini-logs -->
            <details style="margin-top:var(--space-lg);">
                <summary style="cursor:pointer;font-weight:600;font-size:0.9rem;color:var(--text-muted);">
                    📋 Derniers cycles (logs)
                </summary>
                <div id="daemon-logs-container" style="margin-top:var(--space-md);max-height:200px;overflow-y:auto;">
                    <table style="width:100%;font-size:0.78rem;border-collapse:collapse;">
                        <thead>
                            <tr style="border-bottom:1px solid var(--border-color);">
                                <th style="text-align:left;padding:4px 8px;">Heure</th>
                                <th style="text-align:right;padding:4px 8px;">Durée</th>
                                <th style="text-align:center;padding:4px 8px;">Anomalies</th>
                                <th style="text-align:left;padding:4px 8px;">Checks</th>
                            </tr>
                        </thead>
                        <tbody id="daemon-logs-body"></tbody>
                    </table>
                </div>
            </details>

            <!-- Bouton sauvegarder -->
            <div style="margin-top:var(--space-xl);display:flex;justify-content:flex-end;">
                <button class="btn-primary" onclick="saveDaemonConfig()" style="font-size:0.85rem;">
                    💾 Sauvegarder la configuration du démon
                </button>
            </div>
        </div>

        <!-- Section 2 : autoDream -->
        <div class="glass-panel" id="dreamer-section">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-lg);">
                <div style="display:flex;align-items:center;gap:var(--space-md);">
                    <div style="font-size:1.4rem;">🌙</div>
                    <div>
                        <div style="font-weight:700;font-size:1.1rem;">autoDream — Mémoire Nocturne</div>
                        <div style="font-size:0.78rem;color:var(--text-muted);">Consolidation mémoire (decay, GC graphe, leçons apprises)</div>
                    </div>
                </div>
                <div style="display:flex;align-items:center;gap:var(--space-lg);">
                    <span id="dreamer-status-badge" class="status-badge" style="font-size:0.75rem;">⏳ Chargement...</span>
                    <label class="toggle-switch" title="Activer/Désactiver autoDream">
                        <input type="checkbox" id="dreamer-toggle" onchange="onDreamerToggle(this.checked)">
                        <span class="toggle-slider"></span>
                    </label>
                </div>
            </div>

            <div class="grid grid-3" style="gap:var(--space-xl);">
                <!-- Horaire -->
                <div class="form-group">
                    <label class="form-label" for="dreamer-schedule">Heure de déclenchement</label>
                    <input type="time" id="dreamer-schedule" value="02:00"
                           style="background:var(--bg-tertiary);border:1px solid var(--border-color);color:var(--text-primary);padding:8px 12px;border-radius:var(--radius-md);font-size:0.9rem;">
                </div>
                <!-- Inactivité -->
                <div class="form-group">
                    <label class="form-label" for="dreamer-idle">Seuil d'inactivité (heures)</label>
                    <div style="display:flex;align-items:center;gap:var(--space-md);">
                        <input type="range" id="dreamer-idle" min="1" max="12" value="3"
                               style="flex:1;accent-color:var(--accent-secondary);"
                               oninput="document.getElementById('dreamer-idle-value').textContent = this.value + 'h'">
                        <span id="dreamer-idle-value" style="font-weight:600;min-width:30px;text-align:right;">3h</span>
                    </div>
                </div>
                <!-- Modèle -->
                <div class="form-group">
                    <label class="form-label" for="dreamer-model">Modèle / Tier</label>
                    <div class="select-wrap">
                        <select id="dreamer-model">
                            <option value="leger">Tier Léger (DeepSeek Chat / Flash Free)</option>
                            <option value="moyen">Tier Moyen (Intermédiaire)</option>
                            <option value="fort">Tier Fort (Claude Pro)</option>
                            <option value="automatique">Tier Automatique</option>
                        </select>
                    </div>
                </div>
            </div>

            <!-- Dernier rapport -->
            <div id="dreamer-report" style="margin-top:var(--space-lg);display:none;">
                <div style="font-weight:600;font-size:0.9rem;color:var(--accent-secondary);margin-bottom:var(--space-sm);">📊 Dernier rapport de consolidation</div>
                <div id="dreamer-report-content" style="font-size:0.82rem;background:var(--bg-secondary);border-radius:var(--radius-md);padding:var(--space-lg);"></div>
            </div>

            <!-- Métriques dreamer -->
            <div style="margin-top:var(--space-lg);">
                <div style="display:grid;grid-template-columns:repeat(4, 1fr);gap:var(--space-md);font-size:0.82rem;">
                    <div>Consolidations : <strong id="dreamer-total-runs">0</strong></div>
                    <div>Dernier run : <strong id="dreamer-last-run">—</strong></div>
                    <div>Durée : <strong id="dreamer-last-duration">—</strong></div>
                    <div>Prochain : <strong id="dreamer-next">—</strong></div>
                </div>
            </div>

            <!-- Actions -->
            <div style="margin-top:var(--space-xl);display:flex;gap:var(--space-md);justify-content:flex-end;">
                <button class="btn-secondary" onclick="triggerDreamerManual()" style="font-size:0.85rem;">
                    🌙 Déclencher maintenant
                </button>
                <button class="btn-primary" onclick="saveDreamerConfig()" style="font-size:0.85rem;">
                    💾 Sauvegarder la configuration
                </button>
            </div>
        </div>

        <!-- Section 3 : Routines (placeholder pour extension future) -->
        <div class="glass-panel">
            <div style="display:flex;align-items:center;gap:var(--space-md);margin-bottom:var(--space-lg);">
                <div style="font-size:1.4rem;">⚡</div>
                <div>
                    <div style="font-weight:700;font-size:1.1rem;">Routines de Développement</div>
                    <div style="font-size:0.78rem;color:var(--text-muted);">Tâches planifiées récurrentes — linting, tests, documentation auto</div>
                </div>
            </div>
            <div class="grid grid-2" style="gap:var(--space-xl);">
                <div class="form-group">
                    <label class="form-label" for="routines-model">Modèle / Tier pour les routines</label>
                    <div class="select-wrap">
                        <select id="routines-model">
                            <option value="leger">Tier Léger</option>
                            <option value="moyen">Tier Moyen</option>
                            <option value="fort">Tier Fort (Raisonnement)</option>
                            <option value="automatique">Tier Automatique</option>
                        </select>
                    </div>
                </div>
                <div style="display:flex;align-items:center;justify-content:center;color:var(--text-muted);font-size:0.85rem;font-style:italic;">
                    🚧 Planification de routines — bientôt disponible
                </div>
            </div>
        </div>
    `;

    // Charger les données depuis l'API
    loadPersistentData();
}


/**
 * Charge la config et les statuts depuis l'API
 */
async function loadPersistentData() {
    try {
        // Charger en parallèle : config, daemon status, daemon logs, dreamer status
        const [configRes, daemonRes, logsRes, dreamerRes] = await Promise.allSettled([
            fetch('/api/persistent-agents/config'),
            fetch('/api/daemon/status'),
            fetch('/api/daemon/logs?limit=20'),
            fetch('/api/dreamer/status'),
        ]);

        // Config
        if (configRes.status === 'fulfilled' && configRes.value.ok) {
            persistentConfig = await configRes.value.json();
            applyConfigToUI(persistentConfig);
        }

        // Daemon status
        if (daemonRes.status === 'fulfilled' && daemonRes.value.ok) {
            daemonStatus = await daemonRes.value.json();
            updateDaemonStatusUI(daemonStatus);
        }

        // Daemon logs
        if (logsRes.status === 'fulfilled' && logsRes.value.ok) {
            const logsData = await logsRes.value.json();
            daemonLogs = logsData.logs || [];
            renderDaemonLogs(daemonLogs);
        }

        // Dreamer status
        if (dreamerRes.status === 'fulfilled' && dreamerRes.value.ok) {
            dreamerStatus = await dreamerRes.value.json();
            updateDreamerStatusUI(dreamerStatus);
        }

    } catch (err) {
        console.error('[PERSISTENT] Erreur de chargement:', err);
    }
}


/**
 * Applique la config aux éléments du formulaire
 */
function applyConfigToUI(config) {
    // Daemon
    const daemonToggle = document.getElementById('daemon-toggle');
    const daemonInterval = document.getElementById('daemon-interval');
    const daemonModel = document.getElementById('daemon-model');
    
    if (daemonToggle) daemonToggle.checked = config.daemon_enabled !== false;
    if (daemonInterval) {
        daemonInterval.value = config.daemon_interval_minutes || 10;
        document.getElementById('daemon-interval-value').textContent = (config.daemon_interval_minutes || 10) + ' min';
    }
    if (daemonModel) daemonModel.value = config.daemon_model || 'leger';

    // Dreamer
    const dreamerToggle = document.getElementById('dreamer-toggle');
    const dreamerSchedule = document.getElementById('dreamer-schedule');
    const dreamerIdle = document.getElementById('dreamer-idle');
    const dreamerModel = document.getElementById('dreamer-model');

    if (dreamerToggle) dreamerToggle.checked = config.dreamer_enabled !== false;
    if (dreamerSchedule) dreamerSchedule.value = config.dreamer_schedule || '02:00';
    if (dreamerIdle) {
        dreamerIdle.value = config.dreamer_idle_trigger_hours || 3;
        document.getElementById('dreamer-idle-value').textContent = (config.dreamer_idle_trigger_hours || 3) + 'h';
    }
    if (dreamerModel) dreamerModel.value = config.dreamer_model || 'leger';

    // Routines
    const routinesModel = document.getElementById('routines-model');
    if (routinesModel) routinesModel.value = config.routines_model || 'fort';
}


/**
 * Met à jour l'UI du daemon avec les données de statut
 */
function updateDaemonStatusUI(status) {
    // Badge de statut
    const badge = document.getElementById('daemon-status-badge');
    if (badge) {
        if (!status.enabled) {
            badge.textContent = '⏸️ Désactivé';
            badge.style.color = 'var(--text-muted)';
        } else if (status.anomalies && status.anomalies.length > 0) {
            badge.textContent = `⚠️ ${status.anomalies.length} anomalie(s)`;
            badge.style.color = 'var(--warning)';
        } else if (status.total_cycles > 0) {
            badge.textContent = '✅ Actif';
            badge.style.color = 'var(--success)';
        } else {
            badge.textContent = '🔄 Démarrage...';
            badge.style.color = 'var(--accent-primary)';
        }
    }

    // Métriques
    const el = (id) => document.getElementById(id);
    if (el('daemon-total-cycles')) el('daemon-total-cycles').textContent = status.total_cycles || 0;
    if (el('daemon-errors')) el('daemon-errors').textContent = status.errors_count || 0;
    if (el('daemon-last-cycle')) {
        el('daemon-last-cycle').textContent = status.last_cycle_at
            ? new Date(status.last_cycle_at).toLocaleTimeString('fr-FR')
            : '—';
    }
    if (el('daemon-last-duration')) {
        el('daemon-last-duration').textContent = status.last_cycle_duration_ms
            ? `${status.last_cycle_duration_ms}ms`
            : '—';
    }

    // Anomalies
    const anomaliesDiv = document.getElementById('daemon-anomalies');
    const anomaliesList = document.getElementById('daemon-anomalies-list');
    if (anomaliesDiv && anomaliesList && status.anomalies && status.anomalies.length > 0) {
        anomaliesDiv.style.display = 'block';
        anomaliesList.innerHTML = status.anomalies.map(a =>
            `<div style="padding:4px 0;border-bottom:1px solid var(--border-color);">
                <span style="color:var(--warning);">[${a.check}]</span> ${a.alert || a.status}
            </div>`
        ).join('');
    } else if (anomaliesDiv) {
        anomaliesDiv.style.display = 'none';
    }
}


/**
 * Rendu des logs du daemon dans le tableau
 */
function renderDaemonLogs(logs) {
    const tbody = document.getElementById('daemon-logs-body');
    if (!tbody) return;

    if (!logs || logs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:12px;color:var(--text-muted);">Aucun cycle enregistré</td></tr>';
        return;
    }

    tbody.innerHTML = logs.map(log => {
        const time = log.timestamp ? new Date(log.timestamp).toLocaleTimeString('fr-FR') : '—';
        const checksStr = log.checks_summary
            ? Object.entries(log.checks_summary).map(([k, v]) => {
                const emoji = v === 'ok' ? '✅' : v === 'warning' ? '⚠️' : v === 'error' ? '❌' : '⏭️';
                return `${emoji} ${k.replace('_', ' ')}`;
              }).join(', ')
            : '—';
        const anomalyColor = log.anomalies_count > 0 ? 'var(--warning)' : 'var(--text-muted)';
        return `
            <tr style="border-bottom:1px solid var(--border-color);">
                <td style="padding:4px 8px;">${time}</td>
                <td style="padding:4px 8px;text-align:right;">${log.duration_ms}ms</td>
                <td style="padding:4px 8px;text-align:center;color:${anomalyColor};font-weight:600;">${log.anomalies_count}</td>
                <td style="padding:4px 8px;font-size:0.72rem;">${checksStr}</td>
            </tr>
        `;
    }).join('');
}


/**
 * Met à jour l'UI du dreamer avec les données de statut
 */
function updateDreamerStatusUI(status) {
    const badge = document.getElementById('dreamer-status-badge');
    if (badge) {
        if (status.running) {
            badge.textContent = '🔄 En cours...';
            badge.style.color = 'var(--accent-secondary)';
        } else if (!status.enabled) {
            badge.textContent = '⏸️ Désactivé';
            badge.style.color = 'var(--text-muted)';
        } else if (status.total_runs > 0) {
            badge.textContent = '✅ Prêt';
            badge.style.color = 'var(--success)';
        } else {
            badge.textContent = '🌙 En attente';
            badge.style.color = 'var(--text-muted)';
        }
    }

    // Métriques
    const el = (id) => document.getElementById(id);
    if (el('dreamer-total-runs')) el('dreamer-total-runs').textContent = status.total_runs || 0;
    if (el('dreamer-last-run')) {
        el('dreamer-last-run').textContent = status.last_run_at
            ? new Date(status.last_run_at).toLocaleTimeString('fr-FR')
            : '—';
    }
    if (el('dreamer-last-duration')) {
        el('dreamer-last-duration').textContent = status.last_run_duration_ms
            ? `${status.last_run_duration_ms}ms`
            : '—';
    }
    if (el('dreamer-next')) {
        el('dreamer-next').textContent = status.next_scheduled
            ? new Date(status.next_scheduled).toLocaleTimeString('fr-FR')
            : status.schedule || '02:00';
    }

    // Dernier rapport
    const reportDiv = document.getElementById('dreamer-report');
    const reportContent = document.getElementById('dreamer-report-content');
    if (reportDiv && reportContent && status.last_report) {
        reportDiv.style.display = 'block';
        const rpt = status.last_report;
        const actions = rpt.actions || {};
        const consolidation = actions.consolidation || {};
        const applied = actions.applied || {};
        reportContent.innerHTML = `
            <div style="margin-bottom:8px;"><strong>Résumé :</strong> ${rpt.summary || '—'}</div>
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;">
                <div>📉 Decayed : <strong>${consolidation.decayed || 0}</strong></div>
                <div>🗑️ GC archivées : <strong>${consolidation.gc_archived || 0}</strong></div>
                <div>📝 Leçons ajoutées : <strong>${applied.lessons_added || 0}</strong></div>
            </div>
        `;
    }
}


// ──────────────────────────────────────────────────────────────────
// Actions utilisateur
// ──────────────────────────────────────────────────────────────────

function onDaemonToggle(enabled) {
    savePersistentField('daemon_enabled', enabled);
}

function onDreamerToggle(enabled) {
    savePersistentField('dreamer_enabled', enabled);
}

async function saveDaemonConfig() {
    const interval = parseInt(document.getElementById('daemon-interval')?.value || '10');
    const model = document.getElementById('daemon-model')?.value || 'leger';
    const enabled = document.getElementById('daemon-toggle')?.checked ?? true;

    await savePersistentFields({
        daemon_enabled: enabled,
        daemon_interval_minutes: interval,
        daemon_model: model,
    });
}

async function saveDreamerConfig() {
    const schedule = document.getElementById('dreamer-schedule')?.value || '02:00';
    const idle = parseInt(document.getElementById('dreamer-idle')?.value || '3');
    const model = document.getElementById('dreamer-model')?.value || 'leger';
    const enabled = document.getElementById('dreamer-toggle')?.checked ?? true;
    const routinesModel = document.getElementById('routines-model')?.value || 'fort';

    await savePersistentFields({
        dreamer_enabled: enabled,
        dreamer_schedule: schedule,
        dreamer_idle_trigger_hours: idle,
        dreamer_model: model,
        routines_model: routinesModel,
    });
}

async function savePersistentField(key, value) {
    await savePersistentFields({ [key]: value });
}

async function savePersistentFields(fields) {
    try {
        const res = await fetch('/api/persistent-agents/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(fields),
        });
        if (res.ok) {
            const data = await res.json();
            console.log('[PERSISTENT] Config sauvegardée:', data);
            // Rafraîchir les données
            loadPersistentData();
        } else {
            const err = await res.text();
            console.error('[PERSISTENT] Erreur de sauvegarde:', err);
            alert(`Erreur de sauvegarde: ${err}`);
        }
    } catch (err) {
        console.error('[PERSISTENT] Erreur réseau:', err);
        alert(`Erreur réseau: ${err.message}`);
    }
}

async function triggerDreamerManual() {
    const btn = event.target;
    const originalText = btn.textContent;
    btn.textContent = '⏳ Consolidation en cours...';
    btn.disabled = true;

    try {
        const res = await fetch('/api/dreamer/trigger', { method: 'POST' });
        if (res.ok) {
            const data = await res.json();
            console.log('[DREAMER] Cycle manuel terminé:', data);
            // Rafraîchir l'UI
            loadPersistentData();
        } else {
            const err = await res.text();
            alert(`Erreur: ${err}`);
        }
    } catch (err) {
        alert(`Erreur réseau: ${err.message}`);
    } finally {
        btn.textContent = originalText;
        btn.disabled = false;
    }
}
