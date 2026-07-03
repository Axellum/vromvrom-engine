"""
api/routes/workflows.py — Routes CRUD pour la gestion des workflows (graphes d'agents).

Extraites de gui_server.py dans le cadre de l'Audit V9 (P1.3).
Gère la sauvegarde, le chargement, la suppression et l'application des workflows
au moteur d'orchestration.

@version 1.0.0 — Extraction depuis gui_server.py
"""

import os
import json
import logging
from fastapi import APIRouter, HTTPException

logger = logging.getLogger("api.workflows")

router = APIRouter(prefix="/api/workflows", tags=["Workflows"])

# Chemins des fichiers de workflows (résolus par rapport à la racine du moteur)
_ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKFLOWS_FILE = os.path.join(_ENGINE_ROOT, "agents_workflows.json")
WORKFLOWS_DIR = os.path.join(_ENGINE_ROOT, "workflows")

# S'assurer que le dossier workflows existe
os.makedirs(WORKFLOWS_DIR, exist_ok=True)


@router.get("")
def get_workflows():
    """Récupère le workflow sauvegardé depuis agents_workflows.json."""
    if os.path.exists(WORKFLOWS_FILE):
        try:
            with open(WORKFLOWS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erreur de lecture du workflow: {str(e)}")
    return {"nodes": [], "connections": [], "metadata": {}}


@router.post("")
def save_workflows(body: dict):
    """Sauvegarde un workflow dans agents_workflows.json."""
    try:
        with open(WORKFLOWS_FILE, 'w', encoding='utf-8') as f:
            json.dump(body, f, indent=2, ensure_ascii=False)
        return {"message": "Workflow sauvegardé avec succès.", "nodes": len(body.get("nodes", []))}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur de sauvegarde du workflow: {str(e)}")


@router.get("/list")
def list_workflows():
    """Retourne la liste des noms de tous les workflows sauvegardés."""
    try:
        files = os.listdir(WORKFLOWS_DIR)
        names = [os.path.splitext(f)[0] for f in files if f.endswith(".json")]
        if "Default" not in names:
            names.append("Default")
        return {"workflows": sorted(names)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur de listage des workflows: {str(e)}")


@router.get("/load/{name}")
def load_workflow_by_name(name: str):
    """Charge un workflow spécifique et l'applique comme workflow actif."""
    if not name or not all(c.isalnum() or c in "-_" for c in name):
        raise HTTPException(status_code=400, detail="Nom de workflow invalide.")

    file_path = os.path.join(WORKFLOWS_DIR, f"{name}.json")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"Le workflow '{name}' n'existe pas.")

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            workflow_data = json.load(f)

        with open(WORKFLOWS_FILE, 'w', encoding='utf-8') as active_f:
            json.dump(workflow_data, active_f, indent=2, ensure_ascii=False)

        from core.workflow_bridge import WorkflowBridge
        from agents.planner import _workflow_bridge as planner_bridge
        planner_bridge.reload()
        WorkflowBridge().reload()

        return {"message": f"Workflow '{name}' chargé et appliqué.", "workflow": workflow_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur de chargement: {str(e)}")


@router.post("/save/{name}")
def save_workflow_by_name(name: str, body: dict):
    """Sauvegarde un workflow sous un nom spécifique et l'active."""
    if not name or not all(c.isalnum() or c in "-_" for c in name):
        raise HTTPException(status_code=400, detail="Nom de workflow invalide.")

    file_path = os.path.join(WORKFLOWS_DIR, f"{name}.json")
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(body, f, indent=2, ensure_ascii=False)

        with open(WORKFLOWS_FILE, 'w', encoding='utf-8') as active_f:
            json.dump(body, active_f, indent=2, ensure_ascii=False)

        from core.workflow_bridge import WorkflowBridge
        from agents.planner import _workflow_bridge as planner_bridge
        planner_bridge.reload()
        WorkflowBridge().reload()

        return {"message": f"Workflow '{name}' sauvegardé.", "nodes": len(body.get("nodes", []))}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur de sauvegarde: {str(e)}")


@router.delete("/{name}")
def delete_workflow_by_name(name: str):
    """Supprime un workflow (interdiction de supprimer 'Default')."""
    if name == "Default":
        raise HTTPException(status_code=400, detail="Impossible de supprimer 'Default'.")

    if not name or not all(c.isalnum() or c in "-_" for c in name):
        raise HTTPException(status_code=400, detail="Nom de workflow invalide.")

    file_path = os.path.join(WORKFLOWS_DIR, f"{name}.json")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"Workflow '{name}' introuvable.")

    try:
        os.remove(file_path)
        return {"message": f"Workflow '{name}' supprimé."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur de suppression: {str(e)}")


@router.post("/apply")
def apply_workflow():
    """Recharge le WorkflowBridge pour appliquer les modifications."""
    try:
        from core.workflow_bridge import WorkflowBridge
        from agents.planner import _workflow_bridge as planner_bridge

        planner_bridge.reload()
        bridge = WorkflowBridge()
        bridge.reload()
        return {
            "message": "Workflow appliqué au moteur.",
            "agents": bridge.get_registered_agent_names(),
            "custom_agents": bridge.get_custom_agent_names(),
            "planner_enum": bridge.get_planner_enum()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur d'application: {str(e)}")
