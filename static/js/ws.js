/* ============================================================
   WS.JS — D4 — Client WebSocket bidirectionnel
   Canal temps réel pour les actions utilisateur (Stop, Approval,
   Input) avec reconnexion automatique et backoff exponentiel.
   ============================================================ */

let wsConnection = null;
let wsReconnectAttempts = 0;
const WS_MAX_RECONNECT = 10;
const WS_BASE_DELAY = 1000; // Délai initial en ms

/**
 * Initialise la connexion WebSocket vers le serveur
 */
function initWebSocket() {
    // Construire l'URL WS à partir de la page courante
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    try {
        wsConnection = new WebSocket(wsUrl);

        wsConnection.onopen = () => {
            console.log('[WS] Connecté.');
            wsReconnectAttempts = 0;
            // Ping initial pour stabiliser
            wsSend({ type: 'ping' });
        };

        wsConnection.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                handleWSMessage(msg);
            } catch (err) {
                console.warn('[WS] Message non-JSON reçu :', event.data);
            }
        };

        wsConnection.onclose = (event) => {
            console.log(`[WS] Déconnecté (code ${event.code}). Tentative de reconnexion...`);
            wsConnection = null;
            scheduleReconnect();
        };

        wsConnection.onerror = (err) => {
            console.warn('[WS] Erreur :', err);
            // onclose sera déclenché automatiquement
        };

    } catch (err) {
        console.warn('[WS] Impossible d\'initialiser :', err);
        scheduleReconnect();
    }
}

/**
 * Planifie une reconnexion avec backoff exponentiel
 */
function scheduleReconnect() {
    if (wsReconnectAttempts >= WS_MAX_RECONNECT) {
        console.warn(`[WS] Abandon après ${WS_MAX_RECONNECT} tentatives. Fallback SSE uniquement.`);
        return;
    }

    const delay = Math.min(WS_BASE_DELAY * Math.pow(2, wsReconnectAttempts), 30000);
    wsReconnectAttempts++;

    console.log(`[WS] Reconnexion dans ${delay}ms (tentative ${wsReconnectAttempts}/${WS_MAX_RECONNECT})...`);
    setTimeout(initWebSocket, delay);
}

/**
 * Envoie un message JSON via WebSocket
 * @param {Object} msg — Message à envoyer
 * @returns {boolean} — true si envoyé, false sinon
 */
function wsSend(msg) {
    if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
        wsConnection.send(JSON.stringify(msg));
        return true;
    }
    console.warn('[WS] Non connecté — message non envoyé :', msg);
    return false;
}

/**
 * Traite les messages reçus depuis le serveur WebSocket
 */
function handleWSMessage(msg) {
    const type = msg.type || '';

    switch (type) {
        case 'pong':
            // Réponse au ping — connexion vivante
            break;

        case 'ack':
            // Accusé de réception d'une action
            console.log(`[WS] ACK reçu : ${msg.action} → ${msg.status || 'ok'}`);
            break;

        case 'approval_required':
            // Approval Gate reçue via WS (en parallèle du SSE)
            if (typeof showApprovalGate === 'function') {
                showApprovalGate(msg.data || msg);
            }
            break;

        case 'sandbox_flushed':
            // Sandbox vidé — rafraîchir la vue
            if (typeof refreshSandboxView === 'function') {
                refreshSandboxView();
            }
            break;

        case 'thinking_stream':
            // CoT via WS
            if (typeof appendCoTText === 'function' && msg.data) {
                appendCoTText(msg.data.text || '', msg.data.agent_name || 'agent');
            }
            break;

        case 'error':
            console.warn('[WS] Erreur serveur :', msg.message);
            break;

        default:
            // Messages inconnus — log pour debug
            console.log('[WS] Message non géré :', msg);
    }
}

/**
 * Envoie un Stop via WebSocket (plus rapide que le REST)
 */
function wsStop() {
    return wsSend({ type: 'stop' });
}

/**
 * Envoie une réponse d'approbation via WebSocket
 */
function wsApproval(approvalId, approved) {
    return wsSend({ type: 'approval', approval_id: approvalId, approved: approved });
}

// Initialiser la connexion WS au chargement de la page
// Le SSE reste actif en parallèle (dual-channel pour la résilience)
document.addEventListener('DOMContentLoaded', () => {
    // Petit délai pour laisser le SSE se connecter d'abord
    setTimeout(initWebSocket, 500);
});
