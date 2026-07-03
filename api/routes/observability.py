# -*- coding: utf-8 -*-
"""
api/routes/observability.py — Observabilité du moteur (Phase 2, item 14).

Expose la santé des Circuit Breakers (et donc des providers LLM) sous deux formes :
- JSON         : GET /api/observability/circuit-breakers  (source du dashboard)
- Prometheus   : GET /api/observability/prometheus         (scrape, format texte natif)
- Dashboard    : GET /api/observability/dashboard          (page HTML autonome qui poll le JSON)

Choix : pas de dépendance `prometheus_client` (format texte émis à la main) pour
ne rien ajouter au conteneur de prod. Les métriques sont lues directement depuis
le registre global CircuitBreaker._registry (niveau classe, sans instance gateway).
"""

import logging
import time

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse

logger = logging.getLogger("observability")

router = APIRouter(prefix="/api/observability", tags=["Observabilité"])

# Horodatage de démarrage du process (pour l'uptime exposé en métrique).
_PROCESS_START = time.time()

# Mapping état CB (chaîne) → valeur numérique pour Prometheus.
_STATE_TO_NUM = {"CLOSED": 0, "HALF_OPEN": 1, "OPEN": 2}


def _snapshot_breakers() -> list:
    """Retourne une copie thread-safe des to_dict() de tous les circuit breakers."""
    try:
        from core.llm.circuit_breaker import CircuitBreaker
    except Exception as e:  # pragma: no cover - import défensif
        logger.warning(f"[OBS] CircuitBreaker indisponible : {e}")
        return []

    breakers = []
    try:
        with CircuitBreaker._registry_lock:
            for cb in CircuitBreaker._registry.values():
                breakers.append(cb.to_dict())
    except Exception as e:
        logger.error(f"[OBS] Lecture du registre CB échouée : {e}")
    return breakers


@router.get("/circuit-breakers")
async def circuit_breakers_status() -> dict:
    """État JSON de tous les circuit breakers + résumé agrégé."""
    breakers = _snapshot_breakers()
    open_count = sum(1 for b in breakers if b.get("state") == "OPEN")
    half_count = sum(1 for b in breakers if b.get("state") == "HALF_OPEN")
    return {
        "uptime_seconds": round(time.time() - _PROCESS_START, 1),
        "total": len(breakers),
        "open": open_count,
        "half_open": half_count,
        "healthy": len(breakers) - open_count - half_count,
        "circuit_breakers": breakers,
    }


@router.get("/prometheus", response_class=PlainTextResponse)
async def prometheus_metrics() -> str:
    """Exposition Prometheus (format texte natif, sans dépendance externe)."""
    breakers = _snapshot_breakers()
    lines = []

    def metric(name: str, mtype: str, help_text: str):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {mtype}")

    # Uptime process.
    metric("moteur_uptime_seconds", "gauge", "Uptime du process moteur en secondes.")
    lines.append(f"moteur_uptime_seconds {time.time() - _PROCESS_START:.0f}")

    # Nombre de circuit breakers enregistrés.
    metric("moteur_circuit_breakers_total", "gauge", "Nombre de circuit breakers enregistres.")
    lines.append(f"moteur_circuit_breakers_total {len(breakers)}")

    # État par breaker (0=closed, 1=half_open, 2=open).
    metric("moteur_circuit_breaker_state", "gauge",
           "Etat du circuit breaker (0=closed,1=half_open,2=open).")
    for b in breakers:
        name = b.get("name", "unknown")
        lines.append(f'moteur_circuit_breaker_state{{breaker="{name}"}} '
                     f'{_STATE_TO_NUM.get(b.get("state"), 0)}')

    # Compteurs par breaker.
    for field, mname, mtype, helptxt in [
        ("failure_count", "moteur_circuit_breaker_failure_count", "gauge",
         "Echecs consecutifs courants du circuit breaker."),
        ("total_calls", "moteur_circuit_breaker_calls_total", "counter",
         "Appels totaux passes par le circuit breaker."),
        ("total_failures", "moteur_circuit_breaker_failures_total", "counter",
         "Echecs totaux du circuit breaker."),
        ("total_trips", "moteur_circuit_breaker_trips_total", "counter",
         "Ouvertures totales (trips) du circuit breaker."),
    ]:
        metric(mname, mtype, helptxt)
        for b in breakers:
            name = b.get("name", "unknown")
            lines.append(f'{mname}{{breaker="{name}"}} {b.get(field, 0)}')

    return "\n".join(lines) + "\n"


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> str:
    """Mini dashboard HTML autonome qui poll /api/observability/circuit-breakers."""
    return """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<title>Santé Circuit Breakers — Moteur</title>
<style>
 body{font-family:system-ui,sans-serif;background:#0f1419;color:#e6e6e6;margin:0;padding:24px}
 h1{font-size:18px;margin:0 0 4px} .sub{color:#8b95a1;font-size:13px;margin-bottom:16px}
 .cards{display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap}
 .card{background:#1a212b;border-radius:10px;padding:12px 18px;min-width:90px}
 .card .n{font-size:24px;font-weight:600} .card .l{font-size:12px;color:#8b95a1}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #232b36}
 th{color:#8b95a1;font-weight:500}
 .pill{padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600}
 .CLOSED{background:#173d2a;color:#4ade80} .OPEN{background:#3d1717;color:#f87171}
 .HALF_OPEN{background:#3d3417;color:#fbbf24}
</style></head><body>
<h1>🩺 Santé des Circuit Breakers</h1>
<div class="sub">Rafraîchi toutes les 5 s — source : /api/observability/circuit-breakers</div>
<div class="cards" id="cards"></div>
<table><thead><tr><th>Provider</th><th>État</th><th>Échecs</th><th>Appels</th>
<th>Échecs tot.</th><th>Trips</th></tr></thead><tbody id="rows"></tbody></table>
<script>
async function refresh(){
 try{
  const r=await fetch('/api/observability/circuit-breakers'); const d=await r.json();
  document.getElementById('cards').innerHTML=
   card(d.total,'Total')+card(d.healthy,'Sains')+card(d.half_open,'Half-open')+card(d.open,'Ouverts')
   +card(Math.round(d.uptime_seconds)+'s','Uptime');
  document.getElementById('rows').innerHTML=(d.circuit_breakers||[]).map(b=>
   `<tr><td>${b.name}</td><td><span class="pill ${b.state}">${b.state}</span></td>
    <td>${b.failure_count}</td><td>${b.total_calls}</td>
    <td>${b.total_failures}</td><td>${b.total_trips}</td></tr>`).join('')
   ||'<tr><td colspan="6" style="color:#8b95a1">Aucun circuit breaker actif pour l\\'instant.</td></tr>';
 }catch(e){document.getElementById('rows').innerHTML=
   `<tr><td colspan="6" style="color:#f87171">Erreur: ${e}</td></tr>`}
}
function card(n,l){return `<div class="card"><div class="n">${n}</div><div class="l">${l}</div></div>`}
refresh(); setInterval(refresh,5000);
</script></body></html>"""
