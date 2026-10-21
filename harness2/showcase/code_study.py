"""WorkBuddy task execution with private native grading and upstream harnesses."""

from __future__ import annotations

import json
import re
import shutil
import threading
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from harness2.benchmarks.workbuddy import WbAdapter, workbuddy_prompts
from harness2.substrate.opencode import compose_prompt

from .experiment import ExperimentAdapter
from .protocol import inventory, write_json
from .runtime import ROOT, CampaignStop, command, fingerprint
from .telemetry import export_session
from .v2 import V2Adapter
from .workbuddy_preflight import save

_SHARED = threading.RLock()
TASKS = ROOT / "dependencies/workbuddy-code-data/wb-bench-code-v1.0/tasks"


def render_code(source, dest):
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "prompt.j2").write_text(compose_prompt(Path(source)))
    for src, target in (
        ("skills", ".config/opencode/skill"),
        ("scripts", "harness_scripts"),
        ("plugin", ".config/opencode/plugin"),
    ):
        if (source / src).exists():
            shutil.copytree(source / src, dest / target)
    for path in sorted((source / "sub_agents").glob("*/AGENT.md")):
        target = dest / ".config/opencode/agent" / (path.parent.name + ".md")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())


class StudyAdapter(V2Adapter):
    def __init__(self, runtime, arm, replicate, *, qualification=False):
        from .qualification import attempt

        super().__init__(runtime, arm, replicate, qualification=qualification)
        self.root = (
            runtime.state
            / "studies"
            / runtime.study
            / (f"qualification-{attempt(runtime)}" if qualification else "main")
            / arm
            / f"trial{replicate}"
        )
        self.store = self.root / "dataset"

    def execute(self, task_id, harness_dir, run_id, *, timeout=None):
        label = run_id.replace("/", "__")
        out, trajectory = self._out_dir(task_id, label), self._traj_dir(task_id, label)
        marker = trajectory / "showcase.json"
        if marker.exists():
            saved = json.loads(marker.read_text())
            from .protocol import harness_record, verify_executed_harness

            if fingerprint(out) != saved["output_fingerprint"]:
                raise CampaignStop("Completed deliverable changed; no paid rerun")
            source = Path(harness_dir or self.base_harness())
            executed = harness_record(source, self.domain_of(task_id), self.runtime.manifest)
            verify_executed_harness(saved, executed)
            directory = Path(saved["execution_dir"])
            write_json(directory / "harness-record.json", executed)
            shutil.copytree(source, directory / "source-harness", dirs_exist_ok=True)
            if not saved.get("trace", {}).get("verified"):
                saved["trace"] = export_session(self.runtime, saved["session"], saved, directory)
                save(marker, saved)
            return out
        base = re.fullmatch(r"base([12])", run_id.split("/")[-1])
        if base and self.arm != "baseline":
            source = type(self)(
                self.runtime, "baseline", self.replicate, qualification=self.qualification
            )
            with _SHARED:
                shared = source.execute(task_id, None, f"shared/base{base[1]}")
                shutil.copytree(shared, out)
                shutil.copytree(source._traj_dir(task_id, shared.name), trajectory)
                saved = json.loads(marker.read_text())
                saved.update(shared_baseline=True, arm=self.arm, run_id=run_id, output_dir=str(out))
                save(marker, saved)
            return out
        return self.execute_fresh(task_id, harness_dir, run_id, timeout=timeout)

    def execute_fresh(self, task_id, harness_dir, run_id, *, timeout=None):
        return ExperimentAdapter.execute(self, task_id, harness_dir, run_id, timeout=timeout)


class CodeAdapter(StudyAdapter):
    def __init__(self, runtime, arm, replicate, *, qualification=False):
        super().__init__(runtime, arm, replicate, qualification=qualification)
        self.native = WbAdapter("code", substrate="oc")
        self.spec, self.substrate = self.native.spec, self.native.substrate

    def _task_and_label(self, run_dir):
        parts = Path(run_dir).relative_to(self.store).parts
        if len(parts) != 3 or parts[1] != "model_output":
            raise ValueError("Unexpected coding deliverable path")
        return parts[0], parts[2]

    def domain_of(self, task_id):
        import tomllib

        return tomllib.loads((TASKS / task_id / "task.toml").read_text())["metadata"]["category"]

    def prompts_for(self, task_id):
        return workbuddy_prompts("code", domain=self.domain_of(task_id))

    def task_prompt(self, task_id):
        return (TASKS / task_id / "instruction.md").read_text()

    def task_files(self, task_id):
        # Upstream WorkBuddy gives the improver the instruction and metadata;
        # its leak_globs exclude the environment, hidden tests, and solution.
        dest = self.runtime.state / "code-public-inputs" / task_id
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("instruction.md", "task.toml"):
            shutil.copy2(TASKS / task_id / name, dest / name)
        return dest

    def base_harness(self):
        return self.native.base_harness()

    def validate_tree(self, tree):
        return self.native.validate_tree(tree)

    def execute_fresh(self, task_id, harness_dir, run_id, *, timeout=None):
        from .protocol import harness_record, verify_executed_harness

        label = run_id.replace("/", "__")
        out, trajectory = self._out_dir(task_id, label), self._traj_dir(task_id, label)
        harness = Path(harness_dir or self.base_harness())
        key = fingerprint(harness, executable_modes=True) + "-" + str(self.replicate)
        work = self.root / "work" / task_id / label / key
        home = work.parent / (key + "-home")
        image = json.loads((self.runtime.state / "code-images.json").read_text())[task_id]
        if not work.exists():
            container = command(["docker", "create", image]).stdout.strip()
            try:
                work.mkdir(parents=True)
                command(["docker", "cp", container + ":/seed-workspace/.", str(work)])
            finally:
                command(["docker", "rm", container])
            render_code(harness, home)
        prompt = (
            Environment(undefined=StrictUndefined)
            .from_string((home / "prompt.j2").read_text())
            .render(instruction=self.task_prompt(task_id))
        )
        result, directory = self.runtime.run_agent(
            work,
            prompt,
            entry_message=self.task_prompt(task_id),
            phase=self.phase,
            role="solver",
            task=task_id,
            arm=self.arm,
            run_id=run_id,
            adaptation_replicate=self.replicate,
            harness_fingerprint=fingerprint(harness, executable_modes=True),
            rendered_fingerprint=fingerprint(home, executable_modes=True),
            worker_image=image,
            rendered_home=home,
        )
        out.mkdir(parents=True, exist_ok=True)
        # Capture tracked edits plus untracked deliverables; ignored caches stay excluded.
        command(["git", "add", "-A"], cwd=work)
        patch = command(["git", "diff", "--cached", "--binary", "HEAD"], cwd=work).stdout
        (out / "delivered.patch").write_text(patch)
        trajectory.mkdir(parents=True, exist_ok=True)
        shutil.copy2(directory / "events.jsonl", trajectory / "events.jsonl")
        result.update(
            task=task_id,
            arm=self.arm,
            run_id=run_id,
            output_dir=str(out),
            output_fingerprint=fingerprint(out),
            execution_dir=str(directory),
        )
        save(trajectory / "showcase.json", result)
        write_json(directory / "file-inventory.json", inventory(out))
        executed = harness_record(harness, self.domain_of(task_id), self.runtime.manifest)
        verify_executed_harness(result, executed)
        write_json(directory / "harness-record.json", executed)
        shutil.copytree(harness, directory / "source-harness", dirs_exist_ok=True)
        result["trace"] = export_session(self.runtime, result["session"], result, directory)
        save(trajectory / "showcase.json", result)
        self.execution_records.append(result)
        return out

    def delivered(self, run_dir):
        path = Path(run_dir) / "delivered.patch"
        return path.is_file() and bool(path.read_text().strip())

    def deliverable_files(self, run_dir):
        return {"delivered.patch": (Path(run_dir) / "delivered.patch").read_text()}

    def deliverable_text(self, run_dir):
        return (Path(run_dir) / "delivered.patch").read_text()

    def deliverable_images(self, run_dir):
        return []

    def score(self, run_dir, task_id):
        if self.phase != "smoke" and not (self.runtime.state / "execute-complete.json").exists():
            raise CampaignStop("All transfer executions must finish before native grading")
        task, label = self._task_and_label(run_dir)
        dest = self.root / "judgments" / task / label
        receipt = dest / "judgment.json"
        if receipt.exists():
            return json.loads(receipt.read_text())
        dest.mkdir(parents=True, exist_ok=True)
        command(
            [
                str(self.runtime.state / "preflight-venv/bin/python"),
                "-m",
                "harness2.showcase.native_code_grade",
                "--task",
                task_id,
                "--patch",
                str(Path(run_dir) / "delivered.patch"),
                "--output",
                str(dest),
                "--state",
                str(self.runtime.state),
            ],
            cwd=ROOT,
        )
        return json.loads(receipt.read_text())
