"""Operator-selected local worker profiles and durable process outcomes."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from worker_bees.models import Record, new_id
from worker_bees.planning_models import Key

Argument = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=4096, pattern=r"^[^\x00]+$")
]
EnvironmentName = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
]


class WorkerProfile(Record):
    id: Key
    argv: list[Argument] = Field(min_length=1, max_length=32)
    inherit_environment: list[EnvironmentName] = Field(default_factory=list, max_length=32)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    heartbeat_seconds: int = Field(default=10, ge=1, le=30)
    lease_ttl_seconds: int = Field(default=120, ge=30, le=900)
    terminate_grace_seconds: int = Field(default=2, ge=1, le=10)
    max_output_bytes: int = Field(default=65_536, ge=1024, le=1_048_576)


RunStatus = Literal[
    "starting",
    "running",
    "exited",
    "failed",
    "cancelled",
    "timed_out",
    "lease_lost",
    "output_limit",
    "interrupted",
    "supervision_failed",
    "stop_unconfirmed",
]


class WorkerRun(Record):
    id: str = Field(default_factory=new_id)
    plan_id: str
    task_id: str
    snapshot_id: str
    base_revision: str
    workspace_id: str
    workspace: str
    lease_id: str
    lease_generation: int
    owner: str
    profile_id: str
    profile_sha256: str
    directory: str
    status: RunStatus = "starting"
    active: bool = True
    cancel_requested: bool = False
    cancel_reason: str | None = None
    supervisor_pid: int
    child_pid: int | None = None
    started_at_ms: int
    observed_at_ms: int
    heartbeat_seconds: int = 10
    finished_at_ms: int | None = None
    exit_code: int | None = None
    leader_reaped: bool = False
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    error: str | None = None
    authority: Literal["operator_local_worker_invocation"] = "operator_local_worker_invocation"
    task_status_changed: Literal[False] = False
    acceptance_verified: Literal[False] = False


class WorkerRunInspection(Record):
    run: WorkerRun
    last_observed_age_ms: int
    needs_reconciliation: bool
    process_liveness_verified: Literal[False] = False
