"""
core/agent_steps.py — Trace pas-à-pas des invocations d'agent (#T422).

La table `agent_steps` existe dans le schéma depuis l'origine, avec ses deux
index et sa purge de rétention. Relevé le 19/08 sur la base de production du
Deck : **0 ligne**. Rien ne l'écrivait, rien ne la lisait — un `grep agent_steps`
sur le dépôt ne rendait que le `CREATE TABLE`, les index, le `DELETE` de la
purge et un test qui vérifie que la table existe.

Conséquence mesurée : pour reconstituer la campagne autonome des 18–19/08, il a
fallu croiser les branches Git, les rapports de cycle JSON et le journal
systemd. Aucune décision d'agent n'était rejouable — on voyait le résultat, pas
le chemin.

## Ce qui est enregistré, et ce qui ne l'est pas

Une ligne par **invocation d'agent**, posée dans l'enveloppe de
`BaseAgent.__init_subclass__` : tous les call-sites (`engine`, `dag_runner`,
`healing`, `review_loop`, `swarm_dispatcher`) y passent, aucun ne peut l'oublier.

`tool_calls_json` reste **NULL** : à ce niveau, la boucle ReAct interne de
l'exécuteur n'est pas visible. Mieux vaut une colonne vide qu'une colonne
remplie de « outils autorisés » qu'un lecteur prendrait pour « outils appelés ».
Le jour où l'exécuteur exposera ses appels, la colonne est là.

Ce qui est enregistré est mesuré, pas raconté : l'objectif reçu, le statut
rendu, la durée réelle, et le coût — calculé par delta sur `token_usage` pour
cette session sur la fenêtre de l'appel.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Longueur maximale des textes stockés. Une trace n'est pas une archive : elle
# doit rester lisible et ne pas faire enfler la base runtime (la purge de
# rétention existante ne passe qu'au-delà du seuil d'âge).
_MAX_TEXTE = 2000

# Phase du cycle déduite du nom de l'agent. La colonne `phase` documente
# « planning, execution, review, correction » ; on tient cette promesse.
_PHASES = {
    "planner": "planning",
    "executor": "execution",
    "reviewer": "review",
    "healing": "correction",
    "auditor": "review",
}


def phase_de_l_agent(nom: str) -> str:
    """Phase du cycle pour un nom d'agent. `autre` quand on ne sait pas."""
    minuscule = (nom or "").lower()
    for cle, phase in _PHASES.items():
        if cle in minuscule:
            return phase
    return "autre"


def _tronquer(valeur: Any, limite: int = _MAX_TEXTE) -> str:
    """Rend une chaîne bornée, quelle que soit la forme de l'entrée."""
    if valeur is None:
        return ""
    texte = valeur if isinstance(valeur, str) else str(valeur)
    if len(texte) <= limite:
        return texte
    return texte[:limite] + f"… (+{len(texte) - limite} caractères)"


def _cout_de_la_fenetre(conn, session_id: str, depuis: float) -> tuple[int, int, float]:
    """
    Jetons et coût imputés à cette session depuis `depuis`.

    C'est un delta sur `token_usage`, la même source que la comptabilité : la
    trace ne réinvente pas un compteur parallèle qui divergerait.
    """
    ligne = conn.execute(
        "SELECT COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0), "
        "COALESCE(SUM(cost_usd), 0.0) FROM token_usage "
        "WHERE session_id = ? AND timestamp >= ?",
        (session_id, depuis),
    ).fetchone()
    if not ligne:
        return 0, 0, 0.0
    return int(ligne[0] or 0), int(ligne[1] or 0), float(ligne[2] or 0.0)


def enregistrer_etape(
    *,
    session_id: str,
    agent_name: str,
    objectif: str,
    statut: str,
    task_id: str | None = None,
    erreur: str | None = None,
    extrait_resultat: Any = None,
    debut: float | None = None,
) -> bool:
    """
    Écrit une ligne de trace. Rend `True` si la ligne est posée.

    **Best effort, jamais bloquant pour l'appelant** : toute erreur est
    journalisée en DEBUG et avalée. Une trace qui ferait échouer l'agent
    qu'elle observe serait pire que pas de trace du tout.
    """
    if not session_id:
        return False
    try:
        from core.runtime_db import get_connection

        maintenant = time.time()
        with get_connection() as conn:
            rang = conn.execute(
                "SELECT COALESCE(MAX(step_index), -1) + 1 FROM agent_steps "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            step_index = int(rang[0]) if rang else 0

            jetons_in, jetons_out, cout = (0, 0, 0.0)
            if debut is not None:
                jetons_in, jetons_out, cout = _cout_de_la_fenetre(conn, session_id, debut)

            observations = {
                "statut": statut,
                "erreur": _tronquer(erreur, 500) or None,
                "extrait": _tronquer(extrait_resultat),
                "duree_ms": round((maintenant - debut) * 1000) if debut else None,
            }

            conn.execute(
                "INSERT INTO agent_steps (session_id, task_id, step_index, agent_name, "
                "phase, thought, tool_calls_json, observations_json, prompt_tokens, "
                "completion_tokens, cost_usd, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                (
                    session_id, task_id, step_index, agent_name,
                    phase_de_l_agent(agent_name),
                    _tronquer(objectif),
                    json.dumps(observations, ensure_ascii=False),
                    jetons_in, jetons_out, cout, maintenant,
                ),
            )
            conn.commit()
        return True
    except Exception as err:  # pragma: no cover — dépend de l'état de la base
        logger.debug(f"[AGENT_STEPS] Trace non enregistrée ({agent_name}) : {err}")
        return False


def etapes_de_session(session_id: str, limite: int = 200) -> list[dict[str, Any]]:
    """
    Trace d'une session, dans l'ordre d'exécution.

    Sans lecteur, une table de trace est une table morte — c'était précisément
    l'état de `agent_steps` avant #T422.
    """
    try:
        from core.runtime_db import get_connection

        with get_connection() as conn:
            conn.row_factory = __import__("sqlite3").Row
            lignes = conn.execute(
                "SELECT step_index, agent_name, phase, thought, observations_json, "
                "prompt_tokens, completion_tokens, cost_usd, timestamp, task_id "
                "FROM agent_steps WHERE session_id = ? "
                "ORDER BY step_index ASC LIMIT ?",
                (session_id, limite),
            ).fetchall()
    except Exception as err:
        logger.warning(f"[AGENT_STEPS] Lecture impossible pour '{session_id}' : {err}")
        return []

    etapes: list[dict[str, Any]] = []
    for ligne in lignes:
        etape = dict(ligne)
        try:
            etape["observations"] = json.loads(etape.pop("observations_json") or "{}")
        except (ValueError, TypeError):
            etape["observations"] = {}
        etapes.append(etape)
    return etapes
