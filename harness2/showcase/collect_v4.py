"""Recover persisted OpenCode audit records without another agent execution."""

import json
import sqlite3

from .runtime import CampaignStop
from .workbuddy_preflight import save


def recover(directory, runtime):
    database = directory / "opencode-db/opencode.db"
    process = directory / "opencode-db/process.json"
    if not database.exists() or not process.exists():
        raise CampaignStop(f"Child completion is unproven for {directory}; do not rerun")
    child = json.loads(process.read_text())
    audit = [{"type": "audit_exit", **child}]
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        audit.extend(
            {"type": "audit_session", "session": dict(row)}
            for row in db.execute("SELECT * FROM session ORDER BY time_created,id")
        )
        for row in db.execute("SELECT * FROM part ORDER BY time_created,id"):
            part = json.loads(row["data"])
            if part.get("type") == "tool":
                part.update(id=row["id"], sessionID=row["session_id"], messageID=row["message_id"])
                audit.append({"type": "audit_tool", "part": part})
    audit.append({"type": "audit_complete", "collection_status": "complete"})
    raw = directory / "events.raw.jsonl"
    events = directory / "events.jsonl"
    if not raw.exists():
        raw.write_bytes(events.read_bytes() if events.exists() else b"")
    lines, seen = [], set()
    for line in raw.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            lines.append(line)
            continue
        if not event.get("type", "").startswith("audit_"):
            lines.append(line)
            seen.add(event.get("part", {}).get("callID"))
    for record in audit:
        part = record.get("part", {})
        if part.get("callID") and part["callID"] not in seen:
            lines.append(json.dumps(record))
            seen.add(part["callID"])
    events.write_text("\n".join(lines) + "\n")
    save(directory / "opencode-audit.json", audit)
    identity = json.loads((directory / "identity.json").read_text())
    result_path = directory / "execution.json"
    result = (
        json.loads(result_path.read_text())
        if result_path.exists()
        else {
            "session": identity["session"],
            "identity": identity,
            "started": child["started"],
            "ended": child["ended"],
            "exit_code": None,
            "timed_out": False,
            "provider_calls": len(runtime.calls(identity["session"])),
        }
    )
    result.update(
        child_exit_code=child["child_exit_code"],
        collection_status="complete",
        collection_recovered=True,
    )
    save(result_path, result)
    return result
