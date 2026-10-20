"""Build a portable evidence browser and publish allowlisted records to W&B."""

from __future__ import annotations

import csv
import difflib
import hashlib
import json
import os
import shutil
import statistics
from collections import defaultdict
from pathlib import Path

import httpx

from .runtime import ROOT, fingerprint


def ratio(row):
    score = row.get("score")
    if score is None or not score.get("n_criteria"):
        return None
    return score["n_passed"] / score["n_criteria"]


def reconcile(rows):
    """One value per task occurrence; native numerator and denominator remain available."""
    keys = set()
    for row in rows:
        key = (row["arm"], row["task"], row["cohort"], row["repetition"])
        if key in keys:
            raise ValueError(f"Duplicate scored occurrence: {key}")
        keys.add(key)
        evidence = row.get("judge_evidence")
        if evidence and row.get("score"):
            original = json.loads(Path(evidence).read_text())
            if (
                original.get("total_score") != row["score"]["n_passed"]
                or original.get("max_score") != row["score"]["n_criteria"]
            ):
                raise ValueError("Published value differs from original judge evidence")
        row["fraction"] = ratio(row)
        row["earned_weight"] = (row.get("score") or {}).get("n_passed")
        row["possible_weight"] = (row.get("score") or {}).get("n_criteria")
    baselines = {
        (r["task"], r["cohort"], r["repetition"]): r for r in rows if r["arm"] == "baseline"
    }
    for row in rows:
        baseline = baselines.get((row["task"], row["cohort"], row["repetition"]))
        row["paired_change"] = (
            row["fraction"] - baseline["fraction"]
            if baseline and baseline["fraction"] is not None and row["fraction"] is not None
            else None
        )
    return rows


def allowed_files(runtime):
    state = runtime.state
    exact = (
        "manifest.json",
        "source-provenance.json",
        "qualification.json",
        "linux-judge-qualification.json",
        "allocation.json",
        "runtime-provenance.json",
        "isolation.json",
        "adapt-complete.json",
        "evaluate-complete.json",
        "stopped.json",
        "setup-repair.json",
        "phase-provenance.json",
        "validation.json",
        "cost-ledger.json",
    )
    for name in exact:
        if (state / name).is_file():
            yield state / name
    if runtime.manifest.get("schema_version", 1) >= 2:
        for name in (
            "tool-qualification.json",
            "qualification-attempt.json",
            "cost-projection.json",
            "evaluation-schedule.json",
            "transfer-executions-complete.json",
            "mechanism-selection.json",
            "article-gate.json",
        ):
            if (state / name).is_file():
                yield state / name
        for area in (
            "qualification",
            "qualification-frozen",
            "qualification-attempts",
            "mechanism",
            "provenance",
            "presentation",
        ):
            for path in (state / area).rglob("*"):
                if path.is_file() and not path.is_symlink():
                    yield path
        for path in (state / "qualification-tools").rglob("receipt.json"):
            if not path.is_symlink():
                yield path
        for directory in (state / "executions").glob("*"):
            for name in (
                "entry-message.txt",
                "runtime-settings.json",
                "file-inventory.json",
                "harness-record.json",
                "mission.md",
                "decision.json",
            ):
                if (directory / name).is_file():
                    yield directory / name
            for area in ("source-harness", "proposed-harness", "proposal-output", "diagnostics"):
                for path in (directory / area).rglob("*"):
                    if path.is_file() and not path.is_symlink():
                        yield path
    for area in ("scores", "frozen", "arms", "setup-failures", "attempt-history"):
        for path in (state / area).rglob("*"):
            if path.is_file() and not path.is_symlink():
                yield path
    for directory in (state / "executions").glob("*"):
        for name in (
            "identity.json",
            "execution.json",
            "trace.json",
            "stored-spans.json",
            "events.jsonl",
            "opencode-audit.json",
            "stderr.txt",
            "judge.log",
        ):
            if (directory / name).is_file():
                yield directory / name
    for path in (state / "gateway/sessions").glob("*/*.json"):
        yield path


def bundle(runtime, destination):
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    secrets = [os.environ.get("WANDB_API_KEY", ""), runtime.admin]
    index = []
    for source in sorted(set(allowed_files(runtime))):
        raw = source.read_bytes()
        if any(secret and secret.encode() in raw for secret in secrets):
            raise RuntimeError(f"Credential detected in allowlisted export: {source.name}")
        relative = source.relative_to(runtime.state)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # Make local locators portable without retaining developer home-directory paths.
        if source.suffix.lower() in (
            ".json",
            ".jsonl",
            ".txt",
            ".md",
            ".log",
            ".diff",
            ".judge_failed",
        ):
            text = raw.decode("utf-8", errors="strict")
            text = text.replace(str(runtime.state) + "/", "evidence/").replace(
                str(ROOT), "<checkout>"
            )
            raw = text.encode()
        target.write_bytes(raw)
        index.append(
            {
                "path": str(relative),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        )
    (destination / "files.json").write_text(json.dumps(index, indent=2))
    return index


def index_generated_files(destination, index):
    """Include generated harness snapshots and diffs in the downloadable hash manifest."""
    known = {entry["path"] for entry in index}
    for path in sorted(destination.rglob("*")):
        relative = str(path.relative_to(destination))
        if path.is_file() and relative not in known and relative != "files.json":
            index.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "generated": True,
                }
            )
    (destination / "files.json").write_text(json.dumps(index, indent=2))


def dataset(runtime):
    from .audit import rendered_harnesses

    rows = reconcile(
        [json.loads(p.read_text()) for p in sorted((runtime.state / "scores").rglob("*.json"))]
    )
    sessions = []
    budget = runtime.budget()
    for path in sorted((runtime.state / "gateway/sessions").glob("*/identity.json")):
        identity = json.loads(path.read_text())
        sid = identity["session"]
        calls = [c for c in budget["calls"] if c["session"] == sid]
        execution = runtime.state / "executions" / sid
        receipt = execution / "trace.json"
        sessions.append(
            {
                **identity,
                "calls": len(calls),
                "cost_usd": sum(c["spent"] for c in calls) / 1e6
                if all(c["spent"] is not None for c in calls)
                else None,
                "trace": json.loads(receipt.read_text()) if receipt.exists() else None,
            }
        )
    cost = defaultdict(float)
    solvers = {
        (s.get("arm"), s.get("task"), s.get("run_id", "").replace("/", "__")): s
        for s in sessions
        if s.get("role") == "solver" and s.get("run_id")
    }
    scored = {row["session"]: row for row in rows}
    for session in sessions:
        if session["cost_usd"] is not None:
            cost[session["phase"]] += session["cost_usd"]
        execution = session if session.get("role") == "solver" else None
        if session.get("role") == "judge":
            execution = solvers.get(
                (session.get("arm"), session.get("task"), session.get("run_id"))
            )
        if execution:
            session["evaluated_session"] = execution["session"]
            session["harness_fingerprint"] = execution.get("harness_fingerprint")
            score = scored.get(execution["session"])
            if score:
                session.update(
                    score=score["fraction"],
                    cohort=score["cohort"],
                    repetition=score["repetition"],
                )
    planned = 3 * len(runtime.manifest["domains"]) * allocation_repetitions(runtime)
    heldout = [r for r in rows if r["cohort"] == "heldout"]
    summary = {
        "campaign": runtime.manifest["campaign"],
        "rows": rows,
        "sessions": sessions,
        "cost_by_phase": dict(cost),
        "spent_usd": budget["spent_usd"],
        "reserved_usd": budget["reserved_usd"],
        "unresolved_requests": budget["unresolved"],
        "heldout_planned": planned,
        "heldout_judged": sum(r["fraction"] is not None for r in heldout),
        "complete": (runtime.state / "evaluate-complete.json").exists(),
        "rendered_harnesses": rendered_harnesses(runtime),
        "unavailable_conditions": [
            json.loads(p.read_text())
            for p in sorted((runtime.state / "frozen").glob("*/*/unavailable.json"))
        ],
    }
    summary["task_means"] = []
    for task in sorted({r["task"] for r in heldout}):
        for arm in ("baseline", "harness2", "diagnostics"):
            group = [
                r["fraction"]
                for r in heldout
                if r["task"] == task and r["arm"] == arm and r["fraction"] is not None
            ]
            summary["task_means"].append(
                {
                    "task": task,
                    "arm": arm,
                    "mean": statistics.mean(group) if group else None,
                    "judged_repetitions": len(group),
                    "planned_repetitions": allocation_repetitions(runtime),
                }
            )
    return summary


def allocation_repetitions(runtime):
    path = runtime.state / "allocation.json"
    return (
        json.loads(path.read_text())["repetitions"]
        if path.exists()
        else runtime.manifest["repetitions"]
    )


def harness_index(runtime, destination):
    base = ROOT / "third_party/jb_runner/harness_baseline_oc"
    candidates = [
        base,
        *(runtime.state / "frozen").glob("*/*/harness"),
        *(runtime.state / "arms").glob("*/runs/*/*/harnesses/*"),
    ]
    index = {}
    for source in candidates:
        if not source.is_dir():
            continue
        digest = fingerprint(source)
        if digest in index:
            continue
        target = destination / "harnesses" / digest
        shutil.copytree(source, target / "harness", dirs_exist_ok=True)
        paths = {str(p.relative_to(base)) for p in base.rglob("*") if p.is_file()}
        paths |= {str(p.relative_to(source)) for p in source.rglob("*") if p.is_file()}
        diff = []
        for relative in sorted(paths):
            a, b = base / relative, source / relative
            diff.extend(
                difflib.unified_diff(
                    a.read_text().splitlines(True) if a.exists() else [],
                    b.read_text().splitlines(True) if b.exists() else [],
                    fromfile="baseline/" + relative,
                    tofile="execution/" + relative,
                )
            )
        (target / "harness.diff").write_text("".join(diff))
        index[digest] = "evidence/harnesses/" + digest + "/harness.diff"
    return index


def portable_rows(runtime, data, harnesses):
    def preview(value):
        text = value if isinstance(value, str) else json.dumps(value)
        return (
            text
            if len(text) <= 4000
            else text[:4000] + "\n[Preview truncated; download stored spans for the full value.]"
        )

    for condition in data["unavailable_conditions"]:
        condition["result"] = "evidence/" + str(
            Path(condition["result"]).relative_to(runtime.state)
        )
    for row in data["rows"]:
        row["same_rendered_harness_as_baseline"] = any(
            harness["arm"] == row["arm"]
            and harness["domain"] == row["task"].split("/")[0]
            and harness["source_fingerprint"] == row["harness_fingerprint"]
            and harness["same_rendered_harness_as_baseline"]
            for harness in data.get("rendered_harnesses", [])
        )
        row["output_path"] = "evidence/" + str(Path(row["output_dir"]).relative_to(runtime.state))
        row.pop("output_dir")
        if row.get("judge_evidence"):
            row["judge_evidence"] = "evidence/" + str(
                Path(row["judge_evidence"]).relative_to(runtime.state)
            )
        row["harness_diff"] = harnesses[row["harness_fingerprint"]]
        directory = runtime.state / "executions" / row["session"]
        span_path = directory / "stored-spans.json"
        spans = json.loads(span_path.read_text()) if span_path.exists() else []
        row["operations"] = [
            {
                "id": s["span_id"],
                "name": s["span_name"],
                "status": s["status_code"],
                "message": s.get("status_message", ""),
                "start": s["started_at"],
                "end": s["ended_at"],
                "tool_arguments": preview(s.get("tool_call_arguments", "")),
                "result": preview(s.get("tool_call_result", "")),
            }
            for s in spans
            if s.get("tool_name") or s.get("error_type")
        ]
        source = runtime.state / row["output_path"].removeprefix("evidence/")
        row["outputs"] = [
            {
                "name": str(p.relative_to(source)),
                "path": row["output_path"] + "/" + str(p.relative_to(source)),
            }
            for p in sorted(source.rglob("*"))
            if p.is_file()
        ]
        diff = runtime.state / "public" / row["harness_diff"]
        row["diff_text"] = diff.read_text() if diff.exists() else "No frozen harness available."
    return data


def markdown(data, *, artifact_url="", demo_url="https://ash0ts.github.io/harness2-wandb/"):
    lines = [
        "# Can traces help an agent improve its harness?",
        "",
        "A JobBench-based pilot with a frozen-harness transfer test. Two held-out tasks do not support a broad benchmark ranking.",
        "",
        f"**Coverage:** {data['heldout_judged']}/{data['heldout_planned']} planned held-out scores. "
        f"**Recorded inference cost:** ${data['spent_usd']:.4f}. "
        f"**Unresolved reservations:** ${data['reserved_usd']:.4f}.",
        "",
        "**Pilot finding:** no evidence here that trace diagnostics improved the execution harness. "
        "The diagnostics condition froze the same rendered harness as baseline. Standard Harness² "
        "had a small adaptation gain and lower scores on its available transfer task. "
        "Failed adaptation and one unavailable judgment limit coverage.",
        "",
        "Baseline uses the unmodified upstream harness. Harness² runs Propose–Probe–Compose. "
        "The diagnostics condition adds deterministic observations read from stored Agent Lens spans. "
        "Upstream Harness² already provides trajectories to its improver.",
        "",
        "[Reproducible code](https://github.com/ash0ts/harness2-wandb) · "
        f"[Evidence browser]({demo_url}) · "
        f"[Captioned walkthrough]({demo_url}media/walkthrough.mp4) · "
        "[Download all evidence](https://github.com/ash0ts/harness2-wandb/releases/tag/pilot-v1)"
        + (f" · [Downloadable evidence artifact]({artifact_url})" if artifact_url else ""),
        "",
        "## Held-out task scores",
        "",
        "| Task | Condition | Mean score | Repetitions scored |",
        "|---|---|---:|---:|",
    ]
    for row in data["task_means"]:
        value = f"{row['mean'] * 100:.2f}%" if row["mean"] is not None else "Unavailable"
        lines.append(
            f"| {row['task']} | {row['arm']} | {value} | {row['judged_repetitions']}/{row['planned_repetitions']} |"
        )
    for condition in data["unavailable_conditions"]:
        lines += [
            "",
            f"**Unavailable transfer condition: {condition['arm']} / {condition['domain']}.** "
            + condition["reason"]
            + " It is excluded from score means and paired changes, and remains in planned coverage.",
        ]
    for harness in data.get("rendered_harnesses", []):
        if harness["arm"] != "baseline" and harness["same_rendered_harness_as_baseline"]:
            lines += [
                "",
                f"**No executable harness change: {harness['arm']} / {harness['domain']}.** "
                "Its frozen source diff changes metadata or placeholder documentation, but the "
                "OpenCode renderer produces exactly the same files as the baseline. Any score "
                "difference for this condition cannot be attributed to a changed execution harness. "
                "Source and rendered fingerprints are included in the validation artifact.",
            ]
    lines += [
        "",
        "Means average repetitions of the same task. Native earned and possible rubric weights appear below. "
        "A completed execution with no deliverables receives the native zero score. A failed judgment "
        "stays unavailable, even when deliverables exist. Four planned transfer executions could not run "
        "because no task-2 harness was adopted; they remain in planned coverage.",
        "",
        "## Every held-out occurrence",
        "",
        "| Task | Condition | Rep | Native score | Change from baseline | Trace |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in data["rows"]:
        if row["cohort"] != "heldout":
            continue
        score = row["score"]
        native = f"{score['n_passed']:g}/{score['n_criteria']:g}" if score else "Unavailable"
        change = (
            f"{row['paired_change'] * 100:+.2f} pp"
            if row["paired_change"] is not None
            else "Unavailable"
        )
        lines.append(
            f"| {row['task']} | {row['arm']} | {row['repetition']} | {native} | {change} | [Inspect]({row['trace']['url']}) |"
        )
    lines += [
        "",
        "## Inspect a gain and a regression",
        "",
        "These examples select the largest positive and negative paired changes across "
        "the recorded adaptation and transfer attempts. Their stage is explicit; an "
        "adaptation gain is not evidence of transfer. The full occurrence table remains downloadable.",
    ]
    pairs = [
        row for row in data["rows"] if row["arm"] != "baseline" and row["paired_change"] is not None
    ]
    for label, candidates in (
        (
            "Gain",
            sorted((r for r in pairs if r["paired_change"] > 0), key=lambda r: -r["paired_change"]),
        ),
        (
            "Regression",
            sorted((r for r in pairs if r["paired_change"] < 0), key=lambda r: r["paired_change"]),
        ),
    ):
        if not candidates:
            lines += ["", f"No {label.lower()} was recorded among available matched pairs."]
            continue
        row = candidates[0]
        score = row["score"]
        lines += [
            "",
            f"**{label}: {row['arm']} / {row['task']} / {row['cohort']} / repetition {row['repetition']}.** "
            f"Native score {score['n_passed']:g}/{score['n_criteria']:g}; "
            f"change {row['paired_change'] * 100:+.2f} percentage points.",
            f"[Score, diff, and deliverables]({demo_url}#{row['session']}) · "
            f"[Harness diff]({demo_url}{row['harness_diff']}) · "
            f"[Agent Lens trace]({row['trace']['url']})",
        ]
        if row.get("same_rendered_harness_as_baseline"):
            lines += [
                "This condition loads the same executable harness as baseline. Its score "
                "difference is not evidence of an improved harness.",
            ]
    lines += [
        "",
        "## Recorded walkthrough",
        "",
        f"[Watch the 75-second captioned video]({demo_url}media/walkthrough.mp4) · "
        f"[Read the transcript]({demo_url}media/transcript.md)",
        "This silent walkthrough is an edited sequence of recorded browser views. "
        "It follows a small adaptation gain and a transfer regression for the same Harness² harness. "
        "Captures and stored span records remain accessible without signing into Agent Lens.",
        f"![Adaptation score, harness diff, and generated files]({demo_url}media/adaptation-gain-final.png)",
        f"![Recorded Agent Lens tool failure with supporting span ID]({demo_url}media/agent-lens-gain-error.png)",
    ]
    lines += ["", "## Costs", "", "| Phase | USD |", "|---|---:|"]
    for phase, cost in data["cost_by_phase"].items():
        lines.append(f"| {phase} | ${cost:.4f} |")
    lines += [
        "",
        "Adaptation includes baseline task executions, proposals, probes, composition, reflection, and retries. Judging is separate. Costs use recorded provider usage and frozen prices; they are not a billing invoice.",
        "Agent Lens may show a lower cost: stored span cost fields in this pilot assigned zero to cached input. This report charges cached input at the frozen provider price and uses that ledger for the budget.",
        "",
        "## Limits",
        "",
        "The two domains each have one held-out task and one adapted harness per condition. Repetitions describe execution variability, not additional independent tasks. Model IDs do not pin provider weights. Scores can vary with judging. "
        "Trace patterns describe observed operations; a score difference alone does not prove the mechanism.",
        "",
        "Some post-execution collectors returned 120 after their output pipe became nonblocking. "
        "The primary tool stream was retained and reconciled against completed model tool-call IDs; "
        "those wrapper codes are not evidence of agent failure. See the validation receipt and pilot notes.",
        "",
        "Automatic Agent Lens Insights clustering is outside this pilot. The portable evidence browser includes stored span excerpts and downloadable records when Agent Lens requires a login.",
        "Implementation repairs and trace-coverage limits are documented in [pilot notes](https://github.com/ash0ts/harness2-wandb/blob/showcase/wandb-agent-lens/showcase/pilot-notes.md). No benchmark grades were available while adaptation ran.",
    ]
    if not data["complete"]:
        lines += [
            "",
            "**Campaign incomplete.** This page preserves available results and must not be presented as a completed comparison.",
        ]
    return "\n".join(lines) + "\n"


def make_public(runtime, api):
    """Publish only a project containing this campaign's recorded data."""
    entity, project = runtime.manifest["project"].split("/")
    # An already-public destination needs no visibility mutation. This also lets
    # later campaigns coexist with preserved public results in the same project.
    response = httpx.post(
        "https://api.wandb.ai/graphql",
        json={
            "query": "query($name:String!,$entity:String!){project(name:$name,entityName:$entity){name access}}",
            "variables": {"name": project, "entity": entity},
        },
        timeout=60,
    )
    response.raise_for_status()
    if (response.json().get("data", {}).get("project") or {}).get("access") == "USER_READ":
        return
    for run in api.runs(runtime.manifest["project"]):
        if run.config.get("campaign") != runtime.manifest["campaign"]:
            raise RuntimeError(
                "Project contains another campaign; refusing to change its visibility"
            )
    known = {p.name for p in (runtime.state / "gateway/sessions").iterdir() if p.is_dir()}
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
        rows = body.get("spans", [])
        if any(row.get("conversation_id") not in known for row in rows):
            raise RuntimeError("Project contains unknown traces; refusing to change its visibility")
        offset += len(rows)
        if offset >= body.get("total_count", 0) or not rows:
            break
    response = httpx.post(
        "https://api.wandb.ai/graphql",
        auth=("api", os.environ["WANDB_API_KEY"]),
        json={
            "query": "mutation($input: UpsertModelInput!){upsertModel(input:$input){project{name access}}}",
            "variables": {"input": {"name": project, "entityName": entity, "access": "USER_READ"}},
        },
        timeout=60,
    )
    response.raise_for_status()
    result = response.json()
    if (
        result.get("errors")
        or result.get("data", {}).get("upsertModel", {}).get("project", {}).get("access")
        != "USER_READ"
    ):
        raise RuntimeError("Public project visibility was not confirmed: " + json.dumps(result))
    # Verify the same metadata without credentials, cookies, or the SDK's auth session.
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
        raise RuntimeError("Anonymous public project readback failed")


def publish_results(runtime, data, output):
    import wandb
    import wandb_workspaces.reports.v2 as wr

    entity, project = runtime.manifest["project"].split("/")
    receipt_path = runtime.state / "publication.json"
    previous = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    with wandb.init(
        entity=entity,
        project=project,
        id=runtime.manifest["campaign"] + "-summary",
        resume="allow",
        job_type="campaign",
        config={"campaign": runtime.manifest["campaign"], "manifest": runtime.manifest},
        settings=wandb.Settings(code_dir=None, disable_git=True),
    ) as run:
        evidence = wandb.Artifact(
            runtime.manifest["campaign"] + "-evidence",
            type="evidence",
            metadata={"source": "public JobBench pilot", "complete": data["complete"]},
        )
        evidence.add_dir(str(output / "evidence"))
        evidence.add_file(str(output / "results.json"))
        evidence.add_file(str(output / "results.csv"))
        evidence = run.log_artifact(evidence)
        evidence.wait()
        artifact_url = evidence.url
        for path in (runtime.state / "frozen").glob("*/*/freeze.json"):
            frozen = json.loads(path.read_text())
            artifact = wandb.Artifact(
                "harness-" + frozen["fingerprint"][:16], type="harness", metadata=frozen
            )
            artifact.add_dir(str(path.parent))
            run.log_artifact(artifact)
        columns = [
            "session",
            "arm",
            "task",
            "phase",
            "role",
            "stage",
            "run_id",
            "harness_fingerprint",
            "input_fingerprint",
            "evaluated_session",
            "score",
            "cohort",
            "repetition",
            "cost_usd",
            "trace",
        ]
        table = wandb.Table(columns=columns)
        for session in data["sessions"]:
            table.add_data(
                *[
                    session.get(c) if c != "trace" else (session.get("trace") or {}).get("url")
                    for c in columns
                ]
            )
        run.log({"executions": table})
        run.summary.update(
            {
                "spent_usd": data["spent_usd"],
                "heldout_judged": data["heldout_judged"],
                "heldout_planned": data["heldout_planned"],
                "complete": data["complete"],
            }
        )
        campaign_url = run.url
    for row in data["rows"]:
        with wandb.init(
            entity=entity,
            project=project,
            id=row["session"][:12],
            resume="allow",
            job_type=row["cohort"],
            name=f"{row['arm']} / {row['task']} / {row['repetition']}",
            config={
                "campaign": runtime.manifest["campaign"],
                "condition": row["arm"],
                "task": row["task"],
                "repetition": row["repetition"],
                "cohort": row["cohort"],
                "harness_fingerprint": row["harness_fingerprint"],
            },
            settings=wandb.Settings(disable_git=True),
        ) as run:
            run.summary.update(
                {
                    "score": row["fraction"],
                    "paired_change": row["paired_change"],
                    "trace_url": row["trace"]["url"],
                    "evidence_artifact": artifact_url,
                    "status": row["status"],
                }
            )
            if row["score"]:
                run.summary.update(row["score"])
            run.use_artifact(evidence)
    make_public(runtime, wandb.Api())
    text = markdown(data, artifact_url=artifact_url)
    report = wr.Report(
        project=project,
        entity=entity,
        title="Harness² + Agent Lens: frozen-harness pilot",
        description="Reproducible scores, harness diffs, traces, outputs, costs, and negative results.",
        blocks=[wr.MarkdownBlock(text=text)],
    )
    if previous.get("report_id"):
        report.id = previous["report_id"]
    report.save()
    receipt = {
        "report_url": f"https://wandb.ai/{entity}/{project}/reports/Harness-Agent-Lens-frozen-harness-pilot--{report.id}",
        "sdk_report_url": report.url,
        "report_id": report.id,
        "artifact_url": artifact_url,
        "campaign_url": campaign_url,
    }
    receipt_path.write_text(json.dumps(receipt, indent=2))
    (output / "publication.json").write_text(json.dumps(receipt, indent=2))
    (output / "report.md").write_text(text)
    return receipt


def report(runtime, publish=False):
    data = dataset(runtime)
    if publish and (not data["complete"] or data["unresolved_requests"]):
        raise RuntimeError(
            "Finish or explicitly document the stopped campaign before public upload"
        )
    if publish:
        from .audit import audit

        audit(runtime)
    output = runtime.state / "public"
    output.mkdir(parents=True, exist_ok=True)
    index = bundle(runtime, output / "evidence")
    harnesses = harness_index(runtime, output / "evidence")
    index_generated_files(output / "evidence", index)
    data = portable_rows(runtime, data, harnesses)
    (output / "results.json").write_text(json.dumps(data, indent=2))
    with (output / "results.csv").open("w") as stream:
        fields = [
            "arm",
            "task",
            "cohort",
            "repetition",
            "earned_weight",
            "possible_weight",
            "fraction",
            "paired_change",
            "status",
            "session",
            "harness_fingerprint",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data["rows"])
    (output / "report.md").write_text(markdown(data))
    template = (ROOT / "showcase/report.html").read_text()
    encoded = json.dumps(data).replace("<", "\\u003c").replace("&", "\\u0026")
    (output / "index.html").write_text(template.replace("__RESULTS_JSON__", encoded))
    if publish:
        receipt = publish_results(runtime, data, output)
        print(json.dumps(receipt, indent=2))
    print(f"Evidence browser: {output / 'index.html'}")
    return output
