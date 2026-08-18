# Carte de la cascade de décision vocale

Ce document cartographie l'ordre de consultation des étages dans la cascade de décision vocale du moteur.

## Ordre de consultation

La cascade est consultée dans l'ordre suivant :

1. **Court-circuits Zero-LLM (Domotique)** : `services/execute_service.py` (commandes), `services/ha_state_query.py` (état), `services/ha_weather_query.py` (météo), `services/datetime_query.py` (heure/date).
2. **Spécialistes synchrones** : `core/vocal_host.py` (Web, Calendrier).
3. **Chat généraliste** : `core/vocal_host.py` (Discussion).

## Tableau récapitulatif

| Étage | Fichier:Fonction | Routing Type | Condition de passage au suivant |
| :--- | :--- | :--- | :--- |
| HA Commande | `execute_service.py:resolve_ha_command_for_execute` | `discussion_ha_command` | Commande non reconnue |
| HA État | `ha_state_query.py:resolve_ha_state_query` | `discussion_ha_state` | Non reconnu comme question d'état |
| HA Météo | `ha_weather_query.py:resolve_weather_query` | `discussion_ha_weather` | Non reconnu comme question météo |
| Date/Heure | `datetime_query.py:resolve_datetime_query` | `discussion_datetime` | Non reconnu comme question temporelle |
| Spécialistes | `vocal_host.py:handle_discussion` (intent) | `vocal_host_{intent}` | Intent = CHAT |
| Chat | `vocal_host.py:handle_discussion` (chat) | `discussion_chat` | N/A (dernier recours) |

## Décision de sortie

Une phrase est considérée comme "non pour cet étage" si :
- **Zero-LLM** : La fonction de détection (`match_*`) retourne `None`.
- **Spécialistes** : L'intention classifiée (`classify_vocal_intent`) ne correspond pas à l'intent de l'étage.
- **Chat** : C'est le dernier recours, il traite tout ce qui reste.

*Note : Si un point n'est pas clair, il doit être considéré comme une question ouverte.*
