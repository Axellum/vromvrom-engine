"""
core/whatsapp_service.py — Service d'envoi de messages WhatsApp (Option B).

Supporte :
1. Twilio WhatsApp API (Sandbox ou Compte Pro)
2. Meta WhatsApp Cloud API (API Officielle)
3. CallMeBot API (Unidirectionnel en secours)
"""

import os
import logging
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)

class WhatsAppService:
    """
    Service d'envoi et de routage des messages WhatsApp.
    """
    _instance: Optional['WhatsAppService'] = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(WhatsAppService, cls).__new__(cls, *args, **kwargs)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        # Chargement des configurations depuis l'environnement
        self.twilio_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
        self.twilio_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
        self.twilio_from = os.getenv("TWILIO_PHONE_NUMBER", "whatsapp:+14155238886").strip()
        
        self.meta_token = os.getenv("META_WHATSAPP_ACCESS_TOKEN", "").strip()
        self.meta_phone_id = os.getenv("META_WHATSAPP_PHONE_NUMBER_ID", "").strip()
        
        self.default_to = os.getenv("WHATSAPP_USER_PHONE_NUMBER", "").strip()
        
        # Optionnel: CallMeBot pour les notifications rapides
        self.callmebot_apikey = os.getenv("CALLMEBOT_APIKEY", "").strip()

        # Détermination du mode actif
        if self.twilio_sid and self.twilio_token:
            self.mode = "twilio"
            logger.info("[WHATSAPP] Service initialisé en mode TWILIO.")
        elif self.meta_token and self.meta_phone_id:
            self.mode = "meta"
            logger.info("[WHATSAPP] Service initialisé en mode META Cloud API.")
        elif self.callmebot_apikey and self.default_to:
            self.mode = "callmebot"
            logger.info("[WHATSAPP] Service initialisé en mode CALLMEBOT (unidirectionnel).")
        else:
            self.mode = "disabled"
            logger.warning("[WHATSAPP] Aucune configuration valide trouvée. Le service WhatsApp est désactivé.")
            
        self._initialized = True

    async def send_message(self, message: str, to: Optional[str] = None) -> bool:
        """
        Envoie un message WhatsApp de manière asynchrone.
        
        Args:
            message: Le contenu textuel à envoyer.
            to: Le numéro de téléphone du destinataire (facultatif, utilise WHATSAPP_USER_PHONE_NUMBER par défaut).
            
        Returns:
            True si l'envoi a réussi, False sinon.
        """
        if self.mode == "disabled":
            logger.error("[WHATSAPP] Impossible d'envoyer le message : service désactivé.")
            return False
            
        target_to = to or self.default_to
        if not target_to:
            logger.error("[WHATSAPP] Aucun numéro de destinataire configuré.")
            return False

        # S'assurer que le numéro commence par + pour l'international
        if not target_to.startswith("+") and not target_to.startswith("whatsapp:"):
            # Si c'est un numéro français sans code pays (ex: 06...)
            if target_to.startswith("0"):
                target_to = "+33" + target_to[1:]
            else:
                target_to = "+" + target_to

        if self.mode == "twilio":
            return await self._send_twilio(message, target_to)
        elif self.mode == "meta":
            return await self._send_meta(message, target_to)
        elif self.mode == "callmebot":
            return await self._send_callmebot(message, target_to)
        return False

    async def _send_twilio(self, message: str, to: str) -> bool:
        """Envoi via l'API REST de Twilio."""
        account_sid = self.twilio_sid
        auth_token = self.twilio_token
        
        # Twilio attend les numéros sous le format "whatsapp:+33612345678"
        twilio_to = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
        twilio_from = self.twilio_from if self.twilio_from.startswith("whatsapp:") else f"whatsapp:{self.twilio_from}"
        
        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        
        data = {
            "To": twilio_to,
            "From": twilio_from,
            "Body": message
        }
        
        try:
            # On tente d'utiliser httpx si disponible, sinon on fait un fallback vers requests dans un thread
            try:
                import httpx
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(url, auth=(account_sid, auth_token), data=data)
                    if response.status_code in [200, 201]:
                        logger.info(f"[WHATSAPP] Message envoyé avec succès via Twilio à {twilio_to}")
                        return True
                    else:
                        logger.error(f"[WHATSAPP] Échec d'envoi Twilio (HTTP {response.status_code}): {response.text}")
                        return False
            except ImportError:
                import requests
                def sync_post():
                    return requests.post(url, auth=(account_sid, auth_token), data=data, timeout=15)
                
                response = await asyncio.to_thread(sync_post)
                if response.status_code in [200, 201]:
                    logger.info(f"[WHATSAPP] Message envoyé avec succès via Twilio (requests) à {twilio_to}")
                    return True
                else:
                    logger.error(f"[WHATSAPP] Échec d'envoi Twilio requests (HTTP {response.status_code}): {response.text}")
                    return False
        except Exception as e:
            logger.error(f"[WHATSAPP] Erreur lors de l'envoi Twilio : {e}")
            return False

    async def _send_meta(self, message: str, to: str) -> bool:
        """Envoi via l'API WhatsApp Cloud officielle de Meta."""
        # Enlever "whatsapp:" si présent pour l'API Meta
        clean_to = to.replace("whatsapp:", "").replace("+", "")
        url = f"https://graph.facebook.com/v18.0/{self.meta_phone_id}/messages"
        
        headers = {
            "Authorization": f"Bearer {self.meta_token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": clean_to,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": message
            }
        }
        
        try:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    if response.status_code in [200, 201]:
                        logger.info(f"[WHATSAPP] Message envoyé avec succès via Meta Cloud API à {clean_to}")
                        return True
                    else:
                        logger.error(f"[WHATSAPP] Échec d'envoi Meta (HTTP {response.status_code}): {response.text}")
                        return False
            except ImportError:
                import requests
                def sync_post():
                    return requests.post(url, headers=headers, json=payload, timeout=15)
                
                response = await asyncio.to_thread(sync_post)
                if response.status_code in [200, 201]:
                    logger.info(f"[WHATSAPP] Message envoyé avec succès via Meta requests à {clean_to}")
                    return True
                else:
                    logger.error(f"[WHATSAPP] Échec d'envoi Meta requests (HTTP {response.status_code}): {response.text}")
                    return False
        except Exception as e:
            logger.error(f"[WHATSAPP] Erreur lors de l'envoi Meta Cloud API : {e}")
            return False

    async def _send_callmebot(self, message: str, to: str) -> bool:
        """Envoi via CallMeBot (notifications unidirectionnelles)."""
        clean_to = to.replace("whatsapp:", "")
        url = "https://api.callmebot.com/whatsapp.php"
        
        params = {
            "phone": clean_to,
            "text": message,
            "apikey": self.callmebot_apikey
        }
        
        try:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url, params=params)
                    if response.status_code == 200:
                        logger.info(f"[WHATSAPP] Message envoyé avec succès via CallMeBot à {clean_to}")
                        return True
                    else:
                        logger.error(f"[WHATSAPP] Échec d'envoi CallMeBot (HTTP {response.status_code})")
                        return False
            except ImportError:
                import requests
                def sync_get():
                    return requests.get(url, params=params, timeout=15)
                
                response = await asyncio.to_thread(sync_get)
                if response.status_code == 200:
                    logger.info(f"[WHATSAPP] Message envoyé avec succès via CallMeBot requests à {clean_to}")
                    return True
                else:
                    logger.error(f"[WHATSAPP] Échec d'envoi CallMeBot requests (HTTP {response.status_code})")
                    return False
        except Exception as e:
            logger.error(f"[WHATSAPP] Erreur lors de l'envoi CallMeBot : {e}")
            return False
