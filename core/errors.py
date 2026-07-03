"""
core/errors.py — Taxonomie d'erreurs typée pour le tab5-engine.

Fournit une classification structurée des erreurs permettant au Self-Healing
de distinguer les erreurs retriables (réseau, timeout) des erreurs fatales
(logique, authentification). Chaque erreur porte une catégorie, un message,
et un flag de retriabilité.

Créé dans le cadre de l'audit V5.5 (Axe H1).
"""

from enum import Enum
from typing import Optional


class ErrorCategory(str, Enum):
    """
    Classification des erreurs du moteur.
    Permet au HealingManager et à l'Executor de prendre des décisions
    de retry/fallback adaptées au type d'erreur.
    """
    # --- Erreurs retriables (le système peut réessayer automatiquement) ---
    NETWORK = "network"          # Erreur réseau, timeout HTTP, DNS
    TIMEOUT = "timeout"          # Timeout d'outil ou de provider LLM
    RATE_LIMIT = "rate_limit"    # Limite de requêtes atteinte (429)
    PROVIDER_DOWN = "provider_down"  # Provider LLM indisponible temporairement
    TRANSIENT = "transient"      # Erreur transitoire non classifiée

    # --- Erreurs non-retriables (nécessitent une correction logique) ---
    LOGIC = "logic"              # Erreur de logique métier, mauvais résultat
    AUTH = "auth"                # Erreur d'authentification, clé API invalide
    VALIDATION = "validation"    # Erreur de validation de schéma JSON, YAML invalide
    PERMISSION = "permission"    # Permission insuffisante (fichier, API)
    NOT_FOUND = "not_found"      # Ressource introuvable (fichier, entité HA)
    BUDGET = "budget"            # Budget de tokens ou de coût dépassé

    # --- Erreurs système (critiques, arrêt recommandé) ---
    SYSTEM = "system"            # Erreur système (crash, mémoire, disque)
    UNKNOWN = "unknown"          # Erreur non classifiable


# Ensemble des catégories d'erreurs retriables
RETRIABLE_CATEGORIES = {
    ErrorCategory.NETWORK,
    ErrorCategory.TIMEOUT,
    ErrorCategory.RATE_LIMIT,
    ErrorCategory.PROVIDER_DOWN,
    ErrorCategory.TRANSIENT,
}


class AgentError:
    """
    Erreur structurée produite par un agent ou un outil.
    Remplace les chaînes de texte brutes pour permettre un traitement
    programmatique par le HealingManager et l'Executor.
    """
    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        source: str = "",
        original_exception: Optional[Exception] = None,
    ):
        self.category = category
        self.message = message
        self.source = source  # Ex: "tool:read_file", "provider:deepseek", "agent:executor"
        self.original_exception = original_exception

    @property
    def is_retriable(self) -> bool:
        """Indique si l'erreur peut être corrigée par un simple retry."""
        return self.category in RETRIABLE_CATEGORIES

    def __str__(self) -> str:
        return f"[{self.category.value.upper()}] {self.message}"

    def __repr__(self) -> str:
        return f"AgentError(category={self.category.value}, source={self.source}, message={self.message[:80]})"


def classify_error(error_message: str, source: str = "") -> AgentError:
    """
    Classifie automatiquement une erreur texte brute en AgentError typé.

    Analyse heuristique basée sur les patterns d'erreurs les plus courants
    rencontrés dans le moteur (timeout, 429, connexion refusée, etc.).

    Args:
        error_message: Le message d'erreur brut (string)
        source: La source de l'erreur (ex: "tool:run_terminal_command")

    Returns:
        AgentError avec la catégorie la plus probable.
    """
    msg_lower = error_message.lower()

    # --- Patterns de timeout ---
    if any(kw in msg_lower for kw in ["timeout", "timed out", "asyncio.timeouterror", "deadline exceeded"]):
        return AgentError(ErrorCategory.TIMEOUT, error_message, source)

    # --- Patterns de rate limiting ---
    if any(kw in msg_lower for kw in ["rate limit", "429", "too many requests", "quota exceeded", "resource_exhausted"]):
        return AgentError(ErrorCategory.RATE_LIMIT, error_message, source)

    # --- Patterns réseau ---
    if any(kw in msg_lower for kw in [
        "connection refused", "connectionerror", "dns", "unreachable",
        "network", "ssl", "certificate", "econnreset", "socket",
        "502", "503", "504", "bad gateway", "service unavailable"
    ]):
        return AgentError(ErrorCategory.NETWORK, error_message, source)

    # --- Patterns d'authentification ---
    if any(kw in msg_lower for kw in ["401", "403", "unauthorized", "forbidden", "invalid api key", "authentication"]):
        return AgentError(ErrorCategory.AUTH, error_message, source)

    # --- Patterns de validation ---
    if any(kw in msg_lower for kw in [
        "validation", "invalid json", "schema", "parse error",
        "jsondecodeerror", "yaml", "configuration is invalid",
        "pydantic", "field required"
    ]):
        return AgentError(ErrorCategory.VALIDATION, error_message, source)

    # --- Patterns de permission ---
    if any(kw in msg_lower for kw in ["permission denied", "access denied", "permissionerror", "readonly", "read-only"]):
        return AgentError(ErrorCategory.PERMISSION, error_message, source)

    # --- Patterns de ressource introuvable ---
    if any(kw in msg_lower for kw in [
        "not found", "404", "filenotfounderror", "no such file",
        "does not exist", "introuvable", "enoent"
    ]):
        return AgentError(ErrorCategory.NOT_FOUND, error_message, source)

    # --- Patterns de budget ---
    if any(kw in msg_lower for kw in ["budget", "token limit", "cost exceeded", "insufficient balance", "solde"]):
        return AgentError(ErrorCategory.BUDGET, error_message, source)

    # --- Patterns de provider down ---
    if any(kw in msg_lower for kw in ["provider", "model not found", "overloaded", "capacity", "internal server error", "500"]):
        return AgentError(ErrorCategory.PROVIDER_DOWN, error_message, source)

    # --- Patterns système ---
    if any(kw in msg_lower for kw in ["memory", "oom", "disk full", "segfault", "oserror", "systemexit"]):
        return AgentError(ErrorCategory.SYSTEM, error_message, source)

    # --- Défaut : erreur de logique non classifiée ---
    # Les erreurs commençant par "Erreur" sont souvent des retours d'outils
    if msg_lower.startswith("erreur"):
        return AgentError(ErrorCategory.LOGIC, error_message, source)

    return AgentError(ErrorCategory.UNKNOWN, error_message, source)

