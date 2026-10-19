# Pilot-v2 qualification results

Pilot-v2 stopped before its controlled comparison. Qualification demonstrated executable harness changes and subsequent executions using them, but failed a completion gate. The conservative launch projection also exceeded the main-phase budget. No fresh-task transfer results or diagnostics-benefit claim are available.

[W&B Report](https://wandb.ai/wandb/harness2-agent-lens/reports/Harness-Agent-Lens-pilot-v2--VmlldzoxODE0NTEwMw==) · [Evidence and walkthrough](https://github.com/ash0ts/harness2-wandb/releases/tag/pilot-v2) · [Protocol](pilot-v2-protocol.md) · [Manifest](pilot-v2.json)

## Qualification

| Previously inspected task | Valid refinement decisions | Selected harness | Readable files | Native weighted score |
|---|---:|---|---:|---:|
| Technical writing task 1 | 6/6, including a valid keep | c1 | 4 Markdown files | 38/66 |
| Management analysis task 1 | 5/6 | c2 | Word, Excel, CSV | 14/88 |

Both selected executions used the rendered harness fingerprint in their freeze receipts. These scores are neither a before/after comparison nor a transfer test. Management analysis was judged after the failed gate to finish its evidence, without changing that gate's outcome.

Both attempts at the first management-analysis composer stage omitted generated Python compiler caches from the receipt's affected-file list. The workflow fell back for that stage. Its second composer succeeded on a bounded retry. Every attempt remains recorded.

In technical writing, an agent-authored verification script ran, reported two webhook violations, induced edits and passed on rerun. At least one flag was a false positive: the old API-reference text already denied the `dataset.deleted` event and said archiving fires `dataset.updated`. The checker required a narrower wording. Its transferability is untested; it embeds task-specific names, schema and facts.

The original edit is span `26eee8dbd6620742` in conversation `8dccac4b37fe4cfe83d059b470edc33b`. The Report links its regrouped presentation view and preserved original records. Another candidate used all 32,768 output tokens as reasoning, returned `finish_reason: length`, and produced no deliverable. This is a qualification failure, not a held-out regression.

Three failed technical-writing grading reasons demanded internal ticket or customer references while the task instructions prohibited documenting internal references. The native 38/66 remains unchanged. The downloadable review identifies the instruction, criteria and generated passages.

## Cost and verification

New spending was **$5.181534**, below the $8 qualification allocation. Including pilot-v1, lifetime spending is **$15.122852**. All usage is resolved; no main or mechanism spending occurred.

The projection uses the largest observed cost per stage, includes retries, counts shared baselines once and adds 50% contingency. Main-phase forecasts were $152.24 for 72 transfers and **$132.30 for 36 transfers**, before judging. Both exceed $67.05. These are conservative forecasts, not incurred costs or guaranteed minimum future costs. Qualification failure independently prevents launch.

The audit reconciled 1,414 requests, 38 sessions and 3,221 stored spans: complete messages, models, limits, finish reasons, usage, timestamps, tool arguments, results and failures. JSON-valued tool results permit formatting normalization. Twenty output fingerprints include shared baseline copies, not twenty independent tasks. Fifteen decisions were accepted, ten with executable changes. Actual offline OpenCode skill, grep, glob, read, shell and delegated-task tools were exercised.

## Source and presentation limits

The exact qualification source is preserved in `provenance/qualification-source/`. Later repairs provide receipt-rejection feedback, a full initial improvement mission, separate auxiliary title calls, stronger budget stops and stricter readback audits. Deterministic tests cover them, but they were not all exercised by the original qualification. The final runtime has not been live-qualified.

Three presentation conversations regroup the same captured records to separate title generation. Original traces remain intact; corrected exports passed readback without new inference. Existing improver requests retain their wrapper wording. Native screenshots are unmodified; the captioned walkthrough is an edited sequence of recorded browser views, without audio.

A future main campaign must qualify its final runtime and fit its cost projection. Model or protocol changes require a new identity. Continuing under the original lifetime ceiling must carry forward the full $15.122852 already spent. Pilot-v1 remains unchanged.
