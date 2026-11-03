"""Automatic Git Pull & Daily 8:00 AM Update Scheduler for Zeno (`scripts/auto_updater.py`).

Ensures every team PC stays automatically up to date in two ways:
  1. **Pre-Job Auto-Pull**: Whenever an editor starts a new job (`zeno.py --inspect`, `prepare.py`,
     `zeno.py --check`) or when >= 12 hours have elapsed since the last check, Zeno automatically runs
     a fast, safe `git pull --rebase --autostash origin main` (and syncs `brain_memory.json` rules
     from Zeno Cloud) without touching `.env`, `.zeno_session.json`, `input/`, `work/`, or outputs.
  2. **Daily 8:00 AM OS Scheduler**: Installs a lightweight background job (`launchd` LaunchAgent on
     macOS with `Hour=8, Minute=0` + `RunAtLoad=true`; Scheduled Task on Windows at `08:00`) so even
     before the editor opens their IDE, Zeno is already updated to the latest commit.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
STATE_FILE = WORK / ".last_auto_update.json"
LOG_FILE = WORK / "auto_update.log"
LAUNCHD_LABEL = "live.gosupernova.zeno.autoupdate"


def _find_python() -> str:
    if sys.platform.startswith("win") or os.name == "nt":
        cand = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        cand = ROOT / ".venv" / "bin" / "python"
    return str(cand) if cand.is_file() else sys.executable


def _read_state() -> Dict[str, Any]:
    if not STATE_FILE.is_file():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(payload: Dict[str, Any]) -> None:
    try:
        WORK.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass


def _git_head_sha() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _has_uncommitted_code_edits() -> bool:
    """Return True if `.zeno-dev` is present and `scripts/` or `service/` have uncommitted changes."""
    if not (ROOT / ".zeno-dev").exists():
        return False
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "--", "scripts", "service", "styles"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        return bool(r.stdout.strip())
    except Exception:
        return False


def ensure_latest_update(
    force: bool = False,
    min_interval_hours: float = 0.0,
    timeout_sec: float = 8.0,
    quiet: bool = False,
    sync_brain: bool = True,
) -> Dict[str, Any]:
    """Run a fast, non-blocking `git pull --rebase --autostash` + Brain Memory sync.

    - Never touches `.env`, `.zeno_session.json`, `input/`, `work/`, or `~/Downloads/Zeno Outputs/`.
    - Protects uncommitted code in owner dev mode (`.zeno-dev`).
    - If `min_interval_hours > 0` and the last check was more recent (and `not force`), skips network call.
    """
    if (
        os.environ.get("ZENO_NO_AUTO_UPDATE", "").strip() == "1"
        or os.environ.get("CLAUDE_EDITOR_OFFLINE", "").strip() == "1"
        or os.environ.get("SCRIPTCUT_OFFLINE", "").strip() == "1"
        or os.environ.get("ZENO_NO_LIVE", "").strip() == "1"
    ):
        return {"ok": True, "status": "skipped_offline", "updated": False}

    if not (ROOT / ".git").exists():
        return {"ok": False, "status": "no_git_repo", "updated": False}

    now = time.time()
    state = _read_state()
    last_ts = float(state.get("last_checked_epoch") or 0.0)
    if not force and min_interval_hours > 0 and (now - last_ts) < (min_interval_hours * 3600.0):
        return {
            "ok": True,
            "status": "recent",
            "updated": False,
            "head": state.get("head") or _git_head_sha(),
            "last_checked": state.get("last_checked_iso", ""),
        }

    if _has_uncommitted_code_edits():
        head = _git_head_sha()
        return {
            "ok": True,
            "status": "skipped_dev_dirty",
            "updated": False,
            "head": head,
        }

    before_sha = _git_head_sha()
    pull_ok = False
    pull_msg = ""

    try:
        # Reset local auto-generated ledger conflicts on team PCs if needed so git pull never blocks
        r = subprocess.run(
            ["git", "pull", "--rebase", "--autostash", "origin", "main"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        if r.returncode == 0:
            pull_ok = True
            pull_msg = (r.stdout.strip() or "Already up to date.").splitlines()[-1]
        else:
            # Abort any failed rebase cleanly so the working tree stays untouched
            subprocess.run(
                ["git", "rebase", "--abort"],
                cwd=str(ROOT),
                capture_output=True,
                timeout=3.0,
                check=False,
            )
            pull_msg = (r.stderr.strip() or r.stdout.strip() or "git pull skipped").splitlines()[-1]
    except Exception as exc:
        pull_msg = f"offline/timeout ({exc})"

    after_sha = _git_head_sha()
    updated = bool(pull_ok and before_sha and after_sha and before_sha != after_sha)

    # If requirements.txt changed between before_sha and after_sha, refresh .venv packages automatically
    if updated:
        try:
            diff_r = subprocess.run(
                ["git", "diff", "--name-only", before_sha, after_sha, "--", "requirements.txt"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
            if "requirements.txt" in (diff_r.stdout or ""):
                py_bin = _find_python()
                uv_bin = shutil.which("uv")
                if uv_bin:
                    subprocess.run(
                        [uv_bin, "pip", "install", "--python", py_bin, "-r", str(ROOT / "requirements.txt")],
                        cwd=str(ROOT),
                        capture_output=True,
                        timeout=60.0,
                        check=False,
                    )
                else:
                    subprocess.run(
                        [py_bin, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")],
                        cwd=str(ROOT),
                        capture_output=True,
                        timeout=60.0,
                        check=False,
                    )
        except Exception:
            pass

    brain_pulled = 0
    if sync_brain:
        try:
            sys.path.insert(0, str(ROOT / "scripts"))
            import brain_memory
            b_res = brain_memory.sync_brain_memory_with_cloud("micronarrative")
            brain_pulled = int(b_res.get("pulled") or 0)
        except Exception:
            pass

    iso_now = dt.datetime.now().isoformat(timespec="seconds")
    result = {
        "ok": pull_ok,
        "status": "updated" if updated else ("up_to_date" if pull_ok else "offline"),
        "updated": updated,
        "before_head": before_sha,
        "head": after_sha,
        "brain_rules_pulled": brain_pulled,
        "last_checked_epoch": now,
        "last_checked_iso": iso_now,
        "message": pull_msg,
    }
    _write_state(result)

    if not quiet:
        if updated:
            print(f"  [Auto-Update] ✓ Pulled latest Zeno update ({before_sha} -> {after_sha})")
        elif pull_ok:
            print(f"  [Auto-Update] ✓ Up to date ({after_sha})")
        if brain_pulled > 0:
            print(f"  [Auto-Update] ✓ Pulled {brain_pulled} new team editorial rule(s) into brain_memory.json")

    return result


def install_daily_scheduler(hour: int = 8, minute: int = 0) -> Dict[str, Any]:
    """Install a daily 8:00 AM OS background task (macOS LaunchAgent / Windows Scheduled Task)."""
    py_bin = _find_python()
    script_path = str(Path(__file__).resolve())
    WORK.mkdir(parents=True, exist_ok=True)

    if sys.platform == "darwin":
        agents_dir = Path.home() / "Library" / "LaunchAgents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        plist_path = agents_dir / f"{LAUNCHD_LABEL}.plist"
        plist_data = {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": [py_bin, script_path, "--scheduled"],
            "WorkingDirectory": str(ROOT),
            "RunAtLoad": True,
            "StartCalendarInterval": {"Hour": int(hour), "Minute": int(minute)},
            "StandardOutPath": str(LOG_FILE),
            "StandardErrorPath": str(LOG_FILE),
            "EnvironmentVariables": {
                "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            },
        }
        try:
            with plist_path.open("wb") as fp:
                plistlib.dump(plist_data, fp)
            subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True, check=False)
            subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, check=False)
            return {
                "ok": True,
                "platform": "macos_launchd",
                "schedule": f"Daily at {hour:02d}:{minute:02d} + on login/wake",
                "path": str(plist_path),
            }
        except Exception as exc:
            return {"ok": False, "platform": "macos_launchd", "error": str(exc)}

    if sys.platform.startswith("win") or os.name == "nt":
        task_name = "ZenoDailyAutoUpdate"
        time_str = f"{int(hour):02d}:{int(minute):02d}"
        cmd_str = f'"{py_bin}" "{script_path}" --scheduled'
        try:
            r = subprocess.run(
                ["schtasks", "/Create", "/F", "/SC", "DAILY", "/TN", task_name, "/TR", cmd_str, "/ST", time_str],
                capture_output=True,
                text=True,
                check=False,
            )
            return {
                "ok": r.returncode == 0,
                "platform": "windows_schtasks",
                "schedule": f"Daily at {time_str}",
                "task_name": task_name,
            }
        except Exception as exc:
            return {"ok": False, "platform": "windows_schtasks", "error": str(exc)}

    return {"ok": True, "platform": sys.platform, "schedule": "pre-job auto-pull active"}


def main() -> None:
    ap = argparse.ArgumentParser(description="Zeno Automatic Git Updater & Daily 8:00 AM Scheduler")
    ap.add_argument("--scheduled", action="store_true", help="Run as background scheduled job (logs to work/auto_update.log)")
    ap.add_argument("--install-schedule", action="store_true", help="Install daily 8:00 AM background auto-update schedule")
    ap.add_argument("--hour", type=int, default=8, help="Daily schedule hour in 24h format (default: 8 for 8:00 AM)")
    ap.add_argument("--status", action="store_true", help="Print last auto-update status")
    args = ap.parse_args()

    if args.install_schedule:
        res = install_daily_scheduler(hour=args.hour, minute=0)
        if res.get("ok"):
            print(f"  [Auto-Update] ✓ Scheduled automatic daily git pull ({res.get('schedule')})")
        else:
            print(f"  [Auto-Update] Schedule note: {res.get('error', 'pre-job auto-pull active')}")
        return

    if args.status:
        st = _read_state()
        print(json.dumps(st or {"head": _git_head_sha(), "status": "never_checked"}, indent=2))
        return

    res = ensure_latest_update(force=True, min_interval_hours=0.0, quiet=False)
    if args.scheduled:
        try:
            WORK.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as fp:
                fp.write(f"[{res.get('last_checked_iso')}] status={res.get('status')} head={res.get('head')} msg={res.get('message')}\n")
        except Exception:
            pass


if __name__ == "__main__":
    main()
