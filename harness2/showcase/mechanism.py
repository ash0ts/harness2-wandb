"""A predeclared, post-comparison change-reversion check, outside primary results."""

from __future__ import annotations

import statistics

from harness2.core.schedule import Namespace

from .protocol import write_json
from .runtime import CampaignStop, fingerprint
from .v2 import V2Adapter, provenance, read
from .v2_report import choose_case, dataset


def run(runtime):
    if not (runtime.state / "evaluate-complete.json").exists():
        raise CampaignStop("The mechanism check requires the completed primary comparison")
    provenance(runtime, "mechanism")
    data = dataset(runtime)
    case = choose_case(data)
    selection_path = runtime.state / "mechanism-selection.json"
    if not case:
        value = {
            "complete": True,
            "executed": False,
            "reason": "No positive complete held-out task mean difference from baseline",
        }
        write_json(selection_path, value)
        return value
    rows = [r for r in data["rows"] if r["arm"] == case["arm"] and r["task"] == case["task"]]
    replicates = sorted(
        {r["adaptation_replicate"] for r in rows},
        key=lambda rep: (
            -statistics.mean(r["paired_change"] for r in rows if r["adaptation_replicate"] == rep),
            rep,
        ),
    )
    rep = replicates[0]
    domain = case["task"].split("/")[0]
    frozen = runtime.state / "frozen" / case["arm"] / f"rep{rep}" / domain
    info = read(frozen / "freeze.json")
    adapter = V2Adapter(runtime, case["arm"], rep)
    previous = adapter.base_harness()
    if info["chosen_label"] == "c2":
        ns = Namespace("jb", "oc", "seq", 2, runtime.manifest["seed"], case["arm"])
        previous = ns.task_dir(adapter.root / "runs", domain + "/task1") / "harnesses/c1"
    if not info["executable_change"] or fingerprint(previous) == fingerprint(frozen / "harness"):
        value = {
            "complete": True,
            "executed": False,
            "case": case,
            "replicate": rep,
            "reason": "The selected positive example has no executable change to revert; score variation is not attributed to harness improvement.",
        }
        write_json(selection_path, value)
        return value
    allocation = read(runtime.state / "allocation.json")
    projected = (
        2
        * (
            allocation["observed_max_session_usd"]["solver"]
            + allocation["observed_max_session_usd"]["judge"]
        )
        * 1.5
    )
    maximum = float(runtime.manifest["budget_buckets"]["mechanism"]["usd"])
    if projected > maximum:
        value = {
            "complete": True,
            "executed": False,
            "case": case,
            "reason": "Paired follow-up exceeds its reserved budget projection",
            "projected_usd": projected,
        }
        write_json(selection_path, value)
        return value
    selection = {
        "case": case,
        "adaptation_replicate": rep,
        "preceding_harness": str(previous),
        "frozen_harness": str(frozen / "harness"),
        "projected_usd": projected,
        "rule": runtime.manifest["selection_rule"],
        "replicate_rule": "Within the selected task/condition, largest paired replicate mean; ties by replicate ID.",
        "interpretation": "Post-selection mechanism check, excluded from primary results.",
    }
    if selection_path.exists() and read(selection_path).get("case") != case:
        raise CampaignStop("Mechanism selection drift")
    write_json(selection_path, selection)
    outputs = []
    for variant, harness in (("preceding", previous), ("frozen", frozen / "harness")):
        adapter.phase, adapter.repetition = "mechanism", 1
        output = adapter.execute(case["task"], harness, "mechanism/" + variant)
        marker = read(adapter._traj_dir(case["task"], output.name) / "showcase.json")
        outputs.append((variant, output, marker))
    results = []
    for variant, output, marker in outputs:
        path = runtime.state / "mechanism" / (variant + ".json")
        if path.exists():
            results.append(read(path))
            continue
        score = adapter.score(output, case["task"])
        _, evidence = adapter._details_candidates(case["task"], output.name)
        if not evidence.exists():
            evidence = evidence.with_name(evidence.name + ".judge_failed")
        value = {
            "variant": variant,
            "task": case["task"],
            "score": score,
            "output_dir": str(output),
            "session": marker["session"],
            "trace": marker["trace"],
            "harness_fingerprint": marker["identity"]["harness_fingerprint"],
            "judge_evidence": str(evidence) if evidence.exists() else None,
            "status": "judgment_unavailable"
            if score is None
            else "judged"
            if adapter.delivered(output)
            else "agent_no_output",
        }
        write_json(path, value)
        results.append(value)
    selection.update(complete=True, executed=True, results=results)
    write_json(selection_path, selection)
    return selection
