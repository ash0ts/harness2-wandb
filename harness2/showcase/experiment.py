"""Use upstream refinement, prompts, selection, and grading with isolated I/O."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from harness2 import config
from harness2.benchmarks.jobbench import (
    JobBenchAdapter,
    _render_ctx,
    render_opencode_tree,
)
from harness2.core.proposer import Proposer, _opencode_stdout
from harness2.substrate.opencode import render_events

from .runtime import CampaignStop, fingerprint
from .telemetry import diagnostics, export_session

_INPUT_LOCK = threading.Lock()


def session_cost(runtime, session):
    rows = [c for c in runtime.budget()["calls"] if c["session"] == session]
    if any(c["spent"] is None for c in rows):
        raise RuntimeError("Session cost is unresolved")
    return sum(c["spent"] for c in rows) / 1_000_000


class ExperimentAdapter(JobBenchAdapter):
    def __init__(self, runtime, arm):
        config.PROPOSER_MODEL_BY_CHANNEL["opencode"] = "showcase/actor"
        config.JB_JUDGE_PYTHON = Path(sys.executable)
        config.JB_JUDGE_MODEL = runtime.manifest["models"]["judge"]
        self.runtime, self.arm = runtime, arm
        self.phase, self.repetition = "adapt", 0
        super().__init__(substrate="oc", store_root=runtime.state / "arms" / arm / "dataset")
        self.execution_records = []

    def task_files(self, task_id):
        with _INPUT_LOCK:
            return self._materialize_task_files(task_id)

    def _materialize_task_files(self, task_id):
        # An explicit mirror is the entire solver-visible input boundary.
        source = self._dataset_task(task_id)
        dest = self.runtime.state / "task-inputs" / task_id
        if not dest.exists():
            shutil.copytree(source / "task_folder", dest)
            references = source / "files_required_to_search"
            if references.exists():
                shutil.copytree(references, dest / "references")
        for part, local in (
            (source / "task_folder", dest),
            (source / "files_required_to_search", dest / "references"),
        ):
            if part.exists():
                for p in part.rglob("*"):
                    if p.is_file() and (local / p.relative_to(part)).read_bytes() != p.read_bytes():
                        raise CampaignStop("Task input mirror changed")
        for path in dest.rglob("*"):
            if path.is_symlink() or path.name == "RUBRICS.json":
                raise RuntimeError("Unsafe task input")
        return dest

    def task_prompt(self, task_id):
        return super().task_prompt(task_id) + (
            "\n\nExternal reference snapshots are provided in task_folder/references/ when "
            "the task requires external sources. Use these pinned files; internet access "
            "is unavailable. Write requested deliverables in /work/output/.\n"
        )

    def artifacts_intact(self, run_dir):
        task_id, label = self._task_and_label(run_dir)
        marker = self._traj_dir(task_id, label) / "showcase.json"
        if not marker.exists():
            return False
        saved = json.loads(marker.read_text())
        return saved.get("trace", {}).get("verified", False) and saved.get(
            "output_fingerprint"
        ) == fingerprint(run_dir)

    def execute(self, task_id, harness_dir, run_id, *, timeout=None):
        label = run_id.replace("/", "__")
        out = self._out_dir(task_id, label)
        record_dir = self._traj_dir(task_id, label)
        marker = record_dir / "showcase.json"
        if marker.exists():
            saved = json.loads(marker.read_text())
            if not saved.get("trace", {}).get("verified"):
                raise RuntimeError("Prior execution has unverified tracing; do not re-execute")
            if saved["output_fingerprint"] != fingerprint(out):
                raise RuntimeError("Prior output changed")
            return out
        if out.exists() or record_dir.exists():
            raise RuntimeError("Incomplete attempt exists; preserve it and reconcile before retry")
        harness = Path(harness_dir or self.base_harness())
        session_key = uuid.uuid4().hex
        work = self.runtime.state / "work" / session_key
        render_opencode_tree(harness, work, _render_ctx(self.domain_of(task_id)))
        shutil.copytree(self.task_files(task_id), work / "task_folder")
        (work / "output").mkdir()
        result, directory = self.runtime.run_agent(
            work,
            self.task_prompt(task_id),
            phase=self.phase,
            role="solver",
            task=task_id,
            arm=self.arm,
            run_id=run_id,
            repetition=self.repetition,
            harness_fingerprint=fingerprint(harness),
        )
        record_dir.mkdir(parents=True)
        shutil.copy(directory / "events.jsonl", record_dir / "events.jsonl")
        out.mkdir(parents=True)
        for path in (work / "output").rglob("*"):
            if path.is_symlink():
                raise RuntimeError("Refusing a symlink in generated deliverables")
        shutil.copytree(work / "output", out, dirs_exist_ok=True)
        result.update(
            task=task_id,
            arm=self.arm,
            run_id=run_id,
            output_dir=str(out),
            output_fingerprint=fingerprint(out),
            execution_dir=str(directory),
        )
        marker.write_text(json.dumps(result, indent=2))
        try:
            trace = export_session(self.runtime, result["session"], result, directory)
        except Exception as exc:
            raise CampaignStop(f"Trace persistence failed for {result['session']}: {exc}") from exc
        result["trace"] = trace
        marker.write_text(json.dumps(result, indent=2))
        stored = json.loads((directory / "stored-spans.json").read_text())
        dossier = diagnostics(stored, self.delivered(out))
        (record_dir / "diagnostics.json").write_text(json.dumps(dossier, indent=2))
        self.execution_records.append(result)
        print(
            f"completed {self.arm} {task_id} {run_id} ({result['provider_calls']} calls)",
            flush=True,
        )
        return out

    def trajectory(self, run_dir, *, obs_trunc):
        task_id, label = self._task_and_label(run_dir)
        directory = self._traj_dir(task_id, label)
        original = render_events(
            directory / "events.jsonl", obs_trunc=obs_trunc, max_chars=2_000_000
        )
        if self.arm == "diagnostics":
            dossier = directory / "diagnostics.json"
            original += "\n\n[STORED EXECUTION DIAGNOSTICS]\n" + dossier.read_text()
        return original

    def _run_judge(self, task_id, label, run_dir):
        session = uuid.uuid4().hex
        token = self.runtime.register(
            {
                "session": session,
                "phase": "smoke" if self.phase == "smoke" else "judge",
                "role": "judge",
                "task": task_id,
                "arm": self.arm,
                "run_id": label,
                "campaign": self.runtime.manifest["campaign"],
            }
        )
        _, own = self._details_candidates(task_id, label)
        own.parent.mkdir(parents=True, exist_ok=True)
        directory = self.runtime.state / "executions" / session
        directory.mkdir(parents=True)
        own.parent.chmod(0o777)
        name = "h2-judge-" + session
        cmd = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--label",
            "harness2.showcase=true",
            "--network",
            self.runtime.manifest["network"],
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--memory=2g",
            "--cpus=2",
            "--mount",
            f"type=bind,src={config.jb_repo() / 'eval/judge.py'},dst=/judge.py,readonly",
            "--mount",
            f"type=bind,src={run_dir},dst=/output,readonly",
            "--mount",
            f"type=bind,src={self._dataset_task(task_id) / 'RUBRICS.json'},dst=/RUBRICS.json,readonly",
            "--mount",
            f"type=bind,src={own.parent},dst=/results",
            "--env",
            "JUDGE_API_KEY",
            "--env",
            "JUDGE_MAX_COMPLETION_TOKENS",
            "--env",
            "JUDGE_REASONING_EFFORT",
            self.runtime.manifest["image"] + "-judge",
            "--output-dir",
            "/output",
            "--rubrics-file",
            "/RUBRICS.json",
            "--details-file",
            "/results/" + own.name,
            "--judge-model",
            self.runtime.manifest["models"]["judge"],
            "--api-base",
            "http://gateway:8080/v1",
            "--max-workers",
            "2",
            "--max-retries",
            "1",
            "--timeout-per-rubric",
            "240",
            "--evaluated-model",
            label,
        ]
        env = dict(
            os.environ,
            JUDGE_API_KEY=token,
            JUDGE_MAX_COMPLETION_TOKENS="16384",
            JUDGE_REASONING_EFFORT="high",
        )
        try:
            with (directory / "judge.log").open("w") as log:
                result = subprocess.run(cmd, env=env, stdout=log, stderr=log, timeout=1800)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "stop", "--time", "3", name], capture_output=True)
            raise CampaignStop(f"Judge timed out for {session}; preserve the attempt")
        if self.runtime.calls(session):
            self.runtime.wait_calls(session)
            export_session(self.runtime, session, directory=directory)
        return result.returncode == 0


class ExperimentProposer(Proposer):
    def __init__(self, runtime, adapter, task_id, audit_dir):
        super().__init__(adapter.spec.channels["proposer"], audit_dir=audit_dir)
        self.runtime, self.adapter, self.task_id = runtime, adapter, task_id
        self.local = threading.local()

    def call(self, mission, context_dir, label, **kwargs):
        self.local.label = label
        return super().call(mission, context_dir, label, **kwargs)

    def _opencode(self, cwd, mission, system, wall):
        (cwd / "MISSION.md").write_text((system + "\n\n" if system else "") + mission)
        (cwd / "output").mkdir(exist_ok=True)
        result, directory = self.runtime.run_agent(
            cwd,
            "Read MISSION.md first. Do what it asks. Reply with the requested output as "
            "your final message AND save it verbatim to output/answer.md.",
            phase="adapt",
            role="reflection" if self.local.label.startswith("experience.") else "improver",
            task=self.task_id,
            arm=self.adapter.arm,
            stage=self.local.label,
            input_fingerprint=fingerprint(cwd),
            harness_fingerprint=fingerprint(cwd / "context/harness"),
        )
        try:
            export_session(self.runtime, result["session"], result, directory)
        except Exception as exc:
            raise CampaignStop(f"Trace persistence failed for {result['session']}: {exc}") from exc
        cost = session_cost(self.runtime, result["session"])
        answer = cwd / "output/answer.md"
        if answer.exists() and answer.read_text().strip():
            return answer.read_text(), cost, ""
        text, error = _opencode_stdout((directory / "events.jsonl").read_text())
        return text, cost, error or f"exit {result['exit_code']}"
