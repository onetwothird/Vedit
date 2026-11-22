import json
import sys

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
    r = run_script("render.py", sample_edl, "--preview", env={"VEDIT_PROGRESS": ""})
    assert "@@progress" not in r.stderr


def _rows(stderr):
    return [json.loads(ln[11:]) for ln in stderr.splitlines() if ln.startswith("@@progress ")]


def test_analyze_progress_is_stage_relative(talking, tmp_path):
    r = run_script("analyze.py", talking / "raw.mp4", "--no-transcribe", "--out", tmp_path / "w",
                   env={"VEDIT_PROGRESS": "1"})
    rows = _rows(r.stderr)
    stages = {row["stage"] for row in rows}
    assert {"audio", "silences"} <= stages
    assert "framing" not in stages
    for stage in stages:
        pcts = [row["pct"] for row in rows if row["stage"] == stage]
        assert pcts == sorted(pcts) and pcts[-1] == 100.0, stage


def test_transcribe_stage_runs_zero_to_one_hundred(talking, tmp_path, monkeypatch, capsys):
    import transcribe
    monkeypatch.setenv("VEDIT_PROGRESS", "1")
    monkeypatch.setattr(transcribe, "transcribe_words", lambda *a, **k: ([{"start": 0.0, "end": 0.5, "text": "hi", "p": 0.9}], "en"))
    monkeypatch.setattr(sys, "argv", ["transcribe.py", str(talking / "raw.mp4"), "--out", str(tmp_path / "w")])
    transcribe.main()
    pcts = [row["pct"] for row in _rows(capsys.readouterr().err) if row["stage"] == "transcribe"]
    assert pcts[0] == 0.0 and pcts[-1] == 100.0 and pcts == sorted(pcts)
