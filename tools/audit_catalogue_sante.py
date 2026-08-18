"""
tools/audit_catalogue_sante.py — État des lieux du catalogue de modèles croisé
avec la santé de la sonde de vivacité.

POURQUOI : le 17/08, la mesure en production (models_registry.db du Deck croisé
avec model_health) montrait 52 modèles `active` SANS `routing_tier`. Le routage
par tier les ignore (`get_models_for_tier` filtre sur le tier ET le statut),
mais la sonde de vivacité les teste chaque heure par une vraie inférence : ils
coûtent des tokens et du temps sans jamais pouvoir servir la cascade. Cet outil
rend ce fait MESURABLE et RELANÇABLE — pas un chiffre collé dans une PR — pour
vérifier que la situation ne se redégrade pas.

Ce que fait l'outil (LECTURE SEULE — il n'écrit RIEN, aucune migration) :
  1. matrice routing_tier × statut (le tableau mesuré le 17/08) ;
  2. profondeur réelle de chaque tier (modèles actifs routables) ;
  3. causes de mort des modèles inactifs (sonde vs décision humaine) ;
  4. détail de la zone grise : chaque modèle actif sans tier, croisé avec la
     santé (échecs consécutifs, dernière erreur), l'usage réel (token_usage
     depuis toujours) et le câblage gateway ; verdict de la règle explicite
     (core.models_db.SPECIALITES_HORS_ROUTAGE : une spécialité hors
     chat-complétion justifie l'absence de tier).

Aucun retrait de modèle n'est proposé automatiquement : les modèles hors tier
restent appelables par nom explicite (proxy /v1, `model_override` IHM/vocal,
dropdown /v1/models). Toute désactivation est une décision humaine, prise après
avoir vérifié les appels explicites — cf. corps de PR.

Usage :
    python tools/audit_catalogue_sante.py            # bases du dépôt
    python tools/audit_catalogue_sante.py --json     # sortie machine
    python tools/audit_catalogue_sante.py --seuil 5  # verdict ajusté
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

# Permet `python tools/audit_catalogue_sante.py` depuis la racine du dépôt.
_RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RACINE not in sys.path:
    sys.path.insert(0, _RACINE)

from core.models_db import (  # noqa: E402
    ROUTING_TIERS,
    SPECIALITES_HORS_ROUTAGE,
    est_hors_routage_legitime,
)

CHEMIN_REGISTRE_DEFAUT = os.path.join(_RACINE, "models_registry.db")
CHEMIN_RUNTIME_DEFAUT = os.path.join(_RACINE, "moteur_runtime.db")


def _ouvrir_lecture_seule(chemin: str) -> sqlite3.Connection | None:
    """Ouvre la base EN LECTURE SEULE (URI mode=ro). None si absente."""
    if not os.path.exists(chemin):
        return None
    conn = sqlite3.connect(f"file:{chemin}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ══════════════════════════════════════════════════════════════════
# Analyse (pure : connexions passées en argument → testable sur bases
# temporaires sans toucher aux bases livrées)
# ══════════════════════════════════════════════════════════════════

def analyser(
    conn_registre: sqlite3.Connection,
    conn_runtime: sqlite3.Connection | None = None,
    cables: set[str] | None = None,
    seuil: int = 0,
) -> dict:
    """
    Croise catalogue (models_registry.db) et santé/usage (moteur_runtime.db).

    `cables` : noms (minuscules) des modèles câblés dans la gateway. None =
    information indisponible (la gateway n'a pas pu être instanciée) — le
    rapport l'indique sans conclure.
    """
    rapport: dict = {"seuil": seuil}

    # ── 1. Matrice routing_tier × statut ──
    lignes = conn_registre.execute(
        "SELECT COALESCE(routing_tier, '') AS tier, status, COUNT(*) AS nb "
        "FROM models GROUP BY COALESCE(routing_tier, ''), status"
    ).fetchall()
    rapport["matrice"] = [
        {"routing_tier": ligne["tier"] or None, "status": ligne["status"], "nb": ligne["nb"]}
        for ligne in lignes
    ]

    # ── 2. Profondeur réelle des tiers (actifs uniquement) ──
    profondeur = {tier: 0 for tier in ROUTING_TIERS}
    profondeur["hors_tier"] = 0
    for ligne in conn_registre.execute(
        "SELECT COALESCE(routing_tier, 'hors_tier') AS tier, COUNT(*) AS nb "
        "FROM models WHERE status = 'active' "
        "GROUP BY COALESCE(routing_tier, 'hors_tier')"
    ).fetchall():
        profondeur[ligne["tier"]] = ligne["nb"]
    rapport["profondeur_tiers"] = profondeur

    # ── Santé et usage (facultatifs : l'outil marche sans la base runtime) ──
    sante: dict[str, sqlite3.Row] = {}
    appels: dict[str, int] = {}
    if conn_runtime is not None:
        try:
            for ligne in conn_runtime.execute(
                "SELECT model_id, echecs_consecutifs, succes_consecutifs, "
                "desactive_par_sonde, derniere_erreur, dernier_succes "
                "FROM model_health"
            ).fetchall():
                sante[ligne["model_id"]] = ligne
        except sqlite3.Error:
            pass  # table model_health absente (base plus ancienne)
        try:
            for ligne in conn_runtime.execute(
                "SELECT model, COUNT(*) AS nb FROM token_usage GROUP BY model"
            ).fetchall():
                appels[ligne["model"]] = ligne["nb"]
        except sqlite3.Error:
            pass  # table token_usage absente
    rapport["sante_disponible"] = bool(sante)
    rapport["usage_disponible"] = bool(appels)

    # ── 3. Causes de mort des modèles inactifs ──
    morts = []
    for ligne in conn_registre.execute(
        "SELECT id, speciality FROM models WHERE status = 'inactive' ORDER BY id"
    ).fetchall():
        etat = sante.get(ligne["id"])
        if etat is not None and etat["desactive_par_sonde"]:
            cause = "desactive_par_sonde"
        elif etat is not None and (etat["echecs_consecutifs"] or 0) > 0:
            cause = "muet_sonde_non_desactive"
        else:
            cause = "decision_humaine"
        morts.append({
            "model_id": ligne["id"],
            "cause": cause,
            "echecs_consecutifs": (etat["echecs_consecutifs"] or 0) if etat else 0,
            "derniere_erreur": etat["derniere_erreur"] if etat else None,
        })
    rapport["morts"] = morts
    rapport["causes"] = {
        cause: sum(1 for m in morts if m["cause"] == cause)
        for cause in ("desactive_par_sonde", "muet_sonde_non_desactive", "decision_humaine")
    }

    # ── 4. Zone grise : actifs sans tier, croisés santé/usage/câblage ──
    zone_grise, justifies = [], []
    for ligne in conn_registre.execute(
        "SELECT * FROM models WHERE status = 'active' AND routing_tier IS NULL "
        "ORDER BY provider_id, id"
    ).fetchall():
        modele = dict(ligne)
        etat = sante.get(modele["id"])
        entree = {
            "model_id": modele["id"],
            "provider_id": modele["provider_id"],
            "tier_catalogue": modele.get("tier"),
            "speciality": modele.get("speciality"),
            "cable": None if cables is None else modele["id"].lower() in cables,
            "appels_total": appels.get(modele["id"], 0),
            "echecs_consecutifs": (etat["echecs_consecutifs"] or 0) if etat else 0,
            "dernier_succes": etat["dernier_succes"] if etat else None,
            "justifie": est_hors_routage_legitime(modele),
        }
        (justifies if entree["justifie"] else zone_grise).append(entree)
    rapport["zone_grise"] = zone_grise
    rapport["justifies_hors_routage"] = justifies
    rapport["conforme"] = len(zone_grise) <= seuil
    rapport["regle"] = (
        "Un modèle actif sans routing_tier est légitime si sa spécialité est "
        f"hors routage chat-complétion ({sorted(SPECIALITES_HORS_ROUTAGE)}) ; "
        "sinon c'est une anomalie : sondé chaque heure, jamais routable."
    )
    return rapport


def noms_cables_dans_la_gateway() -> set[str] | None:
    """Modèles réellement câblés dans le gateway ; None si instanciation impossible.

    Aucune requête réseau : l'instanciation ne fait qu'enregistrer les
    providers. En cas d'échec (dépendance manquante…), l'information est
    signalée indisponible plutôt qu'inventée.
    """
    try:
        from core.llm_gateway import LLMGateway
        return {str(n).lower() for n in LLMGateway().providers.keys()}
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
# Présentation
# ══════════════════════════════════════════════════════════════════

def formater_texte(rapport: dict) -> str:
    lignes: list[str] = []
    sep = "─" * 78

    lignes.append(sep)
    lignes.append("AUDIT CATALOGUE × SANTÉ — zone grise du routage par tier")
    lignes.append(sep)

    lignes.append("\n1. Matrice routing_tier × statut")
    lignes.append(f"   {'routing_tier':<14} {'status':<10} {'nb':>4}")
    for entree in sorted(
        rapport["matrice"],
        key=lambda e: (e["routing_tier"] or "", e["status"]),
    ):
        lignes.append(
            f"   {(entree['routing_tier'] or '(vide)'):<14} "
            f"{entree['status']:<10} {entree['nb']:>4}"
        )

    lignes.append("\n2. Profondeur réelle des tiers (modèles ACTIFS routables)")
    for tier in (*ROUTING_TIERS, "hors_tier"):
        lignes.append(f"   {tier:<10} {rapport['profondeur_tiers'].get(tier, 0):>3}")

    lignes.append("\n3. Modèles inactifs par cause de mort")
    for cause, nb in rapport["causes"].items():
        lignes.append(f"   {cause:<26} {nb:>3}")

    lignes.append(
        "\n4. Zone grise — actifs sans tier (règle : spécialité hors routage = légitime)"
    )
    for entree in rapport["justifies_hors_routage"]:
        lignes.append(
            f"   [LÉGITIME] {entree['model_id']:<38} spécialité={entree['speciality']}"
        )
    for entree in rapport["zone_grise"]:
        cable = "câblé" if entree["cable"] else (
            "NON câblé" if entree["cable"] is False else "câblage inconnu"
        )
        lignes.append(
            f"   [GRIS]     {entree['model_id']:<38} provider={entree['provider_id']:<18} "
            f"speciality={entree['speciality'] or '(aucune)':<18} {cable}, "
            f"appels_total={entree['appels_total']}, "
            f"echecs_consecutifs={entree['echecs_consecutifs']}"
        )

    verdict = "CONFORME" if rapport["conforme"] else "NON CONFORME"
    lignes.append(sep)
    lignes.append(
        f"VERDICT (seuil={rapport['seuil']}) : {verdict} — "
        f"{len(rapport['zone_grise'])} modèle(s) en zone grise, "
        f"{len(rapport['justifies_hors_routage'])} légitime(s) hors routage."
    )
    lignes.append(
        "RAPPEL : cet outil n'écrit rien. Ne jamais désactiver en masse la zone "
        "grise — ces modèles restent appelables par nom (proxy /v1, "
        "model_override) ; tout retrait est une décision humaine vérifiée."
    )
    return "\n".join(lignes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="État des lieux du catalogue de modèles croisé avec la santé "
        "(lecture seule, relançable, n'écrit rien).",
    )
    parser.add_argument("--registre", default=CHEMIN_REGISTRE_DEFAUT,
                        help="Chemin de models_registry.db")
    parser.add_argument("--runtime", default=CHEMIN_RUNTIME_DEFAUT,
                        help="Chemin de moteur_runtime.db (santé + usage)")
    parser.add_argument("--json", action="store_true",
                        help="Sortie JSON (reproductible, consommable par un script)")
    parser.add_argument("--seuil", type=int, default=0,
                        help="Nombre maximal de modèles en zone grise toléré (défaut 0)")
    parser.add_argument("--sans-gateway", action="store_true",
                        help="Ne pas instancier la gateway (info câblage absente)")
    args = parser.parse_args(argv)

    conn_registre = _ouvrir_lecture_seule(args.registre)
    if conn_registre is None:
        print(f"Base registre introuvable : {args.registre}", file=sys.stderr)
        return 1
    conn_runtime = _ouvrir_lecture_seule(args.runtime)

    cables = None if args.sans_gateway else noms_cables_dans_la_gateway()
    try:
        rapport = analyser(conn_registre, conn_runtime, cables=cables, seuil=args.seuil)
    finally:
        conn_registre.close()
        if conn_runtime is not None:
            conn_runtime.close()

    if args.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=2, default=str))
    else:
        print(formater_texte(rapport))
    return 0 if rapport["conforme"] else 2


if __name__ == "__main__":
    sys.exit(main())
