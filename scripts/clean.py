#!/usr/bin/env python3
"""Free disk space safely. Dry run by default: nothing is deleted without --apply.

Usage:
    python3 scripts/clean.py                       # show what would be deleted
    python3 scripts/clean.py --apply               # delete it
    python3 scripts/clean.py --days 7 --purge-days 60 --standins --references --apply

What it removes (all can be rebuilt):
  1. work/_segments/        render cache
  2. work/<project>/        heavy files (audio, video, images) of projects not touched for --days (default 14)
  3. work/<project>/        the whole folder of projects not touched for --purge-days (default 90)
     work/_clips/<clip>/     per-clip caches follow the same two age rules
  4. input/standins/        with --standins   (stand-in clips cut from a reference)
  5. input/reference/       with --references (reference videos used to learn a style)

It never touches: Downloads/Zeno Outputs/, output/, styles/, assets/, models/, scripts/, input/footage/, input/script/, .env.
"""
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUTPUT, ROOT  # noqa: E402

HEAVY_EXT = {".wav", ".mkv", ".mp4", ".mov", ".jpg", ".jpeg", ".png", ".npy", ".aiff"}


def size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def newest_mtime(path: Path) -> float:
    times = [f.stat().st_mtime for f in path.rglob("*") if f.is_file()]
    return max(times) if times else path.stat().st_mtime


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def plan_cleanup(root: Path, days: float, purge_days: float, standins: bool, references: bool, now: float) -> list:
    """Return [(label, path, bytes)] of things to delete. Every path is inside root/work or root/input/{standins,reference}."""
    items = []
    work = root / "work"
    seg = work / "_segments"
    if seg.is_dir():
        for f in seg.iterdir():
            items.append(("render cache", f, size_of(f)))
    if work.is_dir():
        clip_cache = work / "_clips"
        folders = [p for p in work.iterdir() if p.is_dir() and not p.name.startswith("_")]
        folders += [p for p in clip_cache.iterdir() if p.is_dir()] if clip_cache.is_dir() else []
        for proj in sorted(folders):
            age_days = (now - newest_mtime(proj)) / 86400
            if age_days >= purge_days:
                items.append((f"project older than {purge_days:g} days", proj, size_of(proj)))
            elif age_days >= days:
                for f in proj.rglob("*"):
                    if f.is_file() and f.suffix.lower() in HEAVY_EXT:
                        items.append((f"heavy files, project older than {days:g} days", f, f.stat().st_size))
    for flag, rel, label in ((standins, "input/standins", "stand-in clips"), (references, "input/reference", "reference videos")):
        d = root / rel
        if flag and d.is_dir():
            for f in d.iterdir():
                if f.name != ".gitkeep":
                    items.append((label, f, size_of(f)))
    return items


def safe_delete(path: Path, root: Path) -> None:
    resolved = path.resolve()
    allowed = [(root / "work").resolve(), (root / "input" / "standins").resolve(), (root / "input" / "reference").resolve()]
    if not any(str(resolved).startswith(str(a) + "/") or str(resolved).startswith(str(a) + "\\") for a in allowed):
        raise RuntimeError(f"refusing to delete outside work/ or input/standins|reference: {resolved}")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def auto_clean(root: Path = ROOT, min_free_gb: float = None, days: float = 14.0, purge_days: float = 90.0) -> dict:
    """The no-questions cleanup run by the daily background task: does nothing unless free disk is under `min_free_gb`
    (default 20, or ZENO_MIN_FREE_GB), then applies the normal age rules. Stand-ins and reference videos are left alone."""
    limit = float(min_free_gb if min_free_gb is not None else os.environ.get("ZENO_MIN_FREE_GB", "20"))
    free_gb = shutil.disk_usage(root).free / 1e9
    if free_gb >= limit:
        return {"ran": False, "free_gb": round(free_gb, 1)}
    freed = 0
    for _, path, n in plan_cleanup(root, days, purge_days, False, False, time.time()):
        if path.exists():
            safe_delete(path, root)
            freed += n
    return {"ran": True, "free_gb_before": round(free_gb, 1), "freed_mb": round(freed / 1e6, 1)}


TRANSIENT_PROJECT_DIRS = ("_sub_frames", "_page_frames", "_scroll_frames", "_tmp_frames", "_preview_frames")
TRANSIENT_PROJECT_FILES = ("direct_call_pending.json",)


def _prune_work_logs(work: Path, max_age_hours: float = 24.0) -> tuple[int, int]:
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


def _prune_ota_staging(work: Path) -> tuple[int, int]:
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


def cleanup_after_job(root: Path = ROOT, keep_hours: float = None, project_dir: Path = None) -> dict:
    """Run at the end of every successful render, without asking.

    - Clears finished render-cache segments (`work/_segments`): when `keep_hours` is omitted, defaults to
      `ZENO_KEEP_CACHE_HOURS` (`0` = immediate cleanup once final MP4s + QC pass).
    - Prunes transient render scratch inside `project_dir` (`_sub_frames`, `_page_frames`, `*.tmp`, `*.part`),
      leftover `work/_ota_stage_*`, and `work/_logs/*.log` older than 24h.
    - Transcripts, alignments (`work/_clips/`), plans, finished videos and raw footage are NEVER touched.
    - `ZENO_AUTO_CLEAN=0` or `ZENO_CLEAR_AFTER_JOB=0` turns it off.
    """
    if os.environ.get("ZENO_AUTO_CLEAN", "1") == "0" or os.environ.get("ZENO_CLEAR_AFTER_JOB", "1") == "0":
        return {"ran": False}
    hours = float(keep_hours if keep_hours is not None else os.environ.get("ZENO_KEEP_CACHE_HOURS", "0"))
    work = root / "work"
    seg = work / "_segments"
    freed = kept = files = 0
    cutoff = time.time() - hours * 3600
    try:
        if seg.is_dir():
            for f in list(seg.iterdir()):
                n = size_of(f)
                if hours <= 0 or newest_mtime(f) < cutoff:
                    safe_delete(f, root)
                    freed += n
                    files += 1
                else:
                    kept += n
        if project_dir is not None:
            pdir = Path(project_dir).resolve()
            work_res = work.resolve()
            if pdir.is_dir() and (str(pdir).startswith(str(work_res) + "/") or str(pdir).startswith(str(work_res) + "\\")):
                for dname in TRANSIENT_PROJECT_DIRS:
                    td = pdir / dname
                    if td.is_dir():
                        n = size_of(td)
                        safe_delete(td, root)
                        freed += n
                        files += 1
                for fname in TRANSIENT_PROJECT_FILES:
                    tf = pdir / fname
                    if tf.is_file():
                        n = tf.stat().st_size
                        safe_delete(tf, root)
                        freed += n
                        files += 1
                for child in list(pdir.iterdir()):
                    if child.is_file() and (child.name.endswith(".tmp") or child.name.endswith(".part")):
                        n = child.stat().st_size
                        safe_delete(child, root)
                        freed += n
                        files += 1
        f_ota, n_ota = _prune_ota_staging(work)
        f_log, n_log = _prune_work_logs(work, max_age_hours=24.0)
        freed += f_ota + f_log
        files += n_ota + n_log
        return {"ran": True, "freed_mb": round(freed / 1e6, 1), "kept_mb": round(kept / 1e6, 1), "files": files}
    except Exception as exc:   # noqa: BLE001  (tidying must never fail a finished job)
        return {"ran": True, "error": str(exc)[:120], "freed_mb": round(freed / 1e6, 1), "files": files}


def cleanup_after_update(root: Path = ROOT, version: str = "") -> dict:
    """Run automatically whenever an OTA software update finishes installing.

    Clears stale Python `__pycache__` bytecode under `scripts/` & `service/`, old engine render chunks
    in `work/_segments`, leftover `work/_ota_stage_*`, stale `work/_update_check.json`, stale job-window markers,
    and old run logs (> 24h), while preserving all measured clips (`work/_clips/`), active project plans,
    credentials, and finished videos. Disable with `ZENO_AUTO_CLEAN=0` or `ZENO_CLEAR_ON_UPDATE=0`.
    """
    if os.environ.get("ZENO_AUTO_CLEAN", "1") == "0" or os.environ.get("ZENO_CLEAR_ON_UPDATE", "1") == "0":
        return {"ran": False}
    import json as _json
    freed = files = 0
    work = root / "work"
    try:
        # 1. Clear stale Python bytecode caches inside scripts/ and service/ so updated .py files take effect immediately
        for code_sub in ("scripts", "service"):
            base_d = (root / code_sub).resolve()
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
                safe_delete(f, root)
                freed += sz
                files += 1
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
        if root.resolve() == ROOT.resolve():
            try:
                import common as _common
                _common.job_done()
            except Exception:
                pass
        res = {
            "ran": True,
            "trigger": "after_update",
            "version": version,
            "freed_mb": round(freed / 1e6, 1),
            "files": files,
            "cleaned_at": int(time.time()),
        }
        try:
            work.mkdir(parents=True, exist_ok=True)
            (work / ".last_cleanup_state.json").write_text(_json.dumps(res, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass
        return res
    except Exception as exc:   # noqa: BLE001
        return {"ran": True, "trigger": "after_update", "error": str(exc)[:120], "freed_mb": round(freed / 1e6, 1), "files": files}


def main():
    ap = argparse.ArgumentParser(description="Free disk space (dry run unless --apply)")
    ap.add_argument("--apply", action="store_true", help="actually delete")
    ap.add_argument("--after-job", action="store_true", help="prune finished render/transient scratch safely")
    ap.add_argument("--after-update", action="store_true", help="prune stale bytecode, segments, and update scratch after OTA update")
    ap.add_argument("--project", default="", help="optional project folder path for --after-job")
    ap.add_argument("--version", default="", help="installed version tag for --after-update")
    ap.add_argument("--days", type=float, default=14, help="delete heavy files of projects untouched this long (default 14)")
    ap.add_argument("--purge-days", type=float, default=90, help="delete whole projects untouched this long (default 90)")
    ap.add_argument("--standins", action="store_true", help="also delete input/standins/")
    ap.add_argument("--references", action="store_true", help="also delete input/reference/ videos")
    ap.add_argument("--root", default=str(ROOT), help="project root (for testing)")
    a = ap.parse_args()

    root = Path(a.root)
    if a.after_update:
        import json as _json
        print(_json.dumps(cleanup_after_update(root=root, version=a.version)))
        return
    if a.after_job:
        import json as _json
        pdir = Path(a.project) if a.project else None
        print(_json.dumps(cleanup_after_job(root=root, project_dir=pdir)))
        return
    items = plan_cleanup(root, a.days, a.purge_days, a.standins, a.references, time.time())
    totals: dict = {}
    for label, _, n in items:
        c = totals.setdefault(label, [0, 0])
        c[0] += 1
        c[1] += n
    total = sum(n for _, _, n in items)
    print(("DELETING" if a.apply else "DRY RUN (nothing deleted)") + f" in {root}")
    for label, (count, n) in totals.items():
        print(f"  {label:<42} {count:>5} item(s)  {human(n):>9}")
    print(f"  {'total':<42} {len(items):>5} item(s)  {human(total):>9}")
    for out in (OUTPUT, root / "output"):
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
