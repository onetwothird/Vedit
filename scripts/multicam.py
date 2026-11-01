#!/usr/bin/env python3
"""Multi-camera / multi-mic helpers (podcasts, interviews, B-cam footage). Needs numpy.

  python multicam.py sync REF.mp4 CAM2.mp4 [CAM3.mp4 ...]
      Audio cross-correlation. Prints offset per file: offset = (time in that file) - (time in REF)
      for the same moment. Put it in edl.json: "sources": {"cam2": {"path": "CAM2.mp4", "offset": 1.234}}

  python multicam.py speakers MIC_A.wav MIC_B.wav --names host guest [--offsets 0 1.234] [--out DIR]
      With one isolated mic per person: who is talking when (loudest mic wins, with hysteresis).
      Writes speakers.json + speakers.txt (timeline in REF time) -> use it to pick camera angles.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import die, need, save_json, ts  # noqa: E402

try:
    import numpy as np
except ImportError:
    die("numpy required: pip install numpy")


def pcm(path: str, sr: int, start: float = 0.0, dur: float | None = None) -> "np.ndarray":
    need("ffmpeg")
    cmd = ["ffmpeg", "-v", "error", "-ss", str(max(0.0, start)), "-i", path]
    if dur:
        cmd += ["-t", str(dur)]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def offset(ref: "np.ndarray", other: "np.ndarray", sr: int) -> tuple[float, float]:
    def env(x):  # onset envelope is robust to different mics/EQ
        hop = sr // 100
        n = len(x) // hop
        e = np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(1) + 1e-9)
        if n == 0:
            die("no audio decoded — does the file have an audio track?")
        e = np.log(e)
        d = np.diff(e, prepend=e[0])
        d[d < 0] = 0
        return (d - d.mean()) / (d.std() + 1e-9)
    a, b = env(ref), env(other)
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    c = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    c = np.concatenate([c[-(len(b) - 1):], c[: len(a)]])
    k = int(np.argmax(c))
    lag = k - (len(b) - 1)                      # ref[i] ~ other[i - lag]
    conf = float((c[k] - c.mean()) / (c.std() + 1e-9))
    return -lag / 100.0, conf                    # other_time = ref_time + offset


def cmd_sync(a):
    sr = 8000
    ref = pcm(a.ref, sr, 0, a.window)
    for f in a.others:
        o = pcm(f, sr, 0, a.window)
        off, conf = offset(ref, o, sr)
        q = "good" if conf > 8 else ("ok" if conf > 5 else "LOW — verify visually (clap/slate)")
        print(f"{f}\toffset={off:+.3f}s\tconfidence={conf:.1f} ({q})")


def cmd_speakers(a):
    sr, win = 8000, 0.25
    names = a.names or [Path(m).stem for m in a.mics]
    offs = a.offsets or [0.0] * len(a.mics)
    sigs = []
    for m, off in zip(a.mics, offs):
        x = pcm(m, sr, max(0.0, off))
        if off < 0:
            x = np.concatenate([np.zeros(int(-off * sr), np.float32), x])
        sigs.append(x)
    n = min(len(s) for s in sigs) // int(sr * win)
    hop = int(sr * win)
    rms = np.array([[np.sqrt((s[i * hop:(i + 1) * hop] ** 2).mean() + 1e-12) for i in range(n)] for s in sigs])
    db = 20 * np.log10(rms)
    floor = np.percentile(db, 20, axis=1, keepdims=True)
    act = db - floor
    talking = act.max(0) > 10
    who = act.argmax(0)
    hold = int(a.min_hold / win)
    cur, since, segs = None, 0, []
    for i in range(n):
        cand = int(who[i]) if talking[i] else cur
        if cand != cur and (cur is None or i - since >= hold):
            if cur is not None:
                segs.append({"speaker": names[cur], "start": round(since * win, 2), "end": round(i * win, 2)})
            cur, since = cand, i
    if cur is not None:
        segs.append({"speaker": names[cur], "start": round(since * win, 2), "end": round(n * win, 2)})
    # merge consecutive same-speaker
    merged = []
    for s in segs:
        if merged and merged[-1]["speaker"] == s["speaker"]:
            merged[-1]["end"] = s["end"]
        else:
            merged.append(s)
    out = Path(a.out or Path(a.mics[0]).resolve().parent / (Path(a.mics[0]).stem + ".edit"))
    save_json(merged, out / "speakers.json")
    lines = [f"[{ts(s['start'])} → {ts(s['end'])}] {s['speaker']}" for s in merged]
    (out / "speakers.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:40]) + ("\n..." if len(lines) > 40 else ""))
    print(f"-> {out / 'speakers.json'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync")
    s.add_argument("ref")
    s.add_argument("others", nargs="+")
    s.add_argument("--window", type=float, default=600, help="seconds of audio to compare (default 600)")
    s.set_defaults(fn=cmd_sync)
    p = sub.add_parser("speakers")
    p.add_argument("mics", nargs="+")
    p.add_argument("--names", nargs="*")
    p.add_argument("--offsets", nargs="*", type=float)
    p.add_argument("--min-hold", type=float, default=1.5, help="min seconds before switching speaker")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_speakers)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
