// Zeno desktop app: the window and its bridge to the Python backend. It only reads job state (scripts/jobs.py) and opens things; it never edits a job.
const { app, BrowserWindow, ipcMain, shell, nativeTheme } = require('electron');
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
  ipcMain.handle('zeno:update', () => new Promise((resolve) => {
    if (!ROOT) return resolve({ ok: false, text: 'Zeno folder not found' });
    const code = "import sys;sys.path.insert(0,'scripts');import auto_updater;print(auto_updater.ensure_latest_update(force=True,quiet=True))";
    execFile(pythonFor(ROOT), ['-c', code], { cwd: ROOT, timeout: 180000 }, (err, out, errout) => resolve({ ok: !err, text: String(out || errout || (err && err.message) || '').slice(-300) }));
  }));
  createWindow();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});
app.on('window-all-closed', () => { if (!IS_MAC || arg('screenshot')) app.quit(); });
