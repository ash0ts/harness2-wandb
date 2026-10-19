"""Auditable task-level results for pilot-v2, including stopped qualifications."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import random
import re
import statistics
import zipfile
from collections import defaultdict
from pathlib import Path

from .protocol import write_json
from .report import bundle, make_public, ratio
from .runtime import ROOT, fingerprint
from .telemetry import tool_events, verify_readback


def read(path):
    return json.loads(Path(path).read_text())


def message_texts(value):
    """Agent Lens readback can encode content parts as a JSON string."""
    if isinstance(value, str):
        if value.startswith(("[", "{")):
            try:
                return message_texts(json.loads(value))
            except ValueError:
                pass
        return [value]
    if isinstance(value, list):
        return [text for part in value for text in message_texts(part)]
    if isinstance(value, dict):
        return [
            text
            for key in ("content", "text")
            if key in value
            for text in message_texts(value[key])
        ]
    return []


def row_key(row):
    return row["task"], row["adaptation_replicate"], row["repetition"]


def reconcile(rows):
    seen = set()
    for row in rows:
        key = row["arm"], *row_key(row)
        if key in seen:
            raise ValueError(f"Duplicate evaluation occurrence: {key}")
        seen.add(key)
        row["fraction"] = ratio(row)
        row["earned_weight"] = (row.get("score") or {}).get("n_passed")
        row["possible_weight"] = (row.get("score") or {}).get("n_criteria")
        if row.get("score") and row.get("judge_evidence"):
            evidence = read(row["judge_evidence"])
            if (evidence.get("total_score"), evidence.get("max_score")) != (
                row["earned_weight"],
                row["possible_weight"],
            ):
                raise ValueError("Native score differs from judge evidence")
        if row["status"] == "judgment_unavailable" and row["fraction"] is not None:
            raise ValueError("Unavailable judgments must not become zeros")
    baselines = {row_key(r): r for r in rows if r["arm"] == "baseline"}
    controls = {row_key(r): r for r in rows if r["arm"] == "harness2"}
    for row in rows:
        base = baselines.get(row_key(row))
        row["paired_change"] = (
            row["fraction"] - base["fraction"]
            if base and base["fraction"] is not None and row["fraction"] is not None
            else None
        )
        control = controls.get(row_key(row))
        row["diagnostics_increment"] = (
            row["fraction"] - control["fraction"]
            if row["arm"] == "diagnostics"
            and control
            and row["fraction"] is not None
            and control["fraction"] is not None
            else None
        )
    return rows


def task_means(rows, planned_per_task):
    """Repetitions are averaged within tasks; each held-out task gets one overall vote."""
    result = []
    for task in sorted({r["task"] for r in rows}):
        for arm in ("baseline", "harness2", "diagnostics"):
            group = [r for r in rows if r["task"] == task and r["arm"] == arm]
            values = [r["fraction"] for r in group if r["fraction"] is not None]
            changes = [r["paired_change"] for r in group if r["paired_change"] is not None]
            increments = [
                r["diagnostics_increment"]
                for r in group
                if r.get("diagnostics_increment") is not None
            ]
            result.append(
                {
                    "task": task,
                    "arm": arm,
                    "mean": statistics.mean(values) if values else None,
                    "paired_change": statistics.mean(changes) if changes else None,
                    "observations": len(values),
                    "paired_observations": len(changes),
                    "planned_observations": planned_per_task,
                    "complete": len(values) == planned_per_task
                    and len(changes) == planned_per_task,
                    "diagnostics_increment": statistics.mean(increments) if increments else None,
                    "increment_observations": len(increments),
                }
            )
    return result


def task_uncertainty(means, seed):
    values = [
        r["diagnostics_increment"]
        for r in means
        if r["arm"] == "diagnostics"
        and r["complete"]
        and r["increment_observations"] == r["planned_observations"]
    ]
    if not values:
        return {"complete_tasks": 0, "mean": None}
    rng = random.Random(seed)
    samples = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(10000))
    return {
        "complete_tasks": len(values),
        "mean": statistics.mean(values),
        "positive_tasks": sum(v > 0 for v in values),
        "negative_tasks": sum(v < 0 for v in values),
        "task_range": [min(values), max(values)],
        "bootstrap_95_interval": [samples[249], samples[9749]],
        "method": "Exploratory percentile bootstrap over task families, 10000 samples. Execution and adaptation repetitions are averaged within each task. Six tasks do not establish broad generalization.",
    }


def audit(runtime):
    from decimal import Decimal

    from .budget import micros

    budget = runtime.budget()
    if budget["unresolved"]:
        raise ValueError("Unresolved usage blocks publication certification")
    for call in budget["calls"]:
        record = read(runtime.state / "gateway/sessions" / call["session"] / (call["id"] + ".json"))
        if call["status"] == "rejected":
            assert call["spent"] == 0 and 400 <= record["error"]["http_status"] < 500
            continue
        usage = record["response"]["usage"]
        assert usage == json.loads(call["usage"])
        assert record["request"]["model"] == record["response"]["model"] == call["model"]
        price = runtime.manifest["prices_per_million"][call["model"]]
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        cost = (
            (usage["prompt_tokens"] - cached) * Decimal(price["input"])
            + cached * Decimal(price["cached"])
            + usage["completion_tokens"] * Decimal(price["output"])
        ) / 1_000_000
        assert micros(cost) == call["spent"] <= call["reserved"]
    sessions, spans, outputs, decisions = 0, 0, 0, []
    for directory in sorted((runtime.state / "gateway/sessions").glob("*")):
        if not directory.is_dir() or not runtime.calls(directory.name):
            continue
        execution = runtime.state / "executions" / directory.name
        trace = read(execution / "trace.json")
        assert trace["verified"]
        stored = read(execution / "stored-spans.json")
        verify_readback(
            stored, runtime.calls(directory.name), tool_events(execution / "events.jsonl")
        )
        assert len(stored) == trace["expected_spans"]
        if (execution / "execution.json").exists():
            result = read(execution / "execution.json")
            assert result["collection_status"] == "complete"
            # Check the public Turn input is the actual task/mission, not title generation.
            entry = (execution / "entry-message.txt").read_text()
            roots = [r for r in stored if not r.get("parent_span_id")]
            if not any(entry in message_texts(r.get("input_messages", [])) for r in roots):
                raise ValueError("Stored Turn does not begin with the execution mission")
        if (execution / "decision.json").exists():
            decisions.append(read(execution / "decision.json"))
        sessions += 1
        spans += len(stored)
    for area in ("arms", "qualification"):
        for path in (runtime.state / area).rglob("showcase.json"):
            record = read(path)
            assert record["output_fingerprint"] == fingerprint(record["output_dir"])
            outputs += 1
    for area in ("frozen", "qualification-frozen"):
        for path in (runtime.state / area).rglob("freeze.json"):
            record = read(path)
            assert record["source_fingerprint"] == fingerprint(path.parent / "harness")
    write_json(runtime.state / "cost-ledger.json", budget)
    value = {
        "provider_requests_reconciled": len(budget["calls"]),
        "sessions_verified": sessions,
        "stored_spans_verified": spans,
        "output_fingerprints_verified": outputs,
        "saved_decisions": len(decisions),
        "accepted_decisions": sum(d["accepted"] for d in decisions),
        "accepted_executable_changes": sum(
            d["accepted"] and d.get("executable_change", False) for d in decisions
        ),
        "new_spend_usd": budget["spent_usd"],
        "lifetime_spend_usd": float(runtime.manifest["prior_spend_usd"]) + budget["spent_usd"],
    }
    write_json(runtime.state / "validation.json", value)
    return value


def dataset(runtime):
    rows = reconcile([read(p) for p in sorted((runtime.state / "scores").rglob("*.json"))])
    allocation = (
        read(runtime.state / "allocation.json")
        if (runtime.state / "allocation.json").exists()
        else None
    )
    planned_per_task = runtime.manifest["adaptation_replicates"] * (
        allocation["repetitions"] if allocation else runtime.manifest["repetitions"]
    )
    budget = runtime.budget()
    for row in rows:
        directory = runtime.state / "executions" / row["session"]
        execution = read(directory / "execution.json")
        spans = read(directory / "stored-spans.json")
        files = read(directory / "file-inventory.json")
        row.update(
            latency_seconds=execution["ended"] - execution["started"],
            explicit_errors=sum(
                bool(s.get("error_type") or s.get("status_code") in (2, "ERROR")) for s in spans
            ),
            produced_files=len(files),
            readable_files=sum(f["readable"] is True for f in files),
            inference_cost_usd=sum(
                c["spent"] or 0 for c in budget["calls"] if c["session"] == row["session"]
            )
            / 1e6,
        )
    costs = defaultdict(float)
    for call in budget["calls"]:
        if call["spent"] is not None:
            costs[call["phase"]] += call["spent"] / 1e6
    sessions = []
    for path in sorted((runtime.state / "gateway/sessions").glob("*/identity.json")):
        identity = read(path)
        directory = runtime.state / "executions" / identity["session"]
        trace = read(directory / "trace.json") if (directory / "trace.json").exists() else None
        decision = (
            read(directory / "decision.json") if (directory / "decision.json").exists() else None
        )
        sessions.append(
            {
                **identity,
                "trace": trace,
                "decision": decision,
                "cost_usd": sum(
                    c["spent"] or 0 for c in budget["calls"] if c["session"] == identity["session"]
                )
                / 1e6,
            }
        )
    frozen = [read(p) for p in sorted((runtime.state / "frozen").rglob("freeze.json"))]
    qualification_runs = []
    for path in sorted((runtime.state / "qualification-frozen").rglob("freeze.json")):
        frozen_case = read(path)
        if not frozen_case.get("result"):
            continue
        result = read(frozen_case["result"])
        selection = result.get("selections", {}).get("seq", {})
        if not selection.get("chosen_run_dir"):
            continue
        out = Path(selection["chosen_run_dir"])
        from .v2 import V2Adapter

        adapter = V2Adapter(
            runtime, frozen_case["arm"], frozen_case["adaptation_replicate"], qualification=True
        )
        task = frozen_case["domain"] + "/task1"
        marker = read(adapter._traj_dir(task, out.name) / "showcase.json")
        _, evidence = adapter._details_candidates(task, out.name)
        grade = read(evidence) if evidence.exists() else None
        stages = result.get("modes", {}).get("seq", {}).get("stages", [])
        qualification_runs.append(
            {
                "cohort": "qualification",
                "arm": frozen_case["arm"],
                "task": task,
                "adaptation_replicate": frozen_case["adaptation_replicate"],
                "repetition": 0,
                "session": marker["session"],
                "trace": marker["trace"],
                "output_dir": str(out),
                "harness_fingerprint": frozen_case["source_fingerprint"],
                "rendered_fingerprint": frozen_case["rendered_fingerprint"],
                "score": {"n_passed": grade["total_score"], "n_criteria": grade["max_score"]}
                if grade
                else None,
                "fraction": grade["total_score"] / grade["max_score"] if grade else None,
                "earned_weight": grade["total_score"] if grade else None,
                "possible_weight": grade["max_score"] if grade else None,
                "paired_change": None,
                "status": "judged" if grade else "judgment_unavailable",
                "judge_evidence": str(evidence) if grade else None,
                "valid_edit_stages": sum(not stage["fell_back"] for stage in stages),
                "fallback_stages": [stage["label"] for stage in stages if stage["fell_back"]],
                "files": read(
                    runtime.state / "executions" / marker["session"] / "file-inventory.json"
                ),
            }
        )
    complete = (runtime.state / "evaluate-complete.json").exists()
    means = task_means(rows, planned_per_task)
    return {
        "campaign": runtime.manifest["campaign"],
        "manifest": runtime.manifest,
        "rows": rows,
        "task_means": means,
        "diagnostics_comparison": task_uncertainty(means, runtime.manifest["seed"]),
        "sessions": sessions,
        "frozen": frozen,
        "complete": complete,
        "qualification": read(runtime.state / "qualification.json")
        if (runtime.state / "qualification.json").exists()
        else None,
        "qualification_runs": qualification_runs,
        "evidence_review": (runtime.state / "presentation/evidence-review.md").read_text()
        if (runtime.state / "presentation/evidence-review.md").exists()
        else None,
        "allocation": allocation,
        "cost_projection": read(runtime.state / "cost-projection.json")
        if (runtime.state / "cost-projection.json").exists()
        else None,
        "stopped": read(runtime.state / "stopped.json")
        if (runtime.state / "stopped.json").exists() and not complete
        else None,
        "spent_usd": budget["spent_usd"],
        "lifetime_spent_usd": float(runtime.manifest["prior_spend_usd"]) + budget["spent_usd"],
        "reserved_usd": budget["reserved_usd"],
        "unresolved_requests": budget["unresolved"],
        "costs_by_phase": dict(costs),
        "heldout_judged": sum(r["fraction"] is not None for r in rows),
        "heldout_planned": planned_per_task * len(runtime.manifest["task_families"]) * 3,
        "independent_tasks": len(runtime.manifest["task_families"]),
    }


def choose_case(data):
    candidates = [
        r
        for r in data["task_means"]
        if r["arm"] != "baseline" and r["complete"] and r["paired_change"] > 0
    ]
    return (
        sorted(candidates, key=lambda r: (-r["paired_change"], r["task"], r["arm"]))[0]
        if candidates
        else None
    )


def narrative(data):
    if not data["complete"]:
        return (
            "The campaign stopped before the controlled comparison. No held-out quality or diagnostics-benefit claim is supported."
            if data.get("stopped") and not data["rows"]
            else "The comparison has not completed. No performance claim is supported."
        )
    changes = [
        s
        for s in data["sessions"]
        if s.get("decision")
        and s.get("phase") in ("adapt", "retry")
        and s["decision"].get("accepted")
        and s["decision"].get("executable_change")
    ]
    case = choose_case(data)
    if not changes:
        return "The main comparison completed, but no accepted executable change was verified in its adaptations. Score variation does not establish a harness improvement."
    if not case:
        return "The loop produced and tested executable harness changes. No complete positive task comparison is available; inspect coverage and per-task results before interpreting this as a quality result."
    return "Some held-out task means improved. The full table retains every result and unavailable judgment. A selected positive example alone does not establish a benefit from diagnostics."


def markdown(data, artifact_url=""):
    text = [
        "# Harness² + Agent Lens: pilot-v2",
        "",
        narrative(data),
        "",
        "The protocol defines a JobBench-based pilot with a frozen-harness transfer test. Model weights stay fixed. Harness² already receives execution trajectories; the added treatment is a bounded deterministic summary retrieved from stored Agent Lens spans.",
        "",
        f"Coverage: **{data['heldout_judged']}/{data['heldout_planned']}** planned held-out judgments. New inference spend: **${data['spent_usd']:.6f}**; including pilot-v1: **${data['lifetime_spent_usd']:.6f}**. Unresolved reservations: **${data['reserved_usd']:.6f}**.",
        "",
        "The protocol calls for two independent adaptation runs per condition and domain using sequential recursion with k=2. The adaptive conditions share initial baseline executions, then develop separately. Frozen harnesses use an interleaved evaluation schedule. Repetitions are averaged within tasks; the proposed comparison has six held-out tasks.",
        "",
        "[Reproducible fork](https://github.com/ash0ts/harness2-wandb/tree/showcase/wandb-agent-lens) · [Preserved pilot-v1](https://github.com/ash0ts/harness2-wandb/releases/tag/pilot-v1)"
        + (f" · [Download evidence]({artifact_url})" if artifact_url else ""),
        "",
        "## Held-out results",
        "",
        "| Task | Condition | Mean native score | Paired change | Judged / planned |",
        "|---|---|---:|---:|---:|",
    ]
    for row in data["task_means"]:
        score = "Unavailable" if row["mean"] is None else f"{row['mean']:.3%}"
        change = (
            "Unavailable"
            if row["paired_change"] is None
            else f"{100 * row['paired_change']:+.2f} pp"
        )
        text.append(
            f"| {row['task']} | {row['arm']} | {score} | {change} | {row['observations']} / {row['planned_observations']} |"
        )
    if not data["rows"]:
        text += [
            "",
            "No held-out results were collected. Proposed executions are not completed observations.",
        ]
    text += [
        "",
        "A no-output execution receives the native zero. An unavailable judgment remains missing. Failed adaptations transfer with baseline rather than disappearing from coverage.",
        "",
        "## Incremental diagnostics comparison",
        "",
        "| Task | Diagnostics minus Harness² | Paired observations |",
        "|---|---:|---:|",
    ]
    for row in data["task_means"]:
        if row["arm"] == "diagnostics":
            increment = (
                "Unavailable"
                if row["diagnostics_increment"] is None
                else f"{100 * row['diagnostics_increment']:+.2f} pp"
            )
            text.append(
                f"| {row['task']} | {increment} | {row['increment_observations']} / {row['planned_observations']} |"
            )
    comparison = data["diagnostics_comparison"]
    if comparison["mean"] is not None:
        lower, upper = comparison["bootstrap_95_interval"]
        text += [
            "",
            f"Equal-task mean difference: {100 * comparison['mean']:+.2f} percentage points across {comparison['complete_tasks']} complete tasks. Positive on {comparison['positive_tasks']} tasks; negative on {comparison['negative_tasks']}. Exploratory 95% task-bootstrap interval: [{100 * lower:+.2f}, {100 * upper:+.2f}] percentage points. Repetitions are averaged before resampling. This small sample does not support broad generalization.",
        ]
    if data.get("qualification_runs"):
        text += [
            "",
            "## Qualification",
            "",
            "These previously inspected tasks test completion and instrumentation. Their scores are native judgments of the selected qualification outputs, not a controlled comparison or transfer result.",
            "",
            "| Task | Valid decision stages | Readable files | Native score | Evidence |",
            "|---|---:|---:|---:|---|",
        ]
        for case in data["qualification_runs"]:
            score = (
                f"{case['earned_weight']} / {case['possible_weight']}"
                if case["score"]
                else "Unavailable"
            )
            run_url = f"https://wandb.ai/{data['manifest']['project']}/runs/{case['session'][:12]}"
            text.append(
                f"| {case['task']} | {case['valid_edit_stages']} / 6 | {sum(f['readable'] is True for f in case['files'])} | {score} | [Score, harness, deliverable]({run_url}) · [Trace]({case['trace']['url']}) |"
            )
    if data.get("evidence_review"):
        text += ["", "## What the qualification evidence shows", "", data["evidence_review"]]
    if data.get("cost_projection"):
        text += [
            "",
            "## Launch budget projection",
            "",
            f"The main-phase limit is ${float(data['manifest']['budget_buckets']['main']['usd']):.2f}. The gate uses per-stage maxima observed during qualification and a 50% contingency. These are spending forecasts, not incurred costs.",
            "",
            "| Transfer executions | Main forecast | Judge forecast | Fits both phase limits |",
            "|---|---:|---:|---|",
        ]
        for candidate in data["cost_projection"]["candidates"]:
            fits = candidate["fits_main_bucket"] and candidate["fits_judging_bucket"]
            judge = candidate["projected_judging_usd"]
            judge_text = f"${judge:.2f}" if judge is not None else "Not projected"
            text.append(
                f"| {candidate['planned_transfer_executions']} | ${candidate['projected_main_usd']:.2f} | {judge_text} | {'Yes' if fits else 'No'} |"
            )
        if data["cost_projection"].get("lower_bound"):
            text += [
                "",
                "Qualification did not pass. These forecasts exclude unobserved stages and judging; they are cost floors under the stated projection method. They cannot authorize a campaign launch.",
            ]
    text += [
        "",
        "## Cost",
        "",
        "| Phase | Recorded USD |",
        "|---|---:|",
    ]
    text.extend(
        f"| {phase} | ${cost:.6f} |" for phase, cost in sorted(data["costs_by_phase"].items())
    )
    text += [
        "",
        "Costs are recomputed from returned usage at the pinned prices, including auxiliary requests and retries. Agent Lens displays its own cost estimates.",
    ]
    selected = choose_case(data)
    negative = [
        r
        for r in data["task_means"]
        if r["arm"] != "baseline" and r["complete"] and r["paired_change"] < 0
    ]
    regression = (
        min(negative, key=lambda r: (r["paired_change"], r["task"], r["arm"])) if negative else None
    )
    for title, case in (
        ("Largest positive task difference", selected),
        ("Largest regression", regression),
    ):
        if not case:
            continue
        text += [
            "",
            "## " + title,
            "",
            f"{case['task']} / {case['arm']}: {100 * case['paired_change']:+.2f} percentage points versus baseline, averaged across all planned observations.",
            "",
            "| Condition | Adaptation / execution | Native score | W&B evidence | Agent Lens |",
            "|---|---|---:|---|---|",
        ]
        for row in data["rows"]:
            if row["task"] != case["task"] or row["arm"] not in (case["arm"], "baseline"):
                continue
            run_url = f"https://wandb.ai/{data['manifest']['project']}/runs/{row['session'][:12]}"
            text.append(
                f"| {row['arm']} | {row['adaptation_replicate']} / {row['repetition']} | {row['earned_weight']} / {row['possible_weight']} | [Score, harness and deliverable]({run_url}) | [Trace]({row['trace']['url']}) |"
            )
    text += [
        "",
        "## Execution evidence",
        "",
        "| Condition / role | Task | Stage | Agent Lens |",
        "|---|---|---|---|",
    ]
    # The complete session catalog is downloadable; keep this landing report readable.
    displayed_sessions = [
        s for s in data["sessions"] if s.get("decision") and s["decision"].get("accepted")
    ][:8]
    if not displayed_sessions:
        displayed_sessions = data["sessions"][:8]
    for session in displayed_sessions:
        trace = session.get("trace")
        if trace:
            text.append(
                f"| {session.get('arm')} / {session['role']} | {session.get('task')} | {session.get('stage', session.get('run_id', ''))} | [Conversation]({trace['url']}) |"
            )
    if data["stopped"]:
        text += ["", "## Stopping condition", "", data["stopped"]["reason"]]
    text += [
        "",
        "Agent Lens conversations can require authentication. The downloadable evidence includes stored spans, requests, tool events, timestamps, usage, completion receipts, harness versions and diffs, generated files, and judge evidence. Public captures accompany the walkthrough.",
        "",
        "Automatic Insights clustering is outside this pilot. Reading a diagnostic file does not establish that it helped; compare the saved decision, executable diff, subsequent execution, and held-out score.",
    ]
    return "\n".join(text) + "\n"


def publish_results(runtime, data, output):
    import wandb
    import wandb_workspaces.reports.v2 as wr

    entity, project = runtime.manifest["project"].split("/")
    settings = wandb.Settings(disable_git=True)
    harness_artifacts = {}
    with wandb.init(
        entity=entity,
        project=project,
        id=runtime.manifest["campaign"] + "-summary",
        resume="allow",
        job_type="campaign",
        config={"manifest": runtime.manifest},
        settings=settings,
    ) as run:
        artifact = wandb.Artifact(
            runtime.manifest["campaign"] + "-evidence",
            type="evidence",
            metadata={"complete": data["complete"], "new_spend_usd": data["spent_usd"]},
        )
        for name in ("results.json", "results.csv", "qualification.csv", "report.md"):
            artifact.add_file(str(output / name))
        artifact.add_dir(str(output / "evidence"))
        artifact = run.log_artifact(artifact)
        artifact.wait()
        cols = [
            "arm",
            "task",
            "adaptation_replicate",
            "repetition",
            "earned_weight",
            "possible_weight",
            "fraction",
            "paired_change",
            "status",
            "session",
            "harness_fingerprint",
            "trace_url",
        ]
        table = wandb.Table(columns=cols)
        for row in data["rows"]:
            table.add_data(
                *[(row["trace"]["url"] if c == "trace_url" else row.get(c)) for c in cols]
            )
        qualification_table = wandb.Table(columns=cols)
        for row in data["qualification_runs"]:
            qualification_table.add_data(
                *[(row["trace"]["url"] if c == "trace_url" else row.get(c)) for c in cols]
            )
        run.log({"heldout_results": table, "qualification_results": qualification_table})
        run.summary.update(
            {
                k: data[k]
                for k in (
                    "spent_usd",
                    "lifetime_spent_usd",
                    "heldout_judged",
                    "heldout_planned",
                    "complete",
                )
            }
        )
        decisions = wandb.Table(
            columns=[
                "session",
                "arm",
                "task",
                "stage",
                "phase",
                "adaptation_replicate",
                "accepted",
                "decision",
                "executable_change",
                "diagnostic_reference_calls",
                "trace_url",
            ]
        )
        for session in data["sessions"]:
            decision = session.get("decision")
            if decision:
                decisions.add_data(
                    session["session"],
                    session["arm"],
                    session["task"],
                    session["stage"],
                    session["phase"],
                    session.get("adaptation_replicate"),
                    decision["accepted"],
                    decision.get("receipt", {}).get("decision"),
                    decision.get("executable_change"),
                    len(decision["diagnostic_reads"]),
                    session["trace"]["url"],
                )
        run.log({"harness_decisions": decisions})
        for path in sorted(
            [
                *(runtime.state / "frozen").rglob("freeze.json"),
                *(runtime.state / "qualification-frozen").rglob("freeze.json"),
            ]
        ):
            frozen = read(path)
            version = wandb.Artifact(
                f"{runtime.manifest['campaign']}-{frozen['arm']}-{frozen['domain']}-rep{frozen['adaptation_replicate']}",
                type="harness",
                metadata={
                    k: frozen[k]
                    for k in (
                        "source_fingerprint",
                        "rendered_fingerprint",
                        "adoption",
                        "executable_change",
                    )
                },
            )
            version.add_dir(str(output / "evidence" / path.parent.relative_to(runtime.state)))
            version.add_file(
                str(output / "evidence" / path.parent.relative_to(runtime.state) / "harness.diff"),
                name="harness-diff.txt",
            )
            version = run.log_artifact(version)
            version.wait()
            harness_artifacts[(frozen["arm"], frozen["domain"], frozen["adaptation_replicate"])] = (
                version
            )
        campaign_url = run.url
    for row in data["rows"] + data["qualification_runs"]:
        with wandb.init(
            entity=entity,
            project=project,
            id=row["session"][:12],
            resume="allow",
            job_type=row["cohort"],
            name=f"v2 {row['cohort']} / {row['arm']} / {row['task']} / a{row['adaptation_replicate']} e{row['repetition']}",
            config={
                "campaign": runtime.manifest["campaign"],
                **{
                    k: row[k]
                    for k in (
                        "arm",
                        "task",
                        "adaptation_replicate",
                        "repetition",
                        "harness_fingerprint",
                        "rendered_fingerprint",
                    )
                },
            },
            settings=settings,
        ) as run:
            harness = harness_artifacts[
                (row["arm"], row["task"].split("/")[0], row["adaptation_replicate"])
            ]
            run.use_artifact(harness)
            deliverable = wandb.Artifact(
                runtime.manifest["campaign"] + "-output-" + row["session"][:12],
                type="deliverable",
                metadata={
                    "status": row["status"],
                    "harness_fingerprint": row["harness_fingerprint"],
                },
            )
            public_output = output / "evidence" / Path(row["output_dir"]).relative_to(runtime.state)
            deliverable.add_dir(str(public_output))
            deliverable.add_file(
                str(output / "evidence/executions" / row["session"] / "file-inventory.json")
            )
            deliverable = run.log_artifact(deliverable)
            deliverable.wait()
            run.summary.update(
                {
                    "score": row["fraction"],
                    "native_earned_weight": row["earned_weight"],
                    "native_possible_weight": row["possible_weight"],
                    "cohort": row["cohort"],
                    "paired_change": row["paired_change"],
                    "status": row["status"],
                    "trace_url": row["trace"]["url"],
                    "evidence_artifact": artifact.url,
                    "harness_artifact": harness.url,
                    "deliverable_artifact": deliverable.url,
                }
            )
            run.use_artifact(artifact)
    make_public(runtime, wandb.Api())
    report_text = "\n".join(markdown(data, artifact.url).splitlines()[4:])
    blocks = []
    cursor = 0
    for match in re.finditer(r"(?m)^!\[([^\]]*)\]\((https?://[^)]+)\)$", report_text):
        if report_text[cursor : match.start()].strip():
            blocks.append(wr.MarkdownBlock(text=report_text[cursor : match.start()]))
        blocks.append(wr.Image(url=match[2], caption=match[1]))
        cursor = match.end()
    if report_text[cursor:].strip():
        blocks.append(wr.MarkdownBlock(text=report_text[cursor:]))
    if (runtime.state / "presentation/walkthrough.mp4").exists():
        media = f"https://api.wandb.ai/files/{entity}/{project}/{runtime.manifest['campaign']}-presentation"
        blocks[:0] = [
            wr.MarkdownBlock(
                text=(
                    f"[Watch the 88-second walkthrough]({media}/walkthrough.mp4) · "
                    f"[Captions]({media}/walkthrough.vtt) · "
                    f"[Transcript]({media}/walkthrough-transcript.md)\n\n"
                    "An edited sequence of recorded native browser views, with captions and no audio."
                )
            ),
            wr.Image(
                url=f"{media}/pilot-v2-task-first-agent-lens.png",
                caption="Recorded Agent Lens task conversation. The walkthrough follows its harness, execution and deliverable evidence.",
            ),
        ]
    report = wr.Report(
        entity=entity,
        project=project,
        title="Harness² + Agent Lens: pilot-v2",
        description=narrative(data),
        blocks=blocks,
    )
    previous = runtime.state / "publication.json"
    if previous.exists():
        report.id = read(previous)["report_id"]
    report.save()
    receipt = {
        "report_url": f"https://wandb.ai/{entity}/{project}/reports/Harness-Agent-Lens-pilot-v2--{report.id}",
        "sdk_report_url": report.url,
        "report_id": report.id,
        "artifact_url": artifact.url,
        "campaign_url": campaign_url,
    }
    write_json(previous, receipt)
    write_json(output / "publication.json", receipt)
    return receipt


def report(runtime, *, publish=False):
    validation = audit(runtime)
    if (runtime.state / "stopped.json").exists() and not (
        runtime.state / "allocation.json"
    ).exists():
        from .v2 import stopped_cost_floor

        stopped_cost_floor(runtime)
    data = dataset(runtime)
    if publish and not data["complete"] and not data["stopped"]:
        raise ValueError("Only a complete or explicitly stopped campaign can be published")
    output = runtime.state / "public"
    output.mkdir(exist_ok=True)
    gate_path = runtime.state / "article-gate.json"
    prior_gate = read(gate_path) if gate_path.exists() else {}
    review_hash = hashlib.sha256((data.get("evidence_review") or "").encode()).hexdigest()
    reviewed = (
        prior_gate.get("article_ready", False)
        and prior_gate.get("validation") == validation
        and prior_gate.get("evidence_review_sha256") == review_hash
    )
    write_json(
        gate_path,
        {
            **(prior_gate if reviewed else {}),
            "evidence_audited": True,
            "validation": validation,
            "evidence_review_sha256": review_hash,
            "primary_comparison_complete": data["complete"],
            "article_ready": bool(reviewed),
            "remaining_review": None
            if reviewed
            else "Inspect selected deliverable and native criteria; capture native product screens and follow-up evidence before writing.",
        },
    )
    bundle(runtime, output / "evidence")
    # Canonical experimental source is public alongside its pinned manifest.
    source = output / "source"
    source.mkdir(exist_ok=True)
    for p in (ROOT / "harness2/showcase").glob("*.py"):
        (source / p.name).write_bytes(p.read_bytes())
    portable = json.loads(
        json.dumps(data)
        .replace(str(runtime.state) + "/", "evidence/")
        .replace(str(ROOT), "<checkout>")
    )
    write_json(output / "results.json", portable)
    with (output / "results.csv").open("w") as stream:
        cols = [
            "arm",
            "task",
            "adaptation_replicate",
            "repetition",
            "earned_weight",
            "possible_weight",
            "fraction",
            "paired_change",
            "status",
            "session",
            "harness_fingerprint",
            "rendered_fingerprint",
        ]
        writer = csv.DictWriter(stream, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(portable["rows"])
    with (output / "qualification.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(portable["qualification_runs"])
    text = markdown(data)
    (output / "report.md").write_text(text)
    # A portable readable fallback, while the walkthrough uses native product screens.
    body = html.escape(text)
    (output / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Harness² pilot-v2 evidence</title><style>body{max-width:1100px;margin:3rem auto;padding:0 1.5rem;font:16px/1.6 system-ui;background:#f4f1eb;color:#19242c}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Harness² + Agent Lens</h1><p><a href="results.csv">Complete results CSV</a> · <a href="results.json">Machine-readable evidence index</a> · <a href="evidence/manifest.json">Frozen protocol</a></p><pre>'
        + body
        + "</pre></html>"
    )
    if publish:
        receipt = publish_results(runtime, data, output)
        print(json.dumps(receipt, indent=2))
    archive = runtime.state / "pilot-v2-evidence.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(output.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(output))
    return output
