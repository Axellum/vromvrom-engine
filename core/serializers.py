from core.state import GlobalState, TaskPayload, StateUpdate

def task_payload_to_dict(p: TaskPayload) -> dict:
    if not p: return None
    return {
        "task_objective": p.task_objective,
        "relevant_context": p.relevant_context,
        "available_tools": p.available_tools,
        "metadata": p.metadata,
        "task_id": p.task_id,
        "depends_on": p.depends_on
    }

def state_update_to_dict(u: StateUpdate) -> dict:
    if not u: return None
    return {
        "agent_name": u.agent_name,
        "status": u.status,
        "result_data": u.result_data,
        "next_agent": u.next_agent,
        "error_message": u.error_message,
        "new_tasks": [task_payload_to_dict(t) for t in u.new_tasks],
        "metadata": getattr(u, "metadata", {})
    }

def global_state_to_dict(s: GlobalState) -> dict:
    if not s: return None
    # Faire une copie superficielle de la liste history et de la queue de tâches pour éviter RuntimeError sur modifications concurrentes
    history_list = list(s.history) if s.history else []
    task_queue_list = list(s.task_queue) if s.task_queue else []
    return {
        "session_id": s.session_id,
        "history": [state_update_to_dict(h) for h in history_list],
        "current_payload": task_payload_to_dict(s.current_payload),
        "shared_memory": s.shared_memory,
        "task_queue": [task_payload_to_dict(t) for t in task_queue_list],
        "working_memory": s.working_memory
    }

