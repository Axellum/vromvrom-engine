# AGENTS.md — tab5-engine

Tu travailles **dans ce dépôt**. Le parapluie `.\contexte_ia\` est le journal d'écosystème (Tab5, HA, sessions). Ici = source de vérité du moteur.

## Lire, dans cet ordre

1. `docs/travail/INDEX.md` — carte du kit (10 lignes)
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
