# Harness² + Agent Lens walkthrough

An edited walkthrough of recorded browser views, with captions and no audio. The captures show actual experiment evidence; this is not a continuous screen recording.

## 00s–10s

Three harness conditions. Two held-out tasks. This pilot tests transfer, not a broad benchmark ranking.

[Inspect evidence](https://ash0ts.github.io/harness2-wandb/)

## 10s–21s

Adaptation: Harness² scored 20/80 versus baseline 18/80. Follow the score to its harness diff, trace, and generated files.

[Inspect evidence](https://ash0ts.github.io/harness2-wandb/#9135d64d8c134bbdbbcfe763b79e1378)

## 21s–31s

Agent Lens records an actual tool failure with a supporting span ID. The offline worker blocked a tool dependency download.

[Inspect evidence](https://forge.coreweave.com/agent-lens/wandb/harness2-agent-lens/conversations/9135d64d8c134bbdbbcfe763b79e1378/spans/c87de62d69d0b8f7)

## 31s–42s

Transfer: the same frozen Harness² harness scored 0/72, versus 14/72. This first repetition produced no deliverables.

[Inspect evidence](https://ash0ts.github.io/harness2-wandb/#cc8f1673adc7426da1b8f627958a4284)

## 42s–52s

Its final model response reached the 16,384 output-token limit. The trace records 11,079 reasoning tokens and finish_reason=length.

[Inspect evidence](https://forge.coreweave.com/agent-lens/wandb/harness2-agent-lens/conversations/cc8f1673adc7426da1b8f627958a4284/spans/b95bb3ddb104d193)

## 52s–63s

Diagnostics froze the same executable harness as baseline. A higher score here cannot establish harness improvement.

[Inspect evidence](https://ash0ts.github.io/harness2-wandb/)

## 63s–75s

Public report verified while signed out. Cost: $9.94; scores: 7/12. Download the code, requests, traces, harnesses, outputs, and judge evidence.

[Inspect evidence](https://wandb.ai/wandb/harness2-agent-lens/reports/Harness-Agent-Lens-frozen-harness-pilot--VmlldzoxODE0MzU2Mw==)

The adaptation gain and transfer regression use the same management-analysis harness. They were chosen to illustrate that an adaptation gain need not transfer. The report and evidence browser also expose the largest observed changes and every recorded occurrence.

The gain is +2 weighted rubric points: a 10-weight cost-impact criterion passed while an 8-weight Go/No-Go matrix criterion failed. Scores depend on this judge and these tasks. The trace observations do not establish a causal explanation for the score changes.

The pilot has eight of twelve planned held-out executions and seven available native scores. Four executions are unavailable because technical-writing adaptation did not adopt a task-2 harness. One baseline technical-writing judgment is unavailable after a judge response reached its output limit. An execution with no deliverables receives the native zero score; an unavailable judgment does not.

Recorded inference cost is $9.941318, including $4.045305 adaptation, $1.015923 held-out execution, $4.856616 judging, and $0.023474 qualification. These are usage-based estimates at frozen prices, not a billing invoice.

The audit reconciled 1,905 provider requests, 101 traced sessions, and 4,293 stored spans. Post-execution collector errors affected the redundant audit stream in 31 sessions; completed model tool-call IDs were reconciled against the retained primary stream. Those wrapper errors are not classified as agent failures. See [pilot notes](https://github.com/ash0ts/harness2-wandb/blob/showcase/wandb-agent-lens/showcase/pilot-notes.md).
