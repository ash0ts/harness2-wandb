"""Verify the published pilot-v3 archive without inference or network calls."""

import argparse
import csv
import hashlib
import io
import json
import os
import statistics
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

if not __debug__:
    raise SystemExit("Run without -O; validation checks must remain enabled.")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive", type=Path, required=True)
parser.add_argument("--output", type=Path, help="Validation receipt; defaults beside the ZIP")
parser.add_argument(
    "--secret-env",
    action="append",
    default=[],
    help="Optionally reject an environment variable's value if it occurs in the archive",
)
parser.add_argument(
    "--secret-file",
    type=Path,
    action="append",
    default=[],
    help="Optionally reject a credential read from a local file; values are never printed",
)
args = parser.parse_args()
archive = args.archive.resolve()
output = (args.output or archive.with_name(archive.stem + "-validation.json")).resolve()
if output in (archive, archive.with_suffix(".zip.sha256")):
    raise SystemExit("The validation receipt must not overwrite the archive or its checksum.")
secrets = [
    value.encode()
    for value in (
        *(os.environ.get(name, "") for name in args.secret_env),
        *(path.read_text().strip() for path in args.secret_file),
    )
    if value
]
initial_stat = archive.stat()


def equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: {actual!r} != {expected!r}")


with zipfile.ZipFile(archive) as z:
    names = z.namelist()
    equal(len(names), len(set(names)), "Unique ZIP paths")
    hashes = {}
    for name in names:
        path = Path(name)
        assert not path.is_absolute() and ".." not in path.parts
        assert not (
            ".admin-token" in name
            or name.endswith(".env")
            or "/gateway/ledger" in name
            or name.endswith("/gateway/sessions.json")
            or "/controls/" in name
        ), f"Private path in archive: {name}"
        raw = z.read(name)
        if any(secret in raw for secret in secrets):
            raise AssertionError("Credential found in export; release blocked")
        hashes[name] = hashlib.sha256(raw).hexdigest()

    def read(name):
        return json.loads(z.read(name))

    index = read("evidence/files.json")
    equal(len(index), len({r["path"] for r in index}), "Unique evidence index")
    for row in index:
        equal(hashes["evidence/" + row["path"]], row["sha256"], row["path"])
    equal(
        {name for name in names if name.startswith("evidence/")} - {"evidence/files.json"},
        {"evidence/" + row["path"] for row in index},
        "Evidence index coverage",
    )
    data = read("results.json")
    manifest = read("evidence/manifest.json")
    source_index = {r["path"]: r for r in index}
    partial_sessions = data.get("partial_collection_sessions", [])
    for partial in partial_sessions:
        sid = partial["session"]
        record = read(f"evidence/provenance/interrupted-{sid}.json")
        facts = record["facts"]
        equal({k: v for k, v in partial.items() if k != "trace_url"}, facts, "Partial trace facts")
        equal(record["action"], "baseline_fallback_after_interrupted_adaptation", "Recovery action")
        equal(record["engine_changed"], False, "Frozen execution engine")
        equal(record["applied_before_any_heldout_execution"], True, "Outcome-blind fallback")
        execution = read(f"evidence/executions/{sid}/execution.json")
        equal(execution["collection_status"], "incomplete", "Missing collection remains incomplete")
        equal(execution["timed_out"], True, "Timed-out attempt retained")
        trace = read(f"evidence/executions/{sid}/trace.json")
        equal(trace["coverage"], "partial", "Partial trace disclosure")
        for path, source_sha in facts["source_hashes"].items():
            equal(source_index[path]["source_sha256"], source_sha, "Preserved interrupted source")
        requests = [
            read(name)
            for name in names
            if name.startswith(f"evidence/gateway/sessions/{sid}/")
            and not name.endswith("/identity.json")
        ]
        requested = {
            tool["id"]
            for call in requests
            for choice in call["response"].get("choices", [])
            for tool in choice.get("message", {}).get("tool_calls", [])
        }
        observed = set()
        for line in z.read(f"evidence/executions/{sid}/events.jsonl").decode().splitlines():
            event = json.loads(line)
            part = event.get("part", {})
            if part.get("type") == "tool" and part.get("state", {}).get("status") in (
                "completed",
                "error",
            ):
                observed.add(part["callID"])
        equal(len(requests), facts["provider_requests"], "Interrupted request count")
        equal(len(requested), facts["requested_tool_calls"], "Requested tool count")
        equal(len(observed), facts["observed_tool_completions"], "Observed tool count")
        equal(
            sorted(requested - observed),
            facts["unobserved_tool_call_ids"],
            "Unobserved tools remain unobserved",
        )
    equal(data["campaign"], "pilot-v3", "Campaign")
    equal(data["manifest"], manifest, "Frozen manifest")
    expected = {
        (f"{family['domain']}/{family['heldout']}", arm)
        for family in manifest["task_families"]
        for arm in ("baseline", "harness2", "diagnostics")
    }
    equal(len(expected), 18, "Planned comparison size")
    rows = {(r["task"], r["arm"]): r for r in data["rows"]}
    equal(len(rows), len(data["rows"]), "Unique evaluation keys")
    assert set(rows) <= expected
    planned = {(r["task"], r["arm"]): r for r in data["planned_evaluations"]}
    equal(set(planned), expected, "All planned evaluations accounted for")
    csv_rows = list(csv.DictReader(io.StringIO(z.read("results.csv").decode())))
    equal(len(csv_rows), len(rows), "CSV result count")
    for row in csv_rows:
        source = rows[row["task"], row["arm"]]
        equal(row["session"], source["session"], "CSV session")
        value = float(row["fraction"]) if row["fraction"] else None
        equal(value, source["fraction"], "CSV fraction")
    for key, row in rows.items():
        assert row["session"] not in {r["session"] for r in partial_sessions}
        score = row["fraction"]
        equal(planned[key]["score"], score, "Planned row score")
        if row["status"] in ("judgment_unavailable", "not_judged"):
            equal(score, None, "Unavailable score remains missing")
        if score is not None:
            equal(score, row["earned_weight"] / row["possible_weight"], "Native ratio")
        if row.get("judge_evidence") and score is not None:
            evidence = read(row["judge_evidence"])
            equal(evidence["total_score"], row["earned_weight"], "Native earned weight")
            equal(evidence["max_score"], row["possible_weight"], "Native possible weight")
            equal(len(evidence["rubrics"]), evidence["total_count"], "Complete native rubrics")
            equal(
                sum(r["weight"] for r in evidence["rubrics"]),
                evidence["max_score"],
                "Native denominator",
            )
            equal(
                sum(r["result"]["score"] for r in evidence["rubrics"]),
                evidence["total_score"],
                "Native numerator",
            )
            for rubric in evidence["rubrics"]:
                result = rubric["result"]
                equal(
                    result["score"],
                    rubric["weight"] if result["passed"] else 0,
                    "Native group weight",
                )
        frozen = read(
            f"evidence/frozen/{row['arm']}/rep{row['adaptation_replicate']}/{row['domain']}/freeze.json"
        )
        equal(
            row["harness_fingerprint"], frozen["source_fingerprint"], "Executed source fingerprint"
        )
        equal(
            row["rendered_fingerprint"],
            frozen["rendered_fingerprint"],
            "Executed rendered fingerprint",
        )
        equal(
            read(f"evidence/executions/{row['session']}/trace.json")["verified"],
            True,
            "Trace readback",
        )
        for file in row["deliverables"]:
            equal(
                hashes[row["output_dir"] + "/" + file["path"]], file["sha256"], "Deliverable hash"
            )

    differences = []
    for comparison in data["comparison"]:
        raw = rows.get((comparison["task"], "harness2"), {}).get("fraction")
        diagnostic = rows.get((comparison["task"], "diagnostics"), {}).get("fraction")
        delta = (diagnostic - raw) * 100 if raw is not None and diagnostic is not None else None
        equal(comparison["difference_pp"], delta, "Paired difference")
        if delta is not None:
            differences.append(delta)
    primary = data["primary_result"]
    equal(primary["diagnostics_complete_pairs"], len(differences), "Paired denominator")
    equal(
        primary["diagnostics_mean_difference_pp"],
        statistics.mean(differences) if differences else None,
        "Equal-task mean",
    )
    equal(primary["diagnostics_wins"], sum(v > 1e-12 for v in differences), "Wins")
    equal(primary["diagnostics_ties"], sum(abs(v) <= 1e-12 for v in differences), "Ties")
    equal(primary["diagnostics_losses"], sum(v < -1e-12 for v in differences), "Losses")
    eligible = []
    for comparison in data["comparison"]:
        gap = any(
            r["task"].split("/")[0] == comparison["task"].split("/")[0] for r in partial_sessions
        )
        equal(
            comparison["evidence_complete"],
            comparison["complete"] and not gap,
            "Example evidence eligibility",
        )
        if comparison["evidence_complete"]:
            eligible.append(comparison)
    positives = [r for r in eligible if r["difference_pp"] > 1e-12]
    ranked = sorted(
        positives or eligible,
        key=lambda r: (
            -round(r["difference_pp"] if positives else abs(r["difference_pp"]), 12),
            r["task"],
        ),
    )
    equal(
        (data.get("example") or {}).get("task"),
        ranked[0]["task"] if ranked else None,
        "Fixed complete-evidence example",
    )
    regressions = sorted(
        [r for r in data["comparison"] if r["complete"] and r["difference_pp"] < -1e-12],
        key=lambda r: (round(r["difference_pp"], 12), r["task"]),
    )
    equal(
        (data.get("regression") or {}).get("task"),
        regressions[0]["task"] if regressions else None,
        "Largest regression retained",
    )
    if data["complete"]:
        equal(set(rows), expected, "Complete evaluation set")
        equal(len(differences), 6, "Complete comparison")
        assert all(row["fraction"] is not None for row in rows.values())

    ledger = read("evidence/cost-ledger.json")
    equal(ledger["unresolved"], 0, "No unresolved usage")
    equal(ledger["reserved_usd"], 0, "No in-flight reservations")
    phase_micros = defaultdict(int)
    for call in ledger["calls"]:
        record = read(f"evidence/gateway/sessions/{call['session']}/{call['id']}.json")
        if call["status"] == "rejected":
            assert 400 <= record["error"]["http_status"] < 500
            equal(call["spent"], 0, "Rejected request cost")
        else:
            usage = record["response"]["usage"]
            equal(usage, json.loads(call["usage"]), "Recorded usage")
            equal(record["request"]["model"], call["model"], "Requested model")
            equal(record["response"]["model"], call["model"], "Returned model")
            cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            assert 0 <= cached <= usage["prompt_tokens"] and usage["completion_tokens"] >= 0
            price = manifest["prices_per_million"][call["model"]]
            amount = (
                (usage["prompt_tokens"] - cached) * Decimal(price["input"])
                + cached * Decimal(price["cached"])
                + usage["completion_tokens"] * Decimal(price["output"])
            ).to_integral_value(rounding=ROUND_CEILING)
            equal(call["spent"], int(amount), "Pinned-price request cost")
            assert call["spent"] <= call["reserved"]
        phase_micros[call["phase"]] += call["spent"]
    spent = sum(phase_micros.values()) / 1e6
    equal(spent, ledger["spent_usd"], "Ledger total")
    equal(spent, data["spent_usd"], "Published total")
    assert spent <= float(manifest["budget_usd"])
    assert spent + float(manifest["prior_spend_usd"]) <= 100
    for bucket in manifest["budget_buckets"].values():
        assert sum(phase_micros[p] for p in bucket["phases"]) / 1e6 <= float(bucket["usd"])
    gate = read("evidence/article-gate.json")
    equal(gate["article_ready"], True, "Article review complete")
    equal(gate["primary_comparison_complete"], data["complete"], "Article scope")
    video = read("evidence/presentation/video-validation.json")
    equal(hashes["evidence/presentation/walkthrough.mp4"], video["sha256"], "Validated walkthrough")
    publication = read("publication.json")
    assert publication["report_url"].startswith(
        "https://wandb.ai/wandb/harness2-agent-lens-v3/reports/"
    )

digest = hashlib.sha256()
with archive.open("rb") as source:
    for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
        digest.update(chunk)
sha = digest.hexdigest()
final_stat = archive.stat()
assert (final_stat.st_ino, final_stat.st_mtime_ns, final_stat.st_size) == (
    initial_stat.st_ino,
    initial_stat.st_mtime_ns,
    initial_stat.st_size,
), "Archive changed during verification"
archive.with_suffix(".zip.sha256").write_text(sha + "  " + archive.name + "\n")
validation = {
    "verified_at": datetime.now(timezone.utc).isoformat(),
    "archive": archive.name,
    "bytes": archive.stat().st_size,
    "sha256": sha,
    "files": len(names),
    "zip_entries_crc_verified": True,
    "evidence_hashes_verified": len(index),
    "known_credentials_checked": len(secrets),
    "known_credentials_absent": True if secrets else None,
    "planned_evaluations": len(expected),
    "evaluation_rows": len(rows),
    "status_counts": dict(Counter(row["status"] for row in rows.values())),
    "complete": data["complete"],
    "partial_adaptation_trace_sessions": len(partial_sessions),
    "primary_result": primary,
    "new_spending_usd": spent,
    "all_cost_buckets_within_cap": True,
    "report_url": publication["report_url"],
    "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
}
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(validation, indent=2) + "\n")
print(json.dumps(validation, indent=2))
