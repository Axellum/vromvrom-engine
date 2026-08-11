"""
api/services/billing_service.py — Service de synchronisation de facturation GCP/Claude.

Extrait de gui_server.py (A8 Audit V5.5).
"""

import asyncio
import csv
import json
import logging
import os
from datetime import datetime

from core import token_tracker

logger = logging.getLogger(__name__)

# Variables pour le suivi de la synchronisation de facturation
billing_sync_state = {
    "status": "idle",
    "message": "",
    "cost": 0.0,
    "currency": "USD",
    "last_sync": None
}

# Détail de conso complet exporté par console.anthropic.com (CSV par modèle/jour) —
# conservé en local pour audit manuel, jamais commité (cf. .gitignore).
_ANTHROPIC_EXPORT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "anthropic_usage_exports",
)


def _save_anthropic_csv_export(csv_content: str) -> str:
    """Sauvegarde l'export CSV Anthropic (détail coût par modèle/jour) sur disque, horodaté."""
    from datetime import datetime
    os.makedirs(_ANTHROPIC_EXPORT_DIR, exist_ok=True)
    filename = f"anthropic_usage_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    filepath = os.path.join(_ANTHROPIC_EXPORT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(csv_content)
    logger.info(f"[BILLING] Export CSV Anthropic sauvegardé : {filepath}")
    return filepath


async def handle_scraper_success(stdout_str: str):
    """Parse et enregistre les résultats du scraper de facturation."""
    global billing_sync_state
    try:
        result_json = None
        for line in stdout_str.split("\n"):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    result_json = json.loads(line)
                    break
                except Exception:
                    pass

        if result_json and result_json.get("status") == "success":
            gcp = result_json.get("gcp")
            claude = result_json.get("claude")

            cost_raw = 0.0
            currency = "USD"

            if gcp:
                gcp_cost_usd = gcp.get("cost_usd", 0.0)
                cost_raw = gcp.get("cost_raw", 0.0)
                currency = gcp.get("currency", "USD")
                token_tracker.update_real_billing(gcp_cost_usd=gcp_cost_usd)

            if claude:
                claude_usage = claude.get("message_usage_pct")
                claude_text = claude.get("summary_text")
                token_tracker.update_real_billing(
                    claude_message_usage_pct=claude_usage,
                    claude_summary_text=claude_text
                )

            anthropic = result_json.get("anthropic")
            if anthropic:
                token_tracker.update_real_billing(anthropic_api_cost_usd=anthropic.get("cost_usd"))
                csv_export = anthropic.get("csv_export")
                if csv_export:
                    _save_anthropic_csv_export(csv_export)

            gemini_sub = result_json.get("gemini_subscription")
            if gemini_sub:
                token_tracker.update_real_billing(gemini_subscription_summary=gemini_sub.get("summary_text"))

            billing_sync_state["status"] = "success"
            billing_sync_state["message"] = "Synchronisation réussie !"
            billing_sync_state["cost"] = cost_raw
            billing_sync_state["currency"] = currency
            from datetime import datetime
            billing_sync_state["last_sync"] = datetime.now().isoformat()
        else:
            billing_sync_state["status"] = "error"
            billing_sync_state["message"] = (
                result_json.get("message") if result_json
                else "Format de sortie du scraper invalide."
            )
    except Exception as e:
        billing_sync_state["status"] = "error"
        billing_sync_state["message"] = f"Erreur de décodage des résultats : {str(e)}"


async def run_billing_sync_flow():
    """Exécute le processus de synchronisation de facturation en arrière-plan."""
    global billing_sync_state

    node_cmd = ["node", "tools/billing_scraper.js", "--headless=true"]
    cwd = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    try:
        billing_sync_state["status"] = "running"
        billing_sync_state["message"] = "Vérification de la session en arrière-plan (mode headless)..."

        proc = await asyncio.create_subprocess_exec(
            *node_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd
        )

        stdout, stderr = await proc.communicate()
        exit_code = proc.returncode

        stdout_str = stdout.decode("utf-8", errors="ignore").strip()
        stderr_str = stderr.decode("utf-8", errors="ignore").strip()

        logger.info(f"Headless scraper exit code: {exit_code}")

        # Détecter si login requis
        needs_login = (exit_code == 2) or ("Authentification requise" in stderr_str) or ("signin" in stdout_str)

        if needs_login:
            billing_sync_state["status"] = "needs_login"
            billing_sync_state["message"] = "Connexion requise. Veuillez vous connecter dans Chrome."

            node_cmd_headed = ["node", "tools/billing_scraper.js", "--headless=false"]
            proc_headed = await asyncio.create_subprocess_exec(
                *node_cmd_headed,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd
            )
            stdout_headed, stderr_headed = await proc_headed.communicate()
            exit_code_headed = proc_headed.returncode
            stdout_str_headed = stdout_headed.decode("utf-8", errors="ignore").strip()
            stderr_str_headed = stderr_headed.decode("utf-8", errors="ignore").strip()

            if exit_code_headed == 0:
                await handle_scraper_success(stdout_str_headed)
            else:
                billing_sync_state["status"] = "error"
                billing_sync_state["message"] = f"Échec de l'authentification : {stderr_str_headed}"
        elif exit_code == 0:
            await handle_scraper_success(stdout_str)
        else:
            billing_sync_state["status"] = "error"
            billing_sync_state["message"] = f"Erreur de facturation : {stderr_str}"

    except Exception as e:
        logger.error(f"Erreur lors de la synchronisation de facturation: {e}")
        billing_sync_state["status"] = "error"
        billing_sync_state["message"] = str(e)


# ──────────────────────────────────────────────────────────────────
# Réconciliation coûts RÉELS (exports CSV console.anthropic.com) vs
# coûts ESTIMÉS (token_usage.cost_usd, tarifs du catalogue) — #T182
# ──────────────────────────────────────────────────────────────────


def _normalize_header(cell: str) -> str:
    """Normalise une cellule d'en-tête CSV pour une reconnaissance tolérante.

    Ex : "Date (UTC)" → "date utc", "Cost (USD)" → "cost usd". Les variantes
    d'en-tête de l'export console (parenthèses, casse, espaces) se comparent
    ainsi sur leur terme significatif.
    """
    return " ".join(cell.strip().lower().replace("(", " ").replace(")", " ").split())


def _parse_csv_date(raw: str) -> str | None:
    """Parse une date 'YYYY-MM-DD' (UTC, cohérent avec l'en-tête 'Date (UTC)')."""
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _parse_csv_cost(raw: str) -> float | None:
    """Parse un montant CSV : '1.50', '0,75', '$ 12.34', '1,234.56'.

    Retourne None si la cellule n'est pas un nombre lisible (ligne à ignorer).
    """
    cleaned = raw.strip().replace("$", "").replace("€", "").replace(" ", "").replace("\u00a0", "")
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")  # '1,234.56' → séparateur de milliers
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")  # '0,75' → virgule décimale
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_anthropic_export_csv(csv_content: str, filename: str) -> tuple[dict[str, float], int, int]:
    """Parse un export CSV console.anthropic.com (détail coût par modèle/jour).

    L'en-tête est DÉTECTÉ (une colonne date + une colonne coût reconnues), pas
    supposé en ligne 1 : l'export peut être précédé de lignes de méta (titre,
    organisation…). Les lignes de données illisibles (champs manquants, date ou
    coût non parsables) sont ignorées individuellement et journalisées — jamais
    d'exception remontée.

    Retourne (coûts réels agrégés par jour UTC, nb de lignes valides, nb de
    lignes ignorées).
    """
    rows_by_day: dict[str, float] = {}
    valid_rows = 0
    skipped = 0

    header_idx: dict[str, int] | None = None
    for row in csv.reader(csv_content.splitlines()):
        if not row or not any(cell.strip() for cell in row):
            continue  # ligne vide : sans objet
        norm = [_normalize_header(cell) for cell in row]
        if header_idx is None:
            date_col = next((j for j, n in enumerate(norm) if n.startswith("date") or n.startswith("timestamp")), None)
            cost_col = next((j for j, n in enumerate(norm) if n.startswith("cost")), None)
            if date_col is not None and cost_col is not None:
                header_idx = {"date": date_col, "cost": cost_col}
                continue
            # Ligne de méta précédant l'en-tête (titre, période, org…) : sans objet.
            continue
        if max(header_idx.values()) >= len(row):
            skipped += 1
            logger.warning(f"[BILLING] Ligne ignorée ({filename}, {skipped}): champs manquants.")
            continue
        day = _parse_csv_date(row[header_idx["date"]])
        cost = _parse_csv_cost(row[header_idx["cost"]])
        if day is None or cost is None:
            skipped += 1
            logger.warning(f"[BILLING] Ligne ignorée ({filename}, {skipped}): date ou coût illisible.")
            continue
        rows_by_day[day] = rows_by_day.get(day, 0.0) + cost
        valid_rows += 1
    if header_idx is None:
        logger.warning(f"[BILLING] Export ignoré ({filename}): aucune ligne d'en-tête reconnue (date + coût).")
    return rows_by_day, valid_rows, skipped


def _estimated_anthropic_costs_by_day() -> tuple[dict[str, float], int]:
    """Somme des coûts ESTIMÉS (token_usage.cost_usd) par jour UTC, modèles Anthropic.

    Filtre `model LIKE 'claude-%'` : tous les modèles de l'API Anthropic du
    catalogue (claude-sonnet-*, claude-opus-*, claude-haiku-*, claude-fable-*)
    commencent par ce préfixe (cf. data/model_inventory.json).
    `date(timestamp, 'unixepoch')` agrège en UTC, cohérent avec la colonne
    "Date (UTC)" des exports console.

    Retourne (coûts par jour, nb de lignes token_usage retenues).
    """
    from core.runtime_db import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT date(timestamp, 'unixepoch') AS day, COUNT(*) AS n, COALESCE(SUM(cost_usd), 0.0) AS cost
            FROM token_usage
            WHERE model LIKE 'claude-%'
            GROUP BY day
            """
        ).fetchall()
    finally:
        conn.close()
    return {day: float(cost) for day, n, cost in rows}, sum(n for day, n, cost in rows)


def _no_exports_response() -> dict:
    """Réponse contractuelle quand aucun export CSV n'est disponible.

    Aucun champ numérique : un montant absent ne doit jamais se lire comme un
    écart nul (un zéro se lirait comme « aucun écart »).
    """
    return {
        "status": "no_exports",
        "period": "day",
        "currency": "USD",
        "message": (
            "Aucun export de coûts Anthropic disponible dans "
            f"{_ANTHROPIC_EXPORT_DIR} : lancez la sync console.anthropic.com "
            "(POST /api/billing/sync) pour générer un CSV."
        ),
    }


def reconcile_anthropic_costs(export_dir: str | None = None) -> dict:
    """Réconcilie coûts RÉELS (CSV export console) et coûts ESTIMÉS (token_usage).

    Période : JOUR (UTC) — granularité native de l'export console Anthropic
    (une ligne par jour et modèle) et la plus fine commune avec token_usage
    (timestamp Unix, agrégeable par jour sans perte). Un écart quotidien est
    actionnable (jour de dérive : tarif catalogue périmé, coût de cache…), là
    où un total mensuel lisserait la dérive.

    L'union des jours (CSV ∪ BDD) révèle aussi les jours sans contrepartie
    d'un côté ou de l'autre. L'écart relatif est basé sur le RÉEL (vérité
    terrain) et vaut None si le réel est nul mais l'estimé non nul (relatif
    mathématiquement indéfini — un chiffre absent n'est pas un chiffre nul).

    Statuts : "ok" (écarts calculés), "no_exports" (aucun CSV présent),
    "no_data" (CSV présents mais aucun contenu exploitable).
    """
    export_dir = export_dir or _ANTHROPIC_EXPORT_DIR
    if not os.path.isdir(export_dir):
        return _no_exports_response()
    csv_files = sorted(f for f in os.listdir(export_dir) if f.lower().endswith(".csv"))
    if not csv_files:
        return _no_exports_response()

    real_by_day: dict[str, float] = {}
    export_details: list[dict] = []
    total_valid_rows = 0
    for filename in csv_files:
        filepath = os.path.join(export_dir, filename)
        try:
            with open(filepath, encoding="utf-8-sig") as f:
                content = f.read()
        except OSError as e:
            logger.warning(f"[BILLING] Export CSV illisible ({filename}) : {e}")
            export_details.append({"file": filename, "rows_parsed": 0, "rows_skipped": 0})
            continue
        parsed, valid_rows, skipped = _parse_anthropic_export_csv(content, filename)
        for day, cost in parsed.items():
            real_by_day[day] = real_by_day.get(day, 0.0) + cost
        total_valid_rows += valid_rows
        export_details.append({"file": filename, "rows_parsed": valid_rows, "rows_skipped": skipped})

    if total_valid_rows == 0:
        # Des fichiers existent mais rien d'exploitable : on ne renvoie pas un
        # « écart » vide qui se lirait comme un écart nul.
        return {
            "status": "no_data",
            "period": "day",
            "currency": "USD",
            "message": (
                f"{len(csv_files)} export(s) présent(s) dans {export_dir} mais aucun contenu "
                "exploitable (lignes malformées ou en-têtes inconnus — voir logs)."
            ),
            "export_files": export_details,
        }

    estimated_by_day, db_rows_used = _estimated_anthropic_costs_by_day()

    days = sorted(set(real_by_day) | set(estimated_by_day))
    periods: list[dict] = []
    total_real = 0.0
    total_estimated = 0.0
    for day in days:
        real = real_by_day.get(day, 0.0)
        estimated = estimated_by_day.get(day, 0.0)
        total_real += real
        total_estimated += estimated
        abs_diff = real - estimated
        if real != 0:
            rel_diff_pct = round(abs_diff / real * 100, 2)
        elif estimated == 0:
            rel_diff_pct = 0.0
        else:
            rel_diff_pct = None  # réel nul, estimé non nul : relatif indéfini
        periods.append({
            "date": day,
            "real_cost_usd": round(real, 6),
            "estimated_cost_usd": round(estimated, 6),
            "abs_diff_usd": round(abs_diff, 6),
            "rel_diff_pct": rel_diff_pct,
        })

    total_abs_diff = total_real - total_estimated
    if total_real != 0:
        total_rel_diff_pct = round(total_abs_diff / total_real * 100, 2)
    elif total_estimated == 0:
        total_rel_diff_pct = 0.0
    else:
        total_rel_diff_pct = None

    return {
        "status": "ok",
        "period": "day",
        "currency": "USD",
        "message": (
            "Réconciliation coûts réels (CSV console Anthropic) vs estimés "
            "(token_usage) — période journalière (UTC)."
        ),
        "periods": periods,
        "totals": {
            "real_cost_usd": round(total_real, 6),
            "estimated_cost_usd": round(total_estimated, 6),
            "abs_diff_usd": round(total_abs_diff, 6),
            "rel_diff_pct": total_rel_diff_pct,
        },
        "export_files": export_details,
        "db_rows_used": db_rows_used,
    }
