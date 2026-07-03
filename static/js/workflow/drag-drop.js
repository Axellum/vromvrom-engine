/* ============================================================
   DRAG-DROP.JS — Logique Drag & Drop depuis la palette vers le canvas
   + Panneau de propriétés (édition d'un nœud)
   ============================================================ */

/* ─── Drag depuis la palette ─── */

let paletteGhost = null;
let paletteDragData = null;

/**
 * Initialise le drag & drop depuis la palette d'agents
 */
function initWfDragDrop() {
    const paletteItems = document.querySelectorAll('.wf-palette__item');
    paletteItems.forEach(item => {
        item.addEventListener('mousedown', onPaletteDragStart);
    });

    document.addEventListener('mousemove', onPaletteDragMove);
    document.addEventListener('mouseup', onPaletteDragEnd);
}

function onPaletteDragStart(e) {
    e.preventDefault();
    const item = e.currentTarget;
    const type = item.getAttribute('data-type');
    const label = item.getAttribute('data-label');
    const icon = item.getAttribute('data-icon');
    const agentName = item.getAttribute('data-agent-name') || '';

    paletteDragData = { type, label, icon, agentName };

    // Créer le ghost
    paletteGhost = document.createElement('div');
    paletteGhost.className = 'wf-ghost';
    paletteGhost.innerHTML = `<span>${icon}</span> ${label}`;
    paletteGhost.style.left = `${e.clientX - 40}px`;
    paletteGhost.style.top = `${e.clientY - 15}px`;
    document.body.appendChild(paletteGhost);
}

function onPaletteDragMove(e) {
    if (!paletteGhost) return;
    paletteGhost.style.left = `${e.clientX - 40}px`;
    paletteGhost.style.top = `${e.clientY - 15}px`;
}

function onPaletteDragEnd(e) {
    if (!paletteGhost || !paletteDragData) {
        paletteGhost = null;
        paletteDragData = null;
        return;
    }

    paletteGhost.remove();
    paletteGhost = null;

    // Vérifier si on lâche dans le canvas
    const canvasWrap = wfState.canvasWrap;
    if (!canvasWrap) { paletteDragData = null; return; }

    const rect = canvasWrap.getBoundingClientRect();
    if (e.clientX >= rect.left && e.clientX <= rect.right &&
        e.clientY >= rect.top && e.clientY <= rect.bottom) {

        // Position dans le SVG
        const svgPt = getSvgPoint(e);
        const { type, label, icon, agentName } = paletteDragData;

        addWfNode(type, label, icon, svgPt.x - WF_NODE_W / 2, svgPt.y - WF_NODE_H / 2, {
            agentName,
            tier: type === 'agent' ? 'automatique' : undefined
        });

        showToast('success', `Nœud "${label}" ajouté au workflow.`);
    }

    paletteDragData = null;
}

/* ─── Panneau de propriétés ─── */

function openWfProps(nodeId) {
    const node = wfState.nodes.find(n => n.id === nodeId);
    if (!node) return;

    const panel = document.getElementById('wf-props-panel');
    if (!panel) return;

    panel.classList.add('open');

    const body = panel.querySelector('.wf-props__body');
    const title = panel.querySelector('.wf-props__header span');
    if (title) title.textContent = `${node.icon} ${node.label}`;

    if (!body) return;

    if (node.type === 'agent') {
        // Construire la liste dynamique des agents (registre + custom)
        const customAgents = JSON.parse(localStorage.getItem('custom_agents') || '[]');
        const allAgentNames = [
            ...Object.keys(AGENTS_REGISTRY),
            ...customAgents.map(a => a.name)
        ];
        const agentOptionsHtml = allAgentNames.map(name => {
            const reg = AGENTS_REGISTRY[name] || customAgents.find(a => a.name === name) || {};
            const label = reg.role || name;
            return `<option value="${name}" ${node.agentName === name ? 'selected' : ''}>${reg.icon || '🤖'} ${label}</option>`;
        }).join('');

        // Récupérer les détails du registre pour l'agent sélectionné
        const agentInfo = AGENTS_REGISTRY[node.agentName] || customAgents.find(a => a.name === node.agentName) || {};

        // Résoudre le system prompt : priorité au nœud, sinon le registre
        const effectivePrompt = node.systemPrompt || agentInfo.systemPrompt || '';

        // Résoudre les outils : priorité au nœud, sinon le registre
        const effectiveTools = node.tools || agentInfo.tools || [];

        // Construire le HTML des outils avec checkboxes
        const toolsCheckboxesHtml = effectiveTools.map((t, i) =>
            `<label class="wf-tool-item">
                <input type="checkbox" checked data-tool-idx="${i}" onchange="onWfToolToggle('${node.id}', ${i}, this.checked)">
                <span>${t}</span>
            </label>`
        ).join('') || '<div style="font-size:0.65rem;color:var(--text-muted);">(aucun outil)</div>';

        // Construire le dropdown modèle — selon le tier courant
        const currentTier = node.tier || 'automatique';
        let modelOptionsHtml = `<option value="auto" ${(!node.model || node.model === 'auto') ? 'selected' : ''}>🔄 Auto (cascade routing)</option>`;
        try {
            if (typeof buildModelOptionsForTier === 'function') {
                // Réutiliser la fonction existante et remplacer la sélection
                const tmpHtml = buildModelOptionsForTier(currentTier, node.agentName || '');
                // Remplacer la sélection par celle du nœud
                modelOptionsHtml = tmpHtml.replace(/selected/g, '').replace(
                    `value="${node.model || 'auto'}"`,
                    `value="${node.model || 'auto'}" selected`
                );
            }
        } catch {}

        body.innerHTML = `
            <div class="form-group">
                <label class="form-label">Nom affiché</label>
                <input type="text" id="wf-prop-label" value="${node.label}" onchange="updateWfNodeProp('${node.id}', 'label', this.value)">
            </div>
            <div class="form-group">
                <label class="form-label">Agent backend</label>
                <div class="select-wrap">
                    <select id="wf-prop-agent" onchange="updateWfNodeProp('${node.id}', 'agentName', this.value); openWfProps('${node.id}');">
                        ${agentOptionsHtml}
                    </select>
                </div>
            </div>
            <div class="form-group">
                <label class="form-label">Tier de complexité</label>
                <div class="select-wrap">
                    <select id="wf-prop-tier" onchange="updateWfNodeProp('${node.id}', 'tier', this.value); onWfTierChange('${node.id}', this.value);">
                        <option value="leger" ${node.tier === 'leger' ? 'selected' : ''}>Léger</option>
                        <option value="moyen" ${node.tier === 'moyen' ? 'selected' : ''}>Moyen</option>
                        <option value="fort" ${node.tier === 'fort' ? 'selected' : ''}>Fort</option>
                        <option value="automatique" ${node.tier === 'automatique' ? 'selected' : ''}>Automatique</option>
                    </select>
                </div>
            </div>

            <!-- Choix du moteur / modèle LLM -->
            <div class="form-group">
                <label class="form-label">🧪 Moteur / Modèle LLM</label>
                <div class="select-wrap">
                    <select id="wf-prop-model" onchange="updateWfNodeProp('${node.id}', 'model', this.value)">
                        ${modelOptionsHtml}
                    </select>
                </div>
            </div>

            <!-- System Prompt éditable -->
            <div class="wf-props-section">
                <div class="wf-props-section__header" style="color:var(--accent-primary);">
                    📋 System Prompt
                    <button class="wf-props-section__btn" onclick="resetWfNodePrompt('${node.id}')" title="Réinitialiser depuis le registre">⟲</button>
                </div>
                <textarea id="wf-prop-prompt" class="wf-prompt-editor" rows="6" placeholder="Instructions système pour cet agent..."
                    onchange="updateWfNodeProp('${node.id}', 'systemPrompt', this.value)">${effectivePrompt}</textarea>
            </div>

            <!-- Outils configurables -->
            <div class="wf-props-section">
                <div class="wf-props-section__header" style="color:var(--success);">
                    🛠️ Outils (${effectiveTools.length})
                    <button class="wf-props-section__btn" onclick="resetWfNodeTools('${node.id}')" title="Réinitialiser depuis le registre">⟲</button>
                </div>
                <div class="wf-tools-list" id="wf-tools-list-${node.id}">
                    ${toolsCheckboxesHtml}
                </div>
                <div style="display:flex;gap:var(--space-xs);margin-top:var(--space-sm);">
                    <input type="text" id="wf-new-tool-${node.id}" placeholder="Ajouter un outil..." style="flex:1;font-size:0.72rem;padding:0.3rem 0.5rem;">
                    <button class="btn btn--ghost btn--xs" onclick="addWfNodeTool('${node.id}')">+</button>
                </div>
            </div>

            <div style="display:flex;gap:var(--space-sm);flex-wrap:wrap;">
                <button class="btn btn--danger btn--sm" onclick="deleteWfNode('${node.id}')">🗑️ Supprimer</button>
                <button class="btn btn--ghost btn--sm" onclick="openCreateAgentModal()" title="Créer un nouvel agent dans le registre">🤖 Nouvel Agent</button>
            </div>
        `;
    } else if (node.type === 'condition') {
        body.innerHTML = `
            <div class="form-group">
                <label class="form-label">Condition (expression)</label>
                <textarea id="wf-prop-condition" rows="3" placeholder="ex: result.status === 'error'"
                    onchange="updateWfNodeProp('${node.id}', 'conditionExpr', this.value)">${node.conditionExpr || ''}</textarea>
            </div>
            <div class="callout callout--info" style="font-size:0.78rem;">
                <span class="callout__icon">💡</span>
                <div>La branche <strong style="color:var(--success);">✓ droite</strong> sera suivie si la condition est vraie, et la branche <strong style="color:var(--error);">✗ gauche</strong> dans le cas contraire.</div>
            </div>
            <button class="btn btn--danger btn--sm" onclick="deleteWfNode('${node.id}')">Supprimer</button>
        `;
    } else {
        body.innerHTML = `
            <div style="font-size:0.85rem;color:var(--text-secondary);">
                Nœud de type <strong>${node.type === 'start' ? 'Entrée' : 'Sortie'}</strong>.
                <br><br>Ce nœud marque le ${node.type === 'start' ? 'début' : 'fin'} du workflow.
            </div>
            <button class="btn btn--danger btn--sm" onclick="deleteWfNode('${node.id}')">Supprimer</button>
        `;
    }
}

function closeWfProps() {
    const panel = document.getElementById('wf-props-panel');
    if (panel) panel.classList.remove('open');
}

/**
 * Met à jour une propriété d'un nœud et re-render
 */
function updateWfNodeProp(nodeId, prop, value) {
    const node = wfState.nodes.find(n => n.id === nodeId);
    if (!node) return;
    node[prop] = value;
    renderWfNode(node);
    refreshConnections();
}

/* ─── Actions de la toolbar ─── */

function wfToolbarAction(action) {
    switch (action) {
        case 'clear':
            if (confirm('Effacer tout le workflow ?')) {
                clearWfCanvas();
                showToast('info', 'Workflow effacé.');
            }
            break;

        case 'save':
            saveCurrentWorkflow();
            break;

        case 'load':
            loadCurrentWorkflow();
            break;

        case 'delete':
            if (wfState.selectedNode) {
                deleteWfNode(wfState.selectedNode);
            } else if (wfState.selectedConnection) {
                deleteWfConnection(wfState.selectedConnection);
            } else {
                showToast('warning', 'Sélectionnez un nœud ou une connexion à supprimer.');
            }
            break;

        case 'auto-layout':
            autoLayoutWorkflow();
            break;
    }
}

/**
 * Sauvegarde le workflow actuel via l'API
 */
async function saveCurrentWorkflow() {
    const data = serializeWorkflow();
    try {
        await saveWorkflow(data);
        showToast('success', 'Workflow sauvegardé avec succès !');
    } catch (err) {
        showToast('error', 'Erreur de sauvegarde : ' + err.message);
    }
}

/**
 * Charge le dernier workflow depuis l'API
 */
async function loadCurrentWorkflow() {
    try {
        const data = await fetchWorkflows();
        if (data && data.nodes && data.nodes.length > 0) {
            deserializeWorkflow(data);
            showToast('success', `Workflow chargé (${data.nodes.length} nœuds, ${data.connections.length} connexions).`);
        } else {
            showToast('info', 'Aucun workflow sauvegardé trouvé.');
        }
    } catch (err) {
        showToast('error', 'Erreur de chargement : ' + err.message);
    }
}

/**
 * Disposition automatique simple (gauche à droite)
 */
function autoLayoutWorkflow() {
    const margin = 40;
    const spacingX = WF_NODE_W + 60;
    const spacingY = WF_NODE_H + 40;

    // Grouper par type : start, agents, conditions, end
    const starts = wfState.nodes.filter(n => n.type === 'start');
    const agents = wfState.nodes.filter(n => n.type === 'agent');
    const conditions = wfState.nodes.filter(n => n.type === 'condition');
    const ends = wfState.nodes.filter(n => n.type === 'end');

    let col = 0;
    [starts, agents, conditions, ends].forEach(group => {
        group.forEach((node, row) => {
            node.x = margin + col * spacingX;
            node.y = margin + row * spacingY;
        });
        if (group.length > 0) col++;
    });

    // Re-render tout
    wfState.nodes.forEach(n => renderWfNode(n));
    refreshConnections();
    showToast('info', 'Disposition automatique appliquée.');
}

/* ═══════════════════════════════════════════════════════════════
   HELPERS — Gestion interactive des propriétés de nœud agent
   (outils, system prompt, modèle)
   ═══════════════════════════════════════════════════════════════ */

/**
 * Toggle un outil (checkbox) — supprime l'outil quand décoché
 */
function onWfToolToggle(nodeId, toolIdx, checked) {
    const node = wfState.nodes.find(n => n.id === nodeId);
    if (!node) return;

    // Initialiser tools sur le nœud si pas encore fait
    if (!node.tools) {
        const customAgents = JSON.parse(localStorage.getItem('custom_agents') || '[]');
        const agentInfo = AGENTS_REGISTRY[node.agentName] || customAgents.find(a => a.name === node.agentName) || {};
        node.tools = [...(agentInfo.tools || [])];
    }

    if (!checked) {
        // Supprimer l'outil
        node.tools.splice(toolIdx, 1);
        // Re-ouvrir le panneau pour rafraîchir la liste
        openWfProps(nodeId);
    }
}

/**
 * Ajoute un outil custom au nœud
 */
function addWfNodeTool(nodeId) {
    const input = document.getElementById(`wf-new-tool-${nodeId}`);
    if (!input) return;

    const toolName = input.value.trim();
    if (!toolName) {
        showToast('warning', 'Saisissez le nom de l\'outil.');
        return;
    }

    const node = wfState.nodes.find(n => n.id === nodeId);
    if (!node) return;

    // Initialiser tools si pas encore fait
    if (!node.tools) {
        const customAgents = JSON.parse(localStorage.getItem('custom_agents') || '[]');
        const agentInfo = AGENTS_REGISTRY[node.agentName] || customAgents.find(a => a.name === node.agentName) || {};
        node.tools = [...(agentInfo.tools || [])];
    }

    node.tools.push(toolName);
    input.value = '';
    openWfProps(nodeId); // Rafraîchir
    showToast('success', `Outil "${toolName}" ajouté.`);
}

/**
 * Réinitialise le system prompt du nœud depuis le registre
 */
function resetWfNodePrompt(nodeId) {
    const node = wfState.nodes.find(n => n.id === nodeId);
    if (!node) return;

    const customAgents = JSON.parse(localStorage.getItem('custom_agents') || '[]');
    const agentInfo = AGENTS_REGISTRY[node.agentName] || customAgents.find(a => a.name === node.agentName) || {};

    node.systemPrompt = agentInfo.systemPrompt || '';
    openWfProps(nodeId); // Rafraîchir
    showToast('info', 'System prompt réinitialisé depuis le registre.');
}

/**
 * Réinitialise les outils du nœud depuis le registre
 */
function resetWfNodeTools(nodeId) {
    const node = wfState.nodes.find(n => n.id === nodeId);
    if (!node) return;

    const customAgents = JSON.parse(localStorage.getItem('custom_agents') || '[]');
    const agentInfo = AGENTS_REGISTRY[node.agentName] || customAgents.find(a => a.name === node.agentName) || {};

    node.tools = [...(agentInfo.tools || [])];
    openWfProps(nodeId); // Rafraîchir
    showToast('info', 'Outils réinitialisés depuis le registre.');
}

/**
 * Quand le tier change dans les propriétés, mettre à jour le dropdown modèle
 */
function onWfTierChange(nodeId, newTier) {
    const node = wfState.nodes.find(n => n.id === nodeId);
    if (!node) return;

    // Réinitialiser le modèle à 'auto' quand le tier change
    node.model = 'auto';
    // Rafraîchir tout le panneau pour reconstruire le dropdown modèle
    openWfProps(nodeId);
}

