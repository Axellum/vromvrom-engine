"""
tests/test_whatsapp.py — Tests unitaires pour WhatsAppService et ses webhooks FastAPI.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from core.whatsapp_service import WhatsAppService
from gui_server import app

client = TestClient(app)

@pytest.fixture(autouse=True)
def clean_env():
    """Nettoie l'environnement avant chaque test pour éviter les interférences."""
    orig_env = os.environ.copy()
    keys_to_remove = [
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER",
        "META_WHATSAPP_ACCESS_TOKEN", "META_WHATSAPP_PHONE_NUMBER_ID",
        "WHATSAPP_USER_PHONE_NUMBER", "WHATSAPP_WEBHOOK_VERIFY_TOKEN",
        "CALLMEBOT_APIKEY"
    ]
    for key in keys_to_remove:
        if key in os.environ:
            del os.environ[key]
            
    yield
    
    # Restaurer l'environnement d'origine
    os.environ.clear()
    os.environ.update(orig_env)


def test_whatsapp_service_disabled_by_default():
    """Vérifie que le service est désactivé si aucune clé n'est fournie."""
    WhatsAppService._instance = None  # Reset singleton
    service = WhatsAppService()
    assert service.mode == "disabled"


def test_whatsapp_service_init_twilio():
    """Vérifie l'initialisation du service en mode Twilio."""
    os.environ["TWILIO_ACCOUNT_SID"] = "AC_test"
    os.environ["TWILIO_AUTH_TOKEN"] = "token_test"
    
    WhatsAppService._instance = None
    service = WhatsAppService()
    assert service.mode == "twilio"
    assert service.twilio_sid == "AC_test"
    assert service.twilio_token == "token_test"


def test_whatsapp_service_init_meta():
    """Vérifie l'initialisation du service en mode Meta Cloud API."""
    os.environ["META_WHATSAPP_ACCESS_TOKEN"] = "meta_token"
    os.environ["META_WHATSAPP_PHONE_NUMBER_ID"] = "phone_id"
    
    WhatsAppService._instance = None
    service = WhatsAppService()
    assert service.mode == "meta"
    assert service.meta_token == "meta_token"
    assert service.meta_phone_id == "phone_id"


@pytest.mark.asyncio
@patch("httpx.AsyncClient.post")
async def test_send_message_twilio_success(mock_post):
    """Teste l'envoi réussi de message via Twilio."""
    os.environ["TWILIO_ACCOUNT_SID"] = "AC_test"
    os.environ["TWILIO_AUTH_TOKEN"] = "token_test"
    os.environ["WHATSAPP_USER_PHONE_NUMBER"] = "+33612345678"
    
    WhatsAppService._instance = None
    service = WhatsAppService()
    
    # Simuler un retour HTTP 201 Created
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_post.return_value = mock_response
    
    res = await service.send_message("Hello World")
    
    assert res is True
    mock_post.assert_called_once()
    # Vérifier l'URL et les données d'appel
    args, kwargs = mock_post.call_args
    assert "api.twilio.com" in args[0]
    assert kwargs["data"]["To"] == "whatsapp:+33612345678"
    assert kwargs["data"]["Body"] == "Hello World"


@pytest.mark.asyncio
@patch("httpx.AsyncClient.post")
async def test_send_message_meta_success(mock_post):
    """Teste l'envoi réussi de message via Meta Cloud API."""
    os.environ["META_WHATSAPP_ACCESS_TOKEN"] = "meta_token"
    os.environ["META_WHATSAPP_PHONE_NUMBER_ID"] = "phone_id"
    os.environ["WHATSAPP_USER_PHONE_NUMBER"] = "+33612345678"
    
    WhatsAppService._instance = None
    service = WhatsAppService()
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response
    
    res = await service.send_message("Test Meta")
    
    assert res is True
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert "graph.facebook.com" in args[0]
    assert kwargs["json"]["to"] == "33612345678"
    assert kwargs["json"]["text"]["body"] == "Test Meta"


def test_meta_webhook_verification_success():
    """Teste la validation GET du webhook Meta (challenge)."""
    os.environ["WHATSAPP_WEBHOOK_VERIFY_TOKEN"] = "my_token"
    
    response = client.get("/api/webhook/whatsapp/meta?hub.mode=subscribe&hub.verify_token=my_token&hub.challenge=12345")
    
    assert response.status_code == 200
    assert response.text == "12345"


def test_meta_webhook_verification_failure():
    """Teste l'échec de validation GET du webhook Meta avec un mauvais token."""
    os.environ["WHATSAPP_WEBHOOK_VERIFY_TOKEN"] = "my_token"
    
    response = client.get("/api/webhook/whatsapp/meta?hub.mode=subscribe&hub.verify_token=wrong_token&hub.challenge=12345")
    
    assert response.status_code == 403


def test_webhook_twilio_post_security_failure():
    """Vérifie que le webhook Twilio refuse les requêtes sans token de sécurité valide."""
    os.environ["WHATSAPP_WEBHOOK_VERIFY_TOKEN"] = "my_token"
    
    response = client.post(
        "/api/webhook/whatsapp/twilio",
        data={"From": "whatsapp:+33612345678", "Body": "Test", "To": "whatsapp:+14155238886"}
    )
    
    assert response.status_code == 403


@patch("api.routes.whatsapp.process_incoming_whatsapp_message")
def test_webhook_twilio_post_security_success(mock_process):
    """Vérifie que le webhook Twilio accepte les requêtes avec token de sécurité valide."""
    os.environ["WHATSAPP_WEBHOOK_VERIFY_TOKEN"] = "my_token"
    
    response = client.post(
        "/api/webhook/whatsapp/twilio?token=my_token",
        data={"From": "whatsapp:+33612345678", "Body": "Test", "To": "whatsapp:+14155238886"}
    )
    
    assert response.status_code == 200
    assert response.json() == {"status": "queued"}
    mock_process.assert_called_once()
