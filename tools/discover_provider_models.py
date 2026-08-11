"""
tools/discover_provider_models.py — Inventaire LIVE des modèles offerts par chaque API.

Répond à « qu'est-ce qui existe réellement chez nos providers aujourd'hui ? », par
appel direct à leur endpoint de listing — pas depuis une liste écrite à la main qui
vieillit en silence. Croise ensuite avec `models_registry.db` pour montrer les trois
écarts qui comptent :

  - **nouveau**   : offert par l'API, absent du catalogue → candidat à ajouter ;
  - **catalogué** : les deux se recoupent → rien à faire ;
  - **fantôme**   : au catalogue, plus offert par l'API → candidat à retirer.

Aucune clé n'est jamais affichée : seuls les noms de variables d'environnement le sont.

Usage :
    python tools/discover_provider_models.py                 # tableau de synthèse
    python tools/discover_provider_models.py --detail        # + la liste des modèles
    python tools/discover_provider_models.py --provider xai  # un seul provider
    python tools/discover_provider_models.py --json rapport.json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict

import requests
from dotenv import load_dotenv

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)
load_dotenv(os.path.join(RACINE, ".env"))

TIMEOUT = (5, 20)

# Providers hors OpenAI-compat : endpoint et authentification propres.
SPECIAUX = {
    "gemini_free": {
        "url": "https://generativelanguage.googleapis.com/v1beta/models",
        "env": "GEMINI_API_KEY",
        "auth": "query",  # ?key=
        "chemin": "models",
        "champ": "name",
    },
    "gemini_paid": {
        "url": "https://generativelanguage.googleapis.com/v1beta/models",
        "env": "GEMINI_PAYANT_API_KEY",
        "auth": "query",
        "chemin": "models",
        "champ": "name",
    },
    "anthropic_native": {
        "url": "https://api.anthropic.com/v1/models",
        "env": "ANTHROPIC_API_KEY",
        "auth": "x-api-key",
        "chemin": "data",
        "champ": "id",
    },
    "ollama_local": {
        "url": (os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434") + "/api/tags",
        "env": None,
        "auth": "aucune",
        "chemin": "models",
        "champ": "name",
    },
}


def _endpoints_openai_compat() -> dict[str, dict]:
    """Dérive l'endpoint /models de chaque provider OpenAI-compatible depuis le
    registre existant : `.../chat/completions` → `.../models`. Évite de recopier
    (et de laisser vieillir) une seconde table d'URL."""
    from core.openai_compat_provider import OPENAI_COMPAT_PROVIDERS

    sortie = {}
    for pid, conf in OPENAI_COMPAT_PROVIDERS.items():
        base = (conf.get("base_url") or "").strip()
        if not base.endswith("/chat/completions"):
            continue
        # Ollama n'exige aucune clé (env_key présent pour create_provider, pas pour le listing).
        sans_cle = pid.startswith("ollama")
        sortie[pid] = {
            "url": base[: -len("/chat/completions")] + "/models",
            "env": None if sans_cle else conf.get("env_key"),
            "auth": "aucune" if sans_cle else "bearer",
            "chemin": "data",
            "champ": "id",
            "extra_headers": conf.get("extra_headers"),
        }
    return sortie


def interroger(pid: str, conf: dict) -> tuple[str, list[str], str]:
    """Retourne (statut, modèles, détail). Ne lève jamais : un provider muet ne doit
    pas interrompre l'inventaire des autres."""
    env_var = conf.get("env")
    cle = os.environ.get(env_var, "").strip() if env_var else ""
    if env_var and not cle:
        return "SANS_CLE", [], f"{env_var} absente du .env"

    url = conf["url"]
    headers = dict(conf.get("extra_headers") or {})
    params = {}
    if conf["auth"] == "bearer":
        headers["Authorization"] = f"Bearer {cle}"
    elif conf["auth"] == "x-api-key":
        headers["x-api-key"] = cle
        headers["anthropic-version"] = "2023-06-01"
    elif conf["auth"] == "query":
        params["key"] = cle
        params["pageSize"] = 200

    try:
        rep = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
    except Exception as exc:
        return "INJOIGNABLE", [], f"{type(exc).__name__}"

    if rep.status_code != 200:
        # On ne renvoie que le code : le corps d'erreur peut contenir la clé.
        return "HTTP_" + str(rep.status_code), [], f"HTTP {rep.status_code}"

    try:
        data = rep.json()
    except Exception:
        return "REPONSE_ILLISIBLE", [], "corps non JSON"

    brut = data.get(conf["chemin"], data if isinstance(data, list) else [])
    if not isinstance(brut, list):
        # Clé présente mais null / objet : ne pas faire planter tout l'inventaire.
        brut = []
    modeles = []
    for item in brut:
        if isinstance(item, dict):
            val = item.get(conf["champ"]) or item.get("id") or ""
        else:
            val = str(item)
        val = str(val)
        if val.startswith("models/"):  # Gemini prefixe ses noms
            val = val[len("models/"):]
        if val:
            modeles.append(val)
    return "OK", sorted(set(modeles)), f"{len(modeles)} modèle(s)"


def catalogue() -> dict[str, set[str]]:
    """Modèles actifs du catalogue local, par provider."""
    chemin = os.path.join(RACINE, "models_registry.db")
    if not os.path.exists(chemin):
        return {}
    con = sqlite3.connect(chemin)
    par_provider = defaultdict(set)
    for pid, mid in con.execute("SELECT provider_id, id FROM models WHERE status='active'"):
        par_provider[pid].add(mid)
    return par_provider


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--detail", action="store_true", help="liste les modèles")
    ap.add_argument("--provider", help="limiter à un provider")
    ap.add_argument("--json", dest="json_out", help="écrire le rapport JSON")
    args = ap.parse_args()

    endpoints = {**_endpoints_openai_compat(), **SPECIAUX}
    disponibles = sorted(endpoints)
    if args.provider:
        endpoints = {k: v for k, v in endpoints.items() if k == args.provider}
        if not endpoints:
            print(f"Provider inconnu. Disponibles : {', '.join(disponibles)}")
            return 2

    cat = catalogue()
    rapport = {}

    print(f"{'PROVIDER':<18} {'STATUT':<16} {'API':>5} {'CATAL':>6} {'NOUVEAU':>8} {'FANTOME':>8}")
    print("-" * 68)
    for pid in sorted(endpoints):
        statut, modeles, detail = interroger(pid, endpoints[pid])
        au_catalogue = cat.get(pid, set())
        offerts = set(modeles)
        nouveaux = sorted(offerts - au_catalogue) if statut == "OK" else []
        fantomes = sorted(au_catalogue - offerts) if statut == "OK" else []
        rapport[pid] = {
            "statut": statut,
            "detail": detail,
            "offerts": modeles,
            "au_catalogue": sorted(au_catalogue),
            "nouveaux": nouveaux,
            "fantomes": fantomes,
        }
        print(f"{pid:<18} {statut:<16} {len(offerts):>5} {len(au_catalogue):>6} "
              f"{len(nouveaux):>8} {len(fantomes):>8}")
        if args.detail and statut == "OK":
            if nouveaux:
                print("   nouveaux : " + ", ".join(nouveaux))
            if fantomes:
                print("   fantomes : " + ", ".join(fantomes))

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(rapport, fh, ensure_ascii=False, indent=2)
        print(f"\nRapport JSON écrit : {args.json_out}")

    joignables = sum(1 for r in rapport.values() if r["statut"] == "OK")
    print(f"\n{joignables}/{len(rapport)} provider(s) ont répondu. "
          f"{sum(len(r['nouveaux']) for r in rapport.values())} modèle(s) offerts hors catalogue, "
          f"{sum(len(r['fantomes']) for r in rapport.values())} fantôme(s) au catalogue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
