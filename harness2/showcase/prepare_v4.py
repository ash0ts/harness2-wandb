"""Prepare every public worker image and private grader before paid execution."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from harness2 import config

from .data import prepare
from .runtime import ROOT, CampaignStop, command
from .workbuddy_preflight import run, save


def setup(runtime):
    for directory, url, revision in (
        (
            ROOT / "dependencies/workbuddy-bench",
            "https://github.com/Tencent/workbuddy-bench.git",
            runtime.manifest["studies"]["code"]["code_revision"],
        ),
        (
            config.jb_repo(),
            "https://github.com/Job-Bench/job-bench-eval.git",
            runtime.manifest["jobbench_revision"],
        ),
    ):
        if not directory.exists():
            command(["git", "clone", url, str(directory)])
            command(["git", "-C", str(directory), "checkout", "--detach", revision])
        if command(["git", "-C", str(directory), "rev-parse", "HEAD"]).stdout.strip() != revision:
            raise CampaignStop(f"Pinned benchmark revision differs: {directory.name}")
    patch = ROOT / "third_party/patches/job-bench-eval-judge.diff"
    if run(
        ["git", "-C", str(config.jb_repo()), "apply", "--reverse", "--check", str(patch)],
        check=False,
    ).returncode:
        command(["git", "-C", str(config.jb_repo()), "apply", str(patch)])
    if run(
        [
            "git",
            "-C",
            str(ROOT),
            "diff",
            "--quiet",
            runtime.manifest["harness2_revision"],
            "--",
            "harness2/core",
            "harness2/benchmarks",
            "harness2/substrate",
        ],
        check=False,
    ).returncode:
        raise CampaignStop("Pinned upstream workflow, adapter, or harness source changed")
    selection_path = runtime.state / "preflight/selection.json"
    if not selection_path.exists() or not json.loads(selection_path.read_text())["ready"]:
        raise CampaignStop("Complete the unpaid WorkBuddy fixture gate first")
    selection = json.loads(selection_path.read_text())
    tasks = sorted(
        set(selection["qualification"])
        | {task for group in selection["groups"] for task in [group["adapt"], *group["heldout"]]}
    )
    public = ROOT / "showcase/study-v4-selection.json"
    if public.exists() and json.loads(public.read_text()) != selection:
        raise CampaignStop("Published task selection changed")
    save(public, selection)
    base = runtime.manifest["image"] + "-base"
    run(
        ["docker", "build", "-t", base, "-f", "showcase/Dockerfile", "."],
        log=runtime.state / "base-build.log",
        timeout=1800,
    )
    base_image = command(["docker", "image", "inspect", base, "--format", "{{.Id}}"]).stdout.strip()
    run(
        [
            "docker",
            "build",
            "-t",
            runtime.manifest["image"],
            "--build-arg",
            "BASE_IMAGE=" + base,
            "-f",
            "showcase/Dockerfile.v4",
            ".",
        ],
        log=runtime.state / "runtime-build.log",
        timeout=1200,
    )
    engine = command(
        ["docker", "image", "inspect", runtime.manifest["image"], "--format", "{{.Id}}"]
    ).stdout.strip()
    engine_tag = "harness2-v4-pinned:engine-" + engine.removeprefix("sha256:")[:16]
    command(["docker", "tag", engine, engine_tag])
    volume = "harness2-v4-opencode-" + engine.removeprefix("sha256:")[:16]
    command(["docker", "volume", "create", "--label", "harness2.study=study-v4", volume])
    command(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--mount",
            f"type=volume,src={volume},dst=/opt/opencode",
            "--entrypoint",
            "sh",
            engine,
            "-c",
            "test -f /opt/opencode/packages/opencode/src/index.ts",
        ]
    )
    save(runtime.state / "runtime-volume.json", {"name": volume, "engine_image": engine})

    def build(task):
        record = json.loads((runtime.state / "preflight" / task / "result.json").read_text())
        if not record["eligible"]:
            raise CampaignStop("Ineligible fixture in frozen task selection")
        dest = runtime.state / "worker-builds" / task
        dest.mkdir(parents=True, exist_ok=True)
        base_tag = "harness2-v4-pinned:base-" + record["image_id"].removeprefix("sha256:")[:16]
        command(["docker", "tag", record["image_id"], base_tag])
        dockerfile = f"""FROM {engine_tag} AS engine
FROM {base_tag}
COPY --from=engine /usr/local/bin/bun /usr/local/bin/bun
COPY --from=engine /usr/local/bin/node /usr/local/bin/node
COPY --from=engine /opt/showcase-worker.py /opt/showcase-worker.py
COPY --from=engine /usr/bin/rg /usr/bin/rg
RUN cd / && useradd -m -u 10001 runner && install -d -o runner -g runner /home/runner/.local /home/runner/.local/state /home/runner/.local/share /home/runner/.local/share/opencode /home/runner/.cache /home/runner/.config /home/runner/.config/opencode && mkdir -p /work && mv /workspace /seed-workspace && ln -s /work /workspace && git config --system --add safe.directory /work
ENV OPENCODE_DISABLE_MODELS_FETCH=1 OPENCODE_DISABLE_AUTOUPDATE=1 OPENCODE_DISABLE_SHARE=1 PYTHONDONTWRITEBYTECODE=1
USER runner
WORKDIR /work
ENTRYPOINT ["python", "/opt/showcase-worker.py"]
"""
        (dest / "Dockerfile").write_text(dockerfile)
        tag = "harness2-v4-code:" + task.replace("_", "-")
        run(["docker", "build", "-t", tag, str(dest)], log=dest / "build.log", timeout=1200)
        image = command(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]).stdout.strip()
        # Inspect the actual final image, not just the build context.
        checked = command(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                "sh",
                image,
                "-c",
                "test ! -e /tests && test ! -e /solution && test ! -e /scorer/scorer.py && test -w /home/runner/.local/state && test -w /home/runner/.local/share/opencode && rg --version && bun --version",
            ]
        )
        (dest / "isolation-check.txt").write_text(checked.stdout)
        return task, image

    with ThreadPoolExecutor(max_workers=2) as pool:
        images = dict(pool.map(build, tasks))
    save(runtime.state / "code-images.json", images)
    spec = runtime.manifest["studies"]["jobbench"]
    job_tasks = spec["qualification_tasks"] + [
        task for group in spec["groups"] for task in [group["adapt"], *group["heldout"]]
    ]
    prepare(runtime.manifest, runtime.state, tasks=job_tasks)
    source = (config.jb_repo() / "eval/judge.py").read_text()
    old = "return OpenAI(base_url=api_base, api_key=api_key)"
    if source.count(old) != 1:
        raise CampaignStop("Unexpected pinned native judge client constructor")
    (runtime.state / "native-judge-v4.py").write_text(
        source.replace(old, "return OpenAI(base_url=api_base, api_key=api_key, max_retries=0)")
    )
    run(
        [
            "docker",
            "build",
            "-t",
            runtime.manifest["image"] + "-judge",
            "-f",
            "showcase/Dockerfile.judge",
            ".",
        ],
        log=runtime.state / "judge-build.log",
        timeout=1200,
    )
    save(
        runtime.state / "prepared.json",
        {
            "base_image": base_image,
            "engine_image": engine,
            "code_images": images,
            "jobbench_tasks": job_tasks,
            "opencode_volume": volume,
        },
    )
