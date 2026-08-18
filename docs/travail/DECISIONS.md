# Décisions — tab5-engine

Une entrée = contexte / choix / date. Les **en attente** restent ici jusqu'à tranchage (pas dans `TACHES.md` comme du code).

## Tranchées

### D-1 — Conventions projet : agents de code seulement
**11/08/2026** · #T287
`CLAUDE.md` entier dans chaque appel (~1264 tokens) provoquait des functionCall fantômes et n'atteignait pas l'Executor (`messages=` ignore `system_prompt`).
**Choix :** drapeau `conventions_projet=True` sur Executor / Reviewer / ToolMaker / front de code. Chat, vocal, Planner, proxy `/v1` exclus. Fichier injectable = `CONVENTIONS.md`.

### D-2 — Isolation git : pas dans un worktree lié
**12/08/2026** · #T318
`git_safety` détruisait le travail non commité (stash + `clean -fd` + checkout `master` en dur).
**Choix :** refus d'isolation si `--git-dir` ≠ `--git-common-dir`. `git clean -fd` retiré du rollback.

### D-3 — Validité des modèles : sonde automatique
**12/08/2026** · #T333 · décision Axel
Cycle signaler → désactiver → réactiver pour la **validité**. Soldes/quotas = autre décision (#T334, pas encore tranchée en code).

### D-4 — `claude-fable-5` hors routage auto
**07/2026** · Axel
Présent nulle part dans les tiers. Appel explicite seulement. `routing_policy.excluded_models`.

### D-5 — OpenRouter = `openrouter/auto` seulement
**13/08/2026** · #T331 · décision Axel
Clé saine (14,99 $) mais slugs Llama `:free` en 404. Les 9 autres modèles catalogue n'étaient pas câblés.
**Choix :** un seul modèle gateway, `openrouter/auto`. Slugs `:free` exclus de la cascade.

### D-6 — Claude API native hors cascade, clé conservée
**13/08/2026** · #T331 · décision Axel
`ANTHROPIC_API_KEY` présente, crédit à sec (HTTP 400 sur Haiku/Sonnet/Opus).
**Choix :** exclure `claude-sonnet-5`, `claude-opus-4-8-direct`, `claude-haiku-4-5-direct`. Accès explicite possible après recharge.

### D-7 — CLI : installer sur le Deck
**13/08/2026** · #T331 · décision Axel
`claude` présent sur Windows, absent du Deck (hôte + ubuntu-dev). `gemini` CLI nulle part.
**Choix :** installer / reconnecter les binaires dans le conteneur Deck (login Axel).

### D-8 — Dashscope désactivé
**13/08/2026** · #T331 · décision Axel
Clé présente (Windows + Deck) mais `POST /chat/completions` en 401 ; CGU Alibaba interdisent le backend auto.
**Choix :** désactiver. Catalogue + clé conservés. `DASHSCOPE_ACTIF = False` : pas d'instance gateway, hors cascade (préfixe), hors BudgetGuard, règles de routage repliées.

### D-9 — Verrou d'exécution par session (#T324)
**16/08/2026** · #T324
L'ancien verrou **global** sérialisait toutes les exécutions (IHM, vocal, DAG) : une commande vocale Tab5 → HTTP 409 sur tout le reste. Mesuré 16/08 : 28 % des exécutions > 10 s, 15 % > 1 min, pendant lesquelles les trois portes (`/api/run`, `/api/execute`, `/api/execute/stream`) renvoyaient 409.
**Choix :** granularité = **session**. Clé d'exécution = `session_id` fourni par le client, sinon la source de la requête (device_id, sinon type:mode) — le Tab5 qui rejoue une commande est bloqué en doublon. Plafond global `MOTEUR_EXECUTION_MAX_CONCURRENCY` (défaut 3) pour ne pas saturer le Deck ; au-delà, 409 distinct du doublon. `execution_state` reste une vue agrégée (4 clés) : `running` dès qu'une exécution tourne → aucun consommateur (status, stop, abort, Dreamer, pipeline) cassé, aucun changement IHM.

## En attente d'Axel

| Id | Question | Options | Bloque |
|---|---|---|---|
| #T315 | Seuil HITL (`medium` = presque tout plan) | relever à `high` / restreindre les mots-clés / garder | 3/8 demandes campagne |
| #T335 | Provider CLI dans le tier `fort` ? | retirer / plafonner le contexte / garder | coût $0,67 / appel Opus CLI |
| #T326 | `MOTEUR_API_KEY` locale ≠ prod | unifier / documenter | 401 croisés |
| #T327 | Second moteur `vromvrom-engine` :8002 sur le Deck | arrêter / assumer | bruit daemon |
| #T223 | TLS HA (`HA_VERIFY_TLS` / CA) | bundle / hostname / `false` LAN | — |
| #T260 | Cible produit (makers vs devs IA) | — | #T257/#T258 |
