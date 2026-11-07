---
name: vedit
description: Edit raw video or make videos from photos end to end - cut silences/ums/flubbed takes, captions, shorts/reels from long videos, multicam podcasts, b-roll, music, voice polish, and AI-voiced skits/stories from photos + a script. Use whenever the user wants a video edited, cut, captioned, reframed to vertical, turned into shorts, or wants a narrated/voiced video, skit, story or slideshow made from images.
---

# vedit: AI Video Editor

You are the editor. You can't watch video directly, so these tools give you **eyes** (transcripts with word numbers, contact sheets you Read as images) and **hands** (an edit decision list, `edl.json`, rendered by ffmpeg). Your edits are decisions in JSON. They are cheap to change and re-render, and unchanged clips stay cached.

All commands go through one CLI. If `vedit` is on PATH (the user ran `pip install -e .`), use it. Otherwise call `python SKILL/scripts/vedit.py <command>`, where `SKILL` is the folder that contains this file. Below it's written as `vedit <command>`.

Run `vedit doctor` once per machine. If something required is missing, install it: `pip install -e "SKILL[all]"` (or `pip install -r SKILL/requirements.txt`), and ffmpeg from the OS package manager (Windows: `winget install Gyan.FFmpeg`).

**Fast path:** when the user just wants it done ("clean this up", "make it a short"), run `vedit auto VIDEO --preset <youtube|shorts|tiktok|reels|square|podcast|tutorial|clean>`. Then do steps 3–5 of workflow A on the `edl.<preset>.json` it wrote, so the result is *edited*, not only trimmed. Use `--formats 9:16` for extra aspect ratios and `--thumbs` for thumbnails. Several files at once means batch: `vedit auto *.mp4 --preset shorts`.

**Brand kit:** if a `brand.json` exists next to the video, in the working directory or in `~/.vedit/`, autocut/auto apply it automatically (font, colors, logo, intro/outro, music, LUT, spelling fixes). If the user mentions their branding, offer to create one from `examples/brand.example.json`.

## Pick the workflow

| User has... | Workflow |
|---|---|
| Raw talking / tutorial / vlog footage | **A. Edit footage** |
| A long video and wants Shorts/Reels/TikToks | **A**, then **B. Shorts** |
| Podcast / interview with 2+ cameras or mics | **C. Multicam**, then A |
| Photos (or clips) + an idea for a skit/story/explainer and wants voices | **D. Story mode** |
| A fact-Reel channel (folder with `brand.json` + `stories/`), or wants Reels like Juan Fact a Day from their own photos/clips | **E. Fact Reels kit** |

Ask at most one or two questions if critical info is missing (target platform/aspect, tone). Otherwise make good choices and tell the user what you chose.

---

## A. Edit footage

1. **Analyze**: `vedit analyze raw.mp4` (several files: `vedit analyze a.mp4 b.mp4 --names main broll1`). This creates `raw.edit/` with:
   - `transcript.main.txt`: **read this fully**. Lines look like `[0:12.34 → 0:15.10] #120-135 text`. `{um}` marks a hard filler, `(like)` a soft filler, `⏸ 2.1s pause` a pause, and `⚠ possible flubbed take` a sentence that gets repeated soon after.
   - `frames/overview_main_*.jpg`: **Read these images** to learn what is on screen (framing, b-roll moments, screen recording vs face).
2. **Draft**: `vedit autocut raw.edit --punchin 1.12` (talking head) or add `--aspect 9:16`. This writes `raw.edit/edl.json` with dead air, hard fillers and flubbed takes removed.
3. **Refine like a real editor**. Edit `edl.json` using the transcript:
   - Cut rambling, repeated points, off-topic tangents and false starts the heuristics missed. Keep the best take. Remove soft fillers only where they are filler.
   - Make the **hook** strong: if the best line comes late, consider moving a short teaser clip to the start.
   - Use `"words": [first, last]` ranges (precise, auto-padded). Use `"in"/"out"` seconds only for non-speech parts.
   - Add polish: punch-ins on jump cuts, `overlays` (titles, b-roll, images, sfx), `audio.music`, `color`. See `references/edl.md` for every field.
4. **Preview**: `vedit render raw.edit/edl.json --preview`, then `vedit frames <preview.mp4> --every 3` and **Read the sheets** to check framing, caption placement and overlays. Fix and re-render (cached clips are reused).
5. **Final**: `vedit render raw.edit/edl.json` (add `--formats 9:16,1:1` if they post on several platforms).
6. **Publish kit** (creators love this). Write `raw.edit/publish.md` with:
   - 3 title options: curiosity plus a clear benefit, ≤ 60 characters
   - a description: 2 hook lines, a summary, links placeholder, and hashtags
   - **chapters**: add `"chapters": [{"at": {"word": ["main", N]}, "title": "..."}]` to edl.json before the final render, and the renderer writes `<output>.chapters.txt` in YouTube format
   - thumbnails: run `vedit thumbs raw.mp4`, Read `candidates_sheet`, pick the most expressive frame, then `vedit thumbs raw.mp4 --pick N --title "3-5 WORDS"`
7. Tell the user where the `.mp4`, `.srt`, chapters, thumbnail and publish.md are, the before/after length, and what you cut and why in a few bullets.

## B. Shorts from a long video

After analyzing, read the whole transcript and pick 3–5 **self-contained** moments of 20–60 s each. Look for a strong opening line, one clear idea, and a payoff or punchline. Avoid moments that need earlier context. For each one, write `raw.edit/short_N.edl.json` with `"output": {"aspect": "9:16", "path": "short_N.mp4"}`, captions `pop`, `"crop_x": "auto"` (or a value read from `vedit frames raw.mp4 --at T --grid`, where each line = 0.1), tight cuts (pauses ≤ 0.3 s) and a title overlay for the hook. Explain to the user why each moment works.

## C. Multicam podcast / interview

1. `vedit multicam sync cam1.mp4 cam2.mp4 cam3.mp4` gives an offset per camera. Put it in `sources`: `"cam2": {"path": "cam2.mp4", "offset": 1.234}`. If confidence is LOW, verify with frames at a clap.
2. If each person has their own mic: `vedit multicam speakers micA.wav micB.wav --names host guest --offsets 0 0.8` gives `speakers.txt`, a timeline of who is talking.
3. Analyze `main` (best audio). Clip times and words for synced sources are in **main's time base**, and audio comes from main automatically. Cut to the speaker's camera, use a wide shot for crosstalk or reactions, and avoid shots shorter than 1.5 s.

## D. Story / skit mode (photos + AI voices)

1. Write the **script** with the user's idea: short punchy lines (≤ 15 words each), a hook in the first 3 seconds, and a clear ending or punchline. Use one speaker per line. For skits, give each character a distinct voice and caption color.
2. Run `vedit story grid story.json` and **Read** each `grid_sceneN.jpg` to get `focus` points for each character's face. On the grid, x uses the yellow labels and y uses the cyan labels.
3. Write `story.json` (full schema in `references/story.md`). Use a close `shot` + `focus` when a character speaks and a wide or Ken Burns `motion` for narration. Add `sfx`, `title`, `transition: fade` between scenes, and `music`.
4. `vedit story voices story.json` synthesizes the lines and prints a timing table. Check the pacing (a short is usually ≤ 60 s).
5. `vedit story build story.json --preview`, then check frames, then run `vedit story build story.json`.

Voices: `edge` is free and natural, with no API key needed. Pick voices with `vedit tts --list-voices --lang en` (Filipino: `--lang fil`). Use `openai` (acting directions via `"style"`) or `elevenlabs` only when the user has a key and asks for premium voices. Never invent a real person's voice or impersonate real people.

---

## E. Fact Reels kit (raw photos/clips -> branded fact Reels)

Guide: `templates/fact_reels/README.md`. New channel: `vedit reels new DIR`; new video: `vedit reels story DIR NAME`.
1. Look at the user's raw files (`vedit frames clip.mp4` for clips) and pick scene types: `image` + `card` with
   narration, `video` + `lines` (B-roll, narrated, muted), or `video` + `keep_audio: true` with no `lines` (the user
   talking: real voice, auto-captions). Choose `in`/`out` from the frames/transcript.
2. Write `story.json`: hook → fact → "And did you know?" → fact → ... → comment/share/follow, ~185 words ≈ 60 s,
   one flowing narration. Use `say` for numbers, years and names the voice may misread. Card text: `*stars*` = accent.
3. `vedit reels build STORY_DIR --only cards` and **Read** the cards and `thumbnail.jpg`. Fix text that overflows.
4. `vedit reels build STORY_DIR --preview`, check frames, then build at full quality. Verify every fact; put sources in `caption.txt`.

---

## Editing judgment (what makes it good)

- **Pacing**: for YouTube talking heads, cut pauses down to ~0.3–0.45 s. For Shorts, cut tighter (~0.2–0.3 s). Leave breathing room after a punchline or emotional beat (`"pad": [0.1, 0.6]` on that clip).
- **Hide jump cuts** by alternating punch-ins (1.0 ↔ 1.10–1.15) or by cutting to b-roll. Don't punch in on every cut of a screen recording, where it hurts readability. Use `"fit": "blur"` or `"pad"` instead when going vertical.
- **Captions**: use `pop` for Shorts/Reels, `clean` for long-form and tutorials, and `box` for busy backgrounds. Fix brand names and misheard terms with `captions.replace`.
- **Audio** is half the video. Keep `normalize` on (−14 LUFS for social/YouTube). Set `denoise: true` only on noisy rooms. Keep music at 0.1–0.2 with ducking on.
- **Always verify visually** before calling it done, and say what you checked.
- Long renders: preview a slice first with `vedit render edl.json --preview --clips 0-10`.

## Troubleshooting

- Whisper missed the ums: it was prompted to keep them, but if they're still missing, use `--model medium` or cut by pauses.
- Wrong language: `vedit analyze raw.mp4 --language tl` (Tagalog), `es`, and so on.
- Captions in the wrong font: set `captions.font` to an installed font (Montserrat, Poppins, Arial), or drop .ttf files in `SKILL/fonts/`.
- A segment fails to render: the error names the clip index. Check its in/out against the source duration.
- The final ffmpeg command is saved at `<workdir>/.render_cache/last_final_cmd.txt`.
