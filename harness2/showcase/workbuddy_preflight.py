"""Unpaid fixture qualification using the pinned WorkBuddy CompositeVerifier.

Only metadata and verification outcomes leave the grader. Instructions, solutions,
and hidden checks are never included in the selection report or model contexts.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import tarfile
import time
import tomllib
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
CATEGORIES = (
    "api-contract",
    "bug-fix",
    "feature",
    "performance",
    "refactor",
    "reliability",
    "security-hardening",
    "testing",
)
ARCHIVE_SHA256 = "f60394e44341f45f66af17ba69efc51ceadc61f17c08aa89e4af35b51f6f34e1"
CODE_REVISION = "b516950be5b56eb3be406c2f76ee1c5111dcb57f"
DATA_REVISION = "3e8ad839685c589d83a1dd60fe2d15eabb75f27a"
SEED = 20261021


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def run(argv, *, log=None, timeout=900, check=True):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if log:
        Path(log).write_text(result.stdout + result.stderr)
    if check and result.returncode:
        raise RuntimeError(f"{argv[0]} exited {result.returncode}; see {log}")
    return result


def order(task_id, seed=SEED):
    return hashlib.sha256(f"{seed}:{task_id}".encode()).hexdigest(), task_id


def metadata(task):
    config = tomllib.loads((task / "task.toml").read_text())
    item = config["metadata"]
    return {
        "task_id": task.name,
        "category": item["category"],
        "repository": item.get("repo"),
        "surface": item.get("surface"),
        "artifact_type": item.get("artifact_type"),
        "verification_mode": item.get("verification_mode"),
        "task_sha256": digest(task / "task.toml"),
        "workspace_sha256": digest(task / "environment/workspace.tar.gz"),
    }


def metadata_exclusion(row):
    if row["category"] not in CATEGORIES:
        return "category outside predeclared study"
    if row["surface"] == "synthetic_workspace" or not row["repository"]:
        return "synthetic workspace or missing repository identity"
    if row["artifact_type"] not in (
        "product_code",
        "test_code",
        "tests_only",
        "mixed",
        "config_tooling",
    ):
        return "non-code deliverable"
    return None


def build_context(task, destination):
    """Never send the grader or solution to Docker's executor build context."""
    destination.mkdir(parents=True, exist_ok=True)
    upstream = (task / "environment/Dockerfile").read_text()
    text = upstream.replace("FROM python:3.12-slim", "FROM python:3.12.13-slim-bookworm")
    text = text.replace("COPY scorer.py /scorer/scorer.py\n", "")
    text = text.replace(
        "RUN apt-get update &&",
        'RUN NODE_ARCH="$(dpkg --print-architecture)" && '
        '[ "$NODE_ARCH" != "amd64" ] || NODE_ARCH=x64; '
        "apt-get update &&",
    )
    text = text.replace("linux-x64.tar.xz", "linux-${NODE_ARCH}.tar.xz")
    # Preserve all task-specific installation commands. Pin their resolved
    # environment by image ID and pip inventory before any paid execution.
    if "scorer.py" in text or "COPY tests" in text or "COPY solution" in text:
        raise ValueError("Unrecognized grader material in executor Dockerfile")
    with tarfile.open(task / "environment/workspace.tar.gz") as archive:
        names = [member.name.lstrip("./") for member in archive.getmembers()]
        if any(n in {"scorer/scorer.py", "tests/gold.patch", "solution/solve.sh"} for n in names):
            raise ValueError("Hidden benchmark material in public workspace archive")
    shutil.copy2(task / "environment/workspace.tar.gz", destination / "workspace.tar.gz")
    (destination / "Dockerfile").write_text(text)
    return {
        "upstream_dockerfile_sha256": digest(task / "environment/Dockerfile"),
        "executor_dockerfile_sha256": digest(destination / "Dockerfile"),
    }


class DockerEnvironment:
    """The minimal Harbor environment interface used by its native verifier."""

    os = "linux"
    capabilities = SimpleNamespace(mounted=True)

    def __init__(self, container):
        self.container = container

    async def exec(self, command, cwd=None, env=None, timeout_sec=None):
        argv = ["docker", "exec"]
        if cwd:
            argv += ["--workdir", cwd]
        for key, value in (env or {}).items():
            argv += ["--env", f"{key}={value}"]
        argv += [self.container, "bash", "-lc", command]
        result = await asyncio.to_thread(run, argv, timeout=timeout_sec or 900, check=False)
        return SimpleNamespace(
            return_code=result.returncode, stdout=result.stdout, stderr=result.stderr
        )

    async def upload_dir(self, source_dir, target_dir):
        await self.exec(f"mkdir -p {target_dir}")
        await asyncio.to_thread(
            run, ["docker", "cp", str(source_dir) + "/.", self.container + ":" + target_dir]
        )


async def native_verify(task, environment, output):
    # Call the exact pinned dispatcher; do not reimplement native scoring.
    from workbuddy_bench.judge.profiles.dispatcher import _run_registry_verifier

    verifier = SimpleNamespace(
        environment=environment,
        task=SimpleNamespace(
            short_name=task.name,
            paths=SimpleNamespace(task_dir=task, tests_dir=task / "tests"),
            config=SimpleNamespace(verifier=SimpleNamespace(env={})),
        ),
        trial_paths=SimpleNamespace(verifier_dir=output, reward_json_path=output / "reward.json"),
        verifier_env={},
        override_env={},
    )
    result = await _run_registry_verifier(verifier)
    return result.model_dump(mode="json")


def verify_control(task, image, destination, control):
    destination.mkdir(parents=True, exist_ok=True)
    completed = destination / "completed.json"
    if completed.exists():
        return json.loads(completed.read_text())
    name = "h2-v4-preflight-" + uuid.uuid4().hex[:12]
    started = time.time()
    run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--label",
            "harness2.study=study-v4",
            "--network",
            "none",
            "--cpus",
            "2",
            "--memory",
            "4g",
            "--pids-limit",
            "256",
            "--mount",
            f"type=bind,src={destination},dst=/logs/verifier",
            "--entrypoint",
            "sleep",
            image,
            "infinity",
        ]
    )
    env = DockerEnvironment(name)
    try:
        solution_exit = None
        if control == "reference":
            asyncio.run(env.upload_dir(task / "solution", "/solution"))
            asyncio.run(env.upload_dir(task / "tests", "/tests"))
            result = asyncio.run(env.exec("bash /solution/solve.sh", cwd="/workspace"))
            (destination / "reference.log").write_text(result.stdout + result.stderr)
            solution_exit = result.return_code
        native = asyncio.run(native_verify(task, env, destination))
        score = json.loads((destination / "score.json").read_text())
        record = {
            "control": control,
            "image": image,
            "solution_exit": solution_exit,
            "native": native,
            "score": score,
            "duration_seconds": time.time() - started,
        }
        save(completed, record)
        return record
    finally:
        run(["docker", "inspect", name], log=destination / "container.json", check=False)
        run(["docker", "rm", "-f", name], check=False)


def control_signature(record):
    score = record["score"]
    return (
        record["native"]["rewards"].get("reward"),
        score.get("test_status"),
        score.get("tests_passed"),
        score.get("tests_total"),
    )


def fixture_check(task, destination, repeats=3):
    destination.mkdir(parents=True, exist_ok=True)
    completed = destination / "result.json"
    source_hashes = {
        str(p.relative_to(task)): digest(p) for p in sorted(task.rglob("*")) if p.is_file()
    }
    if completed.exists():
        record = json.loads(completed.read_text())
        if record["source_hashes"] != source_hashes:
            raise ValueError("Fixture drift: use a new preflight attempt")
        return record
    record = {**metadata(task), "source_hashes": source_hashes, "eligible": False}
    try:
        context = destination / "build"
        record.update(build_context(task, context))
        image = "harness2-workbuddy:" + task.name.replace("_", "-")
        run(
            ["docker", "build", "-t", image, str(context)],
            log=destination / "build.log",
            timeout=1200,
        )
        record["image_id"] = run(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"]
        ).stdout.strip()
        image = record["image_id"]
        run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                "python",
                image,
                "-m",
                "pip",
                "freeze",
                "--all",
            ],
            log=destination / "pip-freeze.txt",
        )
        # The negative control is the untouched task workspace, not a model result.
        controls = {
            control: [
                verify_control(task, image, destination / f"{control}-{i}", control)
                for i in range(1, repeats + 1)
            ]
            for control in ("reference", "unchanged")
        }
        signatures = {key: [control_signature(r) for r in rows] for key, rows in controls.items()}
        record["controls"] = signatures
        failures = []
        if any(row["solution_exit"] != 0 for row in controls["reference"]):
            failures.append("reference solution did not complete")
        if any(s[0] != 1.0 or s[1] != "full_pass" for s in signatures["reference"]):
            failures.append("reference solution does not pass native verifier")
        if any(
            s[0] is None or s[0] >= 1.0 or s[1] in ("build_error", "judge_error")
            for s in signatures["unchanged"]
        ):
            failures.append("unchanged control not distinguishable by working verifier")
        if any(len(set(rows)) != 1 for rows in signatures.values()):
            failures.append("repeated verifier outcomes disagree")
        record["eligible"] = not failures
        record["exclusions"] = failures
    except Exception as exc:
        record["exclusions"] = [f"fixture infrastructure failure: {type(exc).__name__}: {exc}"]
    save(completed, record)
    print(
        json.dumps({key: record.get(key) for key in ("task_id", "eligible", "exclusions")}),
        flush=True,
    )
    return record


def select(records, seed=SEED):
    eligible = sorted(
        (r for r in records if r.get("eligible")), key=lambda r: order(r["task_id"], seed)
    )
    qualification = []
    for category in ("bug-fix", "feature"):
        qualification.extend([r["task_id"] for r in eligible if r["category"] == category][:2])
    groups, shortfalls = [], {}
    for category in CATEGORIES:
        rows = [
            r for r in eligible if r["category"] == category and r["task_id"] not in qualification
        ]
        chosen = None
        for adapt in rows:
            heldout = [r for r in rows if r["repository"] != adapt["repository"]][:3]
            if len(heldout) == 3:
                chosen = {
                    "group": category,
                    "adapt": adapt["task_id"],
                    "heldout": [r["task_id"] for r in heldout],
                }
                break
        if chosen:
            groups.append(chosen)
        else:
            shortfalls[category] = {"required": 4, "eligible_after_qualification": len(rows)}
    return {
        "seed": seed,
        "ordering": "sha256(UTF-8(str(seed) + ':' + task_id)), then task_id",
        "qualification": qualification,
        "groups": groups,
        "shortfalls": shortfalls,
        "ready": not shortfalls and len(qualification) == 4,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "dependencies/workbuddy-code-data")
    parser.add_argument("--output", type=Path, default=ROOT / "showcase/state/study-v4/preflight")
    parser.add_argument("--task", action="append", help="Run a subset; never freezes selection")
    args = parser.parse_args()
    archive = args.data / "wb-bench-code-v1.0.tar.gz"
    if digest(archive) != ARCHIVE_SHA256:
        raise ValueError("Pinned archive checksum mismatch")
    code = ROOT / "dependencies/workbuddy-bench"
    if run(["git", "-C", str(code), "rev-parse", "HEAD"]).stdout.strip() != CODE_REVISION:
        raise ValueError("Pinned WorkBuddy code revision mismatch")
    tasks = sorted((args.data / "wb-bench-code-v1.0/tasks").iterdir())
    exclusions, selected = [], []
    for task in tasks:
        row = metadata(task)
        reason = metadata_exclusion(row)
        if reason:
            exclusions.append({**row, "eligible": False, "exclusions": [reason]})
        elif not args.task or task.name in args.task:
            selected.append(task)
    args.output.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(
            pool.map(lambda task: fixture_check(task, args.output / task.name), selected)
        )
    save(args.output / "metadata-exclusions.json", exclusions)
    if not args.task:
        selection = select(records)
        save(args.output / "selection.json", selection)
        save(
            args.output / "summary.json",
            {
                "code_revision": CODE_REVISION,
                "dataset_revision": DATA_REVISION,
                "archive_sha256": ARCHIVE_SHA256,
                "model_spend_usd": 0,
                "records": records,
                "selection": selection,
            },
        )
        print(json.dumps(selection, indent=2))
        if not selection["ready"]:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
