"""
api/routes/agents_crud.py — CRUD /api/agents pour la vue AgentsManager (#T159).

Contrats RÉELS uniquement (leçon #T160 : ne jamais inventer un contrat côté vue) :
- Agents cœur (planner, executor, antigravity_agent, ha_agent, reviewer,
  prompt_engineer, tool_maker) : le "modèle" est la clé `<agent>_model` de
  config.json (tier leger/moyen/fort/automatique OU id de modèle littéral),
  celle que core/factory.py et services/pipeline_service.py lisent réellement.
- Prompts systèmes : fichiers Markdown de #T188 (core/prompt_loader.py) pour
  reviewer/planner/tool_maker/prompt_engineer. Les autres prompts vivent dans
  le code Python et sont exposés en lecture seule (null → non éditable).
- Agents persistants (daemon, dreamer) : clés `persistent_agents.*` de
  config.json (mêmes clés que /api/persistent-agents/config).
- Agents custom : section `custom_agents` de config.json, réellement chargée
  par core/factory.py et services/pipeline_service.py (ExecutorAgent cloné).
  Les agents custom dessinés dans l'éditeur de workflow restent listés
  (source "workflow") mais se gèrent dans l'éditeur, pas ici.

Toutes les écritures config.json : read-modify-write atomique sous FileLock
(même discipline que api/routes/daemon.py).
"""

import json
import logging
import os
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.prompt_loader import has_external_prompt, load_agent_prompt, save_agent_prompt
from core.safe_io import file_lock, safe_json_write

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agents CRUD"])

CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.json"
)

# Tiers acceptés comme valeur de "modèle" pour un agent (résolus par
# LLMGateway.get_provider_for_tier) — tout autre valeur = id de modèle littéral.
VALID_TIERS = ("leger", "moyen", "fort", "automatique")

# Agents cœur du pipeline : nom → clé config.json portant leur modèle/tier.
# None = pas de clé config (le modèle est résolu en interne par l'agent).
CORE_AGENTS: dict[str, dict] = {
    "planner": {
        "config_key": "planner_model",
        "default_model": "fort",
        "label": "Planner (architecte du plan DAG)",
        "prompt_agent": "planner",
    },
    "executor": {
        "config_key": "executor_model",
        "default_model": "automatique",
        "label": "Executor (boucle ReAct + outils)",
        "prompt_agent": None,
    },
    "antigravity_agent": {
        "config_key": "antigravity_model",
        "default_model": "fort",
        "label": "Antigravity (raisonnement avancé)",
        "prompt_agent": None,
    },
    "ha_agent": {
        "config_key": "ha_model",
        "default_model": "moyen",
        "label": "Home Assistant (domotique)",
        "prompt_agent": None,
    },
    "reviewer": {
        "config_key": "reviewer_model",
        "default_model": "moyen",
        "label": "Reviewer (revue de code)",
        "prompt_agent": "reviewer",
    },
    "prompt_engineer": {
        "config_key": "prompt_engineer_model",
        "default_model": "fort",
        "label": "Prompt Engineer (reformulation experte)",
        "prompt_agent": "prompt_engineer",
    },
    "tool_maker": {
        "config_key": None,
        "default_model": None,
        "label": "Tool Maker (génération d'outils)",
        "prompt_agent": "tool_maker",
    },
}

# Agents persistants : nom → clés persistent_agents.* réellement lues par le moteur.
PERSISTENT_AGENTS: dict[str, dict] = {
    "daemon": {
        "model_key": "daemon_model",
        "enabled_key": "daemon_enabled",
        "interval_key": "daemon_interval_minutes",
        "label": "Daemon Sentinelle 24/7",
    },
    "dreamer": {
        "model_key": "dreamer_model",
        "enabled_key": "dreamer_enabled",
        "interval_key": None,  # planifié par dreamer_schedule (cron), pas un intervalle
        "label": "Dreamer (consolidation nocturne + DreamCoder)",
    },
}

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


class AgentCreateBody(BaseModel):
    name: str = Field(..., description="Identifiant snake_case unique de l'agent")
    label: str | None = None
    tier: str = Field("automatique", description="Tier ou id de modèle littéral")
    system_prompt: str | None = None


class AgentPatchBody(BaseModel):
    model: str | None = None
    tier: str | None = None
    label: str | None = None
    enabled: bool | None = None
    interval_minutes: int | None = None
    system_prompt: str | None = None


def _load_config() -> dict:
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def _mutate_config(mutator) -> dict:
    """Read-modify-write atomique de config.json sous FileLock."""
    with file_lock(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            config = json.load(f)
        mutator(config)
        safe_json_write(CONFIG_FILE, config, lock=False)
    return config


def _tier_of(model_value: str | None) -> str:
    """Tier affiché : la valeur elle-même si c'en est un, sinon 'automatique'."""
    return model_value if model_value in VALID_TIERS else "automatique"


def _core_agent_dict(name: str, meta: dict, config: dict) -> dict:
    prompt_agent = meta["prompt_agent"]
    system_prompt = None
    prompt_source = None
    if prompt_agent:
        system_prompt = load_agent_prompt(prompt_agent, "")
        prompt_source = "markdown" if has_external_prompt(prompt_agent) else "defaut_code"
    model = config.get(meta["config_key"], meta["default_model"]) if meta["config_key"] else None
    return {
        "name": name,
        "label": meta["label"],
        "kind": "core",
        "enabled": True,
        "can_disable": False,
        "model": model,
        "tier": _tier_of(model),
        "system_prompt": system_prompt,
        "prompt_editable": prompt_agent is not None,
        "prompt_source": prompt_source,
        "is_custom": False,
        "source": "config",
    }


def _persistent_agent_dict(name: str, meta: dict, config: dict) -> dict:
    pa = config.get("persistent_agents", {})
    model = pa.get(meta["model_key"])
    interval = pa.get(meta["interval_key"]) if meta["interval_key"] else None
    return {
        "name": name,
        "label": meta["label"],
        "kind": "persistent",
        "enabled": bool(pa.get(meta["enabled_key"], False)),
        "can_disable": True,
        "model": model,
        "tier": _tier_of(model),
        "system_prompt": None,
        "prompt_editable": False,
        "prompt_source": None,
        "is_custom": False,
        "interval_minutes": interval,
        "source": "config",
    }


def _custom_agent_dict(entry: dict) -> dict:
    return {
        "name": entry["name"],
        "label": entry.get("label", entry["name"]),
        "kind": "custom",
        "enabled": bool(entry.get("enabled", True)),
        "can_disable": True,
        "model": entry.get("tier", "automatique"),
        "tier": _tier_of(entry.get("tier", "automatique")),
        "system_prompt": entry.get("system_prompt"),
        "prompt_editable": True,
        "prompt_source": "config",
        "is_custom": True,
        "source": "config",
    }


def get_config_custom_agents(config: dict | None = None) -> list[dict]:
    """Agents custom de config.json (délégué au helper partagé, cf. #T213)."""
    from core.custom_agents import load_custom_agent_entries
    if config is None:
        try:
            config = _load_config()
        except Exception:
            return []
    return load_custom_agent_entries(config)


@router.get("/api/agents")
def list_agents():
    """Liste unifiée des agents : cœur + persistants + custom (config et workflow)."""
    try:
        config = _load_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"config.json illisible : {e}")

    agents = [_core_agent_dict(n, m, config) for n, m in CORE_AGENTS.items()]
    agents += [_persistent_agent_dict(n, m, config) for n, m in PERSISTENT_AGENTS.items()]
    agents += [_custom_agent_dict(e) for e in get_config_custom_agents(config)]

    # Agents custom dessinés dans l'éditeur de workflow (lecture seule ici :
    # leur cycle de vie appartient à agents_workflows.json / WorkflowBuilder).
    try:
        from core.workflow_bridge import WorkflowBridge
        known = {a["name"] for a in agents}
        for custom in WorkflowBridge().get_custom_agents_config():
            if custom["name"] in known:
                continue
            agents.append({
                "name": custom["name"],
                "label": custom.get("label", custom["name"]),
                "kind": "custom",
                "enabled": True,
                "can_disable": False,
                "model": custom.get("tier", "automatique"),
                "tier": _tier_of(custom.get("tier", "automatique")),
                "system_prompt": None,
                "prompt_editable": False,
                "prompt_source": None,
                "is_custom": True,
                "source": "workflow",
            })
    except Exception as e:
        logger.warning(f"[AGENTS] WorkflowBridge indisponible : {e}")

    return {"agents": agents}


@router.post("/api/agents")
def create_agent(body: AgentCreateBody):
    """Crée un agent custom (config.json → réellement enregistré dans le moteur)."""
    name = body.name.strip().lower()
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail="Nom invalide : snake_case, 2-41 caractères, commence par une lettre.",
        )
    if name in CORE_AGENTS or name in PERSISTENT_AGENTS or name == "router":
        raise HTTPException(status_code=409, detail=f"'{name}' est un agent réservé du moteur.")

    created = {
        "name": name,
        "label": (body.label or name).strip(),
        "tier": body.tier if body.tier in VALID_TIERS else body.tier.strip(),
        "system_prompt": (body.system_prompt or "").strip() or None,
        "enabled": True,
    }

    def mutate(config: dict):
        entries = config.setdefault("custom_agents", [])
        if any(e.get("name") == name for e in entries if isinstance(e, dict)):
            raise HTTPException(status_code=409, detail=f"L'agent custom '{name}' existe déjà.")
        entries.append(created)

    _mutate_config(mutate)
    logger.info(f"[AGENTS] Agent custom '{name}' créé (tier={created['tier']}).")
    return _custom_agent_dict(created)


@router.put("/api/agents/{name}")
def update_agent(name: str, body: AgentPatchBody):
    """
    Patch partiel d'un agent :
    - cœur      : model/tier → clé `<agent>_model` de config.json ;
                  system_prompt → fichier Markdown #T188 (si prompt_editable).
    - persistant: model, enabled, interval_minutes → persistent_agents.*.
    - custom    : label, tier/model, system_prompt, enabled → custom_agents[].
    """
    new_model = body.model if body.model is not None else body.tier

    if name in CORE_AGENTS:
        meta = CORE_AGENTS[name]
        if new_model is not None:
            if meta["config_key"] is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"'{name}' n'a pas de modèle configurable (résolu en interne).",
                )
            config = _mutate_config(lambda c: c.__setitem__(meta["config_key"], new_model))
        else:
            config = _load_config()
        if body.system_prompt is not None:
            if not meta["prompt_agent"]:
                raise HTTPException(
                    status_code=422,
                    detail=f"Le prompt de '{name}' vit dans le code Python (non éditable ici).",
                )
            path = save_agent_prompt(meta["prompt_agent"], body.system_prompt)
            logger.info(f"[AGENTS] Prompt '{name}' sauvegardé → {path}")
        if body.enabled is not None:
            raise HTTPException(status_code=422, detail=f"'{name}' (agent cœur) ne se désactive pas.")
        return _core_agent_dict(name, meta, config)

    if name in PERSISTENT_AGENTS:
        meta = PERSISTENT_AGENTS[name]

        def mutate(config: dict):
            pa = config.setdefault("persistent_agents", {})
            if new_model is not None:
                pa[meta["model_key"]] = new_model
            if body.enabled is not None:
                pa[meta["enabled_key"]] = body.enabled
            if body.interval_minutes is not None:
                if not meta["interval_key"]:
                    raise HTTPException(
                        status_code=422,
                        detail=f"'{name}' est planifié par cron (dreamer_schedule), pas par intervalle.",
                    )
                pa[meta["interval_key"]] = body.interval_minutes

        config = _mutate_config(mutate)
        return _persistent_agent_dict(name, meta, config)

    # ── Agent custom (config.json) ──
    updated: dict = {}

    def mutate(config: dict):
        entries = config.get("custom_agents", [])
        for entry in entries:
            if isinstance(entry, dict) and entry.get("name") == name:
                if new_model is not None:
                    entry["tier"] = new_model
                if body.label is not None:
                    entry["label"] = body.label.strip()
                if body.system_prompt is not None:
                    entry["system_prompt"] = body.system_prompt.strip() or None
                if body.enabled is not None:
                    entry["enabled"] = body.enabled
                updated.update(entry)
                return
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{name}' introuvable (les agents du workflow s'éditent dans l'éditeur).",
        )

    _mutate_config(mutate)
    return _custom_agent_dict(updated)


@router.post("/api/agents/{name}/toggle")
def toggle_agent(name: str):
    """Active/désactive un agent persistant ou custom. Les agents cœur : refus explicite."""
    if name in CORE_AGENTS:
        raise HTTPException(
            status_code=422,
            detail=f"'{name}' est un agent cœur du pipeline : il ne peut pas être désactivé.",
        )

    result = {"enabled": False}

    if name in PERSISTENT_AGENTS:
        meta = PERSISTENT_AGENTS[name]

        def mutate(config: dict):
            pa = config.setdefault("persistent_agents", {})
            pa[meta["enabled_key"]] = not bool(pa.get(meta["enabled_key"], False))
            result["enabled"] = pa[meta["enabled_key"]]

        _mutate_config(mutate)
        return result

    def mutate(config: dict):
        for entry in config.get("custom_agents", []):
            if isinstance(entry, dict) and entry.get("name") == name:
                entry["enabled"] = not bool(entry.get("enabled", True))
                result["enabled"] = entry["enabled"]
                return
        raise HTTPException(status_code=404, detail=f"Agent '{name}' introuvable.")

    _mutate_config(mutate)
    return result


@router.delete("/api/agents/{name}")
def delete_agent(name: str):
    """Supprime un agent custom (config.json uniquement)."""
    if name in CORE_AGENTS or name in PERSISTENT_AGENTS:
        raise HTTPException(status_code=422, detail=f"'{name}' est un agent du moteur, non supprimable.")

    def mutate(config: dict):
        entries = config.get("custom_agents", [])
        remaining = [e for e in entries if not (isinstance(e, dict) and e.get("name") == name)]
        if len(remaining) == len(entries):
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{name}' introuvable ici (ceux du workflow se suppriment dans l'éditeur).",
            )
        config["custom_agents"] = remaining

    _mutate_config(mutate)
    return {"status": "ok", "deleted": name}
