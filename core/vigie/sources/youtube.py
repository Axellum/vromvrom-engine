"""
core/vigie/sources/youtube.py — Collecteur YouTube : nouveaux commentaires sur une chaîne.

API YouTube Data v3, en lecture seule, avec une simple clé d'API : les
commentaires publiés sont publics. Faits mesurés le 01/11/2026 :
  - `commentThreads.list` avec `allThreadsRelatedToChannelId` rend les fils de
    TOUTES les vidéos de la chaîne (1 unité de quota par page de 100) ; la
    portée OAuth `youtube.readonly` y est refusée (403), une clé d'API passe ;
  - un fil ne détaille qu'une partie de ses réponses : au-delà, `comments.list`
    avec `parentId` (1 unité par page), appelé seulement quand le nombre de
    réponses du fil a changé depuis le passage précédent ;
  - les commentaires « retenus pour examen » ne se lisent qu'avec la portée
    `youtube.force-ssl` (qui permet aussi d'écrire) : pas ici, YouTube les
    signale lui-même dans YouTube Studio.
Une passe complète coûte 1 à 3 unités ; le quota du projet est de 10 000/jour.

Alerte : chaque nouveau commentaire ou réponse d'un autre compte que la
chaîne (« te répond » quand la chaîne a déjà écrit dans le fil). Clé :
`MOTEUR_VIGIE_YOUTUBE_CLE`, à défaut `CLOUD_API_KEY` (projet Google ha-delta,
où l'API YouTube Data est activée), lue à chaque passage.
"""

from __future__ import annotations

import asyncio
import copy
import os
from collections.abc import Callable
from typing import Any

from core.vigie.modeles import Element, Niveau, nettoyer_texte
from core.vigie.sources.base import Collecteur, ResultatCollecte

API = "https://www.googleapis.com/youtube/v3/"
PAGES_MAX = 10           # 1 000 fils : bien au-delà de la chaîne du Tab5 (49 le 01/11/2026)
TITRES_MAX = 300         # titres de vidéos gardés dans l'état
VARIABLES_CLE = ("MOTEUR_VIGIE_YOUTUBE_CLE", "CLOUD_API_KEY")
# « te répond » : le message juste avant, dans le fil, est de la chaîne.
ACTIONS = {"commentaire": "a commenté", "reponse": "répond dans un fil", "te_repond": "te répond"}


class ErreurYoutube(Exception):
    pass


def _get_defaut(url: str, params: dict) -> tuple[int, Any]:
    import requests

    try:
        reponse = requests.get(url, params=params, timeout=20)
    except requests.RequestException as exc:
        raise ErreurYoutube(f"YouTube injoignable ({type(exc).__name__})") from None
    try:
        return reponse.status_code, reponse.json()
    except ValueError:
        return reponse.status_code, None


class CollecteurYoutube(Collecteur):
    def __init__(self, chaine: str, http_get: Callable | None = None, env: dict | None = None):
        self.chaine = chaine
        self._http_get = http_get or _get_defaut
        self._env = env
        self._cle = ""
        self.nom = "youtube"

    def _get(self, ressource: str, params: dict) -> Any:
        statut, donnees = self._http_get(API + ressource, {**params, "key": self._cle})
        if statut != 200 or not isinstance(donnees, dict):
            erreurs = ((donnees or {}).get("error") or {}).get("errors") if isinstance(donnees, dict) else None
            raison = ", ".join(e.get("reason", "") for e in erreurs or [] if isinstance(e, dict))
            # Jamais l'URL ni les paramètres : la clé y figure.
            raise ErreurYoutube(f"{ressource} : HTTP {statut} {raison}".strip())
        return donnees

    def _pages(self, ressource: str, params: dict) -> list[dict]:
        items: list[dict] = []
        jeton = None
        for _ in range(PAGES_MAX):
            donnees = self._get(ressource, {**params, **({"pageToken": jeton} if jeton else {})})
            items.extend(i for i in donnees.get("items", []) if isinstance(i, dict))
            jeton = donnees.get("nextPageToken")
            if not jeton:
                break
        return items

    def _de_la_chaine(self, snippet: dict) -> bool:
        return ((snippet.get("authorChannelId") or {}).get("value")) == self.chaine

    def _element(self, commentaire: dict, video: str, titre_video: str, genre: str) -> Element:
        s = commentaire.get("snippet") or {}
        auteur = nettoyer_texte(str(s.get("authorDisplayName") or "?"), 40)
        titre = f"{auteur} {ACTIONS[genre]}"
        return Element(
            source=self.nom,
            ident=str(commentaire.get("id", "")),
            titre=nettoyer_texte(f"{titre} — « {titre_video} »", 110),
            message=nettoyer_texte(str(s.get("textDisplay") or s.get("textOriginal") or ""), 200),
            lien=f"https://www.youtube.com/watch?v={video}&lc={commentaire.get('id', '')}" if video else None,
            niveau=Niveau.URGENT,
            methode_tri="youtube",
            date_iso=str(s.get("publishedAt") or ""),
        )

    async def collecter(self, etat_source: dict, *, essai_tri: int = 0) -> ResultatCollecte:
        return await asyncio.to_thread(self._collecter_sync, etat_source, essai_tri)

    def _collecter_sync(self, etat_source: dict, essai_tri: int) -> ResultatCollecte:
        env = os.environ if self._env is None else self._env
        self._cle = next((env[n].strip() for n in VARIABLES_CLE if env.get(n, "").strip()), "")
        if not self._cle:
            raise ErreurYoutube("aucune clé d'API YouTube (MOTEUR_VIGIE_YOUTUBE_CLE ou CLOUD_API_KEY)")
        etat = copy.deepcopy(etat_source)
        premier = not etat_source.get("initialise")
        vus = set(etat.get("vus") or [])
        nb_reponses: dict = etat.get("reponses") or {}
        titres: dict = etat.get("titres") or {}
        erreurs: list[str] = []

        fils = self._pages("commentThreads", {
            "part": "snippet,replies", "allThreadsRelatedToChannelId": self.chaine,
            "maxResults": 100, "order": "time", "textFormat": "plainText",
        })
        # (commentaire, vidéo, genre : commentaire, réponse, réponse juste après la chaîne)
        candidats: list[tuple[dict, str, str]] = []
        tous: set[str] = set()
        for fil in fils:
            s = fil.get("snippet") or {}
            video = str(s.get("videoId") or "")
            haut = s.get("topLevelComment") or {}
            if not haut.get("id"):
                continue
            reponses = list((fil.get("replies") or {}).get("comments") or [])
            total = int(s.get("totalReplyCount") or 0)
            if total > len(reponses) and nb_reponses.get(fil["id"]) != total:
                try:
                    reponses = self._pages("comments", {"part": "snippet", "parentId": haut["id"],
                                                        "maxResults": 100, "textFormat": "plainText"})
                    nb_reponses[fil["id"]] = total
                except ErreurYoutube as exc:
                    # Réponses partielles du fil ; le nombre n'est pas enregistré : relu au passage suivant.
                    erreurs.append(str(exc))
            else:
                nb_reponses[fil["id"]] = total
            messages = [haut] + sorted(reponses, key=lambda c: (c.get("snippet") or {}).get("publishedAt") or "")
            for rang, c in enumerate(messages):
                tous.add(str(c.get("id")))
                if self._de_la_chaine(c.get("snippet") or {}):
                    continue
                if rang == 0:
                    genre = "commentaire"
                else:
                    genre = "te_repond" if self._de_la_chaine(messages[rang - 1].get("snippet") or {}) else "reponse"
                candidats.append((c, video, genre))

        if essai_tri:
            nouveaux = sorted(candidats, key=lambda x: (x[0].get("snippet") or {}).get("publishedAt") or "")[-essai_tri:]
        elif premier:
            nouveaux = []
        else:
            nouveaux = [x for x in candidats if str(x[0].get("id")) not in vus]

        manquants = sorted({v for _, v, _ in nouveaux if v and v not in titres})
        if manquants:
            try:
                for i in range(0, len(manquants), 50):
                    for v in self._get("videos", {"part": "snippet", "id": ",".join(manquants[i:i + 50])}).get("items", []):
                        titres[v["id"]] = nettoyer_texte((v.get("snippet") or {}).get("title") or "", 70)
            except (ErreurYoutube, KeyError, TypeError) as exc:
                erreurs.append(f"titres des vidéos : {exc}")   # l'alerte part quand même, sans le titre
        elements = [self._element(c, v, titres.get(v) or "vidéo", genre) for c, v, genre in nouveaux]

        # Les identifiants vus s'accumulent (quelques centaines) : une réponse absente d'une lecture
        # partielle ne doit pas redevenir « nouvelle » au passage suivant.
        etat["vus"] = sorted(vus | tous)
        ids_fils = {f.get("id") for f in fils}
        etat["reponses"] = {k: n for k, n in nb_reponses.items() if k in ids_fils}
        videos = {str((f.get("snippet") or {}).get("videoId") or "") for f in fils}
        etat["titres"] = dict(list({k: t for k, t in titres.items() if k in videos}.items())[-TITRES_MAX:])
        etat["initialise"] = True
        return ResultatCollecte(
            elements, etat, premier_passage=premier and not essai_tri,
            nb_marques_vus=len(candidats) if premier and not essai_tri else 0, erreurs=erreurs,
            detail=f"{len(fils)} fil(s) de commentaires, {len(tous)} message(s), chaîne {self.chaine}",
        )
