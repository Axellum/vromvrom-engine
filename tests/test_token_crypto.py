"""Tests #T126 : chiffrement au repos de google_token.json (core/token_crypto.py)."""
import json

import pytest
from cryptography.fernet import Fernet

from core import token_crypto


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    monkeypatch.delenv("MCP_TOKEN_KEY", raising=False)


def test_save_then_load_roundtrip_encrypted(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("MCP_TOKEN_KEY", key)

    path = str(tmp_path / "google_token.json")
    data = {"refresh_token": "rt-secret", "client_id": "abc"}

    token_crypto.save_token_json(path, data)

    # Le fichier sur disque ne doit pas contenir le secret en clair.
    with open(path, "rb") as f:
        raw = f.read()
    assert b"rt-secret" not in raw

    loaded = token_crypto.load_token_json(path)
    assert loaded == data


def test_save_without_key_writes_plaintext(tmp_path):
    path = str(tmp_path / "google_token.json")
    data = {"refresh_token": "rt-secret"}

    token_crypto.save_token_json(path, data)

    with open(path, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk == data


def test_load_legacy_plaintext_without_key(tmp_path):
    path = str(tmp_path / "google_token.json")
    data = {"refresh_token": "legacy-rt"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    assert token_crypto.load_token_json(path) == data


def test_load_legacy_plaintext_even_with_key_set(tmp_path, monkeypatch):
    """Un fichier legacy en clair reste lisible même après ajout de MCP_TOKEN_KEY
    (tant que tools/encrypt_google_token.py n'a pas migré le fichier)."""
    monkeypatch.setenv("MCP_TOKEN_KEY", Fernet.generate_key().decode())
    path = str(tmp_path / "google_token.json")
    data = {"refresh_token": "legacy-rt"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    assert token_crypto.load_token_json(path) == data


def test_load_encrypted_without_key_raises(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("MCP_TOKEN_KEY", key)
    path = str(tmp_path / "google_token.json")
    token_crypto.save_token_json(path, {"refresh_token": "rt"})

    monkeypatch.delenv("MCP_TOKEN_KEY", raising=False)
    with pytest.raises(ValueError):
        token_crypto.load_token_json(path)
