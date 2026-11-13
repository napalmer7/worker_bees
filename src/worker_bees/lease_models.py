"""Bounded reservation requests, private storage, and public effective lease state."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from worker_bees.models import Record, new_id
from worker_bees.planning_models import Key

Hash = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
TTL = Annotated[int, Field(strict=True, ge=30, le=900)]


class ClaimRequest(Record):
    plan_id: Key
    task_id: Key
    owner: Key
    submission_sha256: Hash
    specification_sha256: Hash
    expected_approval_generation: int = Field(ge=1)
    expected_lease_generation: int = Field(ge=0)
    ttl_seconds: TTL = 120


class LeaseCredential(Record):
    plan_id: Key
    task_id: Key
    owner: Key
    lease_id: Key
    generation: int = Field(ge=1)
    token: Hash = Field(repr=False)


class LeaseContext(LeaseCredential):
    specification_sha256: Hash


class RenewRequest(LeaseContext):
    ttl_seconds: TTL = 120


class LeaseRecord(Record):
    id: str = Field(default_factory=new_id)
    plan_id: str
    task_id: str
    owner: str
    generation: int = Field(ge=1)
    approval_generation: int = Field(ge=1)
    snapshot_id: str
    base_revision: str
    submission_sha256: str
    specification_sha256: str
    token_sha256: str = Field(repr=False)
    created_at_ms: int
    renewed_at_ms: int
    expires_at_ms: int
    state: Literal["open", "released", "invalidated"] = "open"


class LeaseView(Record):
    plan_id: str
    task_id: str
    lease_id: str | None
    generation: int
    approval_generation: int | None
    owner: str | None
    effective_owner: str | None
    state: Literal["absent", "active", "expired", "released", "invalidated"]
    observed_at_ms: int
    created_at_ms: int | None
    renewed_at_ms: int | None
    expires_at_ms: int | None
    reasons: list[str]
    execution_available: Literal[False] = False


class LeaseGrant(Record):
    lease: LeaseView
    token: str = Field(repr=False)
    warnings: list[str]
