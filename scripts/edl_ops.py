"""Pure edit operations on an edl dict (cut / restore / trim words, caption fixes, section patches).

Every function deep-copies its input and returns a new dict. Only clips with a "words" range are touched by the word
operations; clips cut by time ("in"/"out") pass through unchanged.
"""
from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).parent))
from autocut import make_edl  # noqa: E402
from common import load_json, parse_time  # noqa: E402
from edl import DEFAULT_PAD, clip_span, word_range  # noqa: E402
from highlights import Highlight  # noqa: E402
from presets import make_edl_kwargs  # noqa: E402

Section = Literal["captions", "audio", "color", "framing"]


def clip_edl(wd: Path, h: Highlight, preset: str, language: str | None, brand: Path | None) -> dict:
    """An edl for one highlight: make_edl with the preset's settings, kept inside the highlight's range."""
    wd = Path(wd)
    edl, _ = make_edl(wd, "main", None, brand=brand, **make_edl_kwargs(preset))
    if (wd / "transcript.main.json").exists():
        edl["clips"] = _clamp_words(edl["clips"], h["w0"], h["w1"])
    else:
        edl["clips"] = _clamp_time(edl["clips"], h["start"], h["end"])
    if not edl["clips"]:
        raise ValueError(f"highlight {h['id']} has no speech left after cuts")
    edl["output"]["path"] = ""
    if language is not None:
        edl["captions"]["language"] = language
    framing = wd / "framing.json"
    entry = load_json(framing).get(h["id"]) if framing.exists() else None
    if entry and "crop_x" in entry:
        for c in edl["clips"]:
            c["crop_x"] = entry["crop_x"]
    return edl


def _clamp_words(clips: list[dict], w0: int, w1: int) -> list[dict]:
    out = []
    for c in clips:
        a, b = max(c["words"][0], w0), min(c["words"][1], w1)
        if a <= b:
            out.append({**c, "words": [a, b]})
    return out


def _clamp_time(clips: list[dict], t0: float, t1: float) -> list[dict]:
    out = []
    for c in clips:
        a, b = max(c["in"], t0), min(c["out"], t1)
        if a < b:
            out.append({**c, "in": round(a, 3), "out": round(b, 3)})
    return out


def _split_words(clips: list[dict], w0: int, w1: int) -> list[dict]:
    """Remove words w0..w1 from every words clip (splitting a clip around the hole)."""
    out = []
    for c in clips:
        if "words" not in c:
            out.append(c)
            continue
        a, b = c["words"]
        if b < w0 or a > w1:
            out.append(c)
            continue
        if a < w0:
            out.append({**c, "words": [a, w0 - 1]})
        if b > w1:
            out.append({**c, "words": [w1 + 1, b]})
    return out


def _check_range(w0: int, w1: int) -> None:
    if w0 < 0 or w1 < 0 or w0 > w1:
        raise ValueError(f"invalid word range [{w0},{w1}]")


def cut_words(edl: dict, w0: int, w1: int) -> dict:
    """Remove words w0..w1 (inclusive) from the edit. Raises ValueError for a negative or reversed range."""
    _check_range(w0, w1)
    e = deepcopy(edl)
    e["clips"] = _split_words(e["clips"], w0, w1)
    return e


def restore_words(edl: dict, w0: int, w1: int) -> dict:
    """Put words w0..w1 back as a words clip in source order, merging neighbours that become contiguous."""
    _check_range(w0, w1)
    e = deepcopy(edl)
    clips = e["clips"]
    words = [(i, c) for i, c in enumerate(clips) if "words" in c]
    src = words[0][1].get("src", "main") if words else "main"
    same = [(i, c) for i, c in words if c.get("src", "main") == src]
    touching = [(i, c) for i, c in same if c["words"][1] + 1 >= w0 and c["words"][0] - 1 <= w1]
    if touching:
        first = touching[0][1]
        lo = min([w0] + [c["words"][0] for _, c in touching])
        hi = max([w1] + [c["words"][1] for _, c in touching])
        new = {**first, "words": [lo, hi]}
        drop = {i for i, _ in touching}
        pos = touching[0][0]
    else:
        new = {"src": src, "words": [w0, w1]}
        drop = set()
        after = [i for i, c in same if c["words"][0] > w1]
        pos = after[0] if after else (same[-1][0] + 1 if same else len(clips))
    e["clips"] = [c for i, c in enumerate(clips[:pos]) if i not in drop] + [new] + \
                 [c for i, c in enumerate(clips[pos:], pos) if i not in drop]
    return e


def trim(edl: dict, w_first: int, w_last: int) -> dict:
    """Keep only words w_first..w_last (inclusive). Raises ValueError for a negative or reversed range."""
    _check_range(w_first, w_last)
    e = deepcopy(edl)
    e["clips"] = [c for c in e["clips"] if "words" not in c or (c["words"][1] >= w_first and c["words"][0] <= w_last)]
    e["clips"] = [{**c, "words": [max(c["words"][0], w_first), min(c["words"][1], w_last)]} if "words" in c else c
                  for c in e["clips"]]
    return e


def fix_word(edl: dict, wrong: str, right: str) -> dict:
    """Spell `wrong` as `right` in the captions. `right == wrong` removes the replacement (the undo of a first fix)."""
    e = deepcopy(edl)
    rep = e.setdefault("captions", {}).setdefault("replace", {})
    if right == wrong:
        rep.pop(wrong, None)
    else:
        rep[wrong] = right
    return e


def patch_section(edl: dict, section: Section, patch: dict) -> dict:
    """Shallow-merge `patch` into a section. "framing" maps crop_x/zoom onto every clip and fit onto the edl.
    A null value removes the key (the undo of setting a key that was absent)."""
    e = deepcopy(edl)
    if section in ("captions", "audio", "color"):
        sec = e.setdefault(section, {})
        for k, v in patch.items():
            if v is None:
                sec.pop(k, None)
            else:
                sec[k] = deepcopy(v)
    elif section == "framing":
        bad = set(patch) - {"crop_x", "zoom", "fit"}
        if bad:
            raise ValueError(f"unknown framing keys: {sorted(bad)}")
        if "fit" in patch:
            if patch["fit"] is None:
                e.pop("fit", None)
            else:
                e["fit"] = patch["fit"]
        for c in e["clips"]:
            for k, v in patch.items():
                if k == "fit":
                    continue
                if v is None:
                    c.pop(k, None)
                else:
                    c[k] = v
    else:
        raise ValueError(f"unknown section '{section}'")
    return e


def kept_ranges(edl: dict, transcript: dict) -> list[tuple[float, float]]:
    """Source-time (start, end) of each clip in play order, exactly as edl.Timeline resolves them (pad applied, clip
    length snapped to whole frames of edl["output"]["fps"], default 30). Raises ValueError where Timeline would die."""
    W = transcript.get("words")
    fps = (edl.get("output") or {}).get("fps", 30)
    out = []
    for c in edl.get("clips", []):
        if "words" in c:
            pad = tuple(c.get("pad", edl.get("pad", DEFAULT_PAD)))
            tin, tout = word_range(W, int(c["words"][0]), int(c["words"][1]), pad, c.get("src", "main"))
        else:
            tin = parse_time(c.get("in", 0))
            tout = parse_time(c["out"]) if "out" in c else float(transcript.get("duration", tin))
        out.append((tin, clip_span(tin, tout, float(c.get("speed", 1.0)), fps)[1]))
    return out
