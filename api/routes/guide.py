"""
api/routes/guide.py — Routes de l'écran « Guide » : le moteur expliqué par ses propres mesures.

    GET /api/guide/overview   état d'ensemble, chiffré, avec alertes calculées
    GET /api/guide/cascade    ordre des modèles essayés (discussion vocale, niveaux)

Ces routes ne font qu'AGRÉGER ce que le moteur sait déjà (catalogue, sonde de santé,
consommation, journal vocal, sauvegardes). Elles n'écrivent rien et ne sortent pas du
processus : aucun ping LLM, aucun appel à Home Assistant (réponse en quelques dizaines
de millisecondes).

Règle d'or du projet (INVARIANTS : « ne jamais mentir ») :
  - un chiffre INCONNU vaut `null`, jamais 0 ni une valeur inventée ;
  - une source absente se dit (`source`, `note`) et produit une alerte ;
  - si TOUTES les sources de données sont hors service, la route répond 503 avec la
    raison — pas un 200 vide.

Ce qui est volontairement approximatif est écrit en clair dans les champs `note`
(ex. « sondes » = appels sans session ni agent, critère de l'audit de prod du
01/11/2026 : il compte aussi les pings manuels, donc il surestime légèrement).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from core.log_redaction import masquer_secrets

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/guide", tags=["Guide"])

_DEPOT = Path(__file__).resolve().parents[2]

# Une sauvegarde plus vieille que ça est une alerte critique (consigne du cahier des charges).
SEUIL_SAUVEGARDE_CRITIQUE_H = 48
# Au-delà de N intervalles sans test, l'état « fournisseurs » est périmé.
_PEREMPTION_SONDE_INTERVALLES = 3


def _iso(epoch: float | None) -> str | None:
    """Epoch → ISO 8601 local avec décalage (jamais une date sans fuseau)."""
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch)).astimezone().isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Sections de /overview — chacune est isolée : une source en panne ne tue pas les autres
# ═══════════════════════════════════════════════════════════════════════════

def _section_moteur() -> dict:
    """Commit, heure de démarrage et uptime du process (sources déjà exposées par ailleurs)."""
    commit = None
    try:
        from core.version_info import get_short_commit
        commit = get_short_commit()
    except Exception as exc:
        logger.debug("[GUIDE] commit indisponible : %s", exc)
    demarre_le, uptime = None, None
    try:
        # Même horodatage que /api/observability/version (uptime_seconds) : une seule
        # définition de « depuis quand le moteur tourne ».
        from api.routes.observability import _PROCESS_START
        demarre_le = _iso(_PROCESS_START)
        uptime = max(0, int(time.time() - _PROCESS_START))
    except Exception as exc:
        logger.debug("[GUIDE] uptime indisponible : %s", exc)
    return {"commit": commit, "demarre_le": demarre_le, "uptime_s": uptime}


def _lire_sante_modeles() -> dict[str, dict]:
    """Une ligne par modèle testé par la sonde (table `model_health` de la base runtime)."""
    from core.runtime_db import get_connection

    conn = get_connection()
    try:
        lignes = conn.execute(
            "SELECT model_id, dernier_test, echecs_consecutifs, succes_consecutifs, "
            "desactive_par_sonde, derniere_erreur FROM model_health"
        ).fetchall()
    finally:
        conn.close()
    return {
        ligne[0]: {
            "dernier_test": ligne[1], "echecs": ligne[2] or 0, "succes": ligne[3] or 0,
            "eteint_par_sonde": bool(ligne[4]), "erreur": ligne[5],
        }
        for ligne in lignes
    }


def _section_catalogue() -> tuple[dict, dict, dict]:
    """
    Fournisseurs et modèles, depuis le catalogue (`models_registry.db`) et la sonde de
    vivacité (`model_health`). Lève si le catalogue est illisible.

    Un fournisseur est :
      - UTILISABLE si au moins un de ses modèles (actifs, ou éteints par la sonde) a réussi
        son dernier test ;
      - EN PANNE si ses modèles ont été testés et qu'AUCUN n'a réussi son dernier test ;
      - NI L'UN NI L'AUTRE s'il n'a jamais été testé (clé absente, modèle non câblé) ou n'a
        aucun modèle actif : compté dans `non_testes` / `sans_modele_actif`.
    """
    from core import models_db
    from core.model_health_probe import intervalle_sonde, sonde_activee

    stats = models_db.get_db_stats()
    if "error" in stats:
        raise RuntimeError(f"catalogue illisible : {stats['error']}")

    fournisseurs_db = models_db.get_all_providers()
    modeles_db = models_db.get_all_models()
    sante = _lire_sante_modeles()

    par_fournisseur: dict[str, list[dict]] = {}
    for m in modeles_db:
        eteint = sante.get(m["id"], {}).get("eteint_par_sonde", False)
        # Les modèles éteints PAR LA SONDE restent testés : ils comptent pour dire
        # « ce fournisseur est en panne » (sinon un fournisseur dont tous les modèles
        # ont été éteints disparaîtrait de la liste au lieu d'y figurer).
        if m.get("status") == "active" or eteint:
            par_fournisseur.setdefault(m["provider_id"], []).append(m)

    utilisables = 0
    non_testes = 0
    sans_modele_actif = 0
    en_panne: list[dict] = []
    for f in fournisseurs_db:
        concernes = par_fournisseur.get(f["id"], [])
        if not concernes:
            sans_modele_actif += 1
            continue
        testes = [(m, sante[m["id"]]) for m in concernes
                  if m["id"] in sante and sante[m["id"]]["dernier_test"] is not None]
        if not testes:
            non_testes += 1
            continue
        if any(s["echecs"] == 0 and s["succes"] > 0 for _m, s in testes):
            utilisables += 1
            continue
        ko = [s for _m, s in testes if s["echecs"] > 0]
        if not ko:
            non_testes += 1          # testé mais sans succès ni échec enregistré : état inconnu
            continue
        recent = max(ko, key=lambda s: s["dernier_test"] or 0)
        erreur = " ".join(masquer_secrets(recent["erreur"] or "").split())[:160] or "échec sans message d'erreur"
        eteints = sum(1 for _m, s in testes if s["eteint_par_sonde"])
        raison = f"{len(ko)} modèle(s) sur {len(testes)} testé(s) en échec : {erreur}"
        if eteints:
            raison += f" ({eteints} éteint(s) par la sonde)"
        en_panne.append({"nom": f.get("name") or f["id"], "raison": raison})

    dernier_test = max((s["dernier_test"] for s in sante.values() if s["dernier_test"]), default=None)
    note = None
    if not sonde_activee():
        note = ("La sonde de vivacité est désactivée (MODEL_HEALTH_PROBE) : ces états datent "
                "du dernier cycle connu et ne sont plus mis à jour.")
    elif dernier_test is None:
        note = "Aucun modèle n'a encore été testé par la sonde : l'état des fournisseurs est inconnu."
    elif time.time() - dernier_test > _PEREMPTION_SONDE_INTERVALLES * intervalle_sonde():
        note = (f"Le dernier test de la sonde date de plus de {_PEREMPTION_SONDE_INTERVALLES} "
                f"intervalles ({intervalle_sonde()} s) : l'état des fournisseurs est périmé.")

    fournisseurs = {
        "total": len(fournisseurs_db),
        "utilisables": utilisables,
        "en_panne": en_panne,
        # Champs additifs : de quoi comprendre pourquoi total != utilisables + en_panne.
        "non_testes": non_testes,
        "sans_modele_actif": sans_modele_actif,
        "source": "model_health (sonde de vivacité) croisé avec models_registry.db",
        "dernier_test": _iso(dernier_test),
        "note": note,
    }
    modeles = {"total": stats["models"], "actifs": stats["active_models"]}
    return fournisseurs, modeles, {"sonde_activee": sonde_activee(), "note_sonde": note,
                                   "dernier_test": dernier_test}


def _section_usage() -> dict:
    """
    Consommation de tokens, avec la part des sondes de santé mise à nu.

    Mesuré sur la prod le 01/11/2026 : 99,5 % des lignes `token_usage` des 7 derniers
    jours sont des appels de la sonde de santé des modèles, pas de l'usage. Un
    « nombre d'appels » qui les mélange ment par omission : on les sépare.

    Critère des sondes (celui de l'audit de prod) : ligne sans `session_id` ET sans
    `agent_name`. La table ne garde pas le prompt, donc la sonde n'est pas
    identifiable plus finement : les autres appels sans session (ping manuel d'un
    modèle depuis l'IHM, comparatif) y sont comptés aussi → `appels_sondes_7j` est une
    BORNE HAUTE, `appels_reels_7j` une borne basse.
    """
    from core.runtime_db import get_connection
    from core.session_history import get_token_stats

    stats_24h = get_token_stats(since_hours=24)
    stats_30j = get_token_stats(since_hours=30 * 24)
    if "error" in stats_24h or "error" in stats_30j:
        raise RuntimeError(f"consommation illisible : {stats_24h.get('error') or stats_30j.get('error')}")

    depuis_7j = time.time() - 7 * 86400        # `timestamp` est un epoch flottant (piège connu)
    conn = get_connection()
    try:
        sondes, reels = conn.execute(
            "SELECT "
            "COALESCE(SUM(CASE WHEN COALESCE(session_id, '') = '' AND COALESCE(agent_name, '') = '' "
            "THEN 1 ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN COALESCE(session_id, '') <> '' OR COALESCE(agent_name, '') <> '' "
            "THEN 1 ELSE 0 END), 0) "
            "FROM token_usage WHERE timestamp >= ?",
            (depuis_7j,),
        ).fetchone()
    finally:
        conn.close()

    return {
        "appels_24h": int(stats_24h["call_count"]),
        "appels_reels_7j": int(reels),
        "appels_sondes_7j": int(sondes),
        "cout_30j_usd": round(float(stats_30j["total_cost_usd"]), 4),
        "source": "token_usage (base runtime)",
        "note": (
            "appels_24h et cout_30j_usd comptent TOUS les appels enregistrés, sondes de santé "
            "comprises (elles coûtent réellement des tokens). Sonde = ligne sans session ni "
            "agent (critère de l'audit de prod du 01/11/2026) : borne haute, elle inclut les "
            "pings manuels ; appels_reels_7j est donc une borne basse."
        ),
    }


def _verifier_journal_vocal() -> None:
    from core.runtime_db import get_connection

    conn = get_connection()
    try:
        conn.execute("SELECT 1 FROM vocal_audit_log LIMIT 1").fetchall()
    finally:
        conn.close()


async def _section_vocal() -> dict:
    """Échanges vocaux sur 7 jours : réutilise `vocal_stats` (mêmes chiffres que l'écran vocal)."""
    from api.routes.vocal import _rows, vocal_stats

    # `vocal_stats` avale les erreurs SQLite et répond alors « 0 échange » : un zéro qui
    # mentirait. On vérifie d'abord que le journal est lisible ; sinon la section lève
    # et devient `null` + une alerte.
    await run_in_threadpool(_verifier_journal_vocal)
    stats = await vocal_stats("7d")
    p50 = stats["latency_ms"]["p50"]
    derniere = _rows("SELECT MAX(created_at) AS d FROM vocal_audit_log WHERE phase = 'response'")
    dernier_iso = None
    if derniere and derniere[0].get("d"):
        try:
            # `created_at` est CURRENT_TIMESTAMP de SQLite : UTC, sans fuseau. On le dit.
            dernier_iso = (
                datetime.strptime(derniere[0]["d"], "%Y-%m-%d %H:%M:%S")
                .replace(tzinfo=UTC).isoformat(timespec="seconds")
            )
        except ValueError:
            dernier_iso = None
    return {
        "echanges_7j": int(stats["total_exchanges"]),
        "latence_p50_ms": int(round(p50)) if p50 is not None else None,
        "dernier_echange": dernier_iso,
    }


def _section_home_assistant() -> dict:
    """HA est-il configuré ? (jeton présent) — sans AUCUN appel réseau."""
    from core.ha_token import get_ha_token
    return {"configure": bool(get_ha_token())}


def _section_sauvegardes() -> dict:
    from core.sauvegarde import etat_sauvegardes

    etat = etat_sauvegardes()
    valides = [s for s in etat["sauvegardes"] if s.get("valide")]
    return {
        "derniere": etat["derniere"],
        "nombre": len(valides),
        "dossier": etat["dossier"],
        # Additif : la dernière copie est-elle saine ? (alerte critique sinon)
        "derniere_integrite_ko": [
            f["nom"] for f in (valides[0]["fichiers"] if valides else []) if f.get("integrite") == "ko"
        ],
    }


def _persistent_agents() -> dict | None:
    """Section `persistent_agents` de config.json telle que le moteur la charge, ou None."""
    try:
        from core.llm_gateway import load_config
        return load_config().get("persistent_agents", {})
    except Exception as exc:
        logger.debug("[GUIDE] persistent_agents illisible : %s", exc)
        return None


def _en_marche(valeur: bool) -> str:
    return "actuellement activée" if valeur else "actuellement désactivée"


def _boucles() -> list[dict]:
    """
    Les boucles de fond lancées par le `lifespan` de gui_server, avec leur interrupteur.

    ⚠️ `active` dit « autorisée à tourner d'après son réglage », PAS « un cycle vient de
    se terminer » : le lifespan journalise « lancée » même quand la boucle sort aussitôt
    (constaté à l'audit). `None` quand le réglage n'a pas pu être lu. Les boucles sans
    interrupteur (`reglage` le dit) sont toujours lancées au démarrage.
    """
    pa = _persistent_agents()
    boucles: list[dict] = []

    def ajouter(nom: str, active: bool | None, description: str, reglage: str) -> None:
        boucles.append({"nom": nom, "active": active, "description": description, "reglage": reglage})

    # ── Sonde de santé des modèles ──
    try:
        from core.model_health_probe import intervalle_sonde, peut_desactiver, sonde_activee
        actif = sonde_activee()
        ajouter(
            "Sonde de santé des modèles", actif,
            "Teste chaque modèle actif par une vraie mini-génération et éteint ceux qui "
            "échouent plusieurs cycles de suite. Ses appels coûtent des tokens.",
            f"MODEL_HEALTH_PROBE ({_en_marche(actif)}), intervalle MODEL_HEALTH_INTERVAL_SECONDS="
            f"{intervalle_sonde()} s ; MODEL_HEALTH_AUTO_DISABLE="
            f"{'true' if peut_desactiver() else 'false'} (false = observation seule).",
        )
    except Exception:
        ajouter("Sonde de santé des modèles", None, "Teste chaque modèle actif.", "MODEL_HEALTH_PROBE (illisible)")

    # ── Alerte soldes et quotas ──
    try:
        from core.quota_alert import alerte_activee, intervalle_alerte
        actif = alerte_activee()
        ajouter(
            "Alerte soldes et quotas", actif,
            "Surveille les soldes et la saturation des quotas et prévient quand un seuil est franchi.",
            f"QUOTA_ALERTE_ENABLED ({_en_marche(actif)}), intervalle {intervalle_alerte()} s.",
        )
    except Exception:
        ajouter("Alerte soldes et quotas", None, "Surveille les soldes et les quotas.", "QUOTA_ALERTE_ENABLED (illisible)")

    # ── Boucles sans interrupteur ──
    try:
        from core.quota_collector import get_quota_refresh_interval
        intervalle = f"{get_quota_refresh_interval()} s"
    except Exception:
        intervalle = "inconnu"
    ajouter("Rafraîchissement des quotas", True,
            "Relève périodiquement les quotas des fournisseurs (alimente les jauges).",
            f"QUOTA_REFRESH_INTERVAL_SECONDS (intervalle actuel : {intervalle}) ; pas d'interrupteur.")
    ajouter("Nettoyage des sessions zombies", True,
            "Marque comme terminées les sessions restées « en cours » après un arrêt brutal.",
            "ZOMBIE_SWEEP_INTERVAL_SECONDS (défaut 600 s) ; pas d'interrupteur.")
    ajouter("Flux de quotas en direct", True,
            "Pousse l'état des quotas aux écrans connectés (SSE).", "Aucun réglage ; toujours lancée.")
    ajouter("Heartbeat des workers Swarm", True,
            "Vérifie toutes les 30 s que les workers distants répondent.", "Aucun réglage ; toujours lancée.")
    ajouter("Scan des consommations CLI", True,
            "Relève les conversations des CLI (Claude, Gemini) pour compter leur consommation.",
            "Aucun réglage ; toujours lancée.")

    # ── Agents persistants (config.json → persistent_agents) ──
    def agent(nom: str, cle: str, defaut: bool, description: str, extra: str) -> None:
        if pa is None:
            ajouter(nom, None, description, f"config.json → persistent_agents.{cle} (illisible)")
            return
        actif = bool(pa.get(cle, defaut))
        ajouter(nom, actif, description,
                f"config.json → persistent_agents.{cle} ({_en_marche(actif)}){extra}")

    agent("Démon sentinelle", "daemon_enabled", True,
          "Surveille Home Assistant et le système toutes les N minutes ; peut lever des anomalies.",
          f", cadence daemon_interval_minutes={pa.get('daemon_interval_minutes', 10)} min." if pa else ".")
    agent("Dreamer et DreamCoder", "dreamer_enabled", True,
          "Consolide la mémoire (faits, leçons) et fait avancer le backlog de tâches de code "
          "sur des branches. Réécrit memory.db : à sauvegarder.", ".")
    agent("Auditeur autonome", "auditor_enabled", False,
          "Audite le code par périmètres et alimente le backlog (optionnel).", ".")

    # ── Boucles pilotées par l'environnement ──
    try:
        from core.veille_scheduler import _enabled as veille_activee
        actif = veille_activee()
        ajouter("Veille modèles et tarifs", actif,
                "Compare le catalogue et les tarifs aux sources publiques, produit un rapport "
                "et peut appliquer les mises à jour (avec sauvegarde).",
                f"MOTEUR_VEILLE_ENABLED ({_en_marche(actif)}) ; MOTEUR_VEILLE_APPLIQUER=0 = rapport seul.")
    except Exception:
        ajouter("Veille modèles et tarifs", None, "Veille du catalogue et des tarifs.", "MOTEUR_VEILLE_ENABLED (illisible)")
    try:
        from core.vigie.reglages import lire_reglages
        actif = bool(lire_reglages().actif)
        ajouter("Vigie", actif,
                "Surveille des forums, GitHub et YouTube (et Gmail si activé) et envoie des alertes "
                "sur le téléphone via Home Assistant.",
                f"MOTEUR_VIGIE_ENABLED ({_en_marche(actif)}).")
    except Exception:
        ajouter("Vigie", None, "Veille des forums et alertes téléphone.", "MOTEUR_VIGIE_ENABLED (illisible)")
    actif = os.environ.get("TASK_DISCOVERY_ENABLED", "1") not in ("0", "false", "False")
    ajouter("Découverte de tâches", actif,
            "Scanne le dépôt toutes les heures et injecte des tâches dans le backlog.",
            f"TASK_DISCOVERY_ENABLED ({_en_marche(actif)}).")
    actif = bool(os.environ.get("MQTT_HOST"))
    ajouter("Watchdog MQTT", actif,
            "Surveille le moteur via MQTT (bascule vocale de secours côté Home Assistant).",
            f"MQTT_HOST ({'renseigné' if actif else 'vide : le watchdog n’est pas lancé'}).")
    return boucles


def _alertes(
    *, fournisseurs: dict | None, modeles: dict | None, sauvegardes: dict | None, usage: dict | None,
    ha: dict | None, infos_sonde: dict | None, hitl_en_attente: int | None, sources_ko: list[str],
) -> list[dict]:
    """Alertes CALCULÉES à partir des sections ci-dessus (jamais saisies à la main)."""
    alertes: list[dict] = []

    def ajouter(niveau: str, texte: str) -> None:
        alertes.append({"niveau": niveau, "texte": texte})

    # ── Sauvegardes ──
    if sauvegardes is None:
        ajouter("critique", "Le dossier de sauvegardes est illisible : on ne sait pas si les bases sont sauvegardées.")
    elif not sauvegardes["derniere"]:
        ajouter("critique", f"Aucune sauvegarde des bases n'existe (dossier : {sauvegardes['dossier']}). "
                            "Lancer une sauvegarde (POST /api/sauvegardes) et installer la sauvegarde quotidienne.")
    else:
        try:
            age_h = (datetime.now().astimezone() - datetime.fromisoformat(sauvegardes["derniere"])).total_seconds() / 3600
            if age_h > SEUIL_SAUVEGARDE_CRITIQUE_H:
                ajouter("critique", f"La dernière sauvegarde des bases date de {int(age_h)} h "
                                    f"(seuil : {SEUIL_SAUVEGARDE_CRITIQUE_H} h).")
        except ValueError:
            ajouter("attention", "La date de la dernière sauvegarde est illisible.")
        if sauvegardes.get("derniere_integrite_ko"):
            ajouter("critique", "La dernière sauvegarde contient une copie altérée : "
                                + ", ".join(sauvegardes["derniere_integrite_ko"]) + ".")

    # ── Catalogue ──
    if modeles is not None and modeles["actifs"] == 0:
        ajouter("critique", "Aucun modèle actif dans le catalogue : le moteur ne peut répondre à rien.")
    if fournisseurs is not None:
        if fournisseurs["en_panne"]:
            noms = ", ".join(f["nom"] for f in fournisseurs["en_panne"])
            ajouter("attention", f"{len(fournisseurs['en_panne'])} fournisseur(s) en panne : {noms}.")
        if fournisseurs.get("note"):
            ajouter("attention", fournisseurs["note"])
    if infos_sonde is not None and not infos_sonde["sonde_activee"]:
        ajouter("info", "La sonde de vivacité des modèles est désactivée (MODEL_HEALTH_PROBE).")

    # ── Usage ──
    if usage is not None:
        total_7j = usage["appels_reels_7j"] + usage["appels_sondes_7j"]
        if total_7j >= 50 and usage["appels_sondes_7j"] / total_7j > 0.9:
            part = round(100 * usage["appels_sondes_7j"] / total_7j)
            ajouter("info", f"{part} % des appels enregistrés sur 7 jours sont des sondes de santé, "
                            "pas de l'usage : lire « appels_reels_7j ».")

    # ── Home Assistant ──
    if ha is not None and not ha["configure"]:
        ajouter("attention", "Home Assistant n'est pas configuré (jeton HASS_TOKEN absent) : "
                             "la domotique vocale ne peut pas agir.")

    if hitl_en_attente:
        ajouter("info", f"{hitl_en_attente} demande(s) de validation humaine en attente.")

    for source in sources_ko:
        ajouter("attention", f"Source indisponible : {source}. Les valeurs correspondantes sont à null.")

    ordre = {"critique": 0, "attention": 1, "info": 2}
    return sorted(alertes, key=lambda a: ordre[a["niveau"]])


def _hitl_en_attente() -> int | None:
    try:
        from core.hitl_store import compter_par_statut
        return int(compter_par_statut().get("pending", 0))
    except Exception as exc:
        logger.debug("[GUIDE] HITL illisible : %s", exc)
        return None


async def construire_overview() -> dict:
    """Assemble l'état d'ensemble. Chaque section en échec devient `null` + une alerte."""
    sources_ko: list[str] = []
    morts = 0          # sections « données » (catalogue, usage, vocal) en échec

    fournisseurs = modeles = infos_sonde = None
    try:
        fournisseurs, modeles, infos_sonde = await run_in_threadpool(_section_catalogue)
    except Exception as exc:
        logger.warning("[GUIDE] catalogue indisponible : %s", exc)
        sources_ko.append(f"catalogue des modèles ({masquer_secrets(str(exc))[:120]})")
        morts += 1

    usage = None
    try:
        usage = await run_in_threadpool(_section_usage)
    except Exception as exc:
        logger.warning("[GUIDE] usage indisponible : %s", exc)
        sources_ko.append(f"consommation de tokens ({masquer_secrets(str(exc))[:120]})")
        morts += 1

    vocal = None
    try:
        vocal = await _section_vocal()
    except Exception as exc:
        logger.warning("[GUIDE] vocal indisponible : %s", exc)
        sources_ko.append(f"journal vocal ({masquer_secrets(str(exc))[:120]})")
        morts += 1

    if morts == 3:
        # Rien de ce que l'écran doit montrer n'est lisible : un 200 de zéros mentirait.
        raise HTTPException(
            status_code=503,
            detail="Aucune source de données du moteur n'est lisible : " + " ; ".join(sources_ko),
        )

    sauvegardes = None
    try:
        sauvegardes = await run_in_threadpool(_section_sauvegardes)
    except Exception as exc:
        logger.warning("[GUIDE] sauvegardes illisibles : %s", exc)

    ha = None
    try:
        ha = _section_home_assistant()
    except Exception as exc:
        logger.debug("[GUIDE] HA illisible : %s", exc)
        sources_ko.append("configuration Home Assistant")

    hitl = await run_in_threadpool(_hitl_en_attente)

    alertes = _alertes(
        fournisseurs=fournisseurs, modeles=modeles, sauvegardes=sauvegardes, usage=usage, ha=ha,
        infos_sonde=infos_sonde, hitl_en_attente=hitl, sources_ko=sources_ko,
    )

    return {
        "genere_le": datetime.now().astimezone().isoformat(timespec="seconds"),
        "moteur": _section_moteur(),
        "fournisseurs": fournisseurs or {"total": None, "utilisables": None, "en_panne": []},
        "modeles": modeles or {"total": None, "actifs": None},
        "usage": usage or {"appels_24h": None, "appels_reels_7j": None, "appels_sondes_7j": None,
                           "cout_30j_usd": None},
        "vocal": vocal or {"echanges_7j": None, "latence_p50_ms": None, "dernier_echange": None},
        "home_assistant": ha or {"configure": False},
        "sauvegardes": (
            {k: sauvegardes[k] for k in ("derniere", "nombre", "dossier")}
            if sauvegardes else {"derniere": None, "nombre": 0, "dossier": ""}
        ),
        "boucles": await run_in_threadpool(_boucles),
        "alertes": alertes,
    }


@router.get("/overview")
async def guide_overview() -> dict:
    """État d'ensemble du moteur (voir le module pour les règles de vérité)."""
    return await construire_overview()


# ═══════════════════════════════════════════════════════════════════════════
# /cascade
# ═══════════════════════════════════════════════════════════════════════════

_TIERS = ("leger", "moyen", "fort", "automatique")


def _tiers_du_catalogue(cfg: dict) -> tuple[dict[str, list[str]], dict[str, str], list[str]]:
    """
    Candidats de chaque niveau tels que le gateway les établit AVANT son tri dynamique :
    modèles actifs du catalogue (`routing_tier`), moins les exclus de la politique de
    routage, moins les modèles CLI là où les binaires sont déclarés absents.

    Retourne (candidats par niveau, source par niveau, exclusions appliquées).
    """
    from core import models_db
    from core.llm_gateway import EXCLUSIONS_CASCADE_DEFAUT, _cli_desactives, _est_exclu_cascade, _est_modele_cli

    exclus = set((cfg.get("routing_policy") or {}).get("excluded_models", [])) | set(EXCLUSIONS_CASCADE_DEFAUT)
    sans_cli = _cli_desactives()
    candidats: dict[str, list[str]] = {}
    sources: dict[str, str] = {}
    for niveau in _TIERS:
        ids = [m["id"] for m in models_db.get_models_for_tier(niveau)]
        if ids:
            sources[niveau] = "models_registry.db"
        else:
            # Même repli que le gateway : config.json → tiers, puis liste codée en dur.
            repli = (cfg.get("tiers") or {}).get(niveau) or []
            ids = [str(m) for m in repli]
            sources[niveau] = (
                "config.json (repli : le catalogue ne renvoie rien)" if ids
                else "défaut du code (liste non reproduite ici)"
            )
        ids = [m for m in ids if not _est_exclu_cascade(m, exclus)]
        if sans_cli:
            ids = [m for m in ids if not _est_modele_cli(m)]
        candidats[niveau] = ids
    return candidats, sources, sorted(exclus)


@router.get("/cascade")
async def guide_cascade() -> dict:
    """
    Ordre des modèles essayés. Deux réalités à ne pas confondre :

    - `fast_path_providers` : l'ordre RÉEL de la discussion vocale, lu dans
      config.json → routing_policy.fast_path_providers avec la fonction même du moteur
      (donc avec son repli et son cache) ;
    - `tiers` : le contenu de config.json → tiers, qui n'est qu'un FILET DE SÉCURITÉ.
      Le gateway lit d'abord le catalogue (`tiers_catalogue`), puis retrie à chaque requête
      selon l'Elo, le coût, le quota et la latence : l'ordre final n'est pas figé.
    """
    try:
        from core.llm_gateway import load_config
        cfg = await run_in_threadpool(load_config)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"config.json illisible : {masquer_secrets(str(exc))[:200]}") from exc

    try:
        from services.pipeline_service import _fast_path_providers_config
        fast_path = _fast_path_providers_config()
        fast_path_tab5 = _fast_path_providers_config("tab5")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"cascade du fast path indisponible : {masquer_secrets(str(exc))[:200]}") from exc

    fichier_present = (_DEPOT / "config.json").is_file()
    tiers_cfg = cfg.get("tiers")
    if not isinstance(tiers_cfg, dict):
        tiers_cfg = {}
    budget_brut = cfg.get("budget_guard")
    # Seulement les plafonds chiffrés : une valeur texte n'a rien à faire dans cette vue.
    budget = (
        {k: v for k, v in budget_brut.items() if isinstance(v, int | float | bool)}
        if isinstance(budget_brut, dict) else None
    )

    catalogue: dict[str, list[str]] | None = None
    sources_tiers: dict[str, str] | None = None
    exclusions: list[str] | None = None
    note_catalogue = None
    try:
        catalogue, sources_tiers, exclusions = await run_in_threadpool(_tiers_du_catalogue, cfg)
    except Exception as exc:
        logger.warning("[GUIDE] tiers du catalogue illisibles : %s", exc)
        note_catalogue = f"Catalogue des modèles illisible ({masquer_secrets(str(exc))[:120]}) : tiers_catalogue vaut null."

    return {
        "source": "config.json" if fichier_present else "défaut du code (config.json absent)",
        "fast_path_providers": fast_path,
        "tiers": {nom: [str(m) for m in liste] for nom, liste in tiers_cfg.items() if isinstance(liste, list)},
        "budget": budget,
        "explication": {
            "fast_path_providers": (
                "Ordre dans lequel la discussion vocale (mode chat) et le chat rapide essaient les "
                "modèles : le premier qui répond gagne. Un modèle local qui ne répond pas (ex. "
                "ajean_pc éteint) est sauté sans attendre, sauf s'il est le dernier de la liste. "
                "Depuis la tablette (source tab5), les modèles de fast_path_providers_tab5 passent "
                "d'abord. Réglage : config.json → routing_policy.fast_path_providers, rechargé à chaud."
            ),
            "tiers": (
                "Contenu de config.json → tiers, qui n'est qu'un filet de sécurité : le moteur lit "
                "d'abord le catalogue (models_registry.db, modèles actifs de chaque niveau — voir "
                "tiers_catalogue) et ne se rabat sur cette liste que si le catalogue ne renvoie rien. "
                "Il écarte ensuite les modèles exclus, puis retrie les candidats à chaque requête "
                "(Elo, coût, quota, latence) : l'ordre final n'est donc pas figé. Les niveaux sont "
                "léger, moyen, fort ; « automatique » regroupe tous les modèles actifs."
            ),
            # Champ additif.
            "budget": (
                "Plafonds du garde-fou budget (config.json → budget_guard) : dépense maximale par jour "
                "en dollars et quotas gratuits. Une fois un plafond atteint, le moteur cesse d'appeler "
                "les modèles payants correspondants. null = aucun garde-fou défini dans config.json."
            ),
        },
        # ── Champs additifs : la réalité côté catalogue, que `tiers` seul ne montre pas ──
        "fast_path_providers_tab5": fast_path_tab5,
        "tiers_catalogue": catalogue,
        "tiers_source": sources_tiers,
        "exclus_de_la_cascade": exclusions,
        "note": note_catalogue,
    }
