// Zeno desktop app: shows every edit and what the backend is doing, step by step. It only reads job state through the bridge in preload.js.
'use strict';
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n) => (n == null ? '–' : Number(n).toLocaleString());
const mmss = (s) => { s = Math.round(s || 0); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const hms = (t) => new Date(t * 1000).toTimeString().slice(0, 8);
const ago = (t) => { const s = Math.max(0, Date.now() / 1000 - t); if (s < 60) return 'just now'; if (s < 3600) return `${Math.floor(s / 60)} min ago`; if (s < 86400) return `${Math.floor(s / 3600)} h ago`; return `${Math.floor(s / 86400)} d ago`; };

const state = { tab: 'faces', scans: [], scanIdx: 0, jobs: [], selected: new URLSearchParams(location.search).get('select') || null, detail: null, events: [], last: 0, filter: 'all', info: null, busy: false };
const KIND = { face: ['FACE', 'face'], speech: ['VOICE', 'speech'], stage: ['STEP', 'stage'], cut: ['CUT', 'cut'], render: ['REND', 'render'], check: ['CHK', 'check'], verify: ['QA', 'verify'], subs: ['SUBS', 'subs'], info: ['INFO', 'info'], warn: ['WARN', 'warn'], error: ['ERR', 'error'] };

// ---------------------------------------------------------------- sidebar
function renderSide() {
  const label = (j) => (j.status.phase === 'working' ? `${esc(j.stage_name || 'Working')} ${j.pct || 0}%` : esc(shortStatus(j.status)));
  $('#jobs').innerHTML = state.jobs.map((j) =>
    `<li data-name="${esc(j.name)}" class="${j.name === state.selected ? 'sel' : ''}"><span class="dot ${j.status.phase}"></span><div><div class="j-title">${esc(shortTitle(j.title))}</div><div class="j-meta">${label(j)} · ${ago(j.updated)}</div></div></li>`).join('') || '<li class="muted" style="cursor:default">No edits yet</li>';
  fitList();
  const i = state.info;
  if (i) {
    $('#ver').textContent = i.version ? `v${i.version}` : '';
    $('#who').textContent = i.signed_in || 'Not signed in';
    $('#upd').innerHTML = i.behind > 0 ? `<span class="chip" id="doUpdate">Update (${i.behind})</span>` : (i.behind === 0 ? '<span class="chip ok">Up to date</span>' : '<span class="chip ok">Offline</span>');
  }
}
// no scrolling: hide the edits that do not fit and say how many there are
function fitList() {
  const ul = $('#jobs'); const items = [...ul.querySelectorAll('li[data-name]')];
  items.forEach((li) => { li.style.display = ''; });
  const old = ul.querySelector('.more'); if (old) old.remove();
  const bottom = ul.getBoundingClientRect().bottom - 26;       // keep room for the "+N older" line
  let hidden = 0;
  items.forEach((li) => { if (li.getBoundingClientRect().bottom > bottom) { li.style.display = 'none'; hidden++; } });
  if (hidden) { const m = document.createElement('li'); m.className = 'more'; m.textContent = `+${hidden} older`; ul.appendChild(m); }
}
const shortTitle = (t) => String(t).replace(/^W\d+\s*-\s*/, '').replace(/\s*-\s*(Replication|Competitor|Xs|Abhishek|Balesh|Disha|Sandeep|Aliva).*$/i, '').replace(/AI Only\s*-\s*/i, '');
const shortStatus = (st) => ({ plan: 'Waiting for Yes', measured: 'Measured', done: 'Rendered', failed: 'Failed', stalled: 'No update', new: 'New' }[st.phase] || st.label);

// ---------------------------------------------------------------- main view
function pipeHtml(d) {
  const names = ['Scan', 'Words', 'Match', 'Edit', 'Finish'];
  const ph = d.status.phase; const cur = d.stage || 0;
  let done = 0, waitAt = -1;
  if (ph === 'done') done = 5; else if (ph === 'plan' || ph === 'measured') { done = 3; waitAt = 3; } else if (ph === 'working' || ph === 'stalled' || ph === 'failed') done = Math.max(0, cur - 1);
  // the engine's five stages are Mapping, Transcribing, Matching, Editing, Mastering
  const out = [];
  names.forEach((name, i) => {
    const cls = i < done ? 'done' : (i === waitAt ? 'wait' : (i === cur - 1 && (ph === 'working' || ph === 'stalled') ? 'active' : ''));
    out.push(`<div class="step ${cls}"><div class="c">${cls === 'done' ? '✓' : i + 1}</div>${name}</div>`);
    if (i < names.length - 1) out.push(`<div class="bar"><i style="width:${i < done ? 100 : (i === cur - 1 && ph === 'working' ? Math.min(100, ((d.pct || 0) % 20) * 5) : 0)}%"></i></div>`);
  });
  return out.join('');
}

function timelineHtml(d) {
  const p = d.plan;
  if (!p || !p.timeline.length) return '<div class="empty">The timeline appears when the editor has written a plan.</div>';
  const total = p.seconds || 1;
  const segs = p.timeline.map((s) => {
    const split = /^split|top_pip|left_right|top_bottom/.test(s.layout), other = !split && s.layout !== 'single';
    const tip = `${s.id} · ${s.src} · ${s.layout}${s.zoom ? ' · zoom ' + s.zoom : ''}${s.transition !== 'cut' ? ' · ' + s.transition : ''} · ${s.dur}s`;
    return `<div class="seg ${split ? 'split' : other ? 'other' : ''}" title="${esc(tip)}" style="left:${(s.start / total) * 100}%;width:${(s.dur / total) * 100}%">${s.dur / total > 0.045 ? `<b>${esc(s.id)}</b>` : ''}</div>` +
      (s.transition !== 'cut' ? `<div class="tick" style="left:${(s.start / total) * 100}%"></div>` : '');
  }).join('');
  return `<div class="tl-wrap"><div class="tl">${segs}</div><div class="tl-scale"><span>0:00</span><span>${mmss(total / 2)}</span><span>${mmss(total)}</span></div></div>`;
}

function feedText(e) {
  const raw = String(e.text || '');
  if (/\/Users|\\Users|Clip pictures|per line:|Plan to write/.test(raw)) return '';
  let tx = raw.replace(/(\/Users|\/home|[A-Za-z]:\\)[^\s]*/g, '').replace(/\s+/g, ' ').trim();
  if (e.kind === 'stage') tx = tx.replace(/^[^:]*:\s*/, '').replace(/\s*\(work\/[^)]*\)/, '');
  return tx.slice(0, 150);
}
function feedHtml() {
  const rows = state.events.map((e) => ({ e, tx: feedText(e) })).filter((r) => r.tx).slice(-40);
  if (!rows.length) return '<div class="empty">Steps appear here as the engine works.</div>';
  return rows.map(({ e, tx }, i) => {
    const [ic] = KIND[e.kind] || KIND.info;
    const bad = e.ok === false ? ' bad' : (e.ok === true ? ' ok' : '');
    const last = i === rows.length - 1 && state.detail && state.detail.status.phase === 'working' ? ' last' : '';
    return `<div class="ev ${e.kind}${bad}${last}"><span class="tm">${hms(e.t)}</span><span class="ic">${ic}</span><span class="tx" title="${esc(tx)}">${esc(tx)}</span></div>`;
  }).join('');
}

function footageHtml(d) {
  if (!d.clips.length) return '<div class="empty">Each clip appears here as it is scanned.</div>';
  const rows = d.clips.map((c) => {
    const blocks = c.spans.map((s) => `<i style="left:${Math.max(0, s[0]) * 100}%;width:${Math.max(1, (s[1] - s[0]) * 100)}%"></i>`).join('');
    return `<div class="clip" title="${esc(c.clip)}${c.silent ? ' · silent' : ''}"><div class="n">${esc(c.clip.replace(/\.[^.]+$/, ''))}</div><div class="fbar ${c.silent ? 'silent' : ''}">${blocks}</div><div class="m">${c.silent ? '–' : c.faces}</div></div>`;
  }).join('');
  return `<div class="clips">${rows}</div>`;
}

function statsLine(d) {
  const p = d.plan; const reel = d.outputs.find((o) => o.kind === 'reel' && o.mb > 1);
  const v = d.verify || []; const bad = v.filter((x) => !x.ok).length;
  const chk = v.length ? `${v.length - bad}/${v.length} checks` : (d.plan_check ? (d.plan_check.result === 'passed' ? 'plan ok' : 'plan blocked') : '');
  const bits = [];
  if (reel) bits.push(`<span><b>${reel.mb} MB</b> reel</span>`);
  if (chk) bits.push(`<span class="${bad || (d.plan_check && d.plan_check.result !== 'passed' && !v.length) ? 'warn' : 'good'}"><b>${chk}</b></span>`);
  if (d.tokens && d.tokens.total && !d.tokens.mixed) bits.push(`<span><b>${(d.tokens.total / 1000).toFixed(0)}k</b> tokens</span>`);
  if (p && p.subs) bits.push(`<span><b>${esc(d.subs ? d.subs.style : p.subs)}</b> subtitles</span>`);
  const out = d.outputs.find((o) => ['reel', 'video'].includes(o.kind) && o.mb > 1);
  const act = out ? `<span class="acts"><button class="btn" data-reveal="${esc(out.path)}">Show in folder</button><button class="btn" data-open="${esc(out.path)}">Play</button></span>` : '';
  return `<div class="tl-foot">${bits.join('')}${act}</div>`;
}

function picsHtml(d) {
  const im = d.images || {};
  const items = [['clip_sheet', 'Clips'], ['review_sheet', 'Every cut'], ['marks', 'Marks']].filter(([k]) => im[k]).slice(0, 3);
  if (!items.length) return '<div class="empty">Pictures appear as they are made.</div>';
  const v = Math.floor(Date.now() / 60000);
  return `<div class="pics">${items.map(([k, n]) => `<figure data-zoom="${esc(im[k])}" data-title="${esc(n)}"><img src="file://${encodeURI(im[k])}?v=${v}" alt="${esc(n)}"><figcaption>${n}</figcaption></figure>`).join('')}</div>`;
}

function mainHtml(d) {
  if (!d) {
    return `<div class="top"><h1>Zeno</h1></div><div class="body" style="display:flex"><div class="welcome"><h1>No edit selected</h1><div>${state.jobs.length ? 'Pick an edit on the left.' : 'Press New edit to start one. This window then shows every step.'}</div></div></div>`;
  }
  const s = d.status;
  return `<div class="top"><h1 title="${esc(d.title)}">${esc(shortTitle(d.title))}</h1><button class="btn vbtn" data-vision>Live vision</button><span class="pill ${s.phase}">${esc(s.phase === 'working' ? `Working${d.elapsed ? ' · ' + d.elapsed : ''}` : shortStatus(s))}</span></div>
  <div class="pipe">${pipeHtml(d)}</div>
  <div class="body">
    <div class="col">
      <div class="card"><h2>Timeline<span>${d.plan ? `${d.plan.cuts} cuts · ${mmss(d.plan.seconds)}` : ''}</span></h2>${timelineHtml(d)}${statsLine(d)}</div>
      <div class="card fill"><h2>Live<span>${s.phase === 'working' ? 'working' : ''}</span></h2><div id="feed">${feedHtml()}</div></div>
    </div>
    <div class="col">
      <div class="card fill"><h2><span class="tabs"><button data-tab="faces" class="${state.tab === 'faces' ? 'on' : ''}">Faces</button><button data-tab="scan" class="${state.tab === 'scan' ? 'on' : ''}">Scan</button></span><span>${state.tab === 'faces' ? (d.clips.length ? d.clips.length + ' clips' : '') : 'frame by frame'}</span></h2>${state.tab === 'faces' ? footageHtml(d) : '<div id="scanview" class="scan"></div>'}</div>
      <div class="card"><h2>Pictures<span>click to enlarge</span></h2>${picsHtml(d)}</div>
    </div>
  </div>`;
}

let lastMain = '';
function renderMain() {
  const html = mainHtml(state.detail);
  if (html === lastMain) return;
  lastMain = html;
  $('#main').innerHTML = html;
  fitClips();
  paintScan();
}
// the scan view: plays the scanned frames in a loop (the newest frame stays up while a scan is running)
function paintScan() {
  const box = $('#scanview'); if (!box) return;
  const list = state.scans;
  if (!list.length) { box.innerHTML = '<div class="empty">No frame scans yet. Run: python3 scripts/vision_scan.py "&lt;clip&gt;"</div>'; return; }
  const live = Date.now() - (state.lastScanAt || 0) < 4000;
  const i = live ? list.length - 1 : state.scanIdx % list.length;
  const e = list[i]; const w = (e.size && e.size[0]) || 9, h = (e.size && e.size[1]) || 16;
  const faces = (e.faces || []).map((f, k) => {
    const b = f.box || [0, 0, 0, 0]; const mo = f.mouth;
    const eye = f.eyes ? `<i class="eye" style="left:${f.eyes[0] * 100}%;top:${f.eyes[1] * 100}%"></i>` : '';
    const mouth = mo ? `<i class="mouth ${f.speaking ? 'talk' : ''}" style="left:${mo[0] * 100}%;top:${mo[1] * 100}%;width:${(mo[2] - mo[0]) * 100}%;height:${(mo[3] - mo[1]) * 100}%"></i>` : '';
    return `<div class="fbox" style="left:${b[0] * 100}%;top:${b[1] * 100}%;width:${(b[2] - b[0]) * 100}%;height:${(b[3] - b[1]) * 100}%"><b>${k + 1}</b></div>${eye}${mouth}`;
  }).join('');
  const rows = (e.faces || []).map((f, k) => `<div class="srow"><span>Face ${k + 1}</span><span class="${f.speaking ? 'talk' : ''}">${f.speaking ? 'mouth open' : 'mouth closed'}</span></div>`).join('') || '<div class="srow"><span>No face</span><span></span></div>';
  const dots = list.map((x, k) => `<i class="${k === i ? 'cur' : ''} ${x.faces && x.faces.length ? '' : 'none'}"></i>`).join('');
  box.innerHTML = `<div class="sframe" style="aspect-ratio:${w}/${h}"><img src="file://${encodeURI(e.thumb)}" alt="">${faces}</div>
    <div class="sinfo"><div class="sclip" title="${esc(e.clip)}">${esc(e.clip)}</div><div class="muted">frame ${e.i}${e.n ? '/' + e.n : ''} · ${e.t_clip}s</div><div class="shead"><b>${(e.faces || []).length}</b> face${(e.faces || []).length === 1 ? '' : 's'} · ${e.ms ? Math.round(e.ms) + ' ms' : 'scan'}</div>${rows}<div class="muted sfoot">${live ? 'scanning live' : 'replaying the scan'}</div></div>
    <div class="sdots">${dots}</div>`;
}
setInterval(() => { if (state.tab === 'scan' && state.scans.length) { state.scanIdx = (state.scanIdx + 1) % state.scans.length; paintScan(); } }, 380);


// ---------------------------------------------------------------- live vision: a large reel-shaped view of the face scan
const vis = { open: false, frames: [], idx: 0, since: 0, clip: null, lastNew: 0, shown: 0, timers: [], ms: [], trail: [[], [], [], []] };
const posWord = (f) => { const cx = f.box ? (f.box[0] + f.box[2]) / 2 : 0.5; return cx < 0.38 ? 'left' : cx > 0.62 ? 'right' : 'centre'; };
function openVision() {
  if (!state.detail) return;
  vis.open = true; vis.frames = []; vis.idx = 0; vis.since = 0; vis.clip = null; vis.ms = []; vis.trail = [[], [], [], []];
  const b = document.createElement('div'); b.id = 'vision';
  const clips = (state.detail.clips || []).filter((c) => !c.silent || true);
  b.innerHTML = `<div class="v-top"><div><b>Live vision</b><span class="muted"> · YuNet face detector, frame by frame</span></div><div><button class="btn" id="vClose">Close</button></div></div>
    <div class="v-body">
      <div class="v-stage"><div class="vframe" id="vframe"><img id="vimg" alt=""><div class="vsweep"></div><div id="vover"></div>
        <div class="vhud tl" id="vhudTl">waiting for a scan…</div><div class="vhud tr" id="vhudTr"></div><div class="vhud bl" id="vhudBl"></div></div></div>
      <div class="v-side">
        <div class="v-card"><h3>How it scans</h3><ol class="v-steps"><li>Read one frame of the clip</li><li>The face detector finds every face and its eyes, nose and mouth</li><li>The tracker keeps each face's path across frames</li></ol></div>
        <div class="v-card"><h3>Faces in this frame</h3><div id="vfaces" class="muted">–</div></div>
        <div class="v-card"><h3>Detection time <span class="muted" id="vms"></span></h3><svg id="vspark" viewBox="0 0 200 36" preserveAspectRatio="none"></svg></div>
        <div class="v-card grow"><h3>Scan a clip live</h3><div id="vclips">${clips.length ? clips.map((c) => `<button class="vclip" data-scan="${esc(c.clip)}">${esc(c.clip.replace(/\.[^.]+$/, ''))}${c.silent ? ' <i>silent</i>' : ''}</button>`).join('') : '<span class="muted">No clips listed yet.</span>'}</div></div>
      </div>
    </div><div class="v-bar" id="vbar"></div>`;
  document.body.appendChild(b);
  vis.timers.push(setInterval(pollVision, 250));
  vis.timers.push(setInterval(stepVision, 70));
}
function closeVision() { vis.open = false; vis.timers.forEach(clearInterval); vis.timers = []; const b = $('#vision'); if (b) b.remove(); }
async function pollVision() {
  if (!vis.open || !state.selected) return;
  const ev = await window.zeno.scanEvents(state.selected, vis.since);
  if (!ev.length) return;
  vis.since = ev[ev.length - 1].id; vis.lastNew = Date.now();
  // follow only the newest run: a frame 1 starts a run, so everything before the last frame 1 is an older scan
  let start = -1;
  ev.forEach((e, k) => { if (e.i === 1) start = k; });
  let fresh = ev;
  if (start >= 0) { fresh = ev.slice(start); vis.frames = []; vis.idx = 0; vis.trail = [[], [], [], []]; vis.ms = []; }
  const clip = fresh[fresh.length - 1].clip;
  vis.clip = clip;
  vis.frames = vis.frames.concat(fresh.filter((e) => e.clip === clip)).slice(-200);
}
function stepVision() {
  if (!vis.open || !vis.frames.length) return;
  const live = Date.now() - vis.lastNew < 2500;
  const now = Date.now();
  const cur = vis.frames[Math.min(vis.idx, vis.frames.length - 1)];
  const nxt = vis.frames[vis.idx + 1];
  const wait = nxt ? Math.max(60, Math.min(260, ((nxt.t_clip || 0) - (cur.t_clip || 0)) * 1000)) : 140;
  if (vis.shown && now - vis.shown < wait) return;
  if (nxt) { vis.idx++; } else if (!live) { vis.idx = 0; vis.trail = [[], [], [], []]; } else { if (vis.shown && now - vis.shown < 400) return; }
  vis.shown = now; paintVision(vis.frames[Math.min(vis.idx, vis.frames.length - 1)], live);
}
function paintVision(e, live) {
  if (!e) return;
  const fr = $('#vframe'); if (!fr) return;
  const w = (e.size && e.size[0]) || 9, h = (e.size && e.size[1]) || 16; fr.style.aspectRatio = `${w}/${h}`;
  $('#vimg').src = 'file://' + encodeURI(e.thumb);
  const faces = e.faces || [];
  faces.forEach((f, k) => { if (k < 4 && f.box) { const t = vis.trail[k]; t.push([(f.box[0] + f.box[2]) / 2, (f.box[1] + f.box[3]) / 2]); if (t.length > 14) t.shift(); } });
  const over = faces.map((f, k) => {
    const b = f.box || [0, 0, 0, 0], mo = f.mouth, ey = f.eyes;
    const trail = (vis.trail[k] || []).map((p, j, arr) => `<i class="trail" style="left:${p[0] * 100}%;top:${p[1] * 100}%;opacity:${(j + 1) / arr.length * 0.8};transform:translate(-50%,-50%) scale(${0.5 + (j + 1) / arr.length * 0.6})"></i>`).join('');
    return `${trail}<div class="vbox ${f.speaking ? 'talk' : ''}" style="left:${b[0] * 100}%;top:${b[1] * 100}%;width:${(b[2] - b[0]) * 100}%;height:${(b[3] - b[1]) * 100}%"><b>FACE ${k + 1} · ${Math.round((f.conf || 0) * 100)}%</b></div>
      ${ey ? `<i class="vcross" style="left:${ey[0] * 100}%;top:${ey[1] * 100}%"></i>` : ''}${mo ? `<i class="vmouth ${f.speaking ? 'talk' : ''}" style="left:${mo[0] * 100}%;top:${mo[1] * 100}%;width:${(mo[2] - mo[0]) * 100}%;height:${(mo[3] - mo[1]) * 100}%"></i>` : ''}`;
  }).join('');
  $('#vover').innerHTML = over;
  $('#vhudTl').textContent = `${e.clip}  ·  ${Number(e.t_clip).toFixed(2)} s`;
  $('#vhudTr').innerHTML = `<i class="${live ? 'live' : ''}"></i>${live ? 'LIVE' : 'REPLAY'}`;
  $('#vhudBl').textContent = `frame ${e.i}${e.n ? ' / ' + e.n : ''}  ·  ${Math.round(e.ms || 0)} ms  ·  ${faces.length} face${faces.length === 1 ? '' : 's'}`;
  $('#vfaces').innerHTML = faces.length ? faces.map((f, k) => `<div class="vf"><div class="vf-h"><b>Face ${k + 1}</b><span>${posWord(f)}</span></div><div class="vf-m"><span>mouth</span><div class="vf-bar"><i class="${f.speaking ? 'talk' : ''}" style="width:${Math.min(100, (f.mar || 0) / 0.5 * 100)}%"></i></div><span class="${f.speaking ? 'talk' : ''}">${f.speaking ? 'open' : 'closed'}</span></div></div>`).join('') : '<span class="muted">No face in this frame</span>';
  if (!vis.ms.length || vis.ms[vis.ms.length - 1].i !== e.i) { vis.ms.push({ i: e.i, ms: e.ms || 0 }); if (vis.ms.length > 60) vis.ms.shift(); }
  const mx = Math.max(40, ...vis.ms.map((m) => m.ms)); const pts = vis.ms.map((m, k) => `${(k / Math.max(1, vis.ms.length - 1)) * 200},${34 - (m.ms / mx) * 32}`).join(' ');
  $('#vspark').innerHTML = `<polyline points="${pts}" fill="none" stroke="#3ddc97" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`;
  $('#vms').textContent = `${Math.round(e.ms || 0)} ms`;
  $('#vbar').innerHTML = vis.frames.map((x, k) => `<i class="${k === vis.idx ? 'cur' : ''} ${x.faces && x.faces.length ? '' : 'none'}"></i>`).join('');
  document.querySelectorAll('.vclip').forEach((bt) => bt.classList.toggle('on', bt.dataset.scan === e.clip));
}

// no scrolling: show the clips that fit and say how many more there are
function fitClips() {
  const box = $('.clips'); if (!box) return;
  const card = box.closest('.card'); const items = [...box.querySelectorAll('.clip')];
  items.forEach((el) => { el.style.display = ''; });
  const bottom = card.getBoundingClientRect().bottom - 30;
  let hidden = 0;
  items.forEach((el) => { if (el.getBoundingClientRect().bottom > bottom) { el.style.display = 'none'; hidden++; } });
  if (hidden) { const m = document.createElement('div'); m.className = 'more'; m.textContent = `+${hidden} more`; box.appendChild(m); }
}

// ---------------------------------------------------------------- data
async function loadJobs() {
  const r = await window.zeno.jobs();
  if (Array.isArray(r)) {
    state.jobs = r;
    if (!state.selected || !r.find((j) => j.name === state.selected)) { const w = r.find((j) => j.status.phase === 'working') || r[0]; state.selected = w ? w.name : null; state.detail = null; state.events = []; state.scans = []; state.scanIdx = 0; state.last = 0; }
  }
  renderSide();
}
async function loadDetail() {
  if (!state.selected || state.busy) return;
  state.busy = true;
  try {
    const name = state.selected;
    const d = await window.zeno.detail(name, state.last);
    if (name !== state.selected || !d || d.error) return;
    state.events = state.events.concat(d.events || []).slice(-600);
    if (d.scans && d.scans.length) { state.scans = state.scans.concat(d.scans).slice(-150); state.lastScanAt = Date.now(); }
    state.last = d.last_event || state.last;
    state.detail = d;
    renderMain();
  } finally { state.busy = false; }
}
async function loadInfo(check) { const i = await window.zeno.info(check); if (i && !i.error) { state.info = i; renderSide(); } }

function closeBox() { const b = $('#lightbox'); if (b) b.remove(); }
function openBox(path, title) {
  closeBox();
  const b = document.createElement('div'); b.id = 'lightbox';
  b.innerHTML = `<div class="lb-top"><span>${esc(title)}</span><span><button class="btn" data-open="${esc(path)}">Open outside</button><button class="btn" id="lbClose">Close</button></span></div><img src="file://${encodeURI(path)}?v=${Date.now()}" alt="${esc(title)}">`;
  document.body.appendChild(b);
}
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeBox(); closeVision(); } });
document.addEventListener('click', async (e) => {
  if (e.target.closest('[data-vision]')) { openVision(); return; }
  if (e.target.closest('#vClose')) { closeVision(); return; }
  const sc = e.target.closest('[data-scan]');
  if (sc && state.detail && state.detail.plan) { vis.frames = []; vis.idx = 0; vis.trail = [[], [], [], []]; vis.ms = []; vis.clip = null; window.zeno.startScan(state.selected, state.detail.plan.footage + '/' + sc.dataset.scan, 0.12); return; }
  const tb = e.target.closest('[data-tab]'); if (tb) { state.tab = tb.dataset.tab; lastMain = ''; renderMain(); return; }
  const zm = e.target.closest('[data-zoom]'); if (zm) { openBox(zm.dataset.zoom, zm.dataset.title); return; }
  if (e.target.closest('#lbClose') || e.target.id === 'lightbox') { closeBox(); return; }
  const li = e.target.closest('#jobs li[data-name]');
  if (li) { state.selected = li.dataset.name; state.detail = null; state.events = []; state.scans = []; state.scanIdx = 0; state.last = 0; lastMain = ''; renderSide(); renderMain(); loadDetail(); return; }
  const rv = e.target.closest('[data-reveal]'); if (rv) { window.zeno.reveal(rv.dataset.reveal); return; }
  const op = e.target.closest('[data-open]'); if (op) { window.zeno.open(op.dataset.open); return; }
  if (e.target.closest('#newEdit')) { window.zeno.newEdit(); return; }
  const up = e.target.closest('#doUpdate');
  if (up) { up.textContent = 'Updating…'; const r = await window.zeno.update(); up.textContent = r.ok ? 'Updated · restart Zeno' : 'Update failed'; setTimeout(() => loadInfo(true), 1500); }
});

(async function start() {
  const root = await window.zeno.root();
  if (!root) { $('#main').innerHTML = '<div class="top"><h1>Zeno</h1></div><div class="scroll" style="display:flex"><div class="welcome"><h1>Zeno folder not found</h1><div>Install Zeno first (install.command on Mac, install.bat on Windows), or set ZENO_HOME to its folder.</div></div></div>'; return; }
  await loadInfo(false);
  await loadJobs();
  renderMain();
  loadDetail();
  loadInfo(true);
  setInterval(loadJobs, 2500);
  setInterval(loadDetail, 1000);
  setInterval(() => loadInfo(true), 600000);
  window.addEventListener('resize', () => { fitList(); lastMain = ''; renderMain(); });
})();
