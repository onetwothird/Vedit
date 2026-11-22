#!/usr/bin/env python3
"""Word-level transcription -> transcript.json + transcript.txt (Claude-readable).

Usage:
  python transcribe.py VIDEO [--model small] [--language en] [--out DIR] [--name main]

Backends (first one installed wins): faster-whisper (recommended), openai-whisper.
  pip install faster-whisper
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (FILLER_PHRASES, FILLERS_HARD, FILLERS_SOFT, die, ffmpeg_bin, need, norm_word,  # noqa: E402
                    run, save_json, ts, workdir_for)

# Whisper drops disfluencies unless prompted with text that contains them.
FILLER_PROMPT = "Umm, let me think, like, hmm... Okay, uh, so here's what I'm, you know, thinking."


# Bump when prompts or decoding change, so cached transcripts get redone.
TRANSCRIBE_VERSION = 4

# Tagalog/Taglish: an English filler prompt makes Whisper guess English-sounding spellings.
# A short, correctly spelled Taglish sample steers spelling and sentence structure instead.
TAGLISH_PROMPT = ("Ito ay isang usapan sa Taglish. Okay, so ngayon, pag-usapan natin kung paano natin ito gagawin. "
                  "Hindi ba mas maganda kung araw-araw na itong nararanasan ng mga Pilipino, at hindi lang kapag may kondisyon?")


def _norm(text: str) -> str:
    return " ".join("".join(c for c in text.lower() if c.isalnum() or c.isspace()).split())


def echoes_prompt(words: list[tuple[str, float]], prompt: str | None) -> bool:
    """Whisper sometimes 'hears' its prompt during silence or music. Catch those fake segments.

    Fake: mostly prompt words AND low confidence (seen: avg ~0.1). Real speech that happens to use the
    same words is kept, because Whisper is confident about it (real Tagalog median ~0.9).
    """
    if not prompt or len(words) < 3:
        return False
    vocab = set(_norm(prompt).split())
    toks = [(_norm(t), p) for t, p in words if _norm(t)]
    if not toks:
        return False
    overlap = sum(1 for t, _ in toks if t in vocab) / len(toks)
    confidence = sum(p for _, p in toks) / len(toks)
    return overlap >= 0.75 and confidence < 0.35


def pick_prompt(language: str | None, keep_fillers: bool):
    if language in ("tl", "fil"):
        return TAGLISH_PROMPT
    return FILLER_PROMPT if keep_fillers else None


def extract_audio(video: str, wav: str):
    need("ffmpeg")
    run([ffmpeg_bin("ffmpeg"), "-y", "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav])


def _load_wav16k(path: str):
    """16 kHz mono 16-bit WAV (what extract_audio writes) -> float32 numpy array, else None."""
    import wave
    try:
        import numpy as np
        with wave.open(path, "rb") as w:
            if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
                return None
            return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
    except Exception:
        return None


def transcribe_words(wav: str, model_name: str, language: str | None, keep_fillers: bool):
    prompt = pick_prompt(language, keep_fillers)
    try:
        from faster_whisper import WhisperModel  # type: ignore
        device, compute = "cpu", "int8"
        try:
            import torch  # type: ignore
            if torch.cuda.is_available():
                device, compute = "cuda", "float16"
        except Exception:
            pass
        audio = _load_wav16k(wav)  # hand over samples: avoids PyAV decoding (breaks on some av versions)

        def run(device, compute):
            print(f"[transcribe] faster-whisper model={model_name} device={device}", file=sys.stderr)
            model = WhisperModel(model_name, device=device, compute_type=compute)
            segs, info = model.transcribe(audio if audio is not None else wav, language=language, word_timestamps=True,
                                          initial_prompt=prompt, vad_filter=False, condition_on_previous_text=False, beam_size=5)
            words = []
            for s in segs:  # lazy: GPU errors (missing CUDA/cuBLAS DLLs) show up here, not at load
                if echoes_prompt([(w.word, w.probability) for w in s.words or []], prompt):
                    print(f"\n[transcribe] dropped fake caption (repeats the prompt): {s.text.strip()}", file=sys.stderr)
                    continue
                for w in s.words or []:
                    words.append({"start": w.start, "end": w.end, "text": w.word.strip(), "p": round(w.probability, 3)})
                print(f"\r[transcribe] {ts(s.end)}", end="", file=sys.stderr)
            print(file=sys.stderr)
            return words, info.language

        if device == "cuda":
            try:
                return run(device, compute)
            except (RuntimeError, OSError) as e:  # e.g. "cublas64_12.dll is not found": fall back instead of crashing
                print(f"\n[transcribe] GPU not usable ({e}); using CPU instead", file=sys.stderr)
        return run("cpu", "int8")
    except ImportError:
        pass
    try:
        import whisper  # type: ignore
        print(f"[transcribe] openai-whisper model={model_name}", file=sys.stderr)
        model = whisper.load_model(model_name)
        res = model.transcribe(wav, language=language, word_timestamps=True, initial_prompt=prompt,
                               condition_on_previous_text=False)
        words = [{"start": w["start"], "end": w["end"], "text": w["word"].strip(), "p": round(w.get("probability", 1), 3)}
                 for s in res["segments"]
                 if not echoes_prompt([(w["word"], w.get("probability", 1)) for w in s.get("words", [])], prompt)
                 for w in s.get("words", [])]
        return words, res.get("language")
    except ImportError:
        die("No transcription backend. Install one:  pip install faster-whisper")


def mark_fillers(words: list[dict]):
    n = [norm_word(w["text"]) for w in words]
    for i, w in enumerate(words):
        w["filler"] = "hard" if n[i] in FILLERS_HARD else ("soft" if n[i] in FILLERS_SOFT else None)
    for a, b in FILLER_PHRASES:
        for i in range(len(words) - 1):
            if n[i] == a and n[i + 1] == b:
                words[i]["filler"] = words[i + 1]["filler"] = "soft"


def sentences(words: list[dict], gap: float = 0.6, max_words: int = 40) -> list[dict]:
    out, cur = [], []
    for i, w in enumerate(words):
        if cur and (w["start"] - words[i - 1]["end"] > gap or len(cur) >= max_words):
            out.append(cur)
            cur = []
        cur.append(i)
        if w["text"][-1:] in ".?!" and len(cur) >= 3:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{"w0": c[0], "w1": c[-1], "start": words[c[0]]["start"], "end": words[c[-1]]["end"],
             "text": " ".join(words[j]["text"] for j in c)} for c in out]


def flag_retakes(words: list[dict], sents: list[dict], k: int = 3, lookahead: int = 3):
    """A sentence whose opening k content-words reappear at the start of a later nearby sentence
    is probably a flubbed take. Usually keep the LAST take."""
    def head(s):
        toks = [norm_word(words[j]["text"]) for j in range(s["w0"], s["w1"] + 1) if not words[j]["filler"]]
        return tuple(t for t in toks if t)[:k]
    heads = [head(s) for s in sents]
    for i, h in enumerate(heads):
        if len(h) < k:
            continue
        for j in range(i + 1, min(len(sents), i + 1 + lookahead)):
            if heads[j] == h:
                sents[i]["retake_of_later"] = j
                break


def write_txt(path: Path, data: dict):
    words, sents = data["words"], data["sentences"]
    lines = [f"# Transcript: {Path(data['source']).name}  ({ts(data['duration'])}, lang={data['language']})",
             "# Format: [start → end] #firstWord-lastWord  text     {um}=hard filler  (like)=soft filler",
             "# Use word numbers in edl.json clips: {\"src\": \"%s\", \"words\": [first, last]}" % data["name"], ""]
    prev_end = 0.0
    for si, s in enumerate(sents):
        g = s["start"] - prev_end
        if g >= 0.6:
            lines.append(f"      ⏸ {g:.1f}s pause")
        toks = []
        for j in range(s["w0"], s["w1"] + 1):
            w = words[j]
            t = w["text"]
            if w["filler"] == "hard":
                t = "{" + t + "}"
            elif w["filler"] == "soft":
                t = "(" + t + ")"
            if w["p"] < 0.4:
                t += "?"
            toks.append(t)
        flag = f"   ⚠ possible flubbed take (redone at sentence starting #{sents[s['retake_of_later']]['w0']})" \
            if "retake_of_later" in s else ""
        lines.append(f"[{ts(s['start'])} → {ts(s['end'])}] #{s['w0']}-{s['w1']}  {' '.join(toks)}{flag}")
        prev_end = s["end"]
    if data["duration"] - prev_end >= 0.6:
        lines.append(f"      ⏸ {data['duration'] - prev_end:.1f}s pause (end)")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build(video: str, words: list[dict], language: str | None, name: str, duration: float) -> dict:
    words = [w for w in words if w["text"]]
    for i, w in enumerate(words):
        w["i"] = i
        w["start"], w["end"] = round(w["start"], 3), round(max(w["end"], w["start"] + 0.02), 3)
    mark_fillers(words)
    sents = sentences(words)
    flag_retakes(words, sents)
    return {"source": str(Path(video).resolve()), "name": name, "language": language, "duration": duration,
            "words": words, "sentences": sents}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--model", default="small", help="tiny|base|small|medium|large-v3|distil-large-v3 (default small)")
    ap.add_argument("--language", default=None, help="force language code, e.g. en, tl, es")
    ap.add_argument("--name", default="main", help="source alias used in edl.json")
    ap.add_argument("--out", default=None, help="work dir (default: <video>.edit/)")
    ap.add_argument("--no-fillers", action="store_true", help="don't bias Whisper toward transcribing um/uh")
    a = ap.parse_args()

    from common import ffprobe
    info = ffprobe(a.video)
    wd = workdir_for(a.video, a.out)
    with tempfile.TemporaryDirectory() as td:
        wav = str(Path(td) / "a.wav")
        extract_audio(a.video, wav)
        words, lang = transcribe_words(wav, a.model, a.language, not a.no_fillers)
    data = build(a.video, words, lang, a.name, info["duration"])
    # what produced this transcript, so auto.py can redo it when the model/language/prompt changes
    data["made_with"] = {"model": a.model, "language": a.language, "version": TRANSCRIBE_VERSION}
    save_json(data, wd / f"transcript.{a.name}.json")
    write_txt(wd / f"transcript.{a.name}.txt", data)
    hard = sum(1 for w in data["words"] if w["filler"] == "hard")
    print(f"[transcribe] {len(data['words'])} words, {len(data['sentences'])} sentences, {hard} hard fillers "
          f"-> {wd / f'transcript.{a.name}.txt'}")


if __name__ == "__main__":
    main()
