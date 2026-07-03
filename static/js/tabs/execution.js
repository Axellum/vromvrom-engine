/* ============================================================
   EXECUTION.JS — Page 2 : Console d'exécution + Stages
   ============================================================ */

function renderExecution(container) {
    container.innerHTML = `
        <!-- Lanceur -->
        <div class="glass-panel">
            <div class="section-title">Lancer une exécution multi-agents</div>
            <div class="form-group">
                <label class="form-label" for="task-objective">Définir l'objectif ou la tâche à accomplir</label>
                <textarea id="task-objective" placeholder="Exemple : Crée un fichier texte 'a.txt' contenant le mot 'Bonjour'..."></textarea>
            </div>
            <div style="display:flex;gap:var(--space-sm);align-items:center;">
                <button id="btn-run" class="btn" onclick="startExecution()">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
                    Exécuter l'Orchestrateur
                </button>
                <button id="btn-stop" class="btn btn--danger" onclick="stopExecution()" style="display:none;">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="2"></rect></svg>
                    Arrêter
                </button>
            </div>
        </div>

        <!-- Barre de progression globale du DAG + Compteur coût live -->
        <div class="glass-panel" id="dag-progress-panel" style="display:none;">
            <div class="section-title">
                <span>Progression du DAG</span>
                <div style="display:flex;gap:var(--space-lg);align-items:center;">
                    <span class="subtitle" id="dag-progress-text">0 / 0 tâches</span>
                    <span id="dag-live-cost" style="font-family:var(--font-mono);font-size:0.78rem;color:var(--success);font-weight:700;">0.0000 $</span>
                    <span id="dag-live-tokens" style="font-family:var(--font-mono);font-size:0.72rem;color:var(--text-muted);">0 tokens</span>
                </div>
            </div>
            <div class="progress-bar" style="height:8px;">
                <div class="progress-bar__fill" id="dag-progress-fill" style="width:0%;transition:width 0.5s ease;"></div>
            </div>
        </div>

        <!-- Visualiseur de Stages -->
        <div class="glass-panel">
            <div class="section-title">
                <span>Phases d'Exécution Asynchrone (Stages)</span>
                <span class="subtitle" id="stage-count-badge">Aucune tâche active</span>
            </div>
            <div id="stages-visualizer" style="display:flex;flex-direction:column;gap:var(--space-md);">
                <div class="empty-state">
                    <div class="empty-state__icon">🗂️</div>
                    <div>Le visualiseur s'activera lors de l'exécution d'une tâche.</div>
                    <div style="font-size:0.8rem;color:var(--text-muted);">Le Planner découpera automatiquement l'objectif par phases parallèles.</div>
                </div>
            </div>
        </div>

        <!-- Axe 3 — Timeline d'Exécution Gantt -->
        <div class="glass-panel" id="execution-timeline" style="display:none;">
            <div class="section-title">
                <span>⏱️ Timeline d'Exécution</span>
                <button class="btn btn--ghost btn--xs" onclick="resetTimeline(); document.getElementById('execution-timeline').style.display='none';">Effacer</button>
            </div>
            <div id="timeline-svg-container" style="width:100%;overflow-x:auto;">
            </div>
        </div>

        <!-- Panneau Trace de Pensée (Chain of Thought) -->
        <div class="glass-panel" id="cot-panel" style="display:none;">
            <div class="section-title collapsible-header" onclick="toggleCoTPanel()">
                <span>🧠 Trace de Pensée (Chain of Thought)</span>
                <div style="display:flex;align-items:center;gap:var(--space-sm);">
                    <span id="cot-status" class="subtitle">En attente</span>
                    <span class="arrow">▼</span>
                </div>
            </div>
            <div id="cot-body" class="cot-console">
                <div class="empty-state" style="padding:var(--space-lg);">
                    <div>Le flux de raisonnement s'affichera ici en temps réel.</div>
                </div>
            </div>
        </div>

        <!-- D2 — Panneau Generative UI (composants dynamiques de l'agent) -->
        <div class="glass-panel" id="gen-ui-panel" style="display:none;">
            <div class="section-title">
                <span>🎨 Composants Générés par l'Agent</span>
                <button class="btn btn--ghost btn--xs" onclick="clearGenerativeUI()">Effacer</button>
            </div>
            <div id="gen-ui-container" style="display:flex;flex-direction:column;gap:var(--space-md);">
            </div>
        </div>

        <!-- Visual-QA — Rendu Visuel & Analyse Vision -->
        <div class="glass-panel" id="visual-qa-panel" style="display:none;">
            <div class="section-title">
                <span>👁️ Rendu Visuel &amp; Analyse Vision (Visual-QA)</span>
                <button class="btn btn--ghost btn--xs" onclick="document.getElementById('visual-qa-panel').style.display='none';">Fermer</button>
            </div>
            <div style="display:flex;gap:var(--space-lg);flex-wrap:wrap;margin-top:var(--space-md);">
                <div style="flex:1;min-width:280px;max-width:500px;border:1px solid var(--border-color);border-radius:var(--radius-md);overflow:hidden;background:#000;display:flex;justify-content:center;align-items:center;">
                    <img id="visual-qa-screenshot" style="width:100%;height:auto;object-fit:contain;display:none;" src="" alt="Capture d'écran de l'interface">
                    <div id="visual-qa-no-screenshot" style="padding:var(--space-xl);color:var(--text-muted);text-align:center;">Pas de capture d'écran disponible.</div>
                </div>
                <div style="flex:1.2;min-width:280px;display:flex;flex-direction:column;gap:var(--space-md);">
                    <div>
                        <div style="font-size:0.75rem;color:var(--text-muted);">Score Qualité Visuelle</div>
                        <div style="display:flex;align-items:baseline;gap:var(--space-xs);">
                            <span id="visual-qa-score" style="font-size:2.2rem;font-weight:800;color:var(--accent-primary);">N/A</span>
                            <span style="font-size:1rem;color:var(--text-muted);">/10</span>
                        </div>
                    </div>
                    <div>
                        <div style="font-size:0.75rem;color:var(--text-muted);margin-bottom:2px;">Verdict Visual-QA</div>
                        <p id="visual-qa-verdict" style="font-size:0.85rem;line-height:1.5;margin:0;color:var(--text-secondary);"></p>
                    </div>
                    <div>
                        <div style="font-size:0.75rem;color:var(--text-muted);margin-bottom:4px;">Problèmes Visuels Détectés</div>
                        <ul id="visual-qa-issues" style="font-size:0.8rem;margin:0;padding-left:1.2rem;color:var(--error);line-height:1.4;">
                        </ul>
                    </div>
                </div>
            </div>
        </div>

        <!-- Console de logs enrichie -->
        <div class="glass-panel">
            <div class="section-title">
                <span>Journal d'Audit &amp; Traces d'Exécution</span>
                <div style="display:flex;gap:var(--space-sm);">
                    <button class="btn btn--ghost btn--xs" onclick="clearConsoleLogs()">Effacer</button>
                    <button class="btn btn--ghost btn--xs" onclick="exportLogs()">Exporter</button>
                </div>
            </div>
            <div id="console-logs" class="console">
                <div class="empty-state">
                    <div class="empty-state__icon">📟</div>
                    <div>Aucune trace d'exécution pour le moment.</div>
                </div>
            </div>
        </div>

        <!-- B3 — Panneau Vue Sandbox (écritures en attente) -->
        <div class="glass-panel" id="sandbox-panel" style="display:none;">
            <div class="section-title">
                <span>🛡️ Sandbox — Écritures en attente de validation</span>
                <div style="display:flex;gap:var(--space-sm);align-items:center;">
                    <span class="subtitle" id="sandbox-count-badge">0 fichier(s)</span>
                    <button class="btn btn--success btn--xs" onclick="approveSandbox()">✓ Approuver tout</button>
                    <button class="btn btn--danger btn--xs" onclick="rejectSandbox()">✗ Rejeter tout</button>
                </div>
            </div>
            <div id="sandbox-files-list" style="display:flex;flex-direction:column;gap:var(--space-sm);">
                <div class="empty-state" style="padding:var(--space-md);">
                    <div>Aucune écriture en attente.</div>
                </div>
            </div>
        </div>
    `;
}

async function startExecution() {
    const objective = document.getElementById("task-objective").value.trim();
    if (!objective) {
        showToast("warning", "Veuillez saisir un objectif.");
        return;
    }

    try {
        const btn = document.getElementById("btn-run");
        btn.disabled = true;
        btn.innerHTML = `<svg class="spin" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="2" x2="12" y2="6"></line><line x1="12" y1="18" x2="12" y2="22"></line><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"></line><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"></line><line x1="2" y1="12" x2="6" y2="12"></line><line x1="18" y1="12" x2="22" y2="12"></line><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"></line><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"></line></svg> Exécution...`;

        engineState.runningTasks.clear();
        engineState.healingStages = {};

        await runEngine(objective);

        engineState.isRunning = true;
        setEngineStatusUI("running", "Orchestration en cours...");
        // Afficher le bouton Stop
        toggleStopButton(true);

        if (!sseSource && !pollInterval) {
            pollInterval = setInterval(pollCheckStatus, 1500);
        }

    } catch (err) {
        showToast("error", err.message);
        resetRunButton();
    }
}

/**
 * Met à jour le visualiseur de stages
 */
function updateVisualizer(state, objective) {
    const vis = document.getElementById("stages-visualizer");
    const consoleLogs = document.getElementById("console-logs");
    if (!vis || !state || !state.history) return;

    let allTasks = [];
    const plannerUpdate = state.history.find(h => h.agent_name === "planner");
    if (plannerUpdate && plannerUpdate.new_tasks) {
        allTasks = [...plannerUpdate.new_tasks];
    }

    if (allTasks.length === 0) {
        vis.innerHTML = `
            <div class="stage-card" style="border-color:rgba(var(--accent-primary-rgb),0.4);">
                <div class="stage-info">
                    <span class="stage-badge">Phase 1</span>
                    <div>
                        <div class="stage-objective">${objective}</div>
                        <div class="stage-meta"><span>Initialisation du flux multi-agent...</span></div>
                    </div>
                </div>
                <div class="stage-status" style="color:var(--warning);">
                    <span class="status-dot running"></span>En cours
                </div>
            </div>`;
        document.getElementById("stage-count-badge").innerText = "1 étape en attente";
        return;
    }

    // Grouper par stage_id
    const stages = {};
    allTasks.forEach(task => {
        const sId = task.metadata.stage_id || 1;
        if (!stages[sId]) stages[sId] = [];
        stages[sId].push(task);
    });

    let html = "";
    const stageIds = Object.keys(stages).sort((a, b) => a - b);

    stageIds.forEach(sId => {
        const tasks = stages[sId];
        const healing = engineState.healingStages[sId];
        let healingBadgeHtml = "";
        if (healing) {
            healingBadgeHtml = `
                <span style="font-size:0.72rem;background:rgba(var(--warning-rgb),0.15);border:1px solid rgba(var(--warning-rgb),0.3);color:var(--warning);padding:0.2rem 0.5rem;border-radius:4px;font-weight:bold;display:inline-flex;align-items:center;gap:0.25rem;animation:pulse-glow 1.5s infinite;">
                    🔄 Auto-correction (${healing.retry_count}/3)
                </span>
            `;
        }

        tasks.forEach((task, idx) => {
            const taskUpdate = state.history.find(h =>
                h.agent_name !== "planner" &&
                h.metadata &&
                h.metadata.task_objective === task.task_objective
            );

            let statusLabel = "En attente";
            let statusColor = "var(--text-muted)";
            let statusDotClass = "idle";

            if (taskUpdate) {
                if (taskUpdate.status === "success") {
                    statusLabel = "Terminé"; statusColor = "var(--success)"; statusDotClass = "success";
                } else if (taskUpdate.status === "error") {
                    statusLabel = "Erreur"; statusColor = "var(--error)"; statusDotClass = "error";
                }
            } else if (engineState.runningTasks.has(task.task_objective)) {
                statusLabel = "En cours"; statusColor = "var(--warning)"; statusDotClass = "running";
            }

            html += `
                <div class="stage-card" ${statusDotClass === 'running' ? 'style="border-color:rgba(var(--accent-primary-rgb),0.4);"' : ''}>
                    <div class="stage-info">
                        <span class="stage-badge">Phase ${sId}.${idx + 1}</span>
                        <div>
                            <div class="stage-objective" style="display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap;">
                                <span>${task.task_objective}</span>
                                ${idx === 0 ? healingBadgeHtml : ''}
                            </div>
                            <div class="stage-meta">
                                <span>Agent : <strong style="color:var(--accent-primary);">${task.metadata.target_agent}</strong></span>
                                <span>Tier : <strong>${task.metadata.model_tier}</strong></span>
                            </div>
                        </div>
                    </div>
                    <div class="stage-status" style="color:${statusColor};">
                        <span class="status-dot ${statusDotClass}"></span>${statusLabel}
                    </div>
                </div>`;
        });
    });

    vis.innerHTML = html;
    document.getElementById("stage-count-badge").innerText = `${allTasks.length} étape(s) planifiée(s)`;

    // Logs enrichis avec timestamps, durée, modèle et tokens
    if (consoleLogs) {
        let logsHtml = "";
        state.history.forEach((upd, idx) => {
            let logClass = "log-entry";
            if (upd.agent_name === "planner") logClass += " planner";
            else if (upd.agent_name === "executor") logClass += " executor";
            else if (upd.agent_name === "antigravity_agent") logClass += " antigravity";
            else if (upd.agent_name === "reviewer") logClass += " reviewer";
            else if (upd.agent_name === "ha_agent") logClass += " ha-agent";
            if (upd.status === "error") logClass += " error";

            // Métadonnées enrichies
            const meta = upd.metadata || {};
            const model = meta.model_used || meta.model_tier || '';
            const tokens = meta.tokens_used || '';
            const taskId = meta.task_id || '';
            const duration = meta.duration_ms ? `${(meta.duration_ms / 1000).toFixed(1)}s` : '';
            const timestamp = meta.timestamp 
                ? new Date(meta.timestamp).toLocaleTimeString('fr-FR', {hour:'2-digit', minute:'2-digit', second:'2-digit'}) 
                : '';

            // Badges de métadonnées
            let metaBadges = '';
            if (timestamp) metaBadges += `<span style="color:var(--text-dim);font-size:0.68rem;">${timestamp}</span>`;
            if (duration) metaBadges += `<span style="color:var(--accent-secondary);font-size:0.68rem;">⏱ ${duration}</span>`;
            if (model) metaBadges += `<span class="model-badge" style="font-size:0.62rem;">${model}</span>`;
            if (tokens) metaBadges += `<span style="color:var(--text-muted);font-size:0.68rem;">${formatGaugeValue(tokens, '')} tok</span>`;

            logsHtml += `
                <div class="${logClass}">
                    <div class="log-header">
                        <span>[${idx + 1}] ${upd.agent_name.toUpperCase()} (${upd.status.toUpperCase()})${taskId ? ' — ' + taskId : ''}</span>
                        <span class="log-toggle" onclick="toggleLogBody(${idx})">Détails</span>
                    </div>
                    ${metaBadges ? `<div style="display:flex;gap:var(--space-sm);flex-wrap:wrap;margin:0.2rem 0;">${metaBadges}</div>` : ''}
                    <div id="log-body-${idx}" class="log-body" style="display:none;">${upd.status === "error" ? upd.error_message || "" : upd.result_data || ""}</div>
                </div>`;
        });
        consoleLogs.innerHTML = logsHtml || '<div class="empty-state"><div class="empty-state__icon">📟</div><div>Aucune trace.</div></div>';
    }

    // Mise à jour de la progression DAG
    updateDAGProgress(state, allTasks);
}

function toggleLogBody(idx) {
    const el = document.getElementById(`log-body-${idx}`);
    if (el) el.style.display = el.style.display === "none" ? "block" : "none";
}

/* ============================================================
   Nouvelles fonctions — Stop, Progression, CoT, Export
   ============================================================ */

/**
 * B1 — Arrêter l'exécution en cours
 */
async function stopExecution() {
    try {
        const res = await fetch(`${API_BASE}/stop`, { method: 'POST' });
        if (res.ok) {
            showToast("warning", "Arrêt demandé. Le moteur terminera la tâche en cours puis s'arrêtera.");
        } else {
            showToast("error", "Impossible d'arrêter le moteur.");
        }
    } catch (err) {
        showToast("error", `Erreur : ${err.message}`);
    }
}

/**
 * Affiche/masque le bouton Stop selon l'état du moteur
 */
function toggleStopButton(show) {
    const btnStop = document.getElementById('btn-stop');
    const btnRun = document.getElementById('btn-run');
    if (btnStop) btnStop.style.display = show ? 'inline-flex' : 'none';
    if (btnRun && show) {
        btnRun.disabled = true;
        btnRun.innerHTML = `<svg class="spin" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="2" x2="12" y2="6"></line><line x1="12" y1="18" x2="12" y2="22"></line><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"></line><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"></line><line x1="2" y1="12" x2="6" y2="12"></line><line x1="18" y1="12" x2="22" y2="12"></line></svg> Exécution...`;
    }
}

/**
 * C3 — Met à jour la barre de progression globale du DAG
 */
function updateDAGProgress(state, allTasks) {
    const panel = document.getElementById('dag-progress-panel');
    const fill = document.getElementById('dag-progress-fill');
    const text = document.getElementById('dag-progress-text');

    if (!panel || !allTasks || allTasks.length === 0) {
        if (panel) panel.style.display = 'none';
        return;
    }

    panel.style.display = 'block';
    
    // Compter les tâches terminées
    let completed = 0;
    let errored = 0;
    if (state && state.history) {
        allTasks.forEach(task => {
            const match = state.history.find(h =>
                h.agent_name !== "planner" &&
                h.metadata &&
                h.metadata.task_objective === task.task_objective
            );
            if (match) {
                if (match.status === "success") completed++;
                else if (match.status === "error") errored++;
            }
        });
    }

    const total = allTasks.length;
    const pct = total > 0 ? ((completed + errored) / total * 100) : 0;

    if (fill) {
        fill.style.width = pct + '%';
        // Couleur selon l'état
        fill.className = 'progress-bar__fill';
        if (errored > 0) fill.classList.add('warning');
        if (pct >= 100 && errored === 0) fill.classList.add(''); // vert par défaut
    }
    if (text) text.textContent = `${completed} / ${total} tâches (${errored > 0 ? errored + ' erreur(s)' : 'en cours'})`;
}

/**
 * C4 — Met à jour le compteur de coût live
 */
function updateLiveCost(tokens, cost) {
    const costEl = document.getElementById('dag-live-cost');
    const tokEl = document.getElementById('dag-live-tokens');
    if (costEl && cost !== undefined) costEl.textContent = `${cost.toFixed(4)} $`;
    if (tokEl && tokens !== undefined) tokEl.textContent = `${formatGaugeValue(tokens, '')} tokens`;
}

/**
 * A1 — Toggle du panneau Chain of Thought
 */
function toggleCoTPanel() {
    const body = document.getElementById('cot-body');
    const arrow = document.querySelector('#cot-panel .arrow');
    if (body) {
        body.classList.toggle('collapsed');
        if (arrow) arrow.style.transform = body.classList.contains('collapsed') ? 'rotate(-90deg)' : '';
    }
}

/**
 * Axe 2 — Typewriter CoT : buffer de caractères + rendu progressif
 * Inspiré de Open WebUI — affichage caractère par caractère à 60fps.
 */

// Buffer global pour le typewriter
let _cotBuffer = '';
let _cotFlushRAF = null;
let _cotActiveAgent = null;
let _cotActiveSpan = null;
let _cotCursor = null;

function appendCoTText(text, agentName) {
    const panel = document.getElementById('cot-panel');
    const body = document.getElementById('cot-body');
    const status = document.getElementById('cot-status');

    if (!panel || !body) return;

    panel.style.display = 'block';
    if (status) status.textContent = `${agentName || 'Agent'} en réflexion...`;

    // Supprimer l'empty state si présent
    const empty = body.querySelector('.empty-state');
    if (empty) empty.remove();

    // Si l'agent change, créer une nouvelle ligne
    if (agentName !== _cotActiveAgent || !_cotActiveSpan) {
        _cotActiveAgent = agentName;

        const line = document.createElement('div');
        line.className = 'cot-line';

        const badge = document.createElement('span');
        badge.className = 'cot-agent';
        badge.textContent = `[${agentName || '?'}]`;
        line.appendChild(badge);

        const textSpan = document.createElement('span');
        textSpan.className = 'cot-text-stream';
        line.appendChild(textSpan);

        body.appendChild(line);
        _cotActiveSpan = textSpan;
    }

    // Ajouter le texte au buffer
    _cotBuffer += text;

    // Démarrer le flush si pas déjà en cours
    if (!_cotFlushRAF) {
        _ensureCotCursor();
        _cotFlushRAF = requestAnimationFrame(_flushCoTBuffer);
    }
}

/**
 * Flush le buffer caractère par caractère à ~480 chars/sec (8 chars × 60fps)
 */
function _flushCoTBuffer() {
    const CHARS_PER_FRAME = 8;
    const batch = _cotBuffer.slice(0, CHARS_PER_FRAME);
    _cotBuffer = _cotBuffer.slice(CHARS_PER_FRAME);

    if (_cotActiveSpan && batch) {
        _cotActiveSpan.textContent += batch;
    }

    // Auto-scroll
    const body = document.getElementById('cot-body');
    if (body) body.scrollTop = body.scrollHeight;

    // Repositionner le curseur après le texte
    _repositionCotCursor();

    if (_cotBuffer.length > 0) {
        _cotFlushRAF = requestAnimationFrame(_flushCoTBuffer);
    } else {
        _cotFlushRAF = null;
        // Garder le curseur visible quelques secondes puis le retirer
        setTimeout(() => {
            if (!_cotFlushRAF && _cotCursor) {
                _cotCursor.style.opacity = '0';
            }
        }, 2000);
    }
}

/**
 * Crée ou réaffiche le curseur clignotant █
 */
function _ensureCotCursor() {
    if (!_cotCursor) {
        _cotCursor = document.createElement('span');
        _cotCursor.className = 'cot-cursor';
        _cotCursor.textContent = '█';
    }
    _cotCursor.style.opacity = '1';
}

/**
 * Positionne le curseur juste après le dernier caractère écrit
 */
function _repositionCotCursor() {
    if (!_cotActiveSpan || !_cotCursor) return;
    // Insérer le curseur après le span de texte actif
    if (_cotCursor.parentNode !== _cotActiveSpan.parentNode) {
        _cotActiveSpan.parentNode.appendChild(_cotCursor);
    }
}

/**
 * Efface les logs de la console
 */
function clearConsoleLogs() {
    const logs = document.getElementById('console-logs');
    if (logs) {
        logs.innerHTML = '<div class="empty-state"><div class="empty-state__icon">📟</div><div>Console effacée.</div></div>';
    }
}

/**
 * Exporte les logs en fichier texte
 */
function exportLogs() {
    const logs = document.getElementById('console-logs');
    if (!logs) return;
    const text = logs.innerText;
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `moteur_logs_${new Date().toISOString().slice(0,19).replace(/:/g,'-')}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    showToast("success", "Logs exportés !");
}

/* ============================================================
   Phase 3 — Approval Gate & Résultats Partiels
   ============================================================ */

/**
 * B2 — Crée et affiche l'overlay d'Approval Gate
 */
/**
 * B2 — Crée et affiche l'overlay d'Approval Gate (HITL)
 */
function showApprovalGate(data) {
    // Supprimer un gate existant s'il y en a un
    const existing = document.getElementById('approval-overlay');
    if (existing) existing.remove();

    const requestId = data.request_id || '';
    const description = data.description || 'Action en attente de validation';
    const planSummary = data.plan_summary || '';
    const riskLevel = data.risk_level || 'medium';
    
    // Déterminer la couleur selon le risque
    const riskColor = riskLevel === 'critical' ? 'var(--error)' : riskLevel === 'high' ? 'var(--warning)' : 'var(--success)';

    const overlay = document.createElement('div');
    overlay.id = 'approval-overlay';
    overlay.className = 'modal-overlay visible';
    overlay.innerHTML = `
        <div class="glass-panel modal-content" style="max-width:600px;text-align:left;">
            <div class="section-title" style="margin-bottom:0; display:flex; justify-content:space-between; align-items:center;">
                <span>⚠️ Validation HITL Requise</span>
                <span class="status-badge" style="background:rgba(255,255,255,0.05); color:${riskColor}; border-color:${riskColor}; font-size:0.7rem;">
                    RISQUE : ${riskLevel.toUpperCase()}
                </span>
            </div>
            
            <div class="callout callout--warning" style="margin:var(--space-md) 0;">
                <span class="callout__icon">🛡️</span>
                <div>
                    <div style="font-weight:600;margin-bottom:0.3rem;">Demande d'autorisation</div>
                    <div style="color:var(--text-secondary); font-size:0.85rem; line-height:1.4;">${description}</div>
                </div>
            </div>
            
            ${planSummary ? `
                <div style="font-size:0.75rem;color:var(--text-muted);margin-bottom:4px;font-weight:600;">Résumé du plan d'action :</div>
                <div class="console" style="max-height:180px;font-size:0.78rem;margin-bottom:var(--space-md);overflow-y:auto;background:rgba(0,0,0,0.3);">
                    <pre style="white-space:pre-wrap;margin:0;font-family:var(--font-mono);">${planSummary}</pre>
                </div>
            ` : ''}
            
            <div class="form-group" style="margin-top:var(--space-md);">
                <label class="form-label" for="approval-feedback" style="font-size:0.78rem;color:var(--text-secondary);">Feedback / Consignes de correction (Optionnel)</label>
                <textarea id="approval-feedback" placeholder="Ex: Corrige la variable X dans le script, ou déploie sur le port 8080..." rows="3" style="font-size:0.8rem;"></textarea>
            </div>
            
            <div style="display:flex;gap:var(--space-sm);justify-content:flex-end;margin-top:var(--space-lg);">
                <button class="btn btn--danger" onclick="handleApproval('${requestId}', false)">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                    Rejeter &amp; Corriger
                </button>
                <button class="btn btn--success" onclick="handleApproval('${requestId}', true)">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>
                    Approuver
                </button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
}

/**
 * B2 — Envoie la décision d'approbation au serveur
 */
async function handleApproval(requestId, approved) {
    const overlay = document.getElementById('approval-overlay');
    const feedback = document.getElementById('approval-feedback')?.value.trim() || '';
    
    const endpoint = approved ? '/api/approval/approve' : '/api/approval/reject';
    
    try {
        const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                request_id: requestId,
                feedback: feedback
            })
        });
        if (res.ok) {
            showToast(approved ? "success" : "warning", approved ? "Action approuvée, reprise du flux." : "Action rejetée avec feedback.");
        } else {
            showToast("error", "Erreur lors de l'envoi de la décision.");
        }
    } catch (err) {
        showToast("error", `Erreur réseau : ${err.message}`);
    }
    if (overlay) overlay.remove();
}

/**
 * C1 — Affiche les résultats partiels quand le DAG s'arrête en erreur
 */
function showPartialResults(state, allTasks) {
    if (!state || !state.history || !allTasks || allTasks.length === 0) return;

    const successful = [];
    const failed = [];
    const pending = [];

    allTasks.forEach(task => {
        const match = state.history.find(h =>
            h.agent_name !== "planner" &&
            h.metadata &&
            h.metadata.task_objective === task.task_objective
        );
        if (match) {
            if (match.status === "success") successful.push(task.task_objective);
            else if (match.status === "error") failed.push(task.task_objective);
        } else {
            pending.push(task.task_objective);
        }
    });

    // Afficher un toast de résultat partiel si au moins une tâche a réussi et une a échoué
    if (successful.length > 0 && (failed.length > 0 || pending.length > 0)) {
        showToast("partial",
            `Résultat partiel : ${successful.length} tâche(s) réussie(s), ${failed.length} échouée(s), ${pending.length} en attente.`,
            8000
        );
    }
}

/* ============================================================
   B3 — Vue Sandbox (écritures en attente)
   ============================================================ */

/**
 * Rafraîchit la vue Sandbox en interrogeant l'API
 */
async function refreshSandboxView() {
    try {
        const res = await fetch(`${API_BASE}/sandbox/pending`);
        if (!res.ok) return;
        const data = await res.json();

        const panel = document.getElementById('sandbox-panel');
        const badge = document.getElementById('sandbox-count-badge');
        const list = document.getElementById('sandbox-files-list');

        if (!panel) return;

        const total = (data.pending_writes || 0) + (data.blocked_commands || 0);

        if (total === 0) {
            panel.style.display = 'none';
            return;
        }

        panel.style.display = 'block';
        if (badge) badge.textContent = `${total} opération(s) en attente`;

        if (list && data.files_pending && data.files_pending.length > 0) {
            list.innerHTML = data.files_pending.map((file, idx) => `
                <div style="display:flex;align-items:center;gap:var(--space-sm);padding:var(--space-sm) var(--space-md);background:rgba(245,158,11,0.05);border:1px solid rgba(245,158,11,0.15);border-radius:var(--radius-md);">
                    <span style="color:var(--warning);font-size:0.9rem;">📝</span>
                    <span style="font-family:var(--font-mono);font-size:0.75rem;color:var(--text-secondary);flex:1;">${file}</span>
                    <span style="font-size:0.65rem;color:var(--text-muted);">${data.pending_writes > idx ? 'Écriture' : 'Commande'}</span>
                </div>
            `).join('');
        }
    } catch (err) {
        // Silencieux — le panneau reste masqué
    }
}

/**
 * Approuve toutes les opérations en attente du sandbox
 */
async function approveSandbox() {
    try {
        const res = await fetch(`${API_BASE}/sandbox/approve`, { method: 'POST' });
        if (res.ok) {
            const data = await res.json();
            showToast("success", data.message || "Sandbox approuvé !");
            refreshSandboxView();
        } else {
            showToast("error", "Erreur lors de l'approbation du sandbox.");
        }
    } catch (err) {
        showToast("error", `Erreur : ${err.message}`);
    }
}

/**
 * Rejette et vide toutes les opérations en attente du sandbox
 */
async function rejectSandbox() {
    try {
        const res = await fetch(`${API_BASE}/sandbox/reject`, { method: 'POST' });
        if (res.ok) {
            const data = await res.json();
            showToast("warning", data.message || "Sandbox rejeté.");
            refreshSandboxView();
        } else {
            showToast("error", "Erreur lors du rejet du sandbox.");
        }
    } catch (err) {
        showToast("error", `Erreur : ${err.message}`);
    }
}

/* ============================================================
   D2 — Generative UI (composants dynamiques de l'agent)
   ============================================================ */

/**
 * Rend un composant HTML dynamique envoyé par l'agent
 * @param {Object} data — { type: 'html'|'table'|'chart', content: '...', title: '...', agent_name: '...' }
 */
function renderGenerativeUI(data) {
    const panel = document.getElementById('gen-ui-panel');
    const container = document.getElementById('gen-ui-container');

    if (!panel || !container) return;

    panel.style.display = 'block';

    const wrapper = document.createElement('div');
    wrapper.className = 'gen-ui-component';
    wrapper.style.cssText = 'background:rgba(255,255,255,0.02);border:1px solid var(--border-color);border-radius:var(--radius-md);padding:var(--space-md);animation:cotFadeIn 0.3s ease forwards;';

    // En-tête du composant
    const header = document.createElement('div');
    header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--space-sm);';
    header.innerHTML = `
        <span style="font-weight:600;font-size:0.82rem;color:var(--text-primary);">${data.title || 'Composant'}</span>
        <span style="font-size:0.65rem;color:var(--text-muted);">${data.agent_name || 'agent'} · ${new Date().toLocaleTimeString('fr-FR', {hour:'2-digit',minute:'2-digit'})}</span>
    `;
    wrapper.appendChild(header);

    // Corps du composant
    const body = document.createElement('div');
    body.style.cssText = 'font-size:0.82rem;line-height:1.5;';

    const type = data.type || 'html';

    if (type === 'table' && data.rows) {
        // Table dynamique
        const table = document.createElement('table');
        table.style.cssText = 'width:100%;border-collapse:collapse;font-size:0.78rem;';

        if (data.headers) {
            const thead = document.createElement('thead');
            thead.innerHTML = '<tr>' + data.headers.map(h => `<th style="text-align:left;padding:0.4rem;border-bottom:1px solid var(--border-color);color:var(--accent-primary);font-weight:600;">${h}</th>`).join('') + '</tr>';
            table.appendChild(thead);
        }

        const tbody = document.createElement('tbody');
        (data.rows || []).forEach(row => {
            const tr = document.createElement('tr');
            tr.innerHTML = (Array.isArray(row) ? row : Object.values(row)).map(cell =>
                `<td style="padding:0.35rem;border-bottom:1px solid rgba(255,255,255,0.03);color:var(--text-secondary);">${cell}</td>`
            ).join('');
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        body.appendChild(table);

    } else if (type === 'kv' && data.pairs) {
        // Key-Value pairs
        body.innerHTML = data.pairs.map(([k, v]) =>
            `<div style="display:flex;gap:var(--space-sm);padding:0.25rem 0;border-bottom:1px solid rgba(255,255,255,0.02);">
                <span style="color:var(--accent-primary);font-weight:600;min-width:120px;font-size:0.75rem;">${k}</span>
                <span style="color:var(--text-secondary);font-size:0.75rem;">${v}</span>
            </div>`
        ).join('');

    } else {
        // HTML brut (sanitisé)
        body.innerHTML = data.content || data.html || '';
    }

    wrapper.appendChild(body);
    container.appendChild(wrapper);

    // Auto-scroll
    container.scrollTop = container.scrollHeight;
}

/**
 * Efface tous les composants générés
 */
function clearGenerativeUI() {
    const panel = document.getElementById('gen-ui-panel');
    const container = document.getElementById('gen-ui-container');
    if (container) container.innerHTML = '';
    if (panel) panel.style.display = 'none';
}

/**
 * Affiche les résultats de Visual-QA reçus par SSE
 */
function displayVisualQAResult(data) {
    const panel = document.getElementById('visual-qa-panel');
    const img = document.getElementById('visual-qa-screenshot');
    const noImg = document.getElementById('visual-qa-no-screenshot');
    const score = document.getElementById('visual-qa-score');
    const verdict = document.getElementById('visual-qa-verdict');
    const issuesList = document.getElementById('visual-qa-issues');
    
    if (!panel) return;
    
    if (data && data.visual_qa) {
        panel.style.display = 'block';
        const vqa = data.visual_qa;
        
        if (vqa.screenshot_path) {
            img.src = vqa.screenshot_path + '?t=' + Date.now(); // évite le cache navigateur
            img.style.display = 'block';
            if (noImg) noImg.style.display = 'none';
        } else {
            img.style.display = 'none';
            if (noImg) noImg.style.display = 'block';
        }
        
        if (score) {
            score.textContent = vqa.score !== undefined && vqa.score >= 0 ? vqa.score : 'N/A';
            if (vqa.score >= 8) score.style.color = 'var(--success)';
            else if (vqa.score >= 6) score.style.color = 'var(--accent-primary)';
            else if (vqa.score >= 4) score.style.color = 'var(--warning)';
            else score.style.color = 'var(--error)';
        }
        
        if (verdict) verdict.textContent = vqa.visual_verdict || 'Verdict non disponible.';
        
        if (issuesList) {
            issuesList.innerHTML = '';
            const issues = vqa.issues || [];
            if (issues.length === 0) {
                issuesList.innerHTML = '<li style="color:var(--success);">Aucun problème visuel détecté.</li>';
            } else {
                issues.forEach(issue => {
                    const li = document.createElement('li');
                    li.textContent = issue;
                    issuesList.appendChild(li);
                });
            }
        }
        
        // Auto-scroll pour forcer la visibilité du panneau Visual-QA
        panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
}
