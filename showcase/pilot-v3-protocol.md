# Six-job diagnostics comparison

## Question

Does adding deterministic summaries of Agent Lens execution records improve the
quality of a frozen Harness² harness on a fresh JobBench task, compared with raw
execution evidence alone? Model weights remain fixed.

The [manifest](pilot-v3.json) is the executable research contract. It inherits
the upstream, data, executor, model, SDK, role-limit, and offline-reference pins
from pilot-v2. Pilot-v1 and pilot-v2 remain separate published records.

## Design

The six task families each contribute one adaptation task and one held-out task.
Each adaptive condition runs once, with two upstream sequential refinement
rounds. Both start from byte-identical copies of the same two baseline execution
records; their later candidates, probes, and experience libraries are isolated.
Only the diagnostics condition receives the bounded summary directory. Complete
raw requests, tools, and stored spans remain available to both.

Freeze all adopted harnesses before transfer. Run original, raw-feedback, and
diagnostics harnesses once per held-out task, in the manifest's seeded interleaved
schedule: eighteen final executions. Keep rejected proposals and failed
adaptations. A missing valid adoption deploys the previous valid adoption, or
baseline when no adoption exists. All transfer executions finish before native
judging. Judges receive opaque identifiers, outputs, and native rubrics; neither
improver nor solver can access grades, rubrics, or another condition's workspace.

## Measures and evidence

The primary endpoint is the native earned-weight/possible-weight score. Preserve
both native values. For each task compute diagnostics minus raw feedback in
percentage points, then take the equal-weight mean over the six complete task
pairs. Report the paired denominator and wins/ties/losses. Missing judgments stay
missing; no-output results use the native zero. Do not present a complete answer
unless all eighteen results are accounted for and all six contrasts are judged.
One execution per condition cannot estimate run-to-run variability.

Secondary observations are readable-file coverage, explicit tool failures per
tool invocation, repeated error signatures, request limits, wall-clock duration,
and inference cost. Count an identical error signature by tool name, normalized
arguments, error type, and exact status message. Report raw counts and
denominators, retain supporting span IDs, and do not infer recovery intent.
Report adaptation costs separately, counting shared baselines once.

Every record joins campaign, task, condition, stage, round, attempt, session,
source fingerprint, and rendered fingerprint. The trace, saved decision, actual
diff, next execution, generated file, judge criterion, and score form the
evidence chain. Inspect native files and judge reasons before making a semantic
claim. Reading a summary or passing an agent-written checker is not a quality
measurement.

Select the largest positive complete diagnostics-minus-raw contrast for the
walkthrough; otherwise select the largest absolute contrast. Break ties by task
ID. Also include the most negative complete contrast when present. A selected
example is descriptive; there is no additional mechanism or reversion study.

## Qualification and spending

Requalify the final runtime on the already inspected technical-writing and
management-analysis task 1. Require complete two-round decisions, explicit
adoption, readable output, native judging, and Agent Lens readback. The exact
qualified execution engine and image IDs must match adaptation and transfer.
Python bytecode-cache creation is disabled equally for every worker.

Prior lifetime spend is $15.122852. New spending is capped at $84.87: qualification
$8, adaptation/execution $66.87, and judging/retries $10. Every call uses the
existing atomic reservation ledger. Unknown usage stops paid work. Project the
complete campaign from per-stage qualification costs, native rubric counts, and
the existing 1.5 contingency before launch. If qualification or affordability
fails, retain and report the stop; never shrink or retune the campaign after
seeing held-out results.

The first qualification stopped after its round-2 domain proposal used
`context/harness/skills/api-doc-set/SKILL.md` in its completion receipt where the
validator expected `skills/api-doc-set/SKILL.md`. Its bounded retry reached the
100-step limit without saving its completion files. The validator now accepts
either path spelling and still requires the exact changed-file set. The original
decisions remain rejected in the record. A fresh qualification uses isolated
workspaces and the same model settings, tasks, and $8 budget. All attempts count
toward spending and cost projections.

`smoke --restart-qualification "repair reason"` records a new qualification
attempt only after a stopped qualification and a changed execution engine. It
preserves prior work, source snapshots, trace IDs, and the shared cost ledger.
It refuses in-flight usage and any restart after main execution has begun.

## Public interface

Use `harness2-showcase {smoke,adapt,evaluate,report} --manifest showcase/pilot-v3.json`;
add `--prepare` to the initial smoke and `--publish` to report. Credentials remain
environment configuration. The project is `wandb/harness2-agent-lens-v3`.

Publish one W&B Report with a six-row comparison, paired score plot, native
conversation links, judge evidence, versioned harnesses, downloadable outputs,
and the full result CSV. Saved Agent Lens views separate adaptation, held-out
execution, and judging; qualification and auxiliary title calls remain retained.
Export only allowlisted public experiment material. Inspect screenshots,
walkthrough playback, signed-out Report access, and public downloads. Draft the
article only after the semantic evidence review.
