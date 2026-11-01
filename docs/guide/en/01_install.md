# Install the AI Engine

> **In one sentence.** Clone the repository, run the install script, copy `config.example.json` to
> `config.json`, start `gui_server.py`, then check with `/healthz` and a first request. Allow about
> ten minutes, most of it spent downloading dependencies.

This page follows the order of a first installation. Every statement comes from the code of the repository (file cited) or from a
test done to write this guide (marked "tried"). What was not tried is called "not tried".

**Tests done for this guide**: clean copy of the repository, Windows 11, Python 3.13, real server start,
`/healthz`, `/version`, `/api/execute`, `/api/vocal/simulate`, `/api/sauvegardes` and `/api/setup/diagnostics`,
with Windows PowerShell 5.1 and PowerShell 7. **Not tried**: Linux, macOS, Docker, systemd units, the build of the
interface (the `ihm-v2/dist` folder was already built).

Related pages: [Guide README](README.md) · [02 Understand](02_understand.md) · [03 Settings](03_settings.md) ·
[05 Operate](05_operate.md).

---

## 1. What you need

| Item | Required? | Detail | Source |
|---|---|---|---|
| **Python 3.12 or later** | Yes | See the box below. | `README.md`, `.github/workflows/ci.yml` |
| **git** | To clone | Or download the repository archive. | — |
| **At least one model key** | For anything that is not a shortcut | A free Gemini key is enough (aistudio.google.com). Without a key, the engine starts, but no chat or task can call a model. | `.env.example`, `api/routes/setup.py` |
| **Node.js 20.19+ or 22.12+** | No | Only to build the web interface (`ihm-v2`). Without it, the engine serves the old `static/` interface. | `ihm-v2/package.json` (`engines`) |
| **Home Assistant** | No | For home automation and voice. See [04 Home and voice](04_home_and_voice.md). | — |
| **Docker** | No | Alternative to the local installation (§ 8). | `Dockerfile`, `docker-compose.yml` |

> **Why Python 3.12?** The code contains at least one syntax reserved to Python 3.12
> (`agents/dreamer_agent.py:2594`: nested double quotes in an f-string), and this module is imported at
> startup (`gui_server.py:230`). The install script, the `Dockerfile` and continuous integration therefore
> require 3.12. Startup was tried on 3.13.

No special hardware is required: the engine does not need a graphics card. It runs on a PC, a mini PC or a small Linux machine
(a Raspberry Pi or a Steam Deck, for example). The memory it uses was not measured for this guide: "—".

---

## 2. Install with the script

The script does six things (header of `scripts/install.ps1` and `scripts/install.sh`):

1. checks the Python version (≥ 3.12, see the box);
2. creates a virtual environment `.venv/` and installs `requirements.txt`;
3. copies `.env.example` to `.env` **only if `.env` does not exist** (it never overwrites);
4. writes into this `.env` a random `MOTEUR_API_KEY` of 32 hexadecimal characters;
5. runs `seed_models_db.py` if `models_registry.db` does not exist (model catalog: 97 models after the seed,
   observed during the test);
6. prints the start command.

It is **idempotent**: running it again breaks nothing.

### Windows (PowerShell 5.1 or 7)

```powershell
git clone https://github.com/Axellum/vromvrom-engine.git
cd vromvrom-engine
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

`-ExecutionPolicy Bypass` applies to this command only: the default policy of Windows PowerShell 5.1 often blocks downloaded
scripts.

### Linux and macOS (bash)

```bash
git clone https://github.com/Axellum/vromvrom-engine.git
cd vromvrom-engine
bash scripts/install.sh
```

### What the script does NOT do

It does not copy `config.json`. The README asks for it at the "Engine config" step: **do it** (§ 3).

---

## 3. The files to create after the script

| File to create | Template | Required? | Effect of its absence (tried) |
|---|---|---|---|
| `config.json` | `config.example.json` | **Recommended** | The engine starts and answers, but the « Installation » screen shows an error « config.json Illisible » (config.json unreadable). The models of the tiers then come from the internal defaults (`core/llm_gateway.py`, function `load_config`). |
| `ha_commands.json` | `ha_commands.json.example` | Only for home automation | The shortcuts fall back to room tables built into the code (the voice simulation still recognizes « allume la lumière du salon », "turn on the living room light"). Replace them with your own entities: [04](04_home_and_voice.md). |
| `pricing_strategy.json` | `pricing_strategy.json.example` | No | `core/pricing.py` has a built-in fallback price list. |

```powershell
Copy-Item config.example.json config.json
Copy-Item ha_commands.json.example ha_commands.json
Copy-Item pricing_strategy.json.example pricing_strategy.json
```

On Linux and macOS, replace `Copy-Item` with `cp`.

> **Before you copy `config.example.json`.** It turns on three background agents (`daemon_enabled`,
> `dreamer_enabled`, `auditor_enabled` set to `true`). They can consume tokens as soon as a model key is
> present. Their switches and their cost are detailed in [05 Operate, § 6](05_operate.md) and
> [03 Settings, § 4](03_settings.md). If you prefer to start without them, set these three values to `false` in
> `config.json` before the first start.

---

## 4. Fill in the `.env`

Open the `.env` created by the script. The minimum:

| Variable | Role | Note |
|---|---|---|
| `MOTEUR_API_KEY` | Key that every client (interface, script, IDE) presents to call `/api`. | Already filled in by the script. **Without it, all `/api` routes answer 503** (`core/auth.py`). If you install by hand: `python -c "import secrets;print(secrets.token_urlsafe(32))"`. |
| `GEMINI_API_KEY` | Free model key. | Any provider key is enough; the list is in [03 Settings](03_settings.md). |
| `HASS_URL` | Address of Home Assistant. | **Warning**: `.env.example` contains an example value (`http://192.168.1.10:8123`) that the script copies. Replace it with your address, or **empty the line** if you do not have Home Assistant. |

Rules to remember: the `.env` is read **only once, at startup** (any change needs a restart); it must never be committed
(`.gitignore`); a variable already present in the process environment **wins over the `.env`** (`load_dotenv` without
override). Observed during the test: an old `MOTEUR_API_KEY` in the shell made the engine answer 401 despite the right key in the
`.env`.

---

## 5. Build the web interface (optional)

```powershell
cd ihm-v2
npm install
npm run build
cd ..
```

The same commands work in bash. `npm run build` runs `tsc -b && vite build` (`ihm-v2/package.json`) and
produces `ihm-v2/dist/`.

At startup, `gui_server.py` (lines ~1291-1300) serves the React interface on `/` (the old one on `/v1`) if `ihm-v2/dist`
exists; otherwise it logs a warning and serves the old `static/` interface on `/`. To develop with hot
reload (`npm run dev`, port 5173, `MOTEUR_CORS_ORIGINS=http://localhost:5173` in the `.env`):
[`ihm-v2/README.md`](../../../ihm-v2/README.md) (French only).

---

## 6. First start

```text
.\.venv\Scripts\python.exe gui_server.py      (Windows, PowerShell)
.venv/bin/python gui_server.py                (Linux and macOS)
```

Options (`gui_server.py`, `__main__` block):

| Option | Default | Role |
|---|---|---|
| `--host` | `0.0.0.0` | **Listens on all interfaces**: reachable from the local network. For use on a single machine: `--host 127.0.0.1` (and refuse the Windows firewall prompt if it appears). |
| `--port` | variable `PORT`, otherwise `8000` | If the port is taken, choose another (tried: port 8043 was refused by Windows, 8361 worked). |
| `--reload` | off | Hot reload (development). |

During the test, the server answered in under 15 seconds. The log shows « Démarrage complet » ("Startup complete",
`gui_server.py:323`) once the background loops have started. The « Fichier introuvable » ("File not found") messages about a
`contexte_ia/` folder are **normal**: it is an optional documentation folder (`memory/context_loader.py`) that is absent from the
public repository.

---

## 7. Check that it works

### 7.1 The liveness probe (no key)

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz     # bash: curl http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8000/version     # bash: curl http://127.0.0.1:8000/version
```

Responses observed:

```json
{"ok":true,"service":"tab5-engine","status":"idle"}
{"version":"12.1.0","git_hash":"unknown","build_date":"unknown","engine":"moteur_agents","python":"3.13.14"}
```

`/healthz` adds a `commit` field if the repository is a git clone. `/healthz` and `/version` are the two useful public routes;
every `/api/...` route requires the key. `"service":"tab5-engine"` is the internal name of the engine.

### 7.2 The key

Without an `Authorization` header, `/api/status` answers **401** « Authentification requise » (Authentication required). With a wrong key:
**401** « Token invalide » (Invalid token). If `MOTEUR_API_KEY` is not defined on the server side: **503**.

### 7.3 A first request, without a model provider

The question about the time is handled by a rule, without a model: it proves that the engine answers **without a provider
key**.

Windows PowerShell (5.1 or 7):

```powershell
# Read the key from the .env
$cle = ((Get-Content .env | Where-Object { $_ -match '^MOTEUR_API_KEY=' }) -replace '^MOTEUR_API_KEY=','').Trim()
$h = @{ Authorization = "Bearer $cle" }

$corps = @{ user_prompt = "Quelle heure est-il ?"; source = @{ type = "web"; mode = "chat" } } | ConvertTo-Json
$r = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/execute -Headers $h `
     -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($corps))
$r.response
```

Linux and macOS:

```bash
CLE=$(grep '^MOTEUR_API_KEY=' .env | cut -d= -f2-)
curl -s -X POST http://127.0.0.1:8000/api/execute \
  -H "Authorization: Bearer $CLE" -H "Content-Type: application/json" \
  -d '{"user_prompt":"Quelle heure est-il ?","source":{"type":"web","mode":"chat"}}'
```

(The prompt « Quelle heure est-il ? » means "What time is it?". It stays in French because the engine's voice matching is
French.)

Response observed (PowerShell): `Il est 18 heures 45.` ("It is 18:45."); the full JSON contains `status: "completed"`,
`session_id`, `response`, `history` and `agents_used` (`datetime_query`, `vocal_host`).

> **UTF-8 on Windows PowerShell 5.1.** Tried: a text body containing an accented letter (« lumière ») is sent badly
> encoded and the engine answers `{"detail":"There was an error parsing the body"}`. With
> `[System.Text.Encoding]::UTF8.GetBytes(...)` and `charset=utf-8`, the same request succeeds (PowerShell 7 does not have
> this flaw).

> **The body format.** The required field is `user_prompt` (not `message`): tried, `{"message":"Bonjour"}`
> returns **422** « Field required ». Fields: [`docs/API_REFERENCE.md`](../../API_REFERENCE.md) and
> `api/routes/agents.py` (`ExecuteRequestBody`): `source.type`, `source.mode` (`ha`, `chat`, `files`, `default`),
> `tier`, `model`, `session_id`.

### 7.4 A request that calls a model

With at least one provider key in the `.env` (engine restarted), repeat the command of § 7.3 with another
`user_prompt` (for example « Explique en deux phrases ce qu'est un moteur multi-agents. », "Explain in two sentences what a multi-agent engine is."). **Not tried**: no provider key
was used in the tests. An error response, or « Désolé, je n'ai pas su répondre » ("Sorry, I could not answer"), means
that no model in the chat list answered: see [05 Operate, § 4](05_operate.md).

### 7.5 The control screen

Open `http://127.0.0.1:8000/` then the menu **Avancé** (Advanced) **→ Installation** (address `#/installation`). It calls
`GET /api/setup/diagnostics` (`api/routes/setup.py`) and sorts the checks into five groups: Authentification (Authentication), Bases de
données (Databases), Configuration, Fournisseurs de modèles (Model providers), Home Assistant. On a clean installation without a provider key
(tried, after the copy of § 3), everything is `ok` or `info` except: a `warn` "chroma_db" missing (lexical search
only), a `warn` as long as `HASS_TOKEN` is not defined, and an **`error`** « Aucune clé de provider LLM » (No LLM provider key) as long as
you do not set one. The `summary.operational` result stays `false` while there is an error: this is expected.

To build the documentation search index (`chroma_db`): `python populate_embeddings.py`. It uses
a Gemini key to compute the embeddings (`.env.example`). It is optional.

---

## 8. Docker

The repository provides a `Dockerfile` and a `docker-compose.yml`. **Not tried** in this guide; what follows comes from
reading the files.

```bash
cp .env.example .env        # then edit: MOTEUR_API_KEY and a model key
docker compose up -d --build
docker compose logs -f
```

Points to know:

- `docker-compose.yml` reads a `.env` **that you must create yourself** (`env_file`), mounts the whole repository in
  `/app` and publishes port `8000`.
- `docker-entrypoint.sh` copies `config.example.json` to `config.json` and `.env.example` to `.env` if they are missing, then
  runs `touch` on `memory.db` and `models_registry.db`. **`models_registry.db` is therefore empty, not populated**:
  run `docker compose exec vromvrom-engine python seed_models_db.py` once.
- **`MOTEUR_API_KEY` is empty in `.env.example`** and nothing generates it in Docker: the API answers 503 until you
  set it.
- The image does not run `npm run build`: the React interface is served only if `ihm-v2/dist` exists on the host
  (the repository is mounted in the container). Otherwise the old interface is served.
- The image starts from `python:3.12-slim`. (The `version: '3.8'` key of `docker-compose.yml` is obsolete: at most
  a warning.)

---

## 9. Run the engine permanently

The engine is a single process. It needs a launcher that restarts it if it goes down.

| Platform | What the repository provides | What to adapt |
|---|---|---|
| Windows | `deploy/lancer_moteur.bat` (runs `gui_server.py` in the console) and `deploy/lancer_moteur_silencieux.bat` (writes to `moteur_pc.log`, listens on `0.0.0.0:8000`). | Call them from Task Scheduler (trigger "At log on"). The second one refers to a `start_moteur_silent.vbs` that is not in the repository. |
| Linux | **No systemd unit for the engine itself.** | To be written (example below). |

Example of a user systemd unit for Linux. **Example, not tried**: paths and user to adapt.

```ini
# ~/.config/systemd/user/moteur.service
[Unit]
Description=Moteur IA (vromvrom-engine)
After=network-online.target

[Service]
WorkingDirectory=%h/vromvrom-engine
ExecStart=%h/vromvrom-engine/.venv/bin/python -u gui_server.py --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now moteur.service
journalctl --user -u moteur.service -f
```

For it to start without an open session: `loginctl enable-linger $USER`. The engine does not write a log file
(`gui_server.py:57`: standard output only); under systemd, `journalctl` keeps it. The files
`deploy/moteur-worker.service`, `deploy/worker_standalone.py` and `deploy/deploy_deck.sh` are for advanced uses
(remote worker, deployment of one specific machine through git) and are not needed.

---

## 10. Connect an MCP client (Claude Desktop, Cursor…)

`mcp_server.py` exposes **17 tools** to an MCP client (FastMCP, stdio transport), split into three modules:

| Module | Tools |
|---|---|
| `orchestrator` (8) | `run_tab5_agent`, `query_deepseek`, `get_engine_status`, `get_routing_recommendation`, `get_routing_matrix`, `delegate_complex_reasoning`, `query_llm_direct`, `delegate_to_gateway` |
| `memory` (6) | `get_models_catalog`, `rag_search`, `query_token_usage`, `list_available_models`, `query_runtime`, `search_memory` |
| `homeassistant` (3) | `search_ha_entities`, `validate_config_format`, `execute_ha_action` |

Configuration of a client (`mcpServers` format), with **the paths of your machine**:

```json
{
  "mcpServers": {
    "moteur": {
      "command": "C:\\path\\to\\vromvrom-engine\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\vromvrom-engine\\mcp_server.py"]
    }
  }
}
```

On Linux: `"command": "/path/to/vromvrom-engine/.venv/bin/python"`.

Two pitfalls:

1. **The path matters**: the server loads the `.env` of the folder where `mcp_server.py` is located. The file
   `claude_desktop_config.json.example` of the repository follows the form above.
2. **`mcp_server.py` is not an HTTP client of the `gui_server` server.** It creates its own model gateway in
   its own process and reads the files and databases of the folder where it is launched (`core/mcp_tools/orchestrator.py`). If your
   engine runs on another machine, these tools do **not** query it.

Conversely, the engine can **use** third-party MCP servers: copy `mcp_config.json.example` to
`mcp_config.json` (never put a secret in it); `core/mcp_bridge.py` looks for it in `~/.gemini/antigravity-ide/`,
then at the root of the repository. Without `mcp_activation_rules.json` (a `default` list and keyword rules), **no
server is started** (`core/mcp_bridge.py`, lines ~128-168). Details: [03 Settings](03_settings.md).

---

## 11. Daily backup

Make a first backup by hand to check:

```powershell
.\.venv\Scripts\python.exe -m core.sauvegarde
.\.venv\Scripts\python.exe -m core.sauvegarde --lister
```

It creates a timestamped folder in `sauvegardes/` (backups; SQLite databases and configurations, never the `.env`). Tried: the
route `POST /api/sauvegardes` (same code) produced a healthy backup of 14 files; the command line
itself was not run. Details, verification and restore: [05 Operate](05_operate.md).

On Linux, the repository provides a unit and a timer (`deploy/moteur-sauvegarde.service`, `.timer`) for a
backup at 04:30. The unit assumes the repository is in `~/vromvrom-engine` and the venv of `scripts/install.sh`:
adapt `WorkingDirectory` and `ExecStart` if needed (not tried).

```ini
WorkingDirectory=%h/vromvrom-engine
ExecStart=%h/vromvrom-engine/.venv/bin/python -m core.sauvegarde
```

Copy the two files to `~/.config/systemd/user/`, then `systemctl --user enable --now moteur-sauvegarde.timer`.
The timer is `Persistent=true` (a missed backup runs at the next startup). On Windows, no timer
is provided: create a scheduled task that runs the command above (not tried).

---

## 12. What can go wrong

| Symptom | Probable cause | Check |
|---|---|---|
| `ModuleNotFoundError` or syntax error at launch | Wrong Python version, or venv not activated | `.\.venv\Scripts\python.exe --version` must show 3.12 or later |
| The port is refused | Port taken or reserved | Restart with `--port 8361` (or another) |
| 422 « Field required » | Body `{"message": ...}` instead of `user_prompt` | § 7.3 |
| « Aucune clé de provider LLM » (No LLM provider key) | No key in the `.env` (or engine not restarted) | § 4 |
| The interface shows the old design | `ihm-v2/dist` missing | § 5 |

The other startup failures (503 or 401 on `/api`, « config.json Illisible ») have their row in
[05 Operate, § 4](05_operate.md). Next: understand what happens when you send a request
([02](02_understand.md)), then tune the engine ([03](03_settings.md)).
