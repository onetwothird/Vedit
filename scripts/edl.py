"""Load + resolve an edit decision list (edl.json) into a concrete timeline.

Time-base rule:
  * Unsynced source (no "offset"): clip in/out/words are in THAT source's own time + transcript.
  * Synced source (has "offset", e.g. a 2nd camera): in/out/words are in the REFERENCE ("main") time base
    and use main's transcript; the renderer adds the offset. Its audio defaults to main's audio.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import die, even, ffprobe, load_json, parse_time  # noqa: E402

ASPECTS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}
DEFAULT_PAD = (0.10, 0.18)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic"}


def is_image(path: str, info: dict) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXT or (info.get("vcodec") in ("png", "mjpeg", "webp", "bmp", "tiff")
                                                      and info.get("duration", 0) < 0.5)


class Timeline:
    def __init__(self, edl_path: str | Path, aspect: str | None = None):
        self.path = Path(edl_path).resolve()
        self.dir = self.path.parent
        self.edl = load_json(self.path)
        if aspect:  # re-target the same edit to another format (multi-format export)
            out = dict(self.edl.get("output") or {})
            out.pop("width", None)
            out.pop("height", None)
            out["aspect"] = aspect
            p = Path(out.get("path", "final.mp4"))
            out["path"] = str(p.with_name(p.stem + "_" + aspect.replace(":", "x") + p.suffix))
            self.edl["output"] = out
            if aspect in ("9:16", "4:5", "1:1"):
                for c in self.edl.get("clips", []):
                    c.setdefault("crop_x", "auto")
        self.sources: dict[str, dict] = {}
        self._load_sources()
        self.out = self._output()
        self.clips: list[dict] = []
        self._resolve_clips()

    # ---------- sources ----------
    def _abs(self, p: str) -> str:
        q = Path(p).expanduser()
        return str(q if q.is_absolute() else (self.dir / q).resolve())

    def _load_sources(self):
        srcs = self.edl.get("sources") or die("edl.json needs a 'sources' map")
        for alias, s in srcs.items():
            s = {"path": s} if isinstance(s, str) else dict(s)
            s["path"] = self._abs(s["path"])
            if not Path(s["path"]).exists():
                die(f"source '{alias}' not found: {s['path']}")
            s["info"] = ffprobe(s["path"])
            s["is_image"] = is_image(s["path"], s["info"])
            s["synced"] = "offset" in s and alias != self.ref_alias(srcs)
            s["offset"] = float(s.get("offset", 0.0))
            tp = s.get("transcript") or self.dir / f"transcript.{alias}.json"
            s["transcript"] = load_json(self._abs(str(tp))) if Path(self._abs(str(tp))).exists() else None
            self.sources[alias] = s

    @staticmethod
    def ref_alias(srcs) -> str:
        return "main" if "main" in srcs else next(iter(srcs))

    @property
    def ref(self) -> str:
        return self.ref_alias(self.sources)

    def words_for(self, alias: str) -> list[dict] | None:
        s = self.sources[alias]
        t = self.sources[self.ref]["transcript"] if s["synced"] else s["transcript"]
        return t["words"] if t else None

    # ---------- output ----------
    def _output(self) -> dict:
        o = dict(self.edl.get("output", {}))
        first = self.sources[self.ref]["info"]
        if "width" not in o or "height" not in o:
            if o.get("aspect") in ASPECTS:
                w, h = ASPECTS[o["aspect"]]
                # don't upscale small sources too much
                short = min(first.get("width", 1920), first.get("height", 1080))
                if short < 1080:
                    w, h = even(w * short / 1080), even(h * short / 1080)
            else:
                w, h = first.get("width", 1920), first.get("height", 1080)
            o["width"], o["height"] = even(w), even(h)
        ref_img = self.sources[self.ref].get("is_image")
        o.setdefault("fps", 30 if ref_img else min(60, round(first.get("fps", 30)) or 30))
        o["path"] = self._abs(o.get("path", "final.mp4"))
        o.setdefault("crf", 20)
        o.setdefault("preset", "medium")
        return o

    # ---------- clips ----------
    def _word_range(self, alias: str, a: int, b: int, pad) -> tuple[float, float]:
        W = self.words_for(alias)
        if not W:
            die(f"clip uses 'words' but no transcript for '{alias}' (run analyze.py/transcribe.py)")
        if not (0 <= a <= b < len(W)):
            die(f"word range [{a},{b}] out of bounds for '{alias}' (0..{len(W) - 1})")
        p0, p1 = pad
        start = W[a]["start"] - p0
        if a > 0:
            start = max(start, (W[a - 1]["end"] + W[a]["start"]) / 2)
        end = W[b]["end"] + p1
        if b + 1 < len(W):
            end = min(end, (W[b]["end"] + W[b + 1]["start"]) / 2)
        return max(0.0, start), end

    def _resolve_clips(self):
        raw = list(self.edl.get("clips") or [])
        if self.edl.get("intro"):
            raw.insert(0, {**self._as_clip(self.edl["intro"]), "_bumper": True})
        if self.edl.get("outro"):
            raw.append({**self._as_clip(self.edl["outro"]), "_bumper": True})
        if not raw:
            die("edl.json has no clips")
        t = 0.0
        fps = self.out["fps"]
        for i, c in enumerate(raw):
            c = dict(c)
            alias = c.get("src", self.ref)
            if alias not in self.sources:
                die(f"clip {i}: unknown src '{alias}'")
            s = self.sources[alias]
            pad = tuple(c.get("pad", self.edl.get("pad", DEFAULT_PAD)))
            if "words" in c:
                tin, tout = self._word_range(alias, int(c["words"][0]), int(c["words"][1]), pad)
            else:
                tin = parse_time(c.get("in", 0))
                if "out" in c:
                    tout = parse_time(c["out"])
                elif "duration" in c or s.get("is_image"):
                    tout = tin + float(c.get("duration", 3.0))
                else:
                    tout = s["info"]["duration"] - (s["offset"] if s["synced"] else 0)
            if tout <= tin:
                die(f"clip {i}: out ({tout}) <= in ({tin})")
            speed = float(c.get("speed", 1.0))
            frames = max(1, round((tout - tin) / speed * fps))
            dur = frames / fps
            # video source time
            vin = tin + (s["offset"] if s["synced"] else 0.0)
            # audio source
            a_alias = c.get("audio_src") or (self.ref if s["synced"] and s.get("audio", "ref") == "ref" else alias)
            if a_alias not in self.sources:
                die(f"clip {i}: unknown audio_src '{a_alias}'")
            asrc = self.sources[a_alias]
            if a_alias == alias:
                ain = vin
            elif asrc["synced"]:
                ain = tin + asrc["offset"]
            else:
                ain = tin  # ref time
            c.update({"index": i, "src": alias, "tin": tin, "tout": tin + dur * speed, "vin": vin, "ain": ain,
                      "audio_alias": a_alias, "speed": speed, "dur": dur, "t0": t, "t1": t + dur})
            self.clips.append(c)
            t += dur
        self.duration = t

    def _as_clip(self, v) -> dict:
        v = {"path": v} if isinstance(v, str) else dict(v)
        alias = f"_bumper{len(self.sources)}"
        info = ffprobe(self._abs(v["path"]))
        self.sources[alias] = {"path": self._abs(v["path"]), "info": info, "synced": False, "offset": 0.0,
                               "transcript": None, "is_image": is_image(v["path"], info)}
        return {"src": alias, "in": v.get("in", 0), **({"out": v["out"]} if "out" in v else {}),
                **({"duration": v["duration"]} if "duration" in v else {}),
                "fade_in": v.get("fade_in", 0), "fade_out": v.get("fade_out", 0)}

    # ---------- mapping ----------
    def map_time(self, alias: str, t: float) -> float | None:
        """Source time (in that source's word time base) -> output time. None if cut."""
        for c in self.clips:
            if c["src"] == alias or (self.sources[c["src"]]["synced"] and alias == self.ref):
                if c["tin"] - 1e-3 <= t <= c["tout"] + 1e-3:
                    return c["t0"] + (t - c["tin"]) / c["speed"]
        return None

    def anchor(self, spec) -> float | None:
        """overlay 'at' may be a number (output time) or {"word": [alias, idx]} / {"src": alias, "t": sec}."""
        if isinstance(spec, (int, float, str)):
            return parse_time(spec)
        if "clip" in spec:  # {"clip": index, "t": seconds into that clip}
            c = self.clips[int(spec["clip"])]
            return c["t0"] + float(spec.get("t", 0))
        if "word" in spec:
            alias, idx = spec["word"]
            W = self.words_for(alias)
            return self.map_time(alias, W[int(idx)]["start"]) if W else None
        return self.map_time(spec["src"], parse_time(spec["t"]))

    def output_words(self) -> list[dict]:
        """All transcript words that survive the edit, in output time, tagged with clip index."""
        out = []
        for c in self.clips:
            if c.get("_bumper") or c.get("mute") or c.get("captions") is False:
                continue
            W = self.words_for(c["src"])
            if not W and c["audio_alias"] != c["src"]:  # e.g. photo + voice-over line: words live on the audio
                t = self.sources[c["audio_alias"]]["transcript"]
                if t:
                    shift = c["ain"] - c["tin"]
                    W = [{**w, "start": w["start"] - shift, "end": w["end"] - shift} for w in t["words"]]
            if not W:
                continue
            for w in W:
                mid = (w["start"] + w["end"]) / 2
                if c["tin"] <= mid <= c["tout"]:
                    s = c["t0"] + (max(w["start"], c["tin"]) - c["tin"]) / c["speed"]
                    e = c["t0"] + (min(w["end"], c["tout"]) - c["tin"]) / c["speed"]
                    out.append({**w, "ostart": s, "oend": max(e, s + 0.05), "clip": c["index"]})
        return out
