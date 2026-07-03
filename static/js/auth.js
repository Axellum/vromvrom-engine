/* ============================================================
   AUTH.JS — Authentification de l'IHM (P0-1.1)
   Chargé EN PREMIER. Les routes /api, /v1 et /ws sont protégées
   par require_auth côté serveur. Le navigateur ne peut pas
   « cacher » de secret : on stocke MOTEUR_API_KEY en localStorage
   (outil perso LAN), et :
     - fetch        → header Authorization: Bearer <clé>
     - EventSource  → ?token=<clé> (pas de header possible en SSE)
     - WebSocket    → ?token=<clé> (idem)
   ============================================================ */
(function () {
    "use strict";

    const KEY_STORAGE = "moteur_api_key";
    const ORIGIN = window.location.origin;
    const HOST = window.location.host;

    function getKey() { return localStorage.getItem(KEY_STORAGE) || ""; }
    function setKey(k) { if (k) localStorage.setItem(KEY_STORAGE, k); }
    function clearKey() { localStorage.removeItem(KEY_STORAGE); }

    function promptKey(message) {
        const k = window.prompt(
            message || "🔐 Clé API du moteur (MOTEUR_API_KEY) :",
            getKey()
        );
        if (k !== null) setKey(k.trim());
        return getKey();
    }
    function ensureKey() {
        if (!getKey()) promptKey();
        return getKey();
    }

    // URL même-hôte ciblant une route protégée ?
    function _sameHostPath(url) {
        try {
            const u = new URL(url, ORIGIN);
            if (u.host !== HOST) return null;       // jamais de fuite cross-hôte
            return u;
        } catch (_) { return null; }
    }
    function _isProtectedRest(url) {
        const u = _sameHostPath(url);
        if (!u) return false;
        return u.pathname.startsWith("/api") || u.pathname.startsWith("/v1");
    }
    // Ajoute ?token= pour les transports sans header (SSE / WebSocket).
    function _withToken(url) {
        const u = _sameHostPath(url);
        if (!u) return url;
        const p = u.pathname;
        if (p.startsWith("/api") || p.startsWith("/v1") || p === "/ws") {
            const key = getKey();
            if (key && !u.searchParams.has("token")) {
                u.searchParams.set("token", key);
                return u.toString();
            }
        }
        return url;
    }

    // ── Wrapper fetch : injecte le Bearer + redemande la clé sur refus ──
    const _fetch = window.fetch.bind(window);
    window.fetch = async function (input, init) {
        init = init || {};
        const url = (typeof input === "string") ? input : (input && input.url) || "";
        if (_isProtectedRest(url)) {
            const key = getKey();
            if (key) {
                const headers = new Headers(
                    init.headers || (typeof input !== "string" && input && input.headers) || {}
                );
                if (!headers.has("Authorization")) {
                    headers.set("Authorization", "Bearer " + key);
                }
                init = Object.assign({}, init, { headers });
            }
        }
        const res = await _fetch(input, init);
        if (_isProtectedRest(url) && (res.status === 401 || res.status === 403 || res.status === 503)) {
            promptKey("🔐 Accès refusé (" + res.status + "). Saisis une MOTEUR_API_KEY valide :");
        }
        return res;
    };

    // ── Wrapper EventSource (SSE) : ?token= ──
    const _ES = window.EventSource;
    if (_ES) {
        const Wrapped = function (url, config) { return new _ES(_withToken(url), config); };
        Wrapped.prototype = _ES.prototype;
        Wrapped.CONNECTING = _ES.CONNECTING; Wrapped.OPEN = _ES.OPEN; Wrapped.CLOSED = _ES.CLOSED;
        window.EventSource = Wrapped;
    }

    // ── Wrapper WebSocket : ?token= ──
    const _WS = window.WebSocket;
    if (_WS) {
        const Wrapped = function (url, protocols) {
            url = _withToken(url);
            return (protocols !== undefined) ? new _WS(url, protocols) : new _WS(url);
        };
        Wrapped.prototype = _WS.prototype;
        Wrapped.CONNECTING = _WS.CONNECTING; Wrapped.OPEN = _WS.OPEN;
        Wrapped.CLOSING = _WS.CLOSING; Wrapped.CLOSED = _WS.CLOSED;
        window.WebSocket = Wrapped;
    }

    // API minimale (console / futur bouton réglages).
    window.MoteurAuth = { getKey, setKey, clearKey, promptKey, ensureKey };

    // S'assurer qu'une clé est présente dès le chargement.
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", ensureKey);
    } else {
        ensureKey();
    }
})();
