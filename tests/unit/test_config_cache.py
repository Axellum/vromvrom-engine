"""
tests/unit/test_config_cache.py — Tests du cache de configuration (Phase 1, D1).

Vérifie que load_config() :
- met en cache le résultat et évite la relecture disque quand le mtime est stable ;
- retourne des copies indépendantes (mutation sans effet de bord sur le cache) ;
- recharge bien quand le fichier change (ou via force_reload).
"""

import json
import os
from unittest import mock

import core.llm_gateway as gw


def test_returns_independent_copies():
    """Muter le dict retourné ne doit pas polluer les appels suivants."""
    cfg1 = gw.load_config()
    cfg1["__injected__"] = "pollution"
    cfg2 = gw.load_config()
    assert "__injected__" not in cfg2


def test_cache_avoids_disk_reads(tmp_path, monkeypatch):
    """Sur mtime stable, le second appel ne relit pas le fichier."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"max_session_tokens": 123456}), encoding="utf-8")

    # Rediriger load_config vers notre fichier temporaire.
    monkeypatch.setattr(
        gw.os.path, "join",
        lambda *a: str(config_file) if a and a[-1] == "config.json" else os.path.join(*a),
    )
    # Réinitialiser le cache module.
    gw._CONFIG_CACHE["mtime"] = None
    gw._CONFIG_CACHE["data"] = None

    first = gw.load_config()
    assert first.get("max_session_tokens") == 123456

    real_open = open
    with mock.patch("builtins.open", side_effect=real_open) as m_open:
        second = gw.load_config()
        # Cache hit : aucune ouverture du fichier de config.
        assert second.get("max_session_tokens") == 123456
        opened_paths = [str(c.args[0]) for c in m_open.call_args_list]
        assert str(config_file) not in opened_paths


def test_force_reload_bypasses_cache(tmp_path, monkeypatch):
    """force_reload=True relit toujours le fichier."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"max_session_tokens": 1}), encoding="utf-8")
    monkeypatch.setattr(
        gw.os.path, "join",
        lambda *a: str(config_file) if a and a[-1] == "config.json" else os.path.join(*a),
    )
    gw._CONFIG_CACHE["mtime"] = None
    gw._CONFIG_CACHE["data"] = None

    assert gw.load_config().get("max_session_tokens") == 1
    config_file.write_text(json.dumps({"max_session_tokens": 999}), encoding="utf-8")
    assert gw.load_config(force_reload=True).get("max_session_tokens") == 999
