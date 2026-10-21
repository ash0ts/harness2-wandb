"""Actual OpenCode session boundaries and inference-free export recovery."""

import json
import shutil
import time
from collections import defaultdict


def session_groups(session, calls, tools, primary_messages, audit):
    from .telemetry_v2 import is_title_call

    sessions = {r["session"]["id"]: r["session"] for r in audit if r["type"] == "audit_session"}
    roots = sorted(key for key, row in sessions.items() if not row.get("parent_id"))
    root = roots[0] if len(roots) == 1 else None
    records, parts = defaultdict(list), defaultdict(list)
    for call in calls:
        native = call.get("opencode_session_id") or root or session
        records[native, is_title_call(call)].append(call)
    for tool in tools:
        parts[tool.get("sessionID") or root or session, False].append(tool)
    groups = []
    keys = sorted(records.keys() | parts.keys(), key=lambda key: (key[0] != root, key))
    for native, auxiliary in keys:
        group_calls, group_parts = records[native, auxiliary], parts[native, auxiliary]
        primary = native in (root, session) and not auxiliary
        conversation = (
            session if primary else f"{session}-{native}" + ("-auxiliary" if auxiliary else "")
        )
        if auxiliary:
            inputs = [{"role": "user", "content": "OpenCode auxiliary title-generation request."}]
        elif primary:
            inputs = primary_messages
        else:
            # This is the child model's actual mission, preserved in its first request.
            inputs = (
                [
                    m
                    for m in group_calls[0]["request"].get("messages", [])
                    if m.get("role") == "user"
                ]
                if group_calls
                else []
            )
        kind = "auxiliary_title" if auxiliary else "execution" if primary else "child_execution"
        groups.append((kind, conversation, group_calls, group_parts, inputs))
    return groups


def export_recoverable(runtime, session, execution, directory):
    from .telemetry_v2 import export_grouped

    saved = directory / "trace.json"
    for attempt in range(3):
        try:
            return export_grouped(runtime, session, execution, directory)
        except Exception:
            if attempt == 2:
                raise
            # Retry readback first. If export never finished, preserve its receipt
            # and create a new export attempt; no model execution is involved.
            if saved.exists():
                receipt = json.loads(saved.read_text())
                partial = len(receipt.get("trace_groups", [])) != receipt.get("expected_groups")
                if partial or attempt == 1:
                    history = directory / "export-attempts"
                    history.mkdir(exist_ok=True)
                    shutil.move(saved, history / f"trace-{time.time_ns()}.json")
            time.sleep(2)
