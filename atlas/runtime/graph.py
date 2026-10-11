from atlas.domain.models import PlanNode


def validate_plan(nodes: list[PlanNode]) -> None:
    ids = {node.id for node in nodes}
    if len(ids) != len(nodes):
        raise ValueError("duplicate plan node")
    remaining = {node.id: set(node.dependencies) for node in nodes}
    if any(not dependencies <= ids for dependencies in remaining.values()):
        raise ValueError("unknown plan dependency")
    while remaining:
        ready = {key for key, dependencies in remaining.items() if not dependencies}
        if not ready:
            raise ValueError("cyclic plan")
        remaining = {
            key: dependencies - ready for key, dependencies in remaining.items() if key not in ready
        }


def node_ready(nodes: list[PlanNode], node_id: str) -> bool:
    by_id = {node.id: node for node in nodes}
    if node_id not in by_id:
        raise ValueError("unknown plan node")
    node = by_id[node_id]
    return all(by_id[parent].status == "succeeded" for parent in node.dependencies)
