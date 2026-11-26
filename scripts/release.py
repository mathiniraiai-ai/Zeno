#!/usr/bin/env python3
"""Publish a Zeno OTA software update directly to the Zeno Cloud server (`scripts/release.py`).

Replaces GitHub on client laptops: you build/edit on your Mac and publish once to Zeno Cloud.
Every connected client PC/Mac (`Zeno.app` / `Zeno.exe` / CLI) detects the new version automatically,
shows a "New update arrived" notification popup in the desktop app, or auto-downloads it at 11:00 AM / pre-job.

Usage:
    python3 scripts/release.py "Faster zooms & whiteboard text sync"          # bumps patch (1.0.0 -> 1.0.1) & uploads
    python3 scripts/release.py --no-bump "Sync current version to cloud"      # keeps current VERSION & uploads
    python3 scripts/release.py --bump minor "New subtitle engine"             # bumps minor (1.0.x -> 1.1.0) & uploads
    python3 scripts/release.py --mandatory "Critical fix"                     # marks update for auto-install immediately
    python3 scripts/release.py --dry-run "Preview bundle size"                # builds ZIP locally in work/ without uploading
    python3 scripts/release.py --status                                       # shows currently live cloud release
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

TOP_FILES = (
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
    "VERSION",
    "RELEASE_NOTES.txt",
    "requirements.txt",
    "install.command",
    "install.bat",
    "update.command",
    "update.bat",
    "edit.command",
    "edit.bat",
    "edit-7k.command",
    "edit-7k.bat",
    "edit-codex.command",
    "edit-codex.bat",
    ".env.example",
    ".gitignore",
    ".mcp.json",
    "LICENSE",
    "LICENSES.md",
)

# Keep gold/ excluded from client bundles so the OTA ZIP stays ~25 MB under Railway's 45 MB limit.
BUNDLE_DIRS = (
    ".agent",
    ".claude",
    ".codex",
    "scripts",
    "formats",
    "subtitles",
    "assets",
    "tests",
    "app/renderer",
    "app/build",
    "app/scripts",
)

APP_TOP_FILES = (
    "app/package.json",
    "app/main.js",
    "app/preload.js",
    "app/README.md",
)

APP_EXCLUDE = (
    "node_modules",
    "dist",
    "__pycache__",
    ".DS_Store",
    "kavioo_lexicon.db",
    "zeno_keys.db*",
)

SKIP_NAMES = {
    ".DS_Store",
    "Thumbs.db",
    "__pycache__",
    ".env",
    ".zeno_session.json",
    ".zeno_owner_session.json",
    ".zeno-dev",
    "zeno_telemetry.db",
    "zeno_keys.db",
    "kavioo_lexicon.db",
    "settings.local.json",
}

SKIP_SUFFIXES = (".pyc", ".pyo", ".pyd", ".so", ".dylib", ".db", ".db-wal", ".db-shm", ".db-journal", ".sqlite", ".log")


def read_version() -> str:
    vf = ROOT / "VERSION"
    return vf.read_text(encoding="utf-8").strip() if vf.is_file() else "1.0.0"


def read_release_notes() -> str:
    nf = ROOT / "RELEASE_NOTES.txt"
    return nf.read_text(encoding="utf-8", errors="replace").strip() if nf.is_file() else ""


def write_release_notes(notes: str) -> None:
    clean = (notes or "").strip()
    if clean:
        (ROOT / "RELEASE_NOTES.txt").write_text(f"{clean}\n", encoding="utf-8")


def bump_semver(ver: str, part: str = "patch") -> str:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(.*)$", (ver or "1.0.0").strip())
    if not m:
        return ver
    maj, mino, pat = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if part == "major":
        return f"{maj + 1}.0.0"
    if part == "minor":
        return f"{maj}.{mino + 1}.0"
    return f"{maj}.{mino}.{pat + 1}"


def write_version(ver: str) -> None:
    (ROOT / "VERSION").write_text(f"{ver}\n", encoding="utf-8")
    pkg = ROOT / "app" / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            data["version"] = ver
            pkg.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except Exception:
            pass
    lock = ROOT / "app" / "package-lock.json"
    if lock.is_file():
        try:
            ldata = json.loads(lock.read_text(encoding="utf-8"))
            ldata["version"] = ver
            if isinstance(ldata.get("packages"), dict) and isinstance(ldata["packages"].get(""), dict):
                ldata["packages"][""]["version"] = ver
            lock.write_text(json.dumps(ldata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except Exception:
            pass


def git_short_sha() -> str:
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


def _should_skip(rel_posix: str, name: str) -> bool:
    if name in SKIP_NAMES or name.startswith(("._", "zeno_keys.db")):
        return True
    if name.endswith(SKIP_SUFFIXES):
        return True
    parts = rel_posix.split("/")
    if parts and parts[0] == "gold":
        return True
    if any(fnmatch.fnmatch(p, pat) for p in parts for pat in APP_EXCLUDE):
        return True
    if any(p in SKIP_NAMES or p in ("node_modules", "dist", ".git", ".venv", "work", "models", "tools") for p in parts):
        return True
    return False


MANIFEST_NAME = "_zeno_manifest.json"
# Deleted 1.x directories, docs, scripts, and tests pruned on client upgrades (1.0.x -> 2.0.0)
OBSOLETE_PATHS: Tuple[str, ...] = (
    # Deleted 1.x directories
    "agent",
    "styles",
    "config",
    "scratch",
    "gold",
    # Deleted 1.x top-level docs
    "CONCEPT.md",
    "LOW_COST_WORKFLOW.md",
    "TOKEN_LEDGER.md",
    "COST_OPTIMIZATION_REPORT.md",
    "DEBUG_REPORT.md",
    "DEVELOPING.md",
    "ONBOARDING.md",
    # Deleted 1.x scripts
    "scripts/ai_director.py",
    "scripts/zeno_mcp.py",
    "scripts/compact_run.py",
    "scripts/direct_mode.py",
    "scripts/sol_direct.py",
    "scripts/request_budget.py",
    "scripts/editorial_retrieval.py",
    "scripts/cost_control.py",
    "scripts/chat_guard.py",
    "scripts/call_cap.py",
    "scripts/hooks_setup.py",
    "scripts/launch_edit.py",
    "scripts/preproduce.py",
    "scripts/session_check.py",
    "scripts/eval_alignment.py",
    "scripts/brain_memory.py",
    "scripts/compact_run.py",
    "scripts/format_learning.py",
    "scripts/style_readiness.py",
    "scripts/team_patterns.py",
    "scripts/knowledge_sync.py",
    "scripts/editor_note.py",
    "scripts/learn_reference.py",
    # Deleted 2.x alignment assets (replaced by Meta omniASR-CTC-300M-v2 in v3.0.0)
    "scripts/align_engine/uroman",
    # Deleted 1.x tests
    "tests/test_cost_controls.py",
    "tests/test_editorial_retrieval.py",
    "tests/test_engine.py",
    "tests/test_hooks_setup.py",
    "tests/test_planner_context.py",
    "tests/test_planner_stop_routing.py",
    "tests/test_request_budget.py",
    "tests/test_token_savings.py",
    "tests/test_usage_budget_audit.py",
)


def build_bundle_bytes(version: str = "", clear_work_projects: bool = False) -> Tuple[bytes, int, str]:
    """Pack Zeno runtime, agent instructions, styles/formats, assets, desktop renderer, and _zeno_manifest.json."""
    entries: List[Tuple[str, Path]] = []

    for rel in TOP_FILES:
        p = ROOT / rel
        if p.is_file():
            entries.append((rel, p))

    for rel in APP_TOP_FILES:
        p = ROOT / rel
        if p.is_file():
            entries.append((rel, p))

    for d_rel in BUNDLE_DIRS:
        base = ROOT / d_rel
        if not base.is_dir():
            continue
        for fp in sorted(base.rglob("*")):
            if not fp.is_file():
                continue
            rel_posix = fp.relative_to(ROOT).as_posix()
            if _should_skip(rel_posix, fp.name):
                continue
            entries.append((rel_posix, fp))

    # Deduplicate while keeping deterministic order
    seen = set()
    unique_entries: List[Tuple[str, Path]] = []
    for rel_posix, fp in sorted(entries, key=lambda x: x[0]):
        if rel_posix not in seen and rel_posix != MANIFEST_NAME:
            seen.add(rel_posix)
            unique_entries.append((rel_posix, fp))

    ver = (version or read_version()).strip()
    manifest_payload = {
        "schema": 1,
        "version": ver,
        "managed_dirs": list(BUNDLE_DIRS),
        "managed_top_files": sorted(set(TOP_FILES) | set(APP_TOP_FILES)),
        "obsolete_paths": list(OBSOLETE_PATHS),
        "clear_work_projects": bool(clear_work_projects),
        "files": [rel_posix for rel_posix, _ in unique_entries],
    }
    manifest_bytes = (json.dumps(manifest_payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel_posix, fp in unique_entries:
            zi = zipfile.ZipInfo(rel_posix, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            is_exec = rel_posix.endswith((".command", ".sh")) or os.access(fp, os.X_OK)
            zi.external_attr = (0o100755 if is_exec else 0o100644) << 16
            zf.writestr(zi, fp.read_bytes())
        zi_man = zipfile.ZipInfo(MANIFEST_NAME, date_time=(2026, 1, 1, 0, 0, 0))
        zi_man.compress_type = zipfile.ZIP_DEFLATED
        zi_man.external_attr = 0o100644 << 16
        zf.writestr(zi_man, manifest_bytes)

    blob = buf.getvalue()
    sha256 = hashlib.sha256(blob).hexdigest()
    return blob, len(unique_entries) + 1, sha256


OWNER_SESSION_FILE = ROOT / ".zeno_owner_session.json"


def _load_owner_token() -> str:
    if OWNER_SESSION_FILE.is_file():
        try:
            d = json.loads(OWNER_SESSION_FILE.read_text(encoding="utf-8", errors="replace"))
            if isinstance(d, dict):
                t = str(d.get("token") or "").strip()
                if t:
                    return t
        except Exception:
            pass
    return ""


def _save_owner_session_from_active() -> None:
    src = ROOT / ".zeno_session.json"
    if not src.is_file():
        return
    try:
        raw = src.read_bytes()
        OWNER_SESSION_FILE.write_bytes(raw)
        os.chmod(OWNER_SESSION_FILE, 0o600)
    except Exception:
        pass


def _auth_headers(admin_password: str = "", prefer_owner: bool = True) -> Tuple[str, Dict[str, str]]:
    import auth_session
    try:
        import common as _common  # loads .env into os.environ safely without printing secrets
        _ = _common.ROOT
    except Exception:
        pass

    base = auth_session.get_cloud_url().rstrip("/")
    tok = (_load_owner_token() if prefer_owner else "") or auth_session.get_active_token()
    headers: Dict[str, str] = {"X-Zeno-Admin": "1"}
    pw = (admin_password or os.environ.get("ZENO_ADMIN_PASSWORD", "")).strip()
    if pw:
        headers["X-Admin-Password"] = pw
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    return base, headers


def check_live_status(admin_password: str = "") -> Dict[str, Any]:
    import httpx

    base, headers = _auth_headers(admin_password)
    if "Authorization" not in headers and "X-Admin-Password" not in headers:
        return {"ok": False, "error": "Not signed in. Run: python3 scripts/setup.py --login"}
    r = httpx.get(f"{base}/v1/version", headers=headers, timeout=15.0)
    return {"status_code": r.status_code, "cloud_url": base, "data": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text}


def publish_to_cloud(
    version: str,
    sha: str,
    blob: bytes,
    notes: str,
    mandatory: bool = False,
    dmg_url: str = "",
    exe_url: str = "",
    admin_password: str = "",
) -> Dict[str, Any]:
    import httpx

    base, headers = _auth_headers(admin_password)
    if "Authorization" not in headers and "X-Admin-Password" not in headers:
        raise RuntimeError("Not signed in to Zeno Cloud. Run: python3 scripts/setup.py --login")

    files = {"file": (f"zeno-update-{version}.zip", blob, "application/zip")}
    data = {
        "version": version,
        "sha": sha,
        "notes": notes,
        "mandatory": "1" if mandatory else "0",
        "dmg_url": dmg_url,
        "exe_url": exe_url,
    }
    resp = httpx.post(f"{base}/v1/admin/release", headers=headers, data=data, files=files, timeout=120.0)
    if resp.status_code == 403 and os.environ.get("ZENO_RELEASE_LOGIN_ON_403") == "1":
        import auth_session
        print("  [Release] Current session is not the release owner; opening browser to sign in as owner...")
        if auth_session.interactive_login(cloud_url=base, timeout_sec=180.0):
            _save_owner_session_from_active()
            base, headers = _auth_headers(admin_password, prefer_owner=True)
            resp = httpx.post(f"{base}/v1/admin/release", headers=headers, data=data, files=files, timeout=120.0)
    if resp.status_code != 200:
        raise RuntimeError(f"Cloud server returned HTTP {resp.status_code}: {resp.text[:300]}")
    if not OWNER_SESSION_FILE.is_file():
        _save_owner_session_from_active()
    return resp.json()


# Files that decide HOW Zeno edits (cuts, zooms, subtitles, banners, rules and agent instructions). They may only change in a release
# made on purpose with `--training "<reason>"`; every other release (speed, tokens, dashboard, fixes) must leave the edit pattern untouched.
EDIT_PATTERN_PATHS: Tuple[str, ...] = (
    "formats/",
    "subtitles/",
    "CLAUDE.md",
    "AGENTS.md",
    ".claude/",
    ".agent/",
    ".codex/",
    "scripts/script_order.py",
    "scripts/edl_parser.py",
    "scripts/layouts.py",
    "scripts/subtitles.py",
    "scripts/flashcard_placer.py",
    "scripts/edit_packet.py",
)


def edit_pattern_changes(base_sha: str) -> List[str]:
    """Edit-pattern files that differ between the last published commit and the working tree (empty when unknown)."""
    if not base_sha:
        return []
    names: List[str] = []
    for cmd in (["git", "diff", "--name-only", f"{base_sha}..HEAD"], ["git", "diff", "--name-only", "HEAD"]):
        try:
            r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=20.0, check=False)
        except Exception:
            continue
        if r.returncode == 0:
            names.extend(x.strip() for x in r.stdout.splitlines() if x.strip())
    hit = sorted({n for n in names if any(n == pth or (pth.endswith("/") and n.startswith(pth)) for pth in EDIT_PATTERN_PATHS)})
    return hit


def main() -> int:
    ap = argparse.ArgumentParser(description="Publish a Zeno OTA update to the Zeno Cloud server")
    ap.add_argument("notes", nargs="?", default="", help="Release notes shown to users in the update popup")
    ap.add_argument("--bump", choices=("patch", "minor", "major"), default="patch", help="Semver part to bump (default: patch)")
    ap.add_argument("--no-bump", action="store_true", help="Publish using the exact version already in VERSION")
    ap.add_argument("--version", default="", help="Set an explicit version string (e.g. 1.2.0)")
    ap.add_argument("--mandatory", action="store_true", help="Mark release as mandatory (auto-downloads & prompts immediately)")
    ap.add_argument("--dmg-url", default="", help="Optional download URL if a new macOS DMG shell is needed")
    ap.add_argument("--exe-url", default="", help="Optional download URL if a new Windows .exe shell is needed")
    ap.add_argument("--admin-password", default="", help="Optional ZENO_ADMIN_PASSWORD override")
    ap.add_argument("--dry-run", action="store_true", help="Build the release bundle locally without uploading")
    ap.add_argument("--clear-work", action="store_true", help="Instruct client updaters to also clear old work/ project caches on install (preserving work/_clips)")
    ap.add_argument("--status", action="store_true", help="Check the currently published version on Zeno Cloud")
    ap.add_argument("--training", default="", metavar="REASON", help="Allow edit-pattern files to change in this release (only when deliberately training the edit style)")
    args = ap.parse_args()

    if args.status:
        st = check_live_status(args.admin_password)
        print(json.dumps(st, indent=2, ensure_ascii=False))
        return 0 if st.get("status_code") == 200 else 1

    if not args.training.strip():
        live = check_live_status(args.admin_password) if not args.dry_run else {}
        base = str(((live or {}).get("data") or {}).get("sha") or "")
        changed = edit_pattern_changes(base)
        if changed:
            print("  [Release] BLOCKED: edit-pattern files changed since the last published release (" + base + "):")
            for name in changed:
                print("    - " + name)
            print("  The edit pattern may only change when you are training the edit. Revert these files, or publish on purpose with:")
            print('    python3 scripts/release.py --training "<what you are training>" ...')
            return 3
    elif not args.dry_run:
        print(f"  [Release] Edit-pattern change allowed (training): {args.training.strip()}")

    cur_ver = read_version()
    if args.version.strip():
        target_ver = args.version.strip()
    elif args.no_bump or args.dry_run:
        target_ver = cur_ver
    else:
        target_ver = bump_semver(cur_ver, args.bump)

    notes_file = ROOT / "RELEASE_NOTES.txt"
    prev_notes_bytes = notes_file.read_bytes() if notes_file.is_file() else None
    provided_notes = args.notes.strip()
    wrote_notes = False

    if target_ver != cur_ver and not args.dry_run:
        write_version(target_ver)
    if provided_notes and not args.dry_run:
        write_release_notes(provided_notes)
        wrote_notes = True

    blob, file_count, sha256 = build_bundle_bytes(version=target_ver, clear_work_projects=args.clear_work)
    short_sha = git_short_sha() or sha256[:7]
    mb = len(blob) / (1024 * 1024)

    print(f"  [Release] Version : v{target_ver} (sha={short_sha})")
    print(f"  [Release] Bundle  : {file_count} files, {mb:.2f} MB (sha256={sha256[:12]}...)")

    if args.dry_run:
        out_zip = ROOT / "work" / f"zeno-update-{target_ver}.zip"
        out_zip.parent.mkdir(parents=True, exist_ok=True)
        out_zip.write_bytes(blob)
        print(f"  [Release] Dry-run saved to: {out_zip}")
        return 0

    notes = provided_notes or read_release_notes() or f"Zeno v{target_ver} cloud update"
    try:
        res = publish_to_cloud(
            version=target_ver,
            sha=short_sha,
            blob=blob,
            notes=notes,
            mandatory=args.mandatory,
            dmg_url=args.dmg_url,
            exe_url=args.exe_url,
            admin_password=args.admin_password,
        )
    except Exception:
        if target_ver != cur_ver:
            write_version(cur_ver)
        if wrote_notes:
            if prev_notes_bytes is not None:
                notes_file.write_bytes(prev_notes_bytes)
            elif notes_file.is_file():
                notes_file.unlink()
        raise
    rel = res.get("release") or {}
    print(f"  [Release] ✓ Published Zeno v{rel.get('version')} to Zeno Cloud! All client apps will receive it automatically.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
