"""Durable, idempotent execution envelopes for independently repeated studies."""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
from pathlib import Path

from .runtime import CampaignStop, Runtime, fingerprint, prepare_worker_permissions
from .workbuddy_preflight import save

_WORKERS = threading.BoundedSemaphore(2)


def restore(source, destination):
    """Restore a disposable workspace exactly, including agent-deleted files."""
    if source.resolve() == destination.resolve():
        raise ValueError("Cannot restore a workspace onto itself")
    shutil.rmtree(destination)
    shutil.copytree(source, destination, symlinks=True)


class StudyRuntime(Runtime):
    def __init__(self, manifest, state, study="code"):
        Path(state).mkdir(parents=True, exist_ok=True)
        super().__init__({**manifest, "active_study": study}, state)
        self.study = study

    def register(self, identity):
        return super().register({**identity, "study": self.study})

    def run_agent(self, work, prompt, *, phase, role, **identity):
        work = Path(work)
        identity["study"] = self.study
        if phase == "smoke":
            from .qualification import attempt

            identity["qualification_attempt"] = attempt(self)
        key_fields = {
            k: v
            for k, v in identity.items()
            if k not in ("worker_image", "rendered_home", "entry_message")
        }
        key = hashlib.sha256(
            json.dumps(
                {"identity": key_fields, "prompt": prompt, "phase": phase, "role": role},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        journal = self.state / "operations" / (key + ".json")
        durable = self.state / "durable-work" / key
        if journal.exists():
            record = json.loads(journal.read_text())
            directory = Path(record["execution_dir"]) if record.get("execution_dir") else None
            if directory is None:
                matches = [
                    p.parent
                    for p in (self.state / "executions").glob("*/identity.json")
                    if json.loads(p.read_text()).get("operation_key") == key
                ]
                if len(matches) == 1:
                    directory = matches[0]
            if directory:
                from .collect_v4 import recover

                result = (
                    json.loads((directory / "execution.json").read_text())
                    if (directory / "execution.json").exists()
                    else {}
                )
                if result.get("collection_status") != "complete":
                    result = recover(directory, self)
                self.wait_calls(result["session"])
                restore(durable, work)
                save(journal, {**record, "status": "complete", "execution_dir": str(directory)})
                return result, directory
            raise CampaignStop(f"Execution state is uncertain for {key}; preserve and reconcile")
        if durable.exists():
            raise CampaignStop("Durable workspace exists without a launch record")
        prepare_worker_permissions(work, v4=True)
        shutil.copytree(work, durable, symlinks=True)
        queued = time.time()
        record = {
            "status": "queued",
            "work": str(durable),
            "input_fingerprint": fingerprint(durable),
            "identity": key_fields,
            "queued_at": queued,
        }
        save(journal, record)
        with _WORKERS:
            record.update(status="started", queue_seconds=time.time() - queued)
            save(journal, record)
            try:
                result, directory = super().run_agent(
                    durable, prompt, phase=phase, role=role, operation_key=key, **identity
                )
                result["queue_seconds"] = record["queue_seconds"]
                save(directory / "execution.json", result)
                save(journal, {**record, "status": "complete", "execution_dir": str(directory)})
            except BaseException:
                save(journal, {**record, "status": "interrupted"})
                raise
            finally:
                # Upstream may discard its temporary proposer workspace next. Retain
                # the durable original regardless of collection or export failure.
                restore(durable, work)
        return result, directory
