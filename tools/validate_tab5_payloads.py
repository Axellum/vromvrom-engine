# -*- coding: utf-8 -*-
"""Validation des payloads Tab5 contre les buffers documentés du firmware.

Lit tests/fixtures/tab5_payloads.json et simule, pour chaque service
``tab5_maj_*``, le comportement exact du code C++ embarqué :

  - ``tab5_maj_alerte_meteo_france`` : strncpy dans char[1024] (1023 octets
    utiles) → troncature SILENCIEUSE au-delà, + sémantique strtok_r qui
    fusionne les délimiteurs (un champ vide décale tous les suivants) ;
  - ``tab5_maj_previsions_{heures,jours}_bulk`` : garde ``length() > 2048`` →
    rejet complet loggé côté device (ESP_LOGE) mais invisible côté HA ;
  - ``tab5_maj_clim`` : atof puis sprintf "%.1f" dans char[16] → au-delà de
    15 caractères ce n'est pas une troncature mais un débordement (UB) ;
  - services à littéraux (volet, pluie_1h) : valeur hors contrat ignorée
    sans erreur.

Toutes les tailles sont mesurées en OCTETS UTF-8 (c'est ce que voit
``payload.length()`` côté C++), pas en caractères Python.

Rapport seulement : ce script ne modifie ni le firmware ni la fixture.
Code retour 0 si chaque payload se comporte comme son champ ``attendu``,
1 sinon (et en cas de fixture introuvable/invalide).

Usage :
    python tools/validate_tab5_payloads.py [chemin/vers/fixture.json]

Sources du contrat (14/07/2026) :
    00ProjetTab/Tab5/tab5-api-logic.yaml, tab5_custom.cpp, README.md
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FIXTURE_DEFAUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "tab5_payloads.json"

# ---------------------------------------------------------------------------
# Constantes du firmware (voir $meta.buffers_documentes de la fixture)
# ---------------------------------------------------------------------------
ALERTE_BUF = 1024          # char buf[1024] — tab5-api-logic.yaml:251
ALERTE_UTILE = ALERTE_BUF - 1
ALERTE_ANCIEN_BUF = 512    # buffer d'avant #T165, gardé pour information
BULK_MAX = 2048            # garde payload.length() > 2048 — tab5_custom.cpp:143/188
CLIM_BUF = 16              # char buf_target[16] / buf_curr[16] — tab5-api-logic.yaml:148/154

CHAMPS_ALERTE = ["phrase_pluie", "globale", "vent", "inondation", "orages",
                 "pluie_inondation", "neige_verglas", "grand_froid",
                 "vagues_submersion", "canicule", "avalanches"]

VOLET_LITTERAUX = {"En_mouvement", "Ouvert", "Partiel", "open", "Ferme", "closed"}
PLUIE_LITTERAUX = {"Pluie faible", "Pluie modérée", "Pluie forte",
                   "Pluie très forte", "Pluie trés forte"}

_ATOF_RE = re.compile(r"^[ \t]*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def octets(s: str) -> int:
    """Taille en octets UTF-8 — équivalent de payload.length() côté C++."""
    return len(s.encode("utf-8"))


def c_atof(s: str) -> float:
    """Reproduit atof() : préfixe numérique, 0.0 si rien de parsable."""
    m = _ATOF_RE.match(s)
    if not m:
        return 0.0
    try:
        return float(m.group(0))
    except ValueError:
        return 0.0


def strtok_r_tokens(texte: str, sep: str) -> list[str]:
    """Reproduit strtok_r : les délimiteurs consécutifs sont fusionnés,
    aucun token vide n'est produit (un champ vide DÉCALE donc les suivants)."""
    return [t for t in texte.split(sep) if t != ""]


def strncpy_utf8(payload: str, n_utiles: int) -> str:
    """Reproduit strncpy(buf, s, n) + terminaison : coupe à n_utiles OCTETS,
    éventuellement au milieu d'un caractère UTF-8 (remplacé par U+FFFD)."""
    return payload.encode("utf-8")[:n_utiles].decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Simulateurs par service — chacun renvoie (statut, [notes])
# ---------------------------------------------------------------------------

def verifier_bulk(valeurs: dict, nb_champs_min: int, cap_parts: int) -> tuple[str, list[str]]:
    """Parsers parse_and_update_{heures,jours}_bulk (tab5_custom.cpp)."""
    payload = valeurs["payload"]
    taille = octets(payload)
    notes = [f"{taille} octets / garde {BULK_MAX}"]

    if taille > BULK_MAX:
        notes.append("REJET COMPLET : ESP_LOGE côté device, mais HA n'est pas "
                      "informé → les 15 cartes restent sur les anciennes données "
                      "sans aucun signal côté automation.")
        return "rejet_firmware", notes

    notes.append(f"marge restante : {BULK_MAX - taille} octets")

    appliques, ignores = 0, []
    for enreg in strtok_r_tokens(payload, ";"):
        # split '|' via strchr : préserve les champs vides, mais borné à
        # cap_parts — les '|' excédentaires restent collés au dernier champ.
        parts = enreg.split("|", cap_parts - 1)
        if len(parts) < nb_champs_min:
            ignores.append(f"'{enreg[:40]}…' ({len(parts)} champs < {nb_champs_min})")
            continue
        try:
            idx = int(_ATOF_RE.match(parts[0]).group(0)) if _ATOF_RE.match(parts[0]) else 0
        except ValueError:
            idx = 0
        if 0 <= idx < 15:
            appliques += 1
        else:
            ignores.append(f"idx {idx} hors plage 0-14")

    notes.append(f"{appliques} enregistrement(s) appliqué(s)")
    if ignores:
        apercu = "; ".join(ignores[:3]) + ("…" if len(ignores) > 3 else "")
        notes.append(f"{len(ignores)} enregistrement(s) ignoré(s) SILENCIEUSEMENT : {apercu}")
        return "hors_contrat", notes
    return "ok", notes


def verifier_alerte(valeurs: dict) -> tuple[str, list[str]]:
    """Service tab5_maj_alerte_meteo_france (tab5-api-logic.yaml:242-319)."""
    payload = valeurs["payload"]
    taille = octets(payload)
    notes = [f"{taille} octets / buffer {ALERTE_BUF} ({ALERTE_UTILE} utiles)"]

    champs_voulus = payload.split("|")
    tronque = taille > ALERTE_UTILE

    if tronque:
        survivant = strncpy_utf8(payload, ALERTE_UTILE)
        tokens = strtok_r_tokens(survivant, "|")
        perdus = CHAMPS_ALERTE[len(tokens):]
        notes.append(f"TRONCATURE SILENCIEUSE à {ALERTE_UTILE} octets (strncpy, aucun log)")
        if perdus:
            notes.append(f"champs perdus : {', '.join(perdus)}")
            for nom, valeur in zip(CHAMPS_ALERTE, champs_voulus):
                if nom in perdus and valeur not in ("", "Vert", "unknown"):
                    notes.append(f"ALERTE PERDUE : {nom}='{valeur}' ne sera jamais affichée")
        if tokens:
            dernier = CHAMPS_ALERTE[min(len(tokens), len(CHAMPS_ALERTE)) - 1]
            notes.append(f"dernier champ survivant '{dernier}' potentiellement corrompu "
                         "(coupe possible en plein caractère UTF-8)")
        return "troncature_silencieuse", notes

    if taille > ALERTE_ANCIEN_BUF:
        notes.append(f"tient dans {ALERTE_BUF} mais aurait tronqué dans l'ancien "
                     f"buffer {ALERTE_ANCIEN_BUF} (#T165)")

    # Sémantique strtok_r : un champ vide fait glisser tous les suivants.
    tokens = strtok_r_tokens(payload, "|")
    if len(tokens) != len(champs_voulus):
        vides = [CHAMPS_ALERTE[i] if i < len(CHAMPS_ALERTE) else f"champ {i}"
                 for i, v in enumerate(champs_voulus) if v == ""]
        notes.append(f"DÉCALAGE SILENCIEUX : {len(champs_voulus)} champs émis mais "
                     f"strtok_r n'en voit que {len(tokens)} (champ(s) vide(s) : "
                     f"{', '.join(vides)}) — chaque niveau est lu sous le mauvais nom")
        return "decalage_champs", notes

    if len(champs_voulus) != len(CHAMPS_ALERTE):
        notes.append(f"{len(champs_voulus)} champs au lieu de {len(CHAMPS_ALERTE)} attendus")
        return "hors_contrat", notes

    return "ok", notes


def verifier_clim(valeurs: dict) -> tuple[str, list[str]]:
    """Service tab5_maj_clim : sprintf %.1f dans char[16] (deux buffers)."""
    notes = []
    deborde = False

    # buf_target[16] : sprintf(buf, "%.1f", f_target) → 15 caractères max
    f_target = c_atof(valeurs["target"])
    rendu_t = f"{f_target:.1f}"
    notes.append(f"target → \"{rendu_t}\" ({len(rendu_t) + 1}/{CLIM_BUF} octets avec \\0)")
    if len(rendu_t) + 1 > CLIM_BUF:
        notes.append("DÉBORDEMENT buf_target[16] : sprintf ne tronque pas, il écrase "
                     "la pile (undefined behavior, pire qu'une troncature)")
        deborde = True

    # buf_curr[16] : sprintf(buf, "%.1f \xC2\xB0C", f_current) → +4 octets (' ', 0xC2, 0xB0, 'C')
    f_current = c_atof(valeurs["current"])
    rendu_c = f"{f_current:.1f}"
    total_c = len(rendu_c) + 4 + 1
    notes.append(f"current → \"{rendu_c} °C\" ({total_c}/{CLIM_BUF} octets avec \\0)")
    if total_c > CLIM_BUF:
        notes.append("DÉBORDEMENT buf_curr[16] : le suffixe ' °C' (4 octets) laisse "
                     "seulement 11 caractères au nombre")
        deborde = True

    return ("overflow_sprintf" if deborde else "ok"), notes


def verifier_volet(valeurs: dict) -> tuple[str, list[str]]:
    etat = valeurs["etat_physique"]
    if etat in VOLET_LITTERAUX:
        return "ok", [f"'{etat}' reconnu par le contrat"]
    return "hors_contrat", [f"'{etat}' absent des littéraux {sorted(VOLET_LITTERAUX)} : "
                            "icônes et libellés non mis à jour, sans erreur ni log"]


def verifier_pluie(valeurs: dict) -> tuple[str, list[str]]:
    notes = []
    statut = "ok"
    try:
        idx = int(valeurs["index_5mn"])
    except ValueError:
        idx = 0
        notes.append(f"index_5mn '{valeurs['index_5mn']}' non numérique → atoi = 0")
    if not 0 <= idx <= 8:
        notes.append(f"index {idx} hors plage 0-8 : aucune barre ciblée, mise à jour "
                     "perdue SILENCIEUSEMENT")
        statut = "hors_contrat"
    else:
        notes.append(f"barre {idx} ciblée")

    intensite = valeurs["intensite"]
    if intensite in PLUIE_LITTERAUX:
        notes.append(f"intensité '{intensite}' reconnue")
    elif intensite == "":
        notes.append("intensité vide → barre remise à zéro (comportement voulu)")
    else:
        notes.append(f"intensité '{intensite}' hors littéraux → traitée comme "
                     "'pas de pluie' sans erreur")
        statut = "hors_contrat"
    return statut, notes


def verifier_sans_buffer(valeurs: dict, remarque: str) -> tuple[str, list[str]]:
    tailles = ", ".join(f"{k}={octets(v)}o" for k, v in valeurs.items())
    return "ok", [tailles, remarque]


VERIFICATEURS = {
    "tab5_maj_previsions_heures_bulk": lambda v: verifier_bulk(v, nb_champs_min=5, cap_parts=6),
    "tab5_maj_previsions_jours_bulk": lambda v: verifier_bulk(v, nb_champs_min=9, cap_parts=10),
    "tab5_maj_alerte_meteo_france": verifier_alerte,
    "tab5_maj_clim": verifier_clim,
    "tab5_maj_volet_etat": verifier_volet,
    "tab5_maj_pluie_1h": verifier_pluie,
    "tab5_maj_planning": lambda v: verifier_sans_buffer(
        v, "aucun buffer fixe (std::string) — pas de limite documentée côté firmware"),
    "tab5_maj_meteo_actuelle": lambda v: verifier_sans_buffer(
        v, "seule 'humidite' est consommée ; 'condition' et 'temperature' sont "
           "ignorées depuis le retrait de l'icône centrale"),
    "tab5_maj_probabilites": lambda v: verifier_sans_buffer(
        v, "3 atoi, aucun buffer fixe"),
    "tab5_maj_info_texte": lambda v: verifier_sans_buffer(
        v, "lambda vide (placeholder) — payload entièrement ignoré"),
}

ETIQUETTES = {
    "ok": "OK        ",
    "troncature_silencieuse": "TRONQUE   ",
    "rejet_firmware": "REJET     ",
    "decalage_champs": "DECALAGE  ",
    "hors_contrat": "HORS-CTR  ",
    "overflow_sprintf": "OVERFLOW  ",
}


def main(argv: list[str]) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    chemin = Path(argv[1]) if len(argv) > 1 else FIXTURE_DEFAUT
    if not chemin.is_file():
        print(f"Fixture introuvable : {chemin}", file=sys.stderr)
        return 1
    fixture = json.loads(chemin.read_text(encoding="utf-8"))

    print("=" * 78)
    print("VALIDATION PAYLOADS TAB5 — rapport seulement (aucune modification firmware)")
    print(f"Fixture : {chemin}")
    print("=" * 78)

    total, conformes, ecarts = 0, 0, []
    for service, spec in fixture["services"].items():
        verificateur = VERIFICATEURS.get(service)
        print(f"\n### {service}")
        print(f"    format : {spec['format']}")
        if verificateur is None:
            print("    !! aucun vérificateur implémenté pour ce service")
            ecarts.append((service, "-", "verificateur manquant", "-"))
            continue
        for nom, cas in spec["payloads"].items():
            total += 1
            statut, notes = verificateur(cas["valeurs"])
            attendu = cas["attendu"]
            conforme = statut == attendu
            conformes += conforme
            marqueur = "  " if conforme else "!!"
            print(f"  {marqueur}[{ETIQUETTES[statut].strip():<9}] {nom} — {cas['description']}")
            for note in notes:
                print(f"        - {note}")
            if not conforme:
                ecarts.append((service, nom, attendu, statut))
                print(f"        => ECART : attendu '{attendu}', obtenu '{statut}'")

    print("\n" + "=" * 78)
    print(f"BILAN : {conformes}/{total} payloads conformes à leur comportement attendu")
    if ecarts:
        print("Écarts (le firmware ne se comporte pas comme la fixture le prédit) :")
        for service, nom, attendu, statut in ecarts:
            print(f"  - {service}/{nom} : attendu={attendu} obtenu={statut}")
        print("=" * 78)
        return 1
    print("Les cas TRONQUE/REJET/DECALAGE/OVERFLOW ci-dessus sont des démonstrations")
    print("volontaires : ils documentent les pertes silencieuses du contrat actuel.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
