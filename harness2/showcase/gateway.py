"""A fixed-destination inference proxy. Executor containers have no other egress."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .budget import BudgetError, Ledger


def write_record(path, record):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2))
    os.replace(temporary, path)


def merge_stream(chunks):
    result = {"choices": [{"message": {"role": "assistant", "content": ""}}]}
    message = result["choices"][0]["message"]
    tools = {}
    for chunk in chunks:
        for key in ("id", "model", "usage"):
            if chunk.get(key) is not None:
                result[key] = chunk[key]
        for choice in chunk.get("choices", []):
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta", {})
            for key in ("content", "reasoning_content", "reasoning"):
                if delta.get(key):
                    message[key] = message.get(key, "") + delta[key]
            for tool in delta.get("tool_calls", []):
                item = tools.setdefault(
                    tool["index"],
                    {"type": "function", "id": "", "function": {"name": "", "arguments": ""}},
                )
                if tool.get("id"):
                    item["id"] = tool["id"]
                for key, value in tool.get("function", {}).items():
                    if value:
                        item["function"][key] = item["function"].get(key, "") + value
            if choice.get("finish_reason"):
                result["choices"][0]["finish_reason"] = choice["finish_reason"]
    if tools:
        message["tool_calls"] = [tools[k] for k in sorted(tools)]
    return result


class Gateway:
    def __init__(self, manifest, state):
        self.manifest, self.state = manifest, Path(state)
        self.state.mkdir(parents=True, exist_ok=True)
        self.ledger = Ledger(self.state / "budget.sqlite", manifest)
        self.ledger.interrupt_pending()
        self.sessions = {}
        self.lock = threading.Lock()
        self.admin = os.environ["SHOWCASE_ADMIN_TOKEN"]
        self.api_key = os.environ["WANDB_API_KEY"]
        if manifest.get("schema_version") == 4:
            from .transport_v4 import Circuit

            self.circuit = Circuit(
                self.state / "circuit.json",
                threshold=manifest["accounting"]["circuit_breaker_failures"],
                cooldown=manifest["accounting"]["circuit_breaker_cooldown_seconds"],
            )

    def handler(self):
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *_):
                pass

            def send_json(self, code, data):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

            def do_GET(self):
                if self.path == "/health":
                    return self.send_json(200, {"ok": True})
                if not secrets.compare_digest(
                    self.headers.get("X-Showcase-Admin", ""), gateway.admin
                ):
                    return self.send_json(403, {"error": "admin required"})
                if self.path == "/budget":
                    return self.send_json(200, gateway.ledger.snapshot())
                return self.send_json(404, {"error": "unknown endpoint"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                if length < 1 or length > 8_000_000:
                    return self.send_json(413, {"error": "invalid request size"})
                try:
                    body = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeError):
                    return self.send_json(400, {"error": "invalid JSON"})
                if self.path == "/register":
                    if not secrets.compare_digest(
                        self.headers.get("X-Showcase-Admin", ""), gateway.admin
                    ):
                        return self.send_json(403, {"error": "admin required"})
                    if body.get("phase") not in (
                        "smoke",
                        "adapt",
                        "evaluate",
                        "judge",
                        "retry",
                        "mechanism",
                    ):
                        return self.send_json(400, {"error": "invalid phase"})
                    session = body.get("session", "")
                    if not session or not session.replace("-", "").isalnum():
                        return self.send_json(400, {"error": "invalid session"})
                    token = secrets.token_urlsafe(32)
                    with gateway.lock:
                        gateway.sessions[token] = body
                    directory = gateway.state / "sessions" / session
                    directory.mkdir(parents=True, exist_ok=True)
                    (directory / "identity.json").write_text(json.dumps(body, indent=2))
                    return self.send_json(200, {"token": token})
                if self.path != "/v1/chat/completions":
                    return self.send_json(404, {"error": "fixed chat endpoint only"})
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                with gateway.lock:
                    identity = gateway.sessions.get(token)
                if not identity:
                    return self.send_json(403, {"error": "unknown session"})

                def reject_call(code, reason, error_code="invalid_request_error"):
                    stamp = time.time()
                    call_id = "rejected-" + secrets.token_hex(16)
                    write_record(
                        gateway.state / "sessions" / identity["session"] / (call_id + ".json"),
                        {
                            "call_id": call_id,
                            "started": stamp,
                            "ended": stamp,
                            "request": body,
                            "identity": identity,
                            "response": None,
                            "error": {"http_status": code, "body": reason},
                            "forwarded_to_provider": False,
                            "cost_usd": 0,
                        },
                    )
                    return self.send_json(
                        code,
                        {
                            "error": {
                                "message": reason,
                                "type": "invalid_request_error",
                                "code": error_code,
                            }
                        },
                    )

                expected = gateway.manifest["models"][
                    "judge" if identity.get("role") == "judge" else "actor"
                ]
                if (gateway.state / "halted.json").exists():
                    return reject_call(
                        402, "Campaign halted after a budget or usage gate; no new paid requests"
                    )
                if body.get("model") != expected:
                    return reject_call(400, "model differs from manifest")
                # One provider request per invocation; the client owns explicit retries.
                requested = body.pop("max_completion_tokens", None)
                body["max_tokens"] = min(
                    requested or body.get("max_tokens") or gateway.manifest["max_output_tokens"],
                    gateway.manifest["max_output_tokens"],
                )
                request_bytes = len(json.dumps(body).encode())
                v2 = gateway.manifest.get("schema_version", 1) >= 2
                if v2:
                    if request_bytes > gateway.manifest["max_request_bytes"]:
                        return reject_call(
                            413,
                            "Request body exceeds the byte transport limit; read smaller evidence slices.",
                            "request_too_large",
                        )
                    # This is a billing upper bound for text, NOT a context-window estimate.
                    # UTF-8 bytes cover text tokens; allow additional chat-template overhead.
                    input_limit = request_bytes + 8192
                    if input_limit > gateway.manifest["max_input_tokens"]:
                        return reject_call(
                            400, "Request exceeds the reserved billing bound", "request_too_large"
                        )
                    if any(
                        part.get("type") not in ("text", "input_text")
                        for msg in body.get("messages", [])
                        if isinstance(msg.get("content"), list)
                        for part in msg["content"]
                    ):
                        return reject_call(400, "Only text inputs are qualified for this campaign")
                else:
                    input_limit = None
                    if request_bytes + 4096 > gateway.manifest["max_input_tokens"]:
                        return reject_call(413, "conservative input limit exceeded")
                if body.get("stream"):
                    body["stream_options"] = {"include_usage": True}
                if gateway.manifest.get("schema_version") == 4:
                    from .transport_v4 import forward

                    return forward(self, gateway, identity, body, input_limit)
                try:
                    call_id = gateway.ledger.reserve(
                        identity["session"],
                        identity["phase"],
                        expected,
                        body["max_tokens"],
                        input_limit,
                    )
                except BudgetError as exc:
                    if gateway.manifest.get("schema_version", 1) >= 2:
                        with gateway.lock:
                            write_record(
                                gateway.state / "halted.json",
                                {
                                    "reason": str(exc),
                                    "session": identity["session"],
                                    "time": time.time(),
                                },
                            )
                    return reject_call(402, str(exc))
                directory = gateway.state / "sessions" / identity["session"]
                started = time.time()
                record = {
                    "call_id": call_id,
                    "started": started,
                    "request": body,
                    "identity": identity,
                    "forwarded_to_provider": True,
                }
                path = directory / (call_id + ".json")
                write_record(path, record)
                request = urllib.request.Request(
                    "https://api.inference.wandb.ai/v1/chat/completions",
                    data=json.dumps(body).encode(),
                    headers={
                        "Authorization": "Bearer " + gateway.api_key,
                        "Content-Type": "application/json",
                        "OpenAI-Project": gateway.manifest["project"],
                    },
                )
                response = None
                rejected = None
                error = None
                try:
                    with urllib.request.urlopen(request, timeout=240) as upstream:
                        self.send_response(200)
                        self.send_header("Content-Type", upstream.headers.get("Content-Type"))
                        self.end_headers()
                        if body.get("stream"):
                            chunks = []
                            client_alive = True
                            for line in upstream:
                                if client_alive:
                                    try:
                                        self.wfile.write(line)
                                        self.wfile.flush()
                                    except (BrokenPipeError, ConnectionResetError):
                                        client_alive = False
                                if line.startswith(b"data:") and b"[DONE]" not in line:
                                    try:
                                        chunks.append(json.loads(line[5:].strip()))
                                    except ValueError:
                                        pass
                            response = merge_stream(chunks)
                        else:
                            raw = upstream.read()
                            response = json.loads(raw)
                            try:
                                self.wfile.write(raw)
                            except (BrokenPipeError, ConnectionResetError):
                                pass
                except urllib.error.HTTPError as exc:
                    detail = exc.read().decode(errors="replace")
                    error = {"http_status": exc.code, "body": detail}
                    if 400 <= exc.code < 500:
                        rejected = "Provider rejected HTTP " + str(exc.code)
                    try:
                        try:
                            provider_error = json.loads(detail)
                        except ValueError:
                            provider_error = {
                                "error": {"message": detail, "type": "provider_error"}
                            }
                        # Preserve context_length_exceeded and the provider's message so
                        # OpenCode can distinguish context overflow from transport errors.
                        self.send_json(exc.code, provider_error)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                except Exception as exc:
                    error = {"type": type(exc).__name__, "message": str(exc)}
                finally:
                    gateway.ledger.finish(
                        call_id, (response or {}).get("usage"), rejection=rejected
                    )
                    record.update(ended=time.time(), response=response, error=error)
                    write_record(path, record)

        return Handler


def main():
    manifest = json.loads(Path(os.environ["SHOWCASE_MANIFEST"]).read_text())
    gateway = Gateway(manifest, os.environ["SHOWCASE_STATE"])
    fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    saved = gateway.state / "manifest.sha256"
    if saved.exists() and saved.read_text() != fingerprint:
        raise SystemExit("Existing campaign belongs to a different manifest")
    saved.write_text(fingerprint)
    ThreadingHTTPServer(("0.0.0.0", 8080), gateway.handler()).serve_forever()


if __name__ == "__main__":
    main()
