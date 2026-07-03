/* ============================================================
   CHAT.JS — Onglet conversationnel avec historique, Markdown et cartes HA
   Interface type ChatGPT/Claude avec intégration domotique active
   ============================================================ */

// ── État global des sessions de chat ──
let chatSessions = [];
let activeSessionId = null;
let chatIsStreaming = false;

/**
 * Rendu principal de l'onglet Chat
 */
function renderChat(container) {
    container.innerHTML = `
        <div class="chat-container">
            <div class="chat-sidebar">
                <div class="chat-sidebar-header">
                    <h3>💬 Sessions</h3>
                    <button class="btn-new-chat" onclick="chatNewSession()">
                        <span>+</span> Nouvelle
                    </button>
                </div>
                <div class="chat-session-list" id="chat-session-list">
                    <!-- Rendu dynamiquement -->
                </div>
            </div>
            <div class="chat-main">
                <div class="chat-messages" id="chat-messages">
                    <!-- Rendu dynamiquement -->
                </div>
                <div class="chat-input-area">
                    <div class="chat-input-wrapper">
                        <textarea 
                            id="chat-input" 
                            placeholder="Tapez votre message... (Entrée pour envoyer, Shift+Entrée pour un saut de ligne)"
                            rows="1"
                            onkeydown="chatHandleKeydown(event)"
                            oninput="chatAutoResize(this)"
                        ></textarea>
                        <button class="chat-send-btn" id="chat-send-btn" onclick="chatSendMessage()">
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M22 2L11 13"></path>
                                <path d="M22 2L15 22L11 13L2 9L22 2Z"></path>
                            </svg>
                        </button>
                    </div>
                    <div class="chat-input-footer">
                        <span class="chat-model-indicator" id="chat-model-indicator">🧠 Routage automatique</span>
                        <span class="chat-char-count" id="chat-char-count">0</span>
                    </div>
                </div>
            </div>
        </div>
    `;

    // Charger les sessions et les messages persistés
    chatLoadHistory();
}

/**
 * Charge l'historique des sessions et messages depuis localStorage
 */
function chatLoadHistory() {
    try {
        const storedSessions = localStorage.getItem('chat_sessions');
        const storedActive = localStorage.getItem('chat_active_session_id');

        if (storedSessions) {
            chatSessions = JSON.parse(storedSessions);
        }

        // Si aucune session n'existe, on en initialise une par défaut
        if (chatSessions.length === 0) {
            const defaultSession = {
                id: `session-${Date.now()}`,
                title: "Nouvelle discussion",
                messages: []
            };
            chatSessions.push(defaultSession);
            activeSessionId = defaultSession.id;
        } else {
            activeSessionId = storedActive || chatSessions[0].id;
            // Sécurité si l'id actif stocké n'est pas dans la liste
            if (!chatSessions.find(s => s.id === activeSessionId)) {
                activeSessionId = chatSessions[0].id;
            }
        }

        localStorage.setItem('chat_active_session_id', activeSessionId);
        localStorage.setItem('chat_sessions', JSON.stringify(chatSessions));

        // Rendu
        renderChatSessionList();
        renderActiveSessionMessages();

    } catch (err) {
        console.error("Erreur lors du chargement de l'historique :", err);
        showToast('error', 'Impossible de charger l\'historique du chat');
    }
}

/**
 * Sauvegarde les sessions dans localStorage
 */
function chatSaveSessions() {
    try {
        localStorage.setItem('chat_sessions', JSON.stringify(chatSessions));
        localStorage.setItem('chat_active_session_id', activeSessionId);
    } catch (err) {
        console.error("Erreur de sauvegarde localStorage :", err);
    }
}

/**
 * Rendu de la barre latérale des sessions
 */
function renderChatSessionList() {
    const list = document.getElementById('chat-session-list');
    if (!list) return;

    list.innerHTML = chatSessions.map(session => `
        <div class="chat-session-item ${session.id === activeSessionId ? 'active' : ''}" 
             onclick="chatSwitchSession('${session.id}')" 
             style="position:relative; display:flex; justify-content:space-between; align-items:center; padding-right:32px;">
            <div style="display:flex; align-items:center; gap:8px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1;">
                <span class="session-icon">💬</span>
                <span class="session-title" title="${session.title}">${session.title}</span>
            </div>
            ${chatSessions.length > 1 ? `
                <span onclick="event.stopPropagation(); chatDeleteSession('${session.id}')" 
                      style="position:absolute; right:8px; opacity:0.6; cursor:pointer; font-weight:bold; font-size: 0.8rem; padding: 2px 6px;" 
                      title="Supprimer la session">✕</span>
            ` : ''}
        </div>
    `).join('');
}

/**
 * Rendu des messages de la session active
 */
function renderActiveSessionMessages() {
    const container = document.getElementById('chat-messages');
    if (!container) return;

    const session = chatSessions.find(s => s.id === activeSessionId);
    if (!session || session.messages.length === 0) {
        // Écran d'accueil/bienvenue
        container.innerHTML = `
            <div class="chat-welcome">
                <div class="chat-welcome-icon">🤖</div>
                <h2>Moteur Multi-Agents V8</h2>
                <p>Posez une question, lancez une tâche complexe, ou pilotez votre domotique.</p>
                <div class="chat-suggestions">
                    <button class="chat-suggestion" onclick="chatSendSuggestion('Quel est l\\'état de mes capteurs de température ?')">
                        🌡️ État des capteurs
                    </button>
                    <button class="chat-suggestion" onclick="chatSendSuggestion('Analyse le fichier esphome/tab5.yaml et propose des optimisations')">
                        📝 Analyser un fichier
                    </button>
                    <button class="chat-suggestion" onclick="chatSendSuggestion('Allume la lumière du salon et ouvre les volets')">
                        💡 Allumer & Ouvrir volets
                    </button>
                </div>
            </div>
        `;
        return;
    }

    container.innerHTML = '';
    
    // Injecter les messages existants un par un
    session.messages.forEach(msg => {
        appendMessageHtml(msg.role, msg.content, msg.id, msg.timestamp);
    });

    // Initialiser les cartes Home Assistant après insertion
    initHomeAssistantCards(container);

    container.scrollTop = container.scrollHeight;
}

/**
 * Basculer vers une autre session
 */
function chatSwitchSession(sessionId) {
    if (chatIsStreaming) return;
    activeSessionId = sessionId;
    chatSaveSessions();
    renderChatSessionList();
    renderActiveSessionMessages();
}

/**
 * Créer une nouvelle session
 */
function chatNewSession() {
    if (chatIsStreaming) return;
    const newSession = {
        id: `session-${Date.now()}`,
        title: "Nouvelle discussion",
        messages: []
    };
    chatSessions.unshift(newSession);
    activeSessionId = newSession.id;
    chatSaveSessions();
    renderChatSessionList();
    renderActiveSessionMessages();
}

/**
 * Supprimer une session
 */
function chatDeleteSession(sessionId) {
    if (chatIsStreaming) return;
    if (chatSessions.length <= 1) return;

    chatSessions = chatSessions.filter(s => s.id !== sessionId);
    if (activeSessionId === sessionId) {
        activeSessionId = chatSessions[0].id;
    }
    chatSaveSessions();
    renderChatSessionList();
    renderActiveSessionMessages();
}

/**
 * Envoi d'un message utilisateur
 */
async function chatSendMessage() {
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text || chatIsStreaming) return;

    // Masquer l'accueil si premier message
    const container = document.getElementById('chat-messages');
    const welcome = container?.querySelector('.chat-welcome');
    if (welcome) welcome.style.display = 'none';

    // Ajouter le message utilisateur
    const userMsgId = `msg-${Date.now()}-${Math.random().toString(36).substr(2, 6)}`;
    chatAddMessage('user', text, userMsgId);
    input.value = '';
    chatAutoResize(input);

    // Mettre à jour le titre de la session au premier message utilisateur
    const session = chatSessions.find(s => s.id === activeSessionId);
    if (session && session.messages.length === 1) {
        session.title = text.length > 25 ? text.substring(0, 22) + "..." : text;
        renderChatSessionList();
    }

    // Indicateur de streaming
    chatIsStreaming = true;
    const sendBtn = document.getElementById('chat-send-btn');
    if (sendBtn) sendBtn.classList.add('streaming');

    // Ajouter un placeholder pour la réponse de l'assistant
    const assistantId = `msg-${Date.now()}-${Math.random().toString(36).substr(2, 6)}`;
    chatAddMessage('assistant', '', assistantId, true);

    try {
        const response = await fetch('/api/execute/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_prompt: text, source: { type: 'web' } }),
        });

        if (response.status === 409) {
            chatUpdateMessage(assistantId, '⏳ Une exécution est déjà en cours. Attendez sa fin ou arrêtez-la.');
            return;
        }

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        let responseText = '';
        let agentsUsed = [];
        let thinkingContainer = null;
        let thinkingContentEl = null;

        // Met à jour la boîte de pensée (Chain of Thought)
        function logThinking(type, details, agentName = 'system') {
            const assistantMsgEl = document.getElementById(assistantId);
            if (!assistantMsgEl) return;

            if (!thinkingContainer) {
                const contentContainer = assistantMsgEl.querySelector('.chat-message-content');
                if (contentContainer) {
                    thinkingContainer = document.createElement('details');
                    thinkingContainer.className = 'chat-thinking-details';
                    thinkingContainer.open = true;
                    thinkingContainer.innerHTML = `
                        <summary class="chat-thinking-summary">🧠 Réflexion &amp; Étapes du Moteur (<span class="chat-thinking-agent-name">${agentName}</span>)</summary>
                        <div class="chat-thinking-content"></div>
                    `;
                    const body = assistantMsgEl.querySelector('.chat-message-body');
                    contentContainer.insertBefore(thinkingContainer, body);
                    thinkingContentEl = thinkingContainer.querySelector('.chat-thinking-content');
                }
            }

            if (thinkingContentEl) {
                const agentNameEl = thinkingContainer.querySelector('.chat-thinking-agent-name');
                if (agentNameEl && agentName && agentName !== 'system') {
                    agentNameEl.textContent = agentName;
                }

                let logMsg = '';
                if (type === 'thinking_stream') {
                    logMsg = details;
                } else if (type === 'agent_started') {
                    logMsg = `\n\n[▶ Agent ${agentName.toUpperCase()} démarré : ${details}]\n`;
                } else if (type === 'agent_completed') {
                    logMsg = `\n[✔ Agent ${agentName.toUpperCase()} terminé (Statut: ${details})]\n`;
                } else if (type === 'task_started') {
                    logMsg = `\n  • Tâche lancée : ${details} (Cible: ${agentName})\n`;
                } else if (type === 'task_completed') {
                    logMsg = `  • Tâche terminée (${details}) : ${this_task_obj(details, agentName)}\n`;
                } else {
                    logMsg = `\n[${type.toUpperCase()}] ${details}\n`;
                }

                thinkingContentEl.textContent += logMsg;
                thinkingContentEl.scrollTop = thinkingContentEl.scrollHeight;
            }
        }

        function this_task_obj(status, data) {
            return data || '';
        }

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop(); // Garder la ligne incomplète

            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed || !trimmed.startsWith('data: ')) continue;

                const dataStr = trimmed.slice(6);
                if (dataStr === '[DONE]') continue;

                try {
                    const parsed = JSON.parse(dataStr);

                    if (parsed.type === 'error') {
                        responseText = `❌ Erreur : ${parsed.message || 'Erreur inconnue'}`;
                        chatUpdateMessage(assistantId, responseText);
                    } 
                    else if (parsed.type === 'thinking_stream' && parsed.data) {
                        logThinking('thinking_stream', parsed.data.text || '', parsed.data.agent_name);
                    } 
                    else if (parsed.type === 'agent_started' && parsed.data) {
                        logThinking('agent_started', parsed.data.task_objective || '', parsed.data.agent_name);
                    } 
                    else if (parsed.type === 'agent_completed' && parsed.data) {
                        logThinking('agent_completed', parsed.data.status || '', parsed.data.agent_name);
                    }
                    else if (parsed.type === 'task_started' && parsed.data) {
                        logThinking('task_started', parsed.data.task_objective || '', parsed.data.target_agent);
                    }
                    else if (parsed.type === 'task_completed' && parsed.data) {
                        logThinking('task_completed', parsed.data.status || '', parsed.data.task_objective);
                    }
                    else if (parsed.type === 'done') {
                        responseText = parsed.response || '';
                        agentsUsed = parsed.agents_used || [];
                        
                        // Mettre à jour la session
                        const modelIndicator = document.getElementById('chat-model-indicator');
                        if (modelIndicator && parsed.session_id) {
                            modelIndicator.textContent = `📊 Session: ${parsed.session_id}`;
                        }
                    }
                } catch (e) {
                    console.error('[SSE] Erreur parsing chunk data:', dataStr, e);
                }
            }
        }

        // Si la pensée a été affichée, la replier
        if (thinkingContainer) {
            thinkingContainer.removeAttribute('open');
        }

        // Ajouter les agents impliqués
        if (agentsUsed && agentsUsed.length > 0) {
            const agentsBadges = agentsUsed.map(a => `\`${a}\``).join(' → ');
            responseText += `\n\n---\n*Agents impliqués : ${agentsBadges}*`;
        }

        // Rendu final
        chatUpdateMessage(assistantId, responseText || '✅ Exécution complétée.');
    } finally {
        chatIsStreaming = false;
        if (sendBtn) sendBtn.classList.remove('streaming');
    }
}

/**
 * Envoi d'une suggestion prédéfinie
 */
function chatSendSuggestion(text) {
    const input = document.getElementById('chat-input');
    if (input) {
        input.value = text;
        chatSendMessage();
    }
}

/**
 * Ajout d'un message dans l'état local et appel de l'affichage HTML
 */
function chatAddMessage(role, content, msgId, isStreaming = false) {
    const timestamp = Date.now();
    
    // Mettre à jour l'état local
    const session = chatSessions.find(s => s.id === activeSessionId);
    if (session) {
        session.messages.push({ role, content, id: msgId, timestamp });
        chatSaveSessions();
    }

    appendMessageHtml(role, content, msgId, timestamp, isStreaming);
}

/**
 * Injection physique du message dans le DOM
 */
function appendMessageHtml(role, content, msgId, timestamp, isStreaming = false) {
    const container = document.getElementById('chat-messages');
    if (!container) return;

    const msgEl = document.createElement('div');
    msgEl.className = `chat-message chat-message-${role}`;
    msgEl.id = msgId;

    const avatar = role === 'user' ? '👤' : '🤖';
    const name = role === 'user' ? 'Vous' : 'Moteur V8';
    const timeString = new Date(timestamp).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });

    msgEl.innerHTML = `
        <div class="chat-message-avatar">${avatar}</div>
        <div class="chat-message-content">
            <div class="chat-message-header">
                <span class="chat-message-name">${name}</span>
                <span class="chat-message-time">${timeString}</span>
            </div>
            <div class="chat-message-body ${isStreaming ? 'streaming' : ''}">
                ${isStreaming ? '<div class="chat-typing-indicator"><span></span><span></span><span></span></div>' : chatRenderMarkdown(content)}
            </div>
        </div>
    `;

    container.appendChild(msgEl);
    container.scrollTop = container.scrollHeight;
}

/**
 * Met à jour un message assistant existant (streaming terminé)
 */
function chatUpdateMessage(msgId, content) {
    const msgEl = document.getElementById(msgId);
    if (!msgEl) return;

    const body = msgEl.querySelector('.chat-message-body');
    if (body) {
        body.classList.remove('streaming');
        body.innerHTML = chatRenderMarkdown(content);
        
        // Initialiser les cartes HA détectées dans ce nouveau contenu
        initHomeAssistantCards(msgEl);
    }

    // Mettre à jour l'état local
    const session = chatSessions.find(s => s.id === activeSessionId);
    if (session) {
        const msg = session.messages.find(m => m.id === msgId);
        if (msg) msg.content = content;
        chatSaveSessions();
    }

    // Scroll vers le bas
    const container = document.getElementById('chat-messages');
    if (container) container.scrollTop = container.scrollHeight;
}

/**
 * Rendu Markdown enrichi avec détection de cartes Home Assistant
 */
function chatRenderMarkdown(text) {
    if (!text) return '';

    let haCards = [];
    
    // 1. Détection format bloc : ```ha-card\nentity: domain.name\n```
    let cleanText = text.replace(/```ha-card\n([\s\S]*?)```/g, (match, content) => {
        let entityId = '';
        const lines = content.split('\n');
        for (const line of lines) {
            const parts = line.split(':');
            if (parts.length >= 2 && (parts[0].trim() === 'entity' || parts[0].trim() === 'entity_id')) {
                entityId = parts[1].trim();
                break;
            }
        }
        if (entityId) {
            const idx = haCards.length;
            haCards.push(entityId);
            return `__HA_CARD_PLACEHOLDER_${idx}__`;
        }
        return match;
    });
    
    // 2. Détection format inline : [HA:domain.name] ou [HA:domain.name:on]
    cleanText = cleanText.replace(/\[HA:([a-zA-Z0-9\._\-]+)(?::[a-zA-Z0-9]+)?\]/g, (match, entityId) => {
        const idx = haCards.length;
        haCards.push(entityId);
        return `__HA_CARD_PLACEHOLDER_${idx}__`;
    });

    // 3. Parser Markdown basique
    let html = cleanText
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre class="chat-code-block"><code class="language-$1">$2</code></pre>')
        .replace(/`([^`]+)`/g, '<code class="chat-code-inline">$1</code>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/^### (.+)$/gm, '<h4>$1</h4>')
        .replace(/^## (.+)$/gm, '<h3>$1</h3>')
        .replace(/^# (.+)$/gm, '<h2>$1</h2>')
        .replace(/^[\-\*] (.+)$/gm, '<li>$1</li>')
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" class="chat-link">$1</a>')
        .replace(/\n\n/g, '</p><p>')
        .replace(/\n/g, '<br>');

    html = html.replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>');
    
    // 4. Injecter les placeholders de cartes HA
    haCards.forEach((entityId, index) => {
        html = html.replace(`__HA_CARD_PLACEHOLDER_${index}__`, `
            <div class="ha-card-placeholder" data-entity-id="${entityId}">
                <div class="ha-card-container">
                    <div class="ha-card-header">
                        <div class="ha-card-info">
                            <span class="ha-card-icon">⏳</span>
                            <div>
                                <div class="ha-card-title">Chargement...</div>
                                <div class="ha-card-entity-id">${entityId}</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `);
    });

    return `<p>${html}</p>`;
}

/**
 * Initialise et peuple asynchronement les cartes Home Assistant
 */
async function initHomeAssistantCards(element) {
    const placeholders = element.querySelectorAll('.ha-card-placeholder');
    const promises = Array.from(placeholders).map(async (placeholder) => {
        const entityId = placeholder.getAttribute('data-entity-id');
        if (!entityId) return;

        try {
            const resp = await fetch(`/api/ha/state/${entityId}`);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const stateData = await resp.json();

            // Créer le HTML de la carte
            const cardHtml = buildHaCardHtml(entityId, stateData);
            const tempDiv = document.createElement('div');
            tempDiv.innerHTML = cardHtml;
            const cardEl = tempDiv.firstElementChild;
            
            // Remplacer le placeholder
            placeholder.replaceWith(cardEl);

            // Attacher les listeners
            attachHaCardListeners(cardEl, entityId, stateData);

        } catch (err) {
            placeholder.innerHTML = `
                <div class="ha-card-container error">
                    <div class="ha-card-header">
                        <div class="ha-card-info">
                            <span class="ha-card-icon" style="background:rgba(239,68,68,0.1);color:var(--error);">⚠️</span>
                            <div>
                                <div class="ha-card-title">Appareil hors ligne</div>
                                <div class="ha-card-entity-id">${entityId}</div>
                            </div>
                        </div>
                    </div>
                    <div class="ha-card-body" style="padding-top:4px;">
                        <span style="font-size:0.75rem;color:var(--error);">${err.message === 'HTTP 404' ? 'Entité introuvable dans Home Assistant' : 'Impossible de contacter Home Assistant'}</span>
                    </div>
                </div>
            `;
        }
    });
    await Promise.all(promises);
}

/**
 * Génère le code HTML d'une carte Home Assistant en fonction du domaine
 */
function buildHaCardHtml(entityId, stateData) {
    const domain = entityId.split('.')[0];
    const attributes = stateData.attributes || {};
    const friendlyName = attributes.friendly_name || entityId;
    const state = stateData.state;
    
    // Déterminer le statut actif
    const isActive = state === 'on' || state === 'open' || (domain === 'cover' && state !== 'closed' && state !== 'unknown');
    
    // Icônes & Libellés adaptés
    let icon = '🔌';
    if (domain === 'light') icon = '💡';
    else if (domain === 'cover') icon = '🪟';
    else if (domain === 'sensor' || domain === 'binary_sensor') {
        if (entityId.includes('temp')) icon = '🌡️';
        else if (entityId.includes('humidity') || entityId.includes('moisture') || entityId.includes('pot') || entityId.includes('plant')) icon = '🪴';
        else if (entityId.includes('battery')) icon = '🔋';
        else icon = '📊';
    }

    let stateLabel = state === 'on' ? 'Allumé' : (state === 'off' ? 'Éteint' : state);
    if (domain === 'cover') {
        stateLabel = state === 'open' ? 'Ouvert' : (state === 'closed' ? 'Fermé' : state);
    }
    if (attributes.unit_of_measurement) {
        stateLabel = `${state} ${attributes.unit_of_measurement}`;
    }

    let html = `
        <div class="ha-card-container ${domain}" id="ha-card-${entityId.replace('.', '-')}">
            <div class="ha-card-header">
                <div class="ha-card-info">
                    <span class="ha-card-icon ${isActive ? 'active' : ''}">${icon}</span>
                    <div>
                        <div class="ha-card-title">${friendlyName}</div>
                        <div class="ha-card-entity-id">${entityId}</div>
                    </div>
                </div>
                <div class="ha-card-state ${isActive ? 'active' : ''}">${stateLabel}</div>
            </div>
            <div class="ha-card-body">
    `;

    // ── Contrôles pour lumières et interrupteurs ──
    if (domain === 'light' || domain === 'switch') {
        html += `
            <div class="ha-card-controls">
                <label class="ha-switch">
                    <input type="checkbox" id="ha-switch-${entityId.replace('.', '-')}" ${state === 'on' ? 'checked' : ''}>
                    <span class="ha-slider"></span>
                </label>
            </div>
        `;

        if (domain === 'light' && 'brightness' in attributes) {
            const currentBright = Math.round((attributes.brightness / 255) * 100);
            html += `
                <div class="ha-dimmer-wrap" style="margin-top: 6px;">
                    <span style="font-size:0.7rem;color:var(--text-muted);">🔆</span>
                    <input type="range" class="ha-dimmer-input" id="ha-dimmer-${entityId.replace('.', '-')}" min="0" max="100" value="${currentBright}">
                    <span style="font-size:0.7rem;color:var(--text-muted);width:24px;text-align:right;" id="ha-dimmer-val-${entityId.replace('.', '-')}">${currentBright}%</span>
                </div>
            `;
        }
    } 
    // ── Contrôles pour volets ──
    else if (domain === 'cover') {
        html += `
            <div class="ha-card-controls">
                <div class="ha-cover-buttons">
                    <button class="ha-cover-btn" id="ha-cover-open-${entityId.replace('.', '-')}" ${state === 'open' ? 'disabled' : ''}>🔼 Monter</button>
                    <button class="ha-cover-btn" id="ha-cover-stop-${entityId.replace('.', '-')}">Stop</button>
                    <button class="ha-cover-btn" id="ha-cover-close-${entityId.replace('.', '-')}" ${state === 'closed' ? 'disabled' : ''}>🔽 Descendre</button>
                </div>
            </div>
        `;
    }

    html += `
            </div>
        </div>
    `;
    return html;
}

/**
 * Attache les écouteurs de clics et sliders pour agir sur Home Assistant
 */
function attachHaCardListeners(cardEl, entityId, stateData) {
    const domain = entityId.split('.')[0];
    const idKey = entityId.replace('.', '-');

    if (domain === 'light' || domain === 'switch') {
        const checkbox = cardEl.querySelector(`#ha-switch-${idKey}`);
        if (checkbox) {
            checkbox.addEventListener('change', async (e) => {
                const service = e.target.checked ? 'turn_on' : 'turn_off';
                try {
                    await fetch('/api/ha/control', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ entity_id: entityId, service: service })
                    });
                    showToast('success', `${entityId} ${e.target.checked ? 'allumé' : 'éteint'}`);
                    
                    // Màj visuelle locale rapide
                    const stateText = cardEl.querySelector('.ha-card-state');
                    const iconEl = cardEl.querySelector('.ha-card-icon');
                    if (stateText) stateText.textContent = e.target.checked ? 'Allumé' : 'Éteint';
                    if (iconEl) {
                        if (e.target.checked) iconEl.classList.add('active');
                        else iconEl.classList.remove('active');
                    }
                } catch (err) {
                    showToast('error', `Échec de commande pour ${entityId}`);
                    e.target.checked = !e.target.checked;
                }
            });
        }

        const dimmer = cardEl.querySelector(`#ha-dimmer-${idKey}`);
        if (dimmer) {
            const dimmerVal = cardEl.querySelector(`#ha-dimmer-val-${idKey}`);
            dimmer.addEventListener('input', (e) => {
                if (dimmerVal) dimmerVal.textContent = `${e.target.value}%`;
            });
            dimmer.addEventListener('change', async (e) => {
                const brightness = Math.round((e.target.value / 100) * 255);
                try {
                    await fetch('/api/ha/control', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            entity_id: entityId,
                            service: 'turn_on',
                            service_data: { brightness: brightness }
                        })
                    });
                    showToast('success', `${entityId} : luminosité à ${e.target.value}%`);
                } catch (err) {
                    showToast('error', `Impossible de varier ${entityId}`);
                }
            });
        }
    } 
    else if (domain === 'cover') {
        const btnOpen = cardEl.querySelector(`#ha-cover-open-${idKey}`);
        const btnClose = cardEl.querySelector(`#ha-cover-close-${idKey}`);
        const btnStop = cardEl.querySelector(`#ha-cover-stop-${idKey}`);

        const callCoverService = async (service, toastMsg) => {
            try {
                await fetch('/api/ha/control', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ entity_id: entityId, service: service })
                });
                showToast('success', toastMsg);
            } catch (err) {
                showToast('error', `Erreur sur le volet ${entityId}`);
            }
        };

        if (btnOpen) btnOpen.addEventListener('click', () => callCoverService('open_cover', 'Ouverture du volet'));
        if (btnClose) btnClose.addEventListener('click', () => callCoverService('close_cover', 'Fermeture du volet'));
        if (btnStop) btnStop.addEventListener('click', () => callCoverService('stop_cover', 'Volet arrêté'));
    }
}

/**
 * Gestion des touches clavier dans le textarea
 */
function chatHandleKeydown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        chatSendMessage();
    }
    setTimeout(() => {
        const counter = document.getElementById('chat-char-count');
        const input = document.getElementById('chat-input');
        if (counter && input) counter.textContent = input.value.length;
    }, 0);
}

/**
 * Auto-resize du textarea
 */
function chatAutoResize(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';

    const counter = document.getElementById('chat-char-count');
    if (counter) counter.textContent = textarea.value.length;
}

