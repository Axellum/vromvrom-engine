"""
api/routes/whatsapp.py — Routes de webhook WhatsApp pour Twilio et Meta Cloud API.
"""

import os
import hmac
import logging
from typing import Optional
from fastapi import APIRouter, Request, Form, BackgroundTasks, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(tags=["WhatsApp Webhook"])


def _get_verify_token() -> str:
    """
    Retourne le token de vérification des webhooks WhatsApp depuis l'environnement.

    Sécurité : AUCUNE valeur par défaut en dur. Si WHATSAPP_WEBHOOK_VERIFY_TOKEN
    n'est pas défini, les webhooks sont refusés (fail-closed) plutôt qu'ouverts.
    """
    return os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "").strip()


def _token_matches(provided: Optional[str], expected: str) -> bool:
    """Comparaison à temps constant (anti-timing) ; False si l'un est vide."""
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)

# ──────────────────────────────────────────────────────────────────
# Fonctions de traitement asynchrones
# ──────────────────────────────────────────────────────────────────

async def process_incoming_whatsapp_message(sender: str, message_text: str):
    """
    Traite la commande WhatsApp reçue en tâche de fond pour éviter les timeouts HTTP.
    """
    from core.whatsapp_service import WhatsAppService
    
    service = WhatsAppService()
    
    # 1. Vérification de sécurité
    authorized_number = os.getenv("WHATSAPP_USER_PHONE_NUMBER", "").strip().replace("+", "").replace("whatsapp:", "")
    clean_sender = sender.replace("+", "").replace("whatsapp:", "").strip()
    
    if not authorized_number:
        logger.warning("[WHATSAPP] Aucun numéro autorisé configuré dans WHATSAPP_USER_PHONE_NUMBER.")
        await service.send_message("Erreur système : aucun administrateur WhatsApp configuré.", to=sender)
        return
        
    if clean_sender != authorized_number:
        logger.warning(f"[WHATSAPP] Requête refusée de {sender} (non autorisé).")
        await service.send_message("Accès refusé. Vous n'êtes pas autorisé à interagir avec cet agent.", to=sender)
        return
        
    # 2. Notification de début
    await service.send_message("🤖 Demande reçue. Analyse et planification en cours...", to=sender)
    
    # 3. Exécution de la tâche
    try:
        from gui_server import execute_chat, ExecuteRequestBody
        body = ExecuteRequestBody(user_prompt=message_text)
        
        # Exécution du chat (synchrone du point de vue du moteur, asynchrone pour FastAPI)
        result = await execute_chat(body)
        
        # 4. Formater et envoyer le résultat
        if result.get("status") == "completed":
            response_msg = result.get("response", "✅ Tâche terminée avec succès.")
            # Tronquer le message si trop long pour WhatsApp (limite de message individuel)
            if len(response_msg) > 3000:
                response_msg = response_msg[:3000] + "\n\n... (résultat tronqué car trop long pour WhatsApp, consultez l'IHM)"
            await service.send_message(f"✅ Résultat :\n\n{response_msg}", to=sender)
        else:
            error_msg = result.get("error", "Une erreur inconnue est survenue lors de l'exécution.")
            await service.send_message(f"❌ Échec de l'exécution :\n\n{error_msg}", to=sender)
            
    except Exception as e:
        logger.error(f"[WHATSAPP] Erreur lors du traitement de la tâche : {e}")
        await service.send_message(f"❌ Erreur système lors de l'exécution : {str(e)}", to=sender)


# ──────────────────────────────────────────────────────────────────
# Routes Webhook
# ──────────────────────────────────────────────────────────────────

@router.get("/api/webhook/whatsapp/meta")
async def verify_meta_webhook(request: Request):
    """
    Endpoint requis par Meta pour la validation initiale du webhook.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    
    verify_token = _get_verify_token()
    if not verify_token:
        logger.error("[WHATSAPP] WHATSAPP_WEBHOOK_VERIFY_TOKEN non configuré : validation refusée.")
        raise HTTPException(status_code=503, detail="Webhook non configuré")

    if mode == "subscribe" and _token_matches(token, verify_token):
        logger.info("[WHATSAPP] Webhook Meta validé avec succès !")
        # Meta attend le challenge sous forme de texte brut
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(content=challenge)
    else:
        logger.error("[WHATSAPP] Échec de la validation du Webhook Meta (Token incorrect)")
        raise HTTPException(status_code=403, detail="Verification token mismatch")


@router.post("/api/webhook/whatsapp/meta")
async def receive_meta_message(
    request: Request,
    background_tasks: BackgroundTasks,
    token: Optional[str] = None
):
    """
    Réceptionne les messages poussés par l'API Meta Cloud.
    """
    verify_token = _get_verify_token()
    if not verify_token:
        logger.error("[WHATSAPP] WHATSAPP_WEBHOOK_VERIFY_TOKEN non configuré : requête refusée.")
        raise HTTPException(status_code=503, detail="Webhook non configuré")

    # Validation du token de sécurité (argument OU paramètre de requête)
    if not (_token_matches(token, verify_token)
            or _token_matches(request.query_params.get("token"), verify_token)):
        logger.warning("[WHATSAPP] Requête Meta POST rejetée : token de sécurité invalide ou absent.")
        raise HTTPException(status_code=403, detail="Invalid security token")

    try:
        body = await request.json()
        logger.info(f"[WHATSAPP] Webhook Meta reçu : {body}")
        
        # Navigation sécurisée dans la structure JSON de Meta
        entry = body.get("entry", [])[0]
        change = entry.get("changes", [])[0]
        value = change.get("value", {})
        
        # Vérifier s'il s'agit d'un nouveau message
        messages = value.get("messages", [])
        if messages:
            msg_obj = messages[0]
            sender = msg_obj.get("from") # ex: "33612345678"
            
            # S'assurer que c'est un message textuel
            if msg_obj.get("type") == "text":
                message_text = msg_obj.get("text", {}).get("body", "")
                if sender and message_text:
                    logger.info(f"[WHATSAPP] Traitement du message Meta de {sender} : '{message_text}'")
                    background_tasks.add_task(process_incoming_whatsapp_message, sender, message_text)
            else:
                logger.info(f"[WHATSAPP] Type de message Meta ignoré : {msg_obj.get('type')}")
                
    except Exception as e:
        logger.error(f"[WHATSAPP] Erreur de traitement du webhook Meta : {e}")
        
    return {"status": "ok"}


@router.post("/api/webhook/whatsapp/twilio")
async def receive_twilio_message(
    request: Request,
    background_tasks: BackgroundTasks,
    From: str = Form(...),
    Body: str = Form(...),
    To: str = Form(...),
    token: Optional[str] = None
):
    """
    Réceptionne les messages poussés par Twilio (format Form URL Encoded).
    """
    verify_token = _get_verify_token()
    if not verify_token:
        logger.error("[WHATSAPP] WHATSAPP_WEBHOOK_VERIFY_TOKEN non configuré : requête refusée.")
        raise HTTPException(status_code=503, detail="Webhook non configuré")

    # Validation du token de sécurité (argument OU paramètre de requête)
    if not (_token_matches(token, verify_token)
            or _token_matches(request.query_params.get("token"), verify_token)):
        logger.warning("[WHATSAPP] Requête Twilio POST rejetée : token de sécurité invalide ou absent.")
        raise HTTPException(status_code=403, detail="Invalid security token")

    logger.info(f"[WHATSAPP] Webhook Twilio reçu de {From} vers {To} : '{Body}'")
    
    # Nettoyer l'identifiant "whatsapp:" inséré par Twilio
    sender = From.replace("whatsapp:", "").strip()
    
    if sender and Body:
        logger.info(f"[WHATSAPP] Traitement du message Twilio de {sender} : '{Body}'")
        background_tasks.add_task(process_incoming_whatsapp_message, sender, Body)
        
    return {"status": "queued"}

