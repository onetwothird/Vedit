"""Studio project store (save / open / list recent) and output file naming."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from common import workdir_for
from highlights import Highlight

LANG_MODEL = {"en": ("en", "small"), "tl": ("tl", "large-v3"), "taglish": ("tl", "large-v3")}

PROJECT_FILE = "project.json"
MAX_NAME_LEN = 60  # whole name before ".mp4": "NN " prefix + title + any " (WxH)" suffix
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')


@dataclass
class ProjectClip:
    highlight: Highlight
    kept: bool
    edl_file: str  # relative to the workdir, e.g. "clip-h01.edl.json"


@dataclass
class Project:
    id: str
    type: Literal["clip"]
    source: str
    language: Literal["en", "tl", "taglish"]
    platform: Literal["reels", "shorts", "tiktok"]
    created: str
    clips: list[ProjectClip] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    last_job: dict | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_id(source: Path) -> str:
    key = os.path.normcase(str(Path(source).resolve()))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _home() -> Path:
    home = os.environ.get("VEDIT_HOME")
    if home:
        return Path(home)
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) if local else Path.home() / ".local" / "share") / "vedit"


def _registry_path() -> Path:
    return _home() / "projects.json"


def _atomic_write_json(path: Path, data) -> None:
    """Write JSON to a temp file beside `path`, then os.replace; on failure the old file is untouched."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_registry() -> dict:
    try:
        data = json.loads(_registry_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # missing or unreadable: the registry is only an index and rebuilds as projects are created
    return data if isinstance(data, dict) else {}


def _register(pid: str, wd: Path) -> None:
    reg = _read_registry()
    reg[pid] = {"workdir": str(wd), "opened": _now()}
    _atomic_write_json(_registry_path(), reg)


def _from_dict(d: dict) -> Project:
    clips = [ProjectClip(highlight=c["highlight"], kept=c["kept"], edl_file=c["edl_file"]) for c in d["clips"]]
    return Project(id=d["id"], type=d["type"], source=d["source"], language=d["language"], platform=d["platform"],
                   created=d["created"], clips=clips, settings=d["settings"], last_job=d["last_job"])


def _read_project(wd: Path) -> Project:
    f = wd / PROJECT_FILE
    try:
        return _from_dict(json.loads(f.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ValueError(f"corrupt or unreadable project file {f}: {e}") from e


def save_project(p: Project) -> None:
    _atomic_write_json(workdir_for(p.source) / PROJECT_FILE, asdict(p))


def create_project(source: Path, language: str, platform: str) -> Project:
    """New project for `source`. The id is derived from the path, so an existing project keeps its work."""
    source = Path(source).resolve()
    wd = workdir_for(source)
    try:
        p = _read_project(wd)
    except ValueError:
        p = Project(id=project_id(source), type="clip", source=str(source), language=language,
                    platform=platform, created=_now())
        save_project(p)
    _register(p.id, wd)
    return p


def load_project(pid: str) -> Project:
    """Raises KeyError for an unknown id and ValueError for a missing or corrupt project file."""
    entry = _read_registry()[pid]
    wd = Path(entry["workdir"])
    p = _read_project(wd)
    _register(pid, wd)
    return p


def list_recent(limit: int = 10) -> list[Project]:
    rows = sorted(_read_registry().items(), key=lambda kv: str(kv[1].get("opened", "")), reverse=True)
    out: list[Project] = []
    for _pid, entry in rows:
        try:
            out.append(_read_project(Path(entry["workdir"])))
        except (ValueError, KeyError, TypeError):
            continue  # workdir or project.json gone/corrupt: skip, don't raise
        if len(out) >= limit:
            break
    return out


def output_dir(source: Path) -> Path:
    source = Path(source)
    return source.parent / f"{source.stem} - vedit"


def output_name(index: int, title: str, aspect: str) -> str:
    # The "NN " prefix guarantees the name is never a Windows reserved device name (CON, NUL, COM1, ...).
    prefix = f"{index:02d}"
    suffix = "" if aspect == "9:16" else f" ({aspect.replace(':', 'x')})"
    clean = " ".join(_ILLEGAL.sub("", title or "").split()).strip(" .")
    budget = MAX_NAME_LEN - len(prefix) - 1 - len(suffix)
    if len(clean) > budget:
        cut = clean[:budget]
        if clean[budget] != " " and " " in cut:  # trim on a word boundary when possible
            cut = cut.rsplit(" ", 1)[0]
        clean = cut.rstrip(" .")
    return f"{prefix} {clean or f'Clip {prefix}'}{suffix}.mp4"
