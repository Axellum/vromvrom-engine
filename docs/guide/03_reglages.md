# Régler le moteur

> **En une phrase.** Les réglages vivent à quatre endroits : le `.env` (clés et interrupteurs, lu au démarrage),
> `config.json` (modèles, budgets, agents de fond), le catalogue de modèles (`models_registry.db`) et quelques
> fichiers de données (`ha_commands.json`, `pricing_strategy.json`). Cette page liste chaque réglage, son défaut **lu
> dans le code**, son effet et s'il faut redémarrer.

Les défauts sont ceux du code de ce dépôt (fichier cité). « — » = défaut non déterminé ou sans objet. Les valeurs
des fichiers `.env.example` et `config.example.json` sont signalées comme telles quand elles diffèrent du code.

Pages liées : [01 Installer](01_installer.md) · [02 Comprendre](02_comprendre.md) ·
[04 Maison et voix](04_maison_et_voix.md) · [05 Exploiter](05_exploiter.md).

---

## 1. Où se trouve chaque réglage et quand il prend effet

| Où | Contenu | Quand ça prend effet |
|---|---|---|
| **`.env`** | Clés de fournisseurs, accès Home Assistant, interrupteurs, chemins | **Au redémarrage seulement** : le fichier est chargé une fois. Une variable déjà présente dans l'environnement du processus **gagne sur le `.env`** (`load_dotenv` sans écrasement). `MOTEUR_API_KEY` est de plus mise en cache. |
| **`config.json`** | Modèles par agent, niveaux, budgets, agents de fond, validation humaine | Le fichier est relu quand sa date de modification change (`core/llm_gateway.py`, `load_config`). Les boucles de fond (sentinelle, Dreamer, Auditeur) relisent à chaque tour. **Exceptions** : `budget_guard` est lu à la création du garde, `ha_commands.json` à la création du routeur : **redémarrer**. Dans le doute, redémarrer. |
| **Catalogue** (`models_registry.db`) | Modèles, niveaux, activation | Écran **Régler → Modèles & routage**, ou `POST /api/models/{id}/toggle`, `/routing-tier`, `/bulk-status`. Immédiat. |
| **`ha_commands.json`** | Catalogue des phrases domotiques | Redémarrage ([04](04_maison_et_voix.md)). |
| **`pricing_strategy.json`** | Barème des tarifs | Réécrit par la veille des tarifs (§ 2.5, `MOTEUR_VEILLE_APPLIQUER`). |

**Écran Configuration** (menu **Régler → Configuration**) : il lit et écrit `config.json` par `GET`/`POST /api/config`. Attention,
`POST /api/config` ne fusionne qu'au **premier niveau** : envoyer une section imbriquée (`budget_guard`, par exemple)
**remplace toute la section** (`api/routes/context.py:42-49`). L'écran envoie la section complète ; si vous appelez
l'API vous-même, renvoyez-la en entier. `POST /api/persistent-agents/config` (agents de fond) fusionne, lui
(`api/routes/daemon.py:76-96`).

**Sans `config.json`**, `load_config()` utilise des défauts internes : `planner_model=deepseek-reasoner`,
`executor_model=gemini-3.5-flash-free`, `antigravity_model=fort`, et les agents de fond **sentinelle et Dreamer à
`true`**. Il **réécrit** aussi `config.json` pour compléter les clés manquantes (niveaux, `persistent_agents`) quand le
fichier existe.

---

## 2. Le `.env`, groupe par groupe

### 2.1 Accès et sécurité

| Variable | Rôle | Défaut |
|---|---|---|
| `MOTEUR_API_KEY` | Clé Bearer de toutes les routes `/api` | vide → **503** (`core/auth.py`) |
| `MCP_TOKEN_KEY` | Chiffre `google_token.json` (jeton OAuth Google) | vide → jeton en clair (`.env.example`, `core/token_crypto.py`) |
| `MOTEUR_CORS_ORIGINS` | Origines autorisées, séparées par des virgules | vide → CORS ouvert `*` **sans** cookies (`gui_server.py`) |
| `MOTEUR_WORKSPACE_ROOT` | Racine où les outils de fichiers ont le droit d'écrire et de lire | dossier parent du dépôt (`.env.example`) |
| `MOTEUR_PRIVACY_LEVEL` | `local_only` : aucun appel au cloud, échec explicite sinon | vide (désactivé) |
| `MOTEUR_ENABLE_PLUGINS` | Charge le code de `plugins/*/agent.py` (exécute du code) | `0` (`core/plugin_registry.py`) |
| `MOTEUR_ENABLE_TOOL_MAKER` | Autorise le ToolMaker à persister des outils générés | `0` (`agents/tool_maker_agent.py`) |
| `MOTEUR_FILE_WRITE_GUARD` | Garde d'écriture des outils de fichiers | `1` (`tools/system.py`) |
| `MOTEUR_TERMINAL_GARDE_CHEMINS` | Garde de chemins de l'outil terminal | `1` (`tools/terminal.py`) |
| `MOTEUR_ROUTER_GARDE_HITL` | Une demande d'agir ne prend pas le raccourci | `1` (`core/router.py`) |
| `MOTEUR_HITL_NON_BLOQUANT` | Une demande d'approbation suspend la session au lieu de bloquer | `1` (`core/engine.py`) |
| `MOTEUR_DISABLE_CLI_PROVIDERS` | Exclut de la cascade les fournisseurs « abonnement en ligne de commande » | vide (`core/budget_guard.py`) |

Garde-fous activés par défaut dont l'effet précis n'est pas détaillé ici : `MOTEUR_CONTRAT_SUR_ECHEC`
(`core/review_loop.py`), `MOTEUR_DAG_NODE_TIERS` (`core/dag/node_tier_policy.py`), `MOTEUR_CHAT_CONTEXTE_PROJET`
(`core/router.py`), `MOTEUR_PROVIDER_PLUGINS` (`core/llm/provider_registration.py`), tous à `1`. Les couper
n'est utile que pour un diagnostic.

### 2.2 Fournisseurs de modèles

Une clé absente désactive simplement le fournisseur. L'écran **Installation** en liste 11 (Gemini gratuit, Gemini payant,
DeepSeek, Anthropic, Mistral, Cohere, OpenRouter, xAI, MiniMax, DeepInfra, Zhipu) ; le moteur en instancie davantage
(29 lors de l'essai, `core/llm_gateway.py`).

| Variable | Fournisseur |
|---|---|
| `GEMINI_API_KEY`, `GEMINI_API_KEY_2` … `GEMINI_API_KEY_6` | Google Gemini (palier gratuit) ; jusqu'à 6 clés en rotation ; sert aussi aux embeddings de la recherche documentaire |
| `GEMINI_PAYANT_API_KEY` | Gemini payant (GCP / Vertex) |
| `GEMINI_PROJECT_NAME` | Projet GCP ciblé. **Le code porte un défaut inutile pour vous** (`api/routes/apis_external.py:96`) : renseignez votre projet ou laissez la fonction GCP inutilisée |
| `GEMINI_MODEL` | Modèle Gemini par défaut (worker distant, `deploy/worker_standalone.py`) |
| `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`, `MISTRAL_API_KEY`, `COHERE_API_KEY` | Fournisseurs du même nom |
| `CEREBRAS_API_KEY`, `CEREBRAS_PAYANT_API_KEY` | Cerebras (gratuit / payant) |
| `OPENROUTER_API_KEY`, `XAI_API_KEY`, `MINIMAX_API_KEY`, `DEEPINFRA_API_KEY`, `ZHIPU_API_KEY` | Fournisseurs du même nom |
| `DASHSCOPE_API_KEY`, `DASHSCOPE_BASE_URL` | Alibaba DashScope (l'adresse par défaut est celle du « Coding Plan » international) |
| `BAILIAN_CODING_PLAN_API_KEY` | Variante du plan Alibaba (lue par `core/llm_gateway.py:255`, absente de `.env.example`) |
| `GITHUB_TOKEN` | **Sans usage de modèle** : `.env.example` indique que GitHub Models a été retiré ; sert à `seed_github_models.py` |
| `XAI_MANAGEMENT_API_KEY`, `XAI_TEAM_ID` | Lecture du solde xAI (`core/provider_balances.py`) |
| `MOTEUR_MAX_OUTPUT_TOKENS` | Plafond de sortie quand le catalogue n'en donne pas (`.env.example` : 8192) |
| `EUR_USD_RATE` | Taux pour convertir des tarifs en euros (**1,08** par défaut, `core/pricing.py`) |

> **Fournisseurs locaux (Ollama, llama.cpp, LM Studio).** Leurs adresses sont **fixées dans le code**
> (`core/openai_compat_provider.py:822-853` : Ollama sur `127.0.0.1:11434`, un serveur llama.cpp sur `127.0.0.1:8080`,
> plus des entrées pour une machine du réseau local). Le fournisseur `ollama_local` attend des noms de modèles précis
> (`core/llm_gateway.py:541-546`). La section `local_llm` de `config.example.json` et celle de `STRATEGIES.fr.md` ne sont
> lues par aucun code du dépôt (recherche du nom). Pour un autre modèle ou une autre adresse locale, il faut modifier
> ces fichiers : non essayé dans ce guide. `LM_STUDIO_URL` (défaut `http://192.168.1.10:1234`,
> `core/ha_fuzzy_matcher.py:85`) ne sert qu'au rapprochement approché des commandes domotiques.

### 2.3 Home Assistant

| Variable | Rôle | Défaut |
|---|---|---|
| `HASS_URL` (repli `HA_URL`) | Adresse de Home Assistant | **aucun** (`core/ha_url.py`) : sans elle, aucun appel part |
| `HASS_TOKEN` (repli `HA_TOKEN`) | Jeton d'accès longue durée | aucun |
| `HA_VERIFY_TLS` | Vérifier le certificat HTTPS | `true` (`api/routes/ha.py`) |
| `HA_CA_BUNDLE` | Fichier `.pem` de l'autorité de certification (préférable à couper la vérification) | vide |
| `HA_TLS_SERVER_HOSTNAME` | Nom à vérifier dans le certificat quand `HASS_URL` est une IP | vide (`core/ha_tls.py`) |
| `HA_WEATHER_ENTITY` | Entité météo lue sans modèle | `weather.home` (`services/ha_weather_query.py:44`) |
| `VOCAL_SATELLITE_ENTITY` | Entité du satellite vocal ciblé pour les annonces | une valeur propre à un écran précis (`core/vocal_jobs.py:129`) : à renseigner si vous utilisez cette fonction |

Procédure complète : [04 Maison et voix](04_maison_et_voix.md).

### 2.4 Limites d'exécution

| Variable | Rôle | Défaut |
|---|---|---|
| `MOTEUR_EXECUTION_MAX_CONCURRENCY` | Exécutions lourdes simultanées (les chemins légers n'en consomment pas) | `3` (`core/app_state.py`) |
| `MOTEUR_EXECUTION_STALE_TTL_SECONDS` | Âge à partir duquel une exécution orpheline est purgée | `1800` |
| `MOTEUR_PLANNER_TIMEOUT_S` | Fenêtre du Planificateur | calculée (assez large pour que la cascade bascule ; une valeur trop basse est refusée, `core/fenetres_execution.py`) |
| `MOTEUR_SESSION_FOND_TIMEOUT_S` | Durée max d'une session de fond | `900` s |
| `MOTEUR_DREAMCODER_TACHE_TIMEOUT_S` | Durée max d'une tâche de code autonome | `600` s |
| `MOTEUR_DAG_TASK_TIMEOUT_S` | Délai de base d'une étape de plan | voir `core/dag_runner.py:121` |

### 2.5 Boucles de fond

| Variable | Rôle | Défaut |
|---|---|---|
| `MODEL_HEALTH_PROBE` | Sonde de santé des modèles (**vraies générations**) | `true` |
| `MODEL_HEALTH_AUTO_DISABLE` | `false` : observation seule, sans éteindre de modèle | `true` |
| `MODEL_HEALTH_INTERVAL_SECONDS` | Intervalle des cycles | `3600` |
| `QUOTA_ALERTE_ENABLED` | Alerte quotas et soldes | `true` (`core/quota_alert.py`) |
| `QUOTA_ALERTE_OBSERVATION` | `true` : journalise sans notifier | `false` |
| `QUOTA_REFRESH_INTERVAL_SECONDS` | Relevé des quotas | `300` |
| `ZOMBIE_SWEEP_INTERVAL_SECONDS` | Nettoyage des sessions zombies | `600` |
| `MOTEUR_VEILLE_ENABLED` | Veille des tarifs | `1` (`core/veille_scheduler.py`) |
| `MOTEUR_VEILLE_APPLIQUER` | `0` : rapport seul, sans réécrire `pricing_strategy.json` | `1` |
| `MOTEUR_VEILLE_INTERVAL_H` | Périodicité (heures) | `24` |
| `TASK_DISCOVERY_ENABLED` | Découverte de tâches (TODO, board) | `1` (`gui_server.py:263`) |
| `TASK_DISCOVERY_MAX_INJECTIONS` | Tâches ajoutées au plus par passage | `10` |
| `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_CLIENT_ID` | Surveillance MQTT (bascule de secours côté Home Assistant) ; **lancée seulement si `MQTT_HOST` est défini** | port `1883` |

Détail du coût et de la coupure de chaque boucle : [05 Exploiter, § 6](05_exploiter.md).

### 2.6 Vigie (alertes sur le téléphone)

Coupée par défaut. Elle lit Gmail, des fils de forum, GitHub et YouTube, trie en urgent / à lire / bruit et prévient le
téléphone par Home Assistant. **Lecture seule** : rien n'est écrit, marqué ni supprimé. Le contenu des courriels n'est
trié que par un modèle **local** ; sans modèle local, le tri se fait par règles, jamais par le cloud
(`.env.example`). Essai sans effet : `python -m core.vigie --une-passe --a-blanc`.

| Variable | Rôle | Défaut (`core/vigie/reglages.py`) |
|---|---|---|
| `MOTEUR_VIGIE_ENABLED` | Interrupteur | `0` |
| `MOTEUR_VIGIE_NOTIFY_SERVICE` | Service `notify` de l'application mobile de Home Assistant | vide ; **obligatoire** pour démarrer |
| `MOTEUR_VIGIE_INTERVALLE_MIN` | Minutes entre deux relevés | `10` (minimum 2) |
| `MOTEUR_VIGIE_GMAIL`, `MOTEUR_VIGIE_GMAIL_REQUETE` | Source Gmail (jeton `google_token.json`) et requête | `1`, `in:inbox newer_than:2d` |
| `MOTEUR_VIGIE_FORUMS`, `MOTEUR_VIGIE_FORUMS_JOURS` | Fils suivis (`base\|id\|pseudo`, séparés par des virgules) et suivi des fils où vous avez écrit | vide, `90` |
| `MOTEUR_VIGIE_GITHUB_PSEUDO`, `_DISCUSSIONS`, `_JOURS` | Source GitHub | vide, vide, `60` |
| `MOTEUR_VIGIE_YOUTUBE_CHAINE` | Source YouTube (identifiant de chaîne) | vide |
| `MOTEUR_VIGIE_SILENCE` | Plage sans alerte, heure locale (`22-7`, `0` = aucune) | `22-7` |
| `MOTEUR_VIGIE_RESUME_HEURE` | Heure du résumé quotidien (vide ou `non` = aucun) | `8` |
| `MOTEUR_VIGIE_LLM`, `_LLM_MODELES`, `_LLM_TIMEOUT_S` | Tri des courriels par modèle local, liste des modèles, délai | `1`, liste du code, `90` |
| `MOTEUR_VIGIE_GMAIL_ALERTER`, `_GMAIL_IGNORER` | Expéditeurs toujours urgents / toujours ignorés | vides |
| `MOTEUR_VIGIE_GROUPE_SEUIL`, `_PANNE_SEUIL` | Alertes groupées au-delà de N ; alerte « source en panne » après N échecs | `3`, `3` |

Jetons optionnels : `MOTEUR_VIGIE_GITHUB_TOKEN` (à défaut `GITHUB_PERSONAL_ACCESS_TOKEN`) et `MOTEUR_VIGIE_YOUTUBE_CLE`
(à défaut `CLOUD_API_KEY`).

### 2.7 Cycle de code autonome (DreamCoder) et git

| Variable | Rôle | Défaut |
|---|---|---|
| `MOTEUR_DREAMCODER_REPO_PATHS` | Clones **dédiés** où le cycle crée ses branches (séparés par `;` sous Windows, `:` sous Linux). Prime sur `config.json`. | vide → **le cycle refuse de travailler** plutôt que d'utiliser le dépôt du moteur (`.env.example`) |
| `MOTEUR_GITHUB_PUSH_TOKEN` | Pousser les branches **et ouvrir des PR** | vide → les PR ne sont pas ouvertes |
| `MOTEUR_PROJECT_<ID>_PATH` | Racine d'un projet connu du moteur (écran **Projets & git**) | vide |
| `MOTEUR_REJEU_ECHEC_S` | Délai avant de rejouer une tâche en échec | `21600` (6 h, `agents/dreamer_agent.py:1179`) |
| `MOTEUR_WORKTREES_ROOT` | Dossier des arbres de travail | — |
| `MOTEUR_TOOLMAKER_PAT`, `MOTEUR_TOOLMAKER_REPO`, `MOTEUR_TOOLMAKER_TIMEOUT_S` | Bac à sable distant du ToolMaker (jeton limité à un dépôt) | PAT vide → tout outil candidat est refusé ; délai `300` |

> **Écart à connaître.** `MOTEUR_TOOLMAKER_REPO` vaut `Axellum/vromvrom-engine` dans `.env.example`, mais le code
> (`core/toolmaker_sandbox.py:62`) a un autre dépôt par défaut. Si vous utilisez le ToolMaker, renseignez-la.

### 2.8 Observabilité, divers

| Variable | Rôle | Défaut |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | Traces Langfuse ; sans les deux clés, le pont est inactif | hôte : une adresse privée d'exemple (`core/langfuse_bridge.py:59`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_EXPORTER_GCP`, `OTEL_SERVICE_NAME` | Export OpenTelemetry | vides ; nom de service `moteur-agents` (`core/otel.py`) |
| `RAG_BACKEND` | `LOCAL` ou un index Vertex (`VERTEX_DATA_STORE_ID`, `GOOGLE_CLOUD_PROJECT`) | `LOCAL` |
| `GOOGLE_APPLICATION_CREDENTIALS` | Identifiants Google pour GCP | — |
| `CLOUD_API_KEY` | Clé Google (YouTube, reconnaissance vocale cloud) | vide |
| `WEATHER_API_KEY` | Seulement signalée présente ou absente par `GET /api/system/tokens-status` (`gui_server.py:1287`) ; aucun autre usage trouvé | — |
| `TWILIO_*`, `META_WHATSAPP_*`, `CALLMEBOT_APIKEY`, `WHATSAPP_*` | Canal WhatsApp (webhooks publics) | vides ; non essayés |
| `SSH_USER`, `SSH_PASSWORD` | Diagnostic SSH de la sentinelle | **voir l'avertissement du § 4** |

---

## 3. `config.json`

`config.example.json` est la référence. Les sections ci-dessous sont celles que le code **lit**. Les clés du fichier d'exemple
qu'aucun code ne lit sont dans le § 6.

### 3.1 Modèles et niveaux

| Clé | Rôle | Défaut si absente |
|---|---|---|
| `planner_model`, `executor_model`, `reviewer_model`, `ha_model` | Modèle (ou niveau) de chaque agent | `fort`, `automatique`, `moyen`, `moyen` (`core/factory.py`) ; exemple : `gemini-3.5-flash-free`, `gemini-3.5-flash-free`, `leger`, `moyen` |
| `antigravity_model` | Agent spécialisé code et architecture | `fort` |
| `prompt_engineer_model` | Reformulation de consignes | `fort` (`api/routes/prompt_tools.py:40`) |
| `tiers` | Modèles de `leger` / `moyen` / `fort` / `automatique` | **Filet de secours** : le catalogue gagne (`automatique` = tous les modèles actifs) |
| `routing_policy.excluded_models` | Modèles retirés du routage, tous niveaux | liste de l'exemple |
| `routing_policy.fast_path_providers` | Ordre de la discussion rapide | repli du code : un fournisseur local optionnel, `gemini-3.5-flash-free`, `deepseek-chat`, `gemini-2.5-flash-free` (`services/pipeline_service.py:83`) |
| `routing_policy.tab_llm_providers` | Liste dédiée aux demandes `source.type = tab5` | `["gpt-oss-120b"]` |
| `elo_enabled` | Classement par score Elo | `true` (`core/router.py:197`) |
| `provider_timeouts` | Délais (connexion, lecture) par fournisseur compatible OpenAI | sections de l'exemple |
| `json_prompt_only_providers`, `latency_penalty_exempt_models` | Réglages fins de cascade | — (`core/llm_gateway.py:323`, `:155`) |
| `cascade_quality_escalation` | Réessaie au niveau `fort` si la note de qualité est sous le seuil | `enabled` vrai, seuil 6,0 (exemple), domaines `code_generation`, `analysis`, `sysadmin` |
| `semantic_cache` | Cache sémantique des réponses | **éteint**, seuil 0,95 (`core/semantic_cache.py:167`) |
| `custom_agents` | Agents créés dans l'écran **Agents** | liste vide |

### 3.2 Budgets et garde-fous

| Clé | Rôle | Défaut |
|---|---|---|
| `max_session_tokens` | Jetons maximum par exécution (0 = désactivé) | `500000` (`core/execution_budget.py:63`) |
| `max_execution_seconds` | Durée maximale par exécution (0 = désactivé) | `0` |
| `max_execution_cost_usd` | Coût maximal par exécution en dollars (0 = désactivé) | `0` |
| `auto_review` | Relecture automatique après un plan (un appel de modèle de plus) | `true` (`core/engine.py:866`) |
| `hitl.seuil_approbation` | `low`, `medium`, `high` ou `critical` : risque à partir duquel on demande | `high` |
| `hitl.interactive_auto_approve` | Approuver seul les plans à risque en session interactive | `false`. **Laissez `false`** |
| `budget_guard.*` | `gemini_free_tokens_per_hour`, `deepseek_free_requests_per_day`, `daily_budget_usd`, `auditor_weekly_budget_usd`, `total_daily_budget_usd` | exemple : 1 000 000, 200, 0,5, 1,0, 1,0 (`core/budget_guard.py:262`) |

Le chemin interactif n'est pas soumis aux budgets journaliers du `budget_guard` (ils bornent le cycle autonome) ; il l'est à
`max_session_tokens`. Vérification : une dépense réelle est visible dans **Surveiller → Observabilité**.

### 3.3 Agents de fond (`persistent_agents`)

| Clé | Rôle | Défaut du code / exemple |
|---|---|---|
| `daemon_enabled` | Sentinelle | `true` / `true` |
| `daemon_interval_minutes` | Cadence (minutes) | 10 / 10 |
| `daemon_model` | Niveau ou modèle | `leger` |
| `dreamer_enabled` | Dreamer + DreamCoder | `true` / `true` |
| `dreamer_schedule` | Heure du cycle de mémoire | `02:00` (exemple) |
| `dreamer_idle_trigger_hours` | Déclenche aussi le cycle après N heures d'inactivité | **1** (code) / **3** (exemple) |
| `dreamcoder_max_cycle_minutes` | Budget de temps d'un cycle de code | 30 |
| `dreamcoder_repo_path`, `dreamcoder_autoriser_depot_courant` | Dépôt de travail du cycle, et autorisation d'utiliser le dépôt courant | vide, `false` |
| `auditor_enabled` | Auditeur | **`false` (code, `api/routes/guide.py`) / `true` (exemple)** |
| `auditor_trigger_mode`, `_trigger_task_count`, `_schedule_weekday`, `_schedule_time` | Déclenchement : `task_count` (après N tâches terminées), `schedule` (créneau hebdomadaire) ou `both` | `task_count`, 10, 6 (dimanche), `03:00` |
| `auditor_scopes`, `_model_tier`, `_escalation_tier`, `_confidence_threshold`, `_dedup_similarity_threshold`, `_max_corpus_chars` | Périmètre et qualité de l'audit | exemple : `core, agents, api, memory, tools` ; `moyen` ; `fort` ; 6,0 ; 0,75 ; 150000 |
| `routines_model` | Niveau des routines | `fort` (exemple) |

Les trois interrupteurs `*_enabled` de `persistent_agents` se changent aussi dans **Avancé → Agents autonomes**. Ils sont
relus à chaque tour : **pas de redémarrage**.

---

## 4. Avertissement : la sentinelle et le SSH

La sentinelle (`daemon_enabled`, actif dans l'exemple) fait cinq contrôles par cycle (`core/daemon_loop.py`) : git (fichiers non
commités du dépôt du moteur), santé de Home Assistant, pré-chargement de l'agenda, santé de la mémoire et **surcharge de
Home Assistant**. Ce dernier contrôle tente, toutes les 6 heures ou quand le processeur dépasse 80 % deux cycles de suite,
un **diagnostic par SSH vers une adresse fixée dans le code** (`192.168.1.10`, ligne 361) avec l'utilisateur `SSH_USER`
(valeur par défaut fixée dans le code, ligne 360, qui n'est pas la vôtre), et peut y relancer des conteneurs (`sudo docker …`, lignes 422-480). Cette machine est celle
de l'installation d'origine. Chez vous :

- sans clé SSH ni `SSH_PASSWORD`, le contrôle échoue et le signale une fois ; rien n'est exécuté ;
- **ne définissez pas `SSH_PASSWORD`**, et si vous n'avez pas Home Assistant, mettez `persistent_agents.daemon_enabled` à `false` ;
- si une machine de votre réseau a l'adresse `192.168.1.10` et un compte SSH accessible avec vos clés, la sentinelle pourrait s'y
  connecter : désactivez-la dans ce cas.

Ce comportement n'a pas été déclenché lors des essais (aucun accès SSH configuré) ; il est lu dans le code.

---

## 5. Quel réglage pour quel objectif ?

| Je veux… | Je règle… | Effet attendu |
|---|---|---|
| Ne rien dépenser | Aucune clé payante dans le `.env` ; `MODEL_HEALTH_PROBE=false` ; `persistent_agents.dreamer_enabled` et `auditor_enabled` à `false` | Seuls les modèles gratuits (Gemini gratuit, etc.) sont appelés |
| Que rien ne quitte ma machine | `MOTEUR_PRIVACY_LEVEL=local_only` et un fournisseur local joignable | Échec explicite plutôt qu'un appel cloud |
| Ouvrir l'interface depuis un autre poste | Lancer avec `--host 0.0.0.0` (le défaut) et `MOTEUR_CORS_ORIGINS` | Voir [05 Exploiter, § 5](05_exploiter.md) |
| Changer le modèle d'un agent | `config.json` (`planner_model`…) ou l'écran **Agents** | Prochaine exécution |
| Changer les modèles d'un niveau | Écran **Modèles & routage** | Immédiat |
| Réduire le coût d'un plan | `auto_review=false`, `max_session_tokens` plus bas, `cascade_quality_escalation.enabled=false` | Moins d'appels de modèle ; qualité moindre |
| Plafonner la dépense d'une exécution | `max_execution_cost_usd` (par exemple `0.10`) | L'exécution s'arrête au plafond |

---

## 6. Clés de `config.example.json` que le code ne lit pas

Recherche du nom de chaque clé dans le code Python du dépôt (hors tests et interface) : aucune lecture trouvée pour
les clés suivantes. Elles n'ont **aucun effet constaté** ; ne comptez pas dessus.

| Clé | Remarque |
|---|---|
| `timeout_global_s`, `max_retries`, `max_dag_stages` | Les délais et reprises sont réglés ailleurs (variables du § 2.4, constantes du code) |
| `fast_path_enabled`, `fast_path_cache_ttl_s` | Le raccourci de discussion est toujours actif ; son cache de 15 s est une constante |
| `context_compression_threshold`, `max_tool_output_chars` | — |
| `hitl.enabled`, `hitl.timeout_s` | Seuls `seuil_approbation` et `interactive_auto_approve` sont lus (`core/engine.py:1159-1175`) |
| `swarm` | Les workers distants sont lus depuis un fichier dédié (`core/worker_registry.py`), pas depuis `config.json` |
| `visual_qa` | — |
| `local_llm` | Voir l'encadré du § 2.2 |

Autre écart : `config.example.json` contient trois fois la clé `provider_timeouts` (lignes 158, 163 et 168). En JSON, la
dernière l'emporte ; les trois sont identiques.

---

## 7. Fichiers de données

| Fichier | Rôle | Modèle |
|---|---|---|
| `ha_commands.json` | Catalogue de phrases domotiques → services Home Assistant. Lu à la création du routeur : redémarrer après modification. | `ha_commands.json.example` (entités d'exemple `light.living_room`, `light.bedroom`…) |
| `pricing_strategy.json` | Barème des tarifs par modèle. Source unique pour le calcul des coûts (`core/pricing.py`) ; un barème de repli existe si le fichier manque. Réécrit par la veille des tarifs si `MOTEUR_VEILLE_APPLIQUER=1`, avec sauvegarde dans `backups_prod/`. | `pricing_strategy.json.example` |
| `mcp_config.json` | Serveurs MCP tiers que le moteur peut utiliser (jamais de secret dedans) | `mcp_config.json.example` |
| `mcp_activation_rules.json` | Quels serveurs MCP démarrer : liste `default` + règles par mots-clés (`keywords` → `activate`). **Absent, aucun serveur n'est démarré.** | — |
| `agents_workflows.json` | Enchaînements d'agents de l'éditeur **Workflows** | fourni |
| `projects.example.json` | Modèle du registre de projets | — |
| `prompts/agents/*.md` | Consigne de chaque agent | fournis |

Pour le format de `ha_commands.json` et les règles de rapprochement : [04 Maison et voix, § 3](04_maison_et_voix.md).
