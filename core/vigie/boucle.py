"""
core/vigie/boucle.py — Un passage de la vigie, et la boucle de fond du moteur.

Un passage :
  1. chaque collecteur lit SA source (une source en panne ne bloque pas les
     autres ; après `MOTEUR_VIGIE_PANNE_SEUIL` échecs de suite, UNE alerte
     « source X en panne », pas une par passage — O-3, ne jamais mentir) ;
  2. les mails passent au tri local (`tri_gmail.py`) ;
  3. 🔴 → file d'alertes ; 🟡 → résumé du matin ; ⚪ → compté pour le résumé ;
  4. hors silence de nuit, la file part sur le téléphone : une notification par
     alerte, ou UNE seule si elles sont plus de `MOTEUR_VIGIE_GROUPE_SEUIL` ou
     si elles ont attendu la fin de la nuit. Une alerte non livrée (HA
     injoignable) reste dans la file et repart au passage suivant ;
  5. à partir de `MOTEUR_VIGIE_RESUME_HEURE`, une fois par jour : le résumé
     (téléphone + notification persistante HA), seulement s'il y a quelque chose.

Activation : `MOTEUR_VIGIE_ENABLED=1` (défaut 0) ET `MOTEUR_VIGIE_NOTIFY_SERVICE`.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from core.vigie.etat import charger_etat, sauver_etat, verrou_passe
from core.vigie.modeles import Element, Niveau, nettoyer_texte
from core.vigie.notification import Notifieur
from core.vigie.reglages import Reglages, en_silence, lire_reglages
from core.vigie.sources.base import Collecteur, ResultatCollecte
from core.vigie.tri_gmail import BilanTri, TrieurMails

logger = logging.getLogger(__name__)

FUSEAU = "Europe/Paris"
DELAI_DEMARRAGE_S = 90        # laisse les autres boucles du lifespan s'installer
MAX_LIGNES_GROUPE = 8
MAX_A_LIRE_TELEPHONE = 5      # par source, dans le résumé envoyé au téléphone
ID_RESUME_HA = "moteur_vigie_resume"
LIEN_BOITE_GMAIL = "https://mail.google.com/mail/u/0/#inbox"

_fuseau_absent_signale = False


def heure_locale() -> datetime:
    """Heure de Paris ; à défaut de base de fuseaux, l'heure locale de l'hôte."""
    global _fuseau_absent_signale
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(FUSEAU))
    except Exception:
        if not _fuseau_absent_signale:
            _fuseau_absent_signale = True
            logger.warning("[VIGIE] Fuseau %s introuvable : heure locale de l'hôte utilisée.", FUSEAU)
        return datetime.now().astimezone()


def libelle_source(source: str) -> str:
    if source == "gmail":
        return "Gmail"
    if source.startswith("forum:"):
        return f"Forum {source[len('forum:'):]}"
    if source == "vigie":
        return "Vigie"
    return {"github": "GitHub", "youtube": "YouTube"}.get(source, source)


def tag_de(cle: str) -> str:
    return "vigie-" + re.sub(r"[^A-Za-z0-9_-]", "_", cle)[:80]


def alerte_panne(source: str, echecs: int, erreur: str, maintenant: datetime) -> Element:
    return Element(
        source="vigie",
        ident=f"panne-{source}-{maintenant:%Y%m%d%H%M}",
        titre=f"Vigie : {libelle_source(source)} en panne",
        message=nettoyer_texte(f"{echecs} relevés de suite en échec. Dernière erreur : {erreur}", 200),
        niveau=Niveau.URGENT,
        methode_tri="panne",
        date_iso=maintenant.isoformat(timespec="seconds"),
    )


def construire_collecteurs(reglages: Reglages) -> list[Collecteur]:
    from core.vigie.sources.forum import CollecteurForum
    from core.vigie.sources.github import CollecteurGithub
    from core.vigie.sources.gmail import CollecteurGmail
    from core.vigie.sources.youtube import CollecteurYoutube

    collecteurs: list[Collecteur] = []
    if reglages.gmail:
        collecteurs.append(CollecteurGmail(reglages.gmail_requete))
    groupes: dict[tuple[str, str], list[int]] = {}
    for suivi in reglages.forums:
        groupes.setdefault((suivi.base, suivi.pseudo), []).append(suivi.fil)
    noms = {c.nom for c in collecteurs}
    for (base, pseudo), fils in groupes.items():
        forum = CollecteurForum(base, pseudo, fils, jours_decouverte=reglages.forums_jours)
        if forum.nom in noms:   # même forum, autre pseudo : deux sources distinctes
            forum.nom = f"{forum.nom}/{pseudo}"
        noms.add(forum.nom)
        collecteurs.append(forum)
    if reglages.github_pseudo:
        collecteurs.append(CollecteurGithub(reglages.github_pseudo, reglages.github_discussions,
                                            jours=reglages.github_jours))
    if reglages.youtube_chaine:
        collecteurs.append(CollecteurYoutube(reglages.youtube_chaine))
    return collecteurs


@dataclass
class BilanSource:
    nom: str
    ok: bool
    premier_passage: bool
    nb_marques_vus: int
    erreurs: list[str]
    detail: str
    echecs_consecutifs: int


@dataclass
class RapportPasse:
    saute: bool = False
    a_blanc: bool = False
    silence: bool = False
    sources: list[BilanSource] = field(default_factory=list)
    elements: list[Element] = field(default_factory=list)
    tri: BilanTri | None = None
    envois: list[dict] = field(default_factory=list)
    en_attente: int = 0
    duree_s: float = 0.0


def texte_resume(resume: dict, max_par_source: int | None) -> str:
    """🟡 listés et ⚪ comptés, par source. `max_par_source=None` : tout lister."""
    a_lire: list[dict] = resume.get("a_lire", [])
    bruit: dict = resume.get("bruit", {})
    sources = list(dict.fromkeys([i["source"] for i in a_lire] + [s for s, n in bruit.items() if n]))
    lignes: list[str] = []
    for source in sources:
        items = [i for i in a_lire if i["source"] == source]
        lignes.append(f"{libelle_source(source)} : 🟡 {len(items)} à lire, ⚪ {bruit.get(source, 0)} sans intérêt")
        visibles = items if max_par_source is None else items[:max_par_source]
        for item in visibles:
            suite = f" : {item['message']}" if item.get("message") else ""
            lignes.append(f"• {item['titre']}{suite}")
        if len(items) > len(visibles):
            lignes.append(f"… et {len(items) - len(visibles)} autre(s) (détail dans Home Assistant)")
    if resume.get("a_lire_perdus"):
        lignes.append(f"(+{resume['a_lire_perdus']} plus ancien(s) non gardé(s))")
    return "\n".join(lignes)


class Vigie:
    def __init__(
        self,
        reglages: Reglages,
        *,
        a_blanc: bool = False,
        collecteurs: list[Collecteur] | None = None,
        trieur: TrieurMails | None = None,
        notifieur: Notifieur | None = None,
        horloge: Callable[[], datetime] | None = None,
        chemin_etat: str | None = None,
    ):
        self.reglages = reglages
        self.a_blanc = a_blanc
        self.collecteurs = collecteurs if collecteurs is not None else construire_collecteurs(reglages)
        self.trieur = trieur or TrieurMails(
            llm_actif=reglages.llm, modeles=reglages.llm_modeles, timeout_s=reglages.llm_timeout_s,
            ignorer=reglages.gmail_ignorer, alerter=reglages.gmail_alerter,
        )
        self.notifieur = notifieur or Notifieur(reglages.notify_service, a_blanc=a_blanc)
        self.horloge = horloge or heure_locale
        self.chemin_etat = chemin_etat

    async def passe(self, *, essai_tri: int = 0) -> RapportPasse:
        if self.a_blanc:
            # Lecture seule : ni verrou de passage, ni écriture de l'état, ni envoi.
            return await self._passe(essai_tri)
        with verrou_passe(self.chemin_etat) as obtenu:
            if not obtenu:
                logger.info("[VIGIE] Un autre passage est en cours : celui-ci est sauté.")
                return RapportPasse(saute=True)
            # `essai_tri` n'existe qu'à blanc : un état réel ne doit jamais en dépendre.
            return await self._passe(0)

    async def _passe(self, essai_tri: int) -> RapportPasse:
        debut = time.monotonic()
        maintenant = self.horloge()
        self.notifieur.journal.clear()
        etat = charger_etat(self.chemin_etat)
        rapport = RapportPasse(a_blanc=self.a_blanc)
        nouveaux: list[Element] = []

        # 1. Collecte, source par source.
        for collecteur in self.collecteurs:
            ancien = etat["sources"].get(collecteur.nom, {})
            try:
                resultat = await collecteur.collecter(copy.deepcopy(ancien), essai_tri=essai_tri)
            except Exception as exc:
                erreur = nettoyer_texte(str(exc), 200) or type(exc).__name__
                resultat = ResultatCollecte([], copy.deepcopy(ancien), erreurs=[erreur])
            etat_source = dict(resultat.etat)
            if resultat.erreurs:
                echecs = int(ancien.get("echecs", 0)) + 1
                deja_signalee = bool(ancien.get("panne_signalee"))
                etat_source.update(echecs=echecs, derniere_erreur=resultat.erreurs[0][:200],
                                   panne_signalee=deja_signalee)
                if echecs >= self.reglages.panne_seuil and not deja_signalee:
                    nouveaux.append(alerte_panne(collecteur.nom, echecs, resultat.erreurs[0], maintenant))
                    etat_source["panne_signalee"] = True
                logger.warning("[VIGIE] %s : échec %d de suite (%s).", collecteur.nom, echecs, resultat.erreurs[0])
            else:
                if ancien.get("panne_signalee"):
                    logger.info("[VIGIE] %s : source rétablie.", collecteur.nom)
                etat_source.update(echecs=0, derniere_erreur=None, panne_signalee=False)
            etat["sources"][collecteur.nom] = etat_source
            nouveaux.extend(resultat.elements)
            rapport.sources.append(BilanSource(
                nom=collecteur.nom, ok=not resultat.erreurs, premier_passage=resultat.premier_passage,
                nb_marques_vus=resultat.nb_marques_vus, erreurs=list(resultat.erreurs),
                detail=resultat.detail, echecs_consecutifs=int(etat_source.get("echecs", 0)),
            ))

        # 2. Tri local des mails.
        a_trier = [e for e in nouveaux if e.a_trier]
        if a_trier:
            rapport.tri = await self.trieur.trier(a_trier, session_id=f"vigie-{maintenant:%Y%m%d-%H%M}")

        # 3. Répartition.
        silence = en_silence(maintenant.hour, self.reglages.silence)
        rapport.silence = silence
        cles = {a.get("cle") for a in etat["en_attente"]}
        for element in nouveaux:
            if element.niveau == Niveau.URGENT:
                if element.cle not in cles:
                    etat["en_attente"].append(element.vers_attente(differe_nuit=silence))
                    cles.add(element.cle)
            elif element.niveau == Niveau.A_LIRE:
                etat["resume"]["a_lire"].append(element.vers_attente(differe_nuit=False))
            else:
                bruit = etat["resume"]["bruit"]
                bruit[element.source] = int(bruit.get(element.source, 0)) + 1
        rapport.elements = nouveaux

        # 4. Livraison des alertes, hors silence.
        if not silence and etat["en_attente"]:
            etat["en_attente"] = await self._livrer(etat["en_attente"], maintenant)

        # 5. Résumé quotidien.
        if self.reglages.resume_heure is not None and not silence:
            await self._resume_si_l_heure(etat, maintenant)

        etat["dernier_passage_iso"] = maintenant.isoformat(timespec="seconds")
        rapport.en_attente = len(etat["en_attente"])
        rapport.envois = list(self.notifieur.journal)
        if not self.a_blanc:
            sauver_etat(etat, self.chemin_etat)
        rapport.duree_s = time.monotonic() - debut
        return rapport

    async def _livrer(self, alertes: list[dict], maintenant: datetime) -> list[dict]:
        """Envoie la file ; rend ce qui n'a pas pu partir.

        Tags : un par alerte (un renvoi après échec remplace au lieu de doubler) et un
        par envoi groupé, horodaté — un tag fixe ferait REMPLACER le groupe précédent
        sur le téléphone, peut-être pas encore lu (doc compagnon, « Replacing »).
        """
        n = len(alertes)
        de_nuit = any(a.get("differe_nuit") for a in alertes)
        if n > self.reglages.groupe_seuil or (de_nuit and n > 1):
            lignes = [f"• {a['titre']}" for a in alertes[:MAX_LIGNES_GROUPE]]
            if n > MAX_LIGNES_GROUPE:
                lignes.append(f"… et {n - MAX_LIGNES_GROUPE} autre(s)")
            sources = {a.get("source") for a in alertes}
            lien = None
            if sources == {"gmail"}:
                lien = LIEN_BOITE_GMAIL
            elif len(sources) == 1:
                lien = alertes[0].get("lien")   # le plus ancien message d'un même forum
            ok = await self.notifieur.telephone(
                f"Vigie : {n} alertes" + (" de la nuit" if de_nuit else ""),
                "\n".join(lignes), tag=f"vigie-groupe-{maintenant:%Y%m%d-%H%M}", lien=lien, urgent=True,
            )
            return [] if ok else alertes
        restantes = []
        for alerte in alertes:
            ok = await self.notifieur.telephone(
                alerte["titre"], alerte.get("message", ""), tag=tag_de(alerte["cle"]),
                lien=alerte.get("lien"), urgent=True,
            )
            if not ok:
                restantes.append(alerte)
        return restantes

    async def _resume_si_l_heure(self, etat: dict, maintenant: datetime) -> None:
        jour = maintenant.date().isoformat()
        if maintenant.hour < self.reglages.resume_heure or etat.get("dernier_resume_jour") == jour:
            return
        resume = etat["resume"]
        if not (resume["a_lire"] or any(resume["bruit"].values()) or resume.get("a_lire_perdus")):
            etat["dernier_resume_jour"] = jour   # rien à dire : pas de résumé
            return
        titre = f"Vigie : résumé du {maintenant:%d/%m}"
        ok = await self.notifieur.telephone(
            titre, texte_resume(resume, MAX_A_LIRE_TELEPHONE), tag="vigie-resume",
        )
        # La notification persistante (tout le détail) est un complément : le résumé
        # n'est considéré comme livré que si le téléphone l'a reçu.
        await self.notifieur.persistante(titre, texte_resume(resume, None), ID_RESUME_HA)
        if ok:
            etat["resume"] = {"a_lire": [], "a_lire_perdus": 0, "bruit": {}}
            etat["dernier_resume_jour"] = jour


async def vigie_main_loop() -> None:
    """Boucle de fond lancée par le lifespan de `gui_server.py`."""
    reglages = lire_reglages()
    if not reglages.actif:
        logger.info("[VIGIE] Désactivée (MOTEUR_VIGIE_ENABLED=0).")
        return
    if not reglages.notify_service:
        logger.error("[VIGIE] MOTEUR_VIGIE_NOTIFY_SERVICE absent ou invalide : vigie non démarrée.")
        return
    vigie = Vigie(reglages)
    if not vigie.collecteurs:
        logger.error("[VIGIE] Aucune source activée (MOTEUR_VIGIE_GMAIL, MOTEUR_VIGIE_FORUMS, MOTEUR_VIGIE_GITHUB_PSEUDO, "
                     "MOTEUR_VIGIE_YOUTUBE_CHAINE) : vigie non démarrée.")
        return
    logger.info("[VIGIE] Démarrage : %s, relevé toutes les %d min, notify.%s.",
                ", ".join(c.nom for c in vigie.collecteurs), reglages.intervalle_min, reglages.notify_service)
    await asyncio.sleep(DELAI_DEMARRAGE_S)
    while True:
        try:
            rapport = await vigie.passe()
            if not rapport.saute:
                comptes = {n.value: sum(1 for e in rapport.elements if e.niveau == n) for n in Niveau}
                logger.info("[VIGIE] Passage en %.1f s : %s nouveau(x) %s, %d envoi(s), %d en attente.",
                            rapport.duree_s, len(rapport.elements), comptes, len(rapport.envois),
                            rapport.en_attente)
        except Exception as exc:
            # La boucle ne doit jamais mourir : un passage raté vaut mieux qu'une vigie morte.
            logger.error("[VIGIE] Erreur de passage : %s", exc, exc_info=True)
        await asyncio.sleep(reglages.intervalle_min * 60)
