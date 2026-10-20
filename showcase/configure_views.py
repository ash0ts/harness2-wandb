"""Create the campaign's native Agent Lens views and verify their configuration."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests

API = "https://forge.coreweave.com/api/pulse/views"
VIEWS = (
    (
        "Qualification",
        "00 · Qualification",
        "qualification",
        "Runtime qualification before the comparison. Actual sessions only.",
    ),
    (
        "Adaptation",
        "01 · Adaptation",
        "adaptation",
        "Shared starting executions and isolated two-round harness refinement.",
    ),
    (
        "Held-out execution",
        "02 · Held-out execution",
        "heldout",
        "Frozen-harness transfer executions. Qualification and adaptation are excluded.",
    ),
    (
        "Judging",
        "03 · Native judging",
        "judging",
        "Native JobBench judging after transfer. Qualification is excluded.",
    ),
)


def configuration(project, cohort, start, end):
    columns = dict.fromkeys(
        (
            "agent agent_version provider model error_type tool_name status_code "
            "intent_category intent_topic_id failure_topic_id intent_sentiment "
            "failure_category failure_severity input_tokens output_tokens total_tokens "
            "first_message last_message tags turns spans started last_activity"
        ).split(),
        False,
    )
    columns.update(dict.fromkeys(("conversation_id", "duration", "cost", "errors"), True))
    for name in ("condition", "stage_name", "task"):
        columns[f"custom_attribute:custom_attrs_string:harness2.{name}"] = True
    for name in ("attempt", "round"):
        columns[f"custom_attribute:custom_attrs_int:harness2.{name}"] = True
    filters = [
        {
            "id": f"custom_attribute:custom_attrs_string:harness2.{name}",
            "value": {"operator": "eq", "value": [value]},
        }
        for name, value in (("cohort", cohort), ("trace_kind", "execution"))
    ]
    query = urlencode(
        {
            "cols": json.dumps(columns, separators=(",", ":")),
            "filter": json.dumps(filters, separators=(",", ":")),
            "range": f"custom:{start}:{end}",
        }
    )
    return f"/{project}/conversations?{query}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("pilot-v3.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text())
    state = manifest_path.parent / manifest["state_dir"]
    project = manifest["project"]
    entity, project_name = project.split("/", 1)
    timestamps = [time.time()]
    for path in (state / "executions").glob("*/execution.json"):
        execution = json.loads(path.read_text())
        timestamps.extend(execution[k] for k in ("started", "ended") if k in execution)
    start = int(min(timestamps) // 86400 * 86400 * 1000)
    end = int((max(timestamps) // 86400 + 1) * 86400 * 1000 - 1)
    plans = {
        label: {
            "name": name,
            "description": description + " Auxiliary title calls remain in All rows.",
            "url": configuration(project, cohort, start, end),
        }
        for label, name, cohort, description in VIEWS
    }
    if args.dry_run:
        print(json.dumps(plans, indent=2))
        return
    headers = {
        "Authorization": "Bearer " + os.environ["WANDB_API_KEY"],
        "x-wandb-entity": entity,
        "x-wandb-project": project_name,
        "User-Agent": "harness2-showcase/1.0",
    }

    def read_views():
        response = requests.get(API, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()["data"]

    existing = read_views()
    for body in plans.values():
        matches = [view for view in existing if view["name"] == body["name"]]
        if len(matches) > 1:
            raise ValueError(f"Multiple views named {body['name']}; reconcile before updating")
        if matches and all(matches[0].get(key) == value for key, value in body.items()):
            continue
        response = (
            requests.patch(API + "/" + matches[0]["id"], headers=headers, json=body, timeout=30)
            if matches
            else requests.post(API, headers=headers, json=body, timeout=30)
        )
        response.raise_for_status()
    actual = read_views()
    records, readback = {}, []
    for label, body in plans.items():
        matches = [view for view in actual if view["name"] == body["name"]]
        assert len(matches) == 1
        found = matches[0]
        assert all(found.get(key) == value for key, value in body.items())
        clean = (
            f"https://forge.coreweave.com/agent-lens/{project}/conversations/views/{found['id']}"
        )
        # Preserve the explicit settings in shared links as well as the saved view.
        records[label] = {
            "name": found["name"],
            "id": found["id"],
            "url": clean + "?" + found["url"].split("?", 1)[1],
            "saved_view_url": clean,
            "filter_url": "https://forge.coreweave.com/agent-lens" + found["url"],
            "type": "native saved view",
            "configuration_verified": True,
            "rendering_verified": False,
            "verification": "Configuration read back from Agent Lens; browser review is separate.",
        }
        readback.append(
            {key: found[key] for key in ("id", "name", "description", "url", "updated_at")}
        )
        print(f"{found['name']}: {clean}")
    output = state / "presentation"
    output.mkdir(parents=True, exist_ok=True)
    (output / "views.json").write_text(json.dumps(records, indent=2) + "\n")
    (output / "native-views-readback.json").write_text(
        json.dumps(
            {"verified_at": datetime.now(timezone.utc).isoformat(), "views": readback}, indent=2
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
