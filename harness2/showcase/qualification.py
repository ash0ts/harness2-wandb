"""Preserve failed infrastructure qualifications before testing a repaired runtime."""

from __future__ import annotations

import hashlib
import json
import shutil

from .runtime import ROOT, CampaignStop


def attempt(runtime):
    path = runtime.state / "qualification-attempt.json"
    return json.loads(path.read_text())["attempt"] if path.exists() else 1


def area(runtime, name, *, number=None):
    number = attempt(runtime) if number is None else number
    root = runtime.state / name
    return root if number == 1 else root / f"attempt-{number}"


def restart(runtime, reason):
    """A repair can be qualified anew; completed runs and the ledger are immutable."""
    stopped = runtime.state / "stopped.json"
    if not reason.strip() or not stopped.exists():
        raise CampaignStop("Requalification requires a recorded stop and repair reason")
    previous = json.loads(stopped.read_text())
    if previous["phase"] != "smoke" or (runtime.state / "qualification.json").exists():
        raise CampaignStop("Only a failed qualification can be restarted")
    budget = runtime.budget()
    if budget["unresolved"] or budget["reserved_usd"]:
        raise CampaignStop("Unresolved or in-flight usage blocks requalification")
    for path in (runtime.state / "gateway/sessions").glob("*/identity.json"):
        if json.loads(path.read_text())["phase"] != "smoke":
            raise CampaignStop("Cannot repair qualification after the main campaign starts")
    provenance = runtime.state / "provenance/qualification.json"
    old = json.loads(provenance.read_text())
    changed = [
        relative
        for relative, digest in old["execution_engine"].items()
        if not (ROOT / relative).exists()
        or hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != digest
    ]
    if not changed:
        raise CampaignStop("Requalification requires an execution-engine repair")
    number = attempt(runtime)
    dest = runtime.state / "qualification-attempts" / str(number)
    dest.mkdir(parents=True, exist_ok=False)
    for name in ("stopped.json", "tool-qualification.json", "cost-projection.json"):
        source = runtime.state / name
        if source.exists():
            shutil.copy2(source, dest / name)
    shutil.copy2(provenance, dest / "provenance.json")
    shutil.copytree(runtime.state / "provenance/qualification-source", dest / "source")
    record = {
        "attempt": number + 1,
        "previous_attempt": number,
        "repair_reason": reason,
        "changed_engine_files": changed,
        "previous_stop": str(dest / "stopped.json"),
        "spent_before_usd": budget["spent_usd"],
        "qualification_cap_usd": runtime.manifest["qualification_budget_usd"],
    }
    (runtime.state / "qualification-attempt.json").write_text(json.dumps(record, indent=2) + "\n")
    # Prior workspaces, decisions, freezes, traces, and charges stay at their original paths.
    stopped.unlink()
    return record
