# Zeno desktop app

A lightweight desktop launcher & companion for Zeno: handles Google Workspace Sign-In (`@gosupernova.live`), initial workspace download into `~/Documents/Zeno`, one-click setup bootstrap, and Cloud OTA auto-updates with clean hot-reload. Video editing itself runs through the zero-MCP Host-AI Direct launchers (`edit.command`, `edit-7k.command`, `edit-codex.command` or their `.bat` equivalents on Windows) and delivers outputs to `~/Downloads/Zeno Outs/<VideoName>/`.

## Use
- **Mac:** open `Zeno-<version>-arm64.dmg` (Apple Silicon) or `Zeno-<version>-x64.dmg` (Intel) and drag Zeno to Applications. The build is ad-hoc signed, not notarised (that needs an Apple Developer account), so macOS blocks the first open. macOS 15 or later: double-click Zeno, press **Done**, then **System Settings > Privacy & Security > Open Anyway**. Older macOS: right-click Zeno, **Open**, **Open**. Or in Terminal: `xattr -dr com.apple.quarantine /Applications/Zeno.app`.
- **Windows:** run `Zeno Setup <version>.exe`. It is not code-signed, so SmartScreen may say "Windows protected your PC": press **More info > Run anyway**.
- **First run on a new computer:** sign in with Google; the app downloads Zeno into `Documents/Zeno` and opens the installer (Python, FFmpeg, packages, Node for captions, the timing model).
- **Zeno folder:** the app finds Zeno at `ZENO_HOME`, `~/Documents/Zeno` (where the installer puts it), or the folder above `app/` in development.

## Develop
    cd app && npm install
    npm start                                   # run (unset ELECTRON_RUN_AS_NODE if your shell sets it)
    npm start -- --screenshot=out.png --select=<work folder> --size=1280x820   # save a picture of the window and quit
    npm run dist:mac                            # two .dmg files in app/dist: Apple Silicon (arm64) and Intel (x64)
    npm run dist:win                            # Zeno Setup <version>.exe (x64), builds on a Mac too

## Not done yet
Signing and notarising (needs an Apple Developer account and a Windows certificate), automatic app updates.
