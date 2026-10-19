"""Pilot-v2 evidence and completion contracts, outside the editable harness."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from harness2.benchmarks.jobbench import _render_ctx, render_opencode_tree

from .runtime import CampaignStop, fingerprint
from .telemetry import tool_events


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def render_context(domain, manifest):
    return {**_render_ctx(domain), "date": manifest["render_date"]}


def tree_hashes(path):
    path = Path(path)
    result = {}
    for p in sorted(path.rglob("*")):
        if p.is_symlink():
            raise ValueError("Symlinks are outside the public harness surface")
        if p.is_file():
            result[str(p.relative_to(path))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def harness_record(path, domain, manifest):
    with tempfile.TemporaryDirectory(prefix="render-audit-") as tmp:
        dest = Path(tmp) / "rendered"
        render_opencode_tree(path, dest, render_context(domain, manifest))
        return {
            "source_fingerprint": fingerprint(path),
            "source_files": tree_hashes(path),
            "rendered_fingerprint": fingerprint(dest),
            "rendered_files": tree_hashes(dest),
        }


def changed_files(before, after):
    return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))


def completed_session(result, events_path):
    if result.get("timed_out") or result.get("child_exit_code") != 0:
        return False
    if result.get("collection_status") != "complete":
        return False
    events = [
        json.loads(line) for line in events_path.read_text().splitlines() if line.startswith("{")
    ]
    finishes = [e for e in events if e.get("type") == "step_finish"]
    return bool(finishes) and finishes[-1].get("part", {}).get("reason") == "stop"


def validate_completion(cwd, before, result, events_path, *, editing):
    """A final response is insufficient: a completed session must save its decision."""
    if not completed_session(result, events_path):
        raise ValueError("Session did not complete normally; partial narration is not a decision")
    answer = cwd / "output/answer.md"
    if not answer.is_file() or not answer.read_text().strip():
        raise ValueError("Completed answer was not saved to output/answer.md")
    receipt_path = cwd / "output/completion.json"
    if not receipt_path.is_file():
        raise ValueError("Missing saved output/completion.json receipt")
    receipt = json.loads(receipt_path.read_text())
    if (
        receipt.get("status") != "complete"
        or not isinstance(receipt.get("rationale"), str)
        or not receipt["rationale"].strip()
    ):
        raise ValueError("Receipt must declare status=complete and explain its rationale")
    if editing:
        after = tree_hashes(cwd / "context/harness")
        changed = changed_files(before, after)
        if receipt.get("decision") not in ("change", "keep"):
            raise ValueError("Improvement receipt must declare change or keep")
        if (receipt["decision"] == "change") != bool(changed):
            raise ValueError("Receipt decision disagrees with the actual source changes")
        affected = receipt.get("affected_files")
        if (
            not isinstance(affected, list)
            or any(not isinstance(p, str) for p in affected)
            or sorted(affected) != changed
        ):
            raise ValueError(
                "Receipt affected_files must exactly match paths changed under context/harness. "
                f"Observed changed paths: {json.dumps(changed)}. "
                "Remove incidental compiler caches before completion; only intended harness files should remain."
            )
    cited = receipt.get("cited_span_ids", [])
    if not isinstance(cited, list) or any(
        not isinstance(s, str) or not re.fullmatch(r"[a-f0-9]{16}", s) for s in cited
    ):
        raise ValueError("cited_span_ids must contain stored 16-character hexadecimal span IDs")
    return receipt


def inventory(output):
    """Open file containers without grading their contents or interpreting task criteria."""
    rows = []
    for path in sorted(Path(output).rglob("*")):
        if path.is_symlink():
            raise ValueError("Deliverable symlink")
        if not path.is_file():
            continue
        row = {
            "path": str(path.relative_to(output)),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "readable": None,
        }
        try:
            suffix = path.suffix.lower()
            if suffix in (".docx", ".xlsx", ".pptx"):
                with zipfile.ZipFile(path) as archive:
                    if archive.testzip():
                        raise ValueError("Invalid ZIP member CRC")
                    expected = {
                        ".docx": "word/document.xml",
                        ".xlsx": "xl/workbook.xml",
                        ".pptx": "ppt/presentation.xml",
                    }[suffix]
                    if expected not in archive.namelist():
                        raise ValueError("Missing document container entry")
            elif suffix == ".pdf":
                import pdfplumber

                with pdfplumber.open(path) as pdf:
                    if not len(pdf.pages):
                        raise ValueError("PDF contains no pages")
                    row["pages"] = len(pdf.pages)
            elif suffix in (".txt", ".md", ".csv", ".json", ".html", ".py", ".log"):
                text = path.read_text(encoding="utf-8-sig")
                if suffix == ".json":
                    json.loads(text)
            else:
                row["check"] = "No format reader configured"
                rows.append(row)
                continue
            row["readable"] = bool(row["bytes"])
        except Exception as exc:
            row.update(readable=False, error=f"{type(exc).__name__}: {exc}"[:200])
        rows.append(row)
    return rows


def _bounded(value, limit):
    """Drop tail entries in a fixed order, reporting omissions; never truncate JSON."""
    value["omitted"] = {}
    lists = [k for k, v in value.items() if isinstance(v, list)]
    while len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) > limit:
        eligible = [k for k in lists if value[k]]
        if not eligible:
            raise ValueError("Diagnostic header exceeds its bound")
        key = max(eligible, key=lambda k: (len(json.dumps(value[k]).encode()), k))
        value[key].pop()
        value["omitted"][key] = value["omitted"].get(key, 0) + 1
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"


def summarize(rows, files, trace_url, *, limit=8192):
    errors, repeats, limits, operations = [], [], [], []
    signatures, totals, previous_errors = defaultdict(list), defaultdict(lambda: Counter()), {}
    recoveries = []
    for row in sorted(rows, key=lambda r: (r.get("started_at", ""), r["span_id"])):
        sid, name = row["span_id"], row.get("tool_name") or row.get("operation_name")
        if row.get("operation_name") == "invoke_agent":
            continue
        seconds = 0
        if row.get("started_at") and row.get("ended_at"):
            seconds = (
                datetime.fromisoformat(row["ended_at"]) - datetime.fromisoformat(row["started_at"])
            ).total_seconds()
        op = {"span_id": sid, "operation": name, "seconds": round(seconds, 3)}
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "reasoning_tokens"):
            op[key] = row.get(key)
            if row.get(key) is not None:
                totals[name][key] += row[key]
        totals[name]["seconds"] += seconds
        totals[name]["count"] += 1
        operations.append(op)
        failed = bool(row.get("error_type") or row.get("status_code") in (2, "ERROR"))
        if failed:
            errors.append(
                {
                    "span_id": sid,
                    "operation": name,
                    "type": row.get("error_type"),
                    "message": str(row.get("status_message", ""))[:600],
                }
            )
        if row.get("tool_name"):
            args = row.get("tool_call_arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    pass
            signature = json.dumps([name, args], sort_keys=True)
            signatures[signature].append(sid)
            if name in previous_errors:
                recoveries.append(
                    {
                        "error_span_id": previous_errors.pop(name),
                        "subsequent_span_id": sid,
                        "operation": name,
                        "subsequent_status": "error" if failed else "completed",
                        "interpretation": "next call to the same tool; recovery intent is not inferred",
                    }
                )
            if failed:
                previous_errors[name] = sid
        reasons = row.get("finish_reasons") or []
        error_text = str(row.get("status_message", "")).lower()
        context_rejection = bool(
            failed
            and not row.get("tool_name")
            and any(
                term in error_text
                for term in (
                    "context_length_exceeded",
                    "maximum context length",
                    "context window",
                    "context length exceeded",
                )
            )
        )
        if reasons or row.get("request_max_tokens") or context_rejection:
            limits.append(
                {
                    "span_id": sid,
                    "finish_reasons": reasons,
                    "max_output_tokens": row.get("request_max_tokens"),
                    "explicit_output_truncation": "length" in reasons,
                    "context_rejection": context_rejection,
                }
            )
    for sig, ids in sorted(signatures.items()):
        if len(ids) > 1:
            name, args = json.loads(sig)
            repeats.append(
                {
                    "operation": name,
                    "arguments_excerpt": json.dumps(args)[:300],
                    "signature_sha256": hashlib.sha256(sig.encode()).hexdigest(),
                    "count": len(ids),
                    "span_ids": ids,
                }
            )
    value = {
        "schema_version": 2,
        "source": "Agent Lens stored spans",
        "trace_url": trace_url,
        "full_records": "stored-spans.jsonl and events.jsonl in the matching evidence directory",
        "errors": errors,
        "observed_followups": recoveries,
        "repeated_identical_calls": repeats,
        "limits": limits,
        "operation_totals": [{"operation": k, **v} for k, v in sorted(totals.items())],
        "operations": operations,
        "produced_files": files,
        "deliverable_present": bool(files),
        "readable_files": sum(f["readable"] is True for f in files),
        "note": "Readability and tool status are observations, not benchmark grades.",
    }
    return _bounded(value, limit - 1)


def materialize_evidence(cwd, runtime, *, diagnostics, task, arm, replicate):
    """Both arms see identical raw records; diagnostics is the sole additional input."""
    sessions = set()
    for path in (cwd / "context").rglob("*.md"):
        sessions.update(
            re.findall(r"SHOWCASE_SESSION:([a-f0-9]{32})", path.read_text(errors="replace"))
        )
    index, summaries = [], []
    for session in sorted(sessions):
        source = runtime.state / "executions" / session
        identity = json.loads((source / "identity.json").read_text())
        if identity["role"] != "solver" or identity["phase"] not in ("adapt", "smoke"):
            raise CampaignStop("Improvement context requested non-adaptation execution evidence")
        if (
            identity.get("task") != task
            or identity.get("arm") not in (arm, "baseline")
            or identity.get("adaptation_replicate") != replicate
        ):
            raise CampaignStop(
                "Improvement evidence crosses a task, condition, or replicate boundary"
            )
        dest = cwd / "context/evidence" / session
        dest.mkdir(parents=True)
        stored = json.loads((source / "stored-spans.json").read_text())
        (dest / "stored-spans.jsonl").write_text("".join(json.dumps(r) + "\n" for r in stored))
        shutil.copy(source / "events.jsonl", dest / "events.jsonl")
        # Requests contain only this execution's task and rendered harness, never rubrics.
        (dest / "requests.jsonl").write_text(
            "".join(
                json.dumps(
                    {
                        k: r.get(k)
                        for k in ("call_id", "started", "ended", "request", "response", "error")
                    }
                )
                + "\n"
                for r in runtime.calls(session)
            )
        )
        trace = json.loads((source / "trace.json").read_text())
        index.append(
            {
                "session": session,
                "directory": f"context/evidence/{session}",
                "spans": len(stored),
                "trace_url": trace["url"],
            }
        )
        if diagnostics:
            path = cwd / "context/diagnostics" / (session + ".json")
            path.parent.mkdir(exist_ok=True)
            files = json.loads((source / "file-inventory.json").read_text())
            path.write_text(
                summarize(
                    stored,
                    files,
                    trace["url"],
                    limit=runtime.manifest["diagnostics_execution_bytes"],
                )
            )
            summaries.append(
                {
                    "session": session,
                    "summary": f"context/diagnostics/{path.name}",
                    "span_count": len(stored),
                }
            )
    evidence = cwd / "context/evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "INDEX.json").write_text(_bounded({"executions": index}, 16383))
    if diagnostics:
        dest = cwd / "context/diagnostics"
        dest.mkdir(exist_ok=True)
        (dest / "INDEX.json").write_text(
            _bounded(
                {
                    "summaries": summaries,
                    "instructions": "Inspect summaries selectively, then consult supporting spans in context/evidence. Cite span IDs in completion.json when they support a decision.",
                },
                runtime.manifest["diagnostics_stage_bytes"] - 1,
            )
        )
    return sorted(sessions)


def diagnostic_reads(events_path):
    return [
        {"call_id": p["callID"], "tool": p["tool"], "input": p["state"].get("input")}
        for p in tool_events(events_path)
        if p["tool"] in ("read", "bash", "grep")
        and "context/diagnostics" in json.dumps(p["state"].get("input", {}))
    ]
