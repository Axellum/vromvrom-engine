<div align="center">

# ⚡ vromvrom-engine

**Moteur d'orchestration multi-agents LLM — asynchrone, hybride, auto-optimisant**

*Vroom Vroom — ça tourne à plein régime 🏁*

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136%2B-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![asyncio](https://img.shields.io/badge/asyncio-native-green)](https://docs.python.org/3/library/asyncio.html)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-60%20fichiers%20pytest-brightgreen)](tests/)

*Construit pour Home Assistant · Fonctionne avec n'importe quel projet*

> ⚠️ **État du projet : En développement actif**
> Ce moteur est un outil personnel pointu partagé avec la communauté. Bien que l'architecture backend (DAG, async, routage Elo) soit robuste et utilisée quotidiennement, **l'interface web (IHM) est actuellement en construction**. Attendez-vous à des aspérités. C'est un projet expérimental destiné aux makers.

🇬🇧 **[English version → README.md](README.md)** &nbsp;·&nbsp; 🗺️ **[Quels LLMs utiliser ? → STRATEGIES.fr.md](STRATEGIES.fr.md)**

</div>

---

> **Arrêtez de configurer. Commencez à apprendre.**
> vromvrom-engine achemine automatiquement vos tâches vers le bon LLM —
> en utilisant un système de score Elo qui s'adapte à votre propre historique d'utilisation.
> Asynchrone, optimisé pour les coûts, auto-réparateur. Construit pour le budget d'un Raspberry Pi.

## 🎯 C'est quoi ?

**vromvrom-engine** est un moteur d'orchestration multi-agents entièrement asynchrone (`asyncio`) qui coordonne plusieurs LLMs pour résoudre des tâches complexes. Il est né du besoin de piloter un écran domotique [M5Stack Tab5](https://docs.m5stack.com/en/core/tab5) via Home Assistant — mais son architecture est générique et réutilisable pour tout projet.

> 💡 **Expérimentez GPT-4o & Llama 70B gratuitement**
> Le moteur est préconfiguré pour se connecter à l'API [GitHub Models](https://github.com/marketplace/models), offrant un accès gratuit (soumis à quotas) à des modèles comme **GPT-4o** et **Llama 70B** pour vos essais, en utilisant simplement votre compte GitHub standard.

### Ce qui le distingue

| Fonctionnalité | Description |
|---|---|
| 🧠 **Routage hybride 4 niveaux** | Regex → ML (sklearn) → LLM → Elo scoring — 0ms à 200ms selon la complexité |
| 🏆 **Elo scoring dynamique** | Chaque modèle gagne/perd des points Elo par domaine de tâche. Le meilleur est sélectionné automatiquement |
| 🔄 **DAG parallèle async** | Les tâches indépendantes s'exécutent en parallèle via `asyncio.gather()` |
| 🛡️ **Self-Healing** | Circuit breaker + retry avec backoff exponentiel + fallback automatique entre providers |
| 💰 **Cost-aware** | Cascade par disponibilité/budget : bascule vers le provider suivant configuré s'il est indisponible, limité en débit ou hors budget — ce n'est pas une escalade par qualité |
| 🔍 **RAG hybride local** | TF-IDF + BM25 + ChromaDB Embeddings fusionnés par RRF (k=60) — sans appel cloud |
| 👁️ **HITL** | Pause/reprise de l'orchestration pour validation humaine |
| 📊 **Dashboard FastAPI** | Interface glassmorphism HTML/JS avec SSE temps-réel, éditeur de workflows, graphiques Elo |
| 🔌 **API Proxy Drop-in** | API 100% compatible OpenAI (`/v1/chat/completions`). Branchez Cursor, Cline ou Continue.dev directement sur le moteur |
| 🌐 **Swarm distribué** | Dispatch de tâches vers des workers distants (Raspberry Pi, VM, etc.) |
| 🔌 **Système de plugins** | Ajout d'agents custom via `plugins/<nom>/agent.py` + `plugin.json` |

---

## 🏗️ Architecture

```
vromvrom-engine/
├── gui_server.py          # Point d'entrée FastAPI (lifespan + 15 routeurs)
├── main.py                # CLI — lancement direct sans HTTP
│
├── core/                  # Cœur du moteur
│   ├── engine.py          # Orchestrateur principal (DAG → Agents)
│   ├── llm_gateway.py     # Gateway multi-providers (18+ providers)
│   ├── router.py          # Routage hybride (fast-path + ML + LLM + Elo)
│   ├── dag_runner.py      # Exécution parallèle async par stages
│   ├── factory.py         # Instanciation des agents (Planner/Executor/Reviewer)
│   ├── state.py           # GlobalState Pydantic thread-safe
│   ├── checkpoint.py      # Snapshots ACID (SQLite WAL)
│   ├── healing.py         # Self-healing + retry
│   ├── review_loop.py     # Boucle Reviewer → Correction
│   ├── elo_scorer.py      # Elo des modèles par domaine
│   ├── elo_router.py      # Elo des types de routage
│   ├── circuit_breaker.py # Disjoncteur async (CLOSED/OPEN/HALF_OPEN)
│   ├── hitl.py            # Human-In-The-Loop (asyncio.Event)
│   ├── models_db.py       # SSOT SQLite — catalogue modèles/tarifs/quotas
│   └── ...
│
├── agents/                # Agents spécialisés
│   ├── planner.py         # Découpe la tâche en DAG JSON
│   ├── executor.py        # Exécute les tâches (ReAct loop + outils)
│   ├── reviewer.py        # Vérifie la qualité du résultat
│   └── tool_maker_agent.py  # Génère de nouveaux outils Python automatiquement
│
├── memory/                # Mémoire sémantique + RAG
│   ├── rag.py             # RAG hybride (TF-IDF + BM25 + Embeddings + RRF)
│   ├── facts.py           # Base de faits (SQLite FTS5 BM25)
│   ├── episodes.py        # Mémoire épisodique (similarité Jaccard)
│   ├── embeddings.py      # Stockage vectoriel ChromaDB
│   └── skills.py          # Mémoire procédurale (séquences d'outils réussies)
│
├── tools/                 # Outils utilisables par les agents
│   ├── tool_registry.py   # Registre avec timeouts ciblés par outil
│   └── sanitizer.py       # Masquage des secrets (6 patterns)
│
├── api/routes/            # 15 modules de routes FastAPI
├── services/              # Logique métier découplée du HTTP
├── plugins/               # Plugins custom (chargement dynamique)
├── workflows/             # Définitions de workflows JSON (graphes)
├── static/                # IHM HTML/JS/CSS (glassmorphism, legacy)
├── ihm-v2/                # Nouvelle IHM React/Vite/TS (en cours, remplacera static/)
├── tests/                 # 60 fichiers de tests pytest
└── docs/                  # Documentation architecture
```

### Pipeline de routage hybride

```
Requête utilisateur
       │
       ▼
┌─────────────────────┐
│ 1. Fast-Path Regex  │ ──→ 0ms    (commandes simples détectées par pattern)
└─────────────────────┘
       │ ambiguïté
       ▼
┌─────────────────────┐
│ 2. ML Router sklearn│ ──→ 0ms    (classificateur local, seuil 75%)
└─────────────────────┘
       │ confiance < 75%
       ▼
┌─────────────────────┐
│ 3. LLM Slow-Path    │ ──→ ~200ms (Gemini Flash pour lever l'ambiguïté)
└─────────────────────┘
       │
       ▼
┌─────────────────────┐
│ 4. Elo Scoring      │ ──→ sélection du meilleur modèle pour la tâche
└─────────────────────┘
       │
       ▼
  Planner → DAG → Executor(s) parallèles → Reviewer → Réponse
```

---

## ⚙️ Installation rapide

### Prérequis
- Python 3.11+
- Au moins **une clé API LLM** (Gemini gratuit fonctionne, voir `.env.example`)

### Étapes

```bash
# 1. Cloner le repo
git clone https://github.com/Axellum/vromvrom-engine.git
cd vromvrom-engine

# 2. Environnement virtuel
python -m venv .venv
# Windows :
.venv\Scripts\activate
# Linux/Mac :
source .venv/bin/activate

# 3. Dépendances
pip install -r requirements.txt

# 4. Configuration
cp .env.example .env
# Éditer .env et renseigner au minimum GEMINI_API_KEY (gratuit sur aistudio.google.com)

# 5. Config du moteur
cp config.example.json config.json
# Optionnel : ajuster les modèles par défaut

# 6. Lancer
python gui_server.py
# → Dashboard disponible sur http://localhost:8000
```

### Configuration minimale (`.env`)

Le moteur fonctionne avec **une seule clé gratuite** :

```env
# Clé Gemini AI Studio (gratuite) : https://aistudio.google.com/apikey
GEMINI_API_KEY=AIza...

# Clé d'authentification de l'API locale (générer une valeur aléatoire)
MOTEUR_API_KEY=changez-moi-avec-secrets-token-urlsafe-32
```

Tous les autres providers (DeepSeek, Anthropic, Mistral...) sont **optionnels** — le moteur se dégrade gracieusement.

---

## 🚀 Utilisation

### Via le Dashboard Web

Ouvrir `http://localhost:8000` après `python gui_server.py`.

Le dashboard expose :
- **Onglet Chat** : interface conversationnelle avec streaming token-par-token
- **Onglet Workflows** : éditeur visuel des graphes d'exécution
- **Onglet Modèles** : catalogue des LLMs avec scores Elo temps-réel
- **Onglet Supervision** : monitoring des workers Swarm
- **Onglet Données** : exploration des bases SQLite

### Via l'API REST

```python
import httpx

response = httpx.post(
    "http://localhost:8000/api/execute",
    json={"message": "Analyse ce code Python et propose des améliorations"},
    headers={"Authorization": "Bearer <MOTEUR_API_KEY>"}
)
print(response.json()["result"])
```

### Comme Proxy pour IDEs (Cursor, Cline, Continue)

vromvrom-engine expose un point d'accès standard compatible OpenAI. Vous pouvez y brancher votre assistant de code favori pour bénéficier du routage gratuit et du disjoncteur (Circuit Breaker) :

- **Base URL** : `http://localhost:8000/v1`
- **API Key** : `<MOTEUR_API_KEY>` (celle de votre `.env`)
- **Model** : `gemini-2.0-flash` (ou n'importe quel modèle de votre routage)

### Via la CLI

```bash
python main.py "Explique l'architecture hexagonale en 3 points"
```

---

## 🔌 Providers LLM supportés

| Provider | Gratuit | Commentaire |
|---|---|---|
| **Gemini** (Google AI Studio) | ✅ | Recommandé pour démarrer — free tier généreux |
| **GitHub Models** | ✅ | GPT-4o, Llama 3.3 70B via token GitHub |
| **DeepSeek** | 💰 | Excellent rapport qualité/prix (~$0.14/M tokens) |
| **Anthropic Claude** | 💰 | Via API ou Claude Code CLI (abonnement Pro) |
| **Mistral** | ✅ | Modèles européens RGPD-friendly — free tier |
| **OpenRouter** | 💰 | Agrégateur (200+ modèles) |
| **LM Studio** | ✅ | Inférence locale (Qwen, Llama, Mistral...) |
| **Ollama** | ✅ | Inférence locale alternative |
| **MiniMax** | 💰 | Raisonnement avec `<think>` intégré |
| **Cohere** | ✅ | Modèles gratuits en trial (Command R / R+) |
| **Cerebras** | ✅ | 100% gratuit — inférence ultra-rapide (GPT-OSS 120B, GLM 4.7) |
| **Zhipu AI (GLM)** | 💰 | 8 modèles GLM, excellents en code/agentique |
| **xAI (Grok)** | 💰 | Payant uniquement |

---

## 🧪 Tests

```bash
# Installer les dépendances de dev
pip install -r requirements-dev.txt

# Lancer tous les tests
pytest

# Avec couverture
pytest --cov=. --cov-report=html
```

---

## 🗺️ Quels LLMs utiliser ?

Consultez **[STRATEGIES.fr.md](STRATEGIES.fr.md)** pour 4 configurations prêtes à l'emploi :
- 🆓 Gratuit & Local uniquement — 0 €/mois
- 🐉 Modèles chinois — ~5–15 €/mois, qualité proche de l'état de l'art
- ⚡ API/CLI Claude — coût additionnel quasi nul avec un abonnement Pro
- 🚀 Pleine puissance + coût optimisé — état de l'art à un coût ~10× moindre

---

## 🤝 Contribution

Les contributions sont les bienvenues ! Voir [CONTRIBUTING.md](CONTRIBUTING.md).

**Axes prioritaires :**
- 🐳 Dockerisation (image minimale)
- 📖 Documentation des APIs
- 🧪 Augmentation de la couverture de tests
- 🌍 Support de nouveaux providers LLM
- 🎨 Améliorations de l'interface

---

## 📄 Licence

MIT — Voir [LICENSE](LICENSE).

---

## 🙏 Inspirations

- [LangChain](https://github.com/langchain-ai/langchain) — patterns d'orchestration
- [CrewAI](https://github.com/crewAIInc/crewAI) — coordination multi-agents
- [Home Assistant](https://github.com/home-assistant/core) — écosystème domotique
- [FastAPI](https://github.com/tiangolo/fastapi) — framework API

---

<div align="center">
<sub>Construit avec ❤️ pour la communauté domotique et IA open-source</sub>
</div>
