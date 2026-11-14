import hashlib
import io
import json
import sys
from pathlib import Path

import httpx
import pytest

from worker_bees.ai_worker import context, read_config, run_provider, validate_proposal
from worker_bees.provider_api import (
    ProviderError,
    extract,
    make_request,
    proposal_schema,
    send,
    usage,
)
from worker_bees.provider_models import ChangeProposal, ProviderConfig, WorkerInput
from worker_bees.storage import Store
from worker_bees.workers import Workers


@pytest.fixture
def provider_case(assignment, tmp_path, monkeypatch):
    _, database, index, workspaces, request, _, _ = assignment()
    bundle = tmp_path / "work"
    workspaces.prepare(request, bundle)
    run_id = "provider-test"
    directory = bundle / "runs" / run_id
    directory.mkdir(parents=True)
    envelope = WorkerInput(
        protocol_version=1,
        run_id=run_id,
        profile_id="test-profile",
        profile_sha256="0" * 64,
        authority="operator_local_worker_invocation",
        brief=workspaces.brief(request),
    )
    monkeypatch.setenv("WORKER_BEES_RUN_ID", run_id)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    def setup(provider="openai", **options):
        value = {
            "provider": provider,
            "model": "operator-selected-model",
            "context_paths": ["shop/pricing.py"],
            "allowed_new_paths": ["tests/test_pricing.py"],
        } | options
        path = tmp_path / "provider-config.json"
        path.write_text(json.dumps(value))
        config = ProviderConfig.model_validate(value)
        sources = context(bundle / "checkout", envelope, config)
        text = sources[0].content.replace(
            "    return max(value, 0)",
            "    if value < 0:\n        raise ValueError('negative price')\n    return value",
        )
        proposal = {
            "decision": "propose_changes",
            "summary": "Reject negatives and preserve nonnegative prices.",
            "changes": [
                {
                    "path": sources[0].path,
                    "operation": "modify",
                    "base_sha256": sources[0].sha256,
                    "new_content": text,
                    "rationale": "Both entry points use normalization.",
                    "requirement_ids": ["R1", "R2"],
                }
            ],
            "validations": [
                {
                    "criterion_ids": ["AC1", "AC2"],
                    "method": "automated",
                    "procedure": "Run negative, zero, and positive cases for both functions.",
                    "expected_result": "Negatives raise ValueError; other outputs stay unchanged.",
                }
            ],
            "assumptions": [],
            "uncertainties": [],
        }
        return path, config, sources, proposal

    return bundle, envelope, setup, database, index, request


def response(provider, proposal):
    if provider == "openai":
        return {
            "id": "resp-test",
            "model": "resolved-model",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(proposal)}],
                }
            ],
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "input_tokens_details": {"cached_tokens": 10},
                "output_tokens_details": {"reasoning_tokens": 20},
            },
        }
    return {
        "responseId": "gem-test",
        "modelVersion": "resolved-model",
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {"role": "model", "parts": [{"text": json.dumps(proposal)}]},
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 100,
            "candidatesTokenCount": 50,
            "totalTokenCount": 170,
            "cachedContentTokenCount": 10,
            "thoughtsTokenCount": 20,
        },
    }


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_providers_make_one_scoped_request_and_export_unapplied_patch(provider_case, provider):
    bundle, envelope, setup, database, _, _ = provider_case
    path, config, sources, proposal = setup(provider)
    calls = []
    before = database.read_bytes()
    original = (bundle / "checkout" / "shop" / "pricing.py").read_bytes()

    def handle(request):
        calls.append(request)
        body = json.loads(request.content)
        if provider == "openai":
            assert str(request.url) == "https://api.openai.com/v1/responses"
            assert request.headers["Authorization"] == "Bearer fake-openai-key"
            assert body["store"] is False
            assert body["max_output_tokens"] == 4096
            wire = body["text"]["format"]
            assert wire["strict"] and wire["type"] == "json_schema"
            prompt = body["input"]
        else:
            assert str(request.url).endswith("/models/operator-selected-model:generateContent")
            assert request.headers["x-goog-api-key"] == "fake-gemini-key"
            assert request.url.query == b""
            wire = body["generationConfig"]["responseFormat"]["text"]
            assert wire["mimeType"] == "APPLICATION_JSON"
            assert body["generationConfig"]["maxOutputTokens"] == 4096
            prompt = body["contents"][0]["parts"][0]["text"]
        assert envelope.brief.snapshot.repository not in prompt
        assert "fake-openai-key" not in prompt and "fake-gemini-key" not in prompt
        assert sources[0].content in json.loads(prompt)["context"][0]["content"]
        return httpx.Response(200, json=response(provider, proposal))

    result = run_provider(
        path,
        io.BytesIO(envelope.model_dump_json().encode()),
        bundle / "checkout",
        transport=httpx.MockTransport(handle),
    )
    assert len(calls) == 1 and result.requests_attempted == 1 and result.status == "proposed"
    assert result.request_sha256 == hashlib.sha256(calls[0].content).hexdigest()
    assert result.usage.input_tokens == 100 and result.usage.reasoning_tokens == 20
    assert result.resolved_model == "resolved-model" and result.latency_ms is not None
    assert not result.task_status_changed and not result.acceptance_verified
    assert (bundle / "checkout" / "shop" / "pricing.py").read_bytes() == original
    assert database.read_bytes() == before
    assert "raise ValueError" in Path(result.diff_file).read_text()
    assert "new_content" in Path(result.proposal_file).read_text()
    report = (bundle / "runs" / envelope.run_id / "provider" / "report.json").read_text()
    assert "fake-openai-key" not in report and "fake-gemini-key" not in report


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_dry_run_has_no_credentials_lookup_or_http_request(provider_case, provider, monkeypatch):
    bundle, envelope, setup, _, _, _ = provider_case
    path, _, _, _ = setup(provider)
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.delenv("GEMINI_API_KEY")

    def no_network(request):
        raise AssertionError("dry run tried a network request")

    result = run_provider(
        path,
        io.BytesIO(envelope.model_dump_json().encode()),
        bundle / "checkout",
        dry_run=True,
        transport=httpx.MockTransport(no_network),
    )
    assert result.status == "dry_run" and result.requests_attempted == 0
    assert result.usage.total_tokens is None and result.request_sha256
    with pytest.raises(FileExistsError):
        run_provider(
            path, io.BytesIO(envelope.model_dump_json().encode()), bundle / "checkout", dry_run=True
        )


@pytest.mark.parametrize("status", [302, 400, 401, 429, 500])
def test_http_errors_do_not_retry_redirect_or_echo_vendor_body(provider_case, status):
    bundle, envelope, setup, _, _, _ = provider_case
    path, _, _, _ = setup()
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={"Location": "https://evil.invalid"},
            text="fake-openai-key PRIVATE_SOURCE_ECHO",
        )

    result = run_provider(
        path,
        io.BytesIO(envelope.model_dump_json().encode()),
        bundle / "checkout",
        transport=httpx.MockTransport(handle),
    )
    assert len(calls) == 1 and result.http_status == status
    assert result.status == "failed" and result.error_code == "http_error"
    assert "PRIVATE_SOURCE_ECHO" not in result.model_dump_json()


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_truncation_or_refusal_preserves_reported_usage(provider_case, provider):
    bundle, envelope, setup, _, _, _ = provider_case
    path, _, _, proposal = setup(provider)
    raw = response(provider, proposal)
    if provider == "openai":
        raw["status"] = "incomplete"
    else:
        raw["candidates"][0]["finishReason"] = "MAX_TOKENS"
    result = run_provider(
        path,
        io.BytesIO(envelope.model_dump_json().encode()),
        bundle / "checkout",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=raw)),
    )
    assert result.status == "failed" and result.usage.input_tokens == 100
    assert result.proposal_file is None


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_needs_context_is_visible_and_has_no_changes(provider_case, provider):
    bundle, envelope, setup, _, _, _ = provider_case
    path, _, _, proposal = setup(provider)
    proposal.update(
        decision="needs_context",
        changes=[],
        validations=[],
        uncertainties=["Missing consumer contract."],
    )
    result = run_provider(
        path,
        io.BytesIO(envelope.model_dump_json().encode()),
        bundle / "checkout",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response(provider, proposal))
        ),
    )
    assert result.status == "needs_context"
    assert Path(result.diff_file).read_text() == ""


@pytest.mark.parametrize(
    "change",
    [
        {"path": "../outside.py"},
        {"path": ".git/config"},
        {"path": "unselected.py"},
        {"base_sha256": "0" * 64},
        {"operation": "add", "base_sha256": ""},
        {"requirement_ids": ["UNKNOWN"]},
        {"new_content": "x\x00y"},
    ],
)
def test_untrusted_changes_are_rejected_without_applying_anything(provider_case, change):
    bundle, envelope, setup, _, _, _ = provider_case
    path, _, _, proposal = setup()
    proposal["changes"][0].update(change)
    result = run_provider(
        path,
        io.BytesIO(envelope.model_dump_json().encode()),
        bundle / "checkout",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response("openai", proposal))
        ),
    )
    assert result.status == "failed" and result.error_code == "invalid_proposal"
    assert result.proposal_file is None


def test_new_test_file_is_only_a_reviewable_proposal(provider_case):
    bundle, envelope, setup, _, _, _ = provider_case
    path, _, _, proposal = setup()
    proposal["changes"].append(
        {
            "path": "tests/test_pricing.py",
            "operation": "add",
            "base_sha256": "",
            "new_content": "def test_placeholder():\n    assert True\n",
            "rationale": "Proposed regression coverage.",
            "requirement_ids": ["R1", "R2"],
        }
    )
    result = run_provider(
        path,
        io.BytesIO(envelope.model_dump_json().encode()),
        bundle / "checkout",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response("openai", proposal))
        ),
    )
    assert result.status == "proposed"
    assert not (bundle / "checkout" / "tests").exists()
    assert "+++ b/tests/test_pricing.py" in Path(result.diff_file).read_text()


@pytest.mark.parametrize("mode", ["coverage", "criteria", "extra", "noop", "duplicate"])
def test_shared_business_validation_exposes_gaps(provider_case, mode):
    _, envelope, setup, _, _, _ = provider_case
    _, config, sources, proposal = setup()
    if mode == "coverage":
        proposal["changes"][0]["requirement_ids"] = ["R1"]
    elif mode == "criteria":
        proposal["validations"][0]["criterion_ids"] = ["UNKNOWN"]
    elif mode == "extra":
        proposal["completed"] = True
    elif mode == "noop":
        proposal["changes"][0]["new_content"] = sources[0].content
    else:
        proposal["changes"] *= 2
    with pytest.raises(ValueError):
        validate_proposal(ChangeProposal.model_validate(proposal), envelope, config, sources)


@pytest.mark.parametrize("mode", ["dirty", "symlink", "oversize", "new_exists"])
def test_context_errors_prevent_billing_attempt(provider_case, mode):
    bundle, envelope, setup, _, _, _ = provider_case
    path, _, _, _ = setup()
    source = bundle / "checkout" / "shop" / "pricing.py"
    if mode == "dirty":
        source.write_text("# uncommitted mutation\n")
    elif mode == "symlink":
        source.unlink()
        source.symlink_to("/etc/passwd")
    elif mode == "oversize":
        value = json.loads(path.read_text())
        value["context_paths"] = ["shop/pricing.py", "shop/service.py"]
        value["max_context_bytes"] = 1024
        path.write_text(json.dumps(value))
        # A changed selected file still fails before any request, regardless of which bound fails.
        source.write_text("x" * 1025)
    else:
        (bundle / "checkout" / "tests").mkdir()
        (bundle / "checkout" / "tests" / "test_pricing.py").write_text("existing")

    def no_network(request):
        raise AssertionError("invalid context tried a network request")

    result = run_provider(
        path,
        io.BytesIO(envelope.model_dump_json().encode()),
        bundle / "checkout",
        transport=httpx.MockTransport(no_network),
    )
    assert result.status == "failed" and result.requests_attempted == 0


@pytest.mark.parametrize("raw", [b"not json", b"[]", b'{"x":1,"x":2}', b'{"x":NaN}'])
def test_invalid_response_json_is_sanitized(provider_case, raw):
    bundle, envelope, setup, _, _, _ = provider_case
    path, _, _, _ = setup()
    result = run_provider(
        path,
        io.BytesIO(envelope.model_dump_json().encode()),
        bundle / "checkout",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=raw)),
    )
    assert result.error_code == "invalid_json" and result.status == "failed"


def test_timeout_and_large_responses_are_bounded(provider_case, monkeypatch):
    import worker_bees.provider_api as module

    bundle, envelope, setup, _, _, _ = provider_case
    path, config, _, _ = setup()
    url, body = make_request(config, "prompt")

    def timeout(request):
        raise httpx.ReadTimeout("fake-openai-key PRIVATE_SOURCE", request=request)

    with pytest.raises(ProviderError, match="timeout") as failure:
        send(config, url, body, "secret", httpx.MockTransport(timeout))
    assert "secret" not in str(failure.value)
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 8)
    with pytest.raises(ProviderError, match="response_too_large"):
        send(
            config,
            url,
            body,
            "secret",
            httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 9)),
        )


def test_wire_schema_has_required_closed_objects_and_no_local_constraints():
    schema = proposal_schema()

    def check(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert set(value["required"]) == set(value["properties"])
                assert value["additionalProperties"] is False
                assert "schema_version" not in value["properties"]
            assert not {"default", "pattern", "minLength", "maxLength"}.intersection(value)
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(schema)
    assert "ProposedChange" in schema["$defs"]


def test_unknown_usage_is_not_reported_as_zero():
    for provider in ["openai", "gemini"]:
        assert usage(provider, {}).total_tokens is None
    assert (
        usage("openai", {"usage": {"input_tokens": True, "output_tokens": -1}}).input_tokens is None
    )


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_refusals_and_tool_outputs_are_rejected(provider_case, provider):
    _, _, setup, _, _, _ = provider_case
    _, config, _, proposal = setup(provider)
    raw = response(provider, proposal)
    if provider == "openai":
        raw["output"][0]["content"] = [{"type": "refusal", "refusal": "no"}]
    else:
        raw["candidates"][0]["content"]["parts"] = [{"functionCall": {"name": "run_shell"}}]
    with pytest.raises(ProviderError):
        extract(config, raw)


def test_real_supervisor_can_run_adapter_dry_without_keys(provider_case, tmp_path, monkeypatch):
    bundle, _, setup, database, index, request = provider_case
    path, _, _, _ = setup()
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.delenv("GEMINI_API_KEY")
    profile = tmp_path / "dry-profile.json"
    profile.write_text(
        json.dumps(
            {
                "id": "dry-ai",
                "argv": [sys.executable, "-m", "worker_bees", "ai-worker", str(path), "--dry-run"],
            }
        )
    )
    record = Workers(Store(database, writable=True), index).run(request, bundle, profile)
    assert record.status == "exited" and record.exit_code == 0
    report = json.loads((Path(record.directory) / "provider" / "report.json").read_text())
    assert report["status"] == "dry_run" and report["requests_attempted"] == 0


def test_provider_config_cannot_set_endpoint_or_escape_paths(provider_case):
    _, _, setup, _, _, _ = provider_case
    path, _, _, _ = setup()
    base = json.loads(path.read_text())
    for update in [
        {"base_url": "https://evil.invalid"},
        {"model": "../escape"},
        {"context_paths": ["../escape"]},
        {"context_paths": ["a.py", "A.py"]},
    ]:
        path.write_text(json.dumps(base | update))
        with pytest.raises(ValueError):
            read_config(path)


def test_generated_diff_is_checkable_without_applying_it(provider_case):
    from worker_bees.workspaces import git

    bundle, envelope, setup, _, _, _ = provider_case
    path, _, _, proposal = setup()
    proposal["changes"][0]["new_content"] = proposal["changes"][0]["new_content"].rstrip("\n")
    result = run_provider(
        path,
        io.BytesIO(envelope.model_dump_json().encode()),
        bundle / "checkout",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response("openai", proposal))
        ),
    )
    assert result.status == "proposed" and result.network_mode == "injected_transport"
    git(bundle / "checkout", "apply", "--check", result.diff_file)


def test_request_budget_is_checked_before_network(provider_case):
    bundle, envelope, setup, _, _, _ = provider_case
    path, _, _, _ = setup(max_request_bytes=4096)
    value = envelope.model_dump()
    value["brief"]["specification"] = "Requirements. " * 1500
    value["brief"]["specification_sha256"] = hashlib.sha256(
        value["brief"]["specification"].encode()
    ).hexdigest()
    result = run_provider(
        path,
        io.BytesIO(json.dumps(value).encode()),
        bundle / "checkout",
        transport=httpx.MockTransport(lambda request: pytest.fail("network attempted")),
    )
    assert result.status == "failed" and result.requests_attempted == 0
    assert result.error_code == "request_too_large"


def test_missing_credentials_fail_without_attempt_or_secret_echo(provider_case, monkeypatch):
    bundle, envelope, setup, _, _, _ = provider_case
    path, _, _, _ = setup()
    monkeypatch.delenv("OPENAI_API_KEY")
    result = run_provider(
        path,
        io.BytesIO(envelope.model_dump_json().encode()),
        bundle / "checkout",
        transport=httpx.MockTransport(lambda request: pytest.fail("network attempted")),
    )
    assert result.status == "failed" and result.requests_attempted == 0
    assert result.error_code == "credentials_missing_or_invalid"


def test_context_changed_during_request_is_reported_with_usage(provider_case):
    bundle, envelope, setup, _, _, _ = provider_case
    path, _, _, proposal = setup()

    def handle(request):
        (bundle / "checkout" / "shop" / "pricing.py").write_text("# concurrent operator edit\n")
        return httpx.Response(200, json=response("openai", proposal))

    result = run_provider(
        path,
        io.BytesIO(envelope.model_dump_json().encode()),
        bundle / "checkout",
        transport=httpx.MockTransport(handle),
    )
    assert result.status == "failed" and result.error_code == "context_content_changed"
    assert result.usage.total_tokens == 150 and result.proposal_file is None


def test_needs_context_cannot_invent_validation_links(provider_case):
    _, envelope, setup, _, _, _ = provider_case
    _, config, sources, proposal = setup()
    proposal.update(decision="needs_context", changes=[], uncertainties=["Need more context."])
    proposal["validations"][0]["criterion_ids"] = ["INVENTED"]
    with pytest.raises(ProviderError, match="invalid_criterion_links"):
        validate_proposal(ChangeProposal.model_validate(proposal), envelope, config, sources)


def test_gemini_unspecified_block_and_thinking_metadata_are_handled(provider_case):
    _, _, setup, _, _, _ = provider_case
    _, config, _, proposal = setup("gemini")
    raw = response("gemini", proposal)
    raw["promptFeedback"] = {"blockReason": "BLOCK_REASON_UNSPECIFIED"}
    raw["candidates"][0]["content"]["parts"].insert(0, {"thought": True, "text": "not persisted"})
    assert json.loads(extract(config, raw)) == proposal
    raw["candidates"][0]["content"]["parts"][0]["functionCall"] = {"name": "run_shell"}
    with pytest.raises(ProviderError, match="unexpected_output"):
        extract(config, raw)


def test_mistaken_plaintext_key_in_config_is_not_echoed(provider_case):
    _, _, setup, _, _, _ = provider_case
    path, _, _, _ = setup()
    config = json.loads(path.read_text()) | {"api_key": "SECRET-MUST-NOT-BE-LOGGED"}
    path.write_text(json.dumps(config))
    with pytest.raises(ProviderError) as failure:
        read_config(path)
    assert "SECRET-MUST-NOT-BE-LOGGED" not in str(failure.value)
