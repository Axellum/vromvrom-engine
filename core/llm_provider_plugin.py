"""
core/llm_provider_plugin.py — Point d'entrée de compatibilité (shim).

⚠️ CE FICHIER EST UN SHIM DE COMPATIBILITÉ. Le module réel du contrat de plugin
provider vit dans `core/llm/provider_plugin.py` (dossier `core/llm/`, pas à la
racine de `core/`).

Ce shim ré-exporte l'intégralité de l'API publique du module réel pour que les
imports historiques `from core.llm_provider_plugin import ...` continuent de
fonctionner sans modification. Il ne contient AUCUNE logique propre.

Structure réelle du pattern provider :
  - `core/llm/provider_plugin.py`        → contrat d'extension (ProviderPlugin,
                                           ProviderDescriptor, ModelSpec, AuthSpec,
                                           verifier_interface_transport, ...)
  - `core/llm/providers/base.py`         → classe de base LLMProvider (generate,
                                           generate_structured, generate_async, ...)
  - `core/llm/providers/deepseek.py`     → LMStudioProvider, OllamaDeckProvider,
                                           ClaudeCLIProvider, FallbackProvider
  - `core/llm/providers/gemini.py`       → GeminiProvider, GeminiCLIProvider
  - `core/llm/builtin_providers.py`      → plugins internes (BUILTIN_PROVIDER_PLUGINS)
  - `core/llm/provider_registration.py`  → register_provider_plugins()
  - `core/llm/provider_health.py`        → sonde d'accessibilité d'hôte (TCP)
  - `core/llm/circuit_breaker.py`        → disjoncteur par modèle
  - `core/llm/fallback_trace.py`         → trace du modèle ayant répondu
"""

from __future__ import annotations

# Ré-export intégral du module réel. `*` est sûr ici : le module source ne définit
# que des noms publics (classes, dataclasses, fonctions, constantes).
from core.llm.provider_plugin import *  # noqa: F401,F403
from core.llm.provider_plugin import (  # noqa: F401
    AuthKind,
    AuthSpec,
    CredentialsManquantes,
    METHODES_TRANSPORT_REQUISES,
    ModelSpec,
    ProviderDescriptor,
    ProviderPlugin,
    ProviderPluginError,
    TransportInvalide,
    verifier_interface_transport,
)
