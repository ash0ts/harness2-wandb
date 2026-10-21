"""Save native Agent Lens views for each study, trial, and experiment phase."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import requests
from configure_views import API, configuration


def plans(manifest, start, end):
    result = {}
    for study, specification in manifest["studies"].items():
        for trial in range(1, specification["adaptation_trials"] + 1):
            phases = (
                ("adaptation", "heldout", "judging")
                if study == "jobbench"
                else ("adaptation", "heldout")
            )
            for phase in phases:
                label = f"{study} / Trial {trial} / {phase}"
                raw = configuration(manifest["project"], phase, start, end)
                parsed = urlsplit(raw)
                query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
                filters, columns = json.loads(query["filter"]), json.loads(query["cols"])
                for name, kind, value in (("study", "string", study), ("trial", "int", trial)):
                    key = f"custom_attribute:custom_attrs_{kind}:harness2.{name}"
                    filters.append(
                        {
                            "id": key,
                            "value": {
                                "operator": "eq",
                                "value": value if kind == "int" else [value],
                            },
                        }
                    )
                    columns[key] = True
                columns["custom_attribute:custom_attrs_string:harness2.rendered_fingerprint"] = True
                query.update(
                    filter=json.dumps(filters, separators=(",", ":")),
                    cols=json.dumps(columns, separators=(",", ":")),
                )
                result[label] = {
                    "name": label,
                    "description": f"study-v4 {phase}, {study}, trial {trial}. Qualification and auxiliary title calls excluded. Actual session boundaries retained.",
                    "url": parsed.path + "?" + urlencode(query),
                }
    result["Qualification"] = {
        "name": "Qualification",
        "description": "Pre-main runtime checks. Excluded from study outcomes.",
        "url": configuration(manifest["project"], "qualification", start, end),
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("study-v4.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    state = args.manifest.resolve().parent / manifest["state_dir"]
    times = [time.time()]
    for path in (state / "executions").glob("*/execution.json"):
        execution = json.loads(path.read_text())
        times.extend(execution[key] for key in ("started", "ended") if key in execution)
    settings = plans(
        manifest, int(min(times) // 86400 * 86400000), int((max(times) // 86400 + 1) * 86400000 - 1)
    )
    if args.dry_run:
        print(json.dumps(settings, indent=2))
        return
    entity, project = manifest["project"].split("/")
    headers = {
        "Authorization": "Bearer " + os.environ["WANDB_API_KEY"],
        "x-wandb-entity": entity,
        "x-wandb-project": project,
    }

    def read_views():
        response = requests.get(API, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()["data"]

    existing = read_views()
    for body in settings.values():
        matches = [view for view in existing if view["name"] == body["name"]]
        if len(matches) > 1:
            raise ValueError("Duplicate native view names")
        if matches and all(matches[0].get(key) == value for key, value in body.items()):
            continue
        response = (
            requests.patch(API + "/" + matches[0]["id"], headers=headers, json=body, timeout=30)
            if matches
            else requests.post(API, headers=headers, json=body, timeout=30)
        )
        response.raise_for_status()
    actual, records = read_views(), {}
    for label, body in settings.items():
        matches = [view for view in actual if view["name"] == body["name"]]
        if len(matches) != 1 or not all(matches[0].get(k) == v for k, v in body.items()):
            raise ValueError("Native view configuration readback differs")
        view = matches[0]
        clean = f"https://forge.coreweave.com/agent-lens/{manifest['project']}/conversations/views/{view['id']}"
        records[label] = {
            "id": view["id"],
            "name": label,
            "url": clean + "?" + body["url"].split("?", 1)[1],
            "saved_view_url": clean,
            "configuration_verified": True,
            "rendering_verified": False,
        }
    destination = state / "presentation"
    destination.mkdir(exist_ok=True)
    (destination / "views.json").write_text(json.dumps(records, indent=2))
    (destination / "native-views-readback.json").write_text(
        json.dumps(
            {"verified_at": datetime.now(timezone.utc).isoformat(), "views": actual}, indent=2
        )
    )
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
