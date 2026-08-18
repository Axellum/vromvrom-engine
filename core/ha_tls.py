"""
core/ha_tls.py — Politique TLS centralisée pour les connexions Home Assistant (P0-1.5).

UNIQUE endroit du code autorisé à manipuler la vérification TLS. Tous les appels
HA (aiohttp `ssl=`, contextes SSL manuels) passent par `ha_ssl_context()`.

Comportement (sécurisé par défaut) :
  - HA_VERIFY_TLS absent/true  → vérification ACTIVÉE (CA système, ou HA_CA_BUNDLE
    si fourni — recommandé pour un certificat auto-signé : pointer le .pem/.crt).
  - HA_VERIFY_TLS=false        → vérification DÉSACTIVÉE (certif auto-signé sans CA
    fournie). Tolérable sur un LAN de confiance, à éviter sinon. Log un warning.
  - HA_TLS_SERVER_HOSTNAME     → nom d'hôte à vérifier dans le certificat, quand
    `HASS_URL` vise une IP locale mais que le certificat ne couvre qu'un nom DNS
    (cas Freebox : certificat Let's Encrypt `axellum.freeboxos.fr` servi sur
    192.168.1.10:8123). Équivalent du `--resolve` de curl : on joint l'IP locale
    (rapide, sans dépendance WAN) tout en validant chaîne ET nom du certificat.

⚠️ DÉPLOIEMENT : si Home Assistant est en HTTPS avec un certificat auto-signé,
définir soit `HA_CA_BUNDLE=/chemin/vers/ca.pem` (préféré), soit `HA_VERIFY_TLS=false`,
sinon les appels HA échoueront (échec de validation du certificat).
"""

import logging
import os
import ssl

logger = logging.getLogger(__name__)

_FALSY = {"0", "false", "no", "off", "non"}

# Le warning d'opt-out TLS n'est émis qu'une seule fois par démarrage : c'est un
# choix de configuration, pas un événement. Répété à chaque construction de
# contexte (jusqu'à ~644 fois en 36 h), il noierait le reste du journal.
_warning_optout_affiche = False


class _PinnedHostnameContext(ssl.SSLContext):
    """Contexte SSL qui vérifie le certificat contre un nom d'hôte imposé.

    `HASS_URL` pointe une IP (192.168.1.10) alors que le certificat ne couvre
    qu'un nom DNS ; sans cela la vérification échoue sur « IP address mismatch ».
    Le nom présenté en SNI et vérifié est donc forcé ici, pour tous les appelants
    d'un coup — aucun site d'appel n'a à passer `server_hostname`, y compris
    `aiohttp.TCPConnector` qui ne l'accepte pas.

    La vérification reste ENTIÈRE : chaîne de confiance, date, et nom d'hôte. Un
    certificat qui ne couvre pas `_pinned_hostname` est rejeté (« Hostname
    mismatch »), donc la valeur doit correspondre au certificat réellement servi.
    """

    _pinned_hostname = ""

    def wrap_bio(self, incoming, outgoing, server_side=False,
                 server_hostname=None, session=None):
        # Chemin utilisé par asyncio/aiohttp (TLS sur BIO mémoire).
        return super().wrap_bio(incoming, outgoing, server_side=server_side,
                                server_hostname=self._pinned_hostname, session=session)

    def wrap_socket(self, sock, server_side=False, do_handshake_on_connect=True,
                    suppress_ragged_eofs=True, server_hostname=None, session=None):
        # Chemin utilisé par les clients synchrones (requests, http.client).
        return super().wrap_socket(
            sock, server_side=server_side,
            do_handshake_on_connect=do_handshake_on_connect,
            suppress_ragged_eofs=suppress_ragged_eofs,
            server_hostname=self._pinned_hostname, session=session,
        )


def ha_tls_verification_enabled() -> bool:
    """True si la vérification TLS des connexions HA est active (défaut : True)."""
    return os.environ.get("HA_VERIFY_TLS", "true").strip().lower() not in _FALSY


def ha_tls_pinned_hostname() -> str:
    """Nom d'hôte imposé pour la vérification du certificat ("" si aucun)."""
    return os.environ.get("HA_TLS_SERVER_HOSTNAME", "").strip()


def ha_ssl_context() -> ssl.SSLContext:
    """Contexte SSL pour les connexions Home Assistant.

    Utilisable directement avec aiohttp (`ssl=ha_ssl_context()`) ou tout client
    acceptant un `ssl.SSLContext`.
    """
    if ha_tls_verification_enabled():
        ca_bundle = os.environ.get("HA_CA_BUNDLE", "").strip()
        if ca_bundle and os.path.exists(ca_bundle):
            logger.debug("[HA-TLS] Vérification TLS via CA dédiée : %s", ca_bundle)
            ctx = ssl.create_default_context(cafile=ca_bundle)
        else:
            ctx = ssl.create_default_context()

        pinned = ha_tls_pinned_hostname()
        if pinned:
            # `create_default_context()` porte les réglages durcis de la stdlib
            # (versions min, ciphers) : on le conserve et on ne substitue que la
            # classe, plutôt que de reconstruire un contexte moins bien réglé.
            ctx.__class__ = _PinnedHostnameContext
            ctx._pinned_hostname = pinned
            logger.debug("[HA-TLS] Nom d'hôte vérifié imposé : %s", pinned)
        return ctx

    # Opt-out explicite (HA_VERIFY_TLS=false) — seule désactivation TLS du projet.
    # Le warning est émis une seule fois par démarrage (premier appel) : le
    # comportement TLS, lui, est identique à chaque appel.
    global _warning_optout_affiche
    if not _warning_optout_affiche:
        _warning_optout_affiche = True
        logger.warning(
            "[HA-TLS] Vérification TLS DÉSACTIVÉE (HA_VERIFY_TLS=false). "
            "À réserver à un certificat auto-signé sur LAN de confiance ; "
            "préférer HA_CA_BUNDLE."
        )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def ha_requests_verify() -> bool | str:
    """Valeur `verify=` pour `requests` : False, chemin de CA, ou True."""
    if not ha_tls_verification_enabled():
        return False
    ca_bundle = os.environ.get("HA_CA_BUNDLE", "").strip()
    if ca_bundle and os.path.exists(ca_bundle):
        return ca_bundle
    return True


def ha_requests_session():
    """Session `requests` appliquant la politique TLS HA.

    Pendant synchrone de `ha_ssl_context()` : `verify=` ne sait pas exprimer un
    nom d'hôte imposé, donc le contexte est monté via un adaptateur quand
    HA_TLS_SERVER_HOSTNAME est défini.
    """
    import requests

    session = requests.Session()
    session.verify = ha_requests_verify()

    if ha_tls_verification_enabled() and ha_tls_pinned_hostname():
        from requests.adapters import HTTPAdapter

        context = ha_ssl_context()

        class _PinnedAdapter(HTTPAdapter):
            def init_poolmanager(self, *args, **kwargs):
                kwargs["ssl_context"] = context
                return super().init_poolmanager(*args, **kwargs)

        session.mount("https://", _PinnedAdapter())

    return session
