#!/usr/bin/env python3
"""Save one frame of a finished clip as a .jpg: python thumb_frame.py VIDEO OUT.jpg

Never fails the job: a missing thumbnail is reported by the export result, the video is already done."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def frame_time(duration: float) -> float:
    """0.5 s in, or the middle of a clip shorter than 1 s."""
    return min(0.5, duration / 2) if duration > 0 else 0.0


def main() -> None:
    video, out = sys.argv[1], sys.argv[2]
    try:
        Path(out).unlink(missing_ok=True)  # an older thumbnail must never stand in for this one
        sys.path.insert(0, str(Path(__file__).parent))
        from common import ffmpeg_bin, ffprobe
        try:
            at = frame_time(float(ffprobe(video)["duration"]))
        except (Exception, SystemExit):
            at = 0.5
        subprocess.run([ffmpeg_bin("ffmpeg"), "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", video,
                        "-frames:v", "1", "-q:v", "2", out], check=True, capture_output=True)
    except (Exception, SystemExit) as e:
        print(f"[thumb] could not save a thumbnail: {e}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:  # whatever happens, the finished video stays a finished video
        print(f"[thumb] crashed: {e}", file=sys.stderr)
    sys.exit(0)
