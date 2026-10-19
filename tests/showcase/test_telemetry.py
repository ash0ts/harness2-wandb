import json

import pytest

from harness2.showcase.telemetry import (
    diagnostics,
    spans_from_records,
    tool_events,
    verify_readback,
)


def test_original_tool_timestamps_and_attempt_identity(tmp_path):
    path = tmp_path / "events.jsonl"
    parts = [
        {
            "type": "tool",
            "callID": "first",
            "tool": "bash",
            "state": {
                "status": "error",
                "input": {"command": "false"},
                "error": "exit 1",
                "time": {"start": 1000, "end": 1500},
            },
        },
        {
            "type": "tool",
            "callID": "retry",
            "tool": "bash",
            "state": {
                "status": "completed",
                "input": {"command": "true"},
                "output": "",
                "time": {"start": 2000, "end": 2500},
            },
        },
    ]
    path.write_text("\n".join(json.dumps({"part": p}) for p in parts + parts))
    tools = tool_events(path)
    assert len(tools) == 2
    spans = spans_from_records([], tools)
    assert [s.tool_call_id for s in spans] == ["first", "retry"]
    assert spans[0].started_at.timestamp() == 1
    assert spans[0].ended_at.timestamp() == 1.5


def test_request_and_final_provider_usage_preserved():
    record = {
        "call_id": "independent",
        "started": 1,
        "ended": 2,
        "request": {
            "model": "model",
            "messages": [{"role": "user", "content": "do it"}],
            "max_tokens": 100,
        },
        "response": {
            "id": "r",
            "model": "served-model",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "prompt_tokens_details": {"cached_tokens": 80},
            },
            "choices": [
                {"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}
            ],
        },
    }
    span = spans_from_records([record], [])[0]
    assert span.usage.input_tokens == 100
    assert span.usage.cache_read_input_tokens == 80
    assert span.response_model == "served-model"
    assert span.started_at.timestamp() == 1
    assert span.output_messages[0].parts[0].content == "done"


def test_diagnostics_only_observations():
    rows = [
        {"span_id": "a", "tool_name": "read", "tool_call_arguments": {"path": "file"}},
        {
            "span_id": "b",
            "tool_name": "read",
            "tool_call_arguments": {"path": "file"},
            "status_code": "ERROR",
        },
    ]
    got = diagnostics(rows, False)
    assert got["repeated_tool_calls"][0]["span_ids"] == ["a", "b"]
    assert got["errors"][0]["span_id"] == "b"
    assert got["deliverable_present"] is False
    assert "score" not in got


def test_readback_preserves_tool_results_and_times():
    tool = {
        "callID": "failed-check",
        "tool": "bash",
        "state": {
            "status": "completed",
            "input": {"command": "check"},
            "output": '{\n  "violations": 2\n}',
            "metadata": {"exit": 1},
            "time": {"start": 1000, "end": 2000},
        },
    }
    row = {
        "operation_name": "execute_tool",
        "tool_call_id": "failed-check",
        "tool_name": "bash",
        "tool_call_arguments": '{"command":"check"}',
        "tool_call_result": '{"violations": 2}',
        "started_at": "1970-01-01T00:00:01+00:00",
        "ended_at": "1970-01-01T00:00:02+00:00",
        "status_code": "ERROR",
    }
    verify_readback([row], [], [tool])
    for key, bad in (
        ("tool_call_result", '{"violations": 0}'),
        ("ended_at", "1970-01-01T00:00:03+00:00"),
        ("status_code", "OK"),
    ):
        with pytest.raises(RuntimeError):
            verify_readback([{**row, key: bad}], [], [tool])


def test_readback_preserves_reasoning_usage_limits_and_finish_reason():
    record = {
        "call_id": "call",
        "started": 1,
        "ended": 2,
        "request": {"model": "model", "max_tokens": 100},
        "response": {
            "id": "response",
            "model": "model",
            "usage": {
                "prompt_tokens": 80,
                "completion_tokens": 100,
                "prompt_tokens_details": {"cached_tokens": 50},
                "completion_tokens_details": {"reasoning_tokens": 100},
            },
            "choices": [{"finish_reason": "length"}],
        },
    }
    row = {
        "operation_name": "chat",
        "response_id": "response",
        "request_model": "model",
        "response_model": "model",
        "input_tokens": 80,
        "output_tokens": 100,
        "cache_read_input_tokens": 50,
        "reasoning_tokens": 100,
        "request_max_tokens": 100,
        "finish_reasons": ["length"],
        "started_at": "1970-01-01T00:00:01+00:00",
        "ended_at": "1970-01-01T00:00:02+00:00",
    }
    verify_readback([row], [record], [])
    for key, bad in (
        ("cache_read_input_tokens", 0),
        ("reasoning_tokens", 0),
        ("request_max_tokens", 99),
        ("finish_reasons", ["stop"]),
    ):
        with pytest.raises(RuntimeError):
            verify_readback([{**row, key: bad}], [record], [])
