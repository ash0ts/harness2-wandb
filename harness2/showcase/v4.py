"""Repeated multi-benchmark campaign, gated by fixtures, qualification, and budget."""

from __future__ import annotations

import difflib
import fcntl
import hashlib
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

from harness2 import config
from harness2.core.library import Library
from harness2.core.schedule import Namespace, run_task

from .code_study import CodeAdapter, StudyAdapter
from .protocol import changed_files, harness_record, inventory
from .runtime import ROOT, CampaignStop, fingerprint
from .runtime_v4 import StudyRuntime
from .study import CONDITIONS, evaluation_schedule, status
from .v2 import V2Proposer, bounded_parallel, qualify_tools
from .workbuddy_preflight import save


def read(path):
    return json.loads(Path(path).read_text())


def adapter(runtime, arm, trial, *, qualification=False):
    cls = CodeAdapter if runtime.study == "code" else StudyAdapter
    return cls(runtime, arm, trial, qualification=qualification)


def groups(runtime, qualification=False):
    spec = runtime.manifest["studies"][runtime.study]
    if runtime.study == "code":
        selection = read(runtime.state / "preflight/selection.json")
        if not selection["ready"]:
            raise CampaignStop("WorkBuddy task category shortfall; no paid launch")
        if qualification:
            tasks = selection["qualification"]
            return [
                {"group": category, "adapt": tasks[i], "heldout": [tasks[i + 1]]}
                for category, i in (("bug-fix", 0), ("feature", 2))
            ]
        return selection["groups"]
    if qualification:
        return [
            {"group": task.split("/")[0], "adapt": task, "heldout": []}
            for task in spec["qualification_tasks"]
        ]
    return spec["groups"]


def freeze_path(runtime, arm, trial, group, qualification=False):
    from .qualification import attempt

    cohort = f"qualification-{attempt(runtime)}" if qualification else "main"
    return (
        runtime.state
        / "studies"
        / runtime.study
        / "frozen"
        / cohort
        / arm
        / f"trial{trial}"
        / group
    )


def adapt_one(runtime, group, arm, trial, qualification=False):
    destination = freeze_path(runtime, arm, trial, group["group"], qualification)
    if (destination / "freeze.json").exists():
        receipt = read(destination / "freeze.json")
        if (
            fingerprint(destination / "harness", executable_modes=True)
            != receipt["source_fingerprint"]
        ):
            raise CampaignStop("Frozen harness drift")
        return destination
    bench = adapter(runtime, arm, trial, qualification=qualification)
    task = group["adapt"]
    selected, result, chosen = bench.base_harness(), None, None
    result_path = bench.root / "results" / (task.replace("/", "__") + ".json")
    if arm == "baseline":
        for number in (1, 2):
            bench.execute(task, None, f"shared/base{number}")
    else:
        if result_path.exists():
            result = read(result_path)
        else:
            library = Library(bench.root / "libraries", group["group"], bench.substrate.surface())
            prop = V2Proposer(runtime, bench, task, bench.root / "audit" / task)
            result = run_task(
                task,
                2,
                adapter=bench,
                prop=prop,
                selector_prop=prop,
                runs_root=bench.root / "runs",
                modes=("seq",),
                seed=runtime.manifest["seed"],
                tag=arm,
                libraries={"seq": library},
            ).to_dict()
            save(result_path, result)
        choice = result.get("selections", {}).get("seq", {})
        if choice.get("ok"):
            chosen = choice.get("chosen_label")
            if chosen and not chosen.startswith("base"):
                ns = Namespace(bench.spec.name, "oc", "seq", 2, runtime.manifest["seed"], arm)
                selected = ns.task_dir(bench.root / "runs", task) / "harnesses" / chosen
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(selected, destination / "harness")
    current = harness_record(selected, group["group"], runtime.manifest)
    original = harness_record(bench.base_harness(), group["group"], runtime.manifest)
    lines = []
    for rel in changed_files(original["source_files"], current["source_files"]):
        old, new = bench.base_harness() / rel, Path(selected) / rel
        if (
            old.exists()
            and new.exists()
            and bool(old.stat().st_mode & 0o111) != bool(new.stat().st_mode & 0o111)
        ):
            lines.extend(
                [
                    f"diff --git baseline/{rel} adopted/{rel}\n",
                    f"old mode {100755 if old.stat().st_mode & 0o111 else 100644}\n",
                    f"new mode {100755 if new.stat().st_mode & 0o111 else 100644}\n",
                ]
            )
        lines.extend(
            difflib.unified_diff(
                old.read_text().splitlines(True) if old.exists() else [],
                new.read_text().splitlines(True) if new.exists() else [],
                fromfile="baseline/" + rel,
                tofile="adopted/" + rel,
            )
        )
    (destination / "harness.diff").write_text("".join(lines))
    stages = result.get("modes", {}).get("seq", {}).get("stages", []) if result else []
    save(
        destination / "freeze.json",
        {
            **current,
            "study": runtime.study,
            "group": group["group"],
            "trial": trial,
            "condition": arm,
            "adapt_task": task,
            "chosen_label": chosen,
            "adoption": "original"
            if arm == "baseline"
            else "selected"
            if chosen
            else "baseline_fallback",
            "adaptation_complete": bool(
                chosen and len(stages) == 6 and not any(s["fell_back"] for s in stages)
            ),
            "valid_stage_decisions": sum(not s["fell_back"] for s in stages),
            "fallback_stages": [s["label"] for s in stages if s["fell_back"]],
            "executable_change": current["rendered_fingerprint"]
            != original["rendered_fingerprint"],
            "changed_executable_files": changed_files(
                original["rendered_files"], current["rendered_files"]
            ),
            "result": str(result_path) if result else None,
        },
    )
    return destination


def engine_snapshot(runtime):
    source = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in (ROOT / "harness2/showcase", ROOT / "showcase")
        for p in folder.glob("*")
        if p.is_file()
        and (p.suffix in (".py", ".json") or p.name.startswith("Dockerfile"))
        and "report" not in p.name
        and "analysis" not in p.name
    }
    return {
        "source": source,
        "images": read(runtime.state / "prepared.json"),
        "analysis_sha256": hashlib.sha256(
            (ROOT / "harness2/showcase/study_analysis.py").read_bytes()
        ).hexdigest(),
    }


def smoke(runtime):
    from .qualification import attempt

    root = runtime.state / "qualifications" / f"attempt{attempt(runtime)}"
    root.mkdir(parents=True, exist_ok=True)
    snapshot = engine_snapshot(runtime)
    provenance = root / "engine.json"
    if provenance.exists() and read(provenance) != snapshot:
        raise CampaignStop("Qualification engine changed; preserve and restart qualification")
    save(provenance, snapshot)
    qualify_tools(runtime)
    for group in groups(runtime, qualification=True):
        receipt_path = root / runtime.study / (group["group"] + ".json")
        if receipt_path.exists():
            continue
        frozen = adapt_one(runtime, group, "diagnostics", 1, qualification=True)
        freeze = read(frozen / "freeze.json")
        if not freeze["adaptation_complete"]:
            raise CampaignStop(
                f"Full two-round decisions did not qualify: {runtime.study}/{group['group']}"
            )
        result = read(freeze["result"])
        output = Path(result["selections"]["seq"]["chosen_run_dir"])
        bench = adapter(runtime, "diagnostics", 1, qualification=True)
        score = bench.score(output, group["adapt"])
        if not score or (
            score.get("score") is None if runtime.study == "code" else not score.get("n_criteria")
        ):
            raise CampaignStop("Native judging did not qualify")
        if runtime.study == "jobbench" and (
            not inventory(output) or any(f["readable"] is not True for f in inventory(output))
        ):
            raise CampaignStop("Qualification deliverables are not readable")
        transfers = []
        for task in group["heldout"]:
            out = bench.execute(task, frozen / "harness", "qualification-transfer")
            transfer_score = bench.score(out, task)
            if not transfer_score or transfer_score.get("score") is None:
                raise CampaignStop("Coding qualification transfer was not gradeable")
            transfers.append({"task": task, "output": str(out), "score": transfer_score})
        save(
            receipt_path,
            {
                "passed": True,
                "study": runtime.study,
                "group": group,
                "freeze": str(frozen),
                "score": score,
                "transfers": transfers,
            },
        )
    expected = sum(2 for _ in runtime.manifest["studies"])
    if len(list(root.glob("*/*.json"))) == expected:
        save(
            runtime.state / "qualification.json",
            {
                "passed": True,
                "attempt": attempt(runtime),
                "engine": snapshot,
                "receipts": str(root),
            },
        )


def forecast(runtime):
    """Cost every predeclared session type and rubric, with an explicit reserve."""
    if not (runtime.state / "qualification.json").exists():
        raise CampaignStop("Both studies must qualify before forecasting the main campaign")
    qualified_attempt = read(runtime.state / "qualification.json")["attempt"]
    buckets = defaultdict(lambda: defaultdict(float))
    judge_rates = []
    for row in runtime.budget()["calls"]:
        if row["phase"] != "smoke":
            continue
        identity_path = runtime.state / "gateway/sessions" / row["session"] / "identity.json"
        identity = read(identity_path)
        if identity.get("qualification_attempt") != qualified_attempt:
            continue
        if identity.get("task") == "infrastructure-tools":
            continue
        study, task, role = identity.get("study"), identity.get("task"), identity.get("role")
        cost = (row["spent"] if row["spent"] is not None else row["reserved"]) / 1_000_000
        kind = (
            "judge"
            if role == "judge"
            else "proposal"
            if role in ("improver", "reflection")
            else "baseline"
            if identity.get("arm") == "baseline"
            else "solver"
        )
        buckets[study, kind][task] += cost
        if role == "judge":
            judge_rates.append(cost)
    comparison, details = 0, {}
    for study, spec in runtime.manifest["studies"].items():
        scoped = StudyRuntime(runtime.manifest, runtime.state, study)
        count = len(groups(scoped)) * spec["adaptation_trials"]
        baseline = max(buckets[study, "baseline"].values(), default=0)
        proposal = max(buckets[study, "proposal"].values(), default=0)
        # Each qualification adaptation has six solver probes. A separate coding
        # transfer is its own task; retain the more expensive per-execution estimate.
        probe = max(
            (
                cost / (6 if task in {g["adapt"] for g in groups(scoped, True)} else 1)
                for task, cost in buckets[study, "solver"].items()
            ),
            default=0,
        )
        if not all((baseline, proposal, probe)):
            raise CampaignStop("Qualification has insufficient stage-cost observations")
        actor = count * (baseline + 2 * proposal + 12 * probe) + spec["final_executions"] * probe
        judging, rubrics = 0, 0
        if study == "jobbench":
            from .study import evaluation_schedule

            schedule = evaluation_schedule(runtime.manifest, {study: groups(scoped)})
            repeat_count = 8
            chosen = sorted(
                schedule,
                key=lambda r: hashlib.sha256(
                    f"{runtime.manifest['analysis']['seed']}:{r['key']}".encode()
                ).hexdigest(),
            )[:repeat_count]
            for row in schedule + chosen:
                material = read(config.jb_repo() / "dataset/main" / row["task"] / "RUBRICS.json")
                rubrics += (
                    len(material)
                    if isinstance(material, list)
                    else len(material.get("rubrics", material))
                )
            if not judge_rates:
                raise CampaignStop("No native rubric judging costs observed")
            judging = rubrics * max(judge_rates)
        details[study] = {
            "actor_usd": actor,
            "judging_usd": judging,
            "rubric_calls": rubrics,
            "baseline_pair_usd": baseline,
            "proposal_rounds_usd": proposal,
            "solver_execution_usd": probe,
        }
        comparison += actor + judging
    projected = comparison * 1.25
    value = {
        "studies": details,
        "fixed_comparison_usd": comparison,
        "with_contingency_usd": projected,
        "main_cap_usd": 410,
        "recovery_reserved_usd": 50,
        "fits": projected <= 410,
        "shortfall_usd": max(0, projected - 410),
    }
    save(runtime.state / "cost-projection.json", value)
    if not value["fits"]:
        raise CampaignStop(
            f"Complete fixed comparison forecast exceeds allocation by ${value['shortfall_usd']:.2f}"
        )
    return value


def verify_qualified_engine(runtime):
    qualified = read(runtime.state / "qualification.json")["engine"]
    current = engine_snapshot(runtime)
    if current == qualified:
        return
    # A documented transport repair may resume saved work without replacing the
    # historical qualification. Agent, fixture, image and analysis changes still stop.
    receipt_path = runtime.state / "runtime-repairs/provider-stream-errors.json"
    if receipt_path.exists():
        receipt = read(receipt_path)
        changed = {
            path
            for path in qualified["source"].keys() | current["source"].keys()
            if qualified["source"].get(path) != current["source"].get(path)
        }
        allowed = {
            "harness2/showcase/transport_v4.py",
            "harness2/showcase/runtime.py",
            "harness2/showcase/v4.py",
        }
        validation = receipt.get("validation", {})
        log = runtime.state / "runtime-repairs" / validation.get("log", "missing")
        if (
            receipt.get("qualified_engine") == qualified
            and receipt.get("repaired_engine") == current
            and changed <= allowed
            and current["images"] == qualified["images"]
            and current["analysis_sha256"] == qualified["analysis_sha256"]
            and validation.get("passed") is True
            and log.is_file()
            and hashlib.sha256(log.read_bytes()).hexdigest() == validation.get("sha256")
        ):
            return
    raise CampaignStop("Runtime differs from qualified source or images")


def adapt(runtime):
    forecast(runtime)
    verify_qualified_engine(runtime)
    trials = runtime.manifest["studies"][runtime.study]["adaptation_trials"]
    jobs = [(group, trial) for group in groups(runtime) for trial in range(1, trials + 1)]

    def run(job):
        group, trial = job
        for arm in CONDITIONS:
            adapt_one(runtime, group, arm, trial)

    bounded_parallel(jobs, run, 2)
    save(runtime.state / f"{runtime.study}-adapt-complete.json", {"complete": True})
    if all(
        (runtime.state / f"{s}-adapt-complete.json").exists() for s in runtime.manifest["studies"]
    ):
        save(runtime.state / "adapt-complete.json", {"complete": True})


def schedule(runtime):
    planned = evaluation_schedule(
        runtime.manifest,
        {
            study: groups(StudyRuntime(runtime.manifest, runtime.state, study))
            for study in runtime.manifest["studies"]
        },
    )
    path = runtime.state / "evaluation-schedule.json"
    if path.exists() and read(path) != planned:
        raise CampaignStop("Frozen evaluation schedule drift")
    save(path, planned)
    return planned


def evaluate(runtime):
    if not (runtime.state / "adapt-complete.json").exists():
        raise CampaignStop("Freeze every adaptation in both studies before any transfer execution")
    verify_qualified_engine(runtime)
    jobs = [row for row in schedule(runtime) if row["study"] == runtime.study]

    def execute(row):
        key = hashlib.sha256(row["key"].encode()).hexdigest()
        path = runtime.state / "evaluations" / (key + ".json")
        if path.exists():
            return
        bench = adapter(runtime, row["condition"], row["trial"])
        bench.phase = "evaluate"
        frozen = freeze_path(runtime, row["condition"], row["trial"], row["group"])
        freeze = read(frozen / "freeze.json")
        if fingerprint(frozen / "harness", executable_modes=True) != freeze["source_fingerprint"]:
            raise CampaignStop("Executed frozen harness changed")
        output = bench.execute(row["task"], frozen / "harness", "heldout")
        marker = read(bench._traj_dir(row["task"], output.name) / "showcase.json")
        save(
            path,
            {
                **row,
                "output": str(output),
                "freeze": str(frozen),
                "score": None,
                "status": "awaiting_judgment",
                "execution": marker,
            },
        )

    bounded_parallel(jobs, execute, 2)
    save(runtime.state / f"{runtime.study}-execute-complete.json", {"complete": True})
    if all(
        (runtime.state / f"{s}-execute-complete.json").exists() for s in runtime.manifest["studies"]
    ):
        save(runtime.state / "execute-complete.json", {"complete": True})


def grade(runtime):
    if not (runtime.state / "execute-complete.json").exists():
        return
    for path in sorted((runtime.state / "evaluations").glob("*.json")):
        row = read(path)
        if row["study"] != runtime.study or row["status"] != "awaiting_judgment":
            continue
        bench = adapter(runtime, row["condition"], row["trial"])
        bench.phase = "evaluate"
        result = bench.score(Path(row["output"]), row["task"])
        score = (
            (
                result.get("score")
                if runtime.study == "code"
                else result["n_passed"] / result["n_criteria"]
            )
            if result
            else None
        )
        row.update(
            score=score,
            judgment=result,
            status="graded" if score is not None else "unavailable_judgment",
        )
        save(path, row)
    save(runtime.state / f"{runtime.study}-graded.json", {"complete": True})
    if all((runtime.state / f"{s}-graded.json").exists() for s in runtime.manifest["studies"]):
        save(runtime.state / "evaluate-complete.json", {"complete": True})


def dispatch(args, manifest, state):
    state.mkdir(parents=True, exist_ok=True)
    lock = (state / ".runner.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("A v4 runner is active; use the read-only status command")
    studies = list(manifest["studies"]) if args.study == "all" else [args.study]
    runtime = StudyRuntime(manifest, state, studies[0])
    try:
        if args.prepare:
            from .prepare_v4 import setup

            setup(runtime)
        if not (state / "prepared.json").exists():
            raise CampaignStop("Prepare and verify all task images before paid qualification")
        runtime.start()
        if args.restart_qualification:
            from .qualification import attempt

            if args.phase != "smoke" or not (state / "stopped.json").exists():
                raise CampaignStop(
                    "A qualification restart requires its recorded stop and repair reason"
                )
            if (
                read(state / "stopped.json")["phase"] != "smoke"
                or (state / "adapt-complete.json").exists()
            ):
                raise CampaignStop("Only failed pre-main qualification can be restarted")
            if runtime.budget()["unresolved"]:
                raise CampaignStop(
                    "Resolve unbounded or pending usage before restarting qualification"
                )
            number = attempt(runtime)
            archive = state / "qualifications" / f"attempt{number}"
            archive.mkdir(parents=True, exist_ok=True)
            shutil.copy2(state / "stopped.json", archive / "stopped.json")
            save(
                state / "qualification-attempt.json",
                {
                    "attempt": number + 1,
                    "repair_reason": args.restart_qualification,
                    "previous_attempt": number,
                },
            )
            (state / "stopped.json").unlink()
        for study in studies:
            runtime = StudyRuntime(manifest, state, study)
            if args.phase == "report":
                from .report_v4 import report

                return report(runtime, publish=args.publish)
            if args.phase not in ("smoke", "adapt", "evaluate"):
                raise CampaignStop("V4 has no separate mechanism phase")
            globals()[args.phase](runtime)
        if args.phase == "evaluate":
            for study in manifest["studies"]:
                grade(StudyRuntime(manifest, state, study))
        print(json.dumps(status(manifest, state, args.study), indent=2))
    except (CampaignStop, Exception) as exc:
        save(
            state / "stopped.json",
            {
                "phase": args.phase,
                "reason": str(exc),
                "budget": runtime.budget() if (state / "gateway").exists() else None,
            },
        )
        print(f"STOPPED: {exc}", file=sys.stderr)
        raise SystemExit(2)
    finally:
        lock.close()
