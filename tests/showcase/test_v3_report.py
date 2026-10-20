from copy import deepcopy

import pytest

from harness2.showcase.v3_report import (
    choose_examples,
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
