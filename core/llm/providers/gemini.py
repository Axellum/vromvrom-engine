"""
core/llm/providers/gemini.py — Providers Google Gemini (API OpenAI-compatible et Antigravity CLI).
"""

import json
import logging
import os
import shutil
import uuid
from typing import Any

import requests

from core.llm_timeouts import get_timeout
from core.openai_compat_provider import filtrer_champs_prives

from .base import LLMProvider, run_cli_command

logger = logging.getLogger(__name__)

# [Audit sécu 10/07 — IPC Antigravity] Préfixe des fichiers d'échange avec la CLI
# Antigravity. Le nom complet est UNIQUE par appel (cf. GeminiCLIProvider.generate) :
# il est dicté par notre propre prompt, donc la CLI s'y adapte sans qu'aucun
# changement ne soit nécessaire de son côté.
_EXCHANGE_PREFIX = "antigravity_exchange_temp"

# Nom historique, à sortie fixe. Conservé UNIQUEMENT comme filet de sécurité en
# lecture, au cas où la CLI ignorerait le nom demandé (cf. _lire_fichier_echange).
_LEGACY_EXCHANGE_FILE = f"{_EXCHANGE_PREFIX}.txt"


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


class GeminiCLIProvider(LLMProvider):
    """Provider pour exécuter localement la CLI Antigravity en mode chat (OAuth Claude Pro)."""

    def __init__(self, mode: str = "agent", model_name: str = "gemini-cli"):
        self.mode = mode
        self.model_name = model_name
        user_home = os.path.expanduser("~")
        self.cmd_path = os.path.join(user_home, "AppData", "Local", "Programs", "Antigravity IDE", "bin", "antigravity-ide.cmd")
        if not os.path.exists(self.cmd_path):
            self.cmd_path = os.path.join(user_home, "AppData", "Local", "Programs", "Antigravity", "bin", "antigravity.cmd")
        if not os.path.exists(self.cmd_path):
            self.cmd_path = (
                shutil.which("antigravity-ide") or
                shutil.which("antigravity-ide.cmd") or
                shutil.which("antigravity") or
                shutil.which("antigravity.exe") or
                "antigravity-ide"
            )

    def _lire_fichier_echange(self, exchange_file: str) -> str | None:
        """
        Lit la réponse écrite par la CLI, puis supprime le fichier lu.

        Le fichier attendu est celui, unique, demandé dans le prompt de cet appel.
        S'il est absent, on tente le nom historique à sortie fixe : filet de
        sécurité au cas où la CLI ignorerait le nom demandé. Ce repli est
        best-effort et reste soumis, lui, au croisement de réponses entre appels
        concurrents — c'est précisément ce que le nom unique élimine sur le
        chemin nominal. Retourne None si aucun fichier n'a été produit.
        """
        for chemin in (exchange_file, _LEGACY_EXCHANGE_FILE):
            if not os.path.exists(chemin):
                continue
            if chemin == _LEGACY_EXCHANGE_FILE:
                logger.warning(
                    "[GeminiCLI] La CLI a écrit dans le fichier d'échange historique "
                    f"('{_LEGACY_EXCHANGE_FILE}') au lieu du nom unique demandé "
                    f"('{exchange_file}') : réponses potentiellement croisées si "
                    "plusieurs appels tournent en parallèle."
                )
            with open(chemin, encoding="utf-8", errors="ignore") as f:
                contenu = f.read().strip()
            _supprimer_silencieusement(chemin)
            return contenu
        return None

    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        prompt = f"{system_prompt}\n\n{user_prompt}".strip()

        # [Audit sécu 10/07] Un nom de fichier FIXE et relatif au CWD servait
        # d'IPC avec la CLI : deux appels concurrents (les providers tournent en
        # parallèle via generate_async → asyncio.to_thread) se supprimaient et se
        # relisaient mutuellement, d'où des réponses croisées ; et un appel parti
        # en timeout laissait sa réponse être lue par l'appel suivant. Le nom est
        # désormais unique par appel — et comme c'est notre prompt qui le dicte,
        # rien ne change côté Antigravity.
        exchange_file = f"{_EXCHANGE_PREFIX}_{uuid.uuid4().hex[:12]}.txt"

        # Résidu éventuel d'un ancien appel au format historique : on repart propre.
        _supprimer_silencieusement(_LEGACY_EXCHANGE_FILE)

        full_prompt = (
            f"{prompt}\n\n"
            f"CRITIQUE : Tu doit absolument écrire ton résultat ou ton analyse finale dans le fichier texte nommé '{exchange_file}' dans ton espace de travail actuel. Ne fais rien d'autre."
        )

        # Fichier temp si prompt long (>8000 chars, limite Windows CLI)
        prompt_file = None
        if len(full_prompt) > 8000:
            import tempfile
            try:
                tmp = tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.txt', delete=False)
                tmp.write(full_prompt)
                tmp.close()
                prompt_file = tmp.name
                cmd = [self.cmd_path, 'chat', '-m', self.mode, f'@{prompt_file}']
                logger.info(f'[GeminiCLI] Prompt long ({len(full_prompt)} chars) -> fichier temp : {prompt_file}')
            except Exception as e:
                logger.warning(f'[GeminiCLI] Impossible de créer fichier temp : {e}')
                cmd = [self.cmd_path, 'chat', '-m', self.mode, full_prompt[:8000]]
        else:
            cmd = [self.cmd_path, 'chat', '-m', self.mode, full_prompt]
        logger.debug(f'Exécution Gemini CLI : {self.cmd_path} chat -m {self.mode} [prompt]')

        try:
            result = run_cli_command(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=300)
            response_text = self._lire_fichier_echange(exchange_file)
            if response_text is None:
                response_text = result.stdout.strip()
                if not response_text:
                    if result.returncode != 0:
                        raise RuntimeError(f"Antigravity CLI error ({result.returncode}): {result.stderr}")
                    else:
                        raise RuntimeError("Antigravity CLI n'a produit aucune sortie et aucun fichier d'échange.")
        except Exception as e:
            logger.error(f"Erreur lors de l'appel Gemini CLI (Antigravity): {e}")
            raise e
        finally:
            # Sans ce nettoyage, un appel interrompu (timeout de 300 s, exception)
            # laisserait derrière lui un fichier d'échange et le fichier de prompt
            # long — d'autant plus visibles maintenant que chaque appel a son
            # propre nom.
            _supprimer_silencieusement(exchange_file)
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
