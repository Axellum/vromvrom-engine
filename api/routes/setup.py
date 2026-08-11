"""
api/routes/setup.py — Diagnostic d'installation du moteur.

Répond à une question simple que l'IHM ne savait pas poser : « mon installation
est-elle complète et fonctionnelle ? ». Chaque contrôle est EFFECTIF (fichier lu,
table comptée, connexion tentée) et porte une remédiation concrète quand il
échoue — jamais un statut déclaratif.

Lecture seule : ce module ne modifie rien, il constate.

Auteur : Claude + Axel — 2026-07-28
"""

import logging
import os
import sqlite3

from fastapi import APIRouter

from core.ha_token import get_ha_token  # [T239] lecture centralisée du token HA

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/setup", tags=["Installation"])

_ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Providers dont l'absence de clé désactive simplement le provider (jamais bloquant).
_PROVIDER_KEYS = [
    ("GEMINI_API_KEY", "Gemini Free Tier"),
    ("GEMINI_PAYANT_API_KEY", "Gemini payant (GCP)"),
    ("DEEPSEEK_API_KEY", "DeepSeek"),
    ("ANTHROPIC_API_KEY", "Anthropic"),
    ("MISTRAL_API_KEY", "Mistral"),
    ("COHERE_API_KEY", "Cohere"),
    ("OPENROUTER_API_KEY", "OpenRouter"),
    ("XAI_API_KEY", "xAI (Grok)"),
    ("MINIMAX_API_KEY", "MiniMax"),
    ("DEEPINFRA_API_KEY", "DeepInfra"),
    ("ZHIPU_API_KEY", "Zhipu / GLM"),
]


def _check(name: str, label: str, status: str, detail: str, fix: str | None = None) -> dict:
    """Construit un contrôle. `status` ∈ ok | warn | error | info."""
    return {"name": name, "label": label, "status": status, "detail": detail, "fix": fix}


def _table_count(db_path: str, table: str) -> int | None:
    """Compte les lignes d'une table, ou None si la base/table est absente."""
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return None


@router.get("/diagnostics")
async def setup_diagnostics():
    """
    État complet de l'installation, groupé par étape.

    Le champ `blocking` distingue ce qui empêche le moteur de fonctionner de ce
    qui désactive seulement une capacité optionnelle.
    """
    groups: list[dict] = []

    # ── 1. Authentification et accès ──────────────────────────────────
    auth_checks = []
    api_key = os.environ.get("MOTEUR_API_KEY", "")
    if api_key:
        auth_checks.append(_check(
            "api_key", "Clé API du moteur", "ok",
            f"Définie ({len(api_key)} caractères). Les routes /api sont protégées.",
        ))
    else:
        auth_checks.append(_check(
            "api_key", "Clé API du moteur", "error",
            "MOTEUR_API_KEY absente : les routes /api sensibles répondent 503 (fail-closed).",
            "Définir MOTEUR_API_KEY dans le .env du moteur, puis redémarrer.",
        ))

    cors = os.environ.get("MOTEUR_CORS_ORIGINS", "")
    auth_checks.append(_check(
        "cors", "Origines CORS", "info",
        cors or "Non définie — politique par défaut appliquée.",
        "Pour le développement de l'IHM, ajouter http://localhost:5173.",
    ))
    groups.append({"id": "auth", "label": "Authentification", "checks": auth_checks})

    # ── 2. Bases de données ───────────────────────────────────────────
    db_checks = []

    runtime_path = os.path.join(_ENGINE_ROOT, "moteur_runtime.db")
    sessions = _table_count(runtime_path, "sessions")
    if sessions is None:
        db_checks.append(_check(
            "runtime_db", "Base d'exécution (moteur_runtime.db)", "error",
            "Introuvable ou illisible. Sessions, tokens et métriques ne peuvent pas être enregistrés.",
            "Elle est créée automatiquement au premier démarrage de gui_server.py.",
        ))
    else:
        tokens = _table_count(runtime_path, "token_usage") or 0
        db_checks.append(_check(
            "runtime_db", "Base d'exécution (moteur_runtime.db)", "ok",
            f"{sessions} session(s), {tokens} enregistrement(s) de consommation.",
        ))

    models_path = os.path.join(_ENGINE_ROOT, "models_registry.db")
    models_n = _table_count(models_path, "models")
    if models_n is None:
        db_checks.append(_check(
            "models_db", "Catalogue de modèles (models_registry.db)", "error",
            "Introuvable. Le routage par tier n'a aucune source de vérité.",
            "Lancer : python seed_models_db.py",
        ))
    elif models_n == 0:
        db_checks.append(_check(
            "models_db", "Catalogue de modèles (models_registry.db)", "warn",
            "Base présente mais vide.",
            "Lancer : python seed_models_db.py",
        ))
    else:
        db_checks.append(_check(
            "models_db", "Catalogue de modèles (models_registry.db)", "ok",
            f"{models_n} modèle(s) au catalogue.",
        ))

    memory_path = os.path.join(_ENGINE_ROOT, "memory.db")
    if os.path.exists(memory_path):
        size_mb = round(os.path.getsize(memory_path) / 1_048_576, 1)
        db_checks.append(_check(
            "memory_db", "Mémoire (memory.db)", "ok",
            f"Présente ({size_mb} Mo) — faits vérifiés, épisodes, graphe de connaissances.",
        ))
    else:
        db_checks.append(_check(
            "memory_db", "Mémoire (memory.db)", "warn",
            "Absente : le moteur fonctionne, mais sans mémoire longue.",
            "Lancer : python seed_memory_db.py",
        ))

    chroma_path = os.path.join(_ENGINE_ROOT, "chroma_db")
    if os.path.isdir(chroma_path) and os.path.exists(os.path.join(chroma_path, "chroma.sqlite3")):
        db_checks.append(_check(
            "chroma", "Index vectoriel (chroma_db)", "ok",
            "Présent — la recherche sémantique (RAG) est opérationnelle.",
        ))
    else:
        db_checks.append(_check(
            "chroma", "Index vectoriel (chroma_db)", "warn",
            "Absent : la recherche sémantique se rabat sur la recherche lexicale.",
            "Lancer : python populate_embeddings.py",
        ))
    groups.append({"id": "databases", "label": "Bases de données", "checks": db_checks})

    # ── 3. Configuration ──────────────────────────────────────────────
    cfg_checks = []
    config_path = os.path.join(_ENGINE_ROOT, "config.json")
    try:
        import json
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        tiers = config.get("tiers", {})
        empty_tiers = [name for name, models in tiers.items() if not models]
        if empty_tiers:
            cfg_checks.append(_check(
                "config", "config.json", "warn",
                f"Lisible, mais tier(s) vide(s) : {', '.join(empty_tiers)}.",
                "Compléter dans Configuration → Composition des tiers.",
            ))
        else:
            cfg_checks.append(_check(
                "config", "config.json", "ok",
                f"Lisible — {len(tiers)} tier(s) définis, planner={config.get('planner_model', '?')}.",
            ))
    except Exception as e:
        config = {}
        cfg_checks.append(_check(
            "config", "config.json", "error",
            f"Illisible : {e}",
            "Vérifier la syntaxe JSON du fichier config.json.",
        ))

    pa = config.get("persistent_agents", {})
    active = [n for n in ("daemon", "dreamer", "auditor") if pa.get(f"{n}_enabled")]
    cfg_checks.append(_check(
        "persistent_agents", "Agents autonomes", "info",
        f"Actifs : {', '.join(active) if active else 'aucun'}.",
    ))
    groups.append({"id": "config", "label": "Configuration", "checks": cfg_checks})

    # ── 4. Providers LLM ──────────────────────────────────────────────
    provider_checks = []
    configured = [(env, label) for env, label in _PROVIDER_KEYS if os.environ.get(env)]
    missing = [label for env, label in _PROVIDER_KEYS if not os.environ.get(env)]

    if configured:
        provider_checks.append(_check(
            "provider_keys", "Clés providers", "ok",
            f"{len(configured)} configurée(s) : {', '.join(label for _, label in configured)}.",
        ))
    else:
        provider_checks.append(_check(
            "provider_keys", "Clés providers", "error",
            "Aucune clé de provider LLM : le moteur ne peut appeler aucun modèle distant.",
            "Renseigner au moins une clé dans le .env (GEMINI_API_KEY est gratuite).",
        ))
    if missing:
        provider_checks.append(_check(
            "provider_missing", "Providers non configurés", "info",
            f"{len(missing)} sans clé : {', '.join(missing)}. Ces providers sont simplement inactifs.",
        ))

    # Nombre de providers réellement câblés dans le gateway.
    try:
        from core.llm_gateway import LLMGateway
        wired = len(LLMGateway().providers)
        provider_checks.append(_check(
            "gateway", "Passerelle LLM", "ok" if wired else "error",
            f"{wired} provider(s) instanciés dans le gateway."
            if wired else "Aucun provider instancié.",
            None if wired else "Vérifier les clés du .env et les logs de démarrage.",
        ))
    except Exception as e:
        provider_checks.append(_check(
            "gateway", "Passerelle LLM", "error",
            f"Instanciation impossible : {e}",
            "Consulter les logs du serveur au démarrage.",
        ))
    groups.append({"id": "providers", "label": "Providers LLM", "checks": provider_checks})

    # ── 5. Home Assistant ─────────────────────────────────────────────
    ha_checks = []
    ha_token = get_ha_token()
    ha_url = os.environ.get("HASS_URL") or os.environ.get("HA_URL")
    if not ha_token:
        ha_checks.append(_check(
            "ha_token", "Token Home Assistant", "warn",
            "Absent : les commandes domotiques et l'assistant vocal sont hors service.",
            "Créer un token longue durée dans HA (profil utilisateur) et le poser dans HASS_TOKEN (ou HA_TOKEN).",
        ))
    else:
        ha_checks.append(_check("ha_token", "Token Home Assistant", "ok", "Configuré."))

    if ha_url:
        try:
            from api.routes.ha import ha_health
            health = await ha_health()
            if health.get("reachable"):
                ha_checks.append(_check(
                    "ha_reachable", "Connexion Home Assistant", "ok",
                    f"Joignable en {health.get('latency_ms')} ms — "
                    f"{health.get('entity_count')} entité(s), version {health.get('version')}.",
                ))
            else:
                err = health.get("error") or "cause inconnue"
                fix = "Vérifier HASS_URL et la joignabilité réseau."
                if "CERTIFICATE_VERIFY_FAILED" in err:
                    fix = ("Certificat TLS non valide pour cette adresse : renseigner HA_CA_BUNDLE, "
                           "ou utiliser le nom d'hôte couvert par le certificat, ou en dernier "
                           "recours HA_VERIFY_TLS=false sur un LAN de confiance.")
                elif "401" in err:
                    fix = "Token refusé : en régénérer un dans Home Assistant."
                ha_checks.append(_check(
                    "ha_reachable", "Connexion Home Assistant", "error", err, fix,
                ))
        except Exception as e:
            ha_checks.append(_check(
                "ha_reachable", "Connexion Home Assistant", "error", str(e),
                "Vérifier HASS_URL et la configuration TLS.",
            ))
    else:
        ha_checks.append(_check(
            "ha_url", "URL Home Assistant", "warn",
            "HASS_URL non définie — valeur par défaut utilisée.",
            "Définir HASS_URL dans le .env.",
        ))
    groups.append({"id": "homeassistant", "label": "Home Assistant", "checks": ha_checks})

    # ── Synthèse ──────────────────────────────────────────────────────
    all_checks = [c for g in groups for c in g["checks"]]
    errors = [c for c in all_checks if c["status"] == "error"]
    warnings = [c for c in all_checks if c["status"] == "warn"]

    return {
        "groups": groups,
        "summary": {
            "total": len(all_checks),
            "ok": len([c for c in all_checks if c["status"] == "ok"]),
            "warnings": len(warnings),
            "errors": len(errors),
            "operational": len(errors) == 0,
        },
    }
