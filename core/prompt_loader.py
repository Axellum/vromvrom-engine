"""
core/prompt_loader.py — Prompts systèmes des agents externalisés en Markdown (#T188).

Les prompts des agents (reviewer, planner, tool_maker, prompt_engineer) vivent
dans `contexte_ia/03_Software/prompts_agents/<agent>.md` plutôt qu'en dur dans
le Python : modifiables par l'IDE/IHM sans redéploiement de code.

Contrat fail-safe : si le fichier Markdown est absent ou illisible (ex. prod
Deck déployée par overlay sans le dossier contexte_ia), l'agent retombe sur son
prompt par défaut codé en dur — le moteur ne casse JAMAIS pour un prompt manquant.

Résolution du dossier :
1. Variable d'env `MOTEUR_PROMPTS_DIR` si définie (chemin absolu).
2. Sinon `<parent de moteur_agents>/contexte_ia/03_Software/prompts_agents/`
   (même convention de racine workspace que tools/system.py).

Cache : contenu mémorisé par (chemin, mtime) — une édition du fichier est
prise en compte au prochain chargement sans redémarrage, sans relire le disque
à chaque requête si rien n'a changé.
"""

import logging
import os
import threading

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_LOCK = threading.Lock()


def get_prompts_dir() -> str:
    """Retourne le dossier des prompts agents (env MOTEUR_PROMPTS_DIR prioritaire)."""
    env_dir = os.environ.get("MOTEUR_PROMPTS_DIR", "").strip()
    if env_dir:
        return env_dir
    moteur_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(
        os.path.dirname(moteur_root), "contexte_ia", "03_Software", "prompts_agents"
    )


def prompt_file_path(agent_name: str) -> str:
    """Chemin du fichier Markdown du prompt d'un agent (existant ou non)."""
    # Nom de fichier strictement dérivé du nom d'agent (pas de traversée possible).
    safe_name = "".join(c for c in agent_name if c.isalnum() or c in ("_", "-"))
    return os.path.join(get_prompts_dir(), f"{safe_name}.md")


def load_agent_prompt(agent_name: str, default: str) -> str:
    """
    Charge le prompt système d'un agent depuis son Markdown, sinon `default`.

    Le fichier est du Markdown brut : tout son contenu (strip) devient le
    system prompt. Un fichier vide est traité comme absent (repli sur default).
    """
    path = prompt_file_path(agent_name)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return default

    with _CACHE_LOCK:
        cached = _CACHE.get(path)
        if cached and cached[0] == mtime:
            return cached[1] or default

    try:
        with open(path, encoding="utf-8") as f:
            content = f.read().strip()
    except OSError as e:
        logger.warning(f"[PROMPTS] Lecture impossible {path} : {e} — prompt par défaut utilisé")
        return default

    with _CACHE_LOCK:
        _CACHE[path] = (mtime, content)

    if not content:
        return default
    logger.debug(f"[PROMPTS] Prompt '{agent_name}' chargé depuis {path}")
    return content


def save_agent_prompt(agent_name: str, content: str) -> str:
    """
    Écrit le prompt Markdown d'un agent (création du dossier si besoin).

    Retourne le chemin écrit. Utilisé par PUT /api/agents/{name} (IHM).
    """
    path = prompt_file_path(agent_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content.strip() + "\n")
    with _CACHE_LOCK:
        _CACHE.pop(path, None)
    return path


def has_external_prompt(agent_name: str) -> bool:
    """True si un fichier Markdown non vide existe pour cet agent."""
    path = prompt_file_path(agent_name)
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False
