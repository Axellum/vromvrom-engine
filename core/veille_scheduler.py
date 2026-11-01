"""
core/veille_scheduler.py — Phase 3 : veille planifiée des modèles & tarifs.

Lance périodiquement `tools/veille_catalogue.lancer_veille` (défaut : toutes
les 24 h) et notifie Home Assistant via le canal EXISTANT des alertes
(`core.daemon_loop._send_ha_notification`, le même que les alertes quotas —
aucun nouveau canal inventé).

Comportement par défaut :
  - application automatique des tarifs activée (le but de la veille est
    d'adapter le barème) ; backup horodaté + vérification + exclusions
    permanentes protègent déjà l'écriture ;
  - désactivable : `MOTEUR_VEILLE_APPLIQUER=0` (rapport seul),
    `MOTEUR_VEILLE_ENABLED=0` (boucle coupée), `MOTEUR_VEILLE_INTERVAL_H`
    (périodicité en heures, défaut 24).

L'état du dernier passage est persisté dans `data/veille_scheduler_state.json`
(état runtime, gitignoré comme l'instantané d'inventaire).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHEMIN_ETAT = os.path.join(RACINE, "data", "veille_scheduler_state.json")

DELAI_DEMARRAGE_S = 60       # laisse les autres boucles s'installer
PAS_VERIFICATION_S = 900     # regarde toutes les 15 min si c'est l'heure


def _enabled() -> bool:
    return os.environ.get("MOTEUR_VEILLE_ENABLED", "1") not in ("0", "false", "False")


def _auto_appliquer() -> bool:
    return os.environ.get("MOTEUR_VEILLE_APPLIQUER", "1") not in ("0", "false", "False")


def _intervalle_secondes() -> float:
    try:
        heures = float(os.environ.get("MOTEUR_VEILLE_INTERVAL_H", "24"))
    except ValueError:
        heures = 24.0
    return max(heures, 1.0) * 3600.0


def est_du(dernier_ts: float | None, maintenant_ts: float, intervalle_s: float) -> bool:
    """Vrai si jamais passé ou si la période est écoulée depuis le dernier run."""
    if dernier_ts is None:
        return True
    return (maintenant_ts - dernier_ts) >= intervalle_s


def charger_etat() -> dict:
    """État persisté du planificateur, {} si absent/illisible."""
    if not os.path.exists(CHEMIN_ETAT):
        return {}
    try:
        with open(CHEMIN_ETAT, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logger.warning("[VEILLE] État illisible (%s) : %s", CHEMIN_ETAT, exc)
        return {}


def sauver_etat(etat: dict) -> None:
    from core.safe_io import safe_json_write

    os.makedirs(os.path.dirname(CHEMIN_ETAT), exist_ok=True)
    safe_json_write(CHEMIN_ETAT, etat)


def construire_message(rapport: dict) -> str | None:
    """Message de notification digne d'être envoyé, ou None si rien à signaler.

    Ne notifie que ce qui demande de l'attention : tarifs appliqués, écarts
    non chiffrables restants, vérification en échec, ou nouveautés chez nos
    vendors. Un passage sans signal reste silencieux (pas de spam quotidien).
    """
    morceaux: list[str] = []

    application = rapport.get("application") or {}
    journal = application.get("journal", [])
    appliques = [j for j in journal if j.get("statut") == "applique"]
    if appliques:
        morceaux.append(f"{len(appliques)} tarif(s) mis à jour automatiquement")
    verdict = application.get("vérification")
    if verdict == "ÉCHEC":
        residuels = ", ".join(f["modele"] for f in application.get("résiduels", []))
        morceaux.append(f"⚠️ vérification EN ÉCHEC ({residuels})")

    fiches = rapport.get("ecarts_tarifs", [])
    non_chiffrables = [
        f["modele"] for f in fiches
        if any((f.get(a) or {}).get("variation_pct") is None for a in ("input", "output"))
    ]
    if non_chiffrables:
        morceaux.append(
            f"{len(non_chiffrables)} écart(s) non chiffrable(s) à arbitrer : "
            f"{', '.join(sorted(set(non_chiffrables)))}"
        )

    nouveaux = rapport.get("nouveaux_par_vendor", {})
    total_nouveaux = sum(len(v) for v in nouveaux.values())
    if total_nouveaux:
        morceaux.append(f"{total_nouveaux} nouveau(x) modèle(s) chez nos vendors")

    if not morceaux:
        return None
    entete = f"Veille catalogue & tarifs du {rapport.get('genere_le', '?')} :"
    return entete + "\n" + "\n".join(f"• {m}" for m in morceaux)


def cycle_veille_sync() -> dict:
    """Un passage de veille (bloquant : appels réseau) + persistance de l'état.

    Retourne le rapport ; une exception remonte vers la boucle qui la journalise.
    """
    from tools.veille_catalogue import lancer_veille

    rapport = lancer_veille(appliquer=_auto_appliquer())
    etat = charger_etat()
    etat.update({
        "dernier_run_ts": time.time(),
        "dernier_run_iso": datetime.now(UTC).isoformat(timespec="seconds"),
        "appliquer": _auto_appliquer(),
        "ecarts": len(rapport.get("ecarts_tarifs", [])),
        "appliques": sum(
            1 for j in (rapport.get("application") or {}).get("journal", [])
            if j.get("statut") == "applique"
        ),
        "erreur": None,
    })
    sauver_etat(etat)
    return rapport


async def _notifier_ha(message: str) -> bool:
    """Canal existant des alertes HA ; un échec ne doit jamais casser la boucle."""
    try:
        from core.daemon_loop import _send_ha_notification
        # id stable : le rapport du jour remplace celui de la veille dans l'UI HA
        return bool(await _send_ha_notification(
            "Veille catalogue & tarifs", message,
            notification_id="moteur_veille_catalogue",
        ))
    except Exception as exc:
        logger.warning("[VEILLE] Notification impossible : %s", exc)
        return False


async def veille_main_loop() -> None:
    """Boucle cron asyncio (même motif que quota_refresh_loop / auditor)."""
    logger.info("[VEILLE] 📅 Démarrage de la planification (intervalle %s h, appliquer=%s)",
                _intervalle_secondes() / 3600.0, _auto_appliquer())
    await asyncio.sleep(DELAI_DEMARRAGE_S)

    while True:
        try:
            if _enabled() and est_du(
                charger_etat().get("dernier_run_ts"), time.time(), _intervalle_secondes()
            ):
                try:
                    rapport = await asyncio.to_thread(cycle_veille_sync)
                    message = construire_message(rapport)
                    if message:
                        await _notifier_ha(message)
                    logger.info("[VEILLE] Cycle terminé (%d écart(s), %d appliqué(s)).",
                                len(rapport.get("ecarts_tarifs", [])),
                                sum(1 for j in (rapport.get("application") or {}).get("journal", [])
                                    if j.get("statut") == "applique"))
                except Exception as exc:
                    logger.error("[VEILLE] Échec du cycle : %s", exc, exc_info=True)
                    etat = charger_etat()
                    etat.update({"dernier_run_ts": time.time(), "erreur": str(exc)})
                    sauver_etat(etat)
                    await _notifier_ha(f"Veille catalogue & tarifs : échec du cycle ({exc})")
        except Exception as exc:
            # La boucle ne doit JAMAIS mourir : un planificateur mort est pire
            # qu'un cycle raté.
            logger.error("[VEILLE] Erreur de boucle : %s", exc, exc_info=True)
        await asyncio.sleep(PAS_VERIFICATION_S)


def get_veille_status() -> dict:
    """État du planificateur (supervision / future route IHM)."""
    etat = charger_etat()
    return {
        "enabled": _enabled(),
        "appliquer": _auto_appliquer(),
        "intervalle_h": _intervalle_secondes() / 3600.0,
        **etat,
    }
