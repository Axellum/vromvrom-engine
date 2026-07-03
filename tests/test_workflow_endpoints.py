import os
import sys
import unittest
import json
import shutil

# Ajouter le répertoire racine au PATH
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
from gui_server import app
from api.routes.workflows import WORKFLOWS_DIR, WORKFLOWS_FILE

class TestWorkflowEndpoints(unittest.TestCase):
    
    def setUp(self):
        # [P0-1.1] Les routes /api/workflows sont protégées par require_auth :
        # on envoie le Bearer correspondant à la MOTEUR_API_KEY de test (conftest).
        _auth = {"Authorization": f"Bearer {os.environ.get('MOTEUR_API_KEY', '')}"}
        self.client = TestClient(app, headers=_auth)
        self.test_name = "test_unit_workflow"
        self.test_file_path = os.path.join(WORKFLOWS_DIR, f"{self.test_name}.json")
        
        # Sauvegarder le fichier actif d'origine s'il existe
        self.backup_active_file = WORKFLOWS_FILE + ".backup"
        if os.path.exists(WORKFLOWS_FILE):
            shutil.copy2(WORKFLOWS_FILE, self.backup_active_file)
            
        # S'assurer que le fichier de test n'existe pas au départ
        if os.path.exists(self.test_file_path):
            os.remove(self.test_file_path)
            
    def tearDown(self):
        # Supprimer le fichier de test s'il a été créé
        if os.path.exists(self.test_file_path):
            try:
                os.remove(self.test_file_path)
            except Exception:
                pass
                
        # Restaurer le fichier actif d'origine
        if os.path.exists(self.backup_active_file):
            if os.path.exists(WORKFLOWS_FILE):
                os.remove(WORKFLOWS_FILE)
            shutil.move(self.backup_active_file, WORKFLOWS_FILE)
            
    def test_crud_workflow_endpoints(self):
        """Teste le cycle de vie d'un workflow nommé via les nouveaux endpoints API."""
        dummy_workflow = {
            "metadata": {"version": "2.0_test"},
            "connections": [{"from": "node-1", "to": "node-2"}],
            "nodes": [
                {"id": "node-1", "type": "start", "x": 10, "y": 10},
                {"id": "node-2", "type": "end", "x": 100, "y": 100}
            ]
        }
        
        # 1. Sauvegarder le workflow (POST)
        response_save = self.client.post(f"/api/workflows/save/{self.test_name}", json=dummy_workflow)
        self.assertEqual(response_save.status_code, 200)
        save_data = response_save.json()
        self.assertIn("sauvegardé", save_data.get("message", ""))
        self.assertEqual(save_data.get("nodes"), 2)
        
        # Vérifier que le fichier physique existe dans workflows/ et a été copié dans agents_workflows.json
        self.assertTrue(os.path.exists(self.test_file_path))
        self.assertTrue(os.path.exists(WORKFLOWS_FILE))
        with open(WORKFLOWS_FILE, "r", encoding="utf-8") as active_f:
            active_content = json.load(active_f)
        self.assertEqual(active_content.get("metadata", {}).get("version"), "2.0_test")
        
        # 2. Lister les workflows (GET /list)
        response_list = self.client.get("/api/workflows/list")
        self.assertEqual(response_list.status_code, 200)
        list_data = response_list.json()
        self.assertIn("workflows", list_data)
        self.assertIn(self.test_name, list_data["workflows"])
        self.assertIn("Default", list_data["workflows"])
        
        # 3. Charger un autre workflow (GET /load/Default)
        response_load_def = self.client.get("/api/workflows/load/Default")
        self.assertEqual(response_load_def.status_code, 200)
        load_def_data = response_load_def.json()
        self.assertIn("Default", load_def_data.get("message", ""))
        
        # Vérifier que le fichier actif a bien été réécrit avec le contenu de Default
        with open(WORKFLOWS_FILE, "r", encoding="utf-8") as active_f:
            active_content_def = json.load(active_f)
        self.assertNotEqual(active_content_def.get("metadata", {}).get("version"), "2.0_test")
        
        # 4. Charger notre workflow de test à nouveau (GET /load/{name})
        response_load_test = self.client.get(f"/api/workflows/load/{self.test_name}")
        self.assertEqual(response_load_test.status_code, 200)
        load_test_data = response_load_test.json()
        self.assertEqual(load_test_data.get("workflow", {}).get("metadata", {}).get("version"), "2.0_test")
        
        # Vérifier à nouveau la réécriture du fichier actif
        with open(WORKFLOWS_FILE, "r", encoding="utf-8") as active_f:
            active_content_test = json.load(active_f)
        self.assertEqual(active_content_test.get("metadata", {}).get("version"), "2.0_test")
        
        # 5. Tenter de supprimer Default (doit échouer - HTTP 400)
        response_del_def = self.client.delete("/api/workflows/Default")
        self.assertEqual(response_del_def.status_code, 400)
        
        # 6. Supprimer le workflow de test (DELETE)
        response_del = self.client.delete(f"/api/workflows/{self.test_name}")
        self.assertEqual(response_del.status_code, 200)
        self.assertIn("supprimé", response_del.json().get("message", ""))
        
        # Vérifier que le fichier physique dans workflows/ a disparu
        self.assertFalse(os.path.exists(self.test_file_path))
        
        # 7. Tenter de charger le workflow supprimé (doit retourner HTTP 404)
        response_load_deleted = self.client.get(f"/api/workflows/load/{self.test_name}")
        self.assertEqual(response_load_deleted.status_code, 404)

if __name__ == "__main__":
    unittest.main()
