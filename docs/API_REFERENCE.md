# 📖 Référence de l'API vromvrom-engine

Le moteur `vromvrom-engine` expose deux types d'API : une API REST native propulsée par FastAPI, et un Proxy compatible OpenAI pour une intégration universelle.

## 1. Documentation Interactive (Swagger)

L'intégralité des endpoints natifs du moteur est documentée de manière interactive grâce à FastAPI.
Une fois le moteur lancé (`python gui_server.py`), ouvrez votre navigateur à l'adresse suivante :

👉 **[http://localhost:8000/docs](http://localhost:8000/docs)**

Vous y trouverez :
- L'interface pour tester chaque endpoint.
- Les schémas JSON attendus en entrée et en sortie.
- Les codes d'erreur.

---

## 2. Le Proxy OpenAI-Compatible (`/v1`)

C'est l'une des fonctionnalités les plus puissantes du moteur. Il expose un endpoint 100% compatible avec l'API OpenAI, vous permettant d'utiliser `vromvrom-engine` comme backend LLM pour n'importe quel IDE (Cursor, Cline, Aider) ou interface tierce.

**Endpoint :** `POST /v1/chat/completions`

### Configuration dans un IDE (ex: Cursor)
- **Base URL** : `http://localhost:8000/v1`
- **API Key** : La valeur de votre `MOTEUR_API_KEY` définie dans votre `.env`.
- **Model** : Utilisez n'importe quel modèle configuré dans votre `config.json` (ex: `gemini-2.5-pro`, `github/gpt-4o`, ou `auto` pour laisser le moteur choisir via le système Elo).

### Comment ça marche sous le capot
Même si votre IDE croit parler à OpenAI, le moteur intercepte la requête, applique le **Routage Elo**, utilise ses propres clés (via le KeyPool), et déclenche le **Circuit Breaker** en cas d'erreur du fournisseur, puis renvoie la réponse formattée comme OpenAI.

---

## 3. L'API Native du Moteur (`/api/*`)

Voici un aperçu des routes principales spécifiques au moteur :

### Exécution Générale
- `POST /api/execute` : Envoie une tâche complexe au moteur (Routage -> Planificateur -> Exécuteur -> Reviewer).
- `POST /api/execute/stream` : Identique mais retourne la réponse en Server-Sent Events (SSE) pour du temps réel.

### Intégration Home Assistant (Domotique)
- `GET /api/ha/state/{entity_id}` : Récupère l'état actuel d'un capteur (ex: `sensor.temperature_salon`).
- `POST /api/ha/control` : Exécute un service (ex: allumer une lumière).
  ```json
  {
    "entity_id": "light.salon",
    "service": "turn_on",
    "service_data": {"brightness": 255}
  }
  ```

### Gestion des Workflows & Modèles
- `GET /api/workflows` : Liste les graphes d'exécution DAG disponibles.
- `GET /api/models` : Liste les LLMs disponibles et leurs scores Elo actuels.

> *Pour la spécification complète, consultez le Swagger UI à `http://localhost:8000/docs`.*
