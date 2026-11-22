"""Optional AI re-ranking of highlight candidates, using the user's own Claude or OpenAI key.

Only text leaves the machine: the transcript sentences with [mm:ss] stamps and the candidate ids and time
ranges. No audio, video, file names or paths. Any failure (network, timeout, bad JSON, nothing valid) returns
the offline candidates untouched with the code "ai_failed". The key is never logged or put in an exception.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from highlights import Highlight  # noqa: E402

DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-5"}
TIMEOUT = 30.0
HID_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")  # highlight ids are used in file names
TITLE_MAX = 120
AI_FAILED = "ai_failed"

SYSTEM_PROMPT = """You pick the best short clips from a video transcript for Reels, TikTok and Shorts.

The speech may be English, Filipino or Taglish (Tagalog and English mixed). Judge by meaning, whatever the language.
You get the transcript as sentences, each starting with its [mm:ss] time, and a list of candidate clips with ids and
time ranges found by an offline scorer.

Choose the best clips: a strong hook in the first seconds, a complete thought, something surprising, funny, useful
or emotional. Each clip must start and end on sentence boundaries, run 20 to 90 seconds, and not overlap another
clip. You may pick a candidate by its id, or propose your own window with a new id. Write each title in the
language of the clip, short and specific, under 60 characters, no quotes or hashtags.

Reply with JSON only, best clip first, no other text:
{"clips": [{"id": "h01", "title": "...", "start": 12.5, "end": 48.0, "why": "one short reason"}]}
start and end are seconds from the start of the video."""


def _stamp(sec: float) -> str:
    m, s = divmod(int(max(0.0, sec)), 60)
    return f"[{m:02d}:{s:02d}]"


def _user_message(transcript: dict, candidates: list[Highlight], limit: int) -> str:
    lines = [f"{_stamp(s['start'])} {s['text'].strip()}" for s in transcript.get("sentences", [])]
    cands = [{"id": c["id"], "start": c["start"], "end": c["end"]} for c in candidates]
    return (f"Pick at most {limit} clips.\n\nTranscript:\n" + "\n".join(lines)
            + "\n\nCandidates:\n" + json.dumps(cands))


def _parse_json(text: str) -> dict:
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S | re.I)
    if fence:
        t = fence.group(1).strip()
    try:
        return json.loads(t)
    except ValueError:
        a, b = t.find("{"), t.rfind("}")
        if a < 0 or b <= a:
            raise
        return json.loads(t[a:b + 1])


def _call(http, provider: str, api_key: str, model: str, user: str) -> str:
    if provider == "anthropic":
        r = http.post("https://api.anthropic.com/v1/messages", timeout=TIMEOUT,
                      headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json={"model": model, "max_tokens": 2000, "system": SYSTEM_PROMPT,
                            "messages": [{"role": "user", "content": user}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")
    if provider == "openai":
        r = http.post("https://api.openai.com/v1/chat/completions", timeout=TIMEOUT,
                      headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
                      json={"model": model, "response_format": {"type": "json_object"},
                            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                         {"role": "user", "content": user}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    raise ValueError("unknown provider")


def _nearest(values: list[float], x: float) -> int:
    return min(range(len(values)), key=lambda k: abs(values[k] - x))


def rank_with_llm(transcript: dict, candidates: list[Highlight], provider: Literal["anthropic", "openai"],
                  api_key: str, model: str | None = None, http=None, *, max_clips: int | None = None,
                  min_len: float = 20, max_len: float = 90) -> tuple[list[Highlight], str | None]:
    """Re-rank and retitle `candidates` with an LLM. Returns (clips, None), or (candidates, "ai_failed")."""
    try:
        import httpx  # lazy: the rest of the engine runs without it

        sents = transcript.get("sentences") or []
        if not sents or not candidates:
            raise ValueError("nothing to rank")
        words = transcript.get("words") or []
        duration = float(transcript.get("duration") or (words[-1]["end"] if words else sents[-1]["end"]))
        floor = min(min_len, duration)
        limit = max(1, max_clips or len(candidates))
        starts, ends = [float(s["start"]) for s in sents], [float(s["end"]) for s in sents]

        owned = http is None
        client = httpx.Client(timeout=TIMEOUT) if owned else http
        try:
            text = _call(client, provider, api_key, model or DEFAULT_MODELS[provider],
                         _user_message(transcript, candidates, limit))
        finally:
            if owned:
                client.close()
        clips = _parse_json(text)["clips"]
        if not isinstance(clips, list):
            raise ValueError("clips is not a list")

        by_id = {c["id"]: c for c in candidates}
        taken: list[tuple[float, float]] = []
        used: set[str] = set()
        picked: list[Highlight] = []
        for c in clips:
            try:
                a, b = float(c["start"]), float(c["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (-1.0 <= a < b <= duration + 1.0):
                continue
            i, j = _nearest(starts, a), _nearest(ends, b)
            if j < i:
                continue
            start, end = starts[i], ends[j]
            if not floor <= end - start <= max_len:
                continue
            if any(start < tb and ta < end for ta, tb in taken):
                continue
            cid = str(c.get("id") or "")
            if cid and not HID_RE.fullmatch(cid):  # ids become file names: never trust the model's
                continue
            if cid in used or not cid:
                cid = ""
            base = by_id.get(cid)
            if not cid:
                n = 1
                while f"h{n:02d}" in used or f"h{n:02d}" in by_id:
                    n += 1
                cid = f"h{n:02d}"
            title = re.sub(r"\s+", " ", str(c.get("title") or "")).strip()[:TITLE_MAX] or (base or {}).get("title", "")
            used.add(cid)
            taken.append((start, end))
            picked.append({"id": cid, "start": round(start, 3), "end": round(end, 3),
                           "w0": sents[i]["w0"], "w1": sents[j]["w1"], "score": 0, "title": title,
                           "features": dict(base["features"]) if base else {}})
            if len(picked) >= limit:
                break
        if not picked:
            raise ValueError("no valid clips")
        n = len(picked)
        for k, h in enumerate(picked):
            h["score"] = round(100 * (n - k) / n)
        return picked, None
    except Exception:  # noqa: BLE001 - every failure means "keep the offline picks"; never echo details (key safety)
        return candidates, AI_FAILED
