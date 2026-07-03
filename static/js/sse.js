/* ============================================================
   SSE.JS — Server-Sent Events + Polling Fallback
   Gestion de la connexion temps réel avec le moteur
   ============================================================ */

let sseSource = null;
let pollInterval = null;

/* État global du moteur */
let engineState = {
    isRunning: false,
    runningTasks: new Set(),
    healingStages: {},
    activeAgent: null
};

/**
 * Initialise la connexion SSE, avec fallback en polling HTTP
 */
function setupSSE() {
    if (sseSource) {
        sseSource.close();
        sseSource = null;
    }

    console.log("[SSE] Connexion au flux du serveur...");
    const streamUrl = `${API_BASE}/stream`;
    sseSource = new EventSource(streamUrl);

    sseSource.onopen = () => {
        console.log("[SSE] Connecté avec succès.");
        setConnectionBadge(true);
        if (pollInterval) {
            clearInterval(pollInterval);
            pollInterval = null;
        }
    };

    sseSource.onerror = (err) => {
        console.warn("[SSE] Déconnecté. Passage en Polling HTTP...", err);
        setConnectionBadge(false);
        if (sseSource) {
            sseSource.close();
            sseSource = null;
        }
        // Démarrage du polling de secours
        if (!pollInterval) {
            pollInterval = setInterval(pollCheckStatus, 1500);
        }
    };

    // Enregistrement de tous les événements du moteur
    const events = [
        "agent_started", "agent_completed", "stage_started",
        "task_started", "task_completed", "healing_started",
        "healing_completed", "orchestration_completed",
        "tokens_updated", "quotas_updated", "apis_status_updated",
        // Nouveaux événements
        "review_started", "review_completed", "review_correction_started",
        "model_fallback", "thinking_stream", "approval_required",
        // D2 Generative UI + B3 Sandbox
        "ui_component", "sandbox_flushed"
    ];

    events.forEach(eventName => {
        sseSource.addEventListener(eventName, (event) => {
            try {
                const payload = JSON.parse(event.data);
                handleSSEEvent(eventName, payload);
            } catch (e) {
                console.error(`[SSE] Erreur parsing ${eventName}:`, e);
            }
        });
    });

    // Messages génériques
    sseSource.onmessage = (event) => {
        try {
            const payload = JSON.parse(event.data);
            if (payload && payload.event) {
                handleSSEEvent(payload.event, payload);
            }
        } catch (e) {
            // Ignorer les heartbeats
        }
    };
}

/**
 * Traite un événement SSE et met à jour l'état global
 */
function handleSSEEvent(eventType, payload) {
    const state = payload.engine_state;
    const status = payload.status;
    const eventData = payload.data;

    // Mise à jour du statut moteur
    if (status === "running") {
        engineState.isRunning = true;
        setEngineStatusUI("running", "Orchestration en cours...");
        // Afficher le bouton Stop
        if (typeof toggleStopButton === 'function') toggleStopButton(true);
    } else if (status === "success") {
        if (engineState.isRunning) {
            engineState.isRunning = false;
            showToast("success", "Tâche accomplie avec succès !");
        }
        setEngineStatusUI("success", "Exécution terminée avec succès");
        resetRunButton();
        if (typeof toggleStopButton === 'function') toggleStopButton(false);
    } else if (status === "error") {
        if (engineState.isRunning) {
            engineState.isRunning = false;
            const errMsg = payload.error_message || (eventData && eventData.error_message) || "Erreur inconnue";
            showToast("error", `Erreur d'exécution: ${errMsg}`);
        }
        setEngineStatusUI("error", `Erreur : ${payload.error_message || "inconnue"}`);
        resetRunButton();
        if (typeof toggleStopButton === 'function') toggleStopButton(false);
    } else {
        setEngineStatusUI("idle", "Moteur inactif");
        resetRunButton();
        if (typeof toggleStopButton === 'function') toggleStopButton(false);
    }

    // Gestion de l'état local par type d'événement
    if (eventType === "task_started" && eventData) {
        engineState.runningTasks.add(eventData.task_objective);
    } else if (eventType === "task_completed" && eventData) {
        engineState.runningTasks.delete(eventData.task_objective);
    }

    // A2 — Surbrillance du nœud actif dans le pipeline SVG
    // D1 — Mise à jour live du status des Agent Cards
    // Axe 3 — Timeline d'exécution Gantt
    if (eventType === "agent_started" && eventData) {
        engineState.activeAgent = eventData.agent_name;
        if (typeof highlightPipelineNode === 'function') highlightPipelineNode(eventData.agent_name || '');
        if (typeof updateAgentCardStatus === 'function') {
            updateAgentCardStatus(eventData.agent_name, 'running', eventData.task_objective || 'Démarrage');
        }
        if (typeof timelineOnAgentStarted === 'function') {
            timelineOnAgentStarted(eventData.agent_name, eventData.task_objective);
        }
    } else if (eventType === "agent_completed" && eventData) {
        engineState.activeAgent = null;
        if (typeof updateAgentCardStatus === 'function') {
            const s = eventData.status === 'error' ? 'error' : 'success';
            updateAgentCardStatus(eventData.agent_name, s, eventData.task_objective || 'Terminé');
        }
        if (typeof timelineOnAgentCompleted === 'function') {
            timelineOnAgentCompleted(eventData.agent_name, eventData.status === 'error' ? 'error' : 'success');
        }
    } else if (eventType === "orchestration_completed") {
        engineState.activeAgent = null;
        if (typeof highlightPipelineNode === 'function') highlightPipelineNode('result');
        // Remettre tous les agents en idle
        ['planner', 'executor', 'antigravity_agent', 'ha_agent', 'reviewer'].forEach(a => {
            if (typeof updateAgentCardStatus === 'function') updateAgentCardStatus(a, 'idle');
        });
    } else if (eventType === "orchestration_started") {
        engineState.activeAgent = null;
        // Reset de la timeline pour une nouvelle exécution
        if (typeof resetTimeline === 'function') resetTimeline();
    }

    if (eventType === "healing_started" && eventData) {
        engineState.healingStages[eventData.stage_id] = {
            retry_count: eventData.retry_count,
            error_details: eventData.error_details,
            error: false
        };
    } else if (eventType === "healing_completed" && eventData) {
        if (eventData.status === "success") {
            delete engineState.healingStages[eventData.stage_id];
        } else if (engineState.healingStages[eventData.stage_id]) {
            engineState.healingStages[eventData.stage_id].error = true;
        }
    } else if (eventType === "orchestration_completed") {
        engineState.runningTasks.clear();
        engineState.healingStages = {};
        // [C1] Résultats partiels si l'exécution a échoué
        if (status === "error" && state && typeof showPartialResults === 'function') {
            const plannerUpdate = state.history ? state.history.find(h => h.agent_name === "planner") : null;
            if (plannerUpdate && plannerUpdate.new_tasks) {
                showPartialResults(state, plannerUpdate.new_tasks);
            }
        }
    } else if (eventType === "tokens_updated" && eventData) {
        const tokens = eventData;
        if (tokens && tokens.total) {
            const kpiTokens = document.getElementById('kpi-tokens');
            const kpiCost = document.getElementById('kpi-cost');
            // Utiliser combined_total si disponible (pas écraser avec moteur seul)
            if (kpiTokens) {
                kpiTokens.textContent = tokens.combined_total
                    ? formatGaugeValue(tokens.combined_total.grand_total, '')
                    : formatGaugeValue(tokens.total.total_tokens, '');
            }
            if (kpiCost) {
                const totalCost = tokens.combined_total?.grand_cost_usd || tokens.total.estimated_cost_usd;
                kpiCost.textContent = `${totalCost.toFixed(4)} $`;
            }
        }
        if (tokens && tokens.real_billing) {
            const rb = tokens.real_billing;
            const dsEl = document.getElementById('kpi-deepseek-balance');
            if (dsEl && rb.deepseek_balance_usd != null) {
                dsEl.textContent = `DeepSeek: ${rb.deepseek_balance_usd.toFixed(2)} $`;
            }
        }
        // Ne PAS re-render l'onglet Tokens complet (perte de scroll/pagination)
        // Les KPIs sont déjà mis à jour ci-dessus. Le tableau se rafraîchira au prochain switchTab.
    } else if (eventType === "quotas_updated" && eventData) {
        const quotas = eventData;
        if (typeof updateDashboardGauges === 'function') updateDashboardGauges(quotas);
        if (typeof updateQuotaGauges === 'function') updateQuotaGauges(quotas);
        if (typeof updatePricingQuotaGauges === 'function') updatePricingQuotaGauges(quotas);
    } else if (eventType === "apis_status_updated" && eventData) {
        // Update PARTIEL : ne pas re-render toute la page (cause les sauts)
        if (typeof activeTab !== 'undefined' && activeTab === 'apis') {
            if (typeof renderApiStatusCards === 'function') renderApiStatusCards(eventData);
            if (typeof updateBudgetGauges === 'function') updateBudgetGauges(eventData);
        }
    }
    // Nouveaux événements
    else if (eventType === "review_started" && eventData) {
        showToast("info", "Revue automatique post-DAG en cours...");
        if (typeof appendCoTText === 'function') {
            appendCoTText("Démarrage de la revue automatique post-DAG...", "reviewer");
        }
    } else if (eventType === "review_completed" && eventData) {
        if (eventData.approved) {
            showToast("success", "Revue approuvée par le Reviewer.");
        } else {
            showToast("warning", "Revue rejetée — corrections en cours...");
        }
        if (typeof displayVisualQAResult === 'function') {
            displayVisualQAResult(eventData);
        }
    } else if (eventType === "review_correction_started" && eventData) {
        showToast("info", `Correction post-review (round ${eventData.round}) — ${eventData.corrections_count} correction(s)`);
    } else if (eventType === "model_fallback" && eventData) {
        // [B4] Toast de fallback modèle
        showToast("info", `Modèle basculé : ${eventData.from || '?'} \u2192 ${eventData.to || '?'}. Raison : ${eventData.reason || 'quota/erreur'}.`);
    } else if (eventType === "thinking_stream" && eventData) {
        // [A1] Streaming Chain of Thought
        if (typeof appendCoTText === 'function') {
            appendCoTText(eventData.text || '', eventData.agent_name || 'agent');
        }
    } else if (eventType === "approval_required" && eventData) {
        // [B2] Approval Gate — affiche la modal de validation
        if (typeof showApprovalGate === 'function') {
            showApprovalGate(eventData);
        } else {
            showToast("warning", `Validation requise : ${eventData.message || 'Action en attente.'}`);
        }
    } else if (eventType === "ui_component" && eventData) {
        // [D2] Generative UI — rendu d'un composant HTML dynamique
        if (typeof renderGenerativeUI === 'function') {
            renderGenerativeUI(eventData);
        }
    } else if (eventType === "sandbox_flushed") {
        // [B3] Rafraîchir la vue Sandbox
        if (typeof refreshSandboxView === 'function') refreshSandboxView();
    }

    // [C4] Mise à jour du compteur de coût live
    if (eventType === "tokens_updated" && eventData && eventData.total) {
        if (typeof updateLiveCost === 'function') {
            updateLiveCost(eventData.total.total_tokens || 0, eventData.total.estimated_cost_usd || 0);
        }
    }

    // Mise à jour du visualiseur si la page exécution est active
    if (typeof updateVisualizer === "function") {
        const objectiveEl = document.getElementById("task-objective");
        updateVisualizer(state, objectiveEl ? objectiveEl.value.trim() : "Objectif");
    }

    // Mise à jour du live status du Dashboard s'il est affiché
    if (typeof updateDashboardLiveStatus === 'function') {
        updateDashboardLiveStatus(state, status, engineState.activeAgent);
    }
}

/**
 * Polling de secours quand SSE est déconnecté
 */
async function pollCheckStatus() {
    try {
        const data = await fetchStatus();
        if (data.status === "running") {
            engineState.isRunning = true;
            setEngineStatusUI("running", "Orchestration en cours...");
        } else if (data.status === "success") {
            if (engineState.isRunning) {
                engineState.isRunning = false;
                showToast("success", "Tâche accomplie avec succès !");
                if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
            }
            setEngineStatusUI("success", "Exécution terminée avec succès");
            resetRunButton();
        } else if (data.status === "error") {
            if (engineState.isRunning) {
                engineState.isRunning = false;
                showToast("error", `Erreur: ${data.error_message}`);
                if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
            }
            setEngineStatusUI("error", `Erreur : ${data.error_message || "inconnue"}`);
            resetRunButton();
        } else {
            setEngineStatusUI("idle", "Moteur inactif");
            resetRunButton();
        }
        if (typeof updateVisualizer === "function") {
            updateVisualizer(data.engine_state, data.objective);
        }
    } catch (err) {
        console.error("[Polling] Erreur:", err);
    }
}

/**
 * Met à jour le badge de connexion SSE/Polling dans le header
 */
function setConnectionBadge(isSSE) {
    const badge = document.getElementById("connection-badge");
    if (!badge) return;
    const dot = badge.querySelector(".status-dot");
    const label = badge.querySelector("[data-label]");
    if (isSSE) {
        badge.style.borderColor = "rgba(16, 185, 129, 0.2)";
        badge.style.color = "var(--success)";
        if (dot) dot.className = "status-dot success";
        if (label) label.innerText = "SSE Connecté";
    } else {
        badge.style.borderColor = "rgba(107, 114, 128, 0.2)";
        badge.style.color = "var(--text-muted)";
        if (dot) dot.className = "status-dot idle";
        if (label) label.innerText = "Mode Polling";
    }
}

/**
 * Met à jour l'indicateur de statut du moteur dans le header
 */
function setEngineStatusUI(status, text) {
    const dot = document.getElementById("engine-status-dot");
    if (dot) dot.className = `status-dot ${status}`;
    const txt = document.getElementById("engine-status-text");
    if (txt) txt.innerText = text;

    const dashDot = document.getElementById("dash-engine-dot");
    if (dashDot) dashDot.className = `status-dot ${status}`;
    const dashTxt = document.getElementById("dash-engine-text");
    if (dashTxt) dashTxt.innerText = text;
}

/**
 * Reset le bouton de lancement d'exécution
 */
function resetRunButton() {
    const btn = document.getElementById("btn-run");
    if (!btn) return;
    btn.disabled = false;
    btn.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg> Exécuter l'Orchestrateur`;
}
