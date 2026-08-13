# Invariants — tab5-engine

Interdits **mesurés**. Les casser réintroduit un défaut déjà payé.

1. **Pas de succès codé en dur.** Statut, API et texte utilisateur disent l'échec (#T328).
2. **Pas d'injection `CLAUDE.md` hors agents de code.** Chat/vocal n'en ont pas l'usage et la payaient (~1264 tokens/appel, functionCall fantômes) (#T287). Seul `CONVENTIONS.md` est injectable.
3. **Pas de `git clean -fd` sur l'arbre utilisateur.** Un fichier non suivi laissé est une gêne ; supprimé, une perte (#T318).
4. **Un worktree n'est pas la prod.** Pas de `.env`, pas de bases seedées, `env_bootstrap` résout depuis `__file__` (#T284).
5. **Git > board.** Si `TACHES.md` et `origin/master` divergent, git a raison.
6. **La catégorie conditionne le chemin et le tier, jamais l'accès aux outils.** La boucle ReAct choisit sur la description.
7. **Ne pas envelopper une exception que `FallbackProvider` lit au message** (`"429" in err_msg`) — un type maison casse la cascade (#T265).
8. **SQLite du moteur : local uniquement**, jamais via SMB.
9. **Auth fail-closed.** `MOTEUR_API_KEY` absente → 503 sur `/api` sensible.
10. **Commentaires en français.** Jamais de `.db` / `.env` / clé commités.
