#!/usr/bin/env python3
"""Zeno-themed floating desktop notification pop-up (`notify_popup.py`).

Renders a sleek, borderless, top-right floating desktop toast matching the exact
Zeno dark + lime (`#bacf65`) design system from `app/renderer/styles.css`:
  - macOS: Native Cocoa `NSPanel` (non-activating, floating, transparent corners,
    zero Dock icon) + Retina `WKWebView` loaded with `assets/ui/zeno_toast.html`.
    Supports interactive `Open Folder`, `Play Reel`, `×` dismiss, hover-to-pause
    progress bar, and automatic dismissal.
  - Windows: Native WPF borderless topmost transparent toast matching the exact
    same `#0A0A0A` / `#111111` + `#bacf65` Zeno card styling with clickable
    `Open Folder` and `Play Reel` actions.

Usage:
    python3 scripts/notify_popup.py --title "Reel - W46 - Kannada" --folder "~/Downloads/Zeno Outs/<FolderName>"
    python3 scripts/notify_popup.py --test
"""
from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

ROOT = SCRIPTS_DIR.parent
UI_DIR = ROOT / "assets" / "ui"
TEMPLATE_HTML = UI_DIR / "zeno_toast.html"
ICON_PNG = UI_DIR / "zeno_icon.png"

TOAST_WIDTH = 424
TOAST_HEIGHT = 156


def _default_output_root() -> Path:
    """Return the default Zeno delivery root (`$ZENO_OUTPUT_DIR` or `~/Downloads/Zeno Outs`)."""
    override = os.environ.get("ZENO_OUTPUT_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "Downloads" / "Zeno Outs"


def _pretty_path(p: Path) -> str:
    try:
        home = Path.home().resolve()
        rel = p.resolve().relative_to(home)
        return f"~/{rel.as_posix()}"
    except Exception:
        return str(p)


def _deliverable_summary(files: Sequence[str], video_path: Optional[Path]) -> str:
    names = [str(f).lower() for f in (files or [])]
    if video_path is not None:
        names.append(video_path.name.lower())
    parts: List[str] = []
    if any(n.startswith("reel_") and n.endswith(".mp4") for n in names):
        parts.append("9:16 reel")
    elif any(n.endswith(".mp4") for n in names):
        parts.append("MP4")
    if any(n.startswith("sq_") and n.endswith(".mp4") for n in names):
        parts.append("1:1 square")
    if any(n.endswith(".prproj") for n in names):
        parts.append("Premiere project")
    if any(n == "review_sheet.jpg" for n in names):
        parts.append("review sheet")
    return " · ".join(parts)


def build_toast_html(
    *,
    title: str,
    folder: Optional[Path] = None,
    video_path: Optional[Path] = None,
    files: Sequence[str] = (),
    badge: str = "Video ready",
    duration_sec: float = 8.0,
    notes: str = "",
) -> str:
    """Render `assets/ui/zeno_toast.html`. Delivery toasts show where the files are saved
    (no buttons); update toasts (`notes` set, no folder) show the release summary instead.
    """
    tpl = TEMPLATE_HTML.read_text(encoding="utf-8")
    details = ""
    display_title = str(title or "").strip() or "Zeno"
    if notes:
        details = f'<div class="row note">{html.escape(str(notes).strip())}</div>'
    else:
        raw_folder = str(folder or "").strip() if folder is not None else ""
        dest = Path(raw_folder).expanduser() if raw_folder else _default_output_root()
        display_title = str(title or "").strip() or dest.name or "Zeno"
        details = (
            f'<div class="row path" title="{html.escape(str(dest))}"><span class="label">Saved in </span>'
            f"{html.escape(_pretty_path(dest))}</div>"
        )
        summary = _deliverable_summary(files, video_path)
        if summary:
            details += f'<div class="row">{html.escape(summary)}</div>'
    dur_ms = max(2500, int(round(float(duration_sec) * 1000)))
    out = tpl
    for k, v in {
        "__BADGE__": html.escape(str(badge or "Video ready").strip()),
        "__TITLE__": html.escape(display_title),
        "__DETAILS__": details,
        "__DURATION_MS__": str(dur_ms),
    }.items():
        out = out.replace(k, v)
    return out


_MAC_JXA_SCRIPT = r"""
ObjC.import('Cocoa');
ObjC.import('WebKit');
ObjC.import('Foundation');

function run(argv) {
  const cfgPath = argv[0];
  const raw = $.NSString.stringWithContentsOfFileEncodingError(cfgPath, $.NSUTF8StringEncoding, null);
  if (!raw || raw.isNil()) return 'ERR: cannot read cfg';
  const cfg = JSON.parse(ObjC.unwrap(raw));

  const app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);

  const W = Number(cfg.width) || 420;
  const H = Number(cfg.height) || 228;
  const screen = $.NSScreen.mainScreen;
  const vf = screen.visibleFrame;
  const x = vf.origin.x + vf.size.width - W - 4;
  const y = vf.origin.y + vf.size.height - H - 8;

  const styleMask = (1 << 0) * 0 | (1 << 7); // NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
  const panel = $.NSPanel.alloc.initWithContentRectStyleMaskBackingDefer(
    $.NSMakeRect(x, y, W, H),
    styleMask,
    $.NSBackingStoreBuffered,
    false
  );
  panel.setLevel($.NSFloatingWindowLevel);
  panel.setOpaque(false);
  panel.setHasShadow(false); // CSS handles the smooth ambient shadow inside transparent canvas
  panel.setBackgroundColor($.NSColor.clearColor);
  panel.setHidesOnDeactivate(false);
  panel.setCollectionBehavior((1 << 0) | (1 << 4)); // CanJoinAllSpaces | Stationary

  function openTarget(targetPath) {
    if (!targetPath) return;
    try {
      const fm = $.NSFileManager.defaultManager;
      let pathToOpen = String(targetPath);
      if (!fm.fileExistsAtPath(pathToOpen)) {
        const parent = $(pathToOpen).stringByDeletingLastPathComponent;
        if (parent && fm.fileExistsAtPath(parent)) {
          pathToOpen = ObjC.unwrap(parent);
        }
      }
      const task = $.NSTask.alloc.init;
      task.launchPath = '/usr/bin/open';
      task.arguments = $([pathToOpen]);
      task.launch;
    } catch (e) {}
  }

  function finishAndQuit() {
    try {
      if (cfg.cleanupPaths) {
        const fm = $.NSFileManager.defaultManager;
        for (const p of cfg.cleanupPaths) {
          fm.removeItemAtPathError(p, null);
        }
      }
    } catch (e) {}
    app.terminate(null);
  }

  ObjC.registerSubclass({
    name: 'ZenoToastHandler',
    superclass: 'NSObject',
    methods: {
      'userContentController:didReceiveScriptMessage:': {
        types: ['void', ['id', 'id']],
        implementation: function (ucc, msg) {
          finishAndQuit();
        }
      },
      'onWatchdog:': {
        types: ['void', ['id']],
        implementation: function () {
          finishAndQuit();
        }
      },
      'onCapture:': {
        types: ['void', ['id']],
        implementation: function () {
          try {
            const capPath = cfg.capturePath;
            if (capPath) {
              const winNum = String(panel.windowNumber);
              const task = $.NSTask.alloc.init;
              task.launchPath = '/usr/sbin/screencapture';
              task.arguments = $(['-x', '-o', '-l', winNum, String(capPath)]);
              task.launch;
              task.waitUntilExit;
            }
          } catch (e) {}
          if (cfg.captureOnly) {
            finishAndQuit();
          }
        }
      }
    }
  });

  const handler = $.ZenoToastHandler.alloc.init;
  const wkCfg = $.WKWebViewConfiguration.alloc.init;
  wkCfg.userContentController.addScriptMessageHandlerName(handler, 'zenoAction');

  const webView = $.WKWebView.alloc.initWithFrameConfiguration($.NSMakeRect(0, 0, W, H), wkCfg);
  webView.setValueForKey($.NSNumber.numberWithBool(false), 'drawsBackground');

  const htmlStr = $.NSString.stringWithContentsOfFileEncodingError(cfg.htmlPath, $.NSUTF8StringEncoding, null);
  const baseUrl = $.NSURL.fileURLWithPath(cfg.baseDir);
  webView.loadHTMLStringBaseURL(htmlStr, baseUrl);

  panel.setContentView(webView);
  panel.orderFrontRegardless;

  const maxSec = Math.max(6.0, (Number(cfg.durationSec) || 9.0) + 35.0);
  $.NSTimer.scheduledTimerWithTimeIntervalTargetSelectorUserInfoRepeats(
    maxSec, handler, 'onWatchdog:', null, false
  );

  if (cfg.capturePath) {
    $.NSTimer.scheduledTimerWithTimeIntervalTargetSelectorUserInfoRepeats(
      0.8, handler, 'onCapture:', null, false
    );
  }

  app.run;
  return 'OK';
}
"""


_WIN_PS_WPF_TEMPLATE = r"""
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
$title = '__TITLE__'
$line1 = '__LINE1__'
$line2 = '__LINE2__'
$badge = '__BADGE__'
$durSec = __DUR_SEC__

[xml]$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Width="390" SizeToContent="Height" WindowStyle="None" AllowsTransparency="True"
        Background="Transparent" Topmost="True" ShowInTaskbar="False" ShowActivated="False">
  <Border Name="Card" Margin="8" CornerRadius="12" Background="#0A0A0A" BorderBrush="#1F1F1F" BorderThickness="1" Padding="14">
    <Border.RenderTransform><TranslateTransform x:Name="Slide" X="420"/></Border.RenderTransform>
    <DockPanel>
      <Border DockPanel.Dock="Left" Width="32" Height="32" CornerRadius="8" Background="#000000" BorderBrush="#1F1F1F" BorderThickness="1" VerticalAlignment="Top" Margin="0,0,12,0">
        <Border Width="12" Height="12" CornerRadius="3" Background="#BACF65" HorizontalAlignment="Center" VerticalAlignment="Center"/>
      </Border>
      <StackPanel>
        <TextBlock Name="BadgeTxt" Foreground="#BACF65" FontFamily="Consolas" FontSize="10.5" FontWeight="SemiBold" Margin="0,0,0,3"/>
        <TextBlock Name="TitleTxt" Foreground="#F1F5F9" FontSize="14" FontWeight="SemiBold" TextWrapping="Wrap" Margin="0,0,0,6"/>
        <TextBlock Name="Line1Txt" Foreground="#94A3B8" FontSize="11.5" TextWrapping="Wrap" Margin="0,0,0,3"/>
        <TextBlock Name="Line2Txt" Foreground="#94A3B8" FontSize="11.5" TextWrapping="Wrap"/>
      </StackPanel>
    </DockPanel>
  </Border>
</Window>
"@

$reader = New-Object System.Xml.XmlNodeReader $xaml
$win = [Windows.Markup.XamlReader]::Load($reader)
$win.FindName('BadgeTxt').Text = $badge.ToUpper()
$win.FindName('TitleTxt').Text = $title
$win.FindName('Line1Txt').Text = $line1
if ($line2) { $win.FindName('Line2Txt').Text = $line2 } else { $win.FindName('Line2Txt').Visibility = 'Collapsed' }

$wa = [System.Windows.SystemParameters]::WorkArea
$win.Left = $wa.Right - $win.Width - 4
$win.Top = $wa.Top + 8

$slide = $win.FindName('Slide')
$closing = $false
function Swipe($from, $to, $ms, $onDone) {
  $anim = New-Object System.Windows.Media.Animation.DoubleAnimation($from, $to, [TimeSpan]::FromMilliseconds($ms))
  if ($onDone) { $anim.Add_Completed($onDone) }
  $slide.BeginAnimation([System.Windows.Media.TranslateTransform]::XProperty, $anim)
}
function Close-Toast {
  if ($script:closing) { return }
  $script:closing = $true
  $script:timer.Stop()
  Swipe 0 420 330 { $win.Close() }
}
$win.Add_Loaded({ Swipe 420 0 420 $null })
$win.Add_MouseLeftButtonUp({ Close-Toast })

$timer = New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromSeconds($durSec)
$timer.Add_Tick({ Close-Toast })
$timer.Start()

$win.ShowDialog() | Out-Null
"""


def _play_chime() -> bool:
    """Play the bundled Zeno notification chime (same sound as delivery notifications)."""
    try:
        import notify

        if notify._is_suppressed():
            return False
        return bool(notify.play_notification_sound(wait=False))
    except Exception:
        return False


def show_themed_popup(
    *,
    title: str,
    folder: Optional[Path] = None,
    video_path: Optional[Path] = None,
    files: Sequence[str] = (),
    badge: str = "Video ready",
    duration_sec: float = 8.0,
    wait: bool = False,
    capture_path: Optional[Path] = None,
    capture_only: bool = False,
    notes: str = "",
    play_sound: bool = True,
) -> bool:
    """Launch the Zeno-themed floating desktop toast on macOS or Windows."""
    raw_folder = str(folder or "").strip() if folder is not None else ""
    dest = (Path(raw_folder).expanduser() if raw_folder else _default_output_root()).resolve()
    vid = Path(video_path).expanduser().resolve() if video_path is not None else None
    plat = sys.platform.lower()
    if play_sound and not capture_only:
        _play_chime()

    if plat == "darwin":
        osa = shutil.which("osascript") or "/usr/bin/osascript"
        if not Path(osa).is_file() or not TEMPLATE_HTML.is_file():
            return False
        tmp_dir = Path(tempfile.gettempdir())
        pid_tag = f"zeno_toast_{os.getpid()}_{os.urandom(3).hex()}"
        html_file = tmp_dir / f"{pid_tag}.html"
        jxa_file = tmp_dir / f"{pid_tag}.js"
        cfg_file = tmp_dir / f"{pid_tag}.json"

        html_content = build_toast_html(
            title=title,
            folder=dest,
            video_path=vid,
            files=files,
            badge=badge,
            duration_sec=duration_sec,
            notes=notes,
        )
        html_file.write_text(html_content, encoding="utf-8")
        jxa_file.write_text(_MAC_JXA_SCRIPT, encoding="utf-8")
        cfg_payload: Dict[str, Any] = {
            "htmlPath": str(html_file),
            "baseDir": str(TEMPLATE_HTML.parent),
            "folder": str(dest),
            "video": str(vid) if (vid is not None and vid.is_file()) else "",
            "width": TOAST_WIDTH,
            "height": TOAST_HEIGHT,
            "durationSec": float(duration_sec),
            "capturePath": str(Path(capture_path).resolve()) if capture_path else "",
            "captureOnly": bool(capture_only),
            "cleanupPaths": [str(html_file), str(jxa_file), str(cfg_file)],
        }
        cfg_file.write_text(json.dumps(cfg_payload, ensure_ascii=False), encoding="utf-8")

        cmd = [osa, "-l", "JavaScript", str(jxa_file), str(cfg_file)]
        try:
            if wait or capture_only:
                r = subprocess.run(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=max(15.0, float(duration_sec) + 20.0),
                )
                return r.returncode == 0
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except Exception:
            for p in (html_file, jxa_file, cfg_file):
                p.unlink(missing_ok=True)
            return False

    if plat.startswith("win"):
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if not ps:
            return False
        esc_ps = lambda s: str(s or "").replace("\r", " ").replace("\n", " ").replace("'", "''").replace('"', '`"')
        if notes:
            line1, line2 = str(notes).strip(), ""
            display_title = str(title or "").strip() or "Zeno"
        else:
            display_title = str(title or "").strip() or dest.name or "Zeno"
            line1 = "Saved in " + _pretty_path(dest)
            line2 = _deliverable_summary(files, vid)
        ps_body = (
            _WIN_PS_WPF_TEMPLATE
            .replace("__TITLE__", esc_ps(display_title))
            .replace("__LINE1__", esc_ps(line1))
            .replace("__LINE2__", esc_ps(line2))
            .replace("__BADGE__", esc_ps(badge or "Video ready"))
            .replace("__DUR_SEC__", str(max(3, int(round(duration_sec)))))
        )
        tmp_ps = Path(tempfile.gettempdir()) / f"zeno_toast_{os.getpid()}_{os.urandom(3).hex()}.ps1"
        tmp_ps.write_text(ps_body + f"\nRemove-Item -Force -ErrorAction SilentlyContinue '{esc_ps(str(tmp_ps))}'\n", encoding="utf-8")
        cmd = [ps, "-STA", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", str(tmp_ps)]
        try:
            if wait:
                r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=duration_sec + 15.0)
                return r.returncode == 0
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True
        except Exception:
            tmp_ps.unlink(missing_ok=True)
            return False

    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Show a Zeno-themed floating completion toast pop-up.")
    ap.add_argument("--title", default="", help="Delivered project/video title")
    ap.add_argument("--folder", default="", help="Delivered folder path")
    ap.add_argument("--video", default="", help="Delivered primary .mp4 path")
    ap.add_argument("--files", nargs="*", default=[], help="List of delivered filenames for chips")
    ap.add_argument("--badge", default="Video ready", help="Status pill badge text")
    ap.add_argument("--duration", type=float, default=8.0, help="Auto-dismiss duration in seconds")
    ap.add_argument("--wait", action="store_true", help="Wait for toast to close")
    ap.add_argument("--capture", default="", help="Optional PNG screenshot capture path (macOS)")
    ap.add_argument("--capture-only", action="store_true", help="Capture PNG and exit immediately")
    ap.add_argument("--notes", default="", help="Release summary; shows an update toast instead of a delivery toast")
    ap.add_argument("--no-sound", action="store_true", help="Suppress notification chime")
    ap.add_argument("--test", action="store_true", help="Launch a live sample Zeno toast on screen")
    args = ap.parse_args()

    play_sound = not args.no_sound

    if args.test:
        sample_dir = Path(args.folder).expanduser() if args.folder else _default_output_root()
        ok = show_themed_popup(
            title=args.title or "Reel - W46 - Kannada - Split screen with flash card",
            folder=sample_dir,
            video_path=Path(args.video) if args.video else None,
            files=args.files or ["reel_sample.mp4", "SQ_sample.mp4", "sample.prproj", "review_sheet.jpg"],
            badge=args.badge,
            duration_sec=args.duration,
            wait=args.wait,
            capture_path=Path(args.capture) if args.capture else None,
            capture_only=args.capture_only,
            play_sound=play_sound,
        )
        print(f"[POPUP] shown={ok}")
        return 0 if ok else 1

    if args.notes:
        ok = show_themed_popup(
            title=args.title or "Zeno updated",
            folder=Path(args.folder or "."),
            badge=args.badge if args.badge != "Video ready" else "Updated",
            notes=args.notes,
            duration_sec=args.duration,
            wait=args.wait,
            capture_path=Path(args.capture) if args.capture else None,
            capture_only=args.capture_only,
            play_sound=play_sound,
        )
        return 0 if ok else 1

    if not args.folder:
        ap.print_help()
        return 0

    folder_arg = Path(args.folder).expanduser()
    ok = show_themed_popup(
        title=args.title or folder_arg.name or "Zeno",
        folder=folder_arg,
        video_path=Path(args.video) if args.video else None,
        files=args.files,
        badge=args.badge,
        duration_sec=args.duration,
        wait=args.wait,
        capture_path=Path(args.capture) if args.capture else None,
        capture_only=args.capture_only,
        play_sound=play_sound,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
