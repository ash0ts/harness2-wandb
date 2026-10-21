"""Run only in the separate pinned WorkBuddy verifier environment."""

import argparse
import asyncio
import json
import uuid
from pathlib import Path

from .workbuddy_preflight import ROOT, DockerEnvironment, native_verify, run, save


def grade(task_id, patch, destination, state):
    receipt = destination / "judgment.json"
    if receipt.exists():
        return json.loads(receipt.read_text())
    destination.mkdir(parents=True, exist_ok=True)
    fixture = json.loads((state / "preflight" / task_id / "result.json").read_text())
    name = "h2-v4-grader-" + uuid.uuid4().hex[:12]
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
            "--mount",
            f"type=bind,src={destination},dst=/logs/verifier",
            "--entrypoint",
            "sleep",
            fixture["image_id"],
            "infinity",
        ]
    )
    try:
        run(["docker", "cp", str(patch), name + ":/tmp/delivered.patch"])
        environment = DockerEnvironment(name)
        applied = asyncio.run(
            environment.exec(
                "test ! -s /tmp/delivered.patch || git apply --binary /tmp/delivered.patch",
                cwd="/workspace",
            )
        )
        if applied.return_code:
            raise RuntimeError("Delivered patch could not be reproduced in pinned grader image")
        task = ROOT / "dependencies/workbuddy-code-data/wb-bench-code-v1.0/tasks" / task_id
        result = asyncio.run(native_verify(task, environment, destination))
        score = json.loads((destination / "score.json").read_text())
        value = {
            "score": None
            if score.get("test_status") == "judge_error"
            else result["rewards"]["reward"],
            "status": "unavailable_judgment"
            if score.get("test_status") == "judge_error"
            else "graded",
            "native": result,
            "image": fixture["image_id"],
        }
        save(receipt, value)
        return value
    finally:
        run(["docker", "rm", "-f", name], check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()
    if Path(args.task).name != args.task:
        raise ValueError("Invalid task identity")
    print(
        json.dumps(
            grade(args.task, args.patch.resolve(), args.output.resolve(), args.state.resolve())
        )
    )


if __name__ == "__main__":
    main()
