"""
core/vigie/reglages.py — Réglages de la vigie, lus dans l'environnement.

Toutes les variables sont documentées dans `.env.example` (section « Vigie »).
Défaut le plus important : `MOTEUR_VIGIE_ENABLED=0`. Un merge sur master ne
démarre rien et n'envoie rien à personne ; c'est Axel qui l'active dans le
`.env` du Deck.

Ce qui n'est PAS réglable, volontairement : la confidentialité du tri des mails.
`MOTEUR_VIGIE_LLM_MODELES` choisit l'ordre des modèles, mais un modèle non local
y est refusé à l'exécution (`tri_gmail.cascade_locale`). Le contenu d'un mail ne
part jamais vers un LLM cloud (DECISIONS.md, D-23).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from core.vigie.sources.github import parser_discussions
from core.vigie.tri_gmail import MODELES_DEFAUT, normaliser_entrees

logger = logging.getLogger(__name__)

_FAUX = {"0", "false", "no", "off", "non", ""}


def _bool(env: dict, nom: str, defaut: str) -> bool:
    return env.get(nom, defaut).strip().lower() not in _FAUX


def _liste(env: dict, nom: str, defaut: str) -> tuple[str, ...]:
    """Noms séparés par des virgules. Variable ABSENTE → défaut ; présente et vide → ()."""
    texte = env.get(nom)
    if texte is None:
        texte = defaut
    return tuple(x.strip() for x in texte.split(",") if x.strip())


def _int(env: dict, nom: str, defaut: int, minimum: int | None = None) -> int:
    try:
        valeur = int(str(env.get(nom, defaut)).strip())
    except ValueError:
        logger.warning("[VIGIE] %s illisible, défaut %s retenu.", nom, defaut)
        valeur = defaut
    if minimum is not None:
        valeur = max(valeur, minimum)
    return valeur


@dataclass(frozen=True)
class ForumSuivi:
    """Un fil configuré : forum Discourse, numéro du fil, pseudo d'Axel sur ce forum."""

    base: str      # https://community.home-assistant.io (sans / final)
    fil: int
    pseudo: str


@dataclass(frozen=True)
class Reglages:
    actif: bool
    intervalle_min: int
    gmail: bool
    gmail_requete: str
    forums: tuple[ForumSuivi, ...]
    forums_jours: int            # 0 = pas de découverte des fils où Axel a posté
    notify_service: str          # "mobile_app_xxx" (sans « notify. »), "" = non réglé
    silence: tuple[int, int] | None
    resume_heure: int | None
    llm: bool
    llm_modeles: tuple[str, ...]  # cascade locale ordonnée ; un nom non local est refusé
    llm_timeout_s: int
    gmail_ignorer: tuple[str, ...]  # domaines/adresses → ⚪ sans LLM
    gmail_alerter: tuple[str, ...]  # domaines/adresses → 🔴 sans LLM
    groupe_seuil: int            # plus de N alertes dans un passage → une seule notification
    panne_seuil: int             # N échecs consécutifs d'une source → une alerte
    github_pseudo: str = ""      # "" = pas de source GitHub ; jeton lu par le collecteur, jamais ici
    github_discussions: tuple[str, ...] = ()   # clés « discussion:owner/depot#n » suivies en entier
    github_jours: int = 60
    youtube_chaine: str = ""     # identifiant « UC… » ; "" = pas de source YouTube ; clé lue par le collecteur


def parser_forums(texte: str) -> tuple[ForumSuivi, ...]:
    """`base|id_fil|pseudo` séparés par des virgules ; une entrée invalide est ignorée."""
    suivis: list[ForumSuivi] = []
    for brut in (texte or "").split(","):
        brut = brut.strip()
        if not brut:
            continue
        morceaux = [m.strip() for m in brut.split("|")]
        if len(morceaux) != 3:
            logger.warning("[VIGIE] Entrée de forum ignorée (attendu base|id_fil|pseudo) : %r", brut)
            continue
        base, fil, pseudo = morceaux
        parties = urlsplit(base)
        if parties.scheme != "https" or not parties.hostname or not fil.isdigit() or not pseudo:
            logger.warning("[VIGIE] Entrée de forum ignorée (base https, id numérique, pseudo) : %r", brut)
            continue
        suivis.append(ForumSuivi(base=base.rstrip("/"), fil=int(fil), pseudo=pseudo))
    return tuple(suivis)


def parser_silence(texte: str) -> tuple[int, int] | None:
    """`22-7` → (22, 7). Vide, `0`, `non` ou `aucun` → pas de silence."""
    texte = (texte or "").strip().lower()
    if texte in ("", "0", "non", "aucun", "none", "off"):
        return None
    try:
        debut, fin = (int(x) for x in texte.split("-", 1))
    except ValueError:
        logger.warning("[VIGIE] MOTEUR_VIGIE_SILENCE illisible (%r) : pas de silence.", texte)
        return None
    if not (0 <= debut <= 23 and 0 <= fin <= 23) or debut == fin:
        logger.warning("[VIGIE] MOTEUR_VIGIE_SILENCE hors bornes (%r) : pas de silence.", texte)
        return None
    return debut, fin


def en_silence(heure: int, silence: tuple[int, int] | None) -> bool:
    """Vrai si `heure` (0-23) tombe dans la plage ; `22-7` passe minuit."""
    if silence is None:
        return False
    debut, fin = silence
    if debut < fin:
        return debut <= heure < fin
    return heure >= debut or heure < fin


def parser_identifiant(texte: str, nom: str) -> str:
    """Pseudo GitHub ou identifiant de chaîne YouTube : lettres, chiffres, `-` et `_` ; invalide → ""."""
    texte = (texte or "").strip()
    if texte and not all(c.isascii() and (c.isalnum() or c in "-_") for c in texte):
        logger.warning("[VIGIE] %s ignoré (lettres, chiffres, - et _ seulement) : %r", nom, texte)
        return ""
    return texte


def normaliser_service(nom: str) -> str:
    """`notify.mobile_app_x` ou `mobile_app_x` → `mobile_app_x` ; invalide → ""."""
    nom = (nom or "").strip()
    if nom.startswith("notify."):
        nom = nom[len("notify."):]
    # Le nom finit dans un chemin d'URL : on n'accepte que [a-z0-9_].
    if not nom or not all(c.isascii() and (c.isalnum() or c == "_") for c in nom):
        return ""
    return nom.lower()


def lire_reglages(env: dict | None = None) -> Reglages:
    env = dict(os.environ if env is None else env)
    resume = env.get("MOTEUR_VIGIE_RESUME_HEURE", "8").strip()
    resume_heure: int | None
    if resume.lower() in ("", "non", "aucun", "off", "-1"):
        resume_heure = None
    else:
        resume_heure = _int(env, "MOTEUR_VIGIE_RESUME_HEURE", 8, minimum=0)
        if resume_heure > 23:
            resume_heure = None
    return Reglages(
        actif=_bool(env, "MOTEUR_VIGIE_ENABLED", "0"),
        intervalle_min=_int(env, "MOTEUR_VIGIE_INTERVALLE_MIN", 10, minimum=2),
        gmail=_bool(env, "MOTEUR_VIGIE_GMAIL", "1"),
        gmail_requete=env.get("MOTEUR_VIGIE_GMAIL_REQUETE", "").strip() or "in:inbox newer_than:2d",
        forums=parser_forums(env.get("MOTEUR_VIGIE_FORUMS", "")),
        forums_jours=_int(env, "MOTEUR_VIGIE_FORUMS_JOURS", 90, minimum=0),
        notify_service=normaliser_service(env.get("MOTEUR_VIGIE_NOTIFY_SERVICE", "")),
        silence=parser_silence(env.get("MOTEUR_VIGIE_SILENCE", "22-7")),
        resume_heure=resume_heure,
        llm=_bool(env, "MOTEUR_VIGIE_LLM", "1"),
        llm_modeles=_liste(env, "MOTEUR_VIGIE_LLM_MODELES", ",".join(MODELES_DEFAUT)),
        llm_timeout_s=_int(env, "MOTEUR_VIGIE_LLM_TIMEOUT_S", 90, minimum=5),
        gmail_ignorer=normaliser_entrees(env.get("MOTEUR_VIGIE_GMAIL_IGNORER", "")),
        gmail_alerter=normaliser_entrees(env.get("MOTEUR_VIGIE_GMAIL_ALERTER", "")),
        groupe_seuil=_int(env, "MOTEUR_VIGIE_GROUPE_SEUIL", 3, minimum=1),
        panne_seuil=_int(env, "MOTEUR_VIGIE_PANNE_SEUIL", 3, minimum=1),
        github_pseudo=parser_identifiant(env.get("MOTEUR_VIGIE_GITHUB_PSEUDO", ""), "MOTEUR_VIGIE_GITHUB_PSEUDO"),
        github_discussions=parser_discussions(env.get("MOTEUR_VIGIE_GITHUB_DISCUSSIONS", "")),
        github_jours=_int(env, "MOTEUR_VIGIE_GITHUB_JOURS", 60, minimum=1),
        youtube_chaine=parser_identifiant(env.get("MOTEUR_VIGIE_YOUTUBE_CHAINE", ""), "MOTEUR_VIGIE_YOUTUBE_CHAINE"),
    )
