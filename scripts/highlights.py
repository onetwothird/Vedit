#!/usr/bin/env python3
"""Find the best 20-90 s moments in a long video, offline (no AI service).

  vedit highlights VIDEO [--max 12] [--json] [--ai anthropic|openai [--ai-model M]]

Needs `vedit analyze VIDEO` first (transcript.main.json + analysis.main.json in the work dir).
Writes <workdir>/highlights.json and prints a ranked table.

Every candidate is a run of whole sentences with no internal silence over 2 s. Each gets features
scored 0-1, combined with WEIGHTS below into a 0-100 score, then the best non-overlapping
windows are picked, kept 30 s apart while there is room.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import re
import statistics
import sys
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (ffmpeg_bin, load_json, norm_word, progress, run, save_json, ts,  # noqa: E402
                    workdir_for)

# One table: feature -> weight. Negative weights are penalties. Score = 100 * sum(w*f) / sum(positive w).
WEIGHTS: dict[str, float] = {
    "energy": 1.0,          # mean loudness of the window above the video's median
    "energy_var": 0.5,      # loudness swings (emphasis, reactions)
    "speech_rate": 0.6,     # words per spoken second (pauses excluded) against the video's median window
    "laughter": 0.8,        # loud hops with nobody speaking
    "hook": 1.5,            # first 3 s has a question, a number or a hook word
    "direct": 0.6,          # talks to the viewer: you / ikaw / kayo / mo
    "complete": 1.0,        # does not stop on a dangling conjunction
    "filler_density": -0.8,  # um/uh (and half-weight soft fillers) per word
    "face": 0.4,            # a face is on screen; constant 0.5 until framing runs before scoring
    "length_fit": 0.6,      # 30-60 s is the Reels sweet spot; 0.3 at 20 s and at 90 s
}

HOOK_WORDS = {"nobody", "never", "secret", "mistake", "truth", "why", "how", "stop", "best", "worst",
              "grabe", "totoo", "sikreto", "bakit", "paano", "huwag"}
HOOK_PHRASES = ["alam mo ba"]
DIRECT_WORDS = {"you", "your", "ikaw", "kayo", "mo"}
DANGLING = {"and", "so", "but", "kasi", "tapos", "pero", "at"}

HOOK_SECONDS = 3.0
MAX_INTERNAL_SILENCE = 2.0
MIN_GAP = 30.0
TITLE_CHARS = 48
MIN_SPEECH_WORDS = 10
SILENT_DB = -100.0


class Highlight(TypedDict):
    id: str
    start: float
    end: float
    w0: int
    w1: int
    score: int
    title: str
    features: dict[str, float]


def _clamp(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


# --- audio energy ---------------------------------------------------------------------------------------------

def energy_profile(video: str | Path, hop: float = 0.5, wd: str | Path | None = None) -> list[float]:
    """RMS level (dB) of each `hop` seconds of audio, via ffmpeg astats. Cached to <wd>/energy.main.json."""
    video = Path(video)
    wd = Path(wd) if wd else workdir_for(video)
    cache = wd / "energy.main.json"
    key = str(video.resolve())
    if cache.is_file():
        c = load_json(cache)
        if c.get("source") == key and c.get("hop") == hop:
            return c["db"]
    sr = 16000
    af = (f"aresample={sr},aformat=channel_layouts=mono,asetnsamples=n={int(sr * hop)}:p=0,"
          "astats=metadata=1:reset=1,ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=-")
    res = run([ffmpeg_bin("ffmpeg"), "-hide_banner", "-nostats", "-v", "error", "-i", str(video), "-vn",
               "-af", af, "-f", "null", "-"], capture=True)
    db = []
    for m in re.finditer(r"RMS_level=(\S+)", res.stdout):
        try:
            v = float(m.group(1))
        except ValueError:
            v = SILENT_DB
        db.append(round(max(SILENT_DB, v) if math.isfinite(v) else SILENT_DB, 2))
    save_json({"source": key, "hop": hop, "db": db}, cache)
    return db


def _load_energy(wd: Path, tr: dict, ana: dict, hop: float = 0.5) -> list[float] | None:
    cache = wd / "energy.main.json"
    if cache.is_file():
        c = load_json(cache)
        if c.get("db"):
            return c["db"]
    src = Path(tr.get("source") or "")
    probe = ana.get("probe") or ana.get("info") or {}
    if not src.is_file() or probe.get("has_audio") is False:
        return None
    try:
        return energy_profile(src, hop, wd) or None
    except (Exception, SystemExit):  # common.run() exits on ffmpeg failure; score without audio instead
        return None


# --- titles ---------------------------------------------------------------------------------------------------

def make_title(words: list[dict]) -> str:
    """First sentence, fillers out, sentence case, cut to 48 chars at a word boundary."""
    toks = [w for w in words if w.get("filler") != "hard"]
    # soft fillers only go at the front ("So, ..."); mid-sentence they carry meaning
    while toks and toks[0].get("filler"):
        toks = toks[1:]
    toks = [w["text"].strip() for w in toks if w["text"].strip()]
    caps = [t.isupper() and len(re.sub(r"\W", "", t)) > 1 for t in toks]
    shouting = any(a and b for a, b in zip(caps, caps[1:]))  # runs of ALL CAPS are shouting, lone ones acronyms
    toks = [t.lower() if shouting and c else t for t, c in zip(toks, caps)]
    toks = [t.strip(",;:") if k < len(toks) - 1 else t for k, t in enumerate(toks)]
    out = ""
    for t in toks:
        nxt = f"{out} {t}" if out else t
        if len(nxt) > TITLE_CHARS:
            break
        out = nxt
    if not out and toks:
        out = toks[0][:TITLE_CHARS]
    out = out.rstrip(" ,;:.-")
    return out[:1].upper() + out[1:]


# --- scoring --------------------------------------------------------------------------------------------------

def _hook(words: list[dict], w0: int, t0: float) -> float:
    toks, i = [], w0
    while i < len(words) and words[i]["start"] < t0 + HOOK_SECONDS:
        text = words[i]["text"]
        if "?" in text or any(ch.isdigit() for ch in text):
            return 1.0
        toks.append(norm_word(text))
        i += 1
    if HOOK_WORDS.intersection(toks):
        return 1.0
    joined = " " + " ".join(toks) + " "
    return 1.0 if any(f" {p} " in joined for p in HOOK_PHRASES) else 0.0


def _length_fit(length: float) -> float:
    if length < 30:
        return _clamp(0.3 + 0.7 * (length - 20) / 10)
    if length > 60:
        return _clamp(1.0 - 0.7 * (length - 60) / 30)
    return 1.0


def _padded(start: float, end: float, min_len: float, lo: float, hi: float) -> tuple[float, float]:
    """Grow [start, end] evenly toward min_len, inside [lo, hi] (the neighbouring speech or the video edges)."""
    half = max(0.0, min_len - (end - start)) / 2
    a, b = max(lo, start - half), min(hi, end + half)
    lost_a, lost_b = a - (start - half), (end + half) - b  # whatever one side could not take goes to the other
    return max(lo, a - lost_b), min(hi, b + lost_a)


def _prefix(vals) -> list[float]:
    out = [0.0]
    for v in vals:
        out.append(out[-1] + v)
    return out


def _barriers(sents: list[dict], silences: list[dict], duration: float) -> list[bool]:
    """barrier[j]: a silence > 2 s sits between sentence j-1 and j, so no window may contain both."""
    bar = [False] * len(sents)
    for j in range(1, len(sents)):
        bar[j] = sents[j]["start"] - sents[j - 1]["end"] > MAX_INTERNAL_SILENCE
    starts = [s["start"] for s in sents]
    for s in silences:
        end = s["end"] if s.get("end") is not None else duration
        if end - s["start"] > MAX_INTERNAL_SILENCE:
            j = bisect.bisect_left(starts, (s["start"] + end) / 2)
            if 0 < j < len(sents):
                bar[j] = True
    return bar


def _candidates(sents, bar, min_len, max_len) -> list[tuple[int, int]]:
    out = []
    for i, s in enumerate(sents):
        for j in range(i, len(sents)):
            if j > i and bar[j]:
                break
            length = sents[j]["end"] - s["start"]
            if length > max_len:
                break
            if length >= min_len:
                out.append((i, j))
    return out


def find_highlights(wd: Path, max_clips: int = 12, min_len: float = 20,
                    max_len: float = 90) -> tuple[list[Highlight], str | None]:
    wd = Path(wd)
    progress("highlights", 0, "Reading transcript")
    tp, ap = wd / "transcript.main.json", wd / "analysis.main.json"
    tr = load_json(tp) if tp.is_file() else {"words": [], "sentences": []}
    ana = load_json(ap) if ap.is_file() else {}
    words, sents = tr.get("words", []), tr.get("sentences", [])
    real = sum(1 for w in words if norm_word(w["text"]) and w.get("filler") != "hard")
    if real < MIN_SPEECH_WORDS or not sents:
        progress("highlights", 100, "No speech found")
        return [], "no_speech"
    duration = float((ana.get("probe") or {}).get("duration") or (ana.get("info") or {}).get("duration")
                     or words[-1]["end"])
    last, spans = len(sents) - 1, []
    bounds: dict[tuple[int, int], tuple[float, float]] = {}  # spans whose clip is padded past their speech
    if duration < min_len:  # the whole video is shorter than one clip: it is the clip
        bounds[(0, last)] = (0.0, duration)
    else:
        bar = _barriers(sents, ana.get("silences") or [], duration)
        spans = _candidates(sents, bar, min_len, max_len)
        if not spans and sents[last]["end"] - sents[0]["start"] <= max_len:  # all speech fits one clip
            bounds[(0, last)] = _padded(sents[0]["start"], sents[last]["end"], min_len, 0.0, duration)
        elif not spans:  # sparse speech: each barrier-separated run, padded into its own silence
            cuts = [0] + [k for k in range(1, len(sents)) if bar[k]] + [len(sents)]
            for i, nxt in zip(cuts, cuts[1:]):
                j = nxt - 1
                lo = sents[i - 1]["end"] if i else 0.0
                hi = sents[j + 1]["start"] if j < last else duration
                if sents[j]["end"] - sents[i]["start"] <= max_len:
                    bounds[(i, j)] = _padded(sents[i]["start"], sents[j]["end"], min_len, lo, hi)
        if not spans:
            floor = min(min_len, duration)
            bounds = {k: v for k, v in bounds.items() if floor <= v[1] - v[0] <= max_len}
    spans = spans if not bounds else list(bounds)
    if not spans:
        progress("highlights", 100, "No speech fits a clip")
        return [], "no_speech"
    progress("highlights", 20, f"{len(spans)} candidate windows")

    hop = 0.5
    energy = _load_energy(wd, tr, ana, hop)
    progress("highlights", 50, "Measuring energy" if energy else "No audio energy, scoring on speech")

    # per-word prefix sums: direct address, filler weight
    p_direct = _prefix(1.0 if norm_word(w["text"]) in DIRECT_WORDS else 0.0 for w in words)
    p_spoken = _prefix(max(0.0, w["end"] - w["start"]) for w in words)
    p_fill = _prefix(1.0 if w.get("filler") == "hard" else 0.5 if w.get("filler") else 0.0 for w in words)
    hooks = [_hook(words, s["w0"], s["start"]) for s in sents]
    complete = [0.0 if norm_word(words[s["w1"]]["text"]) in DANGLING else 1.0 for s in sents]

    if energy:
        is_voiced = [d > SILENT_DB + 1 for d in energy]
        voiced = [d for d, v in zip(energy, is_voiced) if v]
        med_db = statistics.median(voiced) if voiced else SILENT_DB
        covered = [False] * len(energy)
        for w in words:
            for k in range(int(w["start"] / hop), min(len(energy), int(w["end"] / hop) + 1)):
                covered[k] = True
        p_nv = _prefix(1.0 if v else 0.0 for v in is_voiced)  # silent hops stay out of the mean and variance
        p_db = _prefix(d if v else 0.0 for d, v in zip(energy, is_voiced))
        p_db2 = _prefix(d * d if v else 0.0 for d, v in zip(energy, is_voiced))
        p_laugh = _prefix(1.0 if d > med_db + 6 and not covered[k] else 0.0 for k, d in enumerate(energy))

    rates = [(sents[j]["w1"] - sents[i]["w0"] + 1)
             / max(0.5, p_spoken[sents[j]["w1"] + 1] - p_spoken[sents[i]["w0"]]) for i, j in spans]
    med_rate = (statistics.median(rates) if rates else 0.0) or 1.0
    pos = sum(w for w in WEIGHTS.values() if w > 0)

    scored = []
    for (i, j), rate in zip(spans, rates):
        a, b = sents[i], sents[j]
        w0, w1, start, end = a["w0"], b["w1"], a["start"], b["end"]
        start, end = bounds.get((i, j), (start, end))
        n = w1 - w0 + 1
        f = {"speech_rate": _clamp(0.5 + (rate / med_rate - 1)),
             "hook": hooks[i],
             "direct": _clamp(10 * (p_direct[w1 + 1] - p_direct[w0]) / n),
             "complete": complete[j],
             "filler_density": _clamp(10 * (p_fill[w1 + 1] - p_fill[w0]) / n),
             "face": 0.5,
             "length_fit": _length_fit(end - start)}
        if energy:
            h0 = min(len(energy) - 1, int(start / hop))
            h1 = min(len(energy), max(h0 + 1, math.ceil(end / hop)))
            m = p_nv[h1] - p_nv[h0]
            if m:
                mean = (p_db[h1] - p_db[h0]) / m
                var = max(0.0, (p_db2[h1] - p_db2[h0]) / m - mean * mean)
                f["energy"] = _clamp(0.5 + (mean - med_db) / 12)
                f["energy_var"] = _clamp(math.sqrt(var) / 8)
            else:
                f["energy"] = f["energy_var"] = 0.0
            f["laughter"] = _clamp((p_laugh[h1] - p_laugh[h0]) * hop / 3)
        else:
            f["energy"] = f["energy_var"] = f["laughter"] = 0.5
        raw = sum(WEIGHTS[k] * f[k] for k in WEIGHTS) / pos
        scored.append((raw, start, end, i, j, f))
    progress("highlights", 80, "Picking the best moments")

    scored.sort(key=lambda r: (-r[0], r[1]))
    picked: list[tuple] = []
    for gap in (MIN_GAP, 0.0):  # keep clips apart while there is room, then fill with any non-overlapping window
        for r in scored:
            if len(picked) >= max_clips:
                break
            if all(r[2] + gap <= p[1] or p[2] + gap <= r[1] for p in picked):  # also skips r itself
                picked.append(r)
        if len(picked) >= max_clips:
            break
    picked.sort(key=lambda r: (-r[0], r[1]))

    out: list[Highlight] = []
    for n, (raw, start, end, i, j, f) in enumerate(picked, 1):
        s = sents[i]
        out.append({"id": f"h{n:02d}", "start": round(start, 3), "end": round(end, 3),
                    "w0": s["w0"], "w1": sents[j]["w1"],
                    "score": int(max(0, min(100, round(100 * raw)))),
                    "title": make_title(words[s["w0"]:s["w1"] + 1]),
                    "features": {k: round(f[k], 3) for k in WEIGHTS}})
    progress("highlights", 100, f"{len(out)} highlights")
    return out, None


def _apply_ai(wd: Path, hs: list[Highlight], provider: str, model: str | None, max_clips: int,
              min_len: float, max_len: float) -> tuple[list[Highlight], str | None]:
    """Optional LLM re-rank. The key comes only from env VEDIT_AI_KEY. Any failure keeps the offline picks."""
    key = os.environ.get("VEDIT_AI_KEY")
    if not key:
        return hs, "ai_failed"
    try:
        from highlights_llm import rank_with_llm
        tp = wd / "transcript.main.json"
        tr = load_json(tp) if tp.is_file() else {}
        progress("highlights", 90, "Asking the AI to re-rank")
        return rank_with_llm(tr, hs, provider, key, model, max_clips=max_clips, min_len=min_len, max_len=max_len)
    except Exception:  # noqa: BLE001 - never surface details; they could carry the key
        return hs, "ai_failed"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", help="source video (or its .edit work dir)")
    ap.add_argument("--max", type=int, default=12, help="most clips to return (default 12)")
    ap.add_argument("--min-len", type=float, default=20, help="shortest clip in seconds (default 20)")
    ap.add_argument("--max-len", type=float, default=90, help="longest clip in seconds (default 90)")
    ap.add_argument("--out", default=None, help="work dir (default: <video>.edit/)")
    ap.add_argument("--ai", choices=["anthropic", "openai"], default=None,
                    help="re-rank with your own key (env VEDIT_AI_KEY); only transcript text is sent")
    ap.add_argument("--ai-model", default=None, help="model name for --ai (default: provider's default)")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    a = ap.parse_args()

    wd = Path(a.video) if Path(a.video).is_dir() else workdir_for(a.video, a.out)
    hs, why = find_highlights(wd, a.max, a.min_len, a.max_len)
    if a.ai and hs:
        hs, why = _apply_ai(wd, hs, a.ai, a.ai_model, a.max, a.min_len, a.max_len)
    save_json({"source": str(Path(a.video).resolve()), "highlights": hs, "reason": why}, wd / "highlights.json")
    if a.json:
        print(json.dumps({"highlights": hs, "reason": why}, ensure_ascii=False, indent=2))
        return
    if not hs:
        print(f"[highlights] none found ({why})")
        return
    print(f"{'id':<4} {'score':>5}  {'start':>10}  {'len':>5}  title")
    for h in hs:
        print(f"{h['id']:<4} {h['score']:>5}  {ts(h['start']):>10}  {h['end'] - h['start']:>4.0f}s  {h['title']}")
    print(f"\n-> {wd / 'highlights.json'}")


if __name__ == "__main__":
    main()
