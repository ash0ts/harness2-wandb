import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness2.showcase.identity import conversation_name, identity_fields
from harness2.showcase.qualification import area, restart
from harness2.showcase.runtime import CampaignStop, load_manifest
from harness2.showcase.v2 import allocation, provenance, schedule


def manifest():
    return json.loads(Path("showcase/pilot-v3.json").read_text())


def test_v3_manifest_and_eighteen_distinct_evaluations(tmp_path):
    value = manifest()
    path = tmp_path / "pilot.json"
    path.write_text(json.dumps(value))
    got, _ = load_manifest(path)
    assert got["prior_spend_usd"] == "15.122852"
    assert got["budget_usd"] == "84.87"
    jobs = schedule(SimpleNamespace(manifest=got), 1)
    assert len(jobs) == len({(j["task"], j["arm"]) for j in jobs}) == 18
    assert len({j["task"] for j in jobs}) == 6
    assert all(j["repetition"] == j["adaptation_replicate"] == 1 for j in jobs)
    for start in range(0, len(jobs), 3):
        assert {j["arm"] for j in jobs[start : start + 3]} == {
            "baseline",
            "harness2",
            "diagnostics",
        }


@pytest.mark.parametrize("field,value", [("repetitions", 2), ("adaptation_replicates", 2)])
def test_v3_rejects_accidental_expansion(tmp_path, field, value):
    m = manifest()
    m[field] = value
    path = tmp_path / "pilot.json"
    path.write_text(json.dumps(m))
    with pytest.raises(ValueError, match="Pilot-v3 requires"):
        load_manifest(path)


def test_conversation_names_join_roles_rounds_and_real_attempts():
    identity = identity_fields(
        {
            "task": "computer_user_support_specialists/task1",
            "arm": "diagnostics",
            "role": "improver",
            "phase": "retry",
            "stage": "c2.a1",
            "proposal_attempt": 2,
            "harness_fingerprint": "source",
            "rendered_fingerprint": "rendered",
        },
        manifest(),
    )
    assert identity["cohort"] == "adaptation"
    assert identity["round"] == 2 and identity["attempt"] == 2
    assert identity["source_fingerprint"] == "source"
    assert conversation_name(identity) == (
        "Computer support / Diagnostics / task1 / Round 2 / Compose / Attempt 2"
    )
    assert conversation_name(identity, True).endswith(" / Auxiliary titles")
    judge = identity_fields({**identity, "role": "judge", "phase": "smoke"}, manifest())
    assert judge["cohort"] == "qualification"
    assert "Qualification / Round 2 / Native judge" in conversation_name(judge)
    transfer = identity_fields(
        {
            "task": "online_merchants/task2",
            "arm": "baseline",
            "role": "solver",
            "phase": "evaluate",
            "run_id": "evaluate/rep1",
        },
        manifest(),
    )
    assert transfer["cohort"] == "heldout" and transfer["round"] == 0
    assert "Held-out execution" in conversation_name(transfer)


def test_main_engine_must_match_live_qualification(tmp_path, monkeypatch):
    def command(argv, **kwargs):
        return SimpleNamespace(
            stdout='[{"Id":"pinned-image"}]' if argv[0] == "docker" else "commit"
        )

    monkeypatch.setattr("harness2.showcase.v2.command", command)
    runtime = SimpleNamespace(state=tmp_path, manifest=manifest())
    provenance(runtime, "qualification")
    provenance(runtime, "adapt")
    qualified = tmp_path / "provenance/qualification.json"
    value = json.loads(qualified.read_text())
    value["execution_engine"]["harness2/showcase/runtime.py"] = "different-runtime"
    qualified.write_text(json.dumps(value))
    with pytest.raises(CampaignStop, match="live-qualified"):
        provenance(runtime, "evaluate")


def test_requalification_preserves_failed_work_and_one_budget(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "validator.py").write_text("repaired")
    monkeypatch.setattr("harness2.showcase.qualification.ROOT", source)
    state = tmp_path / "state"
    provenance_dir = state / "provenance"
    provenance_dir.mkdir(parents=True)
    old = {"execution_engine": {"validator.py": hashlib.sha256(b"original").hexdigest()}}
    (provenance_dir / "qualification.json").write_text(json.dumps(old))
    snapshot = provenance_dir / "qualification-source"
    snapshot.mkdir()
    (snapshot / "validator.py").write_text("original")
    (state / "stopped.json").write_text(json.dumps({"phase": "smoke", "reason": "path alias"}))
    baseline = state / "qualification/baseline/rep1/result.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("retained execution")
    runtime = SimpleNamespace(
        state=state,
        manifest=manifest(),
        budget=lambda: {"spent_usd": 2.27, "reserved_usd": 0, "unresolved": 0},
    )
    value = restart(runtime, "Accept the workspace prefix for the same affected file")
    assert value["attempt"] == 2 and value["spent_before_usd"] == 2.27
    assert baseline.read_text() == "retained execution"
    assert (state / "qualification-attempts/1/source/validator.py").read_text() == "original"
    assert not (state / "stopped.json").exists()
    assert area(runtime, "qualification") == state / "qualification/attempt-2"
    assert area(runtime, "qualification", number=1) == state / "qualification"
    (state / "stopped.json").write_text(json.dumps({"phase": "smoke", "reason": "another stop"}))
    old["execution_engine"]["validator.py"] = hashlib.sha256(b"repaired").hexdigest()
    (provenance_dir / "qualification.json").write_text(json.dumps(old))
    with pytest.raises(CampaignStop, match="engine repair"):
        restart(runtime, "Same engine cannot be retried until it passes")
    runtime.budget = lambda: {"spent_usd": 2.27, "reserved_usd": 0.1, "unresolved": 0}
    with pytest.raises(CampaignStop, match="in-flight"):
        restart(runtime, "Never repeat an uncertain call")


def test_allocation_counts_shared_evidence_once_without_a_mechanism_reserve(tmp_path, monkeypatch):
    m = manifest()
    m["budget_usd"] = "37"
    cases = ["technical_writers/task1", "management_analysts/task1"]
    (tmp_path / "qualification.json").write_text(
        json.dumps({"passed": True, "cases": [{"task": task} for task in cases]})
    )
    calls = []
    stages = [
        ("solver", "base1", 0.05),
        ("solver", "dom1", 0.10),
        ("improver", "dom1", 1.50),
        ("reflection", "reflect1", 0.05),
        ("judge", "judge", 0.02),
    ]
    for case_index, task in enumerate(cases):
        for role, stage, cost in stages:
            sid = f"{case_index}-{role}-{stage}"
            path = tmp_path / "gateway/sessions" / sid
            path.mkdir(parents=True)
            (path / "identity.json").write_text(
                json.dumps(
                    {
                        "task": task,
                        "role": role,
                        "stage": stage,
                        "run_id": f"shared/{stage}",
                    }
                )
            )
            calls.append({"session": sid, "spent": round(cost * 1e6)})
    for task in cases + [f"{f['domain']}/{f['heldout']}" for f in m["task_families"]]:
        path = tmp_path / "dependencies/job-bench-eval/dataset/main" / task
        path.mkdir(parents=True)
        (path / "RUBRICS.json").write_text(json.dumps({"rubrics": [{}, {}]}))
    monkeypatch.setattr("harness2.showcase.v2.ROOT", tmp_path)
    monkeypatch.setattr("harness2.showcase.v2.prepare", lambda *args: None)
    runtime = SimpleNamespace(
        state=tmp_path,
        manifest=m,
        budget=lambda: {"calls": calls, "spent_usd": 3.44},
    )
    result = allocation(runtime)
    assert result["planned_transfer_executions"] == 18
    assert result["projected_adaptation_usd"] == pytest.approx(30.15)
    assert result["projected_main_usd"] == pytest.approx(32.85)
    assert result["projected_judging_usd"] == pytest.approx(0.54)
    assert len(json.loads((tmp_path / "cost-projection.json").read_text())["candidates"]) == 1
