"""Cloud OTA Software Updater & Daily 11:00 AM Scheduler for Zeno (`scripts/auto_updater.py`).

Delivers software updates directly from Zeno Cloud (`https://zeno-production-bef7.up.railway.app`)
to every client Mac (`.dmg`) and Windows PC (`.exe`) — zero Git and zero GitHub required on client machines.

Updates happen in three ways:
  1. **Desktop App Live Popup & One-Click / Auto-Download**: The Zeno desktop app polls `check_cloud_update()`
     and displays a "New update arrived" popup card (`Update & Reload Now` / `Later`), hot-reloading the UI
     and Python engine in seconds without reinstalling the `.dmg` or `.exe`.
  2. **Pre-Job Auto-Update**: Whenever an editor starts a job (`zeno.py --inspect`, `prepare.py`,
     `zeno.py --check`) or when >= 12 hours have elapsed, Zeno checks `/v1/version` and downloads `/v1/update-bundle`
     automatically, plus syncs `brain_memory.json` rules.
  3. **Daily 11:00 AM OS Scheduler**: Runs in the background via macOS `launchd` / Windows Scheduled Task.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
STATE_FILE = WORK / ".last_auto_update.json"
RELEASE_STATE_FILE = WORK / ".cloud_release_state.json"
CHECK_CACHE_FILE = WORK / "_update_check.json"
LOG_FILE = WORK / "auto_update.log"
LAUNCHD_LABEL = "live.gosupernova.zeno.autoupdate"

# Protected local paths that OTA updates MUST NEVER overwrite or delete
PROTECTED_PREFIXES = (
    ".env",
    ".zeno_session.json",
    ".zeno-dev",
    ".git/",
    ".venv/",
    "models/",
    "tools/",
    "work/",
    "input/",
    "output/",
    "scratch/",
)


def _find_python() -> str:
    if sys.platform.startswith("win") or os.name == "nt":
        cand = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        cand = ROOT / ".venv" / "bin" / "python"
    return str(cand) if cand.is_file() else sys.executable


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def get_local_version() -> str:
    vf = ROOT / "VERSION"
    if vf.is_file():
        try:
            v = vf.read_text(encoding="utf-8", errors="replace").strip()
            if v:
                return v
        except OSError:
            pass
    return "1.0.0"


def _semver_tuple(ver: str) -> Tuple[int, int, int, str]:
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(.*)$", (ver or "0.0.0").strip())
    if not m:
        return (0, 0, 0, ver or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4) or "")


def _cloud_credentials() -> Tuple[str, str]:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import auth_session

        url = auth_session.get_cloud_url().rstrip("/")
        tok = auth_session.get_active_token()
        return url, tok
    except Exception:
        return "", ""


def _is_offline_mode() -> bool:
    return any(
        os.environ.get(k, "").strip() == "1"
        for k in ("ZENO_NO_AUTO_UPDATE", "CLAUDE_EDITOR_OFFLINE", "SCRIPTCUT_OFFLINE", "ZENO_NO_LIVE")
    )


def _is_owner_dev_tree() -> bool:
    return (ROOT / ".zeno-dev").exists() and os.environ.get("ZENO_FORCE_DEV_OTA", "").strip() != "1"


def check_cloud_update(force: bool = False, timeout_sec: float = 8.0, cache_ttl_sec: float = 180.0) -> Dict[str, Any]:
    """Query Zeno Cloud `/v1/version` and compare against local `VERSION` + `.cloud_release_state.json`."""
    local_ver = get_local_version()
    rel_state = _read_json(RELEASE_STATE_FILE)
    local_sha256 = str(rel_state.get("bundle_sha256") or "").strip()
    local_sha = str(rel_state.get("sha") or "").strip()

    if _is_offline_mode():
        return {
            "ok": True,
            "status": "skipped_offline",
            "available": False,
            "current_version": local_ver,
            "latest_version": local_ver,
            "sha": local_sha,
        }

    now = time.time()
    cached = _read_json(CHECK_CACHE_FILE)
    if not force and cached and (now - float(cached.get("checked_at") or 0.0)) < cache_ttl_sec:
        if cached.get("current_version") == local_ver:
            return cached

    cloud_url, token = _cloud_credentials()
    if not cloud_url or not token:
        return {
            "ok": False,
            "status": "not_signed_in",
            "available": False,
            "current_version": local_ver,
            "latest_version": local_ver,
            "sha": local_sha,
            "message": "Sign in to Zeno Cloud first (python3 scripts/setup.py --login).",
        }

    req = urllib.request.Request(
        f"{cloud_url}/v1/version",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            out = {
                "ok": True,
                "status": "no_cloud_release",
                "available": False,
                "current_version": local_ver,
                "latest_version": local_ver,
                "sha": local_sha,
                "checked_at": now,
            }
            _write_json(CHECK_CACHE_FILE, out)
            return out
        return {
            "ok": False,
            "status": f"http_{exc.code}",
            "available": False,
            "current_version": local_ver,
            "latest_version": local_ver,
            "sha": local_sha,
            "message": f"Cloud server returned HTTP {exc.code}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "offline",
            "available": False,
            "current_version": local_ver,
            "latest_version": local_ver,
            "sha": local_sha,
            "message": f"Could not reach Zeno Cloud ({type(exc).__name__})",
        }

    if not data.get("has_release"):
        out = {
            "ok": True,
            "status": "no_cloud_release",
            "available": False,
            "current_version": local_ver,
            "latest_version": local_ver,
            "sha": local_sha,
            "checked_at": now,
        }
        _write_json(CHECK_CACHE_FILE, out)
        return out

    remote_ver = str(data.get("version") or local_ver).strip()
    remote_sha256 = str(data.get("bundle_sha256") or "").strip()
    remote_sha = str(data.get("sha") or "").strip()
    size_bytes = int(data.get("size_bytes") or 0)

    # Determine whether this client needs the update:
    # - Newer semantic version on cloud -> update available
    # - Same version, and client previously recorded a different bundle_sha256 -> hotfix update available
    # - Fresh install with matching version and no prior bundle_sha256 -> adopt baseline silently (no false popup)
    rm_tup = _semver_tuple(remote_ver)
    lc_tup = _semver_tuple(local_ver)
    if _is_owner_dev_tree():
        available = False
    elif rm_tup[:3] > lc_tup[:3]:
        available = True
    elif rm_tup[:3] == lc_tup[:3] and local_sha256 and remote_sha256 and local_sha256 != remote_sha256:
        available = True
    else:
        available = False
        if rm_tup[:3] == lc_tup[:3] and not local_sha256 and remote_sha256:
            _write_json(
                RELEASE_STATE_FILE,
                {
                    "version": local_ver,
                    "sha": remote_sha,
                    "bundle_sha256": remote_sha256,
                    "synced_at": dt.datetime.now().isoformat(timespec="seconds"),
                },
            )

    out = {
        "ok": True,
        "status": "update_available" if available else "up_to_date",
        "available": available,
        "behind": 1 if available else 0,
        "current_version": local_ver,
        "latest_version": remote_ver,
        "sha": remote_sha or local_sha,
        "bundle_sha256": remote_sha256,
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 1) if size_bytes else 0.0,
        "notes": str(data.get("notes") or "").strip(),
        "mandatory": bool(data.get("mandatory")),
        "dmg_url": str(data.get("dmg_url") or "").strip(),
        "exe_url": str(data.get("exe_url") or "").strip(),
        "published_at": data.get("published_at"),
        "checked_at": now,
    }
    _write_json(CHECK_CACHE_FILE, out)
    return out


def _is_protected_relpath(rel_posix: str) -> bool:
    clean = rel_posix.lstrip("/")
    if not clean or ".." in clean.split("/"):
        return True
    for pref in PROTECTED_PREFIXES:
        if pref.endswith("/"):
            if clean == pref[:-1] or clean.startswith(pref):
                return True
        elif clean == pref:
            return True
    return False


def apply_bundle_bytes(blob: bytes, expected_sha256: str = "", version: str = "", sha: str = "") -> Dict[str, Any]:
    """Verify SHA-256 and atomically extract a Zeno OTA update ZIP into `ROOT` without touching user files."""
    actual_sha256 = hashlib.sha256(blob).hexdigest()
    if expected_sha256 and actual_sha256.lower() != expected_sha256.strip().lower():
        return {"ok": False, "status": "checksum_mismatch", "updated": False, "message": "Downloaded bundle checksum mismatch."}

    req_path = ROOT / "requirements.txt"
    old_req_hash = hashlib.sha256(req_path.read_bytes()).hexdigest() if req_path.is_file() else ""

    stage_dir = WORK / f"_ota_stage_{os.getpid()}_{int(time.time())}"
    try:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
        stage_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
            names = set(zf.namelist())
            if "VERSION" not in names or "scripts/zeno.py" not in names:
                return {"ok": False, "status": "invalid_bundle", "updated": False, "message": "Bundle is missing VERSION or scripts/zeno.py."}
            for info in zf.infolist():
                if info.is_dir():
                    continue
                rel_posix = info.filename.replace("\\", "/").lstrip("/")
                if _is_protected_relpath(rel_posix):
                    continue
                dest = (stage_dir / rel_posix).resolve()
                if not str(dest).startswith(str(stage_dir.resolve())):
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(info.filename))
                mode = (info.external_attr >> 16) & 0o777
                if rel_posix.endswith((".command", ".sh")) or (mode & 0o111):
                    try:
                        os.chmod(dest, 0o755)
                    except OSError:
                        pass

        # Copy verified staged files over ROOT
        copied = 0
        for fp in stage_dir.rglob("*"):
            if not fp.is_file():
                continue
            rel_posix = fp.relative_to(stage_dir).as_posix()
            if _is_protected_relpath(rel_posix):
                continue
            target = ROOT / rel_posix
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp_target = target.with_name(f".{target.name}.ota_tmp")
            shutil.copy2(fp, tmp_target)
            tmp_target.replace(target)
            copied += 1
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)

    new_req_hash = hashlib.sha256(req_path.read_bytes()).hexdigest() if req_path.is_file() else ""
    if old_req_hash and new_req_hash and old_req_hash != new_req_hash:
        try:
            subprocess.run(
                [_find_python(), str(ROOT / "scripts" / "gpu_install.py")],
                cwd=str(ROOT),
                capture_output=True,
                timeout=900.0,
                check=False,
            )
        except Exception:
            pass
    elif not sys.platform.startswith("darwin"):
        # Ensure Windows/Linux PCs with NVIDIA GPUs automatically switch to CUDA PyTorch + NVENC FFmpeg on OTA updates
        try:
            subprocess.run(
                [_find_python(), str(ROOT / "scripts" / "gpu_install.py"), "--ensure-gpu"],
                cwd=str(ROOT),
                capture_output=True,
                timeout=900.0,
                check=False,
            )
        except Exception:
            pass

    final_ver = version or get_local_version()
    _write_json(
        RELEASE_STATE_FILE,
        {
            "version": final_ver,
            "sha": sha or actual_sha256[:7],
            "bundle_sha256": actual_sha256,
            "files_updated": copied,
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        },
    )
    return {
        "ok": True,
        "status": "updated",
        "updated": True,
        "version": final_ver,
        "sha": sha or actual_sha256[:7],
        "bundle_sha256": actual_sha256,
        "files_updated": copied,
        "message": f"Updated Zeno to v{final_ver} ({copied} files synced from Zeno Cloud).",
    }


def ensure_latest_update(
    force: bool = False,
    min_interval_hours: float = 0.0,
    timeout_sec: float = 45.0,
    quiet: bool = False,
    sync_brain: bool = True,
) -> Dict[str, Any]:
    """Check Zeno Cloud and download + install any new software release automatically.

    - Zero Git / zero GitHub required on user machines.
    - Never touches `.env`, `.zeno_session.json`, `.venv/`, `models/`, `tools/`, `work/`, `input/`, or outputs.
    - Never overwrites the owner's dev copy (`.zeno-dev`).
    """
    if _is_offline_mode():
        return {"ok": True, "status": "skipped_offline", "updated": False, "version": get_local_version()}

    if _is_owner_dev_tree():
        ver = get_local_version()
        return {"ok": True, "status": "skipped_owner_dev", "updated": False, "version": ver, "head": ver}

    now = time.time()
    state = _read_json(STATE_FILE)
    last_ts = float(state.get("last_checked_epoch") or 0.0)
    if not force and min_interval_hours > 0 and (now - last_ts) < (min_interval_hours * 3600.0):
        ver = get_local_version()
        return {
            "ok": True,
            "status": "recent",
            "updated": False,
            "version": ver,
            "head": state.get("head") or ver,
            "last_checked": state.get("last_checked_iso", ""),
        }

    before_ver = get_local_version()
    chk = check_cloud_update(force=True, timeout_sec=min(12.0, timeout_sec))

    updated = False
    update_ok = bool(chk.get("ok"))
    msg = str(chk.get("message") or "Up to date.")
    after_ver = before_ver

    if chk.get("available"):
        cloud_url, token = _cloud_credentials()
        sha256 = str(chk.get("bundle_sha256") or "")
        q = f"?sha256={urllib.parse.quote(sha256)}" if sha256 else ""
        req = urllib.request.Request(
            f"{cloud_url}/v1/update-bundle{q}",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            if not quiet:
                print(f"  [Auto-Update] Downloading Zeno v{chk.get('latest_version')} ({chk.get('size_mb', 0)} MB) from Zeno Cloud...")
            with urllib.request.urlopen(req, timeout=max(30.0, timeout_sec)) as resp:
                blob = resp.read()
            applied = apply_bundle_bytes(
                blob=blob,
                expected_sha256=sha256,
                version=str(chk.get("latest_version") or ""),
                sha=str(chk.get("sha") or ""),
            )
            update_ok = bool(applied.get("ok"))
            updated = bool(applied.get("updated"))
            after_ver = str(applied.get("version") or get_local_version())
            msg = str(applied.get("message") or "")
            # Refresh cached version check immediately so UI flips to "Up to date"
            check_cloud_update(force=True, timeout_sec=6.0)
        except Exception as exc:
            update_ok = False
            msg = f"Download failed ({type(exc).__name__}: {exc})"

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
        "ok": update_ok,
        "status": "updated" if updated else (str(chk.get("status") or "up_to_date") if update_ok else "offline"),
        "updated": updated,
        "before_version": before_ver,
        "version": after_ver,
        "before_head": before_ver,
        "head": after_ver,
        "brain_rules_pulled": brain_pulled,
        "last_checked_epoch": now,
        "last_checked_iso": iso_now,
        "message": msg,
    }
    _write_json(STATE_FILE, result)

    if not quiet:
        if updated:
            print(f"  [Auto-Update] ✓ Updated Zeno from Cloud (v{before_ver} -> v{after_ver})")
        elif update_ok:
            print(f"  [Auto-Update] ✓ Up to date (v{after_ver})")
        else:
            print(f"  [Auto-Update] Note: {msg}")
        if brain_pulled > 0:
            print(f"  [Auto-Update] ✓ Pulled {brain_pulled} new team editorial rule(s) into brain_memory.json")

    return result


def connect_to_git(root: Path = None, remote: str = "", branch: str = "main") -> Dict[str, Any]:
    """Legacy compatibility stub: Zeno updates directly from Zeno Cloud without Git or GitHub."""
    return {
        "ok": True,
        "status": "cloud_managed",
        "message": "Zeno updates automatically via Zeno Cloud — no Git or GitHub account is needed.",
    }


def remove_daily_scheduler() -> Dict[str, Any]:
    """Remove the old daily 11:00 AM background task (macOS LaunchAgent / Windows Scheduled Task).

    Updates are now applied from the Zeno desktop app popup ("Update & Reload Now") instead of a daily schedule.
    Safe to call when no task exists.
    """
    removed = []
    if sys.platform == "darwin":
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        if plist_path.exists():
            try:
                subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True, check=False)
            except Exception:
                pass
            try:
                plist_path.unlink()
                removed.append(str(plist_path))
            except Exception:
                pass
    elif sys.platform.startswith("win") or os.name == "nt":
        try:
            r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", "ZenoDailyAutoUpdate"], capture_output=True, text=True, check=False)
            if r.returncode == 0:
                removed.append("ZenoDailyAutoUpdate")
        except Exception:
            pass
    return {"ok": True, "removed": removed}


def install_daily_scheduler(hour: int = 11, minute: int = 0) -> Dict[str, Any]:
    """Install a daily 11:00 AM OS background task (macOS LaunchAgent / Windows Scheduled Task).

    No longer used by the installers: updates now come from the desktop app popup. Kept for manual use.
    """
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

    return {"ok": True, "platform": sys.platform, "schedule": "pre-job cloud auto-update active"}


def main() -> None:
    ap = argparse.ArgumentParser(description="Zeno Cloud OTA Auto-Updater & Daily 11:00 AM Scheduler")
    ap.add_argument("--scheduled", action="store_true", help="Run as background scheduled job (logs to work/auto_update.log)")
    ap.add_argument("--check", action="store_true", help="Check Zeno Cloud for a newer release and output JSON")
    ap.add_argument("--json", action="store_true", help="Output result as JSON (used by Zeno Desktop App)")
    ap.add_argument("--connect-git", action="store_true", help="Legacy no-op (updates come from Zeno Cloud)")
    ap.add_argument("--install-schedule", action="store_true", help="(Legacy) install a daily 11:00 AM background auto-update schedule")
    ap.add_argument("--remove-schedule", action="store_true", help="Remove the old daily background auto-update schedule (updates now come from the app popup)")
    ap.add_argument("--hour", type=int, default=11, help="Daily schedule hour in 24h format (default: 11 for 11:00 AM)")
    ap.add_argument("--status", action="store_true", help="Print last auto-update status")
    args = ap.parse_args()

    if args.connect_git:
        res = connect_to_git()
        print(f"  [Auto-Update] {res['message']}")
        return

    if args.check:
        res = check_cloud_update(force=True)
        print(json.dumps(res, ensure_ascii=False))
        return

    if args.remove_schedule:
        res = remove_daily_scheduler()
        print(f"  [Auto-Update] Daily schedule removed ({', '.join(res['removed']) or 'none was installed'}). Updates now come from the Zeno app popup.")
        return

    if args.install_schedule:
        res = install_daily_scheduler(hour=args.hour, minute=0)
        if res.get("ok"):
            print(f"  [Auto-Update] ✓ Scheduled automatic daily cloud update ({res.get('schedule')})")
        else:
            print(f"  [Auto-Update] Schedule note: {res.get('error', 'pre-job cloud auto-update active')}")
        return

    if args.status:
        st = _read_json(STATE_FILE)
        print(json.dumps(st or {"version": get_local_version(), "status": "never_checked"}, indent=2))
        return

    res = ensure_latest_update(force=True, min_interval_hours=0.0, quiet=args.json)
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
        return

    if args.scheduled:
        try:
            WORK.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as fp:
                fp.write(f"[{res.get('last_checked_iso')}] status={res.get('status')} version={res.get('version')} msg={res.get('message')}\n")
        except Exception:
            pass
        try:
            import clean

            cres = clean.auto_clean()
            if cres.get("ran"):
                with LOG_FILE.open("a", encoding="utf-8") as fp:
                    fp.write(f"[auto-clean] freed {cres['freed_mb']} MB (free disk was {cres['free_gb_before']} GB)\n")
        except Exception:
            pass
        # One last run on PCs that still have the old daily task: remove it, updates now come from the app popup.
        try:
            remove_daily_scheduler()
        except Exception:
            pass


if __name__ == "__main__":
    main()
