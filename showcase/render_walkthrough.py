"""Make a captioned walkthrough from recorded browser views, without modifying the captures."""

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

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
    shutil.copyfile(font, root / "font.ttf")
    for index, scene in enumerate(story["scenes"]):
        source = args.storyboard.parent / scene["capture"]
        if not source.is_file() or scene["seconds"] <= 0:
            raise SystemExit(f"Invalid recorded scene: {scene['capture']}")
        capture = root / f"capture-{index}.png"
        shutil.copy2(source, capture)
        caption = root / f"caption-{index}.png"
        layer = Image.new("RGB", (1920, 130), "#102728")
        draw = ImageDraw.Draw(layer)
        face = ImageFont.truetype(str(root / "font.ttf"), 31)
        bounds = draw.multiline_textbbox((40, 25), scene["caption"], font=face, spacing=10)
        if bounds[2] > 1880 or bounds[3] > 120:
            raise SystemExit(f"Caption does not fit scene {index}")
        draw.multiline_text((40, 25), scene["caption"], font=face, fill="white", spacing=10)
        layer.save(caption)
        output = root / f"scene-{index}.mp4"
        filters = (
            "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=white[screen];"
            "[screen][1:v]overlay=0:950,scale=1920:1080,setsar=1"
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
                "-loop",
                "1",
                "-i",
                str(caption),
                "-t",
                str(scene["seconds"]),
                "-filter_complex",
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
