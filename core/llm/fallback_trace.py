"""
core/llm/fallback_trace.py — Traçabilité du modèle qui a réellement répondu (#T288).

FallbackProvider choisit le modèle au moment de l'appel (parcours des candidats
jusqu'au premier qui aboutit). Aucun appelant ne pouvait savoir lequel avait
répondu : seul l'identifiant du TIER (« tier-moyen ») remontait, et
l'information réelle n'existait que dans les journaux, en texte libre —
impossible d'attribuer un résultat, une latence ou un coût à un modèle précis.

Mécanisme : une ContextVar (bibliothèque standard, aucune dépendance), isolée
par tâche asyncio et par thread :
  - FallbackProvider la renseigne au moment où il rend la réponse d'un candidat
    (le `return res`, seul point où l'on sait quel modèle a gagné) ;
  - l'appelant la lit immédiatement après son appel, dans le même contexte.

Pourquoi c'est sûr en concurrence : chaque asyncio.Task démarre avec une copie
du contexte courant (asyncio exécute la coroutine dans le contexte capturé à
la création de la tâche). Un `set()` dans une tâche n'affecte que la copie de
cette tâche — vérifié expérimentalement (deux tâches concurrentes lisent
chacune leur valeur, la tâche mère reste à None). Le DAG lance chaque nœud dans
sa propre Task (core/dag_runner.py, asyncio.create_task) et les agents appellent
generate_async() directement dans cette tâche : l'appelant B ne peut donc pas
lire le modèle de l'appelant A, même sur un FallbackProvider partagé.

Valeur None = aucun modèle n'a répondu : appel servi par le cache sémantique,
ou appel en échec (la trace est réinitialisée en tête de chaque appel).
"""

from contextvars import ContextVar

_MODELE_REPONDU: ContextVar[str | None] = ContextVar(
    "fallback_modele_repondu", default=None
)


def marquer_modele_repondu(modele: str | None) -> None:
    """[T288] Enregistre le modèle qui a répondu à l'appel courant (None = aucun)."""
    _MODELE_REPONDU.set(modele)


def lire_modele_repondu() -> str | None:
    """[T288] Modèle qui a répondu au dernier appel LLM de la tâche courante, None sinon."""
    return _MODELE_REPONDU.get()
