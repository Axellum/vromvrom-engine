"""
core/llm/providers/base.py — Classe de base abstraite pour les LLM providers.
"""

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


def tuer_arbre_process(proc: subprocess.Popen) -> bool:
    """
    [#T314] Tue un process CLI **et toute sa descendance**. Retourne True si un
    process était encore vivant au moment de l'appel.

    Tuer le seul process direct ne suffit pas : sous Windows un `.cmd` passe par
    `cmd.exe`, qui lance le vrai binaire en enfant ; sous Linux, `claude` est un
    lanceur node qui essaime. C'est l'enfant qui consomme les tokens — donc
    l'enfant qu'il faut atteindre.
    """
    if proc is None or proc.poll() is not None:
        return False

    try:
        if sys.platform == "win32":
            # /T tue l'arbre, /F sans sommation. Pas de killpg sous Windows.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            # Le process a été lancé avec start_new_session=True : son PGID lui
            # est propre, donc tuer le groupe n'atteint JAMAIS le serveur.
            try:
                os.killpg(os.getpgid(proc.pid), 9)
            except (ProcessLookupError, PermissionError):
                proc.kill()
        return True
    except Exception as exc:  # pragma: no cover — dépend de l'OS
        logger.warning(f"[CLI] [#T314] Échec de la terminaison du process {proc.pid} : {exc}")
        try:
            proc.kill()
            return True
        except Exception:
            return False


def run_cli_command(cmd: list, process_sink=None, **kwargs) -> subprocess.CompletedProcess:
    """[P0-1.3] Exécute une commande CLI SANS shell (anti-injection).

    Remplace les anciens appels subprocess avec shell activé : le prompt et les
    arguments sont passés comme éléments de liste (jamais interpolés dans une
    chaîne shell). Résout l'exécutable et gère le cas Windows où les `.cmd`/`.bat`
    ne sont pas lançables directement via CreateProcess (on passe alors par
    `cmd /c`, toujours sans shell, donc sans interprétation de chaîne).

    [#T292] Sous Windows, la ligne passée à cmd.exe est ENCADRÉE d'une paire de
    guillemets supplémentaire : cmd.exe retire la paire extérieure quand la ligne
    qui suit `/c` commence ET finit par un guillemet, ce qui dépouillait le chemin
    de l'exécutable de ses guillemets (coupure au premier espace, ex: « Antigravity
    IDE »). La ligne encadrée est passée en CHAÎNE avec `executable=cmd.exe` :
    passée en liste, subprocess la re-quote et échapperait les guillemets internes
    avec des backslashes, que cmd.exe ne sait pas dépouiller.

    [#T314] `process_sink` : callable optionnel appelé avec le `Popen` dès son
    lancement. Il rend l'appel ANNULABLE — sans lui, un appel LLM continue de
    tourner (et de facturer) après la mort de la tâche qui l'a demandé : mesuré
    le 12/08, $0,6665 facturés 41 s APRÈS que le watchdog a tué la session.
    Quand il est fourni, le process est lancé dans sa propre session (POSIX) pour
    que sa descendance soit tuable en bloc sans jamais toucher au serveur.
    """
    argv = list(cmd)
    executable = None
    if argv:
        argv[0] = shutil.which(argv[0]) or argv[0]
        if sys.platform == "win32" and str(argv[0]).lower().endswith((".cmd", ".bat")):
            ligne = subprocess.list2cmdline(argv)
            executable = shutil.which("cmd") or "cmd"
            argv = f'cmd /c "{ligne}"'
    kwargs.pop("shell", None)  # garde-fou : on force shell=False ci-dessous

    if process_sink is None:
        # Chemin historique inchangé : aucun appelant n'en subit d'effet de bord.
        return subprocess.run(argv, shell=False, executable=executable, **kwargs)

    # [#T314] Même sémantique que subprocess.run (qui fait exactement ceci en
    # interne), mais le Popen est publié avant l'attente : l'appelant peut le
    # tuer si sa tâche est annulée entre-temps.
    timeout = kwargs.pop("timeout", None)
    entree = kwargs.pop("input", None)
    if kwargs.pop("capture_output", False):
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    if sys.platform != "win32":
        kwargs.setdefault("start_new_session", True)

    with subprocess.Popen(argv, shell=False, executable=executable, **kwargs) as proc:
        process_sink(proc)
        try:
            stdout, stderr = proc.communicate(entree, timeout=timeout)
        except subprocess.TimeoutExpired:
            tuer_arbre_process(proc)
            stdout, stderr = proc.communicate()
            raise subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr) from None
        except BaseException:
            # Inclut CancelledError/KeyboardInterrupt : ne jamais laisser un
            # process facturable derrière soi.
            tuer_arbre_process(proc)
            raise
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)

class LLMProvider(ABC):
    """Interface standard pour tout fournisseur de modèle de langage (SRP)."""

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        pass

    @abstractmethod
    def generate_structured(self, system_prompt: str, user_prompt: str, schema: dict[str, Any], **kwargs) -> dict[str, Any]:
        pass

    def generate_stream(self, system_prompt: str, user_prompt: str, **kwargs):
        """
        Génère une réponse en streaming token-par-token.
        
        Yields:
            dict: {"token": str, "done": bool, "usage": dict|None}
        
        Les providers qui supportent nativement le streaming (DeepSeek, Gemini)
        le surchargent. Les autres utilisent ce fallback qui simule le streaming
        en découpant la réponse complète en chunks.
        """
        full_response = self.generate(system_prompt, user_prompt, **kwargs)
        if isinstance(full_response, dict):
            yield {"token": str(full_response), "done": True, "usage": None}
            return

        text = str(full_response)
        words = text.split(" ")
        for i, word in enumerate(words):
            is_last = (i == len(words) - 1)
            yield {
                "token": word + ("" if is_last else " "),
                "done": is_last,
                "usage": None,
            }
            time.sleep(0.02)

    async def generate_async(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        """
        [D5] Variante asynchrone de generate().

        Fondation de la migration vers des providers async natifs : fournit une
        interface async UNIFORME à tous les providers. Par défaut, exécute la
        méthode synchrone generate() dans un thread (non bloquant pour l'event
        loop). Les providers à I/O réseau (OpenAICompatibleProvider) surchargent
        cette méthode avec un vrai client httpx.AsyncClient.
        """
        return await asyncio.to_thread(self.generate, system_prompt, user_prompt, **kwargs)

    async def generate_structured_async(
        self, system_prompt: str, user_prompt: str, schema: dict[str, Any], **kwargs
    ) -> dict[str, Any]:
        """[D5] Variante asynchrone de generate_structured(). Même principe que generate_async."""
        return await asyncio.to_thread(self.generate_structured, system_prompt, user_prompt, schema, **kwargs)
