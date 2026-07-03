/* ============================================================
   SWARM.JS — Gestion et Monitoring des Workers du Swarm (V8)
   ============================================================ */

let swarmWorkersList = [];

function renderSwarm(container) {
    container.innerHTML = `
        <div class="glass-panel" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:var(--space-md);">
            <div>
                <div class="section-title" style="margin-bottom:0.25rem;">📡 Swarm Workers (Inférence Distribuée)</div>
                <div style="font-size:0.83rem;color:var(--text-secondary);">
                    Surveillance et enregistrement des agents workers s'exécutant sur le réseau local (ex: Freebox VM).
                </div>
            </div>
            <div style="display:flex;gap:var(--space-sm);">
                <button class="btn btn--ghost btn--sm" id="btn-ping-swarm" onclick="triggerPingSwarm()">
                    🔄 Ping Heartbeat
                </button>
                <button class="btn btn--success btn--sm" onclick="openRegisterWorkerModal()">
                    ➕ Ajouter un Worker
                </button>
            </div>
        </div>

        <!-- Grille des Workers -->
        <div class="grid grid-2" id="swarm-workers-grid">
            <div class="empty-state"><div class="empty-state__icon">📡</div><div>Chargement des workers...</div></div>
        </div>

        <!-- Modale d'Enregistrement de Worker -->
        <div id="register-worker-modal" class="modal-overlay">
            <div class="glass-panel modal-content" style="max-width:500px; text-align:left;">
                <h3 style="font-family:var(--font-display);font-weight:700;font-size:1.15rem;margin-bottom:var(--space-lg);display:flex;align-items:center;gap:var(--space-sm);">
                    <span>📡</span> Enregistrer un Nouveau Worker
                </h3>
                <div style="display:flex;flex-direction:column;gap:var(--space-lg);">
                    <div class="form-group">
                        <label class="form-label" for="worker-name">Nom technique unique</label>
                        <input type="text" id="worker-name" placeholder="ex: worker-freebox">
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="worker-host">Adresse IP locale ou Host</label>
                        <input type="text" id="worker-host" placeholder="ex: 192.168.0.16">
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="worker-port">Port HTTP (par défaut 8780)</label>
                        <input type="number" id="worker-port" value="8780">
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="worker-capabilities">Capacités (séparées par des virgules)</label>
                        <input type="text" id="worker-capabilities" placeholder="ex: analysis, review, search, summary" value="analysis, review, search, summary, classify">
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="worker-desc">Description</label>
                        <input type="text" id="worker-desc" placeholder="ex: VM Freebox Delta - Routage APIs Cloud">
                    </div>
                </div>
                <div style="display:flex;gap:var(--space-sm);margin-top:var(--space-xl);justify-content:flex-end;">
                    <button class="btn btn--ghost" onclick="closeRegisterWorkerModal()">Annuler</button>
                    <button class="btn btn--success" onclick="submitRegisterWorker()">Enregistrer</button>
                </div>
            </div>
        </div>
    `;

    loadSwarmData();
}

/**
 * Charge les données depuis l'API FastAPI
 */
async function loadSwarmData() {
    const grid = document.getElementById('swarm-workers-grid');
    if (!grid) return;

    try {
        const res = await fetch('/api/swarm/workers');
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        swarmWorkersList = data.workers || [];
        renderWorkersCards(swarmWorkersList, grid);
    } catch (err) {
        console.error('[SWARM] Erreur de chargement:', err);
        grid.innerHTML = `
            <div class="empty-state">
                <div class="empty-state__icon">⚠️</div>
                <div>Erreur de chargement : ${err.message}</div>
            </div>`;
    }
}

/**
 * Rendu visuel des fiches workers
 */
function renderWorkersCards(workers, container) {
    if (!workers || workers.length === 0) {
        container.innerHTML = `
            <div class="empty-state" style="grid-column: span 2;">
                <div class="empty-state__icon">📡</div>
                <div>Aucun worker enregistré pour le moment.</div>
            </div>`;
        return;
    }

    const statusMap = {
        idle: { label: 'En attente', dotClass: 'success', color: 'var(--success)', borderColor: 'rgba(var(--success-rgb),0.2)' },
        busy: { label: 'Actif', dotClass: 'running', color: 'var(--warning)', borderColor: 'rgba(var(--warning-rgb),0.3)' },
        offline: { label: 'Hors ligne', dotClass: 'error', color: 'var(--error)', borderColor: 'rgba(var(--error-rgb),0.3)' },
        unknown: { label: 'Inconnu', dotClass: 'idle', color: 'var(--text-muted)', borderColor: 'var(--border-color)' }
    };

    container.innerHTML = workers.map(w => {
        const s = statusMap[w.status] || statusMap.unknown;
        const capabilitiesHtml = (w.capabilities || []).map(c => 
            `<span style="background:rgba(255,255,255,0.04);border:1px solid var(--border-color);padding:0.12rem 0.35rem;border-radius:4px;font-size:0.68rem;font-family:var(--font-mono);color:var(--accent-secondary);">${c}</span>`
        ).join(' ');

        const heartbeatTime = w.last_heartbeat > 0
            ? new Date(w.last_heartbeat * 1000).toLocaleTimeString('fr-FR')
            : '—';

        return `
            <div class="glass-panel glass-panel--compact" style="display:flex;flex-direction:column;gap:var(--space-md);border-color:${s.borderColor};">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div style="display:flex;align-items:center;gap:var(--space-sm);">
                        <span style="font-size:1.6rem;">🖥️</span>
                        <div>
                            <div style="font-weight:700;font-size:1.05rem;color:var(--text-primary);">${w.name}</div>
                            <div style="font-size:0.75rem;color:var(--text-muted);font-family:var(--font-mono);">${w.host}:${w.port}</div>
                        </div>
                    </div>
                    <div style="display:flex;flex-direction:column;align-items:flex-end;gap:2px;">
                        <span class="status-badge" style="color:${s.color};border-color:${s.borderColor};font-size:0.75rem;padding:0.15rem 0.45rem;">
                            <span class="status-dot ${s.dotClass}"></span>
                            ${s.label}
                        </span>
                        ${w.current_task ? `<span style="font-size:0.62rem;color:var(--warning);font-family:var(--font-mono);">Task: ${w.current_task}</span>` : ''}
                    </div>
                </div>

                <div style="font-size:0.82rem;color:var(--text-secondary);display:flex;flex-direction:column;gap:4px;">
                    <div><strong>Dernier Heartbeat :</strong> ${heartbeatTime}</div>
                    <div style="display:flex;gap:var(--space-lg);margin-top:2px;">
                        <div>Tâches complétées : <strong style="color:var(--success);">${w.tasks_completed}</strong></div>
                        <div>Échecs : <strong style="color:var(--error);">${w.tasks_failed}</strong></div>
                    </div>
                </div>

                <div style="display:flex;flex-direction:column;gap:var(--space-xs);border-top:1px solid var(--border-color);padding-top:var(--space-md);">
                    <div style="font-size:0.75rem;font-weight:600;color:var(--text-muted);">Capacités :</div>
                    <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:2px;">
                        ${capabilitiesHtml || '<span style="font-size:0.72rem;color:var(--text-dim);font-style:italic;">Aucune restriction (Général)</span>'}
                    </div>
                </div>

                <div style="display:flex;justify-content:flex-end;margin-top:var(--space-sm);border-top:1px solid rgba(255,255,255,0.02);padding-top:var(--space-sm);">
                    <button class="btn btn--danger btn--xs" onclick="triggerUnregisterWorker('${w.name}')">
                        Supprimer le worker
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

/**
 * Lance le heartbeat forcé (Ping)
 */
async function triggerPingSwarm() {
    const btn = document.getElementById('btn-ping-swarm');
    if (!btn) return;
    const origHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<svg class="spin" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px;"><line x1="12" y1="2" x2="12" y2="6"></line><line x1="12" y1="18" x2="12" y2="22"></line></svg> Heartbeat...`;

    try {
        const res = await fetch('/api/swarm/workers/ping', { method: 'POST' });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        swarmWorkersList = data.workers || [];
        const grid = document.getElementById('swarm-workers-grid');
        if (grid) renderWorkersCards(swarmWorkersList, grid);
        showToast('success', 'Heartbeats Swarm actualisés avec succès.');
    } catch (err) {
        showToast('error', 'Échec ping : ' + err.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = origHtml;
    }
}

/**
 * Modale de création
 */
function openRegisterWorkerModal() {
    document.getElementById('register-worker-modal')?.classList.add('visible');
}

function closeRegisterWorkerModal() {
    document.getElementById('register-worker-modal')?.classList.remove('visible');
}

/**
 * Soumission du formulaire d'enregistrement de worker
 */
async function submitRegisterWorker() {
    const name = document.getElementById('worker-name')?.value.trim();
    const host = document.getElementById('worker-host')?.value.trim();
    const portVal = document.getElementById('worker-port')?.value;
    const capabilitiesStr = document.getElementById('worker-capabilities')?.value.trim();
    const desc = document.getElementById('worker-desc')?.value.trim();

    if (!name || !host) {
        showToast('warning', 'Le nom technique et l\'hôte sont obligatoires.');
        return;
    }

    const port = parseInt(portVal) || 8780;
    const capabilities = capabilitiesStr ? capabilitiesStr.split(',').map(c => c.trim()).filter(c => c) : [];

    const payload = { name, host, port, capabilities };
    if (desc) payload.description = desc;

    try {
        const res = await fetch('/api/swarm/workers/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        swarmWorkersList = data.workers || [];
        const grid = document.getElementById('swarm-workers-grid');
        if (grid) renderWorkersCards(swarmWorkersList, grid);
        closeRegisterWorkerModal();
        showToast('success', `Worker Swarm "${name}" enregistré.`);
        
        // Reset champs
        document.getElementById('worker-name').value = '';
        document.getElementById('worker-host').value = '';
        document.getElementById('worker-port').value = '8780';
        document.getElementById('worker-desc').value = '';
    } catch (err) {
        showToast('error', 'Échec enregistrement : ' + err.message);
    }
}

/**
 * Désenregistrer un worker
 */
async function triggerUnregisterWorker(name) {
    if (!confirm(`Désenregistrer définitivement le worker Swarm "${name}" ?`)) return;

    try {
        const res = await fetch(`/api/swarm/workers/unregister/${name}`, { method: 'POST' });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        swarmWorkersList = data.workers || [];
        const grid = document.getElementById('swarm-workers-grid');
        if (grid) renderWorkersCards(swarmWorkersList, grid);
        showToast('info', `Worker "${name}" retiré.`);
    } catch (err) {
        showToast('error', 'Échec retrait : ' + err.message);
    }
}
