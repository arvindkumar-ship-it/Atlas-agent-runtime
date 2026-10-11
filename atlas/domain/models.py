from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return str(uuid4())


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}


class Task(StrictModel):
    repository_path: str
    instruction: str = Field(min_length=3, max_length=12000)
    demo_id: str | None = None
    context_tokens: int = Field(default=8192, ge=2048, le=65536)
    total_tokens: int = Field(default=180000, ge=2048, le=2_000_000)
    max_steps: int = Field(default=30, ge=3, le=100)
    priority: int = Field(default=100, ge=0, le=1000)
    approval_policy: Literal["manual", "workspace"] = "manual"
    context_mode: Literal[
        "full_history",
        "recent_window",
        "keyword_memory",
        "summary_memory",
        "atlas_context",
        "atlas_runtime",
        "atlas_full",
    ] = "atlas_full"


class PlanNode(StrictModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=300)
    dependencies: list[str] = Field(default_factory=list)
    status: Literal["pending", "running", "succeeded", "failed"] = "pending"


class Action(StrictModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    node_id: str | None = None


class Decision(StrictModel):
    type: Literal["plan", "tool", "parallel", "finish"]
    summary: str = Field(default="", max_length=6000)
    plan: list[PlanNode] = Field(default_factory=list, max_length=16)
    actions: list[Action] = Field(default_factory=list, max_length=4)
    evidence: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def shape(self):
        if self.type == "plan" and (not self.plan or self.actions):
            raise ValueError("plan decisions need nodes and no actions")
        if self.type == "tool" and len(self.actions) != 1:
            raise ValueError("tool decisions need exactly one action")
        if self.type == "parallel" and not 1 <= len(self.actions) <= 4:
            raise ValueError("parallel decisions need 1-4 actions")
        if self.type == "finish" and (not self.summary or self.actions):
            raise ValueError("finish needs a summary and no actions")
        return self


class ToolResult(StrictModel):
    ok: bool
    output: str = ""
    error: str | None = None
    exit_code: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False


class Memory(StrictModel):
    id: str = Field(default_factory=new_id)
    kind: Literal["observation", "fact", "hypothesis", "failure", "procedure"]
    content: str
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1, ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    subject: str | None = None
    value: str | None = None
    created_at: str = Field(default_factory=utc_now)


class Run(StrictModel):
    id: str = Field(default_factory=new_id)
    task: Task
    status: RunStatus = RunStatus.QUEUED
    sequence: int = 0
    workspace: str | None = None
    plan: list[PlanNode] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)
    memories: list[Memory] = Field(default_factory=list)
    pending: list[Action] = Field(default_factory=list)
    step_count: int = 0
    token_usage: int = 0
    report: dict[str, Any] | None = None
    answer: str | None = None
    error: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
