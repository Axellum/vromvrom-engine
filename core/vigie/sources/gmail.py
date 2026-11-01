"""
core/vigie/sources/gmail.py — Collecteur Gmail (lecture seule).

Jeton : `google_token.json` via `core.gcp_oauth_client` ; droits vérifiés le
07/10/2026 sur le PC et le Deck : `gmail.readonly`, pas `gmail.modify`. La
vigie n'appelle que des GET : ni étiquette, ni « lu », ni suppression.

Un passage = une liste (`messages?q=…`, identifiants seulement) puis, pour les
seuls identifiants jamais vus, une lecture `format=metadata` (From, Subject,
Date, List-Unsubscribe, `snippet`, `labelIds`, `threadId`). Le corps du mail
n'est jamais téléchargé.

`List-Unsubscribe` : seule sa PRÉSENCE est gardée (booléen `desabonnement`),
jamais sa valeur (des liens). Elle sépare l'envoi de masse (lettre
d'information) du transactionnel (facture, sécurité, commande) dans l'onglet
« Mises à jour », où tombent 53 des 78 mails d'Axel sur 7 jours (07/10/2026).
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parseaddr
from urllib.parse import quote

from core.vigie.etat import borner
from core.vigie.modeles import Element, nettoyer_texte
from core.vigie.sources.base import Collecteur, ResultatCollecte

logger = logging.getLogger(__name__)

API = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
ENTETES_LUS = ("From", "Subject", "Date", "List-Unsubscribe")
MAX_VUS = 3000          # identifiants gardés ; la requête en liste au plus 100 par passage
MAX_LISTE = 100


def _client_par_defaut():
    from core.gcp_oauth_client import get_gcp_client

    return get_gcp_client()


def nom_expediteur(entete_from: str) -> str:
    """« Jean Dupont <jean@x.fr> » → « Jean Dupont » ; sans nom → l'adresse."""
    nom, adresse = parseaddr(entete_from or "")
    return nom.strip().strip('"') or adresse or "(expéditeur inconnu)"


def lien_gmail(thread_id: str) -> str | None:
    # Identifiant hexadécimal rendu par l'API ; on refuse tout autre caractère
    # pour qu'un champ inattendu ne fabrique pas un lien arbitraire.
    if not thread_id or not all(c in "0123456789abcdef" for c in thread_id.lower()):
        return None
    return f"https://mail.google.com/mail/u/0/#all/{thread_id}"


def url_metadata(mid: str) -> str:
    """Lecture d'un message : en-têtes choisis + `snippet`, jamais le corps."""
    return f"{API}/{quote(mid, safe='')}?format=metadata" + "".join(
        f"&metadataHeaders={nom}" for nom in ENTETES_LUS
    )


def element_depuis_metadata(meta: dict) -> Element:
    entetes = {
        h.get("name", "").lower(): h.get("value", "")
        for h in (meta.get("payload") or {}).get("headers", [])
    }
    expediteur = entetes.get("from", "")
    objet = entetes.get("subject", "") or "(sans objet)"
    nom = nettoyer_texte(nom_expediteur(expediteur), 40)
    try:
        date_iso = datetime.fromtimestamp(int(meta.get("internalDate", 0)) / 1000, UTC).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        date_iso = ""
    extrait = nettoyer_texte(html.unescape(meta.get("snippet", "")), 300)
    return Element(
        source="gmail",
        ident=str(meta.get("id", "")),
        # Titre = en-têtes lus, jamais un texte du LLM.
        titre=nettoyer_texte(f"{nom} — {objet}", 110),
        message=nettoyer_texte(extrait, 120),
        lien=lien_gmail(str(meta.get("threadId", ""))),
        a_trier=True,
        expediteur=nettoyer_texte(expediteur, 120),
        objet=nettoyer_texte(objet, 200),
        extrait=extrait,
        labels=[str(x) for x in meta.get("labelIds", []) if isinstance(x, str)],
        desabonnement=bool(entetes.get("list-unsubscribe", "").strip()),
        date_iso=date_iso,
    )


class CollecteurGmail(Collecteur):
    nom = "gmail"

    def __init__(self, requete: str, client_factory: Callable | None = None):
        self.requete = requete
        self._client_factory = client_factory or _client_par_defaut

    async def collecter(self, etat_source: dict, *, essai_tri: int = 0) -> ResultatCollecte:
        # Client HTTP synchrone (`requests`) : hors de la boucle asyncio.
        return await asyncio.to_thread(self._collecter_sync, etat_source, essai_tri)

    def _collecter_sync(self, etat_source: dict, essai_tri: int) -> ResultatCollecte:
        client = self._client_factory()
        liste = client.get_json_strict(f"{API}?q={quote(self.requete)}&maxResults={MAX_LISTE}")
        ids = [str(m["id"]) for m in liste.get("messages", []) if isinstance(m, dict) and m.get("id")]

        vus = [str(x) for x in etat_source.get("vus", [])]
        deja = set(vus)
        inconnus = [i for i in ids if i not in deja]
        nouvel_etat = dict(etat_source)
        # L'API rend du plus récent au plus ancien : on ajoute dans l'ordre chronologique.
        nouvel_etat["vus"] = borner(vus + list(reversed(inconnus)), MAX_VUS)
        nouvel_etat["initialise"] = True

        premier = not etat_source.get("initialise")
        if essai_tri:
            a_lire = ids[:essai_tri]
        elif premier:
            return ResultatCollecte(
                [], nouvel_etat, premier_passage=True, nb_marques_vus=len(inconnus),
                detail=f"{len(ids)} mail(s) dans « {self.requete} »",
            )
        else:
            a_lire = inconnus

        elements: list[Element] = []
        for mid in reversed(a_lire):  # du plus ancien au plus récent
            try:
                meta = client.get_json_strict(url_metadata(mid))
            except FileNotFoundError:
                # Supprimé entre la liste et la lecture : rien à signaler.
                continue
            elements.append(element_depuis_metadata(meta))
        return ResultatCollecte(
            elements, nouvel_etat, premier_passage=premier and not essai_tri,
            detail=f"{len(ids)} mail(s) dans « {self.requete} », {len(inconnus)} jamais vu(s)",
        )
