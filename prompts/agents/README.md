# Prompts systèmes des agents

Un fichier Markdown par agent, chargé par `core/prompt_loader.py`.
Le Python garde un repli identique si le fichier est absent (prod Deck sans overlay docs).

Placeholders interpolés au chargement :
- `planner.md` : `{{CMD_RULE}}` (Windows vs Linux)
- `executor.md` : `{{OS_RULES}}` (idem)

Édition : IHM `PUT /api/agents/{name}` ou édition directe. Cache mtime.
Executor / HA relisent le Markdown à chaque `invoke` (pas de restart).
Planner / Dreamer / Reviewer / ToolMaker chargent aussi via `load_agent_prompt`
à l'appel (planner/dreamer) ou à l'init (reviewer/tool_maker — process long :
éditer via IHM puis restart, ou attendre le prochain cycle).
