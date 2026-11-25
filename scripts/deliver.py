"""Stage 5 Deliverable Assembler (`deliver.py`).

Assembles and moves finished deliverables into `~/Downloads/Zeno Outs/<FolderName>/`
(or `$ZENO_OUTPUT_DIR/<FolderName>/` when `ZENO_OUTPUT_DIR` is configured):
  - `reel_<FolderName>.mp4`   (9:16 Master)
  - `SQ_<FolderName>.mp4`     (1:1 Square, when rendered)
  - `<FolderName>.prproj`     (Adobe Premiere Pro project)
  - `<FolderName>.xml`        (Premiere / Final Cut XML timeline)
  - `review_sheet.jpg`        (Whole-video QC contact sheet)
  - `<FolderName>.plan.edl`   (Exact edit decision list)
  - `<FolderName>.script.txt` (Normalized source script)

Usage:
    python3 scripts/deliver.py work/<project>/plan.edl [--name <FolderName>] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

try:
    import edl_parser
except Exception:
    edl_parser = None  # type: ignore[assignment]

ROOT: Path = getattr(common, "ROOT", Path(__file__).resolve().parent.parent)
WORK: Path = getattr(common, "WORK", ROOT / "work")
OUTPUT: Path = getattr(
    common,
    "OUTPUT",
    Path(
        os.environ.get("ZENO_OUTPUT_DIR", "").strip()
        or (Path.home() / "Downloads" / "Zeno Outs")
    ).expanduser(),
)


def _delivery_base_dir() -> Path:
    """Resolve base directory for delivered job folders."""
    fn = getattr(common, "_delivery_base_dir", None)
    if callable(fn):
        return Path(fn()).expanduser()
    override = os.environ.get("ZENO_OUTPUT_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "Downloads" / "Zeno Outs"


def _strip_ratio_prefix(raw: str) -> str:
    if hasattr(common, "strip_ratio_prefix"):
        return str(common.strip_ratio_prefix(raw))
    s = str(raw or "").strip()
    stripped = re.sub(r"^(?:reel|sq)[\s_-]+", "", s, flags=re.IGNORECASE).strip()
    return stripped or s


def _sanitize_folder_name(raw: str) -> str:
    if hasattr(common, "job_folder_name"):
        return str(common.job_folder_name(raw))
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", _strip_ratio_prefix(raw)).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)[:180].strip()
    return cleaned or "Zeno_Edit"


def _load_plan(plan_path: Path) -> Dict[str, Any]:
    if plan_path.suffix.lower() == ".edl":
        if edl_parser is not None:
            parse_fn = getattr(edl_parser, "parse_edl", getattr(edl_parser, "load_edl", None))
            if callable(parse_fn):
                return parse_fn(plan_path)
        json_sib = plan_path.with_suffix(".json")
        if json_sib.is_file():
            return common.read_json(json_sib)
        raise RuntimeError(f"Cannot parse EDL plan {plan_path}: edl_parser unavailable and no {json_sib} found.")
    return common.read_json(plan_path)


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _dedup_paths(paths: Iterable[Path]) -> List[Path]:
    seen: set[str] = set()
    out: List[Path] = []
    for p in paths:
        try:
            key = str(p.expanduser().resolve())
        except OSError:
            key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _move_deliverable(candidates: Iterable[Path], dst: Path, repo_output_dir: Path) -> Optional[Path]:
    """Move the first existing candidate file to `dst` (or keep in place if already at `dst`),
    and clean up any duplicate leftover copies inside `Zeno/output/`.
    """
    existing = [c for c in _dedup_paths(candidates) if c.is_file()]
    if not existing:
        return None

    dst_res = dst.resolve()
    already_at_dst = next((c for c in existing if c.resolve() == dst_res), None)
    chosen = already_at_dst if already_at_dst is not None else existing[0]

    dst.parent.mkdir(parents=True, exist_ok=True)
    if chosen.resolve() != dst_res:
        if dst.exists():
            dst.unlink()
        shutil.move(str(chosen), str(dst))

    # Ensure zero duplicate copies remain inside Zeno/output/
    for extra in existing:
        try:
            extra_res = extra.resolve()
        except OSError:
            continue
        if extra_res != dst_res and extra.is_file() and _is_inside(extra_res, repo_output_dir):
            try:
                extra.unlink()
            except OSError:
                pass

    return dst


def _export_premiere_if_missing(
    plan: Dict[str, Any],
    plan_path: Path,
    dest_dir: Path,
    work_dir: Path,
) -> None:
    pr_mod = None
    for mod_name in ("premiere_export", "premiere"):
        try:
            pr_mod = __import__(mod_name)
            break
        except ImportError:
            continue
    if pr_mod is None:
        return
    export_fn = getattr(pr_mod, "export_premiere_project", getattr(pr_mod, "export_project", None))
    if not callable(export_fn):
        return
    canvas = plan.get("canvas") or {}
    fps = int(canvas.get("fps") or 30)
    width = int(canvas.get("width") or 720)
    height = int(canvas.get("height") or 1280)
    try:
        export_fn(
            plan=plan,
            plan_path=plan_path,
            out_dir=dest_dir,
            work_dir=work_dir,
            fps=fps,
            width=width,
            height=height,
        )
    except Exception:
        pass


def _resolve_script_source(plan: Dict[str, Any], work_dir: Path) -> Optional[Path]:
    refs: List[Any] = [plan.get("script")]
    side_json = work_dir / "plan.json"
    if side_json.is_file():
        try:
            refs.append(common.read_json(side_json).get("script"))
        except Exception:
            pass
    facts_json = work_dir / "facts.json"
    if facts_json.is_file():
        try:
            refs.append(common.read_json(facts_json).get("script"))
        except Exception:
            pass

    for ref in refs:
        if not ref:
            continue
        cand = common.resolve(ref)
        if cand.is_file():
            return cand

    for local_script in (work_dir / "script.txt", work_dir / "script.md"):
        if local_script.is_file():
            return local_script

    input_scripts = sorted(
        p for p in (ROOT / "input" / "script").glob("*")
        if p.is_file() and not p.name.startswith(".")
    ) if (ROOT / "input" / "script").is_dir() else []
    if len(input_scripts) == 1:
        return input_scripts[0]
    return None


def _prune_empty_output_subdirs(dirs: Iterable[Path], repo_output_dir: Path) -> None:
    for d in _dedup_paths(dirs):
        try:
            d_res = d.resolve()
            out_res = repo_output_dir.resolve()
        except OSError:
            continue
        if d_res == out_res or not d_res.is_dir() or not _is_inside(d_res, out_res):
            continue
        try:
            entries = list(d_res.iterdir())
            if all(e.is_file() and e.name == ".DS_Store" for e in entries):
                for e in entries:
                    e.unlink(missing_ok=True)
                d_res.rmdir()
        except OSError:
            pass


def deliver_to_downloads(plan_path: Path, name: str = "", *, notify: bool = True) -> dict:
    """Assemble and move all rendered deliverables for `plan_path` into `~/Downloads/Zeno Outs/<FolderName>/`
    (or `$ZENO_OUTPUT_DIR/<FolderName>/`).

    Once the finished video lands inside the output folder, triggers the bundled Zeno completion
    notification (`scripts/notify.py` + `assets/sfx/zeno_notification.mp3`) unless `notify=False`.

    Returns `{"folder": str(dest_dir), "files": sorted_filenames, "notification": {...}}`.
    """
    p_path = common.resolve(plan_path)
    if not p_path.is_file():
        raise FileNotFoundError(f"Edit plan not found: {plan_path}")

    plan = _load_plan(p_path)
    work_dir = p_path.parent
    raw_plan_out = str(plan.get("output") or "").strip()
    raw_out_stem = Path(raw_plan_out).stem if raw_plan_out else ""
    orig_stem = _strip_ratio_prefix(raw_out_stem) if raw_out_stem else _strip_ratio_prefix(work_dir.name)

    chosen_raw = str(name or "").strip()
    if not chosen_raw and raw_out_stem:
        chosen_raw = _strip_ratio_prefix(raw_out_stem)
    if not chosen_raw:
        chosen_raw = _strip_ratio_prefix(work_dir.name) or work_dir.name
    folder_name = _sanitize_folder_name(chosen_raw)

    downloads_root = _delivery_base_dir()
    dest_dir = (downloads_root / folder_name).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    repo_output_dir = ROOT / "output"
    resolved_plan_out = common.resolve(raw_plan_out) if raw_plan_out else None

    search_dirs: List[Path] = [dest_dir]
    if resolved_plan_out is not None:
        search_dirs.append(resolved_plan_out.parent)
    search_dirs.extend([
        repo_output_dir / folder_name,
        repo_output_dir / orig_stem,
        repo_output_dir,
        OUTPUT / folder_name,
        OUTPUT / orig_stem,
        OUTPUT,
    ])
    search_dirs = _dedup_paths(search_dirs)

    stems_to_check: List[str] = []
    for st in (folder_name, orig_stem, raw_out_stem, work_dir.name):
        s_clean = str(st or "").strip()
        if s_clean and s_clean not in stems_to_check:
            stems_to_check.append(s_clean)

    # 1) 9:16 Master MP4 -> reel_<FolderName>.mp4
    reel_dst = dest_dir / f"reel_{folder_name}.mp4"
    reel_cands: List[Path] = [reel_dst]
    if resolved_plan_out is not None:
        reel_cands.append(resolved_plan_out)
    for d in search_dirs:
        for st in stems_to_check:
            base_st = _strip_ratio_prefix(st)
            reel_cands.append(d / f"reel_{base_st}.mp4")
            reel_cands.append(d / f"{st}.mp4")
    _move_deliverable(reel_cands, reel_dst, repo_output_dir)

    # 2) 1:1 Square MP4 (if rendered) -> SQ_<FolderName>.mp4
    sq_dst = dest_dir / f"SQ_{folder_name}.mp4"
    sq_cands: List[Path] = [sq_dst]
    if resolved_plan_out is not None and hasattr(common, "square_job_output"):
        sq_cands.append(Path(common.square_job_output(resolved_plan_out)))
    for d in search_dirs:
        for st in stems_to_check:
            base_st = _strip_ratio_prefix(st)
            sq_cands.append(d / f"SQ_{base_st}.mp4")
    _move_deliverable(sq_cands, sq_dst, repo_output_dir)

    # 3) Adobe Premiere Pro (.prproj & .xml) -> <FolderName>.prproj & <FolderName>.xml
    prproj_dst = dest_dir / f"{folder_name}.prproj"
    xml_dst = dest_dir / f"{folder_name}.xml"

    def _collect_prproj_cands() -> List[Path]:
        cands = [prproj_dst]
        for d in [*search_dirs, work_dir]:
            for st in stems_to_check:
                base_st = _strip_ratio_prefix(st)
                cands.append(d / f"{base_st}.prproj")
                cands.append(d / f"{st}.prproj")
        return cands

    def _collect_xml_cands() -> List[Path]:
        cands = [xml_dst, dest_dir / f"{folder_name}.premiere.xml"]
        for d in [*search_dirs, work_dir]:
            for st in stems_to_check:
                base_st = _strip_ratio_prefix(st)
                cands.append(d / f"{base_st}.xml")
                cands.append(d / f"{base_st}.premiere.xml")
                cands.append(d / f"{st}.xml")
                cands.append(d / f"{st}.premiere.xml")
        return cands

    if not any(p.is_file() for p in _collect_prproj_cands()) or not any(p.is_file() for p in _collect_xml_cands()):
        _export_premiere_if_missing(plan, p_path, dest_dir, work_dir)

    _move_deliverable(_collect_prproj_cands(), prproj_dst, repo_output_dir)
    _move_deliverable(_collect_xml_cands(), xml_dst, repo_output_dir)

    # Also move optional subtitle (.srt) or timeline (.timeline.txt) sidecars out of Zeno/output/ if present
    srt_cands: List[Path] = [dest_dir / f"{folder_name}.srt", dest_dir / f"reel_{folder_name}.srt"]
    tl_cands: List[Path] = [dest_dir / f"reel_{folder_name}.timeline.txt"]
    for d in search_dirs:
        for st in stems_to_check:
            base_st = _strip_ratio_prefix(st)
            srt_cands.append(d / f"{base_st}.srt")
            srt_cands.append(d / f"reel_{base_st}.srt")
            tl_cands.append(d / f"reel_{base_st}.timeline.txt")
            tl_cands.append(d / f"{st}.timeline.txt")
    if any(c.is_file() for c in srt_cands):
        _move_deliverable(srt_cands, dest_dir / f"{folder_name}.srt", repo_output_dir)
    if any(c.is_file() for c in tl_cands):
        _move_deliverable(tl_cands, dest_dir / f"reel_{folder_name}.timeline.txt", repo_output_dir)

    # 4) review_sheet.jpg -> copy alongside deliverables (or build if missing)
    review_dst = dest_dir / "review_sheet.jpg"
    work_review = work_dir / "review_sheet.jpg"
    if work_review.is_file():
        if work_review.resolve() != review_dst.resolve():
            shutil.copy2(work_review, review_dst)
    elif not review_dst.is_file() and reel_dst.is_file():
        try:
            import verify

            built_sheet = verify.build_review_sheet(
                plan,
                reel_dst,
                work_review,
                sq_path=sq_dst if sq_dst.is_file() else None,
            )
            if built_sheet and built_sheet.is_file():
                shutil.copy2(built_sheet, review_dst)
        except Exception:
            pass

    # 5) <FolderName>.plan.edl -> copy exact edit plan
    edl_dst = dest_dir / f"{folder_name}.plan.edl"
    edl_src = p_path if p_path.suffix.lower() == ".edl" else (work_dir / "plan.edl")
    if edl_src.is_file():
        if edl_src.resolve() != edl_dst.resolve():
            shutil.copy2(edl_src, edl_dst)
    elif edl_parser is not None and hasattr(edl_parser, "format_edl"):
        edl_dst.write_text(edl_parser.format_edl(plan), encoding="utf-8")
    else:
        shutil.copy2(p_path, edl_dst)

    # 6) <FolderName>.script.txt -> copy or extract plain NFC script text
    script_dst = dest_dir / f"{folder_name}.script.txt"
    script_src = _resolve_script_source(plan, work_dir)
    if script_src is not None and script_src.is_file():
        if script_src.resolve() != script_dst.resolve():
            if script_src.suffix.lower() in (".txt", ".md"):
                shutil.copy2(script_src, script_dst)
            elif hasattr(common, "read_script_text"):
                script_dst.write_text(common.read_script_text(script_src), encoding="utf-8")
            else:
                shutil.copy2(script_src, script_dst)

    # Clean up any emptied staging folder inside Zeno/output/
    _prune_empty_output_subdirs(search_dirs, repo_output_dir)

    sorted_filenames = sorted(
        p.name for p in dest_dir.iterdir()
        if p.is_file() and not p.name.startswith(".")
    )

    notif_res: Dict[str, Any] = {"notified": False, "reason": "disabled"}
    if notify:
        try:
            import notify as _notify

            notif_res = _notify.notify_delivery(
                dest_dir,
                folder_name=folder_name,
                files=sorted_filenames,
                work_dir=work_dir,
            )
        except Exception as exc:  # noqa: BLE001
            notif_res = {"notified": False, "reason": f"error:{type(exc).__name__}"}

    try:
        import zeno_tracker as _zt
        _vid_sec = sum(max(0.0, float(s.get("end", 0.0)) - float(s.get("start", 0.0))) for s in (plan.get("segments") or []))
        _zt.record_delivered(
            work_dir,
            p_path,
            plan,
            folder=dest_dir,
            files=sorted_filenames,
            video_seconds=_vid_sec,
        )
    except BaseException:
        pass

    return {
        "folder": str(dest_dir),
        "files": sorted_filenames,
        "notification": notif_res,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Assemble and move Zeno deliverables into ~/Downloads/Zeno Outs/<FolderName>/.")
    ap.add_argument("plan", help="Path to work/<project>/plan.edl or plan.json")
    ap.add_argument("--name", default="", help="Optional deliverable folder name override")
    ap.add_argument("--no-notify", action="store_true", help="Suppress completion notification sound & pop-up")
    ap.add_argument("--json", action="store_true", help="Print machine-readable JSON result")
    args = ap.parse_args()

    res = deliver_to_downloads(Path(args.plan), name=args.name, notify=not args.no_notify)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(f"[DELIVERED] {res['folder']}")
        for fn in res["files"]:
            print(f"  - {fn}")


if __name__ == "__main__":
    main()
