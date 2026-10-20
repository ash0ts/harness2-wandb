"""Render the reconciled six-job comparison with the worker's pinned Matplotlib."""

import argparse
import json
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("data")
parser.add_argument("output")
args = parser.parse_args()
data = json.load(open(args.data))
rows = data["comparison"]
fig, ax = plt.subplots(figsize=(10.8, 5.6), constrained_layout=True)
colors = {"baseline": "#70777d", "harness2": "#1768ac", "diagnostics": "#c25816"}
shapes = {"baseline": "^", "harness2": "s", "diagnostics": "o"}
labels = {"baseline": "Original", "harness2": "Raw feedback", "diagnostics": "Diagnostics"}
offsets = {"baseline": 0.16, "harness2": 0, "diagnostics": -0.16}
observed = [100 * row[arm] for row in rows for arm in colors if row[arm] is not None]
lower = 20 * math.floor(min([0, *observed]) / 20)
upper = 20 * math.ceil(max([100, *observed]) / 20)
for y, row in enumerate(rows):
    if row["paired"]:
        ax.plot(
            [100 * row["harness2"], 100 * row["diagnostics"]],
            [y + offsets["harness2"], y + offsets["diagnostics"]],
            color="#b6bdc3",
            lw=2,
            zorder=1,
        )
    for arm in colors:
        if row[arm] is not None:
            ax.scatter(
                100 * row[arm],
                y + offsets[arm],
                c=colors[arm],
                marker=shapes[arm],
                s=65,
                zorder=3,
            )
    if not row["paired"]:
        ax.text(upper + 4, y, "Unavailable", va="center", fontsize=10)
    else:
        ax.text(upper + 4, y, f"{row['difference_pp']:+.2f} pp", va="center", fontsize=10)
for arm in colors:
    ax.scatter([], [], c=colors[arm], marker=shapes[arm], s=65, label=labels[arm])
ax.set_yticks(range(len(rows)), [r["job"] for r in rows], fontsize=11)
ax.invert_yaxis()
ax.set_xlim(lower - 2, upper + 22)
ax.set_xticks(range(lower, upper + 1, 20))
ax.set_xlabel("Native held-out quality (%)", fontsize=11)
ax.grid(axis="x", color="#e6e8eb")
ax.set_axisbelow(True)
for side in ("top", "right", "left"):
    ax.spines[side].set_visible(False)
ax.legend(loc="lower center", bbox_to_anchor=(0.45, 1.03), ncol=3, frameon=False)
ax.set_title(
    "Layout validation — synthetic values"
    if data.get("synthetic_layout_fixture")
    else "Do Agent Lens summaries help Harness²?",
    loc="left",
    pad=47,
    fontsize=16,
)
fig.text(
    0.02,
    -0.035,
    "Six jobs · One execution per setup · Right labels: diagnostics minus raw feedback",
    fontsize=10,
)
fig.savefig(args.output + ".png", dpi=180, bbox_inches="tight", facecolor="white")
fig.savefig(args.output + ".svg", bbox_inches="tight", facecolor="white")
