"""Platform presets + brand kit. A preset = sensible editing defaults per platform; a brand kit = your look,
applied to every video (fonts, colors, logo, intro/outro, music, LUT, caption spelling fixes)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import die, load_json  # noqa: E402

PRESETS: dict[str, dict] = {
    "youtube": {"desc": "Long-form talking head / vlog, 16:9, clean subtitles, punch-ins on jump cuts",
                "aspect": None, "max_gap": 0.45, "punchin": 1.10, "captions": "clean", "lufs": -14},
    "shorts": {"desc": "YouTube Shorts 9:16, fast pacing, pop captions, face-tracked crop",
               "aspect": "9:16", "max_gap": 0.28, "punchin": 1.12, "captions": "pop", "lufs": -14},
    "tiktok": {"desc": "TikTok 9:16, fastest pacing, pop captions",
               "aspect": "9:16", "max_gap": 0.25, "punchin": 1.12, "captions": "pop", "lufs": -14},
    "reels": {"desc": "Instagram Reels 9:16, pop captions",
              "aspect": "9:16", "max_gap": 0.28, "punchin": 1.12, "captions": "pop", "lufs": -14},
    "square": {"desc": "LinkedIn / feed 1:1, boxed captions (readable muted)",
               "aspect": "1:1", "max_gap": 0.35, "punchin": 1.08, "captions": "box", "lufs": -14},
    "podcast": {"desc": "Podcast / interview: gentle cuts (keeps natural pauses), clean subtitles, -16 LUFS",
                "aspect": None, "max_gap": 0.8, "punchin": 0, "captions": "clean", "lufs": -16,
                "keep_fillers": True},
    "tutorial": {"desc": "Screen recording / course: no zooms (keeps text readable), clean subtitles",
                 "aspect": None, "max_gap": 0.5, "punchin": 0, "captions": "clean", "lufs": -14, "fit": "blur"},
    "clean": {"desc": "Just remove dead air + ums. No captions, no effects",
              "aspect": None, "max_gap": 0.45, "punchin": 0, "captions": "off", "lufs": -14},
}


def get_preset(name: str) -> dict:
    if name not in PRESETS:
        die(f"unknown preset '{name}'. Choose: {', '.join(PRESETS)}")
    return PRESETS[name]


def find_brand(explicit: str | None, near: Path) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.exists() else die(f"brand kit not found: {p}")
    for p in (near / "brand.json", Path.cwd() / "brand.json", Path.home() / ".vedit" / "brand.json"):
        if p.exists():
            return p
    return None


def apply_brand(edl: dict, brand_path: Path | None) -> dict:
    """Merge a brand kit into an edl (explicit values already in the edl win)."""
    if not brand_path:
        return edl
    b = load_json(brand_path)
    base = brand_path.resolve().parent

    def ab(p):
        q = Path(p).expanduser()
        return str(q if q.is_absolute() else (base / q).resolve())

    cap = edl.setdefault("captions", {})
    for src, dst in (("font", "font"), ("caption_color", "color"), ("highlight", "highlight"),
                     ("caption_style", "style"), ("caption_position", "position"), ("uppercase", "uppercase")):
        if src in b and dst not in cap:
            cap[dst] = b[src]
    if b.get("replace"):
        cap["replace"] = {**b["replace"], **cap.get("replace", {})}
    if b.get("logo"):
        lg = b["logo"] if isinstance(b["logo"], dict) else {"path": b["logo"]}
        edl.setdefault("overlays", []).append({"type": "image", "path": ab(lg["path"]), "at": 0, "duration": "full",
                                              "mode": "pip", "position": lg.get("position", "top-right"),
                                              "scale": lg.get("scale", 0.12), "opacity": lg.get("opacity", 0.85),
                                              "fade": 0, "note": "brand logo"})
    for k in ("intro", "outro"):
        if b.get(k) and k not in edl:
            v = b[k] if isinstance(b[k], dict) else {"path": b[k]}
            edl[k] = {**v, "path": ab(v["path"])}
    au = edl.setdefault("audio", {})
    if b.get("music") and "music" not in au:
        m = b["music"] if isinstance(b["music"], dict) else {"path": b["music"]}
        au["music"] = {**m, "path": ab(m["path"])}
    if "target_lufs" in b:
        au.setdefault("target_lufs", b["target_lufs"])
    col = edl.setdefault("color", {})
    for k, v in (b.get("color") or {}).items():
        col.setdefault(k, v)
    if b.get("lut"):
        col.setdefault("lut", ab(b["lut"]))
    edl["brand"] = str(brand_path)
    return edl
