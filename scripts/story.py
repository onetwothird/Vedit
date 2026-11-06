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
Defaults:   narration "continuous" (whole script voiced in ONE take, cut only between scenes -> natural flow),
            animate (true = motion-graphics card animation, needs background = card template w/o content),
            pause_after, motion, motion_strength (1.0 = 18% zoom; 0.4 = gentle, keeps card edges)
Line keys:  speaker, text (caption), say (optional pronunciation spelling for the voice), focus [x,y] (0..1 on the photo), shot wide|medium|close, pause_after,
            any voice override (voice, rate, pitch, style, engine), sfx (path or {path, volume, at})
"""
from __future__ import annotations

import argparse
import json
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


def scale_motion(path, strength: float):
    """Shrink/grow the zoom of a motion path; 1.0 = default (18% zoom), 0.5 = 9%."""
    z0, z1, f0, f1 = path
    s = lambda z: round(1 + (z - 1) * strength, 4)  # noqa: E731
    return s(z0), s(z1), f0, f1


def retime_caption(display: str, words: list[dict]) -> list[dict]:
    """Voice read a phonetic `say` string; show the real spelling (`text`) over the same time span."""
    import difflib
    toks = display.split()
    if not words or not toks:
        return words
    # align caption tokens to spoken words; identical words keep their exact timing, and a differing span
    # (e.g. "7,641" vs "seven thousand six hundred forty-one") takes exactly the time those words are spoken
    a, b = [_norm(t) for t in toks], [_norm(w["text"]) for w in words]
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            out += [{**words[j1 + k], "text": toks[i1 + k]} for k in range(i2 - i1)]
        elif i2 > i1:
            if j2 > j1:
                s, e = words[j1]["start"], words[j2 - 1]["end"]
            else:
                s = words[j1 - 1]["end"] if j1 else words[0]["start"]
                e = s + 0.05
            n = i2 - i1
            out += [{"start": round(s + (e - s) * k / n, 3), "end": round(s + (e - s) * (k + 1) / n, 3),
                     "text": toks[i1 + k]} for k in range(n)]
    return out


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
    story = load_story(a.story)
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


def trim_line(wav: Path, words: list[dict], dur: float, build: Path, tail_keep: float = 0.2,
              lead_keep: float = 0.03):
    """TTS engines pad ~0.2s before and ~0.8s after every line -> dead air that makes narration drag.
    Cut to the spoken words (+ a short natural tail) and shift word timings to match."""
    s = max(0.0, words[0]["start"] - lead_keep)
    e = min(dur, words[-1]["end"] + tail_keep)
    if s < 0.02 and dur - e < 0.05:
        return wav, words, dur
    out = build / "voice_trim" / f"{Path(wav).stem}_{round(s * 1000)}_{round(e * 1000)}.wav"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-af",
             f"atrim={s:.3f}:{e:.3f},asetpts=PTS-STARTPTS,afade=t=out:st={max(0, e - s - 0.06):.3f}:d=0.06",
             str(out)])
    shifted = [{**w, "start": round(max(0.0, w["start"] - s), 3), "end": round(min(e - s, w["end"] - s), 3)}
               for w in words]
    return out, shifted, round(e - s, 3)


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def tighten_pauses(wav: Path, words: list[dict], dur: float, build: Path, max_pause: float = 0.32,
                   keep_after: float = 0.2, keep_before: float = 0.1):
    """Neural TTS leaves ~0.9s of silence after every sentence, which sounds slow and awkward in a Reel.
    Shorten every gap between words that is longer than max_pause to max_pause (cut from the middle of the
    silence only; the speech itself is untouched) and shift the word timings to match."""
    import wave

    import numpy as np
    sr = 48000
    tmp = build / "voice_tmp_raw.pcm"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-f", "s16le", "-ac", "1", "-ar", str(sr), str(tmp)])
    x = np.fromfile(tmp, dtype=np.int16)
    tmp.unlink(missing_ok=True)
    cuts = []  # (start_s, end_s) of silence to remove
    for a, b in zip(words, words[1:]):
        gap = b["start"] - a["end"]
        if gap > max_pause + 0.02:
            s = a["end"] + keep_after
            e = b["start"] - (max_pause - keep_after)
            e = max(e, s)
            if e - s > 0.02:
                cuts.append((s, e))
    if not cuts:
        return wav, words, dur
    keep, prev = [], 0
    for s, e in cuts:
        keep.append(x[prev:int(s * sr)])
        prev = int(e * sr)
    keep.append(x[prev:])
    fade = int(0.008 * sr)  # tiny fades at each join (inside silence) -> no clicks
    for k in range(len(keep) - 1):
        if len(keep[k]) > fade and len(keep[k + 1]) > fade:
            keep[k] = keep[k].copy()
            keep[k + 1] = keep[k + 1].copy()
            keep[k][-fade:] = (keep[k][-fade:] * np.linspace(1, 0, fade)).astype(np.int16)
            keep[k + 1][:fade] = (keep[k + 1][:fade] * np.linspace(0, 1, fade)).astype(np.int16)
    y = np.concatenate(keep)
    out = build / "voice_cache" / f"{Path(wav).stem}_tight{int(max_pause * 1000)}.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(y.tobytes())

    def shift(t):
        return t - sum(min(e, t) - s for s, e in cuts if t > s)
    new_words = [{**w, "start": round(shift(w["start"]), 3), "end": round(shift(w["end"]), 3)} for w in words]
    return out, new_words, round(len(y) / sr, 4)


def synth_continuous(story: dict, root: Path, build: Path):
    """Voice the WHOLE script in one take (natural flow and intonation across sentences, no restarts),
    then cut it at scene changes only: in the breath between sentences, on exact video-frame boundaries.
    Each scene becomes one clip whose audio is its slice of the master take, so the joined result is the
    original uninterrupted narration."""
    voices = story.get("voices") or {}
    d = story.get("defaults") or {}
    fps = int((story.get("output") or {}).get("fps", 30))
    items = [(si, ln) for si, sc in enumerate(story["scenes"]) for ln in (sc.get("lines") or [])]
    spk = items[0][1].get("speaker", "narrator")
    cfg = dict(voices.get(spk) or {})
    spoken = [ln.get("say") or ln["text"] for _, ln in items]
    wav, words, dur, eng = synth(" ".join(spoken), cfg, build / "voice_cache")
    if d.get("max_pause", 0.32):
        wav, words, dur = tighten_pauses(wav, words, dur, build, float(d.get("max_pause", 0.32)))
    # which words belong to which line: walk the letters (robust to punctuation/hyphen differences)
    bounds, acc = [], 0
    for t in spoken:
        acc += len(_norm(t))
        bounds.append(acc)
    per, pos, k = [[] for _ in items], 0, 0
    for w in words:
        n = len(_norm(w["text"]))
        while k < len(bounds) - 1 and pos + n / 2 > bounds[k]:
            k += 1
        per[k].append(w)
        pos += n
    scene_ids = sorted({si for si, _ in items})
    first = {si: min(w["start"] for (s2, _), ws in zip(items, per) if s2 == si for w in ws) for si in scene_ids}
    last = {si: max(w["end"] for (s2, _), ws in zip(items, per) if s2 == si for w in ws) for si in scene_ids}
    fr = lambda t: round(t * fps) / fps  # noqa: E731
    cuts = [max(0.0, fr(first[scene_ids[0]] - 0.06))]
    for a, b in zip(scene_ids, scene_ids[1:]):
        gap_mid = (last[a] + first[b]) / 2 + float(d.get("cut_offset", 0.0))
        cuts.append(min(max(fr(gap_mid), fr(last[a])), max(fr(first[b]), fr(last[a]))))
    end_hold = float(story["scenes"][scene_ids[-1]].get("hold_after", 0.6))
    cuts.append(fr(min(dur, last[scene_ids[-1]] + 0.25) + end_hold))
    table = []
    sl_dir = build / "voice_slices"
    sl_dir.mkdir(parents=True, exist_ok=True)
    for n, si in enumerate(scene_ids):
        a, b = cuts[n], cuts[n + 1]
        out = sl_dir / f"{Path(wav).stem}_{si}_{round(a * 1000)}_{round(b * 1000)}.wav"
        if not out.exists():
            run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-af",
                 f"atrim=start={a:.4f},asetpts=PTS-STARTPTS,apad,atrim=end={b - a:.4f}", "-ar", "48000", str(out)])
        sc = story["scenes"][si]
        disp, beats = [], []
        for (s2, ln), ws in zip(items, per):
            if s2 != si or not ws:
                continue
            ws = [{**w, "start": round(w["start"] - a, 3), "end": round(w["end"] - a, 3)} for w in ws]
            beats.append(ws[0]["start"])
            disp += retime_caption(ln["text"], ws)  # subtitles show the script's exact spelling
        joined = " ".join(ln["text"] for s2, ln in items if s2 == si)
        sc["lines"] = [{"speaker": spk, "text": joined, "_wav": out, "_words": disp, "_dur": round(b - a, 4),
                        "_engine": eng, "pause_after": 0}]
        sc["_beats"] = beats
        sc.pop("hold_after", None)
        sc.pop("hold_before", None)
        table.append((si, 0, spk, b - a, eng, joined))
    return table


def synth_all(story: dict, root: Path, build: Path):
    if (story.get("defaults") or {}).get("narration") == "continuous":
        return synth_continuous(story, root, build)
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
            spoken = ln.get("say") or ln["text"]  # "say" = pronunciation spelling, "text" = caption
            wav, words, dur, eng = synth(spoken, cfg, cache)
            if story.get("defaults", {}).get("trim_silence", True) and words:
                wav, words, dur = trim_line(wav, words, dur, build,
                                            float(story.get("defaults", {}).get("tail_keep", 0.2)))
            if ln.get("say"):
                words = retime_caption(ln["text"], words)
            ln["_wav"], ln["_words"], ln["_dur"], ln["_engine"] = wav, words, dur, eng
            table.append((si, li, spk, dur, eng, spoken))
    return table


def print_table(table):
    total = 0.0
    for si, li, spk, dur, eng, text in table:
        total += dur
        print(f"  s{si}.l{li}  {dur:5.2f}s  {spk:12s} [{eng}] {text[:70]}")
    print(f"  voice total {ts(total)}")


def animate_scene(story: dict, sc: dict, root: Path, build: Path, si: int, parts, T: float, t0: float) -> Path:
    """Render a motion-graphics clip of a fact card (see animate_card.py); cached by content + timing."""
    import hashlib

    from animate_card import render as animate
    defaults = story.get("defaults") or {}
    bg = sc.get("background") or defaults.get("background")
    if not bg:
        die(f"scene {si}: 'animate' needs defaults.background (the card template without content)")
    card, bgp = root / sc["image"], root / bg
    for p in (card, bgp):
        if not p.exists():
            die(f"scene {si}: not found: {p}")
    beats, t = [], 0.0
    for kind, _li, d in parts:
        if kind == "line":
            beats.append(round(t, 3))
        t += d
    if sc.get("_beats"):  # continuous narration: pulse when each script line starts
        beats = [round(b, 3) for b in sc["_beats"]]
    key = hashlib.md5(json.dumps([str(card), card.stat().st_mtime, str(bgp), bgp.stat().st_mtime, round(T, 3),
                                  beats, round(t0, 3), 2]).encode()).hexdigest()[:10]
    out = build / f"anim{si}_{key}.mp4"
    if not out.exists():
        print(f"[story] animating scene {si} ({T:.1f}s)")
        animate(card, bgp, out, T, beats[1:], t0)
    return out


def build_edl(story: dict, root: Path, build: Path) -> dict:
    sources, clips, overlays = {}, [], []
    defaults = story.get("defaults") or {}
    gap = float(defaults.get("pause_after", 0.25))
    scene_t0 = 0.0  # global start time of each scene (keeps the animated background drift continuous)
    for si, sc in enumerate(story["scenes"]):
        if sc.get("image") and sc.get("animate", defaults.get("animate")):
            alias, vin = f"vid{si}", 0.0  # rendered below, once the scene length is known
        elif sc.get("image"):
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
        if alias.startswith("vid") and sc.get("image"):
            sources[alias] = str(animate_scene(story, sc, root, build, si, parts, T, scene_t0).resolve())
            sc = {**sc, "motion": "none"}
        scene_t0 += T
        motion = sc.get("motion", defaults.get("motion", "auto"))
        if motion == "auto":
            motion = AUTO_MOTIONS[si % len(AUTO_MOTIONS)]
        z0, z1, f0, f1 = scale_motion(motion_path(motion),
                                      float(sc.get("motion_strength", defaults.get("motion_strength", 1.0))))
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


def load_story(path) -> dict:
    """story.json may say "extends": "../../channel.json" to share voices/captions/defaults across a series.
    Dicts merge one level deep (the story wins); paths inside stay relative to the STORY file."""
    story = load_json(path)
    if story.get("extends"):
        base = load_story(Path(path).resolve().parent / story.pop("extends"))
        for k, v in story.items():
            base[k] = {**base[k], **v} if isinstance(v, dict) and isinstance(base.get(k), dict) else v
        story = base
    return story


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["grid", "voices", "build"])
    ap.add_argument("story")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()
    if a.cmd == "grid":
        return cmd_grid(a)
    story = load_story(a.story)
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
