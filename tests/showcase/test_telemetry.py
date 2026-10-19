import json

from harness2.showcase.telemetry import diagnostics, spans_from_records, tool_events


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
