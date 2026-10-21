"""One retry owner, bounded accounting, and a shared provider circuit for v4."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid

from .budget import BudgetError


class Circuit:
    def __init__(self, path, *, threshold=3, cooldown=60):
        self.path, self.threshold, self.cooldown = path, threshold, cooldown
        self.condition = threading.Condition()
        self.probing = False
        self.state = json.loads(path.read_text()) if path.exists() else {"failures": 0, "until": 0}

    def save(self):
        from .gateway import write_record

        write_record(self.path, self.state)

    def enter(self):
        started = time.monotonic()
        with self.condition:
            while self.state["failures"] >= self.threshold:
                remaining = self.state["until"] - time.time()
                if remaining <= 0 and not self.probing:
                    self.probing = True
                    break
                self.condition.wait(timeout=max(0.05, min(1, remaining)) if remaining > 0 else 0.2)
        return time.monotonic() - started

    def finish(self, transport_failure):
        with self.condition:
            self.probing = False
            if transport_failure:
                self.state["failures"] += 1
                if self.state["failures"] >= self.threshold:
                    self.state["until"] = time.time() + self.cooldown
            else:
                self.state = {"failures": 0, "until": 0}
            self.save()
            self.condition.notify_all()


def response_headers(headers):
    allowed = {
        "content-type",
        "date",
        "retry-after",
        "retry-after-ms",
        "request-id",
        "x-request-id",
        "x-correlation-id",
        "x-amzn-requestid",
        "cf-ray",
    }
    return {
        k.lower(): v
        for k, v in headers.items()
        if k.lower() in allowed or k.lower().startswith("x-ratelimit-")
    }


def forward(handler, gateway, identity, body, input_limit):
    from .gateway import merge_stream, write_record

    operation = uuid.uuid4().hex
    directory = gateway.state / "sessions" / identity["session"]
    for attempt in range(gateway.manifest["accounting"]["max_transport_retries"] + 1):
        queued = gateway.circuit.enter()
        try:
            call_id = gateway.ledger.reserve(
                identity["session"],
                identity["phase"] if attempt == 0 or identity["phase"] == "smoke" else "retry",
                body["model"],
                body["max_tokens"],
                input_limit,
            )
        except BudgetError as exc:
            gateway.circuit.finish(False)
            write_record(gateway.state / "halted.json", {"reason": str(exc), "time": time.time()})
            return handler.send_json(402, {"error": {"message": str(exc)}})
        path = directory / (call_id + ".json")
        record = {
            "call_id": call_id,
            "operation_id": operation,
            "transport_attempt": attempt + 1,
            "started": time.time(),
            "queue_seconds": queued,
            "request": body,
            "identity": identity,
            "forwarded_to_provider": True,
            "opencode_session_id": handler.headers.get("x-session-affinity"),
            "opencode_parent_session_id": handler.headers.get("x-parent-session-id"),
        }
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
        response, error, chunks = None, None, []
        sent = transport_failure = False
        status = 502
        try:
            with urllib.request.urlopen(request, timeout=240) as upstream:
                record["response_headers"] = response_headers(upstream.headers)
                handler.send_response(200)
                handler.send_header("Content-Type", upstream.headers.get("Content-Type"))
                handler.end_headers()
                sent = True
                if body.get("stream"):
                    client_alive, done = True, False
                    # Keep each observed chunk on disk even if the stream is interrupted.
                    with path.with_suffix(".sse").open("wb") as raw:
                        for line in upstream:
                            raw.write(line)
                            raw.flush()
                            if client_alive:
                                try:
                                    handler.wfile.write(line)
                                    handler.wfile.flush()
                                except (BrokenPipeError, ConnectionResetError):
                                    client_alive = False
                            if line.startswith(b"data:"):
                                if line[5:].strip() == b"[DONE]":
                                    done = True
                                else:
                                    chunks.append(json.loads(line[5:].strip()))
                    response = merge_stream(chunks)
                    if not done:
                        raise OSError("Provider stream ended without its completion marker")
                else:
                    raw = upstream.read()
                    path.with_suffix(".response").write_bytes(raw)
                    response = json.loads(raw)
                    try:
                        handler.wfile.write(raw)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
        except urllib.error.HTTPError as exc:
            status = exc.code
            record["response_headers"] = response_headers(exc.headers)
            error = {"http_status": status, "body": exc.read().decode(errors="replace")}
            transport_failure = status in (408, 429) or status >= 500
        except Exception as exc:
            response = merge_stream(chunks) if chunks else response
            error = {"type": type(exc).__name__, "message": str(exc)}
            transport_failure = True
        finally:
            gateway.ledger.finish(call_id, (response or {}).get("usage"))
            record.update(
                ended=time.time(),
                response=response,
                error=error,
                provider_request_id=(response or {}).get("id"),
            )
            write_record(path, record)
            gateway.circuit.finish(transport_failure)
        if response and response.get("model") != body["model"]:
            write_record(
                gateway.state / "halted.json",
                {"reason": "Returned model differs from manifest", "call_id": call_id},
            )
            return
        if any(c["status"] == "unknown" for c in gateway.ledger.snapshot()["calls"]):
            write_record(
                gateway.state / "halted.json",
                {"reason": "Usage contradicts its proven reservation", "call_id": call_id},
            )
            return
        if not error or sent:
            # Once bytes reach the agent, do not replay the request behind its back.
            return
        if (
            not transport_failure
            or attempt == gateway.manifest["accounting"]["max_transport_retries"]
        ):
            try:
                detail = json.loads(error.get("body", "{}"))
            except ValueError:
                detail = {"error": error}
            return handler.send_json(status, detail if detail else {"error": error})
        time.sleep(min(2**attempt, 4))
