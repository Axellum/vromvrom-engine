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
    # Infos fraîches / sorties produits → spécialiste web (pas chat sans outils)
    "dernier modele", "dernier modèle", "derniere version", "dernière version",
    "vient de sortir", "qui est sorti", "modele sorti", "modèle sorti",
    "nouveaute", "nouveauté", "sortie de",
    # Météo demandée SANS le mot « météo » (#T351) — formulations réelles du
    # vocal_audit_log : « quel temps il fait / il fera », « est-ce qu'il va
    # pleuvoir », « il fait combien dehors ».
    "quel temps il fait", "quel temps il fera", "quel temps fait-il",
    "il va pleuvoir", "il pleuvra", "il fait combien", "il fera",
)
_CALENDAR_MARKERS = (
    "calendrier", "agenda", "rendez-vous", "rendez vous", "rdv", "reunion",
    "réunion", "demain matin", "planning", "evenement", "événement",
    "mon emploi du temps", "qu ai-je", "qu'est-ce que j'ai", "qu est ce que j ai",
    # Agenda demandé SANS le mot « agenda » (#T351) — formulations réelles du
    # vocal_audit_log : « quand est-ce que je travaille », « je bosse quand »,
    # « c'est quand la prochaine fois que je travaille ».
    "quand est-ce que je travaille", "quand est ce que je travaille",
    "je travaille quand", "je bosse quand", "quand je travaille", "quand je bosse",
    # Racines fiables (#T351) : couvrent « c'est quand la prochaine fois que je
    # travaille » et les variantes où « quand » n'est pas collé au verbe.
    "je travaille", "je bosse",
    # [#T365] Le champ horaire du travail, absent de la liste et pourtant la
    # formulation la plus fréquente d'Axel. Mesuré le 17/08 : sur 12 demandes
    # d'agenda en 30 jours, 3 SEULEMENT atteignaient le calendrier, et
    # « A quelle heure je commence à travailler demain ? » recevait « Je n'ai pas
    # accès à ton agenda » alors que l'accès fonctionne. Formulations volontairement
    # longues : « je commence » seul capterait « je commence à comprendre ».
    # [#T383] Les deux orthographes, accentuée ET non accentuée, comme le fait
    # déjà `_HA_STATE_MARKERS` (« c est allume » / « c'est allumé »).
    # `_normalize_prompt` CONSERVE les accents (l. 131-132 : la classe de
    # caractères gardés contient àâäéèêëïîôùûüç), donc un marqueur écrit sans
    # accent ne peut pas matcher le texte réel du STT, qui en produit. Livrés
    # sans accent par #T365, ces six marqueurs ne matchaient donc RIEN :
    # « je commence à travailler », « je finis à quelle heure », « je termine à
    # quelle heure », « je suis en congé », « quoi de prévu », « de prévu ».
    # Les tests de #T365 ne l'ont pas vu : ils écrivent les phrases sans accent.
    "je commence a travailler", "je commence à travailler",
    "je commence le travail", "heure je commence",
    "je finis a quelle heure", "je finis à quelle heure",
    "je finis le travail", "quand je finis",
    "je termine a quelle heure", "je termine à quelle heure",
    "quand je termine", "j'embauche", "j embauche",
    "jour de repos", "jours de repos",
    "je suis en conge", "je suis en congé", "je suis en repos",
    "quoi de prevu", "quoi de prévu", "de prevu", "de prévu",
    "mon planning", "mes horaires",
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
    VocalIntent.CHAT: (
        "\n\n[INTENT=chat] Réponds en 2-3 phrases TTS. Ne JAMAIS affirmer une "
        "panne technique (accès, connexion, outil en panne…) que tu n'as pas "
        "constatée dans CE tour. Si aucune action n'a été tentée dans ce tour, "
        "ne prétends pas qu'elle a échoué : dis honnêtement que tu n'as pas "
        "d'information, ou propose une piste. "
        # [#T365] Le chat ne voit pas les autres chemins du moteur et niait donc
        # des capacités bien réelles : « je n'ai pas accès à ton agenda » (alors
        # que le spécialiste calendrier répond), « je n'ai pas la capacité de
        # contrôler le Tab 5 » (alors que 29 commandes HA ont abouti en 30 jours).
        # Axel finissait par débattre des capacités du moteur avec le moteur.
        "Le système auquel tu appartiens SAIT lire l'agenda Google et piloter la "
        "maison (lumières, volet, climatisation) : d'autres chemins s'en chargent. "
        "N'affirme donc JAMAIS que tu n'as pas accès à l'agenda ou à la domotique. "
        "Si la demande relève de l'un des deux et ne t'est pas parvenue résolue, "
        "invite simplement à la reformuler — par exemple « demande-moi ce que tu "
        "as de prévu demain »."
    ),
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
    """
    Commandes domotiques déterministes (même chemin que mode Domotique).

    [#T323] Retourne None UNIQUEMENT quand la demande n'est pas une commande
    domotique — c'est le seul cas où la cascade doit continuer. Dès qu'une
    commande est RECONNUE, cette fonction répond, y compris pour dire qu'elle a
    échoué : elle n'attrape plus l'échec « vers le haut ».

    Motif, mesuré en prod le 12/08 sur une vraie commande d'Axel : « descend le
    volet du salon » était reconnu (fuzzy 0.88 → close), l'appel HA échouait sur
    `Server disconnected`, l'exception remontait, la cascade repartait vers le
    chat — et le modèle, ignorant tout de ce qui venait de se passer, répondait
    « Je ne peux pas descendre le volet du salon depuis ici. Vous pouvez le
    faire avec… ». Le volet n'avait pas bougé et l'utilisateur recevait un refus
    de compétence au lieu d'une panne. Un mensonge plausible coûte plus cher
    qu'une erreur : il envoie chercher au mauvais endroit.
    """
    from services.execute_service import execute_ha_service, resolve_ha_command_for_execute

    ha_cmd = await resolve_ha_command_for_execute(user_prompt)
    if not ha_cmd:
        return None  # pas une commande domotique → la cascade continue
    logger.info(
        "[VOCAL_HOST] Zero-LLM HA → %s(%s)",
        ha_cmd.service, ha_cmd.entity_id,
    )
    try:
        ok, text = await execute_ha_service(
            ha_cmd.service,
            ha_cmd.entity_id,
            service_data=ha_cmd.service_data,
        )
    except Exception as exc:
        # La commande était comprise : on annonce la panne au lieu de laisser un
        # LLM inventer une excuse. Le message reste court, il part en TTS.
        logger.warning(
            "[VOCAL_HOST] [#T323] Commande '%s(%s)' RECONNUE mais non exécutée : %s",
            ha_cmd.service, ha_cmd.entity_id, exc,
        )
        return "Je n'ai pas pu joindre Home Assistant, la commande n'a pas été exécutée."
    if ok and text:
        return text
    return "Je n'ai pas pu exécuter cette commande."


async def _try_zero_llm_ha_state(user_prompt: str) -> str | None:
    """Lecture d'état domotique déterministe (température/état clim/volet/lumière).

    Court-circuite les outils LLM (ha_list/ha_get_state), qui choisissaient parfois
    un doublon HS (ex. capteur Sonoff cloud « unavailable ») au lieu de la source
    vivante. Retourne None si ce n'est pas une question d'état → la cascade continue.
    """
    from services.ha_state_query import resolve_ha_state_query

    return await resolve_ha_state_query(user_prompt)


async def _try_zero_llm_ha_weather(user_prompt: str) -> str | None:
    """Météo locale déterministe depuis l'entité HA de la maison.

    Intercepte les demandes météo AVANT le spécialiste web, qui n'a pas la
    localisation et finissait par réclamer la position à l'utilisateur puis
    livrer un chiffre inventé sur un autre continent (mesuré en prod le 17/08).
    Source de vérité : l'entité `weather.*` de la maison. Retourne None si ce
    n'est pas une demande météo, si la lecture HA échoue, ou si la prévision
    demandée est absente → la cascade continue vers le spécialiste web.
    """
    from services.ha_weather_query import resolve_weather_query

    return await resolve_weather_query(user_prompt)


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

    # 1b) Lectures d'état HA déterministes (température / état) — fiabilité vs outils LLM
    try:
        state_tts = await _try_zero_llm_ha_state(user_prompt)
    except Exception as exc:
        logger.warning("[VOCAL_HOST] Zero-LLM état HA échec : %s", exc)
        state_tts = None
    if state_tts:
        meta["ha_zero_llm_state"] = True
        return DiscussionHostResult(
            response_text=state_tts,
            agents_used=["ha_state", "vocal_host"],
            routing_type="discussion_ha_state",
            metadata=meta,
        )

    # 1c) Météo locale HA (avant le spécialiste web, qui n'a pas la localisation)
    try:
        weather_tts = await _try_zero_llm_ha_weather(user_prompt)
    except Exception as exc:
        logger.warning("[VOCAL_HOST] Zero-LLM météo HA échec : %s", exc)
        weather_tts = None
    if weather_tts:
        meta["ha_zero_llm_weather"] = True
        return DiscussionHostResult(
            response_text=weather_tts,
            agents_used=["ha_weather", "vocal_host"],
            routing_type="discussion_ha_weather",
            metadata=meta,
        )

    # 1d) Heure/Date déterministe
    try:
        from services.datetime_query import resolve_datetime_query
        dt_tts = resolve_datetime_query(user_prompt)
    except Exception as exc:
        logger.warning("[VOCAL_HOST] Zero-LLM datetime échec : %s", exc)
        dt_tts = None
    if dt_tts:
        meta["datetime_zero_llm"] = True
        return DiscussionHostResult(
            response_text=dt_tts,
            agents_used=["datetime_query", "vocal_host"],
            routing_type="discussion_datetime",
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
        # [#T351] Consigne anti-panne-inventée : le modèle ne doit jamais
        # affirmer un échec technique qu'il n'a pas constaté dans ce tour.
        chat_suffix = system_prompt_suffix + _INTENT_SUFFIX[VocalIntent.CHAT]
        text = await _run_sync_discussion(
            user_prompt=user_prompt,
            session_id=session_id,
            gateway=gateway,
            token_tracker=token_tracker,
            fast_path_cache=fast_path_cache,
            system_prompt_suffix=chat_suffix,
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
