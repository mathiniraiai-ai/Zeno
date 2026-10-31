#!/usr/bin/env python3
"""Save ONE script to input/script/<name>.txt: a single Google Doc tab or heading, or a local file.

Usage:
    python3 scripts/script_intake.py "<google-doc-link>" --name <project>
    python3 scripts/script_intake.py input/script/raw.docx --name <project>

A Google Doc link is read only as far as it points: `?tab=t.xxx` (one tab) or `#heading=h.xxx` (one section).
Document text goes to disk, never to stdout, so the agent reads just the saved file.
"""
import argparse
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, read_script_text  # noqa: E402

DOC_RE = re.compile(r"docs\.google\.com/document/(?:u/\d+/)?d/([A-Za-z0-9_-]+)")
HEADING_RE = re.compile(r"h([1-6])")
BLOCK_TAGS = {"p", "li", "tr", "div", "h1", "h2", "h3", "h4", "h5", "h6"}


def parse_doc_url(url: str) -> dict:
    """Return {'doc_id', 'tab', 'heading'} for a Google Docs link (tab/heading may be None)."""
    m = DOC_RE.search(url)
    if not m:
        raise ValueError("Not a Google Docs link")
    parts = urllib.parse.urlsplit(url)
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
        self.buf: list = []
        self.lines: list = []

    def _flush(self):
        text = "".join(self.buf).strip()
        if text:
            self.lines.append(text)
        self.buf = []

    def handle_starttag(self, tag, attrs):
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

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self.skip = max(0, self.skip - 1)
            return
        if HEADING_RE.fullmatch(tag) and self.in_target_heading:
            self.in_target_heading = False
            self.buf = []           # drop the heading's own text
            return
        if self.capturing and tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self.capturing and not self.skip:
            self.buf.append(data)


def slice_html_by_heading(html_text: str, heading_id: str) -> str:
    parser = _HeadingSlicer(heading_id)
    parser.feed(html_text)
    parser._flush()
    return "\n".join(parser.lines)


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _export(doc_id: str, fmt: str, tab: str | None) -> str:
    url = f"https://docs.google.com/document/d/{doc_id}/export?format={fmt}"
    if tab:
        url += "&tab=" + urllib.parse.quote(tab)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Zeno script intake)"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
            final_url = resp.geturl()
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Google returned HTTP {exc.code}. Check the link and that sharing is 'anyone with the link can view'.")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Could not reach Google Docs: {exc.reason}")
    if "accounts.google.com" in final_url:
        raise SystemExit("The document is private. Ask the user to share it as 'anyone with the link can view'.")
    return body


def fetch_google_doc(url: str) -> tuple[str, str]:
    """Return (text, note). Reads only the linked tab or heading."""
    info = parse_doc_url(url)
    if info["heading"]:
        html_text = _export(info["doc_id"], "html", info["tab"])
        text = slice_html_by_heading(html_text, info["heading"])
        if not text.strip():
            raise SystemExit("Heading not found in that document or tab. Check the link points at the right section.")
        return text, f"tab={info['tab'] or '-'} heading={info['heading']}"
    text = _export(info["doc_id"], "txt", info["tab"]).lstrip("﻿")
    if info["tab"]:
        return text, f"tab={info['tab']}"
    return text, "WHOLE-DOCUMENT (link has no tab or heading)"


def main():
    ap = argparse.ArgumentParser(description="Save one script (a single Google Doc tab/heading or local file) to input/script/")
    ap.add_argument("source", help="Google Doc link or local .txt/.md/.docx path")
    ap.add_argument("--name", help="Output name (default: doc id or file name)")
    args = ap.parse_args()

    out_dir = ROOT / "input" / "script"
    out_dir.mkdir(parents=True, exist_ok=True)

    if DOC_RE.search(args.source):
        info = parse_doc_url(args.source)
        text, note = fetch_google_doc(args.source)
        name = args.name or info["doc_id"][:12]
        header = f"# source: google-doc {info['doc_id']} {note}\n"
    else:
        src = Path(args.source)
        if not src.is_file():
            raise SystemExit(f"File not found: {src}")
        text, note = read_script_text(src), "local file"
        name = args.name or src.stem
        header = ""

    out = out_dir / f"{name}.txt"
    out.write_text(header + text.strip() + "\n", encoding="utf-8")
    lines = [l for l in text.splitlines() if l.strip()]
    print(f"Saved {out.relative_to(ROOT)} ({len(lines)} lines, {len(text)} chars) from {note}")
    if note.startswith("WHOLE-DOCUMENT"):
        print("WARNING: no tab or heading in the link, so the whole document was saved. "
              "Ask the user which tab holds this script, and do not read the rest.")


if __name__ == "__main__":
    main()
