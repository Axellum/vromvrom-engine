"""
core/ha_token.py — Lecture centralisée du token Home Assistant (T239).

UNIQUE endroit du code autorisé à lire le token HA dans l'environnement.
Tous les appels HA (`Authorization: Bearer ...`) passent par `get_ha_token()`.

Deux noms coexistent historiquement dans le .env :
  - HASS_TOKEN (convention de ce moteur, prioritaire),
  - HA_TOKEN   (convention des outils MCP / tab5_pusher, en repli).

`get_ha_token()` normalise le repli : HASS_TOKEN gagne s'il est défini, sinon
HA_TOKEN. Le .env conserve provisoirement les deux variables : le nettoyage
est une étape séparée, faite à la main (core/tab5_pusher.py lisait HA_TOKEN
sur le chemin de notification du watchdog en prod).

⚠️ Les endroits qui testent la seule PRÉSENCE d'une clé pour un diagnostic de
configuration (ex. api/routes/context.py, api/routes/vocal.py) n'utilisent PAS
cet accesseur : leur sémantique est « cette clé précise existe-t-elle », pas
« donne-moi le token ».
"""

import os


def get_ha_token() -> str:
    """Token Bearer Home Assistant : HASS_TOKEN prioritaire, repli HA_TOKEN.

    Retourne "" si aucune des deux variables n'est définie — jamais
    d'exception : chaque appelant décide de la conduite à tenir (skip,
    log, message d'erreur) selon son contexte.
    """
    return os.environ.get("HASS_TOKEN") or os.environ.get("HA_TOKEN") or ""
