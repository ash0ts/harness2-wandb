"""Reconciled repeated-study results, portable evidence, and public W&B records."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
from collections import defaultdict
from pathlib import Path
from statistics import mean
from urllib.parse import quote

import httpx

from .runtime import ROOT, CampaignStop, command
from .study import evaluation_schedule
from .study_analysis import paired_analysis
from .v3_report import cited_evidence, execution_metrics
from .workbuddy_preflight import save


def read(path):
    return json.loads(Path(path).read_text())


def row_id(row):
    return hashlib.sha256(row["key"].encode()).hexdigest()[:20]


def planned_rows(runtime):
    from .runtime_v4 import StudyRuntime
    from .v4 import groups

    return evaluation_schedule(
        runtime.manifest,
        {s: groups(StudyRuntime(runtime.manifest, runtime.state, s)) for s in ("code", "jobbench")},
    )


def stability_sample(rows, seed, fraction=0.2):
    """Choose from planned identities before looking at judgments or outputs."""
    rows = [row for row in rows if row["study"] == "jobbench"]
    return sorted(
        rows,
        key=lambda r: hashlib.sha256(f"{seed}:{r['key']}".encode()).hexdigest(),
    )[: math.ceil(len(rows) * fraction)]


def judge_stability(runtime):
    """Blind repeats retain original primary judgments and use separate stores."""
    from .code_study import StudyAdapter
    from .runtime_v4 import StudyRuntime

    if not (runtime.state / "evaluate-complete.json").exists():
        raise CampaignStop("Complete primary execution and grading before the stability check")
    spec = runtime.manifest["analysis"]
    selected = stability_sample(
        planned_rows(runtime), spec["seed"], spec["jobbench_judge_repeat_fraction"]
    )
    sample_path = runtime.state / "judge-stability/sample.json"
    if sample_path.exists() and read(sample_path) != selected:
        raise CampaignStop("Predetermined judge-stability sample changed")
    save(sample_path, selected)
    scoped = StudyRuntime(runtime.manifest, runtime.state, "jobbench")
    for planned in selected:
        key = row_id(planned)
        receipt = runtime.state / "judge-stability" / (key + ".json")
        if receipt.exists():
            continue
        primary = read(
            runtime.state
            / "evaluations"
            / (hashlib.sha256(planned["key"].encode()).hexdigest() + ".json")
        )
        # Native judge inputs contain only anonymized output, rubrics, and a random
        # evaluated-model ID. Neither condition nor primary score is sent to it.
        bench = StudyAdapter(scoped, "judge-stability", planned["trial"])
        bench.root = runtime.state / "studies/jobbench/judge-stability" / key
        bench.store = bench.root / "dataset"
        bench.phase = "evaluate"
        output = bench._out_dir(planned["task"], key)
        if not output.exists():
            shutil.copytree(primary["output"], output)
        result = bench.score(output, planned["task"])
        score = result["n_passed"] / result["n_criteria"] if result else None
        _, evidence = bench._details_candidates(planned["task"], key)
        save(
            receipt,
            {
                **planned,
                "primary_score": primary.get("score"),
                "repeat_score": score,
                "status": "graded" if score is not None else "unavailable_judgment",
                "judgment": result,
                "judge_evidence": str(evidence) if evidence.exists() else None,
                "primary_unchanged": True,
            },
        )


def session_records(runtime, budget):
    by_session = defaultdict(list)
    for call in budget["calls"]:
        by_session[call["session"]].append(call)
    rows = []
    for path in sorted((runtime.state / "gateway/sessions").glob("*/identity.json")):
        identity = read(path)
        sid = identity["session"]
        directory = runtime.state / "executions" / sid
        execution = (
            read(directory / "execution.json") if (directory / "execution.json").exists() else {}
        )
        trace = read(directory / "trace.json") if (directory / "trace.json").exists() else None
        spans = (
            read(directory / "stored-spans.json")
            if (directory / "stored-spans.json").exists()
            else []
        )
        calls = by_session[sid]
        failures = []
        for captured in (runtime.state / "gateway/sessions" / sid).glob("*.json"):
            if captured.name != "identity.json":
                record = read(captured)
                if record.get("error"):
                    failures.append(record["error"])
        finishes = []
        events = directory / "events.jsonl"
        if events.exists():
            for line in events.read_text().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "step_finish":
                    finishes.append(event["part"].get("reason"))
        outcome = (
            "not_recorded"
            if not execution
            else "infrastructure_failure"
            if execution.get("collection_status") != "complete"
            else "process_completed_with_recovered_provider_errors"
            if failures
            and execution.get("child_exit_code") == 0
            and finishes
            and finishes[-1] == "stop"
            else "incomplete_with_provider_errors"
            if failures
            else "agent_resource_limit"
            if execution.get("timed_out") or (finishes and finishes[-1] == "length")
            else "process_failure_cause_unknown"
            if execution.get("child_exit_code") != 0
            else "process_completed"
            if finishes and finishes[-1] == "stop"
            else "agent_unfinished"
        )
        rows.append(
            {
                **identity,
                "provider_attempts": len(calls),
                "observed_cost_usd": sum(c["spent"] or 0 for c in calls) / 1e6,
                "bounded_unknown_cost_usd": sum(
                    c["reserved"] for c in calls if c["status"] == "bounded_unknown"
                )
                / 1e6,
                "in_flight_reservations_usd": sum(
                    c["reserved"] for c in calls if c["status"] == "pending"
                )
                / 1e6,
                "child_exit_code": execution.get("child_exit_code"),
                "collection_status": execution.get("collection_status"),
                "queue_seconds": execution.get("queue_seconds"),
                "latency_seconds": execution["ended"] - execution["started"]
                if "ended" in execution
                else None,
                "trace": trace,
                "execution_outcome": outcome,
                "provider_errors": failures,
                **execution_metrics(spans),
            }
        )
    return rows


def dataset(runtime):
    budget = runtime.budget()
    sessions = session_records(runtime, budget)
    by_session = {s["session"]: s for s in sessions}
    decisions = defaultdict(list)
    for session in sessions:
        path = runtime.state / "executions" / session["session"] / "decision.json"
        if path.exists() and session.get("cohort") == "adaptation":
            key = (
                session.get("study"),
                session.get("task"),
                session.get("condition"),
                session.get("trial"),
            )
            decision = read(path)
            spans, missing = cited_evidence(runtime, {"decision": decision})
            decisions[key].append(
                {
                    "evidence": decision,
                    "trace": session.get("trace"),
                    "cited_spans": spans,
                    "unverified_cited_span_ids": missing,
                }
            )
    actual = {}
    for path in sorted((runtime.state / "evaluations").glob("*.json")):
        row = read(path)
        if row["key"] in actual:
            raise ValueError("Duplicate durable evaluation identity")
        actual[row["key"]] = row
    rows = []
    project = runtime.manifest["project"]
    for planned in planned_rows(runtime):
        row = {**planned, "score": None, "status": "not_executed", **actual.pop(planned["key"], {})}
        if row["score"] is not None:
            judgment = row["judgment"]
            expected = (
                judgment["score"]
                if row["study"] == "code"
                else judgment["n_passed"] / judgment["n_criteria"]
            )
            if row["score"] != expected:
                raise ValueError("Evaluation score differs from native judgment")
        execution = row.get("execution", {})
        session = by_session.get(execution.get("session"), {})
        row["session"] = execution.get("session")
        row["trace"] = execution.get("trace") or session.get("trace")
        row["run_id"] = row_id(row)
        row["run_url"] = f"https://wandb.ai/{project}/runs/{row['run_id']}"
        row["files_url"] = f"https://api.wandb.ai/files/{project}/{row['run_id']}"
        row["delivered"] = None
        row["execution_outcome"] = session.get("execution_outcome", "not_executed")
        if row.get("output"):
            output = Path(row["output"])
            row["delivered"] = (
                bool((output / "delivered.patch").read_text().strip())
                if row["study"] == "code"
                else any(p.is_file() for p in output.rglob("*"))
            )
            if row["study"] == "code":
                evidence = (
                    output.parents[3] / "judgments" / row["task"] / output.name / "judgment.json"
                )
                if evidence.exists():
                    original = read(evidence)
                    if row["score"] is not None and (
                        original["score"] != row["score"]
                        or original["native"]["rewards"]["reward"] != row["score"]
                    ):
                        raise ValueError("WorkBuddy verifier evidence disagrees with result")
                    row["judge_evidence"] = str(evidence)
        if row.get("freeze"):
            row["frozen_harness"] = read(Path(row["freeze"]) / "freeze.json")
            row["improvement_decisions"] = decisions[
                row["study"], row["frozen_harness"]["adapt_task"], row["condition"], row["trial"]
            ]
        for name in (
            "observed_cost_usd",
            "bounded_unknown_cost_usd",
            "queue_seconds",
            "latency_seconds",
            "tool_calls",
            "tool_errors",
            "repeated_error_occurrences",
            "error_signatures",
        ):
            row[name] = session.get(name)
        if row["study"] == "jobbench" and row.get("output"):
            from .code_study import StudyAdapter
            from .runtime_v4 import StudyRuntime

            bench = StudyAdapter(
                StudyRuntime(runtime.manifest, runtime.state, "jobbench"),
                row["condition"],
                row["trial"],
            )
            _, evidence = bench._details_candidates(row["task"], Path(row["output"]).name)
            if evidence.exists():
                original = read(evidence)
                if (
                    row["score"] is not None
                    and original["total_score"] / original["max_score"] != row["score"]
                ):
                    raise ValueError("JobBench rubric evidence disagrees with result")
                row["judge_evidence"] = str(evidence)
            trace = evidence.with_name(evidence.name + ".trace.json")
            row["judge_trace"] = read(trace) if trace.exists() else None
        rows.append(row)
    if actual:
        raise ValueError("Unexpected evaluation identities outside the frozen design")
    analysis = {
        study: paired_analysis(
            [r for r in rows if r["study"] == study],
            trials=spec["adaptation_trials"],
            seed=runtime.manifest["analysis"]["seed"],
            draws=runtime.manifest["analysis"]["bootstrap_draws"],
            equivalence_pp=runtime.manifest["analysis"]["equivalence_pp"],
        )
        for study, spec in runtime.manifest["studies"].items()
    }
    freezes = [
        read(p) for p in sorted((runtime.state / "studies").glob("*/frozen/main/*/*/*/freeze.json"))
    ]
    repeats = [
        read(p)
        for p in sorted((runtime.state / "judge-stability").glob("*.json"))
        if p.name != "sample.json"
    ]
    paired = [
        r for r in repeats if r["primary_score"] is not None and r["repeat_score"] is not None
    ]
    cost = {k: v for k, v in budget.items() if k != "calls"}
    operating = []
    for study in ("code", "jobbench"):
        for condition in ("baseline", "harness2", "diagnostics"):
            subset = [r for r in rows if r["study"] == study and r["condition"] == condition]
            frozen = [r for r in freezes if r["study"] == study and r["condition"] == condition]
            record = {
                "study": study,
                "condition": condition,
                "planned_executions": len(subset),
                "executed": sum(r.get("session") is not None for r in subset),
                "delivered": sum(r["delivered"] is True for r in subset),
                "frozen_harnesses": len(frozen),
                "complete_adaptations": sum(r["adaptation_complete"] for r in frozen),
                "executable_harness_changes": sum(r["executable_change"] for r in frozen),
            }
            for metric in (
                "tool_errors",
                "repeated_error_occurrences",
                "latency_seconds",
                "queue_seconds",
            ):
                values = [r[metric] for r in subset if r[metric] is not None]
                record["mean_" + metric] = mean(values) if values else None
                record[metric + "_observations"] = len(values)
            for metric in ("observed_cost_usd", "bounded_unknown_cost_usd"):
                record["heldout_" + metric] = sum(r[metric] or 0 for r in subset)
                record["adaptation_" + metric] = sum(
                    s[metric]
                    for s in sessions
                    if s.get("study") == study
                    and s.get("condition") == condition
                    and s.get("cohort") == "adaptation"
                )
            operating.append(record)
    return {
        "campaign": runtime.manifest["campaign"],
        "manifest": runtime.manifest,
        "complete": all(a["complete"] for a in analysis.values()),
        "analysis": analysis,
        "rows": rows,
        "sessions": sessions,
        "freezes": freezes,
        "cost": cost,
        "operating_metrics": operating,
        "judge_stability": {
            "planned": len(stability_sample(rows, runtime.manifest["analysis"]["seed"])),
            "available_pairs": len(paired),
            "records": repeats,
            "mean_absolute_difference_pp": mean(
                abs(r["repeat_score"] - r["primary_score"]) * 100 for r in paired
            )
            if paired
            else None,
        },
    }


def choose_examples(data, study):
    eligible = []
    for task in data["analysis"][study]["tasks"]:
        if task["diagnostics_minus_raw_pp"] is None:
            continue
        records = [r for r in data["rows"] if r["study"] == study and r["task"] == task["task"]]
        if all(
            (r.get("trace") or {}).get("verified") and r.get("frozen_harness") and r.get("judgment")
            for r in records
        ):
            eligible.append(task)
    positive = [r for r in eligible if r["diagnostics_minus_raw_pp"] > 0]
    first = sorted(
        positive or eligible,
        key=lambda r: (
            -(r["diagnostics_minus_raw_pp"] if positive else abs(r["diagnostics_minus_raw_pp"])),
            r["task"],
        ),
    )
    negative = sorted(
        [r for r in eligible if r["diagnostics_minus_raw_pp"] < 0],
        key=lambda r: (r["diagnostics_minus_raw_pp"], r["task"]),
    )
    return {"example": first[0] if first else None, "regression": negative[0] if negative else None}


def allowed_files(runtime):
    state = runtime.state
    for name in (
        "manifest.json",
        "prepared.json",
        "runtime-volume.json",
        "code-images.json",
        "source-provenance.json",
        "qualification.json",
        "qualification-attempt.json",
        "cost-projection.json",
        "evaluation-schedule.json",
        "adapt-complete.json",
        "execute-complete.json",
        "evaluate-complete.json",
        "stopped.json",
        "cost-ledger.json",
        "model-catalog.json",
        "preflight-environment.txt",
        "repository-roundtrip-checks.json",
    ):
        if (state / name).is_file():
            yield state / name
    for name in ("selection.json", "summary.json", "metadata-exclusions.json"):
        if (state / "preflight" / name).is_file():
            yield state / "preflight" / name
    for path in (state / "preflight").glob("*/result.json"):
        yield path
    for pattern in ("*/reference-*/*", "*/negative-*/*", "*/control-*/*"):
        for path in (state / "preflight").glob(pattern):
            if path.is_file() and path.suffix in (".json", ".txt", ".xml", ".log"):
                yield path
    for area in ("evaluations", "judge-stability", "qualifications", "code-public-inputs"):
        for path in (state / area).rglob("*"):
            if path.is_file():
                yield path
    for path in (state / "studies").rglob("*"):
        relative = path.relative_to(state / "studies").parts
        if (
            path.is_file()
            and any(
                part in relative
                for part in ("frozen", "results", "libraries", "audit", "judgments", "dataset")
            )
            and "work" not in relative
        ):
            yield path
    for directory in (state / "executions").glob("*"):
        for name in (
            "identity.json",
            "execution.json",
            "trace.json",
            "stored-spans.json",
            "events.jsonl",
            "events.raw.jsonl",
            "opencode-audit.json",
            "stderr.txt",
            "judge.log",
            "entry-message.txt",
            "runtime-settings.json",
            "file-inventory.json",
            "harness-record.json",
            "mission.md",
            "decision.json",
        ):
            if (directory / name).is_file():
                yield directory / name
        for area in (
            "source-harness",
            "proposed-harness",
            "proposal-output",
            "diagnostics",
            "export-attempts",
        ):
            for path in (directory / area).rglob("*"):
                if path.is_file():
                    yield path
    for path in (state / "gateway/sessions").glob("*/*"):
        if path.suffix in (".json", ".sse"):
            yield path


def portable(runtime, value):
    text = json.dumps(value, ensure_ascii=False)
    text = text.replace(str(runtime.state) + "/", "evidence/").replace(str(ROOT), "<checkout>")
    return json.loads(text)


def bundle(runtime, destination):
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    index = []
    secrets = [os.environ.get("WANDB_API_KEY", ""), runtime.admin]
    for path in sorted(set(allowed_files(runtime))):
        if path.is_symlink():
            raise ValueError("Symlink in public evidence boundary")
        raw = path.read_bytes()
        if any(secret and secret.encode() in raw for secret in secrets):
            raise ValueError(f"Credential in public evidence: {path.name}")
        source_hash = hashlib.sha256(raw).hexdigest()
        if path.suffix in (
            ".json",
            ".jsonl",
            ".txt",
            ".md",
            ".diff",
            ".patch",
            ".log",
            ".sse",
            ".judge_failed",
        ):
            raw = (
                raw.decode()
                .replace(str(runtime.state) + "/", "evidence/")
                .replace(str(ROOT), "<checkout>")
                .encode()
            )
        relative = path.relative_to(runtime.state)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        index.append(
            {
                "path": str(relative),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "source_sha256": source_hash,
            }
        )
    save(destination / "files.json", index)
    return index


def answer(analysis):
    if not analysis["complete"]:
        return f"Incomplete comparison: {analysis['observed_cells']}/{analysis['planned_cells']} scores available. No complete-design effect estimate is reported."
    low, high = analysis["interval_95_pp"]
    finding = "The effect remains uncertain."
    if analysis["supported_gain"]:
        finding = "The paired interval supports a gain on these tasks."
    elif analysis["supported_loss"]:
        finding = "The paired interval supports a loss on these tasks."
    if analysis["practically_equivalent"]:
        finding += " The interval lies within the predeclared ±5-point practical-equivalence band."
    return (
        f"Diagnostics minus raw feedback: **{analysis['diagnostics_minus_raw_pp']:+.2f} percentage points** "
        f"(95% paired hierarchical bootstrap interval {low:+.2f} to {high:+.2f}). {finding} "
        f"Task wins/ties/losses: {analysis['wins']}/{analysis['ties']}/{analysis['losses']}."
    )


def markdown(data, artifact_url="", download_url=""):
    lines = [
        "# Do Agent Lens summaries help Harness²?",
        "",
        "The model stays fixed. Harness² edits instructions, skills, and scripts through two successive Propose–Probe–Compose rounds. The treatment adds bounded summaries retrieved from stored Agent Lens spans to the same raw execution feedback.",
        "",
    ]
    for study, title in (("code", "Repository coding"), ("jobbench", "JobBench replication")):
        analysis = data["analysis"][study]
        lines += [
            f"## {title}",
            "",
            answer(analysis),
            "",
            f"{analysis['heldout_tasks']} held-out tasks in {analysis['independent_groups']} adaptation groups, with {analysis['adaptation_trials_per_group']} fresh adaptations per group and condition. Repetitions are not additional independent tasks.",
            "",
            "| Task | Original | Raw feedback | Diagnostics | Diagnostics − raw |",
            "|---|---:|---:|---:|---:|",
        ]
        for row in analysis["tasks"]:
            cells = [
                "missing" if row[c] is None else f"{row[c]:.2f}"
                for c in ("baseline", "harness2", "diagnostics", "diagnostics_minus_raw_pp")
            ]
            lines.append(f"| {row['task']} | " + " | ".join(cells) + " |")
        lines += [
            "",
            "Scores are native task scores expressed as percentages. Trials are averaged within task; tasks have equal weight. Coding and JobBench are analyzed separately.",
            "",
        ]
        if download_url:
            image_url = download_url.rsplit("/", 1)[0] + f"/{study}-paired.png"
            lines += [
                f"![{title}: paired native task scores and diagnostics-minus-raw differences]({image_url})",
                "",
            ]
    cost = data["cost"]
    lines += [
        "## Accounting and coverage",
        "",
        f"Observed inference cost: **${cost['observed_cost_usd']:.6f}**. Bounded unknown cost: **${cost['bounded_unknown_cost_usd']:.6f}**. In-flight reservations: **${cost['in_flight_reservations_usd']:.6f}**. The new campaign has a $500 ceiling; prior pilots are separate.",
        "",
        "The downloadable records separate qualification, adaptation, held-out execution, ordinary judging, and retries. Unavailable judgments remain missing; agent failures, infrastructure failures, and no-output native scores retain their original records.",
        "",
        "| Study | Setup | Adaptation observed ($) | Adaptation bounded unknown ($) | Held-out observed ($) | Held-out bounded unknown ($) |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in data["operating_metrics"]:
        values = [
            row[phase + "_" + metric]
            for phase in ("adaptation", "heldout")
            for metric in ("observed_cost_usd", "bounded_unknown_cost_usd")
        ]
        lines.append(
            f"| {row['study']} | {row['condition']} | "
            + " | ".join(f"{value:.6f}" for value in values)
            + " |"
        )
    lines += [
        "",
        "The Original adaptation row contains the shared starting executions used by both improving conditions. That cost is recorded once, without assigning it to either condition. These rows exclude qualification and judging; the campaign total includes both.",
        "",
        "## Execution and adaptation",
        "",
        "These operational measurements are separate from quality. Repeated errors count repeated identical failed tool calls within an execution. Latency excludes queue time. Empty coding patches count as no delivered change, although the unchanged repository can still earn a native partial score.",
        "",
        "| Study | Setup | Deliverables / planned | Complete adaptations | Executable harness changes | Mean tool errors | Mean execution seconds |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in data["operating_metrics"]:
        errors = "missing" if row["mean_tool_errors"] is None else f"{row['mean_tool_errors']:.2f}"
        latency = (
            "missing"
            if row["mean_latency_seconds"] is None
            else f"{row['mean_latency_seconds']:.1f}"
        )
        adaptations = (
            "n/a"
            if row["condition"] == "baseline"
            else f"{row['complete_adaptations']}/{row['frozen_harnesses']}"
        )
        lines.append(
            f"| {row['study']} | {row['condition']} | {row['delivered']}/{row['planned_executions']} | {adaptations} | {row['executable_harness_changes']} | {errors} | {latency} |"
        )
    lines += [
        "",
        "## Inspectable examples",
        "",
    ]
    for study in ("code", "jobbench"):
        selected = choose_examples(data, study)
        for kind, task in selected.items():
            if not task:
                continue
            lines += [
                f"**{study}: {kind}, {task['task']}**. Diagnostics minus raw: {task['diagnostics_minus_raw_pp']:+.2f} points averaged across trials.",
                "",
            ]
            for row in data["rows"]:
                if row["study"] != study or row["task"] != task["task"]:
                    continue
                files = row["files_url"]
                trace = (row.get("trace") or {}).get("url")
                links = [
                    f"[Result]({row['run_url']})",
                    f"[Harness diff]({files}/harness.diff)",
                    f"[Improvement decisions]({files}/decisions.json)",
                    f"[Deliverable]({files}/deliverable.zip)",
                ]
                if trace:
                    links.append(f"[Agent Lens]({trace})")
                lines.append(f"- Trial {row['trial']}, {row['condition']}: " + " · ".join(links))
            lines.append("")
    lines += [
        "Examples follow a fixed selection rule: largest positive diagnostics-minus-raw task difference, otherwise largest absolute difference; task ID breaks ties. The largest regression is included when present. A diagnostic citation alone does not establish that a change helped.",
        "",
        "## Complete evidence",
        "",
        f"[Download evidence]({download_url}) · [Versioned W&B artifact]({artifact_url})",
        "",
        "| Study | Task | Trial | Setup | Native score | Result and evidence | Execution |",
        "|---|---|---:|---|---:|---|---|",
    ]
    for row in data["rows"]:
        score = "missing" if row["score"] is None else f"{row['score'] * 100:.2f}"
        trace = (row.get("trace") or {}).get("url")
        lines.append(
            f"| {row['study']} | {row['task']} | {row['trial']} | {row['condition']} | {score} | [record]({row['run_url']}) | "
            + (f"[trace]({trace})" if trace else row["status"])
            + " |"
        )
    lines += [
        "",
        "## Method and limits",
        "",
        "The estimator resamples adaptation groups, paired adaptation trials within groups, and held-out tasks within groups. Each sampled trial is shared across its tasks and all conditions. It uses 10,000 bootstrap draws, seed 20261022, and a 95% percentile interval. The ±5-point equivalence band was declared before execution.",
        "",
        "WorkBuddy-Code includes partial or reconstructed repositories. Selection used metadata and offline reference/control verifier checks, not model scores. Hidden benchmark tests and reference solutions were kept out of solver and improver environments. The selected tasks do not represent all software engineering work.",
        "",
        f"JobBench blind judge check: {data['judge_stability']['available_pairs']}/{data['judge_stability']['planned']} predetermined repeated judgments available. Original judgments remain primary.",
        "",
        "Agent Lens may require authentication. Screenshots and the recorded walkthrough accompany publication so the evidence path remains understandable without product access.",
        "",
    ]
    return "\n".join(lines)


def prepare_output(runtime, data):
    output = runtime.state / "public"
    output.mkdir(exist_ok=True)
    save(runtime.state / "cost-ledger.json", runtime.budget())
    bundle(runtime, output / "evidence")
    save(output / "results.json", portable(runtime, data))
    fields = [
        "study",
        "group",
        "task",
        "trial",
        "condition",
        "score",
        "status",
        "session",
        "delivered",
        "observed_cost_usd",
        "bounded_unknown_cost_usd",
        "latency_seconds",
        "queue_seconds",
        "tool_calls",
        "tool_errors",
        "repeated_error_occurrences",
        "run_url",
    ]
    with (output / "results.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data["rows"])
    (output / "report.md").write_text(markdown(data))
    save(output / "examples.json", {s: choose_examples(data, s) for s in ("code", "jobbench")})
    plots = ROOT / "harness2/showcase/report_v4_plots.py"
    image = read(runtime.state / "prepared.json")["engine_image"]
    command(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--cpus",
            "1",
            "--memory",
            "2g",
            "--user",
            "0",
            "--mount",
            f"type=bind,src={plots},dst=/plots.py,readonly",
            "--mount",
            f"type=bind,src={output},dst=/output",
            "--entrypoint",
            "python",
            image,
            "/plots.py",
            "/output/results.json",
            "/output/figures",
        ]
    )
    archive = runtime.state / (runtime.manifest["campaign"] + "-evidence")
    shutil.make_archive(str(archive), "zip", output)
    return output, archive.with_suffix(".zip")


def make_public(runtime, api):
    """Verify every recorded conversation, including children, before publication."""
    entity, project = runtime.manifest["project"].split("/")
    for run in api.runs(runtime.manifest["project"]):
        if run.config.get("campaign") != runtime.manifest["campaign"]:
            raise ValueError("Another campaign exists in the publication destination")
    known = set()
    for path in (runtime.state / "executions").glob("*/trace.json"):
        known.update(group["conversation_id"] for group in read(path).get("trace_groups", []))
    # Earlier export attempts may exist as additional traces after an interrupted
    # export. They remain campaign evidence and must pass the same ownership check.
    for path in (runtime.state / "executions").glob("*/export-attempts/*.json"):
        known.update(group["conversation_id"] for group in read(path).get("trace_groups", []))
    offset = 0
    while True:
        response = httpx.post(
            "https://trace.wandb.ai/agents/spans/query",
            auth=("api", os.environ["WANDB_API_KEY"]),
            json={
                "project_id": runtime.manifest["project"],
                "limit": 200,
                "offset": offset,
                "include_details": False,
            },
            timeout=60,
        )
        response.raise_for_status()
        body = response.json()
        spans = body.get("spans", [])
        if any(span.get("conversation_id") not in known for span in spans):
            raise ValueError("Unrecorded conversations in publication destination")
        offset += len(spans)
        if not spans or offset >= body.get("total_count", 0):
            break
    response = httpx.post(
        "https://api.wandb.ai/graphql",
        auth=("api", os.environ["WANDB_API_KEY"]),
        json={
            "query": "mutation($input:UpsertModelInput!){upsertModel(input:$input){project{name access}}}",
            "variables": {"input": {"name": project, "entityName": entity, "access": "USER_READ"}},
        },
        timeout=60,
    )
    response.raise_for_status()
    if (response.json().get("data", {}).get("upsertModel", {}).get("project") or {}).get(
        "access"
    ) != "USER_READ":
        raise ValueError("Public visibility readback failed")
    response = httpx.post(
        "https://api.wandb.ai/graphql",
        json={
            "query": "query($name:String!,$entity:String!){project(name:$name,entityName:$entity){name access}}",
            "variables": {"name": project, "entity": entity},
        },
        timeout=60,
    )
    response.raise_for_status()
    if (response.json().get("data", {}).get("project") or {}).get("access") != "USER_READ":
        raise ValueError("Signed-out project readback failed")


def publish(runtime, data, output, archive):
    import wandb
    import wandb_workspaces.reports.v2 as wr

    entity, project = runtime.manifest["project"].split("/")
    campaign = runtime.manifest["campaign"]
    settings = wandb.Settings(disable_git=True)
    with wandb.init(
        entity=entity,
        project=project,
        id=campaign + "-summary",
        resume="allow",
        job_type="campaign",
        config={"campaign": campaign, "manifest": runtime.manifest},
        settings=settings,
    ) as run:
        artifact = wandb.Artifact(
            campaign + "-evidence", type="evidence", metadata={"complete": data["complete"]}
        )
        artifact.add_dir(str(output))
        artifact = run.log_artifact(artifact)
        artifact.wait()
        run.save(str(archive), base_path=str(archive.parent), policy="now")
        for path in sorted((output / "figures").glob("*.png")):
            run.save(str(path), base_path=str(path.parent), policy="now")
        for study in ("code", "jobbench"):
            analysis = data["analysis"][study]
            run.summary[study] = {
                k: v for k, v in analysis.items() if k not in ("tasks", "missing")
            }
            columns = ["task", "baseline", "harness2", "diagnostics", "diagnostics_minus_raw_pp"]
            run.log(
                {
                    study + "/paired_scores": wandb.Table(
                        columns=columns, data=[[r[c] for c in columns] for r in analysis["tasks"]]
                    )
                }
            )
        run.summary["accounting"] = data["cost"]
        for path in sorted((runtime.state / "studies").glob("*/frozen/main/*/*/*/freeze.json")):
            freeze = read(path)
            harness = wandb.Artifact(
                "harness-" + freeze["source_fingerprint"][:20],
                type="harness",
                metadata=portable(runtime, freeze),
            )
            harness.add_dir(str(path.parent))
            run.log_artifact(harness)
        campaign_url = run.url
    for row in data["rows"]:
        if not row.get("output"):
            continue
        package = runtime.state / "row-publication" / row["run_id"]
        package.mkdir(parents=True, exist_ok=True)
        save(package / "evidence.json", portable(runtime, row))
        save(package / "decisions.json", portable(runtime, row.get("improvement_decisions", [])))
        shutil.copy2(Path(row["freeze"]) / "harness.diff", package / "harness.diff")
        shutil.make_archive(str(package / "deliverable"), "zip", row["output"])
        with wandb.init(
            entity=entity,
            project=project,
            id=row["run_id"],
            resume="allow",
            job_type="heldout",
            name=f"{row['study']} / {row['task']} / {row['condition']} / Trial {row['trial']}",
            config={
                "campaign": campaign,
                **{k: row[k] for k in ("study", "task", "group", "trial", "condition")},
                "executed_harness": row["frozen_harness"]["rendered_fingerprint"],
            },
            settings=settings,
        ) as run:
            run.summary.update(
                {
                    "score": row["score"],
                    "status": row["status"],
                    "trace_url": (row.get("trace") or {}).get("url"),
                    "evidence_artifact": artifact.url,
                    "observed_cost_usd": row["observed_cost_usd"],
                    "bounded_unknown_cost_usd": row["bounded_unknown_cost_usd"],
                    "latency_seconds": row["latency_seconds"],
                    "tool_errors": row["tool_errors"],
                    "delivered": row["delivered"],
                }
            )
            for path in sorted(package.iterdir()):
                run.save(str(path), base_path=str(package), policy="now")
            run.use_artifact(artifact)
    make_public(runtime, wandb.Api())
    download_url = f"https://api.wandb.ai/files/{runtime.manifest['project']}/{campaign}-summary/{quote(archive.name)}"
    text = markdown(data, artifact.url, download_url)
    previous = (
        read(runtime.state / "publication.json")
        if (runtime.state / "publication.json").exists()
        else {}
    )
    report = wr.Report(
        entity=entity,
        project=project,
        title="Do Agent Lens summaries help Harness²? A repeated experiment",
        description="Repository coding results and a separate JobBench replication, with uncertainty and complete evidence.",
        blocks=[wr.MarkdownBlock(text=text)],
    )
    if previous.get("report_id"):
        report.id = previous["report_id"]
    report.save()
    receipt = {
        "report_id": report.id,
        "report_url": report.url,
        "artifact_url": artifact.url,
        "download_url": download_url,
        "campaign_url": campaign_url,
    }
    save(runtime.state / "publication.json", receipt)
    (output / "report.md").write_text(text)
    return receipt


def report(runtime, *, publish=False):
    data = dataset(runtime)
    if publish:
        if not (runtime.state / "evaluate-complete.json").exists():
            raise CampaignStop(
                "Account for every planned execution and judgment before publication"
            )
        judge_stability(runtime)
        data = dataset(runtime)
    output, archive = prepare_output(runtime, data)
    if publish:
        return globals()["publish"](runtime, data, output, archive)
    print(
        json.dumps(
            {"complete": data["complete"], "output": str(output), "archive": str(archive)}, indent=2
        )
    )
    return data
