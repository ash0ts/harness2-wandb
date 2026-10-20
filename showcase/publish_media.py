"""Upload selected campaign media to W&B and record public URLs and hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("pilot-v3.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "files", nargs="+", help="Paths relative to the campaign's presentation directory"
    )
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text())
    state = manifest_path.parent / manifest["state_dir"]
    presentation = (state / "presentation").resolve()
    entity, project = manifest["project"].split("/", 1)
    run_id = manifest["campaign"] + "-presentation"
    base_url = f"https://api.wandb.ai/files/{entity}/{project}/{run_id}/"
    secrets = [os.environ.get("WANDB_API_KEY", "")]
    if (state / ".admin-token").exists():
        secrets.append((state / ".admin-token").read_text().strip())
    files = {}
    for name in args.files:
        source = presentation / name
        resolved = source.resolve()
        if (
            not resolved.is_relative_to(presentation)
            or not source.is_file()
            or source.is_symlink()
            or source.suffix.lower()
            not in (".png", ".svg", ".mp4", ".webm", ".vtt", ".md", ".json", ".pdf")
        ):
            raise ValueError(f"Not a supported presentation file: {name}")
        raw = source.read_bytes()
        if any(secret and secret.encode() in raw for secret in secrets):
            raise ValueError(f"Credential detected in presentation file: {name}")
        relative = resolved.relative_to(presentation).as_posix()
        files[relative] = {
            "url": base_url + quote(relative, safe="/"),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }
    if args.dry_run:
        print(json.dumps({"project": manifest["project"], "files": files}, indent=2))
        return

    import wandb

    with wandb.init(
        entity=entity,
        project=project,
        id=run_id,
        resume="allow",
        job_type="publication",
        settings=wandb.Settings(save_code=False, disable_git=True),
    ) as run:
        for name in files:
            run.save(str(presentation / name), base_path=str(presentation), policy="now")
        if "walkthrough.mp4" in files:
            run.log(
                {"walkthrough": wandb.Video(str(presentation / "walkthrough.mp4"), format="mp4")}
            )
        run.summary["campaign"] = manifest["campaign"]
    receipt_path = presentation / "media-publication.json"
    previous = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    receipt = {
        "published_at": datetime.now(timezone.utc).isoformat(),
        "run_url": f"https://wandb.ai/{entity}/{project}/runs/{run_id}",
        "files": {**previous.get("files", {}), **files},
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"published_files": len(files), "run_url": receipt["run_url"]}))


if __name__ == "__main__":
    main()
