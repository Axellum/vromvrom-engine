"""Régression #T135 : path traversal via symlink dans validate_config_format
(core/mcp_tools/homeassistant.py, ex-mcp_server.py avant la segmentation #T124).

abspath() ne résout pas les symlinks : un lien symbolique créé DANS le workspace
mais pointant vers une cible EXTÉRIEURE passait la vérification startswith() tout
en donnant accès au fichier externe une fois ouvert par le linter. realpath()
corrige ça en résolvant la cible réelle avant la comparaison.

NB : shim `tools` identique aux autres tests mcp_server (cf. test_delegate_to_gateway.py)
pour forcer le package local face au paquet site-packages homonyme sous Windows.
"""
import os
import sys
import types
import asyncio

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if not (getattr(sys.modules.get("tools"), "__file__", "") or "").startswith(_REPO):
    _m = types.ModuleType("tools")
    _m.__path__ = [os.path.join(_REPO, "tools")]
    sys.modules["tools"] = _m

import core.mcp_tools.homeassistant as mcp_homeassistant  # noqa: E402


def test_symlink_escaping_workspace_is_rejected(tmp_path):
    outside_secret = tmp_path / "secret.yaml"
    outside_secret.write_text("api_key: super-secret\n")

    # [T124] validate_config_format calcule désormais son workspace_root comme la
    # racine du repo (3 dirname() depuis core/mcp_tools/homeassistant.py), pas le
    # dossier du fichier — reproduire le même calcul ici pour créer le symlink au
    # bon endroit.
    workspace_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(mcp_homeassistant.__file__))))
    link_path = os.path.join(workspace_root, "_test_t135_evil_link.yaml")
    if os.path.islink(link_path) or os.path.exists(link_path):
        os.remove(link_path)
    try:
        os.symlink(str(outside_secret), link_path)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks non supportés dans cet environnement (droits Windows insuffisants)")

    try:
        out = asyncio.run(mcp_homeassistant.validate_config_format("_test_t135_evil_link.yaml"))
        assert "Path Traversal" in out
        assert "secret" not in out.lower()
    finally:
        os.remove(link_path)


def test_normal_file_inside_workspace_is_allowed():
    out = asyncio.run(mcp_homeassistant.validate_config_format("config.json"))
    assert "Path Traversal" not in out
