#!/usr/bin/env python3
"""STORY / SKIT MODE: photos + a script -> voiced, captioned, animated video.

  python story.py grid story.json            # gridded sheet of every photo -> pick "focus" points (Read it)
  python story.py voices story.json          # synthesize all lines only; prints timing table (cheap check)
  python story.py build story.json [--preview] [--no-render]

story.json (paths relative to the file):
{
  "title": "The Lost Wallet",
  "output": {"path": "skit.mp4", "aspect": "9:16"},          # 9:16 shorts/reels/tiktok, 16:9 youtube
  "voices": {
    "narrator": {"engine": "edge", "voice": "en-US-AndrewNeural", "color": "#FFD400"},
    "maria":    {"engine": "edge", "voice": "en-US-AvaNeural", "rate": "+8%", "color": "#FF6FB5"}
  },
  "music": {"path": "music/bgm.mp3", "volume": 0.15},          # optional, auto-ducked under voices
  "captions": {"style": "pop"},                                # pop | box | clean | {"enabled": false}
  "scenes": [
    {"image": "photos/street.jpg", "motion": "in", "title": "Monday, 8:02 AM",
     "lines": [
       {"speaker": "narrator", "text": "Maria was already late."},
       {"speaker": "maria", "text": "Where is my wallet?!", "focus": [0.62, 0.35], "shot": "close",
        "style": "panicked, fast", "pitch": "+8Hz", "pause_after": 0.5, "sfx": "sfx/gasp.mp3"}
     ],
     "sfx": [{"path": "sfx/street.mp3", "at": 0, "volume": 0.3}],
     "transition": "fade", "hold_after": 0.6},
    {"image": "photos/cafe.jpg", "duration": 2.5, "texts": [{"text": "Meanwhile...", "at": 0.3}]}
  ]
}
Scene keys: image | video (+ "in"), motion in|out|left|right|up|down|none|auto, lines[], duration (no lines),
            hold_before, hold_after, transition cut|fade, title, texts[], sfx[]
Line keys:  speaker, text, focus [x,y] (0..1 on the photo), shot wide|medium|close, pause_after,
            any voice override (voice, rate, pitch, style, engine), sfx (path or {path, volume, at})
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import die, load_json, run, save_json, ts  # noqa: E402
from tts import synth  # noqa: E402

SHOT_ZOOM = {"wide": 1.0, "medium": 1.35, "close": 1.8, "extreme": 2.4}
AUTO_MOTIONS = ["in", "right", "out", "left", "up", "in", "down"]


def motion_path(m: str):
    """-> (zoom_from, zoom_to, focus_from, focus_to)"""
    return {
        "in": (1.0, 1.18, [0.5, 0.5], [0.5, 0.5]),
        "out": (1.18, 1.0, [0.5, 0.5], [0.5, 0.5]),
        "left": (1.18, 1.18, [0.85, 0.5], [0.15, 0.5]),
        "right": (1.18, 1.18, [0.15, 0.5], [0.85, 0.5]),
        "up": (1.18, 1.18, [0.5, 0.85], [0.5, 0.15]),
        "down": (1.18, 1.18, [0.5, 0.15], [0.5, 0.85]),
        "none": (1.0, 1.0, [0.5, 0.5], [0.5, 0.5]),
    }.get(m, (1.0, 1.18, [0.5, 0.5], [0.5, 0.5]))


def lerp(a, b, t):
    if isinstance(a, list):
        return [round(x + (y - x) * t, 4) for x, y in zip(a, b)]
    return round(a + (b - a) * t, 4)


def normalize_image(src: Path, build: Path, i: int) -> Path:
    """Apply EXIF rotation (phone photos) so the renderer sees the photo upright."""
    try:
        from PIL import Image, ImageOps  # type: ignore
        im = Image.open(src)
        if (im.getexif() or {}).get(0x0112, 1) not in (1, None) or im.mode not in ("RGB", "L") \
                or src.suffix.lower() in (".heic", ".webp", ".gif"):
            out = build / f"img{i}.jpg"
            ImageOps.exif_transpose(im).convert("RGB").save(out, quality=95)
            return out
    except ImportError:
        pass
    except Exception as e:
        print(f"[story] couldn't normalize {src.name}: {e}", file=sys.stderr)
    return src


def cmd_grid(a):
    story = load_json(a.story)
    root = Path(a.story).resolve().parent
    build = root / (Path(a.story).stem + ".build")
    build.mkdir(exist_ok=True)
    for i, sc in enumerate(story["scenes"]):
        if not sc.get("image"):
            continue
        img = normalize_image(root / sc["image"], build, i)
        out = build / f"grid_scene{i}.jpg"
        lab = "".join(f"drawtext=text='{k / 10:.1f}':x=w*{k / 10}+4:y=4:fontsize=h/28:fontcolor=yellow:box=1:"
                      f"boxcolor=black@0.5," for k in range(1, 10))
        lab += "".join(f"drawtext=text='{k / 10:.1f}':x=4:y=h*{k / 10}+4:fontsize=h/28:fontcolor=cyan:box=1:"
                       f"boxcolor=black@0.5," for k in range(1, 10))
        base = ["ffmpeg", "-y", "-v", "error", "-i", str(img), "-frames:v", "1", "-q:v", "3"]
        grid = "scale='min(1280,iw)':-2,drawgrid=w=iw/10:h=ih/10:t=2:c=yellow@0.5"
        try:
            run(base + ["-vf", f"{grid},{lab.rstrip(',')}", str(out)])
        except SystemExit:
            run(base + ["-vf", grid, str(out)])
        print(f"scene {i}: {out}")
    print("Read each sheet. focus = [x, y] of the speaker's face: x = yellow labels (left->right), "
          "y = cyan labels (top->bottom).")


def synth_all(story: dict, root: Path, build: Path):
    voices = story.get("voices") or {}
    cache = build / "voice_cache"
    table = []
    for si, sc in enumerate(story["scenes"]):
        for li, ln in enumerate(sc.get("lines") or []):
            spk = ln.get("speaker", "narrator")
            cfg = dict(voices.get(spk) or {})
            for k in ("engine", "voice", "rate", "pitch", "volume", "style", "model", "stability"):
                if k in ln:
                    cfg[k] = ln[k]
            wav, words, dur, eng = synth(ln["text"], cfg, cache)
            ln["_wav"], ln["_words"], ln["_dur"], ln["_engine"] = wav, words, dur, eng
            table.append((si, li, spk, dur, eng, ln["text"]))
    return table


def print_table(table):
    total = 0.0
    for si, li, spk, dur, eng, text in table:
        total += dur
        print(f"  s{si}.l{li}  {dur:5.2f}s  {spk:12s} [{eng}] {text[:70]}")
    print(f"  voice total {ts(total)}")


def build_edl(story: dict, root: Path, build: Path) -> dict:
    sources, clips, overlays = {}, [], []
    defaults = story.get("defaults") or {}
    gap = float(defaults.get("pause_after", 0.25))
    for si, sc in enumerate(story["scenes"]):
        if sc.get("image"):
            ip = root / sc["image"]
            if not ip.exists():
                die(f"scene {si}: image not found: {ip}")
            alias = f"img{si}"
            sources[alias] = str(normalize_image(ip, build, si).resolve())
            vin = 0.0
        elif sc.get("video"):
            alias = f"vid{si}"
            sources[alias] = str((root / sc["video"]).resolve())
            vin = float(sc.get("in", 0))
        else:
            die(f"scene {si}: needs 'image' or 'video'")
        lines = sc.get("lines") or []
        # scene timeline: hold_before, each line (audio + pause), hold_after
        parts = []
        if sc.get("hold_before"):
            parts.append(("beat", None, float(sc["hold_before"])))
        for li, ln in enumerate(lines):
            parts.append(("line", li, ln["_dur"] + float(ln.get("pause_after", gap))))
        if not lines:
            parts.append(("beat", None, float(sc.get("duration", 3.0))))
        if sc.get("hold_after"):
            parts.append(("beat", None, float(sc["hold_after"])))
        T = sum(p[2] for p in parts)
        motion = sc.get("motion", defaults.get("motion", "auto"))
        if motion == "auto":
            motion = AUTO_MOTIONS[si % len(AUTO_MOTIONS)]
        z0, z1, f0, f1 = motion_path(motion)
        first = len(clips)
        t = 0.0
        for kind, li, d in parts:
            c = {"src": alias, "note": f"scene {si}" + (f" line {li}" if li is not None else " beat")}
            if alias.startswith("vid"):
                c.update({"in": round(vin + t, 3), "out": round(vin + t + d, 3)})
            else:
                c.update({"in": 0, "duration": round(d, 3)})
            ln = lines[li] if kind == "line" else {}
            if ln.get("focus") or ln.get("shot"):
                fx, fy = ln.get("focus", [0.5, 0.5])
                z = SHOT_ZOOM.get(ln.get("shot", "medium"), 1.35)
                c.update({"crop_x": fx, "crop_y": fy, "focus": [fx, fy],
                          "zoom": {"from": z, "to": round(z * 1.06, 3)}})
            elif alias.startswith("img") and motion != "none":
                a, b = t / T, (t + d) / T
                c["zoom"] = {"from": lerp(z0, z1, a), "to": lerp(z0, z1, b), "focus_from": lerp(f0, f1, a),
                             "focus_to": lerp(f0, f1, b), "ease": False}
            elif sc.get("crop_x") is not None:
                c["crop_x"] = sc["crop_x"]
            if sc.get("fit"):
                c["fit"] = sc["fit"]
            if kind == "line":
                va = f"v{si}_{li}"
                tp = build / f"transcript.{va}.json"
                words = [{"i": k, "start": w["start"], "end": w["end"], "text": w["text"], "p": 1.0, "filler": None,
                          "speaker": ln.get("speaker", "narrator")} for k, w in enumerate(ln["_words"])]
                save_json({"words": words}, tp)
                sources[va] = {"path": str(Path(ln["_wav"]).resolve()), "transcript": str(tp)}
                c["audio_src"] = va
                if ln.get("sfx"):
                    sfx = ln["sfx"] if isinstance(ln["sfx"], dict) else {"path": ln["sfx"]}
                    overlays.append({"type": "audio", "path": str((root / sfx["path"]).resolve()),
                                     "at": {"clip": len(clips), "t": sfx.get("at", 0)},
                                     "volume": sfx.get("volume", 0.8)})
            elif alias.startswith("vid") and not sc.get("keep_audio"):
                c["mute"] = True
            clips.append(c)
            t += d
        if sc.get("transition") == "fade":
            clips[first]["fade_in"] = 0.35
            if first > 0:
                clips[first - 1]["fade_out"] = 0.35
        if sc.get("title"):
            overlays.append({"type": "text", "text": sc["title"], "at": {"clip": first, "t": 0.15},
                             "duration": float(sc.get("title_duration", 2.2)), "position": "top"})
        for tx in sc.get("texts") or []:
            overlays.append({"type": "text", **{k: v for k, v in tx.items() if k != "at"},
                             "at": {"clip": first, "t": float(tx.get("at", 0))}})
        for fx in sc.get("sfx") or []:
            fx = {"path": fx} if isinstance(fx, str) else fx
            overlays.append({"type": "audio", "path": str((root / fx["path"]).resolve()),
                             "at": {"clip": first, "t": float(fx.get("at", 0))}, "volume": fx.get("volume", 0.8),
                             **({"duration": fx["duration"]} if "duration" in fx else {})})
    if story["scenes"] and story["scenes"][-1].get("transition") != "cut":
        clips[-1].setdefault("fade_out", 0.6)

    cap = story.get("captions", {"style": "pop"})
    cap = {"style": cap} if isinstance(cap, str) else dict(cap)
    cap.setdefault("speaker_colors", {k: v["color"] for k, v in (story.get("voices") or {}).items() if v.get("color")})
    out = dict(story.get("output") or {})
    out["path"] = str((root / out.get("path", Path(story_path_stem(root, story)).name + ".mp4")).resolve())
    audio = {"normalize": True, "target_lufs": -14, "voice_polish": False, "denoise": False,
             **(story.get("audio") or {})}
    if story.get("music"):
        m = story["music"] if isinstance(story["music"], dict) else {"path": story["music"]}
        audio["music"] = {**m, "path": str((root / m["path"]).resolve())}
    edl = {"sources": sources, "output": out, "captions": cap, "audio": audio, "color": story.get("color", {}),
           "overlays": overlays, "clips": clips, "fit": story.get("fit", "crop")}
    for k in ("intro", "outro"):
        if story.get(k):
            v = story[k] if isinstance(story[k], dict) else {"path": story[k]}
            edl[k] = {**v, "path": str((root / v["path"]).resolve())}
    return edl


def story_path_stem(root: Path, story: dict) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in story.get("title", "story")).strip("_").lower() or "story"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["grid", "voices", "build"])
    ap.add_argument("story")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()
    if a.cmd == "grid":
        return cmd_grid(a)
    story = load_json(a.story)
    root = Path(a.story).resolve().parent
    build = root / (Path(a.story).stem + ".build")
    build.mkdir(exist_ok=True)
    table = synth_all(story, root, build)
    print_table(table)
    if a.cmd == "voices":
        return
    edl = build_edl(story, root, build)
    ep = build / "edl.json"
    save_json(edl, ep)
    print(f"[story] {len(edl['clips'])} clips -> {ep}")
    if a.no_render:
        return
    cmd = [sys.executable, str(Path(__file__).parent / "render.py"), str(ep)] + (["--preview"] if a.preview else [])
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
