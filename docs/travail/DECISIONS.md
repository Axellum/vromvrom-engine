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

## En attente d'Axel

| Id | Question | Options | Bloque |
|---|---|---|---|
| #T315 | Seuil HITL (`medium` = presque tout plan) | relever à `high` / restreindre les mots-clés / garder | 3/8 demandes campagne |
| #T335 | Provider CLI dans le tier `fort` ? | retirer / plafonner le contexte / garder | coût $0,67 / appel Opus CLI |
| #T331 | Clé Dashscope morte + slug OpenRouter retiré | renouveler / désactiver | 35 % des tentatives cascade |
| #T326 | `MOTEUR_API_KEY` locale ≠ prod | unifier / documenter | 401 croisés |
| #T327 | Second moteur `vromvrom-engine` :8002 sur le Deck | arrêter / assumer | bruit daemon |
| #T223 | TLS HA (`HA_VERIFY_TLS` / CA) | bundle / hostname / `false` LAN | — |
| #T260 | Cible produit (makers vs devs IA) | — | #T257/#T258 |
