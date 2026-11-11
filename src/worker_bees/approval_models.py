"""Operator approval records and advisory assignment policy, never execution authority."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from worker_bees.models import PolicyDecision, Record, Task, new_id, timestamp
from worker_bees.planning_models import Key, Text

Digest = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]


class ApprovalRequest(Record):
    plan_id: Key
    task_id: Key
    submission_sha256: Digest
    specification_sha256: Digest
    expected_generation: int = Field(ge=0)
    action: Literal["approve", "revoke"]
    reason: Text


class TaskApproval(Record):
    id: str = Field(default_factory=new_id)
    plan_id: str
    task_id: str
    snapshot_id: str
    base_revision: str
    submission_sha256: str
    specification_sha256: str
    generation: int = Field(ge=1)
    action: Literal["approve", "revoke"]
    reason: str
    actor: Literal["local_operator"] = "local_operator"
    created_at: str = Field(default_factory=timestamp)
    scope: Literal["prepare_task_assignment"] = "prepare_task_assignment"


class TaskPolicy(Record):
    plan_id: str
    task_id: str
    snapshot_id: str
    base_revision: str
    submission_sha256: str
    specification_sha256: str
    task_status: str
    approval_generation: int
    approval: TaskApproval | None
    policy: PolicyDecision
    blockers: list[str]
    warnings: list[str]
    execution_available: Literal[False] = False


class ApprovalReceipt(Record):
    approval: TaskApproval
    task: Task
    policy: TaskPolicy
