import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from harness2.showcase.budget import BudgetError, Ledger
from harness2.showcase.runtime import load_manifest
from harness2.showcase.telemetry import export_session, tool_error


def test_restart_retains_reservation_and_blocks_new_spending(tmp_path):
    manifest = json.loads(Path("showcase/pilot.json").read_text())
    ledger = Ledger(tmp_path / "ledger.db", manifest)
    model = manifest["models"]["actor"]
    ledger.reserve("one", "smoke", model, 100)
    before = ledger.snapshot()["reserved_usd"]
    restarted = Ledger(tmp_path / "ledger.db", manifest)
    restarted.interrupt_pending()
    assert restarted.snapshot()["reserved_usd"] == before
    with pytest.raises(BudgetError, match="Unreconciled"):
        restarted.reserve("two", "smoke", model, 100)


def test_interrupted_export_queries_same_trace_without_reemission(tmp_path, monkeypatch):
    directory = tmp_path / "executions/session"
    directory.mkdir(parents=True)
    receipt = {"verified": False, "trace_ids": ["original"], "expected_spans": 2}
    (directory / "trace.json").write_text(json.dumps(receipt))
    runtime = SimpleNamespace(
        state=tmp_path,
        manifest={"project": "test/project"},
        calls=lambda _: [{"call_id": "request"}],
    )
    query = Mock(side_effect=RuntimeError("storage still unavailable"))
    init = Mock()
    monkeypatch.setattr("harness2.showcase.telemetry.readback", query)
    monkeypatch.setattr("harness2.showcase.telemetry.tracing.init", init)
    with pytest.raises(RuntimeError, match="still unavailable"):
        export_session(runtime, "session")
    query.assert_called_once_with("test/project", "original", 2)
    init.assert_not_called()
    assert json.loads((directory / "trace.json").read_text()) == receipt


def test_completed_shell_tool_with_nonzero_exit_is_failure():
    assert (
        tool_error({"state": {"status": "completed", "metadata": {"exit": 7}}})
        == "Process exited with status 7"
    )
    assert tool_error({"state": {"status": "completed", "metadata": {"exit": 0}}}) is None


def test_manifest_cannot_drift_after_first_attempt(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"state_dir": "state", "model": "first"}))
    load_manifest(path)
    path.write_text(json.dumps({"state_dir": "state", "model": "changed"}))
    with pytest.raises(ValueError, match="Manifest changed"):
        load_manifest(path)
