import json
from copy import deepcopy

import pytest

from harness2.showcase.v2_report import (
    choose_case,
    harness_version_key,
    reconcile,
    task_means,
    task_uncertainty,
)


def rows():
    return [
        {
            "arm": arm,
            "task": "domain/task2",
            "adaptation_replicate": rep,
            "repetition": execution,
            "score": {"n_passed": value, "n_criteria": 10},
            "status": "judged",
        }
        for arm, value in (("baseline", 4), ("harness2", 5), ("diagnostics", 6))
        for rep in (1, 2)
        for execution in (1, 2)
    ]


def test_task_means_do_not_count_repetitions_as_independent_tasks():
    got = task_means(reconcile(rows()), 4)
    assert len(got) == 3
    diagnostic = next(r for r in got if r["arm"] == "diagnostics")
    assert diagnostic["mean"] == pytest.approx(0.6)
    assert diagnostic["diagnostics_increment"] == pytest.approx(0.1)
    interval = task_uncertainty(got, 42)
    assert interval["complete_tasks"] == 1
    assert interval["mean"] == pytest.approx(0.1)
    assert interval == task_uncertainty(got, 42)


def test_duplicate_replicate_rejected_but_independent_adaptations_retained():
    values = rows()
    assert len(reconcile(values)) == 12
    with pytest.raises(ValueError, match="Duplicate"):
        reconcile(values + [deepcopy(values[0])])


def test_unavailable_judge_not_zero_or_eligible_showcase():
    values = rows()
    for row in values:
        if row["arm"] == "diagnostics" and row["repetition"] == 1:
            row.update(score=None, status="judgment_unavailable")
    got = task_means(reconcile(values), 4)
    diagnostic = next(r for r in got if r["arm"] == "diagnostics")
    assert diagnostic["observations"] == 2 and not diagnostic["complete"]
    assert choose_case({"task_means": got})["arm"] == "harness2"
    assert task_uncertainty(got, 42)["complete_tasks"] == 0


def test_native_grade_evidence_must_match(tmp_path):
    values = rows()
    p = tmp_path / "judge.json"
    p.write_text(json.dumps({"total_score": 1, "max_score": 10}))
    values[0]["judge_evidence"] = str(p)
    with pytest.raises(ValueError, match="Native score"):
        reconcile(values)


def test_selection_largest_positive_complete_task_difference():
    got = task_means(reconcile(rows()), 4)
    assert choose_case({"task_means": got})["arm"] == "diagnostics"
    for row in got:
        row["paired_change"] = 0
    assert choose_case({"task_means": got}) is None


def test_stored_message_parts_decode_without_losing_quotes_or_newlines():
    from harness2.showcase.v2_report import message_texts

    entry = 'Mission:\nRead "context/harness" and inspect the evidence.'
    stored = [{"role": "user", "content": json.dumps([{"type": "text", "content": entry}])}]
    assert message_texts(stored) == [entry]


def test_harness_artifact_lookup_preserves_qualification_attempt_and_actual_version():
    frozen = {
        "arm": "diagnostics",
        "domain": "domain",
        "adaptation_replicate": 1,
        "source_fingerprint": "same-keep-harness",
        "rendered_fingerprint": "rendered",
    }
    second = {**frozen, "qualification_attempt": 2}
    versions = {
        harness_version_key(frozen, qualification=True): "first-attempt-artifact",
        harness_version_key(second, qualification=True): "second-attempt-artifact",
        harness_version_key(second): "heldout-artifact",
    }
    row = {
        "arm": "diagnostics",
        "task": "domain/task1",
        "adaptation_replicate": 1,
        "qualification_attempt": 2,
        "harness_fingerprint": "same-keep-harness",
        "rendered_fingerprint": "rendered",
    }
    assert len(versions) == 3
    assert versions[harness_version_key(row, qualification=True)] == "second-attempt-artifact"
    assert versions[harness_version_key(row)] == "heldout-artifact"
    row["harness_fingerprint"] = "different-harness"
    assert harness_version_key(row, qualification=True) not in versions
