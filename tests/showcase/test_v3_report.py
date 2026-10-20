import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from harness2.showcase.v2_report import evaluation_records
from harness2.showcase.v3_report import (
    choose_examples,
    cited_evidence,
    comparison_rows,
    execution_metrics,
    primary_result,
)


def data():
    families = [
        {"domain": "online_merchants", "heldout": "task2"},
        {"domain": "supply_chain_managers", "heldout": "task2"},
    ]
    return {
        "manifest": {"task_families": families},
        "rows": [
            {
                "task": family["domain"] + "/task2",
                "arm": arm,
                "earned_weight": score * 10,
                "possible_weight": 10,
                "fraction": score,
                "run_url": "https://example.com/run",
            }
            for family, scores in zip(families, [(0.2, 0.9, 0.6), (0.7, 0.4, 0.6)])
            for arm, score in zip(("baseline", "harness2", "diagnostics"), scores)
        ],
    }


def test_primary_contrast_and_case_selection_are_diagnostics_versus_raw():
    comparison = comparison_rows(data())
    result = primary_result(comparison)
    assert result["diagnostics_complete_pairs"] == 2
    assert result["diagnostics_mean_difference_pp"] == pytest.approx(-5)
    assert result["diagnostics_wins"] == result["diagnostics_losses"] == 1
    chosen = choose_examples(comparison)
    assert chosen["example"]["job"] == "Supply chain"
    assert chosen["regression"]["job"] == "Online merchants"
    # With no positive difference, the largest absolute loss is the selected example.
    comparison[1]["difference_pp"] = 0
    assert choose_examples(comparison)["example"]["job"] == "Online merchants"


def test_missing_judgment_remains_missing_and_is_not_a_win():
    value = deepcopy(data())
    value["rows"] = [
        r
        for r in value["rows"]
        if (r["task"], r["arm"]) != ("online_merchants/task2", "diagnostics")
    ]
    comparison = comparison_rows(value)
    assert comparison[0]["difference_pp"] is None
    assert primary_result(comparison)["diagnostics_complete_pairs"] == 1
    assert choose_examples(comparison)["regression"] is None
    assert len(comparison) == 2


@pytest.mark.parametrize("reverse", [False, True])
def test_equal_native_differences_break_ties_by_task_despite_float_rounding(reverse):
    value = data()
    for row in value["rows"]:
        start = 2 if row["task"].startswith("online_merchants/") else 6
        if row["arm"] in ("harness2", "diagnostics"):
            higher = row["arm"] == ("harness2" if reverse else "diagnostics")
            row["earned_weight"] = start + 2 * higher
            row["fraction"] = row["earned_weight"] / row["possible_weight"]
    chosen = choose_examples(comparison_rows(value))
    assert chosen["example"]["task"] == "online_merchants/task2"
    if reverse:
        assert chosen["regression"]["task"] == "online_merchants/task2"


def test_recurrence_counts_identical_errors_with_supporting_spans():
    failure = {
        "tool_name": "bash",
        "tool_call_arguments": '{"command":"false","timeout":100}',
        "error_type": "RuntimeError",
        "status_message": "exit 1",
    }
    spans = [
        {**failure, "span_id": "a"},
        {**failure, "tool_call_arguments": '{"timeout":100,"command":"false"}', "span_id": "b"},
        {**failure, "tool_call_arguments": '{"command":"other"}', "span_id": "c"},
        {"tool_name": "bash", "span_id": "d"},
        {"operation_name": "chat", "error_type": "ContextError", "span_id": "e"},
    ]
    result = execution_metrics(spans)
    assert result["tool_calls"] == 4 and result["tool_errors"] == 3
    assert result["tool_error_rate"] == 0.75
    assert result["repeated_error_occurrences"] == 1
    assert {"a", "b"} in [set(s["span_ids"]) for s in result["error_signatures"]]


def test_span_links_require_readback_and_preserve_child_session_boundaries(tmp_path):
    path = tmp_path / "executions/source"
    path.mkdir(parents=True)
    (path / "stored-spans.json").write_text(
        json.dumps([{"span_id": "0123456789abcdef", "conversation_id": "actual-child-session"}])
    )
    runtime = SimpleNamespace(state=tmp_path, manifest={"project": "wandb/harness2-agent-lens-v3"})
    decision = {
        "decision": {
            "source_sessions": ["source"],
            "receipt": {"cited_span_ids": ["0123456789abcdef", "ffffffffffffffff"]},
        }
    }
    found, missing = cited_evidence(runtime, decision)
    assert len(found) == 1
    assert found[0]["url"].endswith("/conversations/actual-child-session/spans/0123456789abcdef")
    assert missing == ["ffffffffffffffff"]


def test_completed_transfer_stays_in_results_when_judging_stops(tmp_path):
    path = tmp_path / "arms/diagnostics/rep1/dataset/job/task2/model_traj/evaluate"
    path.mkdir(parents=True)
    marker = {
        "session": "completed-transfer",
        "identity": {
            "phase": "evaluate",
            "arm": "diagnostics",
            "domain": "job",
            "task": "job/task2",
            "adaptation_replicate": 1,
            "repetition": 1,
            "harness_fingerprint": "source",
            "rendered_fingerprint": "rendered",
        },
        "trace": {"url": "https://example.com/actual-execution"},
        "output_dir": str(tmp_path / "output"),
        "child_exit_code": 0,
        "collection_status": "complete",
    }
    (path / "showcase.json").write_text(json.dumps(marker))
    runtime = SimpleNamespace(state=tmp_path)
    rows = evaluation_records(runtime, include_ungraded=True)
    assert len(rows) == 1
    assert rows[0]["status"] == "not_judged" and rows[0]["fraction"] is None
    score = {**rows[0], "status": "judged", "score": {"n_passed": 4, "n_criteria": 10}}
    (tmp_path / "scores").mkdir()
    (tmp_path / "scores/score.json").write_text(json.dumps(score))
    rows = evaluation_records(runtime, include_ungraded=True)
    assert len(rows) == 1 and rows[0]["fraction"] == 0.4
