"""
core/vocal_jobs.py — Jobs vocaux asynchrones (Sprint B/C Discussion).

Persiste les tâches longues (web, calendrier, fichiers, deep) déclenchées
depuis le mode Discussion. À la complétion, annonce optionnelle sur le
satellite Tab5 via assist_satellite.announce (HA REST).

Sprint C : handlers spécialistes (grounding Gemini, Workspace read-only, deep).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from typing import Any

import aiohttp

from core.ha_tls import ha_ssl_context

logger = logging.getLogger(__name__)

# ── Prompts spécialistes (TTS vocal Tab5) ─────────────────────────────

_WEB_SYSTEM_PROMPT = (
    "Tu es l'assistant vocal. Réponds en français en 2 à 3 phrases courtes, "
    "adaptées à une synthèse vocale. Pas de markdown, pas de listes à puces. "
    "Appuie-toi sur les résultats de recherche Google si disponibles."
)

_SUMMARIZE_SUFFIX = (
    "\n\n[Résumé TTS] À partir des données ci-dessus, réponds à la question "
    "de l'utilisateur en 2-3 phrases vocales naturelles, sans markdown."
)

_DEEP_SUFFIX = (
    "\n\n[INTENT=deep] Fournis une explication structurée et complète (5-8 phrases). "
    "Reste concis mais couvre les points essentiels pour une écoute vocale."
)

_GROUNDING_MODEL = "gemini-2.5-flash"


def _load_gemini_paid_key() -> str:
    key = (os.environ.get("GEMINI_PAYANT_API_KEY") or "").strip()
    if key:
        return key
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.isfile(env_path):
        for line in open(env_path, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if line.startswith("GEMINI_PAYANT_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


async def _call_gemini_search_grounding(user_prompt: str, *, session_id: str) -> str | None:
    """
    Appel REST direct Search Grounding (même pattern que gemini-tools.js).
    Contourne les wrappers FallbackProvider du gateway.
    """
    api_key = _load_gemini_paid_key()
    if not api_key:
        return None

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{_GROUNDING_MODEL}:generateContent?key={api_key}"
    )
    payload = {
        "systemInstruction": {"parts": [{"text": _WEB_SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.2},
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=45),
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    logger.warning(
                        "[VOCAL_JOBS] grounding HTTP %s : %s",
                        resp.status,
                        body[:300],
                    )
                    return None
                data = await resp.json()
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        logger.warning("[VOCAL_JOBS] grounding échec réseau : %s", exc)
        return None

    candidates = data.get("candidates") or []
    if not candidates:
        return None
    parts = candidates[0].get("content", {}).get("parts") or []
    text = "\n".join(p.get("text", "") for p in parts if p.get("text")).strip()
    if not text:
        return None

    try:
        from core.token_tracker import record_usage

        usage = data.get("usageMetadata") or {}
        record_usage(
            _GROUNDING_MODEL,
            usage.get("promptTokenCount", 0),
            usage.get("candidatesTokenCount", 0),
            session_id=session_id,
        )
    except Exception:
        pass

    return _sanitize_tts(text)

_GMAIL_MARKERS = ("mail", "email", "gmail", "courriel", "message", "boite", "boîte")
_DRIVE_MARKERS = ("drive", "fichier", "document", "pdf", "dossier", "pièce jointe", "piece jointe")

_JOB_STATUSES = ("pending", "running", "done", "error", "cancelled")
_DEFAULT_SATELLITE = "assist_satellite.example_satellite"


def create_vocal_job(
    *,
    intent: str,
    user_prompt: str,
    conversation_id: str | None = None,
    device_id: str | None = None,
    session_id: str | None = None,
) -> str:
    """Crée un job vocal en statut pending. Retourne job_id."""
    job_id = f"vjob_{uuid.uuid4().hex[:12]}"
    now = time.time()
    try:
        from core.runtime_db import get_connection

        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO vocal_jobs
                (job_id, intent, status, user_prompt, conversation_id, device_id,
                 session_id, result_text, error_message, created_at, updated_at)
                VALUES (?, ?, 'pending', ?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    job_id,
                    intent,
                    user_prompt[:2000],
                    conversation_id,
                    device_id,
                    session_id,
                    now,
                    now,
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("[VOCAL_JOBS] create ignoré : %s", exc)
    return job_id


def update_vocal_job(
    job_id: str,
    *,
    status: str | None = None,
    result_text: str | None = None,
    error_message: str | None = None,
) -> None:
    if status and status not in _JOB_STATUSES:
        status = "error"
    try:
        from core.runtime_db import get_connection

        fields: list[str] = ["updated_at = ?"]
        values: list[Any] = [time.time()]
        if status:
            fields.append("status = ?")
            values.append(status)
        if result_text is not None:
            fields.append("result_text = ?")
            values.append(result_text[:4000])
        if error_message is not None:
            fields.append("error_message = ?")
            values.append(error_message[:1000])
        values.append(job_id)
        with get_connection() as conn:
            conn.execute(
                f"UPDATE vocal_jobs SET {', '.join(fields)} WHERE job_id = ?",
                values,
            )
            conn.commit()
    except Exception as exc:
        logger.debug("[VOCAL_JOBS] update ignoré : %s", exc)


def get_vocal_job(job_id: str) -> dict[str, Any] | None:
    try:
        from core.runtime_db import get_connection

        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT job_id, intent, status, user_prompt, conversation_id, device_id,
                       session_id, result_text, error_message, created_at, updated_at
                FROM vocal_jobs WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        if not row:
            return None
        keys = (
            "job_id", "intent", "status", "user_prompt", "conversation_id",
            "device_id", "session_id", "result_text", "error_message",
            "created_at", "updated_at",
        )
        return dict(zip(keys, row, strict=False))
    except Exception as exc:
        logger.debug("[VOCAL_JOBS] get ignoré : %s", exc)
        return None


def _read_ha_credentials() -> tuple[str, str]:
    from services.execute_service import _read_ha_credentials as _read

    return _read()


def _sanitize_tts(text: str, *, max_sentences: int = 3, max_chars: int = 420) -> str:
    from core.vocal_tts_cache import sanitize_discussion_tts

    return sanitize_discussion_tts(text, max_sentences=max_sentences, max_chars=max_chars)


_GROUNDING_PROVIDER_CANDIDATES = (
    "gemini-3.5-flash-paid",
    "gemini-2.5-flash-paid",
    "gemini-2.5-pro-paid",
    "gemini-3.1-pro-preview-paid",
)


def _resolve_grounding_provider(gateway) -> Any | None:
    """Retourne un provider Gemini payant avec Search Grounding, ou None."""
    # get_provider() enveloppe en FallbackProvider sans l'attribut grounding —
    # il faut le provider brut du registre.
    getter = getattr(gateway, "_get_raw_provider", gateway.get_provider)
    for name in _GROUNDING_PROVIDER_CANDIDATES:
        try:
            provider = getter(name)
        except (ValueError, KeyError):
            continue
        if getattr(provider, "search_grounding_available", False):
            return provider
    return None


async def _generate_with_provider(
    provider,
    *,
    system_prompt: str,
    user_prompt: str,
    session_id: str,
    use_search_grounding: bool = False,
) -> str:
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(
        None,
        lambda: provider.generate(
            system_prompt,
            user_prompt,
            session_id=session_id,
            use_search_grounding=use_search_grounding,
        ),
    )
    if isinstance(raw, dict):
        return str(raw.get("content") or raw.get("text") or "")
    return str(raw or "")


async def _summarize_data_for_tts(
    *,
    user_prompt: str,
    raw_data: str,
    session_id: str,
    gateway,
    token_tracker,
    fast_path_cache,
    conversation_id: str | None = None,
) -> str:
    """Condense des données brutes (calendrier, fichiers) en réponse TTS."""
    from services.pipeline_service import run_fast_path

    combined = (
        f"Question utilisateur : {user_prompt}\n\n"
        f"Données récupérées :\n{raw_data[:3500]}"
    )
    raw = await run_fast_path(
        user_prompt=combined,
        session_id=session_id,
        gateway=gateway,
        token_tracker=token_tracker,
        fast_path_cache=fast_path_cache,
        system_prompt_suffix=_SUMMARIZE_SUFFIX,
        conversation_id=conversation_id,
    )
    return _sanitize_tts(raw or "")


async def run_web_specialist(
    user_prompt: str,
    *,
    session_id: str,
    gateway,
    token_tracker,
    fast_path_cache,
    conversation_id: str | None = None,
) -> str:
    """
    Recherche web 1-shot via Gemini Search Grounding (pas de Planner/DAG).
    Fallback fast-path si clé payante absente.
    """
    text = await _call_gemini_search_grounding(user_prompt, session_id=session_id)
    if text:
        return text

    provider = _resolve_grounding_provider(gateway)
    if provider:
        try:
            raw = await _generate_with_provider(
                provider,
                system_prompt=_WEB_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                session_id=session_id,
                use_search_grounding=True,
            )
            text = _sanitize_tts(raw)
            if text:
                return text
        except Exception as exc:
            logger.warning("[VOCAL_JOBS] web grounding échec : %s", exc)

    # Fallback sans grounding (clé payante absente ou erreur API)
    from services.pipeline_service import run_fast_path

    suffix = (
        "\n\n[INTENT=web] Réponds en 2-3 phrases TTS. "
        "Tu n'as pas de données temps réel : dis-le honnêtement."
    )
    raw = await run_fast_path(
        user_prompt=user_prompt,
        session_id=session_id,
        gateway=gateway,
        token_tracker=token_tracker,
        fast_path_cache=fast_path_cache,
        system_prompt_suffix=suffix,
        conversation_id=conversation_id,
    )
    text = _sanitize_tts(raw or "")
    if not text:
        raise RuntimeError("Réponse web vide")
    return text


async def run_calendar_specialist(
    user_prompt: str,
    *,
    session_id: str,
    gateway,
    token_tracker,
    fast_path_cache,
    conversation_id: str | None = None,
) -> str:
    """Interroge Google Calendar (read-only) puis résume pour TTS."""
    from tools.google_workspace import get_calendar_events

    raw_data = await asyncio.to_thread(get_calendar_events, "primary", "10")
    if raw_data.lower().startswith("erreur"):
        return _sanitize_tts(
            "Je n'ai pas accès à ton calendrier pour le moment. "
            "Vérifie la connexion Google OAuth sur le moteur."
        )
    if "aucun événement" in raw_data.lower():
        return _sanitize_tts("Ton agenda est libre, je ne vois aucun événement à venir.")

    return await _summarize_data_for_tts(
        user_prompt=user_prompt,
        raw_data=raw_data,
        session_id=session_id,
        gateway=gateway,
        token_tracker=token_tracker,
        fast_path_cache=fast_path_cache,
        conversation_id=conversation_id,
    )


def _build_gmail_query(user_prompt: str) -> str:
    """Extrait une requête Gmail simple depuis la phrase vocale."""
    norm = user_prompt.lower()
    # Retirer les mots de commande courants
    cleaned = re.sub(
        r"\b(cherche|trouve|lis|montre|dans|mon|ma|mes|le|la|les|un|une|des|"
        r"gmail|mail|email|courriel|boite|boîte|messages?)\b",
        " ",
        norm,
        flags=re.UNICODE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) >= 3:
        return cleaned[:120]
    return "is:recent"


def _files_prefers_gmail(user_prompt: str) -> bool:
    norm = user_prompt.lower()
    gmail_score = sum(1 for m in _GMAIL_MARKERS if m in norm)
    drive_score = sum(1 for m in _DRIVE_MARKERS if m in norm)
    return gmail_score >= drive_score


async def run_files_specialist(
    user_prompt: str,
    *,
    session_id: str,
    gateway,
    token_tracker,
    fast_path_cache,
    conversation_id: str | None = None,
) -> str:
    """Interroge Gmail ou Drive (read-only) puis résume pour TTS."""
    if _files_prefers_gmail(user_prompt):
        from tools.google_workspace import search_gmail

        query = _build_gmail_query(user_prompt)
        raw_data = await asyncio.to_thread(search_gmail, query, "5")
    else:
        from tools.google_workspace import list_drive_files

        raw_data = await asyncio.to_thread(list_drive_files, "10")

    if raw_data.lower().startswith("erreur"):
        return _sanitize_tts(
            "Je n'ai pas accès à tes fichiers ou emails pour le moment. "
            "La connexion Google OAuth n'est peut-être pas configurée."
        )

    return await _summarize_data_for_tts(
        user_prompt=user_prompt,
        raw_data=raw_data,
        session_id=session_id,
        gateway=gateway,
        token_tracker=token_tracker,
        fast_path_cache=fast_path_cache,
        conversation_id=conversation_id,
    )


async def run_deep_specialist(
    user_prompt: str,
    *,
    session_id: str,
    gateway,
    token_tracker,
    fast_path_cache,
    base_suffix: str = "",
    conversation_id: str | None = None,
    tier_override: str | None = "fort",
    model_override: str | None = None,
) -> str:
    """Analyse longue async — réponse plus complète, annoncée via satellite."""
    from services.pipeline_service import run_fast_path

    suffix = f"{base_suffix}{_DEEP_SUFFIX}"
    raw = await run_fast_path(
        user_prompt=user_prompt,
        session_id=session_id,
        gateway=gateway,
        token_tracker=token_tracker,
        fast_path_cache=fast_path_cache,
        system_prompt_suffix=suffix,
        conversation_id=conversation_id,
        tier_override=tier_override,
        model_override=model_override,
        inject_project_context=True,
    )
    text = _sanitize_tts(raw or "", max_sentences=6, max_chars=650)
    if not text:
        raise RuntimeError("Réponse deep vide")
    return text


async def run_vocal_specialist(
    intent: str,
    user_prompt: str,
    *,
    session_id: str,
    gateway,
    token_tracker,
    fast_path_cache,
    base_suffix: str = "",
    conversation_id: str | None = None,
    tier_override: str | None = None,
    model_override: str | None = None,
) -> str:
    """Route un job async vers le handler spécialiste Sprint C."""
    common = {
        "user_prompt": user_prompt,
        "session_id": session_id,
        "gateway": gateway,
        "token_tracker": token_tracker,
        "fast_path_cache": fast_path_cache,
        "conversation_id": conversation_id,
    }
    if intent == "web":
        return await run_web_specialist(**common)
    if intent == "calendar":
        return await run_calendar_specialist(**common)
    if intent == "files":
        return await run_files_specialist(**common)
    if intent == "deep":
        return await run_deep_specialist(
            **common,
            base_suffix=base_suffix,
            tier_override=tier_override or "fort",
            model_override=model_override,
        )
    raise ValueError(f"Intent spécialiste inconnu : {intent}")


async def announce_to_satellite(message: str, entity_id: str | None = None) -> bool:
    """Annonce TTS sur le satellite Tab5 (job async terminé)."""
    text = (message or "").strip()
    if not text:
        return False
    ha_token, ha_url = _read_ha_credentials()
    if not ha_token:
        logger.debug("[VOCAL_JOBS] announce skip : pas de token HA")
        return False
    satellite = entity_id or os.environ.get("VOCAL_SATELLITE_ENTITY", _DEFAULT_SATELLITE)
    url = f"{ha_url.rstrip('/')}/api/services/assist_satellite/announce"
    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }
    payload = {"entity_id": satellite, "message": text[:500]}
    ssl_ctx = ha_ssl_context()
    try:
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                ok = resp.status == 200
                if not ok:
                    body = await resp.text()
                    logger.warning("[VOCAL_JOBS] announce HTTP %s : %s", resp.status, body[:200])
                return ok
    except (aiohttp.ClientError, TimeoutError) as exc:
        logger.warning("[VOCAL_JOBS] announce échec : %s", exc)
        return False
