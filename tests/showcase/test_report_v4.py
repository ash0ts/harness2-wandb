import shutil
import zipfile
from types import SimpleNamespace

import pytest

from harness2.showcase import report_v4
from harness2.showcase.runtime import fingerprint
from harness2.showcase.workbuddy_preflight import save


def planned():
    return [
        {
            "key": f"{task}/{trial}/{condition}",
            "study": "code",
            "group": "repairs",
            "task": task,
            "trial": trial,
            "condition": condition,
        }
        for task in ("first", "entirely-missing")
        for trial in (1, 2)
        for condition in ("baseline", "harness2", "diagnostics")
    ]


def test_entirely_missing_task_is_in_denominator(tmp_path, monkeypatch):
    manifest = {
        "campaign": "study-v4",
        "project": "wandb/test",
        "analysis": {"seed": 42, "bootstrap_draws": 100, "equivalence_pp": 5},
        "studies": {"code": {"adaptation_trials": 2}, "jobbench": {"adaptation_trials": 2}},
    }
    runtime = SimpleNamespace(state=tmp_path, manifest=manifest, budget=lambda: {"calls": []})
    monkeypatch.setattr(report_v4, "planned_rows", lambda _: planned())
    for row in planned():
        if row["task"] == "first":
            save(
                tmp_path / "evaluations" / (row["key"].replace("/", "_") + ".json"),
                {**row, "status": "graded", "score": 0.5, "judgment": {"score": 0.5}},
            )
    data = report_v4.dataset(runtime)
    analysis = data["analysis"]["code"]
    assert len(data["rows"]) == 12
    assert analysis["observed_cells"] == 6
    assert analysis["planned_cells"] == 12
    assert not analysis["complete"]
    assert analysis["diagnostics_minus_raw_pp"] is None
    assert len(analysis["missing"]) == 6


def test_stability_sample_is_fixed_without_scores():
    rows = [{"key": str(i), "study": "jobbench", "score": i / 36} for i in range(36)]
    original = [r["key"] for r in report_v4.stability_sample(rows, 20261022)]
    reversed_scores = [{**r, "score": 1 - r["score"]} for r in reversed(rows)]
    assert len(original) == 8
    assert [r["key"] for r in report_v4.stability_sample(reversed_scores, 20261022)] == original


def test_examples_require_complete_evidence_and_follow_rule():
    tasks = [
        {"task": "a", "diagnostics_minus_raw_pp": 5},
        {"task": "b", "diagnostics_minus_raw_pp": 5},
        {"task": "regression", "diagnostics_minus_raw_pp": -8},
        {"task": "no-trace", "diagnostics_minus_raw_pp": 50},
    ]
    data = {
        "analysis": {"code": {"tasks": tasks}},
        "rows": [
            {
                "study": "code",
                "task": r["task"],
                "trace": None if r["task"] == "no-trace" else {"verified": True},
                "frozen_harness": {"fingerprint": "x"},
                "judgment": {"score": 0.5},
            }
            for r in tasks
        ],
    }
    examples = report_v4.choose_examples(data, "code")
    assert examples["example"]["task"] == "a"
    assert examples["regression"]["task"] == "regression"


def test_reject_score_that_disagrees_with_judge(tmp_path, monkeypatch):
    runtime = SimpleNamespace(
        state=tmp_path, manifest={"project": "test/project"}, budget=lambda: {"calls": []}
    )
    monkeypatch.setattr(report_v4, "planned_rows", lambda _: planned())
    row = planned()[0]
    save(tmp_path / "evaluations/one.json", {**row, "score": 1, "judgment": {"score": 0}})
    with pytest.raises(ValueError, match="native judgment"):
        report_v4.dataset(runtime)


def test_downloadable_harness_retains_executable_fingerprint(tmp_path):
    state = tmp_path / "state"
    harness = state / "executions/session/source-harness"
    harness.mkdir(parents=True)
    script = harness / "check.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    (harness / "prompt.j2").write_text("Use the repository's tests.")
    runtime = SimpleNamespace(state=state, admin="test-admin-secret")
    destination = tmp_path / "evidence"
    report_v4.bundle(runtime, destination)
    copied = destination / harness.relative_to(state)
    assert fingerprint(copied, executable_modes=True) == fingerprint(harness, executable_modes=True)
    archive = shutil.make_archive(str(tmp_path / "download"), "zip", destination)
    with zipfile.ZipFile(archive) as packaged:
        mode = packaged.getinfo("executions/session/source-harness/check.sh").external_attr >> 16
        assert mode & 0o111
