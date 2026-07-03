/* ============================================================
   AGENTS.JS — Page 3 : Gestion des Agents + Éditeur de Workflow
   V3 : Workflow par défaut réel, création d'agents, réglages/contexte
   ============================================================ */

/* ─── Données réelles des agents (extraits des fichiers Python) ─── */
const AGENTS_REGISTRY = {
    planner: {
        icon: "🧠", role: "Orchestrateur / Planner",
        desc: "Découpe l'objectif en sous-tâches (stages) et les distribue aux agents exécuteurs via un DAG parallélisé.",
        file: "agents/planner.py", cls: "PlannerAgent",
        defaultTier: "fort",
        tools: ["(aucun outil direct — génère un plan JSON structuré)"],
        systemPrompt: `Tu es le PlannerAgent, l'architecte du moteur multi-agents.
Ton rôle est de décomposer la demande de l'utilisateur en un plan d'action structuré en lots (stages) pouvant s'exécuter en parallèle ou séquentiellement.
Pour chaque tâche, tu définis un 'stage_id' (entier). Les tâches ayant le même stage_id s'exécutent EN PARALLÈLE.
Les tâches dépendantes doivent être dans des stages successifs.
Méthodologie : Understand → Search/Doc → Plan → Verify (inclure systématiquement une tâche de vérification).
Directives : commentaires en français, privilégier outils MCP, commandes Windows uniquement.`,
        healingPrompt: `Mode Auto-Correction (Self-Healing) : analyse l'erreur fournie et génère des tâches correctives pour résoudre le problème.`
    },
    executor: {
        icon: "🔧", role: "Exécuteur / Outils",
        desc: "Exécute les sous-tâches via la boucle ReAct multi-turn (max 10 tours) en utilisant les outils enregistrés.",
        file: "agents/executor.py", cls: "ExecutorAgent",
        defaultTier: "leger",
        tools: [
            "read_file — Lecture de fichiers locaux",
            "write_file — Création/modification de fichiers",
            "run_terminal_command — Commandes système Windows",
            "call_api — Requêtes HTTP (GET/POST)",
            "validate_config_yaml — Validation ESPHome",
            "git_create_checkpoint — Checkpoint Git",
            "git_rollback_checkpoint — Rollback Git",
            "git_apply_checkpoint — Apply checkpoint",
            "+ Outils MCP dynamiques (mcp_ha_custom_*, mcp_sqlite_ha_*)"
        ],
        systemPrompt: `Tu es l'ExecutorAgent. Ton but est d'accomplir la tâche technique demandée en utilisant tes outils.
Boucle ReAct : jusqu'à 10 tours d'interaction LLM ↔ Outil.
CRITIQUE : Si des résultats sont dans 'RÉSULTATS PHASES PRÉCÉDENTES', ne pas relire les fichiers.
Consignes : Privilégier outils MCP, commandes Windows, sécurité système, pédagogie en français.`
    },
    antigravity_agent: {
        icon: "🚀", role: "Expert / Antigravity",
        desc: "Agent expert pour les tâches de raisonnement avancé. Charge automatiquement les directives LVGL Premium si nécessaire.",
        file: "agents/antigravity_agent.py", cls: "AntigravityAgent",
        defaultTier: "fort",
        tools: ["(délégation via CLI Antigravity ou fallback Gemini — pas d'outils directs)"],
        systemPrompt: `Tu es l'AntigravityAgent. Ton rôle est de résoudre les tâches complexes en tant qu'expert.
Injection automatique des directives LVGL Premium pour les tâches UI/Design.
Fallback vers Gemini si la CLI Antigravity n'est pas disponible.`
    },
    ha_agent: {
        icon: "🏠", role: "Home Assistant",
        desc: "Agent spécialisé domotique. Hérite de la boucle ReAct d'ExecutorAgent avec des instructions dédiées HA & SQLite Recorder.",
        file: "agents/ha_agent.py", cls: "HACommandAgent (extends ExecutorAgent)",
        defaultTier: "moyen",
        tools: [
            "mcp_ha_custom_call_service — Piloter les appareils HA",
            "mcp_ha_custom_get_states — Lire les états HA",
            "mcp_ha_custom_get_services — Lister les services HA",
            "mcp_sqlite_ha_query — Requêtes SQL (Recorder)",
            "mcp_sqlite_ha_read_records — Lecture de tables",
            "mcp_sqlite_ha_list_tables — Lister les tables"
        ],
        systemPrompt: `Tu es l'HACommandAgent, expert en domotique et base de données Home Assistant.
Directives : Utiliser les outils MCP HA & SQLite en priorité.
SQL : requêtes précises, non destructives, LIMIT 50/100 obligatoire.
Pédagogie en français sur les opérations effectuées.`
    }
};

function renderAgents(container) {
    container.innerHTML = `
        <!-- Partie A : Liste des agents avec réglages -->
        <div class="glass-panel">
            <div class="section-title">
                <span>Agents Enregistrés</span>
                <div style="display:flex;gap:var(--space-sm);">
                    <button class="btn btn--success btn--sm" onclick="openCreateAgentModal()">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                        Nouvel Agent
                    </button>
                </div>
            </div>
            <div class="grid grid-2" id="agents-cards-container">
                <div class="empty-state"><div class="empty-state__icon">🤖</div><div>Chargement...</div></div>
            </div>
        </div>

        <!-- Modal création d'agent -->
        <div id="create-agent-modal" class="modal-overlay">
            <div class="glass-panel modal-content" style="max-width:560px;">
                <h3 style="font-family:var(--font-display);font-weight:700;font-size:1.15rem;margin-bottom:var(--space-lg);display:flex;align-items:center;gap:var(--space-sm);">
                    <span style="font-size:1.5rem;">🤖</span> Créer un Nouvel Agent
                </h3>
                <div style="display:flex;flex-direction:column;gap:var(--space-lg);">
                    <div class="form-group">
                        <label class="form-label">Nom technique (sans espaces)</label>
                        <input type="text" id="new-agent-name" placeholder="ex: code_reviewer">
                    </div>
                    <div class="form-group">
                        <label class="form-label">Rôle / Titre</label>
                        <input type="text" id="new-agent-role" placeholder="ex: Réviseur de Code">
                    </div>
                    <div class="form-group">
                        <label class="form-label">Icône (emoji)</label>
                        <input type="text" id="new-agent-icon" value="🤖" style="width:60px;text-align:center;font-size:1.5rem;">
                    </div>
                    <div class="form-group">
                        <label class="form-label">Description</label>
                        <textarea id="new-agent-desc" rows="2" placeholder="Description du rôle de l'agent..."></textarea>
                    </div>
                    <div class="form-group">
                        <label class="form-label">System Prompt (contexte/instructions)</label>
                        <textarea id="new-agent-prompt" rows="6" class="json-editor" placeholder="Tu es un agent spécialisé en..."></textarea>
                    </div>
                    <div class="form-group">
                        <label class="form-label">Tier par défaut</label>
                        <div class="select-wrap">
                            <select id="new-agent-tier">
                                <option value="leger">Léger</option>
                                <option value="moyen">Moyen</option>
                                <option value="fort" selected>Fort</option>
                                <option value="automatique">Automatique</option>
                            </select>
                        </div>
                    </div>
                    <div class="form-group">
                        <label class="form-label">Hérite de</label>
                        <div class="select-wrap">
                            <select id="new-agent-parent">
                                <option value="executor">Executor (ReAct + Outils)</option>
                                <option value="base">Base (Texte seul)</option>
                            </select>
                        </div>
                    </div>
                </div>
                <div style="display:flex;gap:var(--space-sm);margin-top:var(--space-xl);justify-content:flex-end;">
                    <button class="btn btn--ghost" onclick="closeCreateAgentModal()">Annuler</button>
                    <button class="btn btn--success" onclick="createNewAgent()">Créer l'Agent</button>
                </div>
            </div>
        </div>

        <!-- Partie B : Éditeur de Workflow Drag & Drop -->
        <div class="glass-panel glass-panel--flush" style="overflow:visible;">
            <div style="padding:var(--space-xl);border-bottom:1px solid var(--border-color);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:var(--space-md);">
                <div>
                    <div class="section-title" style="margin-bottom:0.25rem;">
                        <span>🔀 Éditeur de Workflow</span>
                    </div>
                    <div style="font-size:0.78rem;color:var(--text-muted);">
                        Glissez les agents depuis la palette vers le canvas. Connectez les ports pour définir le flux. <kbd>Del</kbd> = supprimer.
                    </div>
                </div>
                <div style="display:flex;gap:var(--space-sm);flex-wrap:wrap;align-items:center;">
                    <div style="display:flex;align-items:center;gap:0.35rem;margin-right:var(--space-md);">
                        <label class="form-label" style="white-space:nowrap;margin:0;font-weight:600;">Workflow actif :</label>
                        <div class="select-wrap" style="width:160px;margin-bottom:0;">
                            <select id="workflow-selector" onchange="onWorkflowSelected()" style="padding:0.4rem 0.55rem;font-size:0.8rem;height:auto;">
                                <option value="Default">Default</option>
                            </select>
                        </div>
                    </div>
                    <button class="btn btn--ghost btn--sm" onclick="createNewWorkflow()" title="Nouveau workflow vierge">📄 Nouveau</button>
                    <button class="btn btn--ghost btn--sm" onclick="saveCurrentWorkflowNamed()" title="Sauvegarder">💾 Sauvegarder</button>
                    <button class="btn btn--ghost btn--sm" onclick="saveCurrentWorkflowAs()" title="Sauvegarder sous...">💾 Sauvegarder sous...</button>
                    <button class="btn btn--danger btn--sm" onclick="deleteActiveWorkflow()" title="Supprimer ce workflow">🗑️ Supprimer</button>
                    <button class="btn btn--success btn--sm" onclick="applyWorkflowToEngine()" style="background:linear-gradient(135deg,rgba(var(--warning-rgb),0.15),rgba(var(--accent-primary-rgb),0.15));border-color:var(--warning);color:var(--warning);" title="Appliquer ce workflow au moteur">⚡ Appliquer</button>
                    <button class="btn btn--ghost btn--sm" onclick="exportWorkflowJSON()" title="Exporter en JSON">📤 Exp</button>
                    <button class="btn btn--ghost btn--sm" onclick="importWorkflowJSON()" title="Importer un JSON">📂 Imp</button>
                </div>
            </div>

            <div class="wf-editor" id="wf-editor-root">
                <!-- Palette d'agents -->
                <div class="wf-palette" id="wf-palette">
                    <div class="wf-palette__header">🧩 Palette</div>

                    <div class="wf-palette__section">Points de flux</div>
                    <div class="wf-palette__item" data-type="start" data-label="Entrée" data-icon="▶️">
                        <div class="wf-palette__icon">▶️</div><span>Entrée (Start)</span>
                    </div>
                    <div class="wf-palette__item" data-type="end" data-label="Sortie" data-icon="🏁">
                        <div class="wf-palette__icon">🏁</div><span>Sortie (End)</span>
                    </div>

                    <div class="wf-palette__section">Agents</div>
                    <div id="palette-agents-list"></div>

                    <div class="wf-palette__section">Logique</div>
                    <div class="wf-palette__item" data-type="condition" data-label="Condition" data-icon="❓">
                        <div class="wf-palette__icon">❓</div><span>Condition (if/else)</span>
                    </div>
                </div>

                <!-- Canvas SVG -->
                <div class="wf-canvas-wrap" id="wf-canvas-area">
                    <div class="wf-props" id="wf-props-panel">
                        <div class="wf-props__header">
                            <span>Propriétés</span>
                            <button class="wf-toolbar__btn" onclick="closeWfProps()" title="Fermer">✕</button>
                        </div>
                        <div class="wf-props__body"></div>
                    </div>
                    <div class="wf-toolbar">
                        <button class="wf-toolbar__btn" onclick="wfZoomIn()" title="Zoom +">🔍</button>
                        <button class="wf-toolbar__btn" onclick="wfZoomOut()" title="Zoom -">🔎</button>
                        <button class="wf-toolbar__btn" onclick="fitToContent()" title="Ajuster à la vue">⊞</button>
                        <button class="wf-toolbar__btn" onclick="resetViewport()" title="Reset zoom">⟲</button>
                        <span style="width:1px;background:var(--border-color);margin:4px 2px;"></span>
                        <button class="wf-toolbar__btn" onclick="wfToolbarAction('auto-layout')" title="Disposition auto">📐</button>
                        <button class="wf-toolbar__btn" onclick="wfToolbarAction('save')" title="Sauvegarder">💾</button>
                        <button class="wf-toolbar__btn" onclick="wfToolbarAction('load')" title="Charger">📥</button>
                        <span style="width:1px;background:var(--border-color);margin:4px 2px;"></span>
                        <button class="wf-toolbar__btn danger" onclick="wfToolbarAction('delete')" title="Supprimer sélection">🗑️</button>
                        <button class="wf-toolbar__btn danger" onclick="wfToolbarAction('clear')" title="Tout effacer">🧹</button>
                    </div>
                </div>
            </div>
        </div>
    `;

    loadAgentsData();

    requestAnimationFrame(() => {
        buildPalette();
        const canvasArea = document.getElementById('wf-canvas-area');
        if (canvasArea) {
            initWfCanvas(canvasArea);
            initWfDragDrop();
            // Charger le workflow sauvegardé ou afficher le workflow par défaut
            loadWorkflowOrDefault();
        }
    });
}

/* ─── Variable globale pour le workflow sélectionné courant ─── */
let activeWorkflowName = "Default";

/* ─── Chargement : workflow sauvegardé ou défaut ─── */
async function loadWorkflowOrDefault() {
    try {
        // 1. Charger le sélecteur d'abord
        await loadWorkflowsDropdown();
        
        // 2. Tenter de charger le workflow actif (Default ou le dernier sélectionné)
        const savedActive = localStorage.getItem("active_workflow_name") || "Default";
        const selector = document.getElementById("workflow-selector");
        if (selector) {
            const options = Array.from(selector.options).map(o => o.value);
            if (options.includes(savedActive)) {
                activeWorkflowName = savedActive;
                selector.value = savedActive;
            } else {
                activeWorkflowName = "Default";
                selector.value = "Default";
            }
        }
        
        const res = await loadWorkflowByName(activeWorkflowName);
        if (res && res.workflow && res.workflow.nodes && res.workflow.nodes.length > 0) {
            deserializeWorkflow(res.workflow);
            showToast('info', `Workflow '${activeWorkflowName}' chargé.`);
        } else {
            seedDefaultWorkflow();
        }
    } catch (err) {
        console.error("Erreur de chargement initial du workflow :", err);
        seedDefaultWorkflow();
    }
}

/* ─── Charger la liste des workflows dans le dropdown HMI ─── */
async function loadWorkflowsDropdown() {
    const selector = document.getElementById("workflow-selector");
    if (!selector) return;
    
    try {
        const res = await fetchWorkflowsList();
        const list = res.workflows || ["Default"];
        
        selector.innerHTML = list.map(name => 
            `<option value="${name}" ${name === activeWorkflowName ? 'selected' : ''}>${name}</option>`
        ).join('');
    } catch (err) {
        console.error("Erreur de récupération de la liste des workflows :", err);
    }
}

/* ─── Sélection d'un workflow ─── */
async function onWorkflowSelected() {
    const selector = document.getElementById("workflow-selector");
    if (!selector) return;
    
    const name = selector.value;
    activeWorkflowName = name;
    localStorage.setItem("active_workflow_name", name);
    
    try {
        const res = await loadWorkflowByName(name);
        if (res && res.workflow) {
            deserializeWorkflow(res.workflow);
            showToast('success', `Workflow '${name}' chargé.`);
        } else {
            clearWfCanvas();
            showToast('warning', `Le workflow '${name}' est vide.`);
        }
    } catch (err) {
        showToast('error', `Erreur de chargement de '${name}' : ${err.message}`);
    }
}

/* ─── Créer un nouveau workflow ─── */
function createNewWorkflow() {
    if (!confirm("Créer un nouveau workflow vierge ? Tout changement non sauvegardé sera perdu.")) return;
    clearWfCanvas();
    addWfNode('start', 'Requête Utilisateur', '▶️', 100, 300);
    addWfNode('end', 'Résultat Final', '🏁', 700, 300);
    showToast('info', "Nouveau canvas vierge créé.");
}

/* ─── Sauvegarder sous son nom actif ─── */
async function saveCurrentWorkflowNamed() {
    try {
        const data = serializeWorkflow();
        await saveWorkflowByName(activeWorkflowName, data);
        showToast('success', `Workflow '${activeWorkflowName}' sauvegardé.`);
    } catch (err) {
        showToast('error', "Erreur de sauvegarde : " + err.message);
    }
}

/* ─── Sauvegarder sous un autre nom (Prompt) ─── */
async function saveCurrentWorkflowAs() {
    const name = prompt("Saisissez le nom du nouveau workflow (lettres, chiffres, - et _ uniquement) :");
    if (name === null) return;
    
    const cleanName = name.trim();
    if (!cleanName) {
        showToast('warning', "Le nom du workflow ne peut pas être vide.");
        return;
    }
    
    if (!/^[a-zA-Z0-9\-_]+$/.test(cleanName)) {
        showToast('error', "Le nom contient des caractères non autorisés (lettres, chiffres, - et _ uniquement).");
        return;
    }
    
    try {
        const data = serializeWorkflow();
        await saveWorkflowByName(cleanName, data);
        activeWorkflowName = cleanName;
        localStorage.setItem("active_workflow_name", cleanName);
        
        await loadWorkflowsDropdown();
        const selector = document.getElementById("workflow-selector");
        if (selector) selector.value = cleanName;
        
        showToast('success', `Workflow '${cleanName}' créé et sauvegardé.`);
    } catch (err) {
        showToast('error', "Erreur de sauvegarde : " + err.message);
    }
}

/* ─── Supprimer le workflow actif ─── */
async function deleteActiveWorkflow() {
    if (activeWorkflowName === "Default") {
        showToast('warning', "Le workflow 'Default' est protégé et ne peut pas être supprimé.");
        return;
    }
    
    if (!confirm(`Êtes-vous sûr de vouloir supprimer définitivement le workflow '${activeWorkflowName}' ?`)) return;
    
    try {
        await deleteWorkflow(activeWorkflowName);
        showToast('info', `Workflow '${activeWorkflowName}' supprimé.`);
        
        activeWorkflowName = "Default";
        localStorage.setItem("active_workflow_name", "Default");
        
        await loadWorkflowsDropdown();
        const selector = document.getElementById("workflow-selector");
        if (selector) selector.value = "Default";
        
        const res = await loadWorkflowByName("Default");
        if (res && res.workflow) {
            deserializeWorkflow(res.workflow);
        } else {
            seedDefaultWorkflow();
        }
    } catch (err) {
        showToast('error', "Erreur de suppression : " + err.message);
    }
}

/**
 * Charge le workflow réel du moteur comme workflow par défaut
 * Architecture réelle : Requête → Router → Planner → fan-out {Executor, Antigravity, HA_Agent}
 *                       → convergence → Succès? → End / Self-Healing (boucle)
 *
 * Ports des losanges : in=HAUT, out-true=DROITE, out-false=GAUCHE
 * On positionne donc les nœuds "faux" à GAUCHE du losange
 */
function seedDefaultWorkflow() {
    clearWfCanvas();

    // ── Colonne 1 : Entrée (x=40) ──
    addWfNode('start', 'Requête Utilisateur', '📝', 40,  550);              // node-1

    // ── Colonne 2 : Router (x=290) ──
    addWfNode('agent', 'Router', '🔀', 290, 550, { agentName: 'router', tier: '—' }); // node-2

    // ── Colonne 3 : Planner (x=540) ──
    addWfNode('agent', 'Planner', '🧠', 540, 550, { agentName: 'planner', tier: 'fort' }); // node-3

    // ── Colonne 4 : Agents en fan-out vertical (x=820) ──
    addWfNode('agent', 'Executor',    '🔧', 820, 200, { agentName: 'executor', tier: 'leger' });          // node-4
    addWfNode('agent', 'Antigravity', '🚀', 820, 550, { agentName: 'antigravity_agent', tier: 'fort' });  // node-5
    addWfNode('agent', 'HA Agent',    '🏠', 820, 900, { agentName: 'ha_agent', tier: 'moyen' });          // node-6

    // ── Colonne 5 : Condition Succès? (x=1120) ──
    // Le losange a : in=haut, out-true=droite(→End), out-false=gauche(→Self-Healing)
    addWfNode('condition', 'Succès ?', '❓', 1120, 520, { conditionExpr: 'result.status === "success"' }); // node-7

    // ── Colonne 6 : End (x=1350) à droite du losange (port true) ──
    addWfNode('end', 'Résultat Final', '🏁', 1350, 520);                    // node-8

    // ── Self-Healing : à GAUCHE du losange (port false) ──
    addWfNode('agent', 'Self-Healing (Re-Plan)', '🔄', 820, 1200, { agentName: 'planner', tier: 'fort' }); // node-9

    // ══════════════ Connexions ══════════════

    // Flux principal (gauche → droite)
    addWfConnection('node-1', 'out', 'node-2', 'in');     // Requête → Router
    addWfConnection('node-2', 'out', 'node-3', 'in');     // Router → Planner

    // Fan-out : Planner → 3 agents
    addWfConnection('node-3', 'out', 'node-4', 'in');     // Planner → Executor
    addWfConnection('node-3', 'out', 'node-5', 'in');     // Planner → Antigravity
    addWfConnection('node-3', 'out', 'node-6', 'in');     // Planner → HA Agent

    // Convergence : 3 agents → condition Succès?  (le port in du losange est en HAUT)
    addWfConnection('node-4', 'out', 'node-7', 'in');     // Executor → Succès?
    addWfConnection('node-5', 'out', 'node-7', 'in');     // Antigravity → Succès?
    addWfConnection('node-6', 'out', 'node-7', 'in');     // HA Agent → Succès?

    // Résultat : Succès? ─✓(droite)→ End
    addWfConnection('node-7', 'out-true', 'node-8', 'in'); // Succès? ✓ → Résultat Final

    // Self-Healing : Succès? ─✗(gauche)→ Self-Healing (en dessous à gauche)
    addWfConnection('node-7', 'out-false', 'node-9', 'in'); // Succès? ✗ → Self-Healing

    // Boucle : Self-Healing → Executor (retour au fan-out)
    addWfConnection('node-9', 'out', 'node-4', 'in');     // Self-Healing → Executor (retry)

    showToast('info', 'Workflow par défaut du moteur chargé (architecture réelle).');
}

/* ─── Palette dynamique des agents ─── */
function buildPalette() {
    const list = document.getElementById('palette-agents-list');
    if (!list) return;

    // Agents du registre + agents custom du localStorage
    const customAgents = JSON.parse(localStorage.getItem('custom_agents') || '[]');
    const allAgents = { ...AGENTS_REGISTRY };
    customAgents.forEach(a => { allAgents[a.name] = a; });

    list.innerHTML = Object.entries(allAgents).map(([name, a]) => `
        <div class="wf-palette__item" data-type="agent" data-label="${a.role || name}" data-icon="${a.icon}" data-agent-name="${name}">
            <div class="wf-palette__icon">${a.icon}</div><span>${a.role || name}</span>
        </div>
    `).join('');

    // Re-init drag drop pour les nouveaux items
    initWfDragDrop();
}

/* ─── Cartes agents avec réglages/contexte ─── */
async function loadAgentsData() {
    const container = document.getElementById('agents-cards-container');
    if (!container) return;

    let config = {};
    try { config = await fetchConfig(); } catch {}

    const tierSelectors = {
        planner: config.planner_model || "fort",
        executor: config.executor_model || "leger",
        antigravity_agent: config.antigravity_model || "fort"
    };

    const customAgents = JSON.parse(localStorage.getItem('custom_agents') || '[]');
    const allAgentEntries = [
        ...Object.entries(AGENTS_REGISTRY),
        ...customAgents.map(a => [a.name, a])
    ];

    container.innerHTML = allAgentEntries.map(([name, agent]) => {
        const currentTier = tierSelectors[name] || agent.defaultTier || "automatique";
        const isCustom = !AGENTS_REGISTRY[name];
        const toolsHtml = (agent.tools || []).map(t =>
            `<div style="font-size:0.7rem;color:var(--text-muted);padding:0.15rem 0;border-bottom:1px solid rgba(255,255,255,0.02);">
                <code style="color:var(--accent-secondary);font-family:var(--font-mono);font-size:0.65rem;">${t.split(' — ')[0]}</code>
                ${t.includes(' — ') ? `<span style="color:var(--text-muted);"> — ${t.split(' — ')[1]}</span>` : ''}
            </div>`
        ).join('');

        return `
            <div class="glass-panel glass-panel--compact agent-card" id="agent-card-${name}" style="display:flex;flex-direction:column;gap:var(--space-md);">
                <!-- Header agent D1 enrichi -->
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div style="display:flex;align-items:center;gap:var(--space-sm);">
                        <span class="agent-avatar" id="agent-avatar-${name}" style="font-size:1.5rem;display:inline-block;">${agent.icon}</span>
                        <div>
                            <div style="font-family:var(--font-display);font-weight:700;font-size:1rem;">${agent.role}</div>
                            <div style="font-size:0.7rem;color:var(--text-muted);display:flex;align-items:center;gap:0.35rem;">
                                <span style="font-family:var(--font-mono);">${name}</span>
                                ${agent.cls ? `<span style="color:var(--accent-secondary);">· ${agent.cls}</span>` : ''}
                                ${isCustom ? '<span style="background:rgba(var(--warning-rgb),0.15);color:var(--warning);padding:0.1rem 0.35rem;border-radius:3px;font-size:0.6rem;font-weight:700;">CUSTOM</span>' : ''}
                            </div>
                        </div>
                    </div>
                    <div style="display:flex;flex-direction:column;align-items:flex-end;gap:0.25rem;">
                        <div class="status-badge" id="agent-status-badge-${name}" style="border-color:rgba(var(--success-rgb),0.2);color:var(--success);">
                            <span class="status-dot success" id="agent-dot-${name}"></span>
                            <span id="agent-status-text-${name}">Prêt</span>
                        </div>
                        <span id="agent-last-action-${name}" style="font-size:0.62rem;color:var(--text-dim);font-family:var(--font-mono);">—</span>
                    </div>
                </div>

                <div style="font-size:0.8rem;color:var(--text-secondary);line-height:1.45;">${agent.desc}</div>

                <!-- Double dropdown : Tier + Modèle -->
                <div style="display:flex;gap:var(--space-md);flex-wrap:wrap;">
                    <div class="form-group" style="margin-bottom:0;flex:1;min-width:130px;">
                        <label class="form-label">Tier de complexité</label>
                        <div class="select-wrap">
                            <select id="agent-tier-${name}" onchange="onAgentTierChange('${name}')">
                                <option value="leger" ${currentTier === 'leger' ? 'selected' : ''}>Léger</option>
                                <option value="moyen" ${currentTier === 'moyen' ? 'selected' : ''}>Moyen</option>
                                <option value="fort" ${currentTier === 'fort' ? 'selected' : ''}>Fort</option>
                                <option value="automatique" ${currentTier === 'automatique' ? 'selected' : ''}>Automatique</option>
                            </select>
                        </div>
                    </div>
                    <div class="form-group" style="margin-bottom:0;flex:1.5;min-width:200px;">
                        <label class="form-label">Modèle préféré</label>
                        <div class="select-wrap">
                            <select id="agent-model-${name}" onchange="onAgentModelChange('${name}')">
                                ${buildModelOptionsForTier(currentTier, name)}
                            </select>
                        </div>
                    </div>
                </div>

                <!-- Context / System Prompt (collapsible) -->
                <details style="border:1px solid var(--border-color);border-radius:var(--radius-md);overflow:hidden;">
                    <summary style="padding:var(--space-sm) var(--space-md);cursor:pointer;font-size:0.78rem;font-weight:600;color:var(--accent-primary);background:rgba(var(--accent-primary-rgb),0.03);">
                        📋 Contexte & System Prompt
                    </summary>
                    <div style="padding:var(--space-md);font-size:0.72rem;color:var(--text-secondary);font-family:var(--font-mono);white-space:pre-wrap;line-height:1.5;max-height:200px;overflow-y:auto;background:rgba(0,0,0,0.15);">${agent.systemPrompt || '(Aucun contexte défini)'}</div>
                </details>

                <!-- Outils disponibles (collapsible) -->
                ${(agent.tools && agent.tools.length) ? `
                <details style="border:1px solid var(--border-color);border-radius:var(--radius-md);overflow:hidden;">
                    <summary style="padding:var(--space-sm) var(--space-md);cursor:pointer;font-size:0.78rem;font-weight:600;color:var(--success);background:rgba(var(--success-rgb),0.03);">
                        🛠️ Outils disponibles (${agent.tools.length})
                    </summary>
                    <div style="padding:var(--space-md);display:flex;flex-direction:column;gap:0.15rem;">${toolsHtml}</div>
                </details>` : ''}

                <!-- Fichier source -->
                <div style="display:flex;justify-content:space-between;align-items:center;font-size:0.7rem;color:var(--text-muted);">
                    <span>Fichier : <span style="font-family:var(--font-mono);color:var(--accent-primary);">${agent.file || '—'}</span></span>
                    ${isCustom ? `<button class="btn btn--danger btn--xs" onclick="deleteCustomAgent('${name}')">Supprimer</button>` : ''}
                </div>
            </div>
        `;
    }).join('');
}

/* ─── Création d'un nouvel agent ─── */
function openCreateAgentModal() {
    document.getElementById('create-agent-modal')?.classList.add('visible');
}

function closeCreateAgentModal() {
    document.getElementById('create-agent-modal')?.classList.remove('visible');
}

function createNewAgent() {
    const name = document.getElementById('new-agent-name')?.value.trim().replace(/\s+/g, '_');
    const role = document.getElementById('new-agent-role')?.value.trim();
    const icon = document.getElementById('new-agent-icon')?.value.trim() || '🤖';
    const desc = document.getElementById('new-agent-desc')?.value.trim();
    const prompt = document.getElementById('new-agent-prompt')?.value.trim();
    const tier = document.getElementById('new-agent-tier')?.value;
    const parent = document.getElementById('new-agent-parent')?.value;

    if (!name || !role) {
        showToast('warning', 'Le nom technique et le rôle sont obligatoires.');
        return;
    }

    if (AGENTS_REGISTRY[name]) {
        showToast('error', `L'agent "${name}" existe déjà dans le registre.`);
        return;
    }

    const newAgent = {
        name, icon, role, desc,
        file: `agents/${name}.py (à créer)`,
        cls: parent === 'executor' ? `CustomAgent (extends ExecutorAgent)` : `CustomAgent (extends BaseAgent)`,
        defaultTier: tier,
        tools: parent === 'executor' ? ["(hérite des outils d'ExecutorAgent)"] : ["(agent texte — pas d'outils)"],
        systemPrompt: prompt || `Tu es un agent spécialisé : ${role}.`,
        parentClass: parent
    };

    // Sauvegarder en localStorage
    const customs = JSON.parse(localStorage.getItem('custom_agents') || '[]');
    customs.push(newAgent);
    localStorage.setItem('custom_agents', JSON.stringify(customs));

    closeCreateAgentModal();
    showToast('success', `Agent "${role}" créé ! Il apparaît dans la palette et les cartes.`);

    // Rafraîchir
    loadAgentsData();
    buildPalette();
}

function deleteCustomAgent(name) {
    if (!confirm(`Supprimer l'agent custom "${name}" ?`)) return;
    const customs = JSON.parse(localStorage.getItem('custom_agents') || '[]');
    localStorage.setItem('custom_agents', JSON.stringify(customs.filter(a => a.name !== name)));
    showToast('info', `Agent "${name}" supprimé.`);
    loadAgentsData();
    buildPalette();
}

/* ─── Construction dynamique des options du dropdown Modèle ─── */
function buildModelOptionsForTier(tier, agentName) {
    // Récupérer la config sauvegardée des modèles préférés par agent
    const savedModels = JSON.parse(localStorage.getItem('agent_preferred_models') || '{}');
    const savedModel = savedModels[agentName] || 'auto';

    // Liste des modèles validés pour ce tier (depuis config.json ou RECOMMENDED_BY_TIER)
    let tierModels = [];
    try {
        // Tenter de récupérer depuis le cache global (chargé au boot)
        const configTiers = window.__cachedConfig?.tiers || {};
        tierModels = configTiers[tier] || RECOMMENDED_BY_TIER[tier] || [];
    } catch {
        tierModels = RECOMMENDED_BY_TIER[tier] || [];
    }

    // Construire les options
    let html = `<option value="auto" ${savedModel === 'auto' ? 'selected' : ''}>🔄 Automatique (cascade routing)</option>`;
    tierModels.forEach(modelId => {
        const entry = getModelEntry(modelId);
        const label = entry ? entry.title : modelId;
        const chInfo = entry ? (CHANNELS[entry.channel] || {}) : {};
        const costHint = entry?.pricing?.type === 'free' || entry?.pricing?.type === 'local'
            ? '🆓' : (entry?.pricing?.type === 'subscription' ? '💻' : '💳');
        html += `<option value="${modelId}" ${savedModel === modelId ? 'selected' : ''}>${costHint} ${label}</option>`;
    });

    return html;
}

/* ─── Tier change handler — met à jour le dropdown Modèle dynamiquement ─── */
function onAgentTierChange(agentName) {
    const select = document.getElementById(`agent-tier-${agentName}`);
    if (!select) return;
    const newTier = select.value;

    // Mettre à jour le dropdown Modèle
    const modelSelect = document.getElementById(`agent-model-${agentName}`);
    if (modelSelect) {
        modelSelect.innerHTML = buildModelOptionsForTier(newTier, agentName);
    }

    // Sauvegarder le tier côté serveur
    const configMap = {
        planner: "planner_model",
        executor: "executor_model",
        antigravity_agent: "antigravity_model"
    };
    if (configMap[agentName]) {
        saveAgentTier({ [configMap[agentName]]: newTier });
    }
}

/* ─── Model change handler — sauvegarde le modèle préféré localement ─── */
function onAgentModelChange(agentName) {
    const modelSelect = document.getElementById(`agent-model-${agentName}`);
    if (!modelSelect) return;
    const selectedModel = modelSelect.value;

    // Sauvegarder dans localStorage
    const savedModels = JSON.parse(localStorage.getItem('agent_preferred_models') || '{}');
    savedModels[agentName] = selectedModel;
    localStorage.setItem('agent_preferred_models', JSON.stringify(savedModels));

    const label = selectedModel === 'auto' ? 'Automatique' : selectedModel;
    showToast("info", `Modèle préféré de ${agentName} → ${label}`);
}

async function saveAgentTier(partialConfig) {
    try {
        const currentConfig = await fetchConfig();
        const merged = { ...currentConfig, ...partialConfig };
        // Mettre en cache pour le dropdown dynamique
        window.__cachedConfig = merged;
        await saveConfig(merged);
        showToast("success", "Configuration de l'agent sauvegardée.");
    } catch (err) {
        showToast("error", "Erreur de sauvegarde : " + err.message);
    }
}

/**
 * Valide la structure du workflow (nœuds obligatoires, orphelins)
 */
function validateWorkflowGraph() {
    if (typeof wfState === "undefined" || !wfState.nodes) return true;

    // 1. Vérifier la présence des nœuds Start et End
    const startNodes = wfState.nodes.filter(n => n.type === 'start');
    if (startNodes.length === 0) {
        throw new Error("Le workflow doit contenir un nœud de départ (Entrée).");
    }
    const endNodes = wfState.nodes.filter(n => n.type === 'end');
    if (endNodes.length === 0) {
        throw new Error("Le workflow doit contenir un nœud d'arrivée (Sortie).");
    }

    // 2. Vérifier les connexions orphelines pour les agents et conditions
    for (const node of wfState.nodes) {
        if (node.type === 'agent' || node.type === 'condition') {
            const hasIncoming = wfState.connections.some(c => c.to === node.id);
            const hasOutgoing = wfState.connections.some(c => c.from === node.id);
            
            if (!hasIncoming) {
                throw new Error(`L'agent ou la condition '${node.label}' n'a pas de connexion entrante.`);
            }
            if (!hasOutgoing) {
                throw new Error(`L'agent ou la condition '${node.label}' n'a pas de connexion sortante.`);
            }
        }
    }
    return true;
}

/**
 * Sauvegarde le workflow puis l'applique au moteur Python (rechargement du bridge)
 */
async function applyWorkflowToEngine() {
    try {
        // Valider la topologie du graphe
        validateWorkflowGraph();

        // 1. Sauvegarder d'abord
        const data = serializeWorkflow();
        await saveWorkflow(data);
        
        // 2. Appliquer au moteur
        const res = await fetch('/api/workflows/apply', { method: 'POST' });
        if (!res.ok) throw new Error(await res.text());
        const result = await res.json();
        
        const customCount = result.custom_agents?.length || 0;
        const totalCount = result.agents?.length || 0;
        showToast('success', `Workflow appliqué au moteur ! ${totalCount} agents actifs (${customCount} custom).`);
    } catch (err) {
        showToast('error', 'Erreur d\'application : ' + err.message);
    }
}

/* ============================================================
   D1 — Mise à jour live du status des Agent Cards
   ============================================================ */

/**
 * Met à jour le status visuel d'une agent card
 * @param {string} agentName — Nom technique de l'agent
 * @param {'idle'|'running'|'success'|'error'} status
 * @param {string} [lastAction] — Dernière action effectuée
 */
function updateAgentCardStatus(agentName, status, lastAction) {
    const dot = document.getElementById(`agent-dot-${agentName}`);
    const text = document.getElementById(`agent-status-text-${agentName}`);
    const badge = document.getElementById(`agent-status-badge-${agentName}`);
    const avatar = document.getElementById(`agent-avatar-${agentName}`);
    const lastEl = document.getElementById(`agent-last-action-${agentName}`);

    if (!dot) return;

    // Mapping status → visuels
    const statusMap = {
        idle: { dotClass: 'success', label: 'Prêt', color: 'var(--success)', borderColor: 'rgba(var(--success-rgb),0.2)' },
        running: { dotClass: 'running', label: 'En cours...', color: 'var(--warning)', borderColor: 'rgba(var(--warning-rgb),0.3)' },
        success: { dotClass: 'success', label: 'Terminé ✓', color: 'var(--success)', borderColor: 'rgba(var(--success-rgb),0.3)' },
        error: { dotClass: 'error', label: 'Erreur ✗', color: 'var(--error)', borderColor: 'rgba(var(--error-rgb),0.3)' }
    };

    const s = statusMap[status] || statusMap.idle;

    // Appliquer les visuels
    dot.className = `status-dot ${s.dotClass}`;
    if (text) text.textContent = s.label;
    if (badge) {
        badge.style.borderColor = s.borderColor;
        badge.style.color = s.color;
    }

    // Animation pulse sur l'avatar quand l'agent est en cours
    if (avatar) {
        avatar.style.animation = status === 'running' ? 'agentPulse 1.5s ease-in-out infinite' : '';
    }

    // Dernière action
    if (lastEl && lastAction) {
        const now = new Date().toLocaleTimeString('fr-FR', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
        lastEl.textContent = `${now} — ${lastAction}`;
    }
}
