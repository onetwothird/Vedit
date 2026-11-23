"""Studio project store and output naming."""
import json
import os
import subprocess

import pytest

import studio_projects as sp
from common import ffmpeg_bin
from studio_projects import (ProjectClip, create_project, list_recent, load_project, output_dir, output_name,
                             save_project)


def make_tiny_video(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg_bin("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=size=160x90:rate=10:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                   check=True)
    return path


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VEDIT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_roundtrip_with_unicode_path(tmp_path, home):
    src = tmp_path / "Juan Dela Cruz" / "Mga Video" / "Señora #1.mp4"
    make_tiny_video(src)
    p = create_project(src, "taglish", "reels")
    assert len(p.id) == 12 and load_project(p.id) == p
    assert create_project(src, "taglish", "reels").id == p.id


def test_roundtrip_with_clips_and_unicode_title(tmp_path, home):
    src = make_tiny_video(tmp_path / "a.mp4")
    p = create_project(src, "en", "shorts")
    hl = {"id": "h01", "start": 1.0, "end": 9.5, "w0": 2, "w1": 30, "score": 7, "title": "Ang ganda ng señora",
          "features": {"hook": 0.5}}
    p.clips.append(ProjectClip(highlight=hl, kept=True, edl_file="clip-h01.edl.json"))
    p.last_job = {"kind": "render", "pct": 50}
    save_project(p)
    assert load_project(p.id) == p
    assert "señora" in (sp.workdir_for(src) / "project.json").read_text(encoding="utf-8")


def test_load_errors_and_list_recent(tmp_path, home):
    with pytest.raises(KeyError):
        load_project("nope")
    a = create_project(make_tiny_video(tmp_path / "a.mp4"), "en", "reels")
    b = create_project(make_tiny_video(tmp_path / "b.mp4"), "en", "reels")
    assert [p.id for p in list_recent()] == [b.id, a.id]
    load_project(a.id)  # opening bumps recency
    assert [p.id for p in list_recent()] == [a.id, b.id]
    assert [p.id for p in list_recent(limit=1)] == [a.id]
    (sp.workdir_for(tmp_path / "b.mp4") / "project.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_project(b.id)
    assert [p.id for p in list_recent()] == [a.id]  # corrupt/missing entries are skipped
    (sp.workdir_for(tmp_path / "a.mp4") / "project.json").unlink()
    assert list_recent() == []


def test_output_name_rules():
    assert output_name(1, "Nobody tells you this", "9:16") == "01 Nobody tells you this.mp4"
    assert output_name(2, 'What? "Why" / how: now', "1:1") == "02 What Why how now (1x1).mp4"
    assert output_name(3, "", "9:16") == "03 Clip 03.mp4"
    assert len(output_name(4, "x" * 200, "9:16")) <= 64


def test_output_name_length_and_trimming():
    n = output_name(5, "word " * 40, "16:9")
    assert n.endswith(" (16x9).mp4") and len(n) - 4 <= 60 and "wor (" not in n
    n = output_name(6, "x" * 200, "4:5")
    assert len(n) - 4 == 60 and n.endswith(" (4x5).mp4") and n.startswith("06 x")
    assert output_name(7, "  ends with dots...  ", "9:16") == "07 ends with dots.mp4"
    assert output_name(8, "a\tb\x00c\n d", "9:16") == "08 abc d.mp4"
    assert output_name(9, "<>:/\\|?*", "9:16") == "09 Clip 09.mp4"


def test_output_dir(tmp_path):
    assert output_dir(tmp_path / "ep12.mp4") == tmp_path / "ep12 - Vedit"


def test_save_is_atomic(tmp_path, home, monkeypatch):
    src = make_tiny_video(tmp_path / "a.mp4")
    p = create_project(src, "en", "reels")
    f = sp.workdir_for(src) / "project.json"
    before = f.read_bytes()
    p.settings = {"changed": True}

    def boom(*a, **k):
        raise OSError("disk gone")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        save_project(p)
    assert f.read_bytes() == before
    assert [x.name for x in f.parent.iterdir() if x.suffix == ".tmp"] == []
    monkeypatch.undo()
    save_project(p)
    assert json.loads(f.read_text(encoding="utf-8"))["settings"] == {"changed": True}


def _bak(d, name):
    return [x for x in d.iterdir() if x.name.startswith(name + ".bak-")]


def test_create_never_overwrites_unreadable_file(tmp_path, home, monkeypatch):
    src = make_tiny_video(tmp_path / "a.mp4")
    create_project(src, "en", "reels")
    f = sp.workdir_for(src) / "project.json"
    before = f.read_bytes()
    real = type(f).read_text

    def locked(self, *a, **k):
        if self.name == "project.json":
            raise PermissionError("locked")
        return real(self, *a, **k)
    monkeypatch.setattr(type(f), "read_text", locked)
    with pytest.raises(OSError):
        create_project(src, "en", "reels")
    assert f.read_bytes() == before and _bak(f.parent, "project.json") == []


def test_create_backs_up_corrupt_or_mismatched_file(tmp_path, home):
    src = make_tiny_video(tmp_path / "a.mp4")
    f = sp.workdir_for(src) / "project.json"
    for bad in ("{not json", '{"id": "x"}'):
        f.write_text(bad, encoding="utf-8")
        p = create_project(src, "en", "reels")
        assert load_project(p.id) == p
    baks = _bak(f.parent, "project.json")
    assert len(baks) == 2 and {b.read_text(encoding="utf-8") for b in baks} == {"{not json", '{"id": "x"}'}


def test_recreate_keeps_work_and_settings(tmp_path, home):
    src = make_tiny_video(tmp_path / "a.mp4")
    p = create_project(src, "en", "reels")
    hl = {"id": "h01", "start": 0.0, "end": 5.0, "w0": 0, "w1": 9, "score": 5, "title": "t", "features": {}}
    p.clips.append(ProjectClip(highlight=hl, kept=True, edl_file="clip-h01.edl.json"))
    save_project(p)
    q = create_project(src, "taglish", "tiktok")
    assert q == p and q.language == "en" and q.platform == "reels" and q.clips[0].kept


def test_registry_failure_is_best_effort(tmp_path, home, monkeypatch, capsys):
    src = make_tiny_video(tmp_path / "a.mp4")
    p = create_project(src, "en", "reels")

    def boom(*a, **k):
        raise OSError("registry locked")
    monkeypatch.setattr(sp, "_register", boom)
    assert load_project(p.id) == p
    assert create_project(src, "en", "reels") == p
    assert "registry" in capsys.readouterr().err


def test_corrupt_registry_is_backed_up(tmp_path, home):
    home.mkdir(parents=True)
    (home / "projects.json").write_text("garbage{", encoding="utf-8")
    p = create_project(make_tiny_video(tmp_path / "a.mp4"), "en", "reels")
    baks = _bak(home, "projects.json")
    assert len(baks) == 1 and baks[0].read_text(encoding="utf-8") == "garbage{"
    assert [x.id for x in list_recent()] == [p.id]


def test_create_validates_language_and_platform(tmp_path, home):
    src = make_tiny_video(tmp_path / "a.mp4")
    with pytest.raises(ValueError):
        create_project(src, "fr", "reels")
    with pytest.raises(ValueError):
        create_project(src, "en", "youtube")
    assert not (sp.workdir_for(src) / "project.json").exists()
