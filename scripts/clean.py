#!/usr/bin/env python3
"""Free disk space & run post-job / post-OTA-update cleanup safely.

Dry run by default: nothing is deleted from manual runs without `--apply`.

Usage:
    python3 scripts/clean.py                       # show what would be deleted
    python3 scripts/clean.py --apply               # delete it
    python3 scripts/clean.py --after-job --project work/<project>
    python3 scripts/clean.py --after-update --version 2.0.0 --prev-version 1.0.28
    python3 scripts/clean.py --days 7 --purge-days 60 --standins --references --apply

What it removes (all can be rebuilt):
  1. work/_segments/        render cache
  2. work/<project>/        heavy files (audio, video, images) of projects not touched for --days (default 14)
  3. work/<project>/        the whole folder of projects not touched for --purge-days (default 90)
     work/_clips/<clip>/     per-clip caches follow the same two age rules
  4. input/standins/        with --standins   (stand-in clips cut from a reference)
  5. input/reference/       with --references (reference videos used to learn a style)

It never touches: Downloads/, output/, formats/, assets/, models/, scripts/, subtitles/, input/footage/, input/script/, .env.
"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

import argparse
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUTPUT, ROOT  # noqa: E402

HEAVY_EXT = {".wav", ".mkv", ".mp4", ".mov", ".jpg", ".jpeg", ".png", ".npy", ".aiff"}
TRANSIENT_PROJECT_DIRS = ("_sub_frames", "_page_frames", "_scroll_frames", "_tmp_frames", "_preview_frames")
TRANSIENT_PROJECT_FILES = ("direct_call_pending.json",)


def size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def newest_mtime(path: Path) -> float:
    if path.is_file():
        return path.stat().st_mtime
    times = [f.stat().st_mtime for f in path.rglob("*") if f.is_file()]
    return max(times) if times else path.stat().st_mtime


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def plan_cleanup(
    root: Path = ROOT,
    days: float = 14.0,
    purge_days: float = 90.0,
    standins: bool = False,
    references: bool = False,
    now: Optional[float] = None,
) -> List[Tuple[str, Path, int]]:
    """Return [(label, path, bytes)] of things to delete. Every path is inside root/work or root/input/{standins,reference}."""
    ts = float(now if now is not None else time.time())
    items: List[Tuple[str, Path, int]] = []
    root_path = Path(root)
    work = root_path / "work"
    seg = work / "_segments"
    if seg.is_dir():
        for f in seg.iterdir():
            items.append(("render cache", f, size_of(f)))
    if work.is_dir():
        clip_cache = work / "_clips"
        folders = [p for p in work.iterdir() if p.is_dir() and not p.name.startswith("_")]
        folders += [p for p in clip_cache.iterdir() if p.is_dir()] if clip_cache.is_dir() else []
        for proj in sorted(folders):
            age_days = (ts - newest_mtime(proj)) / 86400.0
            if age_days >= purge_days:
                items.append((f"project older than {purge_days:g} days", proj, size_of(proj)))
            elif age_days >= days:
                for f in proj.rglob("*"):
                    if f.is_file() and f.suffix.lower() in HEAVY_EXT:
                        items.append((f"heavy files, project older than {days:g} days", f, f.stat().st_size))
    for flag, rel, label in ((standins, "input/standins", "stand-in clips"), (references, "input/reference", "reference videos")):
        d = root_path / rel
        if flag and d.is_dir():
            for f in d.iterdir():
                if f.name != ".gitkeep":
                    items.append((label, f, size_of(f)))
    return items


def safe_delete(
    path: Path,
    root: Path = ROOT,
    allowed_roots: Optional[Tuple[Path, ...]] = None,
) -> None:
    """Delete `path` only after verifying it resides strictly inside `root/work` or `root/input/{standins,reference}`."""
    target = Path(path)
    root_path = Path(root)
    resolved = target.resolve()
    base_allowed = (
        allowed_roots
        if allowed_roots is not None
        else (
            root_path / "work",
            root_path / "input" / "standins",
            root_path / "input" / "reference",
        )
    )
    allowed = [Path(a).resolve() for a in base_allowed]
    if not any(
        str(resolved).startswith(str(a) + os.sep)
        or str(resolved).startswith(str(a) + "/")
        or str(resolved).startswith(str(a) + "\\")
        for a in allowed
    ):
        raise RuntimeError(f"refusing to delete outside work/ or input/standins|reference: {resolved}")
    if target.is_symlink():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()


def auto_clean(
    root: Path = ROOT,
    min_free_gb: Optional[float] = None,
    days: float = 14.0,
    purge_days: float = 90.0,
) -> Dict[str, Any]:
    """The no-questions cleanup run by the daily background task: does nothing unless free disk is under `min_free_gb`
    (default 20, or ZENO_MIN_FREE_GB), then applies the normal age rules. Stand-ins and reference videos are left alone."""
    root_path = Path(root)
    limit = float(min_free_gb if min_free_gb is not None else os.environ.get("ZENO_MIN_FREE_GB", "20"))
    free_gb = shutil.disk_usage(root_path).free / 1e9
    if free_gb >= limit:
        return {"ran": False, "free_gb": round(free_gb, 1)}
    freed = 0
    for _, path, n in plan_cleanup(root_path, days, purge_days, False, False, time.time()):
        if path.exists():
            safe_delete(path, root_path)
            freed += n
    return {"ran": True, "free_gb_before": round(free_gb, 1), "freed_mb": round(freed / 1e6, 1)}


def _prune_work_logs(work: Path, max_age_hours: float = 24.0) -> Tuple[int, int]:
    """Remove old run-*.log files (> max_age_hours) and cap _mcp_start.log to 512 KB when > 2 MB."""
    freed = files = 0
    cutoff = time.time() - max_age_hours * 3600.0
    logs_dir = work / "_logs"
    if logs_dir.is_dir():
        for lf in list(logs_dir.iterdir()):
            try:
                if lf.is_file() and lf.suffix == ".log" and lf.stat().st_mtime < cutoff:
                    sz = lf.stat().st_size
                    lf.unlink()
                    freed += sz
                    files += 1
            except OSError:
                pass
    mcp_log = work / "_mcp_start.log"
    try:
        if mcp_log.is_file() and mcp_log.stat().st_size > 2_000_000:
            raw = mcp_log.read_bytes()
            old_sz = len(raw)
            tail = raw[-512_000:]
            mcp_log.write_bytes(tail)
            freed += max(0, old_sz - len(tail))
            files += 1
    except OSError:
        pass
    return freed, files


def _prune_ota_staging(work: Path) -> Tuple[int, int]:
    freed = files = 0
    if not work.is_dir():
        return 0, 0
    for child in list(work.iterdir()):
        if child.name.startswith("_ota_stage_"):
            try:
                sz = size_of(child)
                safe_delete(child, work.parent)
                freed += sz
                files += 1
            except Exception:
                pass
    return freed, files


def cleanup_after_job(
    root: Path = ROOT,
    keep_hours: Optional[float] = None,
    project_dir: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Run at the end of every successful render, without asking.

    - Clears finished render-cache segments (`work/_segments`): when `keep_hours` is omitted, defaults to
      `ZENO_KEEP_CACHE_HOURS` (`0` = immediate cleanup once final MP4s + QC pass).
    - Prunes transient render scratch inside `project_dir` (`_sub_frames`, `_page_frames`, `_scroll_frames`,
      `_tmp_frames`, `_preview_frames`, `direct_call_pending.json`, `*.tmp`, `*.part`), leftover `work/_ota_stage_*`,
      and `work/_logs/*.log` older than 24h.
    - Transcripts, alignments (`work/_clips/`), plans, finished videos and raw footage are NEVER touched.
    - `ZENO_AUTO_CLEAN=0` or `ZENO_CLEAR_AFTER_JOB=0` turns it off.
    """
    if os.environ.get("ZENO_AUTO_CLEAN", "1") == "0" or os.environ.get("ZENO_CLEAR_AFTER_JOB", "1") == "0":
        return {"ran": False}
    hours = float(keep_hours if keep_hours is not None else os.environ.get("ZENO_KEEP_CACHE_HOURS", "0"))
    root_path = Path(root)
    work = root_path / "work"
    seg = work / "_segments"
    freed = kept = files = 0
    cutoff = time.time() - hours * 3600.0
    try:
        if seg.is_dir():
            for f in list(seg.iterdir()):
                n = size_of(f)
                if hours <= 0 or newest_mtime(f) < cutoff:
                    safe_delete(f, root_path)
                    freed += n
                    files += 1
                else:
                    kept += n
        if project_dir is not None:
            p_raw = Path(project_dir)
            if not p_raw.is_absolute() and (root_path / p_raw).is_dir():
                p_raw = root_path / p_raw
            pdir = p_raw.resolve()
            work_res = work.resolve()
            if pdir.is_dir() and (
                str(pdir).startswith(str(work_res) + os.sep)
                or str(pdir).startswith(str(work_res) + "/")
                or str(pdir).startswith(str(work_res) + "\\")
            ):
                for dname in TRANSIENT_PROJECT_DIRS:
                    td = pdir / dname
                    if td.is_dir():
                        n = size_of(td)
                        safe_delete(td, root_path)
                        freed += n
                        files += 1
                for fname in TRANSIENT_PROJECT_FILES:
                    tf = pdir / fname
                    if tf.is_file():
                        n = tf.stat().st_size
                        safe_delete(tf, root_path)
                        freed += n
                        files += 1
                for child in list(pdir.iterdir()):
                    if child.is_file() and (child.name.endswith(".tmp") or child.name.endswith(".part")):
                        n = child.stat().st_size
                        safe_delete(child, root_path)
                        freed += n
                        files += 1
        f_ota, n_ota = _prune_ota_staging(work)
        f_log, n_log = _prune_work_logs(work, max_age_hours=24.0)
        freed += f_ota + f_log
        files += n_ota + n_log
        return {"ran": True, "freed_mb": round(freed / 1e6, 1), "kept_mb": round(kept / 1e6, 1), "files": files}
    except Exception as exc:   # noqa: BLE001  (tidying must never fail a finished job)
        return {"ran": True, "error": str(exc)[:120], "freed_mb": round(freed / 1e6, 1), "files": files}


OTA_MANIFEST_NAME = "_zeno_manifest.json"
OTA_PROTECTED_REL = {
    ".env",
    ".zeno_session.json",
    ".zeno_owner_session.json",
    ".zeno-dev",
    ".claude/settings.local.json",
}
OTA_PROTECTED_PREFIXES = (
    ".git/",
    ".venv/",
    "models/",
    "tools/",
    "subtitles/node_modules/",
    "work/",
    "input/",
    "output/",
    "app/node_modules/",
    "app/dist/",
    "scratch/",
    "service/",
)
OTA_RUNTIME_SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".venv", "dist"}
OTA_WORK_PRESERVE = {
    ".gitkeep",
    ".last_auto_update.json",
    ".cloud_release_state.json",
    "_update_state.json",
    ".last_cleanup_state.json",
    ".last_ota_manifest.json",
    "_clips",
    "_job_outbox",
    "_knowledge",
    "zeno_telemetry.db",
    "token_telemetry.json",
    "auto_update.log",
}
LEGACY_CLAUDE_HOOK_MARKERS = ("call_cap.py", "chat_guard.py", "zeno_mcp", "session_check")
LEGACY_1X_BLOAT_REL = (
    OTA_MANIFEST_NAME,
    "agent",
    "styles",
    "CONCEPT.md",
    "LOW_COST_WORKFLOW.md",
    "scripts/zeno_mcp.py",
    "scripts/ai_director.py",
    "scripts/compact_run.py",
)


def has_pending_ota_cleanup(root: Path = ROOT) -> bool:
    """Return True when an extracted OTA manifest or legacy 1.x bloat remains on a non-dev root."""
    root_path = Path(root)
    if (root_path / ".zeno-dev").exists() and os.environ.get("ZENO_ALLOW_DEV_OTA_PRUNE", "").strip() != "1":
        return False
    return any((root_path / rel).exists() for rel in LEGACY_1X_BLOAT_REL)


def prune_pending_ota_cleanup(root: Path = ROOT) -> Optional[Dict[str, Any]]:
    """Run cleanup_after_update() if has_pending_ota_cleanup() detects unpruned OTA or 1.x files."""
    if os.environ.get("ZENO_AUTO_CLEAN", "1") == "0" or os.environ.get("ZENO_CLEAR_ON_UPDATE", "1") == "0":
        return None
    root_path = Path(root)
    if not has_pending_ota_cleanup(root_path):
        return None
    ver_file = root_path / "VERSION"
    ver = ver_file.read_text(encoding="utf-8").strip() if ver_file.is_file() else ""
    return cleanup_after_update(root=root_path, version=ver)


def _major_ver(ver: str) -> int:
    m = re.match(r"^v?(\d+)\.", str(ver or "").strip())
    return int(m.group(1)) if m else 0


def _is_ota_protected(rel_posix: str) -> bool:
    clean = rel_posix.replace("\\", "/").lstrip("/")
    if not clean or ".." in clean.split("/"):
        return True
    if clean in OTA_PROTECTED_REL:
        return True
    if any(clean.startswith(pfx) or clean == pfx.rstrip("/") for pfx in OTA_PROTECTED_PREFIXES):
        return True
    parts = clean.split("/")
    if any(part in OTA_RUNTIME_SKIP_DIRS for part in parts):
        return True
    return False


def _read_prev_version_for_cleanup(work: Path) -> str:
    for fname, key in (
        (".last_cleanup_state.json", "version"),
        (".cloud_release_state.json", "version"),
        ("_update_state.json", "installed_version"),
        (".last_auto_update.json", "before_version"),
    ):
        fp = work / fname
        if fp.is_file():
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                val = str(data.get(key) or "").strip()
                if val:
                    return val
            except Exception:
                pass
    return ""


def _is_legacy_claude_hook_entry(entry: Any) -> bool:
    if isinstance(entry, str):
        return any(marker in entry for marker in LEGACY_CLAUDE_HOOK_MARKERS)
    if not isinstance(entry, dict):
        return False
    parts: List[str] = []
    for key in ("command", "cmd"):
        val = entry.get(key)
        if isinstance(val, str) and val:
            parts.append(val)
    args = entry.get("args")
    if isinstance(args, list):
        parts.extend(str(a) for a in args if a is not None)
    elif isinstance(args, str) and args:
        parts.append(args)
    haystack = " ".join(parts)
    return any(marker in haystack for marker in LEGACY_CLAUDE_HOOK_MARKERS)


def _is_legacy_zeno_mcp_entry(name: str, entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    cmd = str(entry.get("command") or "")
    args = entry.get("args")
    args_str = " ".join(str(a) for a in args) if isinstance(args, list) else str(args or "")
    combined = f"{cmd} {args_str}".replace("\\", "/").lower()
    if "zeno_mcp.py" in combined or "zeno_mcp" in combined:
        return True
    return str(name).strip().lower() == "zeno" and "scripts/" in combined and "zeno" in combined


def _strip_mcp_map(mcp_map: Any) -> int:
    if not isinstance(mcp_map, dict):
        return 0
    drop_keys = [k for k, v in mcp_map.items() if _is_legacy_zeno_mcp_entry(str(k), v)]
    for k in drop_keys:
        mcp_map.pop(k, None)
    return len(drop_keys)


def _cleanup_legacy_claude_mcp(home_dir: Optional[Path] = None) -> int:
    """Safely remove stale `1.x` `zeno` MCP (`scripts/zeno_mcp.py`) entries from user Claude configs."""
    home = Path(home_dir) if home_dir is not None else Path.home()
    candidates: List[Path] = [
        home / ".claude.json",
        home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        home / ".config" / "Claude" / "claude_desktop_config.json",
    ]
    if home_dir is None:
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            candidates.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    else:
        candidates.append(home / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json")

    total_removed = 0
    seen: set[Path] = set()
    for cfg_path in candidates:
        try:
            res_p = cfg_path.resolve()
        except OSError:
            res_p = cfg_path
        if res_p in seen or not cfg_path.is_file():
            continue
        seen.add(res_p)
        try:
            raw = cfg_path.read_text(encoding="utf-8")
            if not raw.strip():
                continue
            data = json.loads(raw)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue

        removed_here = _strip_mcp_map(data.get("mcpServers"))
        projs = data.get("projects")
        if isinstance(projs, dict):
            for proj_cfg in projs.values():
                if isinstance(proj_cfg, dict):
                    removed_here += _strip_mcp_map(proj_cfg.get("mcpServers"))

        if removed_here <= 0:
            continue
        try:
            orig_mode = cfg_path.stat().st_mode & 0o777
            tmp = cfg_path.with_suffix(".json.zeno_tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.chmod(tmp, orig_mode or 0o600)
            os.replace(tmp, cfg_path)
            total_removed += removed_here
        except OSError:
            continue
    return total_removed


def _cleanup_legacy_claude_hooks(root: Path) -> int:
    """Safely strip legacy Zeno v1.x hook entries from `.claude/settings.local.json` while preserving user hooks/settings."""
    settings_path = Path(root) / ".claude" / "settings.local.json"
    if not settings_path.is_file():
        return 0
    try:
        raw = settings_path.read_text(encoding="utf-8")
        if not raw.strip():
            return 0
        data = json.loads(raw)
    except (OSError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0

    hooks_obj = data.get("hooks")
    removed = 0
    if isinstance(hooks_obj, dict):
        new_hooks: Dict[str, Any] = {}
        for event_name, entries in hooks_obj.items():
            if not isinstance(entries, list):
                new_hooks[event_name] = entries
                continue
            kept_entries: List[Any] = []
            for item in entries:
                if isinstance(item, dict) and isinstance(item.get("hooks"), list):
                    inner = item["hooks"]
                    kept_inner: List[Any] = []
                    for h in inner:
                        if _is_legacy_claude_hook_entry(h):
                            removed += 1
                        else:
                            kept_inner.append(h)
                    if kept_inner:
                        kept_entries.append({**item, "hooks": kept_inner})
                    elif not inner:
                        kept_entries.append(item)
                elif _is_legacy_claude_hook_entry(item):
                    removed += 1
                else:
                    kept_entries.append(item)
            if kept_entries:
                new_hooks[event_name] = kept_entries
        if removed > 0:
            if new_hooks:
                data["hooks"] = new_hooks
            else:
                data.pop("hooks", None)
    elif isinstance(hooks_obj, list):
        kept_list: List[Any] = []
        for item in hooks_obj:
            if _is_legacy_claude_hook_entry(item):
                removed += 1
            else:
                kept_list.append(item)
        if removed > 0:
            if kept_list:
                data["hooks"] = kept_list
            else:
                data.pop("hooks", None)

    if removed <= 0:
        return 0
    try:
        tmp = settings_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, settings_path)
    except OSError:
        return 0
    return removed


def _prune_from_manifest(root: Path, version: str = "", prev_version: str = "") -> Tuple[int, int, int, bool]:
    """Remove obsolete files/empty dirs left inside managed dirs when upgrading via OTA (strictly preserving user data)."""
    man_path = root / OTA_MANIFEST_NAME
    if not man_path.is_file():
        return 0, 0, 0, False
    dev_guard = (root / ".zeno-dev").exists() and os.environ.get("ZENO_ALLOW_DEV_OTA_PRUNE", "").strip() != "1"
    if dev_guard:
        try:
            man_path.unlink(missing_ok=True)
        except OSError:
            pass
        return 0, 0, 0, False

    try:
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
    except Exception:
        return 0, 0, 0, False
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        return 0, 0, 0, False

    root_res = root.resolve()
    keep_files = {str(p).replace("\\", "/").lstrip("/") for p in manifest.get("files") or [] if str(p).strip()}
    managed_dirs = [str(d).replace("\\", "/").strip("/") for d in manifest.get("managed_dirs") or [] if str(d).strip()]
    managed_top = [str(f).replace("\\", "/").lstrip("/") for f in manifest.get("managed_top_files") or [] if str(f).strip()]
    obsolete = [str(p).replace("\\", "/").lstrip("/") for p in manifest.get("obsolete_paths") or [] if str(p).strip()]
    clear_work = bool(manifest.get("clear_work_projects")) or (
        bool(prev_version and version) and _major_ver(version) > _major_ver(prev_version) > 0
    )

    freed = pruned_files = pruned_dirs = 0

    for mdir in managed_dirs:
        if _is_ota_protected(mdir + "/"):
            continue
        base = (root / mdir).resolve()
        if not base.is_dir() or not str(base).startswith(str(root_res) + os.sep):
            continue
        for cur, dirs, fnames in os.walk(base, topdown=True, followlinks=False):
            dirs[:] = [d for d in dirs if d not in OTA_RUNTIME_SKIP_DIRS]
            cur_path = Path(cur)
            for fn in fnames:
                fp = cur_path / fn
                if fn == ".DS_Store" or fp.is_symlink():
                    continue
                try:
                    rel_posix = fp.resolve().relative_to(root_res).as_posix()
                except Exception:
                    continue
                if rel_posix == OTA_MANIFEST_NAME or _is_ota_protected(rel_posix):
                    continue
                if rel_posix not in keep_files:
                    try:
                        sz = fp.stat().st_size
                        fp.unlink()
                        freed += sz
                        pruned_files += 1
                    except OSError:
                        pass
        for cur, dirs, fnames in os.walk(base, topdown=False, followlinks=False):
            cur_path = Path(cur)
            if cur_path.name in OTA_RUNTIME_SKIP_DIRS:
                continue
            try:
                if not any(cur_path.iterdir()):
                    cur_path.rmdir()
                    pruned_dirs += 1
            except OSError:
                pass

    for rel in sorted(set(managed_top) | set(obsolete)):
        if not rel or rel == OTA_MANIFEST_NAME or _is_ota_protected(rel):
            continue
        if rel in keep_files:
            continue
        cand = root / rel
        try:
            cand_res = cand.resolve()
        except OSError:
            continue
        if not str(cand_res).startswith(str(root_res) + os.sep):
            continue
        if cand.is_file() or cand.is_symlink():
            try:
                sz = cand.stat().st_size if cand.is_file() else 0
                cand.unlink()
                freed += sz
                pruned_files += 1
            except OSError:
                pass
        elif cand.is_dir() and rel in obsolete:
            try:
                sz = size_of(cand)
                shutil.rmtree(cand)
                freed += sz
                pruned_dirs += 1
            except OSError:
                pass

    try:
        work = root / "work"
        work.mkdir(parents=True, exist_ok=True)
        (work / ".last_ota_manifest.json").write_text(
            json.dumps({
                "version": version or str(manifest.get("version") or ""),
                "prev_version": prev_version,
                "tracked_files": len(keep_files),
                "pruned_files": pruned_files,
                "pruned_dirs": pruned_dirs,
                "clear_work_projects": clear_work,
                "applied_at": int(time.time()),
            }, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    try:
        man_path.unlink(missing_ok=True)
    except OSError:
        pass
    return freed, pruned_files, pruned_dirs, clear_work


def cleanup_after_update(
    root: Path = ROOT,
    version: str = "",
    prev_version: str = "",
    clear_work: bool = False,
    home_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run automatically whenever an OTA software update finishes installing.

    Clears stale Python `__pycache__` bytecode under `scripts/`, `service/`, & `tests/`, old engine render chunks
    in `work/_segments`, leftover `work/_ota_stage_*`, stale `work/_update_check.json`, stale job-window markers,
    legacy Zeno v1.x hooks inside `.claude/settings.local.json`, stale user-global `zeno` MCP (`zeno_mcp.py`) entries
    in `~/.claude.json`, and prunes obsolete managed files using `_zeno_manifest.json`, while preserving all measured
    clips (`work/_clips/`), credentials, `.venv/`, `models/`, `tools/`, `subtitles/node_modules/`, `app/node_modules/`,
    `input/`, and finished videos.
    On major version upgrades (e.g., `1.x -> 2.0.0`) or `clear_work_projects`, also resets old `1.x` `work/` project
    caches while preserving `work/_clips/` and updater/telemetry state.
    Disable with `ZENO_AUTO_CLEAN=0` or `ZENO_CLEAR_ON_UPDATE=0`.
    """
    if os.environ.get("ZENO_AUTO_CLEAN", "1") == "0" or os.environ.get("ZENO_CLEAR_ON_UPDATE", "1") == "0":
        return {"ran": False}
    freed = files = pruned_files = pruned_dirs = legacy_hooks_removed = legacy_mcp_removed = 0
    root_path = Path(root)
    work = root_path / "work"
    resolved_prev = (prev_version or _read_prev_version_for_cleanup(work)).strip()
    dev_guard = (root_path / ".zeno-dev").exists() and os.environ.get("ZENO_ALLOW_DEV_OTA_PRUNE", "").strip() != "1"
    had_legacy_1x_dirs = (not dev_guard) and any(
        (root_path / d).is_dir() for d in ("agent", "styles")
    )
    try:
        # 0. Prune obsolete files/empty dirs left behind in managed folders when upgrading via OTA
        m_freed, pruned_files, pruned_dirs, m_clear_work = _prune_from_manifest(
            root_path, version=version, prev_version=resolved_prev
        )
        freed += m_freed
        files += pruned_files + pruned_dirs
        clear_work_projects = bool(clear_work) or m_clear_work or had_legacy_1x_dirs
        if not clear_work_projects and (work / ".last_ota_manifest.json").is_file():
            try:
                last_man = json.loads((work / ".last_ota_manifest.json").read_text(encoding="utf-8"))
                if (
                    isinstance(last_man, dict)
                    and str(last_man.get("version") or "") == str(version or "")
                    and bool(last_man.get("clear_work_projects"))
                ):
                    clear_work_projects = True
            except Exception:
                pass
        if not clear_work_projects and resolved_prev and version and _major_ver(version) > _major_ver(resolved_prev) > 0:
            clear_work_projects = True

        # 0b. Safely strip legacy Zeno v1.x hooks & global zeno_mcp.py server entries without touching user tools
        legacy_hooks_removed = _cleanup_legacy_claude_hooks(root_path)
        legacy_mcp_removed = _cleanup_legacy_claude_mcp(home_dir=home_dir)

        # 1. Clear stale Python bytecode caches inside scripts/, service/, and tests/ so updated .py files take effect immediately
        for code_sub in ("scripts", "service", "tests"):
            base_d = (root_path / code_sub).resolve()
            if base_d.is_dir():
                for pyc_dir in list(base_d.rglob("__pycache__")):
                    if pyc_dir.is_dir() and str(pyc_dir.resolve()).startswith(str(base_d)):
                        sz = size_of(pyc_dir)
                        shutil.rmtree(pyc_dir, ignore_errors=True)
                        freed += sz
                        files += 1
        # 2. Clear render segment scratch from previous engine version
        seg = work / "_segments"
        if seg.is_dir():
            for f in list(seg.iterdir()):
                sz = size_of(f)
                safe_delete(f, root_path)
                freed += sz
                files += 1
        # 2b. On major upgrades (1.x -> 2.0.0) or explicit manifest/--clear-work flag, clear stale project packets/caches while keeping work/_clips/
        if clear_work_projects and work.is_dir():
            for child in list(work.iterdir()):
                if child.name in OTA_WORK_PRESERVE or child.name.startswith("_clips"):
                    continue
                if child.is_dir() or child.name.startswith(("_subs", "_job_marker")) or child.suffix == ".log":
                    try:
                        sz = size_of(child)
                        safe_delete(child, root_path)
                        freed += sz
                        files += 1
                    except Exception:
                        pass
        # 3. Clear OTA staging dirs, stale update-check cache, and old run logs
        f_ota, n_ota = _prune_ota_staging(work)
        f_log, n_log = _prune_work_logs(work, max_age_hours=24.0)
        freed += f_ota + f_log
        files += n_ota + n_log
        for stale_f in (work / "_update_check.json", work / ".job_window.json"):
            if stale_f.is_file():
                try:
                    sz = stale_f.stat().st_size
                    stale_f.unlink()
                    freed += sz
                    files += 1
                except OSError:
                    pass
        if root_path.resolve() == ROOT.resolve():
            try:
                import common as _common

                _common.job_done()
            except Exception:
                pass
        res = {
            "ran": True,
            "trigger": "after_update",
            "version": version,
            "prev_version": resolved_prev,
            "pruned_files": pruned_files,
            "pruned_dirs": pruned_dirs,
            "legacy_hooks_removed": legacy_hooks_removed,
            "legacy_mcp_removed": legacy_mcp_removed,
            "clear_work_projects": clear_work_projects,
            "freed_mb": round(freed / 1e6, 1),
            "files": files,
            "cleaned_at": int(time.time()),
        }
        try:
            work.mkdir(parents=True, exist_ok=True)
            (work / ".last_cleanup_state.json").write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass
        return res
    except Exception as exc:   # noqa: BLE001
        return {
            "ran": True,
            "trigger": "after_update",
            "error": str(exc)[:120],
            "freed_mb": round(freed / 1e6, 1),
            "files": files,
        }


def main() -> None:
    ap = argparse.ArgumentParser(description="Free disk space (dry run unless --apply)")
    ap.add_argument("--apply", action="store_true", help="actually delete")
    ap.add_argument("--after-job", action="store_true", help="prune finished render/transient scratch safely")
    ap.add_argument("--after-update", action="store_true", help="prune stale bytecode, segments, and update scratch after OTA update")
    ap.add_argument("--project", default="", help="optional project folder path for --after-job")
    ap.add_argument("--version", default="", help="installed version tag for --after-update")
    ap.add_argument("--prev-version", default="", help="previous version tag before OTA update for --after-update")
    ap.add_argument("--clear-work", action="store_true", help="also clear old work/ project caches on --after-update (keeps work/_clips)")
    ap.add_argument("--days", type=float, default=14, help="delete heavy files of projects untouched this long (default 14)")
    ap.add_argument("--purge-days", type=float, default=90, help="delete whole projects untouched this long (default 90)")
    ap.add_argument("--standins", action="store_true", help="also delete input/standins/")
    ap.add_argument("--references", action="store_true", help="also delete input/reference/ videos")
    ap.add_argument("--root", default=str(ROOT), help="project root (for testing)")
    a = ap.parse_args()

    root = Path(a.root)
    if a.after_update:
        print(
            json.dumps(
                cleanup_after_update(
                    root=root,
                    version=a.version,
                    prev_version=a.prev_version,
                    clear_work=a.clear_work,
                )
            )
        )
        return
    if a.after_job:
        pdir = Path(a.project) if a.project else None
        print(json.dumps(cleanup_after_job(root=root, project_dir=pdir)))
        return
    items = plan_cleanup(root, a.days, a.purge_days, a.standins, a.references, time.time())
    totals: Dict[str, List[int]] = {}
    for label, _, n in items:
        c = totals.setdefault(label, [0, 0])
        c[0] += 1
        c[1] += n
    total = sum(n for _, _, n in items)
    print(("DELETING" if a.apply else "DRY RUN (nothing deleted)") + f" in {root}")
    for label, (count, n) in totals.items():
        print(f"  {label:<42} {count:>5} item(s)  {human(n):>9}")
    print(f"  {'total':<42} {len(items):>5} item(s)  {human(total):>9}")
    seen_outputs = {p.resolve(): p for p in (OUTPUT, root / "output")}
    for out in seen_outputs.values():
        if out.is_dir() and any(out.iterdir()):
            print(f"\n{out} holds {human(size_of(out))}. This tool never deletes it: move finished videos to the shared drive.")
    if not a.apply:
        print("\nRun again with --apply to delete.")
        return
    freed = 0
    for _, path, n in items:
        if path.exists():
            safe_delete(path, root)
            freed += n
    print(f"\nFreed {human(freed)}.")


if __name__ == "__main__":
    main()
