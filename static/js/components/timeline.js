/* ============================================================
   TIMELINE.JS — Axe 3 — Timeline d'Exécution Gantt
   Composant SVG horizontal affichant le timing de chaque agent
   pendant l'exécution (barres proportionnelles, couleurs status).
   ============================================================ */

/* ─── État local de la timeline ─── */
const timelineState = {
    startTime: null,         // Timestamp absolu du premier agent
    endTime: null,           // Timestamp de fin d'orchestration
    agents: {},              // Map { agentName: { start, end, status, label, icon } }
    isActive: false,         // True si au moins un agent est en cours
    rafHandle: null          // Handle requestAnimationFrame
};

/* ─── Mapping agent → couleurs et icônes ─── */
const AGENT_COLORS = {
    planner:            { color: 'rgba(167,139,250,0.7)', glow: 'rgba(167,139,250,0.3)', icon: '🧠' },
    router:             { color: 'rgba(245,158,11,0.7)', glow: 'rgba(245,158,11,0.3)', icon: '⚙️' },
    executor:           { color: 'rgba(16,185,129,0.7)', glow: 'rgba(16,185,129,0.3)', icon: '🔧' },
    antigravity_agent:  { color: 'rgba(129,140,248,0.7)', glow: 'rgba(129,140,248,0.3)', icon: '🚀' },
    ha_agent:           { color: 'rgba(6,182,212,0.7)', glow: 'rgba(6,182,212,0.3)', icon: '🏠' },
    reviewer:           { color: 'rgba(217,70,239,0.7)', glow: 'rgba(217,70,239,0.3)', icon: '🔍' }
};

const TIMELINE_STATUS_COLORS = {
    running: 'rgba(245,158,11,0.8)',
    success: 'rgba(16,185,129,0.7)',
    error:   'rgba(239,68,68,0.7)'
};

/**
 * Enregistre le démarrage d'un agent dans la timeline
 * @param {string} agentName — Nom technique de l'agent
 * @param {string} [taskObjective] — Objectif de la tâche (optionnel)
 */
function timelineOnAgentStarted(agentName, taskObjective) {
    const now = Date.now();

    // Initialiser la timeline si c'est le premier agent
    if (!timelineState.startTime) {
        timelineState.startTime = now;
        timelineState.endTime = null;
        timelineState.agents = {};
    }

    // Enregistrer l'agent
    timelineState.agents[agentName] = {
        start: now,
        end: null,
        status: 'running',
        label: taskObjective || agentName,
        icon: (AGENT_COLORS[agentName] || {}).icon || '🤖'
    };

    timelineState.isActive = true;

    // Afficher le panneau et démarrer le rendu live
    showTimelinePanel();
    startTimelineRAF();
}

/**
 * Enregistre la fin d'un agent dans la timeline
 * @param {string} agentName — Nom technique de l'agent
 * @param {string} status — 'success' ou 'error'
 */
function timelineOnAgentCompleted(agentName, status) {
    const agent = timelineState.agents[agentName];
    if (agent) {
        agent.end = Date.now();
        agent.status = status || 'success';
    }

    // Vérifier s'il reste des agents en cours
    const anyRunning = Object.values(timelineState.agents).some(a => !a.end);
    if (!anyRunning) {
        timelineState.isActive = false;
        timelineState.endTime = Date.now();
        stopTimelineRAF();
        renderTimeline(); // Rendu final
    }
}

/**
 * Réinitialise la timeline pour une nouvelle exécution
 */
function resetTimeline() {
    timelineState.startTime = null;
    timelineState.endTime = null;
    timelineState.agents = {};
    timelineState.isActive = false;
    stopTimelineRAF();

    const container = document.getElementById('timeline-svg-container');
    if (container) container.innerHTML = '';
}

/* ─── Affichage / Masquage du panneau ─── */
function showTimelinePanel() {
    const panel = document.getElementById('execution-timeline');
    if (panel) panel.style.display = 'block';
}

/* ─── Boucle de rendu requestAnimationFrame ─── */
function startTimelineRAF() {
    if (timelineState.rafHandle) return;
    function loop() {
        renderTimeline();
        if (timelineState.isActive) {
            timelineState.rafHandle = requestAnimationFrame(loop);
        }
    }
    timelineState.rafHandle = requestAnimationFrame(loop);
}

function stopTimelineRAF() {
    if (timelineState.rafHandle) {
        cancelAnimationFrame(timelineState.rafHandle);
        timelineState.rafHandle = null;
    }
}

/**
 * Dessine la timeline SVG complète
 */
function renderTimeline() {
    const container = document.getElementById('timeline-svg-container');
    if (!container || !timelineState.startTime) return;

    const agents = Object.entries(timelineState.agents);
    if (agents.length === 0) return;

    // Calcul de la plage temporelle
    const tStart = timelineState.startTime;
    const tEnd = timelineState.endTime || Date.now();
    const totalDuration = Math.max(tEnd - tStart, 1); // Éviter division par 0

    // Dimensions SVG
    const barHeight = 28;
    const barGap = 6;
    const labelWidth = 130;
    const durationLabelWidth = 70;
    const chartLeft = labelWidth + 8;
    const chartWidth = 500;
    const totalWidth = chartLeft + chartWidth + durationLabelWidth + 10;
    const totalHeight = agents.length * (barHeight + barGap) + 40; // +40 pour le footer

    let svg = `<svg width="100%" height="${totalHeight}" viewBox="0 0 ${totalWidth} ${totalHeight}" xmlns="http://www.w3.org/2000/svg" style="overflow:visible;">`;

    // Fond de la timeline (grille)
    svg += `<rect x="${chartLeft}" y="0" width="${chartWidth}" height="${agents.length * (barHeight + barGap)}" rx="4" fill="rgba(255,255,255,0.01)" stroke="rgba(255,255,255,0.04)" stroke-width="0.5"/>`;

    // Lignes de grille verticales (25%, 50%, 75%)
    for (let pct of [0.25, 0.5, 0.75]) {
        const x = chartLeft + chartWidth * pct;
        svg += `<line x1="${x}" y1="0" x2="${x}" y2="${agents.length * (barHeight + barGap)}" stroke="rgba(255,255,255,0.04)" stroke-width="0.5" stroke-dasharray="2,4"/>`;
        svg += `<text x="${x}" y="${agents.length * (barHeight + barGap) + 14}" text-anchor="middle" fill="rgba(255,255,255,0.2)" font-size="8" font-family="JetBrains Mono, monospace">${((totalDuration * pct) / 1000).toFixed(1)}s</text>`;
    }

    // Barres des agents
    agents.forEach(([name, agent], idx) => {
        const y = idx * (barHeight + barGap) + 2;
        const agentStart = agent.start - tStart;
        const agentEnd = (agent.end || Date.now()) - tStart;
        const agentDuration = agentEnd - agentStart;

        // Position proportionnelle
        const x = chartLeft + (agentStart / totalDuration) * chartWidth;
        const w = Math.max((agentDuration / totalDuration) * chartWidth, 3); // Minimum 3px

        // Couleurs
        const agentStyle = AGENT_COLORS[name] || { color: 'rgba(156,163,175,0.5)', glow: 'rgba(156,163,175,0.2)', icon: '🤖' };
        const statusColor = TIMELINE_STATUS_COLORS[agent.status] || agentStyle.color;

        // Label agent (icône + nom)
        svg += `<text x="${labelWidth}" y="${y + barHeight / 2 + 4}" text-anchor="end" fill="var(--text-secondary)" font-size="11" font-family="Inter, sans-serif" font-weight="500">${agent.icon} ${name}</text>`;

        // Barre principale
        svg += `<rect x="${x}" y="${y + 2}" width="${w}" height="${barHeight - 4}" rx="4" fill="${statusColor}" opacity="0.85">`;
        if (agent.status === 'running') {
            // Animation shimmer pour les agents en cours
            svg += `<animate attributeName="opacity" values="0.5;0.9;0.5" dur="1.5s" repeatCount="indefinite"/>`;
        }
        svg += `</rect>`;

        // Glow derrière la barre
        if (agent.status === 'running') {
            svg += `<rect x="${x}" y="${y + 2}" width="${w}" height="${barHeight - 4}" rx="4" fill="${agentStyle.glow}" filter="url(#timeline-glow)" opacity="0.4"/>`;
        }

        // Durée à droite de la barre
        const durationSec = (agentDuration / 1000).toFixed(1);
        svg += `<text x="${x + w + 6}" y="${y + barHeight / 2 + 4}" fill="var(--text-muted)" font-size="10" font-family="JetBrains Mono, monospace" font-weight="400">${durationSec}s</text>`;
    });

    // Filtre glow pour les barres running
    svg += `<defs><filter id="timeline-glow" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur in="SourceGraphic" stdDeviation="4"/></filter></defs>`;

    // Footer : durée totale + coût
    const footerY = agents.length * (barHeight + barGap) + 30;
    const totalSec = (totalDuration / 1000).toFixed(1);
    svg += `<text x="${chartLeft}" y="${footerY}" fill="var(--text-muted)" font-size="10" font-family="Inter, sans-serif">`;
    svg += `Durée totale : <tspan font-weight="600" fill="var(--text-secondary)">${totalSec}s</tspan>`;
    svg += ` · ${agents.length} agent(s)`;
    if (!timelineState.isActive) {
        svg += ` · <tspan fill="var(--success)">Terminé ✓</tspan>`;
    } else {
        svg += ` · <tspan fill="var(--warning)">En cours...</tspan>`;
    }
    svg += `</text>`;

    svg += `</svg>`;
    container.innerHTML = svg;
}
