"""Portable project bundles: export, import, no machine paths, safe file names, size guard."""
import json

import pytest

import sync_bundle
from studio_projects import ProjectClip, _home, create_project, load_project, save_project, use_home, workdir_for


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def make_edl(src, broll):
    return {"sources": {"main": str(src)}, "output": {"path": str(src.with_suffix(".mp4")), "aspect": "9:16"},
            "clips": [{"src": "main", "in": 0.0, "out": 4.0}],
            "overlays": [{"type": "broll", "path": broll, "mode": "full"}, {"type": "text", "text": "/hi there"}]}


@pytest.fixture
def desk(tmp_path):
    """A desktop project with a transcript, highlights, an edl with b-roll and asset refs, and a brand kit."""
    home = tmp_path / "desk home"
    with use_home(home):
        src = tmp_path / "My Videos" / "talk.mp4"
        src.parent.mkdir()
        src.write_bytes(b"x")
        p = create_project(src, "en", "reels")
        wd = workdir_for(src)
        _write(wd / "transcript.main.json", {"words": [{"w": "hi", "start": 0, "end": 1}], "path": str(src)})
        _write(wd / "highlights.json", {"source": str(src), "highlights": [], "reason": ""})
        _write(wd / "clip-h01.edl.json", make_edl(src, str(tmp_path / "b" / "cat.mp4")))
        _write(wd / "clip-h02.edl.json", make_edl(src, "asset:aabcdef012345"))
        p.clips = [ProjectClip({"id": "h01"}, True, "clip-h01.edl.json"),
                   ProjectClip({"id": "h02"}, False, "clip-h02.edl.json")]
        p.settings.update(cloud_id="cid1", cloud_rev=3, web_video=True, title="Talk",
                          proxy=str(tmp_path / "proxy" / "p.mp4"), workdir=str(tmp_path / "wd"))
        e = json.loads((wd / "clip-h02.edl.json").read_text(encoding="utf-8"))
        e["note"] = "/srv/u/1/x.mp4"  # a POSIX path under a key the exporter has never heard of
        e["output"]["alt"] = "/srv/u/1/y.mp4"
        _write(wd / "clip-h02.edl.json", e)
        save_project(p)
        _write(home / "brand.json", {"logo": str(tmp_path / "logo.png"), "music": "asset:a0123456789ab", "color": "#fff"})
        yield p, src, tmp_path


def forms(tmp):
    s = str(tmp)
    return {s, s.replace("\\", "/"), s.replace("\\", "\\\\"), json.dumps(s)[1:-1]}


def test_export_has_no_machine_paths(desk):
    p, src, tmp = desk
    with use_home(tmp / "desk home"):
        b = sync_bundle.export_bundle(p)
    text = json.dumps(b)
    for f in forms(tmp):
        assert f not in text
    assert b["format"] == 1 and b["id"] == "cid1" and b["rev"] == 3 and b["has_video_in_cloud"] is True
    assert b["project"]["source"] == "source:"
    e1 = b["files"]["clip-h01.edl.json"]
    assert e1["sources"]["main"] == "source:" and e1["output"]["path"] == ""
    assert e1["overlays"][0]["path"] == "local:cat.mp4"
    assert e1["overlays"][1]["text"] == "/hi there"
    assert b["files"]["clip-h02.edl.json"]["overlays"][0]["path"] == "asset:aabcdef012345"
    assert b["files"]["highlights.json"]["source"] == "source:"
    assert "/srv/u/1" not in text and "proxy" not in b["project"]["settings"] and "workdir" not in text
    assert b["project"]["settings"] == {"title": "Talk", "web_video": True}
    assert b["files"]["clip-h02.edl.json"]["note"] == "local:x.mp4"
    assert b["brand"]["logo"] == "local:logo.png" and b["brand"]["music"] == "asset:a0123456789ab"


def test_roundtrip_import_rewrites_to_the_new_source(desk, tmp_path_factory):
    p, src, tmp = desk
    with use_home(tmp / "desk home"):
        b = sync_bundle.export_bundle(p)
    other = tmp_path_factory.mktemp("web")
    new_src = other / "source.mp4"
    new_src.write_bytes(b"y")
    with use_home(other / "home"):
        q = sync_bundle.import_bundle(json.loads(json.dumps(b)), new_src)
        assert load_project(q.id) == q
        assert q.settings["cloud_id"] == "cid1" and q.settings["cloud_rev"] == 3
        assert q.settings["title"] == "Talk" and "proxy" not in q.settings and "workdir" not in q.settings
        wd = workdir_for(new_src)
        edl = json.loads((wd / "clip-h01.edl.json").read_text(encoding="utf-8"))
        assert edl["sources"]["main"] == str(new_src)
        assert edl["overlays"][0]["path"] == "local:cat.mp4"
        assert [c.edl_file for c in q.clips] == ["clip-h01.edl.json", "clip-h02.edl.json"]
        assert q.clips[1].kept is False and q.source == str(new_src)
        assert (wd / "transcript.main.json").exists() and (wd / "highlights.json").exists()
        assert json.loads((_home() / "brand.json").read_text(encoding="utf-8"))["music"] == "asset:a0123456789ab"


def test_import_updates_existing_cloud_id_in_place(desk, tmp_path_factory):
    p, src, tmp = desk
    with use_home(tmp / "desk home"):
        b = sync_bundle.export_bundle(p)
        b["rev"] = 4
        b["files"]["clip-h01.edl.json"]["clips"][0]["out"] = 9.0
        q = sync_bundle.import_bundle(b, None)
        assert q.id == p.id and q.source == p.source
        assert q.settings["cloud_rev"] == 4 and q.settings["proxy"] == p.settings["proxy"]
        edl = json.loads((workdir_for(src) / "clip-h01.edl.json").read_text(encoding="utf-8"))
        assert edl["clips"][0]["out"] == 9.0 and edl["sources"]["main"] == str(src)


def test_import_without_source_makes_a_work_folder(desk, tmp_path_factory):
    p, src, tmp = desk
    with use_home(tmp / "desk home"):
        b = sync_bundle.export_bundle(p)
    other = tmp_path_factory.mktemp("web2")
    with use_home(other):
        q = sync_bundle.import_bundle(b, None)
        assert (other / "work") in __import__("pathlib").Path(q.source).parents


def test_oversize_bundle_rejected_both_ways(desk, monkeypatch):
    p, src, tmp = desk
    monkeypatch.setattr(sync_bundle, "MAX_BUNDLE_BYTES", 500)
    with use_home(tmp / "desk home"), pytest.raises(ValueError, match="too large"):
        sync_bundle.export_bundle(p)
    with use_home(tmp / "x"), pytest.raises(ValueError, match="too large"):
        sync_bundle.import_bundle({"format": 1, "pad": "z" * 1000}, None)


def test_unknown_format_rejected(desk):
    p, src, tmp = desk
    with use_home(tmp / "desk home"):
        b = sync_bundle.export_bundle(p)
    for bad in (2, None, "1"):
        b["format"] = bad
        with use_home(tmp / "n"), pytest.raises(ValueError, match="format"):
            sync_bundle.import_bundle(b, None)


@pytest.mark.parametrize("name", ["../x.json", "..\\x.json", "/abs/x.json", "C:\\x.json", "sub/edl.h.json",
                                  "notes.txt", "project.json", "x.edl.json.exe"])
def test_bad_file_names_rejected_and_nothing_written(desk, tmp_path_factory, name):
    p, src, tmp = desk
    with use_home(tmp / "desk home"):
        b = sync_bundle.export_bundle(p)
    other = tmp_path_factory.mktemp("evil")
    b["files"][name] = {"evil": True}
    with use_home(other / "home"), pytest.raises(ValueError):
        sync_bundle.import_bundle(b, other / "sub" / "source.mp4")
    assert not (other / "sub").exists() and not (other / "home").exists()
    assert not (other / "x.json").exists() and not (other.parent / "x.json").exists()


def test_bad_clip_edl_name_rejected(desk, tmp_path_factory):
    p, src, tmp = desk
    with use_home(tmp / "desk home"):
        b = sync_bundle.export_bundle(p)
    b["project"]["clips"][0]["edl_file"] = "../../x.edl.json"
    other = tmp_path_factory.mktemp("evil2")
    with use_home(other), pytest.raises(ValueError):
        sync_bundle.import_bundle(b, other / "s.mp4")


def test_import_ignores_machine_local_settings(desk, tmp_path_factory):
    p, src, tmp = desk
    with use_home(tmp / "desk home"):
        b = sync_bundle.export_bundle(p)
    b["project"]["settings"].update(proxy="/etc/passwd", workdir="/srv/users/other", expired=True,
                                    cloud_id="victim", cloud_rev=99, title="New")
    other = tmp_path_factory.mktemp("settings")
    with use_home(other):  # a new project
        q = sync_bundle.import_bundle(b, other / "s.mp4")
        assert q.settings == {"title": "New", "web_video": True, "cloud_id": "cid1", "cloud_rev": 3}
    with use_home(tmp / "desk home"):  # an in-place update keeps this machine's own settings
        q = sync_bundle.import_bundle(b, None)
        assert q.settings["proxy"] == p.settings["proxy"] and q.settings["workdir"] == p.settings["workdir"]
        assert q.settings["cloud_id"] == "cid1" and "expired" not in q.settings and q.settings["title"] == "New"


def _tamper_edl_source(b, v):
    b["files"]["clip-h01.edl.json"]["sources"]["main"] = v


def _tamper_edl_source_dict(b, v):
    b["files"]["clip-h01.edl.json"]["sources"]["main"] = {"path": v, "offset": 1.0}


def _tamper_overlay(b, v):
    b["files"]["clip-h01.edl.json"]["overlays"][0]["path"] = v


def _tamper_logo(b, v):
    b["brand"]["logo"] = v


def _tamper_font(b, v):
    b["brand"]["fonts"] = {"700": v}


@pytest.mark.parametrize("tamper", [_tamper_edl_source, _tamper_edl_source_dict, _tamper_overlay, _tamper_logo,
                                    _tamper_font])
@pytest.mark.parametrize("bad", ["/etc/passwd", "C:\\Windows\\x.png", "../../x.png", "local:../x.png",
                                 "local:a/b.png", "local:..", "asset:../x", "asset:nothex", "asset:a0123456789ab\n",
                                 "rel/x.png", ""])
def test_crafted_paths_rejected_and_nothing_written(desk, tmp_path_factory, tamper, bad):
    p, src, tmp = desk
    with use_home(tmp / "desk home"):
        b = sync_bundle.export_bundle(p)
    tamper(b, bad)
    other = tmp_path_factory.mktemp("crafted")
    with use_home(other / "home"), pytest.raises(ValueError):
        sync_bundle.import_bundle(b, other / "sub" / "source.mp4")
    assert not (other / "sub").exists() and not (other / "home").exists()


def test_import_resets_output_path(desk, tmp_path_factory):
    p, src, tmp = desk
    with use_home(tmp / "desk home"):
        b = sync_bundle.export_bundle(p)
    b["files"]["clip-h01.edl.json"]["output"]["path"] = "/etc/cron.d/x"
    other = tmp_path_factory.mktemp("outp")
    with use_home(other):
        sync_bundle.import_bundle(b, other / "s.mp4")
        edl = json.loads((workdir_for(other / "s.mp4") / "clip-h01.edl.json").read_text(encoding="utf-8"))
    assert edl["output"]["path"] == ""
