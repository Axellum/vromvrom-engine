"""
core/llm/providers/gemini.py — Providers Google Gemini (API OpenAI-compatible et Antigravity CLI).
"""

import json
import logging
import os
import shutil
import tempfile
from typing import Any

import requests

from core.llm_timeouts import get_timeout
from core.openai_compat_provider import filtrer_champs_prives

from .base import LLMProvider, run_cli_command

logger = logging.getLogger(__name__)

# CreateProcess ~32k ; on reste largement en dessous pour le `-p` inline.
_MAX_PROMPT_ARGV = 20000

# Clés d'API à retirer de l'environnement du sous-processus : `agy` avec
# `modelProvider: gemini` dans settings.json partirait sur l'API payante
# (ce qu'Axel refuse). L'abo Google est le keyring / la session `agy`.
_CLES_API_A_ECARTER = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_GENAI_API_KEY",
    "AV_API_KEY",
    "ANTIGRAVITY_API_KEY",
    "ANTIGRAVITY_TOKEN",
)


def chemin_agy() -> str | None:
    """Binaire headless Antigravity CLI (`agy`), pas le lanceur d'IDE."""
    home = os.path.expanduser("~")
    candidats = [
        shutil.which("agy"),
        shutil.which("agy.exe"),
        os.path.join(home, "AppData", "Local", "agy", "bin", "agy.exe"),
        os.path.join(home, ".local", "bin", "agy"),
    ]
    for chemin in candidats:
        if chemin and os.path.isfile(chemin):
            return chemin
    return None


def _env_agy_abonnement() -> dict[str, str]:
    """Environnement du sous-processus : session Google, jamais une clé API."""
    env = os.environ.copy()
    for cle in _CLES_API_A_ECARTER:
        env.pop(cle, None)
    return env


def _supprimer_silencieusement(chemin: str | None) -> None:
    """Supprime un fichier temporaire sans jamais faire échouer l'appelant."""
    if not chemin:
        return
    try:
        os.remove(chemin)
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.debug(f"[GeminiCLI] Fichier temporaire non supprimé ({chemin}) : {e}")


class GeminiProvider(LLMProvider):
    """Provider optimal pour utiliser les modèles de Google (via compatibilité OpenAI)."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        messages = kwargs.get("messages")
        if messages:
            # Champs privés internes du moteur (ex: _thought_signature Gemini) :
            # jamais transmis aux APIs externes (#T263).
            messages = filtrer_champs_prives(messages)
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.0)
        }

        if "tools" in kwargs:
            payload["tools"] = kwargs["tools"]
        # Relais de la contrainte d'appel d'outil du client (auto / none / outil imposé).
        if kwargs.get("tool_choice") is not None:
            payload["tool_choice"] = kwargs["tool_choice"]

        logger.debug(f"Appel API Gemini ({self.model}) (generate)")
        response = requests.post(self.base_url, headers=self.headers, json=payload, timeout=get_timeout("gemini"))
        response.raise_for_status()

        resp_json = response.json()
        usage = resp_json.get("usage")
        if usage:
            from core.token_tracker import record_usage
            record_usage(self.model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), session_id=kwargs.get("session_id"))

        message = resp_json["choices"][0]["message"]

        # Si le LLM a décidé d'appeler un outil, on retourne l'objet message entier
        if "tool_calls" in message:
            return message

        return message.get("content", "")

    def generate_structured(self, system_prompt: str, user_prompt: str, schema: dict[str, Any], **kwargs) -> dict[str, Any]:
        sys_prompt = system_prompt + "\nTu DOIS répondre UNIQUEMENT au format JSON strict."

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": kwargs.get("temperature", 0.0)
        }

        logger.debug(f"Appel API Gemini ({self.model}) (generate_structured)")
        response = requests.post(self.base_url, headers=self.headers, json=payload, timeout=get_timeout("gemini"))
        response.raise_for_status()

        resp_json = response.json()
        usage = resp_json.get("usage")
        if usage:
            from core.token_tracker import record_usage
            record_usage(self.model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), session_id=kwargs.get("session_id"))

        content = resp_json["choices"][0]["message"].get("content", "{}")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.error(f"Gemini n'a pas retourné un JSON valide: {content}")
            return {}

    def generate_stream(self, system_prompt: str, user_prompt: str, **kwargs):
        """Streaming natif Gemini via OpenAI-compatible stream=true."""
        messages = kwargs.get("messages")
        if messages:
            # Champs privés internes du moteur (ex: _thought_signature Gemini) :
            # jamais transmis aux APIs externes (#T263).
            messages = filtrer_champs_prives(messages)
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.0),
            "stream": True,
        }

        logger.debug(f"Appel API Gemini ({self.model}) (generate_stream)")
        response = requests.post(
            self.base_url, headers=self.headers, json=payload,
            timeout=get_timeout("gemini"), stream=True,
        )
        response.raise_for_status()

        total_tokens = ""
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                yield {"token": "", "done": True, "usage": None}
                break
            try:
                chunk = json.loads(data_str)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                token = delta.get("content", "")
                if token:
                    total_tokens += token
                    yield {"token": token, "done": False, "usage": None}
            except json.JSONDecodeError:
                continue

        from core.token_tracker import record_usage
        prompt_len = sum(len(m.get("content", "")) for m in messages) // 4
        record_usage(
            self.model,
            max(1, prompt_len),
            max(1, len(total_tokens) // 4),
            session_id=kwargs.get("session_id"),
        )


# Slug catalogue moteur → id `agy --model` (sortie de `agy models` 23/08).
_AGY_MODELE: dict[str, str] = {
    "gemini-3.5-flash-medium-cli": "gemini-3.7-flash-medium",
    "gemini-3.7-flash-medium-cli": "gemini-3.7-flash-medium",
    "gemini-3.5-flash-high-cli": "gemini-3.7-flash-high",
    "gemini-3.7-flash-high-cli": "gemini-3.7-flash-high",
    "gemini-cli": "gemini-3.7-flash-medium",
    "antigravity": "gemini-3.7-flash-medium",
}


def modele_agy(slug: str) -> str:
    """Id modèle passé à `agy --model`."""
    return _AGY_MODELE.get(slug) or "gemini-3.7-flash-medium"


class GeminiCLIProvider(LLMProvider):
    """Gemini via `agy` (CLI Antigravity, session Google / abo) — jamais l'API."""

    def __init__(self, mode: str = "chat", model_name: str = "gemini-cli"):
        # `model_name` = slug catalogue moteur → `agy --model` (D-21 : medium).
        self.mode = mode
        self.model_name = model_name
        self.cmd_path = chemin_agy() or "agy"

    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        agy = chemin_agy()
        if not agy:
            raise RuntimeError(
                "CLI Antigravity (`agy`) introuvable. Installer via "
                "https://antigravity.google/docs/cli/install puis `agy` une "
                "fois pour ouvrir la session Google (abonnement, pas de clé API)."
            )
        self.cmd_path = agy

        prompt = f"{system_prompt}\n\n{user_prompt}".strip()
        prompt_file = None
        extra_dirs: list[str] = []
        try:
            if len(prompt) > _MAX_PROMPT_ARGV:
                tmp = tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", suffix=".txt", delete=False,
                )
                tmp.write(prompt)
                tmp.close()
                prompt_file = tmp.name
                extra_dirs = [os.path.dirname(prompt_file)]
                prompt_agy = (
                    f"Lis le fichier UTF-8 '{os.path.basename(prompt_file)}' "
                    "et exécute uniquement les instructions qu'il contient. "
                    "Réponds par le livrable demandé, sans outil."
                )
                logger.info(
                    f"[GeminiCLI] Prompt long ({len(prompt)} chars) -> {prompt_file}"
                )
            else:
                prompt_agy = prompt

            cmd = [agy, "--sandbox", "--print-timeout", "4m"]
            cmd.extend(["--model", modele_agy(self.model_name)])
            for dossier in extra_dirs:
                cmd.extend(["--add-dir", dossier])
            cmd.extend(["-p", prompt_agy])
            logger.debug(
                "[GeminiCLI] agy --sandbox --model %s -p [prompt]",
                modele_agy(self.model_name),
            )

            result = run_cli_command(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=300,
                env=_env_agy_abonnement(),
            )
            response_text = (result.stdout or "").strip()
            if not response_text:
                if result.returncode != 0:
                    raise RuntimeError(
                        f"Antigravity CLI (`agy`) error ({result.returncode}): "
                        f"{result.stderr}"
                    )
                raise RuntimeError(
                    "Antigravity CLI (`agy`) n'a produit aucune sortie. "
                    "Ouvrir un terminal et lancer `agy` une fois pour signer "
                    "l'abonnement Google (pas GEMINI_API_KEY)."
                )
        except Exception as e:
            logger.error(f"Erreur lors de l'appel Gemini CLI (agy): {e}")
            raise
        finally:
            _supprimer_silencieusement(prompt_file)

        prompt_tokens = max(1, len(prompt) // 4)
        completion_tokens = max(1, len(response_text) // 4)

        from core.token_tracker import record_usage
        record_usage("gemini-cli", prompt_tokens, completion_tokens, session_id=kwargs.get("session_id"))

        return response_text


    def generate_structured(self, system_prompt: str, user_prompt: str, schema: dict[str, Any], **kwargs) -> dict[str, Any]:
        sys_prompt = system_prompt + "\nTu DOIS répondre UNIQUEMENT au format JSON strict."
        response_text = self.generate(sys_prompt, user_prompt, **kwargs)
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            cleaned = response_text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            try:
                return json.loads(cleaned.strip())
            except json.JSONDecodeError:
                logger.error(f"Gemini CLI n'a pas retourné un JSON valide: {response_text}")
                return {}
