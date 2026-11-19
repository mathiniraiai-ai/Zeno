#!/usr/bin/env python3
"""Cross-platform Zeno completion notification library (`notify.py`).

Plays the bundled Zeno completion chime (`assets/sfx/zeno_notification.mp3` on macOS/frontend,
`assets/sfx/zeno_notification.wav` on Windows) and displays a native OS desktop pop-up
notification the instant a finished video (`.mp4`) lands inside the delivered Zeno output folder
(`~/Downloads/Zeno Outs/<FolderName>/` or `$ZENO_OUTPUT_DIR/<FolderName>/`).

Guarantees:
  - Strict delivery gate: never fires unless at least one non-empty rendered `.mp4` exists inside
    the delivered output folder.
  - Atomic anti-duplicate lock (`_delivery_notified.lock` + `_delivery_notified.json`): the same
    delivered video file (`folder + video size + mtime_ns`) only notifies once; a fresh render
    produces a new `delivery_id` and chimes again cleanly.
  - Electron desktop app handoff (`work/_last_delivery.json` + `work/_frontend_alive.json`):
    always writes the delivery event for `Zeno.app` / `Zeno.exe`, and yields direct OS audio/popup
    to the frontend when the desktop window is actively alive so editors never get a double chime.
  - Best-effort & non-blocking: notification errors can never fail or delay a video delivery.

Usage:
    python3 scripts/notify.py --test
    python3 scripts/notify.py --folder "~/Downloads/Zeno Outs/<FolderName>"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

ROOT = SCRIPTS_DIR.parent
WORK = ROOT / "work"
UI_DIR = ROOT / "assets" / "ui"
TOAST_HTML = UI_DIR / "zeno_toast.html"
ICON_PNG = UI_DIR / "zeno_icon.png"
SFX_DIR = ROOT / "assets" / "sfx"
NOTIFICATION_MP3 = SFX_DIR / "zeno_notification.mp3"
NOTIFICATION_WAV = SFX_DIR / "zeno_notification.wav"

LAST_DELIVERY_FILE = "_last_delivery.json"
FRONTEND_HEARTBEAT_FILE = "_frontend_alive.json"
NOTIFIED_STAMP_FILE = "_delivery_notified.json"
NOTIFIED_LOCK_FILE = "_delivery_notified.lock"

_LOCK_STALE_SEC = 30.0
_FRONTEND_MAX_AGE_SEC = 20.0


def _notification_sound_path(platform_name: Optional[str] = None) -> Optional[Path]:
    """Return the preferred bundled notification audio file for the host platform."""
    plat = (platform_name or sys.platform).lower()
    if plat.startswith("win"):
        for cand in (NOTIFICATION_WAV, NOTIFICATION_MP3):
            if cand.is_file():
                return cand
        return None
    for cand in (NOTIFICATION_MP3, NOTIFICATION_WAV):
        if cand.is_file():
            return cand
    return None


def find_delivered_video(dest_dir: Path, files: Optional[Sequence[str]] = None) -> Optional[Path]:
    """Return the primary non-empty delivered `.mp4` inside `dest_dir` (preferring `reel_*.mp4`,
    then `SQ_*.mp4`, then any `.mp4`), or `None` if no finished video is present yet.
    """
    if not dest_dir.is_dir():
        return None
    candidates: list[Path] = []
    if files:
        for fn in files:
            p = dest_dir / Path(str(fn)).name
            if p.suffix.lower() == ".mp4" and p.is_file():
                candidates.append(p)
    if not candidates:
        candidates = sorted(p for p in dest_dir.iterdir() if p.is_file() and p.suffix.lower() == ".mp4")

    valid = []
    for p in candidates:
        try:
            if p.stat().st_size > 0:
                valid.append(p)
        except OSError:
            continue
    if not valid:
        return None

    for p in valid:
        if p.name.lower().startswith("reel_"):
            return p
    for p in valid:
        if p.name.lower().startswith("sq_"):
            return p
    return valid[0]


def compute_delivery_id(dest_dir: Path, video_path: Path) -> str:
    """Compute a deterministic SHA-1 fingerprint for a specific delivered video build."""
    st = video_path.stat()
    raw = f"{dest_dir.resolve()}|{video_path.name}|{st.st_size}|{st.st_mtime_ns}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json_safe(path: Path) -> Dict[str, Any]:
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def is_frontend_alive(work_root: Optional[Path] = None, max_age_sec: float = _FRONTEND_MAX_AGE_SEC) -> bool:
    """Return True if the Zeno desktop frontend has updated `work/_frontend_alive.json` recently."""
    root = Path(work_root) if work_root is not None else WORK
    hb = _read_json_safe(root / FRONTEND_HEARTBEAT_FILE)
    ts = hb.get("ts")
    if ts is None:
        return False
    try:
        age = time.time() - float(ts)
        return -5.0 <= age <= float(max_age_sec)
    except (TypeError, ValueError):
        return False


def _acquire_file_lock(lock_path: Path) -> bool:
    """Atomically acquire `lock_path` via `O_CREAT | O_EXCL` with stale-lock recovery."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                os.write(fd, f"{os.getpid()} {time.time():.3f}\n".encode("ascii"))
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > _LOCK_STALE_SEC:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            return False
        except OSError:
            return False
    return False


def _release_file_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def _is_suppressed(force: bool = False) -> bool:
    """Return True when notifications are disabled via env or automated test runners."""
    if force:
        return False
    if os.environ.get("ZENO_NO_NOTIFY", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    if os.environ.get("CI", "").strip().lower() in ("1", "true", "yes"):
        return True
    # Keep `python -m unittest` runs quiet unless a test explicitly passes `force=True`
    if "unittest" in sys.modules:
        argv_joined = " ".join(sys.argv).lower()
        if "unittest" in argv_joined or "test_" in argv_joined:
            return True
    return False


def _spawn_detached(cmd: Sequence[str], *, wait: bool = False, timeout: float = 10.0) -> bool:
    try:
        if wait:
            r = subprocess.run(
                list(cmd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
            return r.returncode == 0
        kwargs: Dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = (
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(list(cmd), **kwargs)
        return True
    except Exception:
        return False


def play_notification_sound(sound_path: Optional[Path] = None, *, wait: bool = False) -> bool:
    """Play the bundled Zeno completion chime cleanly on macOS, Windows, or Linux."""
    snd = Path(sound_path) if sound_path is not None else _notification_sound_path()
    if snd is None or not snd.is_file():
        return False

    plat = sys.platform.lower()
    if plat == "darwin":
        afplay = shutil.which("afplay") or "/usr/bin/afplay"
        if Path(afplay).is_file():
            return _spawn_detached([afplay, str(snd)], wait=wait)
        return False

    if plat.startswith("win"):
        wav = snd if snd.suffix.lower() == ".wav" else (NOTIFICATION_WAV if NOTIFICATION_WAV.is_file() else snd)
        if wav.suffix.lower() == ".wav":
            ps_path = str(wav).replace("'", "''")
            ps_cmd = f"(New-Object Media.SoundPlayer '{ps_path}').PlaySync()"
            ps = shutil.which("powershell") or shutil.which("pwsh")
            if ps:
                return _spawn_detached(
                    [ps, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps_cmd],
                    wait=wait,
                )
            try:
                import winsound  # type: ignore[import-not-found]

                flags = winsound.SND_FILENAME | (0 if wait else winsound.SND_ASYNC)
                winsound.PlaySound(str(wav), flags)
                return True
            except Exception:
                return False
        return False

    for player, args in (
        ("paplay", [str(snd)]),
        ("aplay", ["-q", str(snd)]),
        ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet", str(snd)]),
    ):
        exe = shutil.which(player)
        if exe:
            return _spawn_detached([exe, *args], wait=wait)
    return False


def _default_output_root() -> Path:
    """Return the default Zeno delivery root (`$ZENO_OUTPUT_DIR` or `~/Downloads/Zeno Outs`)."""
    override = os.environ.get("ZENO_OUTPUT_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "Downloads" / "Zeno Outs"


def _format_saved_location(dest: Path) -> str:
    """Return a concise human-readable `Saved to ...` string for fallback OS notifications."""
    if dest.parent.name and dest.name:
        return f"Saved to {dest.parent.name}/{dest.name}"
    if dest.name:
        return f"Saved to {dest.name}"
    return "Saved to Downloads/Zeno Outs"


def _as_applescript_str(text: str) -> str:
    return str(text or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


def show_desktop_popup(
    title: str,
    subtitle: str,
    message: str,
    *,
    folder: Optional[Path] = None,
    video_path: Optional[Path] = None,
    files: Optional[Sequence[str]] = None,
    duration_sec: float = 9.0,
    wait: bool = False,
) -> bool:
    """Show the Zeno-themed floating desktop pop-up (`scripts/notify_popup.py`), falling back
    to a silent native OS notification if the themed toast cannot be launched.
    """
    if folder is not None:
        try:
            import notify_popup

            if notify_popup.show_themed_popup(
                title=str(subtitle or title or "").strip() or "Zeno",
                folder=Path(folder),
                video_path=video_path,
                files=files or (),
                badge="VIDEO READY",
                duration_sec=duration_sec,
                wait=wait,
            ):
                return True
        except Exception:
            pass

    plat = sys.platform.lower()
    if plat == "darwin":
        osa = shutil.which("osascript") or "/usr/bin/osascript"
        if not Path(osa).is_file():
            return False
        script = (
            f'display notification "{_as_applescript_str(message)}" '
            f'with title "{_as_applescript_str(title or "Zeno")}" '
            f'subtitle "{_as_applescript_str(subtitle)}"'
        )
        return _spawn_detached([osa, "-e", script], wait=wait)

    if plat.startswith("win"):
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if not ps:
            return False
        t_esc = str(title or "Zeno").replace("'", "''")
        m_esc = f"{subtitle} — {message}".strip(" —").replace("'", "''")
        ps_cmd = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$n = New-Object System.Windows.Forms.NotifyIcon; "
            "$n.Icon = [System.Drawing.SystemIcons]::Information; "
            "$n.Visible = $true; "
            f"$n.ShowBalloonTip(5000, '{t_esc}', '{m_esc}', [System.Windows.Forms.ToolTipIcon]::Info); "
            "Start-Sleep -Seconds 6; $n.Dispose()"
        )
        return _spawn_detached(
            [ps, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            wait=wait,
        )

    ns = shutil.which("notify-send")
    if ns:
        cmd = [ns]
        if ICON_PNG.is_file():
            cmd.extend(["-i", str(ICON_PNG)])
        cmd.extend([title or "Zeno", f"{subtitle}\n{message}".strip()])
        return _spawn_detached(cmd, wait=wait)
    return False


def notify_delivery(
    dest_dir: Path,
    folder_name: str = "",
    files: Optional[Sequence[str]] = None,
    work_dir: Optional[Path] = None,
    *,
    work_root: Optional[Path] = None,
    force: bool = False,
    play_audio: bool = True,
    show_popup: bool = True,
    wait: bool = False,
) -> Dict[str, Any]:
    """Notify the editor once a finished video is present inside the delivered Zeno output folder.

    Returns a structured status dict (`{"notified", "reason", "delivery_id", "sound_played",
    "popup_shown", "delegated_to_frontend", "video", "folder"}`). Never raises.
    """
    raw_dest = str(dest_dir or "").strip() if dest_dir is not None else ""
    dest = (Path(raw_dest).expanduser() if raw_dest else _default_output_root()).resolve()
    eff_name = str(folder_name or "").strip() or dest.name or "Zeno"
    eff_work_root = Path(work_root) if work_root is not None else WORK
    stamp_dir = Path(work_dir) if (work_dir is not None and Path(work_dir).is_dir()) else eff_work_root

    result: Dict[str, Any] = {
        "notified": False,
        "reason": "",
        "delivery_id": "",
        "sound_played": False,
        "popup_shown": False,
        "delegated_to_frontend": False,
        "video": "",
        "folder": str(dest),
    }

    try:
        video = find_delivered_video(dest, files=files)
        if video is None:
            result["reason"] = "no_video_in_output_folder"
            return result

        delivery_id = compute_delivery_id(dest, video)
        result["delivery_id"] = delivery_id
        result["video"] = str(video)

        stamp_path = stamp_dir / NOTIFIED_STAMP_FILE
        lock_path = stamp_dir / NOTIFIED_LOCK_FILE

        if not force:
            prev = _read_json_safe(stamp_path)
            if prev.get("delivery_id") == delivery_id:
                result["reason"] = "already_notified"
                return result

        if not _acquire_file_lock(lock_path):
            result["reason"] = "lock_held"
            return result

        try:
            # Re-check stamp after acquiring the exclusive lock
            if not force:
                prev = _read_json_safe(stamp_path)
                if prev.get("delivery_id") == delivery_id:
                    result["reason"] = "already_notified"
                    return result

            file_list = sorted(str(f) for f in files) if files else sorted(
                p.name for p in dest.iterdir() if p.is_file() and not p.name.startswith(".")
            )
            frontend_live = is_frontend_alive(work_root=eff_work_root)
            suppressed = _is_suppressed(force=force)

            payload: Dict[str, Any] = {
                "project": Path(work_dir).name if work_dir is not None else eff_name,
                "folder_name": eff_name,
                "folder": str(dest),
                "video": str(video),
                "files": file_list,
                "delivered_at": round(time.time(), 3),
                "delivery_id": delivery_id,
                "sound_file": NOTIFICATION_MP3.name,
                "notified_by": "frontend" if frontend_live else ("suppressed" if suppressed else "python"),
            }
            _write_json_atomic(stamp_path, payload)
            try:
                _write_json_atomic(eff_work_root / LAST_DELIVERY_FILE, payload)
            except Exception:
                pass

            try:
                import events

                events.emit(
                    "info",
                    f"Delivered '{eff_name}' -> {dest}",
                    delivery_id=delivery_id,
                    folder=str(dest),
                    video=str(video),
                )
            except Exception:
                pass

            if suppressed:
                result["reason"] = "suppressed"
                return result

            if frontend_live:
                result["notified"] = True
                result["delegated_to_frontend"] = True
                result["reason"] = "delegated_to_frontend"
                return result

            sound_ok = play_notification_sound(wait=wait) if play_audio else False
            popup_ok = (
                show_desktop_popup(
                    "Zeno — Video Ready",
                    eff_name,
                    _format_saved_location(dest),
                    folder=dest,
                    video_path=video,
                    files=file_list,
                    wait=wait,
                )
                if show_popup
                else False
            )
            result["sound_played"] = sound_ok
            result["popup_shown"] = popup_ok
            result["notified"] = bool(sound_ok or popup_ok)
            result["reason"] = "ok" if result["notified"] else "no_notifier_available"
            return result
        finally:
            _release_file_lock(lock_path)
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"error:{type(exc).__name__}"
        return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Play Zeno completion notification sound and desktop pop-up.")
    ap.add_argument("--test", action="store_true", help="Play the bundled Zeno notification sound and show a test pop-up")
    ap.add_argument("--folder", default="", help="Delivered output folder to verify and notify for")
    ap.add_argument("--name", default="", help="Optional display name for the notification")
    ap.add_argument("--force", action="store_true", help="Bypass duplicate-delivery lock and test suppression")
    ap.add_argument("--no-popup", action="store_true", help="Play sound only without OS pop-up")
    ap.add_argument("--no-sound", action="store_true", help="Show pop-up only without sound")
    ap.add_argument("--wait", action="store_true", help="Wait for playback to finish before exiting")
    args = ap.parse_args()

    if args.test:
        snd = _notification_sound_path()
        print(f"[NOTIFY] Sound asset: {snd}")
        sample_dir = Path(args.folder).expanduser() if args.folder else _default_output_root()
        s_ok = play_notification_sound(wait=False) if not args.no_sound else False
        p_ok = (
            show_desktop_popup(
                "Zeno — Video Ready",
                args.name or "Reel - W46 - Kannada - Split screen with flash card",
                _format_saved_location(sample_dir) if args.folder else "Saved to Downloads/Zeno Outs",
                folder=sample_dir,
                files=["reel_sample.mp4", "SQ_sample.mp4", "sample.prproj", "review_sheet.jpg"],
                wait=args.wait,
            )
            if not args.no_popup
            else False
        )
        print(f"[NOTIFY] sound_played={s_ok}, popup_shown={p_ok}")
        return 0 if (s_ok or p_ok) else 1

    if args.folder:
        res = notify_delivery(
            Path(args.folder),
            folder_name=args.name,
            force=args.force,
            play_audio=not args.no_sound,
            show_popup=not args.no_popup,
            wait=args.wait,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res.get("notified") or res.get("reason") in ("already_notified", "delegated_to_frontend") else 1

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
