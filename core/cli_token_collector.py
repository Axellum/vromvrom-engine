"""
============================================================
CLI_TOKEN_COLLECTOR.PY — Collecteur Passif Multi-Canaux
============================================================
Scanne les logs locaux d'Antigravity IDE (Gemini CLI) et de
Claude Code CLI pour estimer les tokens consommés et les
injecter dans le token_tracker du moteur.

Canaux supportés :
  - Antigravity IDE  → transcript.jsonl dans brain/
  - Claude Code CLI  → résumés dans contexte_ia/historique/

Ratio d'estimation : ~4 chars = 1 token (français/code mixte)
============================================================
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("cli_token_collector")

# ── Configuration ──────────────────────────────────────────
ANTIGRAVITY_BRAIN_DIR = Path(os.path.expanduser("~")) / ".gemini" / "antigravity-ide" / "brain"
CHARS_PER_TOKEN = 4  # Ratio moyen français/code mixte


def estimate_tokens(text: str) -> int:
    """Estime le nombre de tokens à partir du nombre de caractères."""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def scan_antigravity_conversations(since_date: str = None) -> list:
    """
    Scanne toutes les conversations Antigravity IDE et estime
    les tokens consommés par chacune.
    
    Args:
        since_date: Date ISO minimale (ex: "2026-05-22"). None = tout scanner.
    
    Returns:
        Liste de sessions synthétiques [{
            "session_id": str,
            "channel": "antigravity_ide",
            "model_channel": "gemini_cli",
            "timestamp": str,
            "objective": str,
            "prompt_tokens": int,
            "completion_tokens": int,
            "total_tokens": int,
            "estimated_cost_usd": float,
            "models": dict,
            "conversation_id": str
        }]
    """
    sessions = []
    
    if not ANTIGRAVITY_BRAIN_DIR.exists():
        logger.warning(f"[CLICollector] Répertoire Antigravity introuvable : {ANTIGRAVITY_BRAIN_DIR}")
        return sessions
    
    since_dt = None
    if since_date:
        try:
            since_dt = datetime.fromisoformat(since_date)
        except (ValueError, TypeError):
            pass
    
    for conv_dir in ANTIGRAVITY_BRAIN_DIR.iterdir():
        if not conv_dir.is_dir():
            continue
        
        transcript_path = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
        if not transcript_path.exists():
            continue
        
        conversation_id = conv_dir.name
        
        # Filtrer par date de modification du fichier
        if since_dt:
            file_mtime = datetime.fromtimestamp(transcript_path.stat().st_mtime)
            if file_mtime < since_dt:
                continue
        
        try:
            session = _parse_antigravity_transcript(transcript_path, conversation_id)
            if session and session["total_tokens"] > 0:
                sessions.append(session)
        except Exception as e:
            logger.warning(f"[CLICollector] Erreur parsing {conversation_id}: {e}")
    
    # Trier par timestamp
    sessions.sort(key=lambda s: s.get("timestamp", ""))
    logger.info(f"[CLICollector] {len(sessions)} conversations Antigravity IDE scannées")
    return sessions


def _parse_antigravity_transcript(transcript_path: Path, conversation_id: str) -> dict:
    """
    Parse un fichier transcript.jsonl d'Antigravity IDE pour
    estimer les tokens consommés.
    """
    total_prompt = 0
    total_completion = 0
    first_timestamp = None
    last_timestamp = None
    objective = None
    user_messages = 0
    model_responses = 0
    
    with open(transcript_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                step = json.loads(line)
            except json.JSONDecodeError:
                continue
            
            step_type = step.get("type", "")
            source = step.get("source", "")
            content = step.get("content", "") or ""
            
            # Capturer le timestamp
            ts = step.get("timestamp") or step.get("created_at")
            if ts:
                if first_timestamp is None:
                    first_timestamp = ts
                last_timestamp = ts
            
            # Capturer l'objectif (premier message utilisateur)
            if step_type == "USER_INPUT" and source == "USER_EXPLICIT":
                user_messages += 1
                total_prompt += estimate_tokens(content)
                if objective is None and len(content) > 5:
                    # Tronquer l'objectif à 100 chars
                    objective = content[:100].replace("\n", " ").strip()
                    if len(content) > 100:
                        objective += "..."
            
            # Réponses du modèle (LLM output)
            elif step_type == "PLANNER_RESPONSE" or source == "MODEL":
                model_responses += 1
                total_completion += estimate_tokens(content)
            
            # Tool calls (les arguments sont des tokens prompt envoyés au LLM)
            elif step_type in ("VIEW_FILE", "GREP_SEARCH", "RUN_COMMAND", "WRITE_FILE", "REPLACE_FILE"):
                # Les résultats d'outils sont renvoyés au LLM comme contexte
                tool_calls = step.get("tool_calls", [])
                for tc in tool_calls:
                    args = json.dumps(tc.get("arguments", {}))
                    total_prompt += estimate_tokens(args)
                # Le output du tool est aussi du contexte prompt
                output = step.get("output", "") or ""
                total_prompt += estimate_tokens(output)
    
    if total_prompt == 0 and total_completion == 0:
        return None
    
    total_tokens = total_prompt + total_completion
    
    # Estimation du coût (Gemini CLI via abonnement = gratuit, mais on estime la valeur)
    # Tarif indicatif Gemini 3.5 Flash: $0.075/1M input, $0.30/1M output
    cost = (total_prompt * 0.075 / 1_000_000) + (total_completion * 0.30 / 1_000_000)
    
    return {
        "session_id": f"cli_antigravity_{conversation_id[:8]}",
        "channel": "antigravity_ide",
        "model_channel": "gemini_cli",
        "timestamp": first_timestamp or datetime.now().isoformat(),
        "last_activity": last_timestamp,
        "objective": objective or "Session Antigravity IDE",
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "total_tokens": total_tokens,
        "estimated_cost_usd": round(cost, 6),
        "is_subscription": True,  # Inclus dans l'abonnement Gemini Advanced
        "models": {
            "gemini-cli": {
                "prompt_tokens": total_prompt,
                "completion_tokens": total_completion,
                "total_tokens": total_tokens,
                "estimated_cost_usd": round(cost, 6)
            }
        },
        "conversation_id": conversation_id,
        "user_messages": user_messages,
        "model_responses": model_responses,
        "transcript_ref": str(transcript_path),  # lien P3 vers le transcript complet
    }


# ── Répertoires de conversations Claude Code CLI ───────────
user_home_path = Path(os.path.expanduser("~"))


def scan_claude_cli_sessions(since_date: str = None) -> list:
    """
    Scanne les fichiers JSONL de Claude Code CLI dans ~/.claude/projects/
    et extrait les tokens RÉELS (input_tokens, output_tokens, cache).
    """
    sessions = []

    since_dt = None
    if since_date:
        try:
            since_dt = datetime.fromisoformat(since_date)
        except (ValueError, TypeError):
            pass

    # Découverte dynamique de tous les répertoires de projet Claude
    claude_projects_dir = user_home_path / ".claude" / "projects"
    project_dirs = []
    if claude_projects_dir.exists() and claude_projects_dir.is_dir():
        try:
            project_dirs = [p for p in claude_projects_dir.iterdir() if p.is_dir()]
        except Exception as e:
            logger.warning(f"[CLICollector] Impossible de lister {claude_projects_dir}: {e}")

    for project_dir in project_dirs:
        project_label = project_dir.name  # Ex: "E--AuxFilsDesIdees-moteur-agents"

        for jsonl_file in project_dir.glob("*.jsonl"):
            # Filtrer par date de modification
            if since_dt:
                file_mtime = datetime.fromtimestamp(jsonl_file.stat().st_mtime)
                if file_mtime < since_dt:
                    continue

            try:
                session = _parse_claude_jsonl(jsonl_file, project_label)
                if session and session["total_tokens"] > 0:
                    sessions.append(session)
            except Exception as e:
                logger.warning(f"[CLICollector] Erreur parsing Claude {jsonl_file.name[:8]}: {e}")

    sessions.sort(key=lambda s: s.get("timestamp", ""))
    logger.info(f"[CLICollector] {len(sessions)} conversations Claude CLI scannées (tokens réels)")
    return sessions


def _parse_claude_jsonl(jsonl_path: Path, project_label: str) -> dict:
    """
    Parse un fichier JSONL de Claude Code CLI et extrait les tokens réels
    depuis les champs 'usage' des réponses assistant.
    """
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_create = 0
    first_timestamp = None
    last_timestamp = None
    objective = None
    model_name = None
    models_breakdown = {}
    api_calls = 0

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Capturer le timestamp
            ts = entry.get("timestamp")
            if ts:
                if first_timestamp is None:
                    first_timestamp = ts
                last_timestamp = ts

            # Capturer l'objectif (premier message utilisateur)
            if entry.get("type") == "user" and objective is None:
                msg = entry.get("message", {})
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) > 5:
                    objective = content[:100].replace("\n", " ").strip()
                    if len(content) > 100:
                        objective += "..."

            # Extraire les tokens des réponses assistant
            msg = entry.get("message", {})
            if msg.get("role") == "assistant" and "usage" in msg:
                usage = msg["usage"]
                inp = usage.get("input_tokens", 0)
                out = usage.get("output_tokens", 0)
                cache_r = usage.get("cache_read_input_tokens", 0)
                cache_c = usage.get("cache_creation_input_tokens", 0)

                total_input += inp + cache_r + cache_c
                total_output += out
                total_cache_read += cache_r
                total_cache_create += cache_c
                api_calls += 1

                # Tracker le modèle utilisé
                m = msg.get("model", "claude-unknown")
                if m not in models_breakdown:
                    models_breakdown[m] = {
                        "prompt_tokens": 0, "completion_tokens": 0,
                        "total_tokens": 0, "estimated_cost_usd": 0, "calls": 0
                    }
                models_breakdown[m]["prompt_tokens"] += inp + cache_r + cache_c
                models_breakdown[m]["completion_tokens"] += out
                models_breakdown[m]["total_tokens"] += inp + cache_r + cache_c + out
                models_breakdown[m]["calls"] += 1

                if model_name is None:
                    model_name = m

    if total_input == 0 and total_output == 0:
        return None

    total_tokens = total_input + total_output
    conversation_id = jsonl_path.stem  # UUID du fichier

    # Coût: inclus dans l'abonnement Claude Pro (Max), mais on estime la valeur API
    # Claude Sonnet 4: $3/M input, $15/M output (tarif API standard)
    cost = (total_input * 3.0 / 1_000_000) + (total_output * 15.0 / 1_000_000)
    for m_data in models_breakdown.values():
        m_data["estimated_cost_usd"] = round(
            (m_data["prompt_tokens"] * 3.0 / 1_000_000) +
            (m_data["completion_tokens"] * 15.0 / 1_000_000), 6
        )

    return {
        "session_id": f"cli_claude_{conversation_id[:8]}",
        "channel": "claude_cli",
        "model_channel": "claude_cli",
        "timestamp": first_timestamp or datetime.now().isoformat(),
        "last_activity": last_timestamp,
        "objective": objective or f"Session Claude CLI ({project_label})",
        "prompt_tokens": total_input,
        "completion_tokens": total_output,
        "total_tokens": total_tokens,
        "estimated_cost_usd": round(cost, 6),
        "is_subscription": True,  # Inclus dans l'abonnement Claude Pro
        "models": models_breakdown,
        "conversation_id": conversation_id,
        "project": project_label,
        "api_calls": api_calls,
        "cache_read_tokens": total_cache_read,
        "cache_creation_tokens": total_cache_create,
        "estimation_method": "exact_usage_from_jsonl",
        "transcript_ref": str(jsonl_path),  # lien P3 vers le transcript complet
    }


def collect_all_cli_tokens(since_date: str = None, persist_to_db: bool = True) -> dict:
    """
    Point d'entrée principal : scanne tous les canaux CLI et retourne
    un résumé consolidé.
    
    Args:
        since_date: Date ISO minimale (ex: "2026-05-01"). None = tout.
        persist_to_db: Si True, persiste automatiquement les résultats
                       dans la BDD SQLite (table ide_conversations).
    
    Returns:
        {
            "antigravity_sessions": [...],
            "claude_sessions": [...],
            "total_cli_tokens": int,
            "total_sessions": int,
            "persisted_count": int,
            "scan_timestamp": str
        }
    """
    logger.info(f"[CLICollector] Lancement du scan (since={since_date})")
    
    antigravity = scan_antigravity_conversations(since_date)
    claude = scan_claude_cli_sessions(since_date)
    
    total_tokens = (
        sum(s["total_tokens"] for s in antigravity) +
        sum(s["total_tokens"] for s in claude)
    )
    
    # Persistance automatique en BDD SQLite
    persisted_count = 0
    if persist_to_db:
        try:
            from core.session_history import bulk_upsert_ide_conversations
            all_sessions = antigravity + claude
            persisted_count = bulk_upsert_ide_conversations(all_sessions)
        except Exception as e:
            logger.warning(f"[CLICollector] Erreur persistance BDD : {e}")
    
    result = {
        "antigravity_sessions": antigravity,
        "claude_sessions": claude,
        "total_cli_tokens": total_tokens,
        "total_sessions": len(antigravity) + len(claude),
        "persisted_count": persisted_count,
        "scan_timestamp": datetime.now().isoformat()
    }
    
    logger.info(
        f"[CLICollector] Scan terminé : {len(antigravity)} Antigravity + "
        f"{len(claude)} Claude = {total_tokens:,} tokens CLI estimés "
        f"({persisted_count} persistés en BDD)"
    )
    
    return result


# ── Point d'entrée CLI pour test ────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = collect_all_cli_tokens(since_date="2026-05-22")
    
    print(f"\n{'='*60}")
    print(f"  SCAN CLI TOKEN COLLECTOR")
    print(f"{'='*60}")
    print(f"  Antigravity IDE : {len(result['antigravity_sessions'])} sessions")
    for s in result["antigravity_sessions"]:
        print(f"    - {s['session_id']}: {s['total_tokens']:>8,} tokens | {s['objective'][:50]}")
    print(f"  Claude CLI      : {len(result['claude_sessions'])} sessions")
    for s in result["claude_sessions"]:
        print(f"    - {s['session_id']}: {s['total_tokens']:>8,} tokens | {s['objective'][:50]}")
    print(f"{'='*60}")
    print(f"  TOTAL CLI       : {result['total_cli_tokens']:>10,} tokens")
    print(f"{'='*60}")
