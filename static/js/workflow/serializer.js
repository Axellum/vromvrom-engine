/* ============================================================
   SERIALIZER.JS — Sérialisation/Désérialisation du workflow
   Export/Import JSON → agents_workflows.json
   ============================================================ */

/**
 * Sérialise l'état actuel du workflow en objet JSON
 * @returns {Object} — { name, nodes, connections, metadata }
 */
function serializeWorkflow() {
    return {
        name: "workflow_principal",
        version: "1.0",
        created_at: new Date().toISOString(),
        nodes: wfState.nodes.map(n => ({
            id: n.id,
            type: n.type,
            label: n.label,
            icon: n.icon,
            x: Math.round(n.x),
            y: Math.round(n.y),
            tier: n.tier || null,
            agentName: n.agentName || null,
            conditionExpr: n.conditionExpr || null,
            model: n.model || null,
            systemPrompt: n.systemPrompt || null,
            tools: n.tools || null
        })),
        connections: wfState.connections.map(c => ({
            id: c.id,
            from: c.from,
            fromPort: c.fromPort,
            to: c.to,
            toPort: c.toPort
        })),
        metadata: {
            totalNodes: wfState.nodes.length,
            totalConnections: wfState.connections.length,
            agents: wfState.nodes.filter(n => n.type === 'agent').map(n => n.agentName).filter(Boolean),
            hasConditions: wfState.nodes.some(n => n.type === 'condition')
        }
    };
}

/**
 * Désérialise un objet JSON et reconstruit le canvas
 * @param {Object} data — Données du workflow
 */
function deserializeWorkflow(data) {
    if (!data || !data.nodes) return;

    // Effacer l'état actuel
    clearWfCanvas();

    // Calculer le prochain ID
    let maxId = 0;
    data.nodes.forEach(n => {
        const numPart = parseInt(n.id.replace('node-', ''), 10);
        if (numPart > maxId) maxId = numPart;
    });
    wfState.nextId = maxId + 1;

    // Recréer les nœuds
    data.nodes.forEach(n => {
        const node = {
            id: n.id,
            type: n.type,
            label: n.label,
            icon: n.icon,
            x: n.x,
            y: n.y,
            tier: n.tier || 'automatique',
            agentName: n.agentName || '',
            conditionExpr: n.conditionExpr || '',
            model: n.model || null,
            systemPrompt: n.systemPrompt || null,
            tools: n.tools || null
        };
        wfState.nodes.push(node);
        renderWfNode(node);
    });

    // Recréer les connexions
    if (data.connections) {
        data.connections.forEach(c => {
            const conn = {
                id: c.id,
                from: c.from,
                fromPort: c.fromPort,
                to: c.to,
                toPort: c.toPort
            };
            wfState.connections.push(conn);
            renderWfConnection(conn);
        });
    }
}

/**
 * Exporte le workflow en JSON (téléchargement fichier)
 */
function exportWorkflowJSON() {
    const data = serializeWorkflow();
    const json = JSON.stringify(data, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);

    const a = document.createElement('a');
    a.href = url;
    a.download = `workflow_${new Date().toISOString().slice(0,10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    showToast('success', 'Workflow exporté en JSON.');
}

/**
 * Importe un workflow depuis un fichier JSON (input file)
 */
function importWorkflowJSON() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = (e) => {
        const file = e.target.files[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = (ev) => {
            try {
                const data = JSON.parse(ev.target.result);
                deserializeWorkflow(data);
                showToast('success', `Workflow importé (${data.nodes?.length || 0} nœuds).`);
            } catch (err) {
                showToast('error', 'Fichier JSON invalide : ' + err.message);
            }
        };
        reader.readAsText(file);
    };
    input.click();
}
