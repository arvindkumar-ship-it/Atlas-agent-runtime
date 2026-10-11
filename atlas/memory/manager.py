from __future__ import annotations

import re
from datetime import UTC, datetime

from atlas.domain.models import Memory


def terms(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9_]{3,}", text.casefold()))


def rank_memory(memory: Memory, query: str) -> float:
    query_terms = terms(query)
    overlap = len(terms(memory.content) & query_terms) / max(len(query_terms), 1)
    age = max(0, (datetime.now(UTC) - datetime.fromisoformat(memory.created_at)).total_seconds())
    return (
        0.45 * overlap
        + 0.25 * memory.importance
        + 0.2 * memory.confidence
        + 0.1 / (1 + age / 86400)
    )


def retrieve(memories: list[Memory], query: str, limit: int = 8) -> list[Memory]:
    return sorted(memories, key=lambda item: (-rank_memory(item, query), item.id))[:limit]


def contradictions(memories: list[Memory]) -> list[dict]:
    subjects = {}
    for memory in memories:
        if memory.subject and memory.value:
            subjects.setdefault(memory.subject, {}).setdefault(memory.value, []).append(memory.id)
    return [
        {"subject": subject, "alternatives": values}
        for subject, values in subjects.items()
        if len(values) > 1
    ]


def from_observation(tool: str, result: dict, source: str) -> Memory:
    ok = result["ok"]
    output = result.get("output", "")
    # A successful process result is an observation, not a model-inferred root cause.
    content = (
        f"{tool}: {'succeeded' if ok else 'failed'}\n{result.get('error') or ''}\n{output[:1400]}"
    )
    return Memory(
        kind="observation" if ok else "failure",
        content=content,
        sources=[source],
        importance=0.85 if tool in {"run_tests", "apply_patch"} else 0.55,
    )
