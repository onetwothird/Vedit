"""Preview proxies and filmstrip thumbnails for the Studio player and timeline.

  python proxy.py VIDEO    -> <workdir>/proxy.mp4 (only when needed) + filmstrip.jpg/.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import die, ffmpeg_bin, load_json, need, progress, save_json, workdir_for  # noqa: E402

PROXY_SHORT_SIDE = 540
MAX_DIRECT_SECONDS = 1800
MAX_STRIP_COLS = 50  # keeps the sprite under the 65535 px JPEG limit for any realistic source


def _run(cmd: list[str]) -> str:
    """Run a command, decoding output as UTF-8 (Windows consoles default to cp1252); die with its stderr tail."""
    res = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        tail = "\n".join(res.stderr.strip().splitlines()[-25:])
        die(f"command failed ({res.returncode}): {Path(cmd[0]).name}\n{tail}")
    return res.stdout


def probe(path: str | Path) -> dict:
    """Raw ffprobe JSON (format + streams)."""
    need("ffprobe")
    return json.loads(_run([ffmpeg_bin("ffprobe"), "-v", "error", "-print_format", "json", "-show_format",
                            "-show_streams", str(path)]))


def video_stream(info: dict) -> dict | None:
    return next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)


def audio_stream(info: dict) -> dict | None:
    return next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)


def rotation(stream: dict | None) -> int:
    """Display rotation of a video stream normalised to 0/90/180/270 (displaymatrix, else tags.rotate)."""
    if not stream:
        return 0
    rot = None
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = float(sd["rotation"])
    if rot is None:
        try:
            rot = float((stream.get("tags") or {}).get("rotate", 0) or 0)
        except (TypeError, ValueError):
            rot = 0.0
    return int(round(rot / 90.0)) % 4 * 90


def needs_proxy(info: dict) -> bool:
    v = video_stream(info)
    if v is None:
        return False
    a = audio_stream(info)
    fmt = info.get("format", {})
    names = str(fmt.get("format_name", ""))
    try:
        duration = float(fmt.get("duration", 0) or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return (v.get("codec_name") != "h264"
            or (a is not None and a.get("codec_name") != "aac")
            or not ("mp4" in names or "mov" in names)
            or duration > MAX_DIRECT_SECONDS
            or rotation(v) != 0)


def _fps(v: dict) -> int:
    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            num, den = str(v.get(key) or "").split("/")
            if float(den) and float(num) / float(den) > 0:
                return max(1, round(float(num) / float(den)))
        except ValueError:
            continue
    return 30


def _encode(cmd: list[str], duration: float, tmp: Path) -> None:
    """Run ffmpeg, mapping its -progress output onto progress('proxy', 0..100)."""
    proc = subprocess.Popen(cmd + ["-progress", "pipe:1", "-nostats"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    try:
        for line in proc.stdout:
            if line.startswith("out_time_us=") and duration > 0:
                try:
                    progress("proxy", 100 * int(line.split("=", 1)[1]) / 1e6 / duration)
                except ValueError:
                    pass
        err = proc.stderr.read()
    except BaseException:
        proc.kill()
        proc.wait()
        tmp.unlink(missing_ok=True)
        raise
    if proc.wait() != 0:
        tmp.unlink(missing_ok=True)
        die("proxy encode failed:\n" + "\n".join(err.strip().splitlines()[-25:]))


def make_proxy(video: Path, wd: Path) -> Path:
    """Upright 540p H.264/AAC proxy in wd/proxy.mp4 (cached by source size+mtime), or `video` itself if fine."""
    video, wd = Path(video), Path(wd)
    info = probe(video)
    if not needs_proxy(info):
        return video
    need("ffmpeg")
    wd.mkdir(parents=True, exist_ok=True)
    out, meta = wd / "proxy.mp4", wd / "proxy.json"
    st = video.stat()
    key = {"src": str(video.resolve()), "size": st.st_size, "mtime": st.st_mtime}
    if out.exists() and meta.exists():
        try:
            if load_json(meta) == key:
                return out
        except (ValueError, OSError):
            pass
    v = video_stream(info)
    w, h = int(v["width"]), int(v["height"])
    if rotation(v) in (90, 270):
        w, h = h, w
    scale = f"{PROXY_SHORT_SIDE}:-2" if h > w else f"-2:{PROXY_SHORT_SIDE}"
    g = str(_fps(v))
    tmp = wd / "proxy.part.mp4"
    meta.unlink(missing_ok=True)
    cmd = [ffmpeg_bin("ffmpeg"), "-y", "-v", "error", "-i", str(video), "-vf", f"scale={scale}", "-pix_fmt", "yuv420p",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-g", g, "-keyint_min", g,
           "-sc_threshold", "0"]
    cmd += ["-c:a", "aac", "-b:a", "128k"] if audio_stream(info) else ["-an"]
    cmd += ["-movflags", "+faststart", "-f", "mp4", str(tmp)]
    progress("proxy", 0, "Building preview proxy")
    _encode(cmd, float(info.get("format", {}).get("duration", 0) or 0), tmp)
    os.replace(tmp, out)
    save_json(key, meta)
    progress("proxy", 100, "Proxy ready")
    return out


def filmstrip(video: Path, wd: Path, every: float = 2.0, height: int = 96) -> Path:
    """JPEG sprite grid (<=50 columns) of one frame per `every` seconds, plus filmstrip.json
    {every, height, width, count, cols, rows}; width is one frame's width."""
    video, wd = Path(video), Path(wd)
    need("ffmpeg")
    wd.mkdir(parents=True, exist_ok=True)
    duration = float(probe(video).get("format", {}).get("duration", 0) or 0)
    count = max(1, math.ceil(duration / every))
    cols = min(count, MAX_STRIP_COLS)
    rows = math.ceil(count / cols)
    out = wd / "filmstrip.jpg"
    _run([ffmpeg_bin("ffmpeg"), "-y", "-v", "error", "-i", str(video), "-an",
         "-vf", f"fps=1/{every},scale=-2:{height},tile={cols}x{rows}", "-frames:v", "1", "-q:v", "5", str(out)])
    sprite_w = int(video_stream(probe(out))["width"])
    save_json({"every": every, "height": height, "width": sprite_w // cols, "count": count, "cols": cols,
               "rows": rows}, wd / "filmstrip.json")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    a = ap.parse_args()
    video = Path(a.video).resolve()
    wd = workdir_for(video)
    preview = make_proxy(video, wd)
    progress("proxy", 95, "Building filmstrip")
    strip = filmstrip(video, wd)
    print(f"[proxy] preview -> {preview}")
    print(f"[proxy] filmstrip -> {strip}")


if __name__ == "__main__":
    main()
