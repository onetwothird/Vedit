"""Studio stories: photos or clips + a script -> a narrated, captioned video (story.py does the work).

A story is one folder under VEDIT_HOME/stories/<id>/ holding `story.doc.json` (what the app edits) and, written
just before a build, `story.json` (story.py's own format, see story.py). Paths in the doc are absolute.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from studio_projects import _ILLEGAL, _atomic_write_json, _home

ASPECTS = ("9:16", "16:9", "1:1")
CAPTION_STYLES = ("pop", "box", "clean", "none")
MOTIONS = ("auto", "in", "out", "left", "right", "none")
# free neural voices (edge-tts); the app shows these by name
VOICES = {
    "en-US-AndrewNeural": "Andrew (US English)",
    "en-US-AvaNeural": "Ava (US English)",
    "en-GB-RyanNeural": "Ryan (UK English)",
    "en-GB-SoniaNeural": "Sonia (UK English)",
    "fil-PH-AngeloNeural": "Angelo (Filipino)",
    "fil-PH-BlessicaNeural": "Blessica (Filipino)",
}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mts", ".3gp"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"}
MAX_SCENES = 60
MAX_TEXT = 2000
MAX_TITLE = 80
SILENT_SECONDS = 3.0  # a photo with nothing to say stays on screen this long
SID_RE = re.compile(r"^s[0-9a-f]{12}$")
DOC = "story.doc.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stories_root() -> Path:
    return _home() / "stories"


def is_story_id(sid: str) -> bool:
    return isinstance(sid, str) and bool(SID_RE.match(sid))


def story_dir(sid: str) -> Path:
    if not is_story_id(sid):
        raise KeyError(sid)
    return stories_root() / sid


def media_kind(path: str) -> str | None:
    ext = Path(path).suffix.lower()
    return "image" if ext in IMAGE_EXT else "video" if ext in VIDEO_EXT else None


def default_doc(sid: str, title: str = "") -> dict:
    now = _now()
    return {"id": sid, "title": title.strip()[:MAX_TITLE], "aspect": "9:16", "voice": "en-US-AndrewNeural",
            "captions": "pop", "music": None, "music_volume": 0.15, "scenes": [],
            "created": now, "updated": now, "output": None, "last_job": None}


def clean_doc(sid: str, body: dict, old: dict) -> dict:
    """The editable fields of `body`, checked; everything else is kept from `old`. Raises ValueError."""
    title = str(body.get("title", old["title"]) or "").strip()
    if len(title) > MAX_TITLE:
        raise ValueError(f"the title can be at most {MAX_TITLE} characters")
    aspect = body.get("aspect", old["aspect"])
    if aspect not in ASPECTS:
        raise ValueError(f"unsupported shape: {aspect}")
    voice = body.get("voice", old["voice"])
    if voice not in VOICES:
        raise ValueError(f"unsupported voice: {voice}")
    captions = body.get("captions", old["captions"])
    if captions not in CAPTION_STYLES:
        raise ValueError(f"unsupported caption style: {captions}")
    music = body.get("music", old["music"])
    if music is not None:
        if not isinstance(music, str) or Path(music).suffix.lower() not in AUDIO_EXT:
            raise ValueError("music must be an audio file")
    try:
        volume = float(body.get("music_volume", old["music_volume"]))
    except (TypeError, ValueError):
        raise ValueError("music_volume must be a number") from None
    if not 0.0 <= volume <= 1.0:
        raise ValueError("music_volume must be between 0 and 1")
    raw = body.get("scenes", old["scenes"])
    if not isinstance(raw, list):
        raise ValueError("scenes must be a list")
    if len(raw) > MAX_SCENES:
        raise ValueError(f"a story can have at most {MAX_SCENES} scenes")
    scenes = []
    for i, sc in enumerate(raw, 1):
        if not isinstance(sc, dict) or not isinstance(sc.get("media"), str) or not sc["media"]:
            raise ValueError(f"scene {i} needs a photo or a clip")
        kind = media_kind(sc["media"])
        if kind is None:
            raise ValueError(f"scene {i}: {Path(sc['media']).name} is not a photo or a video")
        text = str(sc.get("text") or "")
        if len(text) > MAX_TEXT:
            raise ValueError(f"scene {i}: the script can be at most {MAX_TEXT} characters")
        motion = sc.get("motion", "auto")
        if motion not in MOTIONS:
            raise ValueError(f"scene {i}: unsupported motion: {motion}")
        scenes.append({"media": sc["media"], "kind": kind, "text": text, "motion": motion,
                       "keep_audio": bool(sc.get("keep_audio")) and kind == "video"})
    return {**old, "id": sid, "title": title, "aspect": aspect, "voice": voice, "captions": captions,
            "music": music, "music_volume": round(volume, 3), "scenes": scenes, "updated": _now()}


def create_story(title: str = "") -> dict:
    sid = "s" + uuid.uuid4().hex[:12]
    doc = default_doc(sid, title)
    _atomic_write_json(story_dir(sid) / DOC, doc)
    return doc


def load_story_doc(sid: str) -> dict:
    """Raises KeyError for an unknown or unsafe id."""
    import json
    path = story_dir(sid) / DOC
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        raise KeyError(sid) from None
    except ValueError:
        raise KeyError(sid) from None
    return {**default_doc(sid), **doc, "id": sid}


def save_story_doc(doc: dict) -> None:
    _atomic_write_json(story_dir(doc["id"]) / DOC, doc)


def list_stories(limit: int = 20) -> list[dict]:
    root = stories_root()
    if not root.is_dir():
        return []
    docs = []
    for d in root.iterdir():
        if d.is_dir() and is_story_id(d.name) and (d / DOC).is_file():
            try:
                docs.append(load_story_doc(d.name))
            except KeyError:
                continue
    docs.sort(key=lambda s: s.get("updated") or "", reverse=True)
    return docs[:limit]


def story_media(doc: dict) -> set[str]:
    """Every file the story points at (only these may be served back to the app)."""
    files = {sc["media"] for sc in doc["scenes"]}
    if doc.get("music"):
        files.add(doc["music"])
    return files


def lines_of(text: str) -> list[str]:
    """One caption line per line of the script; blank lines are dropped."""
    return [" ".join(ln.split()) for ln in text.splitlines() if ln.strip()]


def output_file(doc: dict, out_dir: Path) -> Path:
    clean = " ".join(_ILLEGAL.sub("", doc["title"] or "").split()).strip(" .")[:60] or "Story"
    suffix = "" if doc["aspect"] == "9:16" else f" ({doc['aspect'].replace(':', 'x')})"
    return out_dir / f"{clean}{suffix}.mp4"


def check_buildable(doc: dict) -> None:
    """Raises ValueError with a message for the person when the story cannot be made yet."""
    if not doc["scenes"]:
        raise ValueError("add at least one photo or clip first")
    for i, sc in enumerate(doc["scenes"], 1):
        if not Path(sc["media"]).is_file():
            raise ValueError(f"scene {i}: {Path(sc['media']).name} was moved or deleted")
    if doc.get("music") and not Path(doc["music"]).is_file():
        raise ValueError(f"the music file {Path(doc['music']).name} was moved or deleted")


def to_story_json(doc: dict, out: Path) -> dict:
    """story.py's format (see story.py's docstring), with one narrator."""
    scenes = []
    for sc in doc["scenes"]:
        s: dict = {sc["kind"]: sc["media"]}
        if sc["kind"] == "image":
            s["motion"] = sc["motion"]
        if sc.get("keep_audio"):
            s["keep_audio"] = True  # a talking clip: its own voice, its own words as captions
        else:
            lines = lines_of(sc["text"])
            if lines:
                s["lines"] = [{"speaker": "narrator", "text": t} for t in lines]
            elif sc["kind"] == "image":
                s["duration"] = SILENT_SECONDS
        s["transition"] = "fade"
        scenes.append(s)
    story = {
        "title": doc["title"] or "Story",
        "output": {"path": str(out), "aspect": doc["aspect"]},
        "voices": {"narrator": {"engine": "edge", "voice": doc["voice"], "color": "#FFFFFF"}},
        "captions": {"enabled": False} if doc["captions"] == "none" else {"style": doc["captions"]},
        "scenes": scenes,
    }
    if doc.get("music"):
        story["music"] = {"path": doc["music"], "volume": doc["music_volume"]}
    return story


def write_story_json(doc: dict, out: Path) -> Path:
    path = story_dir(doc["id"]) / "story.json"
    _atomic_write_json(path, to_story_json(doc, out))
    return path
