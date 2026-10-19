import copy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from harness2.showcase.report import index_generated_files, make_public, reconcile


def test_existing_public_project_requires_no_visibility_change(monkeypatch):
    response = Mock()
    response.json.return_value = {"data": {"project": {"access": "USER_READ"}}}
    post = Mock(return_value=response)
    monkeypatch.setattr("harness2.showcase.report.httpx.post", post)
    runtime = SimpleNamespace(manifest={"project": "team/project", "campaign": "pilot-v2"})
    make_public(runtime, SimpleNamespace())
    assert post.call_count == 1
    assert "auth" not in post.call_args.kwargs
    assert post.call_args.kwargs["json"]["query"].startswith("query(")


def test_private_project_with_other_campaign_remains_private(monkeypatch):
    response = Mock()
    response.json.return_value = {"data": {"project": None}}
    post = Mock(return_value=response)
    monkeypatch.setattr("harness2.showcase.report.httpx.post", post)
    runtime = SimpleNamespace(manifest={"project": "team/project", "campaign": "pilot-v2"})
    api = SimpleNamespace(runs=lambda _: [SimpleNamespace(config={"campaign": "unrelated"})])
    with pytest.raises(RuntimeError, match="another campaign"):
        make_public(runtime, api)
    assert post.call_count == 1


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
    assert rows[2]["earned_weight"] == 70
    assert rows[2]["possible_weight"] == 100
    assert rows[1]["earned_weight"] is None


def test_duplicate_occurrence_is_rejected():
    original = row("baseline", 1, 80)
    with pytest.raises(ValueError, match="Duplicate"):
        reconcile([original, copy.deepcopy(original)])


def test_generated_harness_diff_is_covered_by_integrity_manifest(tmp_path):
    diff = tmp_path / "harnesses" / "abc" / "harness.diff"
    diff.parent.mkdir(parents=True)
    diff.write_text("+Verify every deliverable\n")
    index_generated_files(tmp_path, [])
    manifest = json.loads((tmp_path / "files.json").read_text())
    assert manifest == [
        {
            "path": "harnesses/abc/harness.diff",
            "sha256": hashlib.sha256(diff.read_bytes()).hexdigest(),
            "generated": True,
        }
    ]
