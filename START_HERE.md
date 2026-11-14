# START HERE: make a finished video in 3 steps

You record a raw video. vedit cuts the silences and "umm"s, adds word-by-word captions, fixes the sound
and gives you a video ready for Reels, TikTok, Shorts or YouTube.

**You don't need to know how to code.** This guide is for Windows. On Mac, Linux or in VS Code,
see [Mac, Linux or VS Code](#mac-linux-or-vs-code) below.

---

## Step 1: Download vedit (one time only)

1. On this GitHub page, click the green **Code** button, then **Download ZIP**.
2. Open your **Downloads** folder, right-click the ZIP and choose **Extract All...**, then **Extract**.
3. Move the extracted folder somewhere easy, like your **Documents**.

## Step 2: Install (one time only)

1. Open the vedit folder.
2. Double-click **`1_INSTALL.bat`**.
   - If Windows says *"Windows protected your PC"*, click **More info**, then **Run anyway**.
   - If a window asks *"Do you want to allow this app to make changes?"*, click **Yes**.
3. Wait. The first time, it may say **"Close this window, then double-click 1_INSTALL.bat ONE MORE TIME"**.
   Do that.
4. When you see **DONE!**, it's ready. You only do this once.

## Step 3: Make your video (every time)

1. Record your video normally (phone or camera). Copy it to your computer.
2. **Drag** the video file and **drop** it on **`2_MAKE_VIDEO.bat`**.
   - Or **double-click** `2_MAKE_VIDEO.bat`, then drag the video **into the black window** and press **Enter**.
     Use this if dropping on the file does nothing.
3. A window asks two questions. Type a number and press **Enter**:

   | Question | Press |
   |---|---|
   | **Where will you post it?** | **1** Reels / TikTok / Shorts (tall) · **2** YouTube (wide) · **3** Podcast · **4** Captions only, no cutting |
   | **What language do you speak?** | **1** English · **2** Filipino / Tagalog · **3** Taglish (mixed English + Filipino) |

4. Wait a few minutes. Don't close the window.
5. When it says **DONE!**, a folder opens with your new video selected. It is saved **next to your original**
   with a new name, for example `myvideo_reels.mp4`. Your original video is never changed.

> **Always watch the new video before posting.** Captions are made by AI and are usually right,
> but names, places and slang can be misspelled. If a word is wrong, see "Fix a wrong caption" below.

---

## Tips for the best result

- **Talk clearly, close to the phone.** Less background noise means better captions.
- **Pause** a moment between sentences. vedit cuts the pauses for you, so you don't need to be perfect.
- Made a mistake? **Just say the sentence again.** vedit tries to keep the last take.
- For **Filipino** or **Taglish** (options 2 and 3), vedit uses its most accurate language model. It's slower
  (about 3 minutes for a 2-minute video on a normal PC), and the first time it downloads a big language file
  (about 3 GB), so use good internet.
- Several videos? Select them all and drop them on `2_MAKE_VIDEO.bat` together.

## Fix a wrong caption

Next to your video there is a new folder ending in **`.edit`** (for example `myvideo.edit`). If you use
Claude Code, open the vedit folder in it and type something like:

> *"In myvideo, the caption says 'Hose Rizal'. Change it to 'José Rizal' and render again."*

Without Claude Code, just record that part again, or edit the caption in your phone's editing app.

---

## Problems?

| What you see | What to do |
|---|---|
| *"Windows protected your PC"* | Click **More info**, then **Run anyway**. |
| *"vedit is not installed yet"* | Double-click `1_INSTALL.bat` first. |
| *"winget is not recognized"* | Open the **Microsoft Store**, search **App Installer** and install or update it. Then run `1_INSTALL.bat` again. |
| The install closes quickly or says *"python is not recognized"* | Restart your computer and double-click `1_INSTALL.bat` again. |
| *"Something went wrong while installing"* | Check your internet and run `1_INSTALL.bat` again. |
| It is very slow | That's normal on older computers: a 1-minute video can take a few minutes. Filipino and Taglish (options 2 and 3) are the slowest. |
| Captions have many wrong words | Choose the right language. For mixed speech use **3 Taglish**. Record in a quieter place. |
| Dropping the video on `2_MAKE_VIDEO.bat` does nothing, or the window closes right away | Double-click `2_MAKE_VIDEO.bat` instead, then drag the video **into the black window** and press Enter. Make sure you use the `2_MAKE_VIDEO.bat` inside your vedit folder, not an old copy. |
| Window shows an error | Take a screenshot of the window and ask for help (open an Issue on GitHub). |
| Captions show words you never said | Update vedit. Older versions sometimes invented captions during silence or music. |

---

## Mac, Linux or VS Code

The same easy launcher, **`easy.py`**, works everywhere.

**Install (one time only)**

- **Mac:** install [Homebrew](https://brew.sh), then open **Terminal** and run:
  `brew install ffmpeg python` and then `python3 -m pip install -r requirements.txt` (from inside the vedit folder).
- **Linux:** `sudo apt install ffmpeg python3-pip`, then `python3 -m pip install -r requirements.txt`.

**Make a video (every time)**

- **Terminal:** go into the vedit folder and run `python3 easy.py`. Drag your video into the window, press
  Enter, and answer the two questions.
- **VS Code:** open the vedit folder (**File > Open Folder**), click `easy.py`, then press the **Run** (▷)
  button at the top right. Answer the questions in the panel that opens at the bottom.

If something is missing, `easy.py` tells you exactly what to install.

---

### Want more?

- More options (thumbnails, brand colors and logo, square videos): [docs/USAGE.md](docs/USAGE.md)
- Make "Did you know?" fact Reels from photos: [templates/fact_reels/README.md](templates/fact_reels/README.md)
