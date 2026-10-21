import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest

from harness2.showcase.budget import BudgetError, Ledger
from harness2.showcase.protocol import _bounded, inventory, summarize
from harness2.showcase.study import evaluation_schedule, read_manifest, status, validate_manifest
from harness2.showcase.study_analysis import paired_analysis
from harness2.showcase.workbuddy_preflight import CATEGORIES, metadata_exclusion, select


def manifest():
    return json.loads((Path(__file__).resolve().parents[2] / "showcase/study-v4.json").read_text())


def test_v4_design_and_read_only_status(tmp_path):
    m = manifest()
    validate_manifest(m)
    path = tmp_path / "study.json"
    path.write_text(json.dumps(m))
    loaded, state = read_manifest(path)
    assert status(loaded, state)["planned_final_executions"] == 252
    assert status(loaded, state, "code")["planned_final_executions"] == 216
    assert not state.exists()
    m["studies"]["code"]["final_executions"] = 200
    with pytest.raises(ValueError):
        validate_manifest(m)


def test_empty_capture_patch_is_not_an_agent_deliverable(tmp_path):
    patch = tmp_path / "delivered.patch"
    patch.write_text("")
    files = inventory(tmp_path)
    assert files[0]["bytes"] == 0
    assert files[0]["readable"] is False
    value = json.loads(
        summarize([], files, "https://example.org/trace", nonempty_deliverables=True)
    )
    assert value["deliverable_present"] is False
    assert value["readable_files"] == 0
    assert value["produced_files"] == files
    patch.write_text("diff --git a/example.py b/example.py\n")
    assert json.loads(summarize([], inventory(tmp_path), "trace", nonempty_deliverables=True))[
        "deliverable_present"
    ]


def test_diagnostic_byte_limit_includes_terminating_newline():
    value = {"items": ["é" * 10]}
    exact_json_length = len(
        json.dumps({**value, "omitted": {}}, ensure_ascii=False, separators=(",", ":")).encode()
    )
    result = _bounded(value, exact_json_length)
    assert len(result.encode()) <= exact_json_length
    assert json.loads(result)["omitted"]["items"] == 1


def test_bounded_usage_retains_charge_across_restart(tmp_path):
    m = manifest()
    ledger = Ledger(tmp_path / "budget.sqlite", m)
    call = ledger.reserve("one", "adapt", m["models"]["actor"], 100, 1000)
    ledger.finish(call, None)
    ledger = Ledger(ledger.path, m)
    ledger.reserve("two", "adapt", m["models"]["actor"], 100, 1000)
    s = ledger.snapshot()
    assert s["observed_cost_usd"] == 0
    assert s["bounded_unknown_cost_usd"] == 0.0002
    assert s["in_flight_reservations_usd"] == 0.0002
    assert s["charged_upper_bound_usd"] == 0.0004
    assert s["calls"][0]["usage"] == "null"
    ledger.interrupt_pending()
    assert ledger.snapshot()["bounded_unknown_cost_usd"] == 0.0004


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": 1001, "completion_tokens": 10},
        {"prompt_tokens": None, "completion_tokens": 101},
        {"prompt_tokens": -1, "completion_tokens": 0},
        {
            "prompt_tokens": None,
            "completion_tokens": 1,
            "prompt_tokens_details": {"cached_tokens": 1001},
        },
        {
            "prompt_tokens": 10,
            "completion_tokens": None,
            "prompt_tokens_details": {"cached_tokens": 11},
        },
        {
            "prompt_tokens": None,
            "completion_tokens": 2,
            "completion_tokens_details": {"reasoning_tokens": 3},
        },
        {"prompt_tokens": 10, "completion_tokens": None, "total_tokens": 1101},
        {"prompt_tokens": 10, "completion_tokens": 10, "prompt_tokens_details": []},
    ],
)
def test_contradictory_usage_stops_v4(tmp_path, usage):
    m = manifest()
    ledger = Ledger(tmp_path / "budget.sqlite", m)
    call = ledger.reserve("one", "adapt", m["models"]["actor"], 100, 1000)
    ledger.finish(call, usage)
    with pytest.raises(BudgetError, match="Unreconciled"):
        ledger.reserve("two", "adapt", m["models"]["actor"], 100, 1000)


def test_bounded_unknown_competes_with_concurrent_reservations(tmp_path):
    m = manifest()
    m["budget_usd"] = ".0008"
    ledger = Ledger(tmp_path / "budget.sqlite", m)
    call = ledger.reserve("one", "adapt", m["models"]["actor"], 100, 1000)
    ledger.finish(call, None)

    def attempt(i):
        try:
            return ledger.reserve(str(i), "adapt", m["models"]["actor"], 100, 1000)
        except BudgetError:
            return None

    with ThreadPoolExecutor(8) as pool:
        assert sum(x is not None for x in pool.map(attempt, range(20))) == 3
    assert ledger.snapshot()["charged_upper_bound_usd"] == 0.0008


def test_seeded_selection_excludes_qualification_and_shared_repository():
    rows = [
        dict(task_id=f"{category}-{i}", category=category, repository=f"repo-{i}", eligible=True)
        for category in CATEGORIES
        for i in range(6)
    ]
    first = select(rows)
    assert first == select(list(reversed(rows)))
    assert first["ready"]
    assert len(first["qualification"]) == 4
    used = {t for group in first["groups"] for t in [group["adapt"], *group["heldout"]]}
    assert not used.intersection(first["qualification"])
    broken = deepcopy(rows)
    for row in broken:
        if row["category"] == "testing":
            row["repository"] = "same"
    assert "testing" in select(broken)["shortfalls"]
    row = dict(
        category="bug-fix", surface="single_file", artifact_type="mixed", repository="public/repo"
    )
    assert metadata_exclusion(row) is None
    row["surface"] = "synthetic_workspace"
    assert metadata_exclusion(row)


def score_rows(effect):
    return [
        dict(
            group=f"g{g}",
            trial=trial,
            task=f"t{g}-{task}",
            condition=condition,
            score=0.5 + (effect if condition == "diagnostics" else 0),
        )
        for g in range(4)
        for trial in range(1, 4)
        for task in range(3)
        for condition in ("baseline", "harness2", "diagnostics")
    ]


@pytest.mark.parametrize("effect", [-0.2, 0, 0.03, 0.2])
def test_paired_analysis_known_effect(effect):
    result = paired_analysis(score_rows(effect), trials=3, draws=300)
    assert result["complete"]
    assert result["heldout_tasks"] == 12
    assert result["independent_groups"] == 4
    assert result["diagnostics_minus_raw_pp"] == pytest.approx(effect * 100)
    assert result["interval_95_pp"] == pytest.approx([effect * 100] * 2)
    assert result["practically_equivalent"] == (abs(effect) <= 0.05)


def test_missing_and_duplicate_measurements_are_not_zero_or_extra_trials():
    rows = score_rows(0.2)
    rows[0]["score"] = None
    result = paired_analysis(rows, trials=3, draws=300)
    assert not result["complete"]
    assert result["diagnostics_minus_raw_pp"] is None
    assert result["interval_95_pp"] is None
    assert len(result["missing"]) == 1
    with pytest.raises(ValueError, match="Duplicate"):
        paired_analysis(rows + rows[:1], trials=3)


def test_schedule_is_frozen_and_contains_each_cell_once():
    m = manifest()
    groups = {"jobbench": m["studies"]["jobbench"]["groups"]}
    rows = evaluation_schedule(m, groups)
    assert len(rows) == len({r["key"] for r in rows}) == 36
    assert rows == evaluation_schedule(m, groups)
    assert {r["condition"] for r in rows[:8]} == {"baseline", "harness2", "diagnostics"}
