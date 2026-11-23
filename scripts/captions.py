#!/usr/bin/env python3
"""Build captions.ass (+ .srt) for the EDITED timeline from edl.json. render.py calls this automatically;
run it alone to inspect/tweak captions.

  python captions.py edl.json [--out captions.ass]

edl.json "captions" block (all optional):
  {"enabled": true, "style": "pop" | "box" | "clean", "max_words": 3, "font": "Arial", "size": null,
   "color": "#FFFFFF", "highlight": "#FFD400", "uppercase": true, "position": "lower" | "middle" | "bottom",
   "hide_fillers": true, "srt": true, "replace": {"teh": "the"}, "min_show": 0.7,
   "speaker_colors": {"maria": "#FF6FB5", "narrator": "#FFD400"}}   # skits: active word colored per speaker
Text overlays (titles, lower-thirds, callouts) come from edl.json "overlays" entries with "type": "text".
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).parent))
from common import hex_to_ass, norm_word  # noqa: E402
from edl import Timeline  # noqa: E402


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def esc(s: str) -> str:
    return s.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", r"\N")


def group_words(words, max_words: int, max_chars: int, gap: float = 0.45):
    groups, cur = [], []
    for w in words:
        if cur:
            prev = cur[-1]
            text_len = sum(len(x["text"]) + 1 for x in cur) + len(w["text"])
            if (len(cur) >= max_words or text_len > max_chars or w["clip"] != prev["clip"]
                    or w.get("speaker") != prev.get("speaker")
                    or w["ostart"] - prev["oend"] > gap or prev["text"][-1:] in ".?!,;:"):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def merge_fast(groups, max_words: int, max_chars: int, min_show: float, gap: float = 0.45):
    """Fast talkers make 1-3 word groups that flash by too quickly to read. Join a group that would be on
    screen less than `min_show` seconds with its neighbour, up to two lines (2x the word/char limits).
    Never joins across a pause, a cut or a speaker change."""
    def dur(g):
        return g[-1]["oend"] - g[0]["ostart"]

    def can_join(a, b):
        return (a[-1]["clip"] == b[0]["clip"] and a[-1].get("speaker") == b[0].get("speaker")
                and b[0]["ostart"] - a[-1]["oend"] <= gap
                and len(a) + len(b) <= 2 * max_words
                and sum(len(x["text"]) + 1 for x in a + b) - 1 <= 2 * max_chars)

    out = []
    for g in groups:
        if out and dur(out[-1]) < min_show and can_join(out[-1], g):
            out[-1] = out[-1] + g
        else:
            out.append(list(g))
    if len(out) > 1 and dur(out[-1]) < min_show and can_join(out[-2], out[-1]): 
        out[-2] = out[-2] + out.pop()
    return out


def join_hyphen_pieces(words):
    """Whisper splits Taglish words like "nag-agree" into "nag" + "-agree". Glue them back into one word."""
    out = []
    for w in words:
        if out and w["text"].startswith("-") and len(w["text"]) > 1 and w["clip"] == out[-1]["clip"] \
                and out[-1]["text"][-1:] not in ".?!,;:":
            out[-1] = {**out[-1], "text": out[-1]["text"] + w["text"], "oend": w["oend"]}
        else:
            out.append(w)
    return out


def apply_replacements(words, repl: dict):
    if not repl:
        return
    for w in words:
        key = norm_word(w["text"])
        for k, v in repl.items():
            if key == norm_word(k):
                trail = re.sub(r"^[\w']+", "", w["text"])
                w["text"] = v + trail


def _hex(color, default: str = "#FFFFFF") -> str:
    """Normalise to uppercase #RRGGBB (same fallback as hex_to_ass for malformed values)."""
    c = str(color).lstrip("#") if color else ""
    return "#" + (c if len(c) == 6 else default.lstrip("#")).upper()


class _Plan(NamedTuple):
    cfg: dict
    W: int
    H: int
    style: str
    upper: bool
    size: int
    margin_v: float
    font: str
    outline: float
    act: int
    groups: list  # [(words, group_end_seconds)]


def _plan(tl: Timeline):
    """Single source of truth for caption look + grouping. Returns (public caption_events dict, _Plan)."""
    cfg = {"enabled": True, "style": "pop", "uppercase": None, "hide_fillers": True, "srt": True,
           **(tl.edl.get("captions") or {})}
    W, H = tl.out["width"], tl.out["height"]
    vertical = H > W
    style = cfg["style"]
    upper = cfg["uppercase"] if cfg["uppercase"] is not None else style in ("pop", "box")
    if style == "clean":
        size = cfg.get("size") or round((W * 0.058) if vertical else (H * 0.056))
        max_words, max_chars = cfg.get("max_words", 10), cfg.get("max_chars", 22 if vertical else 42)
    else:
        size = cfg.get("size") or round((W * 0.085) if vertical else (H * 0.075))
        max_words, max_chars = cfg.get("max_words", 3), cfg.get("max_chars", 16 if vertical else 24)
    pos = cfg.get("position") or ("lower" if style != "clean" else "bottom")
    # position: preset name, or a number = distance of the caption baseline from the bottom (fraction of height)
    margin_v = pos if isinstance(pos, (int, float)) else \
        {"bottom": 0.07, "lower": 0.24 if vertical else 0.14, "middle": 0.45}.get(pos, 0.14)
    font = cfg.get("font", "Arial")
    outline = cfg.get("outline") or max(2, round(size * (0.07 if style != "clean" else 0.06)))
    act = int(cfg.get("active_scale", 108))  # % size of the word being spoken (100 = no jump)

    groups = []
    if cfg.get("enabled", True):
        words = [w for w in tl.output_words() if not (cfg["hide_fillers"] and w["filler"] == "hard")]
        words = join_hyphen_pieces(words)
        apply_replacements(words, cfg.get("replace") or {})
        min_show = float(cfg.get("min_show", 0.7))  # seconds a caption must stay up to be readable
        raw = merge_fast(group_words(words, max_words, max_chars), max_words, max_chars, min_show)
        for gi, g in enumerate(raw):
            g_end = max(g[-1]["oend"] + 0.15, g[0]["ostart"] + min_show)  # hold short ones, if there is room
            if gi + 1 < len(raw):
                g_end = min(g_end, raw[gi + 1][0]["ostart"])
            groups.append((g, g_end))

    box = style == "box"
    spk = {k: _hex(v) for k, v in (cfg.get("speaker_colors") or {}).items()}
    events = []
    for g, g_end in groups:
        words = []
        for k, w in enumerate(g):
            # highlight window = the ASS per-word event: first word from group start, each word until the next
            # word starts, the last until the group ends
            words.append({"text": w["text"], "start": w["ostart"], "end": w["oend"],
                          "hl_start": g[0]["ostart"] if k == 0 else w["ostart"],
                          "hl_end": g[k + 1]["ostart"] if k + 1 < len(g) else g_end,
                          "speaker": w.get("speaker")})
        events.append({"start": g[0]["ostart"], "end": g_end, "words": words})
    # output time at which the edited clips begin: an intro bumper pushes every caption time (and the whole timeline)
    # later by its length, but the live preview skips bumpers, so it subtracts this
    lead_in = next((c["t0"] for c in tl.clips if not c.get("_bumper")), 0.0)
    public = {
        "style": style, "font": font, "size_frac": size / H, "baseline_frac": margin_v, "uppercase": bool(upper),
        "color": _hex(cfg.get("color", "#FFFFFF")), "highlight": _hex(cfg.get("highlight", "#FFD400")),
        "outline_frac": outline / H, "active_scale": act,
        "outline_color": _hex(cfg.get("outline_color"), "#000000"),
        "shadow": cfg.get("shadow", 0 if box else 1),
        "back_color": "#000000" if box else None,
        "back_alpha": round(1 - 0x70 / 255, 4) if box else None,  # ASS &H70 = 44% transparent
        "speaker_colors": spk,
        "lead_in": round(lead_in, 4),
        "events": events,
    }
    return public, _Plan(cfg, W, H, style, bool(upper), size, margin_v, font, outline, act, groups)


def caption_events(tl: Timeline) -> dict:
    """Caption groups + look for the live preview, on the output timeline. Same grouping/timing build() uses."""
    return _plan(tl)[0]


def build(tl: Timeline):
    _, x = _plan(tl)
    cfg, W, H, style, upper, size = x.cfg, x.W, x.H, x.style, x.upper, x.size
    margin_v, font, outline, act = x.margin_v, x.font, x.outline, x.act
    col, hi = hex_to_ass(cfg.get("color", "#FFFFFF")), hex_to_ass(cfg.get("highlight", "#FFD400"))
    oc = hex_to_ass(cfg["outline_color"]) if cfg.get("outline_color") else "&H00000000"
    shadow = cfg.get("shadow", 1 if style != "box" else 0)
    border_style, back = (3, "&H70000000") if style == "box" else (1, "&H80000000")

    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},{col},{hi},{oc},{back},-1,0,0,0,100,100,0,0,{border_style},{outline},{shadow},2,{round(W * 0.06)},{round(W * 0.06)},{round(H * margin_v)},1
Style: Title,{font},{round(size * 1.15)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,{outline},2,8,{round(W * 0.05)},{round(W * 0.05)},{round(H * 0.08)},1
Style: Lower,{font},{round(size * 0.75)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,3,{round(outline * 1.5)},0,1,{round(W * 0.05)},{round(W * 0.05)},{round(H * 0.12)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    spk_colors = {k: hex_to_ass(v) for k, v in (cfg.get("speaker_colors") or {}).items()}
    events, srt = [], []
    for g, g_end in x.groups:
        texts = [esc(w["text"].upper() if upper else w["text"]) for w in g]
        plain = " ".join(texts)
        srt.append((g[0]["ostart"], g_end, plain))
        if style == "clean":
            sc = spk_colors.get(g[0].get("speaker"))
            tag = (r"{\c" + sc + "}") if sc else ""
            events.append(f"Dialogue: 0,{ass_time(g[0]['ostart'])},{ass_time(g_end)},Cap,,0,0,0,,{tag}{plain}")
            continue
        for k, w in enumerate(g):  # one event per active word, active word highlighted + popped
            s = w["ostart"] if k else g[0]["ostart"]
            e = g[k + 1]["ostart"] if k + 1 < len(g) else g_end
            if e <= s:
                continue
            parts = []
            for j, t in enumerate(texts):
                if j == k:
                    h = spk_colors.get(w.get("speaker"), hi)
                    parts.append(r"{\c" + h + rf"\fscx{act}\fscy{act}}}" + t + r"{\c" + col + r"\fscx100\fscy100}")
                else:
                    parts.append(t)
            pop = r"{\fad(0,0)}" if k else r"{\fscx90\fscy90\t(0,80,\fscx100\fscy100)}"
            events.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Cap,,0,0,0,,{pop}{' '.join(parts)}")

    for ov in tl.edl.get("overlays") or []:
        # layers made in the editor carry an id and are drawn by render.py (drawtext, matching the preview); these
        # are the hand-written ones (titles, lower-thirds) from edl.json
        if ov.get("type") != "text" or ov.get("id"):
            continue
        start = tl.anchor(ov.get("at", 0))
        if start is None:
            print(f"[captions] overlay '{ov.get('text', '')[:30]}' anchored to a cut word — skipped", file=sys.stderr)
            continue
        end = start + float(ov.get("duration", 3))
        st = {"title": "Title", "top": "Title", "lower-third": "Lower", "lower": "Lower"}.get(ov.get("position", "top"),
                                                                                               "Title")
        tags = r"{\fad(200,200)"
        if ov.get("position") == "center":
            tags += r"\an5\pos(" + f"{W // 2},{H // 2}" + ")"
        if ov.get("color"):
            tags += r"\c" + hex_to_ass(ov["color"])
        if ov.get("size"):
            tags += r"\fs" + str(int(ov["size"]))
        tags += "}"
        events.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},{st},,0,0,0,,{tags}{esc(ov['text'])}")
    return head + "\n".join(events) + "\n", srt, cfg


def write(tl: Timeline, ass_path: Path) -> tuple[Path | None, int]:
    ass, srt, cfg = build(tl)
    n = ass.count("\nDialogue:")
    if n == 0:
        return None, 0
    ass_path.write_text(ass, encoding="utf-8")
    if cfg.get("srt") and srt:
        lines = [f"{i}\n{srt_time(s)} --> {srt_time(e)}\n{t}\n" for i, (s, e, t) in enumerate(srt, 1)]
        Path(tl.out["path"]).with_suffix(".srt").write_text("\n".join(lines), encoding="utf-8")
    return ass_path, n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("edl")
    ap.add_argument("--out")
    a = ap.parse_args()
    tl = Timeline(a.edl)
    p, n = write(tl, Path(a.out) if a.out else tl.dir / "captions.ass")
    print(f"{n} caption/text events -> {p}")


if __name__ == "__main__":
    main()
