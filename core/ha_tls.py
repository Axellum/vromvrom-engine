# -*- coding: utf-8 -*-
"""
core/ha_tls.py — Politique TLS centralisée pour les connexions Home Assistant (P0-1.5).

UNIQUE endroit du code autorisé à manipuler la vérification TLS. Tous les appels
HA (aiohttp `ssl=`, contextes SSL manuels) passent par `ha_ssl_context()`.

Comportement (sécurisé par défaut) :
  - HA_VERIFY_TLS absent/true  → vérification ACTIVÉE (CA système, ou HA_CA_BUNDLE
    si fourni — recommandé pour un certificat auto-signé : pointer le .pem/.crt).
  - HA_VERIFY_TLS=false        → vérification DÉSACTIVÉE (certif auto-signé sans CA
    fournie). Tolérable sur un LAN de confiance, à éviter sinon. Log un warning.

⚠️ DÉPLOIEMENT : si Home Assistant est en HTTPS avec un certificat auto-signé,
définir soit `HA_CA_BUNDLE=/chemin/vers/ca.pem` (préféré), soit `HA_VERIFY_TLS=false`,
sinon les appels HA échoueront (échec de validation du certificat).
"""

import os
import ssl
import logging

logger = logging.getLogger(__name__)

_FALSY = {"0", "false", "no", "off", "non"}


def ha_tls_verification_enabled() -> bool:
    """True si la vérification TLS des connexions HA est active (défaut : True)."""
    return os.environ.get("HA_VERIFY_TLS", "true").strip().lower() not in _FALSY


def ha_ssl_context() -> ssl.SSLContext:
    """Contexte SSL pour les connexions Home Assistant.

    Utilisable directement avec aiohttp (`ssl=ha_ssl_context()`) ou tout client
    acceptant un `ssl.SSLContext`.
    """
    if ha_tls_verification_enabled():
        ca_bundle = os.environ.get("HA_CA_BUNDLE", "").strip()
        if ca_bundle and os.path.exists(ca_bundle):
            logger.debug("[HA-TLS] Vérification TLS via CA dédiée : %s", ca_bundle)
            return ssl.create_default_context(cafile=ca_bundle)
        return ssl.create_default_context()

    # Opt-out explicite (HA_VERIFY_TLS=false) — seule désactivation TLS du projet.
    logger.warning(
        "[HA-TLS] Vérification TLS DÉSACTIVÉE (HA_VERIFY_TLS=false). "
        "À réserver à un certificat auto-signé sur LAN de confiance ; "
        "préférer HA_CA_BUNDLE."
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
