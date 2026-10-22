"""One retry owner, bounded accounting, and a shared provider circuit for v4."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid

from .budget import BudgetError


class ProviderResponseError(Exception):
    """An error carried inside an HTTP 200 provider response."""

    def __init__(self, detail):
        self.detail = detail
        code = detail.get("code") if isinstance(detail, dict) else None
        self.status = code if isinstance(code, int) and 400 <= code <= 599 else 502
        super().__init__(str(detail))


def merge_stream_v4(chunks):
    from .gateway import merge_stream

    result = merge_stream(chunks)
    errors = [chunk["error"] for chunk in chunks if chunk.get("error")]
    if errors:
        result["error"] = errors[-1]
    return result


def normalize_saved_call(record, path):
    """Recover a discarded SSE error in memory without rewriting historical evidence."""
    response = record.get("response") or {}
    if response.get("model"):
        return record
    if record.get("error") or response.get("error"):
        # The Forge SDK represents a missing response model as the empty string.
        # Keep the raw response unchanged on disk and never infer the requested ID.
        return {
            **record,
            "response": {**response, "model": ""} if response else response,
            "response_model_missing": True,
        }
    if not path.with_suffix(".sse").exists():
        return record
    chunks = []
    for line in path.with_suffix(".sse").read_text().splitlines():
        if line.startswith("data:") and line[5:].strip() != "[DONE]":
            try:
                chunks.append(json.loads(line[5:].strip()))
            except ValueError:
                continue
    if not any(chunk.get("error") for chunk in chunks):
        return record
    response = merge_stream_v4(chunks)
    failure = ProviderResponseError(response["error"])
    return {
        **record,
        "response": {**response, "model": ""},
        "error": {"http_status": failure.status, "provider_error": failure.detail},
        "recovered_from_raw_stream": True,
        "response_model_missing": True,
    }


def model_identity_problem(record):
    response = record.get("response") or {}
    model = response.get("model")
    if model and model != record["request"]["model"]:
        return "Returned model differs from frozen requested model"
    if not response or model:
        return None
    # A failed request has no returned model. An actual completion must identify it.
    has_output = any(
        choice.get("finish_reason")
        or any(
            choice.get("message", {}).get(key)
            for key in ("content", "tool_calls", "reasoning_content", "reasoning")
        )
        for choice in response.get("choices", [])
    )
    if (record.get("error") or response.get("error")) and not has_output:
        return None
    return "Provider completion is missing its model identity"


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
    from .gateway import write_record

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

                def begin_response():
                    nonlocal sent
                    handler.send_response(200)
                    handler.send_header("Content-Type", upstream.headers.get("Content-Type"))
                    handler.end_headers()
                    sent = True

                if body.get("stream"):
                    client_alive, done = True, False
                    buffered = bytearray()
                    # Keep each observed chunk on disk even if the stream is interrupted.
                    with path.with_suffix(".sse").open("wb") as raw:
                        for line in upstream:
                            raw.write(line)
                            raw.flush()
                            chunk = None
                            if line.startswith(b"data:"):
                                if line[5:].strip() == b"[DONE]":
                                    done = True
                                else:
                                    chunk = json.loads(line[5:].strip())
                                    chunks.append(chunk)
                            if chunk and chunk.get("error"):
                                if sent and client_alive:
                                    try:
                                        handler.wfile.write(line)
                                        handler.wfile.flush()
                                    except (BrokenPipeError, ConnectionResetError):
                                        pass
                                raise ProviderResponseError(chunk["error"])
                            if not sent:
                                buffered.extend(line)
                                if chunk is None:
                                    continue
                                begin_response()
                                line = bytes(buffered)
                                buffered.clear()
                            if client_alive and sent:
                                try:
                                    handler.wfile.write(line)
                                    handler.wfile.flush()
                                except (BrokenPipeError, ConnectionResetError):
                                    client_alive = False
                    response = merge_stream_v4(chunks)
                    if not done:
                        raise OSError("Provider stream ended without its completion marker")
                    if not sent:
                        raise OSError("Provider stream contained no completion")
                else:
                    raw = upstream.read()
                    path.with_suffix(".response").write_bytes(raw)
                    response = json.loads(raw)
                    if response.get("error"):
                        raise ProviderResponseError(response["error"])
                    begin_response()
                    try:
                        handler.wfile.write(raw)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
        except ProviderResponseError as exc:
            response = merge_stream_v4(chunks) if chunks else response
            status = exc.status
            error = {"http_status": status, "provider_error": exc.detail}
            transport_failure = status in (408, 429) or status >= 500
        except urllib.error.HTTPError as exc:
            status = exc.code
            record["response_headers"] = response_headers(exc.headers)
            error = {"http_status": status, "body": exc.read().decode(errors="replace")}
            transport_failure = status in (408, 429) or status >= 500
        except Exception as exc:
            response = merge_stream_v4(chunks) if chunks else response
            error = {"type": type(exc).__name__, "message": str(exc)}
            transport_failure = True
        finally:
            gateway.ledger.finish(call_id, (response or {}).get("usage"))
            record.update(
                ended=time.time(),
                response=response,
                error=error,
                provider_request_id=(response or {}).get("id"),
                response_forwarded=sent,
            )
            write_record(path, record)
            gateway.circuit.finish(transport_failure)
        problem = model_identity_problem(record)
        if problem:
            write_record(
                gateway.state / "halted.json",
                {"reason": problem, "call_id": call_id},
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
                detail = (
                    {"error": error["provider_error"]}
                    if "provider_error" in error
                    else json.loads(error.get("body", "{}"))
                )
            except ValueError:
                detail = {"error": error}
            return handler.send_json(status, detail if detail else {"error": error})
        time.sleep(min(2**attempt, 4))
