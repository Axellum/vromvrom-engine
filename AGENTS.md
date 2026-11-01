# AGENTS.md — tab5-engine

Tu travailles **dans ce dépôt**. Le parapluie `.\contexte_ia\` est le journal d'écosystème (Tab5, HA, sessions). Ici = source de vérité du moteur.

## Lire, dans cet ordre

0. Pour comprendre ce que fait le moteur (humains et agents) : `docs/guide/README.md` puis `docs/guide/02_comprendre.md`
1. `docs/travail/INDEX.md` — carte du kit
2. `docs/travail/OBJECTIFS.md` — le nord
3. `docs/travail/TACHES.md` — board ouvert
4. `docs/travail/INVARIANTS.md` — ce qu'on ne casse jamais
5. Le fichier du couloir que tu touches (`docs/travail/PLAN.md`)

N'ouvre **pas** `docs/travail/TACHES_archive.md` ni `contexte_ia/historique/` sauf demande explicite.

## Règles

- **Git > board.** Si `TACHES.md` et `git log origin/master` divergent, git a raison.
- **Mesure avant de coder.** Une campagne / un rejeu vaut mieux qu'un correctif spéculatif.
- **Un fait = un fichier.** Objectif dans `OBJECTIFS.md`, tâche dans `TACHES.md`, décision dans `DECISIONS.md`.
- **Ne déclare un succès qu'après vérification** (test, HTTP 200, relecture).
- Commentaires de code en français. Jamais de `.db` / `.env` / clé commités.
- `master` est protégée : branche + PR.

## Prompts des agents du moteur

Les personnalités vivent dans `prompts/agents/<nom>.md`, chargées par `core/prompt_loader.py`. Le Python garde un repli si le fichier manque (prod Deck).

## Cursor Cloud specific instructions

Le refresh des deps au boot est dans `.cursor/environment.json` (`install`) : `scripts/install.sh` (venv + `requirements.txt` + `.env` + seed `models_registry.db`), puis `requirements-dev.txt`, puis `npm ci` + build `ihm-v2`. Ne relance pas ça dans `start` — le terminal `gui-server` démarre seulement `gui_server.py`. Commandes standard : `README.md`, `scripts/install.sh` / `scripts/install.ps1`, `pyproject.toml`.

- **Service requis** : `gui_server.py` (FastAPI, port **8000**). Sonde publique `GET /healthz`. IHM v2 servie sur `/` si `ihm-v2/dist` existe, sinon fallback `static/` (v1).
- **Auth fail-closed** : `MOTEUR_API_KEY` dans `.env` (générée par l'install, jamais commitée). Routes `/api` → Bearer `Authorization`. Sans clé : 503. `/healthz` reste public.
- **Optionnel, le moteur boot sans eux** : clés LLM, Home Assistant, MQTT, Ollama/AJEAN, Langfuse, serveurs MCP. Un chat/DAG qui a besoin d'un LLM échouera proprement ; le small-talk HA (`POST /api/execute` + `source.mode=ha`, prompt `bonjour`) ne passe pas par un provider.
- **Windows (cette machine) vs Cloud Linux** : venv = `.venv\Scripts\python.exe` ici, `.venv/bin/python` sur l'image Cloud. `:8000` est souvent déjà pris par une instance locale (écoute `0.0.0.0`) : lancer `.venv\Scripts\python.exe gui_server.py --host 127.0.0.1 --port 8021`. Cloud : le terminal `gui-server` reste sur `:8000`.
- **Un seul `gui_server` par worktree.** Les bases SQLite (`moteur_runtime.db`, `models_registry.db`, locks `filelock`) sont locales au dépôt : une seconde instance (même sur un autre port) reste coincée au lifespan. Vérifier le listener avant de relancer.
- **IHM v2 en HMR** : `npm run dev` dans `ihm-v2/` (Vite :5173, proxy `/api` → :8000). Inutile si `ihm-v2/dist` a déjà été buildé par `install`.
- **`python gui_server.py`** passe l'objet `app` à uvicorn (évite le double import). Ne pas relancer via `python -m uvicorn gui_server:app` en plus du terminal déjà ouvert.
- **Lint / tests** : `ruff` est informatif en CI (dette legacy). `pytest` (marqueurs `not live` par défaut) ; seed `models_registry.db` obligatoire avant `tests/test_models_db.py`. Un worktree n'est pas la prod : pas de `.env` / bases seedées partagées (`INVARIANTS.md` §4).
