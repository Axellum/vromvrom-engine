"""
core/vigie/sources/base.py — Contrat commun des collecteurs de la vigie.

Une source = une classe. Ajouter une boîte (Hotmail au lot 2) = écrire un
`Collecteur` de plus et l'ajouter dans `boucle.construire_collecteurs` ; le
tri, la file d'alertes, le silence, le résumé et la détection de panne ne
changent pas. Un collecteur de mails pose `a_trier=True` sur ses éléments pour
passer par le tri local (`tri_gmail.py`, jamais de LLM cloud).

Règles d'un collecteur :
  - LECTURE SEULE : il n'écrit, ne marque, ne déplace ni ne supprime rien dans
    la source ;
  - il ne garde aucun état lui-même : il reçoit l'état de SA source et rend le
    nouvel état, que la boucle enregistre (ou jette, en passage à blanc) ;
  - premier passage (état vide) : tout ce qui existe est marqué vu, aucun
    élément rendu — pas d'avalanche d'alertes ;
  - une panne totale lève une exception ; une panne partielle (un fil
    injoignable sur dix) rend ce qui a pu être lu ET remplit `erreurs`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from core.vigie.modeles import Element


@dataclass
class ResultatCollecte:
    elements: list[Element]
    etat: dict
    premier_passage: bool = False
    nb_marques_vus: int = 0
    erreurs: list[str] = field(default_factory=list)
    detail: str = ""   # une ligne pour la console (« 3 fils suivis »…), sans contenu de mail


class Collecteur(ABC):
    nom: str = ""

    @abstractmethod
    async def collecter(self, etat_source: dict, *, essai_tri: int = 0) -> ResultatCollecte:
        """Lit les nouveautés depuis `etat_source`.

        `essai_tri` (passage à blanc seulement) : traite aussi les N éléments les
        plus récents comme s'ils étaient nouveaux, pour voir le tri sans attendre
        du courrier. L'état rendu n'est alors jamais enregistré.
        """
