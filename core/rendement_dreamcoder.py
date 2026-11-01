"""
core/rendement_dreamcoder.py — Compteur de rendement de DreamCoder.

Le seul chiffre qui dise si cette architecture produit quelque chose :
« N PR mergées pour X tokens », pas « on a brûlé des quotas gratuits ».

Ce module tient l'ENTONNOIR COMPLET de la production autonome :

    tenté → poussé → récolté → mergé

  1. TENTÉ    : tâches exécutées par les cycles DreamCoder (Deck) ;
  2. POUSSÉ   : branches task/* réellement sorties du clone (`push` réussi) ;
  3. RÉCOLTÉ  : branches publiées en PR par `scripts/recolte_dreamcoder.py` ;
  4. MERGÉ    : PR fusionnées dans master (constatées via `gh`).

Il ajoute le ratio qui manque aujourd'hui : tokens (et coût) PAR PR mergée.

Robustesse :
- Les compteurs sont cumulatifs dans `data/rendement_dreamcoder.json`, mais
  `reconstruire_depuis_rapports()` sait aussi les rebâtir depuis les rapports
  de cycle (`checkpoints/dreamcoder_results/cycle_*.json`), dédupliqués par
  nom de fichier — un état effacé n'efface pas l'historique.
- Chaque machine voit la part de l'entonnoir qu'elle connaît : le Deck compte
  tenté/poussé (les cycles y tournent), le PC compte récolté/mergé (la récolte
  et `gh` y vivent). L'API expose la vue de la machine qui répond.
- Écritures atomiques (fichier temporaire + rename) sous verrou thread :
  la collecte peut être appelée depuis plusieurs threads.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("rendement_dreamcoder")

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
_STATE_FILE = _ENGINE_ROOT / "data" / "rendement_dreamcoder.json"
_RAPPORTS_DIR = _ENGINE_ROOT / "checkpoints" / "dreamcoder_results"

# Nombre de cycles détaillés conservés dans l'état (le reste est agrégé).
_MAX_CYCLES_DETAILLES = 200

_verrou = threading.Lock()


def _etat_vide() -> dict[str, Any]:
    return {
        "cree_le": time.time(),
        "tentees": 0,          # tâches exécutées par DreamCoder
        "reussies": 0,         # tâches en succès (branche committée)
        "poussees": 0,         # branches task/* poussées hors du clone
        "recoltees": 0,        # branches publiées en PR
        "mergees": 0,          # PR fusionnées dans master
        "tokens_total": 0,
        "cost_usd_total": 0.0,
        "cycles": [],                # détail des N derniers cycles
        "cycles_comptabilises": [],  # noms de rapports déjà agrégés
        "branches_recoltees": {},    # branche → {pr_url, ts}
        "prs_mergees": {},           # numéro de PR → {branche, ts_constat}
    }


def _charger_etat() -> dict[str, Any]:
    if not _STATE_FILE.exists():
        return _etat_vide()
    try:
        etat = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[RENDEMENT] État illisible, reconstruction : %s", exc)
        return _etat_vide()
    # Un état partiellement écrit ne doit pas faire perdre les compteurs :
    # toute clé absente reprend sa valeur par défaut.
    base = _etat_vide()
    base.update({k: v for k, v in etat.items() if k in base})
    return base


def _sauver_etat(etat: dict[str, Any]) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(etat, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _STATE_FILE)
    except Exception as exc:
        logger.error("[RENDEMENT] Impossible de sauver l'état : %s", exc)


# ──────────────────────────────────────────────────────────────────────
# Alimentation de l'entonnoir
# ──────────────────────────────────────────────────────────────────────

def enregistrer_cycle(results: dict[str, Any], nom_rapport: str | None = None) -> dict[str, Any]:
    """
    Agrège le rapport d'un cycle de drainage DreamCoder (stades TENTÉ et
    POUSSÉ + tokens/coût). Appelée en fin de cycle, jamais bloquante pour
    le pipeline : l'appelant l'enrobe dans un thread + try/except.

    :param nom_rapport: nom de fichier du `cycle_*.json` que ce cycle vient
        d'écrire. **À fournir systématiquement** : il inscrit le cycle au
        registre `cycles_comptabilises`, sans quoi `reconstruire_depuis_rapports`
        recompte le même cycle une seconde fois et double tout l'entonnoir.
        Mesuré le 20/08 : le premier appel à `/api/dreamcoder/rendement` faisait
        passer le cumul de 31 à 62 tentées et de 35,4 M à 70,8 M de tokens.
    """
    tentees = int(results.get("processed", 0) or 0)
    if tentees <= 0:
        return _charger_etat()

    with _verrou:
        etat = _charger_etat()
        # Le compte live et la reconstruction lisent le MÊME cycle : sans cette
        # inscription, chacun l'ajoute de son côté.
        if nom_rapport and nom_rapport not in etat["cycles_comptabilises"]:
            etat["cycles_comptabilises"].append(nom_rapport)
        etat["tentees"] += tentees
        etat["reussies"] += int(results.get("success", 0) or 0)
        etat["poussees"] += len(results.get("branches_pushed", []) or [])
        etat["tokens_total"] += int(results.get("total_tokens", 0) or 0)
        etat["cost_usd_total"] += float(results.get("total_cost_usd", 0.0) or 0.0)
        etat["cycles"].append({
            "ts": time.time(),
            "tentees": tentees,
            "reussies": int(results.get("success", 0) or 0),
            "poussees": len(results.get("branches_pushed", []) or []),
            "tokens": int(results.get("total_tokens", 0) or 0),
            "cost_usd": float(results.get("total_cost_usd", 0.0) or 0.0),
            "stopped_reason": results.get("stopped_reason"),
        })
        etat["cycles"] = etat["cycles"][-_MAX_CYCLES_DETAILLES:]
        _sauver_etat(etat)
    logger.info(
        "[RENDEMENT] Cycle agrégé : +%d tentée(s), +%d poussée(s) — cumul %d/%d.",
        tentees, len(results.get("branches_pushed", []) or []),
        etat["tentees"], etat["poussees"])
    return etat


def enregistrer_recolte(entrees: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Stade RÉCOLTÉ : branches publiées en PR par `scripts/recolte_dreamcoder.py`.
    Chaque entrée : {"branche": str, "pr_url": str}. Rejouable : une branche
    déjà comptée n'est pas recomptée.
    """
    with _verrou:
        etat = _charger_etat()
        nouvelles = 0
        for e in entrees or []:
            branche = (e.get("branche") or "").strip()
            if not branche or branche in etat["branches_recoltees"]:
                continue
            etat["branches_recoltees"][branche] = {
                "pr_url": e.get("pr_url") or "",
                "ts": time.time(),
            }
            nouvelles += 1
        etat["recoltees"] = len(etat["branches_recoltees"])
        _sauver_etat(etat)
    if nouvelles:
        logger.info("[RENDEMENT] Récolte : +%d PR(s) — cumul %d.", nouvelles, etat["recoltees"])
    return etat


def enregistrer_merges(prs: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Stade MERGÉ : PR `task/*` constatées fusionnées. Chaque entrée :
    {"number": int, "branche": str}. Rejouable (dédup par numéro de PR).
    """
    with _verrou:
        etat = _charger_etat()
        nouveaux = 0
        for pr in prs or []:
            numero = pr.get("number")
            if numero is None or str(numero) in etat["prs_mergees"]:
                continue
            etat["prs_mergees"][str(numero)] = {
                "branche": pr.get("branche") or "",
                "ts_constat": time.time(),
            }
            nouveaux += 1
        etat["mergees"] = len(etat["prs_mergees"])
        _sauver_etat(etat)
    if nouveaux:
        logger.info("[RENDEMENT] Merges : +%d PR(s) — cumul %d.", nouveaux, etat["mergees"])
    return etat


def synchroniser_merges(depot: str = ".") -> int:
    """
    Constate via `gh` les PR de tâches mergées depuis le dépôt local.
    Best effort : `gh` absent/non authentifié → 0, pas d'exception.

    Critère de détection : le titre préfixé `[auto]` posé par la récolte
    (`scripts/recolte_dreamcoder.py::corps_de_pr`). Le nom de branche n'en
    est PAS un : vérifié le 18/08 sur les 30 dernières PR mergées, aucune
    n'a de tête `task/*` — la récolte pousse la tête du Deck telle quelle
    et les branches d'agent passent aussi par des branches `fix/`, `feat/`,
    `claude/`. Le titre est le seul marqueur posé systématiquement.

    :return: nombre de NOUVELLES PR mergées constatées.
    """
    try:
        res = subprocess.run(
            ["gh", "pr", "list", "--state", "merged", "--limit", "300",
             "--json", "number,headRefName,title"],
            cwd=depot, capture_output=True, text=True, timeout=60.0,
        )
    except FileNotFoundError:
        logger.info("[RENDEMENT] `gh` absent — synchronisation des merges ignorée.")
        return 0
    except Exception as exc:
        logger.warning("[RENDEMENT] Synchronisation des merges impossible : %s", exc)
        return 0

    if res.returncode != 0:
        logger.warning("[RENDEMENT] `gh pr list` en échec : %s", (res.stderr or "").strip()[:200])
        return 0
    try:
        lot = json.loads(res.stdout or "[]")
    except json.JSONDecodeError:
        return 0

    prs_taches = [
        {"number": pr.get("number"), "branche": pr.get("headRefName", "")}
        for pr in lot
        if str(pr.get("title", "")).startswith("[auto]")
    ]
    avant = _charger_etat()["mergees"]
    enregistrer_merges(prs_taches)
    return _charger_etat()["mergees"] - avant


# ──────────────────────────────────────────────────────────────────────
# Reconstruction et lecture
# ──────────────────────────────────────────────────────────────────────

def reconstruire_depuis_rapports() -> int:
    """
    Ré-agrège les rapports de cycle (`cycle_*.json`) pas encore comptés.
    Rend le compteur rebâtissable même si l'état JSON est effacé, et capte
    les cycles des nuits précédentes au premier démarrage du module.

    :return: nombre de rapports nouvellement comptabilisés.
    """
    if not _RAPPORTS_DIR.exists():
        return 0
    ajoutes = 0
    with _verrou:
        etat = _charger_etat()
        deja = set(etat["cycles_comptabilises"])
        for fichier in sorted(_RAPPORTS_DIR.glob("cycle_*.json")):
            if fichier.name in deja:
                continue
            try:
                rapport = json.loads(fichier.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("[RENDEMENT] Rapport illisible %s : %s", fichier.name, exc)
                continue
            etat["tentees"] += int(rapport.get("processed", 0) or 0)
            etat["reussies"] += int(rapport.get("success", 0) or 0)
            etat["poussees"] += len(rapport.get("branches_pushed", []) or [])
            etat["tokens_total"] += int(rapport.get("total_tokens", 0) or 0)
            etat["cost_usd_total"] += float(rapport.get("total_cost_usd", 0.0) or 0.0)
            etat["cycles_comptabilises"].append(fichier.name)
            ajoutes += 1
        if ajoutes:
            _sauver_etat(etat)
    return ajoutes


def recalculer_totaux_depuis_rapports() -> dict[str, Any]:
    """
    Reconstruit les totaux à partir des SEULS rapports de cycle, source durable.

    Remise à zéro des cumuls et du registre, puis re-agrégation : chaque
    `cycle_*.json` compte exactement une fois. Sert à réparer un état faussé —
    le double comptage du 20/08 laissait des totaux exactement doublés, et sur
    une machine où la liste `cycles` détaillée est tronquée (200 max), aucune
    division ne permet de retrouver la vérité.

    Les stades AVAL (`branches_recoltees`, `prs_mergees`) ne viennent pas des
    rapports : ils sont conservés tels quels.

    :return: l'état reconstruit.
    """
    with _verrou:
        etat = _charger_etat()
        etat["tentees"] = 0
        etat["reussies"] = 0
        etat["poussees"] = 0
        etat["tokens_total"] = 0
        etat["cost_usd_total"] = 0.0
        etat["cycles_comptabilises"] = []
        _sauver_etat(etat)
    ajoutes = reconstruire_depuis_rapports()
    logger.info("[RENDEMENT] Totaux reconstruits depuis %d rapport(s) de cycle.", ajoutes)
    return _charger_etat()


def get_rendement() -> dict[str, Any]:
    """
    Vue complète de l'entonnoir : tenté → poussé → récolté → mergé,
    avec tokens et coût PAR PR MERGÉE — le chiffre qui dit si l'architecture
    produit plus qu'elle ne consomme. Un KPI à 0 est rendu comme tel :
    `tokens_par_pr_mergee: None` quand aucune PR n'est mergée, jamais 0.
    """
    reconstruire_depuis_rapports()
    etat = _charger_etat()

    tentees = etat["tentees"]
    poussees = etat["poussees"]
    recoltees = etat["recoltees"]
    mergees = etat["mergees"]
    tokens = etat["tokens_total"]
    cout = etat["cost_usd_total"]

    return {
        "entonnoir": {
            "tentees": tentees,
            "reussies": etat["reussies"],
            "poussees": poussees,
            "recoltees": recoltees,
            "mergees": mergees,
        },
        "tokens_total": tokens,
        "cost_usd_total": round(cout, 6),
        "tokens_par_pr_mergee": (tokens / mergees) if mergees > 0 else None,
        "cost_usd_par_pr_mergee": round(cout / mergees, 6) if mergees > 0 else None,
        "taux": {
            # Chaque division est gardée : un entonnoir à 0 en amont ne doit
            # pas produire de ZeroDivisionError ni de faux « 0 % ».
            "reussite": round(etat["reussies"] / tentees * 100, 1) if tentees else None,
            "push_sur_reussite": round(poussees / etat["reussies"] * 100, 1)
            if etat["reussies"] else None,
            "recolte_sur_pousse": round(recoltees / poussees * 100, 1) if poussees else None,
            "merge_sur_recolte": round(mergees / recoltees * 100, 1) if recoltees else None,
        },
        "cycles_recents": etat["cycles"][-10:],
        "branches_recoltees": etat["branches_recoltees"],
        "prs_mergees": etat["prs_mergees"],
        "maj_ts": time.time(),
    }
