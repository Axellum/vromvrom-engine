<div align="center">

# ⚡ tab5-engine

**Moteur d'orchestration multi-agents LLM — asynchrone, hybride, auto-optimisant**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136%2B-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![asyncio](https://img.shields.io/badge/asyncio-native-green)](https://docs.python.org/3/library/asyncio.html)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-54%20fichiers%20pytest-brightgreen)](tests/)

*Construit pour Home Assistant · Fonctionne avec n'importe quel projet*

</div>

---

## 🎯 C'est quoi ?

**tab5-engine** est un moteur d'orchestration multi-agents entièrement asynchrone (`asyncio`) qui coordonne plusieurs LLMs pour résoudre des tâches complexes. Il est né du besoin de piloter un écran domotique [M5Stack Tab5](https://docs.m5stack.com/en/core/tab5) via Home Assistant — mais son architecture est générique et réutilisable pour tout projet.

### Ce qui le distingue

| Fonctionnalité | Description |
|---|---|
| 🧠 **Routage hybride 4 niveaux** | Regex → ML (sklearn) → LLM → Elo scoring — 0ms à 200ms selon la complexité |
| 🏆 **Elo scoring dynamique** | Chaque modèle gagne/perd des points Elo par domaine de tâche. Le meilleur modèle est sélectionné automatiquement |
| 🔄 **DAG parallèle async** | Les tâches indépendantes s'exécutent en parallèle via `asyncio.gather()` |
| 🛡️ **Self-Healing** | Circuit breaker + retry avec backoff + fallback automatique entre providers |
| 💰 **Cost-aware** | Cascade Elo+Coût : commence par le provider le moins cher, escalade si nécessaire |
| 🔍 **RAG hybride local** | TF-IDF + BM25 + ChromaDB Embeddings fusionnés par RRF (k=60) — sans appel cloud |
| 👁️ **HITL** | Pause/reprise de l'orchestration pour validation humaine via l'IHM |
| 📊 **IHM FastAPI** | Dashboard glassmorphism HTML/JS avec SSE temps-réel, éditeur de workflows |
| 🌐 **Swarm distribué** | Dispatch de tâches vers des workers distants (Raspberry Pi, VM, etc.) |
| 🔌 **Plugin system** | Ajout d'agents custom via `plugins/<nom>/agent.py` + `plugin.json` |

---

## 🏗️ Architecture

```
tab5-engine/
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
│   ├── models_db.py       # SSOT SQLite catalogue modèles/tarifs/quotas
│   └── ...
│
├── agents/                # Agents spécialisés
│   ├── planner_agent.py   # Découpe la tâche en DAG JSON
│   ├── executor_agent.py  # Exécute les tâches (ReAct loop + outils)
│   ├── reviewer_agent.py  # Vérifie la qualité du résultat
│   ├── antigravity_agent.py # Agent expert avec accès IDE
│   └── tool_maker_agent.py  # Génère de nouveaux outils Python
│
├── memory/                # Mémoire sémantique + RAG
│   ├── rag.py             # RAG hybride (TF-IDF + BM25 + Embeddings + RRF)
│   ├── facts.py           # Base de faits (FTS5 BM25 SQLite)
│   ├── episodes.py        # Mémoire épisodique (Jaccard similarity)
│   ├── embeddings.py      # ChromaDB vectoriel
│   └── skills.py          # Mémoire procédurale (séquences d'outils réussies)
│
├── tools/                 # Outils utilisables par les agents
│   ├── tool_registry.py   # Registre avec timeouts ciblés
│   └── sanitizer.py       # Masquage des secrets (6 patterns)
│
├── api/routes/            # 15 modules de routes FastAPI
├── services/              # Logique métier découplée du HTTP
├── plugins/               # Plugins custom (chargement dynamique)
├── workflows/             # Définitions de workflows JSON (graphes)
├── static/                # IHM HTML/JS/CSS (glassmorphism)
├── tests/                 # 54 fichiers de tests pytest
└── docs/                  # Documentation architecture
```

### Pipeline de routage hybride

```
Requête utilisateur
       │
       ▼
┌─────────────────────┐
│ 1. Fast-Path Regex  │ ──→ 0ms   (commandes simples détectées par pattern)
└─────────────────────┘
       │ ambiguïté
       ▼
┌─────────────────────┐
│ 2. ML Router sklearn│ ──→ 0ms   (classificateur local, seuil 75%)
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
git clone https://github.com/<ton-username>/tab5-engine.git
cd tab5-engine

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
# → IHM disponible sur http://localhost:8000
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

### Via l'IHM Web

Ouvrir `http://localhost:8000` après `python gui_server.py`.

L'IHM expose :
- **Onglet Chat** : interface conversationnelle avec streaming token-par-token
- **Onglet Workflows** : éditeur visuel des graphes d'exécution
- **Onglet Modèles** : catalogue des LLMs avec scores Elo temps-réel
- **Onglet Supervision** : monitoring des workers Swarm
- **Onglet Données** : exploration des bases SQLite

### Via l'API REST

```python
import httpx

# Exécuter une tâche
response = httpx.post(
    "http://localhost:8000/api/execute",
    json={"message": "Analyse ce code Python et propose des améliorations"},
    headers={"Authorization": "Bearer <MOTEUR_API_KEY>"}
)
print(response.json()["result"])
```

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
| **Mistral** | 💰 | Modèles européens RGPD-friendly |
| **OpenRouter** | 💰 | Agrégateur (accès à 200+ modèles) |
| **LM Studio** | ✅ | Inférence locale (Qwen, Llama, Mistral...) |
| **Ollama** | ✅ | Inférence locale alternative |
| **MiniMax** | 💰 | Raisonnement avec `<think>` intégré |
| **Cohere, Cerebras, xAI...** | 💰 | Providers additionnels configurables |

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

## 🤝 Contribution

Les contributions sont les bienvenues ! Voir [CONTRIBUTING.md](CONTRIBUTING.md).

**Axes prioritaires :**
- 🐳 Dockerisation (image minimale)
- 📖 Documentation des APIs
- 🧪 Augmentation de la couverture de tests
- 🌍 Support de nouveaux providers LLM
- 🎨 Améliorations de l'IHM

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
