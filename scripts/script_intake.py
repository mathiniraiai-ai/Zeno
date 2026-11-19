#!/usr/bin/env python3
"""Google Docs (`?tab=...` / `#heading=...`) & local script intake for Zeno (`scripts/script_intake.py`).

Usage:
    python3 scripts/script_intake.py "<google-doc-link>" --name <project>
    python3 scripts/script_intake.py input/script/raw.docx --name <project>

A Google Doc URL is exported strictly for its linked tab (`?tab=t.xxx`) or section (`#heading=h.xxx`),
never dumping multi-tab documents silently. Provides `resolve_script_input()` for programmatic callers.
"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

import argparse
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, read_script_text, resolve  # noqa: E402

DOC_RE = re.compile(r"docs\.google\.com/document/(?:u/\d+/)?d/([A-Za-z0-9_-]+)")
GDOC_RE = DOC_RE
HEADING_RE = re.compile(r"h([1-6])")
BLOCK_TAGS = {"p", "li", "tr", "div", "h1", "h2", "h3", "h4", "h5", "h6"}


def is_google_doc_url(value: str) -> bool:
    """Return True if `value` looks like a Google Docs URL."""
    return bool(DOC_RE.search(str(value or "").strip()))


def parse_doc_url(url: str) -> Dict[str, Optional[str]]:
    """Return `{'doc_id', 'tab', 'heading'}` for a Google Docs link (`tab`/`heading` may be None)."""
    m = DOC_RE.search(str(url or "").strip())
    if not m:
        raise ValueError("Not a Google Docs link")
    parts = urllib.parse.urlsplit(str(url).strip())
    query = urllib.parse.parse_qs(parts.query)
    fragment = urllib.parse.parse_qs(parts.fragment)
    return {
        "doc_id": m.group(1),
        "tab": (query.get("tab") or [None])[0],
        "heading": (fragment.get("heading") or [None])[0],
    }


class _HeadingSlicer(HTMLParser):
    """Collect the text under one heading id, up to the next heading of the same or higher level."""

    def __init__(self, heading_id: str):
        super().__init__(convert_charrefs=True)
        self.target = heading_id
        self.level = 0
        self.capturing = False
        self.done = False
        self.in_target_heading = False
        self.skip = 0
        self.buf: list[str] = []
        self.lines: list[str] = []

    def _flush(self) -> None:
        text = "".join(self.buf).strip()
        if text:
            self.lines.append(text)
        self.buf = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("style", "script"):
            self.skip += 1
            return
        if self.done:
            return
        m = HEADING_RE.fullmatch(tag)
        if m:
            level = int(m.group(1))
            if self.capturing and level <= self.level:
                self._flush()
                self.capturing = False
                self.done = True
                return
            if dict(attrs).get("id") == self.target:
                self.capturing = True
                self.level = level
                self.in_target_heading = True
                self.buf = []
        elif tag == "br" and self.capturing:
            self.buf.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "script"):
            self.skip = max(0, self.skip - 1)
            return
        if HEADING_RE.fullmatch(tag) and self.in_target_heading:
            self.in_target_heading = False
            self.buf = []
            return
        if self.capturing and tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self.capturing and not self.skip:
            self.buf.append(data)


def slice_html_by_heading(html_text: str, heading_id: str) -> str:
    """Extract plain text belonging to a single heading section from exported Google Docs HTML."""
    parser = _HeadingSlicer(heading_id)
    parser.feed(html_text)
    parser._flush()
    return "\n".join(parser.lines)


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _export(doc_id: str, fmt: str, tab: Optional[str]) -> str:
    url = f"https://docs.google.com/document/d/{doc_id}/export?format={fmt}"
    if tab:
        url += "&tab=" + urllib.parse.quote(tab)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Zeno Script Intake)"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
            final_url = resp.geturl()
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Google Docs returned HTTP {exc.code}. Verify the link and ensure sharing is set to 'Anyone with the link can view'."
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Google Docs: {exc.reason}") from exc
    if "accounts.google.com" in final_url:
        raise RuntimeError("The Google Doc is private. Share it as 'Anyone with the link can view'.")
    return body


def fetch_google_doc(url: str) -> Tuple[str, str]:
    """Export text and source note `(text, note)` for a Google Doc tab or heading."""
    info = parse_doc_url(url)
    doc_id = str(info["doc_id"])
    if info["heading"]:
        html_text = _export(doc_id, "html", info["tab"])
        text = slice_html_by_heading(html_text, info["heading"])
        if not text.strip():
            raise RuntimeError("Heading not found in that Google Doc or tab. Check that the link points to the right section.")
        return text, f"tab={info['tab'] or '-'} heading={info['heading']}"
    text = _export(doc_id, "txt", info["tab"]).lstrip("\ufeff")
    if info["tab"]:
        return text, f"tab={info['tab']}"
    return text, "WHOLE-DOCUMENT (link has no tab or heading)"


def resolve_script_input(
    script_arg: str | Path,
    name: Optional[str] = None,
    allow_whole_doc: bool = False,
) -> Path:
    """Resolve a local script file (`.txt`/`.md`/`.docx`) or download one Google Doc tab/heading to `input/script/`."""
    s = str(script_arg or "").strip()
    if not s:
        raise ValueError("Empty script path or URL")

    if not is_google_doc_url(s):
        p = resolve(s)
        if not p.is_file():
            raise FileNotFoundError(f"Script file not found: {s}")
        return p

    info = parse_doc_url(s)
    doc_id = str(info["doc_id"])
    if not allow_whole_doc and not info.get("tab") and not info.get("heading"):
        raise ValueError(
            "This Google Doc link has no ?tab=... or #heading=... parameter, so it would load every tab in the document. "
            "Open the tab holding this script and share that URL (ending with ?tab=t.xxxx)."
        )

    text, _note = fetch_google_doc(s)
    if len(text.strip()) < 10:
        raise RuntimeError("The linked Google Doc tab or section is empty.")

    out_dir = ROOT / "input" / "script"
    out_dir.mkdir(parents=True, exist_ok=True)
    if name:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or f"gdoc_{doc_id[:12]}"
        dest = out_dir / (safe_name if safe_name.endswith(".txt") else f"{safe_name}.txt")
    else:
        key = re.sub(r"[^A-Za-z0-9]+", "_", info.get("tab") or info.get("heading") or "doc").strip("_")[:24]
        dest = out_dir / f"gdoc_{doc_id[:12]}_{key}.txt"

    dest.write_text(text.replace("\r\n", "\n").strip() + "\n", encoding="utf-8")
    return dest


def save_script(source: str, name: Optional[str] = None) -> Tuple[Path, str, int]:
    """Save a Google Doc link or local script file into `input/script/<name>.txt`, returning `(path, note, line_count)`."""
    out_dir = ROOT / "input" / "script"
    out_dir.mkdir(parents=True, exist_ok=True)

    if is_google_doc_url(source):
        info = parse_doc_url(source)
        text, note = fetch_google_doc(source)
        out_name = name or str(info["doc_id"])[:12]
        header = f"# source: google-doc {info['doc_id']} {note}\n"
    else:
        src = resolve(source)
        if not src.is_file():
            raise FileNotFoundError(f"File not found: {source}")
        text, note = read_script_text(src), "local file"
        out_name = name or src.stem
        header = ""

    out = out_dir / f"{out_name}.txt"
    out.write_text(header + text.strip() + "\n", encoding="utf-8")
    non_empty_lines = sum(1 for ln in text.splitlines() if ln.strip())
    return out, note, non_empty_lines


def main() -> None:
    ap = argparse.ArgumentParser(description="Save one script (Google Doc tab/heading or local file) to input/script/")
    ap.add_argument("source", help="Google Doc link or local .txt/.md/.docx path")
    ap.add_argument("--name", help="Output base filename under input/script/ (default: doc id or file stem)")
    args = ap.parse_args()

    try:
        out, note, line_count = save_script(args.source, name=args.name)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc

    char_count = len(out.read_text(encoding="utf-8", errors="replace"))
    print(f"Saved {out.relative_to(ROOT)} ({line_count} lines, {char_count} chars) from {note}")
    if note.startswith("WHOLE-DOCUMENT"):
        print(
            "WARNING: no ?tab=... or #heading=... in the link, so the entire document was exported. "
            "Confirm which tab holds the target script before editing."
        )


if __name__ == "__main__":
    main()
