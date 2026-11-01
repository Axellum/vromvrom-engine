"""
core/vigie/etat.py — État persistant de la vigie (`data/vigie_state.json`).

Contenu : par source, ce qui a déjà été vu (identifiants Gmail bornés, plus haut
identifiant de message par fil de forum) et le compteur d'échecs ; la file des
alertes 🔴 pas encore livrées (silence de nuit, Home Assistant injoignable) ; ce
qui attend le résumé du matin.

Deux verrous `filelock` (règle du projet pour les fichiers partagés) :
  - `<état>.lock` autour de chaque lecture/écriture (via `core.safe_io`),
    l'écriture elle-même étant atomique (temporaire + `os.replace`) ;
  - `vigie_state.passe.json.lock` pendant TOUT un passage, pris sans attendre : si la
    boucle du moteur et un `python -m core.vigie --une-passe` lancé à la main
    tombent en même temps, le second renonce au lieu d'alerter deux fois.

Un état illisible repart de zéro : le prochain passage est un « premier
passage » (tout est marqué vu, aucune alerte). Mieux vaut rater une alerte que
renvoyer d'un coup des centaines de vieux mails.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager

from core.safe_io import file_lock, safe_json_write

logger = logging.getLogger(__name__)

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHEMIN_ETAT = os.path.join(RACINE, "data", "vigie_state.json")

VERSION_ETAT = 1
MAX_EN_ATTENTE = 200     # alertes 🔴 non livrées gardées au plus
MAX_A_LIRE = 100         # 🟡 gardés pour le résumé


def chemin_etat() -> str:
    """Lu à l'appel (et non figé à l'import) pour que les tests puissent le rediriger."""
    return CHEMIN_ETAT


def etat_vide() -> dict:
    return {
        "version": VERSION_ETAT,
        "sources": {},
        "en_attente": [],
        "resume": {"a_lire": [], "a_lire_perdus": 0, "bruit": {}},
        "dernier_resume_jour": None,
        "dernier_passage_iso": None,
    }


def _completer(etat: dict) -> dict:
    """Ajoute les clés manquantes (état d'une version antérieure ou partiel)."""
    base = etat_vide()
    for cle, valeur in base.items():
        etat.setdefault(cle, valeur)
    for cle, valeur in base["resume"].items():
        etat["resume"].setdefault(cle, valeur)
    return etat


def charger_etat(chemin: str | None = None) -> dict:
    chemin = chemin or chemin_etat()
    if not os.path.exists(chemin):
        # Sans verrou : un passage à blanc ne doit créer aucun fichier (ni `data/`, ni `.lock`).
        return etat_vide()
    with file_lock(chemin):
        if not os.path.exists(chemin):
            return etat_vide()
        try:
            with open(chemin, encoding="utf-8") as fh:
                etat = json.load(fh)
            if not isinstance(etat, dict):
                raise ValueError("racine non objet")
        except Exception as exc:
            logger.error("[VIGIE] État illisible (%s), on repart de zéro : %s", chemin, exc)
            return etat_vide()
    return _completer(etat)


def sauver_etat(etat: dict, chemin: str | None = None) -> None:
    chemin = chemin or chemin_etat()
    etat["en_attente"] = borner(etat.get("en_attente", []), MAX_EN_ATTENTE)
    a_lire = etat["resume"].get("a_lire", [])
    if len(a_lire) > MAX_A_LIRE:
        etat["resume"]["a_lire_perdus"] = etat["resume"].get("a_lire_perdus", 0) + len(a_lire) - MAX_A_LIRE
        etat["resume"]["a_lire"] = a_lire[-MAX_A_LIRE:]
    safe_json_write(chemin, etat)


def borner(liste: list, maximum: int) -> list:
    """Garde les `maximum` derniers éléments (les plus récents sont à la fin)."""
    return list(liste[-maximum:]) if len(liste) > maximum else list(liste)


@contextmanager
def verrou_passe(chemin: str | None = None):
    """Verrou d'un passage complet, pris sans attendre. Rend True si obtenu."""
    from filelock import FileLock, Timeout

    chemin = chemin or chemin_etat()
    os.makedirs(os.path.dirname(os.path.abspath(chemin)), exist_ok=True)
    # « vigie_state.passe.json.lock » : couvert par le motif `*.json.lock` du .gitignore.
    racine = chemin[:-5] if chemin.endswith(".json") else chemin
    verrou = FileLock(racine + ".passe.json.lock", timeout=0)
    try:
        verrou.acquire()
    except Timeout:
        yield False
        return
    try:
        yield True
    finally:
        verrou.release()
