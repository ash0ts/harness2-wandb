"""Preserve a failed final composition and apply the declared original-harness fallback."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness2.showcase.protocol import write_json
from harness2.showcase.runtime import ROOT, Runtime, command, load_manifest
from harness2.showcase.telemetry import export_session
from harness2.showcase.v2 import V2Adapter, freeze
from harness2.showcase.v2_report import interrupted_attempt, read


def preserve_adaptation_provenance(runtime):
    """Keep each reporting-source snapshot while enforcing the same qualified engine."""
    state = runtime.state
    old = state / "provenance/adapt.json"
    if not old.exists():
        return None
    prior = read(old)
    report_only = {"report.py", "v2_report.py", "v3_report.py", "audit.py", "mechanism.py"}
    engine_files = [
        p for p in (ROOT / "harness2/showcase").glob("*.py") if p.name not in report_only
    ] + [ROOT / "showcase" / name for name in ("worker.py", "Dockerfile", "Dockerfile.judge")]
    engine = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in engine_files
    }
    if engine != prior["execution_engine"] or engine != read(
        state / "provenance/frozen-engine.json"
    ):
        raise ValueError("Execution-engine drift cannot be resumed within this campaign")
    images = {
        name: json.loads(command(["docker", "image", "inspect", name]).stdout)[0]["Id"]
        for name in prior["images"]
    }
    if (
        images != prior["images"]
        or images != read(state / "provenance/qualification.json")["images"]
    ):
        raise ValueError("Image drift cannot be resumed within this campaign")
    number = 1
    while (state / f"provenance/adapt-attempt-{number}.json").exists():
        number += 1
    preserved = state / f"provenance/adapt-attempt-{number}.json"
    snapshot = state / f"provenance/adapt-attempt-{number}-source"
    if snapshot.exists():
        raise ValueError("An unmatched adaptation source snapshot already exists")
    shutil.copy2(old, preserved)
    shutil.copytree(state / "provenance/adapt-source", snapshot)
    for relative, sha in prior["source_hashes"].items():
        if hashlib.sha256((snapshot / relative).read_bytes()).hexdigest() != sha:
            raise ValueError("The preserved source snapshot does not match its manifest")
    receipt = state / f"provenance/adapt-resumption-{number}.json"
    write_json(
        receipt,
        {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "reason": "Resume unstarted work after declared failed-adaptation fallback; "
            "reporting and capture code have separate source snapshots.",
            "previous_snapshot": str(preserved.relative_to(state)),
            "previous_source": str(snapshot.relative_to(state)),
            "execution_engine_unchanged": True,
            "images_unchanged": True,
            "current_commit": command(["git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip(),
        },
    )
    old.unlink()
    shutil.rmtree(state / "provenance/adapt-source")
    return str(receipt.relative_to(state))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--session", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{32}", args.session):
        raise ValueError("Use the recorded 32-character execution session ID")
    manifest, state = load_manifest(args.manifest)
    runtime = Runtime(manifest, state)
    session = args.session
    facts = interrupted_attempt(runtime, session)
    identity = read(state / "gateway/sessions" / session / "identity.json")
    stopped = state / "stopped.json"
    if not stopped.exists() or session not in read(stopped)["reason"]:
        raise ValueError("Wait for the campaign to stop on this exact interrupted session")
    budget = runtime.budget()
    if budget["unresolved"] or budget["reserved_usd"]:
        raise ValueError("Pending or missing usage blocks recovery")
    if any(
        read(p).get("phase") in ("evaluate", "judge")
        for p in (state / "gateway/sessions").glob("*/identity.json")
    ):
        raise ValueError("Fallback must be chosen before any held-out execution or grading")
    if identity.get("stage") != "c2.a1" or identity.get("proposal_attempt") != 2:
        raise ValueError("This recovery handles an exhausted final-composition retry only")
    for relative, expected in read(state / "provenance/frozen-engine.json").items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("The qualified execution engine changed")

    decisions = []
    for path in (state / "executions").glob("*/decision.json"):
        other = read(state / "gateway/sessions" / path.parent.name / "identity.json")
        if all(
            other.get(key) == identity.get(key) for key in ("task", "arm", "adaptation_replicate")
        ):
            decisions.append((other, read(path)))
    if any(
        d.get("accepted") and i.get("stage", "").startswith(("c1.", "c2.")) for i, d in decisions
    ):
        raise ValueError(
            "A valid composed harness exists; original-harness fallback is inapplicable"
        )
    labels = ("dom1", "inv1", "c1", "dom2", "inv2", "c2")
    stages = [
        {
            "label": label,
            "fell_back": not any(
                d.get("accepted") and i.get("stage", "").startswith(label + ".")
                for i, d in decisions
            ),
            "source": "Saved completion decisions; interrupted final attempt retained separately",
        }
        for label in labels
    ]
    adapter = V2Adapter(runtime, identity["arm"], identity["adaptation_replicate"])
    result_path = adapter.root / "results" / (identity["task"].replace("/", "__") + ".json")
    if result_path.exists() and read(result_path).get("interrupted_session") != session:
        raise ValueError("An adaptation result already exists; do not replace it")
    record_path = state / "provenance" / f"interrupted-{session}.json"
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "action": "baseline_fallback_after_interrupted_adaptation",
        "facts": facts,
        "reason": "Both composition stages lack a valid completion decision. The final retry "
        "timed out while delegated agents awaited interactive questions. No harness from the "
        "interrupted attempt is adopted.",
        "omitted_after_interruption": ["c2 probe", "final selection", "reflection"],
        "engine_changed": False,
        "applied_before_any_heldout_execution": True,
        "additional_inference_requests": 0,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "prior_stop": read(stopped),
    }
    write_json(record_path, record)

    class PartialRuntime:
        def __getattr__(self, name):
            return getattr(runtime, name)

        def calls(self, sid):
            return [
                {
                    **call,
                    "identity": {
                        **call["identity"],
                        "stage_name": "Compose (timed out; partial trace)",
                        "collection_status": "incomplete",
                        "trace_coverage": "partial",
                    },
                }
                for call in runtime.calls(sid)
            ]

    directory = state / "executions" / session
    trace = export_session(PartialRuntime(), session, read(directory / "execution.json"), directory)
    trace.update(
        coverage="partial",
        verification_scope=facts["scope"],
        interruption_record=str(record_path.relative_to(state)),
    )
    write_json(directory / "trace.json", trace)
    write_json(
        result_path,
        {
            "interrupted": True,
            "interrupted_session": session,
            "modes": {"seq": {"stages": stages}},
            "selections": {
                "seq": {
                    "ok": False,
                    "chosen_label": None,
                    "reason": "Original-harness fallback under the declared failed-adaptation rule",
                }
            },
            "recovery_record": str(record_path),
        },
    )
    freeze(
        runtime,
        adapter,
        identity["domain"],
        adapter.base_harness(),
        str(result_path),
        None,
    )
    record["resumption"] = preserve_adaptation_provenance(runtime)
    write_json(record_path, record)
    stopped.rename(state / "provenance" / f"stop-before-recovery-{session}.json")
    print(json.dumps({"session": session, "fallback": "baseline", "trace": trace["url"]}))


if __name__ == "__main__":
    main()
