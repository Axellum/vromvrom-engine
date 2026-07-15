# -*- coding: utf-8 -*-
"""
Garde-fou de câblage : les frontières Home Assistant doivent appeler
`validate_service_data` (protection anti-SSTI) sur le service_data entrant.

Le filtre existe (core/validation.py) mais il ne protège que s'il est
effectivement invoqué avant de transmettre les données à HA. Ce test échoue si
un refactor retire l'appel d'une des frontières, rouvrant le vecteur d'injection
de templates Jinja2 dans Home Assistant.
"""

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

# Fichiers exposant un appel de service HA à partir de service_data externe.
# [T124] execute_ha_action a été déplacé de mcp_server.py vers
# core/mcp_tools/homeassistant.py (segmentation par domaine).
_HA_BOUNDARIES = [
    "api/routes/ha.py",
    "core/mcp_tools/homeassistant.py",
]


def _calls_validate(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # appel direct `validate_service_data(...)`
            if isinstance(func, ast.Name) and func.id == "validate_service_data":
                return True
            # appel qualifié `module.validate_service_data(...)`
            if isinstance(func, ast.Attribute) and func.attr == "validate_service_data":
                return True
    return False


@pytest.mark.parametrize("rel", _HA_BOUNDARIES)
def test_ha_boundary_validates_service_data(rel):
    path = _ROOT / rel
    if not path.exists():
        pytest.skip(f"absent : {rel}")
    tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    assert _calls_validate(tree), (
        f"{rel} : aucun appel à validate_service_data — la protection anti-SSTI "
        f"des routes Home Assistant n'est plus câblée."
    )
