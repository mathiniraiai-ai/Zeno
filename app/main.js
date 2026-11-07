// Zeno desktop app: the window and its bridge to the Python backend. It only reads job state (scripts/jobs.py) and opens things; it never edits a job.
const { app, BrowserWindow, ipcMain, shell, nativeTheme, dialog, clipboard } = require('electron');
const { execFile, spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

const IS_MAC = process.platform === 'darwin';
const IS_WIN = process.platform === 'win32';
const arg = (name) => { const a = process.argv.find(x => x.startsWith(`--${name}=`)); return a ? a.slice(name.length + 3) : null; };

// Where Zeno is installed: ZENO_HOME, a saved choice, the usual team location, or (development) the folder above this app.
function findRoot() {
  const cands = [process.env.ZENO_HOME, path.join(app.getPath('userData'), 'zeno-home.txt') && readSaved(),
    path.join(os.homedir(), 'Documents', 'Zeno'), path.resolve(__dirname, '..')].filter(Boolean);
  for (const c of cands) { if (fs.existsSync(path.join(c, 'scripts', 'jobs.py'))) return c; }
  return null;
}
function readSaved() { try { return fs.readFileSync(path.join(app.getPath('userData'), 'zeno-home.txt'), 'utf8').trim(); } catch (e) { return null; } }

function pythonFor(root) {
  const venv = IS_WIN ? path.join(root, '.venv', 'Scripts', 'python.exe') : path.join(root, '.venv', 'bin', 'python');
  return fs.existsSync(venv) ? venv : (IS_WIN ? 'python' : 'python3');
}

function runJobs(root, args, timeout = 20000) {
  return new Promise((resolve) => {
    execFile(pythonFor(root), [path.join(root, 'scripts', 'jobs.py'), ...args], { cwd: root, timeout, maxBuffer: 20 * 1024 * 1024, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } },
      (err, stdout, stderr) => {
        if (err && !stdout) return resolve({ error: (stderr || err.message || 'failed').toString().slice(-400) });
        try { resolve(JSON.parse(stdout)); } catch (e) { resolve({ error: 'Could not read the answer from Zeno.' }); }
      });
  });
}

let ROOT = null;
// ---------------------------------------------------------------- Claude connection (Claude Code runs behind the chat box)
function claudePath() {
  const home = process.env.HOME || process.env.USERPROFILE || '';
  const dirs = IS_WIN ? [path.join(process.env.APPDATA || '', 'npm'), path.join(home, '.local', 'bin')] : [path.join(home, '.local', 'bin'), '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin'];
  for (const d of dirs) { for (const n of (IS_WIN ? ['claude.exe', 'claude.cmd'] : ['claude'])) { const p = path.join(d, n); if (fs.existsSync(p)) return p; } }
  return null;
}
function claudeEnv() {
  const extra = process.env.ZENO_CLAUDE_CONFIG_DIR ? { CLAUDE_CONFIG_DIR: process.env.ZENO_CLAUDE_CONFIG_DIR } : {};     // a test can point Claude at a temporary settings folder
  const home = process.env.HOME || '';
  return { ...process.env, ...extra, PATH: [path.join(home, '.local', 'bin'), '/opt/homebrew/bin', '/usr/local/bin', process.env.PATH || ''].join(path.delimiter), ZENO_EVENTS: 'on' };
}
function runClaude(args, timeout = 30000) {
  return new Promise((resolve) => {
    const bin = claudePath(); if (!bin) return resolve({ ok: false, text: 'Claude Code is not installed on this computer.' });
    execFile(bin, args, { cwd: ROOT || undefined, timeout, env: claudeEnv(), maxBuffer: 10 * 1024 * 1024 }, (err, out, errout) => resolve({ ok: !err, text: String(out || errout || (err && err.message) || '') }));
  });
}
const ZENO_MCP_NAME = 'zeno';
let chatProc = null;

function createWindow() {
  nativeTheme.themeSource = 'dark';
  const size = (arg('size') || '1240x800').split('x').map(Number);
  const win = new BrowserWindow({
    width: size[0], height: size[1], minWidth: 980, minHeight: 640, backgroundColor: '#000000', show: !arg('screenshot'),
    titleBarStyle: IS_MAC ? 'hiddenInset' : 'default', trafficLightPosition: { x: 16, y: 16 }, title: 'Zeno',
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: false },
  });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'), { query: { select: arg('select') || '' } });
  const shot = arg('screenshot');
  if (shot) {
    win.webContents.on('did-finish-load', () => setTimeout(async () => {
      if (arg('eval')) { const _r = await win.webContents.executeJavaScript(arg('eval')); if (_r !== undefined) console.log('EVAL', typeof _r === 'string' ? _r : JSON.stringify(_r)); await new Promise((r) => setTimeout(r, 500)); }
      for (const clickSel of (arg('click') || '').split('|').filter(Boolean)) { await win.webContents.executeJavaScript(`(document.querySelector(${JSON.stringify(clickSel)})||{click(){}}).click()`); await new Promise((r) => setTimeout(r, 1200)); }
      const img = await win.webContents.capturePage();
      fs.writeFileSync(shot, img.toPNG());
      app.quit();
    }, parseInt(arg('wait') || '3500', 10)));
  }
  return win;
}

app.whenReady().then(() => {
  ROOT = findRoot();
  ipcMain.handle('zeno:root', () => ROOT);
  ipcMain.handle('zeno:jobs', () => (ROOT ? runJobs(ROOT, ['list']) : { error: 'Zeno folder not found' }));
  ipcMain.handle('zeno:detail', (_e, name, since) => (ROOT ? runJobs(ROOT, ['detail', String(name), '--since', String(since || 0)]) : { error: 'Zeno folder not found' }));
  ipcMain.handle('zeno:info', (_e, check) => (ROOT ? runJobs(ROOT, ['info', ...(check ? [] : ['--no-update-check'])], 30000) : { error: 'Zeno folder not found' }));
  ipcMain.handle('zeno:reveal', (_e, p) => { if (p && fs.existsSync(p)) shell.showItemInFolder(p); });
  ipcMain.handle('zeno:open', (_e, p) => { if (p && fs.existsSync(p)) return shell.openPath(p); });
  ipcMain.handle('zeno:newEdit', () => {
    if (!ROOT) return false;
    if (IS_MAC) spawn('open', ['-a', 'Terminal', path.join(ROOT, 'edit.command')], { detached: true, stdio: 'ignore' }).unref();
    else if (IS_WIN) spawn('cmd', ['/c', 'start', '""', path.join(ROOT, 'edit.bat')], { detached: true, stdio: 'ignore', windowsVerbatimArguments: true }).unref();
    else spawn('x-terminal-emulator', ['-e', 'bash', path.join(ROOT, 'edit.command')], { detached: true, stdio: 'ignore' }).unref();
    return true;
  });
  // the editor's Yes: render the approved plan (reel and square) in the background; the app then follows it live
  ipcMain.handle('zeno:render', (_e, name, planFile) => {
    if (!ROOT || !/^[\w.\-]+$/.test(String(name)) || !/^[\w.\-]+\.edl$/.test(String(planFile))) return { ok: false, text: 'Not a valid job.' };
    const plan = path.join(ROOT, 'work', String(name), String(planFile));
    if (!fs.existsSync(plan)) return { ok: false, text: 'The plan file is missing.' };
    const log = fs.openSync(path.join(ROOT, 'work', String(name), '_render.log'), 'a');
    const child = spawn(pythonFor(ROOT), [path.join(ROOT, 'scripts', 'zeno.py'), '--execute', plan, '--square'], { cwd: ROOT, detached: true, stdio: ['ignore', log, log], env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    child.unref();
    return { ok: true };
  });
  // Download: save a copy of the finished video wherever the editor chooses
  ipcMain.handle('zeno:download', async (e, p) => {
    if (!p || !fs.existsSync(p)) return { ok: false, text: 'The file is missing.' };
    const win = BrowserWindow.fromWebContents(e.sender);
    const r = await dialog.showSaveDialog(win, { defaultPath: path.join(app.getPath('downloads'), path.basename(p)), buttonLabel: 'Save' });
    if (r.canceled || !r.filePath) return { ok: false, canceled: true };
    try { await fs.promises.copyFile(p, r.filePath); shell.showItemInFolder(r.filePath); return { ok: true, path: r.filePath }; } catch (err) { return { ok: false, text: String(err.message || err) }; }
  });

  // is Claude Code installed, which account is it signed in with, and does it have the Zeno connector?
  ipcMain.handle('zeno:claudeStatus', async () => {
    if (!claudePath()) return { installed: false, loggedIn: false, connected: false };
    const [auth, mcp] = await Promise.all([runClaude(['auth', 'status'], 20000), runClaude(['mcp', 'get', ZENO_MCP_NAME], 45000)]);
    let a = {}; try { a = JSON.parse(auth.text); } catch (e) { a = {}; }
    const connected = mcp.ok && /Connected/.test(mcp.text);
    return { installed: true, loggedIn: !!a.loggedIn, email: a.email || '', plan: a.subscriptionType || '', connected };
  });
  // sign in (opens the browser); a wrong account can be switched by signing out first
  ipcMain.handle('zeno:claudeLogin', async (_e, opts) => {
    const o = opts || {};
    if (o.switch) await runClaude(['auth', 'logout'], 20000);
    const bin = claudePath(); if (!bin) return { ok: false, text: 'Claude Code is not installed on this computer.' };
    const args = ['auth', 'login'].concat(o.email ? ['--email', String(o.email)] : []);
    spawn(bin, args, { env: claudeEnv(), detached: true, stdio: 'ignore' }).unref();
    return { ok: true };
  });
  // one click: add the Zeno connector to Claude Code (for this user, on this computer)
  ipcMain.handle('zeno:claudeConnect', async () => {
    if (!ROOT) return { ok: false, text: 'Zeno folder not found.' };
    const r = await runClaude(['mcp', 'add', '--scope', 'user', ZENO_MCP_NAME, '--', pythonFor(ROOT), path.join(ROOT, 'scripts', 'zeno_mcp.py')], 45000);
    return { ok: r.ok, text: r.text.slice(0, 300) };
  });
  ipcMain.handle('zeno:pickFolder', async (e) => {
    const r = await dialog.showOpenDialog(BrowserWindow.fromWebContents(e.sender), { properties: ['openDirectory'] });
    return r.canceled ? null : r.filePaths[0];
  });
  ipcMain.handle('zeno:copy', (_e, text) => { clipboard.writeText(String(text || '')); return true; });
  // the chat: one Claude Code run per message, streamed back; a follow-up resumes the same session
  ipcMain.handle('zeno:chatSend', (e, message, sessionId) => {
    const bin = claudePath(); if (!bin || !ROOT) return { ok: false, text: 'Claude Code is not installed on this computer.' };
    if (chatProc) return { ok: false, text: 'Claude is still working on the last message.' };
    const send = (evt) => { try { e.sender.send('zeno:chat', evt); } catch (er) { /* window closed */ } };
    const args = ['-p', String(message), '--output-format', 'stream-json', '--verbose', '--permission-mode', 'acceptEdits',
      '--allowedTools', 'Bash(python3:*)', 'Bash(.venv/bin/python:*)', 'Bash(ls:*)', 'Bash(grep:*)', 'Bash(cat:*)', 'Read', 'Write', 'Edit', 'Glob', 'Grep', 'mcp__zeno'];
    // only the Zeno connector is loaded for these runs: the editor's other connectors are not needed and would cost tokens
    args.push('--strict-mcp-config', '--mcp-config', JSON.stringify({ mcpServers: { [ZENO_MCP_NAME]: { command: pythonFor(ROOT), args: [path.join(ROOT, 'scripts', 'zeno_mcp.py')] } } }));
    if (sessionId) args.push('--resume', String(sessionId));
    chatProc = spawn(bin, args, { cwd: ROOT, env: claudeEnv(), stdio: ['ignore', 'pipe', 'pipe'] });
    let buf = '';
    chatProc.stdout.on('data', (d) => {
      buf += d.toString('utf8'); let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1); if (!line) continue;
        let j; try { j = JSON.parse(line); } catch (er) { continue; }
        if (j.type === 'system' && j.session_id) send({ kind: 'session', id: j.session_id });
        else if (j.type === 'assistant' && j.message && Array.isArray(j.message.content)) {
          for (const b of j.message.content) {
            if (b.type === 'text' && b.text) send({ kind: 'text', text: b.text });
            else if (b.type === 'tool_use') send({ kind: 'tool', name: b.name, input: (b.input && (b.input.command || b.input.file_path || b.input.pattern || '')) || '' });
          }
        } else if (j.type === 'result') send({ kind: 'result', ok: !j.is_error, text: j.result || '', session: j.session_id, cost: j.total_cost_usd });
      }
    });
    let errTxt = '';
    chatProc.stderr.on('data', (d) => { errTxt += d.toString('utf8'); });
    chatProc.on('close', (code) => { chatProc = null; send({ kind: 'done', code, error: code ? errTxt.slice(-300) : '' }); });
    return { ok: true };
  });
  ipcMain.handle('zeno:chatStop', () => { if (chatProc) { chatProc.kill('SIGTERM'); } return true; });
  ipcMain.handle('zeno:update', () => new Promise((resolve) => {
    if (!ROOT) return resolve({ ok: false, text: 'Zeno folder not found' });
    const code = "import sys;sys.path.insert(0,'scripts');import auto_updater;print(auto_updater.ensure_latest_update(force=True,quiet=True))";
    execFile(pythonFor(ROOT), ['-c', code], { cwd: ROOT, timeout: 180000 }, (err, out, errout) => resolve({ ok: !err, text: String(out || errout || (err && err.message) || '').slice(-300) }));
  }));
  createWindow();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});
app.on('window-all-closed', () => { if (!IS_MAC || arg('screenshot')) app.quit(); });
