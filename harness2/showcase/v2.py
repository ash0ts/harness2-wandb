"""Sequential JobBench pilot with paired starting evidence and an explicit article gate."""

from __future__ import annotations

import difflib
import hashlib
import json
import random
import re
import shutil
import sys
import threading
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from harness2.core.library import Library
from harness2.core.proposer import Proposer
from harness2.core.schedule import Namespace, run_task
from harness2.substrate.opencode import render_events

from .cli import ARMS, setup, verify_fixtures
from .data import prepare
from .experiment import ExperimentAdapter, ExperimentProposer, session_cost
from .protocol import (
    changed_files,
    diagnostic_reads,
    harness_record,
    inventory,
    materialize_evidence,
    tree_hashes,
    validate_completion,
    write_json,
)
from .runtime import ROOT, CampaignStop, command, fingerprint
from .telemetry import export_session, tool_events

_SHARED_LOCK = threading.Lock()


def bounded_parallel(jobs, operation, width):
    """Do not launch more work after a stop; retain already-started attempts."""
    iterator = iter(jobs)
    with ThreadPoolExecutor(max_workers=width) as pool:
        pending = set()
        for _ in range(width):
            job = next(iterator, None)
            if job is not None:
                pending.add(pool.submit(operation, job))
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                future.result()
            for _ in done:
                job = next(iterator, None)
                if job is not None:
                    pending.add(pool.submit(operation, job))


def read(path):
    return json.loads(Path(path).read_text())


class V2Adapter(ExperimentAdapter):
    def __init__(self, runtime, arm, replicate, *, qualification=False):
        super().__init__(runtime, arm)
        self.replicate, self.qualification = replicate, qualification
        self.root = (
            runtime.state / ("qualification" if qualification else "arms") / arm / f"rep{replicate}"
        )
        self.store = self.root / "dataset"
        self.phase = "smoke" if qualification else "adapt"

    def execute(self, task_id, harness_dir, run_id, *, timeout=None):
        base = re.fullmatch(r"base([12])", run_id.split("/")[-1])
        if base and self.arm != "baseline":
            # Upstream asks each arm for k baselines. Reuse exactly the same completed
            # executions, copying only their public output and raw trace, never a library.
            source = V2Adapter(
                self.runtime, "baseline", self.replicate, qualification=self.qualification
            )
            with _SHARED_LOCK:
                shared = source.execute(task_id, None, f"shared/base{base[1]}")
                label = run_id.replace("/", "__")
                dest, trajectory = self._out_dir(task_id, label), self._traj_dir(task_id, label)
                if not dest.exists():
                    shutil.copytree(shared, dest)
                    shutil.copytree(source._traj_dir(task_id, shared.name), trajectory)
                    marker = read(trajectory / "showcase.json")
                    marker.update(
                        shared_baseline=True, arm=self.arm, run_id=run_id, output_dir=str(dest)
                    )
                    write_json(trajectory / "showcase.json", marker)
                if not self.artifacts_intact(dest):
                    raise CampaignStop("Shared starting evidence changed")
                return dest
        return super().execute(task_id, harness_dir, run_id, timeout=timeout)

    def trajectory(self, run_dir, *, obs_trunc):
        task_id, label = self._task_and_label(run_dir)
        directory = self._traj_dir(task_id, label)
        marker = read(directory / "showcase.json")
        # Full events and requests are copied to context/evidence by V2Proposer.
        # This readable trajectory is common to both arms, with no diagnostics appended.
        original = render_events(
            directory / "events.jsonl", obs_trunc=obs_trunc, max_chars=2_000_000
        )
        return (
            f"SHOWCASE_SESSION:{marker['session']}\nFull raw records: context/evidence/{marker['session']}/\n\n"
            + original
        )


class V2Proposer(ExperimentProposer):
    def call(self, mission, context_dir, label, **kwargs):
        self.local.label = label
        self.local.editing = kwargs.get("harvest") == "context/harness"
        self.local.attempt = 0
        self.local.rejection = ""
        return Proposer.call(self, mission, context_dir, label, **kwargs)

    def _opencode(self, cwd, mission, system, wall):
        self.local.attempt += 1
        editing = self.local.editing
        before = tree_hashes(cwd / "context/harness")
        old = (
            harness_record(
                cwd / "context/harness", self.adapter.domain_of(self.task_id), self.runtime.manifest
            )
            if editing
            else None
        )
        sessions = materialize_evidence(
            cwd,
            self.runtime,
            diagnostics=self.adapter.arm == "diagnostics",
            task=self.task_id,
            arm=self.adapter.arm,
            replicate=self.adapter.replicate,
        )
        instructions = (
            "\n\n# Completion contract\n"
            "Work from the indexed evidence files. Full raw requests, events, and stored spans "
            "are in context/evidence/INDEX.json. Read bounded slices with read/grep or scripts; "
            "do not dump whole long trajectories into one tool response.\n"
            "Finish the requested work, save the requested response verbatim in output/answer.md, "
            "and save output/completion.json LAST. The JSON must include status='complete', "
            "a substantive rationale, and cited_span_ids (stored 16-character span IDs supporting "
            "your decision, or [] when none). Do not claim completion while work remains.\n"
        )
        if editing:
            instructions += (
                "The completion JSON must also declare decision='change' or 'keep' and affected_files, "
                "the exact list of changed paths relative to context/harness/. If retaining the "
                "harness, use keep and []. A justified keep decision is valid. Metadata-only changes "
                "are recorded as no executable change. Make changes only when justified by evidence.\n"
            )
        if self.adapter.arm == "diagnostics":
            instructions += "Additional deterministic Agent Lens summaries are indexed in context/diagnostics/INDEX.json.\n"
        if self.local.rejection:
            instructions += (
                "\nThe previous attempt did not satisfy the saved completion contract: "
                + self.local.rejection
                + ". Correct this in the new workspace before saving the receipt.\n"
            )
        entry = (system + "\n\n" if system else "") + mission + instructions
        (cwd / "MISSION.md").write_text(entry)
        (cwd / "output").mkdir(exist_ok=True)
        phase = self.adapter.phase
        if phase != "smoke" and self.local.attempt > 1:
            phase = "retry"
        result, directory = self.runtime.run_agent(
            cwd,
            entry,
            entry_message=entry,
            phase=phase,
            role="reflection" if self.local.label.startswith("experience.") else "improver",
            task=self.task_id,
            arm=self.adapter.arm,
            adaptation_replicate=self.adapter.replicate,
            stage=self.local.label,
            proposal_attempt=self.local.attempt,
            input_fingerprint=fingerprint(cwd),
            harness_fingerprint=fingerprint(cwd / "context/harness"),
        )
        # Preserve decisions and evidence before upstream removes its temporary workspace.
        shutil.copytree(cwd / "output", directory / "proposal-output")
        if editing:
            shutil.copytree(cwd / "context/harness", directory / "proposed-harness")
        if (cwd / "context/diagnostics").exists():
            shutil.copytree(cwd / "context/diagnostics", directory / "diagnostics")
        (directory / "mission.md").write_text(entry)
        try:
            export_session(self.runtime, result["session"], result, directory)
        except Exception as exc:
            raise CampaignStop(f"Trace persistence failed for {result['session']}: {exc}") from exc
        cost = session_cost(self.runtime, result["session"])
        if result.get("budget_blocked"):
            raise CampaignStop(
                f"Budget denied proposal requests in {result['session']}; stop the campaign"
            )
        evidence = {
            "session": result["session"],
            "stage": self.local.label,
            "editing": editing,
            "source_sessions": sessions,
            "diagnostic_reads": diagnostic_reads(directory / "events.jsonl"),
            "accepted": False,
            "before": old,
        }
        if editing:
            after = harness_record(
                cwd / "context/harness", self.adapter.domain_of(self.task_id), self.runtime.manifest
            )
            evidence.update(
                after=after,
                executable_change=old["rendered_fingerprint"] != after["rendered_fingerprint"],
                changed_executable_files=changed_files(
                    old["rendered_files"], after["rendered_files"]
                ),
            )
        try:
            receipt = validate_completion(
                cwd, before, result, directory / "events.jsonl", editing=editing
            )
            available = {
                r["span_id"]
                for session in sessions
                for r in read(self.runtime.state / "executions" / session / "stored-spans.json")
            }
            if not set(receipt.get("cited_span_ids", [])) <= available:
                raise ValueError("Receipt cites spans absent from this stage's evidence")
            evidence.update(accepted=True, receipt=receipt)
        except (ValueError, OSError) as exc:
            evidence["rejection"] = str(exc)
            self.local.rejection = str(exc)
            write_json(directory / "decision.json", evidence)
            return "", cost, str(exc)
        write_json(directory / "decision.json", evidence)
        return (cwd / "output/answer.md").read_text(), cost, ""


def freeze(runtime, adapter, domain, harness, result, chosen_label):
    dest = (
        runtime.state
        / ("qualification-frozen" if adapter.qualification else "frozen")
        / adapter.arm
        / f"rep{adapter.replicate}"
        / domain
    )
    current = harness_record(harness, domain, runtime.manifest)
    if (dest / "freeze.json").exists():
        if read(dest / "freeze.json")["source_fingerprint"] != current["source_fingerprint"]:
            raise CampaignStop("Frozen adoption changed")
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(harness, dest / "harness")
    base = adapter.base_harness()
    original = harness_record(base, domain, runtime.manifest)
    lines = []
    for rel in sorted(tree_hashes(base).keys() | tree_hashes(harness).keys()):
        a, b = base / rel, Path(harness) / rel
        lines.extend(
            difflib.unified_diff(
                a.read_text().splitlines(True) if a.exists() else [],
                b.read_text().splitlines(True) if b.exists() else [],
                fromfile="baseline/" + rel,
                tofile="adopted/" + rel,
            )
        )
    (dest / "harness.diff").write_text("".join(lines))
    stages = read(result).get("modes", {}).get("seq", {}).get("stages", []) if result else []
    fallback_stages = [stage["label"] for stage in stages if stage["fell_back"]]
    write_json(
        dest / "freeze.json",
        {
            **current,
            "fingerprint": current["source_fingerprint"],
            "arm": adapter.arm,
            "domain": domain,
            "adaptation_replicate": adapter.replicate,
            "chosen_label": chosen_label,
            "adoption": "baseline"
            if adapter.arm == "baseline"
            else "selected"
            if chosen_label
            else "baseline_fallback",
            "adaptation_complete": bool(chosen_label and len(stages) == 6 and not fallback_stages)
            if adapter.arm != "baseline"
            else None,
            "valid_stage_decisions": sum(not stage["fell_back"] for stage in stages),
            "fallback_stages": fallback_stages,
            "after_task": "task1",
            "result": result,
            "baseline_rendered_fingerprint": original["rendered_fingerprint"],
            "executable_change": current["rendered_fingerprint"]
            != original["rendered_fingerprint"],
        },
    )
    return dest


def adapt_one(runtime, domain, arm, replicate, *, qualification=False):
    adapter = V2Adapter(runtime, arm, replicate, qualification=qualification)
    task = domain + "/task1"
    result_path = adapter.root / "results" / (task.replace("/", "__") + ".json")
    if arm == "baseline":
        for i in (1, 2):
            adapter.execute(task, None, f"shared/base{i}")
        return freeze(runtime, adapter, domain, adapter.base_harness(), None, None)
    if result_path.exists():
        result = read(result_path)
    else:
        library = Library(adapter.root / "libraries", domain, adapter.substrate.surface())
        prop = V2Proposer(runtime, adapter, task, adapter.root / "audit" / task)
        result = run_task(
            task,
            2,
            adapter=adapter,
            prop=prop,
            selector_prop=prop,
            runs_root=adapter.root / "runs",
            modes=("seq",),
            seed=runtime.manifest["seed"],
            tag=arm,
            libraries={"seq": library},
        ).to_dict()
        write_json(result_path, result)
    selection = result.get("selections", {}).get("seq", {})
    chosen_label = selection.get("chosen_label") if selection.get("ok") else None
    chosen = adapter.base_harness()
    if chosen_label:
        ns = Namespace("jb", "oc", "seq", 2, runtime.manifest["seed"], arm)
        chosen = ns.task_dir(adapter.root / "runs", task) / "harnesses" / chosen_label
    # A failed adaptation still participates in transfer with the last valid harness.
    # This campaign has one adaptation task, so absence of adoption falls back to baseline.
    return freeze(runtime, adapter, domain, chosen, str(result_path), chosen_label)


def provenance(runtime, phase):
    source = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in (ROOT / "harness2/showcase", ROOT / "showcase")
        for p in folder.glob("*")
        if p.is_file() and p.suffix in (".py", ".json")
    }
    value = {
        "phase": phase,
        "commit": command(["git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip(),
        "source_hashes": source,
        "images": {
            name: json.loads(command(["docker", "image", "inspect", name]).stdout)[0]["Id"]
            for name in (runtime.manifest["image"], runtime.manifest["image"] + "-judge")
        },
    }
    path = runtime.state / "provenance" / f"{phase}.json"
    qualified = runtime.state / "provenance/qualification.json"
    if (
        phase != "qualification"
        and qualified.exists()
        and read(qualified)["images"] != value["images"]
    ):
        raise CampaignStop("Worker or judge image differs from the qualified environment")
    if phase in ("adapt", "evaluate", "mechanism"):
        engine = {
            key: sha
            for key, sha in source.items()
            if key.startswith("harness2/showcase/")
            and Path(key).name not in ("report.py", "v2_report.py", "audit.py", "mechanism.py")
        }
        engine_path = runtime.state / "provenance/frozen-engine.json"
        if engine_path.exists() and read(engine_path) != engine:
            raise CampaignStop("Execution engine changed after the main campaign froze")
        write_json(engine_path, engine)
    if path.exists() and read(path) != value:
        raise CampaignStop(
            "Source/image drift within a phase; preserve and start a distinct attempt"
        )
    write_json(path, value)
    snapshot = runtime.state / "provenance" / (phase + "-source")
    for relative in source:
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())


def qualify_tools(runtime):
    receipt = runtime.state / "tool-qualification.json"
    if receipt.exists():
        return read(receipt)
    work = runtime.state / "qualification-tools"
    work.mkdir(parents=True, exist_ok=True)
    (work / "input.txt").write_text("qualification: 2 3 5\n")
    skill = work / ".opencode/skills/qualification/SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        "---\nname: qualification\ndescription: Verify offline tool availability.\n---\nRead input.txt and sum its three integers.\n"
    )
    prompt = (
        "Infrastructure qualification only. Use the skill tool to load qualification. Use the glob tool "
        "to find input.txt, grep tool to search for qualification, and read tool to read it. Use the task tool "
        "to delegate verifying the sum to an explore subagent. Use bash to verify rg --version and import "
        "scipy, statsmodels, docx, openpyxl, reportlab, pypdf, pdfplumber, pptx. Write output/sum.txt "
        "containing 10. Finally check its contents. Internet access is unavailable."
    )
    result, directory = runtime.run_agent(
        work, prompt, phase="smoke", role="solver", arm="qualification", task="infrastructure-tools"
    )
    before = fingerprint(work)
    export_session(runtime, result["session"], result, directory)
    observed = {p["tool"] for p in tool_events(directory / "events.jsonl")}
    required = {"skill", "glob", "grep", "read", "bash", "task"}
    if (
        not required <= observed
        or not (work / "output/sum.txt").is_file()
        or (work / "output/sum.txt").read_text().strip() != "10"
    ):
        raise CampaignStop(f"Offline actual-tool qualification failed; observed {sorted(observed)}")
    if fingerprint(work) != before or result["child_exit_code"] != 0:
        raise CampaignStop("Observation or collection qualification failed")
    children = {
        r["session"]["id"]
        for r in read(directory / "opencode-audit.json")
        if r["type"] == "audit_session" and r["session"].get("parent_id")
    }
    if not any(t.get("sessionID") in children for t in tool_events(directory / "events.jsonl")):
        raise CampaignStop("Delegated tool collection did not qualify")
    value = {
        "passed": True,
        "session": result["session"],
        "tools": sorted(observed),
        "observation_preserves_workspace": True,
    }
    write_json(receipt, value)
    return value


def smoke(runtime):
    if (runtime.state / "qualification.json").exists():
        return read(runtime.state / "qualification.json")
    provenance(runtime, "qualification")
    qualify_tools(runtime)
    cases = []
    # Previously inspected domains only; no held-out task is used for development.
    for task in runtime.manifest["qualification_tasks"]:
        domain = task.split("/")[0]
        frozen = adapt_one(runtime, domain, "diagnostics", 1, qualification=True)
        receipt = read(frozen / "freeze.json")
        result = read(receipt["result"])
        stages = result.get("modes", {}).get("seq", {}).get("stages", [])
        if len(stages) != 6 or any(s["fell_back"] for s in stages) or not receipt["chosen_label"]:
            raise CampaignStop(f"Two-round refinement or adoption did not qualify for {task}")
        output = Path(result["selections"]["seq"]["chosen_run_dir"])
        files = inventory(output)
        if not files or any(f["readable"] is not True for f in files):
            raise CampaignStop(f"Qualification deliverables are not readable for {task}")
        adapter = V2Adapter(runtime, "diagnostics", 1, qualification=True)
        score = adapter.score(output, task)
        if not score or score.get("n_criteria", 0) <= 0:
            raise CampaignStop(f"Native judging did not qualify for {task}")
        cases.append(
            {
                "task": task,
                "freeze": str(frozen),
                "files": files,
                "score": score,
                "stages": len(stages),
            }
        )
    budget = runtime.budget()
    if budget["unresolved"]:
        raise CampaignStop("Qualification usage is unresolved")
    value = {
        "passed": True,
        "cases": cases,
        "spent_usd": budget["spent_usd"],
        "settings": {
            k: runtime.manifest[k]
            for k in (
                "models",
                "max_output_tokens",
                "max_steps",
                "model_context_tokens",
                "judge_reasoning_effort",
            )
        },
        "criterion": "Completed two-round refinement, saved decisions, readable output, native judging, verified spans; no score gain required.",
    }
    write_json(runtime.state / "qualification.json", value)
    return value


def allocation(runtime):
    path = runtime.state / "allocation.json"
    if path.exists():
        return read(path)
    if not read(runtime.state / "qualification.json")["passed"]:
        raise CampaignStop("Qualification has not passed")
    budget = runtime.budget()
    session_costs, roles = defaultdict(float), {}
    for call in budget["calls"]:
        if call["spent"] is None:
            raise CampaignStop("Cannot project with unresolved usage")
        identity = read(runtime.state / "gateway/sessions" / call["session"] / "identity.json")
        if identity["task"] == "infrastructure-tools":
            continue
        session_costs[call["session"]] += call["spent"] / 1e6
        roles[call["session"]] = identity["role"]
    maxima = {
        role: max((cost for sid, cost in session_costs.items() if roles[sid] == role), default=0)
        for role in ("solver", "improver", "reflection", "judge")
    }
    if not all(maxima.values()):
        raise CampaignStop("Missing qualified cost observations for a role")
    # Maximum observed real-task session cost, plus 50% contingency. Native judge
    # cost is normalized per rubric and scaled by the held-out rubric counts only.
    # Grading criteria and task instructions are never exposed by this projection.
    judge_per_rubric = []
    for case in read(runtime.state / "qualification.json")["cases"]:
        n = len(
            read(ROOT / "dependencies/job-bench-eval/dataset/main" / case["task"] / "RUBRICS.json")[
                "rubrics"
            ]
        )
        costs = sum(
            cost
            for sid, cost in session_costs.items()
            if roles[sid] == "judge"
            and read(runtime.state / "gateway/sessions" / sid / "identity.json")["task"]
            == case["task"]
        )
        judge_per_rubric.append(costs / n)
    # Download public fixtures without opening held-out contents in development.
    prepare(
        runtime.manifest,
        runtime.state,
        [f"{f['domain']}/{f['heldout']}" for f in runtime.manifest["task_families"]],
    )
    rubric_counts = {
        f["domain"]: len(
            read(
                ROOT
                / "dependencies/job-bench-eval/dataset/main"
                / f["domain"]
                / f["heldout"]
                / "RUBRICS.json"
            )["rubrics"]
        )
        for f in runtime.manifest["task_families"]
    }
    n = len(runtime.manifest["task_families"]) * runtime.manifest["adaptation_replicates"]
    # Keep costs specific to each stage. Applying the most expensive composer to
    # every probe, selector, and reflection miscounts the fixed upstream workflow.
    # Include retries in a stage's observed total, then take that stage's maximum
    # across the qualified real tasks and add a 50% campaign contingency.
    by_case = defaultdict(lambda: defaultdict(float))
    for sid, cost in session_costs.items():
        identity = read(runtime.state / "gateway/sessions" / sid / "identity.json")
        if identity["role"] == "judge":
            continue
        label = (
            identity.get("run_id", "").split("/")[-1]
            if identity["role"] == "solver"
            else re.sub(r"\.a\d+$", "", identity["stage"])
        )
        by_case[identity["task"]][identity["role"] + "/" + label] += cost
    stage_keys = set().union(*(case.keys() for case in by_case.values()))
    if any(set(case) != stage_keys for case in by_case.values()):
        raise CampaignStop(
            "Qualified tasks have different observed stage sets; reconcile before projecting"
        )
    stage_maxima = {key: max(case[key] for case in by_case.values()) for key in sorted(stage_keys)}
    shared = sum(cost for key, cost in stage_maxima.items() if key.startswith("solver/base"))
    independent = sum(stage_maxima.values()) - shared
    adapt = n * (shared + 2 * independent) * 1.5
    projections = []
    for reps in (runtime.manifest["repetitions"], 1):
        evaluations = n * 3 * reps
        main = adapt + evaluations * maxima["solver"] * 1.5
        judges = (
            sum(rubric_counts.values())
            * runtime.manifest["adaptation_replicates"]
            * 3
            * reps
            * max(judge_per_rubric)
            * 1.5
        )
        projections.append(
            {
                "repetitions": reps,
                "planned_transfer_executions": evaluations,
                "projected_adaptation_usd": adapt,
                "projected_main_usd": main,
                "projected_judging_usd": judges,
                "fits_main_bucket": main
                <= float(runtime.manifest["budget_buckets"]["main"]["usd"]),
                "fits_judging_bucket": judges
                <= float(runtime.manifest["budget_buckets"]["retry_and_judging"]["usd"]),
            }
        )
        write_json(
            runtime.state / "cost-projection.json",
            {
                "observed_max_stage_usd": stage_maxima,
                "observed_max_session_usd": maxima,
                "judge_rubric_counts": rubric_counts,
                "qualification_spend_usd": budget["spent_usd"],
                "safety_multiplier": 1.5,
                "candidates": projections,
            },
        )
        if (
            main <= float(runtime.manifest["budget_buckets"]["main"]["usd"])
            and judges <= float(runtime.manifest["budget_buckets"]["retry_and_judging"]["usd"])
            and main + judges + 5 + budget["spent_usd"] <= float(runtime.manifest["budget_usd"])
        ):
            value = {
                "repetitions": reps,
                "planned_transfer_executions": evaluations,
                "projected_main_usd": main,
                "projected_judging_usd": judges,
                "projected_adaptation_usd": adapt,
                "observed_max_session_usd": maxima,
                "observed_max_stage_usd": stage_maxima,
                "projection_method": "Per-stage maximum across qualified real tasks, including retries; shared baselines counted once; 50% contingency.",
                "judge_rubric_counts": rubric_counts,
                "safety_multiplier": 1.5,
                "independent_task_count": len(runtime.manifest["task_families"]),
                "remaining_usd": float(runtime.manifest["budget_usd"]) - budget["spent_usd"],
            }
            write_json(path, value)
            return value
    raise CampaignStop(
        "The complete comparison does not fit the qualified cost projection, even with one evaluation repetition"
    )


def schedule(runtime, reps):
    rng = random.Random(runtime.manifest["seed"])
    result = []
    for family in runtime.manifest["task_families"]:
        for replicate in range(1, runtime.manifest["adaptation_replicates"] + 1):
            for repetition in range(1, reps + 1):
                arms = list(ARMS)
                rng.shuffle(arms)
                for arm in arms:
                    result.append(
                        {
                            "arm": arm,
                            "domain": family["domain"],
                            "task": family["domain"] + "/" + family["heldout"],
                            "adaptation_replicate": replicate,
                            "repetition": repetition,
                        }
                    )
    return result


def adapt(runtime):
    decision = allocation(runtime)
    path = runtime.state / "evaluation-schedule.json"
    if not path.exists():
        write_json(path, schedule(runtime, decision["repetitions"]))
    prepare(
        runtime.manifest,
        runtime.state,
        [f"{f['domain']}/{f['adapt']}" for f in runtime.manifest["task_families"]],
    )
    verify_fixtures(runtime)
    provenance(runtime, "adapt")

    def group(job):
        family, replicate = job
        for arm in (
            "baseline",
            *(("harness2", "diagnostics") if replicate % 2 else ("diagnostics", "harness2")),
        ):
            adapt_one(runtime, family["domain"], arm, replicate)

    jobs = [
        (family, replicate)
        for family in runtime.manifest["task_families"]
        for replicate in range(1, runtime.manifest["adaptation_replicates"] + 1)
    ]
    bounded_parallel(jobs, group, 4)
    freezes = list((runtime.state / "frozen").glob("*/*/*/freeze.json"))
    if (
        len(freezes)
        != 3 * len(runtime.manifest["task_families"]) * runtime.manifest["adaptation_replicates"]
    ):
        raise CampaignStop("Incomplete freeze set")
    write_json(runtime.state / "adapt-complete.json", {"complete": True, "freezes": len(freezes)})


def evaluate(runtime):
    if not (runtime.state / "adapt-complete.json").exists():
        raise CampaignStop("Finish every adaptation and freeze before benchmark grading")
    provenance(runtime, "evaluate")
    verify_fixtures(runtime)
    jobs = read(runtime.state / "evaluation-schedule.json")
    # Complete every transfer execution before invoking any benchmark judge.
    outputs = []

    def execute_job(job):
        adapter = V2Adapter(runtime, job["arm"], job["adaptation_replicate"])
        adapter.phase, adapter.repetition = "evaluate", job["repetition"]
        frozen = (
            runtime.state
            / "frozen"
            / job["arm"]
            / f"rep{job['adaptation_replicate']}"
            / job["domain"]
        )
        info = read(frozen / "freeze.json")
        if fingerprint(frozen / "harness") != info["source_fingerprint"]:
            raise CampaignStop("Frozen source changed")
        out = adapter.execute(job["task"], frozen / "harness", f"evaluate/rep{job['repetition']}")
        marker = read(adapter._traj_dir(job["task"], out.name) / "showcase.json")
        if marker["identity"]["rendered_fingerprint"] != info["rendered_fingerprint"]:
            raise CampaignStop("Executed harness differs from frozen rendering")
        return job, adapter, out, marker

    # A frozen block contains one execution of each condition. Launch each block
    # together; retain schedule order in the records regardless of completion order.
    with ThreadPoolExecutor(max_workers=3) as pool:
        for start in range(0, len(jobs), 3):
            outputs.extend(pool.map(execute_job, jobs[start : start + 3]))
    write_json(
        runtime.state / "transfer-executions-complete.json",
        {"complete": True, "count": len(outputs)},
    )
    for job, adapter, out, marker in outputs:
        score_path = (
            runtime.state
            / "scores"
            / job["arm"]
            / f"rep{job['adaptation_replicate']}"
            / job["task"]
            / f"rep{job['repetition']}.json"
        )
        if score_path.exists():
            continue
        score = adapter.score(out, job["task"])
        _, evidence = adapter._details_candidates(job["task"], out.name)
        if not evidence.exists():
            evidence = evidence.with_name(evidence.name + ".judge_failed")
        write_json(
            score_path,
            {
                **job,
                "cohort": "heldout",
                "score": score,
                "session": marker["session"],
                "trace": marker["trace"],
                "output_dir": str(out),
                "harness_fingerprint": marker["identity"]["harness_fingerprint"],
                "rendered_fingerprint": marker["identity"]["rendered_fingerprint"],
                "status": "judgment_unavailable"
                if score is None
                else "judged"
                if adapter.delivered(out)
                else "agent_no_output",
                "judge_evidence": str(evidence) if evidence.exists() else None,
                "child_exit_code": marker["child_exit_code"],
                "collection_status": marker["collection_status"],
            },
        )
    write_json(runtime.state / "evaluate-complete.json", {"complete": True, "planned": len(jobs)})


def dispatch(args, runtime):
    try:
        if args.prepare:
            setup(runtime)
        runtime.start()
        if args.phase == "mechanism":
            from .mechanism import run

            return run(runtime)
        if args.phase == "report":
            from .v2_report import report

            return report(runtime, publish=args.publish)
        result = globals()[args.phase](runtime)
        if result:
            print(json.dumps(result, indent=2))
    except CampaignStop as exc:
        write_json(
            runtime.state / "stopped.json",
            {"phase": args.phase, "reason": str(exc), "budget": runtime.budget()},
        )
        print(f"STOPPED: {exc}", file=sys.stderr)
        raise SystemExit(2)
