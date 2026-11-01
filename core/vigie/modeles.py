"""
core/vigie/modeles.py — Données échangées entre collecteurs, tri et notification.

Un `Element` est une nouveauté lue dans une source (un mail, un message de
forum, une panne de la vigie elle-même). Son titre vient TOUJOURS de la source
(en-têtes du mail, pseudo et titre du fil) : le LLM local ne fournit qu'un
niveau dans une liste fermée et un résumé tronqué, jamais un titre ni un lien.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum


class Niveau(StrEnum):
    """Trois niveaux, liste fermée (la sortie du LLM y est ramenée ou rejetée)."""

    URGENT = "urgent"   # 🔴 alerte immédiate sur le téléphone
    A_LIRE = "a_lire"   # 🟡 listé dans le résumé du matin
    BRUIT = "bruit"     # ⚪ compté dans le résumé, pas détaillé


LIBELLE_CONSOLE = {Niveau.URGENT: "URGENT", Niveau.A_LIRE: "A LIRE", Niveau.BRUIT: "BRUIT"}

_ESPACES = re.compile(r"\s+")


def nettoyer_texte(texte: str, longueur_max: int) -> str:
    """Texte d'une ligne, sans caractère de contrôle, tronqué à `longueur_max`.

    Sert pour tout ce qui vient d'une source non fiable (mail, forum, sortie du
    LLM) avant d'arriver dans une notification.
    """
    if not isinstance(texte, str):
        return ""
    # Les catégories Unicode « C* » (contrôle, format, non assigné) disparaissent :
    # pas de retour chariot, de caractère bidirectionnel ou d'octet nul injecté.
    propre = "".join(
        " " if unicodedata.category(c).startswith("C") else c for c in texte
    )
    propre = _ESPACES.sub(" ", propre).strip()
    if len(propre) > longueur_max:
        propre = propre[: max(longueur_max - 1, 0)].rstrip() + "…"
    return propre


@dataclass
class Element:
    """Une nouveauté d'une source, prête à être triée puis notifiée."""

    source: str                 # "gmail", "forum:forum.hacf.fr", "vigie"
    ident: str                  # identifiant stable dans la source
    titre: str                  # titre de la notification (lu dans la source)
    message: str                # corps court
    lien: str | None = None     # URL ouverte au toucher (construite par le code)
    niveau: Niveau = Niveau.A_LIRE
    a_trier: bool = False       # True pour un mail : passe par le tri local
    # Champs bruts d'un mail : servent au tri, jamais affichés en console.
    expediteur: str = ""
    objet: str = ""
    extrait: str = ""
    labels: list[str] = field(default_factory=list)
    desabonnement: bool = False  # en-tête List-Unsubscribe présent (envoi de masse)
    methode_tri: str = ""       # "categorie", "llm:<modèle>", "regles", "forum", "panne"
    date_iso: str = ""

    @property
    def cle(self) -> str:
        return f"{self.source}:{self.ident}"

    def vers_attente(self, differe_nuit: bool) -> dict:
        """Forme gardée dans l'état (file d'alertes, résumé) : le strict nécessaire."""
        return {
            "cle": self.cle,
            "source": self.source,
            "titre": self.titre,
            "message": self.message,
            "lien": self.lien,
            "differe_nuit": differe_nuit,
            "date_iso": self.date_iso,
        }
