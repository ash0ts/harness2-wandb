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
from harness2.showcase.transport_v4 import Circuit


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
