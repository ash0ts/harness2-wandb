"""Download only the declared public tasks at an immutable revision."""

import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from .runtime import ROOT


def prepare(manifest, state, tasks=None):
    revision = manifest["dataset_revision"]
    files = []
    if tasks is None and manifest.get("schema_version", 1) >= 2:
        tasks = manifest["qualification_tasks"]
    domains = sorted({t.split("/")[0] for t in tasks}) if tasks else manifest["domains"]
    for domain in domains:
        url = (
            f"https://huggingface.co/api/datasets/JobBench/job-bench/tree/{revision}"
            f"/dataset/{domain}?recursive=true&limit=1000"
        )
        with urllib.request.urlopen(url, timeout=30) as response:
            entries = json.load(response)
        for entry in entries:
            path = PurePosixPath(entry["path"])
            if entry["type"] != "file" or ".." in path.parts:
                continue
            task = path.parts[2]
            if tasks is not None and f"{domain}/{task}" not in tasks:
                continue
            if tasks is None and task not in manifest["adapt_tasks"] + manifest["heldout_tasks"]:
                continue
            # Task cards contain benchmark context; never copy them to an executor.
            if path.name == "task_card.md":
                continue
            files.append(str(path))
    target = ROOT / "dependencies/job-bench-eval/dataset/main"

    def download(path):
        relative = Path(path).relative_to("dataset")
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/datasets/JobBench/job-bench/resolve/{revision}/{path}"
        with urllib.request.urlopen(url, timeout=60) as response:
            content = response.read()
        digest = hashlib.sha256(content).hexdigest()
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"Fixture drift: {relative}")
        dest.write_bytes(content)
        return {"path": str(relative), "sha256": digest, "source": url}

    with ThreadPoolExecutor(8) as pool:
        records = list(pool.map(download, files))
    path = Path(state) / "source-provenance.json"
    old = json.loads(path.read_text())["files"] if path.exists() else []
    merged = {r["path"]: r for r in old}
    for record in records:
        if record["path"] in merged and merged[record["path"]] != record:
            raise RuntimeError("Source manifest drift")
        merged[record["path"]] = record
    provenance = {
        "dataset_revision": revision,
        "files": sorted(merged.values(), key=lambda x: x["path"]),
    }
    path.write_text(json.dumps(provenance, indent=2))
    return provenance
