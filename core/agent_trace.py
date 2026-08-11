"""
core/agent_trace.py — Traçabilité de l'agent qui a déclenché l'appel LLM (#T296).

Le problème : `token_usage.agent_name` était NULL sur 100 % des lignes (54/54
mesurées le 11/08 sur 24 h), donc le tableau « Activité par agent » de l'IHM ne
pouvait structurellement rien afficher. La cause n'était pas un appelant
distrait : `core/token_tracker.py::record_usage()` — le chemin unique par lequel
passent TOUS les providers — n'avait pas de paramètre `agent_name`. Et l'ajouter
n'aurait pas suffi : un provider ne CONNAÎT pas l'agent, c'est une notion de la
couche au-dessus. L'information devait descendre trois étages sans que les
quinze call-sites des providers aient à la transporter.

Mécanisme : une ContextVar, sur le modèle exact de `core/llm/fallback_trace.py`
(#T288) — bibliothèque standard, aucune dépendance, isolée par tâche asyncio et
par thread :
  - `BaseAgent` la renseigne automatiquement autour de chaque `invoke()`
    (`agents/base_agent.py`), donc aucun agent ni aucun appelant n'a à y penser ;
  - `record_usage()` la lit au moment d'écrire la ligne de consommation.

Pourquoi c'est sûr en concurrence : chaque asyncio.Task démarre avec une copie
du contexte courant, donc un `set()` dans une tâche n'affecte que cette tâche.
Le DAG lance chaque nœud dans sa propre Task — deux agents parallèles écrivent
donc chacun leur nom, jamais celui du voisin.

Pourquoi un jeton de restauration (et pas un simple `set()`) : les agents
s'appellent entre eux. `core/healing.py:118` invoque le planner DEPUIS un autre
agent, `core/review_loop.py:199` invoque le reviewer de la même façon. Sans
`reset(jeton)`, l'agent parent finirait ses propres appels LLM sous le nom de
son enfant, et la colonne mentirait au lieu d'être vide — un mensonge coûte plus
cher qu'un trou (#T294).

Valeur None = appel LLM hors agent : routeur, classificateur, collecteur de
quotas, script direct. C'est une absence légitime, pas un défaut.
"""

from contextvars import ContextVar, Token

_AGENT_COURANT: ContextVar[str | None] = ContextVar("agent_courant", default=None)


def poser_agent_courant(nom: str | None) -> Token:
    """[T296] Déclare l'agent qui s'exécute. Rend le jeton à passer à `restaurer_agent_courant`."""
    return _AGENT_COURANT.set(nom)


def restaurer_agent_courant(jeton: Token) -> None:
    """[T296] Rétablit l'agent précédent — indispensable quand un agent en invoque un autre."""
    try:
        _AGENT_COURANT.reset(jeton)
    except ValueError:
        # Jeton créé dans un autre contexte (tâche asyncio différente) : la copie
        # de contexte de cette tâche disparaît avec elle, il n'y a rien à rétablir.
        pass


def lire_agent_courant() -> str | None:
    """[T296] Nom de l'agent dont dépend l'appel LLM en cours, None hors agent."""
    return _AGENT_COURANT.get()
