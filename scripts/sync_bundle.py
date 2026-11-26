"""Portable project bundles: a project as one JSON document that moves between the desktop app and the website.

export_bundle(p) turns a project folder into a dict with no path from this machine in it; import_bundle(bundle,
source) writes one back into the current home (a new project, or the one whose settings.cloud_id matches).
"""
from __future__ import annotations

import json
import re
import secrets
from pathlib import Path, PureWindowsPath

from common import load_json, save_json, workdir_for
from studio_projects import (LANG_MODEL, PLATFORM_FOR, TYPES, Project, ProjectClip, _home, create_project,
                             list_recent, project_doc, save_project)

FORMAT = 1
MAX_BUNDLE_BYTES = 20 * 1024 * 1024
SOURCE = "source:"
LOCAL = "local:"
ASSET = "asset:"

# the only files a bundle may carry, and the only names it may write (no separators, so no traversal)
_NAME = r"[A-Za-z0-9_-]+"
_FILE_PATTERNS = (re.compile(r"transcript\." + _NAME + r"\.json"), re.compile(r"highlights\.json"),
                  re.compile(r"edl\." + _NAME + r"\.json"), re.compile(_NAME + r"\.edl\.json"))
_DRIVE = re.compile(r"^(?:[A-Za-z]:[/\\]|\\\\)")  # C:\x, C:/x, \\server\share
# keys whose value may be a POSIX absolute path ("/home/x"); other text starting with "/" is just text
_PATHY = {"path", "source", "src", "file", "logo", "intro", "outro", "music", "lut", "header_overlay", "background",
          "fonts", "font", "sources"}


def _safe_name(name) -> bool:
    return isinstance(name, str) and any(p.fullmatch(name) for p in _FILE_PATTERNS)


def _is_abs(s: str, pathy: bool) -> bool:
    if s.startswith((ASSET, SOURCE, LOCAL)) or "\n" in s:
        return False
    return bool(_DRIVE.match(s)) or (pathy and s.startswith("/") and len(s) > 1)


def _basename(s: str) -> str:
    return PureWindowsPath(s.replace("\\", "/")).name or "file"


def _portable(node, key: str = "", pathy: bool = False, in_sources: bool = False):
    """Copy of `node` with every absolute path replaced: a `source` key or anything in an edl's `sources` map ->
    "source:", any other -> "local:<basename>". Cloud `asset:` references and everything that is not a path pass."""
    in_sources = in_sources or key == "sources"
    pathy = pathy or key in _PATHY
    if isinstance(node, dict):
        return {k: _portable(v, k, pathy, in_sources) for k, v in node.items()}
    if isinstance(node, list):
        return [_portable(v, key, pathy, in_sources) for v in node]
    if isinstance(node, str) and _is_abs(node, pathy):
        return SOURCE if in_sources or key == "source" else LOCAL + _basename(node)
    return node


def _resolve_source(node, source: str):
    if isinstance(node, dict):
        return {k: _resolve_source(v, source) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_source(v, source) for v in node]
    return source if node == SOURCE else node


def _brand_file() -> Path:
    from studio_server import brand_file  # lazy: studio_server imports this module's callers

    return brand_file()


def _sized(bundle: dict) -> None:
    if len(json.dumps(bundle, ensure_ascii=False).encode("utf-8")) > MAX_BUNDLE_BYTES:
        raise ValueError("Project bundle is too large (limit 20 MB)")


def export_bundle(p: Project) -> dict:
    wd = workdir_for(p.source)
    names = ["highlights.json"] + sorted(f.name for f in wd.glob("transcript.*.json")) \
        + sorted(f.name for f in wd.glob("*.edl.json") if not f.name.startswith("export-"))
    files = {}
    for name in dict.fromkeys(names):
        if not _safe_name(name) or not (wd / name).is_file():
            continue
        try:
            content = _portable(load_json(wd / name))
            out = content.get("output") if isinstance(content, dict) else None
            if isinstance(out, dict) and str(out.get("path", "")).startswith(LOCAL):
                out["path"] = ""  # a render target on this machine, meaningless elsewhere
            files[name] = content
        except ValueError:
            continue  # an unreadable file is left out rather than failing the whole sync
    doc = project_doc(p)
    doc["last_job"] = None
    doc.pop("last_activity", None)
    brand = None
    bf = _brand_file()
    if bf.is_file():
        try:
            data = load_json(bf)
            brand = _portable(data) if isinstance(data, dict) and data else None
        except ValueError:
            brand = None
    bundle = {"format": FORMAT, "id": p.settings.get("cloud_id"), "rev": int(p.settings.get("cloud_rev", 0)),
              "title": str(p.settings.get("title") or Path(p.source).stem), "project": _portable(doc),
              "files": files, "brand": brand, "has_video_in_cloud": bool(p.settings.get("web_video", False))}
    _sized(bundle)
    return bundle


def _validate(bundle) -> tuple[dict, dict, dict]:
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        raise ValueError(f"Unsupported project bundle format: {bundle.get('format') if isinstance(bundle, dict) else None!r}")
    _sized(bundle)
    doc, files = bundle.get("project"), bundle.get("files")
    if not isinstance(doc, dict) or not isinstance(files, dict):
        raise ValueError("Project bundle needs a project and files")
    for name, content in files.items():
        if not _safe_name(name):
            raise ValueError(f"Project bundle has a file name that is not allowed: {name!r}")
        if not isinstance(content, dict):
            raise ValueError(f"Project bundle file {name!r} is not a JSON object")
    clips = doc.get("clips")
    if not isinstance(clips, list):
        raise ValueError("Project bundle needs a clip list")
    for c in clips:
        if not isinstance(c, dict) or not _safe_name(c.get("edl_file")) or not isinstance(c.get("highlight"), dict):
            raise ValueError("Project bundle has an invalid clip")
    kind = doc.get("type")
    if (kind not in TYPES or doc.get("language") not in LANG_MODEL or doc.get("platform") not in PLATFORM_FOR[kind]
            or not isinstance(doc.get("settings", {}), dict)):
        raise ValueError("Project bundle has an unsupported type, language or platform")
    brand = bundle.get("brand")
    if brand is not None and not isinstance(brand, dict):
        raise ValueError("Project bundle brand kit is not a JSON object")
    return doc, files, brand


def _existing(cloud_id) -> Project | None:
    if not cloud_id:
        return None
    return next((q for q in list_recent(limit=10**9) if q.settings.get("cloud_id") == cloud_id), None)


def import_bundle(bundle: dict, source: Path | None) -> Project:
    """Write `bundle` into the current home. An existing project with the same cloud id is updated in place (its
    own source stays); otherwise a new project is made for `source`, or for a placeholder under work/ without one."""
    doc, files, brand = _validate(bundle)
    cloud_id, rev = bundle.get("id"), bundle.get("rev", 0)
    if cloud_id is not None and not isinstance(cloud_id, str):
        raise ValueError("Project bundle id must be a string")
    if not isinstance(rev, int) or isinstance(rev, bool):
        raise ValueError("Project bundle rev must be an integer")
    p = _existing(cloud_id)
    if p is None:
        src = Path(source) if source is not None else _home() / "work" / secrets.token_hex(8) / "source.mp4"
        p = create_project(src, doc["language"], doc["platform"], doc["type"])
    wd = workdir_for(p.source)
    resolved = _resolve_source(doc, p.source)
    p.language, p.platform = resolved["language"], resolved["platform"]
    p.clips = [ProjectClip(highlight=c["highlight"], kept=bool(c.get("kept", True)), edl_file=c["edl_file"])
               for c in resolved["clips"]]
    p.settings = {**p.settings, **resolved.get("settings", {})}
    if cloud_id is not None:
        p.settings["cloud_id"] = cloud_id
    p.settings["cloud_rev"] = rev
    for name, content in files.items():
        save_json(_resolve_source(content, p.source), wd / name)
    if brand and not _brand_file().is_file():  # never replace the kit already on this machine
        save_json(brand, _brand_file())
    save_project(p)
    return p
