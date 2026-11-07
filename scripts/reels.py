#!/usr/bin/env python3
"""FACT REELS: your raw photos and clips -> finished 9:16 Reels with cards, narration, subtitles and sfx.

  vedit reels new  my_channel                 copy the starter kit (templates/fact_reels) to ./my_channel
  vedit reels story my_channel 002_my_topic   new story folder from the kit's _new_story_template
  vedit reels build my_channel                build EVERY story in my_channel/stories
  vedit reels build my_channel/stories/002_x  build ONE story (folder or its story.json)
  vedit reels demo  my_channel/stories/000_example   make sample photo/clips so the example builds

Build = brand background (if missing) -> sound effects (if missing) -> cards -> thumbnail -> video.
Options: --preview (fast low-quality render), --only cards (draw cards + thumbnail, skip the video).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "templates" / "fact_reels"
sys.path.insert(0, str(HERE))
import brand as brandkit  # noqa: E402


def stories_in(target: Path) -> list[Path]:
    target = target.resolve()
    if target.is_file():
        return [target]
    if (target / "story.json").exists():
        return [target / "story.json"]
    found = sorted((target / "stories").glob("*/story.json"))
    if not found:
        sys.exit(f"no story.json found in {target} or {target / 'stories'}")
    return found


def cmd_new(a):
    dst = Path(a.path)
    if dst.exists() and any(dst.iterdir()):
        sys.exit(f"{dst} already exists and is not empty; pick a new folder name")
    shutil.copytree(TEMPLATE, dst, ignore=shutil.ignore_patterns("*.build", "*.mp4", "*.srt", "__pycache__"),
                    dirs_exist_ok=True)
    print(f"Starter kit copied to {dst.resolve()}\nNext: edit {dst / 'brand.json'}, then run:\n"
          f"  vedit reels demo {dst / 'stories' / '000_example'}\n  vedit reels build {dst}")


def cmd_story(a):
    kit = Path(a.kit)
    src = kit / "_new_story_template"
    if not src.exists():
        sys.exit(f"{src} not found: is {kit} a Fact Reels kit (made with `vedit reels new`)?")
    dst = kit / "stories" / a.name
    if dst.exists():
        sys.exit(f"{dst} already exists")
    shutil.copytree(src, dst)
    sp = dst / "story.json"
    story = json.loads(sp.read_text(encoding="utf-8"))
    story.setdefault("output", {})["path"] = f"{a.name}.mp4"
    sp.write_text(json.dumps(story, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for sub in ("photos", "clips", "cards"):
        (dst / sub).mkdir(exist_ok=True)
    print(f"New story: {dst.resolve()}\nPut your photos in photos/, raw videos in clips/, then edit story.json.")


def build_one(sp: Path, a) -> int:
    import cards
    import sfx
    print(f"\n=== {sp.parent.name} ===")
    b = brandkit.load(brandkit.find_brand(sp.parent))
    brandkit.ensure_background(b)
    sfx.make_all(b["sfx_dir"])
    story = json.loads(sp.read_text(encoding="utf-8"))
    if story.get("demo_media"):
        make_demo(sp.parent, quiet=True)
    problem = check_media(sp, story)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    cards.cards_for_story(sp)
    cards.thumbnail_for_story(sp)
    if a.only == "cards":
        return 0
    cmd = [sys.executable, str(HERE / "story.py"), "build", str(sp)] + (["--preview"] if a.preview else [])
    return subprocess.call(cmd)


def check_media(sp: Path, story: dict) -> str | None:
    """Friendly message (instead of a crash mid-render) when a photo or clip is missing."""
    missing = []
    for si, sc in enumerate(story.get("scenes") or []):
        if sc.get("video") and not (sp.parent / sc["video"]).exists():
            missing.append(f"scene {si + 1}: {sc['video']}")
        photo = (sc.get("card") or {}).get("photo")
        if photo and not (sp.parent / photo).exists():
            missing.append(f"scene {si + 1}: {photo}")
        if sc.get("image") and not sc.get("card") and not (sp.parent / sc["image"]).exists():
            missing.append(f"scene {si + 1}: {sc['image']}")
    if missing:
        return "Missing files (paths are relative to " + str(sp.parent) + "):\n  " + "\n  ".join(missing)
    return None


def cmd_build(a):
    worst = 0
    for sp in stories_in(Path(a.path)):
        worst = max(worst, build_one(sp, a))
    print("\nDone. Each video is saved inside its story folder." if not worst else "\nSome builds failed (see above).")
    return worst


def make_demo(folder: Path, quiet=False):
    """Sample media for the example story: a photo, a silent B-roll clip and a talking clip (AI voice)."""
    folder = Path(folder)
    (folder / "photos").mkdir(parents=True, exist_ok=True)
    (folder / "clips").mkdir(parents=True, exist_ok=True)
    photo, broll, talk = folder / "photos" / "sample.jpg", folder / "clips" / "broll.mp4", folder / "clips" / "talking.mp4"
    ff = ["ffmpeg", "-y", "-v", "error"]
    if not photo.exists():
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (1200, 900), "#1d3557")
        d = ImageDraw.Draw(im)
        for i in range(12):
            d.ellipse((60 + i * 85, 380 - (i % 3) * 60, 260 + i * 85, 580 - (i % 3) * 60),
                      fill=("#e63946", "#f1faee", "#a8dadc", "#457b9d")[i % 4])
        d.text((40, 40), "Sample photo: replace with your own", fill="#f1faee")
        im.save(photo, quality=92)
    if not broll.exists():  # 4 s of moving footage: shorter than the narration, so it loops
        subprocess.check_call(ff + ["-f", "lavfi", "-i", "mandelbrot=s=1280x720:r=30", "-t", "4", "-pix_fmt", "yuv420p",
                                    "-c:v", "libx264", "-preset", "veryfast", str(broll)])
    if not talk.exists():  # stands in for you talking to the camera
        from tts import synth
        wav, _w, dur, _e = synth("Which fact surprised you the most? Tell me in the comments, and follow for a "
                                 "new fact every day!", {"engine": "edge", "voice": "en-US-AvaNeural"},
                                 folder / "clips" / ".tts")
        subprocess.check_call(ff + ["-f", "lavfi", "-i", "cellauto=s=1280x720:rule=110:r=30", "-i", str(wav),
                                    "-t", f"{dur + 0.4:.2f}", "-pix_fmt", "yuv420p", "-c:v", "libx264",
                                    "-preset", "veryfast", "-c:a", "aac", "-shortest", str(talk)])
        shutil.rmtree(folder / "clips" / ".tts", ignore_errors=True)
    if not quiet:
        print(f"Sample media ready in {folder}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new", help="copy the starter kit")
    p.add_argument("path")
    p = sub.add_parser("story", help="new story folder in a kit")
    p.add_argument("kit")
    p.add_argument("name")
    p = sub.add_parser("build", help="build one story or all stories of a kit")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--preview", action="store_true", help="fast low-quality render")
    p.add_argument("--only", choices=["cards"], help="cards = draw cards + thumbnail only")
    p = sub.add_parser("demo", help="sample photo + clips for a story folder")
    p.add_argument("path")
    a = ap.parse_args()
    if a.cmd == "new":
        return cmd_new(a)
    if a.cmd == "story":
        return cmd_story(a)
    if a.cmd == "demo":
        return make_demo(Path(a.path))
    return cmd_build(a)


if __name__ == "__main__":
    sys.exit(main() or 0)
