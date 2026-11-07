# Zeno desktop app

A dedicated app that shows every edit and what the backend is doing, step by step: the five stages, the face scan of each clip, who speaks, every cut being rendered, every check, the plan as a timeline, the output files and the token numbers. Dark grey with green accents. It only reads job state (`scripts/jobs.py`); the editing itself still runs in Claude Code or Antigravity, started from **New edit** (the lean launcher).

## Use
- **Mac:** open `Zeno-1.0.0-arm64.dmg`, drag Zeno to Applications. The build is not signed yet, so the first time: right-click Zeno, **Open**, **Open** (or `xattr -dr com.apple.quarantine /Applications/Zeno.app`).
- **Zeno folder:** the app finds Zeno at `ZENO_HOME`, `~/Documents/Zeno` (where the installer puts it), or the folder above `app/` in development.

## Develop
    cd app && npm install
    npm start                                   # run (unset ELECTRON_RUN_AS_NODE if your shell sets it)
    npm start -- --screenshot=out.png --select=<work folder> --size=1280x820   # save a picture of the window and quit
    npm run dist:mac                            # .dmg in app/dist (Apple Silicon)
    npm run dist:win                            # .exe; build on Windows, or run the "Zeno desktop app" workflow on GitHub

## Not done yet
Signing and notarising (needs an Apple Developer account and a Windows certificate), automatic app updates, an Intel Mac build.
