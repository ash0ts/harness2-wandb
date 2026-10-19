"""Run unmodified OpenCode, then export its persisted events, including child sessions."""

import json
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

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
# Bun can leave the inherited pipe nonblocking. Restore it after the child exits
# so large audit records cannot truncate the collector or replace its exit code.
os.set_blocking(sys.stdout.fileno(), True)
os.set_blocking(sys.stderr.fileno(), True)
print(json.dumps({"type": "audit_exit", "child_exit_code": code}), flush=True)
database = Path.home() / ".local/share/opencode/opencode.db"
if not database.exists():
    print("OpenCode audit database missing", file=sys.stderr)
    sys.exit(79)
if database.exists():
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
sys.exit(code)
