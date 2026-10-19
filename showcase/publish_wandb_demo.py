"""Publish a read-only evidence preview inside the campaign's public W&B Report."""

import argparse
import json
import re
from pathlib import Path

import wandb
import wandb_workspaces.reports.v2 as wr

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("public", type=Path)
parser.add_argument("--project", default="wandb/harness2-agent-lens")
parser.add_argument(
    "--asset-base",
    default="https://raw.githubusercontent.com/ash0ts/harness2-wandb/gh-pages/",
)
args = parser.parse_args()
public = args.public
receipt = json.loads((public / "publication.json").read_text())
data = json.loads((public / "results.json").read_text())
entity, project = args.project.split("/")
base = args.asset_base.rstrip("/") + "/"
html = (public / "index.html").read_text()
html = re.sub(
    r'(href|src|poster)="((?:evidence/|media/|results\.|report\.)[^"]*)"',
    lambda m: f'{m[1]}="{base}{m[2]}"',
    html,
)
html = html.replace("fetch('publication.json')", f"fetch({json.dumps(base + 'publication.json')})")
html = html.replace("?esc(x):'#'", f"?esc(x.startsWith('evidence/')?{json.dumps(base)}+x:x):'#'")
html = html.replace(
    "history.pushState(null,'','#'+id);", "{try{history.pushState(null,'','#'+id);}catch{}}"
)
# The W&B iframe sandbox does not allow popup links. Keep an explicit notice and
# the original standalone file; the surrounding Report provides ordinary links.
html = html.replace(
    "<main>",
    '<main><p id="embedded-note" class="note" hidden>Embedded preview: choose an occurrence to inspect its score, diff, and tool records. Use the surrounding report for external downloads and trace links.</p>',
)
html = html.replace(
    "'use strict';",
    "'use strict';document.getElementById('embedded-note').hidden=window===window.top;",
)
with wandb.init(
    entity=entity,
    project=project,
    id=data["campaign"] + "-presentation",
    resume="allow",
    job_type="publication",
    config={"campaign": data["campaign"]},
    settings=wandb.Settings(disable_git=True),
) as run:
    media = wandb.Html(html, inject=False)
    run.log({"evidence_browser": media})
    media_path = media.to_json(run)["path"]
    run.summary["evidence_browser"] = media
    run_url = run.url
demo_url = (
    f"https://api.wandb.ai/files/{entity}/{project}/{data['campaign']}-presentation/{media_path}"
)
old_demo_url = receipt.get("hosted_demo_url")
receipt["hosted_demo_url"] = demo_url
(public / "publication.json").write_text(json.dumps(receipt, indent=2))
report = wr.Report.from_url(receipt["report_url"])
text = (public / "report.md").read_text()
if old_demo_url:
    text = text.replace(old_demo_url, demo_url)
pages = "https://ash0ts.github.io/harness2-wandb/"
text = text.replace(f"[Evidence browser]({pages})", f"[Evidence browser]({demo_url})")
text = text.replace(pages + "#", demo_url + "#")
text = text.replace(pages, base)
(public / "report.md").write_text(text)
intro, rest = text.split("## Held-out task scores", 1)
report.blocks = [
    wr.MarkdownBlock(text=intro),
    wr.PanelGrid(
        hide_run_sets=True,
        runsets=[
            wr.Runset(
                entity=entity,
                project=project,
                filters="ID = '" + data["campaign"] + "-presentation'",
            )
        ],
        panels=[
            wr.MediaBrowser(
                title="Interactive evidence preview",
                media_keys=["evidence_browser"],
                num_columns=1,
                layout=wr.Layout(w=24, h=16),
            )
        ],
    ),
    wr.MarkdownBlock(text="## Held-out task scores" + rest),
]
report.save()
print(json.dumps({"report": receipt["report_url"], "run": run_url, "demo": demo_url}, indent=2))
