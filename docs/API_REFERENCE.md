# 📖 vromvrom-engine API Reference

The `vromvrom-engine` exposes two types of APIs: a native REST API powered by FastAPI, and an OpenAI-compatible Proxy for universal integration.

🇫🇷 **[Version française disponible → API_REFERENCE.fr.md](API_REFERENCE.fr.md)**

## 1. Interactive Documentation (Swagger)

All native engine endpoints are interactively documented thanks to FastAPI.
Once the engine is running (`python gui_server.py`), open your browser to:

👉 **[http://localhost:8000/docs](http://localhost:8000/docs)**

You will find:
- The interface to test each endpoint.
- Expected JSON schemas for inputs and outputs.
- Error codes.

---

## 2. The OpenAI-Compatible Proxy (`/v1`)

This is one of the most powerful features of the engine. It exposes a 100% OpenAI API compatible endpoint, allowing you to use `vromvrom-engine` as an LLM backend for any IDE (Cursor, Cline, Aider) or third-party interface.

**Endpoint:** `POST /v1/chat/completions`

### IDE Configuration (e.g., Cursor)
- **Base URL**: `http://localhost:8000/v1`
- **API Key**: The value of your `MOTEUR_API_KEY` defined in your `.env`.
- **Model**: Use any model configured in your `config.json` (e.g., `gemini-2.5-pro`, `github/gpt-4o`, or `auto` to let the engine choose via the Elo system).

### How it works under the hood
Even though your IDE thinks it's talking to OpenAI, the engine intercepts the request, applies **Elo Routing**, uses its own keys (via the KeyPool), and triggers the **Circuit Breaker** in case of a provider error, then returns the response formatted as OpenAI.

---

## 3. The Native Engine API (`/api/*`)

Here is an overview of the main engine-specific routes:

### General Execution
- `POST /api/execute`: Sends a complex task to the engine (Routing -> Planner -> Executor -> Reviewer).
- `POST /api/execute/stream`: Same but returns the response as Server-Sent Events (SSE) for real-time streaming.

### Home Assistant Integration (Home Automation)
- `GET /api/ha/state/{entity_id}`: Retrieves the current state of a sensor (e.g., `sensor.living_room_temperature`).
- `POST /api/ha/control`: Executes a service (e.g., turning on a light).
  ```json
  {
    "entity_id": "light.living_room",
    "service": "turn_on",
    "service_data": {"brightness": 255}
  }
  ```

### Workflows & Models Management
- `GET /api/workflows`: Lists available DAG execution graphs.
- `GET /api/models`: Lists available LLMs and their current Elo scores.

> *For the full specification, check the Swagger UI at `http://localhost:8000/docs`.*
