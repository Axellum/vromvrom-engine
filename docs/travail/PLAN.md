# Plan du code — tab5-engine

`maj: 2026-08-12` · Groupes + couloirs. Pas une fiche par fichier (ça date en trois semaines). Détail historique : `docs/ARCHITECTURE.md` (daté) et `contexte_ia/03_Software/CARTOGRAPHIE_MOTEUR.md`.

## Groupes

| Groupe | Où | Rôle |
|---|---|---|
| Entrées | `gui_server.py`, `mcp_server.py`, `workspace_mcp.py`, `main.py` | FastAPI :8000, MCP, CLI |
| Orchestration | `core/engine.py`, `core/router.py`, `core/dag_runner.py`, `core/dag/` | Router → Planner → DAG → Reviewer |
| LLM | `core/llm_gateway.py`, `core/llm/`, `core/openai_compat_provider.py` | Cascade, CB, wrappers |
| Agents | `agents/` | planner, executor, reviewer, ha_agent, dreamer, tool_maker |
| Mémoire | `memory/` | faits, épisodes, RAG, graphe |
| Outils | `tools/tool_registry.py` + modules | 23 outils ReAct |
| API | `api/routes/` | REST IHM |
| IHM | `ihm-v2/` | React 19 / Vite |
| Persistance | `core/runtime_db.py`, `core/models_db.py` | runtime + catalogue (gitignorés) |

## Fichiers chauds (y toucher = PR dédiée)

`core/engine.py` · `core/router.py` · `core/dag_runner.py` · `core/llm_gateway.py` · `tools/git_safety.py` · `core/hitl.py`

## Couloirs

Deux agents ne touchent pas le même fichier chaud la même nuit.

| Zone | Réservé si |
|---|---|
| `core/dag_runner.py`, `core/dag/` | #T303 (map-reduce) |
| `core/llm_gateway.py` (résolution / timeouts) | #T169 |
| `generate_async` des providers | #T306 |
| `tools/sync_db_to_markdown.py`, `core/router.py` ~l.446 | #T307 |

Le reste (`ihm-v2/`, `prompts/agents/`, `docs/travail/`, routes hors HA) est libre.

## Config agents

- Mécanique : `config.json` (`tiers`, `allowed_tools`, `custom_agents`)
- Personnalité : `prompts/agents/<nom>.md`
- Workflow visuel IHM : `agents_workflows.json` (pas une source de prompts)
