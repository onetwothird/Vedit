#!/usr/bin/env python3
"""Render edl.json -> finished video.

  python render.py edl.json              # full quality
  python render.py edl.json --preview    # half-res, fast — use while iterating
  python render.py edl.json --clips 0-12 # only part of the timeline (quick checks)
  python render.py edl.json --formats 9:16,1:1   # same edit, extra aspect ratios (face-tracked crops)

Pass 1 renders every clip to a cached segment (re-renders only clips you changed).
Pass 2 joins them and applies overlays/b-roll, captions, music ducking, voice polish and loudness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import captions as cap  # noqa: E402
from common import even, filter_path, need, ts  # noqa: E402
from edl import Timeline  # noqa: E402


def atempo_chain(speed: float) -> list[str]:
    out, s = [], speed
    while s > 2.0:
        out.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        out.append("atempo=0.5")
        s /= 0.5
    if abs(s - 1.0) > 1e-3:
        out.append(f"atempo={s:.5f}")
    return out


def color_filters(c: dict, d: str | Path) -> list[str]:
    f = []
    eq = {k: c[k] for k in ("brightness", "contrast", "saturation", "gamma") if k in c}
    if eq:
        f.append("eq=" + ":".join(f"{k}={v}" for k, v in eq.items()))
    if c.get("lut"):
        lut = Path(c["lut"]) if Path(c["lut"]).is_absolute() else Path(d) / c["lut"]
        f.append(f"lut3d=file='{filter_path(lut)}'")
    if c.get("sharpen"):
        f.append("unsharp=5:5:0.6:5:5:0.0")
    if c.get("vignette"):
        f.append("vignette=PI/5")
    return f


def video_chain(tl: Timeline, c: dict) -> str:
    s = tl.sources[c["src"]]
    sw, sh = s["info"]["width"], s["info"]["height"]
    OW, OH, F = tl.out["width"], tl.out["height"], tl.out["fps"]
    r = OW / OH
    f = ["setpts=PTS-STARTPTS"]
    if c["speed"] != 1.0:
        f.append(f"setpts=PTS/{c['speed']}")
    f.append(f"fps={F}")
    fit = c.get("fit", tl.edl.get("fit", "crop"))
    z = c.get("zoom", 1.0)
    zfrom, zto = (z.get("from", 1.0), z.get("to", 1.15)) if isinstance(z, dict) else (float(z), float(z))
    pan = isinstance(z, dict) and ("focus_from" in z or "focus_to" in z)
    animated = zfrom != zto or pan
    if fit in ("pad", "blur") and abs(sw / sh - r) > 0.02:
        fg = f"scale={OW}:{OH}:force_original_aspect_ratio=decrease"
        if fit == "pad":
            f += [fg, f"pad={OW}:{OH}:(ow-iw)/2:(oh-ih)/2:black"]
        else:  # blurred background fill
            pre = ",".join(f)
            return (f"{pre},split[ba][bb];[ba]scale={OW}:{OH}:force_original_aspect_ratio=increase,"
                    f"crop={OW}:{OH},boxblur=30:2,eq=brightness=-0.08[bg];[bb]{fg}[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1" + tail(tl, c))
    else:
        if sw / sh > r:
            cw, ch = sh * r, sh
        else:
            cw, ch = sw, sw / r
        cx_c = c.get("crop_x", 0.5)
        if cx_c == "auto":
            cx_c = 0.5
        cy_c = c.get("crop_y", 0.5)
        if not animated and zfrom > 1.0:
            fx, fy = c.get("focus", [cx_c, cy_c])
            cw, ch = cw / zfrom, ch / zfrom
            cx_c, cy_c = fx, fy
        cx = min(max(0, cx_c * sw - cw / 2), sw - cw)
        cy = min(max(0, cy_c * sh - ch / 2), sh - ch)
        f.append(f"crop={even(cw)}:{even(ch)}:{int(cx)}:{int(cy)}")
        if animated:
            n = max(1, round(c["dur"] * F))
            zm = max(1.0001, (zfrom + zto) / 2)
            if "focus" in c:  # subject point in SOURCE coords -> zoompan offset fraction inside the crop window
                ux = (c["focus"][0] * sw - cx) / cw
                uy = (c["focus"][1] * sh - cy) / ch
                conv = [min(1.0, max(0.0, (u - 1 / (2 * zm)) / (1 - 1 / zm))) for u in (ux, uy)]
            else:
                conv = [0.5, 0.5]
            f0 = z.get("focus_from", conv) if isinstance(z, dict) else conv
            f1 = z.get("focus_to", f0) if isinstance(z, dict) else f0
            ease = f"(1-cos(PI*on/{n}))/2" if (isinstance(z, dict) and z.get("ease", True)) else f"on/{n}"
            fx = f"({f0[0]}+({f1[0]}-{f0[0]})*{ease})"
            fy = f"({f0[1]}+({f1[1]}-{f0[1]})*{ease})"
            f += [f"scale={OW * 2}:{OH * 2}:flags=lanczos",
                  f"zoompan=z='{zfrom}+({zto}-{zfrom})*{ease}':x='(iw-iw/zoom)*{fx}':y='(ih-ih/zoom)*{fy}'"
                  f":d=1:s={OW}x{OH}:fps={F}"]
        else:
            f.append(f"scale={OW}:{OH}:flags=lanczos")
        f.append("setsar=1")
    return ",".join(f) + tail(tl, c)


def tail(tl: Timeline, c: dict) -> str:
    f = color_filters({**(tl.edl.get("color") or {}), **(c.get("color") or {})}, tl.dir)
    if c.get("fade_in"):
        f.append(f"fade=t=in:st=0:d={c['fade_in']}")
    if c.get("fade_out"):
        f.append(f"fade=t=out:st={max(0, c['dur'] - c['fade_out']):.3f}:d={c['fade_out']}")
    f += ["format=yuv420p", f"trim=duration={c['dur']:.4f}"]
    return "," + ",".join(f)


def audio_chain(c: dict) -> str:
    d = c["dur"]
    f = ["asetpts=PTS-STARTPTS"] + atempo_chain(c["speed"])
    if c.get("volume", 1.0) != 1.0:
        f.append(f"volume={c['volume']}")
    f += ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo", "apad", f"atrim=0:{d:.4f}"]
    fi, fo = max(0.012, float(c.get("fade_in", 0))), max(0.012, float(c.get("fade_out", 0)))
    f += [f"afade=t=in:st=0:d={fi}", f"afade=t=out:st={max(0, d - fo):.4f}:d={fo}"]
    return ",".join(f)


def seg_cmd(tl: Timeline, c: dict, out: Path, preview: bool) -> list[str]:
    s = tl.sources[c["src"]]
    span = c["dur"] * c["speed"] + 0.25
    if s.get("is_image"):
        cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", str(tl.out["fps"]), "-t", f"{span:.4f}",
               "-i", s["path"]]
    else:
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{c['vin']:.4f}", "-t", f"{span:.4f}", "-i", s["path"]]
    a = tl.sources[c["audio_alias"]]
    if a["info"]["has_audio"] and not c.get("mute"):
        cmd += ["-ss", f"{max(0.0, c['ain']):.4f}", "-t", f"{span:.4f}", "-i", a["path"]]
    else:
        cmd += ["-f", "lavfi", "-t", f"{span:.4f}", "-i", "anullsrc=r=48000:cl=stereo"]
    vc = video_chain(tl, c)
    fc = f"[0:v]{vc}[v];[1:a]{audio_chain(c)}[a]"
    q = ["-preset", "ultrafast", "-crf", "26"] if preview else ["-preset", "veryfast", "-crf", "15"]
    return cmd + ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", *q,
                  "-c:a", "pcm_s16le", "-t", f"{c['dur']:.4f}", str(out)]


def render_segments(tl: Timeline, cache: Path, preview: bool, jobs: int) -> list[Path]:
    cache.mkdir(parents=True, exist_ok=True)
    from reframe import auto_crop_x
    for c in tl.clips:
        if c.get("crop_x") == "auto":
            s = tl.sources[c["src"]]
            c["crop_x"] = auto_crop_x(s["path"], c["vin"], c["vin"] + c["dur"] * c["speed"])
    work = []
    for c in tl.clips:
        tmp = cache / "x.mkv"
        key = json.dumps(seg_cmd(tl, c, tmp, preview) + [str(os.path.getmtime(tl.sources[c["src"]]["path"]))])
        out = cache / (hashlib.sha1(key.encode()).hexdigest()[:16] + ".mkv")
        work.append((c, out))
    todo = [(c, o) for c, o in work if not o.exists()]
    print(f"[render] {len(work)} clips ({len(work) - len(todo)} cached), output {tl.out['width']}x"
          f"{tl.out['height']} @ {tl.out['fps']}fps, duration {ts(tl.duration)}", file=sys.stderr)

    def one(item):
        c, o = item
        part = o.with_suffix(".part.mkv")
        r = subprocess.run(seg_cmd(tl, c, part, preview), capture_output=True, text=True)
        if r.returncode != 0:
            return f"clip {c['index']} ({c['src']} {ts(c['vin'])}): " + "\n".join(r.stderr.splitlines()[-8:])
        part.rename(o)
        return None

    done = 0
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        for err in ex.map(one, todo):
            done += 1
            print(f"\r[render] segments {done}/{len(todo)}", end="", file=sys.stderr)
            if err:
                print(f"\nerror: {err}", file=sys.stderr)
                sys.exit(1)
    if todo:
        print(file=sys.stderr)
    return [o for _, o in work]


def final_pass(tl: Timeline, segs: list[Path], ass: Path | None, out: Path, preview: bool):
    wd = tl.dir
    lst = wd / ".render_cache" / "concat.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in segs), encoding="utf-8")
    OW, OH, D = tl.out["width"], tl.out["height"], tl.duration
    inputs = ["-f", "concat", "-safe", "0", "-i", str(lst)]
    fc, vlab, n_in = [], "0:v", 1

    for k, ov in enumerate(o for o in (tl.edl.get("overlays") or []) if o.get("type") in ("image", "video")):
        at = tl.anchor(ov.get("at", 0))
        if at is None:
            print(f"[render] overlay {ov.get('path')} anchored to a cut word — skipped", file=sys.stderr)
            continue
        dur = D - at if ov.get("duration") == "full" else float(ov.get("duration", 3))
        p = Path(ov["path"]) if Path(ov["path"]).is_absolute() else wd / ov["path"]
        if ov["type"] == "image":
            inputs += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(p)]
        else:
            inputs += ["-ss", str(ov.get("src_in", 0)), "-t", f"{dur:.3f}", "-i", str(p)]
        mode = ov.get("mode", "full")
        if mode == "full":
            sc = f"scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH}"
            xy = "0:0"
        else:
            pw = even(OW * float(ov.get("scale", 0.35)))
            sc = f"scale={pw}:-2"
            m = round(OW * 0.04)
            xy = {"top-right": f"W-w-{m}:{m}", "top-left": f"{m}:{m}", "bottom-left": f"{m}:H-h-{m}",
                  "bottom-right": f"W-w-{m}:H-h-{m}", "center": "(W-w)/2:(H-h)/2"}[ov.get("position", "top-right")]
        fade = float(ov.get("fade", 0.25))
        op = float(ov.get("opacity", 1.0))
        ch = f"[{n_in}:v]{sc},setsar=1,format=yuva420p"
        if op < 1:
            ch += f",colorchannelmixer=aa={op}"
        if fade > 0:
            ch += f",fade=t=in:st=0:d={fade}:alpha=1,fade=t=out:st={max(0, dur - fade):.3f}:d={fade}:alpha=1"
        fc.append(f"{ch},setpts=PTS-STARTPTS+{at:.3f}/TB[ov{k}]")
        fc.append(f"[{vlab}][ov{k}]overlay={xy}:enable='between(t,{at:.3f},{at + dur:.3f})':eof_action=pass[vo{k}]")
        vlab = f"vo{k}"
        n_in += 1

    if ass:
        fonts = Path(__file__).resolve().parent.parent / "fonts"
        fd = f":fontsdir='{filter_path(fonts)}'" if fonts.exists() else ""
        fc.append(f"[{vlab}]ass='{ass.name}'{fd}[vcap]")
        vlab = "vcap"
    fc.append(f"[{vlab}]format=yuv420p[vout]")

    au = {"normalize": True, "target_lufs": -14, "voice_polish": True, "denoise": False,
          **(tl.edl.get("audio") or {})}
    af = ["highpass=f=70"]
    if au.get("denoise"):
        af.append("afftdn=nf=-25:tn=1")
    if au.get("voice_polish"):
        af += ["acompressor=threshold=-21dB:ratio=3:attack=5:release=150:makeup=2",
               "equalizer=f=3500:t=q:w=1.2:g=2"]
    fc.append(f"[0:a]{','.join(af)}[voice0]")
    sfx = []
    for k, ov in enumerate(o for o in (tl.edl.get("overlays") or []) if o.get("type") == "audio"):
        at = tl.anchor(ov.get("at", 0))
        if at is None:
            continue
        p = Path(ov["path"]) if Path(ov["path"]).is_absolute() else wd / ov["path"]
        inputs += (["-ss", str(ov.get("src_in", 0))] + (["-t", str(ov["duration"])] if "duration" in ov else [])
                   + ["-i", str(p)])
        ms = int(round(at * 1000))
        fc.append(f"[{n_in}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                  f"volume={ov.get('volume', 0.8)},adelay={ms}|{ms}[sfx{k}]")
        sfx.append(f"[sfx{k}]")
        n_in += 1
    if sfx:
        fc.append(f"[voice0]{''.join(sfx)}amix=inputs={len(sfx) + 1}:normalize=0:duration=first[voice]")
    else:
        fc.append("[voice0]anull[voice]")
    alab = "voice"
    mus = au.get("music")
    if mus:
        mus = {"path": mus} if isinstance(mus, str) else mus
        mp = Path(mus["path"]) if Path(mus["path"]).is_absolute() else wd / mus["path"]
        inputs += ["-stream_loop", "-1", "-i", str(mp)]
        fi, fo, vol = mus.get("fade_in", 1.0), mus.get("fade_out", 2.5), mus.get("volume", 0.18)
        fc.append(f"[{n_in}:a]atrim=0:{D:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
                  f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={vol},afade=t=in:st=0:d={fi},"
                  f"afade=t=out:st={max(0, D - fo):.3f}:d={fo}[mus]")
        if mus.get("duck", True):
            fc.append("[voice]asplit[v1][v2]")
            fc.append("[mus][v2]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[duck]")
            fc.append("[v1][duck]amix=inputs=2:normalize=0:duration=first[mix]")
        else:
            fc.append("[voice][mus]amix=inputs=2:normalize=0:duration=first[mix]")
        alab = "mix"
        n_in += 1
    if au.get("normalize", True):
        fc.append(f"[{alab}]loudnorm=I={au.get('target_lufs', -14)}:TP=-1.5:LRA=11,aresample=48000[aout]")
    else:
        fc.append(f"[{alab}]anull[aout]")

    q = ["-preset", "veryfast", "-crf", "26"] if preview else ["-preset", tl.out["preset"], "-crf", str(tl.out["crf"])]
    cmd = ["ffmpeg", "-y", "-v", "error", "-stats", *inputs, "-filter_complex", ";".join(fc),
           "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", *q, "-pix_fmt", "yuv420p",
           "-r", str(tl.out["fps"]), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
           "-t", f"{D:.3f}", str(out)]
    (wd / ".render_cache" / "last_final_cmd.txt").write_text(" ".join(cmd))
    r = subprocess.run(cmd, cwd=wd, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        print("\n".join(r.stderr.splitlines()[-20:]), file=sys.stderr)
        sys.exit("error: final render failed (full command in .render_cache/last_final_cmd.txt)")


def write_chapters(tl: Timeline, out: Path) -> Path | None:
    """YouTube chapters from edl "chapters": [{"at": anchor, "title": "..."}]. First chapter forced to 0:00."""
    rows = []
    for ch in tl.edl.get("chapters") or []:
        t = tl.anchor(ch.get("at", 0))
        if t is not None:
            rows.append((t, ch["title"]))
    if not rows:
        return None
    rows.sort()
    rows[0] = (0.0, rows[0][1])
    rows = [r for i, r in enumerate(rows) if i == 0 or r[0] - rows[i - 1][0] >= 10]  # YouTube: >=10 s apart

    def yt(t):
        m, s = divmod(int(t), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    p = out.with_suffix(".chapters.txt")
    p.write_text("\n".join(f"{yt(t)} {title}" for t, title in rows) + "\n", encoding="utf-8")
    return p


def render_one(edl: str, aspect: str | None, a) -> Path:
    tl = Timeline(edl, aspect=aspect)
    if a.preview:
        tl.out["width"], tl.out["height"] = even(tl.out["width"] / 2), even(tl.out["height"] / 2)
    if a.clips:
        lo, hi = (int(x) for x in a.clips.split("-"))
        tl.clips = tl.clips[lo:hi + 1]
        t = 0.0
        for c in tl.clips:
            c["t0"], t = t, t + c["dur"]
            c["t1"] = t
        tl.duration = t
    out = Path(a.out) if (a.out and not aspect) else Path(tl.out["path"])
    if a.preview and not a.out:
        out = out.with_name(out.stem + "_preview" + out.suffix)
    if a.clips:
        out = out.with_name(out.stem + f"_clips{a.clips}" + out.suffix)
    out.parent.mkdir(parents=True, exist_ok=True)
    tl.out["path"] = str(out)

    segs = render_segments(tl, tl.dir / ".render_cache", a.preview, a.jobs)
    ass, n = cap.write(tl, tl.dir / ("captions.ass" if not aspect else f"captions_{aspect.replace(':', 'x')}.ass"))
    if ass:
        print(f"[render] {n} caption/text events", file=sys.stderr)
    final_pass(tl, segs, ass, out, a.preview)
    chap = write_chapters(tl, out)
    print(f"\n[render] done -> {out}  ({ts(tl.duration)}, {tl.out['width']}x{tl.out['height']})")
    if Path(out).with_suffix(".srt").exists():
        print(f"[render] subtitles -> {Path(out).with_suffix('.srt')}")
    if chap:
        print(f"[render] chapters  -> {chap}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("edl")
    ap.add_argument("--preview", action="store_true", help="half resolution, fast encode")
    ap.add_argument("--clips", help="render only a clip range, e.g. 0-12")
    ap.add_argument("--formats", help="also export other aspect ratios from the same edit, e.g. 9:16,1:1")
    ap.add_argument("--out", help="override output path")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    a = ap.parse_args()
    need("ffmpeg")
    outs = [render_one(a.edl, None, a)]
    for asp in [x.strip() for x in (a.formats or "").split(",") if x.strip()]:
        outs.append(render_one(a.edl, asp, a))
    print(f"[render] check it: python {Path(__file__).parent / 'frames.py'} \"{outs[0]}\" --count 12")


if __name__ == "__main__":
    main()
