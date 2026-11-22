"""Shared fixtures: a tiny synthetic 'talking' video + fake word-level transcript (no Whisper download needed)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

# (text, seconds of speech, seconds of silence after)
SCRIPT = [
    ("Hello everyone, welcome back.", 1.6, 1.2),
    ("Um, today we build an editor.", 1.8, 1.5),
    ("So first you need FFmpeg.", 1.4, 0.8),
    ("So first you need FFmpeg installed.", 1.8, 1.4),
    ("Thanks for watching.", 1.2, 1.0),
]


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def run_script(name, *args, check=True, env: dict | None = None):
    # scripts print UTF-8; Windows default is cp1252
    r = subprocess.run([sys.executable, str(SCRIPTS / name), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env={**os.environ, **env} if env else None)
    if check and r.returncode != 0:
        raise AssertionError(f"{name} failed:\n{r.stdout}\n{r.stderr}")
    return r


@pytest.fixture(scope="session")
def ffmpeg_ok():
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")


@pytest.fixture(scope="session")
def talking(tmp_path_factory, ffmpeg_ok):
    """raw.mp4 with tone 'speech' bursts + analysis + fake transcript in raw.edit/."""
    d = tmp_path_factory.mktemp("talk")
    parts, t, words = [], 0.0, []
    for i, (text, speech, gap) in enumerate(SCRIPT):
        ff("-f", "lavfi", "-i", f"sine=f={220 + 40 * i}:d={speech}", "-ar", "48000", "-ac", "2", str(d / f"s{i}.wav"))
        ff("-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", str(gap), str(d / f"g{i}.wav"))
        parts += [f"file 's{i}.wav'", f"file 'g{i}.wav'"]
        toks = text.split()
        step = speech / len(toks)
        for k, w in enumerate(toks):
            words.append({"start": t + k * step, "end": t + (k + 0.85) * step, "text": w, "p": 0.95})
        t += speech + gap
    (d / "list.txt").write_text("\n".join(parts))
    ff("-f", "concat", "-safe", "0", "-i", str(d / "list.txt"), str(d / "speech.wav"))
    ff("-f", "lavfi", "-i", f"testsrc2=s=640x360:r=30:d={t}", "-i", str(d / "speech.wav"),
       "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(d / "raw.mp4"))
    run_script("analyze.py", d / "raw.mp4", "--no-transcribe")
    from transcribe import build, write_txt
    from common import save_json
    data = build(str(d / "raw.mp4"), words, "en", "main", t)
    save_json(data, d / "raw.edit" / "transcript.main.json")
    write_txt(d / "raw.edit" / "transcript.main.txt", data)
    shutil.copytree(d / "raw.edit", d / "pristine.edit")  # tests like auto.py overwrite raw.edit; keep a clean copy
    return d


@pytest.fixture(scope="session")
def sample_edl(talking):
    """edl.json cut from the synthetic talking video, rendering to <talking>/prog.mp4."""
    edl = talking / "pristine.edit" / "edl.sample.json"
    run_script("autocut.py", talking / "pristine.edit", "--preset", "youtube", "--out", edl)
    e = json.loads(edl.read_text())
    e["output"]["path"] = str(talking / "prog.mp4")
    edl.write_text(json.dumps(e))
    return edl


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _workdir_from(sample_edl, talking, tmp_path_factory, name, **output):
    """A private copy of the pristine edit (edl + transcript) so tests can't disturb each other."""
    d = tmp_path_factory.mktemp(name)
    for f in (talking / "pristine.edit").glob("transcript.*.json"):
        shutil.copy(f, d / f.name)
    e = json.loads(sample_edl.read_text())
    e["output"] = {**e["output"], "path": str(d / "prog.mp4"), **output}
    (d / "edl.json").write_text(json.dumps(e))
    return d


@pytest.fixture(scope="session")
def pipeline_workdir(talking, sample_edl, tmp_path_factory):
    """16:9 edl.json + transcript, independent of raw.edit."""
    return _workdir_from(sample_edl, talking, tmp_path_factory, "wd169", aspect="16:9")


@pytest.fixture(scope="session")
def pipeline_workdir_9x16(talking, sample_edl, tmp_path_factory):
    """9:16 edl.json with pop captions."""
    d = _workdir_from(sample_edl, talking, tmp_path_factory, "wd916", aspect="9:16")
    p = d / "edl.json"
    e = json.loads(p.read_text())
    e["captions"] = {**(e.get("captions") or {}), "style": "pop"}
    p.write_text(json.dumps(e))
    return d
