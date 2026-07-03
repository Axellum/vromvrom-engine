import requests
import json
import logging

logger = logging.getLogger(__name__)

def call_api(url: str, method: str = "GET", payload_json: str = None, headers_json: str = None) -> str:
    """
    Effectue une requête HTTP vers une API distante (REST/HomeAssistant/ESPHome).
    payload_json et headers_json doivent être des chaînes (string) au format JSON valide s'ils sont utilisés.
    """
    logger.info(f"Appel API distant: {method.upper()} {url}")
    try:
        # Parsing des arguments JSON envoyés par le LLM
        headers = {}
        if headers_json:
            headers = json.loads(headers_json)
            
        data = None
        if payload_json:
            data = json.loads(payload_json)
            # Injecter Content-Type si payload présent et non spécifié
            if "Content-Type" not in headers:
                headers["Content-Type"] = "application/json"

        response = requests.request(
            method=method.upper(),
            url=url,
            headers=headers,
            json=data,
            timeout=15
        )
        
        # Formatage lisible pour l'agent
        try:
            result = response.json()
            output = json.dumps(result, indent=2)
        except json.JSONDecodeError:
            output = response.text
            
        status = response.status_code
        if status >= 400:
            full_response = f"Erreur (HTTP {status}): {output}"
        else:
            full_response = f"Status Code: {status}\nResponse:\n{output}"
        
        MAX_CHARS = 4000
        if len(full_response) > MAX_CHARS:
            return full_response[:MAX_CHARS] + "\n...[SORTIE TRONQUÉE]..."
            
        return full_response
        
    except requests.exceptions.Timeout:
        return "Erreur: Délai d'attente dépassé (Timeout > 15s)."
    except requests.exceptions.RequestException as e:
        return f"Erreur de réseau HTTP: {str(e)}"
    except json.JSONDecodeError as e:
        return f"Erreur de syntaxe JSON dans tes paramètres 'payload_json' ou 'headers_json' : {str(e)}"
    except Exception as e:
        return f"Erreur inattendue de l'outil API: {str(e)}"
