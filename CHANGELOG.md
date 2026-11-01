# Changelog

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
