"""
core/token_crypto.py — Chiffrement au repos des tokens OAuth Google (#T126).

`google_token.json` contient un refresh_token permanent en clair : accès
illimité à tout Google Workspace (Calendar/Gmail/Drive/...) si le fichier
est exfiltré. Chiffré via Fernet (clé symétrique `MCP_TOKEN_KEY` dans `.env`).

Rétro-compatible : si `MCP_TOKEN_KEY` est absente ou si le fichier est encore
en clair (JSON), la lecture retombe sur le JSON brut — pas de casse en prod
tant que la migration (`tools/encrypt_google_token.py`) n'a pas tourné.
"""
import os
import json
import logging

logger = logging.getLogger("core.token_crypto")


def _get_fernet():
    key = os.environ.get("MCP_TOKEN_KEY", "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode("utf-8"))
    except Exception as e:
        logger.warning(f"[TokenCrypto] MCP_TOKEN_KEY invalide, chiffrement désactivé : {e}")
        return None


def load_token_json(path: str) -> dict:
    """Lit un fichier de token OAuth, en le déchiffrant s'il est chiffré (Fernet)."""
    with open(path, "rb") as f:
        raw = f.read()

    # Legacy / MCP_TOKEN_KEY absente : tente d'abord un parsing JSON direct.
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass

    fernet = _get_fernet()
    if fernet is None:
        raise ValueError(
            f"'{path}' est illisible en JSON clair et MCP_TOKEN_KEY est absente : "
            "impossible de le déchiffrer."
        )
    decrypted = fernet.decrypt(raw)
    return json.loads(decrypted.decode("utf-8"))


def save_token_json(path: str, data: dict) -> None:
    """Écrit un fichier de token OAuth, chiffré si MCP_TOKEN_KEY est configurée.

    Permissions restreintes (0600) créées AVANT écriture, comme pour l'ancien
    code en clair — le fichier reste un secret longue durée même chiffré.
    """
    payload = json.dumps(data, indent=2).encode("utf-8")
    fernet = _get_fernet()
    if fernet is not None:
        payload = fernet.encrypt(payload)
    else:
        logger.warning(
            "[TokenCrypto] MCP_TOKEN_KEY absente — écriture de google_token.json en CLAIR."
        )

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(payload)
    try:
        os.chmod(path, 0o600)  # idempotent si le fichier préexistait
    except OSError:
        pass
