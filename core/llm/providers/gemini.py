"""
core/llm/providers/gemini.py — Providers Google Gemini (API OpenAI-compatible et Antigravity CLI).
"""

import os
import json
import logging
import shutil
import requests
from typing import Dict, Any
from .base import LLMProvider, run_cli_command
from core.llm_timeouts import get_timeout

logger = logging.getLogger(__name__)


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
        if not messages:
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
        
    def generate_structured(self, system_prompt: str, user_prompt: str, schema: Dict[str, Any], **kwargs) -> Dict[str, Any]:
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
        if not messages:
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

    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        prompt = f"{system_prompt}\n\n{user_prompt}".strip()
        exchange_file = "antigravity_exchange_temp.txt"
        if os.path.exists(exchange_file):
            try:
                os.remove(exchange_file)
            except Exception:
                pass
                
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
            if os.path.exists(exchange_file):
                with open(exchange_file, "r", encoding="utf-8", errors="ignore") as f:
                    response_text = f.read().strip()
                try:
                    os.remove(exchange_file)
                except Exception:
                    pass
            else:
                response_text = result.stdout.strip()
                if not response_text:
                    if result.returncode != 0:
                        raise RuntimeError(f"Antigravity CLI error ({result.returncode}): {result.stderr}")
                    else:
                        raise RuntimeError("Antigravity CLI n'a produit aucune sortie et aucun fichier d'échange.")
        except Exception as e:
            logger.error(f"Erreur lors de l'appel Gemini CLI (Antigravity): {e}")
            raise e

        prompt_tokens = max(1, len(prompt) // 4)
        completion_tokens = max(1, len(response_text) // 4)
        
        from core.token_tracker import record_usage
        record_usage("gemini-cli", prompt_tokens, completion_tokens, session_id=kwargs.get("session_id"))
        
        return response_text

    def generate_structured(self, system_prompt: str, user_prompt: str, schema: Dict[str, Any], **kwargs) -> Dict[str, Any]:
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
