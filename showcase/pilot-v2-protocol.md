# Pilot-v2 protocol

Pilot-v2 tests recursive improvement of the agent's harness with fixed model weights. It compares the baseline, upstream Harness² execution feedback, and the same workflow with deterministic Agent Lens summaries. A positive result is not an acceptance criterion.

The immutable [manifest](pilot-v2.json) specifies the six adaptation/transfer task pairs, models, prices, upstream commits, SDK version through the package lock, limits, and budget. The published [pilot-v1](https://github.com/ash0ts/harness2-wandb/releases/tag/pilot-v1) remains unchanged.

## Execution and completion

The Linux worker contains ripgrep, document readers/writers, and statistical libraries. It can reach only the budget gateway over an internal Docker network. Only pinned task inputs, reference snapshots, and the rendered harness are mounted into a solver. Judges run separately with read-only output and rubric mounts.

Every improvement session must finish normally and save `output/answer.md` and `output/completion.json`. An editing receipt declares `change` or `keep`, a rationale, affected source paths, and supporting span IDs when used. A justified keep decision is accepted. Interrupted sessions, progress narration, missing receipts, and receipts inconsistent with the files are rejected through the upstream bounded retry path.

Source and rendered fingerprints are separate. A metadata edit can change the source hash while leaving the executed OpenCode harness unchanged. Every solver record includes its rendered fingerprint. The final transfer runner verifies that fingerprint against the frozen rendering.

Request bytes limit transport size. They do not estimate model context length. Provider context errors retain their original OpenAI-compatible shape. The gateway reserves a conservative text-input billing bound plus the requested output ceiling before forwarding a call. Missing usage remains reserved and stops new paid requests.

## Controlled comparison

Each domain has two independent adaptation replicates. Within a replicate, both adaptive conditions share the same two initial baseline executions and raw records. Each runs the pinned upstream sequential workflow with `k=2`, including successive Propose–Probe–Compose rounds, selection, and reflection. Their libraries and candidate workspaces are isolated.

Only the diagnostics condition receives `context/diagnostics/`. Individual summaries are at most 8 KiB; the stage index is at most 16 KiB. Both conditions receive complete raw requests, tool events, and stored spans under `context/evidence/`. Summary fields cover explicit errors, subsequent calls to the same tool, repeated identical calls, finish reasons, request limits, observed truncation, time, usage, and file readability. They contain no benchmark grades.

A subsequent call to a failed tool is an observed follow-up, not proof of a recovery strategy. A file path mentioned in a shell command is a reference, not conclusive evidence that a summary was read. Saved receipts, cited spans, executable diffs, and subsequent probes establish the inspectable sequence. They do not by themselves establish incremental benefit.

All adopted harnesses are frozen before any main benchmark grading. Failure to adopt retains baseline in transfer coverage. Evaluation conditions are interleaved using a frozen schedule, launched in blocks containing one execution of each condition. Up to four independent domain/replicate adaptation groups run concurrently. Two repetitions produce 72 planned executions; a pre-execution budget projection can reduce all conditions to one repetition, producing 36. If the complete reduced comparison does not fit, the campaign stops.

Judges receive an opaque evaluated-model identifier. Failed judgments remain unavailable. Executions with no deliverable retain the benchmark's native zero. All attempts remain in the ledger and downloadable evidence.

## Qualification and budget

Previously inspected technical-writing and management-analysis task 1 qualify the complete two-round workflow, adoption, readable output, native judging, and Agent Lens readback. Offline-tool qualification is an infrastructure check and is not a benchmark result. Qualification does not require a score gain.

The original lifetime ceiling is $100. Pilot-v1 recorded $9.941318. Pilot-v2 caps new spending at $90.05: $8 qualification, $67.05 main execution/adaptation, $5 mechanism check, and $10 retries/judging. SQLite transactions reserve concurrent calls atomically. Per-call usage and pinned prices determine cost; missing usage never becomes zero.

The main projection sums the largest observed cost for each adaptation stage across the qualified real tasks, including retries. Shared baselines are counted once. Transfer uses the largest observed solver-session cost, and judging uses native rubric counts. A 1.5 multiplier supplies contingency. The projection is a launch gate; the gateway separately enforces hard spending limits.

## Evidence and claims

Native held-out quality is the primary endpoint. Repetitions and adaptation replicates are averaged within each task family before overall comparisons. The diagnostics contrast is paired against Harness² on the same task, adaptation replicate, and execution repetition. Exploratory bootstrap intervals resample tasks, not individual executions. Six task families cannot establish a broad benchmark ranking.

The fixed showcase rule selects the largest positive complete held-out task mean difference from baseline, breaking ties by task and condition. Within that task/condition, the highest paired adaptation-replicate mean supplies the example, with replicate ID breaking ties. The reserved mechanism check compares that frozen harness with its preceding version. It is a post-selection follow-up, separate from primary results. If the selected case has no executable change, no harness effect is claimed.

The evidence package includes the full result table, source and rendered fingerprints, diffs, saved decisions, diagnostic summaries, stored spans, requests, tools, outputs, native judge records, source manifests, and the cost ledger. Public screenshots and a recorded walkthrough accompany native Agent Lens links, since those conversations may require authentication.

The article is written only after inspecting the selected deliverable, grading criteria, regression when present, and follow-up evidence. The outcome can support a verified loop with no gain, a Harness² improvement with no diagnostic advantage, or an incremental diagnostic benefit on these six tasks. Automatic Insights clustering is outside this pilot.
