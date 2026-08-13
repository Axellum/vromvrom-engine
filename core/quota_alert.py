"""
core/quota_alert.py — Alerte SOLDES et QUOTAS avant la coupure (#T334).

Pourquoi : le 12/08 à 21h, 35 % des tentatives de cascade partaient dans le
vide (51 tentatives, 18 échecs providers, 6 modèles disjonctés) parce que la
clé DashScope était morte (`invalid_api_key`) et un modèle gratuit OpenRouter
avait été retiré. **Personne ne l'a su avant la mesure.** Coût réel : la même
demande est passée de 39,2 s à 103,8 s, sans un seul changement de code sur
son chemin.

Le cycle complet décidé par Axel : signaler → désactiver → réactiver.
Le volet VALIDITÉ (qui désactive) est livré par #T333 (`core/model_health_probe.py`).
Ce module est le volet SOLDES ET QUOTAS : il ALERTE, il ne coupe rien.

Rien n'est reconstruit ici — tout ce qui suit existait déjà, vérifié le 12/08 :
  - `core/quota_collector.py` collecte périodiquement (défaut 300 s) et écrit
    dans `quota_realtime` ; `core.models_db.get_quota_summary()` agrège ;
  - `tools/comptes.py` met déjà ces chiffres en français (#T320) ;
  - le canal de notification `core/daemon_loop.py::_send_ha_notification()`
    (persistent_notification Home Assistant) existe.

Ce module est l'ordonnanceur manquant, sur le patron de #T333 : des seuils,
une hystérésis, une alerte. Rien de plus.

Garde-fous :
  - AUCUN appel supplémentaire aux API de facturation : on lit ce que le
    collecteur a déjà écrit toutes les 300 s. Une alerte qui déclencherait des
    requêtes de solde serait un amplificateur de panne, pas une surveillance.
  - UNE alerte par franchissement, jamais une par cycle : l'hystérésis
    (confirmation + sortie) est persistée en base ; un solde qui reste bas
    dix cycles produit UNE alerte, pas dix.
  - JAMAIS de secret dans une alerte : ni clé, ni token, même tronqué. Le
    provider, l'id de la clé (un nom d'environnement, pas sa valeur) et le
    chiffre suffisent.
  - Réglable sans code : seuils, interrupteur, mode observation par variables
    d'environnement (documentées dans la PR).
  - NE désactive AUCUN modèle : c'est le domaine de #T333.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)

# ── Réglages par environnement (aucune modification de code) ─────────────────

# Seuil de saturation par défaut : aligné sur tools/comptes.py
# (SEUIL_SATURATION_ALERTE = 80.0) — une clé au-delà est « sous tension ».
SEUIL_SATURATION_ALERTE_PCT = 80.0
# Solde en dessous duquel une clé prépayée est considérée comme sur le point
# de couper. 2 $ : le temps de réagir sans crier au loup à chaque centime.
SEUIL_SOLDE_ALERTE_USD = 2.0
# Cycles CONSÉCUTIFS sous le seuil avant d'alerter : à 300 s, 2 cycles = 10 min
# de panne avérée — assez pour ignorer un refresh foireux, assez peu pour ne
# pas laisser saigner la latence toute la journée (même esprit que #T333).
CONFIRMATION_CYCLES = 2
# Cycles CONSÉCUTIFS au-dessus du seuil avant de lever l'état d'alerte : la
# sortie d'alerte (le solde remonte) réarme le mécanisme pour le franchissement
# suivant. 2 cycles = le retour à la normale doit tenir, pas juste clignoter.
SORTIE_CYCLES = 2
# Intervalle de la boucle : même rythme que le collecteur (300 s) — inutile de
# tourner plus vite que la donnée qu'on lit.
INTERVALLE_DEFAUT_S = 300


def _flottant_env(nom: str, defaut: float) -> float:
    try:
        return float(os.environ.get(nom, defaut))
    except (TypeError, ValueError):
        return defaut


def _entier_env(nom: str, defaut: int) -> int:
    try:
        return int(os.environ.get(nom, defaut))
    except (TypeError, ValueError):
        return defaut


def alerte_activee() -> bool:
    """Interrupteur général : `QUOTA_ALERTE_ENABLED=false` neutralise la boucle."""
    return os.environ.get("QUOTA_ALERTE_ENABLED", "true").strip().lower() not in (
        "0", "false", "no", "non", "off",
    )


def mode_observation() -> bool:
    """`QUOTA_ALERTE_OBSERVATION=true` : mesure et journalise, ne notifie rien."""
    return os.environ.get("QUOTA_ALERTE_OBSERVATION", "false").strip().lower() in (
        "1", "true", "yes", "oui", "on",
    )


def seuil_saturation() -> float:
    return _flottant_env("QUOTA_ALERTE_SEUIL_SATURATION_PCT", SEUIL_SATURATION_ALERTE_PCT)


def seuil_solde() -> float:
    return _flottant_env("QUOTA_ALERTE_SEUIL_SOLDE_USD", SEUIL_SOLDE_ALERTE_USD)


def confirmation_cycles() -> int:
    return _entier_env("QUOTA_ALERTE_CONFIRMATION_CYCLES", CONFIRMATION_CYCLES)


def sortie_cycles() -> int:
    return _entier_env("QUOTA_ALERTE_SORTIE_CYCLES", SORTIE_CYCLES)


def intervalle_alerte() -> int:
    return _entier_env("QUOTA_ALERTE_INTERVAL_SECONDS", INTERVALLE_DEFAUT_S)


# ── État persistant (hystérésis) ─────────────────────────────────────────────

# Colonnes autorisées à l'écriture (liste blanche, même discipline que
# `_ecrire_sante` de #T333 : un nom interpolé reste un nom interpolé).
_COLONNES_ETAT = frozenset({
    "en_alerte", "cycles_sous_seuil", "cycles_au_dessus",
    "derniere_alerte_ts", "dernier_cycle_ts",
})


def lire_etat() -> dict:
    """État d'hystérésis ; valeurs neutres si le premier cycle n'a pas eu lieu."""
    from core.runtime_db import get_connection
    try:
        conn = get_connection()
        ligne = conn.execute(
            "SELECT en_alerte, cycles_sous_seuil, cycles_au_dessus, "
            "derniere_alerte_ts, dernier_cycle_ts FROM quota_alertes WHERE id = 1"
        ).fetchone()
        conn.close()
    except Exception as e:
        logger.warning(f"[ALERTE QUOTAS] Lecture état impossible : {e}")
        ligne = None
    if ligne is None:
        return {
            "en_alerte": 0, "cycles_sous_seuil": 0, "cycles_au_dessus": 0,
            "derniere_alerte_ts": None, "dernier_cycle_ts": None,
        }
    return {
        "en_alerte": ligne[0] or 0, "cycles_sous_seuil": ligne[1] or 0,
        "cycles_au_dessus": ligne[2] or 0, "derniere_alerte_ts": ligne[3],
        "dernier_cycle_ts": ligne[4],
    }


def _ecrire_etat(**champs) -> None:
    inconnues = set(champs) - _COLONNES_ETAT
    if inconnues:
        raise ValueError(f"Colonnes d'état inconnues : {sorted(inconnues)}")
    from core.runtime_db import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT INTO quota_alertes (id) VALUES (1) ON CONFLICT(id) DO NOTHING"
    )
    if champs:
        sets = ", ".join(f"{cle} = ?" for cle in champs)  # noqa: S608 — liste blanche
        conn.execute(
            f"UPDATE quota_alertes SET {sets} WHERE id = 1",  # noqa: S608
            (*champs.values(),),
        )
    conn.commit()
    conn.close()


# ── Détection ────────────────────────────────────────────────────────────────

def detecter_alertes(resume: dict) -> list[dict]:
    """Clés sous tension, d'après l'agrégat DÉJÀ collecté par le collecteur.

    Ne lit que `quota_realtime` (via `get_quota_summary`) : aucune requête vers
    les API de facturation. Une clé est alertante si :
      - `saturation_pct` >= seuil (quota presque épuisé), ou
      - `external_balance_usd` <= seuil (solde prépayé sur le point de couper),
        ou
      - `external_status` == 'exhausted' (le collecteur le pose à solde <= 0).
    Le solde inconnu (None) n'est PAS une alerte : on ne crie pas au loup sur
    une donnée absente (#T325 : distinguer « 0 $ mesuré » de « coût inconnu »).
    """
    seuil_sat = seuil_saturation()
    seuil_sol = seuil_solde()
    alertes = []
    for cle in resume.get("keys") or []:
        api_key_id = str(cle.get("api_key_id") or "?")
        provider = str(cle.get("provider_id") or "?")
        saturation = cle.get("saturation_pct")
        solde = cle.get("external_balance_usd")
        statut = str(cle.get("external_status") or "")

        if isinstance(saturation, (int, float)) and saturation >= seuil_sat:
            alertes.append({
                "api_key_id": api_key_id, "provider": provider,
                "type": "saturation",
                "valeur": f"{saturation:.1f} %",
                "seuil": f"{seuil_sat:.1f} %",
            })
        elif isinstance(solde, (int, float)) and solde <= seuil_sol:
            alertes.append({
                "api_key_id": api_key_id, "provider": provider,
                "type": "solde",
                "valeur": f"{solde:.2f} $",
                "seuil": f"{seuil_sol:.2f} $",
            })
        elif statut == "exhausted":
            alertes.append({
                "api_key_id": api_key_id, "provider": provider,
                "type": "epuise",
                "valeur": "solde épuisé",
                "seuil": f"{seuil_sol:.2f} $",
            })
    return alertes


def formater_message(alertes: list[dict]) -> str:
    """Texte de l'alerte : provider, id de clé (nom d'env, PAS sa valeur) et
    chiffre. Aucun secret — ni clé, ni token, même tronqué."""
    lignes = ["⚠️ Alertes quotas/soldes LLM :"]
    for a in alertes:
        lignes.append(
            f"• {a['api_key_id']} ({a['provider']}) — {a['type']} : "
            f"{a['valeur']} (seuil {a['seuil']})"
        )
    return "\n".join(lignes)


# ── Notification ─────────────────────────────────────────────────────────────

async def notifier_alerte(message: str) -> bool:
    """Canal EXISTANT uniquement : persistent_notification Home Assistant.

    `core/daemon_loop._send_ha_notification()` — déjà utilisé par le daemon
    pour signaler les soucis d'entités. Mode observation → journal seulement.
    """
    if mode_observation():
        logger.warning("[ALERTE QUOTAS] (observation) %s", message.replace("\n", " | "))
        return False
    try:
        from core.daemon_loop import _send_ha_notification
        ok = await _send_ha_notification("Alerte quotas/soldes LLM", message)
        if ok:
            logger.warning("[ALERTE QUOTAS] Notifiée à Home Assistant : %s",
                           message.replace("\n", " | "))
        return ok
    except Exception as e:
        logger.warning(f"[ALERTE QUOTAS] Notification impossible : {e}")
        return False


# ── Cycle complet ────────────────────────────────────────────────────────────

async def executer_cycle_alerte(resume: dict | None = None) -> dict:
    """Un passage : lit l'agrégat collecté, applique l'hystérésis, alerte UNE
    fois par franchissement. Retourne un compte rendu (même patron que #T333).

    Hystérésis :
      - sous le seuil, pas encore en alerte → on confirme (compteur) ; au bout
        de `confirmation_cycles()` cycles consécutifs, l'alerte part ;
      - sous le seuil, déjà en alerte → rien : un solde qui reste bas dix
        cycles produit UNE alerte, pas dix ;
      - au-dessus du seuil, en alerte → on compte les cycles de sortie ; au
        bout de `sortie_cycles()`, l'état est levé et le franchissement
        suivant pourra de nouveau alerter ;
      - au-dessus du seuil, pas en alerte → compteurs remis à zéro.
    """
    if resume is None:
        # Lecture seule de la base : AUCUN appel aux API de facturation.
        from core.models_db import get_quota_summary
        resume = get_quota_summary()

    etat = lire_etat()
    alertes = detecter_alertes(resume)
    maintenant = time.time()
    nouvelle_alerte = False
    en_alerte = etat["en_alerte"]
    cycles_sous = etat["cycles_sous_seuil"]
    cycles_au_dessus = etat["cycles_au_dessus"]

    if alertes:
        if not en_alerte:
            cycles_sous += 1
            cycles_au_dessus = 0
            if cycles_sous >= max(confirmation_cycles(), 1):
                nouvelle_alerte = True
                en_alerte = 1
        # déjà en alerte : on ne re-notifie pas, compteurs stables
    else:
        cycles_sous = 0
        if en_alerte:
            cycles_au_dessus += 1
            if cycles_au_dessus >= max(sortie_cycles(), 1):
                en_alerte = 0
                cycles_au_dessus = 0
        else:
            cycles_au_dessus = 0

    _ecrire_etat(
        en_alerte=en_alerte,
        cycles_sous_seuil=cycles_sous,
        cycles_au_dessus=cycles_au_dessus,
        dernier_cycle_ts=maintenant,
        derniere_alerte_ts=maintenant if nouvelle_alerte else etat["derniere_alerte_ts"],
    )

    notifiee = False
    if nouvelle_alerte:
        message = formater_message(alertes)
        logger.warning(
            "[ALERTE QUOTAS] Franchissement : %d clé(s) sous tension (%s)",
            len(alertes), ", ".join(sorted({a["api_key_id"] for a in alertes})),
        )
        notifiee = await notifier_alerte(message)
    elif alertes:
        logger.info(
            "[ALERTE QUOTAS] En alerte (déjà notifiée) : %d clé(s) sous tension — "
            "pas de nouvelle notification tant que le seuil n'est pas quitté.",
            len(alertes),
        )
    elif en_alerte:
        logger.info(
            "[ALERTE QUOTAS] Retour sous les seuils : %d/%d cycle(s) de sortie.",
            cycles_au_dessus, sortie_cycles(),
        )

    return {
        "alertes": [a["api_key_id"] for a in alertes],
        "nb_alertes": len(alertes),
        "nouvelle_alerte": nouvelle_alerte,
        "en_alerte": bool(en_alerte),
        "notifiee": notifiee,
        "observation": mode_observation(),
        "seuils": {
            "saturation_pct": seuil_saturation(),
            "solde_usd": seuil_solde(),
            "confirmation_cycles": confirmation_cycles(),
            "sortie_cycles": sortie_cycles(),
        },
    }


async def boucle_alerte_quotas(interval_seconds: int | None = None) -> None:
    """Boucle de fond, même motif que `boucle_sonde` (#T333) et
    `quota_refresh_loop` (#T269) : lancée par `asyncio.create_task` depuis
    gui_server, pas de nouvel ordonnanceur.

    Une erreur de cycle est journalisée et la boucle continue — un fournisseur
    injoignable est la situation normale que cette alerte observe, elle ne doit
    jamais faire tomber le serveur.
    """
    if not alerte_activee():
        logger.info("[ALERTE QUOTAS] Alerte désactivée (QUOTA_ALERTE_ENABLED=false).")
        return
    interval = intervalle_alerte() if interval_seconds is None else interval_seconds
    logger.info(
        "[ALERTE QUOTAS] Boucle démarrée (intervalle=%ds, saturation>=%s, "
        "solde<=%s $, confirmation=%d, sortie=%d, observation=%s).",
        interval, seuil_saturation(), seuil_solde(),
        confirmation_cycles(), sortie_cycles(), mode_observation(),
    )
    while True:
        await asyncio.sleep(interval)
        try:
            cr = await executer_cycle_alerte()
            logger.info(
                "[ALERTE QUOTAS] Cycle : %d clé(s) sous tension, alerte=%s, "
                "en_alerte=%s, notifiée=%s",
                cr["nb_alertes"], cr["nouvelle_alerte"], cr["en_alerte"],
                cr["notifiee"],
            )
        except Exception as exc:
            logger.warning("[ALERTE QUOTAS] Erreur de cycle (la boucle continue) : %s", exc)
