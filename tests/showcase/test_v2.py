import io
import json
import shutil
import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from harness2.showcase.budget import BudgetError, Ledger
from harness2.showcase.gateway import Gateway
from harness2.showcase.protocol import (
    harness_record,
    materialize_evidence,
    summarize,
    tree_hashes,
    validate_completion,
    write_json,
)
from harness2.showcase.runtime import ROOT, CampaignStop
from harness2.showcase.v2 import (
    V2Adapter,
    V2Proposer,
    adapt_one,
    allocation,
    schedule,
    stopped_cost_floor,
)


def manifest():
    return json.loads(Path("showcase/pilot-v2.json").read_text())


def completed(tmp_path):
    (tmp_path / "context/harness").mkdir(parents=True)
    (tmp_path / "context/harness/systemprompt.md").write_text("Check deliverables.")
    (tmp_path / "output").mkdir()
    (tmp_path / "output/answer.md").write_text(
        "<notes>The current harness already handles this case.</notes>"
    )
    write_json(
        tmp_path / "output/completion.json",
        {
            "status": "complete",
            "decision": "keep",
            "rationale": "No observed defect justifies an edit.",
            "affected_files": [],
            "cited_span_ids": [],
        },
    )
    (tmp_path / "events.jsonl").write_text(
        json.dumps({"type": "step_finish", "part": {"reason": "stop"}}) + "\n"
    )
    return {"timed_out": False, "child_exit_code": 0, "collection_status": "complete"}


def test_saved_explicit_noop_is_valid(tmp_path):
    result = completed(tmp_path)
    got = validate_completion(
        tmp_path,
        tree_hashes(tmp_path / "context/harness"),
        result,
        tmp_path / "events.jsonl",
        editing=True,
    )
    assert got["decision"] == "keep"


@pytest.mark.parametrize("prefix", ["", "context/harness/"])
def test_receipt_file_aliases_preserve_exact_changed_set(tmp_path, prefix):
    result = completed(tmp_path)
    before = tree_hashes(tmp_path / "context/harness")
    (tmp_path / "context/harness/systemprompt.md").write_text("Verify the written files.")
    path = tmp_path / "output/completion.json"
    receipt = json.loads(path.read_text())
    receipt.update(decision="change", affected_files=[prefix + "systemprompt.md"])
    write_json(path, receipt)
    saved = path.read_bytes()
    validated = validate_completion(
        tmp_path, before, result, tmp_path / "events.jsonl", editing=True
    )
    assert validated["affected_files"] == ["systemprompt.md"]
    assert path.read_bytes() == saved
    if prefix:
        assert validated["supplied_affected_files"] == receipt["affected_files"]


@pytest.mark.parametrize(
    "affected",
    [
        [],
        ["context/harness/../systemprompt.md"],
        ["/work/context/harness/systemprompt.md"],
        ["systemprompt.md", "context/harness/systemprompt.md"],
        ["context/harness/guardrails.md"],
        [None],
    ],
)
def test_receipt_aliases_do_not_accept_omitted_extra_or_escaping_files(tmp_path, affected):
    result = completed(tmp_path)
    before = tree_hashes(tmp_path / "context/harness")
    (tmp_path / "context/harness/systemprompt.md").write_text("Verify the written files.")
    path = tmp_path / "output/completion.json"
    receipt = json.loads(path.read_text())
    receipt.update(decision="change", affected_files=affected)
    write_json(path, receipt)
    with pytest.raises(ValueError):
        validate_completion(tmp_path, before, result, tmp_path / "events.jsonl", editing=True)


@pytest.mark.parametrize(
    "failure", ["narration", "timeout", "exit", "collector", "length", "false_keep"]
)
def test_unfinished_or_misdescribed_proposals_are_rejected(tmp_path, failure):
    result = completed(tmp_path)
    before = tree_hashes(tmp_path / "context/harness")
    if failure == "narration":
        (tmp_path / "output/completion.json").unlink()
    elif failure == "timeout":
        result["timed_out"] = True
    elif failure == "exit":
        result["child_exit_code"] = 1
    elif failure == "collector":
        result["collection_status"] = "incomplete"
    elif failure == "length":
        (tmp_path / "events.jsonl").write_text(
            json.dumps({"type": "step_finish", "part": {"reason": "length"}})
        )
    else:
        (tmp_path / "context/harness/systemprompt.md").write_text("Changed after receipt")
    with pytest.raises(ValueError):
        validate_completion(tmp_path, before, result, tmp_path / "events.jsonl", editing=True)


def test_source_only_changes_do_not_become_executable_changes(tmp_path):
    shutil.copytree(ROOT / "third_party/jb_runner/harness_baseline_oc", tmp_path / "harness")
    before = harness_record(tmp_path / "harness", "test", manifest())
    (tmp_path / "harness/scripts/README.md").write_text("Notes changed.")
    metadata = harness_record(tmp_path / "harness", "test", manifest())
    assert metadata["source_fingerprint"] != before["source_fingerprint"]
    assert metadata["rendered_fingerprint"] == before["rendered_fingerprint"]
    (tmp_path / "harness/guardrails.md").write_text("Check output files before finishing.")
    assert (
        harness_record(tmp_path / "harness", "test", manifest())["rendered_fingerprint"]
        != before["rendered_fingerprint"]
    )


def test_receipt_retry_receives_the_rejection_and_actual_changed_paths(tmp_path, monkeypatch):
    source = tmp_path / "context"
    (source / "harness").mkdir(parents=True)
    (source / "harness/guardrails.md").write_text("Original rule")
    missions = []

    def run_agent(work, prompt, **identity):
        assert prompt == identity["entry_message"]
        assert "Improve the harness" in prompt
        attempt = len(missions) + 1
        missions.append(identity["entry_message"])
        (work / "context/harness/guardrails.md").write_text("Check the deliverable")
        if attempt == 1:
            cache = work / "context/harness/scripts/__pycache__/check.cpython-312.pyc"
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b"compiled cache")
        (work / "output/answer.md").write_text("<notes>Added a completion check.</notes>")
        write_json(
            work / "output/completion.json",
            {
                "status": "complete",
                "decision": "change",
                "rationale": "Verify the files on disk.",
                "affected_files": ["guardrails.md"],
                "cited_span_ids": [],
            },
        )
        directory = tmp_path / "executions" / str(attempt)
        directory.mkdir(parents=True)
        (directory / "events.jsonl").write_text(
            json.dumps({"type": "step_finish", "part": {"reason": "stop"}})
        )
        return {
            "session": str(attempt),
            "child_exit_code": 0,
            "collection_status": "complete",
        }, directory

    runtime = SimpleNamespace(state=tmp_path, manifest=manifest(), run_agent=run_agent)
    adapter = V2Adapter(runtime, "harness2", 1)
    monkeypatch.setattr("harness2.showcase.v2.export_session", lambda *a: None)
    monkeypatch.setattr("harness2.showcase.v2.session_cost", lambda *a: 0)
    monkeypatch.setattr("harness2.core.proposer.time.sleep", lambda *a: None)
    proposer = V2Proposer(runtime, adapter, "domain/task1", tmp_path / "audit")
    reply = proposer.call("Improve the harness", source, "c1.a1", harvest="context/harness")
    assert reply.ok and reply.attempts == 2
    assert "Observed changed paths" not in missions[0]
    assert "scripts/__pycache__/check.cpython-312.pyc" in missions[1]
    assert "Remove incidental compiler caches" in missions[1]


def test_diagnostics_bound_errors_and_usage_remain_grade_free():
    rows = [
        {
            "span_id": f"{i:016x}",
            "operation_name": "execute_tool",
            "tool_name": "bash",
            "tool_call_arguments": '{"command":"false"}',
            "status_code": "ERROR",
            "status_message": "exit 1",
            "started_at": "2026-10-19T00:00:00Z",
            "ended_at": "2026-10-19T00:00:01Z",
        }
        for i in range(1000)
    ]
    rows.append(
        {
            "span_id": "f" * 16,
            "operation_name": "chat",
            "finish_reasons": ["length"],
            "request_max_tokens": 32768,
            "input_tokens": 90,
            "output_tokens": 32768,
        }
    )
    text = summarize(rows, [], "https://example.com/trace")
    got = json.loads(text)
    assert len(text.encode()) <= 8192
    assert got["omitted"] and got["errors"]
    assert got["limits"][0]["explicit_output_truncation"]
    assert got["deliverable_present"] is False
    assert "score" not in got
    assert text == summarize(list(reversed(rows)), [], "https://example.com/trace")


def evidence_fixture(tmp_path, *, phase="adapt", arm="baseline", replicate=1):
    session = "a" * 32
    cwd, state = tmp_path / "work", tmp_path / "state"
    (cwd / "context/rollouts").mkdir(parents=True)
    (cwd / "context/rollouts/base1.md").write_text(f"SHOWCASE_SESSION:{session}")
    directory = state / "executions" / session
    write_json(
        directory / "identity.json",
        {
            "role": "solver",
            "phase": phase,
            "task": "domain/task1",
            "arm": arm,
            "adaptation_replicate": replicate,
        },
    )
    write_json(
        directory / "stored-spans.json",
        [{"span_id": "1" * 16, "operation_name": "chat", "input_tokens": 3, "output_tokens": 2}],
    )
    write_json(directory / "trace.json", {"url": "https://example.com/trace"})
    write_json(directory / "file-inventory.json", [])
    (directory / "events.jsonl").write_text('{"type":"text"}\n')
    runtime = SimpleNamespace(
        state=state,
        manifest=manifest(),
        calls=lambda _: [{"call_id": "call", "request": {"messages": []}}],
    )
    return cwd, runtime


def test_raw_evidence_identical_and_diagnostics_only_added_to_treatment(tmp_path):
    cwd, runtime = evidence_fixture(tmp_path)
    other = tmp_path / "other"
    shutil.copytree(cwd, other)
    args = dict(task="domain/task1", arm="harness2", replicate=1)
    materialize_evidence(cwd, runtime, diagnostics=False, **args)
    materialize_evidence(other, runtime, diagnostics=True, **args)
    assert tree_hashes(cwd / "context/evidence") == tree_hashes(other / "context/evidence")
    assert not (cwd / "context/diagnostics").exists()
    assert (other / "context/diagnostics/INDEX.json").stat().st_size <= 16384


@pytest.mark.parametrize(
    "identity",
    [{"phase": "evaluate"}, {"phase": "judge"}, {"arm": "diagnostics"}, {"replicate": 2}],
)
def test_improver_cannot_import_heldout_judge_other_condition_or_replicate(tmp_path, identity):
    cwd, runtime = evidence_fixture(tmp_path, **identity)
    with pytest.raises(CampaignStop):
        materialize_evidence(
            cwd, runtime, diagnostics=False, task="domain/task1", arm="harness2", replicate=1
        )


def test_new_spend_cannot_reset_original_ceiling(tmp_path):
    m = manifest()
    m["budget_usd"] = "100"
    with pytest.raises(BudgetError, match="lifetime"):
        Ledger(tmp_path / "ledger.sqlite", m)


def test_concurrent_bucket_reservations_preserve_judge_and_mechanism_reserve(tmp_path):
    m = manifest()
    m["budget_buckets"]["main"]["usd"] = "0.0002"
    ledger = Ledger(tmp_path / "ledger.sqlite", m)

    def reserve(i):
        try:
            return ledger.reserve(str(i), "adapt", m["models"]["actor"], 100, 100)
        except BudgetError:
            return None

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(reserve, range(20)))
    assert sum(r is not None for r in results) == 3
    assert ledger.snapshot()["reserved_usd"] <= 0.0002
    assert ledger.reserve("judge", "judge", m["models"]["judge"], 100, 100)


def test_schedule_has_task_level_repeats_and_interleaved_conditions(tmp_path):
    runtime = SimpleNamespace(manifest=manifest())
    jobs = schedule(runtime, 2)
    assert len(jobs) == 72
    assert len({j["task"] for j in jobs}) == 6
    assert jobs == schedule(runtime, 2)
    assert len(schedule(runtime, 1)) == 36
    for i in range(0, len(jobs), 3):
        assert {j["arm"] for j in jobs[i : i + 3]} == {"baseline", "harness2", "diagnostics"}


@pytest.mark.parametrize("receipt", [None, {"passed": False}])
def test_missing_or_failed_qualification_blocks_even_cached_allocation(tmp_path, receipt):
    if receipt is not None:
        write_json(tmp_path / "qualification.json", receipt)
    write_json(tmp_path / "allocation.json", {"repetitions": 2})
    with pytest.raises(CampaignStop, match="Qualification has not passed"):
        allocation(SimpleNamespace(state=tmp_path))


def test_stopped_projection_counts_shared_baselines_once_and_all_retries(tmp_path):
    calls = []
    for session, role, stage, cost in [
        ("base1", "solver", "base1", 100000),
        ("base2", "solver", "base2", 200000),
        ("proposal1", "improver", "inv1.a1", 300000),
        ("proposal2", "improver", "inv1.a1", 200000),
    ]:
        calls.append({"session": session, "spent": cost})
        write_json(
            tmp_path / "gateway/sessions" / session / "identity.json",
            {
                "phase": "smoke",
                "task": "qualification/task1",
                "role": role,
                "stage": stage,
                "run_id": "shared/" + stage,
            },
        )
    runtime = SimpleNamespace(
        state=tmp_path,
        manifest=manifest(),
        budget=lambda: {
            "calls": calls,
            "unresolved": 0,
            "spent_usd": 0.8,
        },
    )
    result = stopped_cost_floor(runtime)
    reduced = result["candidates"][1]
    assert result["lower_bound"] and reduced["projected_judging_usd"] is None
    # Twelve domain/replicate groups: one shared baseline pair, two independent
    # proposal paths including the failed attempt, and 36 transfer executions.
    assert reduced["projected_adaptation_usd"] == pytest.approx(23.4)
    assert reduced["projected_main_usd"] == pytest.approx(34.2)


def test_failed_adaptation_freezes_baseline_instead_of_dropping_transfer(tmp_path, monkeypatch):
    runtime = SimpleNamespace(state=tmp_path, manifest=manifest())
    monkeypatch.setattr(
        "harness2.showcase.v2.run_task",
        lambda *a, **k: SimpleNamespace(to_dict=lambda: {"selections": {"seq": {"ok": False}}}),
    )
    dest = adapt_one(runtime, "domain", "harness2", 1)
    got = json.loads((dest / "freeze.json").read_text())
    assert got["adoption"] == "baseline_fallback" and not got["executable_change"]
    assert V2Adapter(runtime, "harness2", 1).store != V2Adapter(runtime, "harness2", 2).store
    assert V2Adapter(runtime, "harness2", 1).store != V2Adapter(runtime, "diagnostics", 1).store


def test_context_errors_preserve_provider_shape_and_bytes_are_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOWCASE_ADMIN_TOKEN", "admin")
    monkeypatch.setenv("WANDB_API_KEY", "test-key")
    m = manifest()
    m["model_context_tokens"] = 1  # A byte-count comparison would reject all requests.
    seen = []
    error = {
        "error": {
            "message": "maximum context length exceeded",
            "code": "context_length_exceeded",
            "type": "invalid_request_error",
        }
    }

    def provider(request, **kwargs):
        seen.append(request)
        raise urllib.error.HTTPError(
            request.full_url, 400, "context", {}, io.BytesIO(json.dumps(error).encode())
        )

    monkeypatch.setattr("harness2.showcase.gateway.urllib.request.urlopen", provider)
    gateway = Gateway(m, tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), gateway.handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        token = httpx.post(
            base + "/register",
            headers={"X-Showcase-Admin": "admin"},
            json={"session": "one", "phase": "smoke", "role": "solver"},
        ).json()["token"]
        body = {
            "model": m["models"]["actor"],
            "messages": [{"role": "user", "content": "small"}],
            "max_tokens": 32,
        }
        headers = {"Authorization": "Bearer " + token}
        response = httpx.post(base + "/v1/chat/completions", json=body, headers=headers)
        assert response.status_code == 400 and response.json() == error and len(seen) == 1
        body["messages"][0]["content"] = "x" * m["max_request_bytes"]
        response = httpx.post(base + "/v1/chat/completions", json=body, headers=headers)
        assert (
            response.status_code == 413 and response.json()["error"]["code"] == "request_too_large"
        )
        assert len(seen) == 1
        assert gateway.ledger.snapshot()["spent_usd"] == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_context_path_error_is_not_a_model_context_rejection():
    rows = [
        {
            "span_id": "1" * 16,
            "operation_name": "execute_tool",
            "tool_name": "read",
            "status_code": "ERROR",
            "status_message": "context/harness/missing.txt not found",
        },
        {
            "span_id": "2" * 16,
            "operation_name": "chat",
            "status_code": "ERROR",
            "status_message": '{"code":"context_length_exceeded"}',
            "request_max_tokens": 32768,
        },
    ]
    got = json.loads(summarize(rows, [], "https://example.com/trace"))
    assert len(got["limits"]) == 1
    assert got["limits"][0]["span_id"] == "2" * 16
    assert got["limits"][0]["context_rejection"]
