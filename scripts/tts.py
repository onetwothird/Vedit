#!/usr/bin/env python3
"""Text-to-speech with word timings (for captions). Used by story.py; also works standalone.

  python tts.py "Hello there!" --voice en-US-AndrewNeural --out hello.wav
  python tts.py --list-voices [--lang en]          # edge-tts voice catalogue
  python tts.py --engines                          # which engines are usable on this machine

Engines (voice cfg "engine"; "auto" picks the first usable in this order):
  elevenlabs  most expressive/realistic. env ELEVENLABS_API_KEY. voice = voice_id. Exact word timings.
  openai      very natural, takes acting directions ("style"). env OPENAI_API_KEY. voice = alloy|ash|ballad|coral|
              echo|fable|onyx|nova|sage|shimmer|verse. Timings estimated.
  edge        FREE, no key, 400+ neural voices in 100+ languages (pip install edge-tts). Exact word timings.
              voice e.g. en-US-AndrewNeural, en-US-AvaNeural, en-GB-RyanNeural, fil-PH-AngeloNeural, fil-PH-BlessicaNeural
              rate "+10%", pitch "-5Hz", volume "+0%".
  piper       offline local neural TTS. voice = path to .onnx model. Timings estimated.
  system      always available fallback (macOS `say`, espeak-ng, or ffmpeg flite). Robotic.
"auto" order: edge > openai > elevenlabs > system  (paid engines only when explicitly chosen or edge missing).
Results are cached by (engine, settings, text) so re-renders never re-bill.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import die, ffprobe, run  # noqa: E402


def _has(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


def engines() -> dict[str, bool]:
    return {"edge": _has("edge_tts"), "openai": bool(os.environ.get("OPENAI_API_KEY")),
            "elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY")),
            "piper": bool(shutil.which("piper")), "system": True}


def pick_engine(cfg: dict) -> str:
    e = cfg.get("engine", "auto")
    if e != "auto":
        return e
    av = engines()
    for name in ("edge", "openai", "elevenlabs"):
        if av[name]:
            return name
    print("[tts] no neural engine found -> robotic 'system' voice. For free natural voices: pip install edge-tts",
          file=sys.stderr)
    return "system"


def to_wav(src: str, dst: str):
    run(["ffmpeg", "-y", "-v", "error", "-i", src, "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", dst])


def estimate_words(text: str, wav: str) -> list[dict]:
    """Spread words over the voiced part of the clip, weighted by length (+ pause after punctuation)."""
    dur = ffprobe(wav)["duration"]
    res = run(["ffmpeg", "-hide_banner", "-nostats", "-i", wav, "-af", "silencedetect=noise=-40dB:d=0.15",
               "-f", "null", "-"], capture=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", res.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", res.stderr)]
    a = ends[0] if starts and starts[0] < 0.05 and ends else 0.0
    b = starts[-1] if starts and starts[-1] > dur - 0.6 and starts[-1] > a else dur
    toks = text.split()
    weights = [len(t) + 2 + (4 if t[-1:] in ",;:" else 0) + (6 if t[-1:] in ".?!" else 0) for t in toks]
    tot, t, out = sum(weights) or 1, a, []
    for tok, w in zip(toks, weights):
        d = (b - a) * w / tot
        out.append({"start": round(t, 3), "end": round(t + d * 0.85, 3), "text": tok})
        t += d
    return out


# ---------------- engines ----------------
def _edge(text, cfg, out_wav):
    import asyncio

    import edge_tts  # type: ignore

    async def go(mp3):
        kw = dict(voice=cfg.get("voice", "en-US-AndrewNeural"), rate=cfg.get("rate", "+0%"),
                  pitch=cfg.get("pitch", "+0Hz"), volume=cfg.get("volume", "+0%"))
        try:
            com = edge_tts.Communicate(text, boundary="WordBoundary", **kw)
        except TypeError:  # edge-tts < 7
            com = edge_tts.Communicate(text, **kw)
        words = []
        with open(mp3, "wb") as f:
            async for ch in com.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                elif ch["type"] == "WordBoundary":
                    s = ch["offset"] / 1e7
                    words.append({"start": round(s, 3), "end": round(s + ch["duration"] / 1e7, 3), "text": ch["text"]})
        return words

    with tempfile.TemporaryDirectory() as td:
        mp3 = str(Path(td) / "a.mp3")
        words = asyncio.run(go(mp3))
        to_wav(mp3, out_wav)
    return _attach_punct(text, words) if words else estimate_words(text, out_wav)


def _attach_punct(text: str, words: list[dict]) -> list[dict]:
    """Engines report bare words; copy punctuation back from the script so captions read naturally."""
    toks = text.split()
    j = 0
    for w in words:
        core = re.sub(r"[^\w']", "", w["text"].lower())
        for k in range(j, min(j + 4, len(toks))):
            if re.sub(r"[^\w']", "", toks[k].lower()) == core:
                w["text"], j = toks[k], k + 1
                break
    return words


def _http(url, headers, body) -> bytes:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={**headers,
                                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        die(f"TTS API error {e.code}: {e.read().decode(errors='ignore')[:400]}")


def _openai(text, cfg, out_wav):
    key = os.environ.get("OPENAI_API_KEY") or die("OPENAI_API_KEY not set")
    body = {"model": cfg.get("model", "gpt-4o-mini-tts"), "voice": cfg.get("voice", "alloy"), "input": text,
            "response_format": "wav"}
    if cfg.get("style"):
        body["instructions"] = cfg["style"]
    if cfg.get("speed"):
        body["speed"] = float(cfg["speed"])
    data = _http("https://api.openai.com/v1/audio/speech", {"Authorization": f"Bearer {key}"}, body)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(data)
    to_wav(f.name, out_wav)
    os.unlink(f.name)
    return estimate_words(text, out_wav)


def _elevenlabs(text, cfg, out_wav):
    key = os.environ.get("ELEVENLABS_API_KEY") or die("ELEVENLABS_API_KEY not set")
    vid = cfg.get("voice", "JBFqnCBsd6RMkjVDRZzb")
    body = {"text": text, "model_id": cfg.get("model", "eleven_multilingual_v2"),
            "voice_settings": {"stability": cfg.get("stability", 0.45), "similarity_boost": 0.8,
                               "style": cfg.get("style_exaggeration", 0.3)}}
    data = json.loads(_http(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/with-timestamps",
                            {"xi-api-key": key}, body))
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(base64.b64decode(data["audio_base64"]))
    to_wav(f.name, out_wav)
    os.unlink(f.name)
    al = data.get("alignment") or {}
    chars, st, en = al.get("characters", []), al.get("character_start_times_seconds", []), \
        al.get("character_end_times_seconds", [])
    words, cur = [], None
    for ch, s, e in zip(chars, st, en):
        if ch.isspace():
            if cur:
                words.append(cur)
                cur = None
            continue
        cur = {"start": s, "end": e, "text": ch} if cur is None else {**cur, "end": e, "text": cur["text"] + ch}
    if cur:
        words.append(cur)
    return words or estimate_words(text, out_wav)


def _piper(text, cfg, out_wav):
    model = cfg.get("voice") or die("piper needs voice = path to .onnx model")
    with tempfile.TemporaryDirectory() as td:
        raw = str(Path(td) / "p.wav")
        p = subprocess.run(["piper", "--model", model, "--output_file", raw], input=text, text=True,
                           capture_output=True)
        if p.returncode:
            die("piper failed: " + p.stderr[-300:])
        to_wav(raw, out_wav)
    return estimate_words(text, out_wav)


def _system(text, cfg, out_wav):
    with tempfile.TemporaryDirectory() as td:
        if platform.system() == "Darwin" and shutil.which("say"):
            raw = str(Path(td) / "s.aiff")
            cmd = ["say", "-o", raw] + (["-v", cfg["voice"]] if cfg.get("voice") else []) + [text]
            subprocess.run(cmd, check=True)
        elif shutil.which("espeak-ng") or shutil.which("espeak"):
            raw = str(Path(td) / "s.wav")
            exe = shutil.which("espeak-ng") or shutil.which("espeak")
            subprocess.run([exe, "-w", raw] + (["-v", cfg["voice"]] if cfg.get("voice") else []) + [text], check=True)
        else:
            raw = str(Path(td) / "s.wav")
            v = cfg.get("voice") if cfg.get("voice") in ("kal", "kal16", "awb", "rms", "slt") else "slt"
            t = text.replace("'", "").replace(":", " ").replace("\\", " ")
            run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"flite=text='{t}':voice={v}", raw])
        to_wav(raw, out_wav)
    return estimate_words(text, out_wav)


_FAILED: set[str] = set()
ENGINES = {"edge": _edge, "openai": _openai, "elevenlabs": _elevenlabs, "piper": _piper, "system": _system}


def synth(text: str, cfg: dict, cache_dir: Path) -> tuple[Path, list[dict], float, str]:
    """Returns (wav_path, words, duration, engine). Cached."""
    engine = pick_engine(cfg)
    if engine in _FAILED:
        engine = "system"
    key = hashlib.sha1(json.dumps([engine, {k: v for k, v in sorted(cfg.items()) if k not in ("color", "engine")},
                                   text]).encode()).hexdigest()[:16]
    cache_dir.mkdir(parents=True, exist_ok=True)
    wav, meta = cache_dir / f"{key}.wav", cache_dir / f"{key}.json"
    if wav.exists() and meta.exists():
        words = json.loads(meta.read_text())
    else:
        print(f"[tts] {engine}:{cfg.get('voice', '')}  \"{text[:60]}\"", file=sys.stderr)
        try:
            words = ENGINES[engine](text, cfg, str(wav))
        except Exception as e:  # network down, bad voice name... fall back so the build still finishes
            if engine == "system":
                raise
            print(f"[tts] {engine} failed ({e}); falling back to system voice for the rest of this run",
                  file=sys.stderr)
            _FAILED.add(engine)
            engine = "system"
            words = _system(text, cfg | {"voice": None}, str(wav))
        meta.write_text(json.dumps(words))
    return wav, words, ffprobe(wav)["duration"], engine


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="?")
    ap.add_argument("--engine", default="auto")
    ap.add_argument("--voice")
    ap.add_argument("--rate")
    ap.add_argument("--pitch")
    ap.add_argument("--style", help="acting direction (openai), e.g. 'whispering, nervous'")
    ap.add_argument("--out", default="tts.wav")
    ap.add_argument("--list-voices", action="store_true")
    ap.add_argument("--lang", default="")
    ap.add_argument("--engines", action="store_true")
    a = ap.parse_args()
    if a.engines:
        for k, v in engines().items():
            print(f"{k:11s} {'yes' if v else 'no'}")
        return
    if a.list_voices:
        if not _has("edge_tts"):
            die("pip install edge-tts")
        import asyncio

        import edge_tts  # type: ignore
        for v in asyncio.run(edge_tts.list_voices()):
            if v["Locale"].lower().startswith(a.lang.lower()):
                print(f"{v['ShortName']:38s} {v['Gender']:7s} {', '.join(v.get('VoiceTag', {}).get('VoicePersonalities', []))}")
        return
    if not a.text:
        ap.error("text required")
    cfg = {k: v for k, v in {"engine": a.engine, "voice": a.voice, "rate": a.rate, "pitch": a.pitch,
                             "style": a.style}.items() if v}
    wav, words, dur, eng = synth(a.text, cfg, Path(tempfile.gettempdir()) / "vedit_tts_cache")
    shutil.copy(wav, a.out)
    print(f"{a.out}  {dur:.2f}s  engine={eng}  words={len(words)}")


if __name__ == "__main__":
    main()
