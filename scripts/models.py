#!/usr/bin/env python3
"""Managed Whisper model downloads for the Studio app.

Usage:
  python models.py status
  python models.py download NAME     # emits progress("model", ...) while downloading
  python models.py delete NAME

Models live in $VEDIT_MODELS, else %LOCALAPPDATA%\vedit\\models (~/.local/share/vedit/models elsewhere).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import die, progress  # noqa: E402

MODELS = {
    "small": {"repo": "Systran/faster-whisper-small", "size_mb": 484, "for": ["en"]},
    "large-v3": {"repo": "Systran/faster-whisper-large-v3", "size_mb": 3090, "for": ["tl", "taglish"]},
}
REQUIRED_FILES = ("model.bin", "config.json")
SENTINEL = ".vedit-complete"  # written only after a download finished; a partial folder never has it
POLL_SECONDS = 0.5


def model_dir() -> Path:
    env = os.environ.get("VEDIT_MODELS")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".local" / "share") / "vedit" / "models"


def _check(name: str) -> None:
    if name not in MODELS:
        raise KeyError(name)


def is_installed(name: str) -> bool:
    folder = model_dir() / name
    return name in MODELS and (folder / SENTINEL).is_file() and all((folder / f).is_file() for f in REQUIRED_FILES)


def _mark_complete(name: str) -> None:
    row = {"repo": MODELS[name]["repo"], "finished": datetime.now(timezone.utc).isoformat()}
    (model_dir() / name / SENTINEL).write_text(json.dumps(row), encoding="utf-8")


def status() -> dict[str, dict]:
    return {n: {"installed": is_installed(n), "size_mb": m["size_mb"]} for n, m in MODELS.items()}


def _folder_bytes(folder: Path) -> int:
    total = 0
    for p in folder.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass  # file renamed or removed mid-scan
    return total


def download(name: str) -> Path:
    """Fetch the model into model_dir()/name (resumes; huggingface_hub verifies hashes)."""
    _check(name)
    from huggingface_hub import snapshot_download  # lazy: only the download path needs it
    meta = MODELS[name]
    target = model_dir() / name
    target.mkdir(parents=True, exist_ok=True)
    expected = meta["size_mb"] * 1024 * 1024
    stop = threading.Event()

    def poll() -> None:
        while not stop.wait(POLL_SECONDS):
            progress("model", min(99.0, 100 * _folder_bytes(target) / expected), f"Downloading {name}")

    progress("model", 0, f"Downloading {name}")
    t = threading.Thread(target=poll, name="vedit-model-poll", daemon=True)
    t.start()
    try:
        snapshot_download(meta["repo"], local_dir=str(target))
    finally:
        stop.set()
        t.join()
    _mark_complete(name)
    progress("model", 100, f"{name} ready")
    return target


def delete(name: str) -> None:
    _check(name)
    shutil.rmtree(model_dir() / name, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Manage Whisper models")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    for c in ("download", "delete"):
        sub.add_parser(c).add_argument("name")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(json.dumps(status()))
        return 0
    if a.name not in MODELS:
        die(f"unknown model {a.name!r} (choose from {', '.join(MODELS)})")
    if a.cmd == "download":
        download(a.name)
    else:
        delete(a.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
