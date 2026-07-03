/* ============================================================
   GAUGE.JS — Composant Jauge en Barre Horizontale réutilisable
   Remplace les jauges circulaires SVG par des barres linéaires
   conformes au design de référence (image utilisateur).
   ============================================================ */

/**
 * Crée une jauge en barre horizontale avec animation
 * @param {HTMLElement} container — Élément parent
 * @param {Object} opts — Configuration
 * @returns {Object} — API de contrôle { setValue, setMax, setLabel, setSublabel, getElement }
 */
function createGauge(container, opts = {}) {
    let {
        size = 'md',       // 'sm' | 'md' | 'lg'
        value = 0,
        max = 100,
        label = '',
        sublabel = '',
        unit = '',
        rechargeType = '',  // 'Libre', 'Rechargement continu', 'Rechargement le 1er'
        animated = true
    } = opts;

    // Création du wrapper
    const wrap = document.createElement('div');
    wrap.className = `hbar-gauge hbar-gauge--${size}`;

    const pct = max > 0 ? Math.min(value / max, 1) : 0;
    const colorClass = getGaugeColorClass(pct);

    wrap.innerHTML = `
        <div class="hbar-gauge__header">
            <span class="hbar-gauge__label" data-label>${label}</span>
            <span class="hbar-gauge__value" data-value>${formatGaugeValue(value, unit)} / ${formatGaugeValue(max, unit)}</span>
        </div>
        <div class="hbar-gauge__track">
            <div class="hbar-gauge__fill ${colorClass}" data-fill
                 style="width:${animated ? '0%' : (pct * 100) + '%'}"></div>
        </div>
        <div class="hbar-gauge__footer">
            <span class="hbar-gauge__sublabel" data-sublabel>${sublabel || `Limite : ${formatGaugeValue(max, unit)}`}</span>
            <span class="hbar-gauge__recharge" data-recharge>${rechargeType}</span>
        </div>
    `;

    container.appendChild(wrap);

    // Animation initiale
    if (animated) {
        requestAnimationFrame(() => {
            const fill = wrap.querySelector('[data-fill]');
            if (fill) fill.style.width = (pct * 100) + '%';
        });
    }

    // API de contrôle (identique à l'ancienne version)
    return {
        setValue(newValue, newMax) {
            if (newMax !== undefined) max = newMax;
            const newPct = max > 0 ? Math.min(newValue / max, 1) : 0;
            const newColor = getGaugeColorClass(newPct);

            const fill = wrap.querySelector('[data-fill]');
            const valEl = wrap.querySelector('[data-value]');

            if (fill) {
                fill.style.width = (newPct * 100) + '%';
                fill.className = `hbar-gauge__fill ${newColor}${newPct >= 0.8 ? ' alert' : ''}`;
            }
            // Classe d'alerte globale sur le wrapper (badge ⚠ + texte rouge)
            if (newPct >= 0.8) {
                wrap.classList.add('alert-state');
            } else {
                wrap.classList.remove('alert-state');
            }
            if (valEl) valEl.textContent = `${formatGaugeValue(newValue, unit)} / ${formatGaugeValue(max, unit)}`;
        },

        getElement() { return wrap; },

        setLabel(newLabel) {
            const el = wrap.querySelector('[data-label]');
            if (el) el.textContent = newLabel;
        },

        setSublabel(newSub) {
            const el = wrap.querySelector('[data-sublabel]');
            if (el) el.textContent = newSub;
        },

        setRechargeType(newType) {
            const el = wrap.querySelector('[data-recharge]');
            if (el) el.textContent = newType;
        }
    };
}

/**
 * Détermine la classe de couleur selon le pourcentage
 */
function getGaugeColorClass(pct) {
    if (pct >= 0.85) return 'crit';
    if (pct >= 0.6) return 'warn';
    return 'ok';
}

/**
 * Formatte la valeur pour affichage dans la jauge
 */
function formatGaugeValue(val, unit) {
    if (val >= 1000000) return (val / 1000000).toFixed(1).replace('.0', '') + 'M';
    if (val >= 1000) return (val / 1000).toFixed(0) + 'k';
    return val + (unit ? ` ${unit}` : '');
}
