"""Make a captioned walkthrough from recorded browser views, without modifying the captures."""

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("storyboard", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--font", type=Path)
args = parser.parse_args()
story = json.loads(args.storyboard.read_text())
font = args.font or next(
    (
        path
        for path in (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        )
        if path.exists()
    ),
    None,
)
if not font or not shutil.which("ffmpeg"):
    raise SystemExit("Install ffmpeg and provide a TrueType font with --font")
args.output.parent.mkdir(parents=True, exist_ok=True)
elapsed = 0
captions = ["WEBVTT", ""]


def timestamp(seconds):
    milliseconds = round(seconds * 1000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"00:{minutes:02}:{seconds:02}.{milliseconds:03}"


with tempfile.TemporaryDirectory(prefix="h2-walkthrough-") as temporary:
    root = Path(temporary)
    parts = []
    # Copy to simple local names so ffmpeg filter syntax cannot reinterpret paths.
    shutil.copy2(font, root / "font.ttf")
    for index, scene in enumerate(story["scenes"]):
        source = args.storyboard.parent / scene["capture"]
        if not source.is_file() or scene["seconds"] <= 0:
            raise SystemExit(f"Invalid recorded scene: {scene['capture']}")
        capture = root / f"capture-{index}.png"
        shutil.copy2(source, capture)
        caption = root / f"caption-{index}.txt"
        caption.write_text(scene["caption"])
        output = root / f"scene-{index}.mp4"
        filters = (
            "scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=white,"
            "drawbox=x=0:y=950:w=1920:h=130:color=0x102728@0.97:t=fill,"
            f"drawtext=fontfile=font.ttf:textfile={caption.name}:expansion=none:"
            "fontcolor=white:fontsize=31:line_spacing=10:x=40:y=975"
        )
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-loop",
                "1",
                "-i",
                str(capture),
                "-t",
                str(scene["seconds"]),
                "-vf",
                filters,
                "-r",
                "24",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-threads",
                "2",
                "-pix_fmt",
                "yuv420p",
                str(output),
            ],
            cwd=root,
            check=True,
        )
        parts.append(f"file '{output.name}'")
        captions.extend(
            [
                f"{timestamp(elapsed)} --> {timestamp(elapsed + scene['seconds'])}",
                scene["caption"],
                "",
            ]
        )
        elapsed += scene["seconds"]
    (root / "parts.txt").write_text("\n".join(parts))
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            "parts.txt",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(args.output.resolve()),
        ],
        cwd=root,
        check=True,
    )
args.output.with_suffix(".vtt").write_text("\n".join(captions))
print(json.dumps({"output": str(args.output), "seconds": elapsed, "scenes": len(parts)}))
