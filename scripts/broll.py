"""B-roll suggestions from a clip's transcript: moments where the speaker names a thing or place that a photo or
video in a folder the user picked shows.

Offline, a file matches when a word of its name (beach-sunset.jpg -> "beach", "sunset") is spoken in the clip; the
more of its name words fall near that moment, the stronger the match. With the user's own Claude or OpenAI key, the
AI also reads the clip's words and the file names (names only: no folders, no pixels, no audio) and picks moments
by meaning, so "the shore" can find beach.jpg. Any AI failure keeps the offline suggestions.
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv")
MAX_FILES = 2000  # a folder bigger than this is read only this far
MAX_DEPTH = 2  # subfolders below the picked folder that are searched
MAX_SUGGESTIONS = 12
LENGTH = 3.0  # seconds a suggested b-roll lasts (less near the end of the clip)
MIN_LENGTH = 0.5
WINDOW = 4  # words either side of a hit that count towards a multi-word name
AI_FAILED = "ai_failed"

# words a file name often carries that never name what the picture shows (camera prefixes, edit words, particles)
STOPWORDS = frozenset("""
a an and the of in on at to for with from by my our your his her their its is are was were be this that these those
img dsc dscn dscf pxl vid mov mvi gopr gh gx photo photos pic pics picture image images video videos clip clips shot
shots take final edit edited copy new old version ver hd uhd raw stock broll roll footage frame scene screenshot
whatsapp signal telegram camera cam phone iphone android export
ang ng mga sa na si ni kay at ay
""".split())


def _fold(text: str) -> str:
    """Lower case without accents: "Café" and "cafe" are the same word."""
    s = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def norm(word: str) -> str:
    """A spoken or file-name word reduced so plurals meet their singular ("beaches" -> "beach", "cities" -> "city")."""
    w = re.sub(r"[^0-9a-z]", "", _fold(word))
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("es") and w[:-2].endswith(("s", "x", "z", "ch", "sh")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def name_words(path: str | Path) -> list[str]:
    """The words of a file name that may name a thing or place, in order, without repeats."""
    stem = Path(path).stem
    stem = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem)  # camelCase -> camel Case
    out: list[str] = []
    for raw in re.split(r"[^0-9A-Za-zÀ-ÿ]+", stem):
        w = norm(raw)
        if len(w) < 3 or w.isdigit() or w in STOPWORDS or w in out:
            continue
        if re.fullmatch(r"\d+[a-z]{0,2}|[a-z]{0,3}\d+", w):  # 20240501, 4k, img2
            continue
        out.append(w)
    return out


def media_type(path: str | Path) -> str | None:
    ext = Path(path).suffix.lower()
    return "image" if ext in IMAGE_EXT else "video" if ext in VIDEO_EXT else None


def list_media(folder: str | Path, max_files: int = MAX_FILES) -> list[Path]:
    """Photos and videos in `folder` and up to MAX_DEPTH levels of subfolders, sorted, at most `max_files`.
    Hidden files and folders are skipped."""
    root = Path(folder)
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".")) if depth < MAX_DEPTH else []
        for f in sorted(filenames):
            if not f.startswith(".") and media_type(f):
                found.append(Path(dirpath) / f)
                if len(found) >= max_files:
                    return found
    return found


def clip_words(transcript: dict, ranges: list[tuple[float, float]]) -> list[dict]:
    """The words the clip plays (their middle inside a kept range), each with its index, and no hard fillers."""
    out = []
    for i, w in enumerate(transcript.get("words") or []):
        mid = (float(w["start"]) + float(w["end"])) / 2
        if w.get("filler") == "hard":
            continue
        if any(a <= mid <= b for a, b in ranges):
            out.append({"i": i, "text": str(w.get("text", "")), "start": float(w["start"]), "end": float(w["end"])})
    return out


def _range_end(ranges: list[tuple[float, float]], t: float) -> float:
    for a, b in ranges:
        if a <= t <= b:
            return b
    return t


def _suggestion(path: Path, t: float, ranges, word: str, source: str, why: str, score: float) -> dict:
    length = max(MIN_LENGTH, min(LENGTH, _range_end(ranges, t) - t))
    return {"path": str(path), "name": path.name, "type": media_type(path), "t": round(t, 2),
            "duration": round(length, 2), "word": word, "source": source, "why": why, "score": round(score, 3)}


def match_names(words: list[dict], files: list[Path], ranges: list[tuple[float, float]]) -> list[dict]:
    """Every file whose name is spoken in the clip, at its strongest moment, best first. A moment's score is how many
    of the name's words are said within WINDOW words of it, plus the share of the name that covers."""
    normed = [norm(w["text"]) for w in words]
    out = []
    for f in files:
        keys = name_words(f)
        if not keys:
            continue
        best: tuple[float, int] | None = None
        for k, n in enumerate(normed):
            if n not in keys:
                continue
            near = set(normed[max(0, k - WINDOW): k + WINDOW + 1]) & set(keys)
            score = len(near) + len(near) / len(keys)
            if best is None or score > best[0]:
                best = (score, k)
        if best is None:
            continue
        score, k = best
        hit = [w["text"] for w in words[max(0, k - WINDOW): k + WINDOW + 1] if norm(w["text"]) in keys]
        word = " ".join(dict.fromkeys(re.sub(r"[^\w'-]", "", h) for h in hit)).strip() or words[k]["text"]
        out.append(_suggestion(f, words[k]["start"], ranges, word, "name", "", score))
    out.sort(key=lambda s: (-s["score"], s["t"]))
    return out


def pick(candidates: list[dict], taken: list[tuple[float, float]] = (), used_paths=(),
         limit: int = MAX_SUGGESTIONS) -> list[dict]:
    """In the order given, keep suggestions that overlap no kept one (nor `taken` ranges) and show a file once.
    Returned in time order."""
    spans = list(taken)
    used = {os.path.normcase(str(p)) for p in used_paths}
    kept = []
    for s in candidates:
        key = os.path.normcase(s["path"])
        a, b = s["t"], s["t"] + s["duration"]
        if key in used or any(a < y and x < b for x, y in spans):
            continue
        kept.append(s)
        used.add(key)
        spans.append((a, b))
        if len(kept) >= limit:
            break
    return sorted(kept, key=lambda s: s["t"])


# ---- the AI pass ----------------------------------------------------------------------------------------------

AI_PROMPT = """You suggest b-roll for a short video. You get the words of the clip, each line starting with its time
in seconds, and the file names of the user's photos and videos.

Find moments where the speaker names a concrete thing or place (an object, food, animal, building, city, landmark,
product) that one of the files plausibly shows, judging by the file name. The speech may be English, Filipino or
Taglish. Skip abstract talk. Use each file at most once, and only files from the list.

Reply with JSON only, earliest moment first, no other text:
{"broll": [{"t": 12.4, "file": "beach-sunset.jpg", "word": "the beach", "why": "one short reason"}]}
t is the time of the word that names the thing."""


def _lines(words: list[dict], per_line: int = 12) -> str:
    rows = []
    for k in range(0, len(words), per_line):
        chunk = words[k:k + per_line]
        rows.append(f"[{chunk[0]['start']:.1f}] " + " ".join(w["text"] for w in chunk))
    return "\n".join(rows)


def suggest_with_llm(words: list[dict], files: list[Path], ranges: list[tuple[float, float]], provider: str,
                     api_key: str, model: str | None = None, http=None) -> tuple[list[dict], str | None]:
    """AI suggestions (best first), or ([], "ai_failed"). Only the clip's words and the file names are sent."""
    from highlights_llm import DEFAULT_MODELS, TIMEOUT, _call, _parse_json

    try:
        import httpx  # lazy: the rest of the engine runs without it

        if not words or not files:
            return [], None
        by_name: dict[str, Path] = {}
        for f in files:
            by_name.setdefault(f.name.lower(), f)
        user = ("Clip words:\n" + _lines(words) + "\n\nFiles:\n" + "\n".join(sorted({f.name for f in files})))
        owned = http is None
        client = httpx.Client(timeout=TIMEOUT) if owned else http
        try:
            text = _call(client, provider, api_key, model or DEFAULT_MODELS[provider], user, system=AI_PROMPT)
        finally:
            if owned:
                client.close()
        items = _parse_json(text)["broll"]
        if not isinstance(items, list):
            raise ValueError("broll is not a list")
        starts = [w["start"] for w in words]
        out = []
        for n, item in enumerate(items):
            try:
                t, name = float(item["t"]), str(item["file"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            f = by_name.get(Path(name).name.lower())
            if f is None or not (starts[0] - 1 <= t <= words[-1]["end"] + 1):
                continue
            k = min(range(len(starts)), key=lambda j: abs(starts[j] - t))  # snap to a word the clip plays
            word = str(item.get("word") or words[k]["text"])[:60]
            why = str(item.get("why") or "")[:120]
            out.append(_suggestion(f, starts[k], ranges, word, "ai", why, 100.0 - n))
        return out, None
    except Exception:  # noqa: BLE001 - network, auth, bad JSON: keep the offline matches; never echo the key
        return [], AI_FAILED


def suggest(transcript: dict, ranges: list[tuple[float, float]], folder: str | Path | None, *, overlays=(),
            ai: dict | None = None, http=None, named: list[tuple[str, str]] | None = None) -> dict:
    """Suggestions for one clip: {"suggestions": [...], "files": n, "ai": None | "ok" | "ai_failed"}.
    `ranges` are the clip's kept source ranges; existing b-roll `overlays` keep their moments and files.
    `named` (cloud) replaces the folder: (reference, original file name) pairs, matched by name and suggested by
    reference; anything that is not a photo or a video by its name is left out."""
    refs: dict[str, str] = {}
    if named is not None:
        files = []
        for n, (ref, name) in enumerate(named):
            if media_type(name):
                f = Path(str(n)) / Path(name).name  # unique per file; matching only reads the name
                refs[str(f)] = ref
                files.append(f)
    else:
        files = list_media(folder)
    words = clip_words(transcript, ranges)
    taken, used = [], []
    for o in overlays or []:
        if isinstance(o, dict) and o.get("type") in ("image", "video"):
            t = float((o.get("at") or {}).get("t", 0))
            taken.append((t, t + float(o.get("duration", 0))))
            used.append(o.get("path", ""))
    candidates: list[dict] = []
    status = None
    if ai and ai.get("api_key"):
        found, err = suggest_with_llm(words, files, ranges, ai["provider"], ai["api_key"], ai.get("model"), http)
        candidates += found
        status = err or "ok"
    candidates += match_names(words, files, ranges)
    for c in candidates:
        c["path"] = refs.get(c["path"], c["path"])
    out = pick(candidates, taken, used)
    for s in out:
        s.pop("score", None)
    return {"suggestions": out, "files": len(files), "ai": status}
