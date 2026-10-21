"""Keep background model calls visible without making them the execution's task."""

from __future__ import annotations

import json

from opentelemetry import trace as otel_trace

from .identity import conversation_name
from .telemetry import (
    complete_usage,
    dt,
    message,
    readback,
    spans_from_records,
    tool_error,
    tool_events,
    tracing,
    verify_readback,
)


def is_title_call(record):
    return any(
        m.get("role") == "system"
        and isinstance(m.get("content"), str)
        and m["content"].startswith("You are a title generator.")
        for m in record["request"].get("messages", [])
    )


def export_grouped(runtime, session, execution, directory):
    calls = runtime.calls(session)
    if not calls:
        raise RuntimeError("No provider requests to trace")
    tools = tool_events(directory / "events.jsonl")
    project = runtime.manifest["project"]
    saved = directory / "trace.json"
    main = [c for c in calls if not is_title_call(c)]
    auxiliary = [c for c in calls if is_title_call(c)]
    identity = calls[0]["identity"]
    entry = directory / "entry-message.txt"
    primary_messages = (
        [{"role": "user", "content": entry.read_text()}]
        if entry.exists()
        else [m for m in main[0]["request"].get("messages", []) if m.get("role") == "user"]
        if main
        else []
    )
    groups = [("execution", session, main, tools, primary_messages)]
    if auxiliary:
        groups.append(
            (
                "auxiliary_title",
                session + "-auxiliary",
                auxiliary,
                [],
                [
                    {
                        "role": "user",
                        "content": "OpenCode background title-generation calls for execution "
                        + session
                        + ". These are auxiliary requests, not the user's task.",
                    }
                ],
            )
        )
    if runtime.manifest.get("schema_version") == 4:
        from .telemetry_v4 import session_groups

        audit_path = directory / "opencode-audit.json"
        audit = json.loads(audit_path.read_text()) if audit_path.exists() else []
        groups = session_groups(session, calls, tools, primary_messages, audit)
    if saved.exists():
        receipt = json.loads(saved.read_text())
        if receipt["verified"]:
            return receipt
        if len(receipt["trace_groups"]) != receipt["expected_groups"]:
            raise RuntimeError(
                "Interrupted export before all group roots were recorded; preserve the partial export"
            )
    else:
        receipt = {
            "conversation_id": session,
            "trace_ids": [],
            "trace_groups": [],
            "expected_groups": len(groups),
            "expected_spans": len(calls) + len(tools) + len(groups),
            "verified": False,
            "url": f"https://forge.coreweave.com/agent-lens/{project}/conversations/{session}",
        }
        client = tracing.init(
            project, service_name="harness2-showcase", autopatch_integrations=False
        )
        try:
            for kind, conversation, records, parts, inputs in groups:
                spans = spans_from_records(records, parts)
                start = (
                    execution["started"]
                    if execution and kind == "execution"
                    else min(c["started"] for c in (records or calls))
                )
                end = (
                    execution["ended"]
                    if execution and kind == "execution"
                    else max(c["ended"] for c in (records or calls))
                )
                with tracing.Conversation(
                    conversation_id=conversation,
                    conversation_name=(
                        conversation_name(identity, auxiliary=kind == "auxiliary_title")
                        + (
                            " / Child session " + conversation.removeprefix(session + "-")
                            if kind == "child_execution"
                            else ""
                        )
                        if runtime.manifest.get("schema_version", 1) >= 3
                        else f"{identity.get('arm', 'qualification')} / {identity.get('task', '')} / {identity['role']}"
                        + (" / auxiliary title generation" if kind != "execution" else "")
                    ),
                    attributes={
                        **{
                            "harness2." + k: v
                            for k, v in identity.items()
                            if isinstance(v, (str, int, float, bool))
                        },
                        "harness2.execution_conversation": session,
                        "harness2.trace_kind": kind,
                    },
                ):
                    with tracing.Turn(
                        agent_name="harness2-" + identity["role"]
                        if kind != "auxiliary_title"
                        else "opencode-auxiliary-title",
                        agent_version=identity.get("harness_fingerprint", "unversioned"),
                        messages=[message(m) for m in inputs],
                        started_at=dt(start),
                        ended_at=dt(end),
                    ):
                        trace_id = format(
                            otel_trace.get_current_span().get_span_context().trace_id, "032x"
                        )
                        receipt["trace_ids"].append(trace_id)
                        receipt["trace_groups"].append(
                            {
                                "kind": kind,
                                "conversation_id": conversation,
                                "trace_id": trace_id,
                                "expected_spans": len(spans) + 1,
                                "url": f"https://forge.coreweave.com/agent-lens/{project}/conversations/{conversation}",
                            }
                        )
                        saved.write_text(json.dumps(receipt, indent=2))
                        for span, record in zip(spans[: len(records)], records):
                            with span:
                                span.set_attributes(
                                    {
                                        "harness2.provider_call_id": record["call_id"],
                                        "harness2.usage_available": complete_usage(
                                            (record.get("response") or {}).get("usage")
                                        ),
                                        "harness2.provider_usage_json": json.dumps(
                                            (record.get("response") or {}).get("usage")
                                        ),
                                        "harness2.auxiliary": kind == "auxiliary_title",
                                        **(
                                            {
                                                "harness2.provider_request_id": record[
                                                    "provider_request_id"
                                                ]
                                            }
                                            if record.get("provider_request_id")
                                            else {}
                                        ),
                                    }
                                )
                                if record.get("error"):
                                    span.record_error(RuntimeError(json.dumps(record["error"])))
                        for span, part in zip(spans[len(records) :], parts):
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
        finally:
            client.shutdown()
    rows = []
    for group in receipt["trace_groups"]:
        rows.extend(readback(project, group["trace_id"], group["expected_spans"]))
    verify_readback(rows, calls, tools)
    (directory / "stored-spans.json").write_text(json.dumps(rows, indent=2))
    receipt["verified"] = True
    saved.write_text(json.dumps(receipt, indent=2))
    return receipt
