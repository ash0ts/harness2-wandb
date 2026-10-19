import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from harness2.showcase.telemetry_v2 import export_grouped, is_title_call


def test_auxiliary_detection_uses_pinned_system_prompt_not_task_words():
    title = {
        "request": {
            "messages": [
                {
                    "role": "system",
                    "content": "You are a title generator. You output ONLY a thread title.",
                }
            ]
        }
    }
    user = {"request": {"messages": [{"role": "user", "content": "You are a title generator."}]}}
    assert is_title_call(title)
    assert not is_title_call(user)


def test_interrupted_grouped_export_reads_both_existing_traces_without_reemission(
    tmp_path, monkeypatch
):
    receipt = {
        "verified": False,
        "expected_groups": 2,
        "trace_groups": [
            {"trace_id": "main", "expected_spans": 2},
            {"trace_id": "aux", "expected_spans": 2},
        ],
    }
    (tmp_path / "trace.json").write_text(json.dumps(receipt))
    calls = [
        {
            "identity": {"role": "solver"},
            "request": {"messages": [{"role": "user", "content": "actual task"}]},
        }
    ]
    runtime = SimpleNamespace(manifest={"project": "test/project"}, calls=lambda _: calls)
    query = Mock(side_effect=[[{"span_id": "main"}], [{"span_id": "aux"}]])
    verify = Mock()
    init = Mock()
    monkeypatch.setattr("harness2.showcase.telemetry_v2.readback", query)
    monkeypatch.setattr("harness2.showcase.telemetry_v2.verify_readback", verify)
    monkeypatch.setattr("harness2.showcase.telemetry_v2.tracing.init", init)
    result = export_grouped(runtime, "session", None, tmp_path)
    assert result["verified"]
    assert query.call_count == 2
    init.assert_not_called()
    verify.assert_called_once_with([{"span_id": "main"}, {"span_id": "aux"}], calls, [])


def test_partial_group_root_export_fails_closed(tmp_path, monkeypatch):
    (tmp_path / "trace.json").write_text(
        json.dumps({"verified": False, "expected_groups": 2, "trace_groups": []})
    )
    runtime = SimpleNamespace(
        manifest={"project": "test/project"},
        calls=lambda _: [{"identity": {"role": "solver"}, "request": {"messages": []}}],
    )
    init = Mock()
    monkeypatch.setattr("harness2.showcase.telemetry_v2.tracing.init", init)
    with pytest.raises(RuntimeError, match="Interrupted export"):
        export_grouped(runtime, "session", None, tmp_path)
    init.assert_not_called()
