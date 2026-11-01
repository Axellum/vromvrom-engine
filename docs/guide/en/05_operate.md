# Keep the engine running

> **In one sentence.** Check that it is well (`/healthz`, **Accueil** (Home) screen), back up its databases, update it without
> losing your state files, troubleshoot, secure it, and know **what consumes tokens on its own** so you can turn it off.

Related pages: [01 Install](01_install.md) · [02 Understand](02_understand.md) · [03 Settings](03_settings.md) ·
[04 Home and voice](04_home_and_voice.md).

**Tried for this guide** (Windows 11, Python 3.13, clean copy, no provider key): `/healthz`, `/version`,
`/api/guide/overview`, `/api/setup/diagnostics`, `GET` and `POST /api/sauvegardes`, `POST /api/auth/ticket`, `GET /api/keys`.
**Not tried**: the command line `python -m core.sauvegarde`, restore, update, systemd units, any
use with a provider key.

---

## 1. Check that everything is fine

### 1.1 In thirty seconds

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8000/version
```

`/healthz` returns `ok`, `service`, `status` (`idle` at rest) and, if the repository is a git clone, `commit`. `/version` returns `version`,
`git_hash`, `build_date`, `engine`, `python`. These two routes are **public**. A `/healthz` at 200 proves that the process
answers; it **does not prove** that the models answer.

### 1.2 The detailed verdict

| Where | What you read there |
|---|---|
| **Découvrir** (Discover) **→ Accueil** (Home) screen | The verdict in one sentence, the key figures, the alerts |
| **Avancé** (Advanced) **→ Installation** screen | Checks by group: authentication, databases, configuration, providers, Home Assistant (`GET /api/setup/diagnostics`) |
| **Surveiller** (Monitor) **→ Opérations** (Operations) screen | Version, circuit breakers, quotas and balances |
| `GET /api/guide/overview` | Engine (uptime), providers (usable, down, untested), models, usage over 24 h / 7 d / 30 d, **state of each background loop**, backups, alerts |
| `GET /api/observability/circuit-breakers` | Which models are set aside |
| `GET /api/models-health` | State of the health probe per model |

Example, observed on an installation without a provider key: `fournisseurs.utilisables = 0` (usable providers), `modeles.actifs = 97` (active models), an
alert « Home Assistant n'est pas configuré (jeton HASS_TOKEN absent) » (Home Assistant is not configured (HASS_TOKEN token missing)), and the list of the 14 background loops with
`active: true/false` and the name of the setting that drives them.

In `/api/guide/overview`, usage separates `appels_reels_7j` (real calls over 7 days) and `appels_sondes_7j` (probe calls over 7 days): the **health probes** are calls
counted like the others. Read the usage statistics with this separation in mind.

### 1.3 The log

The engine writes its log to **standard output** (`logging.basicConfig` in `gui_server.py:57`), not to a file.
Where to find it depends on how you launch it:

| Launch | Log |
|---|---|
| Console | In the console |
| `deploy/lancer_moteur_silencieux.bat` | `moteur_pc.log` at the root of the repository |
| systemd (example in [01 § 9](01_install.md)) | `journalctl --user -u moteur.service` |
| Docker | `docker compose logs -f` (rotation `10m` × 3 files in `docker-compose.yml`) |

An isolated `ERROR` coming from a provider (403, 429, timeout) is common with free quotas. A `Traceback` or a
`CRITICAL` is never normal. The `Fichier introuvable` ("File not found") messages about `contexte_ia/` at startup are normal.

---

## 2. Back up

### 2.1 What is backed up

`core/sauvegarde.py` copies, with the SQLite hot-copy mechanism (consistent even with the engine running):

| Category | Files |
|---|---|
| Databases | `moteur_runtime.db` (sessions, costs, voice audit), `memory.db` (memory), `models_registry.db` (catalog) |
| Settings | `config.json`, `ha_commands.json`, `mcp_activation_rules.json`, `agents_workflows.json` |
| Folder | `prompts/agents/` (agent instructions) |

**Never**: the `.env`, tokens, keys, certificates. **Also not**: `chroma_db/` (can be rebuilt with
`python populate_embeddings.py`), `pricing_strategy.json`, `mcp_config.json`, `google_token.json`. These files are for you to
back up (the `.env` in a password manager, for example).

Each backup is a folder `AAAAMMJJ_hhmmss/` (that is, YYYYMMDD_hhmmss; suffix `_N` if two fall on the same second) containing the copies and a
`manifeste.json` (sha256 sum of each file and result of the SQLite integrity check). Tried: a backup of
14 files, 617 KB, integrity `ok` for the three databases.

### 2.2 Where and how many

| Setting | Default | Role |
|---|---|---|
| `MOTEUR_SAUVEGARDE_DIR` | `<repo>/sauvegardes/` (ignored by git) | Destination folder. **Point it to another disk** (external disk, mounted share): a backup on the disk that fails is useless. |
| `MOTEUR_SAUVEGARDE_GARDER` | `14` | Number of backups kept. A value below 1 or unreadable = 14. |

Retention is applied **only after a healthy backup**: a damaged copy does not make the old ones disappear. A
lock prevents two simultaneous backups (**409** response from the API if one is already running).

### 2.3 Run, list, verify

Command line (from the root of the repository):

```powershell
.\.venv\Scripts\python.exe -m core.sauvegarde                    # creates a backup
.\.venv\Scripts\python.exe -m core.sauvegarde --lister           # lists the backups
.\.venv\Scripts\python.exe -m core.sauvegarde --verifier 20261101_184734    # re-checks sha256 + integrity
```

Bash: the same with `.venv/bin/python`. Exit codes: **0** everything is healthy, **1** anomaly found (integrity or
fingerprint), **2** backup impossible. This exit code lets systemd or a scheduled task report a failure.

API (same operations, tried): `GET /api/sauvegardes` (list, last backup, retention, `valide` (valid) per entry) and
`POST /api/sauvegardes` (creates; **201** on success, **409** if a backup is running, **503** if impossible).

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/sauvegardes -Headers $h
```

**Surveiller → Sauvegardes** (Backups) screen: what is protected, the last backup, a button to create one. The interface
raises an alert when the last backup is more than **48 hours** old.

### 2.4 Automate

See [01 § 11](01_install.md): systemd timer provided (04:30 every day, with catch-up if the machine was off); to
adapt outside the original installation. On Windows, create a scheduled task that runs the command above.

> The **Dreamer** (memory consolidation) rewrites `memory.db`. With its background agents active, make
> a backup before experimenting with the memory settings.

### 2.5 Restore

There is **no "restore" button**: the **Sauvegardes** screen gives the procedure
(`ihm-v2/src/views/Sauvegardes.tsx`). Not tried in this guide. In short:

1. **Choose** the backup and **re-check** it (`--verifier`).
2. **Stop the engine**, so that nothing writes to the databases anymore.
3. **Set the current state aside**: copy the current files to a dated folder, without deleting anything. This is your way back.
4. **Copy** each file of the backup in place of the original, in the engine's folder, **except `manifeste.json`**. If
   a replaced database has `-wal` or `-shm` files next to it, set them aside with the old state: they
   belong to the old file.
5. **Restart**, open **Accueil**: the verdict must be green, and the memory and settings must be those of the chosen date.
6. Delete the state set aside only after this check. The `.env` is not touched.

---

## 3. Update

The repository does not provide an update procedure for an ordinary installation (the script `deploy/deploy_deck.sh` is a
deployment by `git reset --hard` specific to the original installation: **do not use it** at home). Usual procedure, **not
tried**:

```powershell
# 1. Back up (§ 2) and copy your .env
.\.venv\Scripts\python.exe -m core.sauvegarde
# 2. Get the code
git status
git pull
# 3. Dependencies, if requirements.txt changed
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# 4. Interface, if ihm-v2/ changed
cd ihm-v2; npm install; npm run build; cd ..
# 5. Restart the engine
```

Your state files (`.env`, `config.json`, `ha_commands.json`, `pricing_strategy.json`, the `*.db` databases, `sauvegardes/`) are
in `.gitignore`: `git pull` does not touch them. If there is a conflict on a **tracked** file that you modified,
resolve it before continuing. After the update, compare your `.env` with `.env.example` and your `config.json` with
`config.example.json`: new variables may have appeared. Then check with the **Installation** screen and
`/version`.

---

## 4. Troubleshoot

| Symptom | Probable cause | Check | Remedy |
|---|---|---|---|
| 503 on all `/api` routes | `MOTEUR_API_KEY` missing | `.env` | Set it, restart |
| 401 | Wrong key, or a shell variable masking the `.env` | `echo $env:MOTEUR_API_KEY` | Align them, restart |
| Empty answer, or « Désolé, je n'ai pas su répondre. » (Sorry, I could not answer.) | No model could answer | Installation → « Fournisseurs » (Providers); `GET /api/models-health`; `GET /api/observability/circuit-breakers` | Set a key; re-enable models in **Modèles & routage** (Models and routing); wait for a circuit breaker to end (30 s to 30 min) |
| Many models "off" | The health probe switched them off (key refused, quota, credit exhausted) | `GET /api/models-health`; cause readable per model | Fix the cause; the probe re-enables by itself the models that come back. To turn it off: `MODEL_HEALTH_AUTO_DISABLE=false` (observation) or `MODEL_HEALTH_PROBE=false` |
| 429 / 403 errors from free providers | Quota exceeded, key refused | Log; **Opérations → quotas** | Add Gemini keys (up to 6), wait, use another provider |
| « config.json Illisible » (config.json unreadable) | File missing or invalid JSON | Installation | Copy `config.example.json`; validate the JSON |
| « Home Assistant non configuré » (Home Assistant not configured) | `HASS_TOKEN` / `HASS_URL` missing | `GET /api/ha/health` | [04](04_home_and_voice.md) |
| `reachable: false` with a token | Address, port, certificate | `GET /api/ha/health` (`error` field) | `HASS_URL`, `HA_CA_BUNDLE`, `HA_VERIFY_TLS` |
| Poor documentation search | `chroma_db/` missing | Installation (`warn`) | `python populate_embeddings.py` |
| Disk filling up | `sauvegardes/`, `moteur_runtime.db`, log | `Get-ChildItem -Recurse` / `du -sh` | Lower `MOTEUR_SAUVEGARDE_GARDER`; backups on another disk |
| The code cycle opens no PR | No `MOTEUR_GITHUB_PUSH_TOKEN`, or no dedicated clones | `.env`; [03 § 2.7](03_settings.md) | See that section; otherwise it is the intended behavior |
| 409 on `POST /api/sauvegardes` | A backup is already running | `GET /api/sauvegardes` | Wait |
| The interface asks for the key in a loop | Stale key in the browser | Browser local storage | Enter it again (the interface offers to on 401 or 403) |

Do not trust a single "OK": `/healthz` answers even when all the models are down. Check the real effect (a
real answer, a Home Assistant state, a backup re-read).

---

## 5. Security

| Point | What the engine does | What you must do |
|---|---|---|
| API access | All `/api` routes require `Authorization: Bearer <MOTEUR_API_KEY>`. Without a configured key: **503**, never open access (`core/auth.py`). | A long, random key; do not put it in a repository. |
| Public routes | `/healthz`, `/version`, `/docs`, `/redoc`, `/openapi.json`, and the WhatsApp webhooks | Nothing to do, but know it: `/docs` describes the whole API. |
| SSE streams and WebSocket | A **single-use ephemeral ticket** (`POST /api/auth/ticket`, valid for **60 s**, observed) replaces the key in the URL | — |
| Network listening | `--host 0.0.0.0` by default: reachable from your whole local network | `--host 127.0.0.1` if a single machine uses it |
| Internet | The engine has no TLS of its own | **Do not expose it directly to the Internet.** If you must reach it from outside, put it behind an HTTPS reverse proxy or a VPN (general recommendation, not tried here). |
| CORS | Empty → open without cookies | `MOTEUR_CORS_ORIGINS` with your origins if you develop the interface |
| Key in the browser | The interface keeps the key in the **browser's local storage** (`ihm-v2/src/api/client.ts`) | Enter it only on a browser you trust |
| Secrets | `GET /api/keys` returns only booleans (observed). The `.env` is excluded from backups. The error messages of the health probe go through secret masking before being stored (`core/model_health_probe.py`, `core/log_redaction.py`); I did not check the other logs. | The `.env` is never committed; renew a key if it leaked. |
| Code execution | Plugins and ToolMaker **disabled** (`0`). The command tool has a path guard. File writing is confined to `MOTEUR_WORKSPACE_ROOT` (guard on by default). | Enable them only if you trust the code loaded. |
| Human approval | Plans at risk `high` or above wait for your agreement in an interactive session ([02 § 7](02_understand.md)) | Leave `hitl.interactive_auto_approve` at `false`. |
| Data outside the engine | See the two boxes below | — |

> **Sentinel and SSH.** If `daemon_enabled` is on, the sentinel periodically tries an SSH diagnosis to an address fixed in the
> code, with a default user that is not yours. Detail and precautions: [03 § 4](03_settings.md).

> **Scan of CLI tool conversations.** The engine reads `~/.claude/projects/` and
> `~/.gemini/antigravity-ide/brain/` of the account that launches it every ten minutes, with no switch. Detail: [02 § 8](02_understand.md).

**Privacy mode.** `MOTEUR_PRIVACY_LEVEL=local_only` forbids any cloud call and makes the call fail rather than
fall back to a remote provider. A reachable local provider is then needed ([03 § 2.2](03_settings.md)). Not tried.

**Voice privacy.** The audio goes to the speech recognition service configured in Home Assistant; the text
goes to the model providers for a conversation. Home commands and state questions do not leave
your network (apart from Home Assistant itself).

---

## 6. Costs: what consumes tokens on its own

A conversation or a task that **you** launch consumes what you launch. What follows concerns what runs **without you**.
Amounts are not given: they depend on your providers and were not measured for this guide ("—").

| Loop | Calls models? | On by default? | To turn it off |
|---|---|---|---|
| **Health probe** | **Yes**: one mini-generation per active model per hour (4 in parallel, 20 s at most each, `core/model_health_probe.py`). On a free tier, it **uses up your daily quota**. | **Yes** | `MODEL_HEALTH_PROBE=false` (`.env`) |
| **Dreamer** (memory) | Yes (`leger` tier), at the `dreamer_schedule` time and after inactivity | Yes with `config.example.json` | `persistent_agents.dreamer_enabled=false` |
| **DreamCoder** (autonomous code) | Yes, per backlog task, on branches | Inactive **as long as no dedicated clone is declared** (`MOTEUR_DREAMCODER_REPO_PATHS`) | Do not declare a clone; `dreamer_enabled=false` |
| **Auditor** | Yes (`moyen`, escalation `fort`); weekly budget `auditor_weekly_budget_usd` | Yes with the example; **no** without `config.json` | `persistent_agents.auditor_enabled=false` |
| Sentinel | No (code comment: “no LLM call”, `core/daemon_loop.py`) | Yes | `daemon_enabled=false` |
| Price watch | No (calls OpenRouter and LiteLLM, **rewrites `pricing_strategy.json`** by default) | Yes | `MOTEUR_VEILLE_ENABLED=0`; or `MOTEUR_VEILLE_APPLIQUER=0` for a report only |
| Task discovery | No (adds at most 10 tasks per hour to the backlog) | Yes | `TASK_DISCOVERY_ENABLED=0` |
| Quota alert | No | Yes | `QUOTA_ALERTE_ENABLED=false` |
| Vigie | **Local** model only (never the cloud for the content of emails) | No | `MOTEUR_VIGIE_ENABLED=0` |
| Reviewer after a plan | Yes: one more call per execution | Yes | `auto_review=false` |
| Semantic cache | — | No | — |

**Spending safeguards** (`config.json` → `budget_guard`, example): `daily_budget_usd` 0.5 and `total_daily_budget_usd` 1.0 for the
autonomous cycle, `auditor_weekly_budget_usd` 1.0; a failing provider is paused for 30 minutes. Per execution:
`max_session_tokens` (500,000 by default), `max_execution_cost_usd` and `max_execution_seconds` (disabled at 0). These caps
have not been tested by a real overrun.

### "Zero autonomous spend" recipe

In the `.env`, then restart:

```ini
MODEL_HEALTH_PROBE=false
TASK_DISCOVERY_ENABLED=0
QUOTA_ALERTE_ENABLED=false
MOTEUR_VEILLE_ENABLED=0
```

In `config.json` (re-read live):

```json
"persistent_agents": { "daemon_enabled": false, "dreamer_enabled": false, "auditor_enabled": false }
```

Note: change these three values **inside the existing `persistent_agents` block** of your `config.json` rather than replacing it
with these three lines (the block contains other settings, [03 § 3.3](03_settings.md)). Through the API,
`POST /api/persistent-agents/config` merges; `POST /api/config` would replace the whole section.

**Check that it is off**: `GET /api/guide/overview` → the `boucles` (loops) list gives `active` for each (observed: a loop
turned off by its switch appears there with `active: false`, with the variable that drives it). The log lines
« Démarrage de la boucle… » ("Starting the loop…") appear **even for a loop that is turned off**: the loop is launched and then tests its switch at every
turn; only `active` is authoritative.

**See what you spend**: **Comptes & coûts** (Accounts and costs) and **Observabilité** (Observability) screens, `GET /api/guide/overview` (`usage`) and
`GET /api/metrics/cost-per-success`. The costs shown come from the `pricing_strategy.json` price list: check it if you use
a paid provider.

---

## 7. When something has changed behavior

1. `git log` and `git status`: what changed in the code or in the tracked files?
2. `/version`: is the process running the code you think? An engine started before a `git pull` does not have the new code.
3. Compare `config.json` with `config.example.json` (new keys) and the `.env` with `.env.example`.
4. Go back to a version that worked: git rollback of the code; for databases and settings, restore (§ 2.5).
5. Re-read the log from startup: the first error counts, not the last.

Back: [Guide README](README.md).
