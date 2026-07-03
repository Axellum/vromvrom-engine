"""
tests/unit/test_hitl_risk.py — Tests du durcissement HITL (Phase 1, M5).

Le risque doit dépendre de l'OUTIL/agent ciblé (signal primaire), pas seulement
du libellé. Les sessions interactives à risque ne doivent plus être contournées
d'office ; les sessions autonomes restent bypassées.
"""

from core.engine import Engine
from core.state import TaskPayload


def _engine(session_id="chat_test"):
    return Engine(session_id=session_id)


def _task(objective="", target_agent="executor", direct_tool=None):
    meta = {"target_agent": target_agent}
    if direct_tool:
        meta["direct_tool_call"] = {"name": direct_tool, "arguments": {}}
    return TaskPayload(task_objective=objective, metadata=meta)


def test_read_only_plan_is_low_risk():
    eng = _engine()
    risk, _ = eng._assess_dag_risk([_task("Lire le fichier config et résumer")])
    assert risk == "low"


def test_terminal_tool_is_critical():
    eng = _engine()
    risk, reasons = eng._assess_dag_risk([_task("tâche", direct_tool="run_terminal_command")])
    assert risk == "critical"
    assert any("run_terminal_command" in r for r in reasons)


def test_write_file_tool_is_high():
    eng = _engine()
    risk, _ = eng._assess_dag_risk([_task("tâche", direct_tool="write_file")])
    assert risk == "high"


def test_ha_agent_is_medium():
    eng = _engine()
    risk, _ = eng._assess_dag_risk([_task("Allumer la lumière", target_agent="ha_agent")])
    assert risk == "medium"


def test_keyword_fallback_still_detects_delete():
    """Même sans outil ciblé, un libellé destructeur est détecté (signal de repli)."""
    eng = _engine()
    risk, _ = eng._assess_dag_risk([_task("Supprimer tous les fichiers temporaires")])
    assert risk == "high"


def test_max_risk_across_tasks():
    eng = _engine()
    risk, _ = eng._assess_dag_risk([
        _task("Lire un fichier"),
        _task("tâche", direct_tool="run_terminal_command"),
        _task("Allumer la lumière", target_agent="ha_agent"),
    ])
    assert risk == "critical"


def test_autonomous_session_bypasses_high_risk():
    """Une session daemon/dreamer contourne le HITL même en risque critique."""
    eng = _engine(session_id="daemon_001")
    assert eng._hitl_bypass_reason("daemon_001", "critical") is not None
    assert eng._hitl_bypass_reason("dreamer_x", "high") is not None


def test_interactive_high_risk_requires_approval():
    """[M5] Une session chat_* à risque n'est PLUS contournée d'office."""
    eng = _engine(session_id="chat_abc")
    assert eng._hitl_bypass_reason("chat_abc", "high") is None
    assert eng._hitl_bypass_reason("stream_1", "critical") is None


def test_low_risk_always_bypasses():
    eng = _engine()
    assert eng._hitl_bypass_reason("chat_abc", "low") is not None
