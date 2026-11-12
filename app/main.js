// Zeno desktop app: the window, Google Workspace sign-in, native Cloud OTA updater, and per-user bridge to scripts/jobs.py.
const { app, BrowserWindow, Notification, ipcMain, shell, nativeTheme, dialog, clipboard } = require('electron');
const { execFile, spawn } = require('child_process');
const crypto = require('crypto');
const zlib = require('zlib');
const path = require('path');
const fs = require('fs');
const os = require('os');

const IS_MAC = process.platform === 'darwin';
const IS_WIN = process.platform === 'win32';
const DEFAULT_CLOUD_URL = 'https://zeno-production-bef7.up.railway.app';
const arg = (name) => { const a = process.argv.find((x) => x.startsWith(`--${name}=`)); return a ? a.slice(name.length + 3) : null; };

function ancestorDirs(startPath, depth = 8) {
  const out = [];
  let cur = startPath;
  for (let i = 0; i < depth && cur; i++) {
    const next = path.dirname(cur);
    if (!next || next === cur) break;
    out.push(next);
    cur = next;
  }
  return out;
}

function readSavedRoot() {
  try { return fs.readFileSync(path.join(app.getPath('userData'), 'zeno-home.txt'), 'utf8').trim(); } catch (e) { return null; }
}
function saveRoot(rootPath) {
  try {
    fs.mkdirSync(app.getPath('userData'), { recursive: true });
    fs.writeFileSync(path.join(app.getPath('userData'), 'zeno-home.txt'), String(rootPath) + '\n', 'utf8');
  } catch (e) { /* ignore */ }
}

// Where Zeno is installed: ZENO_HOME, saved choice, ancestor of dev/packaged build, ~/Documents/Zeno, or owner workspace.
function findRoot() {
  const home = os.homedir();
  const cands = [
    process.env.ZENO_HOME,
    readSavedRoot(),
    ...ancestorDirs(__dirname, 8),
    ...ancestorDirs(process.execPath, 8),
    path.join(home, 'Documents', 'Zeno'),
    path.join(home, 'Documents', 'Office project', 'Zeno'),
    path.join(home, 'Documents', 'Office project', 'Claude Editor'),
  ].filter(Boolean);
  for (const c of cands) {
    try { if (fs.existsSync(path.join(c, 'scripts', 'jobs.py'))) return c; } catch (e) { /* skip */ }
  }
  return null;
}

function venvPythonPath(root) {
  if (!root) return '';
  return IS_WIN ? path.join(root, '.venv', 'Scripts', 'python.exe') : path.join(root, '.venv', 'bin', 'python');
}
function hasVenv(root) {
  const vp = venvPythonPath(root);
  return !!(vp && fs.existsSync(vp));
}
function pythonFor(root) {
  const venv = venvPythonPath(root);
  return (venv && fs.existsSync(venv)) ? venv : (IS_WIN ? 'python' : 'python3');
}

function semverKey(v) {
  const m = String(v || '0.0.0').trim().match(/^v?(\d+)\.(\d+)\.(\d+)/);
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : [0, 0, 0];
}
function cmpVer(a, b) {
  const ka = semverKey(a), kb = semverKey(b);
  for (let i = 0; i < 3; i++) { if (ka[i] !== kb[i]) return ka[i] - kb[i]; }
  return 0;
}
function bundledVersion() {
  try { return require('./package.json').version || '1.0.0'; } catch (e) { return '1.0.0'; }
}
function localVersion(root) {
  try {
    if (root) {
      const vf = path.join(root, 'VERSION');
      if (fs.existsSync(vf)) {
        const v = fs.readFileSync(vf, 'utf8').trim();
        if (v) return v;
      }
    }
  } catch (e) { /* ignore */ }
  return bundledVersion();
}
function readUpdateState(root) {
  if (!root) return {};
  try {
    const sf = path.join(root, 'work', '_update_state.json');
    if (fs.existsSync(sf)) return JSON.parse(fs.readFileSync(sf, 'utf8')) || {};
  } catch (e) { /* ignore */ }
  return {};
}
function writeUpdateState(root, patch) {
  if (!root) return;
  try {
    const dir = path.join(root, 'work');
    fs.mkdirSync(dir, { recursive: true });
    const sf = path.join(dir, '_update_state.json');
    const merged = { ...readUpdateState(root), ...patch };
    const tmp = sf + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(merged, null, 2) + '\n', 'utf8');
    fs.renameSync(tmp, sf);
  } catch (e) { /* ignore */ }
}

function rendererEntry(root) {
  try {
    if (root) {
      const extHtml = path.join(root, 'app', 'renderer', 'index.html');
      const extVer = localVersion(root);
      if (fs.existsSync(extHtml) && cmpVer(extVer, bundledVersion()) >= 0) return extHtml;
    }
  } catch (e) { /* fallback to bundled renderer */ }
  return path.join(__dirname, 'renderer', 'index.html');
}
function preloadEntry(root) {
  try {
    if (root) {
      const extPre = path.join(root, 'app', 'preload.js');
      const extVer = localVersion(root);
      if (fs.existsSync(extPre) && cmpVer(extVer, bundledVersion()) >= 0) return extPre;
    }
  } catch (e) { /* fallback */ }
  return path.join(__dirname, 'preload.js');
}

// ---------------------------------------------------------------- Desktop Auth (stored in Electron userData + synced to ROOT/.zeno_session.json & .env)
function authFilePath() {
  return path.join(app.getPath('userData'), 'zeno_auth.json');
}
function readAppAuth() {
  try {
    const p = authFilePath();
    if (!fs.existsSync(p)) return null;
    const d = JSON.parse(fs.readFileSync(p, 'utf8'));
    if (d && typeof d.token === 'string' && d.token.trim()) return d;
  } catch (e) { /* ignore */ }
  return null;
}
function writeAppAuth(auth) {
  const p = authFilePath();
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(auth, null, 2) + '\n', { encoding: 'utf8', mode: 0o600 });
  try { if (!IS_WIN) fs.chmodSync(p, 0o600); } catch (e) { /* ignore */ }
}
function clearAppAuth() {
  try { const p = authFilePath(); if (fs.existsSync(p)) fs.unlinkSync(p); } catch (e) { /* ignore */ }
}

function upsertEnvLine(lines, key, value) {
  const prefix = `${key}=`;
  let replaced = false;
  const out = [];
  for (const line of lines) {
    const s = line.trim();
    if (s.startsWith(prefix) || s.startsWith(`export ${prefix}`)) {
      if (!replaced) { out.push(`${key}=${value}`); replaced = true; }
    } else {
      out.push(line);
    }
  }
  if (!replaced) out.push(`${key}=${value}`);
  return out;
}

function syncAuthToRoot(root, auth) {
  if (!root || !auth || !auth.token) return;
  try {
    fs.mkdirSync(root, { recursive: true });
    const sessPath = path.join(root, '.zeno_session.json');
    const payload = {
      token: String(auth.token).trim(),
      email: String(auth.email || '').trim().toLowerCase(),
      name: String(auth.name || '').trim(),
      cloud_url: String(auth.cloud_url || DEFAULT_CLOUD_URL).replace(/\/+$/, ''),
      authenticated_at: auth.authenticated_at || new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'),
    };
    fs.writeFileSync(sessPath, JSON.stringify(payload, null, 2) + '\n', { encoding: 'utf8', mode: 0o600 });
    try { if (!IS_WIN) fs.chmodSync(sessPath, 0o600); } catch (e) { /* ignore */ }

    const envPath = path.join(root, '.env');
    const exPath = path.join(root, '.env.example');
    let lines = ['# Zeno Environment Configuration (auto-generated)'];
    if (fs.existsSync(envPath)) lines = fs.readFileSync(envPath, 'utf8').split(/\r?\n/);
    else if (fs.existsSync(exPath)) lines = fs.readFileSync(exPath, 'utf8').split(/\r?\n/);
    while (lines.length && lines[lines.length - 1] === '') lines.pop();
    lines = upsertEnvLine(lines, 'ZENO_CLOUD_URL', payload.cloud_url);
    lines = upsertEnvLine(lines, 'ZENO_CLOUD_TOKEN', payload.token);
    lines = upsertEnvLine(lines, 'SCRIPTCUT_CLOUD_URL', payload.cloud_url);
    lines = upsertEnvLine(lines, 'SCRIPTCUT_CLOUD_TOKEN', payload.token);
    if (payload.email) lines = upsertEnvLine(lines, 'ZENO_USER_EMAIL', payload.email);
    if (payload.name) lines = upsertEnvLine(lines, 'ZENO_USER_NAME', payload.name);
    fs.writeFileSync(envPath, lines.join('\n') + '\n', { encoding: 'utf8', mode: 0o600 });
    try { if (!IS_WIN) fs.chmodSync(envPath, 0o600); } catch (e) { /* ignore */ }
  } catch (e) { /* ignore */ }
}

function clearRootAuth(root) {
  if (!root) return;
  try {
    const sessPath = path.join(root, '.zeno_session.json');
    if (fs.existsSync(sessPath)) fs.unlinkSync(sessPath);
    const envPath = path.join(root, '.env');
    if (fs.existsSync(envPath)) {
      let lines = fs.readFileSync(envPath, 'utf8').split(/\r?\n/);
      while (lines.length && lines[lines.length - 1] === '') lines.pop();
      lines = upsertEnvLine(lines, 'ZENO_CLOUD_TOKEN', '');
      lines = upsertEnvLine(lines, 'SCRIPTCUT_CLOUD_TOKEN', '');
      fs.writeFileSync(envPath, lines.join('\n') + '\n', { encoding: 'utf8', mode: 0o600 });
    }
  } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------- Pure Node.js Cloud OTA Bundle Extractor & Updater
const PRESERVE_REL = new Set(['.env', '.zeno_session.json', '.zeno-dev']);
const PRESERVE_PREFIX = ['.git/', '.venv/', 'models/', 'tools/', 'work/'];

function extractZipBuffer(buf, destDir) {
  let eocd = -1;
  const minOff = Math.max(0, buf.length - 65557);
  for (let i = buf.length - 22; i >= minOff; i--) {
    if (buf.readUInt32LE(i) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error('Invalid update bundle (missing ZIP directory).');
  const cdCount = buf.readUInt16LE(eocd + 10);
  let pos = buf.readUInt32LE(eocd + 16);
  let extracted = 0;

  fs.mkdirSync(destDir, { recursive: true });
  for (let idx = 0; idx < cdCount; idx++) {
    if (pos + 46 > buf.length || buf.readUInt32LE(pos) !== 0x02014b50) throw new Error('Corrupt ZIP central directory.');
    const method = buf.readUInt16LE(pos + 10);
    const compSize = buf.readUInt32LE(pos + 20);
    const uncompSize = buf.readUInt32LE(pos + 24);
    const nameLen = buf.readUInt16LE(pos + 28);
    const extraLen = buf.readUInt16LE(pos + 30);
    const commentLen = buf.readUInt16LE(pos + 32);
    const extAttr = buf.readUInt32LE(pos + 38);
    const localOff = buf.readUInt32LE(pos + 42);
    const rawName = buf.subarray(pos + 46, pos + 46 + nameLen).toString('utf8').replace(/\\/g, '/');
    pos += 46 + nameLen + extraLen + commentLen;

    if (!rawName || rawName.endsWith('/')) continue;
    const parts = rawName.split('/').filter(Boolean);
    if (!parts.length || rawName.startsWith('/') || parts.includes('..') || parts[0].includes(':')) continue;
    const cleanRel = parts.join('/');
    if (PRESERVE_REL.has(cleanRel)) continue;
    if (PRESERVE_PREFIX.some((pfx) => cleanRel.startsWith(pfx))) continue;

    if (localOff + 30 > buf.length || buf.readUInt32LE(localOff) !== 0x04034b50) throw new Error(`Corrupt ZIP entry: ${cleanRel}`);
    const lNameLen = buf.readUInt16LE(localOff + 26);
    const lExtraLen = buf.readUInt16LE(localOff + 28);
    const dataStart = localOff + 30 + lNameLen + lExtraLen;
    const compSlice = buf.subarray(dataStart, dataStart + compSize);
    const fileBytes = method === 0 ? compSlice : (method === 8 ? zlib.inflateRawSync(compSlice) : null);
    if (!fileBytes || fileBytes.length !== uncompSize) throw new Error(`Failed to decompress ${cleanRel}`);

    const target = path.join(destDir, ...parts);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    const tmp = `${target}.zeno_tmp_${process.pid}`;
    fs.writeFileSync(tmp, fileBytes);
    if (!IS_WIN) {
      const unixMode = (extAttr >>> 16) & 0o777;
      const isExec = cleanRel.endsWith('.command') || cleanRel.endsWith('.sh') || !!(unixMode & 0o111);
      try { fs.chmodSync(tmp, isExec ? 0o755 : 0o644); } catch (e) { /* ignore */ }
    }
    fs.renameSync(tmp, target);
    extracted++;
  }
  return extracted;
}

let cachedCloudInfo = null;
let cachedCloudAt = 0;

async function checkCloudUpdateNative(auth, root, force = false) {
  const curVer = localVersion(root);
  const base = {
    available: false,
    current_version: curVer,
    latest_version: curVer,
    notes: '',
    size_mb: 0,
    mandatory: false,
    dmg_url: '',
    exe_url: '',
    bundle_sha256: '',
  };
  if (!auth || !auth.token) return base;
  const now = Date.now();
  if (!force && cachedCloudInfo && (now - cachedCloudAt) < 120000) {
    return { ...cachedCloudInfo, current_version: curVer, available: computeUpdateAvailable(curVer, root, cachedCloudInfo) };
  }
  const cloudUrl = String(auth.cloud_url || process.env.ZENO_CLOUD_URL || DEFAULT_CLOUD_URL).replace(/\/+$/, '');
  try {
    const resp = await fetch(`${cloudUrl}/v1/version`, { headers: { Authorization: `Bearer ${auth.token}` } });
    if (!resp.ok) return base;
    const d = await resp.json();
    if (!d || !d.has_release) return base;
    const latestVer = String(d.version || curVer).trim();
    const remoteSha = String(d.bundle_sha256 || '').trim();
    const info = {
      available: computeUpdateAvailable(curVer, root, { latest_version: latestVer, bundle_sha256: remoteSha }),
      current_version: curVer,
      latest_version: latestVer,
      notes: String(d.notes || '').trim(),
      size_mb: Math.round((Number(d.size_bytes || 0) / 1048576) * 10) / 10,
      mandatory: !!d.mandatory,
      dmg_url: String(d.dmg_url || '').trim(),
      exe_url: String(d.exe_url || '').trim(),
      bundle_sha256: remoteSha,
    };
    cachedCloudInfo = info;
    cachedCloudAt = now;
    return info;
  } catch (e) {
    return base;
  }
}

function computeUpdateAvailable(curVer, root, remote) {
  if (!root) return true;
  const latestVer = String((remote && remote.latest_version) || curVer).trim();
  const remoteSha = String((remote && remote.bundle_sha256) || '').trim();
  const cmp = cmpVer(latestVer, curVer);
  if (cmp > 0) return true;
  if (cmp === 0 && remoteSha) {
    const st = readUpdateState(root);
    if (st.last_bundle_sha256 && st.last_bundle_sha256 !== remoteSha) return true;
  }
  return false;
}

async function applyCloudUpdateNative(auth, targetRoot) {
  if (!auth || !auth.token) return { ok: false, text: 'Please sign in with Google first.' };
  const cloudUrl = String(auth.cloud_url || process.env.ZENO_CLOUD_URL || DEFAULT_CLOUD_URL).replace(/\/+$/, '');
  const dest = targetRoot || path.join(os.homedir(), 'Documents', 'Zeno');
  let oldReqSha = '';
  try {
    const rq = path.join(dest, 'requirements.txt');
    if (fs.existsSync(rq)) oldReqSha = crypto.createHash('sha256').update(fs.readFileSync(rq)).digest('hex');
  } catch (e) { /* ignore */ }

  const resp = await fetch(`${cloudUrl}/v1/update-bundle`, { headers: { Authorization: `Bearer ${auth.token}` } });
  if (!resp.ok) {
    if (resp.status === 404) return { ok: false, text: 'No release bundle published on Zeno Cloud yet.' };
    return { ok: false, text: `Zeno Cloud returned HTTP ${resp.status}.` };
  }
  const expectedSha = String(resp.headers.get('x-zeno-bundle-sha256') || '').trim().toLowerCase();
  const headerVer = String(resp.headers.get('x-zeno-version') || '').trim();
  const buf = Buffer.from(await resp.arrayBuffer());
  const actualSha = crypto.createHash('sha256').update(buf).digest('hex').toLowerCase();
  if (expectedSha && expectedSha !== actualSha) {
    return { ok: false, text: 'Downloaded bundle checksum mismatch. Please retry.' };
  }

  const count = extractZipBuffer(buf, dest);
  const installedVer = headerVer || localVersion(dest);
  writeUpdateState(dest, {
    installed_version: installedVer,
    last_bundle_sha256: actualSha,
    last_checked_at: Date.now() / 1000,
    updated_at: Date.now() / 1000,
  });
  syncAuthToRoot(dest, auth);
  saveRoot(dest);
  cachedCloudInfo = null;

  // Refresh Python packages & ensure CUDA PyTorch + NVENC FFmpeg via gpu_install.py (never raw `pip -r` which overwrites CUDA torch!)
  try {
    const rq = path.join(dest, 'requirements.txt');
    const gpuScript = path.join(dest, 'scripts', 'gpu_install.py');
    const newReqSha = fs.existsSync(rq) ? crypto.createHash('sha256').update(fs.readFileSync(rq)).digest('hex') : '';
    if (hasVenv(dest) && fs.existsSync(gpuScript)) {
      if (oldReqSha && newReqSha && oldReqSha !== newReqSha) {
        spawn(venvPythonPath(dest), [gpuScript], { cwd: dest, detached: true, stdio: 'ignore' }).unref();
      } else if (!IS_MAC) {
        spawn(venvPythonPath(dest), [gpuScript, '--ensure-gpu'], { cwd: dest, detached: true, stdio: 'ignore' }).unref();
      }
    }
  } catch (e) { /* ignore */ }

  return { ok: true, updated: true, version: installedVer, files: count, root: dest, text: `Updated to Zeno v${installedVer}` };
}

function runJobs(root, args, timeout = 20000) {
  return new Promise((resolve) => {
    if (!root) return resolve({ error: 'Zeno folder not ready' });
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
  const extra = process.env.ZENO_CLAUDE_CONFIG_DIR ? { CLAUDE_CONFIG_DIR: process.env.ZENO_CLAUDE_CONFIG_DIR } : {};
  const auth = readAppAuth();
  const userEnv = auth && auth.email ? { ZENO_USER_EMAIL: String(auth.email).trim().toLowerCase(), ZENO_CLOUD_TOKEN: String(auth.token || '').trim() } : {};
  const home = process.env.HOME || '';
  return { ...process.env, ...extra, ...userEnv, PATH: [path.join(home, '.local', 'bin'), '/opt/homebrew/bin', '/usr/local/bin', process.env.PATH || ''].join(path.delimiter), ZENO_EVENTS: 'on' };
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
  const iconPath = path.join(__dirname, 'build', IS_WIN ? 'icon.ico' : 'icon.png');
  const winOpts = {
    width: size[0], height: size[1], minWidth: 980, minHeight: 640, backgroundColor: '#000000', show: !arg('screenshot'),
    titleBarStyle: IS_MAC ? 'hiddenInset' : 'default', trafficLightPosition: { x: 16, y: 16 }, title: 'Zeno',
    webPreferences: { preload: preloadEntry(ROOT), contextIsolation: true, nodeIntegration: false, sandbox: false },
  };
  if (fs.existsSync(iconPath)) winOpts.icon = iconPath;
  if (IS_MAC && app.dock && !app.isPackaged) {
    const macPng = path.join(__dirname, 'build', 'icon.png');
    if (fs.existsSync(macPng)) { try { app.dock.setIcon(macPng); } catch (e) { /* ignore */ } }
  }
  const win = new BrowserWindow(winOpts);
  win.loadFile(rendererEntry(ROOT), { query: { select: arg('select') || '' } });
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

let lastNotifiedVer = '';

app.whenReady().then(() => {
  ROOT = findRoot();
  if (ROOT) saveRoot(ROOT);

  const activeEmail = () => {
    if (arg('skip-auth') === '1') return 'preview@gosupernova.live';
    const a = readAppAuth();
    return a && a.email ? String(a.email).trim().toLowerCase() : '';
  };
  const jobOwnedBy = (jobDir, email) => {
    if (!email) return false;
    for (const [fn, key] of [['owner.json', 'owner_email'], ['progress.json', 'owner_email'], ['token_telemetry.json', 'user_email']]) {
      try {
        const p = path.join(jobDir, fn);
        if (fs.existsSync(p)) {
          const d = JSON.parse(fs.readFileSync(p, 'utf8'));
          const em = String((d && d[key]) || '').trim().toLowerCase();
          if (em) return em === email;
        }
      } catch (e) { /* continue */ }
    }
    return false;
  };

  ipcMain.handle('zeno:root', () => ROOT);
  ipcMain.handle('zeno:jobs', () => {
    const em = activeEmail();
    if (!em || !ROOT || !hasVenv(ROOT)) return [];
    return runJobs(ROOT, ['list', '--user', em]);
  });
  ipcMain.handle('zeno:detail', (_e, name, since) => {
    const em = activeEmail();
    if (!em) return { error: 'Not signed in' };
    if (!ROOT || !hasVenv(ROOT)) return { error: 'Engine not ready' };
    return runJobs(ROOT, ['detail', String(name), '--since', String(since || 0), '--user', em]);
  });

  ipcMain.handle('zeno:info', async (_e, check) => {
    if (!ROOT) ROOT = findRoot();
    const auth = readAppAuth();
    const signedIn = arg('skip-auth') === '1'
      ? 'preview@gosupernova.live'
      : (auth ? String(auth.email || auth.name || 'Signed in') : '');
    if (auth && ROOT) {
      let needSync = true;
      try {
        const sp = path.join(ROOT, '.zeno_session.json');
        if (fs.existsSync(sp)) {
          const cur = JSON.parse(fs.readFileSync(sp, 'utf8'));
          needSync = !cur || cur.token !== auth.token || String(cur.email || '').toLowerCase() !== String(auth.email || '').toLowerCase();
        }
      } catch (e) { needSync = true; }
      if (needSync) syncAuthToRoot(ROOT, auth);
    }
    const ver = localVersion(ROOT);
    const st = readUpdateState(ROOT);
    const update = (auth && check)
      ? await checkCloudUpdateNative(auth, ROOT, check === 'force')
      : { available: false, current_version: ver, latest_version: ver, notes: '', size_mb: 0, mandatory: false, dmg_url: '', exe_url: '', bundle_sha256: st.last_bundle_sha256 || '' };

    if (update && update.available && update.latest_version && update.latest_version !== lastNotifiedVer) {
      lastNotifiedVer = update.latest_version;
      try {
        if (Notification && Notification.isSupported()) {
          new Notification({ title: `New Zeno Update Arrived (v${update.latest_version})`, body: update.notes || 'Zeno is installing it now.' }).show();
        }
      } catch (e) { /* ignore notification errors */ }
    }
    return {
      version: ver,
      sha: (st.last_bundle_sha256 || '').slice(0, 7),
      behind: update && update.available ? 1 : 0,
      update,
      root: ROOT,
      engine_ready: !!(ROOT && hasVenv(ROOT)),
      signed_in: signedIn,
    };
  });

  ipcMain.handle('zeno:reveal', (_e, p) => { if (p && fs.existsSync(p)) shell.showItemInFolder(p); });
  ipcMain.handle('zeno:open', (_e, p) => { if (p && fs.existsSync(p)) return shell.openPath(p); });
  ipcMain.handle('zeno:newEdit', () => {
    if (!ROOT) return false;
    if (IS_MAC) spawn('open', ['-a', 'Terminal', path.join(ROOT, 'edit.command')], { detached: true, stdio: 'ignore' }).unref();
    else if (IS_WIN) spawn('cmd', ['/c', 'start', '""', path.join(ROOT, 'edit.bat')], { detached: true, stdio: 'ignore', windowsVerbatimArguments: true }).unref();
    else spawn('x-terminal-emulator', ['-e', 'bash', path.join(ROOT, 'edit.command')], { detached: true, stdio: 'ignore' }).unref();
    return true;
  });
  ipcMain.handle('zeno:runSetup', () => {
    const target = ROOT || path.join(os.homedir(), 'Documents', 'Zeno');
    if (!fs.existsSync(target)) return { ok: false, text: 'Zeno workspace not downloaded yet.' };
    if (IS_MAC) spawn('open', ['-a', 'Terminal', path.join(target, 'install.command')], { detached: true, stdio: 'ignore' }).unref();
    else if (IS_WIN) spawn('cmd', ['/c', 'start', '""', path.join(target, 'install.bat')], { detached: true, stdio: 'ignore', windowsVerbatimArguments: true }).unref();
    else spawn('x-terminal-emulator', ['-e', 'bash', path.join(target, 'install.command')], { detached: true, stdio: 'ignore' }).unref();
    return { ok: true };
  });
  // the editor's Yes: render the approved plan (reel and square) in the background; the app then follows it live
  ipcMain.handle('zeno:render', (_e, name, planFile) => {
    const em = activeEmail();
    if (!em || !ROOT || !/^[\w.\-]+$/.test(String(name)) || !/^[\w.\-]+\.edl$/.test(String(planFile))) return { ok: false, text: 'Not a valid job.' };
    const jobDir = path.join(ROOT, 'work', String(name));
    if (!jobOwnedBy(jobDir, em)) return { ok: false, text: 'This project belongs to another account.' };
    const plan = path.join(jobDir, String(planFile));
    if (!fs.existsSync(plan)) return { ok: false, text: 'The plan file is missing.' };
    const log = fs.openSync(path.join(jobDir, '_render.log'), 'a');
    const child = spawn(pythonFor(ROOT), [path.join(ROOT, 'scripts', 'zeno.py'), '--execute', plan, '--square'], { cwd: ROOT, detached: true, stdio: ['ignore', log, log], env: { ...process.env, ZENO_USER_EMAIL: em, PYTHONIOENCODING: 'utf-8' } });
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
  ipcMain.handle('zeno:claudeLogin', async (_e, opts) => {
    const o = opts || {};
    if (o.switch) await runClaude(['auth', 'logout'], 20000);
    const bin = claudePath(); if (!bin) return { ok: false, text: 'Claude Code is not installed on this computer.' };
    const args = ['auth', 'login'].concat(o.email ? ['--email', String(o.email)] : []);
    spawn(bin, args, { env: claudeEnv(), detached: true, stdio: 'ignore' }).unref();
    return { ok: true };
  });
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
  ipcMain.handle('zeno:chatSend', (e, message, sessionId, opts) => {
    const bin = claudePath(); if (!bin || !ROOT) return { ok: false, text: 'Claude Code is not installed on this computer.' };
    if (chatProc) return { ok: false, text: 'Claude is still working on the last message.' };
    const send = (evt) => { try { e.sender.send('zeno:chat', evt); } catch (er) { /* window closed */ } };
    const args = ['-p', String(message), '--output-format', 'stream-json', '--verbose', '--permission-mode', 'acceptEdits',
      '--allowedTools', 'Bash(python3:*)', 'Bash(python:*)', 'Bash(.venv/bin/python:*)', 'Bash(.venv/Scripts/python.exe:*)', 'Bash(ls:*)', 'Bash(grep:*)', 'Bash(cat:*)', 'Read', 'Write', 'Edit', 'Glob', 'Grep', 'mcp__zeno'];
    args.push('--strict-mcp-config', '--mcp-config', JSON.stringify({ mcpServers: { [ZENO_MCP_NAME]: { command: pythonFor(ROOT), args: [path.join(ROOT, 'scripts', 'zeno_mcp.py')] } } }));
    const o = opts || {};
    if (/^[\w.\-]+$/.test(String(o.model || ''))) args.push('--model', String(o.model));
    if (['low', 'medium', 'high', 'xhigh', 'max'].includes(String(o.effort || ''))) args.push('--effort', String(o.effort));
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

  ipcMain.handle('zeno:update', async () => {
    const auth = readAppAuth();
    const res = await applyCloudUpdateNative(auth, ROOT || path.join(os.homedir(), 'Documents', 'Zeno'));
    if (res && res.ok && res.root) ROOT = res.root;
    return res;
  });

  ipcMain.handle('zeno:reload', (e) => {
    const win = BrowserWindow.fromWebContents(e.sender);
    if (win) win.loadFile(rendererEntry(ROOT));
    return true;
  });

  // Browser-based Google Workspace Sign-In: opens the Zeno Cloud login page in the browser, polls /api/auth/cli-poll,
  // saves desktop session, Cloud-bootstraps ~/Documents/Zeno automatically on fresh client machines, and focuses Zeno.
  const CLOUD = (process.env.ZENO_CLOUD_URL || DEFAULT_CLOUD_URL).replace(/\/+$/, '');
  let signInBusy = false;
  ipcMain.handle('zeno:signIn', async () => {
    if (signInBusy) return { ok: false, text: 'Sign-in is already open in your browser.' };
    signInBusy = true;
    try {
      const state = 'st_' + crypto.randomBytes(18).toString('base64url');
      shell.openExternal(`${CLOUD}/?cli_state=${encodeURIComponent(state)}&cli=1`);
      const deadline = Date.now() + 5 * 60 * 1000;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 1500));
        let d = null;
        try { d = await (await fetch(`${CLOUD}/api/auth/cli-poll?cli_state=${encodeURIComponent(state)}`)).json(); } catch (e) { continue; }
        if (d && d.ready && (d.token || d.key)) {
          const authPayload = {
            token: String(d.token || d.key).trim(),
            email: String(d.email || '').trim().toLowerCase(),
            name: String(d.name || '').trim(),
            cloud_url: CLOUD,
            authenticated_at: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'),
          };
          writeAppAuth(authPayload);
          if (!ROOT) ROOT = findRoot();
          let bootstrapped = false;
          if (!ROOT) {
            const target = path.join(os.homedir(), 'Documents', 'Zeno');
            const pull = await applyCloudUpdateNative(authPayload, target);
            if (pull && pull.ok) { ROOT = target; bootstrapped = true; }
          }
          if (ROOT) {
            saveRoot(ROOT);
            syncAuthToRoot(ROOT, authPayload);
          }
          const w = BrowserWindow.getAllWindows()[0];
          if (w) { if (w.isMinimized()) w.restore(); w.show(); w.focus(); }
          return { ok: true, email: authPayload.email, name: authPayload.name, root: ROOT, bootstrapped };
        }
      }
      return { ok: false, text: 'Sign-in timed out. Please click Sign in with Google again.' };
    } catch (err) {
      return { ok: false, text: String((err && err.message) || err) };
    } finally {
      signInBusy = false;
    }
  });

  ipcMain.handle('zeno:signOut', () => {
    clearAppAuth();
    if (ROOT) clearRootAuth(ROOT);
    cachedCloudInfo = null;
    return { ok: true };
  });

  ipcMain.handle('zeno:openExternal', (_e, u) => {
    if (u && /^https?:\/\//i.test(String(u))) shell.openExternal(String(u));
    return true;
  });

  createWindow();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});
app.on('window-all-closed', () => { if (!IS_MAC || arg('screenshot')) app.quit(); });
