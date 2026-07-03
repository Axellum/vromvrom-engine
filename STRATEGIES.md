# 🗺️ LLM Strategy Guide

> **Which models should I use?** This guide helps you configure vromvrom-engine
> to match your budget, hardware, and performance requirements.
> All strategies are tested and the `.env` + `config.json` snippets are ready to copy-paste.

---

## The 4 Strategies at a Glance

| Strategy | Monthly cost | Performance | Complexity |
|---|---|---|---|
| [🆓 Free & Local](#-strategy-1--free--local-only) | **$0** | Good | Low |
| [🐉 Chinese Power](#-strategy-2--chinese-models--high-perf-low-cost) | **~$5–15** | Excellent | Low |
| [⚡ Claude-optimized](#-strategy-3--claude-apicli--near-zero-extra-cost) | **~$0 extra** | Excellent | Medium |
| [🚀 Full Power](#-strategy-4--full-power--cost-optimized) | **~$20–50** | State of the art | Medium |

> **Key insight**: vromvrom-engine's Elo routing means you don't need to commit to one model.
> Mix providers freely — the engine learns which one performs best *for each task type*.

---

## 🆓 Strategy 1 — Free & Local Only

**Goal**: Run a capable orchestration engine at zero cost.
**Best for**: Makers, hobbyists, privacy-first users, offline setups.

### How it works
- **Gemini free tier** (Google AI Studio): up to 5 API keys in rotation → 5× the quota
- **GitHub Models**: GPT-4o-mini + Llama 3.3 70B for free with any GitHub account
- **OpenRouter Free Tier**: top open-source models (Qwen 72B, Llama 3.1 70B) for free, backed by our Circuit Breaker
- **LM Studio or Ollama**: local inference on your GPU/CPU for heavy lifting

### Recommended models

| Role | Model | Provider | Cost |
|---|---|---|---|
| Planner (reasoning) | `llama-3.3-70b-instruct` | GitHub Models | Free |
| Executor (fast tasks) | `gemini-2.0-flash` | Google AI Studio | Free |
| Reviewer | `gpt-4o-mini` | GitHub Models | Free |
| Compression / summarize | `qwen2.5-14b-instruct` | LM Studio (local) | Free |
| Embeddings (RAG) | `nomic-embed-text` | Ollama (local) | Free |

> 💡 **Pro Tip: The OpenRouter + Circuit Breaker Combo**
> OpenRouter offers massive free models (like `qwen/qwen-2.5-72b-instruct:free` and `nvidia/llama-3.1-nemotron-70b-instruct:free`). Their only downside is strict rate-limiting during peak hours.
> By putting them in your `config.json` with Gemini Flash as a fallback, vromvrom-engine's **Circuit Breaker** will automatically catch any `429 Rate Limit` errors and silently fall back to Gemini without failing your task. You get huge open-source power for free, with 100% uptime.

> 🔑 **Pro Tip: The Gemini KeyPool Trick**
> The Google AI Studio free tier limits you to 15 Requests Per Minute (RPM) per account. To bypass this for heavy multi-agent workflows:
> 1. Create multiple free Google accounts (or use different Google Workspace users).
> 2. Generate one free API key per account.
> 3. List them sequentially in your `.env` as `GEMINI_API_KEY`, `GEMINI_API_KEY_2`, `GEMINI_API_KEY_3`, etc.
> 
> The engine's internal `key_pool.py` will automatically load all of them and round-robin your requests, effectively multiplying your free quota by the number of keys.

### `.env` setup

```env
# Up to 5 Gemini keys for quota rotation — get them at aistudio.google.com/apikey
GEMINI_API_KEY=AIza...key1
GEMINI_API_KEY_2=AIza...key2
GEMINI_API_KEY_3=AIza...key3
GEMINI_MODEL=gemini-2.0-flash

# GitHub Models — free with any GitHub account
# Generate at: github.com/settings/tokens (no special scope needed)
GITHUB_TOKEN=ghp_...

# Local inference — point to your LM Studio or Ollama instance
# No key needed, just the URL
```

### `config.json` setup

```json
{
  "planner_model":  "fort",
  "executor_model": "leger",
  "reviewer_model": "leger",
  "tiers": {
    "leger": ["local", "gemini-flash", "github-gpt4o-mini"],
    "moyen": ["github-llama-70b", "gemini-flash"],
    "fort":  ["github-llama-70b", "gemini-pro"],
    "automatique": ["local", "gemini-flash", "github-gpt4o-mini", "github-llama-70b"]
  },
  "local_llm": {
    "lmstudio_url": "http://localhost:1234",
    "ollama_url":   "http://localhost:11434",
    "model": "qwen2.5-14b-instruct"
  }
}
```

### Expected performance
- Simple tasks (chat, summaries): ✅ Excellent — local or Gemini Flash
- Complex reasoning: ⚠️ Good — Llama 70B via GitHub Models is capable but has latency
- Context window: ✅ Up to 128K tokens with Llama 3.3

> **Tip**: Rotate 5 Gemini keys to multiply your free quota by 5.
> Each key gets ~1500 requests/day on the free tier.

---

## 🐉 Strategy 2 — Chinese Models → High Perf, Low Cost

**Goal**: Near-frontier performance at a fraction of Western provider prices.
**Best for**: Developers who want serious capability without serious bills.

### Why Chinese models?

DeepSeek V3 costs **50× less than GPT-4o** and matches it on most benchmarks.
DeepSeek R1 rivals OpenAI o1 on reasoning tasks at **$0.14/M tokens** vs **$15/M tokens**.
These are not compromises — they are the current price/performance champions.

### Recommended models

| Role | Model | Provider | Input cost | Output cost |
|---|---|---|---|---|
| Planner (deep reasoning) | `deepseek-reasoner` (R1) | DeepSeek | $0.14/M | $2.19/M |
| Executor (standard tasks) | `deepseek-chat` (V3) | DeepSeek | $0.07/M | $1.10/M |
| Fast tasks / routing | `gemini-2.0-flash` | Google (free) | Free | Free |
| Summarization | `qwen2.5-14b` | LM Studio (local) | $0 | $0 |
| Embeddings | `gemini-embedding-004` | Google (free) | Free | Free |

> **Realistic monthly cost for heavy use**: ~$5–15

### `.env` setup

```env
# DeepSeek — get key at platform.deepseek.com
DEEPSEEK_API_KEY=sk-...

# Gemini free for routing and embeddings
GEMINI_API_KEY=AIza...
GEMINI_MODEL=gemini-2.0-flash
```

### `config.json` setup

```json
{
  "planner_model":  "fort",
  "executor_model": "moyen",
  "reviewer_model": "leger",
  "tiers": {
    "leger": ["local", "gemini-flash", "deepseek-chat"],
    "moyen": ["deepseek-chat", "gemini-flash"],
    "fort":  ["deepseek-reasoner", "deepseek-chat"],
    "automatique": ["local", "gemini-flash", "deepseek-chat", "deepseek-reasoner"]
  }
}
```

### Expected performance
- Reasoning / planning: ✅ Excellent — R1 matches o1 on most benchmarks
- Code generation: ✅ Excellent — V3 is top-3 globally
- Latency: ⚠️ DeepSeek can be slow during peak hours (China timezone)
- Privacy: ⚠️ Data processed in China — not suitable for sensitive data

> **Tip**: The Elo router will automatically detect if DeepSeek is slow/unavailable
> and fall back to Gemini Flash — you stay online without manual intervention.

---

## ⚡ Strategy 3 — Claude API/CLI → Near-Zero Extra Cost

**Goal**: Use Claude's frontier intelligence while paying almost nothing extra.
**Best for**: Developers with an Anthropic Claude Pro subscription ($20/month).

### The insight

If you already pay for Claude Pro/Max, the **Claude Code CLI** is included.
vromvrom-engine can call it as a subprocess — meaning you consume your included
subscription quota instead of paying per-token API rates.

**Effective cost for Claude calls via CLI: ~$0 extra** (already in your subscription).

### Recommended models

| Role | Model | Access | Cost |
|---|---|---|---|
| Planner (complex tasks) | `claude-opus-4-5` | Claude Code CLI | Included in Pro |
| Executor (standard) | `claude-sonnet-4-5` | Claude Code CLI | Included in Pro |
| Fast tasks / fallback | `gemini-2.0-flash` | Google (free) | Free |
| Heavy reasoning | `deepseek-reasoner` | DeepSeek API | $0.14/M |
| Local/compression | `qwen2.5-14b` | LM Studio | Free |

### `.env` setup

```env
# Claude Code CLI must be installed: npm install -g @anthropic-ai/claude-code
# No API key needed — the CLI uses your browser session / Pro subscription

# Gemini as free fallback
GEMINI_API_KEY=AIza...
GEMINI_MODEL=gemini-2.0-flash

# DeepSeek for heavy reasoning (very cheap)
DEEPSEEK_API_KEY=sk-...
```

### `config.json` setup

```json
{
  "planner_model":  "fort",
  "executor_model": "moyen",
  "reviewer_model": "leger",
  "tiers": {
    "leger": ["local", "gemini-flash"],
    "moyen": ["claude-sonnet-cli", "gemini-flash"],
    "fort":  ["claude-opus-cli", "deepseek-reasoner"],
    "automatique": ["local", "gemini-flash", "claude-sonnet-cli", "claude-opus-cli"]
  }
}
```

### How the CLI provider works

```python
# The engine calls Claude Code CLI as a subprocess:
# claude --model claude-opus-4-5 --output-format json -p "your prompt"
# It parses the JSON output and treats it like any other provider.
# If your Pro quota is exhausted, it falls back to the next provider in the tier.
```

### Expected performance
- Quality: ✅ State of the art (Claude Opus is currently top-tier)
- Cost: ✅ Near-zero if you already have Claude Pro
- Latency: ⚠️ CLI subprocess adds ~1–2s overhead vs direct API
- Reliability: ✅ Automatic fallback to Gemini if CLI quota exhausted

> **Tip**: Use `claude-sonnet-4-5` for 90% of tasks (faster, cheaper quota-wise)
> and reserve `claude-opus-4-5` for the Planner only.

---

## 🚀 Strategy 4 — Full Power + Cost Optimized

**Goal**: State-of-the-art quality on complex tasks, cheap models on simple ones.
**Best for**: Power users, production deployments, demanding workflows.

### The philosophy

Not every task needs GPT-4o. The Elo router routes:
- Simple lookups → free Gemini Flash (~0ms overhead)
- Standard dev tasks → DeepSeek V3 (~$0.001 per task)
- Complex planning → Gemini 2.5 Pro or Claude Opus (~$0.01–0.05 per task)
- Reasoning chains → DeepSeek R1 (~$0.003 per task, fraction of o1 price)

**Result**: frontier-quality output, ~10× cheaper than using GPT-4o/Claude for everything.

### Recommended models

| Role | Model | Provider | Why |
|---|---|---|---|
| Strategic Planner | `gemini-2.5-pro` | Google | Best at structured planning, huge context |
| Reasoning tasks | `deepseek-reasoner` | DeepSeek | Matches o1, 50× cheaper |
| Code generation | `claude-sonnet-4-5` | Anthropic API | Top code quality |
| Standard execution | `deepseek-chat` | DeepSeek | Best price/perf ratio |
| Fast routing/chat | `gemini-2.0-flash` | Google (free tier) | Near-instant, free |
| Local compression | `qwen2.5-14b` | LM Studio | Zero cost summarization |

### `.env` setup

```env
# Google Gemini (free + paid)
GEMINI_API_KEY=AIza...          # free tier key
GEMINI_PAYANT_API_KEY=AIza...   # paid GCP key for Gemini 2.5 Pro
GEMINI_MODEL=gemini-2.0-flash

# DeepSeek — the cost-optimization backbone
DEEPSEEK_API_KEY=sk-...

# Anthropic — for code tasks where quality matters
ANTHROPIC_API_KEY=sk-ant-...

# GitHub Models as additional free fallback
GITHUB_TOKEN=ghp_...
```

### `config.json` setup

```json
{
  "planner_model":  "fort",
  "executor_model": "moyen",
  "reviewer_model": "leger",
  "tiers": {
    "leger": ["local", "gemini-flash", "deepseek-chat"],
    "moyen": ["deepseek-chat", "claude-sonnet", "gemini-flash"],
    "fort":  ["gemini-2.5-pro", "deepseek-reasoner", "claude-opus"],
    "automatique": ["local", "gemini-flash", "deepseek-chat", "deepseek-reasoner", "gemini-2.5-pro"]
  },
  "local_llm": {
    "lmstudio_url": "http://localhost:1234",
    "model": "qwen2.5-14b-instruct"
  }
}
```

### Cost breakdown (example: 100 complex tasks/day)

| Task type | % of tasks | Model used | Cost/task | Daily cost |
|---|---|---|---|---|
| Simple chat / lookup | 40% | Gemini Flash | $0.00 | $0.00 |
| Standard dev tasks | 35% | DeepSeek V3 | ~$0.001 | ~$0.035 |
| Complex reasoning | 20% | DeepSeek R1 | ~$0.003 | ~$0.06 |
| Strategic planning | 5% | Gemini 2.5 Pro | ~$0.02 | ~$0.10 |
| **Total** | 100% | Mix | — | **~$0.20/day** |

> ~$6/month for 100 complex tasks/day. GPT-4o alone for the same volume: ~$60+/month.

---

## 🔧 How Elo Routing Improves These Configs Over Time

Whatever strategy you choose, the Elo system continuously learns:

```
Week 1: DeepSeek R1 scores well on your "code_analysis" tasks
        → its Elo for "code_analysis" rises
        → engine routes more "code_analysis" to R1

Week 2: Gemini Flash starts failing on "home_assistant" entity lookups
        → its Elo for "home_assistant" drops
        → engine routes those to DeepSeek or local

Week 3: Your personal routing matrix is optimized for YOUR tasks
        → better results, lower cost, no manual tuning
```

**You don't configure the engine once — it configures itself.**

---

## 📊 Provider Quick Reference

| Provider | Free tier | Strengths | Weaknesses |
|---|---|---|---|
| **Google Gemini Flash** | ✅ Generous | Speed, multimodal, huge context | Less sharp on complex reasoning |
| **Google Gemini 2.5 Pro** | ❌ Paid | Best planning, 1M context | Cost, not cheapest |
| **DeepSeek V3** | ❌ Paid | Best price/perf, great code | Peak-hour slowdowns |
| **DeepSeek R1** | ❌ Paid | Reasoning = matches o1 | Slower, verbose thinking |
| **GitHub Models** | ✅ Free | GPT-4o-mini + Llama 70B | Rate limits, no fine-tuning |
| **Claude Opus/Sonnet** | ❌ Paid (CLI=free) | Top code + instruction following | Cost if via API |
| **LM Studio / Ollama** | ✅ Free | Privacy, zero latency, offline | GPU required for quality |
| **OpenRouter** | ❌ Paid | Access to 200+ models | Extra latency layer |

---

## 🎁 "Bons Plans" & Free Cloud Credits (The Cloud Hustle)

Want to run massive workloads for free? You can combine your `vromvrom-engine` with these generous cloud trial programs. Because the engine supports Vertex AI and Google Cloud natively, you can easily tap into these:

### 1. Google Cloud Platform (GCP) $300 Free Trial
- **What it is:** The standard GCP welcome offer.
- **How to get it:** Create a new billing account on Google Cloud with a credit card.
- **What you get:** **$300 in credits** valid for 90 days.
- **How to use it here:** You can use this to call `Gemini 1.5/2.0 Pro` and even `Claude 3.5 Sonnet` (via Vertex AI Model Garden) without paying a cent until the $300 or 90 days run out.

### 2. Dialogflow CX $600 Free Trial
- **What it is:** A specific credit for building conversational AI agents.
- **How to get it:** Automatically activated the first time you use Dialogflow CX in a GCP project.
- **What you get:** **$600 in credits** valid for 12 months.
- **How to use it here:** While `vromvrom-engine` builds its own DAG agents, if you decide to delegate some conversational routing or intent recognition to a Dialogflow CX agent, those API calls will be covered by this specific credit.

### 3. Vertex AI Agent Builder (Search & Conversation) $1,000 Free Trial
- **What it is:** A massive credit strictly reserved for Vertex AI Search and Conversation.
- **How to get it:** Automatically applied to new users trying out Agent Builder / Vertex AI Search.
- **What you get:** **$1,000 in credits** valid for 1 year.
- **How to use it here:** If you want to index thousands of personal PDFs or enterprise documents without building the vector DB locally, you can create a Data Store in Vertex AI Search, and have `vromvrom-engine` query it via the Google Cloud API. The indexing and querying costs are covered by this $1k credit (note: it does *not* cover general Gemini API calls, only Search/Conversation usage).

*Mix these with the Free & Local Strategy, and you have enterprise-grade infrastructure running for $0.*

---

*Last updated: 2026-07 — Model landscape evolves fast. The Elo router adapts automatically.*
