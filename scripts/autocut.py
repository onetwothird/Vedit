#!/usr/bin/env python3
"""Draft an edl.json automatically: drop dead air, hard fillers (um/uh) and flubbed takes.
It's a STARTING POINT — Claude should then read the transcript and refine the clips.

  python autocut.py WORKDIR [--preset youtube|shorts|tiktok|reels|square|podcast|tutorial|clean] [--brand brand.json]
                    [--src main] [--max-gap 0.45] [--keep-fillers] [--keep-retakes]
                    [--punchin 1.12] [--aspect 16:9|9:16|1:1|4:5] [--captions pop|box|clean|off]
                    [--mode words|silence] [--out edl.json]

--mode words   (default when a transcript exists) cut on word timing: tightest, best for talking.
--mode silence (no transcript / music / b-roll) keep everything that isn't silent.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import load_json, save_json, ts  # noqa: E402
from presets import PRESETS, apply_brand, find_brand, get_preset  # noqa: E402


def by_words(tr: dict, max_gap: float, keep_fillers: bool, keep_retakes: bool, min_clip: float):
    W = tr["words"]
    drop = set()
    if not keep_fillers:
        drop |= {w["i"] for w in W if w["filler"] == "hard"}
    if not keep_retakes:
        for s in tr["sentences"]:
            if "retake_of_later" in s:
                drop |= set(range(s["w0"], s["w1"] + 1))
    keep = [w for w in W if w["i"] not in drop]
    ranges, cur = [], None
    for w in keep:
        # extend only if no dropped word sits between and the pause is short
        if cur and w["i"] == cur[1] + 1 and w["start"] - W[cur[1]]["end"] <= max_gap:
            cur[1] = w["i"]
        else:
            if cur:
                ranges.append(cur)
            cur = [w["i"], w["i"]]
    if cur:
        ranges.append(cur)
    out = []
    for a, b in ranges:
        if W[b]["end"] - W[a]["start"] < min_clip and out:
            # tiny fragment: glue onto previous range if close, else keep anyway
            pa, pb = out[-1]
            if W[a]["start"] - W[pb]["end"] < 1.0 and not any(k in drop for k in range(pb + 1, a)):
                out[-1] = [pa, b]
                continue
        out.append([a, b])
    return [{"src": "", "words": r, "note": " ".join(W[i]["text"] for i in range(r[0], min(r[1] + 1, r[0] + 8)))
             + ("…" if r[1] - r[0] >= 8 else "")} for r in out]


def by_silence(analysis: dict, keep_pause: float):
    dur = analysis["probe"]["duration"]
    sil = analysis.get("silences", [])
    clips, t = [], 0.0
    for s in sil:
        end = s["end"] if s["end"] is not None else dur
        if s["start"] - t > 0.05:
            clips.append({"in": round(max(0.0, t - keep_pause / 2), 3), "out": round(s["start"] + keep_pause / 2, 3)})
        t = end
    if dur - t > 0.05:
        clips.append({"in": round(max(0.0, t - keep_pause / 2), 3), "out": round(dur, 3)})
    return clips or [{"in": 0, "out": dur}]


def make_edl(wd: Path, src: str = "main", mode: str | None = None, max_gap: float = 0.45,
             keep_fillers: bool = False, keep_retakes: bool = False, min_clip: float = 0.6, punchin: float = 0.0,
             aspect: str | None = None, captions: str = "pop", lufs: float = -14, fit: str | None = None,
             brand: Path | None = None, out_path: str | None = None) -> tuple[dict, dict]:
    an = load_json(wd / f"analysis.{src}.json")
    trp = wd / f"transcript.{src}.json"
    mode = mode or ("words" if trp.exists() else "silence")
    if mode == "words":
        tr = load_json(trp)
        clips = by_words(tr, max_gap, keep_fillers, keep_retakes, min_clip)
        W = tr["words"]
        spans = [(W[c["words"][0]]["start"], W[c["words"][1]]["end"]) for c in clips]
    else:
        clips = by_silence(an, min(0.25, max_gap))
        spans = [(c["in"], c["out"]) for c in clips]
    for c in clips:
        c["src"] = src
    if punchin and punchin > 1:
        for k in range(1, len(clips)):
            gap = spans[k][0] - spans[k - 1][1]
            if gap < 3.0 and k % 2 == 1:  # jump cut within same shot -> hide it with a punch-in
                clips[k]["zoom"] = punchin

    probe = an["probe"]
    suffix = "_edited" if not aspect else "_" + aspect.replace(":", "x")
    edl = {
        "sources": {src: probe["path"]},
        "output": {"path": out_path or str(Path(probe["path"]).with_name(Path(probe["path"]).stem + suffix + ".mp4")),
                   **({"aspect": aspect} if aspect else {}), "fps": min(60, round(probe.get("fps", 30)))},
        "captions": {"enabled": captions != "off", "style": captions if captions != "off" else "pop"},
        "audio": {"normalize": True, "target_lufs": lufs, "voice_polish": True, "denoise": False},
        "color": {},
        "overlays": [],
        "chapters": [],
        "clips": clips,
    }
    if fit:
        edl["fit"] = fit
    if aspect in ("9:16", "1:1", "4:5"):
        for c in clips:
            c["crop_x"] = "auto"
    apply_brand(edl, brand)
    kept = sum(e - s for s, e in spans)
    stats = {"mode": mode, "clips": len(clips), "kept": kept, "original": probe["duration"]}
    return edl, stats


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workdir")
    ap.add_argument("--src", default="main")
    ap.add_argument("--preset", help=f"platform defaults: {', '.join(PRESETS)} (flags below override)")
    ap.add_argument("--brand", help="brand kit json (default: ./brand.json or ~/.vedit/brand.json if present)")
    ap.add_argument("--mode", choices=["words", "silence"])
    ap.add_argument("--max-gap", type=float, help="pauses longer than this get cut (s), default 0.45")
    ap.add_argument("--keep-fillers", action="store_true")
    ap.add_argument("--keep-retakes", action="store_true")
    ap.add_argument("--min-clip", type=float, default=0.6)
    ap.add_argument("--punchin", type=float, help="alternate zoom on jump cuts, e.g. 1.12")
    ap.add_argument("--aspect", default=None, help="16:9 | 9:16 | 1:1 | 4:5 (default: same as source)")
    ap.add_argument("--captions", default=None, help="pop | box | clean | off")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    p = get_preset(a.preset) if a.preset else {}
    wd = Path(a.workdir).resolve()
    edl, st = make_edl(
        wd, a.src, a.mode,
        max_gap=a.max_gap if a.max_gap is not None else p.get("max_gap", 0.45),
        keep_fillers=a.keep_fillers or p.get("keep_fillers", False), keep_retakes=a.keep_retakes,
        min_clip=a.min_clip, punchin=a.punchin if a.punchin is not None else p.get("punchin", 0),
        aspect=a.aspect or p.get("aspect"), captions=a.captions or p.get("captions", "pop"),
        lufs=p.get("lufs", -14), fit=p.get("fit") if (a.aspect or p.get("aspect")) else None,
        brand=find_brand(a.brand, wd.parent))
    out = Path(a.out) if a.out else wd / "edl.json"
    save_json(edl, out)
    print(f"[autocut] mode={st['mode']}: {st['clips']} clips, ~{ts(st['kept'])} kept of {ts(st['original'])} "
          f"({100 * (1 - st['kept'] / max(st['original'], 1)):.0f}% removed)"
          + (f", brand={Path(edl['brand']).name}" if edl.get("brand") else "") + f" -> {out}")


if __name__ == "__main__":
    main()
