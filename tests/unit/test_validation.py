# -*- coding: utf-8 -*-
"""Tests des validateurs d'entrées externes (P0-1.6, core/validation.py)."""

import pytest

from core.validation import (
    is_valid_ha_entity_id,
    is_valid_ha_domain,
    is_valid_ha_service_name,
    is_valid_gsheet_id,
    is_valid_tool_name,
    is_valid_class_name,
    validate_service_data,
)


def test_validate_service_data_valid():
    """Vérifie que des dictionnaires service_data valides passent sans exception."""
    validate_service_data({})
    validate_service_data({"brightness": 120})
    validate_service_data({"rgb_color": [255, 0, 128]})
    validate_service_data({"nested": {"mode": "auto", "temp": 21.5}})
    validate_service_data({"list_of_dicts": [{"key": "val1"}, {"key2": "val2"}]})


def test_validate_service_data_invalid_keys():
    """Vérifie que les clés avec des caractères non autorisés lèvent ValueError."""
    with pytest.raises(ValueError, match="Nom de clé invalide"):
        validate_service_data({"bad-key": 100})
    with pytest.raises(ValueError, match="Nom de clé invalide"):
        validate_service_data({"key;injection": "val"})


def test_validate_service_data_jinja_ssti():
    """Vérifie que la présence de tags Jinja {{ ou {% lève ValueError (SSTI)."""
    with pytest.raises(ValueError, match="injection de template Jinja2"):
        validate_service_data({"brightness": "{{ 7 + 7 }}"})
    with pytest.raises(ValueError, match="injection de template Jinja2"):
        validate_service_data({"brightness": "{% if true %}100{% endif %}"})
    with pytest.raises(ValueError, match="injection de template Jinja2"):
        validate_service_data({"nested": {"command": "echo {{'hello'}}"}})
    with pytest.raises(ValueError, match="injection de template Jinja2"):
        validate_service_data({"list": ["safe", "also_safe", "unsafe_{{1}}"]})
    with pytest.raises(ValueError, match="injection de template Jinja2"):
        validate_service_data({"list_of_dicts": [{"safe": "ok"}, {"unsafe": "{{evil}}"}]})


def test_validate_service_data_key_trailing_newline():
    """Régression : une clé avec un saut de ligne final doit être rejetée.

    Avant correctif, le `key_regex` utilisait `$` (qui matche avant un \\n
    terminal) au lieu de `\\Z`, laissant passer une clé du type "evil_key\\n".
    """
    with pytest.raises(ValueError, match="Nom de clé invalide"):
        validate_service_data({"evil_key\n": 1})
    with pytest.raises(ValueError, match="Nom de clé invalide"):
        validate_service_data({"x\n": "ok"})


def test_validate_service_data_jinja_ssti_nested_lists():
    """Régression : un payload Jinja caché dans une liste imbriquée ne doit PAS passer.

    Avant correctif, la récursion ne descendait pas dans les listes de listes
    (`[["{{evil}}"]]`), permettant un contournement du filtre SSTI.
    """
    with pytest.raises(ValueError, match="injection de template Jinja2"):
        validate_service_data({"k": [["{{ 7 * 7 }}"]]})
    with pytest.raises(ValueError, match="injection de template Jinja2"):
        validate_service_data({"k": [[{"x": "{% if true %}1{% endif %}"}]]})
    with pytest.raises(ValueError, match="injection de template Jinja2"):
        validate_service_data({"k": [[["deep_{{evil}}"]]]})
    # Les listes imbriquées sans payload restent valides.
    validate_service_data({"matrix": [[1, 2], [3, 4]], "tags": [["a", "b"], ["c"]]})


@pytest.mark.parametrize("value", [
    "light.salon", "light.salon_principal", "binary_sensor.porte_1",
    "climate.chambre", "switch.prise_bureau",
])
def test_valid_entity_ids(value):
    assert is_valid_ha_entity_id(value)


@pytest.mark.parametrize("value", [
    "", "light", "light.", ".salon", "light.salon/../x",
    "light.salon;rm -rf", "Light.Salon", "light salon",
    "../../secret", "light.salon\n",
])
def test_invalid_entity_ids(value):
    assert not is_valid_ha_entity_id(value)


@pytest.mark.parametrize("value,ok", [
    ("light", True), ("binary_sensor", True), ("climate", True),
    ("", False), ("light/x", False), ("Light", False), ("..", False),
])
def test_ha_domain(value, ok):
    assert is_valid_ha_domain(value) is ok


@pytest.mark.parametrize("value,ok", [
    ("turn_on", True), ("set_temperature", True), ("toggle", True),
    ("", False), ("turn_on/x", False), ("turn on", False), ("../x", False),
])
def test_ha_service_name(value, ok):
    assert is_valid_ha_service_name(value) is ok


@pytest.mark.parametrize("value,ok", [
    ("1AbC_dEf-123", True), ("abcDEF123", True),
    ("", False), ("id/with/slash", False), ("id..x", False),
    ("id with space", False), ("id?query=1", False),
])
def test_gsheet_id(value, ok):
    assert is_valid_gsheet_id(value) is ok


@pytest.mark.parametrize("value,ok", [
    ("my_tool", True), ("read_yaml", True), ("t", True), ("tool9", True),
    ("", False),
    ("9tool", False),            # ne démarre pas par une lettre
    ("My_Tool", False),          # majuscules interdites (snake_case)
    ("../escape", False),        # traversée de chemin
    ("a/b", False), ("a.b", False), ("a b", False),
    ("tool\n", False),           # newline final
    ("a" * 65, False),           # trop long
])
def test_tool_name(value, ok):
    assert is_valid_tool_name(value) is ok


@pytest.mark.parametrize("value,ok", [
    ("MyTool", True), ("ReadYaml", True), ("A", True), ("Tool9", True),
    ("", False),
    ("myTool", False),                       # ne démarre pas par une majuscule
    ("My_Tool", False),                      # underscore interdit (PascalCase)
    ("Evil(); import os", False),            # injection de code
    ("Tool\n", False),
    ("A" * 65, False),
])
def test_class_name(value, ok):
    assert is_valid_class_name(value) is ok
