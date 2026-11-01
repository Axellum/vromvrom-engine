"""
core/vigie/sources/github.py — Collecteur GitHub : réponses dans les fils où Axel a écrit.

Reprend les deux veilles du PC (`scripts/veille_fils_github.py` et
`scripts/veille_discussion.py` de la racine, tâches Windows horaires jusqu'en
novembre 2026), en lecture seule, par l'API GitHub (REST + GraphQL) au lieu de
la commande `gh`, absente du Deck.

Fils suivis :
  - les discussions configurées (`owner/depot#n`, ex. la #278 du Tab5) : tout
    message d'un autre compte ;
  - les issues, PR et discussions des dépôts d'AUTRES comptes que le pseudo a
    ouvertes ou commentées, trouvées par la recherche GitHub (fils mis à jour
    depuis `jours` jours) et par ses notifications « participating » (la
    recherche indexe avec retard) : les messages d'autres comptes écrits APRÈS
    sa première intervention. Un fil connu reste suivi tant qu'il a bougé
    depuis moins de `jours` jours.
Alerte : nouveau message, message modifié, et changement d'état du fil
(fermé, mergé, rouvert). Les robots (`…[bot]`) sont ignorés.

Jeton : `MOTEUR_VIGIE_GITHUB_TOKEN`, à défaut `GITHUB_PERSONAL_ACCESS_TOKEN`
(lu à chaque passage, jamais gardé dans les réglages ni dans l'état). Mesuré le
01/11/2026 : un jeton fin sans la permission « Notifications » reçoit 403 sur
/notifications, et la recherche d'issues lui demande `is:issue` ou `is:pr`
(422) ; on cherche donc les deux séparément.
"""

from __future__ import annotations

import asyncio
import copy
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from core.vigie.modeles import Element, Niveau, nettoyer_texte
from core.vigie.sources.base import Collecteur, ResultatCollecte

API = "https://api.github.com"
PAGES_MAX = 5           # 5 × 100 commentaires par fil, largement assez
VARIABLES_JETON = ("MOTEUR_VIGIE_GITHUB_TOKEN", "GITHUB_PERSONAL_ACCESS_TOKEN")

REQ_RECHERCHE_DISC = """query($q:String!){search(query:$q,type:DISCUSSION,first:50){nodes{
... on Discussion{number repository{nameWithOwner}}}}}"""
REQ_DISCUSSION = """query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){discussion(number:$n){
title closed createdAt author{login}
comments(last:50){nodes{id url author{login} createdAt lastEditedAt body
replies(last:50){nodes{id url author{login} createdAt lastEditedAt body}}}}}}}"""


class ErreurGithub(Exception):
    pass


def _requete_defaut(methode: str, url: str, params: dict | None, corps: dict | None,
                    jeton: str) -> tuple[int, object]:
    import requests

    entetes = {"Authorization": f"Bearer {jeton}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "MoteurVigie/1.0"}
    try:
        reponse = requests.request(methode, url, params=params, json=corps, headers=entetes, timeout=20)
    except requests.RequestException as exc:
        raise ErreurGithub(f"GitHub injoignable ({type(exc).__name__})") from None
    try:
        return reponse.status_code, reponse.json()
    except ValueError:
        return reponse.status_code, None


def _message(id_, url, auteur, cree, modifie, texte) -> dict:
    return {"id": str(id_), "url": url or "", "auteur": auteur or "?", "cree": cree or "",
            "stamp": modifie or cree or "", "texte": texte or ""}


def iso(quand: datetime) -> str:
    """Même forme que les dates de GitHub (`2026-11-01T10:00:00Z`), comparables comme des chaînes."""
    return quand.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def court(cle: str) -> str:
    """`issue:owner/depot#12` → `owner/depot#12`."""
    return cle.split(":", 1)[1]


def extrait(texte: str) -> str:
    """Le texte propre au message : sans les lignes citées (`>`) ni les commentaires HTML."""
    lignes = (ligne.strip() for ligne in (texte or "").splitlines())
    return " ".join(ligne for ligne in lignes if ligne and not ligne.startswith((">", "<!--")))


def parser_discussions(texte: str) -> tuple[str, ...]:
    """`owner/depot#278,owner/autre#3` → clés `discussion:owner/depot#278` ; une entrée invalide est ignorée."""
    cles = []
    for brut in (texte or "").split(","):
        brut = brut.strip()
        depot, _, num = brut.rpartition("#")
        if depot.count("/") == 1 and all(depot.split("/")) and num.isdigit():
            cles.append(f"discussion:{depot}#{int(num)}")
    return tuple(dict.fromkeys(cles))


class CollecteurGithub(Collecteur):
    def __init__(
        self,
        pseudo: str,
        discussions: tuple[str, ...] = (),
        jours: int = 60,
        requete: Callable | None = None,
        horloge: Callable[[], datetime] | None = None,
        env: dict | None = None,
    ):
        self.pseudo = pseudo
        self.discussions = tuple(discussions)
        self.jours = jours
        self._requete = requete or _requete_defaut
        self._horloge = horloge or (lambda: datetime.now(UTC))
        self._env = env
        self._jeton_courant = ""   # relu au début de chaque passage
        self.nom = "github"

    # ── accès HTTP ────────────────────────────────────────────────

    def _jeton(self) -> str:
        env = os.environ if self._env is None else self._env
        for nom in VARIABLES_JETON:
            if env.get(nom, "").strip():
                return env[nom].strip()
        raise ErreurGithub("aucun jeton GitHub (MOTEUR_VIGIE_GITHUB_TOKEN ou GITHUB_PERSONAL_ACCESS_TOKEN)")

    def _get(self, chemin: str, params: dict | None = None) -> Any:
        statut, donnees = self._requete("GET", API + chemin, params, None, self._jeton_courant)
        if statut != 200:
            message = donnees.get("message", "") if isinstance(donnees, dict) else ""
            raise ErreurGithub(nettoyer_texte(f"HTTP {statut} {message}", 160))
        return donnees

    def _liste(self, chemin: str, params: dict | None = None) -> list:
        tout: list = []
        for page in range(1, PAGES_MAX + 1):
            lot = self._get(chemin, {**(params or {}), "per_page": 100, "page": page})
            if not isinstance(lot, list):
                raise ErreurGithub(f"réponse inattendue pour {chemin}")
            tout.extend(lot)
            if len(lot) < 100:
                break
        return tout

    def _graphql(self, requete: str, **variables) -> Any:
        statut, donnees = self._requete("POST", API + "/graphql", None,
                                        {"query": requete, "variables": variables}, self._jeton_courant)
        if statut != 200 or not isinstance(donnees, dict) or donnees.get("errors") or "data" not in donnees:
            erreurs = donnees.get("errors") if isinstance(donnees, dict) else None
            detail = (erreurs or [{}])[0].get("message", "") if isinstance(erreurs, list) else ""
            raise ErreurGithub(nettoyer_texte(f"GraphQL HTTP {statut} {detail}", 160))
        return donnees["data"]

    # ── fils ──────────────────────────────────────────────────────

    def _decouvrir(self, depuis: str, erreurs: list[str]) -> set[str]:
        """Clés `issue:…`, `pr:…`, `discussion:…` des fils d'autres comptes où le pseudo a écrit."""
        cles: set[str] = set()
        for qui in ("author", "commenter"):
            q = f"{qui}:{self.pseudo} -user:{self.pseudo} updated:>={depuis}"
            for genre, filtre in (("issue", "is:issue"), ("pr", "is:pr")):
                try:
                    for it in self._get("/search/issues", {"q": f"{q} {filtre}", "per_page": 100}).get("items", []):
                        depot = it["repository_url"].split("/repos/", 1)[1]
                        cles.add(f"{genre}:{depot}#{it['number']}")
                except (ErreurGithub, KeyError, TypeError, AttributeError) as exc:
                    erreurs.append(f"recherche {genre} {qui} : {exc}")
            try:
                for d in self._graphql(REQ_RECHERCHE_DISC, q=q)["search"]["nodes"]:
                    if d:
                        cles.add(f"discussion:{d['repository']['nameWithOwner']}#{d['number']}")
            except (ErreurGithub, KeyError, TypeError) as exc:
                erreurs.append(f"recherche discussions {qui} : {exc}")
        try:
            for n in self._liste("/notifications", {"all": "true", "participating": "true",
                                                    "since": f"{depuis}T00:00:00Z"}):
                depot = n["repository"]["full_name"]
                url = (n.get("subject") or {}).get("url") or ""
                if n["repository"]["owner"]["login"].lower() == self.pseudo.lower() or not url:
                    continue
                genre = {"Issue": "issue", "PullRequest": "pr"}.get(n["subject"]["type"])
                if genre:
                    cles.add(f"{genre}:{depot}#{url.rstrip('/').rsplit('/', 1)[1]}")
        except (ErreurGithub, KeyError, TypeError, AttributeError) as exc:
            erreurs.append(f"notifications : {exc}")
        return cles

    def _lire_fil(self, cle: str) -> tuple[str, str, str | None, list[dict]]:
        """(titre, état du fil, début de la participation du pseudo, messages)."""
        genre, reste = cle.split(":", 1)
        depot, num = reste.rsplit("#", 1)
        if genre == "discussion":
            o, r = depot.split("/")
            d = self._graphql(REQ_DISCUSSION, o=o, r=r, n=int(num))["repository"]["discussion"]
            if d is None:
                raise ErreurGithub("discussion introuvable")
            titre, etat = d.get("title") or court(cle), "fermé" if d.get("closed") else "ouvert"
            msgs = [
                _message(m["id"], m.get("url"), (m.get("author") or {}).get("login"), m.get("createdAt"),
                         m.get("lastEditedAt"), m.get("body"))
                for c in d["comments"]["nodes"] for m in [c, *c["replies"]["nodes"]]
            ]
            auteur_fil, cree_fil = (d.get("author") or {}).get("login"), d.get("createdAt")
        else:
            info = self._get(f"/repos/{depot}/issues/{num}")
            titre, etat = info.get("title") or court(cle), "fermé" if info["state"] == "closed" else "ouvert"

            def modifie(c):
                return c["updated_at"] if c.get("updated_at") != c.get("created_at") else None

            msgs = [_message(c["id"], c.get("html_url"), (c.get("user") or {}).get("login"), c["created_at"],
                             modifie(c), c.get("body"))
                    for c in self._liste(f"/repos/{depot}/issues/{num}/comments")]
            if genre == "pr":
                if self._get(f"/repos/{depot}/pulls/{num}").get("merged"):
                    etat = "mergé"
                msgs += [_message(f"rc{c['id']}", c.get("html_url"), (c.get("user") or {}).get("login"),
                                  c["created_at"], modifie(c), c.get("body"))
                         for c in self._liste(f"/repos/{depot}/pulls/{num}/comments")]
                msgs += [_message(f"rv{v['id']}", v.get("html_url"), v["user"]["login"], v["submitted_at"], None,
                                  v.get("body") or f"revue : {str(v.get('state', '')).lower()}")
                         for v in self._liste(f"/repos/{depot}/pulls/{num}/reviews")
                         if v.get("submitted_at") and v.get("user")]
            auteur_fil, cree_fil = (info.get("user") or {}).get("login"), info.get("created_at")
        moi = [m["cree"] for m in msgs if m["auteur"].lower() == self.pseudo.lower()]
        if (auteur_fil or "").lower() == self.pseudo.lower() and cree_fil:
            moi.append(cree_fil)
        return titre, etat, (min(moi) if moi else None), msgs

    def _element(self, cle: str, titre_fil: str, ident: str, titre: str, texte: str, lien: str, quand: str) -> Element:
        return Element(
            source=self.nom,
            ident=ident,
            titre=nettoyer_texte(f"{titre} — {court(cle)}", 110),
            message=nettoyer_texte(" : ".join(x for x in (f"« {titre_fil} »" if titre_fil else "", texte) if x), 200),
            lien=lien or None,
            niveau=Niveau.URGENT,
            methode_tri="github",
            date_iso=quand,
        )

    # ── passage ───────────────────────────────────────────────────

    async def collecter(self, etat_source: dict, *, essai_tri: int = 0) -> ResultatCollecte:
        return await asyncio.to_thread(self._collecter_sync, etat_source, essai_tri)

    def _collecter_sync(self, etat_source: dict, essai_tri: int) -> ResultatCollecte:
        self._jeton_courant = self._jeton()
        maintenant = self._horloge()
        etat = copy.deepcopy(etat_source)
        premier = not etat_source.get("initialise")
        connus: dict = etat.get("fils") or {}
        limite = iso(maintenant - timedelta(days=self.jours))
        depuis = limite[:10]
        # Fil qui entre dans la liste : la recherche indexe avec retard, on remonte donc à la veille du
        # passage précédent (sinon une réponse arrivée avant sa découverte serait vue sans alerte).
        precedent = etat_source.get("dernier_passage")
        reference = iso(datetime.fromisoformat(precedent.replace("Z", "+00:00")) - timedelta(days=1)) if precedent else limite
        erreurs: list[str] = []
        elements: list[Element] = []
        decouverts = self._decouvrir(depuis, erreurs)
        # Un fil connu reste suivi tant qu'il a bougé dans la fenêtre (filet si la recherche l'oublie).
        gardes = {c for c, f in connus.items() if c in self.discussions or (f.get("bouge") or "") >= limite}
        cles = sorted(set(self.discussions) | decouverts | gardes)
        fils: dict = {}
        marques = 0
        for cle in cles:
            avant = connus.get(cle)
            try:
                titre, etat_fil, debut, msgs = self._lire_fil(cle)
            except (ErreurGithub, KeyError, TypeError, ValueError, AttributeError) as exc:
                erreurs.append(f"{court(cle)} : {exc}")
                if avant is not None:
                    fils[cle] = avant   # rien n'avance : relu au passage suivant
                continue
            configure = cle in self.discussions
            reponses = [m for m in msgs if m["auteur"].lower() != self.pseudo.lower()
                        and not m["auteur"].endswith("[bot]")
                        and (configure or (debut is not None and m["cree"] > debut))]
            bouge = max([m["stamp"] for m in msgs] + [(avant or {}).get("bouge") or ""])
            fils[cle] = {"etat": etat_fil, "messages": {m["id"]: m["stamp"] for m in reponses}, "bouge": bouge}
            if essai_tri:
                vus, etat_avant = {}, None
                reponses = reponses[-essai_tri:]
            elif premier or (avant is None and configure):
                # Premier passage, ou discussion ajoutée à la config : tout ce qui existe est vu.
                marques += len(reponses)
                continue
            elif avant is None:
                vus, etat_avant = {}, None
                marques += sum(1 for m in reponses if m["cree"] <= reference)
                reponses = [m for m in reponses if m["cree"] > reference]
            else:
                vus, etat_avant = avant.get("messages", {}), avant.get("etat")
            for m in reponses:
                if vus.get(m["id"]) == m["stamp"]:
                    continue
                quoi = "a modifié son message" if m["id"] in vus else "a répondu"
                elements.append(self._element(cle, titre, f"{cle}:{m['id']}:{m['stamp']}",
                                              f"{nettoyer_texte(m['auteur'], 30)} {quoi}", extrait(m["texte"]),
                                              m["url"], m["stamp"]))
            if etat_avant and etat_avant != etat_fil:
                elements.append(self._element(cle, titre, f"{cle}:etat:{etat_fil}:{maintenant:%Y%m%d%H%M}",
                                              f"Fil {etat_fil}", "", "", maintenant.isoformat(timespec="seconds")))
        etat["fils"] = fils
        etat["initialise"] = True
        etat["dernier_passage"] = iso(maintenant)
        return ResultatCollecte(
            elements, etat, premier_passage=premier and not essai_tri, nb_marques_vus=marques, erreurs=erreurs,
            detail=(f"{len(fils)} fil(s) suivi(s) : {len(self.discussions)} discussion(s) configurée(s), "
                    f"{len(decouverts)} trouvé(s) (fils de {self.pseudo} sur {self.jours} j)"),
        )
