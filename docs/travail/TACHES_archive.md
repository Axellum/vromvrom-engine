# Archive des tâches moteur — froid

Ne pas lire au boot d'une session. Consulter seulement pour « est-ce que #Txxx est déjà clos ? »

Clôtures **antérieures au 12/08/2026** (158 tâches, board commun) :
`.\contexte_ia\04_Projets\TACHES_terminees.md`

À partir de #T340, les clôtures **moteur** s'archivent ici, dans la même PR que le fix.

## Format

`- ✅ #Tid — zone — date — PR #n — une ligne de preuve`

## 17/08/2026 — soirée (vague de merges #325 → #347)

> Vérifié le 17/08 à 23h30 sur `c30e194` : suite complète **1839 tests, 0 échec, 7 ignorés**.

- ✅ #T363 — domotique — 17/08 — PR #327 — **P0** — une question n'actionne plus la maison. Cause profonde nommée dans le fix : `normalize_vocal_stt` réécrit « est-elle » en « éteins » (`core/vocal_stt_normalize.py:38`), donc le garde-fou juge le **prompt brut**, pas le texte normalisé
- ✅ #T364 — domotique — 17/08 — PR #326 — le résolveur d'état ne devine plus : « combien » ne vaut marqueur de température que s'il est ancré par une pièce connue, et la branche lumière exige le mot de domaine en plus de la pièce
- ✅ #T358 — vocal — 17/08 — PR #325 — une définition (« c'est quoi un volet roulant ? ») n'est plus lue comme un état d'appareil
- ✅ #T365 — vocal — 17/08 — PR #328 — le champ horaire du travail atteint le calendrier ; la consigne CHAT interdit d'affirmer l'absence d'accès agenda/domotique
- ✅ #T366 — vocal/tts — 17/08 — PR #329 — le LaTeX ne part plus à la synthèse, les 4 appelants couverts d'un coup par `sanitize_discussion_tts`
- ✅ #T357 — proxy — 17/08 — PR #330 — le champ `usage` ne porte que des compteurs réels, et disparaît sinon
- ✅ #T362 — proxy/gateway — 17/08 — PR #333 — l'usage réel capturé par le provider remonte jusqu'au client `/v1` au lieu d'être jeté
- ✅ #T367 — vocal/météo — 17/08 — PR #332 — météo répondue depuis l'entité `weather.*` de la maison (`services/ha_weather_query.py`, 333 lignes). **Couvre #T359, qui est clos sans avoir été lancé** : doublon tranché en faveur de la branche déjà écrite. Anti-hallucination vérifié à la relecture — lecture échouée ou prévision absente ⇒ `None` et retour à la cascade, jamais de valeur fabriquée
- ✅ #T359 — vocal — 17/08 — **couvert par #T367**, voir ci-dessus
- ✅ #T360 — routage — 17/08 — PR #324 #334 — sonde d'accessibilité d'hôte (`core/llm/provider_health.py`) : ouverture TCP de 0,3 s avant le premier essai, cache 300 s en succès / 60 s en échec, une seule sonde en vol par hôte. Écarte le PC éteint que le disjoncteur de #T352 ne pouvait pas écarter (il n'ouvre qu'après 3 échecs). Appliquée aux **deux** chemins, fast-path et cascade streamée. Garde-fou relu : le **dernier** candidat n'est jamais sondé — on ne coupe pas la dernière option
- ✅ #T361 — sessions/dag — 17/08 — PR #343 — les tâches non terminales des sessions zombifiées sont réconciliées dans la **même transaction** que l'`UPDATE sessions`, sur les seules sessions zombifiées par cet appel (jamais un `UPDATE dag_tasks` global). Prédicat inchangé : `waiting_approval` jamais touché. ⚠️ **Le commit `e9f15f4` est étiqueté `#T362` par erreur** — il livre bien #T361
- ✅ #T346 — archi — 17/08 — PR #336 — canal de raisonnement de bout en bout (provider → gateway → pipeline → SSE) **et** outils vocaux sur le chemin streamé. Événements `thinking` / `thinking_done` / `tool_call` / `tool_result` émis par `stream_discussion_fast_path_sse` et `core/vocal_tools.py:569,579`. ⚠️ **Portée réelle : la branche `mode=chat` seulement** — le pipeline complet n'est pas couvert, d'où #T381
- ✅ #T369 — vocal — 17/08 — PR #335 — la mesure de latence vocale et les chemins d'écriture sont verrouillés par des tests
- ✅ #T373 #T380 — vocal — 17/08 — PR #345 — heure et date répondues en zéro-LLM ; la sonde est libérée par `bulk-status`
- ✅ #T379 — sessions — 17/08 — PR #344 — la session est close en fin d'exécution avec une cause nommée
- ✅ #T353-a — providers — 17/08 — PR #339 — AJEAN (llama.cpp, `Qwen2.5-14B-Instruct-1M-Q4_K_M`) déclaré **sans** être branché en cascade, par IP LAN et jamais par nom DNS pour rester dans la famille locale de `_est_hote_local` (#T337). Reste bloqué par l'action Axel : `--host 0.0.0.0` + pare-feu 8080
- ✅ #T305-a — ihm-v2 — 17/08 — PR #337 — le tableau de tâches DAG est requalifié et spécifié (`docs/specs_ihm_v2/T305_TaskKanbanBoard_Spec.md`) : c'est une **création**, pas un câblage
- ✅ *(sans id)* — contrat — 17/08 — PR #338 — le contrat approuvé devient un plancher inaliénable, incident du 17/08 rejoué en test
- ✅ *(sans id)* — sonde/catalogue — 17/08 — PR #340 #341 — backoff et classement de cause sur les modèles éteints, alerte une seule fois ; règle explicite + audit de la zone grise du catalogue
- ✅ *(sans id)* — journal — 17/08 — PR #342 — hygiène du journal du Deck : warning TLS unique, `HTTPError` sans réponse rendu diagnosticable
- ✅ *(sans id)* — dag — 17/08 — PR #347 — watchdog configurable par tier au lieu d'un délai unique de 120 s

## 17/08/2026

- ✅ #T351 — vocal — 17/08 — PR #319 — les demandes météo/agenda formulées sans le mot-clé attendu ne partent plus en LLM nu
- ✅ #T350 — domotique — 17/08 — PR #318 — les commandes de volet passent par `script.turn_on` et n'attendent plus les 26 s du script. Vérifié après merge : **le piège a été évité** — le payload porte bien `"variables": dict(service_data)` (sans quoi HA aurait répondu 200 avec un volet immobile), et `tests/unit/test_ha_script_turn_on_t350.py` (178 lignes) asserte le corps du POST
- ✅ #T349 — billing — 17/08 — PR #317 — solde des fournisseurs historisé dans `billing_history` depuis le collecteur de quotas, sans appel réseau ajouté
- ✅ #T348 — tests — 17/08 — PR #316 — la suite n'écrit plus dans la base réelle du dépôt (EventStore aligné sur `runtime_db`, isolation compatible CI)
- ✅ #T347 — coût — 17/08 — PR #315 — le cache-hit DeepSeek est compté à son tarif (0,003 $/M contre 0,14 $/M) au lieu du tarif plein

- ✅ #T345 — agents — 17/08 — PR #311 — le PATCH distingue « champ absent » de « fourni à `null` » via `body.model_fields_set` (`api/routes/agents_crud.py:376`) : `null` explicite rend tous les outils, un PATCH partiel n'efface plus les permissions. Vérifié après merge : le test du champ ABSENT existe bien (c'était le garde-fou de moindre privilège), la config de test est isolée (`tmp_path` + monkeypatch), et le NB obsolète de `ihm-v2/src/api/agents.ts` est remplacé par la sémantique réelle
- ✅ #T344 — api — 17/08 — PR #312 — le portillon `begin_execution` est descendu juste avant `run_full_pipeline` ; les 5 chemins légers ne prennent plus de créneau. Vérifié après merge : la contre-pression du dreamer est **préservée** par un compteur de requêtes en vol distinct (`state.begin_request`/`end_request`, toujours libéré dans le `finally`), le doublon est vérifié tôt sans consommer de créneau ni payer `analyze_request`, et `except HTTPException: raise` empêche le 409 d'être avalé par le repli générique
- ✅ #T169 — gateway — 17/08 — PR #313 — verdict : les deux symptômes sont CADUC, aucun changement de comportement. (a) `zai-glm-4.7` est un modèle Cerebras par design, résolution par clé exacte ; hypothèse « clé absente → repli muet » **réfutée** : sans clé le provider n'est pas construit et l'accès lève une ValueError. (b) le read de 120 s est le défaut de la famille `openai_compat`, aligné sur la fenêtre interactive — arbitrage produit, pas un bug. Verdicts + table des timeouts figés dans `tests/unit/test_t169_gateway_verdicts.py`
- ✅ #T337 — routage — 17/08 — PR #309 — `MOTEUR_PRIVACY_LEVEL=local_only` **fail-closed** : aucun local disponible → échec explicite, jamais de repli cloud. Vérifié : le test central asserte `cloud_provider.calls == 0` ET le contenu de la liste de repli du `FallbackProvider`, pas seulement son premier élément ; famille locale déterminée par `_is_local_provider()`, pas par une liste de noms
- ✅ #T290 — ihm-v2 — 17/08 — PR #308 — `ToolPermissionSelector` câblé à la création ET à l'édition (`AgentsManager.tsx:137,330`), les trois états `null`/`[]`/liste transitent distinctement. **A révélé #T345** : le PATCH backend ignore `null`, donc on ne peut pas revenir à « tous les outils »
- ✅ #T289 — ihm-v2 — 17/08 — PR #307 — `CustomModelModal` monté dans `LLMRegistry.tsx:14,521` et repointé de `/api/models/custom` (inexistant) vers `POST /api/models/update` ; vérifié : aucun `.py` touché par le lot
- ✅ #T344-a — api — 17/08 — PR #304 — le mode Discussion ne prend plus de créneau du plafond de concurrence et ne paie plus un `analyze_request()` jeté ; reste #T344 pour les 5 autres chemins légers
- ✅ #T304 — ihm-v2 — 17/08 — **déjà livré, énoncé erroné** — le SSE mot-à-mot existe (`api/routes/streaming.py:66` et `services/pipeline_service.py:1125` qui émet `{'type':'token'}`). Le board cherchait `/api/stream/chat`, le chemin réel est `/api/chat/stream`. Ce qui manque vraiment (types d'événements `thinking`/`tool_call`) est requalifié en #T346
- ✅ #T343 — agents — 17/08 — PR #301 #302 #303 — (a) un JSON d'arguments illisible n'exécute plus l'outil à vide, et le résumé du tour ne le compte plus comme un dépassement de borne ; (b) hystérésis de sortie consécutive + notification échouée rejouée, cycles concurrents sérialisés par `asyncio.Lock` (`core/quota_alert.py:54`) ; (c) le chemin streaming `/v1` porte l'étiquette `proxy_v1` jusque dans le thread du pool
- ✅ #T324 — api — 17/08 — PR #305 — verrou d'exécution par session + plafond `MOTEUR_EXECUTION_MAX_CONCURRENCY` (défaut 3), vue agrégée `execution_state` intacte ; suite ouverte en #T344 (PR #304)
- ✅ #T316 — llm — 16/08 — PR #300 — plafond de sortie par défaut sur les providers OpenAI-compatibles. Énoncé retourné à la mesure : les six complétions à 40 000 tokens étaient **réelles** (modèle parti en boucle), le comptage en base était juste
- ✅ #T303 — refactoring — 17/08 — **déjà livré**, board en retard — `core/dag/map_reduce.py` (435 lignes) existe et `core/dag_runner.py:50` déclare `DAGRunner(MapReduceMixin, SubgraphMixin)` ; vérifié le 17/08
- ✅ #T307 — mémoire — 17/08 — **déjà livré**, board en retard — `core/router.py:569` importe et appelle `sync_db_to_markdown` via `asyncio.to_thread` sur le chemin « ENREGISTRE » (`core/router.py:541`) ; garde-fous dans `tests/unit/test_sync_db_to_markdown.py` ; vérifié le 17/08

## 13/08/2026

- ✅ #T331 — providers — 13/08 — PR #298 + D-8 — OR=`openrouter/auto` ; Claude API hors cascade ; Dashscope off (401 + CGU)
- ✅ #T342 — docs — 12/08 — PR #291 — kit `docs/travail/` + `prompts/agents/` dans le dépôt (`4681959`)
- ✅ #T340 — outils — 12/08 — PR #290 — token HA configuré prime sur l'en-tête inventé par le modèle
- ✅ #T325 — observabilité — 12/08 — PR #293 — `/api/metrics/cost-per-success` par session (prod 13/08 : 406 succès, $0.00027/succès)
- ✅ #T341 — agents — 12/08 — PR #292 — borne 20 appels/tour + dédup ; description `get_account_status` corrigée
- ✅ #T330 — config — 12/08 — PR #294 — helper `get_ha_url`, plus d'URL HA en dur
- ✅ #T306 — proxy — 12/08 — PR #296 — `/v1` transmet `session_id` ; `agent_courant("proxy_v1")` sur le chemin bufferisé
- ✅ #T334 — quotas — 12/08 — PR #295 — alerte soldes/quotas + hystérésis ; cycle prod 13/08 : 0 clé sous tension, DeepSeek $13.90
