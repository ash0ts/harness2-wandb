"""Opt-in experiment phases; the upstream CLI and workflow remain unchanged."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from harness2.core.library import Library
from harness2.core.schedule import Namespace, run_task

from .data import prepare
from .experiment import ExperimentAdapter, ExperimentProposer
from .runtime import ROOT, CampaignStop, Runtime, command, fingerprint, load_manifest
from .telemetry import export_session, tool_events

ARMS = ("baseline", "harness2", "diagnostics")


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2))


def setup(runtime):
    repo = ROOT / "dependencies/job-bench-eval"
    if not repo.exists():
        command(["git", "clone", "https://github.com/Job-Bench/job-bench-eval.git", str(repo)])
        command(["git", "checkout", "--detach", runtime.manifest["jobbench_revision"]], cwd=repo)
    if (
        command(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
        != runtime.manifest["jobbench_revision"]
    ):
        raise CampaignStop("JobBench source revision differs")
    patch = ROOT / "third_party/patches/job-bench-eval-judge.diff"
    applied = (
        subprocess.run(
            ["git", "apply", "--reverse", "--check", str(patch)], cwd=repo, capture_output=True
        ).returncode
        == 0
    )
    if not applied:
        command(["git", "apply", str(patch)], cwd=repo)
    prepare(runtime.manifest, runtime.state)
    command(
        ["docker", "build", "-t", runtime.manifest["image"], "-f", "showcase/Dockerfile", "."],
        cwd=ROOT,
    )
    command(
        [
            "docker",
            "build",
            "-t",
            runtime.manifest["image"] + "-judge",
            "-f",
            "showcase/Dockerfile.judge",
            ".",
        ],
        cwd=ROOT,
    )


def verify_fixtures(runtime):
    source = json.loads((runtime.state / "source-provenance.json").read_text())
    for record in source["files"]:
        path = ROOT / "dependencies/job-bench-eval/dataset/main" / record["path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise CampaignStop(f"Fixture drift: {record['path']}")


def smoke(runtime):
    receipt = runtime.state / "qualification.json"
    if receipt.exists():
        print(receipt.read_text())
        return
    sessions = []
    for role in ("solver", "judge"):
        session, response = runtime.model_call(
            phase="smoke",
            role=role,
            messages=[{"role": "user", "content": "Reply exactly QUALIFIED."}],
            output_limit=2048,
            arm="qualification",
            task="synthetic-model",
        )
        if "QUALIFIED" not in response["choices"][0]["message"].get("content", ""):
            raise CampaignStop(f"{role} failed text qualification")
        export_session(runtime, session)
        sessions.append(session)
    work = runtime.state / "smoke" / uuid.uuid4().hex
    work.mkdir(parents=True)
    (work / "input.txt").write_text("2,3,5\n")
    result, directory = runtime.run_agent(
        work,
        "First run a shell command that exits with status 7. Then recover: use the task "
        "tool to delegate reading input.txt and summing its integers to an explore subagent. "
        "Write output/answer.txt containing the sum, then verify the file using Python.",
        phase="smoke",
        role="solver",
        arm="qualification",
        task="synthetic-tools",
    )
    if (
        not (work / "output/answer.txt").exists()
        or (work / "output/answer.txt").read_text().strip() != "10"
    ):
        raise CampaignStop("OpenCode did not produce the qualification deliverable")
    before = fingerprint(work)
    export_session(runtime, result["session"], result, directory)
    if fingerprint(work) != before or not tool_events(directory / "events.jsonl"):
        raise CampaignStop("Observation changed execution output or captured no tools")
    sessions.append(result["session"])
    audit = json.loads((directory / "opencode-audit.json").read_text())
    children = {
        e["session"]["id"]
        for e in audit
        if e["type"] == "audit_session" and e["session"]["parent_id"]
    }
    if not children or not any(
        p.get("sessionID") in children for p in tool_events(directory / "events.jsonl")
    ):
        raise CampaignStop("Child-session tool capture did not qualify")
    fixture = work / "synthetic-judge"
    fixture.mkdir()
    write(
        fixture / "RUBRICS.json",
        {
            "rubrics": [
                {
                    "rubric": "Does the answer contain the correct sum?",
                    "weight": 1,
                    "criterion": [
                        "answer.txt contains the integer 10, which is the sum of 2, 3, 5."
                    ],
                }
            ]
        },
    )
    adapter = ExperimentAdapter(runtime, "qualification")
    adapter.phase = "smoke"
    adapter._dataset_task = lambda _: fixture
    native_score = adapter.score(work / "output", "qualification/task")
    if not native_score or native_score["n_passed"] != native_score["n_criteria"]:
        raise CampaignStop("Native JobBench grading did not qualify")
    budget = runtime.budget()
    if budget["unresolved"]:
        raise CampaignStop("Qualification has unresolved usage")
    write(
        receipt,
        {
            "passed": True,
            "sessions": sessions,
            "spent_usd": budget["spent_usd"],
            "native_score": native_score,
            "checks": [
                "requested and returned models",
                "OpenCode tools and deliverable",
                "stored span coverage and usage",
                "export preserves work files",
            ],
        },
    )
    print(receipt.read_text())


def allocation(runtime):
    """Conservative planning assumptions; the gateway separately enforces the hard cap."""
    path = runtime.state / "allocation.json"
    if path.exists():
        return json.loads(path.read_text())
    qualification = json.loads((runtime.state / "qualification.json").read_text())
    if not qualification["passed"]:
        raise CampaignStop("Qualification did not pass")
    budget = runtime.budget()
    actor = runtime.manifest["models"]["actor"]
    samples = [
        json.loads(r["usage"])
        for r in budget["calls"]
        if r["model"] == actor and r["status"] == "complete"
    ]
    if not samples:
        raise CampaignStop("No observed actor usage for projection")
    # Account for longer contexts and deliverables than the synthetic qualification.
    inp = min(runtime.manifest["max_input_tokens"], max(x["prompt_tokens"] for x in samples) * 5)
    out = min(
        runtime.manifest["max_output_tokens"], max(x["completion_tokens"] for x in samples) * 20
    )
    rates = runtime.manifest["prices_per_million"][actor]
    per_session = (
        runtime.manifest["max_steps"]
        * (inp * float(rates["input"]) + out * float(rates["output"]))
        / 1e6
    )
    rubric_counts = {}
    for domain in runtime.manifest["domains"]:
        for task in runtime.manifest["adapt_tasks"] + runtime.manifest["heldout_tasks"]:
            key = domain + "/" + task
            rubric_counts[key] = len(
                json.loads(
                    (
                        ROOT / "dependencies/job-bench-eval/dataset/main" / key / "RUBRICS.json"
                    ).read_text()
                )["rubrics"]
            )
    judge_rates = runtime.manifest["prices_per_million"][runtime.manifest["models"]["judge"]]
    judge_per_rubric = (
        50_000 * float(judge_rates["input"]) + 4096 * float(judge_rates["output"])
    ) / 1e6
    for repetitions in (runtime.manifest["repetitions"], 1):
        # Four baseline adaptation sessions, 8*(4 rollouts + 3 edits + reflection), heldout.
        agent_sessions = 4 + 8 * 8 + 6 * repetitions
        judge_calls = sum(
            n * (3 if key.endswith(tuple(runtime.manifest["adapt_tasks"])) else 3 * repetitions)
            for key, n in rubric_counts.items()
        )
        estimate = agent_sessions * per_session + judge_calls * judge_per_rubric
        if estimate + budget["spent_usd"] < float(runtime.manifest["budget_usd"]):
            decision = {
                "repetitions": repetitions,
                "projected_remaining_usd": estimate,
                "actor_sessions": agent_sessions,
                "judge_rubric_calls": judge_calls,
                "assumptions": {
                    "input_tokens_per_actor_call": inp,
                    "output_tokens_per_actor_call": out,
                    "calls_per_session": runtime.manifest["max_steps"],
                    "judge_input_tokens": 50000,
                    "judge_output_tokens": 4096,
                    "cache_savings": "none",
                    "retry_contingency": "hard gateway reservations",
                },
            }
            write(path, decision)
            print(json.dumps(decision, indent=2))
            return decision
    raise CampaignStop(
        "Complete comparison does not fit the observed-usage projection, even at one repetition"
    )


def freeze(runtime, arm, domain, harness):
    dest = runtime.state / "frozen" / arm / domain / "harness"
    if dest.exists():
        if fingerprint(dest) != fingerprint(harness):
            raise CampaignStop("An adopted frozen harness changed")
        return
    shutil.copytree(harness, dest)
    base = ExperimentAdapter(runtime, arm).base_harness()
    diff = []
    files = {str(p.relative_to(base)) for p in base.rglob("*") if p.is_file()}
    files |= {str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()}
    for rel in sorted(files):
        a, b = base / rel, dest / rel
        diff.extend(
            difflib.unified_diff(
                a.read_text().splitlines(True) if a.exists() else [],
                b.read_text().splitlines(True) if b.exists() else [],
                fromfile="baseline/" + rel,
                tofile=arm + "/" + rel,
            )
        )
    (dest.parent / "harness.diff").write_text("".join(diff))
    write(
        dest.parent / "freeze.json",
        {"fingerprint": fingerprint(dest), "after_task": "task2", "arm": arm, "domain": domain},
    )


def _adapt_domain(runtime, domain, arm):
    adapter = ExperimentAdapter(runtime, arm)
    root = runtime.state / "arms" / arm
    library = Library(root / "libraries", domain, adapter.substrate.surface())
    for task in runtime.manifest["adapt_tasks"]:
        task_id = domain + "/" + task
        if arm == "baseline":
            adapter.execute(task_id, None, "adapt/baseline")
            chosen = adapter.base_harness()
        else:
            prop = ExperimentProposer(runtime, adapter, task_id, root / "audit" / task_id)
            result = run_task(
                task_id,
                1,
                adapter=adapter,
                prop=prop,
                selector_prop=prop,
                runs_root=root / "runs",
                modes=("agg",),
                seed=42,
                tag=arm,
                libraries={"agg": library},
            )
            write(root / "results" / (task_id.replace("/", "__") + ".json"), result.to_dict())
            selected = result.selections.get("agg", {}).get("chosen_label")
            if not selected:
                raise CampaignStop(f"No adoptable harness for {arm} {task_id}; attempts preserved")
            chosen = (
                Namespace("jb", "oc", "agg", 1, 42, arm).task_dir(root / "runs", task_id)
                / "harnesses"
                / selected
            )
        if task == runtime.manifest["adapt_tasks"][-1]:
            freeze(runtime, arm, domain, chosen)


def adapt(runtime):
    allocation(runtime)
    verify_fixtures(runtime)
    provenance = runtime.state / "runtime-provenance.json"
    if not provenance.exists():
        write(
            provenance,
            {
                "experiment_commit": command(["git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip(),
                "worker_image": json.loads(
                    command(["docker", "image", "inspect", runtime.manifest["image"]]).stdout
                )[0]["Id"],
                "judge_image": json.loads(
                    command(
                        ["docker", "image", "inspect", runtime.manifest["image"] + "-judge"]
                    ).stdout
                )[0]["Id"],
                "source_hashes": {
                    str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted((ROOT / "harness2/showcase").glob("*.py"))
                },
            },
        )
    jobs = [(domain, arm) for domain in runtime.manifest["domains"] for arm in ARMS]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_adapt_domain, runtime, domain, arm) for domain, arm in jobs]
        for future in futures:
            future.result()
    write(runtime.state / "adapt-complete.json", {"complete": True})


def score_run(runtime, adapter, task_id, output, cohort, repetition):
    label = output.name
    score_path = runtime.state / "scores" / adapter.arm / task_id / (label + ".json")
    if score_path.exists():
        return
    marker = json.loads((adapter._traj_dir(task_id, label) / "showcase.json").read_text())
    score = adapter.score(output, task_id)
    _, judge = adapter._details_candidates(task_id, label)
    write(
        score_path,
        {
            "arm": adapter.arm,
            "task": task_id,
            "cohort": cohort,
            "repetition": repetition,
            "score": score,
            "session": marker["session"],
            "harness_fingerprint": marker["identity"]["harness_fingerprint"],
            "trace": marker["trace"],
            "output_dir": str(output),
            "judge_evidence": str(judge) if judge.exists() else None,
            "status": "judged" if score is not None else "judgment_unavailable",
        },
    )
    print(f"score {adapter.arm} {task_id} {cohort}/{repetition}: {score}", flush=True)


def _evaluate_domain(runtime, domain, arm, reps):
    adapter = ExperimentAdapter(runtime, arm)
    for task in runtime.manifest["adapt_tasks"]:
        task_id = domain + "/" + task
        if arm == "baseline":
            output = adapter._out_dir(task_id, "adapt__baseline")
        else:
            result = json.loads(
                (
                    runtime.state
                    / "arms"
                    / arm
                    / "results"
                    / (task_id.replace("/", "__") + ".json")
                ).read_text()
            )
            output = Path(result["selections"]["agg"]["chosen_run_dir"])
        score_run(runtime, adapter, task_id, output, "adapt", 0)
    adapter.phase = "evaluate"
    frozen = runtime.state / "frozen" / arm / domain
    expected = json.loads((frozen / "freeze.json").read_text())["fingerprint"]
    if fingerprint(frozen / "harness") != expected:
        raise CampaignStop("Frozen harness drift")
    for task in runtime.manifest["heldout_tasks"]:
        for rep in range(1, reps + 1):
            adapter.repetition = rep
            task_id = domain + "/" + task
            output = adapter.execute(task_id, frozen / "harness", f"evaluate/rep{rep}")
            score_run(runtime, adapter, task_id, output, "heldout", rep)


def evaluate(runtime):
    if not (runtime.state / "adapt-complete.json").exists():
        raise CampaignStop("Finish adaptation and freeze all harnesses before heldout evaluation")
    verify_fixtures(runtime)
    reps = allocation(runtime)["repetitions"]
    jobs = [(domain, arm) for domain in runtime.manifest["domains"] for arm in ARMS]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(_evaluate_domain, runtime, domain, arm, reps) for domain, arm in jobs
        ]
        for future in futures:
            future.result()
    write(runtime.state / "evaluate-complete.json", {"complete": True})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("smoke", "adapt", "evaluate", "report"))
    parser.add_argument("--manifest", type=Path, default=ROOT / "showcase/pilot.json")
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Fetch pinned public fixtures and build the worker image",
    )
    parser.add_argument(
        "--publish", action="store_true", help="Upload allowlisted results and save a W&B Report"
    )
    args = parser.parse_args()
    manifest, state = load_manifest(args.manifest)
    tmp = state / "tmp"
    tmp.mkdir(exist_ok=True)
    tempfile.tempdir = str(tmp)
    runtime = Runtime(manifest, state)
    try:
        if args.prepare:
            setup(runtime)
        runtime.start()
        if args.phase == "report":
            from .report import report

            report(runtime, publish=args.publish)
        else:
            globals()[args.phase](runtime)
    except CampaignStop as exc:
        write(
            state / "stopped.json",
            {"phase": args.phase, "reason": str(exc), "budget": runtime.budget()},
        )
        print(f"STOPPED: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
