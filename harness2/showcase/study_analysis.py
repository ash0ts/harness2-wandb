"""Paired, task-weighted estimates; trials never become independent tasks."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import mean


def percentile(values, fraction):
    values = sorted(values)
    index = fraction * (len(values) - 1)
    lo, hi = math.floor(index), math.ceil(index)
    return values[lo] + (values[hi] - values[lo]) * (index - lo)


def paired_analysis(rows, *, trials, seed=20261022, draws=10000, equivalence_pp=5):
    """Rows include all planned (group, trial, task, condition) cells.

    Missing scores cannot become zero. An incomplete design has task summaries
    but no complete-design mean or confidence interval.
    """
    cells, groups = {}, defaultdict(set)
    for row in rows:
        key = row["group"], row["trial"], row["task"], row["condition"]
        if key in cells:
            raise ValueError(f"Duplicate evaluation cell: {key}")
        score = row.get("score")
        if score is not None and (type(score) not in (int, float) or not 0 <= score <= 1):
            raise ValueError("Native scores must be finite numbers in [0, 1], or missing")
        if not 1 <= row["trial"] <= trials:
            raise ValueError("Unexpected adaptation trial")
        if row["condition"] not in ("baseline", "harness2", "diagnostics"):
            raise ValueError("Unexpected condition")
        cells[key] = score
        groups[row["group"]].add(row["task"])
    results, missing = [], []
    for group, tasks in sorted(groups.items()):
        for task in sorted(tasks):
            item = {"group": group, "task": task}
            for condition in ("baseline", "harness2", "diagnostics"):
                scores = [
                    cells.get((group, trial, task, condition)) for trial in range(1, trials + 1)
                ]
                for trial, score in enumerate(scores, 1):
                    if score is None:
                        missing.append(
                            {"group": group, "task": task, "trial": trial, "condition": condition}
                        )
                item[condition] = mean(scores) * 100 if None not in scores else None
            for name, left, right in (
                ("diagnostics_minus_raw_pp", "diagnostics", "harness2"),
                ("raw_minus_baseline_pp", "harness2", "baseline"),
                ("diagnostics_minus_baseline_pp", "diagnostics", "baseline"),
            ):
                item[name] = (
                    item[left] - item[right]
                    if item[left] is not None and item[right] is not None
                    else None
                )
            results.append(item)
    complete = bool(results) and not missing
    output = {
        "complete": complete,
        "tasks": results,
        "missing": missing,
        "independent_groups": len(groups),
        "heldout_tasks": len(results),
        "adaptation_trials_per_group": trials,
        "planned_cells": len(results) * trials * 3,
        "observed_cells": sum(v is not None for v in cells.values()),
        "diagnostics_minus_raw_pp": None,
        "interval_95_pp": None,
    }
    if not complete:
        return output
    differences = [r["diagnostics_minus_raw_pp"] for r in results]
    output.update(
        diagnostics_minus_raw_pp=mean(differences),
        raw_minus_baseline_pp=mean(r["raw_minus_baseline_pp"] for r in results),
        diagnostics_minus_baseline_pp=mean(r["diagnostics_minus_baseline_pp"] for r in results),
        wins=sum(d > 1e-9 for d in differences),
        ties=sum(abs(d) <= 1e-9 for d in differences),
        losses=sum(d < -1e-9 for d in differences),
    )
    rng, samples = random.Random(seed), []
    names = sorted(groups)
    for _ in range(draws):
        values = []
        for group in rng.choices(names, k=len(names)):
            # A trial's shared harness is sampled once for all tasks in that group.
            selected_trials = rng.choices(range(1, trials + 1), k=trials)
            tasks = sorted(groups[group])
            for task in rng.choices(tasks, k=len(tasks)):
                values.append(
                    mean(
                        cells[group, t, task, "diagnostics"] - cells[group, t, task, "harness2"]
                        for t in selected_trials
                    )
                    * 100
                )
        samples.append(mean(values))
    low, high = percentile(samples, 0.025), percentile(samples, 0.975)
    output.update(
        interval_95_pp=[low, high],
        bootstrap_seed=seed,
        bootstrap_draws=draws,
        supported_gain=low > 0,
        supported_loss=high < 0,
        practically_equivalent=low >= -equivalence_pp and high <= equivalence_pp,
        equivalence_band_pp=[-equivalence_pp, equivalence_pp],
    )
    output["unresolved"] = not (
        output["supported_gain"] or output["supported_loss"] or output["practically_equivalent"]
    )
    return output
