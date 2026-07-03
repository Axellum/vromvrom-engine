/* ============================================================
   EVENTS.JS — Page 11 : Journal d'Audit (Event Sourcing)
   Visualisation chronologique des événements et audit trail.
   ============================================================ */

let eventsAutoRefreshInterval = null;
let selectedEventSessionId = null;

/**
   Rendu principal de l'onglet Journal d'Audit
 */
function renderEvents(container) {
    container.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:var(--space-lg);height:100%;">
            
            <!-- ═══ 1. En-tête & KPIs de l'EventStore ═══ -->
            <div class="glass-panel" style="background:linear-gradient(135deg, rgba(99,102,241,0.08), rgba(16,185,129,0.05)); border-color:rgba(99,102,241,0.2); margin-bottom:0;">
                <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:var(--space-md);margin-bottom:var(--space-md);">
                    <div>
                        <div class="section-title" style="margin:0;">📜 Journal d'Audit (Event Sourcing)</div>
                        <div style="font-size:0.78rem;color:var(--text-muted);margin-top:2px;">
                            Registre d'audit append-only des événements du moteur multi-agents
                        </div>
                    </div>
                    <div style="display:flex;gap:var(--space-sm);align-items:center;">
                        <button class="btn btn--ghost btn--sm" onclick="loadEventsData()">
                            🔄 Actualiser
                        </button>
                        <label style="display:flex;align-items:center;gap:6px;font-size:0.75rem;color:var(--text-secondary);cursor:pointer;user-select:none;">
                            <input type="checkbox" id="chk-events-auto-refresh" onchange="toggleEventsAutoRefresh(this.checked)">
                            Auto-refresh (5s)
                        </label>
                    </div>
                </div>

                <!-- KPIs -->
                <div class="grid grid-4" style="gap:var(--space-md);">
                    <div class="glass-panel glass-panel--compact" style="text-align:center;">
                        <div style="font-size:0.72rem;color:var(--text-muted);text-transform:uppercase;">Événements Totaux</div>
                        <div style="font-weight:700;font-size:1.3rem;color:var(--accent-primary);" id="evt-kpi-total">–</div>
                    </div>
                    <div class="glass-panel glass-panel--compact" style="text-align:center;">
                        <div style="font-size:0.72rem;color:var(--text-muted);text-transform:uppercase;">Dernier Événement</div>
                        <div style="font-weight:500;font-size:0.8rem;color:var(--text-primary);padding:4px 0;" id="evt-kpi-last">–</div>
                    </div>
                    <div class="glass-panel glass-panel--compact" style="text-align:center;">
                        <div style="font-size:0.72rem;color:var(--text-muted);text-transform:uppercase;">Requêtes Reçues</div>
                        <div style="font-weight:700;font-size:1.3rem;color:var(--success);" id="evt-kpi-requests">–</div>
                    </div>
                    <div class="glass-panel glass-panel--compact" style="text-align:center;">
                        <div style="font-size:0.72rem;color:var(--text-muted);text-transform:uppercase;">Erreurs Journalisées</div>
                        <div style="font-weight:700;font-size:1.3rem;color:var(--error);" id="evt-kpi-errors">–</div>
                    </div>
                </div>
            </div>

            <!-- ═══ 2. Double Colonne : Timeline & Replay ═══ -->
            <div style="display:grid;grid-template-columns:1.2fr 1fr;gap:var(--space-lg);flex:1;min-height:500px;">
                
                <!-- Colonne Gauche : Timeline -->
                <div class="glass-panel" style="display:flex;flex-direction:column;gap:var(--space-md);margin-bottom:0;">
                    <div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border-color);padding-bottom:var(--space-sm);">
                        <span style="font-weight:600;font-size:0.85rem;">🕒 Événements Récents</span>
                        <div style="display:flex;gap:var(--space-sm);align-items:center;">
                            <!-- Filtre par type -->
                            <div class="select-wrap" style="margin-bottom:0;width:150px;">
                                <select id="evt-filter-type" onchange="loadEventsData()" style="padding:2px 6px;font-size:0.72rem;height:auto;">
                                    <option value="">Tous les types</option>
                                    <option value="request_received">📝 requêtes</option>
                                    <option value="agent_started">🤖 agents</option>
                                    <option value="tool_called">🛠️ outils</option>
                                    <option value="response_sent">🏁 réponses</option>
                                    <option value="error">❌ erreurs</option>
                                </select>
                            </div>
                            <!-- Limite -->
                            <div class="select-wrap" style="margin-bottom:0;width:90px;">
                                <select id="evt-filter-limit" onchange="loadEventsData()" style="padding:2px 6px;font-size:0.72rem;height:auto;">
                                    <option value="50">50 lignes</option>
                                    <option value="100" selected>100 lignes</option>
                                    <option value="200">200 lignes</option>
                                </select>
                            </div>
                        </div>
                    </div>

                    <!-- Liste Scrollable -->
                    <div id="evt-timeline-list" style="flex:1;overflow-y:auto;max-height:600px;display:flex;flex-direction:column;gap:var(--space-sm);padding-right:4px;">
                        <div class="empty-state"><div class="empty-state__icon">⏳</div><div>Chargement des événements...</div></div>
                    </div>
                </div>

                <!-- Colonne Droite : Audit / Replay de Session -->
                <div class="glass-panel" style="display:flex;flex-direction:column;gap:var(--space-md);margin-bottom:0;border-color:rgba(16,185,129,0.15);">
                    <div style="border-bottom:1px solid var(--border-color);padding-bottom:var(--space-sm);display:flex;justify-content:space-between;align-items:center;">
                        <span style="font-weight:600;font-size:0.85rem;color:var(--success);">🔍 Audit Trail &amp; Replay Session</span>
                        <button class="btn btn--ghost btn--xs" onclick="clearEventSessionReplay()" id="btn-evt-clear-replay" style="display:none;">
                            Effacer la sélection
                        </button>
                    </div>

                    <div id="evt-replay-container" style="flex:1;display:flex;flex-direction:column;gap:var(--space-md);height:100%;">
                        <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;color:var(--text-muted);font-style:italic;text-align:center;padding:var(--space-xl);gap:var(--space-md);">
                            <div style="font-size:2.5rem;opacity:0.4;">📜</div>
                            <div style="font-size:0.8rem;">
                                Sélectionnez un événement contenant un ID de session à gauche pour charger le replay complet de l'exécution et l'audit trail associé.
                            </div>
                        </div>
                    </div>
                </div>

            </div>

        </div>
    `;

    // Nettoyer les intervalles précédents éventuels
    if (eventsAutoRefreshInterval) {
        clearInterval(eventsAutoRefreshInterval);
        eventsAutoRefreshInterval = null;
    }

    // Charger les données initiales
    loadEventsData();
}

/**
   Active ou désactive le rafraîchissement automatique des événements
 */
function toggleEventsAutoRefresh(enabled) {
    if (eventsAutoRefreshInterval) {
        clearInterval(eventsAutoRefreshInterval);
        eventsAutoRefreshInterval = null;
    }
    if (enabled) {
        eventsAutoRefreshInterval = setInterval(() => {
            loadEventsData(true); // mode silencieux (ne pas réinitialiser les sélections)
        }, 5000);
    }
}

/**
   Charge les statistiques globales et la liste des événements
 */
async function loadEventsData(silent = false) {
    try {
        const typeFilter = document.getElementById('evt-filter-type')?.value || '';
        const limitFilter = document.getElementById('evt-filter-limit')?.value || '100';

        // Charger les stats et les événements en parallèle
        const [statsRes, eventsRes] = await Promise.allSettled([
            fetch('/api/events/stats').then(r => r.ok ? r.json() : null),
            fetch(`/api/events?limit=${limitFilter}${typeFilter ? '&type=' + typeFilter : ''}`).then(r => r.ok ? r.json() : null)
        ]);

        // 1. Mettre à jour les KPIs
        const stats = statsRes.status === 'fulfilled' ? statsRes.value : null;
        if (stats && stats.stats) {
            const s = stats.stats;
            const totalEl = document.getElementById('evt-kpi-total');
            const lastEl = document.getElementById('evt-kpi-last');
            const reqsEl = document.getElementById('evt-kpi-requests');
            const errsEl = document.getElementById('evt-kpi-errors');

            if (totalEl) totalEl.textContent = s.total_events ?? '0';
            if (reqsEl) reqsEl.textContent = s.by_type?.request_received ?? '0';
            if (errsEl) errsEl.textContent = s.by_type?.error ?? '0';

            if (lastEl && s.last_event_ts) {
                const lastDate = new Date(s.last_event_ts);
                lastEl.textContent = lastDate.toLocaleTimeString('fr-FR');
                lastEl.title = s.last_event_ts;
            }
        }

        // 2. Rendre la timeline
        const eventsData = eventsRes.status === 'fulfilled' ? eventsRes.value : null;
        if (eventsData && eventsData.events) {
            renderEventsTimeline(eventsData.events, silent);
        } else {
            const list = document.getElementById('evt-timeline-list');
            if (list) {
                list.innerHTML = `<div class="empty-state" style="color:var(--error);">❌ Impossible de récupérer la liste des événements.</div>`;
            }
        }

    } catch (err) {
        console.error('[EVENTS] Erreur de chargement:', err);
    }
}

/**
   Rendu physique de la timeline
 */
function renderEventsTimeline(events, silent = false) {
    const list = document.getElementById('evt-timeline-list');
    if (!list) return;

    if (events.length === 0) {
        list.innerHTML = `
            <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;color:var(--text-muted);font-style:italic;padding:var(--space-xl);gap:var(--space-md);">
                <div style="font-size:2rem;opacity:0.3;">📭</div>
                <div style="font-size:0.75rem;">Aucun événement enregistré correspondant aux critères.</div>
            </div>
        `;
        return;
    }

    list.innerHTML = events.map(evt => {
        // Déterminer les icônes et classes de couleurs
        let badgeColor = 'var(--text-muted)';
        let typeLabel = evt.type;
        let icon = '⚫';

        if (evt.type === 'request_received') {
            badgeColor = 'var(--accent-primary)';
            typeLabel = 'Requête';
            icon = '📝';
        } else if (evt.type === 'agent_started') {
            badgeColor = 'var(--accent-secondary)';
            typeLabel = evt.agent || 'Agent';
            icon = '🤖';
        } else if (evt.type === 'tool_called') {
            badgeColor = 'var(--success)';
            typeLabel = evt.payload?.tool || 'Outil';
            icon = '🛠️';
        } else if (evt.type === 'response_sent') {
            badgeColor = 'var(--warning)';
            typeLabel = 'Réponse';
            icon = '🏁';
        } else if (evt.type === 'error') {
            badgeColor = 'var(--error)';
            typeLabel = 'Erreur';
            icon = '❌';
        }

        const date = new Date(evt.ts);
        const timeStr = date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        
        // Formater l'extrait du payload pour la preview
        let preview = '';
        if (evt.type === 'request_received') {
            preview = evt.payload?.objective || evt.payload?.user_prompt || '';
        } else if (evt.type === 'agent_started') {
            preview = evt.payload?.task_objective || '';
        } else if (evt.type === 'tool_called') {
            preview = `${evt.payload?.tool} (${evt.payload?.arguments ? Object.keys(evt.payload.arguments).join(', ') : ''})`;
        } else if (evt.type === 'response_sent') {
            preview = evt.payload?.response || '';
        } else if (evt.type === 'error') {
            preview = evt.payload?.message || evt.payload?.error || '';
        }
        
        // Limiter la preview
        const cleanPreview = typeof preview === 'object' ? JSON.stringify(preview) : String(preview);
        const truncatedPreview = cleanPreview.length > 70 ? cleanPreview.substring(0, 67) + '...' : cleanPreview;

        const isSelected = evt.session_id && evt.session_id === selectedEventSessionId;

        return `
            <div class="glass-panel glass-panel--compact event-row ${isSelected ? 'active-event' : ''}" 
                 style="padding:var(--space-sm) var(--space-md); cursor:${evt.session_id ? 'pointer' : 'default'}; 
                        border-left: 3px solid ${badgeColor}; display:flex; gap:var(--space-md); align-items:center;
                        background:${isSelected ? 'rgba(16,185,129,0.06)' : ''};"
                 ${evt.session_id ? `onclick="selectEventSession('${evt.session_id}')"` : ''}>
                <div style="font-size:1.1rem; line-height:1;">${icon}</div>
                <div style="flex:1; min-width:0; display:flex; flex-direction:column; gap:2px;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span style="font-weight:700; font-size:0.75rem; color:${badgeColor}; text-transform:uppercase;">${typeLabel}</span>
                        <span style="font-size:0.65rem; color:var(--text-muted); font-family:var(--font-mono);">${timeStr}</span>
                    </div>
                    <div style="font-size:0.75rem; color:var(--text-primary); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${cleanPreview}">
                        ${truncatedPreview}
                    </div>
                    ${evt.session_id ? `
                        <div style="font-size:0.62rem; color:var(--text-muted); display:flex; gap:8px;">
                            <span>Session : <strong style="color:var(--text-secondary);">${evt.session_id}</strong></span>
                            ${evt.source ? `<span>Source : <strong>${evt.source}</strong></span>` : ''}
                        </div>
                    ` : ''}
                </div>
            </div>
        `;
    }).join('');
}

/**
   Sélectionne une session pour rejouer l'audit trail
 */
async function selectEventSession(sessionId) {
    if (!sessionId) return;
    selectedEventSessionId = sessionId;
    
    // Mettre à jour l'effet actif à gauche
    document.querySelectorAll('.event-row').forEach(row => {
        const onclickAttr = row.getAttribute('onclick') || '';
        row.classList.toggle('active-event', onclickAttr.includes(sessionId));
        row.style.background = onclickAttr.includes(sessionId) ? 'rgba(16,185,129,0.06)' : '';
    });

    const clearBtn = document.getElementById('btn-evt-clear-replay');
    if (clearBtn) clearBtn.style.display = 'block';

    const container = document.getElementById('evt-replay-container');
    if (!container) return;

    container.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted);">
            <div style="font-size:1.2rem;">⏳ Chargement de l'audit trail...</div>
        </div>
    `;

    try {
        const [eventsRes, replayRes] = await Promise.allSettled([
            fetch(`/api/events/session/${sessionId}`).then(r => r.ok ? r.json() : null),
            fetch(`/api/events/session/${sessionId}/replay`).then(r => r.ok ? r.json() : null)
        ]);

        const events = eventsRes.status === 'fulfilled' && eventsRes.value ? eventsRes.value.events : [];
        const replay = replayRes.status === 'fulfilled' && replayRes.value ? replayRes.value.lines : [];

        if (events.length === 0) {
            container.innerHTML = `
                <div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--error);font-style:italic;">
                    ❌ Aucun événement pour la session ${sessionId}.
                </div>
            `;
            return;
        }

        // Construire le HTML de l'audit trail
        let html = `
            <div style="display:flex;flex-direction:column;gap:var(--space-md);height:100%;min-height:0;">
                <div style="font-size:0.75rem;padding:var(--space-sm) var(--space-md);background:var(--bg-secondary);border-radius:var(--radius-md);border:1px solid var(--border-color);display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;">
                    <span>Session : <strong style="font-family:var(--font-mono);color:var(--text-primary);">${sessionId}</strong></span>
                    <span>Événements : <strong style="color:var(--success);">${events.length}</strong></span>
                </div>
                
                <!-- Sous-onglets Replay/JSON -->
                <div style="display:flex;gap:12px;border-bottom:1px solid var(--border-color);padding-bottom:6px;font-size:0.78rem;">
                    <button class="btn btn--xs btn--success" onclick="toggleReplayViewMode('timeline')" id="btn-mode-tl">Timeline</button>
                    <button class="btn btn--xs btn--ghost" onclick="toggleReplayViewMode('text')" id="btn-mode-txt">Replay Brut</button>
                </div>

                <!-- Conteneur défilement -->
                <div id="evt-replay-details-scroll" style="flex:1;overflow-y:auto;max-height:480px;padding-right:4px;">
                    <!-- Rendu dynamique par mode -->
                </div>
            </div>
        `;
        container.innerHTML = html;

        // Stocker en local pour le basculement d'onglet interne
        window.__activeReplayEvents = events;
        window.__activeReplayText = replay;

        // Mode par défaut : timeline visuelle
        toggleReplayViewMode('timeline');

    } catch (err) {
        console.error('[REPLAY] Erreur:', err);
        container.innerHTML = `<div class="empty-state" style="color:var(--error);">❌ Erreur de chargement du replay.</div>`;
    }
}

/**
   Bascule le mode de rendu du replay (timeline visuelle vs texte brut)
 */
function toggleReplayViewMode(mode) {
    const scrollContainer = document.getElementById('evt-replay-details-scroll');
    if (!scrollContainer) return;

    const btnTl = document.getElementById('btn-mode-tl');
    const btnTxt = document.getElementById('btn-mode-txt');

    if (btnTl && btnTxt) {
        btnTl.className = mode === 'timeline' ? 'btn btn--xs btn--success' : 'btn btn--xs btn--ghost';
        btnTxt.className = mode === 'text' ? 'btn btn--xs btn--success' : 'btn btn--xs btn--ghost';
    }

    if (mode === 'timeline') {
        const evts = window.__activeReplayEvents || [];
        scrollContainer.innerHTML = `
            <div style="display:flex;flex-direction:column;gap:var(--space-md);position:relative;padding-left:14px;border-left:1px dashed var(--border-color);margin-left:6px;margin-top:var(--space-sm);">
                ${evts.map(evt => {
                    let icon = '⚫';
                    let title = evt.type;
                    let desc = '';
                    let color = 'var(--text-muted)';
                    let extra = '';

                    if (evt.type === 'request_received') {
                        icon = '📝';
                        title = 'Requête Reçue';
                        desc = evt.payload?.objective || evt.payload?.user_prompt || '';
                        color = 'var(--accent-primary)';
                        if (evt.payload?.request_source) {
                            extra = `Source: ${evt.payload.request_source.type} (${evt.payload.request_source.mode})`;
                        }
                    } else if (evt.type === 'agent_started') {
                        icon = '🤖';
                        title = `Démarrage Agent : ${evt.agent || 'inconnu'}`;
                        desc = evt.payload?.task_objective || '';
                        color = 'var(--accent-secondary)';
                        if (evt.payload?.model) {
                            extra = `Modèle : ${evt.payload.model}`;
                        }
                    } else if (evt.type === 'tool_called') {
                        icon = '🛠️';
                        title = `Outil : ${evt.payload?.tool || 'inconnu'}`;
                        desc = `Arguments : <code style="font-family:var(--font-mono);font-size:0.68rem;color:var(--accent-secondary);">${JSON.stringify(evt.payload?.arguments || {})}</code>`;
                        color = 'var(--success)';
                        if (evt.payload?.result_summary) {
                            extra = `Résultat : ${evt.payload.result_summary}`;
                        }
                    } else if (evt.type === 'response_sent') {
                        icon = '🏁';
                        title = 'Réponse Envoyée';
                        desc = evt.payload?.response || '';
                        color = 'var(--warning)';
                    } else if (evt.type === 'error') {
                        icon = '❌';
                        title = 'Erreur';
                        desc = evt.payload?.message || evt.payload?.error || 'Erreur inconnue';
                        color = 'var(--error)';
                    }

                    const time = evt.ts ? new Date(evt.ts).toLocaleTimeString('fr-FR') : '';

                    return `
                        <div style="position:relative;display:flex;flex-direction:column;gap:4px;">
                            <!-- Bulle d'icône absolue sur la bordure gauche -->
                            <div style="position:absolute;left:-20px;top:0;width:12px;height:12px;display:flex;align-items:center;justify-content:center;font-size:0.75rem;">
                                ${icon}
                            </div>
                            <div style="display:flex;justify-content:space-between;align-items:center;font-size:0.75rem;font-weight:700;color:${color};">
                                <span>${title}</span>
                                <span style="font-size:0.62rem;color:var(--text-muted);font-family:var(--font-mono);">${time}</span>
                            </div>
                            <div style="font-size:0.75rem;color:var(--text-primary);line-height:1.4;background:rgba(255,255,255,0.01);padding:var(--space-xs) var(--space-sm);border-radius:var(--radius-sm);border:1px solid rgba(255,255,255,0.02);word-break:break-word;">
                                ${desc}
                            </div>
                            ${extra ? `<div style="font-size:0.65rem;color:var(--text-muted);margin-left:4px;font-style:italic;">${extra}</div>` : ''}
                        </div>
                    `;
                }).join('')}
            </div>
        `;
    } else {
        const replay = window.__activeReplayText || [];
        scrollContainer.innerHTML = `
            <pre style="background:var(--bg-tertiary);color:var(--text-secondary);font-family:var(--font-mono);font-size:0.72rem;padding:var(--space-md);border-radius:var(--radius-md);border:1px solid var(--border-color);white-space:pre-wrap;line-height:1.5;margin-top:var(--space-sm);">` + 
                (replay.length > 0 ? replay.join('\n') : 'Aucun log textuel généré.') + 
            `</pre>
        `;
    }
}

/**
   Efface le replay et la sélection de session
 */
function clearEventSessionReplay() {
    selectedEventSessionId = null;
    
    // Retirer l'effet actif
    document.querySelectorAll('.event-row').forEach(row => {
        row.classList.remove('active-event');
        row.style.background = '';
    });

    const clearBtn = document.getElementById('btn-evt-clear-replay');
    if (clearBtn) clearBtn.style.display = 'none';

    const container = document.getElementById('evt-replay-container');
    if (container) {
        container.innerHTML = `
            <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;color:var(--text-muted);font-style:italic;text-align:center;padding:var(--space-xl);gap:var(--space-md);">
                <div style="font-size:2.5rem;opacity:0.4;">📜</div>
                <div style="font-size:0.8rem;">
                    Sélectionnez un événement contenant un ID de session à gauche pour charger le replay complet de l'exécution et l'audit trail associé.
                </div>
            </div>
        `;
    }
}
