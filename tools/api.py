"""
tools/api.py — Outil "call_api" exposé aux agents via le ToolRegistry.

Exécute des requêtes HTTP vers des APIs distantes (REST générique, Home
Assistant, ESPHome) à partir d'un payload/headers JSON fournis par le LLM.
"""
import json
import logging
import os
from urllib.parse import urlsplit, urlunsplit

import requests

logger = logging.getLogger(__name__)

# [#T329] Signature d'un serveur TLS qui reçoit du HTTP en clair : il ferme la
# connexion sans jamais répondre. `requests` la remonte en ConnectionError
# enveloppant RemoteDisconnected — un message qui ne dit rien de la cause.
_SIGNATURES_PORT_TLS = ("RemoteDisconnected", "Connection aborted")


def _ressemble_a_un_port_tls(url: str, erreur: Exception) -> bool:
    """L'échec ressemble-t-il à « du HTTP en clair envoyé à un port HTTPS » ?"""
    if not url.lower().startswith("http://"):
        return False
    texte = str(erreur)
    return any(signature in texte for signature in _SIGNATURES_PORT_TLS)


def _bascule_en_https(url: str) -> str:
    parties = urlsplit(url)
    return urlunsplit(("https", parties.netloc, parties.path, parties.query, parties.fragment))


def _est_hote_ha(url: str) -> bool:
    """True si l'URL vise l'hôte Home Assistant configuré (HASS_URL / HA_URL)."""
    hote = (urlsplit(url).hostname or "").lower()
    if not hote:
        return False
    for var in ("HASS_URL", "HA_URL"):
        valeur = os.environ.get(var)
        if not valeur:
            continue
        h = (urlsplit(valeur).hostname or "").lower()
        if h and h == hote:
            return True
    return False


def _verify_pour(url: str):
    """
    Valeur `verify=` pour un rejeu HTTPS hors session HA.

    L'hôte HA ne passe PAS par ici : il utilise `ha_requests_session()` pour
    appliquer aussi `HA_TLS_SERVER_HOSTNAME` (pinning IP+DNS). Tout autre hôte
    garde la vérification standard — un outil générique ne doit pas devenir un
    trou de sécurité pour tout Internet.
    """
    if _est_hote_ha(url):
        try:
            from core.ha_tls import ha_requests_verify
            return ha_requests_verify()
        except Exception:
            return True
    return True


def _injecter_token_ha(url: str, headers: dict) -> dict:
    """
    [#T332] Ajoute le Bearer Home Assistant quand l'appel vise l'hôte HA et
    qu'aucune autorisation n'a été fournie. Retourne les en-têtes à utiliser.

    Mesuré en prod le 12/08 : une fois #T329 en place, l'appel atteint enfin HA
    (plus de `RemoteDisconnected`) et se fait refouler en **HTTP 401** — l'agent
    appelait `/api/states` sans aucun en-tête. Il ne peut pas faire autrement :
    le token n'est pas dans son prompt, et il ne DOIT pas y être. C'est donc à
    l'outil de l'ajouter, comme le font déjà les chemins MCP et vocal.

    Trois garde-fous :
      - injection réservée à l'hôte HA de la configuration (égalité stricte) —
        jamais vers une URL quelconque proposée par le modèle ;
      - sur l'hôte HA, le token de la configuration PRIME sur toute autorisation
        fournie par l'appelant (cf. #T340 ci-dessous) ; ailleurs, les en-têtes
        reçus ne sont jamais modifiés ;
      - jamais de Bearer sur `http://` (fuite LAN) — l'appelant doit d'abord
        basculer en HTTPS (pré-bascule hôte HA dans `call_api`).

    [#T340] Pourquoi le token configuré prime, alors que #T332 avait posé
    l'inverse. Mesuré en production le 12/08 sur `c6f4cd2` (session
    `chat_aa88ff7bed`, « quelle température dans le salon ? ») : la bascule
    HTTPS s'exécute, aucune ligne d'injection n'est écrite, et HA répond **401**
    en 118 ms. Rejoué à la main dans le conteneur de prod, le même appel **sans
    en-tête** rend **HTTP 200** avec les entités réelles, et le même appel avec
    `Authorization: Bearer YOUR_LONG_LIVED_ACCESS_TOKEN` rend **401** en silence
    — signature identique à la prod. Le token de la configuration, lui, est
    valide (vérifié : `HTTP 200` sur `https://…:8123/api/`).

    L'appelant de cet outil est un **modèle**, et le vrai token n'est ni dans son
    prompt ni destiné à y être : toute `Authorization` qu'il produit est donc
    nécessairement inventée (ici un placeholder de documentation). Le garde-fou
    « ne jamais écraser un choix explicite » protégeait une valeur qui ne peut
    pas être bonne, et transformait un appel qui aurait réussi en 401.
    """
    if not _est_hote_ha(url):
        return headers
    if url.lower().startswith("http://"):
        # [#T332 / Bugbot High] Le Bearer ne doit JAMAIS partir en clair : même
        # si le serveur TLS ferme sans répondre, la requête HTTP complète
        # (Authorization compris) a déjà traversé le LAN.
        logger.warning(
            "[API] [#T332] Refus d'injecter le token HA sur une URL en clair (%s).",
            urlsplit(url).netloc,
        )
        return headers
    try:
        from core.ha_token import get_ha_token
        token = get_ha_token()
    except Exception:
        token = ""
    if not token:
        # Sans token configuré, on laisse passer ce que l'appelant a fourni :
        # une chance sur mille que ce soit bon vaut mieux qu'une chance nulle.
        logger.warning(
            "[API] [#T332] Appel vers l'hôte Home Assistant sans token disponible "
            "(HASS_TOKEN/HA_TOKEN absents) — l'API répondra 401."
        )
        return headers
    enrichis = {
        cle: valeur for cle, valeur in headers.items() if cle.lower() != "authorization"
    }
    attendu = f"Bearer {token}"
    remplacee = any(
        cle.lower() == "authorization" and valeur != attendu
        for cle, valeur in headers.items()
    )
    enrichis["Authorization"] = attendu
    if remplacee:
        # Jamais la valeur reçue dans le journal : c'est un secret potentiel.
        logger.warning(
            "[API] [#T340] Autorisation fournie par l'appelant remplacée par le token "
            "Home Assistant configuré pour %s (le modèle ne peut pas connaître le vrai "
            "token — c'était la cause du 401).",
            urlsplit(url).netloc,
        )
    else:
        logger.info("[API] [#T332] Token Home Assistant injecté pour %s", urlsplit(url).netloc)
    return enrichis


def _requete_https(method: str, url: str, headers: dict, data, timeout: int = 15):
    """
    Rejeu HTTPS : session HA (verify + pinning hostname) pour l'hôte projet,
    `requests` + `verify=` sinon.
    """
    if _est_hote_ha(url):
        # [#T329 / Bugbot] `verify=` seul ne sait pas exprimer HA_TLS_SERVER_HOSTNAME
        # (certificat DNS servi sur une IP locale). MCP / vocal utilisent déjà
        # `ha_requests_session` — call_api doit suivre le même chemin.
        from core.ha_tls import ha_requests_session
        return ha_requests_session().request(
            method=method, url=url, headers=headers, json=data, timeout=timeout,
        )
    return requests.request(
        method=method, url=url, headers=headers, json=data,
        timeout=timeout, verify=_verify_pour(url),
    )


def call_api(url: str, method: str = "GET", payload_json: str = None, headers_json: str = None) -> str:
    """
    Effectue une requête HTTP vers une API distante (REST/HomeAssistant/ESPHome).
    payload_json et headers_json doivent être des chaînes (string) au format JSON valide s'ils sont utilisés.
    """
    logger.info(f"Appel API distant: {method.upper()} {url}")
    try:
        # Parsing des arguments JSON envoyés par le LLM
        headers = {}
        if headers_json:
            headers = json.loads(headers_json)

        data = None
        if payload_json:
            data = json.loads(payload_json)
            # Injecter Content-Type si payload présent et non spécifié
            if "Content-Type" not in headers:
                headers["Content-Type"] = "application/json"

        # [#T332 / #T329] Hôte HA connu en clair → HTTPS d'abord, SANS sonde HTTP.
        # HASS_URL est déjà en https:// : on sait que le port parle TLS. Sonde
        # http:// + Bearer = fuite du token admin sur le LAN (Bugbot High) avant
        # même le RemoteDisconnected. On saute directement au chemin chiffré.
        if _est_hote_ha(url) and url.lower().startswith("http://"):
            url_https = _bascule_en_https(url)
            logger.info(
                "[API] [#T329/#T332] Hôte HA en clair → bascule immédiate vers %s "
                "(aucune sonde HTTP, aucun Bearer en clair).",
                url_https,
            )
            url = url_https

        headers = _injecter_token_ha(url, headers)

        # [#T329] Rejeu en HTTPS quand l'URL vise en clair un port qui parle TLS
        # (hôtes NON-HA : on ne peut pas préjuger du schéma).
        #
        # Mesuré le 12/08 (session `chat_683ea8b2cd`) : l'agent appelait
        # `http://192.168.1.10:8123/api/states` alors que Home Assistant n'écoute
        # qu'en HTTPS sur ce port. Le serveur ferme la connexion sans répondre →
        # `RemoteDisconnected` → la tâche échoue après 60,9 s sur une question
        # aussi simple que « quelle température dans le salon ? ».
        # Reproduit à la demande : `http://…:8123/api/` lève RemoteDisconnected,
        # `https://…:8123/api/` répond HTTP 401. Même hôte, même port.
        #
        # Le rejeu est SÛR même en POST : le serveur TLS n'a rien pu traiter,
        # puisqu'il n'a jamais lu de requête valide — c'est précisément pourquoi
        # il ferme sans répondre. Et passer de clair à chiffré ne dégrade jamais
        # la sécurité.
        if _est_hote_ha(url) and url.lower().startswith("https://"):
            response = _requete_https(
                method.upper(), url, headers, data, timeout=15,
            )
        else:
            try:
                response = requests.request(
                    method=method.upper(), url=url, headers=headers, json=data, timeout=15,
                )
            except requests.exceptions.ConnectionError as err_conn:
                if not _ressemble_a_un_port_tls(url, err_conn):
                    raise
                url_https = _bascule_en_https(url)
                logger.info(
                    "[API] [#T329] %s a fermé la connexion sans répondre — le port "
                    "parle probablement TLS. Nouvelle tentative sur %s", url, url_https,
                )
                response = _requete_https(
                    method.upper(), url_https, headers, data, timeout=15,
                )
                url = url_https

        # Formatage lisible pour l'agent
        try:
            result = response.json()
            output = json.dumps(result, indent=2)
        except json.JSONDecodeError:
            output = response.text

        status = response.status_code
        if status >= 400:
            full_response = f"Erreur (HTTP {status}): {output}"
        else:
            full_response = f"Status Code: {status}\nResponse:\n{output}"

        MAX_CHARS = 4000
        if len(full_response) > MAX_CHARS:
            return full_response[:MAX_CHARS] + "\n...[SORTIE TRONQUÉE]..."

        return full_response

    except requests.exceptions.Timeout:
        return "Erreur: Délai d'attente dépassé (Timeout > 15s)."
    except requests.exceptions.RequestException as e:
        # [#T329] Nommer la cause probable plutôt que de recopier une trace
        # urllib3. « Remote end closed connection without response » n'apprend
        # rien à l'agent ; « le port parle TLS, utilise https:// » lui permet de
        # se corriger au tour suivant — c'est la boucle d'auto-correction de
        # l'Executor qui échouait faute d'un message actionnable.
        if _ressemble_a_un_port_tls(url, e):
            # Query + fragment conservés : même URL que le rejeu aurait utilisée.
            return (
                f"Erreur de réseau HTTP: {e}. Cause probable : l'URL est en "
                f"http:// alors que ce port attend du TLS — réessaie en "
                f"{_bascule_en_https(url)}"
            )
        return f"Erreur de réseau HTTP: {str(e)}"
    except json.JSONDecodeError as e:
        return f"Erreur de syntaxe JSON dans tes paramètres 'payload_json' ou 'headers_json' : {str(e)}"
    except Exception as e:
        return f"Erreur inattendue de l'outil API: {str(e)}"
