"""
gemini_native.py — Provider Gemini utilisant l'API REST native (pas OpenAI-compatible).

Avantages par rapport au GeminiProvider (OpenAI-compatible) :
- Context Caching implicite (systemInstruction en premier → Google cache auto le préfixe)
- Context Caching explicite (création/gestion de cachedContents via l'API)
- Google Search Grounding (injection de résultats Google Search dans le contexte)
- Accès aux thinking tokens, citations, safety settings natifs
- Meilleur token tracking (cached_content_token_count exposé)

Auteur : Antigravity IDE + Axel
Date : 2026-05-26
"""

import os
import json
import time
import logging
import requests
from typing import Dict, Any, Optional

from core.llm_timeouts import get_timeout

logger = logging.getLogger(__name__)

# Import de la classe de base LLMProvider
try:
    from core.llm_gateway import LLMProvider
except ImportError:
    # Fallback pour les tests unitaires isolés
    from abc import ABC, abstractmethod
    class LLMProvider(ABC):
        @abstractmethod
        def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any: pass
        @abstractmethod
        def generate_structured(self, system_prompt: str, user_prompt: str, schema: Dict[str, Any], **kwargs) -> Dict[str, Any]: pass


# ──────────────────────────────────────────────────────────────────
# Gestionnaire de cache explicite Gemini (cachedContents API)
# ──────────────────────────────────────────────────────────────────

class GeminiCacheManager:
    """
    Gère le cycle de vie des caches explicites Gemini.
    
    Workflow :
    1. create() → Crée un cache avec du contenu et un TTL
    2. get_active_cache_name() → Retourne le nom du cache actif si valide
    3. refresh_if_needed() → Renouvelle le cache si proche de l'expiration
    4. delete() → Supprime le cache
    
    Le stockage des caches est facturé ~1$/M tokens/heure, donc on gère
    le TTL avec soin.
    """

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        
        # État du cache actif
        self._active_cache_name: Optional[str] = None
        self._cache_expire_time: Optional[float] = None  # timestamp UNIX
        self._cache_content_hash: Optional[str] = None    # hash du contenu caché
        
        # Marge de renouvellement : renouveler 5 min avant l'expiration
        self._refresh_margin_seconds = 300
        
    def _headers(self) -> dict:
        """Headers standard pour les appels API."""
        return {"Content-Type": "application/json"}
    
    def _url(self, path: str) -> str:
        """Construit l'URL complète avec la clé API."""
        return f"{self.base_url}/{path}?key={self.api_key}"

    def create(self, system_content: str, ttl_seconds: int = 3600) -> Optional[str]:
        """
        Crée un cache explicite avec le contenu système fourni.
        
        Args:
            system_content: Le texte du system prompt à cacher (doit dépasser le minimum requis)
            ttl_seconds: Durée de vie du cache en secondes (défaut 1h)
            
        Returns:
            Le nom du cache créé (ex: "cachedContents/abc123") ou None si échec
        """
        import hashlib
        content_hash = hashlib.md5(system_content.encode()).hexdigest()
        
        # Si le même contenu est déjà caché et encore valide, ne rien faire
        if (self._active_cache_name 
            and self._cache_content_hash == content_hash 
            and self._cache_expire_time 
            and time.time() < self._cache_expire_time - self._refresh_margin_seconds):
            logger.debug(f"[GEMINI CACHE] Cache actif encore valide : {self._active_cache_name}")
            return self._active_cache_name
        
        # Supprimer l'ancien cache s'il existe
        if self._active_cache_name:
            self.delete()
        
        payload = {
            "model": f"models/{self.model}",
            "systemInstruction": {
                "parts": [{"text": system_content}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": "Cache de contexte systeme."}]
                },
                {
                    "role": "model",
                    "parts": [{"text": "Contexte charge."}]
                }
            ],
            "ttl": f"{ttl_seconds}s"
        }
        
        try:
            resp = requests.post(
                self._url("cachedContents"),
                headers=self._headers(),
                json=payload,
                timeout=(5.0, 30.0)
            )
            resp.raise_for_status()
            data = resp.json()
            
            cache_name = data.get("name")
            if cache_name:
                self._active_cache_name = cache_name
                self._cache_expire_time = time.time() + ttl_seconds
                self._cache_content_hash = content_hash
                
                # Extraire les infos de tokens du cache
                usage_metadata = data.get("usageMetadata", {})
                total_tokens = usage_metadata.get("totalTokenCount", 0)
                
                logger.info(
                    f"[GEMINI CACHE] ✅ Cache créé : {cache_name} | "
                    f"{total_tokens} tokens | TTL={ttl_seconds}s | "
                    f"Modèle={self.model}"
                )
                return cache_name
            else:
                logger.warning(f"[GEMINI CACHE] Réponse sans nom de cache : {data}")
                return None
                
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "?"
            body = e.response.text[:500] if e.response else ""
            logger.warning(
                f"[GEMINI CACHE] ❌ Erreur HTTP {status} lors de la création du cache : {body}"
            )
            # Les erreurs 400 sont souvent "contenu trop court" → log mais pas de crash
            return None
        except Exception as e:
            logger.warning(f"[GEMINI CACHE] ❌ Erreur de création du cache : {e}")
            return None

    def get_active_cache_name(self) -> Optional[str]:
        """Retourne le nom du cache actif s'il est encore valide, None sinon."""
        if not self._active_cache_name:
            return None
        if self._cache_expire_time and time.time() >= self._cache_expire_time:
            logger.info("[GEMINI CACHE] Cache expiré, nettoyage.")
            self._active_cache_name = None
            self._cache_expire_time = None
            self._cache_content_hash = None
            return None
        return self._active_cache_name

    def refresh_if_needed(self, system_content: str, ttl_seconds: int = 3600) -> Optional[str]:
        """Renouvelle le cache si proche de l'expiration ou si le contenu a changé."""
        import hashlib
        content_hash = hashlib.md5(system_content.encode()).hexdigest()
        
        # Si le contenu a changé, recréer le cache
        if self._cache_content_hash != content_hash:
            logger.info("[GEMINI CACHE] Contenu modifié, recréation du cache.")
            return self.create(system_content, ttl_seconds)
        
        # Si le cache est proche de l'expiration, recréer
        if (self._cache_expire_time 
            and time.time() >= self._cache_expire_time - self._refresh_margin_seconds):
            logger.info("[GEMINI CACHE] Cache proche de l'expiration, renouvellement.")
            return self.create(system_content, ttl_seconds)
        
        return self.get_active_cache_name()

    def delete(self):
        """Supprime le cache actif."""
        if not self._active_cache_name:
            return
        try:
            resp = requests.delete(
                self._url(self._active_cache_name),
                headers=self._headers(),
                timeout=(5.0, 10.0)
            )
            if resp.status_code in (200, 204, 404):
                logger.info(f"[GEMINI CACHE] Cache supprimé : {self._active_cache_name}")
            else:
                logger.warning(f"[GEMINI CACHE] Erreur suppression : {resp.status_code}")
        except Exception as e:
            logger.warning(f"[GEMINI CACHE] Erreur suppression : {e}")
        finally:
            self._active_cache_name = None
            self._cache_expire_time = None
            self._cache_content_hash = None

    def get_status(self) -> dict:
        """Retourne l'état du cache pour le monitoring API."""
        ttl_remaining = None
        if self._cache_expire_time:
            ttl_remaining = max(0, int(self._cache_expire_time - time.time()))
        return {
            "active": self._active_cache_name is not None,
            "cache_name": self._active_cache_name,
            "ttl_remaining_seconds": ttl_remaining,
            "content_hash": self._cache_content_hash,
            "model": self.model,
        }


# ──────────────────────────────────────────────────────────────────
# Provider Gemini Natif (API REST directe)
# ──────────────────────────────────────────────────────────────────

class GeminiNativeProvider(LLMProvider):
    """
    Provider Gemini utilisant l'API REST native (pas l'endpoint OpenAI-compatible).
    
    Avantages :
    - Context Caching implicite (systemInstruction placé en premier)
    - Context Caching explicite (via GeminiCacheManager)
    - Google Search Grounding
    - JSON mode natif (response_mime_type)
    - Streaming natif (streamGenerateContent avec SSE)
    - Accès aux thinking tokens et citations
    
    L'interface LLMProvider est respectée : generate(), generate_structured(), 
    generate_stream() — le reste du moteur ne voit aucune différence.
    """
    
    def __init__(self, api_key: str, model: str = "gemini-3.5-flash",
                 search_grounding_available: bool = False,
                 enable_explicit_cache: bool = True,
                 cache_ttl_seconds: int = 3600,
                 use_key_pool: bool = False):
        """
        Args:
            api_key: Clé API Google (gratuite ou payante)
            model: Nom du modèle Gemini (ex: "gemini-3.5-flash", "gemini-2.5-pro")
            search_grounding_available: Si True, le Search Grounding est débloqué
                                        (nécessite clé payante GCP)
            enable_explicit_cache: Si True, utilise le caching explicite pour les
                                   system prompts volumineux
            cache_ttl_seconds: Durée de vie des caches explicites (défaut 1h)
            use_key_pool: Si True, utilise le KeyPool pour rotation automatique
                          des clés Free Tier sur rate-limit (429)
        """
        self.api_key = api_key
        # Mapping de compatibilité robuste pour rediriger les noms de modèles fictifs/futurs
        # vers les modèles Gemini réels et opérationnels supportés par Google AI Studio.
        gemini_compat_mapping = {
            # Versions de dernière génération (3.x) — Accès Direct
            "gemini-3.5-flash": "gemini-3.5-flash",
            "gemini-3.1-flash-lite": "gemini-3.1-flash-lite",
            "gemini-3.1-pro": "gemini-3.1-pro-preview",
            "gemini-3.1-pro-preview": "gemini-3.1-pro-preview",
            "gemini-3.1-pro-preview-customtools": "gemini-3.1-pro-preview-customtools",
            "gemini-3-pro-preview": "gemini-3-pro-preview",
            "gemini-3.1-flash": "gemini-3.5-flash",  # Fallback logique
            
            # Versions intermédiaires (2.5) — Accès Direct
            "gemini-2.5-flash": "gemini-2.5-flash",
            "gemini-2.5-pro": "gemini-2.5-pro",
            
            # Redirections de secours pour modèles bloqués ou obsolètes (2.0 / 1.5)
            "gemini-2.0-flash": "gemini-3.5-flash",  # Rediriger le 2.0 bloqué vers le 3.5 Flash ouvert !
            "gemini-2.0-flash-lite-001": "gemini-3.1-flash-lite",
            "gemini-1.5-flash": "gemini-2.5-flash",
            "gemini-1.5-pro": "gemini-2.5-pro",
        }
        self.model = gemini_compat_mapping.get(model, model)
        self.search_grounding_available = search_grounding_available
        self.enable_explicit_cache = enable_explicit_cache
        self.cache_ttl_seconds = cache_ttl_seconds
        self.use_key_pool = use_key_pool
        
        # Pool de clés pour la rotation automatique Free Tier
        self._key_pool = None
        if use_key_pool:
            try:
                from core.key_pool import get_key_pool
                self._key_pool = get_key_pool()
            except Exception:
                pass
        
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        
        # Gestionnaire de cache explicite
        self._cache_manager = GeminiCacheManager(api_key, model) if enable_explicit_cache else None
        
        # Seuil minimum de tokens pour le caching explicite
        # Gemini 3.x : 4096 tokens (~16K chars en FR)
        # Gemini 2.x : 2048 tokens (~8K chars en FR)
        self._min_cache_chars = 16000 if "3." in model or "3-" in model else 8000
    
    def _get_active_key(self) -> str:
        """Retourne la clé API active (pool ou fixe)."""
        if self._key_pool:
            key = self._key_pool.get_free_key()
            return key if key else self.api_key
        return self.api_key
    
    def _api_url(self, method: str = "generateContent", key: str = None) -> str:
        """Construit l'URL d'appel API avec la clé."""
        active_key = key or self._get_active_key()
        return f"{self.base_url}/models/{self.model}:{method}?key={active_key}"
    
    def _build_contents(self, user_prompt: str, **kwargs) -> list:
        """
        Construit la liste des messages au format natif Gemini.
        
        Si un historique de messages est fourni via kwargs["messages"],
        il est converti du format OpenAI vers le format natif Gemini.
        """
        messages = kwargs.get("messages")
        if messages:
            # Conversion format OpenAI → format natif Gemini
            contents = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    continue  # Les system messages sont gérés via systemInstruction
                
                # Éviter les contenus vides (causes d'erreur 400 de l'API Google)
                if content is None:
                    content = ""
                content_str = str(content).strip()
                
                if role == "assistant":
                    parts = []
                    if content_str:
                        parts.append({"text": content_str})
                    if msg.get("tool_calls"):
                        for tc in msg["tool_calls"]:
                            fn = tc.get("function", {})
                            try:
                                args = json.loads(fn.get("arguments", "{}"))
                            except Exception:
                                args = {}
                            parts.append({
                                "functionCall": {
                                    "name": fn.get("name", ""),
                                    "args": args
                                }
                            })
                    if not parts:
                        parts.append({"text": "[Message vide]"})
                    contents.append({
                        "role": "model",
                        "parts": parts
                    })
                elif role in ("tool", "function"):
                    name = msg.get("name")
                    if not name and msg.get("tool_call_id"):
                        name = "unknown_tool"
                        for prev in reversed(contents):
                            if prev.get("role") == "model":
                                for part in prev.get("parts", []):
                                    if "functionCall" in part:
                                        name = part["functionCall"].get("name", "unknown_tool")
                                        break
                    if not name:
                        name = "unknown_tool"
                    try:
                        response_dict = json.loads(content_str)
                        if not isinstance(response_dict, dict):
                            response_dict = {"result": response_dict}
                    except Exception:
                        response_dict = {"output": content_str}
                    contents.append({
                        "role": "function",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": name,
                                    "response": response_dict
                                }
                            }
                        ]
                    })
                else: # role == "user"
                    if not content_str:
                        content_str = "[Message vide]"
                    contents.append({
                        "role": "user",
                        "parts": [{"text": content_str}]
                    })
            return contents
        
        return [{"role": "user", "parts": [{"text": user_prompt}]}]
    
    def _build_system_instruction(self, system_prompt: str) -> Optional[dict]:
        """Construit l'objet systemInstruction natif pour le caching implicite."""
        if not system_prompt or not system_prompt.strip():
            return None
        return {"parts": [{"text": system_prompt}]}
    
    def _extract_system_from_messages(self, kwargs: dict) -> str:
        """Extrait le system prompt des messages OpenAI-style si présent."""
        messages = kwargs.get("messages", [])
        for msg in messages:
            if msg.get("role") == "system":
                return msg.get("content", "")
        return ""
    
    def _convert_schema_to_gemini(self, schema: Any) -> Any:
        """Convertit récursivement les types d'un schéma OpenAPI/OpenAI en majuscules pour Gemini et exclut les clés non supportées comme $schema ou $id."""
        if isinstance(schema, dict):
            new_schema = {}
            for k, v in schema.items():
                if k.startswith("$"):
                    continue
                # Exclure les clés JSON Schema non supportées par Gemini
                if k in ("additionalProperties", "propertyNames", "patternProperties", "dependencies"):
                    continue
                if k == "type" and isinstance(v, str):
                    new_schema[k] = v.upper()
                else:
                    new_schema[k] = self._convert_schema_to_gemini(v)
            if "type" not in new_schema and ("properties" in new_schema or "required" in new_schema):
                new_schema["type"] = "OBJECT"
            return new_schema
        elif isinstance(schema, list):
            return [self._convert_schema_to_gemini(item) for item in schema]
        return schema

    def _build_tools(self, use_search_grounding: bool = False, tools: list = None) -> Optional[list]:
        """
        Construit la liste des outils au format natif Gemini.
        
        Gère à la fois les function declarations (tool_calls du moteur)
        et le Google Search Grounding.
        """
        result_tools = []
        
        # Google Search Grounding
        if use_search_grounding and self.search_grounding_available:
            result_tools.append({"google_search": {}})
        
        # Function declarations (traduction OpenAI → Gemini natif)
        if tools:
            functions = []
            for tool in tools:
                if tool.get("type") == "function":
                    func = tool.get("function", {})
                    params = func.get("parameters")
                    if params:
                        adapted_params = self._convert_schema_to_gemini(params)
                    else:
                        adapted_params = {"type": "OBJECT", "properties": {}}
                    functions.append({
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "parameters": adapted_params
                    })
            if functions:
                result_tools.append({"function_declarations": functions})
        
        return result_tools if result_tools else None

    def _translate_tool_calls(self, response_parts: list) -> Optional[list]:
        """
        Traduit les function_calls du format natif Gemini vers le format
        OpenAI attendu par le reste du moteur (ExecutorAgent, DAGRunner, etc.).
        
        Format Gemini natif :
            {"functionCall": {"name": "tool_name", "args": {...}}}
        
        Format OpenAI (attendu par le moteur) :
            {"tool_calls": [{"id": "call_...", "type": "function", 
                            "function": {"name": "tool_name", "arguments": "{...}"}}]}
        """
        tool_calls = []
        for i, part in enumerate(response_parts):
            if "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append({
                    "id": f"call_gemini_{i}_{int(time.time())}",
                    "type": "function",
                    "function": {
                        "name": fc.get("name", ""),
                        "arguments": json.dumps(fc.get("args", {}))
                    }
                })
        return tool_calls if tool_calls else None

    def _record_tokens(self, usage_metadata: dict, model_name: str = None, **kwargs):
        """Enregistre la consommation de tokens dans le tracker."""
        try:
            from core.token_tracker import record_usage
            
            prompt_tokens = usage_metadata.get("promptTokenCount", 0)
            completion_tokens = usage_metadata.get("candidatesTokenCount", 0)
            cached_tokens = usage_metadata.get("cachedContentTokenCount", 0)
            
            # Log spécial si des tokens cachés sont détectés (preuve que le caching fonctionne)
            if cached_tokens > 0:
                logger.info(
                    f"[GEMINI NATIF] 💰 Cache hit ! {cached_tokens} tokens cachés "
                    f"(économie ~{cached_tokens * 0.9:.0f} tokens facturés)"
                )
            
            record_usage(
                model_name or self.model,
                prompt_tokens,
                completion_tokens,
                session_id=kwargs.get("session_id")
            )
        except Exception as e:
            logger.warning(f"[GEMINI NATIF] Erreur token tracking : {e}")

    # ──────────────────────────────────────────────────────────
    # Méthode principale : generate()
    # ──────────────────────────────────────────────────────────
    
    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> Any:
        """
        Appelle l'API native Gemini generateContent.
        
        Gère automatiquement :
        - Le caching implicite (systemInstruction en premier)
        - Le caching explicite (si le system prompt est assez long)
        - Le Search Grounding (si use_search_grounding=True dans kwargs)
        - Les tool_calls (traduction Gemini → OpenAI)
        """
        # Extraire le system prompt des messages si fourni en format OpenAI
        if not system_prompt and kwargs.get("messages"):
            system_prompt = self._extract_system_from_messages(kwargs)
        
        # Construire le payload de base
        payload: Dict[str, Any] = {
            "contents": self._build_contents(user_prompt, **kwargs),
            "generationConfig": {
                "temperature": kwargs.get("temperature", 0.0),
            }
        }
        
        # Stratégie de caching :
        # 1. Si le system prompt est assez long ET le cache explicite est activé → utiliser le cache
        # 2. Sinon → utiliser systemInstruction (caching implicite automatique)
        cache_name = None
        if (self._cache_manager 
            and system_prompt 
            and len(system_prompt) >= self._min_cache_chars):
            # Tenter le cache explicite
            cache_name = self._cache_manager.refresh_if_needed(
                system_prompt, self.cache_ttl_seconds
            )
        
        if cache_name:
            # Cache explicite actif → référencer le cache, pas de systemInstruction
            payload["cachedContent"] = cache_name
        else:
            # Cache implicite → placer le system prompt dans systemInstruction
            sys_instr = self._build_system_instruction(system_prompt)
            if sys_instr:
                payload["systemInstruction"] = sys_instr
        
        # Outils (Search Grounding + function declarations)
        use_grounding = kwargs.get("use_search_grounding", False)
        tools = self._build_tools(
            use_search_grounding=use_grounding,
            tools=kwargs.get("tools")
        )
        if tools:
            payload["tools"] = tools
        
        logger.debug(
            f"[GEMINI NATIF] Appel {self.model} | "
            f"cache={'explicite' if cache_name else 'implicite'} | "
            f"grounding={use_grounding}"
        )
        
        # Déterminer la clé active pour cet appel
        active_key = self._get_active_key()
        api_url = self._api_url("generateContent", key=active_key)
        
        try:
            response = requests.post(
                api_url,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=get_timeout("gemini")
            )
            
            # Gestion 429 (rate-limit) → rotation de clé via le KeyPool
            if response.status_code == 429 and self._key_pool:
                self._key_pool.report_rate_limit(active_key)
                # Retenter UNIQUEMENT avec une clé réellement disponible :
                # allow_cooldown=False évite de brûler une requête garantie-429
                # quand toutes les clés sont en cooldown → laisse la cascade
                # escalader vers un autre provider (cf. #T62).
                next_key = self._key_pool.get_free_key(allow_cooldown=False)
                if next_key and next_key != active_key:
                    logger.info(
                        f"[GEMINI NATIF] 🔄 429 → rotation de clé (KeyPool)"
                    )
                    retry_url = self._api_url("generateContent", key=next_key)
                    response = requests.post(
                        retry_url,
                        headers={"Content-Type": "application/json"},
                        json=payload,
                        timeout=get_timeout("gemini")
                    )
                    if response.status_code == 200:
                        self._key_pool.report_success(next_key)
                else:
                    wait = self._key_pool.seconds_until_available()
                    logger.warning(
                        f"[GEMINI NATIF] ⚠️ 429 et toutes les clés en cooldown "
                        f"(dispo dans {wait:.0f}s) → pas de retry, escalade cascade."
                    )
            
            if response.status_code != 200:
                logger.error(f"[GEMINI NATIF] Échec de la requête ({response.status_code}) : {response.text}")
            response.raise_for_status()
            data = response.json()
            
            # Signaler le succès au pool
            if self._key_pool:
                self._key_pool.report_success(active_key)
            
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "?"
            body = e.response.text[:500] if e.response else ""
            logger.error(f"[GEMINI NATIF] Erreur HTTP {status} : {body}")
            
            # Si c'est une erreur de cache (404 = cache expiré), retenter sans cache
            if status == 404 and cache_name:
                logger.warning("[GEMINI NATIF] Cache expiré côté serveur, retry sans cache.")
                if self._cache_manager:
                    self._cache_manager.delete()
                # Reconstruire avec systemInstruction
                payload.pop("cachedContent", None)
                sys_instr = self._build_system_instruction(system_prompt)
                if sys_instr:
                    payload["systemInstruction"] = sys_instr
                response = requests.post(
                    self._api_url("generateContent"),
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=get_timeout("gemini")
                )
                response.raise_for_status()
                data = response.json()
            else:
                raise
        
        # Extraction de la réponse
        usage_metadata = data.get("usageMetadata", {})
        self._record_tokens(usage_metadata, **kwargs)
        
        candidates = data.get("candidates", [])
        if not candidates:
            logger.warning(f"[GEMINI NATIF] Aucun candidat retourné : {data}")
            return ""
        
        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        
        if not parts:
            return ""
        
        # Vérifier si c'est un appel d'outil (function_call)
        tool_calls = self._translate_tool_calls(parts)
        if tool_calls:
            # Retourner au format attendu par le moteur (comme OpenAI)
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls
            }
        
        # Extraire le texte de la réponse
        text_parts = [p.get("text", "") for p in parts if "text" in p]
        return "\n".join(text_parts)
    
    # ──────────────────────────────────────────────────────────
    # Méthode structurée : generate_structured()
    # ──────────────────────────────────────────────────────────
    
    def generate_structured(self, system_prompt: str, user_prompt: str,
                           schema: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """
        Appelle l'API Gemini avec le JSON mode natif (response_mime_type).
        
        Avantage sur l'OpenAI-compatible : le modèle est contraint nativement
        à produire du JSON valide (pas juste un hint dans le system prompt).
        """
        sys_prompt = system_prompt + "\nTu DOIS répondre UNIQUEMENT au format JSON strict."
        
        # Forcer le JSON mode natif dans les kwargs
        kwargs["_force_json_mode"] = True
        
        # Sauvegarder les kwargs originaux et ajouter la config JSON
        original_generate = self.generate
        
        # Construire l'appel manuellement pour injecter response_mime_type
        if not sys_prompt and kwargs.get("messages"):
            sys_prompt = self._extract_system_from_messages(kwargs)
        
        payload: Dict[str, Any] = {
            "contents": self._build_contents(user_prompt, **kwargs),
            "generationConfig": {
                "temperature": kwargs.get("temperature", 0.0),
                "responseMimeType": "application/json",
            }
        }
        
        # Ajouter le schéma de réponse si fourni et non vide
        if schema:
            payload["generationConfig"]["responseSchema"] = schema
        
        # System instruction (pas de cache explicite pour les appels structurés courts)
        sys_instr = self._build_system_instruction(sys_prompt)
        if sys_instr:
            payload["systemInstruction"] = sys_instr
        
        logger.debug(f"[GEMINI NATIF] Appel structuré {self.model} (JSON mode natif)")
        
        try:
            response = requests.post(
                self._api_url("generateContent"),
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=get_timeout("gemini")
            )
            response.raise_for_status()
            data = response.json()
            
            # Token tracking
            usage_metadata = data.get("usageMetadata", {})
            self._record_tokens(usage_metadata, **kwargs)
            
            # Extraction du JSON
            candidates = data.get("candidates", [])
            if not candidates:
                return {}
            
            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                return {}
            
            text = parts[0].get("text", "{}")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                # Fallback : nettoyer le markdown
                cleaned = text.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                elif cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                try:
                    return json.loads(cleaned.strip())
                except json.JSONDecodeError:
                    logger.error(f"[GEMINI NATIF] JSON invalide : {text[:200]}")
                    return {}
                    
        except Exception as e:
            logger.error(f"[GEMINI NATIF] Erreur appel structuré : {e}")
            return {}
    
    # ──────────────────────────────────────────────────────────
    # Streaming : generate_stream()
    # ──────────────────────────────────────────────────────────
    
    def generate_stream(self, system_prompt: str, user_prompt: str, **kwargs):
        """
        Streaming natif Gemini via streamGenerateContent (SSE).
        
        Le format SSE Gemini est différent d'OpenAI :
        - Pas de "data: [DONE]" → le stream se termine quand la connexion ferme
        - Chaque chunk est un JSON complet avec candidates[0].content.parts[0].text
        
        Yields:
            dict: {"token": str, "done": bool, "usage": dict|None}
        """
        if not system_prompt and kwargs.get("messages"):
            system_prompt = self._extract_system_from_messages(kwargs)
            
        payload: Dict[str, Any] = {
            "contents": self._build_contents(user_prompt, **kwargs),
            "generationConfig": {
                "temperature": kwargs.get("temperature", 0.0),
            }
        }
        
        sys_instr = self._build_system_instruction(system_prompt)
        if sys_instr:
            payload["systemInstruction"] = sys_instr
        
        logger.debug(f"[GEMINI NATIF] Appel streaming {self.model}")
        
        try:
            # streamGenerateContent retourne un flux de JSON objects séparés par des newlines
            url = f"{self.base_url}/models/{self.model}:streamGenerateContent?alt=sse&key={self.api_key}"
            response = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=get_timeout("gemini"),
                stream=True
            )
            response.raise_for_status()
            
            total_text = ""
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if not data_str:
                    continue
                    
                try:
                    chunk = json.loads(data_str)
                    candidates = chunk.get("candidates", [])
                    if not candidates:
                        continue
                    
                    parts = candidates[0].get("content", {}).get("parts", [])
                    for part in parts:
                        text = part.get("text", "")
                        if text:
                            total_text += text
                            yield {"token": text, "done": False, "usage": None}
                    
                    # Vérifier si c'est le dernier chunk (finishReason présent)
                    finish_reason = candidates[0].get("finishReason")
                    if finish_reason:
                        # Enregistrer les tokens avec les metadata du dernier chunk
                        usage_metadata = chunk.get("usageMetadata", {})
                        self._record_tokens(usage_metadata, **kwargs)
                        yield {"token": "", "done": True, "usage": usage_metadata}
                        return
                        
                except json.JSONDecodeError:
                    continue
            
            # Si on arrive ici sans finishReason, le stream s'est terminé normalement
            # Estimer les tokens
            from core.token_tracker import record_usage
            prompt_len = max(1, (len(system_prompt or "") + len(user_prompt)) // 4)
            record_usage(
                self.model,
                prompt_len,
                max(1, len(total_text) // 4),
                session_id=kwargs.get("session_id")
            )
            yield {"token": "", "done": True, "usage": None}
            
        except Exception as e:
            logger.error(f"[GEMINI NATIF] Erreur streaming : {e}")
            yield {"token": f"[Erreur streaming: {e}]", "done": True, "usage": None}
    
    # ──────────────────────────────────────────────────────────
    # TTS : generate_audio() [P8]
    # ──────────────────────────────────────────────────────────
    
    def generate_audio(
        self, text: str, voice_name: str = "Kore", output_path: str = None, **kwargs
    ) -> dict:
        """
        [P8] Génère de l'audio TTS via l'API Gemini native.
        
        Utilise responseModalities: ['AUDIO'] avec speechConfig pour
        produire de l'audio synthétisé à partir de texte.
        
        Args:
            text: Le texte à synthétiser en audio
            voice_name: Nom de la voix Gemini (Kore, Charon, Fenrir, Aoede, Puck)
            output_path: Chemin de sortie pour le fichier WAV (optionnel)
            
        Returns:
            dict avec les clés :
            - success: bool
            - audio_b64: str (audio PCM en base64, si pas de output_path)
            - output_path: str (chemin du fichier WAV, si output_path fourni)
            - duration_estimate_s: float (estimation de la durée en secondes)
            - error: str (message d'erreur si échec)
        """
        payload = {
            "contents": [{"role": "user", "parts": [{"text": f"Dis à voix haute: {text}"}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": voice_name,
                        }
                    }
                }
            }
        }
        
        logger.info(f"[GEMINI TTS] Synthèse audio : voix={voice_name}, texte={len(text)} chars")
        
        try:
            response = requests.post(
                self._api_url("generateContent"),
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=get_timeout("gemini")
            )
            response.raise_for_status()
            data = response.json()
            
            # Token tracking
            usage_metadata = data.get("usageMetadata", {})
            self._record_tokens(usage_metadata, **kwargs)
            
            candidates = data.get("candidates", [])
            if not candidates:
                return {"success": False, "error": "Aucun candidat retourné"}
            
            parts = candidates[0].get("content", {}).get("parts", [])
            
            # Chercher la partie audio dans les parts
            for part in parts:
                inline_data = part.get("inlineData")
                if inline_data and "audio" in inline_data.get("mimeType", ""):
                    audio_b64 = inline_data.get("data", "")
                    mime_type = inline_data.get("mimeType", "audio/pcm")
                    
                    if output_path:
                        # Sauvegarder en fichier WAV
                        import base64
                        audio_bytes = base64.b64decode(audio_b64)
                        
                        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                        
                        # Wrapper PCM brut en WAV (16-bit, 24kHz, mono)
                        import struct
                        sample_rate = 24000
                        bits_per_sample = 16
                        num_channels = 1
                        data_size = len(audio_bytes)
                        
                        with open(output_path, "wb") as f:
                            # En-tête WAV
                            f.write(b"RIFF")
                            f.write(struct.pack("<I", 36 + data_size))
                            f.write(b"WAVE")
                            f.write(b"fmt ")
                            f.write(struct.pack("<I", 16))  # Taille du chunk fmt
                            f.write(struct.pack("<H", 1))   # PCM
                            f.write(struct.pack("<H", num_channels))
                            f.write(struct.pack("<I", sample_rate))
                            f.write(struct.pack("<I", sample_rate * num_channels * bits_per_sample // 8))
                            f.write(struct.pack("<H", num_channels * bits_per_sample // 8))
                            f.write(struct.pack("<H", bits_per_sample))
                            f.write(b"data")
                            f.write(struct.pack("<I", data_size))
                            f.write(audio_bytes)
                        
                        duration_s = data_size / (sample_rate * num_channels * bits_per_sample // 8)
                        logger.info(f"[GEMINI TTS] Audio sauvegardé : {output_path} ({duration_s:.1f}s)")
                        
                        return {
                            "success": True,
                            "output_path": output_path,
                            "duration_estimate_s": round(duration_s, 1),
                            "mime_type": mime_type,
                            "size_bytes": data_size,
                        }
                    else:
                        # Retourner le base64 directement
                        import base64
                        audio_bytes = base64.b64decode(audio_b64)
                        duration_s = len(audio_bytes) / (24000 * 2)  # 24kHz, 16-bit
                        
                        return {
                            "success": True,
                            "audio_b64": audio_b64,
                            "duration_estimate_s": round(duration_s, 1),
                            "mime_type": mime_type,
                        }
            
            return {"success": False, "error": "Aucune partie audio trouvée dans la réponse"}
            
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "?"
            body = e.response.text[:300] if e.response else ""
            logger.error(f"[GEMINI TTS] Erreur HTTP {status} : {body}")
            return {"success": False, "error": f"HTTP {status}: {body}"}
        except Exception as e:
            logger.error(f"[GEMINI TTS] Erreur : {e}")
            return {"success": False, "error": str(e)}
    
    # ──────────────────────────────────────────────────────────
    # Méthodes utilitaires
    # ──────────────────────────────────────────────────────────
    
    def get_cache_status(self) -> dict:
        """Retourne l'état du cache pour le monitoring."""
        if self._cache_manager:
            return self._cache_manager.get_status()
        return {"active": False, "cache_name": None, "model": self.model}
    
    def cleanup(self):
        """Nettoyage : supprimer les caches actifs (à appeler à l'arrêt du moteur)."""
        if self._cache_manager:
            self._cache_manager.delete()
