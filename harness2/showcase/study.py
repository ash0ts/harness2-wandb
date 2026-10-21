"""Version-four study validation, schedules, and read-only progress inspection."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

CONDITIONS = ("baseline", "harness2", "diagnostics")


def validate_manifest(manifest):
    if manifest["schema_version"] != 4 or manifest["mode"] != "seq" or manifest["k"] != 2:
        raise ValueError("Version four requires sequential recursion with k=2")
    if manifest["worker_concurrency"] != 2:
        raise ValueError("The qualified initial worker concurrency is two")
    if set(manifest["studies"]) != {"code", "jobbench"}:
        raise ValueError("Both predeclared studies are required")
    for study in manifest["studies"].values():
        for field in ("adaptation_trials", "heldout_per_group"):
            if type(study[field]) is not int or study[field] < 1:
                raise ValueError(f"{field} must be a positive integer")
        groups = len(study.get("categories", study.get("groups", [])))
        expected = groups * study["adaptation_trials"] * study["heldout_per_group"] * 3
        if study["final_executions"] != expected:
            raise ValueError("Final execution count does not match the independent design")
        for group in study.get("groups", []):
            if len(group["heldout"]) != study["heldout_per_group"]:
                raise ValueError("Incorrect held-out count")
            if group["adapt"] in group["heldout"]:
                raise ValueError("Adaptation task overlaps transfer")


def read_manifest(path):
    """No mkdir, credentials, Docker, network access, or ledger migration."""
    path = Path(path).resolve()
    value = json.loads(path.read_text())
    validate_manifest(value)
    return value, (path.parent / value["state_dir"]).resolve()


def evaluation_schedule(manifest, groups_by_study):
    rows = []
    for study, groups in sorted(groups_by_study.items()):
        for group in groups:
            for trial in range(1, manifest["studies"][study]["adaptation_trials"] + 1):
                for task in group["heldout"]:
                    for condition in CONDITIONS:
                        key = f"{study}/{group['group']}/trial{trial}/{task}/{condition}"
                        rows.append(
                            {
                                "key": key,
                                "study": study,
                                "group": group["group"],
                                "trial": trial,
                                "task": task,
                                "condition": condition,
                            }
                        )
    return sorted(
        rows,
        key=lambda row: hashlib.sha256(f"{manifest['seed']}:{row['key']}".encode()).hexdigest(),
    )


def status(manifest, state, study="all"):
    state = Path(state)
    value = {
        "campaign": manifest["campaign"],
        "study": study,
        "planned_final_executions": sum(
            s["final_executions"]
            for name, s in manifest["studies"].items()
            if study in ("all", name)
        ),
        "phase": "not_started",
        "observed_cost_usd": 0.0,
        "bounded_unknown_cost_usd": 0.0,
        "in_flight_reservations_usd": 0.0,
    }
    summary = state / "preflight/selection.json"
    if summary.exists():
        selection = json.loads(summary.read_text())
        value["fixture_gate"] = selection
        value["phase"] = "fixtures_ready" if selection["ready"] else "fixture_gate_failed"
    results = sorted(state.glob("preflight/*/result.json"))
    value["checked_code_fixtures"] = len(results)
    value["eligible_code_fixtures"] = sum(json.loads(p.read_text())["eligible"] for p in results)
    for phase in ("qualification", "adapt-complete", "execute-complete", "evaluate-complete"):
        if (state / f"{phase}.json").exists():
            value["phase"] = phase
    ledger = state / "gateway/budget.sqlite"
    if ledger.exists():
        with sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(r) for r in db.execute("SELECT * FROM calls")]
        value.update(
            observed_cost_usd=sum(r["spent"] or 0 for r in rows) / 1_000_000,
            bounded_unknown_cost_usd=sum(
                r["reserved"] for r in rows if r["status"] == "bounded_unknown"
            )
            / 1_000_000,
            in_flight_reservations_usd=sum(r["reserved"] for r in rows if r["status"] == "pending")
            / 1_000_000,
            unbounded_requests=sum(r["status"] == "unknown" for r in rows),
        )
    return value
