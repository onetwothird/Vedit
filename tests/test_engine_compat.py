"""Frozen-safe subprocess helpers, bundled-ffmpeg lookup and the engine entry point."""
import re
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
    assert common.ffmpeg_bin("ffmpeg") == (shutil.which("ffmpeg") or "ffmpeg")


def test_engine_entry_run_dispatches(capsys):
    assert engine_entry.main(["run", "vedit.py", "--version"]) == 0
    assert capsys.readouterr().out.startswith("vedit ")


def test_no_raw_sys_executable_calls():
    offenders = [p.name for p in SCRIPTS.glob("*.py")
                 if p.name not in ("common.py", "engine_entry.py") and "sys.executable" in p.read_text("utf-8")]
    assert offenders == []


def test_ffmpeg_bin_ffprobe_in_env_dir(monkeypatch, tmp_path):
    exe = tmp_path / "ffprobe.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("VEDIT_FFMPEG_DIR", str(tmp_path))
    assert common.ffmpeg_bin("ffprobe") == str(exe)


def test_ffmpeg_bin_env_dir_without_exe_suffix(monkeypatch, tmp_path):
    exe = tmp_path / "ffprobe"
    exe.write_bytes(b"")
    monkeypatch.setenv("VEDIT_FFMPEG_DIR", str(tmp_path))
    assert common.ffmpeg_bin("ffprobe") == str(exe)


def test_engine_entry_keeps_string_exit_message(tmp_path, capsys):
    script = tmp_path / "boom.py"
    script.write_text('import sys; sys.exit("error: boom")', "utf-8")
    assert engine_entry.main(["run", str(script)]) == 1
    assert "error: boom" in capsys.readouterr().err


def test_no_raw_ffmpeg_literals_in_argv():
    # list element or first argument of run()/subprocess calls; need("ffmpeg") and hint strings do not match
    pattern = re.compile(r"(?:\[|(?:run|call|Popen|check_call|check_output)\()\s*[\"'](ffmpeg|ffprobe)[\"']")
    offenders = [p.name for p in SCRIPTS.glob("*.py")
                 if pattern.search(p.read_text("utf-8"))]
    assert offenders == []


def test_ffmpeg_bin_bare_name_when_nothing_found(monkeypatch):
    monkeypatch.delenv("VEDIT_FFMPEG_DIR", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    assert common.ffmpeg_bin("ffmpeg") == "ffmpeg"
