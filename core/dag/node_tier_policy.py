"""
core/dag/node_tier_policy.py — Tier de modèle par rôle de nœud DAG (#T245).

Les deux moitiés d'un MapReduce n'ont pas le même besoin : les tâches **Map**
traitent chacune un fragment (travail large, parallèle, jetable), le nœud
**Reduce** agrège et arbitre (travail court, décisif). Jusqu'ici les deux
héritaient du `model_tier` du parent (`{**task_payload.metadata}` recopié tel
quel dans `core/dag_runner.py`), ce qui produisait les deux mauvais cas :

- parent en tier fort → N maps en tier fort, le fan-out brûle le budget sur la
  partie du travail qui en a le moins besoin ;
- parent en tier léger → un Reduce en tier léger, donc l'arbitrage final (la
  seule étape qui voit tous les résultats) est fait par le modèle le plus faible.

Politique retenue (#T245, « Map free×N → Reduce cerveau ») : maps sur le tier
léger — panier gratuit/low-cost dont les quotas sont justement là pour être
saturés en parallèle — et reduce sur le tier fort, un seul appel amorti.

Précédence, du plus fort au plus faible :
1. surcharge explicite de l'appelant : `map_model_tier` / `reduce_model_tier`
   dans les metadata du payload parent ;
2. `config.json` → `dag_node_tiers` : `{"map": ..., "reduce": ...}` ;
3. valeurs par défaut ci-dessous.

La valeur spéciale `"herite"` (à n'importe quel niveau) rétablit l'héritage du
tier du parent, et `MOTEUR_DAG_NODE_TIERS=0` désactive toute la politique —
repli complet sur le comportement d'avant, sans redéploiement.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Rôles reconnus → clé de surcharge dans les metadata du payload parent.
_CLE_METADATA = {
    "map": "map_model_tier",
    "reduce": "reduce_model_tier",
}

# Tiers par défaut : le fan-out en gratuit/low-cost, l'arbitrage sur le cerveau.
TIERS_DEFAUT = {
    "map": "leger",
    "reduce": "fort",
}

# Valeur signifiant « garder le tier du parent » (pas de surcharge).
VALEUR_HERITAGE = "herite"


def politique_active() -> bool:
    """Faux si `MOTEUR_DAG_NODE_TIERS` vaut 0/false/off/no (kill-switch)."""
    return os.getenv("MOTEUR_DAG_NODE_TIERS", "1").strip().lower() not in (
        "0", "false", "off", "no",
    )


def _tiers_de_config(config: dict | None) -> dict:
    """Lit `dag_node_tiers` dans la config fournie, ou dans `config.json`."""
    if config is None:
        try:
            from core.llm_gateway import load_config
            config = load_config()
        except Exception as err:  # config illisible → on reste sur les défauts
            logger.debug(f"[NodeTier] Config illisible, défauts appliqués : {err}")
            config = {}
    valeurs = (config or {}).get("dag_node_tiers") or {}
    return valeurs if isinstance(valeurs, dict) else {}


def tier_pour_role(
    role: str,
    metadata: dict | None = None,
    config: dict | None = None,
) -> str | None:
    """
    Retourne le tier à appliquer au nœud `role` ("map" ou "reduce").

    `None` signifie « ne rien forcer » : l'appelant laisse alors le nœud hériter
    du `model_tier` du parent, comme avant #T245.
    """
    if role not in _CLE_METADATA or not politique_active():
        return None

    candidats = [
        (metadata or {}).get(_CLE_METADATA[role]),
        _tiers_de_config(config).get(role),
        TIERS_DEFAUT[role],
    ]
    for candidat in candidats:
        if candidat is None:
            continue
        valeur = str(candidat).strip()
        if not valeur:
            continue
        if valeur.lower() == VALEUR_HERITAGE:
            return None
        return valeur
    return None


def _normalisation_de_plan_active(config: dict | None) -> bool:
    """Faux si `dag_node_tiers.plan_normalization` est explicitement désactivé."""
    valeur = _tiers_de_config(config).get("plan_normalization", True)
    if isinstance(valeur, str):
        return valeur.strip().lower() not in ("0", "false", "off", "no")
    return bool(valeur)


def normaliser_tiers_du_plan(taches: list, config: dict | None = None) -> dict:
    """
    Applique la politique de rôle au plan du Planner (#T245).

    Le MapReduce explicite n'est pas le seul éventail du moteur : un plan
    comporte des stages dont les tâches s'exécutent en parallèle
    (`core/dag_runner.py`, cap 8) et, en général, une tâche finale qui dépend de
    toutes les autres. Ce sont des maps et un reduce, sans en porter le nom.
    Le prompt du Planner demande cette forme, mais un prompt dérive : la
    normalisation ci-dessous la garantit de façon déterministe et vérifiable.

    Règles, volontairement conservatrices :
    - une tâche appartenant à un stage d'au moins 2 tâches → tier des maps
      (`leger`), **sauf** les tâches de vérification (`verify_*` ou agent
      `reviewer`), que la règle SDD veut en `moyen`/`fort` ;
    - une tâche **seule** dans son stage et dépendant d'au moins 2 tâches →
      tier du reduce (`fort`) : c'est le nœud d'agrégation, le seul qui voit
      tous les résultats. Plusieurs tâches parallèles à plusieurs dépendances
      restent des maps (deuxième vague), pas des reduces.

    Le tier d'origine est conservé dans `model_tier_planner` pour que l'écart
    entre ce que le Planner a demandé et ce qui a été exécuté reste lisible.
    Retourne un rapport {"maps": [...], "reduce": id|None, "inchangees": n}.
    """
    rapport = {"maps": [], "reduce": None, "inchangees": 0}
    if not taches or not politique_active() or not _normalisation_de_plan_active(config):
        rapport["inchangees"] = len(taches or [])
        return rapport

    tier_map = tier_pour_role("map", None, config)
    tier_reduce = tier_pour_role("reduce", None, config)

    effectif_par_stage: dict = {}
    for tache in taches:
        stage = (tache.metadata or {}).get("stage_id", 1)
        effectif_par_stage[stage] = effectif_par_stage.get(stage, 0) + 1

    for tache in taches:
        meta = tache.metadata or {}
        stage = meta.get("stage_id", 1)
        deps = meta.get("depends_on") or getattr(tache, "depends_on", None) or []
        est_verification = (
            str(getattr(tache, "task_id", "") or "").startswith("verify_")
            or meta.get("target_agent") == "reviewer"
        )

        if effectif_par_stage.get(stage, 1) == 1 and len(deps) >= 2 and tier_reduce:
            role, tier = "reduce", tier_reduce
        elif effectif_par_stage.get(stage, 1) >= 2 and tier_map and not est_verification:
            role, tier = "map", tier_map
        else:
            rapport["inchangees"] += 1
            continue

        if meta.get("model_tier") != tier:
            meta.setdefault("model_tier_planner", meta.get("model_tier"))
            meta["model_tier"] = tier
        meta["node_tier_role"] = role
        tache.metadata = meta

        if role == "reduce":
            rapport["reduce"] = getattr(tache, "task_id", None)
        else:
            rapport["maps"].append(getattr(tache, "task_id", None))

    return rapport


def appliquer_tier_role(role: str, metadata: dict | None, config: dict | None = None) -> dict:
    """
    Copie les metadata du parent en y forçant le tier du rôle demandé.

    Trace `node_tier_role` pour que l'origine du tier soit lisible dans les
    events SSE et l'historique, et n'écrase rien quand la politique renvoie
    `None` (kill-switch, `herite`, ou rôle inconnu).
    """
    base = dict(metadata or {})
    tier = tier_pour_role(role, base, config)
    if tier is None:
        return base
    base["model_tier"] = tier
    base["node_tier_role"] = role
    return base
