"""Static paired figures from the canonical result JSON; run in the pinned image."""

import argparse
import hashlib
import json
from pathlib import Path


def figures(source, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "svg.hashsalt": "study-v4"})
    data = json.loads(source.read_text())
    output.mkdir(parents=True, exist_ok=True)
    records = {}
    for study, title in (("code", "Repository coding"), ("jobbench", "JobBench replication")):
        analysis = data["analysis"][study]
        rows = analysis["tasks"]
        fig, (scores, differences) = plt.subplots(
            1,
            2,
            figsize=(15, max(4, len(rows) * 0.34 + 2.4)),
            gridspec_kw={"width_ratios": [2.0, 1.0]},
            sharey=True,
        )
        series = (
            ("baseline", "Original", "#757575", "o"),
            ("harness2", "Raw feedback", "#1767A6", "s"),
            ("diagnostics", "Diagnostics", "#B65010", "D"),
        )
        for y, row in enumerate(rows):
            if row["harness2"] is not None and row["diagnostics"] is not None:
                scores.plot(
                    [row["harness2"], row["diagnostics"]],
                    [y, y],
                    color="#9B9B9B",
                    linewidth=1,
                    zorder=1,
                )
            if row["diagnostics_minus_raw_pp"] is not None:
                delta = row["diagnostics_minus_raw_pp"]
                differences.plot([0, delta], [y, y], color="#555555", linewidth=1)
                differences.scatter(delta, y, color="#222222", marker="D", s=28, zorder=3)
                differences.annotate(
                    f"{delta:+.1f}",
                    (delta, y),
                    xytext=(5 if delta >= 0 else -5, 0),
                    textcoords="offset points",
                    va="center",
                    ha="left" if delta >= 0 else "right",
                    fontsize=9,
                )
            else:
                differences.text(0, y, "missing", ha="center", va="center", color="#555555")
        for key, label, color, marker in series:
            observed = [(y, row[key]) for y, row in enumerate(rows) if row[key] is not None]
            scores.scatter(
                [v for _, v in observed],
                [y for y, _ in observed],
                label=label,
                color=color,
                marker=marker,
                s=30,
                edgecolors="white",
                linewidths=0.45,
                zorder=3,
            )
        scores.set_yticks(range(len(rows)), [r["task"].replace("_", " ") for r in rows], fontsize=9)
        scores.set_ylim(len(rows) - 0.4, -0.6)
        scores.set_xlim(-2, 102)
        scores.set_xlabel("Native score (%) · mean across adaptation trials")
        scores.legend(loc="lower left", bbox_to_anchor=(0, 1.01), ncol=3, frameon=False)
        differences.axvspan(-5, 5, color="#F0F0F0", zorder=0)
        differences.axvline(0, color="#888888", linewidth=0.7)
        bound = max(
            15, max((abs(r["diagnostics_minus_raw_pp"] or 0) for r in rows), default=0) * 1.25
        )
        differences.set_xlim(-bound, bound)
        differences.set_xlabel("Diagnostics − raw (percentage points)")
        differences.tick_params(axis="y", left=False)
        for axis in (scores, differences):
            axis.grid(axis="x", color="#E5E5E5", linewidth=0.5)
            axis.set_axisbelow(True)
            for edge in ("top", "right", "left"):
                axis.spines[edge].set_visible(False)
        subtitle = f"{analysis['heldout_tasks']} tasks · {analysis['independent_groups']} adaptation groups · {analysis['adaptation_trials_per_group']} fresh trials per group"
        if analysis["complete"]:
            low, high = analysis["interval_95_pp"]
            subtitle += f"\nEqual-task mean difference {analysis['diagnostics_minus_raw_pp']:+.2f} pp; 95% paired hierarchical bootstrap interval [{low:+.2f}, {high:+.2f}]"
        else:
            subtitle += f"\nIncomplete: {analysis['observed_cells']}/{analysis['planned_cells']} scores available; no complete-design effect estimate"
        fig.suptitle(title + "\n" + subtitle, fontsize=12, ha="left", x=0.02)
        fig.text(
            0.02,
            0.01,
            "Shading marks the predeclared ±5-point equivalence band. Repeated trials are not additional independent tasks. Missing is not zero.",
            fontsize=9,
        )
        height = fig.get_figheight()
        fig.subplots_adjust(left=0.31, right=0.98, bottom=0.12, top=1 - 1.05 / height, wspace=0.08)
        fig.savefig(output / f"{study}-paired.png", dpi=180)
        fig.savefig(output / f"{study}-paired.svg", metadata={"Date": None})
        plt.close(fig)
        records[study] = rows
    (output / "chart-data.json").write_text(json.dumps(records, indent=2))
    (output / "figure-contract.json").write_text(
        json.dumps(
            {
                "question": "Does structured Agent Lens feedback improve the frozen harness's held-out quality?",
                "source": "results.json",
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "metric": "Native score times 100; average paired trials within task, then equal task weight",
                "missingness": "Unavailable judgments stay missing; incomplete designs have no aggregate interval",
                "visual": "Paired dots show native scores; adjacent diamonds show task differences; shape and text duplicate color",
                "text_alternative": "The report task tables and results.csv contain every plotted value",
                "limitations": "Exploratory task selection; reconstructed repositories; trials share an adapted harness across tasks",
                "renderer": "matplotlib==3.10.7 in the pinned experiment image",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    figures(args.source, args.output)
