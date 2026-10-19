"""Reconcile published costs and traces against independently captured execution records."""

from __future__ import annotations

import json
import tempfile
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from harness2.benchmarks.jobbench import _render_ctx, render_opencode_tree

from .runtime import ROOT, fingerprint
from .telemetry import tool_events, verify_readback


def rendered_harnesses(runtime):
    """Distinguish source-tree changes from artifacts actually loaded by OpenCode."""
    records = []
    with tempfile.TemporaryDirectory(prefix="h2-render-audit-") as temporary:
        root = Path(temporary)
        for path in sorted((runtime.state / "frozen").glob("*/*/freeze.json")):
            info = json.loads(path.read_text())
            baseline, candidate = root / "baseline", root / "candidate"
            context = _render_ctx(info["domain"])
            render_opencode_tree(
                ROOT / "third_party/jb_runner/harness_baseline_oc", baseline, context
            )
            render_opencode_tree(path.parent / "harness", candidate, context)
            baseline_hash, candidate_hash = fingerprint(baseline), fingerprint(candidate)
            records.append(
                {
                    "arm": info["arm"],
                    "domain": info["domain"],
                    "source_fingerprint": info["fingerprint"],
                    "rendered_fingerprint": candidate_hash,
                    "baseline_rendered_fingerprint": baseline_hash,
                    "same_rendered_harness_as_baseline": candidate_hash == baseline_hash,
                    "rendered_files": sorted(
                        str(p.relative_to(candidate)) for p in candidate.rglob("*") if p.is_file()
                    ),
                }
            )
    return records


def audit(runtime):
    budget = runtime.budget()
    if budget["unresolved"]:
        raise RuntimeError("Cannot certify a campaign with unresolved usage")
    total, spans, sessions, maximum_input = 0, 0, 0, 0
    collector_failures = []
    for call in budget["calls"]:
        path = runtime.state / "gateway/sessions" / call["session"] / (call["id"] + ".json")
        record = json.loads(path.read_text())
        if call["status"] == "rejected":
            assert 400 <= record["error"]["http_status"] < 500
            assert call["spent"] == 0
        else:
            usage = record["response"]["usage"]
            assert json.loads(call["usage"]) == usage
            assert record["request"]["model"] == record["response"]["model"] == call["model"]
            cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            price = runtime.manifest["prices_per_million"][call["model"]]
            micros = (
                (usage["prompt_tokens"] - cached) * Decimal(price["input"])
                + cached * Decimal(price["cached"])
                + usage["completion_tokens"] * Decimal(price["output"])
            ).to_integral_value(rounding=ROUND_CEILING)
            assert int(micros) == call["spent"] <= call["reserved"]
            maximum_input = max(maximum_input, usage["prompt_tokens"])
        total += call["spent"]
    assert total / 1e6 == budget["spent_usd"] <= float(runtime.manifest["budget_usd"])
    for directory in sorted((runtime.state / "gateway/sessions").iterdir()):
        if not directory.is_dir():
            continue
        calls = runtime.calls(directory.name)
        if not calls:
            continue
        execution = runtime.state / "executions" / directory.name
        receipt = json.loads((execution / "trace.json").read_text())
        assert receipt["verified"]
        stored = json.loads((execution / "stored-spans.json").read_text())
        tools = tool_events(execution / "events.jsonl")
        verify_readback(stored, calls, tools)
        assert len(stored) == receipt["expected_spans"]
        stderr = execution / "stderr.txt"
        if stderr.exists() and "BlockingIOError" in stderr.read_text():
            secondary = json.loads((execution / "opencode-audit.json").read_text())
            children = [
                event["session"]["id"]
                for event in secondary
                if event["type"] == "audit_session" and event["session"].get("parent_id")
            ]
            requested = {
                tool["id"]
                for call in calls
                for choice in (call.get("response") or {}).get("choices", [])
                if choice.get("finish_reason") == "tool_calls"
                for tool in choice.get("message", {}).get("tool_calls", [])
            }
            observed = {tool.get("callID") for tool in tools}
            assert not children, "Interrupted child-session audit requires separate reconciliation"
            assert requested <= observed, "Primary tool stream lacks a completed model tool request"
            collector_failures.append(
                {
                    "session": directory.name,
                    "reason": "Post-execution audit pipe was nonblocking; secondary export truncated.",
                    "completed_model_tool_requests": len(requested),
                    "all_requested_tool_ids_in_primary_stream": True,
                    "child_sessions": children,
                    "child_exit_code": None,
                }
            )
        spans += len(stored)
        sessions += 1
    frozen = []
    for path in sorted((runtime.state / "frozen").glob("*/*/freeze.json")):
        info = json.loads(path.read_text())
        assert fingerprint(path.parent / "harness") == info["fingerprint"]
        frozen.append(info)
    output_markers = list((runtime.state / "arms").glob("*/dataset/*/*/model_traj/*/showcase.json"))
    assert output_markers, "No captured output fingerprints found"
    for path in output_markers:
        info = json.loads(path.read_text())
        assert fingerprint(info["output_dir"]) == info["output_fingerprint"]
    result = {
        "provider_requests_reconciled": len(budget["calls"]),
        "trace_sessions_verified": sessions,
        "stored_spans_verified": spans,
        "output_fingerprints_verified": len(output_markers),
        "maximum_recorded_input_tokens": maximum_input,
        "reserved_input_token_ceiling": runtime.manifest["max_input_tokens"],
        "spent_usd": total / 1e6,
        "unresolved_requests": budget["unresolved"],
        "frozen_harnesses": frozen,
        "rendered_harnesses": rendered_harnesses(runtime),
        "post_execution_collector_failures": collector_failures,
        "scope": "Recorded provider requests and tool events; see pilot-notes.md for adaptation preflight-rejection coverage limits.",
    }
    (runtime.state / "validation.json").write_text(json.dumps(result, indent=2))
    (runtime.state / "cost-ledger.json").write_text(json.dumps(budget, indent=2))
    return result
