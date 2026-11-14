"""Offline fixture adapter for the sample repository only. Never calls a live model."""

import io
import json
import os
import sys

import httpx

from worker_bees.ai_worker import context, read_config, read_input, run_provider


def main():
    from pathlib import Path

    config_path = Path(sys.argv[1])
    raw = sys.stdin.buffer.read(262_145)
    envelope = read_input(io.BytesIO(raw))
    config, _ = read_config(config_path)
    sources = context(Path.cwd(), envelope, config)
    original = next(item for item in sources if item.path == "shop/pricing.py")
    proposed = original.content.replace(
        "    return max(value, 0)",
        "    if value < 0:\n        raise ValueError('negative price')\n    return value",
    )
    proposal = {
        "decision": "propose_changes",
        "summary": "Mock proposal: reject negative prices.",
        "changes": [
            {
                "path": original.path,
                "operation": "modify",
                "base_sha256": original.sha256,
                "new_content": proposed,
                "rationale": "Offline fixture response only.",
                "requirement_ids": ["R1", "R2"],
            }
        ],
        "validations": [
            {
                "criterion_ids": ["AC1", "AC2"],
                "method": "automated",
                "procedure": "Propose negative, zero, and positive cases for both functions.",
                "expected_result": "Negatives raise; other results are preserved.",
            }
        ],
        "assumptions": [],
        "uncertainties": ["Mocked output does not establish model quality."],
    }
    os.environ["OPENAI_API_KEY" if config.provider == "openai" else "GEMINI_API_KEY"] = (
        "offline-demo-not-a-key"
    )

    def respond(request):
        if config.provider == "openai":
            result = {
                "id": "mock-openai",
                "model": config.model,
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(proposal)}],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            }
        else:
            result = {
                "responseId": "mock-gemini",
                "modelVersion": config.model,
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"role": "model", "parts": [{"text": json.dumps(proposal)}]},
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 20,
                    "totalTokenCount": 30,
                },
            }
        return httpx.Response(200, json=result)

    report = run_provider(
        config_path, io.BytesIO(raw), Path.cwd(), transport=httpx.MockTransport(respond)
    )
    print(report.model_dump_json())
    return 2 if report.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
