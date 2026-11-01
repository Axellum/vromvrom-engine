<div align="center">

<img src="docs/logo.svg" alt="" width="96" height="96">

# ⚡ vromvrom-engine

**Un cerveau fait maison pour Home Assistant et vos fournisseurs de LLM : des règles d'abord, un modèle seulement quand il sert.**

*Vroum Vroum 🏁*

[![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python)](https://python.org)
[![License](https://img.shields.io/github/license/Axellum/vromvrom-engine)](LICENSE)
[![CI](https://github.com/Axellum/vromvrom-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/Axellum/vromvrom-engine/actions/workflows/ci.yml)
[![Python CI & Security](https://github.com/Axellum/vromvrom-engine/actions/workflows/python-tests.yml/badge.svg)](https://github.com/Axellum/vromvrom-engine/actions/workflows/python-tests.yml)

🇬🇧 **[English version → README.md](README.md)** &nbsp;·&nbsp; 📖 **[Guide d'utilisation → docs/guide](docs/guide/README.md)** &nbsp;·&nbsp; 🗺️ **[Quels LLMs utiliser ? → STRATEGIES.fr.md](STRATEGIES.fr.md)**

</div>

> **Un projet personnel, utilisé tous les jours dans une maison.** Écrit avec beaucoup d'aide de l'IA et partagé au
> cas où il servirait à quelqu'un, sans promesse de support. Cette page dit ce qui marche, ce qui ne marche pas et à
> quoi le comparer, avec les chiffres mesurés sur l'installation de l'auteur le 01/11/2026.

## En une minute

vromvrom-engine (le « Moteur IA » de la documentation) est un serveur Python (FastAPI) placé entre ce qui demande
(une discussion, un script, un éditeur de code, un client MCP, un assistant vocal via Home Assistant) et ce qui
répond (environ dix-huit fournisseurs de LLM, des modèles locaux, Home Assistant). Pour chaque demande, il prend le
chemin le moins cher qui suffit :

1. **une règle, sans aucune IA**, quand la phrase est une commande ou une question connue de la maison (« éteins la
   lumière du salon », « quelle température dans la chambre ? ») ;
2. **un modèle** sinon, choisi selon le coût, le quota et un score par type de tâche (Elo), avec une bascule quand un
   fournisseur échoue ;
3. **un plan de plusieurs agents** (planificateur, exécutants, relecteur) pour une tâche complexe : la partie la moins
   aboutie, voir plus bas.

Il compte chaque jeton et chaque centime, et montre ce qu'il a fait. Tout tient dans des fichiers SQLite, sans serveur
de base de données à faire tourner : l'instance de l'auteur tourne sur un Steam Deck, jour et nuit. Il est né pour
donner une voix à un écran mural Home Assistant ([M5-Tab5-ESPHome-LVGL](https://github.com/Axellum/M5-Tab5-ESPHome-LVGL)).

## Ce qui marche aujourd'hui

Mesuré sur l'installation de l'auteur (un foyer, en français, avec Home Assistant) le 01/11/2026, sauf mention contraire.

| Partie | État | Preuve |
|---|---|---|
| Commandes et questions de la maison sans modèle | ✅ Utilisé tous les jours | 110 à 196 ms côté moteur pour une phrase du catalogue. Le passage vocal complet prend environ 4 s, surtout à cause de la reconnaissance vocale dans Home Assistant. |
| Discussion avec un modèle, plusieurs fournisseurs, bascule | ✅ Utilisé tous les jours | Cascade triée par coût, Elo et quota ; un disjoncteur par fournisseur ; une sonde de santé qui coupe les modèles en panne. 48 réponses vocales en 7 jours : médiane 0,86 s, 90 % sous 4,6 s (discussion comprise). |
| Coût | ✅ | 86 vrais appels en 30 jours ont coûté 0,81 $ en tout. |
| Point d'accès compatible OpenAI (`/v1/chat/completions`, `/v1/models`) | ✅ Dans le code, testé | Streaming ; trois fichiers de tests. Branchez-y un éditeur (Cursor, Cline, Continue) ou n'importe quel client OpenAI. |
| MCP, dans les deux sens | 🟡 Testé, peu utilisé | Un serveur de 17 outils (Home Assistant, mémoire, orchestrateur) et un client de serveurs tiers. |
| Sauvegarde des bases | 🆕 Nouveau (novembre 2026) | Copies à chaud et cohérentes, avec manifeste et rétention (`python -m core.sauvegarde`, minuterie systemd dans `deploy/`). |
| Statistiques de coût et d'usage | 🟡 Faussées | Chaque appel est enregistré, mais les sondes de santé sont comptées avec les vrais appels : 27 805 appels comptés en 30 jours, dont 86 vrais. Les séparer est prévu. |
| Routeur ML local | 🧪 Éteint sur une installation neuve | Un classifieur scikit-learn qui s'entraîne sur votre historique dès qu'il compte au moins 50 demandes. |
| Plans multi-agents (DAG) | 🧪 Marche dans les tests, pas dans l'usage quotidien | Aucune tâche complexe en 30 jours sur l'instance de l'auteur. Ne le prenez pas pour un agent de code. |
| Cycle autonome (Dreamer, DreamCoder) | 🧪 Expérimental | Le Dreamer consolide la mémoire toutes les heures ; DreamCoder, qui traite un carnet de tâches, ne trouve plus rien à faire depuis fin août. |
| Voix via Home Assistant Assist | ⚠️ Pas prêt à brancher | L'agent de conversation qui envoie le texte reconnu au moteur (`moteur_ia_conversation`) **n'est pas dans ce dépôt**. Le [guide](docs/guide/04_maison_et_voix.md) dit ce qu'on peut faire sans lui. |

## Pourquoi il peut vous plaire

- **La maison n'attend jamais un modèle de langage.** Les utilisateurs de Home Assistant demandent de ne pas tout
  envoyer au modèle (latence, coût, actions inventées) et d'exposer peu d'entités
  ([fil du forum](https://community.home-assistant.io/t/custom-component-extended-openai-conversation-lets-control-entities-via-chatgpt/636500?page=9)).
  Ici, une phrase connue est une règle, servie en un cinquième de seconde ; le modèle n'intervient qu'en discussion.
- **Le modèle ne peut pas agir seul.** En discussion, il n'a que des outils de **lecture** de Home Assistant. Les
  appareils ne sont commandés que par des règles explicites (le catalogue de phrases `ha_commands.json`, les tables
  de pièces). La contrepartie : une nouvelle commande, c'est une nouvelle ligne dans le catalogue.
- **Une passerelle complète sur une petite machine.** Les clés de nombreux fournisseurs, la bascule, les quotas des
  offres gratuites, les disjoncteurs, le coût de chaque appel, le tout dans SQLite : rien d'autre à installer.
- **Il montre ce qu'il fait.** Audit vocal (texte, chemin pris, latence), historique des sessions, rejeu d'une vraie
  demande dans la page « Comprendre le moteur » ; un chiffre inconnu s'affiche « — », jamais 0.
- **Le français d'abord.** Le catalogue de phrases, la documentation et les commentaires sont en français, avec un
  guide en anglais.

## Ses limites

- **Un seul utilisateur, une seule clé d'API.** Ni comptes, ni rôles, ni connexion unique, ni budget par personne :
  les passerelles d'équipe comme [LiteLLM](https://docs.litellm.ai/docs/proxy/budget_fallbacks) ou les interfaces de
  discussion comme Open WebUI et LibreChat le font.
- **La voix demande un composant qui n'est pas publié** (voir le tableau).
- **Les statistiques sont faussées** par les sondes de santé, tant qu'elles ne sont pas comptées à part.
- **Beaucoup de fournisseurs, pas tous vivants.** Sur l'instance de l'auteur, la sonde de santé a coupé 42 modèles sur
  76 : les fournisseurs morts restent listés.
- **La partie multi-agents est la moins aboutie**, et la moins utilisée.
- **Fait d'abord pour un développeur.** L'interface est riche mais dense ; le code, ses commentaires et beaucoup de ses
  noms sont en français ; environ la moitié du code est couverte par les tests (54 % le 01/11/2026).
- **Un seul mainteneur**, un code écrit en grande partie avec l'IA, aucune promesse de support.

## Est-ce pour vous ?

| Vous voulez | Regardez |
|---|---|
| Une voix Home Assistant qui reste rapide et ne laisse jamais un modèle inventer une action, sur une petite machine, avec des modèles gratuits | **vromvrom-engine** peut convenir (pour la voix, apportez votre propre agent de conversation pour l'instant) |
| Une passerelle pour une équipe : clés, budgets par utilisateur ou par clé, suivi des dépenses | [LiteLLM](https://docs.litellm.ai/docs/proxy/budget_fallbacks) |
| Une interface de discussion pour plusieurs personnes, avec des rôles | Open WebUI, LibreChat |
| Une bibliothèque pour construire des agents dans votre propre code | LangGraph, CrewAI |

---

## ⚙️ Installation rapide

### Prérequis
- Python 3.12+ (la version testée en CI)
- Node.js 20.19+ ou 22.12+ pour construire l'interface React (facultatif)
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
#    (optionnel) IHM React : cd ihm-v2 && npm install && npm run build
python gui_server.py
# → Dashboard sur http://localhost:8000
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

```bash
cd ihm-v2 && npm install && npm run build   # → ihm-v2/dist/
python gui_server.py                        # http://localhost:8000
```

Sans `ihm-v2/dist`, le moteur retombe sur l'IHM legacy `static/` (aussi sous `/v1`).

Dev avec HMR : `cd ihm-v2 && npm run dev` (Vite `:5173`, proxy `/api` vers `:8000`).

L'IHM React inclut l'Accueil (état en une phrase), Comprendre le moteur, Démo vocale & maison, Chat (SSE + outils), Home Assistant, Modèles & routage, Outils MCP, Sauvegardes, Prompt Studio, observabilité et configuration. Visite guidée : [docs/guide](docs/guide/README.md).

### Via l'API REST

```python
import httpx

response = httpx.post(
    "http://localhost:8000/api/execute",
    json={
        "user_prompt": "Analyse ce code Python et propose des améliorations",
        "source": {"type": "web", "mode": "chat"},
    },
    headers={"Authorization": "Bearer <MOTEUR_API_KEY>"},
    timeout=120,
)
print(response.json()["response"])
```

### Comme Proxy pour IDEs (Cursor, Cline, Continue)

vromvrom-engine expose un point d'accès standard compatible OpenAI. Vous pouvez y brancher votre assistant de code favori pour profiter de son routage, de sa bascule et de ses disjoncteurs :

- **Base URL** : `http://localhost:8000/v1`
- **API Key** : `<MOTEUR_API_KEY>` (celle de votre `.env`)
- **Model** : un identifiant listé par `GET /v1/models`, par exemple `gemini-3.5-flash-free` (le modèle par défaut de `config.example.json`)

### 🏠 Home Assistant (optionnel)

vromvrom-engine fournit des outils (`tools/ha_entity_ingest.py`), des routes (`/api/ha/*`) et des outils MCP pour lire votre instance Home Assistant.

Ajoutez simplement ceci à votre `.env` :
```env
HASS_URL=http://192.168.1.X:8123
HASS_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```
Le modèle peut alors lire les états et les capteurs. Les appareils sont commandés par les règles du catalogue de phrases (`ha_commands.json`, à partir de `ha_commands.json.example`), pas par le modèle. Pour la voix, voir [le guide](docs/guide/04_maison_et_voix.md) : l'agent de conversation dont Home Assistant a besoin n'est pas dans ce dépôt.

### Via la CLI

```bash
python main.py "Explique l'architecture hexagonale en 3 points"
```

---

## 🧩 Les fonctions en détail

| Fonction | Ce qu'elle fait |
|---|---|
| 🧠 **Routage hybride** | Regex (chemin rapide) → classifieur ML local (une fois entraîné) → un modèle léger pour les demandes ambiguës → score Elo par type de tâche pour choisir le modèle |
| 🏆 **Elo par type de tâche** | Chaque modèle gagne ou perd des points par domaine ; la cascade s'en sert avec le coût et le quota |
| 🛡️ **Auto-réparation** | Un disjoncteur par fournisseur, des reprises avec attente exponentielle, la bascule vers le fournisseur suivant |
| 💰 **Cascade selon le coût** | Bascule vers le fournisseur suivant configuré s'il est indisponible, limité en débit ou hors budget — ce n'est pas une escalade par qualité |
| 🔄 **DAG parallèle asynchrone** | Les tâches indépendantes d'un plan s'exécutent en parallèle (`asyncio.gather()`) |
| 🔍 **RAG hybride local** | TF-IDF + BM25 + embeddings ChromaDB fusionnés par RRF (k=60), sur votre machine |
| 👁️ **HITL** | Pause et reprise d'un plan pour une validation humaine (plans à risque élevé) |
| 🔌 **API compatible OpenAI** | `/v1/chat/completions` (avec streaming) et `/v1/models`, pour Cursor, Cline, Continue.dev ou n'importe quel client OpenAI |
| 🧰 **MCP** | Un serveur de 17 outils et un client de serveurs MCP tiers |
| 🏠 **Home Assistant** | Routes `/api/ha`, ingestion des entités et outils de lecture ; les appareils sont commandés par le catalogue de phrases |
| 🌐 **Machines distantes** | Envoi de tâches à d'autres machines (Raspberry Pi, VM…) |
| 🔌 **Plugins** | Agents personnalisés dans `plugins/<nom>/agent.py` + `plugin.json` |
| 📊 **Interface** | Interface React (`ihm-v2/`), avec l'ancienne interface HTML de `static/` en secours |

## 🏗️ Architecture

```
vromvrom-engine/
├── gui_server.py          # Point d'entrée FastAPI (lifespan + 34 routeurs)
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
├── api/routes/            # 34 modules de routes FastAPI
├── services/              # Logique métier découplée du HTTP
├── plugins/               # Plugins custom (chargement dynamique)
├── workflows/             # Définitions de workflows JSON (graphes)
├── static/                # IHM HTML/JS (repli si ihm-v2 n'est pas buildée)
├── ihm-v2/                # IHM React/Vite/TS (Chat, HA, catalogue LLM, Prompt Studio…)
├── tests/                 # plus de 280 fichiers de tests pytest
└── docs/                  # Documentation architecture + guide d'utilisation (docs/guide/)
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
│ 3. LLM Slow-Path    │ ──→ ~200ms (un modèle léger pour lever l'ambiguïté)
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

L'étape 2 ne tourne qu'une fois le classifieur entraîné sur au moins 50 demandes de votre historique ; la partie multi-agents (dernière ligne) ne sert qu'aux tâches complexes.

---

## 🆕 Nouveautés récentes (novembre 2026)

- **Une interface qui explique le moteur.** Navigation par intention (Découvrir · Utiliser · Régler · Surveiller · Avancé). L'*Accueil* dit en une phrase si tout va bien et pourquoi, avec des chiffres sourcés ; *Comprendre le moteur* montre le trajet d'une demande sur une carte cliquable et peut rejouer une vraie demande ; *Démo vocale & maison* détaille chaque maillon, du mot d'éveil à la réponse dite. Les pages principales ont un bloc « Comprendre cette page » repliable et citent la source de leurs chiffres ; une donnée inconnue s'affiche « — », jamais zéro.
- **Sauvegarde des bases.** Copie à chaud et cohérente des bases SQLite et des fichiers de configuration, avec manifeste (sha256, contrôle d'intégrité) et rétention : `python -m core.sauvegarde`, `GET/POST /api/sauvegardes`, page *Sauvegardes*, minuterie systemd dans `deploy/`. Jamais le `.env`.
- **Simulation vocale sans effet.** `POST /api/vocal/simulate` montre le chemin qu'une phrase prendrait (commande Home Assistant, question d'état, discussion) **sans rien exécuter**.
- **Vue des outils MCP.** `GET /api/mcp/overview` et la page *Outils MCP* listent les outils offerts par le moteur et les serveurs qu'il utilise.
- **Erreurs plus sûres.** Les clés d'API sont masquées dans les erreurs de santé des fournisseurs (`/api/models-health`), à l'écriture comme à la lecture.

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

Le tableau donne les principaux ; le code en connaît environ dix-huit. Un fournisseur listé n'est pas la promesse qu'il répond aujourd'hui : la sonde de santé coupe les modèles en panne.

---

## 🧪 Tests

288 fichiers de tests (environ 2 370 fonctions de test), lancés par la CI sous Python 3.12. Environ la moitié du code est couverte (54 % mesurés le 01/11/2026).

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
- [x] 🐳 Dockerisation (image minimale + docker-compose)
- [x] 📖 Documentation des APIs (voir [docs/API_REFERENCE.fr.md](docs/API_REFERENCE.fr.md))
- [ ] 🧪 Augmentation de la couverture de tests
- [ ] 🌍 Support de nouveaux providers LLM
- [ ] 🎨 Améliorations de l'interface

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
