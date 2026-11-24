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
import accel  # noqa: E402
from common import die, ffmpeg_bin, load_json, need, progress, save_json, workdir_for  # noqa: E402

PROXY_SHORT_SIDE = 1080  # the preview crops to vertical and zooms, so it needs the source's detail up to 1080p
PROXY_CRF = "20"
PROXY_CQ = "27"  # NVENC constant quality: about the size and look of x264 at PROXY_CRF
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


def video_codec(ffmpeg: str, gop: str, gpu: bool | None = None) -> list[str]:
    """H.264 settings for the proxy: on an NVIDIA GPU (NVENC: several times faster, CPU left free for the speech
    step) when this ffmpeg can use one, else x264. Both put a keyframe every second so the player seeks fast."""
    if gpu is None:
        gpu = accel.nvenc_ready(ffmpeg)
    if gpu:
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", PROXY_CQ, "-b:v", "0",
                "-g", gop, "-no-scenecut", "1"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", PROXY_CRF, "-g", gop, "-keyint_min", gop,
            "-sc_threshold", "0"]


def _encode(cmd: list[str], duration: float, tmp: Path, fail_ok: bool = False) -> bool:
    """Run ffmpeg, mapping its -progress output onto progress('proxy', 0..100). On failure: False when `fail_ok`
    (the caller tries another encoder), else stop with ffmpeg's error."""
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
    """Upright H.264/AAC proxy (the source's size, at most 1080p) in wd/proxy.mp4 (cached by source size+mtime), or `video` itself if fine."""
    video, wd = Path(video), Path(wd)
    info = probe(video)
    if not needs_proxy(info):
        return video
    need("ffmpeg")
    wd.mkdir(parents=True, exist_ok=True)
    out, meta = wd / "proxy.mp4", wd / "proxy.json"
    st = video.stat()
    key = {"src": str(video.resolve()), "size": st.st_size, "mtime": st.st_mtime,
           "short": PROXY_SHORT_SIDE, "crf": PROXY_CRF}  # an older, smaller proxy is rebuilt
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
    short = min(PROXY_SHORT_SIDE, w, h) // 2 * 2  # never enlarged: the proxy keeps every pixel up to 1080p
    scale = f"{short}:-2" if h > w else f"-2:{short}"
    g = str(_fps(v))
    tmp = wd / "proxy.part.mp4"
    meta.unlink(missing_ok=True)
    ff = ffmpeg_bin("ffmpeg")
    duration = float(info.get("format", {}).get("duration", 0) or 0)

    def cmd(gpu: bool) -> list[str]:
        c = [ff, "-y", "-v", "error", "-i", str(video), "-vf", f"scale={scale}", "-pix_fmt", "yuv420p"]
        c += video_codec(ff, g, gpu)
        c += ["-c:a", "aac", "-b:a", "128k"] if audio_stream(info) else ["-an"]
        return c + ["-movflags", "+faststart", "-f", "mp4", str(tmp)]

    progress("proxy", 0, "Building preview proxy")
    gpu = accel.nvenc_ready(ff)
    if not (gpu and _encode(cmd(True), duration, tmp, fail_ok=True)):
        _encode(cmd(False), duration, tmp)
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
    info = probe(video)
    # the picture's own length: the container's can run past it where the audio encoder pads its last frame
    duration = float((video_stream(info) or {}).get("duration") or info.get("format", {}).get("duration", 0) or 0)
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
