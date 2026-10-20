"""Host orchestration and disposable, network-isolated OpenCode workers."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


class CampaignStop(BaseException):
    """Infrastructure failures must bypass upstream proposal-repair retries."""


def load_manifest(path):
    path = Path(path).resolve()
    manifest = json.loads(path.read_text())
    if manifest.get("schema_version", 1) >= 2:
        if (
            manifest.get("k") != 2
            or manifest.get("mode") != "seq"
            or manifest.get("adaptation_replicates") not in (1, 2)
        ):
            raise ValueError(
                "Sequential campaigns require k=2 and one or two adaptation replicates"
            )
        if manifest.get("repetitions") not in (1, 2):
            raise ValueError("Pilot-v2 supports one or two evaluation repetitions")
        if any(f["adapt"] != "task1" for f in manifest["task_families"]) or any(
            not t.endswith("/task1") for t in manifest["qualification_tasks"]
        ):
            raise ValueError("Pilot-v2 adaptation and qualification are defined on task1")
    if manifest.get("schema_version", 1) >= 3:
        if (
            len(manifest["task_families"]) != 6
            or manifest["adaptation_replicates"] != 1
            or manifest["repetitions"] != 1
            or "mechanism" in manifest["budget_buckets"]
        ):
            raise ValueError(
                "Pilot-v3 requires six families, one adaptation/run, no mechanism phase"
            )
    state = (path.parent / manifest["state_dir"]).resolve()
    state.mkdir(parents=True, exist_ok=True)
    frozen = state / "manifest.json"
    text = json.dumps(manifest, sort_keys=True, indent=2)
    if frozen.exists() and frozen.read_text() != text:
        raise ValueError("Manifest changed: start a new campaign instead of mixing attempts")
    frozen.write_text(text)
    return manifest, state


def command(argv, **kwargs):
    return subprocess.run(argv, check=True, text=True, capture_output=True, **kwargs)


class Runtime:
    def __init__(self, manifest, state):
        self.manifest, self.state = manifest, Path(state)
        self.url = f"http://127.0.0.1:{manifest['gateway_port']}"
        secret_file = self.state / ".admin-token"
        if not secret_file.exists():
            secret_file.write_text(secrets.token_urlsafe(40))
            secret_file.chmod(0o600)
        self.admin = secret_file.read_text()
        self.headers = {"X-Showcase-Admin": self.admin}

    def start(self):
        name = self.manifest["network"]
        networks = command(["docker", "network", "ls", "--format", "{{.Name}}"]).stdout.splitlines()
        if name not in networks:
            command(
                [
                    "docker",
                    "network",
                    "create",
                    "--internal",
                    "--label",
                    "harness2.showcase=true",
                    "--subnet",
                    os.environ.get(
                        "SHOWCASE_SUBNET", self.manifest.get("subnet", "172.30.240.0/24")
                    ),
                    name,
                ]
            )
        info = json.loads(command(["docker", "network", "inspect", name]).stdout)[0]
        if not info["Internal"]:
            raise RuntimeError("Executor network must be internal")
        container = name + "-gateway"
        exists = (
            subprocess.run(["docker", "inspect", container], capture_output=True).returncode == 0
        )
        if not exists:
            env = dict(os.environ, SHOWCASE_ADMIN_TOKEN=self.admin)
            if not env.get("WANDB_API_KEY"):
                raise RuntimeError("WANDB_API_KEY must be configured outside the experiment")
            command(
                [
                    "docker",
                    "create",
                    "--name",
                    container,
                    "--label",
                    "harness2.showcase=true",
                    "--publish",
                    f"127.0.0.1:{self.manifest['gateway_port']}:8080",
                    "--env",
                    "WANDB_API_KEY",
                    "--env",
                    "SHOWCASE_ADMIN_TOKEN",
                    "--env",
                    "SHOWCASE_MANIFEST=/state/manifest.json",
                    "--env",
                    "SHOWCASE_STATE=/state/gateway",
                    "--mount",
                    f"type=bind,src={self.state},dst=/state",
                    "--mount",
                    f"type=bind,src={ROOT / 'harness2/showcase'},dst=/app/harness2/showcase,readonly",
                    "--workdir",
                    "/app",
                    "python:3.12.13-slim-bookworm",
                    "python",
                    "-m",
                    "harness2.showcase.gateway",
                ],
                env=env,
            )
            command(["docker", "network", "connect", "--alias", "gateway", name, container])
        command(["docker", "start", container])
        for _ in range(30):
            try:
                if httpx.get(self.url + "/health", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError("Gateway failed to become healthy")

    def register(self, identity):
        if self.manifest.get("schema_version", 1) >= 3:
            from .identity import identity_fields

            identity = identity_fields(identity, self.manifest)
        response = httpx.post(self.url + "/register", json=identity, headers=self.headers)
        response.raise_for_status()
        return response.json()["token"]

    def budget(self):
        response = httpx.get(self.url + "/budget", headers=self.headers)
        response.raise_for_status()
        return response.json()

    def calls(self, session):
        directory = self.state / "gateway/sessions" / session
        records = [
            json.loads(p.read_text())
            for p in sorted(directory.glob("*.json"))
            if p.name != "identity.json"
        ]
        return sorted(records, key=lambda record: (record["started"], record["call_id"]))

    def wait_calls(self, session):
        for _ in range(60):
            rows = self.calls(session)
            if rows and all("ended" in r for r in rows):
                for row in rows:
                    response = row.get("response") or {}
                    if response and response.get("model") != row["request"]["model"]:
                        raise CampaignStop("Returned model differs from frozen requested model")
                if any(c["status"] == "unknown" for c in self.budget()["calls"]):
                    raise CampaignStop("Missing provider usage: stop and reconcile the reservation")
                return rows
            time.sleep(0.5)
        raise RuntimeError("Provider calls have not finalized; do not rerun this attempt")

    def model_call(self, *, phase, role, messages, output_limit=1024, **identity):
        session = uuid.uuid4().hex
        identity.update(session=session, phase=phase, role=role)
        token = self.register(identity)
        model = self.manifest["models"]["judge" if role == "judge" else "actor"]
        response = httpx.post(
            self.url + "/v1/chat/completions",
            headers={"Authorization": "Bearer " + token},
            json={"model": model, "messages": messages, "max_tokens": output_limit},
            timeout=260,
        )
        response.raise_for_status()
        self.wait_calls(session)
        return session, response.json()

    def run_agent(self, work, prompt, *, phase, role, entry_message=None, **identity):
        work = Path(work).resolve()
        if any(c["status"] == "unknown" for c in self.budget()["calls"]):
            raise CampaignStop("Unreconciled usage blocks execution")
        session = uuid.uuid4().hex
        identity.update(session=session, phase=phase, role=role, campaign=self.manifest["campaign"])
        if self.manifest.get("schema_version", 1) >= 3:
            from .identity import identity_fields

            identity = identity_fields(identity, self.manifest)
        token = self.register(identity)
        control = self.state / "controls" / session
        control.mkdir(parents=True)
        config = {
            "$schema": "https://opencode.ai/config.json",
            "model": "showcase/actor",
            "provider": {
                "showcase": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Budgeted W&B Inference",
                    "options": {
                        "baseURL": "http://gateway:8080/v1",
                        "apiKey": token,
                        "includeUsage": True,
                    },
                    "models": {
                        "actor": {
                            "id": self.manifest["models"]["actor"],
                            "name": "Fixed pilot model",
                            "limit": {
                                "context": self.manifest.get(
                                    "model_context_tokens", self.manifest["max_input_tokens"]
                                ),
                                "output": self.manifest["max_output_tokens"],
                            },
                            "toolcall": True,
                        }
                    },
                }
            },
            "agent": {"build": {"steps": self.manifest["max_steps"]}},
            "permission": {"*": "allow", "external_directory": "deny"},
            "share": "disabled",
        }
        (control / "opencode.json").write_text(json.dumps(config))
        # This is a short-lived gateway token, not a provider credential.
        (control / "opencode.json").chmod(0o644)
        name = "h2-" + session
        argv = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            self.manifest["network"],
            "--label",
            "harness2.showcase=true",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=256",
            "--memory=4g",
            "--cpus=2",
            "--mount",
            f"type=bind,src={work},dst=/work",
            "--mount",
            f"type=bind,src={control},dst=/control,readonly",
            "--env",
            "OPENCODE_CONFIG=/control/opencode.json",
            "--env",
            "OPENCODE_DB=/home/runner/.local/share/opencode/opencode.db",
            "--env",
            f"OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX={self.manifest['max_output_tokens']}",
            self.manifest["image"],
            "run",
            prompt,
            "--dir",
            "/work",
            "--model",
            "showcase/actor",
            "--format",
            "json",
        ]
        # The macOS bind mount retains host IDs. Only this disposable public workspace
        # is writable by the unprivileged Linux worker.
        for path in [work, *work.rglob("*")]:
            if path.is_symlink():
                raise RuntimeError("Symlink in worker inputs")
            path.chmod(0o777 if path.is_dir() else 0o666)
        started = time.time()
        directory = self.state / "executions" / session
        directory.mkdir(parents=True)
        (directory / "identity.json").write_text(json.dumps(identity, indent=2))
        (directory / "entry-message.txt").write_text(entry_message or prompt)
        public_config = json.loads(json.dumps(config))
        public_config["provider"]["showcase"]["options"]["apiKey"] = "<session-token>"
        (directory / "runtime-settings.json").write_text(json.dumps(public_config, indent=2))
        timed_out = False
        try:
            with (
                (directory / "events.jsonl").open("w") as out,
                (directory / "stderr.txt").open("w") as err,
            ):
                result = subprocess.run(
                    argv,
                    stdout=out,
                    stderr=err,
                    text=True,
                    timeout=self.manifest["timeout_seconds"],
                )
                code = result.returncode
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "stop", "--time", "3", name], capture_output=True)
            timed_out, code = True, 124
        finally:
            # Never retain the gateway credential in exportable artifacts.
            (control / "opencode.json").unlink(missing_ok=True)
        rows = self.calls(session)
        if rows:
            rows = self.wait_calls(session)
        result = {
            "session": session,
            "identity": identity,
            "started": started,
            "ended": time.time(),
            "exit_code": code,
            "timed_out": timed_out,
            "provider_calls": len(rows),
            "budget_blocked": any((r.get("error") or {}).get("http_status") == 402 for r in rows),
        }
        (directory / "execution.json").write_text(json.dumps(result, indent=2))
        event_path = directory / "events.jsonl"
        events, audit, seen = [], [], set()
        for line in event_path.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                events.append(line)
                continue
            if event.get("type", "").startswith("audit_"):
                audit.append(event)
            else:
                events.append(line)
                if event.get("part", {}).get("callID"):
                    seen.add(event["part"]["callID"])
        for event in audit:
            part = event.get("part", {})
            if part.get("callID") and part["callID"] not in seen:
                events.append(json.dumps(event))
                seen.add(part["callID"])
        event_path.write_text("\n".join(events) + "\n")
        (directory / "opencode-audit.json").write_text(json.dumps(audit, indent=2))
        exits = [e for e in audit if e["type"] == "audit_exit"]
        result["child_exit_code"] = exits[-1]["child_exit_code"] if exits else None
        result["collection_status"] = (
            "complete" if any(e["type"] == "audit_complete" for e in audit) else "incomplete"
        )
        (directory / "execution.json").write_text(json.dumps(result, indent=2))
        if not any(e["type"] == "audit_session" for e in audit):
            raise CampaignStop(f"OpenCode audit missing for {session}; preserve this attempt")
        if (
            self.manifest.get("schema_version", 1) >= 2
            and result["collection_status"] != "complete"
        ):
            raise CampaignStop(
                f"Collector incomplete for {session}; child exit {result['child_exit_code']}"
            )
        return result, directory


def fingerprint(directory):
    digest = hashlib.sha256()
    for path in sorted(Path(directory).rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(directory)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()
