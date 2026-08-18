"""
tests/unit/test_ajean_declaration.py — Déclaration des providers AJEAN (llama.cpp).

AJEAN = llama.cpp (llama-server, build b10451) sur le PC local, API OpenAI-compatible
sur le port 8080. Deux entrées symétriques : `ajean_pc` (192.168.1.10, joignable depuis
le Deck) et `ajean_deck` (127.0.0.1, instance Deck à venir).

Ce que cette PR verrouille (purement déclaratif, AUCUN appel réseau) :
1. Les deux entrées existent dans `OPENAI_COMPAT_PROVIDERS`, avec une `base_url`
   complète se terminant par `/v1/chat/completions`.
2. **Point central** : les deux hôtes sont reconnus LOCAUX par `LLMGateway._est_hote_local`
   — c'est la preuve qu'on a déclaré des IP privées/loopback, jamais un nom DNS (un nom
   d'hôte serait traité comme externe/cloud et perdrait le bénéfice du mode
   `privacy_level=local_only`, #T337).
3. Le gateway sait construire les deux providers sans lever, et leur timeout de
   connexion est court (≤ 3 s) : un hôte local éteint doit échouer vite.
4. Non-régression : `ollama_local` et `ollama_pc` restent déclarés et constructibles —
   cette PR ne débranche rien.
"""
import os
import sys

# Ajout du répertoire parent au PYTHONPATH (patron des autres tests unitaires).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.llm_gateway import LLMGateway  # noqa: E402
from core.openai_compat_provider import (  # noqa: E402
    AJEAN_DEFAULT_MODEL,
    OPENAI_COMPAT_PROVIDERS,
)

# ── 1. Déclaration dans le registre centralisé ───────────────────────────────

def test_ajean_pc_declare_dans_le_registre():
    """ajean_pc existe et a une base_url complète jusqu'à /v1/chat/completions."""
    assert "ajean_pc" in OPENAI_COMPAT_PROVIDERS
    config = OPENAI_COMPAT_PROVIDERS["ajean_pc"]
    assert config["base_url"].endswith("/v1/chat/completions")
    # Hôte déclaré par IP LAN, jamais par nom DNS.
    assert "192.168.1.10" in config["base_url"]
    assert config["default_model"] == AJEAN_DEFAULT_MODEL


def test_ajean_deck_declare_dans_le_registre():
    """ajean_deck existe et a une base_url complète jusqu'à /v1/chat/completions."""
    assert "ajean_deck" in OPENAI_COMPAT_PROVIDERS
    config = OPENAI_COMPAT_PROVIDERS["ajean_deck"]
    assert config["base_url"].endswith("/v1/chat/completions")
    # Hôte déclaré en loopback, jamais par nom DNS.
    assert "127.0.0.1" in config["base_url"]
    assert config["default_model"] == AJEAN_DEFAULT_MODEL


def test_modele_ajean_en_constante_unique():
    """Le modèle AJEAN est écrit en constante, pas dispersé en dur dans le registre."""
    assert AJEAN_DEFAULT_MODEL == "Qwen2.5-14B-Instruct-1M-Q4_K_M"
    for pid in ("ajean_pc", "ajean_deck"):
        assert OPENAI_COMPAT_PROVIDERS[pid]["default_model"] == AJEAN_DEFAULT_MODEL


# ── 2. Point central : hôtes reconnus LOCAUX ─────────────────────────────────

def test_ajes_hotes_reconnus_locaux_par_est_hote_local():
    """Les deux hôtes AJEAN sont LOCAUX pour `_est_hote_local` (IP privée/loopback).

    C'est la preuve qu'on a déclaré des IP, pas un nom DNS : `_est_hote_local` classe
    toute IP privée RFC 1918 ou loopback comme locale (#T337), alors qu'un nom d'hôte
    (ex. `ajean.local`) serait traité comme externe/cloud et échapperait à la sonde
    d'accessibilité.
    """
    assert LLMGateway._est_hote_local("192.168.1.10") is True
    assert LLMGateway._est_hote_local("127.0.0.1") is True


def test_ajean_providers_classes_locaux_via_base_url():
    """Les providers AJEAN du gateway appartiennent bien à la famille locale."""
    gateway = LLMGateway()
    assert gateway._is_local_provider("ajean_pc") is True
    assert gateway._is_local_provider("ajean_deck") is True


# ── 3. Constructibilité + timeout court ──────────────────────────────────────

def test_gateway_construit_les_providers_ajean():
    """Le gateway instancie ajean_pc et ajean_deck sans lever."""
    gateway = LLMGateway()
    for pid in ("ajean_pc", "ajean_deck"):
        assert pid in gateway.providers, f"provider '{pid}' absent du gateway"
        provider = gateway.providers[pid]
        assert provider.base_url.endswith("/v1/chat/completions")
        assert provider.model == AJEAN_DEFAULT_MODEL


def test_timeout_de_connexion_court_pour_ajean():
    """Timeout de connexion ≤ 3 s : un hôte local éteint doit échouer vite.

    Retrouvé via le tuple `(connect, read)` porté par `provider.timeout`.
    """
    gateway = LLMGateway()
    for pid in ("ajean_pc", "ajean_deck"):
        timeout = gateway.providers[pid].timeout
        assert isinstance(timeout, (tuple, list)), f"{pid} : timeout non-tuple"
        connect, _read = timeout[0], timeout[1]
        assert connect <= 3, f"{pid} : connect timeout {connect}s > 3s (hôte éteint trop lent)"


# ── 4. Non-régression : Ollama intact ────────────────────────────────────────

def test_ollama_toujours_declare_dans_le_registre():
    """ollama_local et ollama_pc restent dans le registre (#T354, ne rien débrancher)."""
    assert "ollama_local" in OPENAI_COMPAT_PROVIDERS
    assert "ollama_pc" in OPENAI_COMPAT_PROVIDERS


def test_ollama_toujours_constructibles():
    """ollama_local et ollama_pc restent constructibles par le gateway."""
    gateway = LLMGateway()
    assert "ollama_local" in gateway.providers
    assert "ollama_pc" in gateway.providers
    for pid in ("ollama_local", "ollama_pc"):
        assert gateway.providers[pid].base_url.endswith("/v1/chat/completions")
