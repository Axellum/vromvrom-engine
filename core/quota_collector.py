"""
core/quota_collector.py — Collecteur de quotas temps réel unifié.

Agrège les quotas depuis toutes les sources et met à jour
la table quota_realtime dans models_registry.db.

Sources :
  1. Calcul glissant depuis session_history.db (RPM/TPM/RPD/TPH par canal)
  2. Balance DeepSeek (GET /user/balance)
  3. Claude CLI /usage (optionnel, ~2s, toutes les 5min)

Fréquence :
  - Périodique : quota_refresh_loop() lancée par gui_server au démarrage,
    intervalle configurable via QUOTA_REFRESH_INTERVAL_SECONDS (défaut 300 s),
    include_claude=False par défaut (scraping Claude jamais automatique)
  - Manuel via POST /api/quotas/refresh ou bouton HMI
  - Claude /usage : toutes les 5min + bouton manuel

Auteur : Antigravity IDE + Axel
Date : 2026-05-26
"""

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Any

import requests

logger = logging.getLogger("core.quota_collector")

# Timestamp du dernier refresh Claude /usage (max toutes les 5min)
_last_claude_refresh: float = 0.0
_CLAUDE_REFRESH_INTERVAL = 300  # 5 minutes

# ── Intervalle du rafraîchissement périodique (#T269) ──
QUOTA_REFRESH_INTERVAL_ENV = "QUOTA_REFRESH_INTERVAL_SECONDS"
QUOTA_REFRESH_INTERVAL_DEFAULT = 300  # 5 min : compromis fraîcheur / coût API DeepSeek
QUOTA_REFRESH_INTERVAL_MIN = 60       # Borne basse : ne jamais marteler les APIs


def get_quota_refresh_interval() -> int:
    """Intervalle (secondes) du refresh périodique, lu depuis l'environnement.

    Variable : QUOTA_REFRESH_INTERVAL_SECONDS (défaut 300 s = 5 min).
    Une valeur invalide retombe sur le défaut ; une valeur trop basse est
    bornée à QUOTA_REFRESH_INTERVAL_MIN.
    """
    raw = os.environ.get(QUOTA_REFRESH_INTERVAL_ENV, str(QUOTA_REFRESH_INTERVAL_DEFAULT))
    try:
        interval = int(raw)
    except ValueError:
        logger.warning(
            f"[QUOTAS] {QUOTA_REFRESH_INTERVAL_ENV} invalide ({raw!r}), "
            f"défaut {QUOTA_REFRESH_INTERVAL_DEFAULT}s."
        )
        interval = QUOTA_REFRESH_INTERVAL_DEFAULT
    return max(interval, QUOTA_REFRESH_INTERVAL_MIN)


# ══════════════════════════════════════════════════════════════════
# 1. Quotas glissants depuis session_history.db
# ══════════════════════════════════════════════════════════════════

def _collect_sliding_quotas() -> dict[str, dict[str, int]]:
    """Calcule les quotas glissants depuis la table token_usage de session_history.db.
    
    Retourne un dict par canal (ex: 'gemini-free-flash') avec les compteurs
    RPM, TPM, RPD, TPH, mensuel.
    """
    try:
        from core.session_history import get_quotas_from_db
        db_result = get_quotas_from_db()
        if db_result:
            return db_result
    except Exception as e:
        logger.warning(f"[QUOTAS] session_history.get_quotas_from_db indisponible : {e}")

    # Fallback : calcul depuis le token_tracker JSON
    try:
        from core.token_tracker import get_quotas_status
        return get_quotas_status()
    except Exception as e:
        logger.warning(f"[QUOTAS] Fallback token_tracker aussi échoué : {e}")
        return {}


# ══════════════════════════════════════════════════════════════════
# 2. Balance DeepSeek (API réelle)
# ══════════════════════════════════════════════════════════════════

def _collect_deepseek_balance() -> float | None:
    """Interroge l'API DeepSeek /user/balance pour le solde prépayé.
    
    Returns:
        Le solde total en USD (converti depuis CNY si nécessaire), ou None si erreur.
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        logger.debug("[QUOTAS] Pas de clé DeepSeek, skip balance.")
        return None

    try:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        resp = requests.get(
            "https://api.deepseek.com/user/balance",
            headers=headers,
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("is_available"):
            logger.warning("[QUOTAS] DeepSeek balance : compte indisponible")
            return 0.0

        # Calculer le solde total en USD
        total_usd = 0.0
        for bi in data.get("balance_infos", []):
            total = float(bi.get("total_balance", 0))
            currency = bi.get("currency", "CNY")
            if currency == "CNY":
                total_usd += total / 7.25  # Taux approximatif CNY → USD
            elif currency == "USD":
                total_usd += total
            else:
                total_usd += total  # Fallback

        logger.info(f"[QUOTAS] DeepSeek balance : ${total_usd:.2f} USD")
        return round(total_usd, 4)

    except Exception as e:
        logger.warning(f"[QUOTAS] Erreur DeepSeek balance : {e}")
        return None


# ══════════════════════════════════════════════════════════════════
# 3. Claude CLI /usage (optionnel, lent ~2s)
# ══════════════════════════════════════════════════════════════════

def _collect_claude_usage(force: bool = False) -> float | None:
    """Parse la sortie de 'claude /usage' pour le pourcentage d'utilisation.
    
    Args:
        force: Si True, ignore l'intervalle de 5min et force le refresh.
    
    Returns:
        Pourcentage d'utilisation (0-100), ou None si erreur/skip.
    """
    global _last_claude_refresh

    # Vérifier l'intervalle (5 minutes sauf si force)
    now = time.time()
    if not force and (now - _last_claude_refresh) < _CLAUDE_REFRESH_INTERVAL:
        logger.debug("[QUOTAS] Claude /usage : skip (intervalle 5min non atteint)")
        return None

    import shutil
    import subprocess

    # Vérifier que claude.cmd est accessible
    claude_path = shutil.which("claude") or shutil.which("claude.cmd")
    if not claude_path:
        logger.debug("[QUOTAS] Claude CLI non trouvé, skip usage.")
        return None

    try:
        result = subprocess.run(
            [claude_path, "/usage"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        output = result.stdout + result.stderr

        # Parser le pourcentage dans la sortie
        # Format typique : "Plan usage: 42% (15,000 / 35,000 tokens)"
        import re
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", output)
        if match:
            pct = float(match.group(1))
            _last_claude_refresh = now
            logger.info(f"[QUOTAS] Claude usage : {pct}%")

            # Stocker le texte complet dans real_billing
            try:
                from core.token_tracker import update_real_billing
                update_real_billing(
                    claude_message_usage_pct=int(pct),
                    claude_summary_text=output.strip()[:500],
                )
            except Exception:
                pass

            return pct
        else:
            logger.debug(f"[QUOTAS] Claude /usage : pas de % trouvé dans : {output[:200]}")
            _last_claude_refresh = now  # Éviter de réessayer trop vite
            return None

    except subprocess.TimeoutExpired:
        logger.warning("[QUOTAS] Claude /usage : timeout (10s)")
        return None
    except Exception as e:
        logger.warning(f"[QUOTAS] Erreur Claude /usage : {e}")
        return None


# ══════════════════════════════════════════════════════════════════
# Orchestrateur principal
# ══════════════════════════════════════════════════════════════════

def _get_gemini_free_key_ids() -> list[str]:
    """Ids des clés Gemini Free depuis api_keys (SSOT seed) — jamais en dur ici.

    La liste est dérivée de core.models_db.get_all_api_keys() (provider_id
    'gemini_free', key_type 'free') : une clé ajoutée au KeyPool et seedée
    (ex. GEMINI_API_KEY_6) est couverte automatiquement (#T269).
    Fallback d'urgence = miroir de core/key_pool.py si la BDD est indisponible.
    """
    try:
        from core.models_db import get_all_api_keys
        keys = get_all_api_keys(hide_values=True)
        free = [
            k["id"] for k in keys
            if k.get("provider_id") == "gemini_free" and k.get("key_type") == "free"
        ]
        if free:
            return free
    except Exception as e:
        logger.warning(f"[QUOTAS] api_keys indisponible, fallback liste statique : {e}")
    return [
        "GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3",
        "GEMINI_API_KEY_4", "GEMINI_API_KEY_5", "GEMINI_API_KEY_6",
    ]

def refresh_all_quotas(include_claude: bool = False, force_claude: bool = False) -> dict[str, Any]:
    """Rafraîchit tous les quotas et met à jour quota_realtime dans la BDD.
    
    Args:
        include_claude: Si True, inclut le refresh Claude /usage (~2s)
        force_claude: Si True, force le refresh Claude même si l'intervalle
                      de 5min n'est pas atteint.
    
    Returns:
        Résumé du refresh avec le statut de chaque source.
    """
    from core.models_db import update_quota_realtime

    result = {
        "timestamp": datetime.now().isoformat(),
        "sources": {},
        "updated_keys": 0,
        "errors": [],
    }

    # ── 1. Quotas glissants ──
    try:
        sliding = _collect_sliding_quotas()
        result["sources"]["sliding_quotas"] = "ok"
    except Exception as e:
        sliding = {}
        result["sources"]["sliding_quotas"] = f"error: {e}"
        result["errors"].append(f"sliding_quotas: {e}")

    # ── 2. DeepSeek balance ──
    ds_balance = None
    try:
        ds_balance = _collect_deepseek_balance()
        result["sources"]["deepseek_balance"] = f"${ds_balance:.2f}" if ds_balance is not None else "skipped"
    except Exception as e:
        result["sources"]["deepseek_balance"] = f"error: {e}"
        result["errors"].append(f"deepseek: {e}")

    # ── 3. Claude /usage (optionnel) ──
    claude_pct = None
    if include_claude or force_claude:
        try:
            claude_pct = _collect_claude_usage(force=force_claude)
            result["sources"]["claude_usage"] = f"{claude_pct}%" if claude_pct is not None else "skipped/no-data"
        except Exception as e:
            result["sources"]["claude_usage"] = f"error: {e}"
            result["errors"].append(f"claude: {e}")
    else:
        result["sources"]["claude_usage"] = "disabled"

    # ── 4. Mettre à jour les quotas dans la BDD ──
    updated = 0
    try:
        # Clés Gemini Free — dérivées de api_keys (SSOT seed) pour ne jamais
        # oublier une clé ajoutée au KeyPool (ex. GEMINI_API_KEY_6, #T269)
        free_keys = _get_gemini_free_key_ids()

        # Les quotas glissants sont agrégés par canal, pas par clé individuelle ;
        # on distribue proportionnellement (approximation : division par N clés)
        flash_rpm = sliding.get("gemini_free_flash_rpm", 0) // max(len(free_keys), 1)
        flash_tpm = sliding.get("gemini_free_flash_tpm", 0) // max(len(free_keys), 1)
        flash_rpd = sliding.get("gemini_free_flash_rpd", 0) // max(len(free_keys), 1)
        for key_id in free_keys:
            ok = update_quota_realtime(
                key_id,
                limit_rpm=15, limit_rpd=500, limit_tpm=250000,
                used_rpm=flash_rpm,
                used_tpm=flash_tpm,
                used_rpd=flash_rpd,
                source="calculated",
            )
            if ok: updated += 1

        # ── Fenêtres glissantes des abonnements (1 h / 30 j) ──
        # Calculées par session_history.get_quotas_from_db() mais jamais
        # propagées vers quota_realtime : used_tph/used_monthly restaient à 0.
        # Les limites viennent de la table subscriptions (aucune valeur en dur).
        try:
            from core.models_db import get_subscriptions
            subs = {s["id"]: s for s in get_subscriptions()}
        except Exception as e:
            logger.warning(f"[QUOTAS] subscriptions indisponibles : {e}")
            subs = {}

        claude_pro = subs.get("claude_pro") or {}
        gemini_adv = subs.get("gemini_advanced") or {}

        # Claude Pro (CLI abonné) → ligne Anthropic du registre
        if claude_pro:
            ok = update_quota_realtime(
                "ANTHROPIC_API_KEY",
                limit_tph=claude_pro.get("hourly_token_limit"),
                limit_monthly=claude_pro.get("monthly_token_limit"),
                used_tph=sliding.get("claude_cli_abo_tph") or sliding.get("claude_cli_tph") or 0,
                used_monthly=sliding.get("claude_cli_abo_tpm") or sliding.get("claude_cli_tpm") or 0,
                source="calculated",
            )
            if ok:
                updated += 1

        # Clé Gemini Paid — agrégation API Gemini (approximation existante :
        # valeurs brutes du canal, non divisées par le nombre de clés)
        gemini_paid_kwargs = {
            "limit_rpm": 500, "limit_rpd": 10000, "limit_tpm": 4000000,
            "used_rpm": sliding.get("gemini_free_flash_rpm", 0),  # Approximation
            "used_tpm": sliding.get("gemini_free_flash_tpm", 0),
            "used_rpd": sliding.get("gemini_free_flash_rpd", 0),
            "source": "calculated",
        }
        # Gemini Advanced (CLI abonné) → fenêtres 1 h / 30 j sur la ligne Gemini
        if gemini_adv:
            gemini_paid_kwargs.update({
                "limit_tph": gemini_adv.get("hourly_token_limit"),
                "limit_monthly": gemini_adv.get("monthly_token_limit"),
                "used_tph": sliding.get("gemini_cli_abo_tph") or sliding.get("gemini_cli_tph") or 0,
                "used_monthly": sliding.get("gemini_cli_abo_tpm") or sliding.get("gemini_cli_tpm") or 0,
            })
        ok = update_quota_realtime("GEMINI_PAYANT_API_KEY", **gemini_paid_kwargs)
        if ok: updated += 1

        # DeepSeek
        ds_kwargs = {
            "limit_rpm": 60, "limit_tpm": 1000000,
            "source": "api_sync" if ds_balance is not None else "calculated",
        }
        if ds_balance is not None:
            ds_kwargs["external_balance_usd"] = ds_balance
            ds_kwargs["external_status"] = "ok" if ds_balance > 1.0 else ("warning" if ds_balance > 0 else "exhausted")
            # Mettre à jour le real_billing aussi
            try:
                from core.token_tracker import update_real_billing
                update_real_billing(deepseek_balance_usd=ds_balance)
            except Exception:
                pass
            # Historie le solde réel dans billing_history (#T349). Le solde est
            # déjà en main (aucun appel réseau ajouté) ; la politique d'écriture
            # (changement OU > 1 h) est portée par insert_balance_snapshot.
            # Une erreur d'écriture ne fait jamais tomber le refresh : elle est
            # journalisée et remontée dans result["errors"] (même motif que
            # l'instantané de quotas, lignes 398-407). Un solde None n'écrit rien.
            try:
                from core.session_history import insert_balance_snapshot
                result["balance_snapshot_rows"] = insert_balance_snapshot("deepseek", ds_balance)
            except Exception as e:
                result["balance_snapshot_rows"] = 0
                result["errors"].append(f"balance_history: {e}")
                logger.warning(f"[QUOTAS] Erreur relevé solde billing_history : {e}")
        ok = update_quota_realtime("DEEPSEEK_API_KEY", **ds_kwargs)
        if ok: updated += 1

        # Cloud APIs (pas de limites glissantes)
        ok = update_quota_realtime("CLOUD_API_KEY", external_status="ok", source="static")
        if ok: updated += 1

        # xAI API (Grok)
        ok = update_quota_realtime(
            "XAI_API_KEY",
            limit_rpm=60, limit_tpm=1000000,
            external_status="ok",
            source="static"
        )
        if ok: updated += 1

    except Exception as e:
        # Une erreur d'écriture ne doit jamais faire tomber le refresh complet
        logger.warning(f"[QUOTAS] Erreur écriture BDD (mise à jour partielle) : {e}")
        result["errors"].append(f"bdd: {e}")

    result["updated_keys"] = updated

    # ── 5. Instantané d'historique (quota_snapshots) ──
    # Écrit à CHAQUE rafraîchissement : alimente les tendances du dashboard
    # de métriques (la table restait vide, aucun historique, #T269).
    try:
        from core.session_history import insert_quota_snapshot
        result["snapshot_rows"] = insert_quota_snapshot(sliding)
    except Exception as e:
        result["snapshot_rows"] = 0
        result["errors"].append(f"quota_snapshots: {e}")
        logger.warning(f"[QUOTAS] Erreur instantané quota_snapshots : {e}")

    # Résumé pour le log
    logger.info(
        f"[QUOTAS] Refresh terminé : {updated} clés mises à jour, "
        f"sources={result['sources']}"
    )

    return result


def get_refresh_summary() -> dict[str, Any]:
    """Retourne un résumé des quotas pour le dashboard (lecture seule, pas de refresh)."""
    try:
        from core.models_db import get_quota_summary
        return get_quota_summary()
    except Exception as e:
        logger.error(f"[QUOTAS] Erreur get_refresh_summary: {e}")
        return {"global_status": "error", "error": str(e)}


async def quota_refresh_loop(interval_seconds: int | None = None) -> None:
    """Boucle de fond : rafraîchit quota_realtime à intervalle configurable.

    Lancée au démarrage par gui_server (même motif que sse_quota_pusher_loop /
    swarm_heartbeat_loop — pas de nouvel ordonnanceur).
    include_claude=False par défaut : la collecte Claude passe par un scraping
    (~2 s) qui ne doit pas partir toute seule en boucle.
    Une erreur de collecte est journalisée et la boucle continue : un provider
    injoignable est normal et ne doit jamais tuer le serveur.

    Args:
        interval_seconds: intervalle forcé (tests, horloge simulée) ;
            None = lire QUOTA_REFRESH_INTERVAL_SECONDS (défaut 300 s).
    """
    interval = get_quota_refresh_interval() if interval_seconds is None else interval_seconds
    logger.info(f"[QUOTAS] Boucle de rafraîchissement démarrée (intervalle={interval}s, include_claude=False).")
    while True:
        await asyncio.sleep(interval)
        try:
            result = refresh_all_quotas(include_claude=False)
            logger.info(
                f"[QUOTAS] Refresh périodique : {result.get('updated_keys', 0)} clés, "
                f"{result.get('snapshot_rows', 0)} lignes d'instantané."
            )
        except Exception as e:
            logger.warning(f"[QUOTAS] Erreur refresh périodique (la boucle continue) : {e}")
