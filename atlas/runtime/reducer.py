from atlas.domain.models import Action, Memory, PlanNode, Run, RunStatus


def reduce_event(state: Run | None, event: dict) -> Run:
    kind, payload = event["type"], event["payload"]
    if kind == "run.created":
        result = Run.model_validate(payload["run"])
    else:
        if state is None:
            raise ValueError("event stream must start with run.created")
        result = state.model_copy(deep=True)
        if kind == "workspace.created":
            result.workspace = payload["path"]
        elif kind == "run.started":
            result.status = RunStatus.RUNNING
        elif kind == "plan.created":
            result.plan = [PlanNode.model_validate(node) for node in payload["nodes"]]
        elif kind == "model.completed":
            result.token_usage += payload["charged_tokens"]
            result.step_count += 1
        elif kind == "actions.proposed":
            result.pending = [Action.model_validate(action) for action in payload["actions"]]
        elif kind == "tool.started":
            result.status = RunStatus.WAITING_TOOL
            for node in result.plan:
                if node.id == payload.get("node_id"):
                    node.status = "running"
        elif kind == "tool.completed":
            result.status = RunStatus.RUNNING
            result.history.append({**payload, "event_id": event["id"]})
            action = Action.model_validate(payload["action"])
            result.pending = [item for item in result.pending if item != action]
            for node in result.plan:
                if node.id == action.node_id:
                    node.status = "succeeded" if payload["result"]["ok"] else "failed"
        elif kind == "memory.created":
            result.memories.append(Memory.model_validate(payload["memory"]))
        elif kind == "approval.requested":
            result.status = RunStatus.WAITING_APPROVAL
        elif kind == "approval.decided":
            result.status = RunStatus.QUEUED if payload["approved"] else RunStatus.FAILED
            if not payload["approved"]:
                result.error = "Patch approval rejected"
        elif kind == "verification.completed":
            result.report = payload["report"]
        elif kind == "run.completed":
            result.status = RunStatus.COMPLETED
            result.answer = payload["answer"]
        elif kind == "run.failed":
            result.status = RunStatus.FAILED
            result.error = payload["error"]
        elif kind == "run.cancelled":
            result.status = RunStatus.CANCELLED
    result.sequence = event["sequence"]
    result.updated_at = event["at"]
    return result
