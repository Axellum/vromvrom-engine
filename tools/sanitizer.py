"""
tools/sanitizer.py — Assainissement des sorties d'outils avant injection dans le contexte LLM.

Masque automatiquement les données sensibles (clés API, tokens, emails, IPs privées)
dans les résultats bruts des outils avant qu'ils ne soient injectés dans la fenêtre
de contexte du LLM. Prévient les fuites accidentelles de secrets via le modèle.

Créé dans le cadre de l'audit V5.5 (Axe S2).
"""

import re
import logging

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Patterns de données sensibles (compilés une seule fois au chargement)
# ──────────────────────────────────────────────────────────────────

_PATTERNS = [
    # Clés API génériques (sk-xxx, key-xxx, api_xxx, token_xxx)
    (
        re.compile(
            r'(?:sk|api[_-]?key|token|secret|password|bearer|auth)[_\-]?'
            r'[=:\s]*["\']?([a-zA-Z0-9_\-]{20,})["\']?',
            re.IGNORECASE,
        ),
        "***CLÉ_MASQUÉE***",
        "clé API / token / secret",
    ),
    # Tokens JWT (3 segments base64 séparés par des points)
    (
        re.compile(r'eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}'),
        "***JWT_MASQUÉ***",
        "token JWT",
    ),
    # Adresses email
    (
        re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'),
        "***EMAIL_MASQUÉ***",
        "adresse email",
    ),
    # Adresses IP privées (192.168.x.x, 10.x.x.x, 172.16-31.x.x)
    (
        re.compile(
            r'\b(?:192\.168\.\d{1,3}\.\d{1,3}|'
            r'10\.\d{1,3}\.\d{1,3}\.\d{1,3}|'
            r'172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b'
        ),
        "***IP_MASQUÉE***",
        "adresse IP privée",
    ),
    # Mots de passe dans les URLs (proto://user:pass@host)
    (
        re.compile(r'(://[^:]+:)[^@]+(@)'),
        r'\1***MOT_DE_PASSE***\2',
        "mot de passe dans URL",
    ),
    # Variables d'environnement sensibles exposées (ENV_VAR=value)
    (
        re.compile(
            r'(?:DEEPSEEK_API_KEY|GEMINI_API_KEY|NABU_CASA_TOKEN|OPENAI_API_KEY|'
            r'ANTHROPIC_API_KEY|LANGFUSE_SECRET_KEY|DATABASE_PASSWORD|XAI_API_KEY)'
            r'\s*[=:]\s*["\']?([^\s"\']+)["\']?',
            re.IGNORECASE,
        ),
        "***ENV_MASQUÉ***",
        "variable d'environnement sensible",
    ),
]

# Nombre de caractères de préfixe à conserver pour le debug
_PREFIX_VISIBLE_CHARS = 4


class OutputSanitizer:
    """
    Assainisseur de sorties d'outils.

    Utilisé par l'ExecutorAgent après chaque appel d'outil pour masquer
    les données sensibles avant injection dans le contexte du LLM.

    Usage:
        sanitizer = OutputSanitizer()
        clean_output = sanitizer.sanitize(raw_output)
    """

    def __init__(self, enabled: bool = True):
        """
        Args:
            enabled: Si False, le sanitizer est désactivé (pass-through).
                     Utile pour le debug en environnement contrôlé.
        """
        self._enabled = enabled
        self._stats = {
            "total_sanitized": 0,
            "patterns_matched": {},
        }

    def sanitize(self, text: str, source: str = "") -> str:
        """
        Masque les données sensibles dans le texte fourni.

        Args:
            text: Le texte brut à assainir (résultat d'un outil)
            source: Le nom de l'outil source (pour le logging)

        Returns:
            Le texte avec les données sensibles remplacées par des placeholders.
        """
        if not self._enabled or not text:
            return text

        sanitized = text
        matches_found = 0

        for pattern, replacement, description in _PATTERNS:
            # Compter les occurrences avant remplacement
            found = pattern.findall(sanitized)
            if found:
                matches_found += len(found)
                sanitized = pattern.sub(replacement, sanitized)

                # Statistiques de suivi
                self._stats["patterns_matched"][description] = (
                    self._stats["patterns_matched"].get(description, 0) + len(found)
                )

        if matches_found > 0:
            self._stats["total_sanitized"] += matches_found
            logger.info(
                f"[SANITIZER] {matches_found} donnée(s) sensible(s) masquée(s) "
                f"dans la sortie de '{source or 'inconnu'}'."
            )

        return sanitized

    def get_stats(self) -> dict:
        """Retourne les statistiques de masquage pour l'observabilité."""
        return dict(self._stats)

    def reset_stats(self) -> None:
        """Réinitialise les compteurs de statistiques."""
        self._stats = {"total_sanitized": 0, "patterns_matched": {}}
