# Configure the engine

> **In one sentence.** Settings live in four places: the `.env` (keys and switches, read at startup),
> `config.json` (models, budgets, background agents), the model catalog (`models_registry.db`) and a few
> data files (`ha_commands.json`, `pricing_strategy.json`). This page lists every setting, its default **read
> in the code**, its effect, and whether a restart is needed.

Defaults are those of the code of this repository (file cited). "—" = default not determined or not applicable. The values
of the `.env.example` and `config.example.json` files are flagged as such when they differ from the code.

Related pages: [01 Install](01_install.md) · [02 Understand](02_understand.md) ·
[04 Home and voice](04_home_and_voice.md) · [05 Operate](05_operate.md).

---

## 1. Where each setting is and when it takes effect

| Where | Content | When it takes effect |
|---|---|---|
| **`.env`** | Provider keys, Home Assistant access, switches, paths | **At restart only**: the file is loaded once. A variable already present in the process environment **wins over the `.env`** (`load_dotenv` without override). `MOTEUR_API_KEY` is also cached. |
| **`config.json`** | Models per agent, tiers, budgets, background agents, human approval | The file is re-read when its modification date changes (`core/llm_gateway.py`, `load_config`). The background loops (sentinel, Dreamer, Auditor) re-read it at every turn. **Exceptions**: `budget_guard` is read when the guard is created, `ha_commands.json` when the router is created: **restart**. When in doubt, restart. |
| **Catalog** (`models_registry.db`) | Models, tiers, activation | **Régler** (Configure) **→ Modèles & routage** (Models and routing) screen, or `POST /api/models/{id}/toggle`, `/routing-tier`, `/bulk-status`. Immediate. |
| **`ha_commands.json`** | Catalog of home automation phrases | Restart ([04](04_home_and_voice.md)). |
| **`pricing_strategy.json`** | Price list | Rewritten by the price watch (§ 2.5, `MOTEUR_VEILLE_APPLIQUER`). |

**Configuration screen** (menu **Régler → Configuration**): it reads and writes `config.json` through `GET`/`POST /api/config`. Be careful,
`POST /api/config` merges only at the **first level**: sending a nested section (`budget_guard`, for example)
**replaces the whole section** (`api/routes/context.py:42-49`). The screen sends the complete section; if you call
the API yourself, send it back in full. `POST /api/persistent-agents/config` (background agents) does merge
(`api/routes/daemon.py:76-96`).

**Without `config.json`**, `load_config()` uses internal defaults: `planner_model=deepseek-reasoner`,
`executor_model=gemini-3.5-flash-free`, `antigravity_model=fort`, and the background agents **sentinel and Dreamer set to
`true`**. It also **rewrites** `config.json` to fill in the missing keys (tiers, `persistent_agents`) when the
file exists.

---

## 2. The `.env`, group by group

### 2.1 Access and security

| Variable | Role | Default |
|---|---|---|
| `MOTEUR_API_KEY` | Bearer key for all `/api` routes | empty → **503** (`core/auth.py`) |
| `MCP_TOKEN_KEY` | Encrypts `google_token.json` (Google OAuth token) | empty → token in clear text (`.env.example`, `core/token_crypto.py`) |
| `MOTEUR_CORS_ORIGINS` | Allowed origins, comma-separated | empty → CORS open `*` **without** cookies (`gui_server.py`) |
| `MOTEUR_WORKSPACE_ROOT` | Root where the file tools are allowed to write and read | parent folder of the repository (`.env.example`) |
| `MOTEUR_PRIVACY_LEVEL` | `local_only`: no cloud call, explicit failure otherwise | empty (disabled) |
| `MOTEUR_ENABLE_PLUGINS` | Loads the code of `plugins/*/agent.py` (runs code) | `0` (`core/plugin_registry.py`) |
| `MOTEUR_ENABLE_TOOL_MAKER` | Allows the ToolMaker to persist generated tools | `0` (`agents/tool_maker_agent.py`) |
| `MOTEUR_FILE_WRITE_GUARD` | Write guard of the file tools | `1` (`tools/system.py`) |
| `MOTEUR_TERMINAL_GARDE_CHEMINS` | Path guard of the terminal tool | `1` (`tools/terminal.py`) |
| `MOTEUR_ROUTER_GARDE_HITL` | A request to act does not take the shortcut | `1` (`core/router.py`) |
| `MOTEUR_HITL_NON_BLOQUANT` | An approval request suspends the session instead of blocking | `1` (`core/engine.py`) |
| `MOTEUR_DISABLE_CLI_PROVIDERS` | Excludes "command-line subscription" providers from the cascade | empty (`core/budget_guard.py`) |

Safeguards on by default whose precise effect is not detailed here: `MOTEUR_CONTRAT_SUR_ECHEC`
(`core/review_loop.py`), `MOTEUR_DAG_NODE_TIERS` (`core/dag/node_tier_policy.py`), `MOTEUR_CHAT_CONTEXTE_PROJET`
(`core/router.py`), `MOTEUR_PROVIDER_PLUGINS` (`core/llm/provider_registration.py`), all at `1`. Turning them off
is useful only for a diagnosis.

### 2.2 Model providers

A missing key simply disables the provider. The **Installation** screen lists 11 (free Gemini, paid Gemini,
DeepSeek, Anthropic, Mistral, Cohere, OpenRouter, xAI, MiniMax, DeepInfra, Zhipu); the engine instantiates more
(29 during the test, `core/llm_gateway.py`).

| Variable | Provider |
|---|---|
| `GEMINI_API_KEY`, `GEMINI_API_KEY_2` … `GEMINI_API_KEY_6` | Google Gemini (free tier); up to 6 keys in rotation; also used for the embeddings of the documentation search |
| `GEMINI_PAYANT_API_KEY` | Paid Gemini (GCP / Vertex) |
| `GEMINI_PROJECT_NAME` | Targeted GCP project. **The code carries a default that is useless to you** (`api/routes/apis_external.py:96`): set your project or leave the GCP feature unused |
| `GEMINI_MODEL` | Default Gemini model (remote worker, `deploy/worker_standalone.py`) |
| `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`, `MISTRAL_API_KEY`, `COHERE_API_KEY` | Providers of the same name |
| `CEREBRAS_API_KEY`, `CEREBRAS_PAYANT_API_KEY` | Cerebras (free / paid) |
| `OPENROUTER_API_KEY`, `XAI_API_KEY`, `MINIMAX_API_KEY`, `DEEPINFRA_API_KEY`, `ZHIPU_API_KEY` | Providers of the same name |
| `DASHSCOPE_API_KEY`, `DASHSCOPE_BASE_URL` | Alibaba DashScope (the default address is that of the international "Coding Plan") |
| `BAILIAN_CODING_PLAN_API_KEY` | Variant of the Alibaba plan (read by `core/llm_gateway.py:255`, absent from `.env.example`) |
| `GITHUB_TOKEN` | **No model use**: `.env.example` says GitHub Models was removed; used by `seed_github_models.py` |
| `XAI_MANAGEMENT_API_KEY`, `XAI_TEAM_ID` | Reading the xAI balance (`core/provider_balances.py`) |
| `MOTEUR_MAX_OUTPUT_TOKENS` | Output cap when the catalog gives none (`.env.example`: 8192) |
| `EUR_USD_RATE` | Rate to convert prices to euros (**1.08** by default, `core/pricing.py`) |

> **Local providers (Ollama, llama.cpp, LM Studio).** Their addresses are **fixed in the code**
> (`core/openai_compat_provider.py:822-853`: Ollama on `127.0.0.1:11434`, a llama.cpp server on `127.0.0.1:8080`,
> plus entries for a machine on the local network). The `ollama_local` provider expects precise model names
> (`core/llm_gateway.py:541-546`). The `local_llm` section of `config.example.json` and that of `STRATEGIES.md` are
> read by no code in the repository (search by name). For another model or another local address, you have to modify
> these files: not tried in this guide. `LM_STUDIO_URL` (default `http://192.168.1.10:1234`,
> `core/ha_fuzzy_matcher.py:85`) is used only for the fuzzy matching of home automation commands.

### 2.3 Home Assistant

| Variable | Role | Default |
|---|---|---|
| `HASS_URL` (fallback `HA_URL`) | Address of Home Assistant | **none** (`core/ha_url.py`): without it, no call is sent |
| `HASS_TOKEN` (fallback `HA_TOKEN`) | Long-lived access token | none |
| `HA_VERIFY_TLS` | Verify the HTTPS certificate | `true` (`api/routes/ha.py`) |
| `HA_CA_BUNDLE` | `.pem` file of the certificate authority (preferable to turning verification off) | empty |
| `HA_TLS_SERVER_HOSTNAME` | Name to verify in the certificate when `HASS_URL` is an IP | empty (`core/ha_tls.py`) |
| `HA_WEATHER_ENTITY` | Weather entity read without a model | `weather.home` (`services/ha_weather_query.py:44`) |
| `VOCAL_SATELLITE_ENTITY` | Entity of the voice satellite targeted for announcements | a value specific to one particular display (`core/vocal_jobs.py:129`): set it if you use this feature |

Full procedure: [04 Home and voice](04_home_and_voice.md).

### 2.4 Execution limits

| Variable | Role | Default |
|---|---|---|
| `MOTEUR_EXECUTION_MAX_CONCURRENCY` | Simultaneous heavy executions (light paths do not use any) | `3` (`core/app_state.py`) |
| `MOTEUR_EXECUTION_STALE_TTL_SECONDS` | Age from which an orphaned execution is purged | `1800` |
| `MOTEUR_PLANNER_TIMEOUT_S` | Planner window | computed (wide enough for the cascade to switch over; a value that is too low is refused, `core/fenetres_execution.py`) |
| `MOTEUR_SESSION_FOND_TIMEOUT_S` | Maximum duration of a background session | `900` s |
| `MOTEUR_DREAMCODER_TACHE_TIMEOUT_S` | Maximum duration of an autonomous code task | `600` s |
| `MOTEUR_DAG_TASK_TIMEOUT_S` | Base timeout of a plan step | see `core/dag_runner.py:121` |

### 2.5 Background loops

| Variable | Role | Default |
|---|---|---|
| `MODEL_HEALTH_PROBE` | Model health probe (**real generations**) | `true` |
| `MODEL_HEALTH_AUTO_DISABLE` | `false`: observation only, no model switched off | `true` |
| `MODEL_HEALTH_INTERVAL_SECONDS` | Interval between cycles | `3600` |
| `QUOTA_ALERTE_ENABLED` | Quota and balance alert | `true` (`core/quota_alert.py`) |
| `QUOTA_ALERTE_OBSERVATION` | `true`: logs without notifying | `false` |
| `QUOTA_REFRESH_INTERVAL_SECONDS` | Quota reading | `300` |
| `ZOMBIE_SWEEP_INTERVAL_SECONDS` | Zombie session sweep | `600` |
| `MOTEUR_VEILLE_ENABLED` | Price watch | `1` (`core/veille_scheduler.py`) |
| `MOTEUR_VEILLE_APPLIQUER` | `0`: report only, without rewriting `pricing_strategy.json` | `1` |
| `MOTEUR_VEILLE_INTERVAL_H` | Period (hours) | `24` |
| `TASK_DISCOVERY_ENABLED` | Task discovery (TODOs, board) | `1` (`gui_server.py:263`) |
| `TASK_DISCOVERY_MAX_INJECTIONS` | Tasks added at most per pass | `10` |
| `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_CLIENT_ID` | MQTT monitoring (failover switch on the Home Assistant side); **started only if `MQTT_HOST` is defined** | port `1883` |

Detail of the cost and of how to turn off each loop: [05 Operate, § 6](05_operate.md).

### 2.6 Vigie (alerts on the phone)

Off by default. It reads Gmail, forum threads, GitHub and YouTube, sorts into urgent / to read / noise, and notifies the
phone through Home Assistant. **Read only**: nothing is written, marked or deleted. The content of emails is
sorted only by a **local** model; without a local model, sorting is done by rules, never by the cloud
(`.env.example`). Test with no effect: `python -m core.vigie --une-passe --a-blanc`.

| Variable | Role | Default (`core/vigie/reglages.py`) |
|---|---|---|
| `MOTEUR_VIGIE_ENABLED` | Switch | `0` |
| `MOTEUR_VIGIE_NOTIFY_SERVICE` | `notify` service of the Home Assistant mobile app | empty; **required** to start |
| `MOTEUR_VIGIE_INTERVALLE_MIN` | Minutes between two readings | `10` (minimum 2) |
| `MOTEUR_VIGIE_GMAIL`, `MOTEUR_VIGIE_GMAIL_REQUETE` | Gmail source (token `google_token.json`) and query | `1`, `in:inbox newer_than:2d` |
| `MOTEUR_VIGIE_FORUMS`, `MOTEUR_VIGIE_FORUMS_JOURS` | Followed threads (`base\|id\|pseudo`, where `pseudo` is the username; comma-separated) and follow-up of the threads where you wrote | empty, `90` |
| `MOTEUR_VIGIE_GITHUB_PSEUDO`, `_DISCUSSIONS`, `_JOURS` | GitHub source | empty, empty, `60` |
| `MOTEUR_VIGIE_YOUTUBE_CHAINE` | YouTube source (channel identifier) | empty |
| `MOTEUR_VIGIE_SILENCE` | Quiet hours with no alerts, local time (`22-7`, `0` = none) | `22-7` |
| `MOTEUR_VIGIE_RESUME_HEURE` | Hour of the daily summary (empty or `non` = none) | `8` |
| `MOTEUR_VIGIE_LLM`, `_LLM_MODELES`, `_LLM_TIMEOUT_S` | Email sorting by local model, list of models, timeout | `1`, list in the code, `90` |
| `MOTEUR_VIGIE_GMAIL_ALERTER`, `_GMAIL_IGNORER` | Senders always urgent / always ignored | empty |
| `MOTEUR_VIGIE_GROUPE_SEUIL`, `_PANNE_SEUIL` | Grouped alerts beyond N; "source down" alert after N failures | `3`, `3` |

Optional tokens: `MOTEUR_VIGIE_GITHUB_TOKEN` (otherwise `GITHUB_PERSONAL_ACCESS_TOKEN`) and `MOTEUR_VIGIE_YOUTUBE_CLE`
(otherwise `CLOUD_API_KEY`).

### 2.7 Autonomous code cycle (DreamCoder) and git

| Variable | Role | Default |
|---|---|---|
| `MOTEUR_DREAMCODER_REPO_PATHS` | **Dedicated** clones where the cycle creates its branches (separated by `;` on Windows, `:` on Linux). Takes precedence over `config.json`. | empty → **the cycle refuses to work** rather than use the engine's repository (`.env.example`) |
| `MOTEUR_GITHUB_PUSH_TOKEN` | Push the branches **and open PRs** | empty → PRs are not opened |
| `MOTEUR_PROJECT_<ID>_PATH` | Root of a project known to the engine (**Projets & git** (Projects and git) screen) | empty |
| `MOTEUR_REJEU_ECHEC_S` | Delay before replaying a failed task | `21600` (6 h, `agents/dreamer_agent.py:1179`) |
| `MOTEUR_WORKTREES_ROOT` | Folder of the worktrees | — |
| `MOTEUR_TOOLMAKER_PAT`, `MOTEUR_TOOLMAKER_REPO`, `MOTEUR_TOOLMAKER_TIMEOUT_S` | Remote sandbox of the ToolMaker (token limited to one repository) | empty PAT → any candidate tool is refused; timeout `300` |

> **A discrepancy to know.** `MOTEUR_TOOLMAKER_REPO` is `Axellum/vromvrom-engine` in `.env.example`, but the code
> (`core/toolmaker_sandbox.py:62`) has another default repository. If you use the ToolMaker, set it.

### 2.8 Observability, miscellaneous

| Variable | Role | Default |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | Langfuse traces; without both keys, the bridge is inactive | host: an example private address (`core/langfuse_bridge.py:59`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_EXPORTER_GCP`, `OTEL_SERVICE_NAME` | OpenTelemetry export | empty; service name `moteur-agents` (`core/otel.py`) |
| `RAG_BACKEND` | `LOCAL` or a Vertex index (`VERTEX_DATA_STORE_ID`, `GOOGLE_CLOUD_PROJECT`) | `LOCAL` |
| `GOOGLE_APPLICATION_CREDENTIALS` | Google credentials for GCP | — |
| `CLOUD_API_KEY` | Google key (YouTube, cloud speech recognition) | empty |
| `WEATHER_API_KEY` | Only reported present or absent by `GET /api/system/tokens-status` (`gui_server.py:1287`); no other use found | — |
| `TWILIO_*`, `META_WHATSAPP_*`, `CALLMEBOT_APIKEY`, `WHATSAPP_*` | WhatsApp channel (public webhooks) | empty; not tried |
| `SSH_USER`, `SSH_PASSWORD` | SSH diagnosis of the sentinel | **see the warning in § 4** |

---

## 3. `config.json`

`config.example.json` is the reference. The sections below are those that the code **reads**. The keys of the example file
that no code reads are in § 6.

### 3.1 Models and tiers

| Key | Role | Default if absent |
|---|---|---|
| `planner_model`, `executor_model`, `reviewer_model`, `ha_model` | Model (or tier) of each agent | `fort`, `automatique`, `moyen`, `moyen` (`core/factory.py`); example: `gemini-3.5-flash-free`, `gemini-3.5-flash-free`, `leger`, `moyen` |
| `antigravity_model` | Agent specialized in code and architecture | `fort` |
| `prompt_engineer_model` | Instruction rewriting | `fort` (`api/routes/prompt_tools.py:40`) |
| `tiers` | Models of `leger` / `moyen` / `fort` / `automatique` (light / medium / strong / automatic) | **Safety net**: the catalog wins (`automatique` = all active models) |
| `routing_policy.excluded_models` | Models removed from routing, all tiers | list from the example |
| `routing_policy.fast_path_providers` | Order of the fast chat | code fallback: an optional local provider, `gemini-3.5-flash-free`, `deepseek-chat`, `gemini-2.5-flash-free` (`services/pipeline_service.py:83`) |
| `routing_policy.tab_llm_providers` | Dedicated list for requests with `source.type = tab5` | `["gpt-oss-120b"]` |
| `elo_enabled` | Ranking by Elo score | `true` (`core/router.py:197`) |
| `provider_timeouts` | Timeouts (connection, read) per OpenAI-compatible provider | sections of the example |
| `json_prompt_only_providers`, `latency_penalty_exempt_models` | Fine settings of the cascade | — (`core/llm_gateway.py:323`, `:155`) |
| `cascade_quality_escalation` | Retries at the `fort` tier if the quality score is under the threshold | `enabled` true, threshold 6.0 (example), domains `code_generation`, `analysis`, `sysadmin` |
| `semantic_cache` | Semantic cache of answers | **off**, threshold 0.95 (`core/semantic_cache.py:167`) |
| `custom_agents` | Agents created in the **Agents** screen | empty list |

### 3.2 Budgets and safeguards

| Key | Role | Default |
|---|---|---|
| `max_session_tokens` | Maximum tokens per execution (0 = disabled) | `500000` (`core/execution_budget.py:63`) |
| `max_execution_seconds` | Maximum duration per execution (0 = disabled) | `0` |
| `max_execution_cost_usd` | Maximum cost per execution in dollars (0 = disabled) | `0` |
| `auto_review` | Automatic review after a plan (one more model call) | `true` (`core/engine.py:866`) |
| `hitl.seuil_approbation` | `low`, `medium`, `high` or `critical`: risk from which approval is requested | `high` |
| `hitl.interactive_auto_approve` | Automatically approve risky plans in an interactive session | `false`. **Leave `false`** |
| `budget_guard.*` | `gemini_free_tokens_per_hour`, `deepseek_free_requests_per_day`, `daily_budget_usd`, `auditor_weekly_budget_usd`, `total_daily_budget_usd` | example: 1,000,000, 200, 0.5, 1.0, 1.0 (`core/budget_guard.py:262`) |

The interactive path is not subject to the daily budgets of `budget_guard` (they cap the autonomous cycle); it is subject to
`max_session_tokens`. Check: real spending is visible in **Surveiller** (Monitor) **→ Observabilité** (Observability).

### 3.3 Background agents (`persistent_agents`)

| Key | Role | Code default / example |
|---|---|---|
| `daemon_enabled` | Sentinel | `true` / `true` |
| `daemon_interval_minutes` | Pace (minutes) | 10 / 10 |
| `daemon_model` | Tier or model | `leger` |
| `dreamer_enabled` | Dreamer + DreamCoder | `true` / `true` |
| `dreamer_schedule` | Time of the memory cycle | `02:00` (example) |
| `dreamer_idle_trigger_hours` | Also triggers the cycle after N hours of inactivity | **1** (code) / **3** (example) |
| `dreamcoder_max_cycle_minutes` | Time budget of a code cycle | 30 |
| `dreamcoder_repo_path`, `dreamcoder_autoriser_depot_courant` | Working repository of the cycle, and permission to use the current repository | empty, `false` |
| `auditor_enabled` | Auditor | **`false` (code, `api/routes/guide.py`) / `true` (example)** |
| `auditor_trigger_mode`, `_trigger_task_count`, `_schedule_weekday`, `_schedule_time` | Trigger: `task_count` (after N completed tasks), `schedule` (weekly slot) or `both` | `task_count`, 10, 6 (Sunday), `03:00` |
| `auditor_scopes`, `_model_tier`, `_escalation_tier`, `_confidence_threshold`, `_dedup_similarity_threshold`, `_max_corpus_chars` | Scope and quality of the audit | example: `core, agents, api, memory, tools`; `moyen`; `fort`; 6.0; 0.75; 150000 |
| `routines_model` | Tier of the routines | `fort` (example) |

The three `*_enabled` switches of `persistent_agents` can also be changed in **Avancé** (Advanced) **→ Agents autonomes** (Autonomous agents). They are
re-read at every turn: **no restart**.

---

## 4. Warning: the sentinel and SSH

The sentinel (`daemon_enabled`, on in the example) does five checks per cycle (`core/daemon_loop.py`): git (uncommitted
files of the engine's repository), Home Assistant health, calendar preloading, memory health and **Home Assistant
overload**. This last check tries, every 6 hours or when the processor exceeds 80% for two cycles in a row,
an **SSH diagnosis to an address fixed in the code** (`192.168.1.10`, line 361) with the user `SSH_USER`
(default value fixed in the code, line 360, which is not yours), and can restart containers there (`sudo docker …`, lines 422-480). This machine is the one
of the original installation. On your side:

- without an SSH key or `SSH_PASSWORD`, the check fails and reports it once; nothing is executed;
- **do not define `SSH_PASSWORD`**, and if you do not have Home Assistant, set `persistent_agents.daemon_enabled` to `false`;
- if a machine on your network has the address `192.168.1.10` and an SSH account reachable with your keys, the sentinel could
  connect to it: disable it in that case.

This behavior was not triggered during the tests (no SSH access configured); it is read in the code.

---

## 5. Which setting for which goal?

| I want to… | I set… | Expected effect |
|---|---|---|
| Spend nothing | No paid key in the `.env`; `MODEL_HEALTH_PROBE=false`; `persistent_agents.dreamer_enabled` and `auditor_enabled` set to `false` | Only free models (free Gemini, etc.) are called |
| Keep everything on my machine | `MOTEUR_PRIVACY_LEVEL=local_only` and a reachable local provider | Explicit failure rather than a cloud call |
| Open the interface from another machine | Launch with `--host 0.0.0.0` (the default) and `MOTEUR_CORS_ORIGINS` | See [05 Operate, § 5](05_operate.md) |
| Change the model of an agent | `config.json` (`planner_model`…) or the **Agents** screen | Next execution |
| Change the models of a tier | **Modèles & routage** (Models and routing) screen | Immediate |
| Reduce the cost of a plan | `auto_review=false`, lower `max_session_tokens`, `cascade_quality_escalation.enabled=false` | Fewer model calls; lower quality |
| Cap the spending of an execution | `max_execution_cost_usd` (for example `0.10`) | The execution stops at the cap |

---

## 6. Keys of `config.example.json` that the code does not read

Search for the name of each key in the Python code of the repository (excluding tests and interface): no read found for
the following keys. They have **no observed effect**; do not count on them.

| Key | Note |
|---|---|
| `timeout_global_s`, `max_retries`, `max_dag_stages` | Timeouts and retries are set elsewhere (variables of § 2.4, constants in the code) |
| `fast_path_enabled`, `fast_path_cache_ttl_s` | The chat shortcut is always on; its 15 s cache is a constant |
| `context_compression_threshold`, `max_tool_output_chars` | — |
| `hitl.enabled`, `hitl.timeout_s` | Only `seuil_approbation` and `interactive_auto_approve` are read (`core/engine.py:1159-1175`) |
| `swarm` | Remote workers are read from a dedicated file (`core/worker_registry.py`), not from `config.json` |
| `visual_qa` | — |
| `local_llm` | See the box in § 2.2 |

Another discrepancy: `config.example.json` contains the key `provider_timeouts` three times (lines 158, 163 and 168). In JSON, the
last one wins; the three are identical.

---

## 7. Data files

| File | Role | Template |
|---|---|---|
| `ha_commands.json` | Catalog of home automation phrases → Home Assistant services. Read when the router is created: restart after a change. | `ha_commands.json.example` (example entities `light.living_room`, `light.bedroom`…) |
| `pricing_strategy.json` | Price list per model. Single source for the cost computation (`core/pricing.py`); a fallback price list exists if the file is missing. Rewritten by the price watch if `MOTEUR_VEILLE_APPLIQUER=1`, with a backup in `backups_prod/`. | `pricing_strategy.json.example` |
| `mcp_config.json` | Third-party MCP servers that the engine can use (never a secret in it) | `mcp_config.json.example` |
| `mcp_activation_rules.json` | Which MCP servers to start: `default` list + keyword rules (`keywords` → `activate`). **Absent, no server is started.** | — |
| `agents_workflows.json` | Agent chains of the **Workflows** editor | provided |
| `projects.example.json` | Template of the project registry | — |
| `prompts/agents/*.md` | Instruction of each agent | provided |

For the format of `ha_commands.json` and the matching rules: [04 Home and voice, § 3](04_home_and_voice.md).
