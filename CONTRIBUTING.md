# Contributing to vedit

Thanks for helping creators edit less and publish more.

## Dev setup
```bash
git clone https://github.com/onetwothird/vedit && cd vedit
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[all,dev]"
vedit doctor
pytest -q
```
Tests use a synthetic video and a fake transcript, so they don't need Whisper models or network access.

## Architecture (5-minute tour)
```
analyze.py / transcribe.py   footage -> facts (word timings, silences, scenes, contact sheets)
autocut.py / presets.py      facts -> draft edl.json (the edit, as data) + brand kit
edl.py                       edl.json -> resolved timeline (words -> times, multicam offsets, mapping)
render.py                    timeline -> per-clip cached segments -> final pass (overlays, captions, audio)
captions.py                  timeline words -> ASS/SRT
story.py / tts.py            script + photos -> voices + edl.json
auto.py / vedit.py           one-command pipeline / CLI entry point
SKILL.md                     how Claude Code drives all of the above
```
Key idea: **every edit is JSON**. Features should be new edl.json fields that render.py understands. The AI (or a human) only writes data, and the renderer stays deterministic.

## Guidelines
- Standard library + ffmpeg first. Heavy dependencies must be optional, with a graceful fallback.
- Every new edl field goes into `references/edl.md`, and every new command into `SKILL.md` + README.
- Paths inside ffmpeg filters go through `common.filter_path()` (Windows drive letters).
- Add a test in `tests/` for new behavior.

## Good first issues
- More caption styles (karaoke fill, emoji emphasis)
- Crossfade transitions between clips
- Auto b-roll suggestions from the transcript
- Speaker diarization for single-mic podcasts
