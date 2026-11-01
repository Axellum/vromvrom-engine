"""
core/vigie — Vigie : surveille Gmail, des fils de forum, GitHub et une chaîne YouTube, trie, alerte le téléphone.

LECTURE SEULE sur toutes les sources : la vigie n'écrit, ne répond, ne poste,
ne supprime et ne modifie jamais rien (ni étiquette Gmail, ni message). Elle
n'agit que par des notifications Home Assistant.

  - `boucle.py`        : un passage + la boucle de fond (lifespan de gui_server)
  - `sources/`         : un collecteur par source (Gmail, forums Discourse, GitHub, YouTube)
  - `tri_gmail.py`     : tri 🔴 / 🟡 / ⚪, LLM local uniquement (D-23)
  - `notification.py`  : `notify.<service>` (téléphone) + notification persistante
  - `etat.py`          : `data/vigie_state.json` sous filelock
  - `reglages.py`      : variables MOTEUR_VIGIE_* (voir .env.example)

Essai sans effet : `python -m core.vigie --une-passe --a-blanc`.
"""

from core.vigie.boucle import Vigie, vigie_main_loop

__all__ = ["Vigie", "vigie_main_loop"]
