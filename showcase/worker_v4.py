"""Persist process status and SQLite before collection; the host can retry collection."""

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

database = Path.home() / ".local/share/opencode/opencode.db"
status = database.parent / "process.json"
started = time.time()
child = subprocess.Popen(
    [
        "bun",
        "run",
        "--cwd",
        "/opt/opencode/packages/opencode",
        "--conditions=browser",
        "src/index.ts",
        *sys.argv[1:],
    ]
)
signal.signal(signal.SIGTERM, lambda *_: child.terminate())
code = child.wait()
record = {"child_exit_code": code, "started": started, "ended": time.time()}
status.write_text(json.dumps(record))
os.set_blocking(sys.stdout.fileno(), True)
os.set_blocking(sys.stderr.fileno(), True)
print(json.dumps({"type": "audit_exit", **record}), flush=True)
try:
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for row in db.execute("SELECT * FROM session ORDER BY time_created,id"):
            print(json.dumps({"type": "audit_session", "session": dict(row)}), flush=True)
        for row in db.execute("SELECT * FROM part ORDER BY time_created,id"):
            part = json.loads(row["data"])
            if part.get("type") == "tool":
                part.update(id=row["id"], sessionID=row["session_id"], messageID=row["message_id"])
                print(json.dumps({"type": "audit_tool", "part": part}), flush=True)
    print(json.dumps({"type": "audit_complete", "collection_status": "complete"}), flush=True)
except Exception as exc:
    print(json.dumps({"type": "audit_collection_error", "error": str(exc)}), flush=True)
    sys.exit(79)
sys.exit(code)
