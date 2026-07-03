<div align="center">

# ⚡ vromvrom-engine

**Async multi-agent LLM orchestrator — hybrid routing, Elo scoring, self-healing**

*Vroom Vroom — running at full throttle 🏁*

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136%2B-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![asyncio](https://img.shields.io/badge/asyncio-native-green)](https://docs.python.org/3/library/asyncio.html)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-54%20pytest%20files-brightgreen)](tests/)

*Built for Home Assistant · Works with any project*

> ⚠️ **Project Status: Active Development**
> This engine is a personal tool being shared with the community. While the core backend architecture (DAG, async, Elo routing) is robust and used daily, the **Web Dashboard (HMI) is currently under construction**. Expect rough edges. It is highly experimental and built for makers.

🇫🇷 **[Version française disponible → README.fr.md](README.fr.md)** &nbsp;·&nbsp; 🗺️ **[Which LLMs to use? → STRATEGIES.md](STRATEGIES.md)**

</div>

---

> **Stop configuring. Start learning.**
> vromvrom-engine routes your tasks to the right LLM automatically —
> using Elo scoring that adapts from your own usage history.
> Async, cost-aware, self-healing. Built on a Raspberry Pi budget.

## 🎯 What is it?

**vromvrom-engine** is a fully asynchronous (`asyncio`) multi-agent orchestration engine that coordinates multiple LLMs to solve complex tasks. It was born from the need to drive a home automation display ([M5Stack Tab5](https://docs.m5stack.com/en/core/tab5)) through Home Assistant — but its architecture is generic and reusable for any project.

> 💡 **Experiment with GPT-4o & Llama 70B for free**
> The engine comes pre-configured to connect to the [GitHub Models](https://github.com/marketplace/models) API, offering free access (subject to rate limits) to models like **GPT-4o** and **Llama 70B** for your tests using your standard GitHub account.

### What makes it different

| Feature | Description |
|---|---|
| 🧠 **4-level hybrid routing** | Regex → ML (sklearn) → LLM → Elo scoring — 0ms to 200ms depending on complexity |
| 🏆 **Dynamic Elo scoring** | Each model gains/loses Elo points per task domain. Best model selected automatically |
| 🔄 **Async parallel DAG** | Independent tasks run concurrently via `asyncio.gather()` |
| 🛡️ **Self-Healing** | Circuit breaker + retry with backoff jitter + automatic provider fallback |
| 💰 **Cost-aware routing** | Elo+Cost cascade: starts with cheapest provider, escalates only when needed |
| 🔍 **Local hybrid RAG** | TF-IDF + BM25 + ChromaDB Embeddings fused via RRF (k=60) — zero cloud cost |
| 👁️ **HITL** | Human-In-The-Loop: pause/resume orchestration for human validation |
| 📊 **FastAPI dashboard** | Glassmorphism HTML/JS UI with real-time SSE, workflow editor, Elo charts |
| 🔌 **Drop-in Proxy API** | 100% OpenAI-compatible API (`/v1/chat/completions`). Plug Cursor, Cline, or Continue.dev directly into the engine |
| 🌐 **Distributed Swarm** | Dispatch tasks to remote workers (Raspberry Pi, VMs, etc.) |
| 🔌 **Plugin system** | Add custom agents via `plugins/<name>/agent.py` + `plugin.json` |

---

## 🏗️ Architecture

```
vromvrom-engine/
├── gui_server.py          # FastAPI entry point (lifespan + 15 routers)
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
├── api/routes/            # 15 FastAPI route modules
├── services/              # Business logic decoupled from HTTP
├── plugins/               # Custom plugins (dynamic loading)
├── workflows/             # JSON workflow definitions (graphs)
├── static/                # HTML/JS/CSS UI (glassmorphism)
├── tests/                 # 54 pytest files
└── docs/                  # Architecture documentation
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
│ 3. LLM Slow-Path    │ ──→ ~200ms (Gemini Flash to resolve ambiguity)
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

---

## ⚙️ Quick Start

### Requirements
- Python 3.11+
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
python gui_server.py
# → Dashboard available at http://localhost:8000
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

Open `http://localhost:8000` after `python gui_server.py`.

The dashboard exposes:
- **Chat tab**: conversational interface with token-by-token streaming
- **Workflows tab**: visual workflow graph editor
- **Models tab**: LLM catalog with real-time Elo scores
- **Swarm tab**: distributed worker monitoring
- **Data tab**: SQLite database explorer

### Via the REST API

```python
import httpx

response = httpx.post(
    "http://localhost:8000/api/execute",
    json={"message": "Analyze this Python code and suggest improvements"},
    headers={"Authorization": "Bearer <MOTEUR_API_KEY>"}
)
print(response.json()["result"])
```

### As a Drop-in Proxy for IDEs (Cursor, Cline, Continue)

vromvrom-engine exposes a standard OpenAI-compatible endpoint. You can point your favorite AI coding assistant to the engine to benefit from free routing and the circuit breaker:

- **Base URL**: `http://localhost:8000/v1`
- **API Key**: `<MOTEUR_API_KEY>` (from your `.env`)
- **Model**: `gemini-2.0-flash` (or any model available in your routing)

### Via the CLI

```bash
python main.py "Explain hexagonal architecture in 3 key points"
```

---

## 🔌 Supported LLM Providers

| Provider | Free tier | Notes |
|---|---|---|
| **Gemini** (Google AI Studio) | ✅ | Recommended to start — generous free tier |
| **GitHub Models** | ✅ | GPT-4o, Llama 3.3 70B via GitHub token |
| **DeepSeek** | 💰 | Excellent price/quality ratio (~$0.14/M tokens) |
| **Anthropic Claude** | 💰 | Via API or Claude Code CLI (Pro subscription) |
| **Mistral** | 💰 | European GDPR-friendly models |
| **OpenRouter** | 💰 | Aggregator (200+ models) |
| **LM Studio** | ✅ | Local inference (Qwen, Llama, Mistral...) |
| **Ollama** | ✅ | Alternative local inference |
| **MiniMax** | 💰 | Built-in `<think>` reasoning |
| **Cohere, Cerebras, xAI...** | 💰 | Additional configurable providers |

---

## 🧪 Tests

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
- 🐳 Dockerization (minimal image)
- 📖 API documentation
- 🧪 Increased test coverage
- 🌍 Support for additional LLM providers
- 🎨 UI improvements

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
