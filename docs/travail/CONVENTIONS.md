# Conventions — injectable agents de code

Seul texte collé dans l'Executor / Reviewer / ToolMaker (`conventions_projet=True`). Pas de liste MCP, pas d'archi, pas de board.

- Commentaires de code en français.
- Chemins : racine canonique `.` (E: = junction). Workspace moteur = ce dépôt.
- Windows : pas de `ls`/`grep`/`cat` via le terminal — `read_file` / `write_file` / `dir` / `findstr`.
- Linux (Deck) : l'inverse — pas de `dir`/`findstr`/`type`.
- Secrets : jamais en clair. HA/ESPHome → `!secret`. Python → env.
- Après un `write_file` sur du `.py` : `run_tests` sur le fichier de test correspondant avant de conclure.
- Privilégier les outils nommés (`read_file`, `write_file`, `mcp_*`) au shell.
- Ne pas modifier hors workspace. Ne pas `git clean -fd`. Ne pas déclarer un succès sans l'avoir rejoué.
- GPIO ESP32-P4 : vérifier les pins réservées au boot avant d'écrire du YAML ESPHome.
