# Licensing for `juan_fact_a_day/`

This folder mixes open-source code with the brand of the **Juan Fact a Day** page.

## Open source (MIT License, same as the rest of the repository)
- `tools/` (`make_cards.py`, `make_thumbnail.py`, `make_sfx.py`)
- `BUILD.bat`, `channel.json`, `_new_story_template/`
- the `story.json` structure and the README/POST_KIT instructions

You are welcome to copy these to build your own fact-video series.

## Not open source: all rights reserved
© 2026 Angelito P. Decatoria III. These are **not** covered by the MIT License and may not be reused, redistributed
or used to imitate the Juan Fact a Day page without permission:
- the name **"Juan Fact a Day"** and its logo
- the **Juan** mascot (`brand/`, `assets/art/mascot_peek.png`)
- the card artwork and illustrations (`brand/card_background.png`, `assets/art/`, `stories/*/cards/`, `stories/*/thumbnail.jpg`)
- the scripts and captions of the published videos (`stories/*/story.json` narration text, `stories/*/caption.txt`)

They are included so the example builds out of the box. If you fork this, please replace them with your own
name, mascot and artwork.

## Third-party
- Font: Poppins, SIL Open Font License 1.1 (`../fonts/OFL.txt`)
- Voice: Microsoft Edge neural text-to-speech via [edge-tts](https://github.com/rany2/edge-tts), generated at build time; no audio is stored in the repository
- Sound effects in `assets/sfx/` are generated from scratch by `tools/make_sfx.py`
