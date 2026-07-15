"""
core/mcp_tools/homeassistant.py - Outils MCP domotique (#T124).

3 outils : search_ha_entities, execute_ha_action, validate_config_format.
Extrait de l'ex-mcp_server.py monolithique. Ne touche pas a LLMGateway/
CircuitBreaker (appels HA directs via requests, ou linter YAML local).
"""
import logging
import os

from core.mcp_app import mcp

logger = logging.getLogger("mcp_server.homeassistant")


# ═══════════════════════════════════════════════════════
# Outil 5 — Recherche d'entités Home Assistant
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def search_ha_entities(
    query: str,
    domain: str = ""
) -> str:
    """
    Recherche d'entités Home Assistant par nom ou domaine.
    
    Interroge directement l'API HA pour trouver des capteurs, lumières,
    interrupteurs ou tout autre type d'entité.
    
    Args:
        query: Terme de recherche dans le nom des entités (ex: "température", "salon", "volet").
        domain: Filtrer par domaine HA (optionnel). Ex: "sensor", "light", "switch", "climate", "binary_sensor".
    """
    try:
        import asyncio
        import requests

        # Récupérer les entités HA via l'API
        ha_url = os.environ.get("HA_URL", "http://${HA_HOST:-192.168.1.x}:8123")
        ha_token = os.environ.get("HA_TOKEN", "")

        if not ha_token:
            return "❌ Variable HA_TOKEN non configurée dans .env. Impossible de contacter Home Assistant."

        # [T121] requests est synchrone → to_thread pour ne pas geler l'event loop.
        response = await asyncio.to_thread(
            requests.get,
            f"{ha_url}/api/states",
            headers={"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"},
            timeout=10,
        )
        
        if response.status_code != 200:
            return f"❌ HA API erreur {response.status_code}: {response.text[:200]}"
        
        entities = response.json()
        
        # Filtrage par domaine
        if domain:
            entities = [e for e in entities if e.get("entity_id", "").startswith(f"{domain}.")]
        
        # Filtrage par query (recherche dans entity_id et friendly_name)
        query_lower = query.lower()
        matched = [
            e for e in entities 
            if query_lower in e.get("entity_id", "").lower()
            or query_lower in e.get("attributes", {}).get("friendly_name", "").lower()
        ]
        
        if not matched:
            return f"🔍 Aucune entité trouvée pour '{query}'" + (f" dans le domaine '{domain}'" if domain else "")
        
        # Limiter à 30 résultats
        matched = matched[:30]
        
        lines = [f"🏠 **{len(matched)} entité(s)** trouvée(s) pour \"{query}\"" + (f" (domaine: {domain})" if domain else "") + "\n"]
        for e in matched:
            eid = e.get("entity_id", "?")
            name = e.get("attributes", {}).get("friendly_name", "")
            state = e.get("state", "?")
            unit = e.get("attributes", {}).get("unit_of_measurement", "")
            unit_str = f" {unit}" if unit else ""
            lines.append(f"  - `{eid}` → **{name}** = `{state}{unit_str}`")
        
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Erreur recherche HA : {e}"
# ═══════════════════════════════════════════════════════
# Outil 8 — Validation de configuration (linter)
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def validate_config_format(file_path: str) -> str:
    """
    Valide localement la syntaxe d'un fichier de configuration (YAML, Jinja2, ESPHome).
    Détecte les erreurs de syntaxe YAML, les expressions Jinja2 mal formées,
    et exécute 'esphome config' sur les configurations ESPHome pour identifier
    les erreurs sémantiques de configuration hardware (DAC, I2C, GPIO) et de C++.
    
    Args:
        file_path: Chemin absolu ou relatif vers le fichier à valider.
    """
    from tools.linter import ConfigurationLinter

    linter = ConfigurationLinter()
    # [T124] Ce fichier vit dans core/mcp_tools/ (2 niveaux sous la racine du repo,
    # où vivait l'ex-mcp_server.py) — remonter explicitement 3 dirname() pour que
    # workspace_root reste la racine du projet, pas core/mcp_tools/.
    workspace_root = os.path.realpath(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )

    # Si le chemin est relatif, le résoudre par rapport à la racine du serveur
    if not os.path.isabs(file_path):
        candidate_path = os.path.join(workspace_root, file_path)
    else:
        candidate_path = file_path

    # [T135] realpath() résout aussi les symlinks (contrairement à abspath()) : un
    # symlink situé DANS le workspace mais pointant vers une cible EXTÉRIEURE
    # passait la vérification startswith() ci-dessous tout en donnant accès au
    # fichier externe une fois ouvert par le linter.
    resolved_path = os.path.realpath(candidate_path)

    # SEC-1 : Sécurité - Path Traversal
    # Vérifier que le chemin réel commence bien par workspace_root
    if not (resolved_path == workspace_root or resolved_path.startswith(workspace_root + os.sep)):
        return "❌ Erreur de sécurité : Accès refusé (Path Traversal détecté)."
        
    try:
        import asyncio
        # Exécuter la validation dans un thread séparé si elle contient des appels bloquants
        result = await asyncio.to_thread(linter.validate_file, resolved_path)
        
        if result["valid"]:
            return f"✅ CONFIGURATION VALIDE : {result['message']} (type: {result['type']})"
        else:
            return f"❌ CONFIGURATION INVALIDE ({result['type']}) :\n\n{result['error']}"
    except Exception as e:
        return f"❌ Erreur lors de la validation du fichier : {e}"
# ═══════════════════════════════════════════════════════
# Outil 12 — Exécution directe de commande Home Assistant
# ═══════════════════════════════════════════════════════

@mcp.tool()
async def execute_ha_action(
    entity_id: str,
    service: str,
    service_data: str = "{}",
) -> str:
    """
    Exécute une action (service) directement sur Home Assistant via l'API REST.

    Contourne le pipeline du moteur pour les commandes domotiques simples et déterministes.
    Idéal pour allumer/éteindre une lumière, régler un thermostat, déclencher une scène.

    Exemples d'appel :
        execute_ha_action("light.salon", "light.turn_on", '{"brightness": 200}')
        execute_ha_action("climate.chambre", "climate.set_temperature", '{"temperature": 21}')
        execute_ha_action("scene.cinema", "scene.turn_on")
        execute_ha_action("switch.prise_bureau", "switch.toggle")

    Args:
        entity_id: Identifiant complet de l'entité HA (ex: "light.salon_principal").
        service: Service HA à appeler (ex: "light.turn_on", "climate.set_temperature").
        service_data: JSON string des données du service (optionnel). Défaut: "{}".
    """
    import asyncio
    import requests
    import json as _json

    ha_url = os.environ.get("HA_URL", os.environ.get("HASS_URL", "http://${HA_HOST:-192.168.1.x}:8123"))
    ha_token = os.environ.get("HA_TOKEN", os.environ.get("HASS_TOKEN", ""))

    if not ha_token:
        return "❌ Variable HA_TOKEN (ou HASS_TOKEN) non configurée dans .env."

    # Parser le domaine depuis le service (ex: "light.turn_on" → domain="light", svc="turn_on")
    if "." in service:
        domain, svc_name = service.split(".", 1)
    else:
        # Déduire le domaine depuis entity_id
        domain = entity_id.split(".")[0] if "." in entity_id else service
        svc_name = service

    # [P0-1.6] Valider les identifiants avant de les injecter dans l'URL de l'API HA
    # (anti-traversée de chemin / injection de segments via entrée LLM non maîtrisée).
    from core.validation import (
        is_valid_ha_entity_id, is_valid_ha_domain, is_valid_ha_service_name,
        validate_service_data,
    )
    if not is_valid_ha_entity_id(entity_id):
        return f"❌ entity_id invalide : {entity_id!r} (attendu : domaine.objet, ex: light.salon)."
    if not is_valid_ha_domain(domain):
        return f"❌ domaine HA invalide : {domain!r}."
    if not is_valid_ha_service_name(svc_name):
        return f"❌ service HA invalide : {svc_name!r}."

    # Parser service_data
    try:
        svc_data = _json.loads(service_data) if service_data.strip() != "{}" else {}
    except _json.JSONDecodeError:
        return f"❌ service_data invalide (JSON attendu) : {service_data}"

    # Valider service_data contre les injections de templates Jinja2 (SSTI) et clés invalides
    try:
        validate_service_data(svc_data)
    except ValueError as val_err:
        return f"❌ service_data invalide : {str(val_err)}"

    # Ajouter entity_id dans les données si pas déjà présent
    if "entity_id" not in svc_data:
        svc_data["entity_id"] = entity_id

    endpoint = f"{ha_url}/api/services/{domain}/{svc_name}"

    try:
        # [T121] requests est synchrone → to_thread pour ne pas geler l'event loop.
        resp = await asyncio.to_thread(
            requests.post,
            endpoint,
            headers={
                "Authorization": f"Bearer {ha_token}",
                "Content-Type": "application/json",
            },
            json=svc_data,
            timeout=10,
        )

        if resp.status_code in (200, 201):
            # HA retourne une liste d'états modifiés
            try:
                changed = resp.json()
                changed_ids = [e.get("entity_id", "?") for e in changed] if isinstance(changed, list) else []
                changed_str = ", ".join(changed_ids) if changed_ids else entity_id
            except Exception:
                changed_str = entity_id

            return (
                f"✅ **Commande exécutée avec succès**\n"
                f"  - Service : `{domain}.{svc_name}`\n"
                f"  - Entité : `{entity_id}`\n"
                f"  - Données : `{svc_data}`\n"
                f"  - Entités modifiées : `{changed_str}`"
            )
        else:
            return (
                f"❌ Erreur HA API ({resp.status_code}) :\n"
                f"  Endpoint : {endpoint}\n"
                f"  Réponse : {resp.text[:300]}"
            )

    except requests.exceptions.ConnectionError:
        return f"❌ Impossible de contacter Home Assistant à {ha_url}. Vérifier le réseau ou l'URL."
    except Exception as e:
        return f"❌ Erreur lors de l'appel HA : {e}"
