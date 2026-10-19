# Research contract

Question: does adding deterministic diagnostics from stored Agent Lens spans to the
upstream Harness² improvement context change deliverable quality or execution cost?
The intended decision is whether a larger, preregistered study is justified.

## Frozen sources

| Input | Revision |
|---|---|
| Harness² | `1cfe254e8d25b15f37048c3730134686abe60335` |
| JobBench data | `d43a66575635da0d3f6cb9a20976dc1a60f3ffc9` |
| JobBench evaluator | `fa03a17f9ff662f136798a274e10011c3aa00fd0` |
| OpenCode | `23fb5e0516c99ac04a1aa46c193efda2e1b9bb24` |
| Forge SDK | `coreweave==0.1.0b0` |

Public sources: [Harness²](https://github.com/google-research/harness2),
[JobBench data](https://huggingface.co/datasets/JobBench/job-bench),
[JobBench evaluator](https://github.com/Job-Bench/job-bench-eval),
[Forge package](https://pypi.org/project/coreweave/0.1.0b0/).
Dependency hashes are in `requirements.lock`; fixture URLs and SHA256 values are saved
in `source-provenance.json`. Model IDs and prices are in `pilot.json`, checked on
2026-10-19 against the authenticated model catalog and the linked provider price page.
Requested and returned IDs must match. Provider-side weights are not pinned by these IDs.

## Treatments and task membership

The unit of comparison is a task within a domain. Conditions are baseline, Harness²,
and Harness² with diagnostics. All use the same actor, judge, tool image, references,
step/output limits, and task order. Baseline uses the unmodified upstream harness.
The two adaptation conditions run upstream parallel recursion (`agg`) with k=1.

Within each of `technical_writers` and `management_analysts`, task1 then task2 are used
for adaptation. Each condition and domain has a separate experience library. The
adopted harness after task2 is frozen and used on task3. Held-out evaluation runs twice,
or once for all conditions if the frozen budget allocation requires it. Repetitions
remain occurrences of the same task, not independent additional tasks. All adaptation
ends before any native judge runs on benchmark outputs. Synthetic qualification is
disjoint from these six tasks.

The diagnostics condition receives stored-span error status, repeated tool signatures,
retry candidates, operation latency, and deliverable presence. It receives no scores,
rubrics, or extra model-generated analysis. Both adaptation conditions retain upstream
trajectory feedback. The raw trajectory also includes captured child-session tools.

## Measures

| Measure | Numerator / denominator | Missingness and aggregation |
|---|---|---|
| Native task score | JobBench earned rubric weight / available rubric weight | Preserve native values; unavailable judgments are null |
| Paired change | Condition score minus baseline score for the same task/repetition | Only complete pairs; report pair coverage |
| Coverage | Completed judgments / planned task occurrences | Agent no-output counts as judged zero; unavailable judge does not |
| Held-out task mean | Sum of available repetition scores / available repetitions | Show each repetition and denominator; do not pool domains into a benchmark claim |
| Inference cost | Inclusive input minus cached input, plus cached input and output, times frozen prices | Missing usage retains a reservation and halts spending |
| Adaptation cost | All solver, improver, reflection, and retry requests in adaptation | Report separately from held-out execution, qualification, and grading |

Keys include campaign, condition, domain/task, phase, stage/run ID, repetition,
execution-session ID, provider request ID, harness fingerprint, and trace ID.
Judge evidence and generated outputs are preserved alongside the native score.

## Stops and interpretation

Qualification has a $10 cap; the entire campaign has a $100 cap. Projection uses observed
qualification usage with explicit allowances for longer task contexts and outputs.
It is a planning estimate, not a guarantee that every scheduled run will finish. Request
reservations enforce the hard cap independently. Missing usage, model mismatch, fixture
drift, and unverified trace persistence stop affected execution. Normal agent mistakes,
invalid harness proposals, and no-output outcomes remain results. Upstream repair
attempts are retained and charged. Failed judgments remain unavailable.

There are only two held-out tasks, one adapted harness per condition/domain, and no
independent adaptation seeds. Findings apply to this pilot. Small score changes can
reflect judge variability. Trace patterns support observations about operations; they
do not establish why a score changed without reviewing the deliverable and judge input.

## Publication

The default code home is `ash0ts/harness2-wandb`, with W&B destination
`wandb/harness2-agent-lens`. The public report must show costs, coverage, paired gains and
losses, harness diffs, trace links, outputs, and judge evidence. Exported screenshots and
a walkthrough keep the evidence readable when Agent Lens needs authentication. Verify
public report access with no authenticated browser session before claiming publication
is complete. No core product source or credentials belong in this repository or its
artifacts.
