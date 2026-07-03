import os
import sys
import logging
import time

# Ajout du dossier courant au path pour les imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from tools.git_safety import (
    is_git_repo,
    git_prepare_agent_branch,
    git_finalize_agent_branch,
    _run_git
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("Test_Git_Branches")

def run_git_tests():
    logger.info("=== STARTING GIT BRANCH SANDBOX TESTS ===")
    
    repo_path = os.path.dirname(os.path.abspath(__file__))
    if not is_git_repo(repo_path):
        logger.error(f"Le chemin {repo_path} n'est pas un dépôt Git. Impossible de tester le sandbox Git.")
        sys.exit(1)
        
    # Obtenir la branche actuelle pour pouvoir restaurer à la fin du test
    _, orig_branch_before_all, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    logger.info(f"Branche d'origine initiale : {orig_branch_before_all}")
    
    # ----------------------------------------------------
    # TEST 1: Succès de l'agent (Merge & Restauration stash)
    # ----------------------------------------------------
    logger.info("\n--- TEST 1: Succès de l'agent (Merge et Restauration Stash) ---")
    
    session_id_1 = f"test_session_success_{int(time.time())}"
    user_file = os.path.join(repo_path, "temp_user_edit_test.txt")
    agent_file = os.path.join(repo_path, "temp_agent_edit_test.txt")
    
    # Nettoyage initial
    for f in [user_file, agent_file]:
        if os.path.exists(f):
            os.remove(f)
            
    # 1. Simuler une modification utilisateur non commitée
    with open(user_file, "w", encoding="utf-8") as f:
        f.write("Modification utilisateur importante")
        
    logger.info("Fichier utilisateur créé pour simuler des modifs en cours.")
    
    # 2. Préparer l'environnement (Stash modifs utilisateur + checkout branche éphémère)
    branch_name = git_prepare_agent_branch(session_id_1, repo_path=repo_path)
    logger.info(f"Branche éphémère créée : {branch_name}")
    
    # Assertions après préparation
    assert branch_name.startswith("agent/run_"), f"Nom de branche invalide : {branch_name}"
    _, current_branch, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    assert current_branch == branch_name, f"On devrait être sur la branche éphémère {branch_name}, mais on est sur {current_branch}"
    assert not os.path.exists(user_file), "Le fichier utilisateur aurait dû être mis dans le stash Git et retiré du workspace"
    
    # 3. Simuler le travail de l'agent (écriture de code)
    with open(agent_file, "w", encoding="utf-8") as f:
        f.write("Modification apportée par l'agent V5")
        
    logger.info("Fichier de l'agent créé sur la branche éphémère.")
    
    # 4. Finaliser avec SUCCÈS (commit + merge + suppression branche + restauration stash)
    logger.info("Finalisation avec succès...")
    merge_res = git_finalize_agent_branch(branch_name, success=True, session_id=session_id_1, repo_path=repo_path)
    logger.info(f"Résultat finalisation : {merge_res}")
    
    # Assertions après finalisation avec succès
    _, final_branch, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    assert final_branch == orig_branch_before_all, f"On devrait être revenus sur la branche {orig_branch_before_all}, mais on est sur {final_branch}"
    assert os.path.exists(agent_file), "Le fichier de l'agent devrait avoir été fusionné et être présent dans la branche principale"
    assert os.path.exists(user_file), "Le fichier utilisateur stashed aurait dû être restauré"
    
    with open(user_file, "r", encoding="utf-8") as f:
        user_content = f.read()
    assert user_content == "Modification utilisateur importante", f"Contenu utilisateur altéré : {user_content}"
    
    with open(agent_file, "r", encoding="utf-8") as f:
        agent_content = f.read()
    assert agent_content == "Modification apportée par l'agent V5", f"Contenu de l'agent altéré : {agent_content}"
    
    # Nettoyage du fichier agent après merge réussi pour ne pas polluer git
    if os.path.exists(agent_file):
        os.remove(agent_file)
    # Commit du nettoyage pour garder la branche propre si nécessaire
    _run_git(["add", "temp_agent_edit_test.txt"], cwd=repo_path)
    _run_git(["commit", "-m", "Nettoyage fichier de test agent"], cwd=repo_path)
    
    logger.info("TEST 1 RÉUSSI AVEC SUCCÈS !")
    
    # ----------------------------------------------------
    # TEST 2: Échec de l'agent (Rollback & Restauration stash)
    # ----------------------------------------------------
    logger.info("\n--- TEST 2: Échec de l'agent (Rollback et Restauration Stash) ---")
    
    session_id_2 = f"test_session_fail_{int(time.time())}"
    
    # Le fichier utilisateur existe toujours grâce au pop du test 1.
    assert os.path.exists(user_file), "Le fichier utilisateur devrait être présent"
    
    # 1. Préparer l'environnement
    branch_name_fail = git_prepare_agent_branch(session_id_2, repo_path=repo_path)
    logger.info(f"Branche éphémère créée : {branch_name_fail}")
    
    # Assertions après préparation
    _, current_branch_fail, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    assert current_branch_fail == branch_name_fail, f"Devrait être sur {branch_name_fail}"
    assert not os.path.exists(user_file), "Le fichier utilisateur devrait être stashed"
    
    # 2. Simuler le travail de l'agent (écriture de code erroné)
    with open(agent_file, "w", encoding="utf-8") as f:
        f.write("Modification erronée de l'agent")
        
    logger.info("Fichier de l'agent créé sur la branche éphémère.")
    
    # 3. Finaliser avec ÉCHEC (suppression de la branche + restauration stash)
    logger.info("Finalisation avec échec (Rollback)...")
    rollback_res = git_finalize_agent_branch(branch_name_fail, success=False, session_id=session_id_2, repo_path=repo_path)
    logger.info(f"Résultat finalisation rollback : {rollback_res}")
    
    # Assertions après échec
    _, final_branch_fail, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    assert final_branch_fail == orig_branch_before_all, f"Devrait être revenus sur {orig_branch_before_all}"
    assert not os.path.exists(agent_file), "Le fichier écrit par l'agent n'aurait pas dû être fusionné ni exister dans l'arbre de travail final"
    assert os.path.exists(user_file), "Le fichier utilisateur stashed aurait dû être restauré après le rollback"
    
    with open(user_file, "r", encoding="utf-8") as f:
        user_content_final = f.read()
    assert user_content_final == "Modification utilisateur importante", "Le fichier utilisateur doit rester intact"
    
    # Nettoyage final du fichier utilisateur temporaire
    if os.path.exists(user_file):
        os.remove(user_file)
        
    logger.info("TEST 2 RÉUSSI AVEC SUCCÈS !")
    logger.info("=== ALL GIT BRANCH SANDBOX TESTS PASSED ===")

if __name__ == "__main__":
    run_git_tests()
