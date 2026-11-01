<div align="center">

<img src="docs/logo.svg" alt="" width="96" height="96">

# ⚡ vromvrom-engine

**A home-made brain for Home Assistant and your LLM providers: rules first, a model only when it helps.**

*Vroom Vroom 🏁*

[![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python)](https://python.org)
[![License](https://img.shields.io/github/license/Axellum/vromvrom-engine)](LICENSE)
[![CI](https://github.com/Axellum/vromvrom-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/Axellum/vromvrom-engine/actions/workflows/ci.yml)
[![Python CI & Security](https://github.com/Axellum/vromvrom-engine/actions/workflows/python-tests.yml/badge.svg)](https://github.com/Axellum/vromvrom-engine/actions/workflows/python-tests.yml)

🇫🇷 **[Version française → README.fr.md](README.fr.md)** &nbsp;·&nbsp; 📖 **[User guide → docs/guide/en](docs/guide/en/README.md)** &nbsp;·&nbsp; 🗺️ **[Which LLMs to use? → STRATEGIES.md](STRATEGIES.md)**

</div>

> **A personal project, used every day in one home.** Written with a lot of help from AI and shared in case it
> helps someone, with no promise of support. This page says what works, what does not and how it compares, with
> the figures measured on the author's installation on 2026-11-01.

## In one minute

vromvrom-engine (the "Moteur IA", "AI Engine", of the French docs) is a Python server (FastAPI) that sits between
what asks (a chat, a script, an IDE, an MCP client, a voice assistant through Home Assistant) and what answers
(about eighteen LLM providers, local models, Home Assistant). For each request it takes the cheapest path that is
enough:

1. **a rule, without any AI**, when the sentence is a known home command or question ("turn off the living room
   light", "what is the temperature in the bedroom?");
2. **a model** otherwise, picked by cost, quota and a score per kind of task (Elo), with a fallback when a
   provider fails;
3. **a plan of several agents** (planner, executors, reviewer) for a complex task: the least mature part, see below.

It counts every token and every cent, and shows what it did. Everything is kept in SQLite files, with no database
server to run: the author's instance runs on a Steam Deck, day and night. It was born to give a voice to a Home
Assistant wall screen ([M5-Tab5-ESPHome-LVGL](https://github.com/Axellum/M5-Tab5-ESPHome-LVGL)).

## What works today

Measured on the author's installation (one household, in French, with Home Assistant) on 2026-11-01, unless stated.

| Part | State | Evidence |
|---|---|---|
| Home commands and questions without a model | ✅ Used every day | 110 to 196 ms on the engine side for a sentence of the catalog. The full voice pass takes about 4 s, most of it speech recognition in Home Assistant. |
| Learning from your own voice log | ✅ By hand | Every voice request is logged with the path it took. Over 30 days in the author's home: 67 real sentences, 4 sent down the wrong path, then fixed ([below](#️-teach-it-your-own-words)). |
| Chat with a model, several providers, fallback | ✅ Used every day | Cascade sorted by cost, Elo and quota; a circuit breaker per provider; a health probe that turns failing models off. 48 voice answers in 7 days: median 0.86 s, 90 % under 4.6 s (chat included). |
| Cost | ✅ | 86 real calls in 30 days cost $0.81 in all. |
| OpenAI-compatible endpoint (`/v1/chat/completions`, `/v1/models`) | ✅ In the code, tested | Streaming; three test files. Plug an IDE (Cursor, Cline, Continue) or any OpenAI client into it. |
| MCP, both ways | 🟡 Tested, little used | A server of 17 tools (Home Assistant, memory, orchestrator) and a client of third-party servers. |
| Backups of the databases | 🆕 New (November 2026) | Hot, consistent copies with a manifest and a retention (`python -m core.sauvegarde`, a systemd timer in `deploy/`). |
| Cost and usage statistics | 🟡 Skewed | Every call is recorded, but the health probes are counted with the real calls: 27,805 calls counted in 30 days, 86 of them real. Separating them is on the list. |
| Local ML router | 🧪 Off on a fresh install | A scikit-learn classifier that trains itself on your history once it holds 50 requests or more. |
| Multi-agent plans (DAG) | 🧪 Works in the tests, not in daily use | No complex task was run in 30 days on the author's instance. Do not take it for a coding agent. |
| Autonomous cycle (Dreamer, DreamCoder) | 🧪 Experimental | The Dreamer consolidates the memory every hour; DreamCoder, which works through a backlog of tasks, has found nothing to do since the end of August. |
| Voice through Home Assistant Assist | ⚠️ Not plug-and-play | The conversation agent that sends the recognized text to the engine (`moteur_ia_conversation`) **is not in this repository**. The [guide](docs/guide/en/04_home_and_voice.md) says what you can do without it. |

## Why you might like it

- **The house never waits for a language model.** Home Assistant users ask not to send everything to a model
  (latency, cost, invented actions) and to expose few entities
  ([forum thread](https://community.home-assistant.io/t/custom-component-extended-openai-conversation-lets-control-entities-via-chatgpt/636500?page=9)).
  Here a known sentence is a rule, answered in a fifth of a second; the model only comes in for a conversation.
- **The model cannot act on its own.** In a conversation, the model only has **read** tools for Home Assistant.
  Devices are commanded only by explicit rules (the phrase catalog `ha_commands.json`, tables of rooms). The price:
  a new command means a new line in the catalog.
- **A full gateway on a small machine.** The keys of many providers, fallback, the quotas of free tiers, circuit
  breakers, the cost of each call, all in SQLite: nothing else to install.
- **It shows its work.** Voice audit (text, path taken, latency), history of sessions, replay of a real request in
  the "Understand the engine" page; a figure it does not know shows "—", never 0.
- **French first.** The phrase catalog, the docs and the comments are in French, with an English guide.

## 🗣️ Teach it your own words

A voice assistant meets the words of one house: the names of its rooms, a way of talking, and the mistakes of speech
recognition. The engine keeps what you need to learn them, and turns them into direct routes:

- **A log of every voice request**: the text as Home Assistant recognized it, the path taken (a direct rule, a state
  question, or a fall into the chat) and the latency (`GET /api/vocal/audit`, `GET /api/vocal/stats`, and the
  "Understand the engine" page).
- **A dry run**: `POST /api/vocal/simulate` tells which path a sentence would take, without doing anything.
- **A cleaner for French transcriptions** (`core/vocal_stt_normalize.py`): it fixes the usual mistakes of speech
  recognition before matching ("est-elle" heard for "éteins", turn off; "de la somme" for "de la chambre", of the
  bedroom), and "cline" is read as "clim" (air conditioning).
- **The phrase catalog** (`ha_commands.json`): a sentence that comes back often becomes a direct route, answered in a
  fifth of a second without a model.

How it was done in the author's home: read 30 days of the voice log (67 real sentences, 4 sent down the wrong path),
then add what was missing. The fixes, all in this repository: the speech-recognition variants of "clim"; a question
on a temperature answered by a rule instead of the chat; the live sensor chosen over a dead duplicate; a single word
("Coupe.", "Chambre.") that no longer turns a light on.

**Not automatic yet**: reading the log and writing the new rules is done by hand, or with an AI assistant that reads
the log for you. The engine does not propose new routes by itself.

## Where it falls short

- **One user, one API key.** No accounts, roles, single sign-on or budget per person: team gateways such as
  [LiteLLM](https://docs.litellm.ai/docs/proxy/budget_fallbacks) or chat interfaces such as Open WebUI and LibreChat
  do that.
- **The voice needs a component that is not published** (see the table).
- **The statistics are skewed** by the health probes, until they are counted apart.
- **Many providers, not all alive.** On the author's instance, the health probe turned off 42 models out of 76: dead
  providers stay listed.
- **The multi-agent part is the least mature**, and the least used.
- **Learning new routes is manual.** The engine logs and simulates, but does not yet propose new rules from
  the log by itself.
- **Made for a developer first.** The dashboard is rich but dense; the code, its comments and many of its names are
  in French; about half of the code is covered by the tests (54 % on 2026-11-01).
- **One maintainer**, code written largely with AI, no promise of support.

## Is it for you?

| You want | Look at |
|---|---|
| Home Assistant voice that stays fast and never lets a model invent an action, on a small machine, with free models | **vromvrom-engine** may fit (for the voice, bring your own conversation agent for now) |
| A gateway for a team: keys, budgets per user or per key, spend tracking | [LiteLLM](https://docs.litellm.ai/docs/proxy/budget_fallbacks) |
| A chat interface for several people, with roles | Open WebUI, LibreChat |
| A library to build agents in your own code | LangGraph, CrewAI |

---

## ⚙️ Quick Start

### Requirements
- Python 3.12+ (the version tested in CI)
- Node.js 20.19+ or 22.12+ to build the React dashboard (optional)
- At least **one LLM API key** (free Gemini tier works, see `.env.example`)

### Setup

```bash
# 1. Clone the repo
git clone https://github.com/Axellum/vromvrom-engine.git
cd vromvrom-engine

# 2. Virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure
cp .env.example .env
# Edit .env — at minimum set GEMINI_API_KEY (free at aistudio.google.com)

# 5. Engine config
cp config.example.json config.json
# Optional: adjust default models

# 6. Launch
#    (optional) React UI: cd ihm-v2 && npm install && npm run build
python gui_server.py
# → Dashboard at http://localhost:8000
```

### Minimal configuration (`.env`)

The engine works with **a single free key**:

```env
# Free Gemini key: https://aistudio.google.com/apikey
GEMINI_API_KEY=AIza...

# Local API authentication key (generate a random value)
MOTEUR_API_KEY=change-me-with-secrets-token-urlsafe-32
```

All other providers (DeepSeek, Anthropic, Mistral...) are **optional** — the engine degrades gracefully.

---

## 🚀 Usage

### Web Dashboard

```bash
cd ihm-v2 && npm install && npm run build   # → ihm-v2/dist/
python gui_server.py                        # http://localhost:8000
```

If `ihm-v2/dist` is missing, the engine falls back to the legacy UI in `static/` (also under `/v1`).

Dev with HMR: `cd ihm-v2 && npm run dev` (Vite `:5173`, proxies `/api` to `:8000`).

The React dashboard includes Home (one-sentence status), Understand the engine, Voice & home demo, Chat (SSE + tools), Home Assistant, LLM registry and routing, MCP tools, Backups, Prompt Studio, observability, and config. Tour: [docs/guide/en](docs/guide/en/README.md).

### Via the REST API

```python
import httpx

response = httpx.post(
    "http://localhost:8000/api/execute",
    json={
        "user_prompt": "Analyze this Python code and suggest improvements",
        "source": {"type": "web", "mode": "chat"},
    },
    headers={"Authorization": "Bearer <MOTEUR_API_KEY>"},
    timeout=120,
)
print(response.json()["response"])
```

### As a Drop-in Proxy for IDEs (Cursor, Cline, Continue)

vromvrom-engine exposes a standard OpenAI-compatible endpoint. You can point your favorite AI coding assistant to the engine to use its routing, fallback and circuit breakers:

- **Base URL**: `http://localhost:8000/v1`
- **API Key**: `<MOTEUR_API_KEY>` (from your `.env`)
- **Model**: an id listed by `GET /v1/models`, for example `gemini-3.5-flash-free` (the default of `config.example.json`)

### 🏠 Home Assistant (optional)

vromvrom-engine ships with tools (`tools/ha_entity_ingest.py`), routes (`/api/ha/*`) and MCP tools to read your Home Assistant instance.

Just add these to your `.env`:
```env
HASS_URL=http://192.168.1.X:8123
HASS_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```
The model can then read states and sensors. Devices are commanded by the rules of the phrase catalog (`ha_commands.json`, start from `ha_commands.json.example`), not by the model. For the voice, see [the guide](docs/guide/en/04_home_and_voice.md): the conversation agent that Home Assistant needs is not in this repository.

### Via the CLI

```bash
python main.py "Explain hexagonal architecture in 3 key points"
```

---

## 🧩 Features in detail

| Feature | What it does |
|---|---|
| 🧠 **Hybrid routing** | Regex fast path → local ML classifier (once trained) → a light model for ambiguous requests → Elo score per kind of task to pick the model |
| 🏆 **Elo per kind of task** | Each model gains or loses points per domain; the cascade uses them with cost and quota |
| 🛡️ **Self-healing** | A circuit breaker per provider, retries with exponential backoff, fallback to the next provider |
| 💰 **Cost-aware cascade** | Falls back to the next configured provider when one is unavailable, rate-limited or over budget — not a quality-based escalation |
| 🔄 **Async parallel DAG** | The independent tasks of a plan run concurrently (`asyncio.gather()`) |
| 🔍 **Local hybrid RAG** | TF-IDF + BM25 + ChromaDB embeddings fused by RRF (k=60), on your machine |
| 👁️ **HITL** | Pause and resume a plan for a human validation (high-risk plans) |
| 🔌 **OpenAI-compatible API** | `/v1/chat/completions` (with streaming) and `/v1/models`, for Cursor, Cline, Continue.dev or any OpenAI client |
| 🧰 **MCP** | A server of 17 tools and a client of third-party MCP servers |
| 🏠 **Home Assistant** | `/api/ha` routes, entity ingestion and read tools; devices are commanded through the phrase catalog |
| 🌐 **Remote workers** | Dispatch tasks to other machines (Raspberry Pi, VMs…) |
| 🔌 **Plugins** | Custom agents in `plugins/<name>/agent.py` + `plugin.json` |
| 📊 **Dashboard** | React interface (`ihm-v2/`), with the older HTML interface of `static/` as a fallback |

## 🏗️ Architecture

```
vromvrom-engine/
├── gui_server.py          # FastAPI entry point (lifespan + 34 routers)
├── main.py                # CLI — direct launch without HTTP
│
├── core/                  # Engine core
│   ├── engine.py          # Main orchestrator (DAG → Agents)
│   ├── llm_gateway.py     # Multi-provider gateway (18+ providers)
│   ├── router.py          # Hybrid routing (fast-path + ML + LLM + Elo)
│   ├── dag_runner.py      # Async parallel execution by stages
│   ├── factory.py         # Agent instantiation (Planner/Executor/Reviewer)
│   ├── state.py           # Thread-safe Pydantic GlobalState
│   ├── checkpoint.py      # ACID snapshots (SQLite WAL)
│   ├── healing.py         # Self-healing + retry
│   ├── review_loop.py     # Reviewer → Correction loop
│   ├── elo_scorer.py      # Per-domain model Elo scoring
│   ├── elo_router.py      # Routing type Elo scoring
│   ├── circuit_breaker.py # Async circuit breaker (CLOSED/OPEN/HALF_OPEN)
│   ├── hitl.py            # Human-In-The-Loop (asyncio.Event)
│   ├── models_db.py       # SQLite SSOT — model catalog, pricing, quotas
│   └── ...
│
├── agents/                # Specialized agents
│   ├── planner.py         # Breaks task into a JSON DAG
│   ├── executor.py        # Executes tasks (ReAct loop + tools)
│   ├── reviewer.py        # Validates result quality
│   └── tool_maker_agent.py  # Generates new Python tools automatically
│
├── memory/                # Semantic memory + RAG
│   ├── rag.py             # Hybrid RAG (TF-IDF + BM25 + Embeddings + RRF)
│   ├── facts.py           # Fact store (SQLite FTS5 BM25)
│   ├── episodes.py        # Episodic memory (Jaccard similarity)
│   ├── embeddings.py      # ChromaDB vector store
│   └── skills.py          # Procedural memory (successful tool sequences)
│
├── tools/                 # Agent-usable tools
│   ├── tool_registry.py   # Registry with per-tool timeouts
│   └── sanitizer.py       # Secret masking (6 patterns)
│
├── api/routes/            # 34 FastAPI route modules
├── services/              # Business logic decoupled from HTTP
├── plugins/               # Custom plugins (dynamic loading)
├── workflows/             # JSON workflow definitions (graphs)
├── static/                # HTML/JS/CSS UI (glassmorphism, fallback if ihm-v2 is not built)
├── ihm-v2/                # React/Vite/TS dashboard (Chat, HA, LLM registry, Prompt Studio…)
├── tests/                 # 280+ pytest files
└── docs/                  # Architecture documentation + user guide (docs/guide/)
```

### Hybrid routing pipeline

```
User request
      │
      ▼
┌─────────────────────┐
│ 1. Regex Fast-Path  │ ──→ 0ms    (simple commands detected by pattern)
└─────────────────────┘
      │ ambiguous
      ▼
┌─────────────────────┐
│ 2. ML Router sklearn│ ──→ 0ms    (local classifier, 75% confidence threshold)
└─────────────────────┘
      │ confidence < 75%
      ▼
┌─────────────────────┐
│ 3. LLM Slow-Path    │ ──→ ~200ms (a light model to resolve ambiguity)
└─────────────────────┘
      │
      ▼
┌─────────────────────┐
│ 4. Elo Scoring      │ ──→ selects the best model for the task domain
└─────────────────────┘
      │
      ▼
  Planner → DAG → Parallel Executor(s) → Reviewer → Response
```

Step 2 only runs once the classifier has been trained on 50 requests or more of your history; the multi-agent part (last line) is used for complex tasks only.

---

## 🆕 Recent changes (November 2026)

- **A dashboard that explains the engine.** Navigation by intent (Discover · Use · Configure · Monitor · Advanced). A *Home* page states in one sentence whether everything is fine and why, with sourced figures; *Understand the engine* shows the path of a request on a clickable map and can replay a real one; *Voice & home demo* walks through every link from the wake word to the spoken answer. The main pages have a foldable “Understand this page” block and cite where their figures come from; unknown data shows “—”, never zero.
- **Database backups.** Hot, consistent copies of the SQLite databases and configuration files, with a manifest (sha256, integrity check) and retention: `python -m core.sauvegarde`, `GET/POST /api/sauvegardes`, a *Backups* page, and a systemd timer in `deploy/`. Never the `.env`.
- **Dry-run voice simulation.** `POST /api/vocal/simulate` shows which path a sentence would take (Home Assistant command, state question, chat) **without executing anything**.
- **MCP overview.** `GET /api/mcp/overview` and an *MCP tools* page list the tools the engine offers and the servers it uses.
- **Safer errors.** API keys are masked in provider health errors (`/api/models-health`) before they are stored and when they are read.

---

## 🔌 Supported LLM Providers

| Provider | Free tier | Notes |
|---|---|---|
| **Gemini** (Google AI Studio) | ✅ | Recommended to start — generous free tier |
| **GitHub Models** | ✅ | GPT-4o, Llama 3.3 70B via GitHub token |
| **DeepSeek** | 💰 | Excellent price/quality ratio (~$0.14/M tokens) |
| **Anthropic Claude** | 💰 | Via API or Claude Code CLI (Pro subscription) |
| **Mistral** | ✅ | European GDPR-friendly models — free tier |
| **OpenRouter** | 💰 | Aggregator (200+ models) |
| **LM Studio** | ✅ | Local inference (Qwen, Llama, Mistral...) |
| **Ollama** | ✅ | Alternative local inference |
| **MiniMax** | 💰 | Built-in `<think>` reasoning |
| **Cohere** | ✅ | Free trial models (Command R / R+) |
| **Cerebras** | ✅ | 100% free — ultra-fast inference (GPT-OSS 120B, GLM 4.7) |
| **Zhipu AI (GLM)** | 💰 | 8 GLM models, strong in code/agentic tasks |
| **xAI (Grok)** | 💰 | Paid only |

The table lists the main ones; the code knows about eighteen. A provider in the list is not a promise that it answers today: the health probe turns off the models that fail.

---

## 🧪 Tests

288 test files (about 2,370 test functions), run by the CI on Python 3.12. About half of the code is covered (54 % measured on 2026-11-01).

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run all tests
pytest

# With coverage report
pytest --cov=. --cov-report=html
```

---

## 🗺️ Which LLMs should I use?

See **[STRATEGIES.md](STRATEGIES.md)** for 4 ready-to-use configurations:
- 🆓 Free & Local only — $0/month
- 🐉 Chinese models — ~$5–15/month, near-frontier quality
- ⚡ Claude API/CLI — near-zero extra cost with a Pro subscription
- 🚀 Full Power + cost-optimized — state of the art at ~10× lower cost

---

## 🤝 Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).

**Priority areas:**
- [x] 🐳 Dockerization (minimal image + docker-compose)
- [x] 📖 API documentation (see [docs/API_REFERENCE.md](docs/API_REFERENCE.md))
- [ ] 🧪 Increased test coverage
- [ ] 🌍 Support for additional LLM providers
- [ ] 🎨 UI improvements

---

## 📄 License

MIT — See [LICENSE](LICENSE).

---

## 🙏 Inspirations

- [LangChain](https://github.com/langchain-ai/langchain) — orchestration patterns
- [CrewAI](https://github.com/crewAIInc/crewAI) — multi-agent coordination
- [Home Assistant](https://github.com/home-assistant/core) — home automation ecosystem
- [FastAPI](https://github.com/tiangolo/fastapi) — API framework

---

<div align="center">
<sub>Built with ❤️ for the home automation and open-source AI community</sub>
</div>
