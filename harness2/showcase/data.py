"""Download only the declared public tasks at an immutable revision."""

import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from .runtime import ROOT


def prepare(manifest, state):
    revision = manifest["dataset_revision"]
    files = []
    for domain in manifest["domains"]:
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
            if task not in manifest["adapt_tasks"] + manifest["heldout_tasks"]:
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
    provenance = {"dataset_revision": revision, "files": sorted(records, key=lambda x: x["path"])}
    (Path(state) / "source-provenance.json").write_text(json.dumps(provenance, indent=2))
    return provenance
