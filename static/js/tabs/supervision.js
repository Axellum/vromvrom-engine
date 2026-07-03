/* ============================================================
   SUPERVISION.JS — Onglet de supervision globale 24/7
   Regroupe sous forme de sous-onglets :
   - Agents 24/7 (Daemon sentinelle & autoDream)
   - Swarm Workers (monitoring des agents distants)

   Panneau de synthèse haut de page :
   - Statut Daemon (actif/inactif, cycles, anomalies)
   - Statut Dreamer (prêt/en cours, dernier run)
   - Workers Swarm (nombre online/total)
   ============================================================ */

// Identifiant unique pour éviter les conflits de classe CSS avec les autres groupes
let activeSupervisionSubTab = 'persistent';

function renderSupervision(container) {
    container.innerHTML = `
        <!-- ═══ Panneau de synthèse 24/7 ═══ -->
        <div class="glass-panel" id="supervision-summary-panel"
             style="background:linear-gradient(135deg,rgba(99,102,241,0.08),rgba(168,85,247,0.05));
                    border-color:rgba(99,102,241,0.2);margin-bottom:0;">
            <div class="section-title" style="margin-bottom:var(--space-md);">
                <span>🔄 Supervision Globale</span>
                <span class="subtitle" id="supervision-last-refresh">Chargement...</span>
            </div>
            <div class="grid grid-3" style="gap:var(--space-lg);">

                <!-- Daemon Sentinelle -->
                <div style="display:flex;gap:var(--space-md);align-items:flex-start;">
                    <div style="font-size:1.6rem;line-height:1;">🛡️</div>
                    <div style="flex:1;min-width:0;">
                        <div style="font-weight:700;font-size:0.88rem;margin-bottom:3px;">Démon Sentinelle</div>
                        <div id="sup-daemon-badge" style="font-size:0.78rem;color:var(--text-muted);">⏳ Chargement...</div>
                        <div style="display:flex;gap:var(--space-lg);margin-top:var(--space-xs);font-size:0.72rem;color:var(--text-secondary);">
                            <span>Cycles : <strong id="sup-daemon-cycles">–</strong></span>
                            <span>Erreurs : <strong id="sup-daemon-errors" style="color:var(--error);">–</strong></span>
                        </div>
                        <div style="font-size:0.68rem;color:var(--text-muted);margin-top:2px;">
                            Dernier : <span id="sup-daemon-last">–</span>
                        </div>
                    </div>
                </div>

                <!-- autoDream -->
                <div style="display:flex;gap:var(--space-md);align-items:flex-start;">
                    <div style="font-size:1.6rem;line-height:1;">🌙</div>
                    <div style="flex:1;min-width:0;">
                        <div style="font-weight:700;font-size:0.88rem;margin-bottom:3px;">autoDream</div>
                        <div id="sup-dreamer-badge" style="font-size:0.78rem;color:var(--text-muted);">⏳ Chargement...</div>
                        <div style="display:flex;gap:var(--space-lg);margin-top:var(--space-xs);font-size:0.72rem;color:var(--text-secondary);">
                            <span>Consolidations : <strong id="sup-dreamer-runs">–</strong></span>
                            <span>Prochain : <strong id="sup-dreamer-next">–</strong></span>
                        </div>
                        <div style="font-size:0.68rem;color:var(--text-muted);margin-top:2px;">
                            Dernier : <span id="sup-dreamer-last">–</span>
                        </div>
                    </div>
                </div>

                <!-- Swarm Workers -->
                <div style="display:flex;gap:var(--space-md);align-items:flex-start;">
                    <div style="font-size:1.6rem;line-height:1;">📡</div>
                    <div style="flex:1;min-width:0;">
                        <div style="font-weight:700;font-size:0.88rem;margin-bottom:3px;">Swarm Workers</div>
                        <div id="sup-swarm-badge" style="font-size:0.78rem;color:var(--text-muted);">⏳ Chargement...</div>
                        <div style="display:flex;gap:var(--space-lg);margin-top:var(--space-xs);font-size:0.72rem;color:var(--text-secondary);">
                            <span>Online : <strong id="sup-swarm-online" style="color:var(--success);">–</strong></span>
                            <span>Total : <strong id="sup-swarm-total">–</strong></span>
                        </div>
                        <div style="font-size:0.68rem;color:var(--text-muted);margin-top:2px;">
                            <span id="sup-swarm-detail">–</span>
                        </div>
                    </div>
                </div>

            </div>
        </div>

        <!-- ═══ Sous-onglets ═══ -->
        <!-- Note : data-group="supervision" permet le scope unique des subtabs -->
        <div class="subnav-tabs" data-group="supervision">
            <button class="subtab-btn ${activeSupervisionSubTab === 'persistent' ? 'active' : ''}"
                    data-subtab="persistent"
                    data-group="supervision"
                    onclick="switchSupervisionSubTab('persistent')">🔄 Agents 24/7 (Daemon &amp; Dreamer)</button>
            <button class="subtab-btn ${activeSupervisionSubTab === 'swarm' ? 'active' : ''}"
                    data-subtab="swarm"
                    data-group="supervision"
                    onclick="switchSupervisionSubTab('swarm')">📡 Swarm Workers</button>
            <button class="subtab-btn ${activeSupervisionSubTab === 'events' ? 'active' : ''}"
                    data-subtab="events"
                    data-group="supervision"
                    onclick="switchSupervisionSubTab('events')">📜 Journal d'Audit</button>
        </div>
        <div id="supervision-subtab-content"></div>
    `;

    // Charger la synthèse en arrière-plan
    loadSupervisionSummary();

    // Rendre le sous-onglet actif
    switchSupervisionSubTab(activeSupervisionSubTab);
}

/**
 * Change le sous-onglet actif — scope limité au groupe "supervision"
 */
function switchSupervisionSubTab(subtabId) {
    activeSupervisionSubTab = subtabId;

    // Mettre à jour UNIQUEMENT les boutons du groupe supervision
    document.querySelectorAll('.subtab-btn[data-group="supervision"]').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-subtab') === subtabId);
    });

    const contentDiv = document.getElementById('supervision-subtab-content');
    if (!contentDiv) return;
    contentDiv.innerHTML = '';

    if (subtabId === 'persistent') {
        renderPersistent(contentDiv);
    } else if (subtabId === 'swarm') {
        renderSwarm(contentDiv);
    } else if (subtabId === 'events') {
        renderEvents(contentDiv);
    }
}

/**
 * Charge les données de synthèse pour le panneau de supervision globale
 * Appelle les APIs daemon/status, dreamer/status et swarm/workers en parallèle
 */
async function loadSupervisionSummary() {
    try {
        const [daemonRes, dreamerRes, swarmRes] = await Promise.allSettled([
            fetch('/api/daemon/status').then(r => r.ok ? r.json() : null),
            fetch('/api/dreamer/status').then(r => r.ok ? r.json() : null),
            fetch('/api/swarm/workers').then(r => r.ok ? r.json() : null),
        ]);

        // ── Daemon ──
        const daemon = daemonRes.status === 'fulfilled' ? daemonRes.value : null;
        const daemonBadge = document.getElementById('sup-daemon-badge');
        if (daemonBadge && daemon) {
            if (!daemon.enabled) {
                daemonBadge.innerHTML = '<span style="color:var(--text-muted);">⏸️ Désactivé</span>';
            } else if (daemon.anomalies?.length > 0) {
                daemonBadge.innerHTML = `<span style="color:var(--warning);">⚠️ ${daemon.anomalies.length} anomalie(s)</span>`;
            } else if (daemon.total_cycles > 0) {
                daemonBadge.innerHTML = '<span style="color:var(--success);">✅ Actif</span>';
            } else {
                daemonBadge.innerHTML = '<span style="color:var(--accent-primary);">🔄 Démarrage...</span>';
            }
            const cyclesEl = document.getElementById('sup-daemon-cycles');
            const errorsEl = document.getElementById('sup-daemon-errors');
            const lastEl   = document.getElementById('sup-daemon-last');
            if (cyclesEl) cyclesEl.textContent = daemon.total_cycles ?? '–';
            if (errorsEl) errorsEl.textContent = daemon.errors_count ?? '–';
            if (lastEl) {
                lastEl.textContent = daemon.last_cycle_at
                    ? new Date(daemon.last_cycle_at).toLocaleTimeString('fr-FR')
                    : '–';
            }
        } else if (daemonBadge) {
            daemonBadge.textContent = '❌ Indisponible';
        }

        // ── Dreamer ──
        const dreamer = dreamerRes.status === 'fulfilled' ? dreamerRes.value : null;
        const dreamerBadge = document.getElementById('sup-dreamer-badge');
        if (dreamerBadge && dreamer) {
            if (dreamer.running) {
                dreamerBadge.innerHTML = '<span style="color:var(--accent-secondary);">🔄 En cours...</span>';
            } else if (!dreamer.enabled) {
                dreamerBadge.innerHTML = '<span style="color:var(--text-muted);">⏸️ Désactivé</span>';
            } else if (dreamer.total_runs > 0) {
                dreamerBadge.innerHTML = '<span style="color:var(--success);">✅ Prêt</span>';
            } else {
                dreamerBadge.innerHTML = '<span style="color:var(--text-muted);">🌙 En attente</span>';
            }
            const runsEl  = document.getElementById('sup-dreamer-runs');
            const nextEl  = document.getElementById('sup-dreamer-next');
            const lastEl2 = document.getElementById('sup-dreamer-last');
            if (runsEl) runsEl.textContent = dreamer.total_runs ?? '–';
            if (nextEl) nextEl.textContent = dreamer.schedule || '02:00';
            if (lastEl2) {
                lastEl2.textContent = dreamer.last_run_at
                    ? new Date(dreamer.last_run_at).toLocaleTimeString('fr-FR')
                    : '–';
            }
        } else if (dreamerBadge) {
            dreamerBadge.textContent = '❌ Indisponible';
        }

        // ── Swarm ──
        const swarmData = swarmRes.status === 'fulfilled' ? swarmRes.value : null;
        const swarmBadge  = document.getElementById('sup-swarm-badge');
        const swarmOnline = document.getElementById('sup-swarm-online');
        const swarmTotal  = document.getElementById('sup-swarm-total');
        const swarmDetail = document.getElementById('sup-swarm-detail');
        if (swarmBadge && swarmData) {
            const workers = swarmData.workers || [];
            const online  = workers.filter(w => w.status === 'online').length;
            const total   = workers.length;
            if (swarmOnline) swarmOnline.textContent = online;
            if (swarmTotal)  swarmTotal.textContent  = total;
            if (swarmBadge) {
                if (online === total && total > 0) {
                    swarmBadge.innerHTML = `<span style="color:var(--success);">✅ ${online}/${total} online</span>`;
                } else if (online > 0) {
                    swarmBadge.innerHTML = `<span style="color:var(--warning);">⚠️ ${online}/${total} online</span>`;
                } else {
                    swarmBadge.innerHTML = `<span style="color:var(--error);">❌ Aucun worker online</span>`;
                }
            }
            if (swarmDetail) {
                swarmDetail.textContent = workers.map(w =>
                    `${w.name} (${w.status})`
                ).join(', ') || '–';
            }
        } else if (swarmBadge) {
            swarmBadge.textContent = '❌ Indisponible';
        }

        // Horodatage du rafraîchissement
        const refresh = document.getElementById('supervision-last-refresh');
        if (refresh) {
            refresh.textContent = `Rafraîchi à ${new Date().toLocaleTimeString('fr-FR')}`;
        }

    } catch (err) {
        console.error('[SUPERVISION SUMMARY] Erreur:', err);
    }
}
