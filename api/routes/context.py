"""
api/routes/context.py — Routes API Contexte IA & Configuration du Moteur.

Extrait de gui_server.py lors du refactoring Semaine 3.
Contient : /api/context-status, /api/context-reload, /api/context-ha-ingest,
           /api/config (GET + POST), /api/pricing (GET + POST),
           /api/models, /api/models/*, /api/providers, /api/keys,
           /api/pricing/auto-update

Auteur : Antigravity IDE + Axel — 2026-06-04
"""

import json
import logging
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.safe_io import file_lock, safe_json_write  # [P2-3.1] écritures atomiques

logger = logging.getLogger(__name__)

router = APIRouter()

CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.json")
PRICING_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "pricing_strategy.json")


@router.get("/api/config", tags=["Configuration"])
def get_config():
    """Retourne la configuration complète du moteur (config.json)."""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="config.json introuvable.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/config", tags=["Configuration"])
def update_config(body: dict):
    """Met à jour la configuration du moteur (merge partiel)."""
    try:
        # [P2-3.1] Read-modify-write atomique sous FileLock (aligné sur llm_gateway).
        with file_lock(CONFIG_FILE):
            with open(CONFIG_FILE, encoding="utf-8") as f:
                config = json.load(f)
            config.update(body)
            safe_json_write(CONFIG_FILE, config, lock=False)
        return {"message": "Configuration mise à jour.", "config": config}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/context-status")
def get_context_status():
    """Retourne le statut du chargement du contexte IA 3-Layers."""
    try:
        from memory.context_loader import get_context_status
        return get_context_status()
    except ImportError:
        return {"status": "unavailable", "files": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/context-reload")
async def context_reload():
    """Force le rechargement du contexte IA depuis les fichiers Markdown."""
    try:
        from memory.context_loader import reload_context
        result = await reload_context()
        return {"status": "ok", "result": result}
    except ImportError:
        return {"status": "unavailable"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/context-ha-ingest")
async def context_ha_ingest():
    """Ingère les entités et états HA dans le contexte RAG."""
    try:
        from core.ha_context_ingestor import ingest_ha_context
        result = await ingest_ha_context()
        return {"status": "ok", "entities_ingested": result}
    except ImportError:
        return {"status": "unavailable", "entities_ingested": 0}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/models")
def get_models(provider: str = None, status: str = "active"):
    """Liste tous les modèles du registre."""
    try:
        from core.models_db import get_active_models, get_all_data
        if provider:
            return get_active_models(provider_id=provider)
        return get_all_data()
    except ImportError:
        return {"error": "Module models_db non disponible", "models": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/models/stats")
def get_models_stats():
    """Retourne les statistiques d'utilisation des modèles."""
    try:
        from core.models_db import get_model_stats
        return get_model_stats()
    except ImportError:
        return {"error": "Module models_db non disponible", "stats": {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/models/registry")
def get_models_registry():
    """
    [#T158] Registre enrichi pour la vue LLMRegistry de l'IHM v2 : chaque modèle
    (actifs ET inactifs, sinon un toggle serait irréversible) est joint à ses
    métriques réelles — Elo moyen (model_elo_scores), latence moyenne (Circuit
    Breaker en mémoire, repli Elo puis ttft_ms), coût/appels 30 jours
    (token_usage), coût par tâche réussie (coût 30j / wins Elo) et état du
    Circuit Breaker. Aucune valeur inventée : les champs sans donnée sont null.

    NOTE : cette route DOIT rester déclarée avant /api/models/{model_id}
    (sinon "registry" serait capturé comme un model_id).
    """
    from core.models_db import get_all_models

    models = get_all_models()

    # ── Scores Elo + latence + wins par modèle ──
    elo_by_model: dict = {}
    try:
        from core.elo_scorer import get_all_scores
        elo_by_model = get_all_scores()
    except Exception as e:
        logger.warning(f"[REGISTRY] Scores Elo indisponibles : {e}")

    # ── Circuit Breakers (registre en mémoire du process, clé = nom lower) ──
    cb_by_name: dict = {}
    try:
        from core.llm.circuit_breaker import CircuitBreaker
        with CircuitBreaker._registry_lock:
            for cb in CircuitBreaker._registry.values():
                stats = cb.get_stats()
                cb_by_name[str(stats.get("name", "")).lower()] = stats
    except Exception as e:
        logger.warning(f"[REGISTRY] Circuit breakers indisponibles : {e}")

    # ── Coût et volume réels sur 30 jours (token_usage) ──
    usage_by_model: dict = {}
    # [#T325] Coût par tâche réussie : même source que `/api/metrics/cost-per-success`
    # (token_usage.session_id × sessions.status). L'ancien calcul local
    # `cost_30d / wins Elo` joignait deux colonnes sans intersection (l'Elo
    # score des TIERS, token_usage porte des ids de modèles) : le registre
    # affichait des chiffres absurdes. La somme des parts par modèle vaut le
    # coût total de la métrique globale.
    cout_succes_par_modele: dict = {}
    total_success_sessions = 0
    try:
        from core.elo_scorer import cost_success_par_modele
        cout_succes_par_modele, total_success_sessions = cost_success_par_modele()
    except Exception as e:
        logger.warning(f"[REGISTRY] Coût par succès indisponible : {e}")
    # ── Volume TOTAL depuis toujours (#T243) ──
    # 30 jours ne suffit pas à décider d'une désactivation : un modèle peut n'avoir
    # servi qu'une fois il y a deux mois. `calls_total` permet de distinguer
    # « inutilisé récemment » de « jamais appelé de toute l'histoire du moteur »,
    # seul critère honnête pour proposer un modèle à la désactivation.
    calls_total_by_model: dict = {}
    try:
        from core.session_history import get_token_stats
        stats_30d = get_token_stats(since_hours=30 * 24)
        for entry in stats_30d.get("by_model", []):
            usage_by_model[entry["model"]] = entry
        stats_all = get_token_stats()  # since_hours=None = tout l'historique
        for entry in stats_all.get("by_model", []):
            calls_total_by_model[entry["model"]] = entry.get("calls")
    except Exception as e:
        logger.warning(f"[REGISTRY] Stats token_usage indisponibles : {e}")

    # ── Instantané de ce que les API offrent réellement (#T243) ──
    # Lecture disque seule : rafraîchir déclencherait une douzaine d'appels réseau,
    # inacceptable sur l'affichage du registre. Le rafraîchissement est explicite
    # (POST /api/models/inventory/refresh).
    instantane = None
    try:
        from core.model_inventory import charger
        instantane = charger()
    except Exception as e:
        logger.warning(f"[REGISTRY] Instantané d'inventaire indisponible : {e}")

    # ── Modèles réellement câblés dans le gateway (63 ne le sont pas, cf. #T186) ──
    wired_names: set = set()
    try:
        from core.llm_gateway import LLMGateway
        wired_names = {str(n).lower() for n in LLMGateway().providers.keys()}
    except Exception as e:
        logger.warning(f"[REGISTRY] Gateway indisponible : {e}")

    enriched = []
    for m in models:
        model_id = m.get("id", "")
        domains = elo_by_model.get(model_id, {})

        elo_score = None
        avg_latency_elo = None
        if domains:
            elos = [d.get("elo") for d in domains.values() if d.get("elo") is not None]
            latencies = [
                d.get("avg_latency_ms") for d in domains.values()
                if d.get("avg_latency_ms")
            ]
            if elos:
                elo_score = round(sum(elos) / len(elos), 1)
            if latencies:
                avg_latency_elo = round(sum(latencies) / len(latencies), 1)

        cb = cb_by_name.get(model_id.lower())
        usage = usage_by_model.get(model_id, {})
        cost_30d = usage.get("cost_usd")

        cost_per_success = None
        cout_succes_modele = cout_succes_par_modele.get(model_id)
        if cout_succes_modele is not None and total_success_sessions > 0:
            cost_per_success = round(cout_succes_modele / total_success_sessions, 6)

        avg_latency_ms = None
        if cb and cb.get("avg_latency_ms") is not None:
            avg_latency_ms = cb["avg_latency_ms"]
        elif avg_latency_elo is not None:
            avg_latency_ms = avg_latency_elo
        elif m.get("ttft_ms"):
            avg_latency_ms = m["ttft_ms"]

        enriched.append({
            **m,
            "elo_score": elo_score,
            "avg_latency_ms": avg_latency_ms,
            "cost_usd_30d": cost_30d,
            "calls_30d": usage.get("calls"),
            "calls_total": calls_total_by_model.get(model_id, 0),
            "cost_per_success": cost_per_success,
            "circuit_breaker_status": cb.get("state") if cb else None,
            "is_wired": model_id.lower() in wired_names,
            # True/False/None — None = on ne sait pas (aucun inventaire, ou provider
            # muet au dernier passage). Ne JAMAIS assimiler None à False.
            "offered_by_api": _offert(m.get("provider_id", ""), model_id, instantane),
        })

    from core.model_inventory import resume
    return {"models": enriched, "count": len(enriched), "inventaire": resume(instantane)}


def _offert(provider_id: str, model_id: str, instantane) -> bool | None:
    """Enveloppe tolérante : l'absence d'inventaire ne doit pas casser le registre."""
    try:
        from core.model_inventory import offert_par_api
        return offert_par_api(provider_id, model_id, instantane)
    except Exception:
        return None


@router.get("/api/models/inventory")
def get_model_inventory():
    """
    [#T243] Bilan du dernier inventaire, sans la liste complète des modèles.

    Endpoint distinct de `/api/models/registry` à dessein : ce dernier alimente une
    clé React Query partagée par quatre vues, dont le contrat ne doit pas changer.
    """
    from core.model_inventory import charger, resume
    return {"inventaire": resume(charger())}


@router.post("/api/models/inventory/refresh")
async def refresh_model_inventory():
    """
    [#T243] Interroge les endpoints de listing de tous les providers et réécrit
    l'instantané lu par `/api/models/registry`.

    Une douzaine d'appels réseau : exécuté dans un thread pour ne pas bloquer la
    boucle asyncio (même discipline que le hot-path LLM migré en D5).

    ⚠️ Le résultat dit ce que chaque API **annonce**, pas ce qu'elle **sert** : un
    alias encore fonctionnel peut être absent du listing (cas vérifié de
    `deepseek-chat`). À traiter comme un indice, jamais comme une preuve de mort.
    """
    import asyncio

    from core.model_inventory import rafraichir, resume

    try:
        instantane = await asyncio.to_thread(rafraichir)
    except Exception as e:
        logger.error(f"[INVENTAIRE] Échec du rafraîchissement : {e}")
        raise HTTPException(
            status_code=500, detail=f"Rafraîchissement impossible : {e}"
        ) from e
    return {"status": "ok", "inventaire": resume(instantane)}


class BulkStatusBody(BaseModel):
    """Corps de `POST /api/models/bulk-status`."""

    ids: list[str] = Field(..., min_length=1, max_length=500)
    status: str


@router.post("/api/models/bulk-status")
def bulk_set_models_status(body: BulkStatusBody):
    """
    [#T243] Active ou désactive PLUSIEURS modèles en un appel.

    L'interrupteur unitaire (`/api/models/{id}/toggle`) existe depuis #T158 mais
    n'avait jamais servi : sur 94 modèles actifs en production, aucun n'était
    désactivé. La cause n'était pas l'absence de bouton mais l'absence d'action en
    lot — personne ne bascule 84 modèles un par un. D'où cette route.

    Volontairement `status` explicite plutôt qu'une bascule : appliquer un toggle à
    une sélection hétérogène inverserait chaque modèle par rapport à SON état, ce qui
    n'est jamais l'intention quand on coche « désactiver ces 40 modèles ».

    Les identifiants inconnus sont signalés dans `introuvables` sans faire échouer le
    lot : une sélection issue d'une liste rafraîchie entre-temps ne doit pas être
    entièrement perdue pour un modèle disparu.
    """
    from core.models_db import get_model, set_model_status

    if body.status not in ("active", "inactive"):
        raise HTTPException(
            status_code=422,
            detail="status doit valoir 'active' ou 'inactive'.",
        )

    modifies: list[str] = []
    inchanges: list[str] = []
    introuvables: list[str] = []
    echecs: list[str] = []

    for model_id in dict.fromkeys(body.ids):  # dédoublonne en gardant l'ordre
        model = get_model(model_id)
        if model is None:
            introuvables.append(model_id)
            continue
        
        # [#T380] Même si le statut est identique, on appelle set_model_status
        # pour forcer la libération de la revendication de la sonde (desactive_par_sonde = 0).
        if model.get("status") == body.status and not model.get("desactive_par_sonde"):
            inchanges.append(model_id)
            continue
        if set_model_status(model_id, body.status):
            modifies.append(model_id)
        else:
            echecs.append(model_id)

    if echecs:
        logger.error("[BULK-STATUS] Échec d'écriture pour : %s", ", ".join(echecs))

    logger.info(
        "[BULK-STATUS] statut='%s' — %d modifié(s), %d inchangé(s), %d introuvable(s), %d échec(s).",
        body.status, len(modifies), len(inchanges), len(introuvables), len(echecs),
    )
    return {
        "status": "ok" if not echecs else "partiel",
        "applique": body.status,
        "modifies": modifies,
        "inchanges": inchanges,
        "introuvables": introuvables,
        "echecs": echecs,
    }


@router.post("/api/models/{model_id}/toggle")
def toggle_model(model_id: str):
    """
    [#T158] Bascule le statut actif/inactif d'un modèle (UPDATE ciblé).
    status='inactive' suffit à retirer le modèle du routage par tier (#T185).
    """
    from core.models_db import get_model, set_model_status
    model = get_model(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Modèle '{model_id}' introuvable.")
    new_status = "inactive" if model.get("status") == "active" else "active"
    if not set_model_status(model_id, new_status):
        raise HTTPException(status_code=500, detail="Échec de l'écriture en base.")
    return {"enabled": new_status == "active", "status": new_status}


@router.post("/api/models/{model_id}/routing-tier")
def update_model_routing_tier(model_id: str, body: dict):
    """
    [#T185/#T186] Change le routing_tier (leger/moyen/fort ou null) d'un modèle
    depuis l'IHM — UPDATE ciblé qui ne touche à aucun autre champ.
    """
    from core.models_db import get_model, set_model_routing_tier
    if get_model(model_id) is None:
        raise HTTPException(status_code=404, detail=f"Modèle '{model_id}' introuvable.")
    routing_tier = body.get("routing_tier")
    try:
        if not set_model_routing_tier(model_id, routing_tier):
            raise HTTPException(status_code=500, detail="Échec de l'écriture en base.")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"status": "ok", "model_id": model_id, "routing_tier": routing_tier}


@router.post("/api/models/{model_id}/ping")
async def ping_model(model_id: str):
    """
    [#T158] Test de vivacité réel d'un modèle : mini-génération chronométrée via
    le gateway (donc avec Circuit Breaker). Les modèles du catalogue non câblés
    dans le gateway (#T186) répondent ok=false avec un message explicite.
    """
    import time as _time

    from core.llm_gateway import LLMGateway

    gateway = LLMGateway()
    try:
        provider = gateway.get_provider(model_id)
    except ValueError:
        return {
            "ok": False,
            "latency_ms": 0,
            "error": "Modèle au catalogue mais non câblé dans le gateway (cf. #T186).",
        }

    start = _time.perf_counter()
    try:
        import asyncio as _asyncio
        # [#T299] Le ping n'appartient à AUCUNE session : test de vivacité
        # ponctuel déclenché depuis l'IHM. La valeur fixe historique
        # "hmi_ping" était un faux rattachement (garde-fou n°1) : sans
        # session_id, la ligne part avec NULL, la seule valeur honnête.
        await _asyncio.wait_for(
            provider.generate_async(
                system_prompt="Réponds uniquement : pong",
                user_prompt="ping",
            ),
            timeout=20.0,
        )
        return {"ok": True, "latency_ms": round((_time.perf_counter() - start) * 1000)}
    except Exception as e:
        return {
            "ok": False,
            "latency_ms": round((_time.perf_counter() - start) * 1000),
            "error": str(e)[:200],
        }


@router.get("/api/models-health")
def get_models_health():
    """
    [#T333] État de la sonde de vivacité : qui répond, qui est muet, qui a été
    éteint automatiquement.

    Une sonde dont personne ne voit les résultats reproduirait le défaut qu'elle
    corrige — les 6 modèles disjonctés du 12/08 n'étaient visibles qu'en lisant
    le journal du serveur.

    Le bloc `catalogue` rend visible SANS ouvrir SQLite la zone grise mesurée
    le 17/08 (52 modèles actifs sans routing_tier, sondés chaque heure, jamais
    choisis par le routeur) : profondeur réelle de chaque tier, modèles sondés
    non routables, et verdict de la règle de justification par spécialité
    (cf. core.models_db.audit_zone_grise). Forme additive : les clés existantes
    (sonde, modeles, muets, eteints_par_la_sonde) restent inchangées.
    """
    from core.model_health_probe import intervalle_sonde, peut_desactiver, sonde_activee
    from core.runtime_db import get_connection

    conn = get_connection()
    lignes = conn.execute(
        "SELECT model_id, dernier_test, dernier_succes, echecs_consecutifs, "
        "succes_consecutifs, desactive_par_sonde, derniere_erreur, latence_ms "
        "FROM model_health ORDER BY echecs_consecutifs DESC, model_id"
    ).fetchall()

    modeles = [
        {
            "model_id": ligne[0], "dernier_test": ligne[1], "dernier_succes": ligne[2],
            "echecs_consecutifs": ligne[3], "succes_consecutifs": ligne[4],
            "desactive_par_sonde": bool(ligne[5]), "derniere_erreur": ligne[6],
            "latence_ms": ligne[7],
        }
        for ligne in lignes
    ]

    # ── Zone grise : sondés (actifs, testés chaque cycle) mais non routables ──
    # Un audit en échec ne doit pas priver l'endpoint de santé de sa forme
    # historique : le bloc `catalogue` se dégrade en erreur explicite.
    try:
        from core.models_db import audit_zone_grise, compter_profondeur_tiers

        audit = audit_zone_grise()
        actifs_hors_tier = set(audit["hors_tier"])
        sondes_non_routables = [
            m["model_id"] for m in modeles if m["model_id"] in actifs_hors_tier
        ]
        catalogue = {
            "profondeur_tiers": compter_profondeur_tiers(),
            "sondes_non_routables": sondes_non_routables,
            "nb_sondes_non_routables": len(sondes_non_routables),
            "zone_grise": audit["zone_grise"],
            "justifies_hors_routage": audit["justifies"],
            "conforme": audit["conforme"],
        }
    except Exception as e:
        logger.warning(f"[MODELS-HEALTH] Audit de la zone grise indisponible : {e}")
        catalogue = {"erreur": str(e)}

    return {
        "sonde": {
            "activee": sonde_activee(),
            "action_automatique": peut_desactiver(),
            "intervalle_s": intervalle_sonde(),
        },
        "modeles": modeles,
        "muets": [m["model_id"] for m in modeles if m["echecs_consecutifs"] > 0],
        "eteints_par_la_sonde": [m["model_id"] for m in modeles if m["desactive_par_sonde"]],
        "catalogue": catalogue,
    }


@router.post("/api/models-health/refresh")
async def refresh_models_health():
    """[#T333] Déclenche un cycle de sonde immédiat (bouton IHM / diagnostic)."""
    from core.model_health_probe import executer_cycle
    return await executer_cycle()


@router.get("/api/quotas/alertes")
def get_quotas_alertes():
    """
    [#T334] État de l'alerte soldes/quotas : seuils, mode, hystérésis.

    Une alerte dont personne ne voit l'état reproduirait le défaut qu'elle
    corrige — le 12/08, personne n'a su avant la mesure que la clé DashScope
    était morte. Ne déclenche AUCUN appel aux API de facturation : c'est une
    lecture de l'état collecté par `quota_refresh_loop`.
    """
    from core.quota_alert import (
        alerte_activee,
        confirmation_cycles,
        intervalle_alerte,
        lire_etat,
        mode_observation,
        seuil_saturation,
        seuil_solde,
        sortie_cycles,
    )
    return {
        "alerte": {
            "activee": alerte_activee(),
            "observation": mode_observation(),
            "intervalle_s": intervalle_alerte(),
            "seuil_saturation_pct": seuil_saturation(),
            "seuil_solde_usd": seuil_solde(),
            "confirmation_cycles": confirmation_cycles(),
            "sortie_cycles": sortie_cycles(),
        },
        "etat": lire_etat(),
    }


@router.post("/api/quotas/alertes/cycle")
async def refresh_quotas_alertes():
    """[#T334] Déclenche un cycle d'alerte immédiat (bouton IHM / diagnostic)."""
    from core.quota_alert import executer_cycle_alerte
    return await executer_cycle_alerte()


@router.get("/api/models/{model_id}")
def get_model_detail(model_id: str):
    """Retourne les détails d'un modèle spécifique."""
    try:
        from core.models_db import get_model_by_id
        model = get_model_by_id(model_id)
        if model is None:
            raise HTTPException(status_code=404, detail=f"Modèle '{model_id}' introuvable.")
        return model
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/providers")
def get_providers():
    """Liste tous les providers LLM configurés avec leur disponibilité."""
    try:
        from core.llm_gateway import LLMGateway
        gateway = LLMGateway()
        return gateway.get_providers_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/keys")
def get_keys_status():
    """Retourne le statut des clés API (présence, non valeur)."""
    api_keys = {
        "GEMINI_API_KEY": bool(os.environ.get("GEMINI_API_KEY")),
        "DEEPSEEK_API_KEY": bool(os.environ.get("DEEPSEEK_API_KEY")),
        "ANTHROPIC_API_KEY": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "OPENROUTER_API_KEY": bool(os.environ.get("OPENROUTER_API_KEY")),
        "MISTRAL_API_KEY": bool(os.environ.get("MISTRAL_API_KEY")),
        "COHERE_API_KEY": bool(os.environ.get("COHERE_API_KEY")),
        # Diagnostic de présence de la clé HASS_TOKEN (T239) — volontairement
        # hors accesseur get_ha_token() : la sémantique est « cette clé précise
        # existe-t-elle », pas « donne-moi le token ».
        "HASS_TOKEN": bool(os.environ.get("HASS_TOKEN")),
    }
    return {"keys": api_keys, "configured_count": sum(api_keys.values())}


@router.get("/api/pricing")
def get_pricing():
    """Retourne la stratégie de pricing des modèles LLM."""
    try:
        with open(PRICING_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"error": "pricing_strategy.json introuvable", "models": {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/pricing")
def update_pricing(body: dict):
    """Met à jour la stratégie de pricing."""
    try:
        # [P2-3.1] Read-modify-write atomique sous FileLock.
        with file_lock(PRICING_FILE):
            pricing = {}
            try:
                with open(PRICING_FILE, encoding="utf-8") as f:
                    pricing = json.load(f)
            except FileNotFoundError:
                pass
            pricing.update(body)
            safe_json_write(PRICING_FILE, pricing, lock=False)
        return {"message": "Pricing mis à jour.", "pricing": pricing}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/pricing/auto-update")
async def pricing_auto_update():
    """Met à jour automatiquement les pricing depuis les APIs providers."""
    try:
        from core.pricing_updater import auto_update_pricing
        result = await auto_update_pricing()
        return {"status": "ok", "updated": result}
    except ImportError:
        return {"status": "unavailable", "updated": 0}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
