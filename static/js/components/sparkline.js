/* ============================================================
   SPARKLINE.JS — Composant micro-graphique SVG réutilisable
   Dessine une ligne de tendance dans un petit SVG.
   Utilisé pour afficher l'historique des quotas et du billing.
   ============================================================ */

/**
 * Crée un sparkline SVG dans un container donné.
 * @param {HTMLElement} container — Élément parent
 * @param {Object} opts — Configuration
 * @returns {Object} — API { update(data), destroy() }
 */
function createSparkline(container, opts = {}) {
    const {
        width = 200,
        height = 40,
        color = 'var(--accent-primary)',
        fillOpacity = 0.15,
        strokeWidth = 1.5,
        showDots = false,
        showLastValue = true,
        unit = '',
        animate = true,
    } = opts;

    // Conteneur
    const wrap = document.createElement('div');
    wrap.className = 'sparkline-container';
    wrap.style.cssText = `display:inline-flex;align-items:center;gap:var(--space-xs);`;

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', width);
    svg.setAttribute('height', height);
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.style.cssText = `overflow:visible;border-radius:4px;`;

    // Groupe pour les éléments
    const fillPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    fillPath.setAttribute('fill', color);
    fillPath.setAttribute('opacity', fillOpacity);

    const linePath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    linePath.setAttribute('fill', 'none');
    linePath.setAttribute('stroke', color);
    linePath.setAttribute('stroke-width', strokeWidth);
    linePath.setAttribute('stroke-linecap', 'round');
    linePath.setAttribute('stroke-linejoin', 'round');
    if (animate) {
        linePath.style.transition = 'all 0.4s ease';
    }

    svg.appendChild(fillPath);
    svg.appendChild(linePath);

    // Valeur actuelle (label à droite)
    const valueLabel = document.createElement('span');
    valueLabel.className = 'sparkline-value';
    valueLabel.style.cssText = `font-size:0.7rem;font-weight:700;font-family:var(--font-mono);color:${color};min-width:40px;text-align:right;`;

    wrap.appendChild(svg);
    if (showLastValue) wrap.appendChild(valueLabel);
    container.appendChild(wrap);

    /**
     * Met à jour le sparkline avec de nouvelles données
     * @param {Array<{value: number, timestamp?: number}>} data
     */
    function update(data) {
        if (!data || data.length === 0) {
            fillPath.setAttribute('d', '');
            linePath.setAttribute('d', '');
            valueLabel.textContent = '–';
            return;
        }

        const values = data.map(d => typeof d === 'number' ? d : d.value);
        const maxVal = Math.max(...values, 1);
        const minVal = Math.min(...values, 0);
        const range = maxVal - minVal || 1;

        const padding = 2;
        const w = width - padding * 2;
        const h = height - padding * 2;

        // Calculer les points
        const points = values.map((v, i) => {
            const x = padding + (i / Math.max(values.length - 1, 1)) * w;
            const y = padding + h - ((v - minVal) / range) * h;
            return { x, y };
        });

        // Chemin de la ligne
        const lineD = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
        linePath.setAttribute('d', lineD);

        // Chemin du remplissage (fermer vers le bas)
        const fillD = lineD + ` L ${points[points.length - 1].x.toFixed(1)} ${height} L ${points[0].x.toFixed(1)} ${height} Z`;
        fillPath.setAttribute('d', fillD);

        // Afficher la dernière valeur
        if (showLastValue) {
            const lastVal = values[values.length - 1];
            valueLabel.textContent = _formatSparkValue(lastVal, unit);
        }

        // Points (optionnel)
        if (showDots) {
            // Nettoyer les anciens points
            svg.querySelectorAll('.spark-dot').forEach(d => d.remove());
            // Dernier point uniquement
            const last = points[points.length - 1];
            const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            dot.setAttribute('cx', last.x);
            dot.setAttribute('cy', last.y);
            dot.setAttribute('r', 2.5);
            dot.setAttribute('fill', color);
            dot.classList.add('spark-dot');
            svg.appendChild(dot);
        }
    }

    function destroy() {
        wrap.remove();
    }

    return { update, destroy, element: wrap };
}

/**
 * Formate une valeur pour le label du sparkline
 */
function _formatSparkValue(val, unit) {
    if (val >= 1000000) return `${(val / 1000000).toFixed(1)}M${unit}`;
    if (val >= 1000) return `${(val / 1000).toFixed(0)}k${unit}`;
    if (val < 1 && val > 0) return `${val.toFixed(3)}${unit}`;
    return `${Math.round(val)}${unit}`;
}
