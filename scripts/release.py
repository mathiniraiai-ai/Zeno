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
    python3 scripts/release.py --dry-run "Preview bundle size"                # builds ZIP locally in scratch without uploading
    python3 scripts/release.py --status                                       # shows currently live cloud release
"""
from __future__ import annotations

import argparse
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
    "VERSION",
    "requirements.txt",
    "AGENTS.md",
    "CLAUDE.md",
    "ONBOARDING.md",
    "README.md",
    "LICENSE",
    "LICENSES.md",
    ".env.example",
    "install.command",
    "install.bat",
    "update.command",
    "update.bat",
    "edit.command",
    "edit.bat",
)

BUNDLE_DIRS = (
    ".agent",
    ".claude",
    "agent",
    "scripts",
    "styles",
    "subtitles",
    "assets",
    "tests",
    "app/renderer",
)

APP_TOP_FILES = (
    "app/package.json",
    "app/main.js",
    "app/preload.js",
    "app/README.md",
)

SKIP_NAMES = {
    ".DS_Store",
    "Thumbs.db",
    "__pycache__",
    ".env",
    ".zeno_session.json",
    ".zeno-dev",
    "zeno_telemetry.db",
    "zeno_keys.db",
}

SKIP_SUFFIXES = (".pyc", ".pyo", ".pyd", ".so", ".dylib", ".db", ".sqlite", ".log")


def read_version() -> str:
    vf = ROOT / "VERSION"
    return vf.read_text(encoding="utf-8").strip() if vf.is_file() else "1.0.0"


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
    if name in SKIP_NAMES or name.startswith("._"):
        return True
    if name.endswith(SKIP_SUFFIXES):
        return True
    parts = rel_posix.split("/")
    if any(p in SKIP_NAMES or p in ("node_modules", "dist", ".git", ".venv", "work", "models", "tools") for p in parts):
        return True
    return False


def build_bundle_bytes() -> Tuple[bytes, int, str]:
    """Pack Zeno runtime, agent instructions, styles, assets, and desktop renderer into a deterministic ZIP."""
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
        if rel_posix not in seen:
            seen.add(rel_posix)
            unique_entries.append((rel_posix, fp))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel_posix, fp in unique_entries:
            zi = zipfile.ZipInfo(rel_posix, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            is_exec = rel_posix.endswith((".command", ".sh")) or os.access(fp, os.X_OK)
            zi.external_attr = (0o100755 if is_exec else 0o100644) << 16
            zf.writestr(zi, fp.read_bytes())

    blob = buf.getvalue()
    sha256 = hashlib.sha256(blob).hexdigest()
    return blob, len(unique_entries), sha256


def _auth_headers(admin_password: str = "") -> Tuple[str, Dict[str, str]]:
    import auth_session

    base = auth_session.get_cloud_url().rstrip("/")
    tok = auth_session.get_active_token()
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
    if resp.status_code != 200:
        raise RuntimeError(f"Cloud server returned HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


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
    ap.add_argument("--status", action="store_true", help="Check the currently published version on Zeno Cloud")
    args = ap.parse_args()

    if args.status:
        st = check_live_status(args.admin_password)
        print(json.dumps(st, indent=2, ensure_ascii=False))
        return 0 if st.get("status_code") == 200 else 1

    cur_ver = read_version()
    if args.version.strip():
        target_ver = args.version.strip()
    elif args.no_bump or args.dry_run:
        target_ver = cur_ver
    else:
        target_ver = bump_semver(cur_ver, args.bump)

    if target_ver != cur_ver and not args.dry_run:
        write_version(target_ver)

    blob, file_count, sha256 = build_bundle_bytes()
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

    notes = args.notes.strip() or f"Zeno v{target_ver} cloud update"
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
    rel = res.get("release") or {}
    print(f"  [Release] ✓ Published Zeno v{rel.get('version')} to Zeno Cloud! All client apps will receive it automatically.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
