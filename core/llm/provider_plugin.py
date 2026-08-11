"""
core/llm/provider_plugin.py — Contrat d'extension pour les providers LLM (#T231, étape 1).

Problème résolu (cf. docs/design_provider_plugin_contract.md) : un provider existe
aujourd'hui à deux endroits sans lien — le catalogue (`core/models_db.py`, peuplé par
`seed_models_db.py`) et le gateway (objet Python instancié dans `core/llm_gateway.py`).
Rien ne garantit qu'ils coïncident, au point qu'on affiche la divergence dans l'IHM
(badge « non câblé », `api/routes/context.py:218`).

Un plugin réunit les deux : un descripteur déclaratif ET la fabrique du transport.
L'invariant qui supprime la divergence est porté par `register_provider_plugins()`
(`core/llm/provider_registration.py`) : on n'écrit au catalogue QUE ce dont le
transport a pu être construit.

Ce module ne fait qu'ajouter un point d'extension : il ne modifie ni le gateway, ni le
seed, ni la logique d'arbitrage (Elo, cascade, budget_guard, provider_scorer restent
strictement hors périmètre, cf. §2 du design).

Deux écarts assumés vis-à-vis de l'esquisse de conception, motivés par le code réel :

1. `type` NE PEUT PAS porter le type de transport. L'esquisse proposait
   `type: "openai_compat" | "native" | ...`, mais la colonne `providers.type` porte
   déjà un autre vocabulaire — le canal commercial — et il est *lu par une requête* :
   `core/models_db.py:1121` fait `WHERE type IN ('pay_as_you_go', 'free')`. Écrire
   "openai_compat" ferait purement et simplement disparaître le provider de cette
   requête. `type` conserve donc la valeur du catalogue, et le type de transport vit
   dans un champ distinct, `transport`.

2. `build()` ne peut pas promettre une sous-classe de `LLMProvider` :
   `OpenAICompatibleProvider` (`core/openai_compat_provider.py:149`) n'hérite de rien
   et fonctionne par duck-typing — c'est déjà ainsi que le gateway le range dans son
   `dict[str, LLMProvider]`. On vérifie donc la *forme* (méthodes présentes) et non
   l'ascendance, via `verifier_interface_transport()`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

# Méthodes que tout transport doit exposer pour être utilisable par le gateway et les
# agents. `generate_async`/`generate_structured_async` sont fournies par défaut par
# `LLMProvider` (base.py:69) et surchargées par les providers à I/O réseau ; un
# transport qui ne les aurait pas casserait le hot-path async migré en D5.
METHODES_TRANSPORT_REQUISES = (
    "generate",
    "generate_structured",
    "generate_async",
    "generate_structured_async",
)

AuthKind = Literal["api_key", "oauth", "device", "cli", "none"]


class ProviderPluginError(Exception):
    """Erreur générique de plugin provider."""


class CredentialsManquantes(ProviderPluginError):
    """Les identifiants nécessaires à `build()` sont absents ou vides.

    Volontairement distincte d'une erreur de transport : c'est le cas *normal* d'un
    provider non configuré sur cette machine, que l'enregistrement doit écarter en
    silence explicite (log) plutôt que de faire échouer tout le démarrage.
    """


class TransportInvalide(ProviderPluginError):
    """`build()` a rendu un objet qui n'expose pas l'interface attendue."""


def verifier_interface_transport(transport: Any) -> None:
    """Vérifie par duck-typing qu'un transport est utilisable par le gateway.

    On ne teste pas `isinstance(..., LLMProvider)` : `OpenAICompatibleProvider`
    n'hérite d'aucune base et représente à lui seul 9 des providers actuels.
    """
    manquantes = [
        nom for nom in METHODES_TRANSPORT_REQUISES if not callable(getattr(transport, nom, None))
    ]
    if manquantes:
        raise TransportInvalide(
            f"{type(transport).__name__} n'expose pas : {', '.join(manquantes)}. "
            "Un transport doit fournir l'interface de core/llm/providers/base.py."
        )


@dataclass(frozen=True)
class AuthSpec:
    """Décrit COMMENT s'authentifier — et comment l'IHM doit le demander.

    C'est ce champ qui permettra à l'écran « ajouter un provider » d'être générique :
    le frontend rendra ce que le plugin déclare, sans connaître aucun provider en dur.
    """

    kind: AuthKind
    env_var: str | None = None  # ex. "MISTRAL_API_KEY" (kind="api_key")
    instructions: str = ""  # affiché tel quel dans l'IHM
    connect_label: str = "Connecter"
    doc_url: str | None = None


@dataclass(frozen=True)
class ModelSpec:
    """Un modèle exposé par le provider. Les noms de champs suivent les colonnes de
    la table `models` (`core/models_db.py:105`) pour que l'enregistrement reste un
    INSERT mécanique, sans couche de traduction.

    `tier` = canal d'accès (free/paid/subscription/pro/local) ;
    `routing_tier` = capacité pour le routeur (leger/moyen/fort, None = hors routage).
    Les deux sont distincts et le restent (cf. commentaire de `models_db.py:140`).

    `api_model_id` = nom EXACT attendu par l'API, quand il diffère de l'identifiant du
    catalogue. Le catalogue utilise parfois un alias pour désambiguïser : `id` doit être
    unique sur toute la table `models`, alors que deux providers peuvent servir le même
    nom de modèle. Constaté le 10/08 en interrogeant les API en direct : le catalogue
    porte `gemma-4-31b-cerebras` (parce que `gemma-4-31b` désignait déjà le modèle local
    LM Studio) et l'appel avec cet identifiant renvoie **HTTP 404** chez Cerebras, le
    vrai nom étant `gemma-4-31b`. Sans ce champ, l'alias part tel quel dans la requête.
    `None` = l'identifiant du catalogue est aussi celui de l'API (cas majoritaire).
    """

    id: str
    api_model_id: str | None = None
    display_name: str = ""
    tier: str = "paid"
    routing_tier: str | None = None
    context_input: int | None = None
    context_output: int | None = None
    cost_input_per_m: float | None = None
    cost_output_per_m: float | None = None
    supports_tools: bool = False
    supports_vision: bool = False
    supports_json_mode: bool = False
    supports_streaming: bool = False
    speciality: str | None = None
    recommended_use: str | None = None
    notes: str = ""

    @property
    def nom_api(self) -> str:
        """Nom à envoyer réellement à l'API (alias résolu)."""
        return self.api_model_id or self.id

    def en_colonnes(self, provider_id: str) -> dict[str, Any]:
        """Projette le spec sur les colonnes de `models` (pour `upsert_model`)."""
        return {
            "provider_id": provider_id,
            "display_name": self.display_name or self.id,
            # [#T254] `status` n'est volontairement PAS projeté : le poser à
            # 'active' à chaque enregistrement réactiverait, à chaque démarrage
            # du moteur, tout modèle désactivé depuis l'IHM (#T243) — l'interrupteur
            # serait sans effet durable. À la création, le défaut SQL vaut 'active'.
            "tier": self.tier,
            "routing_tier": self.routing_tier,
            "context_input": self.context_input,
            "context_output": self.context_output,
            "cost_input_per_m": self.cost_input_per_m,
            "cost_output_per_m": self.cost_output_per_m,
            "supports_tools": int(self.supports_tools),
            "supports_vision": int(self.supports_vision),
            "supports_json_mode": int(self.supports_json_mode),
            "supports_streaming": int(self.supports_streaming),
            "speciality": self.speciality,
            "recommended_use": self.recommended_use,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ProviderDescriptor:
    """Carte d'identité déclarative d'un provider.

    Alimente directement la table `providers` (`core/models_db.py:91`).

    ⚠️ `type` porte le vocabulaire DÉJÀ en base ("pay_as_you_go", "free", ...) et non
    le type de transport : `models_db.py:1121` filtre dessus, et `models_db.py:1156`
    l'affiche. Le type de transport est dans `transport`, qui n'est pas persisté.
    """

    id: str
    name: str
    type: str
    transport: Literal["openai_compat", "native", "cli", "local"] = "openai_compat"
    api_endpoint: str | None = None
    auth: AuthSpec = field(default_factory=lambda: AuthSpec(kind="none"))
    confidentiality: str = "none"
    cascade_priority: float = 5.0
    models: tuple[ModelSpec, ...] = ()
    notes: str = ""

    def en_colonnes(self) -> dict[str, Any]:
        """Projette le descripteur sur les colonnes de `providers`."""
        return {
            "name": self.name,
            "type": self.type,
            "api_endpoint": self.api_endpoint,
            "auth_method": self.auth.kind,
            "confidentiality": self.confidentiality,
            "cascade_priority": self.cascade_priority,
            "notes": self.notes,
        }


class ProviderPlugin(ABC):
    """Un plugin = un descripteur + une fabrique de transport.

    `build()` lève au lieu de rendre `None` : un provider à moitié construit est
    exactement l'état que ce chantier cherche à rendre non représentable.
    """

    @property
    @abstractmethod
    def descriptor(self) -> ProviderDescriptor:
        """Métadonnées statiques — lisibles sans aucun secret ni appel réseau."""

    @abstractmethod
    def build(self, credentials: dict[str, str], model: str | None = None) -> Any:
        """Instancie le transport, ou lève `CredentialsManquantes`.

        `credentials` est un dict de type environnement (souvent `os.environ`) : le
        plugin y pioche ce que son `AuthSpec` déclare, il ne lit jamais l'environnement
        lui-même — c'est ce qui rend l'enregistrement testable sans variable globale.

        `model` permet de construire un transport pour un modèle précis. C'est ce dont
        l'étape 2 aura besoin : le gateway range aujourd'hui UNE instance PAR modèle
        (`core/llm_gateway.py:232-237` pour Mistral). `None` = le premier modèle déclaré.
        """

    def health_check(self, transport: Any) -> tuple[bool, str]:
        """Vérification optionnelle (une mini-génération). Défaut : aucun appel réseau.

        Alimentera le bouton « Ping » existant de `ihm-v2/src/views/LLMRegistry.tsx`.
        """
        return True, "non vérifié"

    def discover_models(self, transport: Any) -> tuple[ModelSpec, ...]:
        """Découverte dynamique (ex. GET /v1/models). Défaut : la liste déclarée,
        ce qui couvre déjà la majorité des providers actuels."""
        return self.descriptor.models

    def credential_requise(self) -> str | None:
        """Nom de la variable d'environnement attendue, ou None si aucune."""
        return self.descriptor.auth.env_var

    def __repr__(self) -> str:  # pragma: no cover - confort de debug
        return f"<{type(self).__name__} id={self.descriptor.id!r}>"
