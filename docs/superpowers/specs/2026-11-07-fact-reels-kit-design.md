# Fact Reels starter kit: design

Date: 2026-11-07 · Status: approved by the owner ("approve what you do, just make it helpful")

## Goal
Let anyone make 60-second fact Reels in the Juan Fact a Day format (animated cards, narration,
word-by-word subtitles, sound effects, music) from **their own raw material**: photos and/or raw video
clips with nothing on them. The creator only writes `story.json` and the tool does the editing.

`juan_fact_a_day/` stays intact (stories, brand, artwork) and must build exactly as before.

## Scene types
| Scene | Creator provides | Tool adds |
|---|---|---|
| `image` | a photo/card | AI narration, subtitles, animated card, Ken Burns, sfx |
| `video` (silent) | raw clip + script `lines` | AI narration, subtitles, card composited over the footage, sfx; clip is looped if shorter than the narration |
| `video` + `keep_audio: true`, no `lines` | raw talking clip | keeps the real voice, transcribes it (faster-whisper), word-by-word subtitles, card over the footage for the first `card_hold` seconds |

## Components
- `scripts/brand.py`: loads `brand.json` (found by walking up from the story folder). It holds the series name,
  colors, fonts, background, art dir, optional mascot, and sfx dir. If the background file is missing, it
  generates a clean branded background (gradient, name header, accent stripe).
- `scripts/cards.py`: brand-driven card + thumbnail renderer (the code from `juan_fact_a_day/tools`,
  with constants replaced by brand values). The defaults equal the old Juan constants.
- `scripts/sfx.py`: whoosh / ding / pop generator (moved from Juan tools).
- `scripts/animate_card.py`: can now take a background **video** (frames streamed from ffmpeg). The footage is
  darkened while the card is visible, and the card snaps out at `card_hold`.
- `scripts/story.py`: prepares video scenes (cut/loop/composite), transcribes talking clips, and handles
  stories with no AI narration at all.
- `scripts/reels.py` (`vedit reels new|build`): scaffolds a kit or story and builds one or all stories
  (sfx → cards → thumbnail → video). It is cross-platform.
- `templates/fact_reels/`: neutral kit with `brand.json`, `channel.json`, `_new_story_template/`, a sample
  story using all three scene types, `build.py`, `BUILD.bat`, and a README with the step-by-step guide.
- `juan_fact_a_day/`: gets a `brand.json` holding today's look. Its `tools/*.py` become thin wrappers, so
  `BUILD.bat` keeps working unchanged.

## Verification
- Before/after pixel comparison of every Juan card, plate and thumbnail (must be identical).
- Unit tests: brand loading/defaults and the generated background.
- Pipeline test: the template's sample story with an image scene, a silent clip with a card, and a talking
  clip (fake transcript, no Whisper download) renders to a valid 9:16 MP4.
