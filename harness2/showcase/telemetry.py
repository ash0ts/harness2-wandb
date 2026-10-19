"""Translate recorded executions with original timestamps, then verify server readback."""

from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
from coreweave.forge.agentlens import tracing
from opentelemetry import trace as otel_trace

_EXPORT_LOCK = threading.Lock()


def dt(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc)


def message(raw):
    parts = []
    content = raw.get("content")
    if isinstance(content, str):
        parts.append(tracing.TextPart(content=content))
    elif isinstance(content, list):
        # Preserve multipart input without claiming to have rendered non-text media.
        for part in content:
            if part.get("type") == "text":
                parts.append(tracing.TextPart(content=part.get("text", "")))
            elif part.get("type") == "image_url":
                parts.append(tracing.UriPart(uri=part["image_url"]["url"], modality="image"))
            else:
                raise ValueError(f"Unsupported provider message part: {part.get('type')}")
    reasoning = raw.get("reasoning_content") or raw.get("reasoning")
    if reasoning:
        parts.append(tracing.ReasoningPart(content=reasoning))
    for call in raw.get("tool_calls") or []:
        fn = call.get("function", {})
        parts.append(
            tracing.ToolCallPart(
                id=call.get("id", ""), name=fn.get("name", ""), arguments=fn.get("arguments", "")
            )
        )
    return tracing.Message(
        role=raw.get("role", "assistant"), parts=parts, tool_call_id=raw.get("tool_call_id", "")
    )


def tool_events(path):
    rows = []
    if not Path(path).exists():
        return rows
    seen = set()
    for line in Path(path).read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        part = event.get("part", {})
        if part.get("type") != "tool":
            continue
        state = part.get("state", {})
        call_id = part.get("callID")
        if not call_id or call_id in seen or state.get("status") not in ("completed", "error"):
            continue
        seen.add(call_id)
        rows.append(part)
    return rows


def spans_from_records(calls, tools):
    spans = []
    for record in calls:
        request, response = record["request"], record.get("response") or {}
        usage = response.get("usage") or {}
        choice = (response.get("choices") or [{}])[0]
        llm = tracing.LLM(
            model=request["model"],
            provider_name="wandb",
            response_model=response.get("model", ""),
            response_id=response.get("id", ""),
            input_messages=[
                message(m) for m in request.get("messages", []) if m.get("role") != "system"
            ],
            system_instructions=[
                m["content"]
                for m in request.get("messages", [])
                if m.get("role") == "system" and isinstance(m.get("content"), str)
            ],
            output_messages=[message(choice["message"])] if choice.get("message") else [],
            request_max_tokens=request.get("max_tokens"),
            started_at=dt(record["started"]),
            ended_at=dt(record["ended"]),
            finish_reasons=[choice["finish_reason"]] if choice.get("finish_reason") else [],
        )
        if usage:
            llm.usage = tracing.Usage(
                input_tokens=usage["prompt_tokens"],
                output_tokens=usage["completion_tokens"],
                total_tokens=usage.get("total_tokens"),
                cache_read_input_tokens=(usage.get("prompt_tokens_details") or {}).get(
                    "cached_tokens", 0
                ),
                reasoning_tokens=(usage.get("completion_tokens_details") or {}).get(
                    "reasoning_tokens", 0
                ),
            )
        spans.append(llm)
    for part in tools:
        state = part["state"]
        times = state.get("time", {})
        if "start" not in times or "end" not in times:
            raise ValueError("Tool execution has no original timestamps")
        tool = tracing.Tool(
            name=part["tool"],
            tool_call_id=part["callID"],
            arguments=json.dumps(state.get("input"), sort_keys=True),
            result=str(state.get("output", state.get("error", ""))),
            started_at=dt(times["start"] / 1000),
            ended_at=dt(times["end"] / 1000),
        )
        spans.append(tool)
    return spans


def readback(project, trace_id, expected_count, timeout=60):
    deadline = time.monotonic() + timeout
    auth = ("api", os.environ["WANDB_API_KEY"])
    while time.monotonic() < deadline:
        all_rows, offset = [], 0
        while True:
            response = httpx.post(
                "https://trace.wandb.ai/agents/spans/query",
                auth=auth,
                json={
                    "project_id": project,
                    "query": {
                        "$expr": {"$eq": [{"$getField": "trace_id"}, {"$literal": trace_id}]}
                    },
                    "include_details": True,
                    "include_costs": True,
                    "limit": 200,
                    "offset": offset,
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            rows = data.get("spans", [])
            all_rows.extend(rows)
            if len(all_rows) >= data.get("total_count", 0) or not rows:
                break
            offset += len(rows)
        if len(all_rows) == expected_count:
            return all_rows
        if len(all_rows) > expected_count:
            raise RuntimeError("Unexpected duplicate spans in backend")
        time.sleep(2)
    raise RuntimeError(f"Readback unverified: expected {expected_count}, found {len(all_rows)}")


def tool_error(part):
    state = part["state"]
    if state["status"] == "error":
        return str(state.get("error", "tool failed"))
    exit_code = state.get("metadata", {}).get("exit")
    if isinstance(exit_code, int) and exit_code != 0:
        return f"Process exited with status {exit_code}"
    return None


def verify_readback(rows, calls, tools):
    """Match each provider response and tool ID, with original times and usage."""
    llms = [r for r in rows if r.get("operation_name") == "chat"]
    stored_tools = [r for r in rows if r.get("operation_name") == "execute_tool"]
    if len(llms) != len(calls) or len(stored_tools) != len(tools):
        raise RuntimeError("Stored operation coverage differs from independent captures")
    for record in calls:
        response = record.get("response") or {}
        candidates = [
            r
            for r in llms
            if json.loads(r.get("raw_span_dump") or "{}")
            .get("attributes", {})
            .get("harness2", {})
            .get("provider_call_id")
            == record["call_id"]
        ]
        if not candidates and response.get("id"):
            candidates = [r for r in llms if r.get("response_id") == response["id"]]
        if len(candidates) != 1:
            raise RuntimeError("Provider response is missing or ambiguous in stored trace")
        row = candidates[0]
        usage = response.get("usage") or {}
        for stored, original in (
            ("input_tokens", "prompt_tokens"),
            ("output_tokens", "completion_tokens"),
        ):
            if usage and row.get(stored) != usage.get(original):
                raise RuntimeError("Stored usage differs from provider usage")
        if row.get("request_model") != record["request"]["model"]:
            raise RuntimeError("Stored request model differs")
        if response and row.get("response_model") != response.get("model"):
            raise RuntimeError("Stored response model differs")
        for field, source in (("started_at", "started"), ("ended_at", "ended")):
            stamp = datetime.fromisoformat(row[field]).replace(tzinfo=timezone.utc).timestamp()
            if abs(stamp - record[source]) > 0.001:
                raise RuntimeError("Stored model timestamp differs")
    for part in tools:
        candidates = [r for r in stored_tools if r.get("tool_call_id") == part["callID"]]
        if len(candidates) != 1 or candidates[0]["tool_name"] != part["tool"]:
            raise RuntimeError("Tool execution is missing or ambiguous")
        row = candidates[0]
        if json.loads(row["tool_call_arguments"]) != part["state"].get("input"):
            raise RuntimeError("Stored tool arguments differ")
        if tool_error(part) and row.get("status_code") != "ERROR":
            raise RuntimeError("Tool failure was lost during export")


def export_session(runtime, session, execution=None, directory=None):
    # The pinned SDK owns a process-global exporter. Serialize exports, not execution.
    with _EXPORT_LOCK:
        return _export_session(runtime, session, execution, directory)


def _export_session(runtime, session, execution=None, directory=None):
    directory = Path(directory or runtime.state / "executions" / session)
    directory.mkdir(parents=True, exist_ok=True)
    saved = directory / "trace.json"
    calls = runtime.calls(session)
    if not calls:
        raise RuntimeError("No provider requests to trace")
    tools = tool_events(directory / "events.jsonl")
    project = runtime.manifest["project"]
    if saved.exists():
        receipt = json.loads(saved.read_text())
        if receipt["verified"]:
            return receipt
        rows = readback(project, receipt["trace_ids"][0], receipt["expected_spans"])
        verify_readback(rows, calls, tools)
        (directory / "stored-spans.json").write_text(json.dumps(rows, indent=2))
        receipt["verified"] = True
        saved.write_text(json.dumps(receipt, indent=2))
        return receipt
    spans = spans_from_records(calls, tools)
    identity = calls[0]["identity"]
    start = execution["started"] if execution else min(c["started"] for c in calls)
    end = execution["ended"] if execution else max(c["ended"] for c in calls)
    client = tracing.init(project, service_name="harness2-showcase", autopatch_integrations=False)
    try:
        with tracing.Conversation(
            conversation_id=session,
            conversation_name=f"{identity.get('arm', 'smoke')} / {identity.get('task', 'qualification')} / {identity['role']}",
            attributes={
                "harness2." + k: v
                for k, v in identity.items()
                if isinstance(v, (str, int, float, bool))
            },
        ):
            with tracing.Turn(
                agent_name="harness2-" + identity["role"],
                agent_version=identity.get("harness_fingerprint", "unversioned"),
                messages=[
                    message(m)
                    for m in calls[0]["request"].get("messages", [])
                    if m.get("role") == "user"
                ],
                started_at=dt(start),
                ended_at=dt(end),
            ):
                trace_id = format(otel_trace.get_current_span().get_span_context().trace_id, "032x")
                receipt = {
                    "conversation_id": session,
                    "trace_ids": [trace_id],
                    "expected_spans": len(spans) + 1,
                    "verified": False,
                    "url": f"https://forge.coreweave.com/agent-lens/{project}/conversations/{session}",
                }
                # Save before ending any span. An interrupted export is read back, never replayed.
                saved.write_text(json.dumps(receipt, indent=2))
                for span, record in zip(spans[: len(calls)], calls):
                    with span:
                        span.set_attributes(
                            {
                                "harness2.provider_call_id": record["call_id"],
                                "harness2.usage_available": bool(
                                    (record.get("response") or {}).get("usage")
                                ),
                            }
                        )
                        if record.get("error"):
                            span.record_error(RuntimeError(json.dumps(record["error"])))
                for span, part in zip(spans[len(calls) :], tools):
                    with span:
                        span.set_attributes(
                            {
                                "harness2.opencode_session_id": part.get("sessionID", ""),
                                "harness2.tool_metadata": json.dumps(
                                    part["state"].get("metadata", {})
                                ),
                            }
                        )
                        if error := tool_error(part):
                            span.record_error(RuntimeError(error))
        if not client.force_flush():
            raise RuntimeError("Trace flush did not complete")
        rows = readback(project, trace_id, len(spans) + 1)
        verify_readback(rows, calls, tools)
        (directory / "stored-spans.json").write_text(json.dumps(rows, indent=2))
        receipt["verified"] = True
        saved.write_text(json.dumps(receipt, indent=2))
        return receipt
    finally:
        client.shutdown()


def diagnostics(rows, delivered):
    """Only observed operations. No correctness judgments or benchmark data."""
    errors, repeated, tools, latency = [], [], [], []
    signatures = Counter()
    evidence = {}
    for row in sorted(rows, key=lambda r: (r.get("started_at", ""), r.get("span_id", ""))):
        sid = row.get("span_id")
        if row.get("started_at") and row.get("ended_at"):
            elapsed = (
                datetime.fromisoformat(row["ended_at"]) - datetime.fromisoformat(row["started_at"])
            ).total_seconds()
            latency.append(
                {"span_id": sid, "operation": row.get("operation_name"), "seconds": elapsed}
            )
        if row.get("error_type") or row.get("status_code") in (2, "ERROR"):
            errors.append(
                {
                    "span_id": sid,
                    "error_type": row.get("error_type"),
                    "message": row.get("status_message", ""),
                }
            )
        name = row.get("tool_name")
        if name:
            arguments = row.get("tool_call_arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    pass
            signature = json.dumps([name, arguments], sort_keys=True)
            signatures[signature] += 1
            evidence.setdefault(signature, []).append(sid)
            tools.append({"span_id": sid, "name": name})
    for signature, count in signatures.items():
        if count > 1:
            repeated.append(
                {
                    "signature": json.loads(signature),
                    "count": count,
                    "span_ids": evidence[signature],
                }
            )
    return {
        "source": "Agent Lens stored spans",
        "errors": errors,
        "repeated_tool_calls": repeated,
        "tools": tools,
        "latency": latency,
        "retry_policy": "An identical tool call after an error is a retry candidate; changed arguments are not counted as retries.",
        "retry_candidates": [
            r for r in repeated if any(e["span_id"] in r["span_ids"][:-1] for e in errors)
        ],
        "deliverable_present": delivered,
        "interpretation": "Repeated calls are observations, not proof of a loop or failure.",
    }
