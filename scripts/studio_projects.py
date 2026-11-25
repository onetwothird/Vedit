"""Studio project store (save / open / list recent) and output file naming."""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from common import workdir_for
from highlights import Highlight

LANG_MODEL = {"en": ("en", "small"), "tl": ("tl", "large-v3"), "taglish": ("tl", "large-v3")}

PLATFORMS = ("reels", "shorts", "tiktok", "youtube")
# clip: many highlights cut from a long video. vlog / reel: the whole video as one tidied edit (16:9 or vertical).
TYPES = ("clip", "vlog", "reel")
PLATFORM_FOR = {"clip": ("reels", "shorts", "tiktok"), "reel": ("reels", "shorts", "tiktok"), "vlog": ("youtube",)}
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
    type: Literal["clip", "vlog", "reel"]
    source: str
    language: Literal["en", "tl", "taglish"]
    platform: Literal["reels", "shorts", "tiktok", "youtube"]
    created: str
    clips: list[ProjectClip] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    last_job: dict | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_id(source: Path, kind: str = "clip") -> str:
    """From the path, so opening the same video again finds its project. A clip project keeps the plain path's id."""
    key = os.path.normcase(str(Path(source).resolve())) + ("" if kind == "clip" else f"|{kind}")
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def project_file(kind: str) -> str:
    """Each type has its own file in the video's workdir, so they share the transcript but never each other's edits."""
    return PROJECT_FILE if kind == "clip" else f"project-{kind}.json"


USER_HOME: contextvars.ContextVar[Path | None] = contextvars.ContextVar("vedit_user_home", default=None)


@contextlib.contextmanager
def use_home(path: Path):
    """Make `path` the app root (registry, stories, batches, brand kit) for this context, e.g. one web request."""
    token = USER_HOME.set(Path(path))
    try:
        yield
    finally:
        USER_HOME.reset(token)


def _home() -> Path:
    override = USER_HOME.get()
    if override is not None:
        return override
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
            f.flush()
            os.fsync(f.fileno())
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


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _backup(path: Path) -> Path:
    """Move an unreadable file aside (never delete it) so a fresh one can take its place."""
    bak = path.with_name(f"{path.name}.bak-{_stamp()}")
    os.replace(path, bak)
    return bak


def _register(pid: str, wd: Path, file: str = PROJECT_FILE) -> None:
    path = _registry_path()
    try:
        reg = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(reg, dict):
            raise ValueError("registry is not an object")
    except FileNotFoundError:
        reg = {}
    except ValueError:  # unparseable: keep a copy before rewriting
        _backup(path)
        reg = {}
    reg[pid] = {"workdir": str(wd), "opened": _now(), **({} if file == PROJECT_FILE else {"file": file})}
    _atomic_write_json(path, reg)


def _touch_registry(pid: str, wd: Path, file: str = PROJECT_FILE) -> None:
    """Recency bump is best-effort: the project itself is already safely on disk."""
    try:
        _register(pid, wd, file)
    except Exception as e:  # noqa: BLE001
        print(f"vedit: could not update project registry: {e}", file=sys.stderr)


def _from_dict(d: dict) -> Project:
    clips = [ProjectClip(highlight=c["highlight"], kept=c["kept"], edl_file=c["edl_file"]) for c in d["clips"]]
    return Project(id=d["id"], type=d["type"], source=d["source"], language=d["language"], platform=d["platform"],
                   created=d["created"], clips=clips, settings=d["settings"], last_job=d["last_job"])


def _read_project(wd: Path, file: str = PROJECT_FILE) -> Project:
    f = wd / file
    try:
        return _from_dict(json.loads(f.read_text(encoding="utf-8")))
    except (ValueError, KeyError, TypeError) as e:  # other OSErrors (locks, permissions) propagate
        raise ValueError(f"corrupt or unreadable project file {f}: {e}") from e


def save_project(p: Project) -> None:
    _atomic_write_json(workdir_for(p.source) / project_file(p.type), asdict(p))


def create_project(source: Path, language: str, platform: str, kind: str = "clip") -> Project:
    """New project for `source`. The id is derived from the path, so an existing project keeps its work.

    A missing project.json starts fresh; a corrupt one is moved to project.json.bak-<stamp> first; any other
    I/O error propagates so a valid project is never replaced.
    """
    if language not in LANG_MODEL:
        raise ValueError(f"unsupported language: {language!r}")
    if kind not in TYPES:
        raise ValueError(f"unsupported project type: {kind!r}")
    if platform not in PLATFORM_FOR[kind]:
        raise ValueError(f"unsupported platform for a {kind} project: {platform!r}")
    source = Path(source).resolve()
    wd = workdir_for(source)
    file = project_file(kind)
    try:
        p = _read_project(wd, file)
    except (FileNotFoundError, ValueError) as e:
        if isinstance(e, ValueError):
            _backup(wd / file)
        p = Project(id=project_id(source, kind), type=kind, source=str(source), language=language,
                    platform=platform, created=_now())
        save_project(p)
    _touch_registry(p.id, wd, file)
    return p


def load_project(pid: str) -> Project:
    """Raises KeyError for an unknown id and ValueError for a missing or corrupt project file."""
    entry = _read_registry()[pid]
    wd = Path(entry["workdir"])
    file = entry.get("file", PROJECT_FILE)
    try:
        p = _read_project(wd, file)
    except FileNotFoundError as e:
        raise ValueError(f"project file missing in {wd}") from e
    _touch_registry(pid, wd, file)
    return p


def list_recent(limit: int = 10) -> list[Project]:
    rows = sorted(_read_registry().items(), key=lambda kv: str(kv[1].get("opened", "")), reverse=True)
    out: list[Project] = []
    for _pid, entry in rows:
        try:
            out.append(_read_project(Path(entry["workdir"]), entry.get("file", PROJECT_FILE)))
        except (OSError, ValueError, KeyError, TypeError):
            continue  # workdir or project.json gone/corrupt: skip, don't raise
        if len(out) >= limit:
            break
    return out


def output_dir(source: Path) -> Path:
    source = Path(source)
    return source.parent / f"{source.stem} - Vedit"


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
