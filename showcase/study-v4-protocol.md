# Study v4 research contract

Does adding structured Agent Lens diagnostics produce better harnesses than raw
execution feedback alone? Model weights remain fixed. This campaign has a new
$500 ceiling and does not modify or pool observations with pilots v1–v3.

## Design

WorkBuddy-Code is primary: eight adaptation groups, three independent adaptation
trials, three held-out tasks per group, three conditions, 216 final executions.
JobBench is a separate replication: six groups, two adaptation trials, one held-out
task per group, three conditions, 36 final executions. Trials share an initial
baseline within a group but never share improvement libraries or candidate probes.
The improving conditions use upstream sequential recursion with k=2. All harnesses
freeze before transfer execution, and all transfer executions finish before grading.

The conditions are the original upstream harness, Harness² with raw evidence, and
Harness² with the same raw evidence plus deterministic summaries of stored Agent
Lens spans. Summaries are limited to 8 KiB per execution and 16 KiB per overview.
Valid KEEP decisions and failed adaptations remain in the experiment. Transfer uses
the last valid adopted harness, with baseline as the final fallback.

## Task selection and fixture gate

Code and data revisions are recorded in `study-v4.json`. The WorkBuddy archive must
match SHA256 `f60394e44341f45f66af17ba69efc51ceadc61f17c08aa89e4af35b51f6f34e1`.
Order task IDs by SHA256 of UTF-8 `20261021:<task_id>`, breaking ties by task ID.
After metadata and offline fixture validation, reserve the first two eligible bug
fixes and first two eligible features for qualification. Within each remaining
category, select the first adaptation task with three eligible held-out tasks from
other repositories, in the same seeded order. Publish every exclusion.

Eligible categories: API contracts, bug fixes, features, performance, refactoring,
reliability, security hardening, and testing. Exclude synthetic workspaces, reports,
and tasks requiring live services. Reconstructed and partial repositories are
eligible and must be described as such. These are curated repository coding tasks,
not a representative sample of production issues.

Use the unmodified pinned CompositeVerifier, including its scoring policy. Run
each supplied reference solution and untouched workspace three times in fresh,
offline grader containers. Require a full-pass reference, a distinguishable failing
control without grader infrastructure errors, and repeat agreement. Never select
on model results. A category shortfall stops the design before paid main execution.

Executor build contexts contain the public workspace archive and a Dockerfile only.
Remove the upstream scorer COPY instruction. Correct its hardcoded Node architecture
for the local Docker platform and pin the Python base. Preserve task installation
commands and record image IDs and installed dependencies. Only grader containers
receive hidden tests, gold patches, solutions, and scorer files. Held-out prompts
remain outside development and improvement contexts.

## Spending and qualification

Allocate $40 to qualification, $410 to the fixed comparison including ordinary
judging, and $50 to recovery/retries. Use two worker containers at most. Reserve a
provable maximum before every provider attempt. With missing usage, preserve the
unknown and count the full proven reservation; never report it as observed cost.
Stop on unbounded usage, usage above its reservation, or model mismatch. Allow at
most two transport retries, with one retry owner and a shared circuit breaker after
three consecutive transport failures. Retry export without repeating inference.

Qualification uses two disjoint coding pairs and the previously inspected technical
writing and management analysis tasks. Require complete two-round decisions,
gradeable deliverables, actual tool coverage, matching executed fingerprints, and
stored-span readback. A quality improvement is not required. Forecast the complete
comparison from qualification stage costs and actual rubric counts, add 25%, and
preserve the $50 reserve. If it does not fit, stop without reducing the design.

## Analysis

The primary estimator is diagnostics minus raw native coding score in percentage
points: average adaptation trials within each held-out task, then weight the 24
tasks equally. Report both conditions against baseline, every task difference,
and wins/ties/losses. JobBench uses its six tasks and is analyzed independently.

Use a paired hierarchical bootstrap, seed 20261022, 10,000 draws, and a 95% percentile
interval. Sample groups, then paired adaptation trials within each sampled group,
then held-out task identities within that group. Use the same sampled trial and
task identities for all conditions, preserving the shared-harness structure.
The practical-equivalence band is ±5 percentage points. Classify supported gains
or losses when the interval excludes zero, practical equivalence when the interval
lies inside the band, and unresolved effects otherwise; report these separately
because a small supported effect can also be practically equivalent.

Missing judgments remain missing. Report planned and observed denominators; no
complete-comparison claim is allowed with missing rows. Agent failure, environment
failure, unavailable judgment, and missing trace evidence are distinct statuses.
Secondary measures are adaptation completion, executable changes, recurring errors,
deliverable completion, latency excluding queue time, and cost. Repeat a seeded
20% sample of JobBench judgments blindly; originals remain primary.

## Evidence and publication

Record campaign, study, group, trial, condition, task, round, stage, attempt, source
and rendered harness hashes, provider request IDs, returned model IDs, full request
settings, process exit status, and export status. A result links to its patch or
document, executed harness, diff, improvement decision, stored spans, and native
judgment. Store full raw evidence separately from bounded model-facing indexes.

Publish coding first and JobBench second in `wandb/harness2-agent-lens-v4`. Select
the walkthrough by the largest positive diagnostics-minus-raw task difference
averaged across trials with complete evidence, otherwise the largest absolute
difference. Break ties by task ID and include the largest regression. Inspect
deliverables and judge criteria before writing the article. Verify signed-out
Report/download access and provide native product screenshots and a headless video.

## Reproduction

Install the pinned WorkBuddy checkout in a separate Python 3.12 environment. Run
`python -m harness2.showcase.workbuddy_preflight` before any model-backed phase.
Its JSON records retain image hashes, source hashes, all control attempts, and
selection or shortfall details. This command has no inference credentials or calls.
The showcase CLI uses `smoke`, `adapt`, `evaluate`, `report`, and read-only `status`,
with `--manifest showcase/study-v4.json --study code|jobbench|all`.
