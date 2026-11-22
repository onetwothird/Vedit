import json

import common
from conftest import run_script


def test_progress_silent_by_default(capsys, monkeypatch):
    monkeypatch.delenv("VEDIT_PROGRESS", raising=False)
    common.progress("render", 50)
    assert capsys.readouterr().err == ""


def test_progress_line_format(capsys, monkeypatch):
    monkeypatch.setenv("VEDIT_PROGRESS", "1")
    common.progress("transcribe", 33.333, "Listening", eta=12)
    line = capsys.readouterr().err.strip()
    assert line.startswith("@@progress ")
    assert json.loads(line[11:]) == {"stage": "transcribe", "pct": 33.3, "message": "Listening", "eta": 12}


def _pcts(stderr, stage=None):
    rows = [json.loads(ln[11:]) for ln in stderr.splitlines() if ln.startswith("@@progress ")]
    return [r["pct"] for r in rows if stage is None or r["stage"] == stage]


def test_render_emits_progress(sample_edl):
    r = run_script("render.py", sample_edl, "--preview", env={"VEDIT_PROGRESS": "1"})
    pcts = _pcts(r.stderr, "render")
    assert pcts and pcts[-1] == 100.0 and pcts == sorted(pcts)


def test_render_silent_without_flag(sample_edl):
    r = run_script("render.py", sample_edl, "--preview")
    assert "@@progress" not in r.stderr


def test_analyze_emits_progress(talking, tmp_path):
    r = run_script("analyze.py", talking / "raw.mp4", "--no-transcribe", "--out", tmp_path / "w",
                   env={"VEDIT_PROGRESS": "1"})
    stages = [json.loads(ln[11:])["stage"] for ln in r.stderr.splitlines() if ln.startswith("@@progress ")]
    assert "silences" in stages
    pcts = _pcts(r.stderr)
    assert pcts == sorted(pcts) and pcts[-1] == 100.0
