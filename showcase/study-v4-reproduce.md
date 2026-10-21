# Reproduce study v4

Run from this fork's root with Python 3.12, Docker, Git, and uv. The campaign uses
W&B Inference credentials from `WANDB_API_KEY`. Preparation and reference/control
checks do not make model calls. Model-backed qualification has a $40 limit.

For a separate reproduction, copy [study-v4.json](study-v4.json) and change its
campaign, project, state directory, network, subnet, gateway port, and image names
before the first request. Preserve model IDs, data pins, task selection, settings,
and analysis if comparing with the published experiment. A changed model or protocol
needs a different campaign identity. Never copy a prior state directory into a new run.
The commands below use the published state path. For a renamed campaign, place its
grader environment at `<state>/preflight-venv` and pass `--output <state>/preflight`
to the fixture command.

## Install the runner and native grader

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python --require-hashes -r showcase/requirements.lock
uv pip install --python .venv/bin/python --no-deps -e .

git clone https://github.com/Tencent/workbuddy-bench.git dependencies/workbuddy-bench
git -C dependencies/workbuddy-bench checkout --detach b516950be5b56eb3be406c2f76ee1c5111dcb57f
git clone https://github.com/Job-Bench/job-bench-eval.git dependencies/job-bench-eval
git -C dependencies/job-bench-eval checkout --detach fa03a17f9ff662f136798a274e10011c3aa00fd0
git -C dependencies/job-bench-eval apply ../../third_party/patches/job-bench-eval-judge.diff

uv venv --python 3.12 showcase/state/study-v4/preflight-venv
uv pip install --python showcase/state/study-v4/preflight-venv/bin/python \
  -r showcase/workbuddy-requirements.lock
```

The native grader uses the pinned WorkBuddy `CompositeVerifier` and Harbor commit
`527d50deb63a5d279e8c20593c18a2cbc7f61f9e`. It lives in a separate environment from
the public Forge SDK. The grader environment is never mounted into an executor.
The requirements lock records the resolved package versions used for this campaign.

## Download and qualify the code fixtures

```sh
mkdir -p dependencies/workbuddy-code-data
curl --fail --location \
  https://huggingface.co/datasets/tencent/workbuddy-bench/resolve/3e8ad839685c589d83a1dd60fe2d15eabb75f27a/wb-bench-code-v1.0.tar.gz \
  --output dependencies/workbuddy-code-data/wb-bench-code-v1.0.tar.gz
.venv/bin/python - <<'PY'
import hashlib
import tarfile
from pathlib import Path
root = Path('dependencies/workbuddy-code-data')
archive = root / 'wb-bench-code-v1.0.tar.gz'
expected = 'f60394e44341f45f66af17ba69efc51ceadc61f17c08aa89e4af35b51f6f34e1'
assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected
with tarfile.open(archive) as source:
    source.extractall(root, filter='data')
PY
showcase/state/study-v4/preflight-venv/bin/python \
  -m harness2.showcase.workbuddy_preflight
```

This builds public-workspace images, then gives separate offline grader containers
the reference solution and hidden verifier. It runs each reference and untouched
control three times. Selection stops if any category lacks four eligible tasks after
the qualification reserve. All exclusions and attempts remain in `preflight/`.

WorkBuddy's supplied `tests/test.sh` entry point is not the native scoring path for
these fixtures. The runner calls the pinned registry verifier and preserves its
reward semantics, including partial credit. Repositories can be partial or
reconstructed; this is not a sample of unrestricted production issues.

## Build and qualify the execution system

Preparation builds a campaign-specific base image and worker images from the pinned
source. It checks the benchmark code revisions and applies the existing native
JobBench judge patch. It does not rebuild or resume the earlier pilots.

```sh
.venv/bin/python -m harness2.showcase.cli smoke \
  --manifest showcase/study-v4.json --study all --prepare
```

Preparation builds every selected worker image, checks that grader material is
absent, prepares pinned JobBench reference files, and builds the private judge image.
OpenCode uses W&B Inference through the budget gateway. The upstream Gemini setup
path is not used. Worker networking is restricted to the experiment gateway.

Qualification requires actual tools and child-session capture, complete two-round
improvement decisions, native grading, readable outputs, matching executed harnesses,
and stored-span readback. It does not require an improvement in score. Source and
image fingerprints are frozen when qualification starts. If a repair is necessary,
retain the failed attempt and use `--restart-qualification 'specific repair reason'`
under the same qualification allocation. Do not repeatedly restart to select a
favorable score.

## Run the fixed comparison

```sh
.venv/bin/python -m harness2.showcase.cli adapt \
  --manifest showcase/study-v4.json --study all
.venv/bin/python -m harness2.showcase.cli evaluate \
  --manifest showcase/study-v4.json --study all
.venv/bin/python -m harness2.showcase.cli report \
  --manifest showcase/study-v4.json --study all
```

Adaptation first forecasts the entire design from qualification costs and actual
JobBench rubric counts, with 25% contingency. It must fit the $410 main allocation
while preserving the $50 recovery reserve. It does not shrink the design after
seeing scores. All adaptations freeze before held-out execution; all held-out
executions finish before grading. `--study code` and `--study jobbench` select a
study without relaxing either cross-study gate.

The model changes only harness instructions, skills, and scripts. Improvements
must save a completed CHANGE or KEEP decision. Executable file modes count in v4
harness fingerprints and affected-file inventories. Repository copies preserve
internal symlinks and executable permissions, so observation does not create a patch.

## Inspect progress and recover

```sh
.venv/bin/python -m harness2.showcase.cli status \
  --manifest showcase/study-v4.json --study all
```

Status is read-only and does not need inference credentials. Durable operation
records prevent completed model executions from being paid for again on resume.
Raw streams, OpenCode SQLite records, child exit status, and output workspaces
survive collection failures. Trace export retries use those records. A missing
verified readback pauses the affected comparison instead of supplying empty summaries.

V4 reports observed cost, bounded unknown cost, and in-flight reservations separately.
A missing usage field is never zero: the full provable reservation counts against
the ceiling. Unbounded usage, usage above its reservation, or a returned model
different from the manifest stops execution. The gateway owns transport retries,
allows two retries, and opens a shared circuit breaker after three consecutive
transport failures. Earlier pilots retain their original accounting rules.

## Review and publish

```sh
.venv/bin/python showcase/report_views_v4.py --manifest showcase/study-v4.json
.venv/bin/python -m harness2.showcase.cli report \
  --manifest showcase/study-v4.json --study all --publish
```

Refresh the saved views after execution and before publishing. Their shared links
include the campaign's time window; views created during qualification would omit
later days of a long run. Refresh and republish if the judgment repeats cross into
another day. Completed repeat judgments are reused on publication retries.

Publication first performs the predetermined 20% blind JobBench judge repeats in a
separate result store. Original judgments remain primary. The Report presents coding
first and JobBench separately, with every planned row and task-level differences.
Unavailable judgments remain missing and suppress the complete-design estimator.
The evidence ZIP contains scores, judge reasons, raw requests, events, stored spans,
decisions, harness versions and diffs, and delivered patches or documents. Export is
allowlisted; the entire state directory must never be uploaded.

Native Agent Lens views separate study, trial, adaptation, held-out execution, and
JobBench model judging. Coding grades link to the native offline test evidence.
Use the existing [headless capture helper](README.md#six-job-comparison) for product
screens and recording. Verify Report and download access in a fresh signed-out
browser. Inspect the selected deliverables and native grading reasons before writing
the article. A diagnostic citation or a passing agent-written check does not prove
that the harness change improved quality.
