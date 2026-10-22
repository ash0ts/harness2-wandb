import io
import json
import threading
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from harness2.showcase.gateway import Gateway
from harness2.showcase.telemetry_v4 import session_groups
from harness2.showcase.transport_v4 import Circuit, model_identity_problem, normalize_saved_call


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOWCASE_ADMIN_TOKEN", "test-admin")
    monkeypatch.setenv("WANDB_API_KEY", "test-key")
    manifest = json.loads(Path("showcase/study-v4.json").read_text())
    gateway = Gateway(manifest, tmp_path)
    http = ThreadingHTTPServer(("127.0.0.1", 0), gateway.handler())
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{http.server_port}"
    token = httpx.post(
        base + "/register",
        headers={"X-Showcase-Admin": "test-admin"},
        json={"session": "session", "phase": "smoke", "role": "solver"},
    ).json()["token"]
    yield gateway, base, {"Authorization": "Bearer " + token, "x-session-affinity": "actual-root"}
    http.shutdown()
    http.server_close()
    thread.join()


def request_body(gateway, **kw):
    return {
        "model": gateway.manifest["models"]["actor"],
        "messages": [{"role": "user", "content": "task"}],
        "max_tokens": 100,
        **kw,
    }


def stream(model, done=True):
    chunks = [
        {
            "id": "provider-request",
            "model": model,
            "choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}],
        }
    ]
    if done:
        chunks.append({"usage": {"prompt_tokens": 20, "completion_tokens": 2}, "choices": []})
    content = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks)
    if done:
        content += b"data: [DONE]\n\n"
    response = io.BytesIO(content)
    response.headers = {"Content-Type": "text/event-stream", "X-Request-ID": "header-request"}
    return response


def test_transport_owner_counts_every_attempt_and_preserves_headers(server, monkeypatch, tmp_path):
    gateway, base, headers = server
    attempts = []

    def provider(request, **kwargs):
        attempts.append(request)
        if len(attempts) < 3:
            raise urllib.error.HTTPError(
                request.full_url, 503, "unavailable", {}, io.BytesIO(b"{}")
            )
        return stream(gateway.manifest["models"]["actor"])

    monkeypatch.setattr("harness2.showcase.transport_v4.urllib.request.urlopen", provider)
    response = httpx.post(
        base + "/v1/chat/completions",
        headers=headers,
        json=request_body(gateway, stream=True),
        timeout=10,
    )
    assert response.status_code == 200
    assert len(attempts) == 3
    snapshot = gateway.ledger.snapshot()
    assert snapshot["bounded_unknown_requests"] == 2
    assert snapshot["observed_cost_usd"] > 0
    assert all(r["phase"] == "smoke" for r in snapshot["calls"])
    records = [
        json.loads(p.read_text())
        for p in (tmp_path / "sessions/session").glob("*.json")
        if p.name != "identity.json"
    ]
    completed = next(r for r in records if r["response"])
    assert completed["provider_request_id"] == "provider-request"
    assert completed["response_headers"]["x-request-id"] == "header-request"
    assert completed["opencode_session_id"] == "actual-root"


def test_interrupted_stream_keeps_partial_records_without_replay(server, monkeypatch, tmp_path):
    gateway, base, headers = server
    calls = []

    def provider(*args, **kwargs):
        calls.append(1)
        return stream(gateway.manifest["models"]["actor"], done=False)

    monkeypatch.setattr("harness2.showcase.transport_v4.urllib.request.urlopen", provider)
    httpx.post(
        base + "/v1/chat/completions", headers=headers, json=request_body(gateway, stream=True)
    )
    assert len(calls) == 1
    assert gateway.ledger.snapshot()["bounded_unknown_requests"] == 1
    assert next((tmp_path / "sessions/session").glob("*.sse")).read_bytes().startswith(b"data:")


def error_stream(code=500, prefix=b""):
    error = {"error": {"message": "Provider failed", "code": code}}
    response = io.BytesIO(prefix + b"data: " + json.dumps(error).encode() + b"\n\ndata: [DONE]\n\n")
    response.headers = {"Content-Type": "text/event-stream"}
    return response


def records_at(tmp_path):
    return [
        json.loads(p.read_text())
        for p in (tmp_path / "sessions/session").glob("*.json")
        if p.name != "identity.json"
    ]


def test_error_only_stream_retries_before_forwarding_and_keeps_all_costs(
    server, monkeypatch, tmp_path
):
    gateway, base, headers = server
    calls = []

    def provider(*args, **kwargs):
        calls.append(1)
        return error_stream() if len(calls) < 3 else stream(gateway.manifest["models"]["actor"])

    monkeypatch.setattr("harness2.showcase.transport_v4.urllib.request.urlopen", provider)
    response = httpx.post(
        base + "/v1/chat/completions",
        headers=headers,
        json=request_body(gateway, stream=True),
        timeout=10,
    )
    assert response.status_code == 200
    assert "Provider failed" not in response.text
    assert "provider-request" in response.text
    assert len(calls) == 3
    assert gateway.ledger.snapshot()["bounded_unknown_requests"] == 2
    rows = records_at(tmp_path)
    failures = [r for r in rows if r["error"]]
    assert len(failures) == 2
    assert all(r["error"]["http_status"] == 500 and not r["response_forwarded"] for r in failures)
    assert all(model_identity_problem(r) is None for r in rows)
    assert not (tmp_path / "halted.json").exists()


@pytest.mark.parametrize("code,attempts", [(500, 3), (400, 1)])
def test_provider_error_limit_and_circuit(server, monkeypatch, tmp_path, code, attempts):
    gateway, base, headers = server
    calls = []

    def provider(*args, **kwargs):
        calls.append(1)
        return error_stream(code)

    monkeypatch.setattr("harness2.showcase.transport_v4.urllib.request.urlopen", provider)
    response = httpx.post(
        base + "/v1/chat/completions",
        headers=headers,
        json=request_body(gateway, stream=True),
        timeout=10,
    )
    assert response.status_code == code
    assert response.json()["error"]["code"] == code
    assert len(calls) == attempts
    assert gateway.ledger.snapshot()["bounded_unknown_requests"] == attempts
    assert gateway.circuit.state["failures"] == (3 if code == 500 else 0)
    assert not (tmp_path / "halted.json").exists()


def test_error_after_partial_output_is_not_replayed(server, monkeypatch, tmp_path):
    gateway, base, headers = server
    prefix = stream(gateway.manifest["models"]["actor"], done=False).read()
    calls = []

    def provider(*args, **kwargs):
        calls.append(1)
        return error_stream(prefix=prefix)

    monkeypatch.setattr("harness2.showcase.transport_v4.urllib.request.urlopen", provider)
    response = httpx.post(
        base + "/v1/chat/completions", headers=headers, json=request_body(gateway, stream=True)
    )
    assert response.status_code == 200
    assert "Provider failed" in response.text
    assert len(calls) == 1
    row = records_at(tmp_path)[0]
    assert row["response_forwarded"] and row["error"]["http_status"] == 500
    assert row["response"]["choices"][0]["message"]["content"] == "done"
    assert gateway.circuit.state["failures"] == 1


def test_historical_error_recovery_is_read_only_and_identity_gate_remains(tmp_path):
    from harness2.showcase.runtime import CampaignStop, Runtime

    manifest = json.loads(Path("showcase/study-v4.json").read_text())
    runtime = Runtime(manifest, tmp_path)
    path = tmp_path / "gateway/sessions/session/call.json"
    path.parent.mkdir(parents=True)
    record = {
        "request": {"model": manifest["models"]["actor"]},
        "response": {"choices": [{"message": {"role": "assistant", "content": ""}}]},
        "error": None,
        "started": 1,
        "ended": 2,
        "call_id": "call",
    }
    original = json.dumps(record)
    path.write_text(original)
    path.with_suffix(".sse").write_bytes(error_stream().read())
    fixed = normalize_saved_call(record, path)
    assert fixed["recovered_from_raw_stream"]
    assert fixed["error"]["http_status"] == 500
    assert fixed["response"]["model"] == ""
    assert fixed["response_model_missing"]
    assert model_identity_problem(fixed) is None
    runtime.budget = lambda: {"calls": []}
    assert runtime.wait_calls("session")[0]["error"]["http_status"] == 500
    assert path.read_text() == original
    assert record["error"] is None
    wrong = {**record, "response": {"model": "other-model"}}
    path.write_text(json.dumps(wrong))
    with pytest.raises(CampaignStop, match="model differs"):
        runtime.wait_calls("session")
    assert "missing its model identity" in model_identity_problem(record)


def test_failed_request_readback_accepts_missing_model_without_inventing_one(tmp_path):
    from harness2.showcase.telemetry import verify_readback

    record = {
        "call_id": "error-call",
        "started": 1,
        "ended": 2,
        "request": {"model": "fixed", "messages": [], "max_tokens": 100},
        "response": {"error": {"message": "stopped"}},
        "error": {"http_status": 402},
    }
    fixed = normalize_saved_call(record, tmp_path / "call.json")
    row = {
        "operation_name": "chat",
        "request_model": "fixed",
        "response_model": "",
        "request_max_tokens": 100,
        "input_messages": [],
        "output_messages": [],
        "started_at": "1970-01-01T00:00:01+00:00",
        "ended_at": "1970-01-01T00:00:02+00:00",
        "raw_span_dump": json.dumps(
            {"attributes": {"harness2": {"provider_call_id": "error-call"}}}
        ),
    }
    verify_readback([row], [fixed], [])
    assert "model" not in record["response"]
    with pytest.raises(RuntimeError, match="response model differs"):
        verify_readback([{**row, "response_model": "invented"}], [fixed], [])


def test_normal_stream_is_forwarded_byte_for_byte(server, monkeypatch):
    gateway, base, headers = server
    expected = stream(gateway.manifest["models"]["actor"]).read()
    monkeypatch.setattr(
        "harness2.showcase.transport_v4.urllib.request.urlopen",
        lambda *args, **kwargs: stream(gateway.manifest["models"]["actor"]),
    )
    response = httpx.post(
        base + "/v1/chat/completions", headers=headers, json=request_body(gateway, stream=True)
    )
    assert response.content == expected


def test_circuit_persists_failure_threshold_and_closes_after_probe(tmp_path):
    circuit = Circuit(tmp_path / "circuit.json", cooldown=0)
    for _ in range(3):
        circuit.finish(True)
    restarted = Circuit(circuit.path, cooldown=0)
    assert restarted.state["failures"] == 3
    restarted.enter()
    assert restarted.probing
    restarted.finish(False)
    assert restarted.state == {"failures": 0, "until": 0}


def test_actual_child_sessions_and_titles_are_separate():
    calls = [
        dict(
            opencode_session_id="root", request={"messages": [{"role": "user", "content": "task"}]}
        ),
        dict(
            opencode_session_id="child",
            request={"messages": [{"role": "user", "content": "delegation"}]},
        ),
        dict(
            opencode_session_id="root",
            request={"messages": [{"role": "system", "content": "You are a title generator."}]},
        ),
    ]
    audit = [
        dict(type="audit_session", session={"id": "root", "parent_id": None}),
        dict(type="audit_session", session={"id": "child", "parent_id": "root"}),
    ]
    groups = session_groups(
        "execution", calls, [{"sessionID": "child"}], [{"role": "user", "content": "task"}], audit
    )
    assert len(groups) == 3
    child = next(g for g in groups if g[0] == "child_execution")
    assert child[4] == [{"role": "user", "content": "delegation"}]
    assert len(child[3]) == 1
