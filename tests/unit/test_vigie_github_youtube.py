"""
tests/unit/test_vigie_github_youtube.py — Sources GitHub et YouTube de la vigie, sans réseau.

Faux GitHub (REST + GraphQL) et faux YouTube Data, horloge fixée. Points
prouvés : premier passage sans alerte, nouvelle réponse alertée une seule fois,
message modifié, robot et messages d'avant la participation ignorés, fil fermé,
discussion configurée suivie en entier, fil découvert tardivement, jeton ou clé
absents, panne partielle ; côté YouTube, réponses tronquées relues seulement
quand leur nombre change, et la clé n'apparaît jamais dans une erreur.
"""

import asyncio
import re
from datetime import UTC, datetime

import pytest

from core.vigie.boucle import construire_collecteurs
from core.vigie.modeles import Niveau
from core.vigie.reglages import lire_reglages
from core.vigie.sources.github import CollecteurGithub, ErreurGithub, parser_discussions
from core.vigie.sources.youtube import CollecteurYoutube, ErreurYoutube

MAINTENANT = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
ENV_GH = {"MOTEUR_VIGIE_GITHUB_TOKEN": "jeton-test"}
ENV_YT = {"MOTEUR_VIGIE_YOUTUBE_CLE": "cle-test"}
CHAINE = "UCchaine"


def collecter(collecteur, etat):
    return asyncio.run(collecteur.collecter(etat))


# ── GitHub ────────────────────────────────────────────────────────


def commentaire_gh(id_, auteur, cree, texte="texte", modifie=None):
    return {"id": id_, "html_url": f"https://github.com/c/{id_}", "user": {"login": auteur},
            "created_at": cree, "updated_at": modifie or cree, "body": texte}


class FauxGithub:
    def __init__(self):
        self.issues = {}            # "owner/depot#n" → (info, commentaires)
        self.discussions = {}       # "owner/depot#n" → discussion GraphQL
        self.trouvees = {"issue": [], "pr": []}
        self.notifications = []
        self.statut_notifications = 200
        self.appels = []

    def ajouter_issue(self, cle, auteur="autre", etat="open", commentaires=()):
        depot, num = cle.rsplit("#", 1)
        info = {"title": f"Titre {cle}", "state": etat, "user": {"login": auteur},
                "created_at": "2026-10-01T08:00:00Z"}
        self.issues[cle] = (info, list(commentaires))
        self.trouvees["issue"].append({"repository_url": f"https://api.github.com/repos/{depot}", "number": int(num)})

    def __call__(self, methode, url, params, corps, jeton):
        assert jeton == "jeton-test"
        chemin = url.removeprefix("https://api.github.com")
        self.appels.append(chemin)
        if chemin == "/graphql":
            v = corps["variables"]
            if "search(" in corps["query"]:
                return 200, {"data": {"search": {"nodes": []}}}
            return 200, {"data": {"repository": {"discussion": self.discussions.get(f"{v['o']}/{v['r']}#{v['n']}")}}}
        if chemin == "/search/issues":
            genre = "pr" if "is:pr" in params["q"] else "issue"
            return 200, {"items": self.trouvees[genre] if params["q"].startswith("commenter:") else []}
        if chemin == "/notifications":
            if self.statut_notifications != 200:
                return self.statut_notifications, {"message": "Resource not accessible by personal access token"}
            return 200, self.notifications if params["page"] == 1 else []
        m = re.fullmatch(r"/repos/(.+)/issues/(\d+)(/comments)?", chemin)
        if m:
            info, commentaires = self.issues[f"{m[1]}#{m[2]}"]
            return 200, (commentaires if params.get("page", 1) == 1 else []) if m[3] else info
        raise AssertionError(f"appel inattendu : {methode} {chemin}")


def collecteur_gh(faux, discussions=(), env=ENV_GH, horloge=MAINTENANT):
    return CollecteurGithub("Axellum", discussions, jours=60, requete=faux, horloge=lambda: horloge, env=env)


def test_github_premier_passage_puis_reponse_alertee_une_fois():
    faux = FauxGithub()
    faux.ajouter_issue("esphome/esphome#21010", commentaires=[
        commentaire_gh(1, "quelquun", "2026-10-02T08:00:00Z"),          # avant Axel : ignoré
        commentaire_gh(2, "Axellum", "2026-10-03T08:00:00Z"),
        commentaire_gh(3, "mainteneur", "2026-10-04T08:00:00Z"),
        commentaire_gh(4, "github-actions[bot]", "2026-10-05T08:00:00Z"),
    ])
    c = collecteur_gh(faux)
    r1 = collecter(c, {})
    assert r1.premier_passage and r1.elements == [] and r1.nb_marques_vus == 1 and not r1.erreurs

    faux.issues["esphome/esphome#21010"][1].append(commentaire_gh(5, "mainteneur", "2026-10-31T09:00:00Z",
                                                                   "> citation\nMerci, corrigé dans la 2026.11"))
    r2 = collecter(c, r1.etat)
    assert len(r2.elements) == 1
    e = r2.elements[0]
    assert e.titre == "mainteneur a répondu — esphome/esphome#21010"
    assert e.message == "« Titre esphome/esphome#21010 » : Merci, corrigé dans la 2026.11"
    assert e.lien == "https://github.com/c/5" and e.niveau == Niveau.URGENT and e.source == "github"
    assert collecter(c, r2.etat).elements == []


def test_github_message_modifie_et_fil_ferme():
    faux = FauxGithub()
    faux.ajouter_issue("o/d#7", commentaires=[commentaire_gh(1, "Axellum", "2026-10-03T08:00:00Z"),
                                               commentaire_gh(2, "autre", "2026-10-04T08:00:00Z")])
    c = collecteur_gh(faux)
    etat = collecter(c, {}).etat
    info, commentaires = faux.issues["o/d#7"]
    commentaires[1] = commentaire_gh(2, "autre", "2026-10-04T08:00:00Z", "nouveau texte", modifie="2026-10-31T10:00:00Z")
    info["state"] = "closed"
    titres = sorted(e.titre for e in collecter(c, etat).elements)
    assert titres == ["Fil fermé — o/d#7", "autre a modifié son message — o/d#7"]


def test_github_discussion_configuree_suivie_en_entier():
    faux = FauxGithub()

    def msg(id_, auteur, cree):
        return {"id": id_, "url": f"https://github.com/d/{id_}", "author": {"login": auteur},
                "createdAt": cree, "lastEditedAt": None, "body": f"message {id_}"}

    discussion = {"title": "Retours", "closed": False, "createdAt": "2026-09-01T00:00:00Z",
                  "author": {"login": "Axellum"},
                  "comments": {"nodes": [{**msg("D1", "visiteur", "2026-09-02T00:00:00Z"), "replies": {"nodes": []}}]}}
    faux.discussions["Axellum/Tab5#278"] = discussion
    cles = parser_discussions("Axellum/Tab5#278, invalide, a/b#x")
    assert cles == ("discussion:Axellum/Tab5#278",)
    c = collecteur_gh(faux, cles)
    etat = collecter(c, {}).etat
    discussion["comments"]["nodes"][0]["replies"]["nodes"] += [msg("D2", "Axellum", "2026-10-30T00:00:00Z"),
                                                               msg("D3", "visiteur", "2026-10-31T00:00:00Z")]
    elements = collecter(c, etat).elements
    assert [e.titre for e in elements] == ["visiteur a répondu — Axellum/Tab5#278"]


def test_github_fil_decouvert_tard_alerte_les_reponses_recentes_seulement():
    faux = FauxGithub()
    c = collecteur_gh(faux)
    etat = collecter(c, {}).etat      # rien de suivi, mais la source est amorcée
    faux.ajouter_issue("o/d#9", commentaires=[
        commentaire_gh(1, "Axellum", "2026-10-01T08:00:00Z"),
        commentaire_gh(2, "ancien", "2026-10-02T08:00:00Z"),          # bien avant le passage précédent
        commentaire_gh(3, "recent", "2026-11-01T11:00:00Z"),          # arrivé avant que la recherche ne trouve le fil
    ])
    r = collecter(c, etat)
    assert [e.titre for e in r.elements] == ["recent a répondu — o/d#9"]


def test_github_sans_jeton_ou_notifications_refusees():
    faux = FauxGithub()
    with pytest.raises(ErreurGithub, match="aucun jeton"):
        collecter(collecteur_gh(faux, env={}), {})
    faux.statut_notifications = 403
    faux.ajouter_issue("o/d#1", commentaires=[commentaire_gh(1, "Axellum", "2026-10-01T08:00:00Z")])
    r = collecter(collecteur_gh(faux), {})
    assert len(r.erreurs) == 1 and r.erreurs[0].startswith("notifications : HTTP 403")
    assert "o/d#1" in str(r.etat["fils"])          # la panne partielle n'empêche pas le reste


# ── YouTube ───────────────────────────────────────────────────────


def commentaire_yt(id_, auteur, date, texte="super", chaine=None):
    return {"id": id_, "snippet": {"authorDisplayName": auteur, "authorChannelId": {"value": chaine or f"UC{auteur}"},
                                   "textDisplay": texte, "publishedAt": date}}


def fil_yt(id_, video, haut, reponses=(), total=None):
    return {"id": id_, "snippet": {"videoId": video, "topLevelComment": haut,
                                   "totalReplyCount": len(reponses) if total is None else total},
            "replies": {"comments": list(reponses)}}


class FauxYoutube:
    def __init__(self):
        self.fils = []
        self.reponses = {}
        self.statut = 200
        self.appels = []

    def __call__(self, url, params):
        assert params["key"] == "cle-test"
        ressource = url.rsplit("/", 1)[1]
        self.appels.append(ressource)
        if self.statut != 200:
            return self.statut, {"error": {"errors": [{"reason": "quotaExceeded"}]}}
        if ressource == "commentThreads":
            assert params["allThreadsRelatedToChannelId"] == CHAINE
            return 200, {"items": self.fils}
        if ressource == "comments":
            return 200, {"items": self.reponses[params["parentId"]]}
        if ressource == "videos":
            return 200, {"items": [{"id": i, "snippet": {"title": f"Vidéo {i}"}} for i in params["id"].split(",")]}
        raise AssertionError(ressource)


def test_youtube_premier_passage_puis_commentaire_et_reponse():
    faux = FauxYoutube()
    c = CollecteurYoutube(CHAINE, http_get=faux, env=ENV_YT)
    haut = commentaire_yt("C1", "Lucie", "2026-10-01T10:00:00Z", "Ça marche avec la 7B ?")
    axel = commentaire_yt("C1.a", "Axel", "2026-10-02T10:00:00Z", "Oui", chaine=CHAINE)
    faux.fils = [fil_yt("C1", "vid1", haut, [axel])]
    r1 = collecter(c, {})
    assert r1.premier_passage and r1.elements == [] and r1.nb_marques_vus == 1

    faux.fils = [fil_yt("C1", "vid1", haut, [axel, commentaire_yt("C1.b", "Lucie", "2026-11-01T09:00:00Z", "Merci !")]),
                 fil_yt("C2", "vid2", commentaire_yt("C2", "Marc", "2026-11-01T10:00:00Z", "Top"))]
    r2 = collecter(c, r1.etat)
    titres = sorted(e.titre for e in r2.elements)
    assert titres == ["Lucie te répond — « Vidéo vid1 »", "Marc a commenté — « Vidéo vid2 »"]
    lien = next(e.lien for e in r2.elements if e.ident == "C2")
    assert lien == "https://www.youtube.com/watch?v=vid2&lc=C2"
    assert collecter(c, r2.etat).elements == []
    assert faux.appels.count("videos") == 1          # titres gardés dans l'état


def test_youtube_reponses_tronquees_relues_si_leur_nombre_change():
    faux = FauxYoutube()
    c = CollecteurYoutube(CHAINE, http_get=faux, env=ENV_YT)
    haut = commentaire_yt("C1", "Lucie", "2026-10-01T10:00:00Z")
    toutes = [commentaire_yt(f"C1.{i}", "Paul", f"2026-10-0{i + 2}T10:00:00Z") for i in range(6)]
    faux.reponses["C1"] = toutes
    faux.fils = [fil_yt("C1", "vid1", haut, toutes[:2], total=6)]
    etat = collecter(c, {}).etat
    assert faux.appels.count("comments") == 1
    assert collecter(c, etat).elements == [] and faux.appels.count("comments") == 1
    toutes.append(commentaire_yt("C1.9", "Paul", "2026-11-01T10:00:00Z", "encore une question"))
    faux.fils = [fil_yt("C1", "vid1", haut, toutes[:2], total=7)]
    r = collecter(c, etat)
    assert [e.ident for e in r.elements] == ["C1.9"] and faux.appels.count("comments") == 2


def test_youtube_sans_cle_ou_quota_depasse_sans_fuite_de_cle():
    faux = FauxYoutube()
    with pytest.raises(ErreurYoutube, match="aucune clé"):
        collecter(CollecteurYoutube(CHAINE, http_get=faux, env={}), {})
    faux.statut = 403
    with pytest.raises(ErreurYoutube) as exc:
        collecter(CollecteurYoutube(CHAINE, http_get=faux, env=ENV_YT), {})
    assert "quotaExceeded" in str(exc.value) and "cle-test" not in str(exc.value)


def test_reglages_ajoutent_github_et_youtube():
    env = {"MOTEUR_VIGIE_GMAIL": "0", "MOTEUR_VIGIE_GITHUB_PSEUDO": "Axellum",
           "MOTEUR_VIGIE_GITHUB_DISCUSSIONS": "Axellum/M5-Tab5-ESPHome-LVGL#278",
           "MOTEUR_VIGIE_YOUTUBE_CHAINE": "UCmMnfENr9qX3K3oTaCP0SUg"}
    noms = [c.nom for c in construire_collecteurs(lire_reglages(env))]
    assert noms == ["github", "youtube"]
    assert lire_reglages({"MOTEUR_VIGIE_GITHUB_PSEUDO": "Axel lum/../x"}).github_pseudo == ""
    assert [c.nom for c in construire_collecteurs(lire_reglages({"MOTEUR_VIGIE_GMAIL": "0"}))] == []
