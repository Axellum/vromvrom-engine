"""
core/llm/providers/deepseek.py — Providers DeepSeek, OpenAI-compatibles locaux, Claude CLI et Cascade Fallback.
"""

import asyncio
import os
import json
import logging
import shutil
import subprocess
import time
import random
import requests
from typing import Dict, Any
from .base import LLMProvider, run_cli_command
from ..circuit_breaker import CircuitBreaker

try:
    from core.otel import llm_span, set_span_tokens
except ImportError:
    from contextlib import contextmanager

    @contextmanager
    def llm_span(*a, **kw):
        yield None

    def set_span_tokens(*a, **kw):
        pass

# Chargement du pool HTTP partagé
try:
    from core.openai_compat_provider import SharedHTTPPool
    _USE_HTTP_POOL = True
except ImportError:
    _USE_HTTP_POOL = False
    SharedHTTPPool = None

logger = logging.getLogger(__name__)


class ClaudeInstructionsWrapper(LLMProvider):
    """
    Wrapper Décorateur qui injecte automatiquement les instructions de CLAUDE.md
    au prompt système de tout appel de génération.
    """
    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self._cached_instructions = None
        self._last_loaded = 0.0
        
    def _get_claude_instructions(self) -> str:
        """Lit et met en cache le fichier CLAUDE.md pour éviter les lectures disques répétées."""
        now = time.time()
        if self._cached_instructions is not None and now - self._last_loaded < 10.0:
            return self._cached_instructions
            
        self._cached_instructions = ""
        self._last_loaded = now
        
        possible_paths = [
            os.path.join(os.getcwd(), "CLAUDE.md"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "CLAUDE.md"),
            "CLAUDE.md"
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:
                            self._cached_instructions = (
                                "\n\n=== CONVENTIONS DE PROJET (CLAUDE.md) ===\n" + content
                            )
                            break
                except Exception as e:
                    logger.warning(f"[ClaudeInstructionsWrapper] Erreur lors de la lecture de CLAUDE.md: {e}")
                    
        return self._cached_instructions

    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        instructions = self._get_claude_instructions()
        full_system = system_prompt
        if instructions:
            full_system = system_prompt + instructions
        return self.provider.generate(full_system, user_prompt, **kwargs)

    def generate_structured(self, system_prompt: str, user_prompt: str, schema: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        instructions = self._get_claude_instructions()
        full_system = system_prompt
        if instructions:
            full_system = system_prompt + instructions
        return self.provider.generate_structured(full_system, user_prompt, schema, **kwargs)

    def generate_stream(self, system_prompt: str, user_prompt: str, **kwargs):
        instructions = self._get_claude_instructions()
        full_system = system_prompt
        if instructions:
            full_system = system_prompt + instructions
        yield from self.provider.generate_stream(full_system, user_prompt, **kwargs)


class LMStudioProvider(LLMProvider):
    """Provider pour l'exécution locale (Garantie de confidentialité, coût 0)."""
    
    def __init__(self, base_url: str = "http://${LMSTUDIO_HOST:-localhost}:1234/v1/chat/completions"):
        self.base_url = base_url
        self.headers = {"Content-Type": "application/json"}
        
    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        messages = kwargs.get("messages")
        if not messages:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        payload = {
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.0),
            "max_tokens": kwargs.get("max_tokens", 800)
        }
        
        logger.debug(f"Appel API LM Studio ({self.base_url})")
        _http = SharedHTTPPool.get_session() if _USE_HTTP_POOL else requests
        response = _http.post(self.base_url, headers=self.headers, json=payload, timeout=(2.0, 120.0))
        response.raise_for_status()
        
        resp_json = response.json()
        usage = resp_json.get("usage")
        if usage:
            from core.token_tracker import record_usage
            record_usage("local", usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), session_id=kwargs.get("session_id"))
            
        message = resp_json["choices"][0]["message"]
        
        if "tool_calls" in message:
            return message
            
        return message.get("content", "")
        
    def generate_structured(self, system_prompt: str, user_prompt: str, schema: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        payload = {
            "messages": [
                {"role": "system", "content": system_prompt + "\nTu DOIS répondre UNIQUEMENT au format JSON strict."},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": kwargs.get("temperature", 0.0)
        }
        
        logger.debug("Appel API LM Studio (generate_structured)")
        _http = SharedHTTPPool.get_session() if _USE_HTTP_POOL else requests
        response = _http.post(self.base_url, headers=self.headers, json=payload, timeout=(2.0, 120.0))
        response.raise_for_status()
        
        resp_json = response.json()
        usage = resp_json.get("usage")
        if usage:
            from core.token_tracker import record_usage
            record_usage("local", usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), session_id=kwargs.get("session_id"))
            
        content = resp_json["choices"][0]["message"].get("content", "{}")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.error(f"LM Studio n'a pas retourné un JSON valide: {content}")
            return {}


class OllamaDeckProvider(LLMProvider):
    """
    Provider pour l'inférence Edge AI sur le Steam Deck via Ollama.
    Compatible API OpenAI (format identique à LMStudioProvider).
    """

    DECK_HOSTS = ["${OLLAMA_HOST:-localhost}", "${DECK_IP_2:-localhost}"]  # Ethernet prioritaire, Wi-Fi fallback

    def __init__(
        self,
        host: str = "${OLLAMA_HOST:-localhost}",
        port: int = 11434,
        model_name: str = "phi3:mini",
    ):
        self.model_name = model_name
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}/v1/chat/completions"
        self.tags_url = f"http://{host}:{port}/api/tags"
        self.headers = {"Content-Type": "application/json"}
        self._connect_timeout = 2.0
        self._infer_timeout  = 60.0

    def ping_available(self) -> bool:
        """
        Vérifie si l'endpoint Ollama sur le Deck est joignable.
        """
        for host in self.DECK_HOSTS:
            try:
                url = f"http://{host}:{self.port}/api/tags"
                resp = requests.get(url, timeout=self._connect_timeout)
                if resp.status_code == 200:
                    if host != self.host:
                        logger.info(f"[OllamaDeck] Basculement Ethernet→Wi-Fi ({host})")
                        self.host = host
                        self.base_url = f"http://{host}:{self.port}/v1/chat/completions"
                    return True
            except Exception:
                continue
        return False

    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        messages = kwargs.get("messages")
        if not messages:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ]
        payload = {
            "model":       kwargs.get("model", self.model_name),
            "messages":    messages,
            "temperature": kwargs.get("temperature", 0.0),
            "max_tokens":  kwargs.get("max_tokens", 512),
        }

        logger.debug(f"[OllamaDeck] Appel {self.base_url} | modèle: {payload['model']}")
        try:
            _http = SharedHTTPPool.get_session() if _USE_HTTP_POOL else requests
            response = _http.post(
                self.base_url,
                headers=self.headers,
                json=payload,
                timeout=(self._connect_timeout, self._infer_timeout),
            )
            response.raise_for_status()
        except requests.exceptions.ConnectTimeout:
            raise RuntimeError("[OllamaDeck] Timeout de connexion — le Deck est hors ligne ou Ollama n'est pas démarré")
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(f"[OllamaDeck] Connexion refusée : {e}")

        resp_json = response.json()
        usage = resp_json.get("usage")
        if usage:
            from core.token_tracker import record_usage
            record_usage(
                "deck_ollama",
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                session_id=kwargs.get("session_id"),
            )

        message = resp_json["choices"][0]["message"]
        return message.get("content", "")

    def generate_structured(self, system_prompt: str, user_prompt: str, schema: dict, **kwargs) -> dict:
        sys_prompt = system_prompt + "\nTu DOIS répondre UNIQUEMENT au format JSON strict, sans bloc markdown."
        payload = {
            "model":       kwargs.get("model", self.model_name),
            "messages":    [
                {"role": "system", "content": sys_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "temperature": kwargs.get("temperature", 0.0),
        }

        logger.debug(f"[OllamaDeck] generate_structured ({payload['model']})")
        try:
            _http = SharedHTTPPool.get_session() if _USE_HTTP_POOL else requests
            response = _http.post(
                self.base_url,
                headers=self.headers,
                json=payload,
                timeout=(self._connect_timeout, self._infer_timeout),
            )
            response.raise_for_status()
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
            raise RuntimeError(f"[OllamaDeck] Connexion échouée : {e}")

        resp_json = response.json()
        usage = resp_json.get("usage")
        if usage:
            from core.token_tracker import record_usage
            record_usage(
                "deck_ollama",
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                session_id=kwargs.get("session_id"),
            )

        content = resp_json["choices"][0]["message"].get("content", "{}")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            cleaned = content.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                logger.error(f"[OllamaDeck] JSON invalide: {content[:200]}")
                return {}


class ClaudeCLIProvider(LLMProvider):
    """
    Provider pour exécuter Claude Code en mode non-interactif via son CLI npm global.
    """
    
    _STDIN_WARNING = "Warning: no stdin data received"
    
    def __init__(self):
        if os.name == "nt":  # Windows
            appdata = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
            self.cmd_path = os.path.join(appdata, "npm", "claude.cmd")
        else:  # Linux/Mac
            self.cmd_path = shutil.which("claude") or "claude"
        if not os.path.exists(self.cmd_path):
            self.cmd_path = shutil.which("claude") or "claude"
        self.default_model = None
            
    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        prompt = f"{system_prompt}\n\n{user_prompt}".strip()
        
        cmd = [
            self.cmd_path, "-p", "--dangerously-skip-permissions",
            "--output-format", "json"
        ]
        
        model = kwargs.get("model") or self.default_model
        if model and not model.endswith("-cli"):
            cmd += ["--model", model]
        
        use_stdin_pipe = False
        prompt_file = None
        if len(prompt) > 7000:
            import tempfile
            try:
                tmp = tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.txt', delete=False)
                tmp.write(prompt)
                tmp.close()
                prompt_file = tmp.name
                use_stdin_pipe = True
                logger.info(f'[Claude Code CLI] Prompt long ({len(prompt)} chars) -> stdin via fichier temp : {prompt_file}')
            except Exception as e:
                logger.warning(f'[Claude Code CLI] Impossible de créer fichier temp : {e}')
                cmd.append(prompt[:7000])
                use_stdin_pipe = False
        else:
            cmd.append(prompt)
        
        logger.info(
            f"[Claude Code CLI] Appel: -p --dangerously-skip-permissions --output-format json"
            + (f" --model {model}" if model else " (modèle par défaut Claude Sonnet)")
        )
        
        try:
            if use_stdin_pipe and prompt_file:
                with open(prompt_file, 'r', encoding='utf-8') as pf:
                    result = run_cli_command(
                        cmd,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=240.0,
                        stdin=pf
                    )
                try:
                    os.remove(prompt_file)
                except Exception:
                    pass
            else:
                result = run_cli_command(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=240.0,
                    stdin=subprocess.DEVNULL
                )
            
            stdout_raw = result.stdout.strip()
            
            if result.returncode != 0:
                stderr_clean = result.stderr.strip()
                if stderr_clean and not stderr_clean.startswith("Warning:"):
                    logger.error(f"[Claude Code CLI] Erreur ({result.returncode}): {stderr_clean}")
                    raise RuntimeError(f"Claude CLI error ({result.returncode}): {stderr_clean}")
            
            if not stdout_raw:
                raise RuntimeError("Claude CLI n'a retourné aucune réponse.")
            
            response_text = stdout_raw
            real_input_tokens = 0
            real_output_tokens = 0
            real_cost_usd = 0.0
            ttft_ms = None
            model_used = "claude"
            
            try:
                data = json.loads(stdout_raw)
                response_text = data.get("result", stdout_raw)
                
                usage = data.get("usage", {})
                real_input_tokens = usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                real_output_tokens = usage.get("output_tokens", 0)
                real_cost_usd = data.get("total_cost_usd", 0.0)
                ttft_ms = data.get("ttft_ms")
                
                model_usage = data.get("modelUsage", {})
                if model_usage:
                    model_used = max(model_usage, key=lambda m: model_usage[m].get("outputTokens", 0))
                
                logger.info(
                    f"[Claude Code CLI] ✅ Réponse OK | "
                    f"Modèle: {model_used} | "
                    f"Tokens: {real_input_tokens}in / {real_output_tokens}out | "
                    f"Coût: ${real_cost_usd:.6f} | "
                    f"TTFT: {ttft_ms}ms"
                )
                
            except json.JSONDecodeError:
                logger.warning("[Claude Code CLI] Réponse non-JSON, utilisation du stdout brut.")
                lines = response_text.splitlines()
                response_text = "\n".join(l for l in lines if not l.startswith("Warning:")).strip()
                real_input_tokens = max(1, len(prompt) // 4)
                real_output_tokens = max(1, len(response_text) // 4)
                
        except subprocess.TimeoutExpired:
            logger.error("[Claude Code CLI] Timeout 240s dépassé.")
            raise RuntimeError("Claude CLI timeout après 240s")
        except Exception as e:
            logger.error(f"[Claude Code CLI] Erreur: {e}")
            raise e
        
        from core.token_tracker import record_usage
        record_usage(
            model_used,
            real_input_tokens or max(1, len(prompt) // 4),
            real_output_tokens or max(1, len(response_text) // 4),
            session_id=kwargs.get("session_id"),
            cost_usd=real_cost_usd
        )
        
        return response_text
        

    def generate_structured(self, system_prompt: str, user_prompt: str, schema: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        sys_prompt = system_prompt + "\nTu DOIS répondre UNIQUEMENT au format JSON strict, sans bloc markdown, sans explication."
        response_text = self.generate(sys_prompt, user_prompt, **kwargs)
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            cleaned = response_text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            try:
                return json.loads(cleaned.strip())
            except json.JSONDecodeError:
                logger.error(f"[Claude Code CLI] JSON invalide: {response_text[:200]}")
                return {}


class FallbackProvider(LLMProvider):
    """Provider qui enveloppe plusieurs providers et bascule sur le suivant en cas d'échec ou de circuit ouvert."""
    
    _LOW_QUALITY_SIGNALS = [
        "je ne sais pas", "je ne peux pas", "i don't know", "i cannot",
        "je suis désolé, je ne", "as an ai", "en tant qu'ia",
    ]
    
    def __init__(self, providers: list[tuple[str, LLMProvider]]):
        self.providers = providers
    
    def _is_response_adequate(self, response) -> bool:
        """Heuristique rapide pour détecter une réponse insuffisante."""
        if not isinstance(response, str):
            return True
        response_stripped = response.strip()
        if len(response_stripped) < 20:
            return False
        response_lower = response_stripped.lower()
        return not any(signal in response_lower for signal in self._LOW_QUALITY_SIGNALS)
        
    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        if not self.providers:
            raise ValueError("Aucun provider disponible pour ce Tier.")

        # [Cache sémantique] Opt-out par appel + skip si outils (réponses tool-call
        # non cachables). Le singleton est no-op tant que config.json ne l'active pas.
        use_cache = kwargs.pop("use_semantic_cache", True) and not kwargs.get("tools")
        sem_cache = None
        cache_key = ""
        if use_cache:
            try:
                from core.semantic_cache import get_semantic_cache
                sem_cache = get_semantic_cache()
                if sem_cache.enabled:
                    cache_key = f"{system_prompt}\n###\n{user_prompt}"
                    cached = sem_cache.get(cache_key)
                    if cached is not None:
                        logger.info("[FALLBACK GATEWAY] ⚡ HIT cache sémantique — appel LLM évité.")
                        return cached
                else:
                    sem_cache = None
            except Exception as _sc_err:
                logger.debug(f"[FALLBACK GATEWAY] Cache sémantique indisponible : {_sc_err}")
                sem_cache = None

        last_error = None
        escalation_used = False
        for model_name, provider in self.providers:
            cb = CircuitBreaker.get_or_create(model_name)
            if cb.is_open():
                logger.warning(f"[FALLBACK GATEWAY] Circuit ouvert pour le modèle '{model_name}'. Modèle court-circuité.")
                continue
                
            retries = 2
            while retries > 0:
                start_time = time.time()
                try:
                    logger.info(f"[FALLBACK GATEWAY] Tentative avec le modèle : {model_name} (CB: {cb.state.value})")
                    res = provider.generate(system_prompt, user_prompt, **kwargs)
                    latency = time.time() - start_time
                    cb.record_success(latency)
                    
                    if not escalation_used and not self._is_response_adequate(res):
                        logger.warning(
                            f"[FALLBACK GATEWAY] ⬆️ Réponse inadéquate de '{model_name}' "
                            f"({len(str(res))} chars). Escalade vers le modèle suivant."
                        )
                        escalation_used = True
                        break

                    # [Cache sémantique] Mémorise les réponses texte adéquates.
                    if sem_cache is not None and isinstance(res, str):
                        sem_cache.put(cache_key, res, model=model_name)

                    return res
                except Exception as e:
                    err_msg = str(e).lower()
                    if "429" in err_msg or "rate limit" in err_msg:
                        retries -= 1
                        if retries > 0:
                            logger.warning(f"[FALLBACK GATEWAY] Modèle {model_name} a levé un Rate Limit (429). Retente après backoff...")
                            cb.record_rate_limit()
                            continue
                    
                    cb.record_failure(e)
                    backoff_delay = 0.5 + random.uniform(0, 0.5)
                    logger.warning(
                        f"[FALLBACK GATEWAY] Échec du modèle {model_name} : {e}. "
                        f"Backoff {backoff_delay:.1f}s avant le modèle suivant..."
                    )
                    time.sleep(backoff_delay)
                    last_error = e
                    break
        
        raise last_error or RuntimeError("Tous les providers configurés ont échoué ou ont leurs circuits ouverts.")

    async def generate_async(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        """[D5] Variante async du hot-path : appelle provider.generate_async() pour chaque candidat.

        Les providers OpenAI-compat utilisent httpx natif ; les autres tombent sur le
        fallback to_thread de base.py. Le backoff devient await asyncio.sleep (non bloquant).
        """
        if not self.providers:
            raise ValueError("Aucun provider disponible pour ce Tier.")

        use_cache = kwargs.pop("use_semantic_cache", True) and not kwargs.get("tools")
        sem_cache = None
        cache_key = ""
        if use_cache:
            try:
                from core.semantic_cache import get_semantic_cache
                sem_cache = get_semantic_cache()
                if sem_cache.enabled:
                    cache_key = f"{system_prompt}\n###\n{user_prompt}"
                    cached = sem_cache.get(cache_key)
                    if cached is not None:
                        logger.info("[FALLBACK GATEWAY] ⚡ HIT cache sémantique — appel LLM évité.")
                        return cached
                else:
                    sem_cache = None
            except Exception as _sc_err:
                logger.debug(f"[FALLBACK GATEWAY] Cache sémantique indisponible : {_sc_err}")
                sem_cache = None

        last_error = None
        escalation_used = False
        for fallback_idx, (model_name, provider) in enumerate(self.providers):
            cb = CircuitBreaker.get_or_create(model_name)
            if cb.is_open():
                logger.warning(f"[FALLBACK GATEWAY] Circuit ouvert pour le modèle '{model_name}'. Modèle court-circuité.")
                continue

            # Dériver le système provider depuis le nom du modèle (best-effort)
            _system = model_name.split("-")[0] if model_name else "unknown"

            retries = 2
            while retries > 0:
                start_time = time.time()
                with llm_span(
                    model_name=model_name,
                    provider_system=_system,
                    fallback_index=fallback_idx,
                    cb_state=cb.state.value,
                ) as _span:
                    try:
                        logger.info(f"[FALLBACK GATEWAY] Tentative async avec le modèle : {model_name} (CB: {cb.state.value})")
                        res = await provider.generate_async(system_prompt, user_prompt, **kwargs)
                        latency = time.time() - start_time
                        cb.record_success(latency)
                        set_span_tokens(_span, latency_ms=latency * 1000)

                        if not escalation_used and not self._is_response_adequate(res):
                            logger.warning(
                                f"[FALLBACK GATEWAY] ⬆️ Réponse inadéquate de '{model_name}' "
                                f"({len(str(res))} chars). Escalade vers le modèle suivant."
                            )
                            escalation_used = True
                            break

                        if sem_cache is not None and isinstance(res, str):
                            sem_cache.put(cache_key, res, model=model_name)

                        return res
                    except Exception as e:
                        err_msg = str(e).lower()
                        if "429" in err_msg or "rate limit" in err_msg:
                            retries -= 1
                            if retries > 0:
                                logger.warning(f"[FALLBACK GATEWAY] Modèle {model_name} rate-limit 429. Retente après backoff async...")
                                cb.record_rate_limit()
                                continue

                        cb.record_failure(e)
                        backoff_delay = 0.5 + random.uniform(0, 0.5)
                        logger.warning(
                            f"[FALLBACK GATEWAY] Échec du modèle {model_name} : {e}. "
                            f"Backoff async {backoff_delay:.1f}s avant le modèle suivant..."
                        )
                        await asyncio.sleep(backoff_delay)
                        last_error = e
                        break

        raise last_error or RuntimeError("Tous les providers configurés ont échoué ou ont leurs circuits ouverts.")

    async def generate_structured_async(
        self, system_prompt: str, user_prompt: str, schema: Dict[str, Any], **kwargs
    ) -> Dict[str, Any]:
        """[D5] Variante async de generate_structured() : appelle provider.generate_structured_async()."""
        if not self.providers:
            raise ValueError("Aucun provider disponible pour ce Tier.")

        last_error = None
        for model_name, provider in self.providers:
            cb = CircuitBreaker.get_or_create(model_name)
            if cb.is_open():
                logger.warning(f"[FALLBACK GATEWAY] Circuit ouvert pour le modèle structuré '{model_name}'. Modèle court-circuité.")
                continue

            retries = 2
            while retries > 0:
                start_time = time.time()
                try:
                    logger.info(f"[FALLBACK GATEWAY] Tentative structurée async avec le modèle : {model_name} (CB: {cb.state.value})")
                    res = await provider.generate_structured_async(system_prompt, user_prompt, schema, **kwargs)
                    latency = time.time() - start_time
                    cb.record_success(latency)
                    return res
                except Exception as e:
                    err_msg = str(e).lower()
                    if "429" in err_msg or "rate limit" in err_msg:
                        retries -= 1
                        if retries > 0:
                            logger.warning(f"[FALLBACK GATEWAY] Modèle structuré {model_name} rate-limit 429. Retente...")
                            cb.record_rate_limit()
                            continue

                    cb.record_failure(e)
                    backoff_delay = 0.5 + random.uniform(0, 0.5)
                    logger.warning(
                        f"[FALLBACK GATEWAY] Échec structuré async du modèle {model_name} : {e}. "
                        f"Backoff {backoff_delay:.1f}s..."
                    )
                    await asyncio.sleep(backoff_delay)
                    last_error = e
                    break

        raise last_error or RuntimeError("Tous les providers configurés ont échoué ou ont leurs circuits ouverts.")

    def generate_structured(self, system_prompt: str, user_prompt: str, schema: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        if not self.providers:
            raise ValueError("Aucun provider disponible pour ce Tier.")
            
        last_error = None
        for model_name, provider in self.providers:
            cb = CircuitBreaker.get_or_create(model_name)
            if cb.is_open():
                logger.warning(f"[FALLBACK GATEWAY] Circuit ouvert pour le modèle structuré '{model_name}'. Modèle court-circuité.")
                continue
                
            retries = 2
            while retries > 0:
                start_time = time.time()
                try:
                    logger.info(f"[FALLBACK GATEWAY] Tentative structurée avec le modèle : {model_name} (CB: {cb.state.value})")
                    res = provider.generate_structured(system_prompt, user_prompt, schema, **kwargs)
                    latency = time.time() - start_time
                    cb.record_success(latency)
                    return res
                except Exception as e:
                    err_msg = str(e).lower()
                    if "429" in err_msg or "rate limit" in err_msg:
                        retries -= 1
                        if retries > 0:
                            logger.warning(f"[FALLBACK GATEWAY] Modèle structuré {model_name} a levé un Rate Limit (429). Retente après backoff...")
                            cb.record_rate_limit()
                            continue
                            
                    cb.record_failure(e)
                    backoff_delay = 0.5 + random.uniform(0, 0.5)
                    logger.warning(
                        f"[FALLBACK GATEWAY] Échec structuré du modèle {model_name} : {e}. "
                        f"Backoff {backoff_delay:.1f}s avant le modèle suivant..."
                    )
                    time.sleep(backoff_delay)
                    last_error = e
                    break
                
        raise last_error or RuntimeError("Tous les providers configurés ont échoué ou ont leurs circuits ouverts.")


def _make_claude(model: str = None) -> "ClaudeCLIProvider":
    """
    Factory helper pour créer un ClaudeCLIProvider avec un modèle spécifique.
    """
    p = ClaudeCLIProvider()
    p.default_model = model
    return p
