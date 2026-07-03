# -*- coding: utf-8 -*-
"""
core/validation.py — Validateurs d'entrées externes (P0-1.6).

Centralise la validation des identifiants injectés dans des URLs d'API (Home
Assistant, Google Sheets). Empêche la traversée de chemin / l'injection de
segments d'URL via des paramètres non maîtrisés (entity_id, domaine, service,
spreadsheet_id) venus du LLM, du MCP ou d'une requête HTTP.
"""

import re

# Home Assistant : domaines/objets en snake_case minuscule.
# \Z (et non $) pour rejeter un éventuel \n final (en regex Python, $ matche
# aussi juste avant un saut de ligne terminal).
_HA_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9_]*\Z")          # ex: light, climate, binary_sensor
_HA_OBJECT_RE = re.compile(r"^[a-z0-9_]+\Z")               # ex: turn_on, set_temperature
_HA_ENTITY_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+\Z")  # ex: light.salon_principal

# Google Sheets : l'ID ne contient que [A-Za-z0-9_-].
_GSHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]+\Z")

# Outils auto-générés (ToolMakerAgent, P0-1.4) : ces identifiants viennent du LLM
# et sont injectés dans des chemins de fichiers (nom d'outil → dossier/fichier) et
# dans du code Python généré (nom de classe interpolé). On exige donc un snake_case
# strict pour l'outil et un PascalCase pour la classe, bornés en longueur et SANS
# séparateur de chemin ni caractère permettant d'injecter du code.
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}\Z")
_CLASS_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]{0,63}\Z")


def is_valid_ha_entity_id(value: str) -> bool:
    """True si `value` est un entity_id HA valide (domaine.objet)."""
    return bool(value) and bool(_HA_ENTITY_RE.match(value))


def is_valid_ha_domain(value: str) -> bool:
    """True si `value` est un domaine HA valide (ex: light, climate)."""
    return bool(value) and bool(_HA_DOMAIN_RE.match(value))


def is_valid_ha_service_name(value: str) -> bool:
    """True si `value` est un nom de service HA valide (ex: turn_on)."""
    return bool(value) and bool(_HA_OBJECT_RE.match(value))


def is_valid_gsheet_id(value: str) -> bool:
    """True si `value` est un ID de spreadsheet Google valide."""
    return bool(value) and bool(_GSHEET_ID_RE.match(value))


def is_valid_tool_name(value: str) -> bool:
    """True si `value` est un nom d'outil auto-généré sûr (snake_case, anti
    traversée de chemin : sert de nom de dossier/fichier)."""
    return bool(value) and bool(_TOOL_NAME_RE.match(value))


def is_valid_class_name(value: str) -> bool:
    """True si `value` est un nom de classe sûr (PascalCase, anti-injection de
    code : interpolé dans le code Python généré et le bloc de test sandbox)."""
    return bool(value) and bool(_CLASS_NAME_RE.match(value))


def validate_service_data(data: dict) -> None:
    """Vérifie récursivement qu'aucune clé ou valeur de service_data ne contient d'injections de templates Jinja2 (SSTI)."""
    if not isinstance(data, dict):
        return
    import re
    # \Z (et non $) : cohérence avec le reste du module — $ matcherait avant un
    # éventuel \n final, laissant passer une clé du type "evil_key\n".
    key_regex = re.compile(r"^[a-zA-Z0-9_]+\Z")
    
    def _scan_value(value, ctx: str) -> None:
        """Inspecte récursivement une valeur (str/dict/list, y compris listes imbriquées)."""
        if isinstance(value, str):
            if "{{" in value or "{%" in value:
                raise ValueError(f"Tentative d'injection de template Jinja2 détectée dans {ctx}")
        elif isinstance(value, dict):
            validate_service_data(value)
        elif isinstance(value, list):
            for item in value:
                _scan_value(item, ctx)

    for k, v in data.items():
        if not key_regex.match(k):
            raise ValueError(f"Nom de clé invalide dans service_data : {k!r}")

        _scan_value(v, f"la valeur de {k!r}")
