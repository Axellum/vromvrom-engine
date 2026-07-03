"""Test d'intégration rapide des 7 axes de l'audit V5.5."""

print("=== Vérification des imports (7 axes) ===")

# A4 — AntigravityAgent hérite d'ExecutorAgent
from agents.antigravity_agent import AntigravityAgent
from agents.executor import ExecutorAgent
assert issubclass(AntigravityAgent, ExecutorAgent), "A4 FAILED"
print("[A4] OK - AntigravityAgent hérite d'ExecutorAgent")

# A6 — Shared Scratchpad dans GlobalState
from core.state import GlobalState
gs = GlobalState(session_id="test-a6")
gs.working_memory["test_key"] = "test_value"
assert gs.working_memory["test_key"] == "test_value"
print("[A6] OK - working_memory dans GlobalState")

# A7 — Langfuse bridge (mode no-op)
import os
from core.langfuse_bridge import LangfuseBridge
# Temporairement retirer les clés du .env pour forcer le mode no-op
orig_pub = os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
orig_sec = os.environ.pop("LANGFUSE_SECRET_KEY", None)
LangfuseBridge._instance = None  # Reset singleton pour réévaluation

bridge = LangfuseBridge.get_instance()
assert not bridge.enabled, "Le bridge devrait être inactif sans clés API"
print("[A7] OK - Langfuse bridge no-op")

# Restaurer les variables d'environnement et réinitialiser le singleton
if orig_pub: os.environ["LANGFUSE_PUBLIC_KEY"] = orig_pub
if orig_sec: os.environ["LANGFUSE_SECRET_KEY"] = orig_sec
LangfuseBridge._instance = None


# A8 — Modules API (routers ACTIFS montés dans gui_server ; les anciens doublons
# non montés tokens.py/config.py/apis_status.py ont été supprimés — P2-3.1)
from fastapi import APIRouter
from api.routes.billing import router as r_tokens        # /api/tokens
from api.routes.context import router as r_config         # /api/config + /api/pricing
from api.routes.apis_external import router as r_apis     # /api/apis-status
from api.services.billing_service import billing_sync_state
for _r in (r_tokens, r_config, r_apis):
    assert isinstance(_r, APIRouter), "A8 FAILED"
assert billing_sync_state is not None
print("[A8] OK - routers actifs + service importés")

# A9 — Mémoire Procédurale
from memory.skills import SkillStore
store = SkillStore()
store.record_skill("test pattern", ["tool_a", "tool_b"], tags=["test"])
results = store.get_relevant_skills("test pattern")
assert len(results) >= 1
store.clear()
print("[A9] OK - SkillStore record + search")

# A11 — Workflow-as-Code
from core.workflow_executor import WorkflowExecutor
wf = WorkflowExecutor()
summary = wf.get_workflow_summary()
assert "agents" in summary and "transitions" in summary
print("[A11] OK - WorkflowExecutor (%d noeuds, %d connexions)" % (summary["total_nodes"], summary["total_connections"]))

# A12 — Persistance SQLite
from core.checkpoint import CheckpointManager
cm = CheckpointManager()
cm.save(gs)
loaded = cm.load("test-a6")
assert loaded is not None
assert loaded.working_memory.get("test_key") == "test_value"
cm.delete("test-a6")
print("[A12] OK - CheckpointManager SQLite save/load/delete")

print()
print("=== TOUS LES 7 AXES VALIDES ===")
