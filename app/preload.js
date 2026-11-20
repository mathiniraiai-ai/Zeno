const { contextBridge, ipcRenderer } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

function cleanVer(v) {
  return String(v || '').trim().replace(/^v/i, '');
}
function semverKey(v) {
  const m = cleanVer(v).match(/^(\d+)\.(\d+)\.(\d+)/);
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : [0, 0, 0];
}
function cmpVer(a, b) {
  const ka = semverKey(a), kb = semverKey(b);
  for (let i = 0; i < 3; i++) { if (ka[i] !== kb[i]) return ka[i] - kb[i]; }
  return 0;
}
function normalizeReleaseNotes(raw) {
  return String(raw || '').replace(/\s+/g, ' ').trim();
}
function persistNotesState(root, notes, notesVer) {
  if (!root || !notes) return;
  try {
    const dir = path.join(root, 'work');
    const sf = path.join(dir, '_update_state.json');
    let cur = {};
    if (fs.existsSync(sf)) {
      cur = JSON.parse(fs.readFileSync(sf, 'utf8')) || {};
    }
    if (normalizeReleaseNotes(cur.notes) === notes && cleanVer(cur.notes_version) === cleanVer(notesVer)) return;
    fs.mkdirSync(dir, { recursive: true });
    const merged = { ...cur, notes, notes_version: notesVer };
    const tmp = `${sf}.tmp_${process.pid}`;
    fs.writeFileSync(tmp, JSON.stringify(merged, null, 2) + '\n', 'utf8');
    fs.renameSync(tmp, sf);
  } catch (e) { /* ignore */ }
}
function localReleaseNotes(root, version) {
  const ver = cleanVer(version);
  // 1. Persisted OTA notes in work/_update_state.json (when matching current version)
  if (root) {
    try {
      const sf = path.join(root, 'work', '_update_state.json');
      if (fs.existsSync(sf)) {
        const st = JSON.parse(fs.readFileSync(sf, 'utf8')) || {};
        const stNotes = normalizeReleaseNotes(st.notes);
        const stVer = cleanVer(st.notes_version);
        if (stNotes && (!stVer || !ver || stVer === ver)) return stNotes;
      }
    } catch (e) { /* ignore */ }
  }
  // 2. Shipped/local RELEASE_NOTES.txt in workspace root
  const notePaths = [...new Set([
    root ? path.join(root, 'RELEASE_NOTES.txt') : null,
    path.join(__dirname, '..', 'RELEASE_NOTES.txt'),
  ].filter(Boolean))];
  for (const np of notePaths) {
    try {
      if (fs.existsSync(np)) {
        const txt = normalizeReleaseNotes(fs.readFileSync(np, 'utf8'));
        if (txt) return txt;
      }
    } catch (e) { /* ignore */ }
  }
  // 3. Cached CLI/auto-updater check payload in work/_update_check.json
  if (root) {
    try {
      const cf = path.join(root, 'work', '_update_check.json');
      if (fs.existsSync(cf)) {
        const chk = JSON.parse(fs.readFileSync(cf, 'utf8')) || {};
        const chkNotes = normalizeReleaseNotes(chk.notes);
        const chkVer = cleanVer(chk.latest_version);
        if (chkNotes && ver && chkVer === ver) return chkNotes;
      }
    } catch (e) { /* ignore */ }
  }
  return '';
}
function venvPy(root) {
  const p = process.platform === 'win32' ? path.join(root, '.venv', 'Scripts', 'python.exe') : path.join(root, '.venv', 'bin', 'python');
  return fs.existsSync(p) ? p : (process.platform === 'win32' ? 'python' : 'python3');
}
let cleanupSpawnedFor = '';
// Runs clean.py --after-update when an older compiled Zeno shell (app.asar) extracted an OTA bundle without pruning.
function triggerPendingCleanup(root, version) {
  try {
    if (!root || process.argv.some((a) => String(a).startsWith('--screenshot='))) return;
    if (fs.existsSync(path.join(root, '.zeno-dev')) && process.env.ZENO_ALLOW_DEV_OTA_PRUNE !== '1') return;
    const cleanScript = path.join(root, 'scripts', 'clean.py');
    if (!fs.existsSync(cleanScript)) return;
    const ver = cleanVer(version);
    const key = `${root}|${ver}`;
    if (cleanupSpawnedFor === key) return;
    const markers = [
      '_zeno_manifest.json',
      'agent',
      'styles',
      'CONCEPT.md',
      'LOW_COST_WORKFLOW.md',
      path.join('scripts', 'zeno_mcp.py'),
      path.join('scripts', 'ai_director.py'),
      path.join('scripts', 'compact_run.py'),
    ];
    const needed = markers.some((rel) => fs.existsSync(path.join(root, rel)));
    if (!needed) return;
    cleanupSpawnedFor = key;
    const args = [cleanScript, '--after-update'];
    if (ver) args.push('--version', ver);
    spawn(venvPy(root), args, {
      cwd: root,
      detached: true,
      windowsHide: true,
      stdio: 'ignore',
    }).unref();
  } catch (e) { /* ignore */ }
}
// Shows the Zeno-styled update toast once per installed version (re-shown after a fresh OTA install).
function showUpdateToast(root, version, notes) {
  try {
    if (!root || !version || !notes) return;
    if (process.argv.some((a) => String(a).startsWith('--screenshot='))) return;
    const script = path.join(root, 'scripts', 'notify_popup.py');
    if (!fs.existsSync(script)) return;
    const sf = path.join(root, 'work', '_update_state.json');
    let cur = {};
    try { cur = JSON.parse(fs.readFileSync(sf, 'utf8')) || {}; } catch (e) { /* none */ }
    if (cleanVer(cur.toast_version) === cleanVer(version)) return;
    fs.mkdirSync(path.dirname(sf), { recursive: true });
    fs.writeFileSync(sf, JSON.stringify({ ...cur, toast_version: cleanVer(version) }, null, 2) + '\n', 'utf8');
    spawn(venvPy(root), [script, '--notes', notes, '--title', `Zeno v${cleanVer(version)} is ready`, '--badge', 'Updated'],
      { detached: true, windowsHide: true, stdio: 'ignore' }).unref();
  } catch (e) { /* ignore */ }
}
function enrichInfo(res) {
  if (!res || typeof res !== 'object' || res.error) return res;
  const shippedRoot = res.root || path.resolve(__dirname, '..');
  triggerPendingCleanup(shippedRoot, res.version);
  let shipped = '';
  try { shipped = normalizeReleaseNotes(fs.readFileSync(path.join(shippedRoot, 'RELEASE_NOTES.txt'), 'utf8')); } catch (e) { /* none */ }
  const existing = shipped || normalizeReleaseNotes(res.release_notes);
  if (existing) {
    showUpdateToast(shippedRoot, res.version, existing);
    return {
      ...res,
      release_notes: existing,
      release_notes_version: String(res.release_notes_version || res.version || '').trim(),
    };
  }
  // Backward-compatible enrichment for installed Zeno.app / Zeno.exe binaries whose bundled main.js predates release_notes.
  const ver = String(res.version || '').trim();
  const root = res.root || path.resolve(__dirname, '..');
  const upd = res.update || {};
  const liveNotes = normalizeReleaseNotes(upd.notes);
  const sameVer = !!(upd.latest_version && cleanVer(upd.latest_version) === cleanVer(ver));
  const liveMatchesOrAhead = !!(upd && (!upd.latest_version || cmpVer(upd.latest_version, ver) >= 0));
  const useLiveNotes = !!(liveNotes && ((!upd.available && liveMatchesOrAhead) || sameVer));
  if (useLiveNotes && root) {
    persistNotesState(root, liveNotes, String(upd.latest_version || ver).trim());
  }
  const localNotes = localReleaseNotes(root, ver);
  const releaseNotes = useLiveNotes ? liveNotes : (localNotes || ((!upd.available && liveNotes) ? liveNotes : ''));
  const releaseNotesVersion = useLiveNotes
    ? String(upd.latest_version || ver).trim()
    : (localNotes ? ver : String(upd.latest_version || ver).trim());
  return {
    ...res,
    release_notes: releaseNotes,
    release_notes_version: releaseNotesVersion,
  };
}

contextBridge.exposeInMainWorld('zeno', {
  info: async (check) => enrichInfo(await ipcRenderer.invoke('zeno:info', check)),
  openExternal: (u) => ipcRenderer.invoke('zeno:openExternal', u),
  update: () => ipcRenderer.invoke('zeno:update'),
  reload: () => ipcRenderer.invoke('zeno:reload'),
  signIn: () => ipcRenderer.invoke('zeno:signIn'),
  runSetup: () => ipcRenderer.invoke('zeno:runSetup'),
  platform: process.platform,
});
