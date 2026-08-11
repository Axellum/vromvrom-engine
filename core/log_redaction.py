"""
core/log_redaction.py — Masquage des secrets à la sortie des journaux (#T262).

Le 10/08, une clé Gemini a été retrouvée **en clair dans le journal systemd de
prod** (4 lignes, 2 clés distinctes sur 7 jours). Le chemin de la fuite explique
pourquoi un correctif local n'aurait pas suffi :

    core/gemini_native.py construit `...:generateContent?key=<CLÉ>`
        → requests.raise_for_status() lève une HTTPError dont le MESSAGE
          contient l'URL complète
            → cette exception est journalisée par le circuit breaker
              (« Échec détecté sur … : 400 Client Error … for url: …?key=… »)
                → puis par la cascade (« Échec du modèle … »)

Le secret voyage donc dans du texte d'exception, à travers des modules qui ne
savent rien de Gemini. Le seul endroit qui les voit tous, c'est le point de
sortie : les handlers de journalisation. D'où ce filtre.

Il couvre aussi les **traces d'exception** (`exc_info`) : sans cela, un simple
`logger.error(..., exc_info=True)` ré-ouvrirait la fuite par la traceback.

Installation (une fois par processus, après la configuration du logging) :

    from core.log_redaction import installer_redaction
    installer_redaction()

⚠️ Ce filtre est une **dernière ligne de défense**, pas une permission d'écrire
des secrets dans les logs : il ne connaît que les formes listées ci-dessous.
"""

import logging
import re

# Formes de secrets connues. Chaque motif garde le préfixe (pour que le log
# reste lisible : on veut savoir QUEL paramètre a été masqué) et remplace la valeur.
MOTIFS: tuple[tuple[re.Pattern, str], ...] = (
    # Secret passé en paramètre d'URL — le cas de la fuite du 10/08.
    (re.compile(r"([?&](?:key|api[_-]?key|access_token|auth|token|password)=)[^&\s\"'<>]+", re.I), r"\1***"),
    # Clés Google, y compris hors URL (message d'erreur, dump de config…).
    (re.compile(r"AIza[0-9A-Za-z_\-]{10,}"), "AIza***"),
    # En-tête Authorization.
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{12,}", re.I), r"\1***"),
    # Clés à préfixe conventionnel (OpenAI, Anthropic, xAI, HuggingFace…).
    # Préfixes les plus longs EN PREMIER : l'alternance regex retient la première
    # branche qui matche, donc `sk-` avant `sk-proj-` masquerait la clé mais
    # afficherait « sk-*** », en perdant l'indice du provider concerné.
    (re.compile(r"\b(sk-proj-|sk-ant-|sk-|gsk_|xai-|hf_)[A-Za-z0-9_\-]{12,}"), r"\1***"),
    # Affectation explicite dans du texte (`API_KEY=…`, `"password": "…"`).
    (re.compile(r"((?:api[_-]?key|secret|password|passwd|token)[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9._\-]{12,}", re.I), r"\1***"),
)


def masquer_secrets(texte: str) -> str:
    """Remplace toute forme de secret connue par `***`, en gardant le contexte."""
    if not texte:
        return texte
    for motif, remplacement in MOTIFS:
        texte = motif.sub(remplacement, texte)
    return texte


def _masquer_arg(valeur):
    """
    Masque un argument de log avant formatage.

    Un filtre de handler tourne AVANT `getMessage()` : si l'appelant fait
    `logger.warning("… %s", exc)`, `exc` n'est pas encore une str — sans
    conversion, `str(exc)` (qui porte l'URL `?key=…`) s'écrit après le filtre
    et la fuite passe. Les scalaires (`int`, `float`…) restent tels quels pour
    ne pas casser `%d` / `%f`.
    """
    if isinstance(valeur, str):
        return masquer_secrets(valeur)
    if isinstance(valeur, BaseException):
        return masquer_secrets(str(valeur))
    if isinstance(valeur, (bytes, bytearray)):
        try:
            return masquer_secrets(valeur.decode("utf-8", errors="replace"))
        except Exception:
            return valeur
    if isinstance(valeur, (int, float, bool, type(None))):
        return valeur
    try:
        texte = str(valeur)
    except Exception:
        return valeur
    masque = masquer_secrets(texte)
    return masque if masque != texte else valeur


class FiltreRedaction(logging.Filter):
    """
    Masque les secrets dans le message, ses arguments et sa trace d'exception.

    Un filtre s'exécute AVANT le formatage : on pré-rend donc la traceback dans
    `record.exc_text` (que le formateur réutilise tel quel) pour pouvoir la
    masquer aussi — sinon `exc_info=True` recréerait la fuite.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = masquer_secrets(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {cle: _masquer_arg(v) for cle, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_masquer_arg(a) for a in record.args)

        if record.exc_info and not record.exc_text:
            try:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            except Exception:
                record.exc_text = None
        if record.exc_text:
            record.exc_text = masquer_secrets(record.exc_text)

        return True


def installer_redaction(logger: logging.Logger | None = None) -> int:
    """
    Attache le filtre à tous les handlers du logger racine (ou de celui fourni).

    Sur les **handlers** et non sur le logger : un filtre posé sur un logger ne
    voit pas les enregistrements de ses enfants qui remontent par propagation —
    or toute la fuite venait de loggers enfants (`core.llm.circuit_breaker`,
    `core.llm.providers.deepseek`). Idempotent : ré-appeler n'empile pas de
    filtres. Retourne le nombre de handlers protégés.
    """
    cible = logger or logging.getLogger()
    protégés = 0
    for handler in cible.handlers:
        if any(isinstance(f, FiltreRedaction) for f in handler.filters):
            continue
        handler.addFilter(FiltreRedaction())
        protégés += 1
    return protégés
