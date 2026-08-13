"""
core/ha_url.py — Lecture centralisée de l'URL Home Assistant (#T330).

UNIQUE endroit du code autorisé à lire l'URL HA dans l'environnement.
Tous les appels HA passent par `get_ha_url()`.

Deux noms coexistent historiquement dans le .env :
  - HASS_URL (convention de ce moteur, prioritaire),
  - HA_URL   (convention des outils MCP / tab5_pusher, en repli).

Même famille, même style, même traitement de l'absence que
`core/ha_token.py::get_ha_token()` (T239) : PAS de valeur par défaut.
Avant #T330, neuf sites portaient `http://192.168.1.x:8123` en dur comme
repli — une URL en clair vers un serveur qui n'écoute qu'en HTTPS. Un
défaut faux est pire que pas de défaut : Home Assistant refoule le clair
sans répondre (`RemoteDisconnected`, cf. #T329), et ces valeurs finissaient
recopiées dans un prompt, un contexte RAG, puis proposées à un agent. Ici :
pas de défaut ; chaque appelant nomme la variable manquante dans son message
d'échec, et le warning ci-dessous la nomme dans le journal.

⚠️ Même réserve que le token : les endroits qui testent la seule PRÉSENCE
d'une clé pour un diagnostic de configuration n'utilisent PAS cet accesseur.
"""

import logging
import os

logger = logging.getLogger(__name__)


def get_ha_url() -> str:
    """URL Home Assistant : HASS_URL prioritaire, repli HA_URL.

    Retourne "" si aucune des deux variables n'est définie — jamais
    d'exception ni d'URL inventée : chaque appelant décide de la conduite à
    tenir (skip, log, message d'erreur) selon son contexte.
    """
    url = os.environ.get("HASS_URL") or os.environ.get("HA_URL") or ""
    if not url:
        logger.warning(
            "[HA_URL] HASS_URL et HA_URL absentes : aucun appel Home Assistant "
            "ne peut partir. Définissez l'une des deux variables (URL en HTTPS, "
            "Home Assistant n'écoute pas en clair sur le port 8123)."
        )
    return url
