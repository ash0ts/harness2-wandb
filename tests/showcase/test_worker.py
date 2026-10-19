import json
import os
import subprocess
import sys
from pathlib import Path


def test_nonblocking_child_cannot_truncate_large_post_execution_audit(tmp_path):
    binary = tmp_path / "bin/bun"
    binary.parent.mkdir()
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sqlite3\n"
        "from pathlib import Path\n"
        "p=Path.home()/'.local/share/opencode/opencode.db'\n"
        "p.parent.mkdir(parents=True)\n"
        "with sqlite3.connect(p) as db:\n"
        " db.execute('CREATE TABLE session(id, parent_id, time_created)')\n"
        " db.execute('INSERT INTO session VALUES (?, ?, ?)', ('root',None,1))\n"
        " db.execute('CREATE TABLE part(id, session_id, message_id, time_created, data)')\n"
        " for i in range(5):\n"
        "  data=json.dumps({'type':'tool','callID':str(i),'result':'x'*200000})\n"
        "  db.execute('INSERT INTO part VALUES (?, ?, ?, ?, ?)',(str(i),'root','m',i,data))\n"
        "os.set_blocking(1,False)\n"
        "os.set_blocking(2,False)\n"
        "raise SystemExit(7)\n"
    )
    binary.chmod(0o755)
    result = subprocess.run(
        [sys.executable, str(Path("showcase/worker.py").resolve())],
        env={**os.environ, "HOME": str(tmp_path / "home"), "PATH": str(binary.parent)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert result.returncode == 7
    assert result.stderr == ""
    assert records[0] == {"type": "audit_exit", "child_exit_code": 7}
    tools = [record["part"] for record in records if record["type"] == "audit_tool"]
    assert [tool["callID"] for tool in tools] == [str(i) for i in range(5)]
    assert all(len(tool["result"]) == 200000 for tool in tools)
