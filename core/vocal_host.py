"""
core/vocal_host.py — Host Discussion vocal (Sprint B).

Classifie l'intention (chat / web / calendrier / fichiers / deep) sans Planner,
route vers discussion_chat synchrone ou job async vocal_jobs.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class VocalIntent(str, Enum):
    CHAT = "chat"
    WEB = "web"
    CALENDAR = "calendar"
    FILES = "files"
    DEEP = "deep"


_WEB_MARKERS = (
    "meteo", "météo", "actualite", "actualité", "actualites", "actualités",
    "news", "nouvelle", "nouvelles", "info", "infos", "internet", "web",
    "google", "recherche", "cherche sur", "prix de", "cours de", "bitcoin",
    "resultat", "résultat", "score", "match", "qui a gagne", "qui a gagné",
    "journal", "presse", "titres",
)
_CALENDAR_MARKERS = (
    "calendrier", "agenda", "rendez-vous", "rendez vous", "rdv", "reunion",
    "réunion", "demain matin", "ce soir", "planning", "evenement", "événement",
    "mon emploi du temps", "qu ai-je", "qu'est-ce que j'ai", "qu est ce que j ai",
)
_FILES_MARKERS = (
    "fichier", "document", "drive", "pdf", "dossier", "piece jointe",
    "pièce jointe", "mail", "email", "gmail", "courriel",
)
_DEEP_MARKERS = (
    "analyse detaillee", "analyse détaillée", "rapport complet", "explique en detail",
    "explique en détail", "etape par etape", "étape par étape", "longue explication",
    "recherche approfondie", "deep dive",
)

_INTENT_SUFFIX: dict[VocalIntent, str] = {
    VocalIntent.WEB: (
        "\n\n[INTENT=web] Réponds en 2-3 phrases TTS. Si tu n'as pas de données "
        "temps réel, dis-le honnêtement et propose une piste."
    ),
    VocalIntent.CALENDAR: (
        "\n\n[INTENT=calendar] Réponds en 2-3 phrases à partir de l'agenda Google."
    ),
    VocalIntent.FILES: (
        "\n\n[INTENT=files] Réponds en 2-3 phrases à partir des emails ou fichiers Drive."
    ),
    VocalIntent.DEEP: (
        "\n\n[INTENT=deep] Réponse approfondie en arrière-plan, annoncée plus tard."
    ),
}

_ASYNC_ACK: dict[VocalIntent, str] = {
    VocalIntent.FILES: "Je cherche dans tes fichiers. Je te réponds dans un instant.",
    VocalIntent.DEEP: "Je prépare une réponse plus complète. Je te préviens quand c'est prêt.",
}

# Web + calendrier : spécialistes sync (grounding / OAuth) — plus fiable que Cerebras tools.
_SYNC_SPECIALIST_INTENTS = frozenset({VocalIntent.WEB, VocalIntent.CALENDAR})

# Lecture d'état HA (pas commande) → mini outils Cerebras.
_HA_STATE_MARKERS = (
    "temperature", "température", "humidite", "humidité",
    "quelle temperature", "quelle température",
    "est ce que", "est-ce que", "c est allume", "c'est allumé",
    "etat de", "état de", "niveau de", "combien de degres", "combien de degrés",
)


def _normalize_prompt(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower().strip()
    t = re.sub(r"[^\w\sàâäéèêëïîôùûüç'-]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def _looks_like_ha_state_query(prompt: str) -> bool:
    norm = _normalize_prompt(prompt)
    return any(m in norm for m in _HA_STATE_MARKERS)


def classify_vocal_intent(prompt: str) -> tuple[VocalIntent, float]:
    """
    Classifieur rapide (0 ms, mots-clés). Retourne (intent, score 0-1).
    """
    norm = _normalize_prompt(prompt)
    if not norm:
        return VocalIntent.CHAT, 0.0

    scores: dict[VocalIntent, float] = {intent: 0.0 for intent in VocalIntent}
    for marker in _WEB_MARKERS:
        if marker in norm:
            scores[VocalIntent.WEB] += 1.0
    for marker in _CALENDAR_MARKERS:
        if marker in norm:
            scores[VocalIntent.CALENDAR] += 1.0
    for marker in _FILES_MARKERS:
        if marker in norm:
            scores[VocalIntent.FILES] += 1.0
    for marker in _DEEP_MARKERS:
        if marker in norm:
            scores[VocalIntent.DEEP] += 1.2

    best_intent = max(scores, key=scores.get)
    best_score = scores[best_intent]
    if best_score <= 0:
        return VocalIntent.CHAT, 0.0
    return best_intent, min(1.0, best_score / 2.0)


@dataclass
class DiscussionHostResult:
    response_text: str
    agents_used: list[str]
    routing_type: str
    metadata: dict[str, Any]
    async_job_id: str | None = None


async def _run_sync_discussion(
    *,
    user_prompt: str,
    session_id: str,
    gateway,
    token_tracker,
    fast_path_cache,
    system_prompt_suffix: str,
    conversation_id: str | None,
    tier_override: str | None,
    model_override: str | None,
    routing_type: str,
    agent_name: str,
    enable_vocal_tools: bool = False,
) -> str:
    from core.vocal_tts_cache import sanitize_discussion_tts
    from services.pipeline_service import run_fast_path

    raw = await run_fast_path(
        user_prompt=user_prompt,
        session_id=session_id,
        gateway=gateway,
        token_tracker=token_tracker,
        fast_path_cache=fast_path_cache,
        tier_override=tier_override,
        model_override=model_override,
        system_prompt_suffix=system_prompt_suffix,
        # RAG projet = bruit + latence en vocal ; outils HA suffisent pour les faits.
        inject_project_context=False,
        conversation_id=conversation_id,
        enable_vocal_tools=enable_vocal_tools,
    )
    text = sanitize_discussion_tts(raw or "")
    if not text:
        raise RuntimeError("Réponse discussion vide")
    return text


async def _try_zero_llm_ha_command(user_prompt: str) -> str | None:
    """Commandes domotiques déterministes (même chemin que mode Domotique)."""
    from services.execute_service import execute_ha_service, resolve_ha_command_for_execute

    ha_cmd = await resolve_ha_command_for_execute(user_prompt)
    if not ha_cmd:
        return None
    logger.info(
        "[VOCAL_HOST] Zero-LLM HA → %s(%s)",
        ha_cmd.service, ha_cmd.entity_id,
    )
    ok, text = await execute_ha_service(
        ha_cmd.service,
        ha_cmd.entity_id,
        service_data=ha_cmd.service_data,
    )
    if ok and text:
        return text
    return "Je n'ai pas pu exécuter cette commande."


async def _process_vocal_job(
    job_id: str,
    intent: VocalIntent,
    user_prompt: str,
    *,
    session_id: str,
    gateway,
    token_tracker,
    fast_path_cache,
    base_suffix: str,
    conversation_id: str | None,
    device_id: str | None,
    tier_override: str | None,
    model_override: str | None,
) -> None:
    from core.vocal_jobs import announce_to_satellite, run_vocal_specialist, update_vocal_job

    update_vocal_job(job_id, status="running")
    try:
        text = await run_vocal_specialist(
            intent.value,
            user_prompt,
            session_id=session_id,
            gateway=gateway,
            token_tracker=token_tracker,
            fast_path_cache=fast_path_cache,
            base_suffix=base_suffix,
            conversation_id=conversation_id,
            tier_override=tier_override,
            model_override=model_override,
        )
        update_vocal_job(job_id, status="done", result_text=text)
        if conversation_id:
            from core.vocal_session import record_vocal_turn
            record_vocal_turn(
                conversation_id, "assistant", text,
                source_mode="chat", device_id=device_id,
            )
        await announce_to_satellite(text)
    except Exception as exc:
        logger.warning("[VOCAL_HOST] job %s échec : %s", job_id, exc)
        update_vocal_job(job_id, status="error", error_message=str(exc)[:500])
        await announce_to_satellite("Désolé, je n'ai pas pu terminer la recherche.")


async def handle_discussion(
    *,
    user_prompt: str,
    session_id: str,
    gateway,
    token_tracker,
    fast_path_cache,
    system_prompt_suffix: str = "",
    conversation_id: str | None = None,
    device_id: str | None = None,
    tier_override: str | None = None,
    model_override: str | None = None,
) -> DiscussionHostResult:
    """
    Point d'entrée Host Discussion : chat sync ou job async selon intent.

    Priorité : Zero-LLM HA (commandes) → spécialistes web/calendrier →
    chat Cerebras (outils seulement pour lecture d'état HA).
    """
    intent, score = classify_vocal_intent(user_prompt)
    meta: dict[str, Any] = {
        "vocal_intent": intent.value,
        "intent_score": round(score, 2),
    }

    # 1) Commandes HA déterministes — rapide, zéro hallucination d'entité
    try:
        ha_tts = await _try_zero_llm_ha_command(user_prompt)
    except Exception as exc:
        logger.warning("[VOCAL_HOST] Zero-LLM HA échec : %s", exc)
        ha_tts = None
    if ha_tts:
        meta["ha_zero_llm"] = True
        return DiscussionHostResult(
            response_text=ha_tts,
            agents_used=["ha_command", "vocal_host"],
            routing_type="discussion_ha_command",
            metadata=meta,
        )

    # 2) Web / calendrier — spécialistes (grounding / OAuth), pas Cerebras tools
    if intent in _SYNC_SPECIALIST_INTENTS:
        from core.vocal_jobs import run_vocal_specialist

        text = await run_vocal_specialist(
            intent.value,
            user_prompt,
            session_id=session_id,
            gateway=gateway,
            token_tracker=token_tracker,
            fast_path_cache=fast_path_cache,
            base_suffix=system_prompt_suffix,
            conversation_id=conversation_id,
            tier_override=tier_override,
            model_override=model_override,
        )
        if conversation_id:
            from core.vocal_session import record_vocal_turn
            record_vocal_turn(
                conversation_id, "assistant", text,
                source_mode="chat", device_id=device_id,
            )
        return DiscussionHostResult(
            response_text=text,
            agents_used=[f"discussion_{intent.value}", "vocal_host"],
            routing_type=f"vocal_host_{intent.value}",
            metadata=meta,
        )

    # 3) Chat (éventuellement lecture état HA via outils)
    if intent == VocalIntent.CHAT:
        use_tools = _looks_like_ha_state_query(user_prompt)
        text = await _run_sync_discussion(
            user_prompt=user_prompt,
            session_id=session_id,
            gateway=gateway,
            token_tracker=token_tracker,
            fast_path_cache=fast_path_cache,
            system_prompt_suffix=system_prompt_suffix,
            conversation_id=conversation_id,
            tier_override=tier_override,
            model_override=model_override,
            routing_type="discussion_chat",
            agent_name="discussion_chat",
            enable_vocal_tools=use_tools,
        )
        agents = ["discussion_chat", "vocal_host"]
        if use_tools:
            agents.append("vocal_tools")
        return DiscussionHostResult(
            response_text=text,
            agents_used=agents,
            routing_type="discussion_chat",
            metadata={**meta, "vocal_tools": use_tools},
        )

    from core.vocal_jobs import create_vocal_job

    job_id = create_vocal_job(
        intent=intent.value,
        user_prompt=user_prompt,
        conversation_id=conversation_id,
        device_id=device_id,
        session_id=session_id,
    )
    meta["async_job_id"] = job_id
    asyncio.create_task(
        _process_vocal_job(
            job_id,
            intent,
            user_prompt,
            session_id=session_id,
            gateway=gateway,
            token_tracker=token_tracker,
            fast_path_cache=fast_path_cache,
            base_suffix=system_prompt_suffix,
            conversation_id=conversation_id,
            device_id=device_id,
            tier_override=tier_override,
            model_override=model_override,
        )
    )
    ack = _ASYNC_ACK[intent]
    return DiscussionHostResult(
        response_text=ack,
        agents_used=[f"discussion_{intent.value}", "vocal_host"],
        routing_type=f"vocal_host_{intent.value}_async",
        metadata=meta,
        async_job_id=job_id,
    )
