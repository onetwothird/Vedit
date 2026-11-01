#!/usr/bin/env python3
"""One-shot analysis of raw footage. Run this FIRST on every source clip.

Usage:
  python analyze.py VIDEO [VIDEO2 ...] [--names main cam2] [--model small] [--language en] [--no-transcribe]

Writes into <first video>.edit/ (or --out):
  analysis.<name>.json   probe info, loudness, silences, scene cuts
  transcript.<name>.txt  readable transcript with word numbers, pauses, fillers, flubbed takes  <- read this
  transcript.<name>.json word timestamps (used by autocut/captions/render)
  frames/overview_<name>_*.jpg  contact sheets across the whole clip                        <- look at these
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import ffprobe, need, run, save_json, ts, workdir_for  # noqa: E402


def loudness(video: str) -> dict:
    # loudnorm's measurement mode works on FFmpeg 4.x through 7.x (ebur128 options differ between versions)
    res = run(["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-vn", "-af", "loudnorm=print_format=json",
               "-f", "null", "-"], capture=True)
    out = {}
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", res.stderr)
    if m:
        import json
        d = json.loads(m.group(0))
        for key, src in (("integrated_lufs", "input_i"), ("lra", "input_lra"), ("true_peak_db", "input_tp")):
            try:
                out[key] = float(d[src])
            except (KeyError, ValueError):
                pass
    res = run(["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-vn", "-af", "volumedetect", "-f", "null", "-"],
              capture=True)
    for key in ("mean_volume", "max_volume"):
        m = re.search(key + r":\s+(-?[\d.]+) dB", res.stderr)
        if m:
            out[key + "_db"] = float(m.group(1))
    return out


def silences(video: str, noise_db: float, min_dur: float) -> list[dict]:
    res = run(["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-vn",
               "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"], capture=True)
    out, start = [], None
    for line in res.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            out.append({"start": round(start, 3), "end": round(float(m.group(1)), 3)})
            start = None
    if start is not None:
        out.append({"start": round(start, 3), "end": None})
    return out


def scenes(video: str, threshold: float) -> list[float]:
    res = run(["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-an", "-vf",
               f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"], capture=True)
    return [round(float(m.group(1)), 3) for m in re.finditer(r"pts_time:([\d.]+)", res.stderr)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("videos", nargs="+")
    ap.add_argument("--names", nargs="*", help="alias per video (default main, src2, src3...)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--model", default="small")
    ap.add_argument("--language", default=None)
    ap.add_argument("--no-transcribe", action="store_true")
    ap.add_argument("--scene-threshold", type=float, default=0.3)
    ap.add_argument("--min-silence", type=float, default=0.35)
    a = ap.parse_args()
    need("ffmpeg")

    names = a.names or []
    names += ["main" if i == 0 else f"src{i + 1}" for i in range(len(names), len(a.videos))]
    wd = workdir_for(a.videos[0], a.out)
    from frames import sheets  # noqa: E402

    for video, name in zip(a.videos, names):
        print(f"\n=== {name}: {video}", file=sys.stderr)
        info = ffprobe(video)
        rep = {"name": name, "probe": info}
        if info["has_audio"]:
            rep["loudness"] = loudness(video)
            mean = rep["loudness"].get("mean_volume_db", -30)
            noise = max(-55.0, min(-28.0, mean - 14))  # adaptive silence threshold
            rep["silence_threshold_db"] = noise
            rep["silences"] = silences(video, noise, a.min_silence)
        if info["has_video"]:
            rep["scene_cuts"] = scenes(video, a.scene_threshold)
            dur = info["duration"]
            every = max(2.0, dur / 24)
            n = int(dur // every) or 1
            times = [every * (k + 0.5) for k in range(n)][:24]
            rep["overview_sheets"] = [str(p) for p in sheets(video, times, wd / "frames", 400, 4, False,
                                                             f"overview_{name}")]
        save_json(rep, wd / f"analysis.{name}.json")

        if info["has_audio"] and not a.no_transcribe:
            import subprocess
            cmd = [sys.executable, str(Path(__file__).parent / "transcribe.py"), video, "--name", name,
                   "--out", str(wd), "--model", a.model]
            if a.language:
                cmd += ["--language", a.language]
            if subprocess.call(cmd) != 0:
                print("[analyze] transcription failed — continue with silences/frames only", file=sys.stderr)

        sil = rep.get("silences", [])
        dead = sum(((s["end"] or info["duration"]) - s["start"]) for s in sil)
        print(f"\n[{name}] {ts(info['duration'])}  {info.get('width')}x{info.get('height')} @ {info.get('fps')}fps  "
              f"audio={info['has_audio']}")
        if "loudness" in rep:
            print(f"[{name}] loudness {rep['loudness'].get('integrated_lufs')} LUFS, "
                  f"{len(sil)} silences totalling {dead:.1f}s ({100 * dead / max(info['duration'], 1):.0f}%)")
        if info["has_video"]:
            print(f"[{name}] {len(rep['scene_cuts'])} scene cuts; overview sheets:")
            for p in rep["overview_sheets"]:
                print("   ", p)
    print(f"\nworkdir: {wd}")


if __name__ == "__main__":
    main()
