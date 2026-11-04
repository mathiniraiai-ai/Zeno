# Zeno — Team Onboarding

Zeno turns raw clips and a script into a finished vertical video. An AI agent (Claude Code, Antigravity, or OpenAI) makes the decisions; Python tools on your own computer do the heavy work (audio, cutting, faces, rendering). A small Zeno server transcribes speech, signs you in, and keeps what the team has learned. This page takes you from zero to a first edit.

## What you need
- A Mac (Apple Silicon) or a Windows PC. Zeno uses the GPU automatically (Apple GPU on a Mac; NVIDIA on Windows, CUDA 12.6+ driver) and falls back to CPU if there is none
- Your company Google account (`@gosupernova.live`)
- One AI agent set up on your machine (Claude Code, Antigravity, or OpenAI)

## 1. Install (once, about 5 minutes, nothing to type)

1. Get Zeno into **`Documents/Zeno`**. The repository is public: you do **not** need a GitHub account.
   - **Windows only, once:** install Git (`winget install --id Git.Git -e`; Windows asks for permission). The installer in step 2 also does this for you if it is missing.
   - Clone:
     ```bash
     cd ~/Documents
     git clone https://github.com/mathiniraiai-ai/Zeno.git
     ```
   - **No Git yet?** Download https://github.com/mathiniraiai-ai/Zeno/archive/refs/heads/main.zip (no sign-in), unzip it, and go to step 2. The installer installs Git if needed and connects the folder to GitHub, so it can update itself like a normal clone. Your files, `.env` and videos are not touched.
2. Double-click the installer:
   - **Mac:** `install.command` (first time: right-click it, choose **Open**, then **Open** again).
   - **Windows:** `install.bat`.
3. The installer asks you nothing and needs no admin password for Zeno itself (installing Git, only if it is missing, makes Windows ask for permission). It:
   - installs `uv`, which brings Python 3.12,
   - uses FFmpeg if the PC already has it, otherwise downloads it into `tools/ffmpeg/`,
   - creates `.venv` and installs the packages (on a Windows PC with an NVIDIA GPU it installs the GPU build of PyTorch),
   - installs Git if it is missing (Windows) and connects the folder to GitHub, so it can update itself,
   - creates `.env` from the template,
   - downloads the alignment model (Meta wav2vec2, about 360 MB, into `models/torch`),
   - sets up a daily background update (11:00 AM) that also frees disk space when it is nearly full,
   - runs real checks, and
   - **opens your browser for Google sign-in.** Sign in with your `@gosupernova.live` account. Your personal key is saved on your computer automatically; there is nothing to copy or paste.
4. Every line should end in `[OK]`. If the browser did not open, run `python scripts/setup.py --login`; to see who you are signed in as, run `python scripts/setup.py --whoami`.

Running the installer again is always safe; it skips what is already there. Never commit or share `.env` or `.zeno_session.json`.

Wherever this page says `python`, use the project's Python: `.venv/bin/python` on Mac, `.venv\Scripts\python` on Windows.

## 2. Before every video: the checks and the one question

Start a **new agent session for each video** (it keeps token use low), open the Zeno folder, and say you want to edit a video. The agent reads `agent/DIRECTOR.md` and does this, in this order:

1. **Live checks** (`python scripts/zeno.py --preflight`). The agent shows you a PASS/FAIL table:
   - FFmpeg runs its filters and loudness tool.
   - OpenCV decodes a real frame and runs the face detector.
   - The wav2vec2 aligner loads and processes a second of audio.
   - A small audio file goes to the Zeno server, which checks your key, its database, and that Gemini answers.

   If anything fails, nothing starts. Fix the line marked FAIL first.
2. **One question.** If everything passes, the agent asks for exactly two things:
   - the **script link**: the Google Doc **tab** for this video (the link ends in `?tab=…`; a link without a tab is refused so the other languages are never loaded), and
   - the **footage folder**: a folder on your computer (Drive or web links are not downloaded; download them first).
3. **Nothing starts before you answer.** Then `python scripts/zeno.py --inspect <footage folder> --script "<script link>" [--lang auto] [--style micronarrative]` repeats the live checks and measures what you gave it: the script (spoken lines, scenes, speakers, silent beats, split-screen notes, strike-through lines) and every clip (readable? has audio? how long?). It stops with `[BLOCKED]` and says why if something is wrong. It also prints which style the job follows; `--style <name>` makes the job count toward that style and the team's learning.

## 3. What Zeno measures, and what the agent decides

Zeno does the mechanical work: it transcribes, measures and renders. **The AI agent (Claude Opus or Sonnet) makes every editing decision.**

| Zeno measures | The agent decides from it |
|---|---|
| The words in each clip (Gemini, through the server) and the exact time of every word (wav2vec2) | which clip says which line, the best take, where to cut |
| Silences and breaths | which pauses to cut |
| Faces, framing, silent clips, plus a picture sheet of every clip | layouts, zooms, listeners in split screens |
| The script itself | transitions, ✗ and ✓ marks, strike-through text |

**The script is the timeline.** File names and file order do not affect the edit. A job uses about 50,000 to 70,000 tokens.

## 4. Approve the plan (one Yes/No)

The agent writes the plan and Zeno checks it: every line present, once, in script order; no cut inside a word; each voice matches its character. A plan that fails is not rendered. Then the agent shows you the plan and asks **one** Yes/No. Nothing renders before a Yes, and it does not ask again afterwards.

## 5. Render, check, hand over

After Yes: `python scripts/zeno.py --execute work/<project>/plan.edl`. You get, in `~/Downloads/Zeno Outputs/`:
- `<name>.mp4` (9:16) and `<name>_1x1.mp4` (square Meta ad),
- `<name>.timeline.txt` (every cut and effect),
- `<name>.prproj` and `<name>.premiere.xml` (open in Adobe Premiere Pro to polish).

Zeno then checks the video itself: every script line present in order, loudness (-11.5 LUFS), no clipped words or long pauses, ✗/✓ marks visible, split screens steady. **Move finished videos to the shared drive.**

The agent prints a token table and adds a row to `README.md`. After a verified job, Zeno sends the team server only numbers (shot lengths, zooms, transitions per minute), never your script, footage or video; an admin approves before a recipe changes for everyone.

## 6. Writing scripts and preparing clips so Zeno understands them

- **Who speaks:** `Lady 1: …`. A `Characters:` list at the top is fine; it is not read as dialogue.
- **Marks:** put ❌ or ✅ after a line (or `[WRONG]` / `[RIGHT]`). The mark appears at the end of that sentence.
- **Scene changes:** `Scene changes: …`, `Back to …`, `Cut to …` and `Flashback …` start a new scene and get a transition.
- **Strike-through text:** end a line with `(X Sign and Text Strikethrough)` and the line's words appear struck through in red.
- **Split screen:** write who sits where: `Top: Robot | Bottom: Lady 1`, or `Woman - Right screen | Miss Nova - Left screen`. "Horizontal split" means top and bottom; "vertical split" means left and right.
- **Silent moments:** `(Man takes out his phone)`, or a reaction on a line: `L4 Man (laughs mockingly): …`.
- **Silent listening clips (important for splits):** while one character speaks, the other panel shows the other character, silent and listening. Left speaks, right listens; right speaks, left listens; the same for top and bottom. Save each character's silent clip with their name in the file name, for example `Robot_listening.mp4` or `Lady1_idle.mp4`. Without one, the agent uses a quiet moment from that character's own clip and tells you.
- **Takes:** clips that say the same thing are takes of one line, whatever they are called. The agent compares them and uses the best. A learner repeating the teacher's corrected sentence is a line of its own, not a retake.
- **No clips yet?** Zeno can write the Veo shot JSON and first-frame prompt for every script line (`scripts/preproduce.py`, see `agent/concepts/08_preproduction`). Name the generated clips `<reel-code>_D-1.mp4`, `_D-2.mp4`, … and run the normal edit.
- Burned-in subtitles in client footage are ignored for now.

## 7. Where files go

| Folder | What | Share? |
|---|---|---|
| your footage folder, `input/script/` | raw clips and the script | no (local) |
| `work/<project>/` | transcript, plan, analysis | no (local, deletable) |
| `Downloads/Zeno Outputs/` | finished videos (outside the Zeno folder) | **move finished videos to the shared drive** |
| `styles/` | learned styles and format recipes | the owner changes these; you just update |

## 8. Disk, caches and updates (mostly automatic)
- **Caches refresh themselves.** When Zeno's code changes, old transcripts and analysis are rebuilt on the next job. You are never asked to clear a cache. Only if a result is wrong for a reason you cannot see, ask the agent to run the job once with `--fresh`.
- **Disk:** each 90-second video leaves about 330 MB of intermediates. The daily task frees space when the disk is nearly full; to do it yourself run `python scripts/clean.py` (shows what would go) and then `python scripts/clean.py --apply`.
- **Updates:** Zeno updates itself daily and before each job. You can also double-click `update.command` (Mac) or `update.bat` (Windows). New format recipes from the team arrive at the start of each job without any update. Do not edit `styles/`, `agent/` or `scripts/` yourself; tell the owner what you need.

## Licences
- Alignment model: Meta wav2vec2 base (95M), MIT licence, commercial use allowed.
- Gemini is used under Google's API terms through the Zeno server (the Gemini key never reaches your computer).
- Bundled third-party components (audited in `LICENSES.md`): `uroman` (MIT), OpenCV `YuNet` face detector (MIT), `Montserrat-ExtraBold.ttf` (SIL OFL 1.1), and `assets/sfx/` (Supernova internal production assets).
- Zeno itself is under the Supernova Internal Use License (`LICENSE`).

## If something goes wrong
- A check shows `[FAIL]` in the pre-job table: it names the part (FFmpeg, face detector, aligner, or the server). Fix that first. "not signed in" or "key rejected": run `python scripts/setup.py --login`.
- `[BLOCKED]` after you gave the script and footage: read the line below it (a link with no `?tab=`, an unreadable clip, a script with no spoken lines).
- Google Doc says private: ask for "anyone with the link can view".
- A transcript or a cut looks wrong: tell the agent the video, the line and what you hear.
- "Cannot update itself" or `[Auto-Update] … not connected to GitHub`: run the installer again (or `python scripts/auto_updater.py --connect-git`). It needs Git and an internet connection, not a GitHub account.
- Out of disk: `python scripts/clean.py --apply`.

## Known limits (be aware on your first jobs)
- The micronarrative style has been learned from two reference videos; full jobs on real raw footage are just starting, so tell the owner what looks off.
- The Windows installer is being tested on a real PC now: the Git step and the ZIP-to-clone connection were added after the first test found a PC with no Git.
- Footage must be a local folder; links are not downloaded.
