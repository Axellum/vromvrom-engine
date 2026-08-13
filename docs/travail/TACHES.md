# TÂCHES — board moteur (ouvert)

> Uniquement les tâches **ouvertes** de ce dépôt. Closes → [TACHES_archive.md](TACHES_archive.md).
> Tab5 / HA / transversal → `contexte_ia/04_Projets/TACHES.md`.
> **Git > board.** Convention : `[statut] #id — zone — prio — description — outil — dépend`

**Statuts** : ⏳ todo · 🔄 wip · 🧪 test · ✅ done · 🧊 gelé

## 📍 13/08/2026 (01h10)

| | |
|---|---|
| Dépôt | `origin/master` @ `2e2ea69`. **0 PR ouverte** |
| Prod | **à jour** : déployée le 13/08 **01:04** sur `2e2ea69` (`deploy_deck.sh`, rollback `deploy/pre-20260813-010408`). `/version` = `2e2ea69`, `/healthz` 200, **0 ERROR** au journal. Clos cette nuit : #T341 #T330 #T334 #T306 #T325 #T340 #T342 (PRs #292 #294 #295 #296 #293 #290 #291). |
| Décisions Axel | [DECISIONS.md](DECISIONS.md) · #T315 #T335 #T331 #T326 #T327 #T223 #T260 |

## 🔄 En cours — campagne 12-13/08

- ⏳ #T343 — agents — **P3** — Résidus Bugbot post-merge : (a) JSON d'arguments malformé → fausse dédup #T341 ; (b) hystérésis de sortie non consécutive + notify HA échouée fige `en_alerte` #T334 ; (c) chemin streaming `/v1` sans `agent_courant("proxy_v1")` #T306. — Cursor —
- ⏳ #T331 — providers — **P2** — **35 % des tentatives de cascade perdues.** Confirmé 13/08 : `dashscope/qwen3-coder-next` a encore échoué avant repli `gemma-4-31b`. Clé Dashscope morte ; slug OpenRouter gratuit retiré ; binaires `claude`/`gemini` absents du conteneur prod. **Axel** : renouveler ou désactiver — ne pas le faire en silence. — Claude — lié #T243, #T314 —
- ⏳ #T324 — api — **P2** — Verrou d'exécution **global** : une commande vocale Tab5 → HTTP 409 sur tout le reste (jusqu'à 65 s). Arbitrer : verrou par session/source. — Claude —
- ⏳ #T326 — config — **P3** — **DÉCISION AXEL** — `MOTEUR_API_KEY` 29 car. local vs 64 prod → 401 croisés. Unifier ou documenter. — Claude —
- ⏳ #T327 — déploiement — **P3** — **DÉCISION AXEL** — second moteur `vromvrom-engine` :8002 sur le Deck depuis le 03/07 (daemon 10 min, 2 anomalies/cycle). Arrêter ou assumer. — Claude —
- ⏳ #T315 — hitl — **P2** — **DÉCISION AXEL** — HITL dès `medium` (« écrire/créer/exécuter/terminal ») : 3/8 demandes campagne mortes, dont un CSV et un `ls` de `docs/`. — **Axel** —
- ⏳ #T335 — routage — **P3** — **DÉCISION AXEL** — CLI dans le tier `fort` ? Un appel `claude-opus-4-8` = 289 966 tokens in / $0,6665 = 100 % du coût campagne. En prod les binaires sont absents (#T331) donc ça échoue au lieu de coûter. — **Axel** — suite #T314 —
- ⏳ #T316 — routeur — **P3** — Parseur classifieur **sain** (énoncé corrigé 12/08). Reste : complétion comptée 40 000 tokens pour 348 in sur `gemma-4-31b`. — Claude/DeepSeek —

## IHM v2 (câblage, composants déjà mergés)

- ⏳ #T289 — ihm-v2 — **P2** — Câbler `CustomModelModal` dans `LLMRegistry.tsx` (vit dans `openfox_prep/`, jamais importé). — Claude —
- ⏳ #T290 — ihm-v2 — **P2** — Câbler `ToolPermissionSelector` dans `AgentsManager.tsx` (`allowed_tools`, #T233). — Claude — dépend #T233 —
- ⏳ #T291 — ihm-v2 — **P2** — Câbler `ThinkingBlock` + `ToolCallDisplay` dans `Chat.tsx`. — Claude — lié #T304 —
- ⏳ #T304 — ihm-v2 — **P2** — SSE `/api/stream/chat` mot-à-mot (alimente `ThinkingBlock`). — Claude —
- ⏳ #T305 — ihm-v2 — **P3** — `TaskKanbanBoard.tsx` dans `WorkflowBuilder.tsx`. — Claude —

## Chantiers code

- ⏳ #T303 — refactoring — **P2** — Extraire le bloc map-reduce de `dag_runner.py` (~400 lignes) vers `core/dag/`. Extraction PURE (preuve AST). — DeepSeek —
- ⏳ #T307 — mémoire — **P3** — Hook `sync_db_to_markdown.py` sur « ENREGISTRE ». **Ajout seulement** — jamais réécrire `04_Projets`. — DeepSeek —
- ⏳ #T169 — gateway — **P3** — Timeout 120 s + routage `zai-glm-4.7` → Cerebras. **Non reproduits.** Verdict par symptôme, réfutation comprise. — DeepSeek —
- ⏳ #T282 — cli — **P2** — Trust workspace Claude CLI : `hasTrustDialogAccepted: true` dans `~\.claude.json`. — Antigravity —
- ⏳ #T179 — scraper — **P2** — `billing_scraper.js` Anthropic + Gemini abo. Valider `--headless=false`. — Claude —
- 🧊 #T183 — catalogue — **P3** — 4 modèles Cohere payants non câblés (gelé Axel). — Claude —
- ⏳ #T184 — billing — **P3** — Cerebras jamais testé en direct (Chrome + API solde). — Claude —
- ⏳ #T144 — infra — **P3** — CRLF dans `.env` du Deck. — Axel/Claude —
- ⏳ #T191 — mémoire — **P3** — Agent d'expansion sémantique de la mémoire active. — Claude —

## À voir (produit, pas le hot-path)

- ⏳ #T336 — auth — **P3** — Multi-tenant + API keys RBAC. — Claude —
- ⏳ #T337 — routing — **P2** — `privacy_level: local_only` (inférence locale forcée). — DeepSeek —
- ⏳ #T338 — ihm-v2 — **P3** — Dashboard multi-bâtiments. — Claude —
- ⏳ #T339 — b2b — **P3** — Pack « VromVrom Box » hôtellerie/serres. — Axel —
