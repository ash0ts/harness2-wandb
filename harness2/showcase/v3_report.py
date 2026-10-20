"""Six-task comparison, with the diagnostics contrast as the primary result."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import statistics
from collections import defaultdict
from urllib.parse import quote

from .identity import CONDITIONS, DOMAINS
from .protocol import write_json
from .runtime import ROOT, command
from .v2_report import dataset as base_dataset
from .v2_report import read
from .v2_report import report as base_report


def execution_metrics(spans):
    tools = [s for s in spans if s.get("tool_name")]
    failures = [s for s in tools if s.get("error_type") or s.get("status_code") in (2, "ERROR")]
    signatures = defaultdict(list)
    for span in failures:
        arguments = span.get("tool_call_arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                pass
        key = json.dumps(
            [span["tool_name"], arguments, span.get("error_type"), span.get("status_message")],
            sort_keys=True,
        )
        signatures[key].append(span["span_id"])
    return {
        "tool_calls": len(tools),
        "tool_errors": len(failures),
        "tool_error_rate": len(failures) / len(tools) if tools else None,
        "repeated_error_occurrences": sum(len(ids) - 1 for ids in signatures.values()),
        "error_signatures": [
            {
                "signature_sha256": hashlib.sha256(key.encode()).hexdigest(),
                "count": len(ids),
                "span_ids": ids,
            }
            for key, ids in sorted(signatures.items())
        ],
    }


def comparison_rows(data):
    actual = {(r["task"], r["arm"]): r for r in data["rows"]}
    result = []
    for family in data["manifest"]["task_families"]:
        task = family["domain"] + "/" + family["heldout"]
        row = {"task": task, "job": DOMAINS[family["domain"]]}
        for arm in ("baseline", "harness2", "diagnostics"):
            source = actual.get((task, arm), {})
            row[arm] = source.get("fraction")
            row[arm + "_run"] = source.get("run_url")
            row[arm + "_native"] = (
                f"{source['earned_weight']:g}/{source['possible_weight']:g}"
                if source.get("fraction") is not None
                else None
            )
        row["paired"] = row["harness2"] is not None and row["diagnostics"] is not None
        row["complete"] = row["paired"] and row["baseline"] is not None
        row["difference_pp"] = (
            (row["diagnostics"] - row["harness2"]) * 100 if row["paired"] else None
        )
        for arm in ("harness2", "diagnostics"):
            row[arm + "_minus_original_pp"] = (
                (row[arm] - row["baseline"]) * 100
                if row[arm] is not None and row["baseline"] is not None
                else None
            )
        result.append(row)
    return result


def primary_result(comparison):
    pairs = [r["difference_pp"] for r in comparison if r["paired"]]
    result = {
        "diagnostics_complete_pairs": len(pairs),
        "independent_tasks": len(comparison),
        "diagnostics_mean_difference_pp": statistics.mean(pairs) if pairs else None,
        "diagnostics_wins": sum(v > 1e-12 for v in pairs),
        "diagnostics_ties": sum(abs(v) <= 1e-12 for v in pairs),
        "diagnostics_losses": sum(v < -1e-12 for v in pairs),
    }
    for arm in ("harness2", "diagnostics"):
        values = [
            r[arm + "_minus_original_pp"]
            for r in comparison
            if r.get(arm + "_minus_original_pp") is not None
        ]
        result[arm + "_original_complete_pairs"] = len(values)
        result[arm + "_mean_difference_from_original_pp"] = (
            statistics.mean(values) if values else None
        )
    return result


def choose_examples(comparison):
    candidates = [r for r in comparison if r["complete"]]
    positives = [r for r in candidates if r["difference_pp"] > 1e-12]
    example = (
        sorted(positives, key=lambda r: (-round(r["difference_pp"], 12), r["task"]))[0]
        if positives
        else sorted(candidates, key=lambda r: (-round(abs(r["difference_pp"]), 12), r["task"]))[0]
        if candidates
        else None
    )
    negatives = [r for r in candidates if r["difference_pp"] < -1e-12]
    regression = (
        sorted(negatives, key=lambda r: (round(r["difference_pp"], 12), r["task"]))[0]
        if negatives
        else None
    )
    return {"example": example, "regression": regression}


def cited_evidence(runtime, decision):
    if not decision:
        return [], []
    record = decision["decision"]
    requested = set((record.get("receipt") or {}).get("cited_span_ids", []))
    found = {}
    for session in record.get("source_sessions", []):
        for span in read(runtime.state / "executions" / session / "stored-spans.json"):
            if span["span_id"] in requested:
                conversation = span["conversation_id"]
                found[span["span_id"]] = {
                    "span_id": span["span_id"],
                    "session": session,
                    "conversation": conversation,
                    "url": f"https://forge.coreweave.com/agent-lens/"
                    f"{runtime.manifest['project']}/conversations/{conversation}"
                    f"/spans/{span['span_id']}",
                }
    return [found[key] for key in sorted(found)], sorted(requested - found.keys())


def dataset(runtime):
    data = base_dataset(runtime, include_ungraded=True)
    project = runtime.manifest["project"]
    for row in data["rows"]:
        directory = runtime.state / "executions" / row["session"]
        row.update(execution_metrics(read(directory / "stored-spans.json")))
        row["readable_output"] = (
            row["readable_files"] > 0 and row["readable_files"] == row["produced_files"]
        )
        row["run_url"] = f"https://wandb.ai/{project}/runs/{row['session'][:12]}"
        files_url = f"https://api.wandb.ai/files/{project}/{row['session'][:12]}"
        row["execution_url"] = row["trace"]["url"]
        row["harness_diff_url"] = files_url + "/harness-diff.txt"
        row["judge_evidence_url"] = (
            files_url + "/judge-evidence.txt" if row.get("judge_evidence") else None
        )
        row["judge_trace_url"] = (row.get("judge_trace") or {}).get("url")
        row["deliverables"] = [
            {**f, "url": files_url + "/deliverables/" + quote(f["path"], safe="/")}
            for f in read(directory / "file-inventory.json")
        ]
        frozen = next(
            f
            for f in data["frozen"]
            if f["arm"] == row["arm"]
            and f["domain"] == row["domain"]
            and f["adaptation_replicate"] == row["adaptation_replicate"]
        )
        row["executable_change"] = frozen["executable_change"]
        row["adoption"] = frozen["adoption"]
        decisions = [
            s
            for s in data["sessions"]
            if s.get("task") == row["domain"] + "/task1"
            and s.get("arm") == row["arm"]
            and s.get("phase") in ("adapt", "retry")
            and (s.get("decision") or {}).get("accepted")
            and (s["decision"].get("after") or {}).get("source_fingerprint")
            == frozen["source_fingerprint"]
        ]
        decisions.sort(
            key=lambda s: (
                not s.get("stage", "").startswith(frozen.get("chosen_label") or "-"),
                s.get("stage", ""),
                s.get("proposal_attempt", 1),
            )
        )
        row["adoption_decision"] = decisions[0] if decisions else None
        row["decision_evidence_url"] = (
            files_url + "/improvement-decision.json" if decisions else None
        )
        row["cited_spans"], row["unverified_cited_span_ids"] = cited_evidence(
            runtime, row["adoption_decision"]
        )
    data["comparison"] = comparison_rows(data)
    data["primary_result"] = primary_result(data["comparison"])
    data.update(choose_examples(data["comparison"]))
    data["executions_complete"] = data["complete"]
    data["complete"] = (
        data["complete"]
        and len(data["rows"]) == 18
        and all(r["complete"] for r in data["comparison"])
    )
    if not data["complete"] and (runtime.state / "stopped.json").exists():
        data["stopped"] = read(runtime.state / "stopped.json")
    costs = defaultdict(float)
    for session in data["sessions"]:
        if session.get("phase") in ("adapt", "retry") and session.get("role") != "judge":
            costs[session["arm"]] += session["cost_usd"]
    data["adaptation_costs_usd"] = dict(costs)
    data["views"] = (
        read(runtime.state / "presentation/views.json")
        if (runtime.state / "presentation/views.json").exists()
        else {}
    )
    capture = runtime.state / "presentation/capture-status.json"
    data["capture_status"] = read(capture) if capture.exists() else {}
    if (runtime.state / "presentation/walkthrough.mp4").exists():
        data["walkthrough"] = (
            "https://github.com/ash0ts/harness2-wandb/releases/download/"
            + runtime.manifest["campaign"]
            + "/walkthrough.mp4"
        )
    data["planned_evaluations"] = []
    actual = {(r["task"], r["arm"]): r for r in data["rows"]}
    executed = {
        (s["task"], s["arm"]): s
        for s in data["sessions"]
        if s.get("phase") == "evaluate" and s.get("role") == "solver"
    }
    for family in runtime.manifest["task_families"]:
        task = family["domain"] + "/" + family["heldout"]
        for arm in ("baseline", "harness2", "diagnostics"):
            score = actual.get((task, arm))
            execution = executed.get((task, arm))
            data["planned_evaluations"].append(
                {
                    "task": task,
                    "arm": arm,
                    "status": score["status"]
                    if score
                    else "executed_ungraded"
                    if execution
                    else "not_run",
                    "session": score["session"]
                    if score
                    else execution["session"]
                    if execution
                    else None,
                    "score": score["fraction"] if score else None,
                }
            )
    return data


def narrative(data):
    primary = data["primary_result"]
    if not data["complete"]:
        return (
            f"The comparison is incomplete: {data['heldout_judged']}/18 available results and "
            f"{primary['diagnostics_complete_pairs']}/6 paired task results. "
            "The experiment has not answered whether diagnostics help."
        )
    change = primary["diagnostics_mean_difference_pp"]
    return (
        f"Across six held-out jobs, diagnostics changed native quality by {change:+.2f} "
        f"percentage points on average versus raw feedback: "
        f"{primary['diagnostics_wins']} wins, {primary['diagnostics_ties']} ties, "
        f"and {primary['diagnostics_losses']} losses. "
        "Each setup ran once per job; this result does not establish repeatability."
    )


def score_cell(row, arm):
    if row[arm] is None:
        return "Unavailable"
    text = f"{row[arm + '_native']} ({100 * row[arm]:.1f}%)"
    return f"[{text}]({row[arm + '_run']})"


def markdown(data, artifact_url=""):
    project = data["manifest"]["project"]
    campaign = data["campaign"]
    media = f"https://api.wandb.ai/files/{project}/{campaign}-summary"
    lines = [
        "# Do Agent Lens summaries help Harness²?",
        "",
        narrative(data),
        "",
        "## Six-job comparison",
        "",
        "The same executor model and task inputs are used in all three setups. "
        "Harness² performs two sequential refinement rounds. Both improvement setups "
        "receive raw execution records; diagnostics adds deterministic summaries from "
        "stored Agent Lens spans. Each adopted harness is frozen before its fresh task.",
        "",
        "| Job | Original | Raw feedback | Diagnostics | Diagnostics − raw |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in data["comparison"]:
        change = f"{row['difference_pp']:+.2f} pp" if row["paired"] else "Unavailable"
        lines.append(
            "| "
            + " | ".join(
                [
                    row["job"],
                    *(score_cell(row, arm) for arm in ("baseline", "harness2", "diagnostics")),
                    change,
                ]
            )
            + " |"
        )
    lines += [
        "",
        "Cells preserve native earned/possible weight and its percentage. The primary "
        "contrast is diagnostics minus raw feedback, averaged equally across tasks. "
        "Executions without output receive the native zero without a judge request. "
        "Unavailable judgments remain missing. The 18 executions represent six jobs.",
        "",
    ]
    if data["complete"]:
        primary = data["primary_result"]
        lines += [
            f"Against the original harness, raw feedback changed quality by "
            f"{primary['harness2_mean_difference_from_original_pp']:+.2f} pp "
            f"and diagnostics by "
            f"{primary['diagnostics_mean_difference_from_original_pp']:+.2f} pp, "
            "with equal weight for each of the six tasks.",
            "",
        ]
    if any(r["paired"] for r in data["comparison"]):
        lines += [
            f"![Native held-out quality for each setup on six jobs]({media}/paired-scores.png)",
            "",
        ]
    if data.get("stopped"):
        lines += ["**Campaign stop:** " + data["stopped"]["reason"], ""]
    if data.get("cost_projection"):
        candidates = data["cost_projection"]["candidates"]
        if candidates:
            p = candidates[-1]
            lines += [
                f"Launch projection: ${p['projected_main_usd']:.2f} adaptation/execution "
                f"against ${data['manifest']['budget_buckets']['main']['usd']}. "
                "This is an observed-usage forecast with contingency, not actual spending.",
                "",
            ]
    lines += ["## Follow the evidence", ""]
    for label, example in (
        ("Selected comparison", data["example"]),
        ("Regression", data["regression"]),
    ):
        if not example or (label == "Regression" and example == data["example"]):
            continue
        lines += [f"### {label}: {example['job']}", ""]
        for arm in ("harness2", "diagnostics"):
            row = next(r for r in data["rows"] if r["task"] == example["task"] and r["arm"] == arm)
            links = [
                f"[Result and artifacts]({row['run_url']})",
                f"[Harness diff]({row['harness_diff_url']})",
                f"[Execution]({row['trace']['url']})",
            ]
            decision = row.get("adoption_decision")
            if decision:
                links.append(f"[Improvement decision]({decision['trace']['url']})")
            if row.get("judge_trace_url"):
                links.append(f"[Native judging]({row['judge_trace_url']})")
            if row.get("judge_evidence_url"):
                links.append(f"[Judge reasons]({row['judge_evidence_url']})")
            lines += [
                f"**{CONDITIONS[arm]}:** " + " · ".join(links),
                f"Executed harness `{row['rendered_fingerprint'][:16]}`; "
                f"executable change from baseline: {'yes' if row['executable_change'] else 'no'}.",
                "",
            ]
            if row["deliverables"]:
                lines += [
                    "Generated files: "
                    + " · ".join(f"[{f['path']}]({f['url']})" for f in row["deliverables"]),
                    "",
                ]
            if row["cited_spans"]:
                lines += [
                    "Decision citations: "
                    + " · ".join(f"[{s['span_id']}]({s['url']})" for s in row["cited_spans"]),
                    "",
                ]
            if row["unverified_cited_span_ids"]:
                lines += [
                    "Unverified decision citations: "
                    + ", ".join(f"`{s}`" for s in row["unverified_cited_span_ids"])
                    + ". These IDs were not found in the supplied execution records.",
                    "",
                ]
        lines += [
            "The trace records what happened. Summary reads, cited spans, and checker "
            "passes alone do not establish a quality gain.",
            "",
        ]
    if data.get("evidence_review"):
        lines += [data["evidence_review"], ""]
    lines += ["## Execution behavior and cost", ""]
    if data["rows"]:
        lines += [
            "| Job | Setup | Tool errors / calls | Repeated errors | Readable output | Seconds | Inference USD |",
            "|---|---|---:|---:|---|---:|---:|",
        ]
        for row in sorted(data["rows"], key=lambda r: (r["task"], r["arm"])):
            lines.append(
                f"| {DOMAINS[row['domain']]} | {CONDITIONS[row['arm']]} | "
                f"{row['tool_errors']} / {row['tool_calls']} | {row['repeated_error_occurrences']} | "
                f"{'yes' if row['readable_output'] else 'no'} | {row['latency_seconds']:.1f} | "
                f"{row['inference_cost_usd']:.4f} |"
            )
        lines += [
            "",
            "Repeated errors count additional occurrences of the same tool, normalized "
            "arguments, error type, and exact error message within an execution. "
            "Readable output is a file check, not confirmation that every requirement was met.",
            "",
        ]
    costs = data["adaptation_costs_usd"]
    lines += [
        f"Adaptation: shared starting executions ${costs.get('baseline', 0):.4f}; "
        f"raw feedback ${costs.get('harness2', 0):.4f}; "
        f"diagnostics ${costs.get('diagnostics', 0):.4f}. Shared executions are counted once.",
        f"New spending: **${data['spent_usd']:.6f}**. Lifetime spending: "
        f"**${data['lifetime_spent_usd']:.6f} / $100**. Qualification, retries, "
        "reflection, and judging are included in the complete ledger. "
        "Costs are calculated from recorded usage and the manifest's pinned prices, including cached input. Agent Lens displays its own cost estimate.",
        "",
        "## Conversations and reproduction",
        "",
    ]
    for label in ("Held-out execution", "Adaptation", "Judging", "Qualification"):
        view = data["views"].get(label)
        if isinstance(view, dict) and view.get("url"):
            lines.append(f"- [{label}]({view['url']})")
    results_view = (
        data["views"]
        .get("Held-out execution", {})
        .get("url", f"https://forge.coreweave.com/agent-lens/{project}/conversations")
    )
    lines += [
        "",
        f"[Agent Lens results view]({results_view}) · "
        f"[Reproducible code](https://github.com/ash0ts/harness2-wandb/tree/{campaign}) · "
        f"[Downloads](https://github.com/ash0ts/harness2-wandb/releases/tag/{campaign})",
        "",
    ]
    if artifact_url:
        lines += [f"[Complete W&B evidence artifact]({artifact_url})", ""]
    if data.get("walkthrough"):
        lines += [f"[Recorded walkthrough]({data['walkthrough']})", ""]
    capture = data.get("capture_status", {})
    if capture.get("status") in ("blocked", "partial"):
        lines += [
            "Native capture is incomplete: "
            + capture.get("observation", "The capture environment is unavailable.")
            + " Trace links and exported records remain available. "
            "No simulated product screens are substituted.",
            "",
        ]
    lines += [
        "Qualification and auxiliary title calls are retained separately from the comparison. "
        "Agent Lens may require authentication; downloaded records provide another route "
        "to the evidence.",
        "",
        "Six selected tasks and one run per setup support a bounded pilot observation. "
        "The study does not estimate run-to-run variability or establish a general ranking.",
    ]
    return "\n".join(lines) + "\n"


def prepare_output(runtime, data, output):
    for name, records in (
        ("comparison.csv", data["comparison"]),
        ("evaluation-status.csv", data["planned_evaluations"]),
    ):
        with (output / name).open("w") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
    write_json(
        output / "chart-data.json",
        {
            "comparison": data["comparison"],
            "complete": data["complete"],
            "primary_result": data["primary_result"],
        },
    )
    write_json(
        runtime.state / "presentation/figure-contract.json",
        {
            "question": "Does diagnostics change native held-out quality versus raw feedback?",
            "source": "public/results.json and public/chart-data.json, reconciled against native judges",
            "unit": "one held-out task; one execution per condition",
            "metric": "100 * native earned weight / native possible weight",
            "missingness": "missing points are labelled unavailable; never plotted as zero",
            "aggregation": "six equally weighted diagnostics-minus-raw paired differences",
            "scope": "six selected jobs; no repeatability or population claim",
            "encoding": "grey triangle original; blue square raw feedback; orange circle diagnostics",
            "text_alternative": "comparison.csv and the adjacent six-job Report table",
        },
    )
    if any(r["paired"] for r in data["comparison"]):
        command(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--env",
                "MPLCONFIGDIR=/tmp/harness2-plot",
                "--entrypoint",
                "python",
                "--mount",
                f"type=bind,src={output.resolve()},dst=/output",
                "--mount",
                f"type=bind,src={ROOT / 'showcase/render_results.py'},dst=/render.py,readonly",
                runtime.manifest["image"],
                "/render.py",
                "/output/chart-data.json",
                "/output/paired-scores",
            ]
        )


def report(runtime, *, publish=False):
    return base_report(
        runtime,
        publish=publish,
        formatters={
            "dataset": dataset,
            "markdown": markdown,
            "narrative": narrative,
            "prepare_output": prepare_output,
        },
    )
