"""Zero-Touch Session Token Grabber & Auto-Auth Manager for Zeno.

Handles automatic browser-based Google OAuth 2.0 login with localhost callback capture
(`127.0.0.1:8765/callback`), persisting the user's personal Zeno session token inside
`.zeno_session.json` (chmod 0600, git-ignored) and syncing `ZENO_CLOUD_TOKEN` /
`SCRIPTCUT_CLOUD_TOKEN` into `os.environ` and `.env` so editors never have to copy-paste
tokens manually.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
SESSION_FILE = ROOT / ".zeno_session.json"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE_FILE = ROOT / ".env.example"
DEFAULT_CLOUD_URL = "https://zeno-production-bef7.up.railway.app"


def _is_placeholder(val: str) -> bool:
    v = (val or "").strip()
    if not v:
        return True
    low = v.lower()
    return low.startswith("your_") or low in ("none", "null", "changeme", "<token>")


def get_cloud_url(default: str = DEFAULT_CLOUD_URL) -> str:
    """Resolve the active Zeno Cloud service URL."""
    for env_key in ("ZENO_CLOUD_URL", "SCRIPTCUT_CLOUD_URL"):
        val = os.environ.get(env_key, "").strip()
        if val and not _is_placeholder(val):
            return val.rstrip("/")
    if SESSION_FILE.is_file():
        try:
            data = json.loads(SESSION_FILE.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                url = str(data.get("cloud_url") or "").strip()
                if url and not _is_placeholder(url):
                    return url.rstrip("/")
        except Exception:
            pass
    return default.rstrip("/")


def _read_clipboard_zeno_token() -> str:
    """If the OS clipboard currently holds a `zeno_...` session token, return it."""
    if os.environ.get("ZENO_NO_LIVE") == "1":
        return ""
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=1.0, check=False)
            raw = (r.stdout or "").strip()
        elif sys.platform.startswith("win") or os.name == "nt":
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            raw = (r.stdout or "").strip()
        else:
            return ""
        m = re.search(r"\b(zeno_[A-Za-z0-9_-]{24,80})\b", raw)
        return m.group(1) if m else ""
    except Exception:
        return ""


def ingest_clipboard_token_if_present(session_path: Optional[Path] = None, write_dotenv: bool = True) -> Optional[Dict[str, Any]]:
    """If a new `zeno_...` token is in the clipboard (auto-copied by the browser on Google Sign-In), save it to `.env` & `.zeno_session.json`."""
    clip_tok = _read_clipboard_zeno_token()
    if not clip_tok:
        return None
    target = Path(session_path) if session_path else SESSION_FILE
    if target.is_file():
        try:
            existing = json.loads(target.read_text(encoding="utf-8", errors="replace"))
            if isinstance(existing, dict) and existing.get("token") == clip_tok:
                sync_session_to_env(existing, write_dotenv=write_dotenv and (session_path is None))
                return existing
        except Exception:
            pass
    cloud_url = get_cloud_url()
    email = ""
    name = ""
    try:
        req = urllib.request.Request(
            f"{cloud_url}/v1/me",
            headers={"Authorization": f"Bearer {clip_tok}"},
        )
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            if resp.status == 200:
                me = json.loads(resp.read().decode("utf-8", errors="replace"))
                email = str(me.get("email") or "").strip()
                name = str(me.get("name") or "").strip()
    except Exception:
        pass
    return save_session(
        token=clip_tok,
        email=email,
        name=name,
        cloud_url=cloud_url,
        session_path=session_path,
        write_dotenv=write_dotenv,
    )


def get_active_session(session_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the active session from `.zeno_session.json`, clipboard auto-ingest, cloud IP-poll, or environment variables."""
    target = Path(session_path) if session_path else SESSION_FILE
    is_default_target = (session_path is None) and (target == SESSION_FILE)
    live_allowed = os.environ.get("ZENO_NO_LIVE") != "1" and os.environ.get("CLAUDE_EDITOR_OFFLINE") != "1"
    if is_default_target and live_allowed:
        try:
            ensure_background_daemon_running(8765)
            ingest_clipboard_token_if_present(session_path=None, write_dotenv=True)
        except Exception:
            pass
    if target.is_file():
        try:
            data = json.loads(target.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                tok = str(data.get("token") or "").strip()
                if tok and not _is_placeholder(tok):
                    return {
                        "token": tok,
                        "email": str(data.get("email") or "").strip(),
                        "name": str(data.get("name") or "").strip(),
                        "cloud_url": str(data.get("cloud_url") or DEFAULT_CLOUD_URL).strip().rstrip("/"),
                        "authenticated_at": str(data.get("authenticated_at") or "").strip(),
                        "source": str(target.name),
                    }
        except Exception:
            pass

    tok = (
        os.environ.get("ZENO_CLOUD_TOKEN", "").strip()
        or os.environ.get("SCRIPTCUT_CLOUD_TOKEN", "").strip()
    )
    if tok and not _is_placeholder(tok):
        return {
            "token": tok,
            "email": os.environ.get("ZENO_USER_EMAIL", "").strip(),
            "name": os.environ.get("ZENO_USER_NAME", "").strip(),
            "cloud_url": get_cloud_url(),
            "authenticated_at": "",
            "source": "env",
        }

    if is_default_target and live_allowed:
        try:
            polled = _poll_cloud_cli_session(get_cloud_url(), "", use_ip_fallback=True)
            if polled and polled.get("token"):
                saved = save_session(
                    token=polled["token"],
                    email=polled.get("email") or "",
                    name=polled.get("name") or "",
                    cloud_url=polled.get("cloud_url") or get_cloud_url(),
                    write_dotenv=True,
                )
                saved["source"] = str(SESSION_FILE.name)
                return saved
        except Exception:
            pass
    return {}


def load_session(session_path: Optional[Path] = None) -> Dict[str, Any]:
    """Alias for `get_active_session` that also injects loaded values into `os.environ`."""
    sess = get_active_session(session_path=session_path)
    if sess.get("token"):
        sync_session_to_env(sess, write_dotenv=False)
    return sess


def get_active_token(session_path: Optional[Path] = None) -> str:
    """Return the active Zeno Cloud token string, or empty string if unauthenticated."""
    sess = get_active_session(session_path=session_path)
    return str(sess.get("token") or "").strip()


def get_logged_in_email(session_path: Optional[Path] = None) -> str:
    """Return the authenticated user's email address if known."""
    sess = get_active_session(session_path=session_path)
    return str(sess.get("email") or os.environ.get("ZENO_USER_EMAIL") or "").strip()


def get_session_info(session_path: Optional[Path] = None) -> Dict[str, Any]:
    """Return non-secret session metadata suitable for CLI status display (`--whoami`)."""
    sess = get_active_session(session_path=session_path)
    tok = str(sess.get("token") or "")
    has_gemini = bool(
        os.environ.get("GEMINI_API_KEY", "").strip()
        and not _is_placeholder(os.environ.get("GEMINI_API_KEY", ""))
    )
    return {
        "authenticated": bool(tok or has_gemini),
        "mode": "zeno_cloud" if tok else ("gemini_direct" if has_gemini else "none"),
        "email": sess.get("email") or "",
        "name": sess.get("name") or "",
        "token_prefix": (tok[:12] + "...") if len(tok) > 12 else ("set" if tok else ""),
        "cloud_url": sess.get("cloud_url") or get_cloud_url(),
        "authenticated_at": sess.get("authenticated_at") or "",
        "source": sess.get("source") or ("GEMINI_API_KEY" if has_gemini else "none"),
    }


def _upsert_env_key(lines: list[str], key: str, value: str) -> list[str]:
    pattern = f"{key}="
    replaced = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(pattern) or stripped.startswith(f"export {pattern}"):
            if not replaced:
                out.append(f"{key}={value}")
                replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    return out


def sync_session_to_env(
    session: Dict[str, Any],
    write_dotenv: bool = False,
    dotenv_path: Optional[Path] = None,
) -> None:
    """Mirror session variables into `os.environ` and optionally update `.env` in-place."""
    token = str(session.get("token") or "").strip()
    cloud_url = str(session.get("cloud_url") or get_cloud_url()).strip().rstrip("/")
    email = str(session.get("email") or "").strip()
    name = str(session.get("name") or "").strip()

    if token:
        os.environ["ZENO_CLOUD_TOKEN"] = token
        os.environ["SCRIPTCUT_CLOUD_TOKEN"] = token
    if cloud_url:
        os.environ["ZENO_CLOUD_URL"] = cloud_url
        os.environ["SCRIPTCUT_CLOUD_URL"] = cloud_url
    if email:
        os.environ["ZENO_USER_EMAIL"] = email
    if name:
        os.environ["ZENO_USER_NAME"] = name

    if not write_dotenv or not token:
        return

    env_target = Path(dotenv_path) if dotenv_path else ENV_FILE
    try:
        if not env_target.is_file():
            if ENV_EXAMPLE_FILE.is_file() and env_target == ENV_FILE:
                lines = ENV_EXAMPLE_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            else:
                lines = ["# Zeno Environment Configuration (auto-generated)"]
        else:
            lines = env_target.read_text(encoding="utf-8", errors="replace").splitlines()

        lines = _upsert_env_key(lines, "ZENO_CLOUD_URL", cloud_url)
        lines = _upsert_env_key(lines, "ZENO_CLOUD_TOKEN", token)
        lines = _upsert_env_key(lines, "SCRIPTCUT_CLOUD_URL", cloud_url)
        lines = _upsert_env_key(lines, "SCRIPTCUT_CLOUD_TOKEN", token)
        if email:
            lines = _upsert_env_key(lines, "ZENO_USER_EMAIL", email)
        if name:
            lines = _upsert_env_key(lines, "ZENO_USER_NAME", name)

        env_target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            os.chmod(env_target, 0o600)
        except OSError:
            pass
    except OSError:
        pass


def save_session(
    token: str,
    email: str = "",
    name: str = "",
    cloud_url: str = "",
    session_path: Optional[Path] = None,
    write_dotenv: bool = True,
    dotenv_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Save the session token & identity to `.zeno_session.json` (0600) and sync to `.env`."""
    clean_tok = (token or "").strip()
    if not clean_tok:
        raise ValueError("Cannot save empty session token")

    resolved_url = (cloud_url or get_cloud_url()).strip().rstrip("/")
    payload = {
        "token": clean_tok,
        "email": (email or "").strip().lower(),
        "name": (name or "").strip(),
        "cloud_url": resolved_url,
        "authenticated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    target = Path(session_path) if session_path else SESSION_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass

    sync_session_to_env(
        payload,
        write_dotenv=write_dotenv and (session_path is None),
        dotenv_path=dotenv_path,
    )
    return payload


def logout(session_path: Optional[Path] = None, dotenv_path: Optional[Path] = None) -> None:
    """Remove `.zeno_session.json` and clear cloud token variables from `os.environ` and `.env`."""
    target = Path(session_path) if session_path else SESSION_FILE
    if target.is_file():
        try:
            target.unlink()
        except OSError:
            pass
    for k in ("ZENO_CLOUD_TOKEN", "SCRIPTCUT_CLOUD_TOKEN", "ZENO_USER_EMAIL", "ZENO_USER_NAME"):
        os.environ.pop(k, None)

    env_target = Path(dotenv_path) if dotenv_path else ENV_FILE
    if env_target.is_file():
        try:
            lines = env_target.read_text(encoding="utf-8", errors="replace").splitlines()
            lines = _upsert_env_key(lines, "ZENO_CLOUD_TOKEN", "")
            lines = _upsert_env_key(lines, "SCRIPTCUT_CLOUD_TOKEN", "")
            env_target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass


def _build_confirmation_html(email: str, name: str) -> bytes:
    who = html.escape(email or name or "Supernova Editor")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Signed in - Google Accounts · Zeno</title>
<style>
  body {{ font-family: "Google Sans", Roboto, -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
         background: #f0f4f9; color: #1f1f1f; display: flex; align-items: center;
         justify-content: center; min-height: 92vh; margin: 0; padding: 24px; }}
  .card {{ background: #ffffff; border-radius: 28px;
          padding: 36px 40px; max-width: 520px; width: 100%; }}
  .badge {{ display: inline-flex; align-items: center; gap: 6px; background: #e6f4ea; color: #137333;
           font-weight: 500; padding: 6px 14px; border-radius: 999px; margin-bottom: 16px; font-size: 13px; }}
  h1 {{ font-size: 28px; font-weight: 400; margin: 0 0 10px; color: #1f1f1f; }}
  p {{ color: #444746; line-height: 1.5; font-size: 14px; margin: 8px 0; }}
  code {{ color: #0b57d0; background: #f0f4f9; padding: 2px 6px; border-radius: 4px; }}
</style></head>
<body><div class="card">
  <div class="badge">✓ Signed in &amp; saved to .env</div>
  <h1>Welcome, {who}</h1>
  <p>Your Zeno session has been automatically saved to your local <code>.env</code> file.</p>
  <p>This tab will close automatically…</p>
</div>
<script>
try {{ window.history.replaceState({{}}, "", "/callback"); }} catch (e) {{}}
setTimeout(() => {{ try {{ window.open('', '_self', ''); window.close(); }} catch (e) {{}} }}, 800);
</script>
</body></html>"""
    return page.encode("utf-8")


class _ReuseHTTPServer(HTTPServer):
    allow_reuse_address = True
    auth_result: Optional[Dict[str, str]] = None


def _make_callback_handler():
    class _CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            # Never log callback URLs containing session tokens to stdout/stderr
            pass

        def _send_cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Cache-Control", "no-store")

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._send_cors_headers()
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in ("/health", "/ping"):
                body = b'{"ok":true}'
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            qs = urllib.parse.parse_qs(parsed.query)
            token = (qs.get("token") or qs.get("key") or [""])[0].strip()
            email = (qs.get("email") or [""])[0].strip()
            name = (qs.get("name") or [""])[0].strip()
            cloud_url = (qs.get("cloud_url") or [""])[0].strip()

            if token:
                self.server.auth_result = {  # type: ignore[attr-defined]
                    "token": token,
                    "email": email,
                    "name": name,
                    "cloud_url": cloud_url,
                }
                body = _build_confirmation_html(email, name)
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            msg = b"Waiting for Zeno Cloud Google Sign-In callback..."
            self.send_response(400)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(max(0, min(length, 65536))).decode("utf-8", errors="replace")
            data: Dict[str, Any] = {}
            if raw.strip().startswith("{"):
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {}
            else:
                qs = urllib.parse.parse_qs(raw)
                data = {k: v[0] for k, v in qs.items() if v}

            token = str(data.get("token") or data.get("key") or "").strip()
            email = str(data.get("email") or "").strip()
            name = str(data.get("name") or "").strip()
            cloud_url = str(data.get("cloud_url") or "").strip()

            if not token:
                err = b'{"ok":false,"error":"missing token"}'
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
                return

            self.server.auth_result = {  # type: ignore[attr-defined]
                "token": token,
                "email": email,
                "name": name,
                "cloud_url": cloud_url,
            }
            resp = json.dumps({"ok": True, "email": email, "name": name}).encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

    return _CallbackHandler


def _open_url_in_browser(url: str) -> bool:
    try:
        if webbrowser.open(url, new=1, autoraise=True):
            return True
    except Exception:
        pass
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if sys.platform.startswith("win") or os.name == "nt":
            subprocess.Popen(["cmd", "/c", "start", "", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except Exception:
        pass
    return False


def start_callback_server(preferred_port: int = 8765) -> Tuple[_ReuseHTTPServer, int]:
    """Start the localhost callback HTTP server on `127.0.0.1:preferred_port` (or an ephemeral port)."""
    handler = _make_callback_handler()
    try:
        srv = _ReuseHTTPServer(("127.0.0.1", preferred_port), handler)
    except OSError:
        srv = _ReuseHTTPServer(("127.0.0.1", 0), handler)
    srv.timeout = 0.25
    srv.auth_result = None
    return srv, int(srv.server_port)


def _poll_cloud_cli_session(base_url: str, cli_state: str, use_ip_fallback: bool = True) -> Optional[Dict[str, str]]:
    """Poll Railway `/api/auth/cli-poll` over HTTPS so login succeeds even if browser blocks localhost fetches."""
    try:
        qs = urllib.parse.urlencode({"cli_state": cli_state, "use_ip_fallback": "1" if use_ip_fallback else "0"})
        url = f"{base_url}/api/auth/cli-poll?{qs}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
                if isinstance(data, dict) and data.get("ready") and data.get("token"):
                    return {
                        "token": str(data["token"]).strip(),
                        "email": str(data.get("email") or "").strip(),
                        "name": str(data.get("name") or "").strip(),
                        "cloud_url": base_url,
                    }
    except Exception:
        pass
    return None


def grab_token_via_browser(
    cloud_url: str = "",
    timeout_sec: float = 180.0,
    open_browser: bool = True,
    preferred_port: int = 8765,
    session_path: Optional[Path] = None,
    write_dotenv: bool = True,
) -> Optional[Dict[str, Any]]:
    """Launch the browser to the Railway login page, wait for HTTPS cli-poll / localhost callback / clipboard, and save to `.env`."""
    base_url = (cloud_url or get_cloud_url()).rstrip("/")
    cli_state = "st_" + secrets.token_urlsafe(18)
    srv, port = start_callback_server(preferred_port=preferred_port)
    login_url = f"{base_url}/?cli_state={cli_state}&callback_port={port}&cli=1"

    print(f"  [Zeno Auth] Opening browser for Google Sign-In (@gosupernova.live): {login_url}")
    if open_browser:
        _open_url_in_browser(login_url)

    deadline = time.monotonic() + float(timeout_sec)
    last_poll = 0.0
    try:
        while time.monotonic() < deadline:
            srv.handle_request()
            res: Optional[Dict[str, str]] = None
            if srv.auth_result and srv.auth_result.get("token"):
                res = srv.auth_result
            elif time.monotonic() - last_poll >= 1.0:
                last_poll = time.monotonic()
                res = _poll_cloud_cli_session(base_url, cli_state, use_ip_fallback=True)
                if not res:
                    clip_saved = ingest_clipboard_token_if_present(session_path=session_path, write_dotenv=write_dotenv)
                    if clip_saved and clip_saved.get("token"):
                        who = clip_saved.get("email") or clip_saved.get("name") or "authenticated user"
                        print(f"  [Zeno Auth] ✓ Session token auto-saved to .env for {who}")
                        return clip_saved

            if res and res.get("token"):
                saved = save_session(
                    token=res["token"],
                    email=res.get("email") or "",
                    name=res.get("name") or "",
                    cloud_url=res.get("cloud_url") or base_url,
                    session_path=session_path,
                    write_dotenv=write_dotenv,
                )
                try:
                    srv.handle_request()
                except Exception:
                    pass
                who = saved.get("email") or saved.get("name") or "authenticated user"
                print(f"  [Zeno Auth] ✓ Session token grabbed & saved automatically to .env for {who}")
                return saved
    finally:
        try:
            srv.server_close()
        except Exception:
            pass
    return None


def run_background_callback_daemon(port: int = 8765) -> None:
    """Always-on lightweight localhost listener on `127.0.0.1:8765` that saves any browser sign-in token straight into `.env`."""
    try:
        srv, actual_port = start_callback_server(preferred_port=port)
    except Exception:
        return
    if actual_port != port:
        try:
            srv.server_close()
        except Exception:
            pass
        return
    srv.timeout = 1.0
    last_bg_check = 0.0
    while True:
        try:
            srv.handle_request()
            if srv.auth_result and srv.auth_result.get("token"):
                res = srv.auth_result
                srv.auth_result = None
                save_session(
                    token=res["token"],
                    email=res.get("email") or "",
                    name=res.get("name") or "",
                    cloud_url=res.get("cloud_url") or get_cloud_url(),
                    write_dotenv=True,
                )
            elif time.monotonic() - last_bg_check >= 3.0:
                last_bg_check = time.monotonic()
                ingest_clipboard_token_if_present(session_path=None, write_dotenv=True)
                if not SESSION_FILE.is_file():
                    polled = _poll_cloud_cli_session(get_cloud_url(), "", use_ip_fallback=True)
                    if polled and polled.get("token"):
                        save_session(
                            token=polled["token"],
                            email=polled.get("email") or "",
                            name=polled.get("name") or "",
                            cloud_url=polled.get("cloud_url") or get_cloud_url(),
                            write_dotenv=True,
                        )
        except KeyboardInterrupt:
            break
        except Exception:
            time.sleep(0.5)


def ensure_background_daemon_running(port: int = 8765) -> None:
    """Ensure the `127.0.0.1:8765` callback listener is running in the background so any browser sign-in saves to `.env`."""
    if os.environ.get("ZENO_NO_LIVE") == "1":
        return
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=0.35) as resp:
            if resp.status == 200:
                return
    except Exception:
        pass
    try:
        kwargs: Dict[str, Any] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
            "cwd": str(ROOT),
        }
        if sys.platform.startswith("win") or os.name == "nt":
            kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--daemon"], **kwargs)
    except Exception:
        pass


def interactive_login(cloud_url: str = "", timeout_sec: float = 180.0) -> bool:
    """Run browser-based zero-copy login and return True if a session token was saved."""
    saved = grab_token_via_browser(cloud_url=cloud_url, timeout_sec=timeout_sec, open_browser=True)
    return bool(saved and saved.get("token"))


def ensure_authenticated(
    interactive: bool = True,
    force_refresh: bool = False,
    cloud_url: str = "",
    timeout_sec: float = 180.0,
) -> Optional[Dict[str, Any]]:
    """Ensure a valid Zeno Cloud session or GEMINI_API_KEY exists; trigger browser login if needed."""
    if not force_refresh:
        sess = get_active_session()
        if sess.get("token"):
            sync_session_to_env(sess, write_dotenv=False)
            return sess
        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if gemini_key and not _is_placeholder(gemini_key):
            return {"token": "", "mode": "gemini_direct", "cloud_url": get_cloud_url()}

    offline = (
        os.environ.get("CLAUDE_EDITOR_OFFLINE", "").strip() == "1"
        or os.environ.get("SCRIPTCUT_OFFLINE", "").strip() == "1"
        or os.environ.get("ZENO_NO_LIVE", "").strip() == "1"
    )
    if not interactive or offline:
        return None
    return grab_token_via_browser(cloud_url=cloud_url, timeout_sec=timeout_sec, open_browser=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Zeno Zero-Touch Session Manager")
    parser.add_argument("--daemon", action="store_true", help="Run background 127.0.0.1:8765 callback listener")
    parser.add_argument("--login", action="store_true", help="Open browser for Google sign-in and auto-save to .env")
    parser.add_argument("--ingest-clipboard", action="store_true", help="Auto-save clipboard zeno_ token into .env")
    parser.add_argument("--whoami", action="store_true", help="Show non-secret session status")
    args = parser.parse_args()

    if args.daemon:
        run_background_callback_daemon(8765)
    elif args.ingest_clipboard:
        ensure_background_daemon_running(8765)
        saved = ingest_clipboard_token_if_present(session_path=None, write_dotenv=True)
        info = get_session_info()
        print(f"Authenticated: {info['authenticated']} | Email: {info['email'] or 'n/a'} | Source: {info['source']}")
    elif args.login:
        ensure_background_daemon_running(8765)
        ok = interactive_login()
        sys.exit(0 if ok else 1)
    else:
        ensure_background_daemon_running(8765)
        info = get_session_info()
        print(f"Authenticated: {info['authenticated']} | Email: {info['email'] or 'n/a'} | Source: {info['source']}")

