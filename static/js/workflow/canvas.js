/* ============================================================
   CANVAS.JS — Rendu SVG du graphe d'agents (workflow editor)
   Gestion des nœuds, ports, connexions et interactions
   ============================================================ */

const WF_NODE_W = 180;
const WF_NODE_H = 70;
const WF_PORT_R = 6;
const WF_COND_SIZE = 60;

/* État global de l'éditeur */
let wfState = {
    nodes: [],
    connections: [],
    selectedNode: null,
    selectedConnection: null,
    nextId: 1,
    svgEl: null,
    canvasWrap: null,
    // État du drag de nœud
    dragging: null,
    dragOffset: { x: 0, y: 0 },
    // État de la création de connexion
    connecting: null, // { fromNodeId, fromPort, tempLine }
    // Viewport (zoom & pan)
    viewport: { x: 0, y: 0, w: 0, h: 0, zoom: 1, minZoom: 0.15, maxZoom: 3 },
    panning: false,
    panStart: { x: 0, y: 0 },
    panViewStart: { x: 0, y: 0 },
};

/**
 * Initialise le canvas SVG dans le conteneur donné
 */
function initWfCanvas(container) {
    wfState.canvasWrap = container;
    const rect = container.getBoundingClientRect();

    // Initialiser le viewport avec les dimensions réelles
    wfState.viewport.w = rect.width;
    wfState.viewport.h = rect.height;
    wfState.viewport.x = 0;
    wfState.viewport.y = 0;
    wfState.viewport.zoom = 1;

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "wf-canvas");
    svg.setAttribute("viewBox", `0 0 ${rect.width} ${rect.height}`);
    svg.id = "wf-svg";

    // Grille de fond (très grande pour couvrir le zoom out)
    const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    defs.innerHTML = `
        <pattern id="wf-grid" width="30" height="30" patternUnits="userSpaceOnUse">
            <path d="M 30 0 L 0 0 0 30" fill="none" class="wf-grid-pattern"/>
        </pattern>
    `;
    svg.appendChild(defs);

    const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    bg.setAttribute("x", "-5000");
    bg.setAttribute("y", "-5000");
    bg.setAttribute("width", "15000");
    bg.setAttribute("height", "15000");
    bg.setAttribute("fill", "url(#wf-grid)");
    svg.appendChild(bg);

    // Couche connexions (dessous)
    const connLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
    connLayer.id = "wf-connections-layer";
    svg.appendChild(connLayer);

    // Couche nœuds (dessus)
    const nodeLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
    nodeLayer.id = "wf-nodes-layer";
    svg.appendChild(nodeLayer);

    container.appendChild(svg);
    wfState.svgEl = svg;

    // Event listeners canvas — nœuds & connexions
    svg.addEventListener("mousedown", onCanvasMouseDown);
    svg.addEventListener("mousemove", onCanvasMouseMove);
    svg.addEventListener("mouseup", onCanvasMouseUp);
    svg.addEventListener("click", onCanvasClick);

    // Event listeners — zoom (molette) & pan (clic milieu/droit)
    svg.addEventListener("wheel", onCanvasWheel, { passive: false });
    svg.addEventListener("contextmenu", (e) => e.preventDefault());
    svg.addEventListener("dblclick", onCanvasDblClick);

    // Clavier
    document.addEventListener("keydown", onWfKeyDown);

    // Badge zoom
    updateZoomBadge();

    return svg;
}

/**
 * Ajoute un nœud au canvas
 */
function addWfNode(type, label, icon, x, y, meta = {}) {
    const id = `node-${wfState.nextId++}`;
    const node = {
        id,
        type,       // 'agent' | 'condition' | 'start' | 'end'
        label,
        icon,
        x, y,
        tier: meta.tier || 'automatique',
        agentName: meta.agentName || '',
        conditionExpr: meta.conditionExpr || '',
        ...meta
    };
    wfState.nodes.push(node);
    renderWfNode(node);
    return node;
}

/**
 * Rendu SVG d'un nœud
 */
function renderWfNode(node) {
    const layer = document.getElementById("wf-nodes-layer");
    if (!layer) return;

    // Supprimer l'ancien rendu si existant
    const old = document.getElementById(node.id);
    if (old) old.remove();

    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.id = node.id;
    g.setAttribute("class", `wf-node${wfState.selectedNode === node.id ? ' selected' : ''}`);
    g.setAttribute("transform", `translate(${node.x}, ${node.y})`);
    g.setAttribute("data-node-id", node.id);

    if (node.type === 'condition') {
        // Losange pour les conditions
        const half = WF_COND_SIZE / 2;
        g.innerHTML = `
            <polygon class="wf-node-body" points="${half},0 ${WF_COND_SIZE},${half} ${half},${WF_COND_SIZE} 0,${half}"
                     style="fill:rgba(18,22,33,0.92);stroke:var(--border-color);stroke-width:1.5;"/>
            <line x1="0" y1="0" x2="${WF_COND_SIZE}" y2="0" class="wf-node-bar condition" style="display:none;"/>
            <text x="${half}" y="${half + 4}" text-anchor="middle" class="wf-node-title" style="font-size:11px;">❓</text>
            <text x="${half}" y="${half + 18}" text-anchor="middle" class="wf-node-subtitle">${truncate(node.conditionExpr || 'if...', 10)}</text>
            <!-- Ports -->
            <circle class="wf-port wf-port-in" cx="${half}" cy="0" r="${WF_PORT_R}" data-port="in" data-node="${node.id}"/>
            <circle class="wf-port wf-port-out" cx="${WF_COND_SIZE}" cy="${half}" r="${WF_PORT_R}" data-port="out-true" data-node="${node.id}"/>
            <circle class="wf-port wf-port-out" cx="0" cy="${half}" r="${WF_PORT_R}" data-port="out-false" data-node="${node.id}" style="stroke:var(--error);fill:rgba(var(--error-rgb),0.3);"/>
            <text x="${WF_COND_SIZE + 12}" y="${half + 3}" style="fill:var(--success);font-size:9px;font-weight:600;">✓</text>
            <text x="-14" y="${half + 3}" style="fill:var(--error);font-size:9px;font-weight:600;">✗</text>
        `;
    } else {
        // Rectangle arrondi pour agents, start, end
        const w = WF_NODE_W;
        const h = WF_NODE_H;
        const barClass = node.type;

        g.innerHTML = `
            <rect class="wf-node-body" x="0" y="0" width="${w}" height="${h}"/>
            <rect class="wf-node-bar ${barClass}" x="0" y="0" width="${w}" height="4"/>
            <text x="36" y="30" class="wf-node-title">${truncate(node.label, 18)}</text>
            <text x="36" y="46" class="wf-node-subtitle">${node.type === 'agent' ? (node.tier || '–') : (node.type === 'start' ? 'Entrée' : 'Sortie')}</text>
            <text x="16" y="36" class="wf-node-icon" text-anchor="middle">${node.icon}</text>
            <!-- Port entrée (gauche) -->
            ${node.type !== 'start' ? `<circle class="wf-port wf-port-in" cx="0" cy="${h/2}" r="${WF_PORT_R}" data-port="in" data-node="${node.id}"/>` : ''}
            <!-- Port sortie (droite) -->
            ${node.type !== 'end' ? `<circle class="wf-port wf-port-out" cx="${w}" cy="${h/2}" r="${WF_PORT_R}" data-port="out" data-node="${node.id}"/>` : ''}
        `;
    }

    layer.appendChild(g);
}

/**
 * Ajoute une connexion entre deux nœuds
 */
function addWfConnection(fromNodeId, fromPort, toNodeId, toPort) {
    // Vérifier les doublons
    const exists = wfState.connections.find(c =>
        c.from === fromNodeId && c.fromPort === fromPort &&
        c.to === toNodeId && c.toPort === toPort
    );
    if (exists) return null;

    const id = `conn-${fromNodeId}-${toNodeId}-${Date.now()}`;
    const conn = { id, from: fromNodeId, fromPort, to: toNodeId, toPort };
    wfState.connections.push(conn);
    renderWfConnection(conn);
    return conn;
}

/**
 * Rendu SVG d'une connexion (courbe Bézier adaptative)
 */
function renderWfConnection(conn) {
    const layer = document.getElementById("wf-connections-layer");
    if (!layer) return;

    const old = document.getElementById(conn.id);
    if (old) old.remove();

    const fromPos = getPortPosition(conn.from, conn.fromPort);
    const toPos = getPortPosition(conn.to, conn.toPort);
    if (!fromPos || !toPos) return;

    // Déterminer la direction de chaque port pour les control points
    const fromDir = getPortDirection(conn.from, conn.fromPort);
    const toDir = getPortDirection(conn.to, conn.toPort);

    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.id = conn.id;
    path.setAttribute("class", `wf-connection${wfState.selectedConnection === conn.id ? ' selected' : ''}`);
    path.setAttribute("d", smartBezierPath(fromPos.x, fromPos.y, fromDir, toPos.x, toPos.y, toDir));
    path.setAttribute("data-conn-id", conn.id);
    path.addEventListener("click", (e) => {
        e.stopPropagation();
        selectWfConnection(conn.id);
    });

    layer.appendChild(path);
}

/**
 * Calcule le chemin Bézier adaptatif selon la direction des ports
 * Directions : 'right', 'left', 'up', 'down'
 */
function smartBezierPath(x1, y1, dir1, x2, y2, dir2) {
    const dist = Math.hypot(x2 - x1, y2 - y1);
    const cp = Math.max(60, Math.min(200, dist * 0.4));

    // Control point 1 : extension depuis le port de sortie
    let cp1x = x1, cp1y = y1;
    if (dir1 === 'right')  { cp1x = x1 + cp; }
    else if (dir1 === 'left')  { cp1x = x1 - cp; }
    else if (dir1 === 'down')  { cp1y = y1 + cp; }
    else if (dir1 === 'up')    { cp1y = y1 - cp; }

    // Control point 2 : approche vers le port d'entrée
    let cp2x = x2, cp2y = y2;
    if (dir2 === 'left')   { cp2x = x2 - cp; }
    else if (dir2 === 'right')  { cp2x = x2 + cp; }
    else if (dir2 === 'up')     { cp2y = y2 - cp; }
    else if (dir2 === 'down')   { cp2y = y2 + cp; }

    return `M ${x1} ${y1} C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${x2} ${y2}`;
}

/**
 * Retourne la direction d'un port ('right', 'left', 'up', 'down')
 */
function getPortDirection(nodeId, portName) {
    const node = wfState.nodes.find(n => n.id === nodeId);
    if (!node) return 'right';

    if (node.type === 'condition') {
        if (portName === 'in') return 'up';
        if (portName === 'out-true') return 'right';
        if (portName === 'out-false') return 'left';
    } else {
        if (portName === 'in') return 'left';
        if (portName === 'out') return 'right';
    }
    return 'right';
}

/**
 * Fallback simple pour la connexion temporaire (drag)
 */
function bezierPath(x1, y1, x2, y2) {
    const dx = Math.abs(x2 - x1);
    const cp = Math.max(50, dx * 0.4);
    return `M ${x1} ${y1} C ${x1 + cp} ${y1}, ${x2 - cp} ${y2}, ${x2} ${y2}`;
}

/**
 * Obtient la position absolue d'un port sur un nœud
 */
function getPortPosition(nodeId, portName) {
    const node = wfState.nodes.find(n => n.id === nodeId);
    if (!node) return null;

    if (node.type === 'condition') {
        const half = WF_COND_SIZE / 2;
        if (portName === 'in') return { x: node.x + half, y: node.y };
        if (portName === 'out-true') return { x: node.x + WF_COND_SIZE, y: node.y + half };
        if (portName === 'out-false') return { x: node.x, y: node.y + half };
    } else {
        if (portName === 'in') return { x: node.x, y: node.y + WF_NODE_H / 2 };
        if (portName === 'out') return { x: node.x + WF_NODE_W, y: node.y + WF_NODE_H / 2 };
    }
    return null;
}

/**
 * Rafraîchit toutes les connexions (après déplacement d'un nœud)
 */
function refreshConnections() {
    wfState.connections.forEach(conn => renderWfConnection(conn));
}

/**
 * Sélectionne un nœud
 */
function selectWfNode(nodeId) {
    wfState.selectedNode = nodeId;
    wfState.selectedConnection = null;
    // Re-render tous les nœuds pour la sélection
    wfState.nodes.forEach(n => renderWfNode(n));
    refreshConnections();
    // Ouvrir le panneau de propriétés
    openWfProps(nodeId);
}

/**
 * Sélectionne une connexion
 */
function selectWfConnection(connId) {
    wfState.selectedConnection = connId;
    wfState.selectedNode = null;
    wfState.nodes.forEach(n => renderWfNode(n));
    refreshConnections();
    closeWfProps();
}

/**
 * Désélectionne tout
 */
function deselectAll() {
    wfState.selectedNode = null;
    wfState.selectedConnection = null;
    wfState.nodes.forEach(n => renderWfNode(n));
    refreshConnections();
    closeWfProps();
}

/**
 * Supprime un nœud et ses connexions
 */
function deleteWfNode(nodeId) {
    wfState.nodes = wfState.nodes.filter(n => n.id !== nodeId);
    wfState.connections = wfState.connections.filter(c => c.from !== nodeId && c.to !== nodeId);
    const el = document.getElementById(nodeId);
    if (el) el.remove();
    // Supprimer les connexions visuelles orphelines
    wfState.connections.forEach(c => {
        const cel = document.getElementById(c.id);
        if (cel) cel.remove();
    });
    refreshConnections();
    deselectAll();
}

/**
 * Supprime une connexion
 */
function deleteWfConnection(connId) {
    wfState.connections = wfState.connections.filter(c => c.id !== connId);
    const el = document.getElementById(connId);
    if (el) el.remove();
    deselectAll();
}

/* ─── Event Handlers ─── */

function onCanvasMouseDown(e) {
    const target = e.target;

    // Clic milieu (button=1) ou clic droit (button=2) → début de pan
    if (e.button === 1 || e.button === 2) {
        e.preventDefault();
        e.stopPropagation();
        wfState.panning = true;
        wfState.panStart = { x: e.clientX, y: e.clientY };
        wfState.panViewStart = { x: wfState.viewport.x, y: wfState.viewport.y };
        wfState.canvasWrap?.classList.add('panning');
        return;
    }

    // Clic gauche (button=0) — priorité : port > nœud > fond (pan)
    if (e.button === 0) {
        // Clic sur un port → début de connexion
        if (target.classList.contains('wf-port-out')) {
            e.stopPropagation();
            const nodeId = target.getAttribute('data-node');
            const port = target.getAttribute('data-port');
            const pos = getPortPosition(nodeId, port);
            if (!pos) return;

            const tempLine = document.createElementNS("http://www.w3.org/2000/svg", "path");
            tempLine.setAttribute("class", "wf-connection-temp");
            tempLine.id = "wf-temp-connection";
            document.getElementById("wf-connections-layer").appendChild(tempLine);

            wfState.connecting = { fromNodeId: nodeId, fromPort: port, tempLine, startX: pos.x, startY: pos.y };
            return;
        }

        // Clic sur un nœud → début de drag
        const nodeGroup = target.closest('.wf-node');
        if (nodeGroup) {
            e.stopPropagation();
            const nodeId = nodeGroup.getAttribute('data-node-id');
            const node = wfState.nodes.find(n => n.id === nodeId);
            if (!node) return;

            selectWfNode(nodeId);

            const svgPt = getSvgPoint(e);
            wfState.dragging = nodeId;
            wfState.dragOffset = { x: svgPt.x - node.x, y: svgPt.y - node.y };
            return;
        }

        // Clic gauche sur le fond du canvas → début de pan
        // (aucun nœud ni port ciblé)
        e.preventDefault();
        wfState.panning = true;
        wfState.panStart = { x: e.clientX, y: e.clientY };
        wfState.panViewStart = { x: wfState.viewport.x, y: wfState.viewport.y };
        wfState.canvasWrap?.classList.add('panning');
        return;
    }
}

function onCanvasMouseMove(e) {
    // Pan (déplacement du viewport)
    if (wfState.panning) {
        const dx = (e.clientX - wfState.panStart.x) / wfState.viewport.zoom;
        const dy = (e.clientY - wfState.panStart.y) / wfState.viewport.zoom;
        wfState.viewport.x = wfState.panViewStart.x - dx;
        wfState.viewport.y = wfState.panViewStart.y - dy;
        updateViewBox();
        return;
    }

    // Drag de nœud
    if (wfState.dragging) {
        const svgPt = getSvgPoint(e);
        const node = wfState.nodes.find(n => n.id === wfState.dragging);
        if (!node) return;

        node.x = svgPt.x - wfState.dragOffset.x;
        node.y = svgPt.y - wfState.dragOffset.y;

        const g = document.getElementById(node.id);
        if (g) g.setAttribute("transform", `translate(${node.x}, ${node.y})`);

        refreshConnections();
    }

    // Création de connexion (ligne temporaire)
    if (wfState.connecting) {
        const svgPt = getSvgPoint(e);
        const { startX, startY, tempLine } = wfState.connecting;
        tempLine.setAttribute("d", bezierPath(startX, startY, svgPt.x, svgPt.y));
    }
}

function onCanvasMouseUp(e) {
    // Fin du pan
    if (wfState.panning) {
        wfState.panning = false;
        wfState.canvasWrap?.classList.remove('panning');
        return;
    }

    // Fin du drag de nœud
    if (wfState.dragging) {
        wfState.dragging = null;
    }

    // Fin de la création de connexion
    if (wfState.connecting) {
        const target = e.target;
        const { fromNodeId, fromPort, tempLine } = wfState.connecting;

        // Supprimer la ligne temporaire
        tempLine.remove();

        // Vérifier si on a lâché sur un port d'entrée
        if (target.classList.contains('wf-port-in')) {
            const toNodeId = target.getAttribute('data-node');
            const toPort = target.getAttribute('data-port');

            // Pas de self-connection
            if (toNodeId !== fromNodeId) {
                addWfConnection(fromNodeId, fromPort, toNodeId, toPort);
            }
        }

        wfState.connecting = null;
    }
}

function onCanvasClick(e) {
    // Clic sur le fond → désélection
    if (e.target === wfState.svgEl || e.target.tagName === 'rect' && !e.target.closest('.wf-node')) {
        deselectAll();
    }
}

function onWfKeyDown(e) {
    if (e.key === 'Delete' || e.key === 'Backspace') {
        // Ne pas interférer avec les inputs
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

        if (wfState.selectedNode) {
            deleteWfNode(wfState.selectedNode);
        } else if (wfState.selectedConnection) {
            deleteWfConnection(wfState.selectedConnection);
        }
    }
    if (e.key === 'Escape') {
        deselectAll();
        if (wfState.connecting) {
            wfState.connecting.tempLine.remove();
            wfState.connecting = null;
        }
    }
}

/* ─── Utilitaires SVG ─── */

function getSvgPoint(e) {
    const svg = wfState.svgEl;
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const ctm = svg.getScreenCTM().inverse();
    return pt.matrixTransform(ctm);
}

function truncate(str, max) {
    if (!str) return '';
    return str.length > max ? str.substring(0, max) + '…' : str;
}

/**
 * Efface tout le canvas et réinitialise l'état
 */
function clearWfCanvas() {
    const nodesLayer = document.getElementById("wf-nodes-layer");
    const connsLayer = document.getElementById("wf-connections-layer");
    if (nodesLayer) nodesLayer.innerHTML = '';
    if (connsLayer) connsLayer.innerHTML = '';
    wfState.nodes = [];
    wfState.connections = [];
    wfState.selectedNode = null;
    wfState.selectedConnection = null;
    wfState.nextId = 1;
    closeWfProps();
}

/* ═══════════════════════════════════════════════════════════════
   ZOOM & PAN — Navigation dans le canvas
   ═══════════════════════════════════════════════════════════════ */

/**
 * Zoom centré sur la position du curseur (molette)
 */
function onCanvasWheel(e) {
    e.preventDefault();
    const svg = wfState.svgEl;
    if (!svg) return;

    const vp = wfState.viewport;
    // Facteur de zoom réduit (1.05) pour un contrôle plus doux et progressif
    const zoomFactor = e.deltaY < 0 ? 1.05 : 1 / 1.05;
    const newZoom = Math.max(vp.minZoom, Math.min(vp.maxZoom, vp.zoom * zoomFactor));

    // Point sous le curseur en coordonnées SVG (avant zoom)
    const pt = getSvgPoint(e);

    // Ajuster le viewport pour que le point sous le curseur reste fixe
    const ratio = newZoom / vp.zoom;
    vp.x = pt.x - (pt.x - vp.x) / ratio;
    vp.y = pt.y - (pt.y - vp.y) / ratio;
    vp.zoom = newZoom;

    updateViewBox();
    updateZoomBadge();
}

/**
 * Double-clic sur le fond → recentrer et reset zoom
 */
function onCanvasDblClick(e) {
    // Ne pas interférer avec les nœuds
    if (e.target.closest('.wf-node')) return;
    fitToContent();
}

/**
 * Met à jour le viewBox du SVG selon le viewport actuel
 */
function updateViewBox() {
    const svg = wfState.svgEl;
    if (!svg) return;
    const vp = wfState.viewport;

    const viewW = vp.w / vp.zoom;
    const viewH = vp.h / vp.zoom;
    svg.setAttribute("viewBox", `${vp.x} ${vp.y} ${viewW} ${viewH}`);
}

/**
 * Remet le viewport par défaut (zoom=1, x=0, y=0)
 */
function resetViewport() {
    const vp = wfState.viewport;
    vp.x = 0;
    vp.y = 0;
    vp.zoom = 1;
    updateViewBox();
    updateZoomBadge();
    showToast('info', 'Vue réinitialisée.');
}

/**
 * Zoom in programmatique (bouton toolbar)
 */
function wfZoomIn() {
    const vp = wfState.viewport;
    // Facteur bouton réduit (1.2) pour cohérence avec la molette
    const newZoom = Math.min(vp.maxZoom, vp.zoom * 1.2);
    // Zoom centré au milieu du viewport
    const cx = vp.x + (vp.w / vp.zoom) / 2;
    const cy = vp.y + (vp.h / vp.zoom) / 2;
    const ratio = newZoom / vp.zoom;
    vp.x = cx - (cx - vp.x) / ratio;
    vp.y = cy - (cy - vp.y) / ratio;
    vp.zoom = newZoom;
    updateViewBox();
    updateZoomBadge();
}

/**
 * Zoom out programmatique (bouton toolbar)
 */
function wfZoomOut() {
    const vp = wfState.viewport;
    const newZoom = Math.max(vp.minZoom, vp.zoom / 1.2);
    const cx = vp.x + (vp.w / vp.zoom) / 2;
    const cy = vp.y + (vp.h / vp.zoom) / 2;
    const ratio = newZoom / vp.zoom;
    vp.x = cx - (cx - vp.x) / ratio;
    vp.y = cy - (cy - vp.y) / ratio;
    vp.zoom = newZoom;
    updateViewBox();
    updateZoomBadge();
}

/**
 * Ajuste le viewport pour afficher tous les nœuds (fit-to-content)
 */
function fitToContent() {
    if (wfState.nodes.length === 0) { resetViewport(); return; }

    const vp = wfState.viewport;
    const pad = 80;

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    wfState.nodes.forEach(n => {
        const w = n.type === 'condition' ? WF_COND_SIZE : WF_NODE_W;
        const h = n.type === 'condition' ? WF_COND_SIZE : WF_NODE_H;
        minX = Math.min(minX, n.x);
        minY = Math.min(minY, n.y);
        maxX = Math.max(maxX, n.x + w);
        maxY = Math.max(maxY, n.y + h);
    });

    const contentW = (maxX - minX) + pad * 2;
    const contentH = (maxY - minY) + pad * 2;

    const zoomX = vp.w / contentW;
    const zoomY = vp.h / contentH;
    vp.zoom = Math.max(vp.minZoom, Math.min(vp.maxZoom, Math.min(zoomX, zoomY)));

    const viewW = vp.w / vp.zoom;
    const viewH = vp.h / vp.zoom;
    vp.x = minX - pad - (viewW - contentW) / 2;
    vp.y = minY - pad - (viewH - contentH) / 2;

    updateViewBox();
    updateZoomBadge();
    showToast('info', `Vue ajustée (${Math.round(vp.zoom * 100)}%).`);
}

/**
 * Met à jour le badge zoom dans le coin du canvas
 */
function updateZoomBadge() {
    let badge = document.getElementById('wf-zoom-badge');
    if (!badge && wfState.canvasWrap) {
        badge = document.createElement('div');
        badge.id = 'wf-zoom-badge';
        badge.className = 'wf-zoom-indicator';
        wfState.canvasWrap.appendChild(badge);
    }
    if (badge) {
        badge.textContent = `${Math.round(wfState.viewport.zoom * 100)}%`;
    }
}
