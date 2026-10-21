import json
import sqlite3
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness2.showcase.code_study import CodeAdapter, StudyAdapter
from harness2.showcase.collect_v4 import recover
from harness2.showcase.protocol import (
    canonical_affected_files,
    materialize_evidence,
    tree_hashes,
    verify_executed_harness,
)
from harness2.showcase.runtime import CampaignStop, Runtime, fingerprint, prepare_worker_permissions
from harness2.showcase.runtime_v4 import StudyRuntime, restore
from harness2.showcase.telemetry_v4 import export_recoverable
from harness2.showcase.workbuddy_preflight import build_context


def manifest():
    return json.loads(Path("showcase/study-v4.json").read_text())


def test_repository_roundtrip_preserves_symlinks_and_executable_modes(tmp_path):
    import shutil

    original, durable, restored = [tmp_path / name for name in ("original", "durable", "restored")]
    original.mkdir()
    (original / "script").write_text("#!/bin/sh\necho unchanged\n")
    (original / "script").chmod(0o755)
    (original / "link").symlink_to("script")
    subprocess.run(["git", "init", "-q", str(original)], check=True)
    subprocess.run(["git", "add", "-A"], cwd=original, check=True)
    prepare_worker_permissions(original, v4=True)
    shutil.copytree(original, durable, symlinks=True)
    prepare_worker_permissions(durable, v4=True)
    restored.mkdir()
    restore(durable, restored)
    assert (restored / "link").is_symlink()
    assert (restored / "link").readlink() == Path("script")
    assert (restored / "script").stat().st_mode & 0o111
    subprocess.run(["git", "diff", "--exit-code"], cwd=restored, check=True)
    (restored / "escape").symlink_to("../outside")
    with pytest.raises(RuntimeError, match="escapes"):
        prepare_worker_permissions(restored, v4=True)


def test_v4_fingerprints_and_affected_files_detect_executable_bit_changes(tmp_path):
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\necho unchanged\n")
    script.chmod(0o644)
    content, before = fingerprint(tmp_path), fingerprint(tmp_path, executable_modes=True)
    files = tree_hashes(tmp_path, executable_modes=True)
    script.chmod(0o755)
    assert fingerprint(tmp_path) == content
    assert fingerprint(tmp_path, executable_modes=True) != before
    assert tree_hashes(tmp_path, executable_modes=True)["run.sh"] != files["run.sh"]


def test_source_match_cannot_hide_wrong_rendered_harness():
    record = {"source_fingerprint": "source", "rendered_fingerprint": "expected"}
    verify_executed_harness({"identity": dict(record)}, record)
    with pytest.raises(CampaignStop, match="rendered harness"):
        verify_executed_harness({"identity": {**record, "rendered_fingerprint": "other"}}, record)


def test_completed_execution_resumes_without_launch_and_preserves_deletions(tmp_path, monkeypatch):
    runtime = StudyRuntime(manifest(), tmp_path / "state")
    work = tmp_path / "work"
    work.mkdir()
    (work / "stale.txt").write_text("delete me")
    launches = []

    def launch(self, directory, prompt, **identity):
        launches.append(identity)
        (directory / "stale.txt").unlink()
        (directory / "changed.txt").write_text("delivered")
        execution = self.state / "executions/session"
        execution.mkdir(parents=True)
        identity["session"] = "session"
        (execution / "identity.json").write_text(json.dumps(identity))
        result = {"session": "session", "collection_status": "complete", "child_exit_code": 0}
        (execution / "execution.json").write_text(json.dumps(result))
        return result, execution

    monkeypatch.setattr(Runtime, "run_agent", launch)
    monkeypatch.setattr(runtime, "wait_calls", lambda session: [])
    kwargs = dict(phase="adapt", role="solver", task="task", arm="harness2", adaptation_replicate=1)
    first = runtime.run_agent(work, "mission", **kwargs)
    (work / "stale.txt").write_text("initial input restored by caller")
    second = runtime.run_agent(work, "mission", **kwargs)
    assert first == second
    assert len(launches) == 1
    assert not (work / "stale.txt").exists()
    assert (work / "changed.txt").read_text() == "delivered"


def test_collector_recovers_child_failures_and_child_tools(tmp_path):
    execution = tmp_path / "execution"
    database = execution / "opencode-db/opencode.db"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE session(id, parent_id, time_created)")
        db.execute("INSERT INTO session VALUES ('root',NULL,1),('child','root',2)")
        db.execute("CREATE TABLE part(id,session_id,message_id,time_created,data)")
        db.execute(
            "INSERT INTO part VALUES ('part','child','message',3,?)",
            (
                json.dumps(
                    {
                        "type": "tool",
                        "callID": "call",
                        "state": {"status": "error", "error": "failure"},
                    }
                ),
            ),
        )
    (database.parent / "process.json").write_text(
        json.dumps({"started": 1, "ended": 3, "child_exit_code": 7})
    )
    (execution / "identity.json").write_text(json.dumps({"session": "root"}))
    (execution / "events.jsonl").write_text('{"type":"text","part":{"text":"partial"}}\n')
    result = recover(execution, SimpleNamespace(calls=lambda session: []))
    assert result["child_exit_code"] == 7
    assert result["collection_status"] == "complete"
    events = (execution / "events.jsonl").read_text()
    assert '"sessionID": "child"' in events
    assert '"error": "failure"' in events
    assert "partial" in (execution / "events.raw.jsonl").read_text()


def test_export_retry_does_not_invoke_inference(tmp_path, monkeypatch):
    calls = []

    def export(runtime, session, execution, directory):
        calls.append(1)
        if len(calls) == 1:
            (directory / "trace.json").write_text(
                json.dumps({"trace_groups": [], "expected_groups": 2})
            )
            raise RuntimeError("interrupted exporter")
        return {"verified": True}

    monkeypatch.setattr("harness2.showcase.telemetry_v2.export_grouped", export)
    monkeypatch.setattr("harness2.showcase.telemetry_v4.time.sleep", lambda _: None)
    assert export_recoverable(object(), "session", None, tmp_path)["verified"]
    assert len(calls) == 2
    assert len(list((tmp_path / "export-attempts").glob("*.json"))) == 1


def test_isolation_namespaces_and_cross_study_evidence(tmp_path):
    runtimes = [StudyRuntime(manifest(), tmp_path / "state", s) for s in ("code", "jobbench")]
    roots = {
        cls(rt, arm, trial).root
        for rt, cls in zip(runtimes, (CodeAdapter, StudyAdapter))
        for arm in ("baseline", "harness2", "diagnostics")
        for trial in (1, 2, 3)
    }
    assert len(roots) == 18
    cwd = tmp_path / "improver"
    (cwd / "context").mkdir(parents=True)
    session = "a" * 32
    (cwd / "context/evidence.md").write_text("SHOWCASE_SESSION:" + session)
    source = runtimes[0].state / "executions" / session
    source.mkdir(parents=True)
    (source / "identity.json").write_text(json.dumps({"study": "jobbench"}))
    (source / "stored-spans.json").write_text("[]")
    (source / "trace.json").write_text('{"verified":true}')
    with pytest.raises(CampaignStop, match="study boundaries"):
        materialize_evidence(
            cwd, runtimes[0], diagnostics=True, task="task", arm="diagnostics", replicate=1
        )


def test_executor_context_and_receipt_paths(tmp_path):
    task, context = tmp_path / "task", tmp_path / "context"
    (task / "environment").mkdir(parents=True)
    (task / "environment/Dockerfile").write_text(
        "FROM python:3.12-slim\nCOPY scorer.py /scorer/scorer.py\nCOPY workspace.tar.gz /tmp/workspace.tar.gz\n"
    )
    (task / "environment/scorer.py").write_text("private grading code")
    with tarfile.open(task / "environment/workspace.tar.gz", "w:gz"):
        pass
    build_context(task, context)
    assert {p.name for p in context.iterdir()} == {"Dockerfile", "workspace.tar.gz"}
    assert "scorer" not in (context / "Dockerfile").read_text()
    assert canonical_affected_files(
        ["./context/harness/scripts/f.py", "/work/context/harness/__pycache__/f.pyc"], v4=True
    ) == ["scripts/f.py"]
    with pytest.raises(ValueError):
        canonical_affected_files(["../../private"], v4=True)
