#!/usr/bin/env python3
"""One command, finished video. analyze -> smart cut -> brand -> render (+ extra formats, thumbnails).

  python auto.py raw.mp4 --preset youtube
  python auto.py raw.mp4 --preset shorts --brand brand.json
  python auto.py *.mp4 --preset tiktok --preview            # batch
  python auto.py raw.mp4 --preset youtube --formats 9:16 --thumbs
  python auto.py --list-presets

Re-running is fast: analysis, transcripts and rendered clips are cached in <video>.edit/.
For a truly *edited* video (story, hook, best takes), ask Claude Code to refine <video>.edit/edl.json afterwards.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from autocut import make_edl  # noqa: E402
from common import save_json, ts, workdir_for  # noqa: E402
from presets import PRESETS, find_brand, get_preset  # noqa: E402

HERE = Path(__file__).parent


def step(cmd: list[str]) -> None:
    r = subprocess.call([sys.executable, *cmd])
    if r != 0:
        sys.exit(r)


def process(video: Path, a) -> dict:
    t0 = time.time()
    p = get_preset(a.preset)
    wd = workdir_for(video)
    print(f"\n━━ {video.name}  [{a.preset}: {p['desc']}]", flush=True)
    if not (wd / "analysis.main.json").exists() or a.reanalyze:
        cmd = [str(HERE / "analyze.py"), str(video), "--model", a.model]
        if a.language:
            cmd += ["--language", a.language]
        if a.no_transcribe:
            cmd += ["--no-transcribe"]
        step(cmd)
    else:
        print("[auto] using cached analysis (--reanalyze to redo)", flush=True)
    aspect = a.aspect or p.get("aspect")
    edl, st = make_edl(wd, "main", None, max_gap=p["max_gap"], keep_fillers=p.get("keep_fillers", False),
                       punchin=p["punchin"], aspect=aspect, captions=a.captions or p["captions"], lufs=p["lufs"],
                       fit=p.get("fit") if aspect else None, brand=find_brand(a.brand, video.parent),
                       out_path=str(video.with_name(f"{video.stem}_{a.preset}.mp4")))
    ep = wd / f"edl.{a.preset}.json"
    save_json(edl, ep)
    print(f"[auto] {st['clips']} clips, {ts(st['original'])} -> ~{ts(st['kept'])} "
          f"({100 * (1 - st['kept'] / max(st['original'], 1)):.0f}% cut)  edl: {ep}", flush=True)
    cmd = [str(HERE / "render.py"), str(ep)]
    if a.preview:
        cmd.append("--preview")
    if a.formats:
        cmd += ["--formats", a.formats]
    step(cmd)
    out = Path(edl["output"]["path"])
    if a.preview:
        out = out.with_name(out.stem + "_preview" + out.suffix)
    if a.thumbs:
        step([str(HERE / "thumbs.py"), str(video)])
    return {"video": video.name, "out": str(out), "before": st["original"], "after": st["kept"],
            "secs": time.time() - t0}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("videos", nargs="*")
    ap.add_argument("--preset", default="youtube", help=", ".join(PRESETS))
    ap.add_argument("--brand", help="brand kit json (auto-detects ./brand.json or ~/.vedit/brand.json)")
    ap.add_argument("--aspect", help="override preset aspect: 16:9 | 9:16 | 1:1 | 4:5")
    ap.add_argument("--captions", help="override preset captions: pop | box | clean | off")
    ap.add_argument("--formats", help="extra aspect ratios from the same edit, e.g. 9:16,1:1")
    ap.add_argument("--thumbs", action="store_true", help="also export thumbnail candidates")
    ap.add_argument("--preview", action="store_true", help="fast half-res render")
    ap.add_argument("--model", default="small", help="whisper model: tiny|base|small|medium|large-v3")
    ap.add_argument("--language", help="force language, e.g. en, tl, es")
    ap.add_argument("--no-transcribe", action="store_true", help="silence-based cutting only (no captions)")
    ap.add_argument("--reanalyze", action="store_true")
    ap.add_argument("--list-presets", action="store_true")
    a = ap.parse_args()
    if a.list_presets or not a.videos:
        print("presets:")
        for k, v in PRESETS.items():
            print(f"  {k:9s} {v['desc']}")
        if not a.videos:
            print("\nusage: vedit auto VIDEO [VIDEO...] --preset NAME")
        return
    results = []
    for v in a.videos:
        vp = Path(v)
        if not vp.exists():
            print(f"[auto] skip, not found: {v}", file=sys.stderr)
            continue
        results.append(process(vp.resolve(), a))
    print("\n━━ summary")
    for r in results:
        print(f"  {r['video']}: {ts(r['before'])} -> {ts(r['after'])}  in {r['secs']:.0f}s  => {r['out']}")


if __name__ == "__main__":
    main()
