import io
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx

from harness2.showcase.gateway import Gateway


def test_observer_forwards_original_stream_and_reserves_entire_request(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOWCASE_ADMIN_TOKEN", "test-admin")
    monkeypatch.setenv("WANDB_API_KEY", "test-key")
    manifest = json.loads(Path("showcase/pilot.json").read_text())
    model = manifest["models"]["actor"]
    chunks = [
        {"id": "provider-id", "model": model, "choices": [{"delta": {"content": "done"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 2}},
    ]
    original = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks)
    original += b"data: [DONE]\n\n"
    captured = []

    def provider(request, **_):
        captured.append(json.loads(request.data))
        stream = io.BytesIO(original)
        stream.headers = {"Content-Type": "text/event-stream"}
        return stream

    monkeypatch.setattr("harness2.showcase.gateway.urllib.request.urlopen", provider)
    gateway = Gateway(manifest, tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), gateway.handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        token = httpx.post(
            base + "/register",
            headers={"X-Showcase-Admin": "test-admin"},
            json={"session": "session", "phase": "smoke", "role": "solver"},
        ).json()["token"]
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "answer"}],
            "stream": True,
            "max_tokens": 100,
        }
        headers = {"Authorization": "Bearer " + token}
        response = httpx.post(base + "/v1/chat/completions", headers=headers, json=body)
        assert response.content == original
        assert captured == [{**body, "stream_options": {"include_usage": True}}]
        records = list((tmp_path / "sessions/session").glob("*.json"))
        record = json.loads(next(p for p in records if p.name != "identity.json").read_text())
        assert record["response"]["choices"][0]["message"]["content"] == "done"
        assert record["response"]["usage"]["prompt_tokens"] == 20
        assert gateway.ledger.snapshot()["unresolved"] == 0
        oversized = {**body, "tools": [{"description": "x" * manifest["max_input_tokens"]}]}
        denied = httpx.post(base + "/v1/chat/completions", headers=headers, json=oversized)
        assert denied.status_code == 413
        assert len(captured) == 1
        assert len(gateway.ledger.snapshot()["calls"]) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
