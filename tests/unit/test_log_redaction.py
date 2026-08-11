"""
test_log_redaction.py — Masquage des secrets dans les journaux (#T262).

Les cas de test reprennent la fuite RÉELLE constatée en prod le 10/08 (clé
remplacée par une valeur factice) et, surtout, son **chemin** : le secret n'était
pas dans un message écrit à la main, il voyageait dans le texte d'une
`requests.HTTPError` relayée par des modules qui ignorent tout de Gemini. Un
test qui ne vérifierait qu'un `logger.info("clé=...")` raterait exactement ce
qui s'est passé.
"""

import io
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.log_redaction import FiltreRedaction, installer_redaction, masquer_secrets

CLE_FACTICE = "AIzaSyFAKE0123456789abcdefGHIJKLmnopqr"
URL_FUITEE = (
    "400 Client Error: Bad Request for url: "
    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={CLE_FACTICE}"
)


class TestMasquage:
    def test_cle_en_parametre_d_url(self):
        masque = masquer_secrets(URL_FUITEE)
        assert CLE_FACTICE not in masque
        assert "key=***" in masque
        # Le contexte reste lisible : on doit toujours savoir quel appel a échoué.
        assert "gemini-3.5-flash:generateContent" in masque
        assert "400 Client Error" in masque

    def test_cle_google_hors_url(self):
        assert CLE_FACTICE not in masquer_secrets(f"clé chargée : {CLE_FACTICE}")

    @pytest.mark.parametrize("secret,attendu", [
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6", "Bearer ***"),
        ("clé openai sk-proj-abcdefghijklmnopqrstuvwx", "sk-proj-***"),
        ("HF token hf_abcdefghijklmnopqrstuvwxyz", "hf_***"),
        ('{"password": "MotDePasseSuperSecret42"}', "***"),
        ("https://api.exemple/x?access_token=abcdef123456&z=1", "access_token=***"),
    ])
    def test_autres_formes(self, secret, attendu):
        masque = masquer_secrets(secret)
        assert attendu in masque

    def test_le_reste_du_texte_est_intact(self):
        texte = "[FALLBACK GATEWAY] Échec du modèle gemini-3.5-flash après 2 tentatives (CB: CLOSED)"
        assert masquer_secrets(texte) == texte

    def test_texte_vide(self):
        assert masquer_secrets("") == ""


def _logger_capture(nom: str):
    """Logger isolé écrivant dans un tampon, avec le filtre installé."""
    tampon = io.StringIO()
    handler = logging.StreamHandler(tampon)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(nom)
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    installer_redaction(logger)
    return logger, tampon


class TestFiltreSurLesHandlers:
    def test_message_formate_avec_f_string(self):
        """Le cas exact de la fuite : l'exception interpolée dans le message."""
        logger, tampon = _logger_capture("test_redaction_f")
        erreur = Exception(URL_FUITEE)
        logger.warning(f"[FALLBACK GATEWAY] Échec du modèle gemini-3.5-flash : {erreur}")

        sortie = tampon.getvalue()
        assert CLE_FACTICE not in sortie
        assert "key=***" in sortie

    def test_message_avec_arguments(self):
        logger, tampon = _logger_capture("test_redaction_args")
        logger.warning("échec sur %s", URL_FUITEE)
        assert CLE_FACTICE not in tampon.getvalue()

    def test_exception_en_argument_lazy(self):
        """`logger.warning("… %s", exc)` : le filtre voit l'objet, pas encore str(exc)."""
        logger, tampon = _logger_capture("test_redaction_exc_arg")
        logger.warning("[FALLBACK GATEWAY] Échec du modèle gemini-3.5-flash : %s", Exception(URL_FUITEE))

        sortie = tampon.getvalue()
        assert CLE_FACTICE not in sortie
        assert "key=***" in sortie

    def test_argument_entier_reste_formatable(self):
        """Les scalaires ne doivent pas être convertis en str (sinon `%d` casse)."""
        logger, tampon = _logger_capture("test_redaction_int")
        logger.warning("HTTP %d sur %s", 400, "gemini-3.5-flash")
        assert "HTTP 400 sur gemini-3.5-flash" in tampon.getvalue()

    def test_trace_d_exception(self):
        """`exc_info=True` ré-ouvrirait la fuite par la traceback sans traitement."""
        logger, tampon = _logger_capture("test_redaction_exc")
        try:
            raise ValueError(URL_FUITEE)
        except ValueError:
            logger.error("appel Gemini en échec", exc_info=True)

        sortie = tampon.getvalue()
        assert "Traceback" in sortie, "la trace doit toujours être journalisée"
        assert CLE_FACTICE not in sortie
        assert "key=***" in sortie

    def test_installation_idempotente(self):
        logger, _ = _logger_capture("test_redaction_idem")
        installer_redaction(logger)
        installer_redaction(logger)
        filtres = [f for f in logger.handlers[0].filters if isinstance(f, FiltreRedaction)]
        assert len(filtres) == 1

    def test_compte_les_handlers_proteges(self):
        logger = logging.getLogger("test_redaction_compte")
        logger.handlers = [logging.StreamHandler(io.StringIO()), logging.StreamHandler(io.StringIO())]
        assert installer_redaction(logger) == 2
        assert installer_redaction(logger) == 0   # déjà protégés
