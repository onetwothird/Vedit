"""Per-request user root: use_home() isolates the registry, stories, batches and brand kit."""
import pytest

import studio_projects as sp
import studio_server
import studio_stories
from studio_projects import create_project, list_recent, load_project, use_home
from test_studio_projects import make_tiny_video


def test_projects_are_isolated_per_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VEDIT_HOME", str(tmp_path / "desktop"))
    src = make_tiny_video(tmp_path / "v.mp4")
    with use_home(tmp_path / "a"):
        pid = create_project(src, "taglish", "reels").id
        assert [p.id for p in list_recent()] == [pid]
    with use_home(tmp_path / "b"):
        assert list_recent() == []
        with pytest.raises(KeyError):
            load_project(pid)
    assert sp.USER_HOME.get() is None


def test_brand_and_stories_follow_the_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VEDIT_HOME", str(tmp_path / "desktop"))
    with use_home(tmp_path / "a"):
        assert studio_server.brand_file() == tmp_path / "a" / "brand.json"
        assert studio_stories.stories_root() == tmp_path / "a" / "stories"
        assert studio_server._batch_path("b" + "0" * 12).parent == tmp_path / "a" / "batches"
    assert studio_server.brand_file() == tmp_path / "desktop" / "brand.json"


def test_unset_home_keeps_desktop_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("VEDIT_HOME", str(tmp_path / "desktop"))
    assert sp._home() == tmp_path / "desktop"
