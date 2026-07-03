import os
import sys
import logging
import asyncio

# Ajout du dossier courant au path pour les imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.engine import Engine
from core.router import Router
from core.llm_gateway import LLMGateway
from tools.tool_registry import ToolRegistry
from agents.executor import ExecutorAgent
from agents.planner import PlannerAgent
from agents.antigravity_agent import AntigravityAgent
from tools.system import read_file, write_file
from memory.context_manager import ContextManager
from dotenv import load_dotenv

load_dotenv()

# Configuration des logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("CI_Test_Engine")

async def run_integration_test():
    logger.info("Démarrage du test d'intégration CI pour le Moteur Agents Asynchrone...")
    
    # 1. Vérification des prérequis (ex: variable d'environnement GEMINI_API_KEY)
    if not os.environ.get("GEMINI_API_KEY"):
        logger.error("GEMINI_API_KEY manquante dans l'environnement ! Impossible de lancer le test.")
        return False
        
    try:
        # 2. Initialisation des composants
        gateway = LLMGateway()
        registry = ToolRegistry()
        context_manager = ContextManager(llm_gateway=gateway)
        
        # Enregistrement des outils basiques
        registry.register("read_file", read_file, "Lit le contenu d'un fichier texte local.")
        registry.register("write_file", write_file, "Crée ou modifie un fichier texte local.")
        
        # Initialisation des agents selon la stratégie de ventilation
        executor = ExecutorAgent(llm_gateway=gateway, tool_registry=registry, provider_name="deepseek-chat")
        planner = PlannerAgent(llm_gateway=gateway, provider_name="deepseek-reasoner")
        antigravity_agent = AntigravityAgent(llm_gateway=gateway, provider_name="gemini")
        
        # Assemblage du moteur
        engine = Engine(session_id="ci_session_test_async", context_manager=context_manager)
        engine.register_agent(executor)
        engine.register_agent(planner)
        engine.register_agent(antigravity_agent)
        
        router = Router(default_agent="planner")
        
        # 3. Requête de test sollicitant la parallélisation
        test_request = (
            "Crée un fichier 'test_ci_a.txt' contenant 'Bonjour' et un fichier 'test_ci_b.txt' contenant 'Monde' en parallèle. "
            "Ensuite, lis ces deux fichiers en parallèle pour en récupérer le contenu. "
            "Enfin, crée un fichier 'test_ci_c.txt' contenant la fusion des deux contenus récupérés."
        )
        logger.info(f"Requête de test soumise : {test_request}")
        
        payload, starting_agent = await router.analyze_request(test_request)
        
        # 4. Exécution du moteur en asynchrone
        final_state = await engine.run(payload, starting_agent)
        
        # 5. Validation des résultats
        # a) Vérification que l'historique n'est pas vide
        if not final_state.history:
            logger.error("Erreur : L'historique d'exécution est vide.")
            return False
            
        # b) Vérification de la présence d'étapes réussies
        for idx, step in enumerate(final_state.history):
            logger.info(f"Étape [{idx+1}] Agent '{step.agent_name}' ({step.status})")
            if step.status == "error":
                logger.error(f"Erreur détectée à l'étape {idx+1} : {step.error_message}")
                return False
                
        # c) Vérification des fichiers créés
        file_a = "test_ci_a.txt"
        file_b = "test_ci_b.txt"
        file_c = "test_ci_c.txt"
        
        if not os.path.exists(file_a) or not os.path.exists(file_b) or not os.path.exists(file_c):
            logger.error("Erreur : Un ou plusieurs fichiers de validation (test_ci_a, test_ci_b, test_ci_c) n'ont pas été créés.")
            return False
            
        with open(file_c, "r", encoding="utf-8") as f:
            content_c = f.read().strip()
            
        if "Bonjour" not in content_c or "Monde" not in content_c:
            logger.error(f"Erreur : Contenu de fusion incorrect dans '{file_c}' : '{content_c}'")
            return False
            
        logger.info(f"Fichier de fusion '{file_c}' lu avec succès : {content_c}")
        
        # Nettoyage des fichiers
        for path in [file_a, file_b, file_c]:
            try:
                os.remove(path)
            except Exception:
                pass
        logger.info("Nettoyage des fichiers de validation effectué.")
        
        logger.info("Test d'intégration CI réussi avec succès !")
        return True
        
    except Exception as e:
        logger.error(f"Exception non gérée durant le test d'intégration : {e}", exc_info=True)
        return False

if __name__ == "__main__":
    success = asyncio.run(run_integration_test())
    if success:
        sys.exit(0)
    else:
        sys.exit(1)
