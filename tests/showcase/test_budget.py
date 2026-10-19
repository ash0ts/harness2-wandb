import concurrent.futures
import json
from copy import deepcopy
from pathlib import Path

import pytest

from harness2.showcase.budget import BudgetError, Ledger
from harness2.showcase.gateway import merge_stream

ROOT = Path(__file__).resolve().parents[2]


def manifest():
    return json.loads((ROOT / "showcase/pilot.json").read_text())


def test_cache_is_inclusive_and_finalization_is_once(tmp_path):
    m = manifest()
    ledger = Ledger(tmp_path / "budget.sqlite", m)
    call = ledger.reserve("one", "smoke", m["models"]["actor"], 100)
    ledger.finish(
        call,
        {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "prompt_tokens_details": {"cached_tokens": 80},
        },
    )
    assert ledger.snapshot()["spent_usd"] == 0.000012
    with pytest.raises(BudgetError):
        ledger.finish(call, {"prompt_tokens": 100, "completion_tokens": 10})


def test_missing_usage_halts_and_survives_restart(tmp_path):
    m = manifest()
    path = tmp_path / "budget.sqlite"
    ledger = Ledger(path, m)
    call = ledger.reserve("one", "adapt", m["models"]["actor"], 100)
    ledger.finish(call, None)
    ledger = Ledger(path, m)
    assert ledger.snapshot()["reserved_usd"] > 0
    with pytest.raises(BudgetError, match="Unreconciled"):
        ledger.reserve("two", "judge", m["models"]["judge"], 100)


def test_concurrent_reservations_never_oversubscribe(tmp_path):
    m = deepcopy(manifest())
    m["budget_usd"] = "0.05"
    ledger = Ledger(tmp_path / "budget.sqlite", m)

    def attempt(_):
        try:
            return ledger.reserve("one", "adapt", m["models"]["actor"], 100)
        except BudgetError:
            return None

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        accepted = list(pool.map(attempt, range(20)))
    assert len([x for x in accepted if x]) == 1
    assert ledger.snapshot()["reserved_usd"] <= 0.05


@pytest.mark.parametrize(
    "usage",
    [
        {
            "prompt_tokens": 10,
            "completion_tokens": 1,
            "prompt_tokens_details": {"cached_tokens": 11},
        },
        {"prompt_tokens": -1, "completion_tokens": 1},
        {"prompt_tokens": 1, "completion_tokens": 999999},
        {},
    ],
)
def test_invalid_usage_is_unknown_not_zero(tmp_path, usage):
    m = manifest()
    ledger = Ledger(tmp_path / "budget.sqlite", m)
    call = ledger.reserve("one", "smoke", m["models"]["actor"], 100)
    ledger.finish(call, usage)
    assert ledger.snapshot()["calls"][0]["status"] == "unknown"
    assert ledger.snapshot()["calls"][0]["spent"] is None


def test_stream_usage_not_summed():
    chunks = [
        {
            "id": "r",
            "model": "m",
            "choices": [{"delta": {"content": "a"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 1},
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "t", "function": {"name": "read", "arguments": '{"'}}
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'x":1}'}}]},
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20}},
    ]
    result = merge_stream(chunks)
    assert result["usage"]["prompt_tokens"] == 100
    assert result["usage"]["completion_tokens"] == 20
    assert result["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == '{"x":1}'


def test_provider_record_replacement_is_atomic(tmp_path):
    from harness2.showcase.gateway import write_record

    path = tmp_path / "request.json"
    write_record(path, {"request": "started"})
    write_record(path, {"request": "started", "response": "complete"})
    assert json.loads(path.read_text())["response"] == "complete"
    assert list(tmp_path.glob("*.tmp")) == []
