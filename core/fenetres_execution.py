"""
core/fenetres_execution.py — Hiérarchie des fenêtres de temps du moteur (#T277).

POURQUOI CE MODULE EXISTE
Le moteur empile des `asyncio.wait_for` : la session borne le pipeline, le
Planner borne sa génération, le provider borne son appel HTTP. Ces bornes ont
été posées à des moments différents, sans jamais être confrontées — et trois
fois de suite, une fenêtre INTERNE s'est retrouvée plus longue que la fenêtre
EXTERNE qui la contient. Le mécanisme interne devient alors décoratif : il est
tué de l'extérieur avant d'avoir pu s'exercer.

Les trois cas mesurés en production :
1. #T267 — approbation humaine de 300 s dans une session de 120 s : aucune
   demande n'a JAMAIS pu être approuvée (8 demandes en 24 h, 0 résolution).
2. #T277 — read timeout provider de 120 s dans un Planner borné à 90 s : la
   cascade `FallbackProvider` ne pouvait pas basculer sur le modèle suivant,
   puisqu'elle était tuée avant la fin de la PREMIÈRE tentative. Mesuré deux
   fois le 11/08 : une seule tentative, aucun second modèle, plan vide.
3. Le même Planner (90 s) dans une session de fond bornée à 120 s : il ne
   restait que 30 s pour le DAG, la revue et la finalisation.

LA RÈGLE, une seule :
    toute fenêtre englobante doit être STRICTEMENT plus longue que la somme des
    fenêtres qu'elle englobe.

`verifier_emboitement()` la rend vérifiable, et un test la fait respecter — de
sorte qu'un futur ajustement de l'une des valeurs ne puisse plus casser
silencieusement les autres.
"""

import logging
import os

from core.llm_timeouts import get_timeout

logger = logging.getLogger(__name__)

# Marge pour les backoffs de la cascade entre deux tentatives (voir
# `FallbackProvider.generate_structured_async` : 0,5 à 1 s + journalisation).
MARGE_BASCULE_S = 20.0

# Nombre de modèles que la cascade doit pouvoir réellement essayer avant que la
# fenêtre du Planner ne se referme. À 1, il n'y a pas de cascade du tout — c'est
# l'état constaté avant #T277.
TENTATIVES_CASCADE = 2


def duree_tentative_llm_s(famille: str = "gemini") -> float:
    """Durée maximale d'UN appel LLM, telle que les providers l'appliquent."""
    valeur = get_timeout(famille)
    # `get_timeout` rend soit (connect, read), soit une durée unique (CLI).
    return float(valeur[1] if isinstance(valeur, tuple) else valeur)


def duree_planner_s() -> float:
    """
    Fenêtre du Planner : assez large pour que la cascade puisse BASCULER.

    Sans cela, le premier modèle lent consomme toute la fenêtre et les suivants
    ne sont jamais essayés — la cascade réparée par #T265 reste alors décorative
    sur les timeouts (elle ne fonctionne que sur les erreurs immédiates type 403).

    Surchargeable par `MOTEUR_PLANNER_TIMEOUT_S` (une valeur trop basse est
    refusée : elle reproduirait le bug).
    """
    minimum = duree_tentative_llm_s() * TENTATIVES_CASCADE + MARGE_BASCULE_S
    brut = os.environ.get("MOTEUR_PLANNER_TIMEOUT_S")
    if brut:
        try:
            demande = float(brut)
        except ValueError:
            logger.warning(f"[FENETRES] MOTEUR_PLANNER_TIMEOUT_S illisible ({brut!r}) — valeur calculée retenue.")
            return minimum
        if demande < duree_tentative_llm_s():
            logger.warning(
                f"[FENETRES] MOTEUR_PLANNER_TIMEOUT_S={demande}s est inférieur à la durée d'UNE "
                f"tentative LLM ({duree_tentative_llm_s()}s) : refusé, sinon la cascade ne peut "
                f"pas basculer (#T277). Valeur retenue : {minimum}s."
            )
            return minimum
        return demande
    return minimum


def duree_session_fond_s() -> float:
    """
    Fenêtre d'une session de fond (`/api/run`, fire-and-forget).

    Personne n'attend derrière une requête HTTP : la borne n'est là que pour
    éviter une session immortelle. Elle doit contenir le Planner ET l'exécution
    du DAG. À 120 s (l'ancien défaut, hérité du chemin interactif), il restait
    30 s pour tout le reste une fois le Planner servi.
    """
    brut = os.environ.get("MOTEUR_SESSION_FOND_TIMEOUT_S")
    if brut:
        try:
            return float(brut)
        except ValueError:
            logger.warning(f"[FENETRES] MOTEUR_SESSION_FOND_TIMEOUT_S illisible ({brut!r}) — défaut retenu.")
    return 900.0


def duree_tache_dreamcoder_s() -> float:
    """
    Fenêtre d'UNE tâche autonome DreamCoder (nuit, backlog).

    Quatrième occurrence du défaut que ce module existe pour empêcher :
    `_process_one_dreamcoder_task` enveloppait le pipeline dans un
    `wait_for(timeout=600)` — 10 minutes affichées — mais appelait
    `run_full_pipeline` SANS `timeout_seconds`, donc avec son défaut de 120 s
    hérité du chemin interactif. L'enveloppe était décorative : le vrai plafond
    d'une tâche de nuit était de 2 minutes, Planner compris. Aucune tâche de
    code sérieuse ne tient là-dedans.

    Pourquoi 600 s et pas les 900 s de la session de fond : le cycle entier est
    borné par `dreamcoder_max_cycle_minutes` (30 min par défaut), qui est le
    budget de TOUTES les tâches de la nuit. À 900 s, une seule tâche lente
    mangerait la moitié du cycle ; à 600 s il en reste pour trois.
    """
    brut = os.environ.get("MOTEUR_DREAMCODER_TACHE_TIMEOUT_S")
    if brut:
        try:
            return float(brut)
        except ValueError:
            logger.warning(
                f"[FENETRES] MOTEUR_DREAMCODER_TACHE_TIMEOUT_S illisible ({brut!r}) — défaut retenu.")
    return 600.0


# Marge de l'enveloppe `wait_for` de DreamCoder au-dessus de la fenêtre du
# pipeline. Elle DOIT rester positive : c'est ce qui laisse le pipeline rendre
# son erreur « Timeout Xs » (et clore la session) au lieu d'être tué de
# l'extérieur, ce qui laisserait une session zombie (#T361).
MARGE_ENVELOPPE_DREAMCODER_S = 60.0


def duree_enveloppe_dreamcoder_s() -> float:
    """Borne extérieure d'une tâche DreamCoder — toujours au-dessus du pipeline."""
    return duree_tache_dreamcoder_s() + MARGE_ENVELOPPE_DREAMCODER_S


def duree_session_interactive_s() -> float:
    """
    Fenêtre d'une session interactive (chat, vocal) : un humain attend.

    Volontairement INCHANGÉE à 120 s. ⚠️ Elle est plus courte que ce qu'une
    cascade complète exige — c'est un arbitrage produit assumé (mieux vaut
    répondre « je n'ai pas pu » que faire patienter deux minutes de plus), pas
    un oubli. Conséquence à connaître : sur ce chemin, la cascade ne peut pas
    basculer si le premier modèle est lent. Le lever suppose de raccourcir le
    read timeout des providers pour ce chemin — décision non prise ici.
    """
    return 120.0


def verifier_emboitement() -> list[str]:
    """
    Vérifie la règle : englobante > somme des englobées.

    Returns:
        La liste des violations (vide si tout est cohérent). Ne lève pas : le
        moteur doit démarrer même mal configuré, mais le dire.
    """
    violations: list[str] = []
    tentative = duree_tentative_llm_s()
    planner = duree_planner_s()
    session_fond = duree_session_fond_s()

    if planner <= tentative:
        violations.append(
            f"Planner ({planner}s) <= une tentative LLM ({tentative}s) : "
            f"la cascade ne peut pas basculer (#T277)."
        )
    if session_fond <= planner:
        violations.append(
            f"Session de fond ({session_fond}s) <= Planner ({planner}s) : "
            f"il ne resterait rien pour exécuter le DAG."
        )

    tache_dreamcoder = duree_tache_dreamcoder_s()
    if tache_dreamcoder <= planner:
        violations.append(
            f"Tâche DreamCoder ({tache_dreamcoder}s) <= Planner ({planner}s) : "
            f"une tâche de nuit ne pourrait pas dépasser son propre plan."
        )
    if duree_enveloppe_dreamcoder_s() <= tache_dreamcoder:
        violations.append(
            f"Enveloppe DreamCoder ({duree_enveloppe_dreamcoder_s()}s) <= tâche "
            f"({tache_dreamcoder}s) : le pipeline serait tué avant de pouvoir "
            f"rendre son erreur, laissant une session zombie."
        )
    return violations


def journaliser_fenetres() -> None:
    """Trace la hiérarchie au démarrage, et signale toute incohérence."""
    logger.info(
        f"[FENETRES] tentative LLM {duree_tentative_llm_s()}s < Planner {duree_planner_s()}s "
        f"< session de fond {duree_session_fond_s()}s (interactive : {duree_session_interactive_s()}s) "
        f"| tâche DreamCoder {duree_tache_dreamcoder_s()}s < enveloppe {duree_enveloppe_dreamcoder_s()}s"
    )
    for violation in verifier_emboitement():
        logger.error(f"[FENETRES] ⚠️ {violation}")
