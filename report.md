# Can traces help an agent improve its harness?

A JobBench-based pilot with a frozen-harness transfer test. Two held-out tasks do not support a broad benchmark ranking.

**Coverage:** 7/12 planned held-out scores. **Recorded inference cost:** $9.9413. **Unresolved reservations:** $0.0000.

**Pilot finding:** no evidence here that trace diagnostics improved the execution harness. The diagnostics condition froze the same rendered harness as baseline. Standard Harness² had a small adaptation gain and lower scores on its available transfer task. Failed adaptation and one unavailable judgment limit coverage.

Baseline uses the unmodified upstream harness. Harness² runs Propose–Probe–Compose. The diagnostics condition adds deterministic observations read from stored Agent Lens spans. Upstream Harness² already provides trajectories to its improver.

[Reproducible code](https://github.com/ash0ts/harness2-wandb) · [Evidence browser](https://ash0ts.github.io/harness2-wandb/) · [Captioned walkthrough](https://ash0ts.github.io/harness2-wandb/media/walkthrough.mp4) · [Download all evidence](https://github.com/ash0ts/harness2-wandb/releases/tag/pilot-v1) · [Downloadable evidence artifact](https://forge.coreweave.com/wandb/wandb/harness2-agent-lens/artifacts/evidence/pilot-v1-evidence/v0)

## Held-out task scores

| Task | Condition | Mean score | Repetitions scored |
|---|---|---:|---:|
| management_analysts/task3 | baseline | 23.61% | 2/2 |
| management_analysts/task3 | harness2 | 8.33% | 2/2 |
| management_analysts/task3 | diagnostics | 29.17% | 2/2 |
| technical_writers/task3 | baseline | 47.62% | 1/2 |
| technical_writers/task3 | harness2 | Unavailable | 0/2 |
| technical_writers/task3 | diagnostics | Unavailable | 0/2 |

**Unavailable transfer condition: diagnostics / technical_writers.** Task2 produced no adoptable final harness; transfer was not executed. It is excluded from score means and paired changes, and remains in planned coverage.

**Unavailable transfer condition: harness2 / technical_writers.** Task2 produced no adoptable final harness; transfer was not executed. It is excluded from score means and paired changes, and remains in planned coverage.

**No executable harness change: diagnostics / management_analysts.** Its frozen source diff changes metadata or placeholder documentation, but the OpenCode renderer produces exactly the same files as the baseline. Any score difference for this condition cannot be attributed to a changed execution harness. Source and rendered fingerprints are included in the validation artifact.

Means average repetitions of the same task. Native earned and possible rubric weights appear below. A completed execution with no deliverables receives the native zero score. A failed judgment stays unavailable, even when deliverables exist. Four planned transfer executions could not run because no task-2 harness was adopted; they remain in planned coverage.

## Every held-out occurrence

| Task | Condition | Rep | Native score | Change from baseline | Trace |
|---|---|---:|---:|---:|---|
| management_analysts/task3 | baseline | 1 | 14/72 | +0.00 pp | [Inspect](https://forge.coreweave.com/agent-lens/wandb/harness2-agent-lens/conversations/c6cfd84d67f346f08529900933aa468f) |
| management_analysts/task3 | baseline | 2 | 20/72 | +0.00 pp | [Inspect](https://forge.coreweave.com/agent-lens/wandb/harness2-agent-lens/conversations/3673e05416364979a9870fd24929a607) |
| technical_writers/task3 | baseline | 1 | 40/84 | +0.00 pp | [Inspect](https://forge.coreweave.com/agent-lens/wandb/harness2-agent-lens/conversations/587f48b8c996473388d382db04f9888f) |
| technical_writers/task3 | baseline | 2 | Unavailable | Unavailable | [Inspect](https://forge.coreweave.com/agent-lens/wandb/harness2-agent-lens/conversations/d8143095343a4b348378c5bad0fa0a6b) |
| management_analysts/task3 | diagnostics | 1 | 22/72 | +11.11 pp | [Inspect](https://forge.coreweave.com/agent-lens/wandb/harness2-agent-lens/conversations/fed6478fb97647e8b9a476aa55c90aae) |
| management_analysts/task3 | diagnostics | 2 | 20/72 | +0.00 pp | [Inspect](https://forge.coreweave.com/agent-lens/wandb/harness2-agent-lens/conversations/42b9e1c62102437085925ac1b4a74d5f) |
| management_analysts/task3 | harness2 | 1 | 0/72 | -19.44 pp | [Inspect](https://forge.coreweave.com/agent-lens/wandb/harness2-agent-lens/conversations/cc8f1673adc7426da1b8f627958a4284) |
| management_analysts/task3 | harness2 | 2 | 12/72 | -11.11 pp | [Inspect](https://forge.coreweave.com/agent-lens/wandb/harness2-agent-lens/conversations/c419344eab7f4f8d9a1d9b82aae91e5c) |

## Inspect a gain and a regression

These examples select the largest positive and negative paired changes across the recorded adaptation and transfer attempts. Their stage is explicit; an adaptation gain is not evidence of transfer. The full occurrence table remains downloadable.

**Gain: diagnostics / management_analysts/task3 / heldout / repetition 1.** Native score 22/72; change +11.11 percentage points.
[Score, diff, and deliverables](https://ash0ts.github.io/harness2-wandb/#fed6478fb97647e8b9a476aa55c90aae) · [Harness diff](https://ash0ts.github.io/harness2-wandb/evidence/harnesses/1d065ca96df56b837a33c83eac4037637856f991f919fc4c29930da53f97f6e6/harness.diff) · [Agent Lens trace](https://forge.coreweave.com/agent-lens/wandb/harness2-agent-lens/conversations/fed6478fb97647e8b9a476aa55c90aae)
This condition loads the same executable harness as baseline. Its score difference is not evidence of an improved harness.

**Regression: diagnostics / technical_writers/task1 / adapt / repetition 0.** Native score 0/66; change -69.70 percentage points.
[Score, diff, and deliverables](https://ash0ts.github.io/harness2-wandb/#e223c19ad2d54e4abbdb90e1970921ad) · [Harness diff](https://ash0ts.github.io/harness2-wandb/evidence/harnesses/1d065ca96df56b837a33c83eac4037637856f991f919fc4c29930da53f97f6e6/harness.diff) · [Agent Lens trace](https://forge.coreweave.com/agent-lens/wandb/harness2-agent-lens/conversations/e223c19ad2d54e4abbdb90e1970921ad)

## Recorded walkthrough

[Watch the 75-second captioned video](https://ash0ts.github.io/harness2-wandb/media/walkthrough.mp4) · [Read the transcript](https://ash0ts.github.io/harness2-wandb/media/transcript.md)
This silent walkthrough is an edited sequence of recorded browser views. It follows a small adaptation gain and a transfer regression for the same Harness² harness. Captures and stored span records remain accessible without signing into Agent Lens.
![Adaptation score, harness diff, and generated files](https://ash0ts.github.io/harness2-wandb/media/adaptation-gain-final.png)
![Recorded Agent Lens tool failure with supporting span ID](https://ash0ts.github.io/harness2-wandb/media/agent-lens-gain-error.png)

## Costs

| Phase | USD |
|---|---:|
| adapt | $4.0453 |
| smoke | $0.0235 |
| judge | $4.8566 |
| evaluate | $1.0159 |

Adaptation includes baseline task executions, proposals, probes, composition, reflection, and retries. Judging is separate. Costs use recorded provider usage and frozen prices; they are not a billing invoice.
Agent Lens may show a lower cost: stored span cost fields in this pilot assigned zero to cached input. This report charges cached input at the frozen provider price and uses that ledger for the budget.

## Limits

The two domains each have one held-out task and one adapted harness per condition. Repetitions describe execution variability, not additional independent tasks. Model IDs do not pin provider weights. Scores can vary with judging. Trace patterns describe observed operations; a score difference alone does not prove the mechanism.

Some post-execution collectors returned 120 after their output pipe became nonblocking. The primary tool stream was retained and reconciled against completed model tool-call IDs; those wrapper codes are not evidence of agent failure. See the validation receipt and pilot notes.

Automatic Agent Lens Insights clustering is outside this pilot. The portable evidence browser includes stored span excerpts and downloadable records when Agent Lens requires a login.
Implementation repairs and trace-coverage limits are documented in [pilot notes](https://github.com/ash0ts/harness2-wandb/blob/showcase/wandb-agent-lens/showcase/pilot-notes.md). No benchmark grades were available while adaptation ran.
