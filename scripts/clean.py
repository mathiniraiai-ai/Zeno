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


def main():
    ap = argparse.ArgumentParser(description="Free disk space (dry run unless --apply)")
    ap.add_argument("--apply", action="store_true", help="actually delete")
    ap.add_argument("--days", type=float, default=14, help="delete heavy files of projects untouched this long (default 14)")
    ap.add_argument("--purge-days", type=float, default=90, help="delete whole projects untouched this long (default 90)")
    ap.add_argument("--standins", action="store_true", help="also delete input/standins/")
    ap.add_argument("--references", action="store_true", help="also delete input/reference/ videos")
    ap.add_argument("--root", default=str(ROOT), help="project root (for testing)")
    a = ap.parse_args()

    root = Path(a.root)
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
