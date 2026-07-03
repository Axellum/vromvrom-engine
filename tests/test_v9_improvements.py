import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("test_v9")

# S'assurer d'être dans le bon path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.llm_gateway import LLMGateway, LLMProvider, ClaudeInstructionsWrapper
from memory.context_manager import ContextManager

class FakeBaseProvider(LLMProvider):
    def __init__(self):
        self.last_system_prompt = None
        self.last_user_prompt = None

    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        self.last_system_prompt = system_prompt
        self.last_user_prompt = user_prompt
        return "Response from FakeBaseProvider"

    def generate_structured(self, system_prompt: str, user_prompt: str, schema: dict, **kwargs) -> dict:
        self.last_system_prompt = system_prompt
        self.last_user_prompt = user_prompt
        return {"result": "Response"}

def test_claude_instructions_wrapper():
    print("\n--- TEST 1 : CLAUDE_INSTRUCTIONS_WRAPPER ---")
    
    # Créer un fichier de test temporaire CLAUDE.md local
    temp_claude_path = "CLAUDE.md"
    temp_created = False
    
    if not os.path.exists(temp_claude_path):
        with open(temp_claude_path, "w", encoding="utf-8") as f:
            f.write("# Rules\n- Use pytest\n- Comments in French")
        temp_created = True
        print("Fichier temporaire CLAUDE.md créé pour le test.")

    try:
        base_provider = FakeBaseProvider()
        wrapped = ClaudeInstructionsWrapper(base_provider)
        
        # Lancer un appel
        wrapped.generate("Prompt Système Original", "Prompt Utilisateur")
        
        print(f"System Prompt après wrapping:\n{base_provider.last_system_prompt}")
        
        assert "Prompt Système Original" in base_provider.last_system_prompt
        assert "CONVENTIONS DE PROJET (CLAUDE.md)" in base_provider.last_system_prompt
        assert "VM Freebox" in base_provider.last_system_prompt or "Use pytest" in base_provider.last_system_prompt
        print("✅ Test ClaudeInstructionsWrapper réussi.")
        
    finally:
        # Nettoyer uniquement si on l'a créé
        if temp_created and os.path.exists(temp_claude_path):
            os.remove(temp_claude_path)
            print("Fichier temporaire CLAUDE.md nettoyé.")

def test_context_manager_clean():
    print("\n--- TEST 2 : PRE-NETTOYAGE DES LOGS ---")
    gateway = LLMGateway()
    mgr = ContextManager(gateway)
    
    raw_logs = (
        "Log entry 1\n"
        "Log entry 2\n\n\n\n"  # trop de lignes vides
        "Log entry 2\n"        # doublon consécutif
        "Log entry 2\n"        # doublon consécutif
        "Log entry 3\n"
    )
    
    cleaned = mgr.clean_raw_data(raw_logs)
    print(f"Logs nettoyés :\n{cleaned}")
    
    # Devrait avoir réduit les sauts de lignes et supprimé les doublons
    lines = cleaned.splitlines()
    assert lines.count("Log entry 2") == 1, "Les doublons consécutifs n'ont pas été supprimés."
    assert cleaned.count("\n\n\n") == 0, "Les lignes vides excessives n'ont pas été réduites."
    print("✅ Test Nettoyage de Logs réussi.")

def test_context_manager_ignore():
    print("\n--- TEST 3 : EXCLUSION PAR IGNORE ---")
    gateway = LLMGateway()
    mgr = ContextManager(gateway)
    mgr.force_ignore_check = True
    # Patterns injectés directement → test déterministe, indépendant des fichiers
    # .antigravityignore ambiants (le workspace_root parent existe en dev mais pas
    # dans le checkout CI, ce qui rendait le test dépendant de l'environnement).
    mgr.ignore_patterns = ["*.log", "ignore_me/", "checkpoints/"]

    # Test 1 : Fichier log ignoré (motif *.log)
    res_log = mgr.optimize_file_read("debug.log", "Contenu volumineux du fichier de log...")
    print(f"Fichier log -> {res_log}")
    assert "ignoré par .antigravityignore" in res_log

    # Test 2 : Fichier dans un répertoire ignoré (motif checkpoints/)
    res_dir = mgr.optimize_file_read("checkpoints/state.json", "Contenu markdown...")
    print(f"Fichier checkpoints/state.json -> {res_dir}")
    assert "ignoré par .antigravityignore" in res_dir

    # Test 3 : Fichier non ignoré (petit contenu → renvoyé tel quel)
    res_ok = mgr.optimize_file_read("rules.md", "Petit markdown")
    print(f"Fichier rules.md -> {res_ok}")
    assert res_ok == "Petit markdown"

    print("✅ Test Ignore réussi.")

def main():
    print("=== DEBUT DES TESTS DES EVOLUTIONS V9 ===")
    try:
        test_claude_instructions_wrapper()
        test_context_manager_clean()
        test_context_manager_ignore()
        print("\n[SUCCESS] TOUS LES TESTS V9 ONT REUSSI AVEC SUCCES !")
    except Exception as e:
        print(f"\n[ERROR] ECHEC D'UN TEST : {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
