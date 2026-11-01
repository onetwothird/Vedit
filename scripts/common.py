"""Shared helpers for the video-editor skill. Stdlib only (plus ffmpeg/ffprobe on PATH)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Windows consoles default to cp1252; never crash on the arrows/emoji used in readable output.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

FILLERS_HARD = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "ahh", "hmm", "hm", "mm", "mhm", "eh"}
# Soft fillers are only fillers in context; Claude decides.
FILLERS_SOFT = {"like", "so", "basically", "actually", "literally", "right", "okay", "ok", "well"}
FILLER_PHRASES = [("you", "know"), ("i", "mean"), ("kind", "of"), ("sort", "of")]


def die(msg: str, code: int = 1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def need(binary: str):
    if not shutil.which(binary):
        die(f"'{binary}' not found on PATH. Install ffmpeg (macOS: brew install ffmpeg, "
            f"Ubuntu: sudo apt install ffmpeg, Windows: winget install ffmpeg).")


def run(cmd: list[str], capture: bool = False, quiet: bool = True) -> subprocess.CompletedProcess:
    if not quiet:
        print("$ " + " ".join(cmd), file=sys.stderr)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        tail = "\n".join(res.stderr.strip().splitlines()[-25:])
        die(f"command failed ({res.returncode}): {' '.join(cmd[:6])} ...\n{tail}")
    return res


def ffprobe(path: str | Path) -> dict:
    need("ffprobe")
    res = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
              capture=True)
    data = json.loads(res.stdout)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    info = {"path": str(Path(path).resolve()), "duration": float(data["format"].get("duration", 0) or 0),
            "has_video": v is not None, "has_audio": a is not None}
    if v:
        w, h = int(v["width"]), int(v["height"])
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
        rot = int(v.get("tags", {}).get("rotate", rot) or rot)
        if abs(rot) in (90, 270):
            w, h = h, w
        num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").split("/")
        fps = float(num) / float(den) if float(den) else 30.0
        info.update({"width": w, "height": h, "fps": round(fps, 3), "vcodec": v.get("codec_name"),
                     "rotation": rot})
    if a:
        info.update({"acodec": a.get("codec_name"), "sample_rate": int(a.get("sample_rate", 48000)),
                     "channels": int(a.get("channels", 2))})
    return info


def workdir_for(video: str | Path, workdir: str | None = None) -> Path:
    p = Path(workdir) if workdir else Path(video).resolve().with_suffix("").parent / (Path(video).stem + ".edit")
    p.mkdir(parents=True, exist_ok=True)
    return p


def ts(sec: float) -> str:
    """Seconds -> M:SS.ss or H:MM:SS.ss"""
    sec = max(0.0, sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}" if h else f"{int(m)}:{s:05.2f}"


def parse_time(v) -> float:
    """Accepts 12.5, '12.5', '1:02.5', '01:02:03.25'."""
    if isinstance(v, (int, float)):
        return float(v)
    parts = str(v).strip().split(":")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def norm_word(w: str) -> str:
    return re.sub(r"[^\w']", "", w.lower())


def load_json(p: str | Path):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_json(obj, p: str | Path):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def hex_to_ass(color: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR"""
    c = color.lstrip("#")
    if len(c) != 6:
        c = "FFFFFF"
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def even(n: float) -> int:
    n = int(round(n))
    return n - (n % 2)


def filter_path(p: str | Path) -> str:
    """Path safe to embed inside an ffmpeg filter argument (Windows drive colons, quotes)."""
    return Path(p).as_posix().replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
