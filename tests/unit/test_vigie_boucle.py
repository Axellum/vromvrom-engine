"""
tests/unit/test_vigie_boucle.py — Passages de la vigie (#T480), sans réseau.

Faux Gmail, faux forum Discourse, faux Home Assistant, horloge fixée. Points
prouvés : premier passage sans alerte, nouveauté alertée une seule fois,
regroupement, silence de nuit, résumé du matin, HA injoignable (alerte gardée),
source en panne → une seule alerte, forum « te répond », état concurrent sous
filelock, passage à blanc sans effet.
"""

import asyncio
import copy
import threading
import time
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlsplit

import pytest

from core.vigie.boucle import Vigie
from core.vigie.etat import charger_etat, verrou_passe
from core.vigie.modeles import Element, Niveau
from core.vigie.notification import Notifieur
from core.vigie.reglages import en_silence, lire_reglages, parser_silence
from core.vigie.sources.base import Collecteur, ResultatCollecte
from core.vigie.sources.forum import CollecteurForum
from core.vigie.sources.gmail import CollecteurGmail
from core.vigie.tri_gmail import TrieurMails

PARIS = timezone(timedelta(hours=2))
REGLAGES = {"MOTEUR_VIGIE_ENABLED": "1", "MOTEUR_VIGIE_NOTIFY_SERVICE": "notify.mobile_app_test"}


class Horloge:
    def __init__(self, *args):
        self.t = datetime(*args, tzinfo=PARIS)

    def __call__(self):
        return self.t


class FauxHA:
    def __init__(self):
        self.statut = 200
        self.telephone = []
        self.persistantes = []

    async def post(self, chemin, payload):
        self.telephone.append((chemin, payload))
        return self.statut

    async def persistante(self, titre, message, notification_id):
        self.persistantes.append((titre, message, notification_id))
        return True


class FauxGmail:
    """Remplace GCPOAuthClient : uniquement des lectures, comme le vrai jeton gmail.readonly."""

    def __init__(self):
        self.mails = {}
        self.urls = []
        self.panne = False

    def ajouter(self, ident, labels=("CATEGORY_PERSONAL", "INBOX", "IMPORTANT"), objet="Objet",
                de="Alice <a@example.com>", desabo=False):
        entetes = [{"name": "From", "value": de}, {"name": "Subject", "value": objet},
                   {"name": "Received", "value": "from mx.example (10.0.0.1)"}]
        if desabo:
            entetes.append({"name": "List-Unsubscribe", "value": "<https://example.com/desabo?u=1>"})
        self.mails[ident] = {
            "id": ident, "threadId": f"18f{len(self.mails):x}", "labelIds": list(labels),
            "snippet": "Bonjour, voici l&#39;extrait", "internalDate": "1791360000000",
            "payload": {"headers": entetes},
        }

    def get_json_strict(self, url):
        self.urls.append(url)
        if self.panne:
            raise RuntimeError("API Google HTTP 401")
        if "/messages?" in url:
            return {"messages": [{"id": i, "threadId": m["threadId"]} for i, m in reversed(list(self.mails.items()))]}
        ident = url.split("/messages/")[1].split("?")[0]
        if ident not in self.mails:
            raise FileNotFoundError("HTTP 404")
        # Comme l'API : format=metadata ne rend QUE les en-têtes demandés.
        demandes = {v.lower() for k, v in parse_qsl(urlsplit(url).query) if k == "metadataHeaders"}
        meta = copy.deepcopy(self.mails[ident])
        meta["payload"]["headers"] = [h for h in meta["payload"]["headers"] if h["name"].lower() in demandes]
        return meta


def fabriquer(tmp_path, collecteurs, horloge, ha=None, env=None, a_blanc=False, trieur=None):
    reglages = lire_reglages({**REGLAGES, **(env or {})})
    ha = ha or FauxHA()
    notifieur = Notifieur(reglages.notify_service, a_blanc=a_blanc, post=ha.post, persistante=ha.persistante)
    vigie = Vigie(reglages, a_blanc=a_blanc, collecteurs=collecteurs, horloge=horloge,
                  trieur=trieur or TrieurMails(llm_actif=False), notifieur=notifieur,
                  chemin_etat=str(tmp_path / "vigie_state.json"))
    return vigie, ha


def passe(vigie, **kw):
    return asyncio.run(vigie.passe(**kw))


@pytest.fixture
def gmail():
    client = FauxGmail()
    for i in range(5):
        client.ajouter(f"ancien{i}")
    return client


# ── premier passage, nouveauté, regroupement ─────────────────────────

def test_premier_passage_tout_marque_vu_aucune_alerte(tmp_path, gmail):
    vigie, ha = fabriquer(tmp_path, [CollecteurGmail("in:inbox", lambda: gmail)], Horloge(2026, 10, 7, 10))
    rapport = passe(vigie)
    assert ha.telephone == []
    assert rapport.sources[0].premier_passage and rapport.sources[0].nb_marques_vus == 5
    assert not any("format=metadata" in u for u in gmail.urls)   # rien n'est lu au premier passage
    etat = charger_etat(str(tmp_path / "vigie_state.json"))
    assert len(etat["sources"]["gmail"]["vus"]) == 5


def test_nouveaute_alertee_une_seule_fois(tmp_path, gmail):
    vigie, ha = fabriquer(tmp_path, [CollecteurGmail("in:inbox", lambda: gmail)], Horloge(2026, 10, 7, 10))
    passe(vigie)
    gmail.ajouter("nouveau", objet="Paiement refusé")
    gmail.urls.clear()
    passe(vigie)
    assert len(ha.telephone) == 1
    chemin, payload = ha.telephone[0]
    assert chemin == "/api/services/notify/mobile_app_test"
    assert payload["title"] == "Alice — Paiement refusé"          # en-têtes lus
    assert payload["data"]["clickAction"].startswith("https://mail.google.com/mail/u/0/#all/")
    assert payload["data"]["priority"] == "high" and payload["data"]["ttl"] == 0
    assert payload["data"]["tag"].startswith("vigie-gmail")
    assert sum("format=metadata" in u for u in gmail.urls) == 1    # seul le nouveau est lu
    passe(vigie)
    passe(vigie)
    assert len(ha.telephone) == 1


def test_a_lire_et_bruit_n_alertent_pas(tmp_path, gmail):
    vigie, ha = fabriquer(tmp_path, [CollecteurGmail("in:inbox", lambda: gmail)], Horloge(2026, 10, 7, 10))
    passe(vigie)
    gmail.ajouter("perso", labels=("CATEGORY_PERSONAL",))
    gmail.ajouter("promo", labels=("CATEGORY_PROMOTIONS",))
    gmail.ajouter("promo_imp", labels=("CATEGORY_PROMOTIONS", "IMPORTANT"))
    passe(vigie)
    assert ha.telephone == []
    etat = charger_etat(str(tmp_path / "vigie_state.json"))
    assert [i["cle"] for i in etat["resume"]["a_lire"]] == ["gmail:perso", "gmail:promo_imp"]
    assert etat["resume"]["bruit"] == {"gmail": 1}


def test_mises_a_jour_list_unsubscribe_demande_et_lu(tmp_path, gmail):
    """Boîte réelle du 07/10 : tout tombe en « Mises à jour ». L'en-tête sépare masse et transactionnel."""
    vigie, ha = fabriquer(tmp_path, [CollecteurGmail("in:inbox", lambda: gmail)], Horloge(2026, 10, 7, 10))
    passe(vigie)
    gmail.ajouter("facture", labels=("CATEGORY_UPDATES", "INBOX"), de="Steeple <no-reply@example.com>")
    gmail.ajouter("lettre", labels=("CATEGORY_UPDATES", "INBOX"), de="Promo <n@newsletter.promo.example>", desabo=True)
    gmail.ajouter("alerte", labels=("CATEGORY_UPDATES", "INBOX", "IMPORTANT"), de="Hackster <h@hackster.io>")
    rapport = passe(vigie)
    lus = [u for u in gmail.urls if "format=metadata" in u]
    assert lus and all("metadataHeaders=List-Unsubscribe" in u for u in lus)
    par_id = {e.ident: e for e in rapport.elements}
    assert par_id["lettre"].desabonnement and not par_id["facture"].desabonnement
    assert [p["data"]["tag"] for _, p in ha.telephone] == ["vigie-gmail_alerte"]   # IMPORTANT monte d'un cran
    etat = charger_etat(str(tmp_path / "vigie_state.json"))
    assert [i["cle"] for i in etat["resume"]["a_lire"]] == ["gmail:facture"]
    assert etat["resume"]["bruit"] == {"gmail": 1}


def test_preferences_d_expediteur_par_les_reglages(tmp_path, gmail):
    env = {**REGLAGES, "MOTEUR_VIGIE_LLM": "0", "MOTEUR_VIGIE_GMAIL_ALERTER": "example.org",
           "MOTEUR_VIGIE_GMAIL_IGNORER": "example.com"}
    ha = FauxHA()
    reglages = lire_reglages(env)
    vigie = Vigie(reglages, collecteurs=[CollecteurGmail("in:inbox", lambda: gmail)],
                  horloge=Horloge(2026, 10, 7, 10), chemin_etat=str(tmp_path / "vigie_state.json"),
                  notifieur=Notifieur(reglages.notify_service, post=ha.post, persistante=ha.persistante))
    passe(vigie)
    gmail.ajouter("rh", labels=("CATEGORY_UPDATES",), de="RH <paie@mail.example.org>", desabo=True)
    gmail.ajouter("ignore", labels=("CATEGORY_PERSONAL", "IMPORTANT"), de="Alice <a@example.com>")
    rapport = passe(vigie)
    assert [p["data"]["tag"] for _, p in ha.telephone] == ["vigie-gmail_rh"]
    assert {e.ident: e.methode_tri for e in rapport.elements} == {"rh": "pref:alerter", "ignore": "pref:ignorer"}


def test_plus_de_trois_alertes_une_seule_notification(tmp_path, gmail):
    vigie, ha = fabriquer(tmp_path, [CollecteurGmail("in:inbox", lambda: gmail)], Horloge(2026, 10, 7, 10))
    passe(vigie)
    for i in range(5):
        gmail.ajouter(f"u{i}")
    passe(vigie)
    assert len(ha.telephone) == 1
    payload = ha.telephone[0][1]
    assert payload["title"] == "Vigie : 5 alertes"
    assert payload["data"]["tag"] == "vigie-groupe-20261007-1000"
    assert payload["message"].count("• ") == 5


def test_deux_groupes_successifs_ne_se_remplacent_pas(tmp_path, gmail):
    horloge = Horloge(2026, 10, 7, 10)
    vigie, ha = fabriquer(tmp_path, [CollecteurGmail("in:inbox", lambda: gmail)], horloge)
    passe(vigie)
    for lot in ("a", "b"):
        for i in range(4):
            gmail.ajouter(f"{lot}{i}")
        horloge.t += timedelta(minutes=10)
        passe(vigie)
    tags = [p["data"]["tag"] for _, p in ha.telephone]
    assert len(tags) == 2 and len(set(tags)) == 2   # même tag = le second effacerait le premier


def test_trois_alertes_trois_notifications(tmp_path, gmail):
    vigie, ha = fabriquer(tmp_path, [CollecteurGmail("in:inbox", lambda: gmail)], Horloge(2026, 10, 7, 10))
    passe(vigie)
    for i in range(3):
        gmail.ajouter(f"u{i}")
    passe(vigie)
    assert len(ha.telephone) == 3
    assert len({p["data"]["tag"] for _, p in ha.telephone}) == 3


# ── silence de nuit ───────────────────────────────────────────────────

def test_silence_de_nuit_alertes_gardees_puis_groupees(tmp_path, gmail):
    horloge = Horloge(2026, 10, 7, 21, 0)
    vigie, ha = fabriquer(tmp_path, [CollecteurGmail("in:inbox", lambda: gmail)], horloge)
    passe(vigie)
    horloge.t = horloge.t.replace(hour=23)
    gmail.ajouter("nuit1")
    passe(vigie)
    horloge.t = datetime(2026, 10, 8, 3, 0, tzinfo=PARIS)
    gmail.ajouter("nuit2")
    passe(vigie)
    assert ha.telephone == []
    assert len(charger_etat(str(tmp_path / "vigie_state.json"))["en_attente"]) == 2
    horloge.t = horloge.t.replace(hour=7, minute=5)
    passe(vigie)
    assert len(ha.telephone) == 1
    assert ha.telephone[0][1]["title"] == "Vigie : 2 alertes de la nuit"
    assert charger_etat(str(tmp_path / "vigie_state.json"))["en_attente"] == []


def test_une_seule_alerte_de_nuit_part_seule(tmp_path, gmail):
    horloge = Horloge(2026, 10, 7, 21, 0)
    vigie, ha = fabriquer(tmp_path, [CollecteurGmail("in:inbox", lambda: gmail)], horloge)
    passe(vigie)
    horloge.t = horloge.t.replace(hour=22, minute=30)
    gmail.ajouter("nuit1", objet="Colis")
    passe(vigie)
    horloge.t = datetime(2026, 10, 8, 7, 0, tzinfo=PARIS)
    passe(vigie)
    assert [p["title"] for _, p in ha.telephone] == ["Alice — Colis"]


def test_plage_de_silence():
    assert parser_silence("22-7") == (22, 7)
    assert parser_silence("0") is None and parser_silence("") is None and parser_silence("x-y") is None
    assert [h for h in range(24) if en_silence(h, (22, 7))] == [0, 1, 2, 3, 4, 5, 6, 22, 23]
    assert [h for h in range(24) if en_silence(h, (1, 3))] == [1, 2]


# ── résumé du matin ───────────────────────────────────────────────────

def test_resume_du_matin_une_fois_par_jour(tmp_path, gmail):
    horloge = Horloge(2026, 10, 7, 10, 0)
    vigie, ha = fabriquer(tmp_path, [CollecteurGmail("in:inbox", lambda: gmail)], horloge)
    passe(vigie)                                   # 10 h, rien à résumer : pas de résumé
    horloge.t = horloge.t.replace(hour=15)
    gmail.ajouter("perso", labels=("CATEGORY_PERSONAL",), objet="Photos")
    gmail.ajouter("promo1", labels=("CATEGORY_PROMOTIONS",))
    gmail.ajouter("promo2", labels=("CATEGORY_SOCIAL",))
    passe(vigie)
    horloge.t = datetime(2026, 10, 8, 7, 55, tzinfo=PARIS)
    passe(vigie)
    assert ha.telephone == [] and ha.persistantes == []
    horloge.t = horloge.t.replace(hour=8, minute=5)
    passe(vigie)
    assert len(ha.telephone) == 1
    payload = ha.telephone[0][1]
    assert payload["title"] == "Vigie : résumé du 08/10"
    assert payload["data"]["tag"] == "vigie-resume"
    assert "Gmail : 🟡 1 à lire, ⚪ 2 sans intérêt" in payload["message"]
    assert "• Alice — Photos" in payload["message"]
    assert ha.persistantes[0][2] == "moteur_vigie_resume"
    horloge.t = horloge.t.replace(hour=8, minute=15)
    passe(vigie)
    assert len(ha.telephone) == 1                  # pas deux résumés le même jour
    horloge.t = datetime(2026, 10, 9, 8, 5, tzinfo=PARIS)
    passe(vigie)
    assert len(ha.telephone) == 1                  # rien depuis : pas de résumé


# ── Home Assistant injoignable, source en panne ───────────────────────

def test_ha_injoignable_l_alerte_attend_le_passage_suivant(tmp_path, gmail):
    vigie, ha = fabriquer(tmp_path, [CollecteurGmail("in:inbox", lambda: gmail)], Horloge(2026, 10, 7, 10))
    passe(vigie)
    gmail.ajouter("u1")
    ha.statut = 0
    rapport = passe(vigie)
    assert rapport.en_attente == 1
    ha.statut = 200
    passe(vigie)
    passe(vigie)
    assert [p["title"] for _, p in ha.telephone] == ["Alice — Objet", "Alice — Objet"]  # échec puis succès
    assert charger_etat(str(tmp_path / "vigie_state.json"))["en_attente"] == []


class CollecteurStable(Collecteur):
    """Source saine qui apporte un message urgent à chaque passage."""

    nom = "forum:sain.example"

    def __init__(self):
        self.n = 0

    async def collecter(self, etat_source, *, essai_tri=0):
        self.n += 1
        e = Element(source=self.nom, ident=str(self.n), titre=f"bob sur « fil » #{self.n}",
                    message="x", niveau=Niveau.URGENT, methode_tri="forum")
        return ResultatCollecte([e], {"initialise": True})


def test_source_en_panne_une_seule_alerte_et_les_autres_continuent(tmp_path, gmail):
    gmail.panne = True
    sain = CollecteurStable()
    vigie, ha = fabriquer(tmp_path, [CollecteurGmail("in:inbox", lambda: gmail), sain],
                          Horloge(2026, 10, 7, 10), env={"MOTEUR_VIGIE_PANNE_SEUIL": "3"})
    for _ in range(6):
        passe(vigie)
    titres = [p["title"] for _, p in ha.telephone]
    assert titres.count("Vigie : Gmail en panne") == 1
    assert sum(t.startswith("bob sur") for t in titres) == 6        # la source saine n'est pas bloquée
    etat = charger_etat(str(tmp_path / "vigie_state.json"))
    assert etat["sources"]["gmail"]["echecs"] == 6 and etat["sources"]["gmail"]["panne_signalee"]
    gmail.panne = False                                              # rétablie
    passe(vigie)
    assert charger_etat(str(tmp_path / "vigie_state.json"))["sources"]["gmail"]["echecs"] == 0
    gmail.panne = True                                               # nouvelle panne : nouvelle alerte
    for _ in range(3):
        passe(vigie)
    assert [p["title"] for _, p in ha.telephone].count("Vigie : Gmail en panne") == 2


# ── état concurrent, état illisible, passage à blanc ──────────────────

def test_passage_saute_si_un_autre_tient_le_verrou(tmp_path, gmail):
    vigie, ha = fabriquer(tmp_path, [CollecteurGmail("in:inbox", lambda: gmail)], Horloge(2026, 10, 7, 10))
    passe(vigie)
    gmail.ajouter("u1")
    with verrou_passe(str(tmp_path / "vigie_state.json")) as obtenu:
        assert obtenu
        rapport = passe(vigie)
    assert rapport.saute and ha.telephone == []
    passe(vigie)
    assert len(ha.telephone) == 1


class GmailLent(FauxGmail):
    def get_json_strict(self, url):
        time.sleep(0.4)
        return super().get_json_strict(url)


def test_deux_passages_simultanes_une_seule_alerte(tmp_path):
    client = GmailLent()
    client.ajouter("a0")
    ha = FauxHA()
    horloge = Horloge(2026, 10, 7, 10)
    v1, _ = fabriquer(tmp_path, [CollecteurGmail("in:inbox", lambda: client)], horloge, ha=ha)
    v2, _ = fabriquer(tmp_path, [CollecteurGmail("in:inbox", lambda: client)], horloge, ha=ha)
    passe(v1)
    client.ajouter("nouveau")
    rapports = []
    fils = [threading.Thread(target=lambda v=v: rapports.append(passe(v))) for v in (v1, v2)]
    fils[0].start()
    time.sleep(0.1)
    fils[1].start()
    for f in fils:
        f.join(10)
    assert sorted(r.saute for r in rapports) == [False, True]
    assert len(ha.telephone) == 1
    assert "nouveau" in charger_etat(str(tmp_path / "vigie_state.json"))["sources"]["gmail"]["vus"]


def test_etat_illisible_repart_sans_avalanche(tmp_path, gmail):
    (tmp_path / "vigie_state.json").write_text("{ pas du json", encoding="utf-8")
    vigie, ha = fabriquer(tmp_path, [CollecteurGmail("in:inbox", lambda: gmail)], Horloge(2026, 10, 7, 10))
    passe(vigie)
    assert ha.telephone == []


def test_passage_a_blanc_n_envoie_rien_et_n_ecrit_rien(tmp_path, gmail):
    vigie, ha = fabriquer(tmp_path, [CollecteurGmail("in:inbox", lambda: gmail)], Horloge(2026, 10, 7, 10),
                          a_blanc=True)
    rapport = passe(vigie, essai_tri=3)
    assert list(tmp_path.iterdir()) == []          # ni état, ni fichier de verrou
    assert ha.telephone == [] and ha.persistantes == []
    assert len(rapport.elements) == 3
    assert len(rapport.envois) == 3                                    # ce qui SERAIT parti
    assert all(e.niveau is Niveau.URGENT for e in rapport.elements)    # Principale + IMPORTANT


def test_desactivee_par_defaut():
    assert lire_reglages({}).actif is False
    assert lire_reglages({"MOTEUR_VIGIE_ENABLED": "1"}).actif is True


# ── forum Discourse ───────────────────────────────────────────────────

BASE = "https://forum.example"
T0 = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)


class FauxDiscourse:
    def __init__(self):
        self.fils = {}            # id -> {"title", "posts": [...]}
        self.actions = []         # user_actions de Axellum
        self.statuts = {}         # chemin -> code HTTP forcé
        self.prochain_id = 1000

    def poster(self, fil, username, quand, **champs):
        posts = self.fils.setdefault(fil, {"title": f"Fil {fil}", "posts": []})["posts"]
        self.prochain_id += 1
        post = {"id": self.prochain_id, "post_number": len(posts) + 1, "username": username,
                "created_at": quand.isoformat().replace("+00:00", "Z"), "cooked": "<p>Bonjour</p>",
                "post_type": 1, **champs}
        posts.append(post)
        return post

    def __call__(self, url, params):
        chemin = url[len(BASE):]
        if chemin in self.statuts:
            return self.statuts[chemin], None
        if chemin == "/user_actions.json":
            offset = dict(params).get("offset", 0)
            return 200, {"user_actions": self.actions if offset == 0 else []}
        morceaux = chemin.strip("/").split("/")
        fil = int(morceaux[1].removesuffix(".json"))
        if fil not in self.fils:
            return 404, None
        posts = self.fils[fil]["posts"]
        if chemin.endswith("/posts.json"):
            voulus = {v for k, v in params if k == "post_ids[]"}
            return 200, {"post_stream": {"posts": [p for p in posts if p["id"] in voulus]}}
        return 200, {"title": self.fils[fil]["title"],
                     "post_stream": {"stream": [p["id"] for p in posts], "posts": posts[:20]}}


def collecteur_forum(faux, horloge, fils=(888543,), jours=90):
    return CollecteurForum(BASE, "Axellum", list(fils), jours_decouverte=jours, http_get=faux,
                           horloge=horloge, pause_s=0)


def test_forum_premier_passage_puis_reponse_a_axel(tmp_path):
    faux = FauxDiscourse()
    faux.poster(888543, "Axellum", T0)
    faux.poster(888543, "carol", T0)
    horloge_utc = Horloge(2026, 10, 7, 10)
    vigie, ha = fabriquer(tmp_path, [collecteur_forum(faux, lambda: T0)], horloge_utc)
    passe(vigie)
    assert ha.telephone == []
    post = faux.poster(888543, "bob", T0, reply_to_user={"username": "axellum"}, reply_to_post_number=1)
    passe(vigie)
    passe(vigie)
    assert len(ha.telephone) == 1
    payload = ha.telephone[0][1]
    assert payload["title"] == "bob te répond — Fil 888543"
    assert payload["data"]["clickAction"] == f"{BASE}/t/888543/{post['post_number']}"
    assert payload["data"]["priority"] == "high"


def test_forum_mention_citation_et_filtrage():
    faux = FauxDiscourse()
    faux.poster(888543, "Axellum", T0)
    col = collecteur_forum(faux, lambda: T0, jours=0)
    etat = asyncio.run(col.collecter({})).etat
    faux.poster(888543, "dan", T0, cooked='<p>Merci <a class="mention" href="/u/axellum">@Axellum</a>.</p>')
    faux.poster(888543, "eve", T0, cooked='<aside class="quote no-group" data-username="Axellum" data-post="1">'
                                         '<blockquote>texte</blockquote></aside><p>Pareil ici</p>')
    faux.poster(888543, "fred", T0, cooked="<p>Écrit à @Axellumx et cite <aside class=\"quote\" "
                                           "data-username=\"zoe\"><p>@Axellum</p></aside></p>")
    faux.poster(888543, "Axellum", T0)                      # son propre message
    faux.poster(888543, "modo", T0, post_type=3)            # action de modération
    faux.poster(888543, "gus", T0, hidden=True)
    elements = asyncio.run(col.collecter(etat)).elements
    assert [e.titre for e in elements] == [
        "dan te répond — Fil 888543",
        "eve te répond — Fil 888543",
        "fred sur « Fil 888543 »",
    ]
    assert elements[1].message == "Pareil ici"              # la citation n'est pas dans l'extrait


def test_forum_fil_decouvert_ne_rate_pas_les_reponses_arrivees_avant(tmp_path):
    faux = FauxDiscourse()
    faux.poster(888543, "Axellum", T0)
    maintenant = {"t": T0}
    col = collecteur_forum(faux, lambda: maintenant["t"])
    etat = asyncio.run(col.collecter({})).etat                       # découverte à T0 : rien
    faux.poster(555, "carol", T0 - timedelta(days=1))                 # avant : déjà connu du monde
    faux.poster(555, "Axellum", T0 + timedelta(hours=1))
    faux.actions = [{"topic_id": 555, "created_at": (T0 + timedelta(hours=1)).isoformat()}]
    faux.poster(555, "bob", T0 + timedelta(hours=2))                  # réponse avant la découverte
    maintenant["t"] = T0 + timedelta(hours=3)
    res = asyncio.run(col.collecter(etat))                            # pas encore l'heure de redécouvrir
    assert res.elements == [] and "555" not in res.etat["fils"]
    maintenant["t"] = T0 + timedelta(hours=6, minutes=5)
    res = asyncio.run(col.collecter(res.etat))
    assert [e.titre for e in res.elements] == ["bob sur « Fil 555 »"]
    assert asyncio.run(col.collecter(res.etat)).elements == []        # une seule fois


def test_forum_fil_configure_introuvable_est_une_erreur(tmp_path):
    faux = FauxDiscourse()
    faux.poster(888543, "Axellum", T0)
    faux.actions = [{"topic_id": 777, "created_at": T0.isoformat()}]  # découvert mais devenu privé
    faux.statuts["/t/777.json"] = 403
    col = collecteur_forum(faux, lambda: T0, fils=(888543, 999))
    res = asyncio.run(col.collecter({}))
    assert res.erreurs == ["fil 999 introuvable (HTTP 404)"]


def test_forum_cloudflare_sur_la_decouverte_ne_bloque_pas_les_fils(tmp_path):
    faux = FauxDiscourse()
    faux.poster(888543, "Axellum", T0)
    faux.statuts["/user_actions.json"] = 403
    col = collecteur_forum(faux, lambda: T0)
    etat = asyncio.run(col.collecter({})).etat
    faux.poster(888543, "bob", T0)
    res = asyncio.run(col.collecter(etat))
    assert len(res.elements) == 1 and res.erreurs and "Axellum" in res.erreurs[0]
