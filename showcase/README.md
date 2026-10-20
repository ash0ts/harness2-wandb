# Harness² + Agent Lens

Can structured observations from execution traces help an agent improve its harness?
This fork compares the upstream baseline, Harness² refinement, and the same refinement
with deterministic diagnostics retrieved from Agent Lens. The original Harness² already
provides trajectories to its improver. The treatment is how that evidence is organized.

These are **JobBench-based pilots with frozen-harness transfer tests**. Their small task
sets cannot establish a general benchmark ranking. Negative results are valid results.

## Six-job comparison

[Pilot-v3](pilot-v3-protocol.md) asks whether summaries improve held-out quality beyond
raw execution feedback. Its [manifest](pilot-v3.json) specifies six task pairs, one
adaptation per condition, two refinement rounds, and eighteen final evaluations.
The original harness is the reference; diagnostics minus raw feedback is the primary
contrast. The new project is `wandb/harness2-agent-lens-v3`.

```sh
harness2-showcase smoke --manifest showcase/pilot-v3.json --prepare
harness2-showcase adapt --manifest showcase/pilot-v3.json
harness2-showcase evaluate --manifest showcase/pilot-v3.json
harness2-showcase report --manifest showcase/pilot-v3.json --publish
```

The complete runtime must pass live qualification and the complete cost projection
before adaptation starts. New spending cannot exceed $84.87, including $8 qualification
and $10 judging/retries. Earlier campaigns consumed $15.122852 of the $100 lifetime cap.
There is no additional mechanism study. The Report preserves native scores, missing
judgments, failed attempts, executable harness changes, and their Agent Lens records.
One execution per setup does not establish repeatability.

After execution, create the four native conversation views before publishing:

```sh
python showcase/configure_views.py --manifest showcase/pilot-v3.json
```

The helper saves qualification, adaptation, held-out execution, and native judging
views, excludes auxiliary title calls, and reads the saved settings back from Agent
Lens. It uses `WANDB_API_KEY`; `--dry-run` prints the proposed settings. Screenshot
and browser checks remain separate from configuration verification.

Native screenshots can be captured independently of the desktop display with
Node 20 or later and Chrome:

```sh
npm ci --prefix showcase/browser
node showcase/browser/capture.mjs --manifest showcase/pilot-v3.json \
  --view Adaptation --authenticated --chrome \
  --output showcase/state/pilot-v3/presentation/adaptation-view.png
```

For a signed-out Report check, omit `--authenticated`, pass its `--url`, and provide
`--wait-text` with a phrase from the published Report. Each capture uses a fresh
browser context and saves its URL, authentication mode, and visible text beside the
PNG. It does not use the existing desktop browser's cookies. Capture helpers never
modify the recorded conversations or experiment inputs.

## Published pilot

[Evidence browser](https://api.wandb.ai/files/wandb/harness2-agent-lens/pilot-v1-presentation/media/html/evidence_browser_2_0884d9a81d0b7bd08982.html) · [W&B Report](https://wandb.ai/wandb/harness2-agent-lens/reports/Harness-Agent-Lens-frozen-harness-pilot--VmlldzoxODE0MzU2Mw==) · [75-second walkthrough](https://github.com/ash0ts/harness2-wandb/releases/download/pilot-v1/walkthrough.mp4) · [Download all evidence](https://github.com/ash0ts/harness2-wandb/releases/tag/pilot-v1)

The completed pilot cost **$9.941318** and has **7/12 available held-out scores**.
Four planned executions were unavailable after failed adaptation; one judge failed.
The diagnostics condition froze the same executable harness as baseline, so its score
difference does not demonstrate harness improvement. Standard Harness² showed a small
adaptation gain and a transfer regression. See [pilot notes](pilot-notes.md) for the
fixed environment, collector limitations, and implementation repairs.

The report was verified in a signed-out browser. The walkthrough is an edited sequence
of recorded browser views, with captions and no audio. Screenshots, a transcript, and
stored spans remain available without an Agent Lens login.

## Run

Use Python 3.12, Docker, and a W&B account with access to the models in
[pilot-v3.json](pilot-v3.json). Credentials stay in the environment. For a new run,
copy the manifest and set your own project, campaign identity, state directory, and
spending limits before the first request. If another campaign is running locally,
also choose separate image, network, gateway-port, and subnet values. Keep the published
manifests as records of their original campaigns. Never put credentials in a manifest.

```bash
uv venv --python 3.12
uv pip install --require-hashes -r showcase/requirements.lock
uv pip install --no-deps -e .
export WANDB_API_KEY=...  # obtain this from your W&B account
```

Run the phases listed in [Six-job comparison](#six-job-comparison), passing the path
to your manifest with `--manifest` each time. Omit `--publish` to inspect the local
Report and evidence export before uploading them.

The runner resolves state paths relative to the manifest. It refuses a changed manifest
in an existing campaign. `--prepare` downloads only the declared public task files and
rubrics at their pinned revision, verifies hashes, applies the upstream judge patch,
and builds the worker and judge images. Image builds require internet access; task
workers and judges run on a Docker internal network with only the inference gateway.
Set `SHOWCASE_SUBNET` before preparation if the default subnet conflicts locally.

`smoke` qualifies both requested and returned model IDs, actual OpenCode tool use,
child-session capture, stored span coverage, native grading, and unchanged work files
after export. No fallback model is selected. `adapt` computes and saves the campaign
allocation before it starts. Pilot-v3 requires all eighteen planned transfer executions
to fit; it stops if the complete comparison cannot fit.

The gateway reserves each in-flight request against the manifest's total and phase
limits. It includes retries, child agents, reflection, and judges. Missing usage keeps its
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

## Package the public demo

After publication, use the allowlisted export under the campaign's `public/` directory.
The archive includes request and usage records, stored spans, diagnostics, harnesses,
diffs, outputs, judge evidence, source manifests, and validation receipts. The smaller
static site includes the files linked from its result rows. Never upload the full state
directory, which also contains local controls.

```bash
python showcase/render_walkthrough.py public/media/storyboard.json public/media/walkthrough.mp4
python showcase/package_demo.py public /path/to/empty/site /path/to/pilot.zip
```

The walkthrough renderer requires ffmpeg, Pillow (included in the pinned dependencies),
and a TrueType font. Its storyboard names recorded PNG captures, durations, and captions.
It produces an H.264 MP4 and WebVTT captions. Raw screenshots remain unchanged.
The release includes the complete ZIP and SHA256 checksum.

The evidence browser is also hosted as public W&B HTML media and embedded in the
Report. Publish that view after `report --publish` with:

```bash
python showcase/publish_wandb_demo.py showcase/state/pilot-v1/public
```

Use `--project` and `--asset-base` when publishing another campaign. The asset base must
serve the static export's evidence and media files. W&B's embedded iframe restricts
external navigation; its standalone public file URL permits ordinary links. The helper
records that URL in `public/publication.json`. Share the API file URL, which redirects
to a fresh signed file URL, rather than copying the expiring storage URL.

The GitHub Pages mirror is configured at https://ash0ts.github.io/harness2-wandb/.
Its initial deployment was queued during GitHub's October 19 Actions/Pages incident.
The W&B-hosted demo and the release download were verified without authentication.
Agent Lens itself redirected to login in the signed-out check; the exported spans,
screenshots, and walkthrough provide public access to the evidence.

## Pilot-v2

The [v2 qualification report](https://wandb.ai/wandb/harness2-agent-lens/reports/Harness-Agent-Lens-pilot-v2--VmlldzoxODE0NTEwMw==) records executable changes, a false-positive checker and a failed completion gate. New spending was $5.181534. The reduced comparison projected $132.30 before judging against $67.05, so no fresh-task comparison ran. See [v2 notes](pilot-v2-notes.md) and the [evidence and walkthrough](https://github.com/ash0ts/harness2-wandb/releases/tag/pilot-v2). The final runtime repairs need live qualification.

The second campaign is specified in [pilot-v2-protocol.md](pilot-v2-protocol.md) and [pilot-v2.json](pilot-v2.json). It uses isolated state, sequential `k=2` refinement, explicit completion receipts, and six fresh task families. Pilot-v1 remains available at its original release.

Run from the fork with Docker and `WANDB_API_KEY` configured:

```sh
python -m harness2.showcase.cli smoke --manifest showcase/pilot-v2.json --prepare
python -m harness2.showcase.cli adapt --manifest showcase/pilot-v2.json
python -m harness2.showcase.cli evaluate --manifest showcase/pilot-v2.json
python -m harness2.showcase.cli mechanism --manifest showcase/pilot-v2.json
python -m harness2.showcase.cli report --manifest showcase/pilot-v2.json
```

`smoke` includes actual offline OpenCode tools and complete two-round JobBench qualification. `adapt` refuses to start without qualification and a cost projection that fits the remaining budget. `evaluate` requires every frozen harness and saves unavailable judgments separately. `report --publish` uploads the evidence and creates a separate W&B Report for v2. It does not overwrite pilot-v1. A failed qualification stops the campaign before fresh-task evaluation.
