# Harness² + Agent Lens

Can structured observations from execution traces help an agent improve its harness?
This fork compares the upstream baseline, Harness² refinement, and the same refinement
with deterministic diagnostics retrieved from Agent Lens. The original Harness² already
provides trajectories to its improver. The treatment is how that evidence is organized.

This is a **JobBench-based pilot with a frozen-harness transfer test**. Two held-out tasks
cannot establish a general benchmark ranking. Negative results are valid results.

## Run

Use Python 3.12, Docker, and a W&B account with access to the models in
[pilot.json](pilot.json). Credentials stay in the environment. The default project is
`wandb/harness2-agent-lens`; change it and the campaign/state directory before the first
request when reproducing under another account. Never put credentials in a manifest.

```bash
uv venv --python 3.12
uv pip install --require-hashes -r showcase/requirements.lock
uv pip install --no-deps -e .
export WANDB_API_KEY=...  # obtain this from your W&B account
harness2-showcase smoke --prepare
harness2-showcase adapt
harness2-showcase evaluate
harness2-showcase report
# Review the local export, then upload the results and save the W&B Report:
harness2-showcase report --publish
```

The runner resolves state paths relative to the manifest. It refuses a changed manifest
in an existing campaign. `--prepare` downloads only the declared public task files and
rubrics at their pinned revision, verifies hashes, applies the upstream judge patch,
and builds the worker and judge images. Image builds require internet access; task
workers and judges run on a Docker internal network with only the inference gateway.
Set `SHOWCASE_SUBNET` before preparation if the default subnet conflicts locally.

`smoke` qualifies both requested and returned model IDs, actual OpenCode tool use,
child-session capture, stored span coverage, native grading, and unchanged work files
after export. No fallback model is selected. `adapt` computes and saves the campaign
allocation before it starts. It uses two evaluation repetitions when the projection
fits, otherwise one for every condition. It stops if the complete comparison cannot fit.

The gateway reserves each in-flight request against $100 total and $10 qualification.
It includes retries, child agents, reflection, and judges. Missing usage keeps its
reservation and blocks further spending, including after a restart. A gateway crash
with pending requests requires independent billing reconciliation; do not delete the
ledger or rerun the attempt to bypass it. Prices are fixed in the manifest, and reported
cost is calculated from provider usage, rounded up to microdollars per request.

## Evidence

The generated report connects each native score to its judge evidence, output artifact,
harness fingerprint and diff, Agent Lens conversation, and independently captured model
requests and tool events. Diagnostics are generated only from stored execution spans
and an output-presence check. They include explicit errors, repeated identical tool
calls, retry candidates, latency, and supporting span IDs. Repeated calls alone are not
proof of a loop. Automatic Insights clustering is outside this pilot.

Observation runs after execution. The pinned Forge SDK (`coreweave==0.1.0b0`) emits spans
with original timestamps; stored model IDs, response IDs, token usage, tool IDs,
arguments, and failures are checked against the independent captures. OpenCode's persisted
child-session tools supplement its root CLI event stream in both refinement conditions.
The executor can edit its harness components but cannot edit the host-side collector or
budget gateway. Tools are represented under their execution conversation, with original
OpenCode session IDs retained in span attributes and session-parent records in the audit.

Public exports use an explicit allowlist and reject configured credentials. The full
state directory also contains local controls and must never be uploaded wholesale.
Generated artifacts use public JobBench fixtures. Rubrics and judge records are mounted
only into judge containers, after all improvement and reflection has ended.

## Protocol and validation

See the [research contract](research-contract.md), [manifest](pilot.json), and generated
`showcase/state/pilot-v1/` receipts. The baseline harness files and the upstream
Propose–Probe–Compose algorithm are unchanged. Shared deployment constraints differ
from upstream defaults: the model provider, offline references, 60 agent steps, 16,384
output-token ceiling, 30-minute session timeout, and a budgeted inference gateway.

Run the upstream workflow and showcase safety tests with:

```bash
uv pip install pytest==8.4.2 ruff==0.14.1
python -m pytest tests/test_workflow.py tests/showcase -q
ruff check harness2/showcase tests/showcase
ruff format --check harness2/showcase tests/showcase showcase/worker.py
```

An unavailable judge is missing, never zero. An agent that produces no deliverable
receives the native no-output score. Failed and interrupted attempts remain in the
state and exports. Resume only completed, verified attempts or work proven not to have
started. This experiment does not silently replay uncertain trace writes.

The gateway is a local experiment component, not a production multi-tenant service.
Cleanup affects only this campaign's labeled containers and network; retain its state
and budget ledger for audit before removing them.
