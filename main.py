"""
main.py — CLI de test du tab5-engine.

⚠️  POINT D'ENTRÉE DE PRODUCTION : `gui_server.py` (serveur FastAPI + IHM, port 8000).
    Les lanceurs `deploy/lancer_moteur.bat` et `start_moteur.sh` démarrent gui_server.py.

Ce module est un harnais de LIGNE DE COMMANDE pour exécuter une requête unique contre
le moteur réel, utile pour le debug et les smoke-tests. Contrairement à l'ancienne version
(« V4 », qui instanciait un Router sans gateway ni RAG → slow-path et RAG désactivés),
il s'appuie désormais sur `core.factory.create_engine()`, l'assemblage canonique partagé
avec gui_server.py et le serveur MCP.

Usage :
    python main.py "Ta requête ici"
    python main.py            # utilise une requête de démonstration
"""

import sys
import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()

logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

logger = logging.getLogger("main")

DEMO_REQUEST = (
    "Crée un fichier texte 'a.txt' contenant le mot 'Bonjour' et un fichier 'b.txt' "
    "contenant 'Monde'. Ensuite, lis ces deux fichiers et crée un fichier 'c.txt' "
    "contenant la fusion des deux contenus."
)


async def run_once(user_request: str) -> None:
    """Assemble le moteur via la factory canonique et exécute une requête unique."""
    from core.factory import create_engine
    from core.mcp_bridge import MCPBridge

    print("\n=== tab5-engine — CLI (debug) ===\n")
    print(f"REQUÊTE : {user_request}\n")

    # Assemblage réel : gateway + RAG + ContextManager + agents + Router complet.
    engine, router, _config = create_engine(session_id="cli_session")

    # Routage (4 couches) → premier agent + payload enrichi (RAG, mémoire, Elo).
    initial_payload, starting_agent = await router.analyze_request(user_request)

    # Pont MCP (outils externes Home Assistant, SQLite, etc.).
    mcp_bridge = MCPBridge()
    try:
        await mcp_bridge.start(engine.agents.get("executor").tool_registry, user_prompt=user_request)
        final_state = await engine.run(initial_payload, starting_agent)
    finally:
        await mcp_bridge.stop()

    print("\n=== Résultat de l'orchestration ===")
    for idx, update in enumerate(final_state.history):
        print(f"[{idx + 1}] Agent '{update.agent_name}' ({update.status}) :\n{update.result_data}\n")


def main() -> None:
    user_request = sys.argv[1] if len(sys.argv) > 1 else DEMO_REQUEST
    asyncio.run(run_once(user_request))


if __name__ == "__main__":
    main()
