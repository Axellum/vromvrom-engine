"""
tools/veille_catalogue.py — Veille automatisée des modèles et tarifs LLM.

Répond à « le barème et le catalogue du moteur sont-ils encore alignés sur ce
que les providers offrent réellement aujourd'hui ? ». Croise trois sources :

  - **OpenRouter `/api/v1/models`** : public, sans clé, tarifs USD/token de
    centaines de modèles chez tous les vendors (DeepSeek, GLM, Mistral, xAI,
    MiniMax, Cohere…) — la source de veille tarifaire la plus rentable ;
  - **LiteLLM `model_prices_and_context_window.json`** : barème communautaire
    très fréquemment mis à jour, complète OpenRouter (Gemini payant…) ;
  - **Inventaire live (#T243)** : ce que NOS providers annoncent sur leur
    endpoint de listing (`data/model_inventory.json`, rafraîchi si `--live`).

Produit un rapport d'écarts :

  - `ecarts_tarifs`  : modèle catalogué dont le tarif externe diffère du
    barème local au-delà du seuil (défaut 5 %) ;
  - `sans_reference` : modèle payant catalogué sans tarif externe connu ;
  - `nouveaux`       : modèles offerts chez nos vendors, absents du catalogue ;
  - `fantomes`       : modèles catalogués, absents du listing de leur API.

**Phase 2 (`--appliquer`)** : met à jour les tarifs des modèles DÉJÀ dans
`pricing_strategy.json` depuis les références externes, après backup horodaté
dans `backups_prod/`. Ne crée jamais de modèle, ne touche jamais aux
abonnements ni au local, ne modifie pas `models_registry.db` (l'ajout d'un
nouveau modèle à la cascade reste une décision humaine, règle de curation).
Chaque application est suivie d'une VÉRIFICATION : le barème est relu puis
re-comparé — un écart chiffrable appliqué doit disparaître du rapport.

Usage :
    python tools/veille_catalogue.py                     # instantané inventaire existant
    python tools/veille_catalogue.py --live              # + rafraîchit l'inventaire live
    python tools/veille_catalogue.py --offline           # aucun appel réseau
    python tools/veille_catalogue.py --seuil-pct 10
    python tools/veille_catalogue.py --appliquer         # maj des tarifs + vérification
    python tools/veille_catalogue.py --json rapport.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import sys
from datetime import UTC, datetime

import requests

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)

from core.pricing import EUR_USD, _build_table  # noqa: E402
from core.safe_io import safe_json_write  # noqa: E402

logger = logging.getLogger(__name__)

CHEMIN_PRICING = os.path.join(RACINE, "pricing_strategy.json")
CHEMIN_REGISTRE = os.path.join(RACINE, "models_registry.db")
CHEMIN_RAPPORT = os.path.join(RACINE, "data", "veille_catalogue.json")
DOSSIER_BACKUPS = os.path.join(RACINE, "backups_prod")

OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
TIMEOUT = (5, 30)

# Préfixes OpenRouter des vendors réellement câblés au gateway
# (`core/openai_compat_provider.py`). La veille se limite à nos providers :
# un modèle neuf chez un vendor non câblé n'est pas un signal pour nous.
PREFIXES_VENDORS = {
    "deepseek": ("deepseek/",),
    "zhipu": ("z-ai/", "zhipuai/"),
    "mistral": ("mistralai/",),
    "xai": ("x-ai/",),
    "minimax": ("minimax/",),
    "cerebras": ("cerebras/",),
    "cohere": ("cohere/",),
    "gemini_paid": ("google/",),
    "anthropic_native": ("anthropic/",),
}

# Préfixes LiteLLM qui désignent le tarif DIRECT du vendor (« deepseek/… »).
# Ces entrées priment sur OpenRouter à la fusion : OpenRouter est un
# revendeur (marge éventuelle), alors que nos appels partent vers l'API
# directe du vendor — c'est son tarif qui est la vraie référence.
PREFIXES_DIRECTS_LITELLM = (
    "deepseek/", "z-ai/", "zhipuai/", "mistralai/", "x-ai/", "minimax/",
    "cerebras/", "cohere/", "google/", "anthropic/",
)

# Suffixes dont le catalogue décore ses identifiants (jamais présents côté
# API ni côté sources externes) — même détourage que core/model_inventory.py.
_SUFFIXES_CATALOGUE = ("-free", "-paid", "-cli", "-preview-paid")

# Exclusions PERMANENTES de l'application automatique, décidées le 18/08/2026
# après vérification contre la facturation RÉELLE (billing_history, solde
# DeepSeek relevé par l'API) :
#   - dépense réelle constatée : 9.71 $ pour 88.5 M in + 41.8 M cache + 0.9 M out ;
#   - le barème local (0.14/0.28) estime ces tokens à 12.8 $ (1.3×, l'écart
#     vient du doublement heures de pic dans le tracker) ;
#   - la référence LiteLLM `deepseek/deepseek-chat` (0.28/0.42) les estime à
#     27.5 $ (2.8× le réel) : elle vise les tarifs des anciennes versions
#     (V3-0324 / R1 d'origine), alors que les alias `deepseek-chat`/`-r1`
#     servent aujourd'hui les modèles récents facturés au tarif V4-Flash.
# Les écarts restent AFFICHÉS par la veille ; seule l'application est écartée.
EXCLUSIONS_PERMANENTES = {
    "deepseek-chat": "référence externe = tarif legacy V3 ; le réel (solde API) "
                     "confirme le barème local 0.14/0.28",
    "deepseek-r1": "référence externe = tarif R1 d'origine ; l'alias sert le "
                   "modèle actuel facturé moins cher",
}


def exclusions_effectives(exclure: tuple[str, ...] = ()) -> dict[str, str]:
    """Exclusions permanentes + exclusions CLI, noms normalisés."""
    sorties = {normaliser_nom(m): raison for m, raison in EXCLUSIONS_PERMANENTES.items()}
    sorties.update({normaliser_nom(m): "exclu en ligne de commande" for m in exclure})
    return sorties

# Providers à coût marginal nul : aucune comparaison tarifaire n'a de sens.
_PROVIDERS_GRATUITS = {"local", "ollama_local", "ollama_pc", "ajean_pc", "ajean_deck"}


def normaliser_nom(model_id: str) -> str:
    """Nom de comparaison : minuscules, sans préfixe vendor ni suffixe catalogue.

    `deepseek/deepseek-chat` → `deepseek-chat` ; `gemini-2.5-flash-paid` →
    `gemini-2.5-flash`. C'est sur cette forme que le local et l'externe se
    rejoignent.
    """
    base = (model_id or "").strip().lower()
    if "/" in base:
        base = base.split("/", 1)[1]
    for suffixe in _SUFFIXES_CATALOGUE:
        if base.endswith(suffixe):
            base = base[: -len(suffixe)]
            break
    return base


def _taux_openrouter(pricing: dict) -> dict | None:
    """Tarifs USD/token d'une entrée OpenRouter, ou None si rien d'exploitable."""
    if not isinstance(pricing, dict):
        return None

    def _flotte(cle: str) -> float | None:
        brut = pricing.get(cle)
        if brut in (None, ""):
            return None
        try:
            return float(brut)
        except (TypeError, ValueError):
            return None

    entree = _flotte("prompt")
    sortie = _flotte("completion")
    if not entree and not sortie:
        # Variante gratuite (id `…:free`) ou fiche muette : aucun signal
        # tarifaire — et la garder masquerait un vrai tarif LiteLLM à la fusion.
        return None
    cache = _flotte("input_cache_read")
    return {
        "input": entree or 0.0,
        "output": sortie or 0.0,
        "cache": cache,  # None accepté : le barème local retombe sur input
    }


def parser_openrouter(data: dict) -> dict[str, dict]:
    """`/api/v1/models` d'OpenRouter → {nom_normalisé: tarifs}.

    Le `pricing` OpenRouter est en USD **par token** (chaînes décimales).
    Les entrées sans tarif exploitable sont écartées.
    """
    sortie: dict[str, dict] = {}
    for item in data.get("data", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        ident = str(item.get("id") or "")
        tarifs = _taux_openrouter(item.get("pricing"))
        if not ident or tarifs is None:
            continue
        sortie[normaliser_nom(ident)] = tarifs
    return sortie


def parser_litellm(data: dict) -> dict[str, dict]:
    """Barème LiteLLM → {nom_normalisé: tarifs} (USD/token).

    Les clés pseudo-modèles (`sample_spec`, `text-embedding-ada-002-v2`…) sont
    filtrées : seules les entrées portant un coût d'entrée OU de sortie sont
    retenues. Les champs cache de LiteLLM (`cache_read_input_cost_per_token`)
    sont repris quand présents.
    """
    sortie: dict[str, dict] = {}
    if not isinstance(data, dict):
        return sortie
    for cle, fiche in data.items():
        if not isinstance(fiche, dict):
            continue
        entree = fiche.get("input_cost_per_token")
        sortie_t = fiche.get("output_cost_per_token")
        if entree is None and sortie_t is None:
            continue
        cache = fiche.get("cache_read_input_cost_per_token")
        sortie[normaliser_nom(cle)] = {
            "input": float(entree or 0.0),
            "output": float(sortie_t or 0.0),
            "cache": float(cache) if cache is not None else None,
        }
    return sortie


def cles_directes_litellm(brut: dict) -> set[str]:
    """Noms normalisés des entrées LiteLLM au tarif DIRECT vendor.

    Identifie les clés du barème brut portant un préfixe vendor connu
    (`PREFIXES_DIRECTS_LITELLM`) — après normalisation, l'information de
    préfixe est perdue, d'où ce repérage AVANT fusion.
    """
    directs: set[str] = set()
    if not isinstance(brut, dict):
        return directs
    for cle, fiche in brut.items():
        if not isinstance(fiche, dict):
            continue
        if any(cle.startswith(p) for p in PREFIXES_DIRECTS_LITELLM):
            directs.add(normaliser_nom(cle))
    return directs


def fusionner_sources(
    openrouter: dict, litellm: dict, directs: set[str] | None = None
) -> dict:
    """Fusion des références externes.

    Priorité : LiteLLM à clé DIRECTE vendor (le tarif facturé par l'API qu'on
    appelle réellement) > OpenRouter (revendeur, très couvrant) > LiteLLM
    sans préfixe (filet). La `source` est notée pour citer la provenance.
    """
    directs = directs or set()
    fusion = {}
    for nom, tarifs in litellm.items():
        fusion[nom] = {**tarifs, "source": "litellm"}
    for nom, tarifs in openrouter.items():
        if nom not in directs:
            fusion[nom] = {**tarifs, "source": "openrouter"}
    return fusion


def barème_local() -> dict[str, dict]:
    """Barème local (USD/token) depuis pricing_strategy.json, clés normalisées.

    Passe par `_build_table` de `core.pricing` : la veille compare exactement
    ce que le moteur facture, pas une recopie du JSON. Un fichier absent ou
    illisible donne un barème vide (le rapport le dira).
    """
    try:
        with open(CHEMIN_PRICING, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[VEILLE] pricing_strategy.json illisible : %s", exc)
        return {}
    return _barème_depuis_data(data)


def _barème_depuis_data(data: dict) -> dict[str, dict]:
    """Barème local aplati (clés normalisées) depuis le contenu du JSON.

    La normalisation fait parfois collisionner deux clés (`gemini-2.5-flash`
    free tier et `gemini-2.5-flash-paid`) : l'entrée PAYANTE l'emporte, car
    c'est elle que la veille doit comparer et mettre à jour. La CLÉ JSON
    survivante est notée (`_cle_json`) : `_build_table` résout déjà la
    collision « même clé ignorée la casse » (dernier écrivain gagne), et
    l'application doit viser exactement cette entrée — sans cela `minimax-m3`
    (présent deux fois sous des casses différentes) serait mis à jour sur la
    mauvaise fiche.
    """
    sortie: dict[str, dict] = {}
    for modele, tarifs in _build_table(data).items():
        nom = normaliser_nom(modele)
        existant = sortie.get(nom)
        payant = tarifs.get("input", 0.0) > 0.0 or tarifs.get("output", 0.0) > 0.0
        if existant is None:
            sortie[nom] = {**tarifs, "_cle_json": modele}
        elif payant and not (existant.get("input", 0.0) or existant.get("output", 0.0)):
            sortie[nom] = {**tarifs, "_cle_json": modele}
    return sortie


def catalogue_local() -> dict[str, list[str]]:
    """Modèles actifs du registre par provider ({} si le registre manque)."""
    if not os.path.exists(CHEMIN_REGISTRE):
        return {}
    con = sqlite3.connect(CHEMIN_REGISTRE)
    try:
        par_provider: dict[str, list[str]] = {}
        for pid, mid in con.execute(
            "SELECT provider_id, id FROM models WHERE status='active'"
        ):
            par_provider.setdefault(pid, []).append(mid)
        return par_provider
    finally:
        con.close()


def _variation(local: float, externe: float) -> float | None:
    """Écart relatif ; None quand aucun référentiel n'a de sens."""
    if local == externe:
        return 0.0
    if externe > 0:
        return (local - externe) / externe
    if local > 0:
        return None  # externe à 0, local payant : écart non chiffrable, à signaler
    return 0.0


def comparer_tarifs(local: dict[str, dict], externe: dict[str, dict], seuil_pct: float) -> dict:
    """Écarts entre barème local et références externes.

    Ne compare que les modèles payants côté local : un tarif 0/0 est une
    décision (free tier, forfait, local) que les sources externes ignorent.
    """
    ecarts = []
    sans_reference = []
    for nom in sorted(local):
        tarifs = local[nom]
        if tarifs.get("input", 0.0) == 0.0 and tarifs.get("output", 0.0) == 0.0:
            continue  # gratuit/forfait/local par décision — rien à comparer
        ref = externe.get(nom)
        if ref is None:
            sans_reference.append(nom)
            continue
        fiche = {"modele": nom, "source": ref.get("source")}
        if "_cle_json" in tarifs:
            # Transmet la clé JSON exacte du barème : l'application ciblera
            # la même entrée que la comparaison (collisions de normalisation).
            fiche["_cle_json"] = tarifs["_cle_json"]
        signale = False
        for axe in ("input", "output"):
            var = _variation(tarifs.get(axe, 0.0), ref.get(axe, 0.0))
            if var is None:
                fiche[axe] = {"local": tarifs.get(axe, 0.0), "externe": ref.get(axe, 0.0),
                              "variation_pct": None}
                signale = True
            elif abs(var) * 100.0 > seuil_pct:
                fiche[axe] = {"local": tarifs.get(axe, 0.0), "externe": ref.get(axe, 0.0),
                              "variation_pct": round(var * 100.0, 1)}
                signale = True
        if signale:
            ecarts.append(fiche)
    return {"ecarts_tarifs": ecarts, "sans_reference": sans_reference}


def nouveaux_openrouter(data_brut: dict, deja_catalogues: set[str]) -> dict[str, list[str]]:
    """Modèles offerts chez nos vendors, absents du catalogue (normalisé).

    Limité aux préfixes de `PREFIXES_VENDORS` : le reste du catalogue
    OpenRouter (des centaines de modèles non câblés) n'est pas un signal.
    """
    par_vendor: dict[str, list[str]] = {}
    items = data_brut.get("data", []) if isinstance(data_brut, dict) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        ident = str(item.get("id") or "")
        if "/" not in ident:
            continue
        prefixe, nom = ident.split("/", 1)
        for pid, attendus in PREFIXES_VENDORS.items():
            if f"{prefixe}/" in attendus and normaliser_nom(nom) not in deja_catalogues:
                par_vendor.setdefault(pid, []).append(nom)
    return {pid: sorted(set(noms)) for pid, noms in par_vendor.items() if noms}


def telecharger(url: str) -> dict | None:
    """GET JSON tolérant : None en cas d'échec (la veille ne doit jamais planter)."""
    try:
        rep = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "moteur-veille/1.0"})
        rep.raise_for_status()
        return rep.json()
    except Exception as exc:
        logger.warning("[VEILLE] Source inaccessible (%s) : %s", url, type(exc).__name__)
        return None


def fantomes_depuis_inventaire(par_provider: dict[str, list[str]], instantane: dict | None) -> dict:
    """Modèles catalogués absents du listing de leur API (réutilise #T243).

    ⚠️ Même réserve que core/model_inventory.py : « absent du listing » ne
    prouve PAS qu'un modèle est mort — seul un appel réel le prouve.
    """
    from core.model_inventory import offert_par_api

    if not instantane:
        return {}
    sortie: dict[str, list[str]] = {}
    for pid, modeles in par_provider.items():
        absents = [m for m in modeles if offert_par_api(pid, m, instantane) is False]
        if absents:
            sortie[pid] = sorted(absents)
    return sortie


def _index_rates(data: dict) -> dict[str, list[tuple[str, dict]]]:
    """{nom_normalisé: [(clé_origine, dict_rates)]} sur apis[].rates.

    Une même normalisation peut recouvrir plusieurs clés (`gemini-2.5-flash`
    et `gemini-2.5-flash-paid`) : toutes sont conservées, la mise à jour
    ciblera l'entrée payante (`_cible_rates`).
    """
    index: dict[str, list[tuple[str, dict]]] = {}
    for api in data.get("apis", []):
        rates = api.get("rates") or {}
        for modele, fiche in rates.items():
            if isinstance(fiche, dict):
                index.setdefault(normaliser_nom(modele), []).append((modele, fiche))
    return index


def _cible_rates(candidats: list[tuple[str, dict]]) -> tuple[str, dict] | None:
    """Choisit l'entrée à mettre à jour : la PAYANTE, sinon la première."""
    if not candidats:
        return None
    for cle, fiche in candidats:
        if any(k.startswith(("input_cost", "output_cost")) and (v or 0) > 0
               for k, v in fiche.items()):
            return cle, fiche
    return candidats[0]


_CHAMPS_AXES = {
    "input": ("input_cost_per_m", "input_cost_per_m_eur"),
    "output": ("output_cost_per_m", "output_cost_per_m_eur"),
}


def appliquer_maj_tarifs(data: dict, fiches: list[dict]) -> list[dict]:
    """Applique les références externes aux tarifs DÉJÀ présents dans `data`.

    Fonction pure (modifie `data` en place, renvoie le journal). Règles :
      - seuls les axes signalés CHIFFRABLES (`variation_pct` non None) sont
        appliqués — un cas « local payant / externe gratuit » est un choix à
        arbitrer humainement ;
      - les entrées libellées en EUR (`*_cost_per_m_eur`, tarifs GCP) restent
        en EUR : la référence USD est convertie au taux `EUR_USD` de
        `core.pricing`, le même que celui utilisé à la comparaison ;
      - jamais de création de modèle, jamais de touche aux abonnements/local ;
      - le tarif cache n'est pas modifié (axe hors comparaison).
    """
    index = _index_rates(data)
    journal: list[dict] = []
    for fiche in fiches:
        nom = fiche.get("modele", "")
        candidats = index.get(nom, [])
        cible = None
        cle_voulue = fiche.get("_cle_json")
        if cle_voulue:
            cible = _cible_rates([(c, f) for c, f in candidats if c == cle_voulue])
        if cible is None:
            cible = _cible_rates(candidats)
        if cible is None:
            journal.append({"modele": nom, "statut": "absent_du_json"})
            continue
        cle, rates = cible
        en_eur = any(k.endswith("_eur") for k in rates)
        avant, changements = {}, {}
        for axe in ("input", "output"):
            detail = fiche.get(axe)
            if not detail or detail.get("variation_pct") is None:
                continue
            valeur_m = float(detail["externe"]) * 1_000_000
            if en_eur:
                valeur_m /= EUR_USD
            champ = next((c for c in _CHAMPS_AXES[axe] if c in rates), None)
            if champ is None:
                # Entrée payante sans champ standard (exotique) : on pose le
                # champ USD plutôt que de renoncer silencieusement.
                champ = _CHAMPS_AXES[axe][0]
            avant[axe] = rates.get(champ)
            # Arrondi 6 décimales PAR MILLION : l'erreur relative introduite
            # (< 0.001 % pour tout tarif ≥ 0.001 $/M) reste très loin sous le
            # seuil de re-comparaison, et le JSON garde des valeurs propres.
            rates[champ] = round(valeur_m, 6)
            changements[axe] = {"champ": champ, "avant": avant[axe],
                                "apres": rates[champ]}
        if changements:
            journal.append({"modele": nom, "cle_json": cle, "devise": "EUR" if en_eur else "USD",
                            "statut": "applique", "axes": changements})
        else:
            journal.append({"modele": nom, "statut": "rien_a_appliquer"})
    return journal


def _sauvegarder_pricing() -> str:
    """Backup horodaté de pricing_strategy.json (pattern `backups_prod/`)."""
    dossier = os.path.join(
        DOSSIER_BACKUPS, datetime.now().strftime("%Y-%m-%d_%H%M%S")
    )
    os.makedirs(dossier, exist_ok=True)
    destination = os.path.join(dossier, "pricing_strategy.json")
    shutil.copy2(CHEMIN_PRICING, destination)
    return destination


def appliquer_et_vérifier(rapport: dict, externe: dict, seuil_pct: float) -> dict:
    """Met à jour le barème puis VÉRIFIE en re-comparant le fichier réécrit.

    Critère de succès : plus aucun écart chiffrable sur les modèles du
    journal (les résiduels légitimes : non chiffrables, sans référence, ou
    écart sous le seuil entre la valeur exacte et l'arrondi).
    """
    fiches = rapport.get("ecarts_tarifs", [])
    if not fiches:
        return {"backup": None, "journal": [], "résiduels": [],
                "vérification": "aucun écart à appliquer"}

    backup = _sauvegarder_pricing()
    with open(CHEMIN_PRICING, encoding="utf-8") as fh:
        data = json.load(fh)
    journal = appliquer_maj_tarifs(data, fiches)

    appliques = [j for j in journal if j.get("statut") == "applique"]
    if appliques:
        safe_json_write(CHEMIN_PRICING, data)

    # Vérification : relecture du fichier écrit, re-comparaison indépendante.
    relecture = _barème_depuis_data(data)
    re_bilan = comparer_tarifs(relecture, externe, seuil_pct)
    appliqués_noms = {j["modele"] for j in appliques}
    résiduels = [f for f in re_bilan["ecarts_tarifs"] if f["modele"] in appliqués_noms]
    return {
        "backup": backup,
        "journal": journal,
        "résiduels": résiduels,
        "vérification": "ok" if not résiduels else "ÉCHEC",
    }


def lancer_veille(
    seuil_pct: float = 5.0,
    live: bool = False,
    offline: bool = False,
    appliquer: bool = False,
    exclure: tuple[str, ...] = (),
) -> dict:
    """Exécute la veille complète et écrit le rapport sur disque. Renvoie le rapport.

    Avec `appliquer=True`, met à jour les tarifs du barème après backup et
    vérifie la mise à jour (voir `appliquer_et_vérifier`). `exclure` écarte
    de l'APPLICATION les modèles normalisés donnés (les écarts restent
    affichés dans le rapport) — utile pour les arbitrages humains, ex.
    `deepseek-chat` dont la référence externe vise une version legacy.
    """
    brut_openrouter: dict | None = None
    brut_litellm: dict | None = None
    sources: dict[str, dict] = {}
    if not offline:
        brut_openrouter = telecharger(OPENROUTER_URL)
        if brut_openrouter is not None:
            sources["openrouter"] = parser_openrouter(brut_openrouter)
        brut_litellm = telecharger(LITELLM_URL)
        if brut_litellm is not None:
            sources["litellm"] = parser_litellm(brut_litellm)
    directs = cles_directes_litellm(brut_litellm or {})
    externe = fusionner_sources(
        sources.get("openrouter", {}), sources.get("litellm", {}), directs
    )

    local = barème_local()
    catalogue = catalogue_local()
    bilan = comparer_tarifs(local, externe, seuil_pct)

    deja = {normaliser_nom(m) for modeles in catalogue.values() for m in modeles}
    deja |= set(local)

    inventaire: dict | None = None
    if live:
        from core.model_inventory import rafraichir
        inventaire = rafraichir()
    else:
        from core.model_inventory import charger
        inventaire = charger()
    fantomes = fantomes_depuis_inventaire(catalogue, inventaire) if inventaire else {}

    nouveaux = (
        nouveaux_openrouter(brut_openrouter, deja) if brut_openrouter is not None else {}
    )

    rapport = {
        "genere_le": datetime.now(UTC).isoformat(timespec="seconds"),
        "seuil_pct": seuil_pct,
        "sources_disponibles": sorted(sources),
        "references_externes": len(externe),
        "modeles_locaux": len(local),
        "ecarts_tarifs": bilan["ecarts_tarifs"],
        "sans_reference": bilan["sans_reference"],
        "nouveaux_par_vendor": nouveaux,
        "fantomes_par_provider": fantomes,
        "inventaire_genere_le": (inventaire or {}).get("genere_le"),
    }

    if appliquer:
        if offline:
            logger.warning("[VEILLE] --appliquer sans source externe : rien à appliquer.")
        else:
            exclus = exclusions_effectives(exclure)
            rapport["exclusions_application"] = exclus
            à_appliquer = {
                "ecarts_tarifs": [
                    f for f in rapport["ecarts_tarifs"] if f["modele"] not in exclus
                ]
            }
            rapport["application"] = appliquer_et_vérifier(à_appliquer, externe, seuil_pct)

    os.makedirs(os.path.dirname(CHEMIN_RAPPORT), exist_ok=True)
    safe_json_write(CHEMIN_RAPPORT, rapport)
    return rapport


def afficher_resume(rapport: dict) -> None:
    """Synthèse console du rapport de veille."""
    print(f"Veille du {rapport['genere_le']} — seuil {rapport['seuil_pct']} % — "
          f"sources : {', '.join(rapport['sources_disponibles']) or 'aucune (offline)'}")
    print("-" * 72)

    ecarts = rapport["ecarts_tarifs"]
    print(f"Écarts tarifaires : {len(ecarts)} (USD par million de tokens)")
    for fiche in ecarts:
        for axe in ("input", "output"):
            detail = fiche.get(axe)
            if not detail:
                continue
            var = detail["variation_pct"]
            var_txt = "non chiffrable" if var is None else f"{var:+.1f} %"
            print(f"  {fiche['modele']:<40} {axe:<7} local {detail['local'] * 1e6:8.3f} "
                  f"/ externe {detail['externe'] * 1e6:8.3f} ({var_txt}) [{fiche['source']}]")

    sans_ref = rapport["sans_reference"]
    if sans_ref:
        print(f"Payants sans référence externe : {len(sans_ref)} — {', '.join(sans_ref)}")

    nouveaux = rapport["nouveaux_par_vendor"]
    total_nouveaux = sum(len(v) for v in nouveaux.values())
    print(f"Nouveaux modèles chez nos vendors : {total_nouveaux}")
    for pid, noms in nouveaux.items():
        print(f"  {pid:<16} {', '.join(noms)}")

    fantomes = rapport["fantomes_par_provider"]
    total_fantomes = sum(len(v) for v in fantomes.values())
    print(f"Absents du listing de leur API : {total_fantomes}")
    for pid, noms in fantomes.items():
        print(f"  {pid:<16} {', '.join(noms)}")

    application = rapport.get("application")
    if application:
        print("-" * 72)
        exclusions = rapport.get("exclusions_application") or {}
        if exclusions:
            print("Exclus de l'application : " + ", ".join(sorted(exclusions)))
        appliques = [j for j in application["journal"] if j.get("statut") == "applique"]
        print(f"Mise à jour des tarifs : {len(appliques)} modèle(s) — "
              f"backup : {application['backup']}")
        for entree in appliques:
            for axe, chgt in entree["axes"].items():
                print(f"  {entree['modele']:<40} {axe:<7} {chgt['avant']} → {chgt['apres']} "
                      f"({entree['devise']}/M)")
        autres = [j for j in application["journal"] if j.get("statut") != "applique"]
        if autres:
            print("Non appliqués : " + ", ".join(
                f"{j['modele']} ({j['statut']})" for j in autres))
        verdict = application["vérification"]
        if verdict == "ÉCHEC":
            print(f"Vérification : ÉCHEC — {len(application['résiduels'])} écart(s) "
                  f"subsiste(nt) après application :")
            for fiche in application["résiduels"]:
                print(f"  {fiche['modele']}")
        elif verdict == "ok":
            print("Vérification : OK — tous les écarts chiffrables appliqués sont résolus.")
        else:
            # « aucun écart à appliquer » : rien n'a été cassé.
            print(f"Vérification : {verdict}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seuil-pct", type=float, default=5.0,
                    help="seuil de signalement d'un écart tarifaire (défaut 5)")
    ap.add_argument("--live", action="store_true",
                    help="rafraîchit l'inventaire live des API (appels réseau)")
    ap.add_argument("--offline", action="store_true",
                    help="aucun appel réseau (barème local uniquement)")
    ap.add_argument("--appliquer", action="store_true",
                    help="met à jour les tarifs du barème (backup + vérification)")
    ap.add_argument("--exclure", action="append", default=[], metavar="MODELE",
                    help="modèle écarté de l'application (répétable) ; l'écart "
                         "reste affiché dans le rapport")
    ap.add_argument("--json", dest="json_out", help="copie du rapport JSON vers ce chemin")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rapport = lancer_veille(seuil_pct=args.seuil_pct, live=args.live,
                            offline=args.offline, appliquer=args.appliquer,
                            exclure=tuple(args.exclure))
    afficher_resume(rapport)

    if args.json_out:
        safe_json_write(args.json_out, rapport)
        print(f"\nRapport copié : {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
