# Changelog

## Unreleased
- **Fact Reels starter kit** (`templates/fact_reels/`, `vedit reels new|story|build|demo`): make fact Reels in the
  Juan Fact a Day format with your own brand and raw footage; step-by-step guide in the kit's README
- Story mode: raw **video scenes with cards**: the card is composited over the footage (dimmed while visible,
  `card_hold` to hide it); clips shorter than the narration loop
- Story mode: **talking clips** (`"video"` + `"keep_audio": true`, no `lines`): real voice kept, auto-transcribed
  for word-by-word subtitles (`defaults.transcribe`); stories without any AI narration work
- New shared modules `scripts/brand.py`, `scripts/cards.py`, `scripts/sfx.py` (moved from `juan_fact_a_day/tools`,
  driven by `brand.json`); Juan Fact a Day output is pixel-identical
- Transcription: pass samples to faster-whisper directly (fixes `metadata_errors` crash with some PyAV versions)
- Story mode: **continuous narration** (`defaults.narration: "continuous"`): whole script voiced in one take,
  long TTS pauses tightened (`max_pause`), cut only between scenes on frame boundaries
- Story mode: `say` pronunciation spelling per line; subtitles keep the real text, aligned word-for-word to the voice
- Story mode: motion-graphics card animation (`animate`, new `scripts/animate_card.py`), `motion_strength`,
  shared series settings via `extends`, TTS silence trimming in per-line mode
- Captions: numeric `position`, `outline_color`, `outline`, `shadow`, `active_scale`; speaker color no longer hides the highlight
- Bundled Poppins font (SIL OFL) in `fonts/`
- Example series `juan_fact_a_day/`: Filipino fact Reels with card/cover generators and one folder per video
- Tests: read script output as UTF-8 (fixes failures on Windows)

## 0.2.0
- `vedit auto`: one command from raw video to finished video, with platform presets
  (youtube, shorts, tiktok, reels, square, podcast, tutorial, clean) and batch processing
- Brand kit (`brand.json`): fonts, caption colors, logo watermark, intro/outro, music, LUT, spelling fixes
- Multi-format export from one edit: `vedit render edl.json --formats 9:16,1:1`
- `vedit thumbs`: thumbnail candidates + ready-made thumbnail with title text
- YouTube chapters file from `chapters` in edl.json
- `pip install` support with the `vedit` command; test suite + GitHub Actions CI
- Windows fixes: UTF-8 console output, ffmpeg filter path escaping

## 0.1.0
- Analyze (word-level transcript, silences, scenes, contact sheets), autocut, renderer with caching
- Captions (pop/box/clean), punch-ins, reframing, b-roll/image/text/sfx overlays, music ducking, loudness
- Multicam sync + speaker detection
- Story mode: photos + script -> AI-voiced skit (edge-tts, OpenAI, ElevenLabs, Piper, system)
