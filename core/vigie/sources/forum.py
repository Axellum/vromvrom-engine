"""
core/vigie/sources/forum.py — Collecteur d'un forum Discourse (API JSON publique).

Sans compte, en lecture seule. Faits vérifiés le 07/10/2026 :
  - `/t/<id>.json` → `post_stream.stream` = TOUS les identifiants de messages,
    triés ; seuls 20 messages sont détaillés dans `post_stream.posts` ;
  - `/t/<id>/posts.json?post_ids[]=A&post_ids[]=B` détaille les autres ;
  - `/user_actions.json?username=<u>&filter=4,5` (sujets créés + réponses) est
    public ; `filter=6,7` (réponses à lui, mentions) rend 404 sans connexion.

User-Agent : `Mozilla/5.0 (compatible; MoteurVigie/1.0)`. Mesuré le 07/10 sur
community.home-assistant.io : un User-Agent de navigateur COMPLET (Chrome ou
Firefox) envoyé par Python reçoit la page de défi Cloudflare (403, « Just a
moment… ») — l'en-tête ne correspond pas à l'empreinte TLS ; `Mozilla/5.0`
court ou un robot qui se nomme passent (200). forum.hacf.fr accepte tout.

Fils suivis = fils configurés + fils où le pseudo a posté depuis
`jours_decouverte` jours (rafraîchi toutes les ~6 h). Un fil découvert alors
que la source tourne déjà est relu sur ses 20 derniers messages : ceux postés
après la découverte précédente sont signalés (sinon une réponse arrivée entre
le message d'Axel et la découverte serait marquée vue sans alerte).
"""

from __future__ import annotations

import asyncio
import copy
import html
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from core.vigie.modeles import Element, Niveau, nettoyer_texte
from core.vigie.sources.base import Collecteur, ResultatCollecte

logger = logging.getLogger(__name__)

ENTETES = {"User-Agent": "Mozilla/5.0 (compatible; MoteurVigie/1.0)", "Accept": "application/json"}
LOT_POSTS = 20                    # identifiants par appel à posts.json
PAGES_DECOUVERTE_MAX = 10
PERIODE_DECOUVERTE_S = 6 * 3600

_ASIDE = re.compile(r"<aside\b.*?</aside>", re.I | re.S)
_BALISE = re.compile(r"<[^>]+>")


class Introuvable(Exception):
    """403 / 404 : fil supprimé, privé, ou défi Cloudflare."""


def _http_get_defaut(url: str, params: list[tuple[str, object]]) -> tuple[int, dict | None]:
    import requests

    try:
        reponse = requests.get(url, params=params, headers=ENTETES, timeout=20)
    except requests.RequestException as exc:
        raise RuntimeError(f"forum injoignable ({type(exc).__name__})") from None
    try:
        donnees = reponse.json()
    except ValueError:
        donnees = None
    return reponse.status_code, donnees


def texte_depuis_cooked(cooked: str) -> str:
    """HTML d'un message → texte brut, sans les citations d'autres messages."""
    sans_citation = _ASIDE.sub(" ", cooked or "")
    return html.unescape(_BALISE.sub(" ", sans_citation))


def _date(valeur) -> datetime | None:
    if not isinstance(valeur, str) or not valeur:
        return None
    try:
        return datetime.fromisoformat(valeur.replace("Z", "+00:00"))
    except ValueError:
        return None


class CollecteurForum(Collecteur):
    def __init__(
        self,
        base: str,
        pseudo: str,
        fils_configures: list[int],
        jours_decouverte: int = 90,
        http_get: Callable | None = None,
        horloge: Callable[[], datetime] | None = None,
        pause_s: float = 0.3,
    ):
        self.base = base.rstrip("/")
        self.pseudo = pseudo
        self.fils_configures = list(dict.fromkeys(int(f) for f in fils_configures))
        self.jours_decouverte = jours_decouverte
        self._http_get = http_get or _http_get_defaut
        self._horloge = horloge or (lambda: datetime.now(UTC))
        self._pause_s = pause_s
        self.nom = f"forum:{urlsplit(self.base).hostname}"

    # ── accès HTTP ────────────────────────────────────────────────

    def _get(self, chemin: str, params: list[tuple[str, object]] | None = None) -> dict:
        statut, donnees = self._http_get(self.base + chemin, params or [])
        if statut in (403, 404):
            raise Introuvable(f"HTTP {statut}")
        if statut != 200 or not isinstance(donnees, dict):
            raise RuntimeError(f"HTTP {statut}")
        if self._pause_s:
            time.sleep(self._pause_s)  # politesse envers le forum
        return donnees

    def _decouvrir(self, maintenant: datetime) -> dict[str, str]:
        """{id du fil: date ISO de la dernière action du pseudo} sur la fenêtre."""
        limite = maintenant - timedelta(days=self.jours_decouverte)
        trouves: dict[str, str] = {}
        offset = 0
        for _ in range(PAGES_DECOUVERTE_MAX):
            donnees = self._get("/user_actions.json", [
                ("username", self.pseudo), ("filter", "4,5"), ("offset", offset),
            ])
            actions = donnees.get("user_actions") or []
            if not actions:
                break
            recente_sur_la_page = False
            for action in actions:
                quand = _date(action.get("created_at"))
                fil = action.get("topic_id")
                if quand is None or not isinstance(fil, int) or quand < limite:
                    continue
                recente_sur_la_page = True
                cle = str(fil)
                if quand.isoformat() > trouves.get(cle, ""):
                    trouves[cle] = quand.isoformat()
            if not recente_sur_la_page:
                break  # page entièrement hors fenêtre : les suivantes sont plus anciennes
            offset += len(actions)
        return trouves

    def _details(self, fil: int, ids: list[int]) -> list[dict]:
        posts: list[dict] = []
        for i in range(0, len(ids), LOT_POSTS):
            lot = ids[i:i + LOT_POSTS]
            donnees = self._get(f"/t/{fil}/posts.json", [("post_ids[]", pid) for pid in lot])
            posts.extend(p for p in (donnees.get("post_stream") or {}).get("posts", []) if isinstance(p, dict))
        return posts

    # ── analyse d'un message ──────────────────────────────────────

    def _est_message_d_autrui(self, post: dict) -> bool:
        if post.get("post_type", 1) != 1:          # 3 = action de modération (fil fermé…)
            return False
        if post.get("hidden") or post.get("deleted_at") or post.get("user_deleted"):
            return False
        return str(post.get("username", "")).lower() != self.pseudo.lower()

    def repond_a_axel(self, post: dict) -> bool:
        """Réponse directe, citation d'un message d'Axel, ou mention @pseudo."""
        cible = post.get("reply_to_user")
        if isinstance(cible, dict) and str(cible.get("username", "")).lower() == self.pseudo.lower():
            return True
        cooked = post.get("cooked") or ""
        pseudo = re.escape(self.pseudo)
        if re.search(rf'<aside\b[^>]*\bdata-username="{pseudo}"', cooked, re.I):
            return True
        # Mention dans le texte propre au message (les citations sont retirées).
        return bool(re.search(rf"(?<![\w@])@{pseudo}(?![\w-])", texte_depuis_cooked(cooked), re.I))

    def _element(self, fil: int, titre_fil: str, post: dict) -> Element:
        auteur = nettoyer_texte(str(post.get("username", "?")), 30)
        if self.repond_a_axel(post):
            titre = f"{auteur} te répond — {titre_fil}"
        else:
            titre = f"{auteur} sur « {titre_fil} »"
        numero = post.get("post_number")
        lien = f"{self.base}/t/{fil}/{numero}" if isinstance(numero, int) else f"{self.base}/t/{fil}"
        return Element(
            source=self.nom,
            ident=str(post.get("id", "")),
            titre=nettoyer_texte(titre, 110),
            message=nettoyer_texte(texte_depuis_cooked(post.get("cooked", "")), 200),
            lien=lien,
            niveau=Niveau.URGENT,
            methode_tri="forum",
            date_iso=str(post.get("created_at", "")),
        )

    # ── passage ───────────────────────────────────────────────────

    async def collecter(self, etat_source: dict, *, essai_tri: int = 0) -> ResultatCollecte:
        return await asyncio.to_thread(self._collecter_sync, etat_source, essai_tri)

    def _collecter_sync(self, etat_source: dict, essai_tri: int) -> ResultatCollecte:
        maintenant = self._horloge()
        etat = copy.deepcopy(etat_source)
        premier = not etat_source.get("initialise")
        erreurs: list[str] = []
        elements: list[Element] = []
        marques = 0

        # 1. Fils où le pseudo a posté récemment (rafraîchis toutes les ~6 h).
        if self.jours_decouverte > 0:
            dernier = etat.get("decouverte_ts")
            if dernier is None or maintenant.timestamp() - dernier >= PERIODE_DECOUVERTE_S:
                try:
                    etat["decouverts"] = self._decouvrir(maintenant)
                    etat["reference_nouveaux_fils_ts"] = dernier
                    etat["decouverte_ts"] = maintenant.timestamp()
                except Exception as exc:
                    erreurs.append(f"fils de {self.pseudo} : {exc}")
        decouverts = [int(f) for f in (etat.get("decouverts") or {}) if str(f).isdigit()]
        suivis = list(dict.fromkeys(self.fils_configures + decouverts))
        reference = etat.get("reference_nouveaux_fils_ts")

        fils_etat: dict = etat.get("fils") or {}
        nouveaux_fils_etat: dict = {}
        for fil in suivis:
            ancien = fils_etat.get(str(fil))
            try:
                sujet = self._get(f"/t/{fil}.json")
                stream = [int(x) for x in (sujet.get("post_stream") or {}).get("stream", [])]
                titre = nettoyer_texte(sujet.get("title") or f"fil {fil}", 80)
                plus_haut = max(stream, default=0)
                seuil_date: datetime | None = None
                if essai_tri:
                    a_lire = stream[-essai_tri:]
                elif premier or (ancien is None and (fil in self.fils_configures or reference is None)):
                    # Premier passage (de la source ou d'un fil ajouté à la config) : tout est vu.
                    nouveaux_fils_etat[str(fil)] = {"max_id": plus_haut, "titre": titre}
                    marques += len(stream)
                    continue
                elif ancien is None:
                    # Fil découvert : seuls les messages postés depuis la découverte précédente.
                    a_lire = stream[-LOT_POSTS:]
                    seuil_date = datetime.fromtimestamp(reference, UTC)
                else:
                    a_lire = [i for i in stream if i > int(ancien.get("max_id", 0))]
                posts = self._details(fil, a_lire) if a_lire else []
            except Introuvable as exc:
                if fil in self.fils_configures:
                    erreurs.append(f"fil {fil} introuvable ({exc})")
                else:
                    logger.info("[VIGIE] %s : fil découvert %s illisible (%s), ignoré.", self.nom, fil, exc)
                if ancien is not None:
                    nouveaux_fils_etat[str(fil)] = ancien
                continue
            except Exception as exc:
                erreurs.append(f"fil {fil} : {exc}")
                if ancien is not None:
                    nouveaux_fils_etat[str(fil)] = ancien  # rien n'avance : relu au prochain passage
                continue

            for post in sorted(posts, key=lambda p: p.get("post_number") or 0):
                if not self._est_message_d_autrui(post):
                    continue
                if seuil_date is not None:
                    quand = _date(post.get("created_at"))
                    if quand is None or quand <= seuil_date:
                        continue
                elements.append(self._element(fil, titre, post))
            precedent = int(ancien.get("max_id", 0)) if ancien else 0
            nouveaux_fils_etat[str(fil)] = {"max_id": max(plus_haut, precedent), "titre": titre}

        # Un fil qui n'est plus suivi sort de l'état (il sera « découvert » s'il revient).
        etat["fils"] = nouveaux_fils_etat
        etat["initialise"] = True
        return ResultatCollecte(
            elements, etat, premier_passage=premier and not essai_tri, nb_marques_vus=marques,
            erreurs=erreurs,
            detail=(
                f"{len(suivis)} fil(s) suivi(s) : {len(self.fils_configures)} configuré(s), "
                f"{len([f for f in decouverts if f not in self.fils_configures])} découvert(s) "
                f"(posts de {self.pseudo} sur {self.jours_decouverte} j)"
            ),
        )
