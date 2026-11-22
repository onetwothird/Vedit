"""Frozen-safe subprocess helpers, bundled-ffmpeg lookup and the engine entry point."""
import shutil
import sys
from pathlib import Path

import common
import engine_entry
from conftest import SCRIPTS


def test_py_cmd_source_mode():
    cmd = common.py_cmd("render.py", "C:/Mga Video/Señora #1/edl.json")
    assert cmd[0] == sys.executable
    assert cmd[1].endswith("render.py") and Path(cmd[1]).is_absolute()
    assert cmd[2] == "C:/Mga Video/Señora #1/edl.json"


def test_py_cmd_frozen_mode(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\App\vedit-engine.exe")
    assert common.py_cmd("render.py", "a b.json") == [r"C:\App\vedit-engine.exe", "run", "render.py", "a b.json"]


def test_ffmpeg_bin_prefers_env(monkeypatch, tmp_path):
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("VEDIT_FFMPEG_DIR", str(tmp_path))
    assert common.ffmpeg_bin("ffmpeg") == str(exe)


def test_ffmpeg_bin_falls_back_to_path(monkeypatch):
    monkeypatch.delenv("VEDIT_FFMPEG_DIR", raising=False)
    assert common.ffmpeg_bin("ffmpeg") == shutil.which("ffmpeg")


def test_engine_entry_run_dispatches(capsys):
    assert engine_entry.main(["run", "vedit.py", "--version"]) == 0
    assert capsys.readouterr().out.startswith("vedit ")


def test_no_raw_sys_executable_calls():
    offenders = [p.name for p in SCRIPTS.glob("*.py")
                 if p.name not in ("common.py", "engine_entry.py") and "sys.executable" in p.read_text("utf-8")]
    assert offenders == []
