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


def test_parallel_conditions_materialize_one_complete_input_mirror(tmp_path, monkeypatch):
    import shutil
    import time
    from concurrent.futures import ThreadPoolExecutor

    from harness2.showcase.experiment import ExperimentAdapter

    source = tmp_path / "fixture"
    (source / "task_folder").mkdir(parents=True)
    (source / "task_folder/input.txt").write_text("public input")
    (source / "files_required_to_search").mkdir()
    (source / "files_required_to_search/ref.txt").write_text("public reference")
    (source / "RUBRICS.json").write_text("never visible")
    adapter = ExperimentAdapter.__new__(ExperimentAdapter)
    adapter.runtime = SimpleNamespace(state=tmp_path / "state")
    adapter._dataset_task = lambda _: source
    original = shutil.copytree

    def slower_copy(src, dst, **kwargs):
        result = original(src, dst, **kwargs)
        time.sleep(0.02)
        return result

    monkeypatch.setattr("harness2.showcase.experiment.shutil.copytree", slower_copy)
    with ThreadPoolExecutor(4) as pool:
        paths = list(pool.map(adapter.task_files, ["domain/task1"] * 4))
    assert len(set(paths)) == 1
    assert sorted(str(p.relative_to(paths[0])) for p in paths[0].rglob("*") if p.is_file()) == [
        "input.txt",
        "references/ref.txt",
    ]


@pytest.mark.parametrize("second_adopted", [True, False])
def test_resume_preserves_failed_task_and_continues_without_extra_paid_attempt(
    tmp_path, monkeypatch, second_adopted
):
    from harness2.showcase.cli import _adapt_domain

    failed = tmp_path / "arms/diagnostics/results/domain__task1.json"
    failed.parent.mkdir(parents=True)
    failure = {"status": "partial", "selections": {"agg": {"chosen_label": ""}}}
    failed.write_text(json.dumps(failure))
    runtime = SimpleNamespace(state=tmp_path, manifest={"adapt_tasks": ["task1", "task2"]})
    run = Mock(
        return_value=SimpleNamespace(
            to_dict=lambda: {
                "status": "ok" if second_adopted else "partial",
                "selections": {"agg": {"chosen_label": "agg1" if second_adopted else ""}},
            }
        )
    )
    freeze = Mock()
    monkeypatch.setattr("harness2.showcase.cli.ExperimentAdapter", Mock())
    monkeypatch.setattr("harness2.showcase.cli.ExperimentProposer", Mock())
    monkeypatch.setattr("harness2.showcase.cli.Library", Mock())
    monkeypatch.setattr("harness2.showcase.cli.run_task", run)
    monkeypatch.setattr("harness2.showcase.cli.freeze", freeze)
    _adapt_domain(runtime, "domain", "diagnostics")
    assert [call.args[0] for call in run.call_args_list] == ["domain/task2"]
    assert json.loads(failed.read_text()) == failure
    if second_adopted:
        freeze.assert_called_once()
    else:
        freeze.assert_not_called()
        unavailable = json.loads(
            (tmp_path / "frozen/diagnostics/domain/unavailable.json").read_text()
        )
        assert unavailable["task"] == "domain/task2"
