"""One-shot provider worker: explicit source context in, unverified proposal artifacts out."""

import difflib
import hashlib
import json
import os
import time
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

import httpx
from pydantic import ValidationError

from worker_bees.provider_api import ProviderError, extract, make_request, object_json, send, usage
from worker_bees.provider_models import (
    ChangeProposal,
    ProviderConfig,
    ProviderReport,
    SourceFile,
    WorkerInput,
)
from worker_bees.workers import bounded_file
from worker_bees.workspaces import entries, git

MAX_INPUT_BYTES = 262_144


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def safe_path(value: str) -> str:
    parts = PurePosixPath(value).parts
    if (
        not parts
        or value != "/".join(parts)
        or PurePosixPath(value).is_absolute()
        or any(part in {".", ".."} or part.casefold() == ".git" for part in parts)
    ):
        raise ProviderError("unsafe_path")
    return value


def read_config(path: Path) -> tuple[ProviderConfig, str]:
    raw = bounded_file(path, 16_384)
    try:
        config = ProviderConfig.model_validate(object_json(raw))
    except ValidationError:
        raise ProviderError("invalid_provider_config") from None
    paths = config.context_paths + config.allowed_new_paths
    for name in paths:
        safe_path(name)
    if len({name.casefold() for name in paths}) != len(paths):
        raise ProviderError("duplicate_context_paths")
    return config, digest(raw)


def read_input(source: BinaryIO) -> WorkerInput:
    raw = source.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ProviderError("input_too_large")
    try:
        return WorkerInput.model_validate(object_json(raw))
    except ValidationError:
        raise ProviderError("invalid_worker_input") from None


def context(checkout: Path, envelope: WorkerInput, config: ProviderConfig) -> list[SourceFile]:
    snapshot = envelope.brief.snapshot
    if git(checkout, "rev-parse", "HEAD").decode().strip() != snapshot.commit:
        raise ProviderError("context_revision_changed")
    if git(checkout, "rev-parse", snapshot.commit + "^{tree}").decode().strip() != snapshot.tree:
        raise ProviderError("context_tree_changed")
    files = {name: (blob, size) for name, _, blob, size in entries(checkout, snapshot.commit)}
    result: list[SourceFile] = []
    total = 0
    for name in config.context_paths + config.allowed_new_paths:
        parts = PurePosixPath(safe_path(name)).parts
        if any((checkout.joinpath(*parts[:i])).is_symlink() for i in range(1, len(parts) + 1)):
            raise ProviderError("context_symlink")
        if name in config.allowed_new_paths:
            if name.casefold() in {path.casefold() for path in files} or (checkout / name).exists():
                raise ProviderError("addition_path_already_exists")
            continue
        if name not in files:
            raise ProviderError("context_not_committed")
        blob, size = files[name]
        total += size
        if total > config.max_context_bytes:
            raise ProviderError("context_too_large")
        raw = git(checkout, "cat-file", "blob", blob)
        if len(raw) != size or bounded_file(checkout / name, size) != raw:
            raise ProviderError("context_content_changed")
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            raise ProviderError("context_not_utf8") from None
        if "\x00" in text:
            raise ProviderError("context_not_text")
        result.append(SourceFile(path=name, sha256=digest(raw), content=text))
    return result


def prompt(envelope: WorkerInput, config: ProviderConfig, sources: list[SourceFile]) -> str:
    brief = envelope.brief
    # Deliberately omit host paths, lease identity, and process metadata from provider input.
    return encode(
        {
            "plan_id": brief.plan_id,
            "task": brief.task.model_dump(mode="json"),
            "specification": brief.specification,
            "constraints": brief.constraints,
            "feasibility_claims": brief.feasibility.model_dump(mode="json"),
            "requirements": [item.model_dump(mode="json") for item in brief.requirements],
            "decisions": [item.model_dump(mode="json") for item in brief.decisions],
            "source_revision": brief.snapshot.commit,
            "context": [item.model_dump(mode="json") for item in sources],
            "allowed_new_paths": config.allowed_new_paths,
        }
    ).decode("utf-8")


def validate_proposal(
    proposal: ChangeProposal,
    envelope: WorkerInput,
    config: ProviderConfig,
    sources: list[SourceFile],
) -> None:
    criteria = {item.id for item in envelope.brief.task.acceptance_criteria}
    checked: set[str] = set()
    for validation in proposal.validations:
        refs = set(validation.criterion_ids)
        if not refs <= criteria or len(refs) != len(validation.criterion_ids):
            raise ProviderError("invalid_criterion_links")
        checked.update(refs)
    if proposal.decision == "needs_context":
        if proposal.changes or not proposal.uncertainties:
            raise ProviderError("invalid_needs_context_proposal")
        return
    if not proposal.changes or not proposal.validations:
        raise ProviderError("empty_change_proposal")
    originals = {item.path: item for item in sources}
    requirements = set(envelope.brief.task.requirement_ids)
    linked: set[str] = set()
    changed: set[str] = set()
    total = 0
    for change in proposal.changes:
        safe_path(change.path)
        if change.path in changed:
            raise ProviderError("duplicate_change_paths")
        changed.add(change.path)
        if change.operation == "modify":
            original = originals.get(change.path)
            if original is None or original.sha256 != change.base_sha256:
                raise ProviderError("invalid_change_base")
            if change.new_content == original.content:
                raise ProviderError("unchanged_file_proposal")
        elif change.path not in config.allowed_new_paths or change.base_sha256:
            raise ProviderError("invalid_addition_scope")
        if "\x00" in change.new_content:
            raise ProviderError("nontext_change")
        total += len(change.new_content.encode("utf-8"))
        if total > 65_536:
            raise ProviderError("proposal_too_large")
        refs = set(change.requirement_ids)
        if not refs <= requirements or len(refs) != len(change.requirement_ids):
            raise ProviderError("invalid_requirement_links")
        linked.update(refs)
    if linked != requirements or checked != criteria:
        raise ProviderError("incomplete_declared_coverage")


def review_diff(proposal: ChangeProposal, sources: list[SourceFile]) -> str:
    originals = {item.path: item.content for item in sources}
    output: list[str] = []
    for change in proposal.changes:
        original = originals.get(change.path, "")
        lines = difflib.unified_diff(
            original.splitlines(keepends=True),
            change.new_content.splitlines(keepends=True),
            fromfile="a/" + change.path if change.operation == "modify" else "/dev/null",
            tofile="b/" + change.path,
            lineterm="\n",
        )
        for line in lines:
            output.append(
                line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
            )
    return "".join(output)


def _metadata(value: Any) -> str | None:
    return (
        value
        if isinstance(value, str) and len(value) <= 256 and not any(ord(c) < 32 for c in value)
        else None
    )


def run_provider(
    config_path: Path,
    source: BinaryIO,
    checkout: Path,
    *,
    dry_run: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> ProviderReport:
    config, config_hash = read_config(config_path)
    envelope = read_input(source)
    if digest(envelope.brief.specification.encode("utf-8")) != envelope.brief.specification_sha256:
        raise ProviderError("specification_hash_mismatch")
    if os.environ.get("WORKER_BEES_RUN_ID") != envelope.run_id:
        raise ProviderError("supervised_run_context_required")
    checkout = checkout.resolve(strict=True)
    if config_path.resolve().is_relative_to(checkout.parent):
        raise ProviderError("operator_config_must_be_outside_workspace_bundle")
    run_directory = checkout.parent / "runs" / envelope.run_id
    if (
        run_directory.is_symlink()
        or run_directory.parent.is_symlink()
        or not run_directory.is_dir()
    ):
        raise ProviderError("supervised_artifact_directory_required")
    directory = run_directory / "provider"
    # One attempt per artifact directory. Retries require a new operator-authorized worker run.
    directory.mkdir(mode=0o700, exist_ok=False)
    report = ProviderReport(
        provider=config.provider,
        requested_model=config.model,
        run_id=envelope.run_id,
        plan_id=envelope.brief.plan_id,
        task_id=envelope.brief.task.id,
        snapshot_id=envelope.brief.snapshot.id,
        base_revision=envelope.brief.snapshot.commit,
        submission_sha256=envelope.brief.submission_sha256,
        specification_sha256=envelope.brief.specification_sha256,
        config_sha256=config_hash,
        network_mode="dry_run"
        if dry_run
        else ("injected_transport" if transport is not None else "live"),
    )
    started = time.monotonic()
    report_file = directory / "report.json"

    def save() -> None:
        temporary = directory / "report.tmp"
        temporary.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(report_file)

    save()
    try:
        sources = context(checkout, envelope, config)
        report = report.model_copy(
            update={"context_sha256": {item.path: item.sha256 for item in sources}}
        )
        url, body = make_request(config, prompt(envelope, config, sources))
        request_bytes = encode(body)
        if len(request_bytes) > config.max_request_bytes:
            raise ProviderError("request_too_large")
        report = report.model_copy(update={"request_sha256": digest(request_bytes)})
        if dry_run:
            report = report.model_copy(update={"status": "dry_run"})
        else:
            name = config.api_key_env or (
                "OPENAI_API_KEY" if config.provider == "openai" else "GEMINI_API_KEY"
            )
            key = os.environ.get(name, "")
            if not key or len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
                raise ProviderError("credentials_missing_or_invalid")
            report = report.model_copy(update={"requests_attempted": 1})
            save()
            response = send(config, url, body, key, transport)
            report = report.model_copy(
                update={
                    "usage": usage(config.provider, response),
                    "response_id": _metadata(
                        response.get("id" if config.provider == "openai" else "responseId")
                    ),
                    "resolved_model": _metadata(
                        response.get("model" if config.provider == "openai" else "modelVersion")
                    ),
                }
            )
            text = extract(config, response)
            try:
                proposal = ChangeProposal.model_validate(object_json(text))
                validate_proposal(proposal, envelope, config, sources)
            except (ValueError, ValidationError, RecursionError):
                raise ProviderError("invalid_proposal", usage=report.usage) from None
            # A changed local context cannot turn into a silently stale proposal artifact.
            if context(checkout, envelope, config) != sources:
                raise ProviderError("context_content_changed", usage=report.usage)
            proposal_file = directory / "proposal.json"
            proposal_file.write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
            diff_file = directory / "proposal.diff"
            diff_file.write_text(review_diff(proposal, sources), encoding="utf-8", newline="")
            report = report.model_copy(
                update={
                    "status": "proposed"
                    if proposal.decision == "propose_changes"
                    else "needs_context",
                    "proposal_file": str(proposal_file),
                    "diff_file": str(diff_file),
                }
            )
    except ProviderError as error:
        update: dict[str, Any] = {
            "status": "failed",
            "error_code": error.code,
            "http_status": error.http_status,
        }
        if error.usage.total_tokens is not None or error.usage.input_tokens is not None:
            update["usage"] = error.usage
        report = report.model_copy(update=update)
    except (ValueError, OSError, RecursionError):
        report = report.model_copy(
            update={"status": "failed", "error_code": "local_validation_or_io_error"}
        )
    finally:
        report = report.model_copy(update={"latency_ms": int((time.monotonic() - started) * 1000)})
        save()
    return report
