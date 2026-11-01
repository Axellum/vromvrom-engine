# Installer le Moteur IA

> **En une phrase.** Cloner le dépôt, lancer le script d'installation, copier `config.example.json` en
> `config.json`, démarrer `gui_server.py`, puis vérifier avec `/healthz` et une première demande. Compter une
> dizaine de minutes, dont l'essentiel est le téléchargement des dépendances.

Cette page suit l'ordre d'une première installation. Chaque affirmation vient du code du dépôt (fichier cité) ou d'un
essai fait pour écrire ce guide (signalé « essayé »). Ce qui n'a pas été essayé est dit « non essayé ».

**Essais faits pour ce guide** : copie propre du dépôt, Windows 11, Python 3.13, démarrage réel du serveur,
`/healthz`, `/version`, `/api/execute`, `/api/vocal/simulate`, `/api/sauvegardes` et `/api/setup/diagnostics`,
avec Windows PowerShell 5.1 et PowerShell 7. **Non essayés** : Linux, macOS, Docker, les unités systemd, le build de
l'interface (le dossier `ihm-v2/dist` était déjà construit).

Pages liées : [README du guide](README.md) · [02 Comprendre](02_comprendre.md) · [03 Réglages](03_reglages.md) ·
[05 Exploiter](05_exploiter.md).

---

## 1. Ce qu'il vous faut

| Élément | Obligatoire ? | Détail | Source |
|---|---|---|---|
| **Python 3.12 ou plus** | Oui | Voir l'encadré ci-dessous. | `README.md`, `.github/workflows/ci.yml` |
| **git** | Pour cloner | Ou télécharger l'archive du dépôt. | — |
| **Au moins une clé de modèle** | Pour tout ce qui n'est pas un raccourci | Une clé Gemini gratuite suffit (aistudio.google.com). Sans clé, le moteur démarre, mais aucune discussion ni tâche ne peut appeler un modèle. | `.env.example`, `api/routes/setup.py` |
| **Node.js 20.19+ ou 22.12+** | Non | Seulement pour construire l'interface web (`ihm-v2`). Sans elle, le moteur sert l'ancienne interface `static/`. | `ihm-v2/package.json` (`engines`) |
| **Home Assistant** | Non | Pour la domotique et la voix. Voir [04 Maison et voix](04_maison_et_voix.md). | — |
| **Docker** | Non | Alternative à l'installation locale (§ 8). | `Dockerfile`, `docker-compose.yml` |

> **Pourquoi Python 3.12 ?** Le code contient au moins une syntaxe réservée à Python 3.12
> (`agents/dreamer_agent.py:2594` : guillemets doubles imbriqués dans un f-string), et ce module est importé au
> démarrage (`gui_server.py:230`). Le script d'installation, le `Dockerfile` et l'intégration continue exigent
> donc 3.12. Le démarrage a été essayé en 3.13.

Aucun matériel particulier n'est requis : le moteur n'a pas besoin de carte graphique. Il tourne sur un PC, un
mini-PC ou une petite machine Linux (un Raspberry Pi ou un Steam Deck, par exemple). La mémoire consommée n'a pas
été mesurée pour ce guide : « — ».

---

## 2. Installer avec le script

Le script fait six choses (en-tête de `scripts/install.ps1` et `scripts/install.sh`) :

1. vérifie la version de Python (≥ 3.12, voir l'encadré) ;
2. crée un environnement virtuel `.venv/` et installe `requirements.txt` ;
3. copie `.env.example` en `.env` **seulement si `.env` n'existe pas** (il n'écrase jamais) ;
4. écrit dans ce `.env` une `MOTEUR_API_KEY` aléatoire de 32 caractères hexadécimaux ;
5. lance `seed_models_db.py` si `models_registry.db` n'existe pas (catalogue de modèles : 97 modèles après le seed,
   constaté lors de l'essai) ;
6. affiche la commande de démarrage.

Il est **idempotent** : le relancer ne casse rien.

### Windows (PowerShell 5.1 ou 7)

```powershell
git clone https://github.com/Axellum/vromvrom-engine.git
cd vromvrom-engine
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

`-ExecutionPolicy Bypass` ne vaut que pour cette commande : la stratégie par défaut de Windows PowerShell 5.1 bloque
souvent les scripts téléchargés.

### Linux et macOS (bash)

```bash
git clone https://github.com/Axellum/vromvrom-engine.git
cd vromvrom-engine
bash scripts/install.sh
```

### Ce que le script NE fait PAS

Il ne copie pas `config.json`. Le README le demande à l'étape « Config du moteur » : **faites-le** (§ 3).

---

## 3. Les fichiers à poser après le script

| Fichier à créer | Modèle | Obligatoire ? | Effet de son absence (essayé) |
|---|---|---|---|
| `config.json` | `config.example.json` | **Recommandé** | Le moteur démarre et répond, mais l'écran « Installation » affiche une erreur « config.json Illisible ». Les modèles des niveaux viennent alors des défauts internes (`core/llm_gateway.py`, fonction `load_config`). |
| `ha_commands.json` | `ha_commands.json.example` | Seulement pour la domotique | Les raccourcis retombent sur des tables de pièces intégrées au code (la simulation vocale reconnaît encore « allume la lumière du salon »). À remplacer par vos propres entités : [04](04_maison_et_voix.md). |
| `pricing_strategy.json` | `pricing_strategy.json.example` | Non | `core/pricing.py` a un barème de repli intégré. |

```powershell
Copy-Item config.example.json config.json
Copy-Item ha_commands.json.example ha_commands.json
Copy-Item pricing_strategy.json.example pricing_strategy.json
```

Sous Linux et macOS, remplacez `Copy-Item` par `cp`.

> **À savoir avant de copier `config.example.json`.** Il active trois agents de fond (`daemon_enabled`,
> `dreamer_enabled`, `auditor_enabled` à `true`). Ils peuvent consommer des jetons dès qu'une clé de modèle est
> présente. Leurs interrupteurs et leur coût sont détaillés dans [05 Exploiter, § 6](05_exploiter.md) et
> [03 Réglages, § 4](03_reglages.md). Si vous préférez démarrer sans eux, passez ces trois valeurs à `false` dans
> `config.json` avant le premier lancement.

---

## 4. Renseigner le `.env`

Ouvrez le `.env` créé par le script. Le minimum :

| Variable | Rôle | Remarque |
|---|---|---|
| `MOTEUR_API_KEY` | Clé que tout client (interface, script, IDE) présente pour appeler `/api`. | Déjà remplie par le script. **Sans elle, toutes les routes `/api` répondent 503** (`core/auth.py`). Si vous installez à la main : `python -c "import secrets;print(secrets.token_urlsafe(32))"`. |
| `GEMINI_API_KEY` | Clé de modèle gratuite. | N'importe quelle clé de fournisseur suffit ; la liste est en [03 Réglages](03_reglages.md). |
| `HASS_URL` | Adresse de Home Assistant. | **Attention** : `.env.example` contient une valeur d'exemple (`http://192.168.1.10:8123`) que le script recopie. Remplacez-la par votre adresse, ou **videz la ligne** si vous n'avez pas Home Assistant. |

Règles à retenir : le `.env` est lu **une seule fois, au démarrage** (toute modification demande un redémarrage) ; il
n'est jamais à commiter (`.gitignore`) ; une variable déjà présente dans l'environnement du processus **gagne sur le
`.env`** (`load_dotenv` sans écrasement). Constaté pendant l'essai : une ancienne `MOTEUR_API_KEY` du shell faisait
répondre 401 malgré la bonne clé dans le `.env`.

---

## 5. Construire l'interface web (facultatif)

```powershell
cd ihm-v2
npm install
npm run build
cd ..
```

Les mêmes commandes fonctionnent sous bash. `npm run build` lance `tsc -b && vite build` (`ihm-v2/package.json`) et
produit `ihm-v2/dist/`.

Au démarrage, `gui_server.py` (lignes ~1291-1300) sert l'interface React sur `/` (l'ancienne sur `/v1`) si `ihm-v2/dist`
existe ; sinon il journalise un avertissement et sert l'ancienne interface `static/` sur `/`. Pour développer avec
rechargement à chaud (`npm run dev`, port 5173, `MOTEUR_CORS_ORIGINS=http://localhost:5173` dans le `.env`) :
[`ihm-v2/README.md`](../../ihm-v2/README.md).

---

## 6. Premier lancement

```text
.\.venv\Scripts\python.exe gui_server.py      (Windows, PowerShell)
.venv/bin/python gui_server.py                (Linux et macOS)
```

Options (`gui_server.py`, bloc `__main__`) :

| Option | Défaut | Rôle |
|---|---|---|
| `--host` | `0.0.0.0` | **Écoute sur toutes les interfaces** : joignable depuis le réseau local. Usage sur un seul poste : `--host 127.0.0.1` (et refusez l'autorisation du pare-feu Windows si elle est demandée). |
| `--port` | variable `PORT`, sinon `8000` | Si le port est pris, en choisir un autre (essayé : le port 8043 a été refusé par Windows, 8361 a fonctionné). |
| `--reload` | désactivé | Rechargement à chaud (développement). |

Lors de l'essai, le serveur répondait en moins de 15 secondes. Le journal affiche « Démarrage complet »
(`gui_server.py:323`) une fois les boucles de fond lancées. Les messages « Fichier introuvable » sur un dossier
`contexte_ia/` sont **normaux** : c'est un dossier de documentation optionnel (`memory/context_loader.py`) absent du
dépôt public.

---

## 7. Vérifier que ça marche

### 7.1 La sonde de vie (sans clé)

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz     # bash : curl http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8000/version     # bash : curl http://127.0.0.1:8000/version
```

Réponses constatées :

```json
{"ok":true,"service":"tab5-engine","status":"idle"}
{"version":"12.1.0","git_hash":"unknown","build_date":"unknown","engine":"moteur_agents","python":"3.13.14"}
```

`/healthz` ajoute un champ `commit` si le dépôt est un clone git. `/healthz` et `/version` sont les deux routes
publiques utiles ; tout `/api/...` exige la clé. `"service":"tab5-engine"` est le nom interne du moteur.

### 7.2 La clé

Sans en-tête `Authorization`, `/api/status` répond **401** « Authentification requise ». Avec une mauvaise clé : **401**
« Token invalide ». Si `MOTEUR_API_KEY` n'est pas définie côté serveur : **503**.

### 7.3 Une première demande, sans fournisseur de modèle

La question de l'heure est traitée par une règle, sans modèle : elle prouve que le moteur répond **sans clé de
fournisseur**.

Windows PowerShell (5.1 ou 7) :

```powershell
# Lire la clé dans le .env
$cle = ((Get-Content .env | Where-Object { $_ -match '^MOTEUR_API_KEY=' }) -replace '^MOTEUR_API_KEY=','').Trim()
$h = @{ Authorization = "Bearer $cle" }

$corps = @{ user_prompt = "Quelle heure est-il ?"; source = @{ type = "web"; mode = "chat" } } | ConvertTo-Json
$r = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/execute -Headers $h `
     -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($corps))
$r.response
```

Linux et macOS :

```bash
CLE=$(grep '^MOTEUR_API_KEY=' .env | cut -d= -f2-)
curl -s -X POST http://127.0.0.1:8000/api/execute \
  -H "Authorization: Bearer $CLE" -H "Content-Type: application/json" \
  -d '{"user_prompt":"Quelle heure est-il ?","source":{"type":"web","mode":"chat"}}'
```

Réponse constatée (PowerShell) : `Il est 18 heures 45.` ; le JSON complet contient `status: "completed"`,
`session_id`, `response`, `history` et `agents_used` (`datetime_query`, `vocal_host`).

> **UTF-8 sous Windows PowerShell 5.1.** Essayé : un corps texte contenant une lettre accentuée (« lumière ») part mal
> encodé et le moteur répond `{"detail":"There was an error parsing the body"}`. Avec
> `[System.Text.Encoding]::UTF8.GetBytes(...)` et `charset=utf-8`, la même demande réussit (PowerShell 7 n'a pas ce
> défaut).

> **Le format du corps.** Le champ obligatoire est `user_prompt` (et non `message`) : essayé, `{"message":"Bonjour"}`
> renvoie **422** « Field required ». Champs : [`docs/API_REFERENCE.fr.md`](../API_REFERENCE.fr.md) et
> `api/routes/agents.py` (`ExecuteRequestBody`) : `source.type`, `source.mode` (`ha`, `chat`, `files`, `default`),
> `tier`, `model`, `session_id`.

### 7.4 Une demande qui appelle un modèle

Avec au moins une clé de fournisseur dans le `.env` (moteur redémarré), reprenez la commande du § 7.3 avec un autre
`user_prompt` (par exemple « Explique en deux phrases ce qu'est un moteur multi-agents. »). **Non essayé** : aucune clé
de fournisseur n'a servi aux essais. Une réponse en erreur, ou « Désolé, je n'ai pas su répondre », veut dire
qu'aucun modèle de la liste de discussion n'a répondu : voir [05 Exploiter, § 4](05_exploiter.md).

### 7.5 L'écran de contrôle

Ouvrez `http://127.0.0.1:8000/` puis le menu **Avancé → Installation** (adresse `#/installation`). Il appelle
`GET /api/setup/diagnostics` (`api/routes/setup.py`) et classe les contrôles en cinq groupes : Authentification, Bases de
données, Configuration, Fournisseurs de modèles, Home Assistant. Sur une installation propre sans clé de fournisseur
(essayé, après la copie du § 3), tout est `ok` ou `info` sauf : un `warn` « chroma_db » absent (recherche lexicale
seulement), un `warn` tant que `HASS_TOKEN` n'est pas défini, et une **`error`** « Aucune clé de provider LLM » tant que
vous n'en posez pas. Le bilan `summary.operational` reste à `false` tant qu'il y a une erreur : c'est attendu.

Pour construire l'index de recherche dans la documentation (`chroma_db`) : `python populate_embeddings.py`. Il utilise
une clé Gemini pour calculer les embeddings (`.env.example`). C'est facultatif.

---

## 8. Docker

Le dépôt fournit un `Dockerfile` et un `docker-compose.yml`. **Non essayé** dans ce guide ; ce qui suit vient de la
lecture des fichiers.

```bash
cp .env.example .env        # puis éditer : MOTEUR_API_KEY et une clé de modèle
docker compose up -d --build
docker compose logs -f
```

Points à connaître :

- `docker-compose.yml` lit un `.env` **que vous devez créer vous-même** (`env_file`), monte le dépôt entier dans
  `/app` et publie le port `8000`.
- `docker-entrypoint.sh` copie `config.example.json` en `config.json` et `.env.example` en `.env` s'ils manquent, puis
  fait un `touch` de `memory.db` et de `models_registry.db`. **`models_registry.db` est donc vide, pas peuplée** :
  lancez `docker compose exec vromvrom-engine python seed_models_db.py` une fois.
- **`MOTEUR_API_KEY` est vide dans `.env.example`** et rien ne la génère en Docker : l'API répond 503 tant que vous ne
  l'avez pas posée.
- L'image n'exécute pas `npm run build` : l'interface React n'est servie que si `ihm-v2/dist` existe sur l'hôte
  (le dépôt est monté dans le conteneur). Sinon c'est l'ancienne interface.
- L'image part de `python:3.12-slim`. (La clé `version: '3.8'` de `docker-compose.yml` est obsolète : au plus
  un avertissement.)

---

## 9. Lancer le moteur en permanence

Le moteur est un processus unique. Il lui faut un lanceur qui le redémarre s'il tombe.

| Plateforme | Ce que fournit le dépôt | Ce qu'il faut adapter |
|---|---|---|
| Windows | `deploy/lancer_moteur.bat` (lance `gui_server.py` dans la console) et `deploy/lancer_moteur_silencieux.bat` (écrit dans `moteur_pc.log`, écoute sur `0.0.0.0:8000`). | Les appeler depuis le Planificateur de tâches (« À l'ouverture de session »). Le second renvoie à un `start_moteur_silent.vbs` qui n'est pas dans le dépôt. |
| Linux | **Aucune unité systemd pour le moteur lui-même.** | À écrire (exemple ci-dessous). |

Exemple d'unité systemd utilisateur pour Linux. **Exemple, non essayé** : chemins et utilisateur à adapter.

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

Pour qu'il démarre sans session ouverte : `loginctl enable-linger $USER`. Le moteur n'écrit pas de fichier journal
(`gui_server.py:57` : sortie standard seulement) ; sous systemd, `journalctl` le conserve. Les fichiers
`deploy/moteur-worker.service`, `deploy/worker_standalone.py` et `deploy/deploy_deck.sh` servent à des usages avancés
(worker distant, déploiement d'une machine précise par git) et ne sont pas nécessaires.

---

## 10. Brancher un client MCP (Claude Desktop, Cursor…)

`mcp_server.py` expose **17 outils** à un client MCP (FastMCP, transport stdio), répartis en trois modules :

| Module | Outils |
|---|---|
| `orchestrator` (8) | `run_tab5_agent`, `query_deepseek`, `get_engine_status`, `get_routing_recommendation`, `get_routing_matrix`, `delegate_complex_reasoning`, `query_llm_direct`, `delegate_to_gateway` |
| `memory` (6) | `get_models_catalog`, `rag_search`, `query_token_usage`, `list_available_models`, `query_runtime`, `search_memory` |
| `homeassistant` (3) | `search_ha_entities`, `validate_config_format`, `execute_ha_action` |

Configuration d'un client (format `mcpServers`), avec **les chemins de votre machine** :

```json
{
  "mcpServers": {
    "moteur": {
      "command": "C:\\chemin\\vers\\vromvrom-engine\\.venv\\Scripts\\python.exe",
      "args": ["C:\\chemin\\vers\\vromvrom-engine\\mcp_server.py"]
    }
  }
}
```

Sous Linux : `"command": "/chemin/vers/vromvrom-engine/.venv/bin/python"`.

Deux pièges :

1. **Le chemin compte** : le serveur charge le `.env` du dossier où se trouve `mcp_server.py`. Le fichier
   `claude_desktop_config.json.example` du dépôt reprend la forme ci-dessus.
2. **`mcp_server.py` n'est pas un client HTTP du serveur `gui_server`.** Il crée sa propre passerelle de modèles dans
   son processus et lit les fichiers et bases du dossier où il est lancé (`core/mcp_tools/orchestrator.py`). Si votre
   moteur tourne sur une autre machine, ces outils n'interrogent **pas** celui-ci.

À l'inverse, le moteur peut **utiliser** des serveurs MCP tiers : copiez `mcp_config.json.example` en
`mcp_config.json` (jamais de secret dedans) ; `core/mcp_bridge.py` le cherche dans `~/.gemini/antigravity-ide/`,
puis à la racine du dépôt. Sans `mcp_activation_rules.json` (liste `default` et règles par mots-clés), **aucun
serveur n'est démarré** (`core/mcp_bridge.py`, lignes ~128-168). Détail : [03 Réglages](03_reglages.md).

---

## 11. Sauvegarde quotidienne

Faites une première sauvegarde à la main pour vérifier :

```powershell
.\.venv\Scripts\python.exe -m core.sauvegarde
.\.venv\Scripts\python.exe -m core.sauvegarde --lister
```

Elle crée un dossier horodaté dans `sauvegardes/` (bases SQLite et configurations, jamais le `.env`). Essayé : la
route `POST /api/sauvegardes` (même code) a produit une sauvegarde saine de 14 fichiers ; la ligne de commande
elle-même n'a pas été lancée. Détails, vérification et restauration : [05 Exploiter](05_exploiter.md).

Sous Linux, le dépôt fournit une unité et un minuteur (`deploy/moteur-sauvegarde.service`, `.timer`) pour une
sauvegarde à 04 h 30. L'unité suppose le dépôt dans `~/vromvrom-engine` et le venv de `scripts/install.sh` :
adaptez `WorkingDirectory` et `ExecStart` si besoin (non essayé).

```ini
WorkingDirectory=%h/vromvrom-engine
ExecStart=%h/vromvrom-engine/.venv/bin/python -m core.sauvegarde
```

Copiez les deux fichiers dans `~/.config/systemd/user/`, puis `systemctl --user enable --now moteur-sauvegarde.timer`.
Le minuteur est `Persistent=true` (une sauvegarde manquée part au démarrage suivant). Sous Windows, aucun minuteur
n'est fourni : créez une tâche planifiée qui lance la commande ci-dessus (non essayé).

---

## 12. Ce qui peut mal tourner

| Symptôme | Cause probable | Vérification |
|---|---|---|
| `ModuleNotFoundError` ou erreur de syntaxe au lancement | Mauvaise version de Python, ou venv non activé | `.\.venv\Scripts\python.exe --version` doit afficher 3.12 ou plus |
| Le port est refusé | Port pris ou réservé | Relancer avec `--port 8361` (ou un autre) |
| 422 « Field required » | Corps `{"message": ...}` au lieu de `user_prompt` | § 7.3 |
| « Aucune clé de provider LLM » | Aucune clé dans le `.env` (ou moteur non redémarré) | § 4 |
| L'interface montre l'ancien design | `ihm-v2/dist` absent | § 5 |

Les autres pannes de départ (503 ou 401 sur `/api`, « config.json Illisible ») ont leur ligne dans
[05 Exploiter, § 4](05_exploiter.md). Pour la suite : comprendre ce qui se passe quand vous envoyez une demande
([02](02_comprendre.md)), puis régler le moteur ([03](03_reglages.md)).
