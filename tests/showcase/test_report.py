import copy

import pytest

from harness2.showcase.report import reconcile


def row(arm, rep, score):
    return {
        "arm": arm,
        "task": "domain/task3",
        "cohort": "heldout",
        "repetition": rep,
        "score": {"n_passed": score, "n_criteria": 100} if score is not None else None,
    }


def test_missing_judgment_is_not_zero_and_pairs_keep_repetitions():
    rows = reconcile(
        [
            row("baseline", 1, 80),
            row("baseline", 2, None),
            row("diagnostics", 1, 70),
            row("diagnostics", 2, 90),
        ]
    )
    assert rows[2]["paired_change"] == pytest.approx(-0.1)
    assert rows[3]["paired_change"] is None
    assert rows[1]["fraction"] is None


def test_duplicate_occurrence_is_rejected():
    original = row("baseline", 1, 80)
    with pytest.raises(ValueError, match="Duplicate"):
        reconcile([original, copy.deepcopy(original)])
