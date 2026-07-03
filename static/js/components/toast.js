/* ============================================================
   TOAST.JS — Notifications toast non-bloquantes
   Remplace tous les alert() de l'ancienne version
   ============================================================ */

/**
 * Affiche un toast de notification
 * @param {'success'|'error'|'warning'|'info'} type
 * @param {string} message
 * @param {number} duration — Durée en ms (0 = pas d'auto-dismiss)
 */
function showToast(type, message, duration = 4000) {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }

    const icons = {
        success: '✅',
        error: '❌',
        warning: '⚠️',
        info: '💡',
        // Types de dégradation gracieuse
        partial: '⚡',
        fallback: '🔄'
    };

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <span class="toast__icon">${icons[type] || '💬'}</span>
        <span class="toast__msg">${message}</span>
        <button class="toast__close" onclick="dismissToast(this)">&times;</button>
    `;

    container.appendChild(toast);

    // Auto-dismiss après la durée
    if (duration > 0) {
        setTimeout(() => dismissToast(toast.querySelector('.toast__close')), duration);
    }
}

/**
 * Ferme un toast avec animation
 */
function dismissToast(btnOrEl) {
    const toast = btnOrEl.closest ? btnOrEl.closest('.toast') : btnOrEl.parentElement;
    if (!toast || toast.classList.contains('hiding')) return;
    toast.classList.add('hiding');
    setTimeout(() => toast.remove(), 300);
}
