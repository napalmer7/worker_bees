"""Shared contracts for one-shot, proposal-only OpenAI and Gemini workers."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from worker_bees.models import Record
from worker_bees.planning_models import Key, Text
from worker_bees.worker_models import EnvironmentName
from worker_bees.workspaces import TaskBrief

RelativePath = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=512, pattern=r"^[A-Za-z0-9_.\-/]+$"),
]
ModelName = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
]
HashOrEmpty = Annotated[str, StringConstraints(strict=True, pattern=r"^([0-9a-f]{64})?$")]


class ProviderConfig(Record):
    provider: Literal["openai", "gemini"]
    model: ModelName
    context_paths: list[RelativePath] = Field(min_length=1, max_length=8)
    allowed_new_paths: list[RelativePath] = Field(default_factory=list, max_length=8)
    api_key_env: EnvironmentName | None = None
    max_output_tokens: int = Field(default=4096, ge=256, le=16_384)
    timeout_seconds: int = Field(default=60, ge=5, le=120)
    max_context_bytes: int = Field(default=32_768, ge=1024, le=65_536)
    max_request_bytes: int = Field(default=131_072, ge=4096, le=196_608)


class WorkerInput(Record):
    protocol_version: Literal[1]
    run_id: Key
    profile_id: Key
    profile_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    authority: Literal["operator_local_worker_invocation"]
    brief: TaskBrief


class SourceFile(Record):
    path: str
    sha256: str
    content: str


class ProposedChange(Record):
    path: RelativePath
    operation: Literal["modify", "add"]
    base_sha256: HashOrEmpty
    new_content: Annotated[str, StringConstraints(strict=True, max_length=32_768)]
    rationale: Text
    requirement_ids: list[Key] = Field(min_length=1, max_length=100)


class ProposedValidation(Record):
    criterion_ids: list[Key] = Field(min_length=1, max_length=20)
    method: Literal["automated", "manual", "inspection"]
    procedure: Text
    expected_result: Text


class ChangeProposal(Record):
    decision: Literal["propose_changes", "needs_context"]
    summary: Text
    changes: list[ProposedChange] = Field(max_length=8)
    validations: list[ProposedValidation] = Field(max_length=20)
    assumptions: list[Text] = Field(max_length=20)
    uncertainties: list[Text] = Field(max_length=20)


class TokenUsage(Record):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    basis: Literal["provider_reported"] = "provider_reported"


class ProviderReport(Record):
    provider: str
    requested_model: str
    run_id: str
    plan_id: str
    task_id: str
    snapshot_id: str
    base_revision: str
    submission_sha256: str
    specification_sha256: str
    config_sha256: str
    network_mode: Literal["live", "dry_run", "injected_transport"]
    request_sha256: str | None = None
    context_sha256: dict[str, str] = Field(default_factory=dict)
    status: Literal["preparing", "dry_run", "proposed", "needs_context", "failed"] = "preparing"
    error_code: str | None = None
    http_status: int | None = None
    response_id: str | None = None
    resolved_model: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    requests_attempted: int = 0
    latency_ms: int | None = None
    proposal_file: str | None = None
    diff_file: str | None = None
    task_status_changed: Literal[False] = False
    acceptance_verified: Literal[False] = False
    proposal_semantics: Literal["unverified_model_proposal"] = "unverified_model_proposal"
