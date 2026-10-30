"""Versioned records shared by storage, analysis, and future adapters."""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def new_id() -> str:
    return uuid4().hex


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = 1


class Issue(Record):
    path: str
    reason: str


class Snapshot(Record):
    id: str = Field(default_factory=new_id)
    repository: str
    commit: str
    tree: str
    parser: str
    created_at: str = Field(default_factory=timestamp)
    status: Literal["complete", "partial"]
    python_files: int = Field(ge=0)
    indexed_files: int = Field(ge=0)
    excluded_paths: list[str]
    issues: list[Issue]
    source_kind: Literal["committed_git_tree"] = "committed_git_tree"
    max_file_bytes: int = Field(gt=0)
    max_total_bytes: int = Field(gt=0)


class Document(Record):
    path: str
    module: str
    source: str
    blob: str
    is_package: bool


class Symbol(Record):
    id: str = Field(default_factory=new_id)
    path: str
    name: str
    qualified_name: str
    kind: Literal["module", "class", "function", "async_function", "method"]
    line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    signature: str | None = None
    docstring: str | None = None


class Call(Record):
    id: str = Field(default_factory=new_id)
    path: str
    line: int = Field(ge=1)
    column: int = Field(ge=0)
    caller_id: str
    expression: str
    target_id: str | None
    resolution: Literal["resolved", "unknown"]
    reason: str
    method: Literal["python_ast_lexical_v1"] = "python_ast_lexical_v1"


class Index(Record):
    snapshot: Snapshot
    documents: list[Document]
    symbols: list[Symbol]
    calls: list[Call]


TaskStatus = Literal[
    "draft",
    "ready",
    "running",
    "validating",
    "review",
    "completed",
    "blocked",
    "awaiting_approval",
    "failed",
    "cancelled",
]


class Task(Record):
    id: str = Field(default_factory=new_id)
    objective: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    validation_needs: list[str] = Field(min_length=1)
    base_revision: str = Field(min_length=1)
    status: TaskStatus = "draft"
    owner: str | None = None
    plan_id: str | None = None
    local_id: str | None = None
    requirement_ids: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    affected_symbols: list[str] = Field(default_factory=list)


class Evidence(Record):
    id: str = Field(default_factory=new_id)
    task_id: str
    criterion: str
    artifact: str
    revision: str
    method: str
    environment: str
    outcome: Literal["pass", "fail", "inconclusive", "not_run"]
    limitations: list[str] = Field(default_factory=list)


class Event(Record):
    id: str = Field(default_factory=new_id)
    entity_id: str
    kind: str
    actor: str
    created_at: str = Field(default_factory=timestamp)


class PolicyDecision(Record):
    action: str
    target: str
    decision: Literal["allow", "require_approval", "deny"]
    reason: str


class QueryResult(Record):
    snapshot: Snapshot
    query: str
    items: list[Symbol | Call]
    truncated: bool = False
    total: int = Field(ge=0)
    warnings: list[str]


class ContextResult(Record):
    snapshot: Snapshot
    symbol: Symbol
    source: str
    truncated: bool
    warnings: list[str]


class StatusResult(Record):
    snapshot: Snapshot
    symbols: int
    resolved_calls: int
    unresolved_calls: int


class Transition(Record):
    """Only non-execution transitions are available in the foundation release."""

    before: TaskStatus
    after: TaskStatus

    @model_validator(mode="after")
    def supported_transition(self) -> "Transition":
        allowed = {
            "draft": {"ready", "blocked", "cancelled"},
            "ready": {"blocked", "cancelled"},
            "blocked": {"ready", "cancelled"},
        }
        if self.after not in allowed.get(self.before, set()):
            raise ValueError("transition requires a supported lifecycle and evidence gate")
        return self
