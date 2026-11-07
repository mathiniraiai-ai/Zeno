// Zeno desktop app: shows every edit and what the backend is doing, step by step. It only reads job state through the bridge in preload.js.
'use strict';
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n) => (n == null ? '–' : Number(n).toLocaleString());
const mmss = (s) => { s = Math.round(s || 0); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const hms = (t) => new Date(t * 1000).toTimeString().slice(0, 8);
const ago = (t) => { const s = Math.max(0, Date.now() / 1000 - t); if (s < 60) return 'just now'; if (s < 3600) return `${Math.floor(s / 60)} min ago`; if (s < 86400) return `${Math.floor(s / 3600)} h ago`; return `${Math.floor(s / 86400)} d ago`; };

const state = { tab: 'scan', scans: [], scanIdx: 0, jobs: [], selected: new URLSearchParams(location.search).get('select') || null, detail: null, events: [], last: 0, filter: 'all', info: null, busy: false };
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

// the timeline as layers, like an editor's: subtitles, effects (zooms, transitions), video cuts, voice per speaker, sound effects
const SPK_COLORS = ['#bacf65', '#e4b24c', '#7aa2d6', '#c98bd0', '#6fc7b8', '#e08a6a', '#9aa5b1'];
function timelineHtml(d) {
  const p = d.plan;
  if (!p || !p.timeline.length) return '<div class="empty">The timeline appears when the editor has written a plan.</div>';
  const total = p.seconds || 1, pct = (t) => (t / total) * 100 + '%';
  const spk = [...new Set(p.timeline.map((s) => s.speaker).filter(Boolean))];
  const col = (n) => SPK_COLORS[spk.indexOf(n) % SPK_COLORS.length];
  const video = p.timeline.map((s) => {
    const split = /^split|top_pip|left_right|top_bottom/.test(s.layout), other = !split && s.layout !== 'single';
    const tip = `${s.id} · ${s.src} · ${s.layout}${s.zoom ? ' · zoom ' + s.zoom : ''}${s.transition !== 'cut' ? ' · ' + s.transition : ''} · ${s.dur}s`;
    return `<div class="seg ${split ? 'split' : other ? 'other' : ''}" title="${esc(tip)}" style="left:${pct(s.start)};width:${pct(s.dur)}">${s.dur / total > 0.03 ? `<b>${esc(s.id)}</b>` : ''}</div>`;
  }).join('');
  const voice = p.timeline.map((s) => `<div class="seg vo" title="${esc(`${s.id} · ${s.speaker || 'speaker not set'} · ${s.dur}s`)}" style="left:${pct(s.start)};width:${pct(s.dur)};background:${s.speaker ? col(s.speaker) : '#475569'}">${s.dur / total > 0.045 && s.speaker ? `<b>${esc(s.speaker.split(' ').map((w) => w[0]).join(''))}</b>` : ''}</div>`).join('');
  const zooms = p.timeline.filter((s) => s.zoom).map((s) => `<div class="seg zm" title="${esc(`${s.id} · zoom ${s.zoom}`)}" style="left:${pct(s.start)};width:${pct(s.dur)}">${s.dur / total > 0.05 ? `<b>${esc(s.zoom)}</b>` : ''}</div>`).join('');
  const trans = p.timeline.filter((s) => s.transition !== 'cut').map((s) => `<div class="mk tr" title="${esc(`${s.id} · ${s.transition}`)}" style="left:${pct(s.start)}"><i></i><span>${esc(s.transition.replace(/_/g, ' '))}</span></div>`).join('');
  const sfx = (p.sfx || []).map((x, k, arr) => {
    const next = arr[k + 1]; const room = !next || (next.t - x.t) / total > 0.07;
    return `<div class="mk ${x.kind}" title="${esc(`${x.file}${x.gain != null ? ' · ' + x.gain + ' dB' : ''} · ${mmss(x.t)}${x.kind === 'auto' ? ' · on the transition' : ''}`)}" style="left:${pct(x.t)}"><i></i>${room ? `<span>${esc(x.file.replace(/\.[a-z0-9]+$/i, ''))}</span>` : ''}</div>`;
  }).join('');
  const lane = (name, cls, inner, empty) => `<div class="lane"><span class="ln">${name}</span><div class="lt ${cls}">${inner || `<em>${empty || ''}</em>`}</div></div>`;
  const subs = d.subs && d.subs.style ? d.subs.style : (p.subs || '');
  return `<div class="tl-wrap">
    ${subs ? lane('Subs', 'sub', `<div class="seg sb" style="left:0;width:100%"><b>${esc(subs)}</b></div>`) : ''}
    ${lane('Effects', 'fx', zooms + trans, 'no zooms or transitions')}
    ${lane('Video', 'tl', video)}
    ${lane('Voice', 'au', voice)}
    ${lane('SFX', 'au', sfx, 'no sound effects')}
    <div class="tl-scale"><span>0:00</span><span>${mmss(total / 2)}</span><span>${mmss(total)}</span></div>
    ${spk.length ? `<div class="spk">${spk.map((n) => `<span><i style="background:${col(n)}"></i>${esc(n)}</span>`).join('')}</div>` : ''}
  </div>`;
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
  return `<div class="top"><h1 title="${esc(d.title)}">${esc(shortTitle(d.title))}</h1><span class="pill ${s.phase}">${esc(s.phase === 'working' ? `Working${d.elapsed ? ' · ' + d.elapsed : ''}` : shortStatus(s))}</span></div>
  <div class="pipe">${pipeHtml(d)}</div>
  <div class="body">
    <div class="col">
      <div class="card tlcard"><h2>Timeline<span>${d.plan ? `${d.plan.cuts} cuts · ${mmss(d.plan.seconds)}` : ''}</span></h2>${timelineHtml(d)}${statsLine(d)}</div>
      <div class="hsplit" id="lsplit" title="Drag to enlarge the timeline layers (double-click to reset)"></div>
      <div class="card fill"><h2>Live<span>${s.phase === 'working' ? 'working' : ''}</span></h2><div id="feed">${feedHtml()}</div></div>
    </div>
    <div class="vsplit" id="vsplit" title="Drag to resize"></div>
    <div class="col right">
      <div class="card fill"><h2>Scan<span>frame by frame</span></h2><div id="scanview" class="scan"></div></div>
      <div class="hsplit" id="hsplit" title="Drag to resize"></div>
      <div class="card pcard"><h2>Pictures<span>click to enlarge</span></h2>${picsHtml(d)}</div>
    </div>
  </div>`;
}

let lastMain = '';
function renderMain() {
  const html = mainHtml(state.detail);
  if (html === lastMain) return;
  lastMain = html;
  const keep = $('#scanview'); if (keep) keep.remove();           // the scan player is moved, not rebuilt, so it never restarts or flickers
  $('#main').innerHTML = html;
  const slot = $('#scanview'); if (keep && slot) { slot.replaceWith(keep); }
  paintScan();
}
// when a clip was scanned more than once, keep only its newest scan (the one with the same frame count as its last frame)
function latestRuns(list) {
  const last = new Map(); list.forEach((e) => last.set(e.clip, e.n));
  const seen = new Set();
  return list.filter((e) => e.n === last.get(e.clip)).reverse().filter((e) => { const k = e.clip + '|' + e.i; if (seen.has(k)) return false; seen.add(k); return true; }).reverse();
}
// the scan view: one persistent player. It is built once and only its picture, boxes and numbers change, so nothing flickers.
// It plays every scanned clip one after the other (click a clip name to loop just that one).
const sp = { built: null, idx: 0, pin: null, prevClip: '', cache: new Map(), faceEls: [], sig: '' };
function preload(url) { if (!sp.cache.has(url)) { const im = new Image(); im.src = url; sp.cache.set(url, im); if (sp.cache.size > 1200) sp.cache.delete(sp.cache.keys().next().value); } }
function scanSkeleton(box) {
  box.innerHTML = `<div class="sarea"><div class="sframe" data-ar="0.5625"><img alt=""><div class="sfaces"></div></div></div>
    <div class="sinfo"><div class="sclip"></div><div class="muted sfr"></div><div class="shead"></div><div class="srows"></div><div class="muted sfoot"></div></div>
    <div class="schips"></div><div class="sdots"></div>`;
  sp.built = box; sp.faceEls = []; sp.sig = '';
  if (sp.ro) sp.ro.disconnect();
  sp.ro = new ResizeObserver(() => { box.classList.toggle('compact', box.clientWidth < 380); fitFrame(box); }); sp.ro.observe(box.querySelector('.sarea'));   // refit whenever the space around the picture changes
}
function scanClips() { const m = new Map(); state.scans.forEach((e, k) => { if (!m.has(e.clip)) m.set(e.clip, []); m.get(e.clip).push(k); }); return m; }
function paintScan() {
  const box = $('#scanview'); if (!box) return;
  const list = state.scans;
  if (!list.length) { sp.built = null; box.innerHTML = '<div class="empty">No frame scans yet. Run: python3 scripts/vision_scan.py "&lt;clip or folder&gt;"</div>'; return; }
  if (sp.built !== box) scanSkeleton(box);
  const clips = scanClips();
  // which frames play: all of them in order, or only the pinned clip
  let seq = sp.pin && clips.has(sp.pin) ? clips.get(sp.pin) : list.map((_, k) => k);
  if (sp.pin && !clips.has(sp.pin)) sp.pin = null;
  if (sp.idx >= seq.length) sp.idx = 0;
  const live = Date.now() - (state.lastScanAt || 0) < 4000;
  if (live && !sp.pin && list.length - sp.idx > 40) sp.idx = list.length - 30;      // a scan far ahead: catch up instead of replaying it slowly
  const e = list[seq[sp.idx]]; if (!e) return;
  const w = (e.size && e.size[0]) || 9, h = (e.size && e.size[1]) || 16;
  const fr = box.querySelector('.sframe'), img = fr.querySelector('img'), layer = fr.querySelector('.sfaces');
  const ar = w / h;
  if (String(ar) !== fr.dataset.ar) { fr.dataset.ar = ar; fitFrame(box); }
  const url = 'file://' + encodeURI(e.thumb);
  if (img.getAttribute('src') !== url) img.setAttribute('src', url);
  for (let k = 1; k <= 8; k++) { const n = list[seq[(sp.idx + k) % seq.length]]; if (n) preload('file://' + encodeURI(n.thumb)); }
  const jump = e.clip !== sp.prevClip; sp.prevClip = e.clip;
  layer.classList.toggle('jump', jump);
  const fs = e.faces || [];
  while (sp.faceEls.length < fs.length) {
    const g = document.createElement('div'); g.className = 'fg';
    g.innerHTML = '<div class="fbox"><b></b></div><i class="eye"></i><i class="mouth"></i>'; layer.appendChild(g); sp.faceEls.push(g);
  }
  sp.faceEls.forEach((g, k) => {
    const f = fs[k]; g.style.display = f ? '' : 'none'; if (!f) return;
    const b = f.box || [0, 0, 0, 0], bx = g.children[0], ey = g.children[1], mo = g.children[2];
    bx.style.cssText = `left:${b[0] * 100}%;top:${b[1] * 100}%;width:${(b[2] - b[0]) * 100}%;height:${(b[3] - b[1]) * 100}%`; bx.firstChild.textContent = k + 1;
    ey.style.display = f.eyes ? '' : 'none'; if (f.eyes) { ey.style.left = f.eyes[0] * 100 + '%'; ey.style.top = f.eyes[1] * 100 + '%'; }
    const m = f.mouth; mo.style.display = m ? '' : 'none';
    if (m) { mo.style.cssText = `left:${m[0] * 100}%;top:${m[1] * 100}%;width:${(m[2] - m[0]) * 100}%;height:${(m[3] - m[1]) * 100}%`; mo.className = 'mouth' + (f.speaking ? ' talk' : ''); }
  });
  const q = (s) => box.querySelector(s), setT = (el, v) => { if (el.textContent !== v) el.textContent = v; };
  setT(q('.sclip'), e.clip); q('.sclip').title = e.clip;
  setT(q('.sfr'), `frame ${e.i}${e.n ? '/' + e.n : ''} · ${e.t_clip}s`);
  q('.shead').innerHTML = `<b>${fs.length}</b> face${fs.length === 1 ? '' : 's'} · ${e.ms ? Math.round(e.ms) + ' ms' : 'scan'}`;
  q('.srows').innerHTML = fs.map((f, k) => `<div class="srow"><span>Face ${k + 1}</span><span class="${f.speaking ? 'talk' : ''}">${f.speaking ? 'mouth open' : 'mouth closed'}</span></div>`).join('') || '<div class="srow"><span>No face</span><span></span></div>';
  setT(q('.sfoot'), sp.pin ? 'looping this clip' : live ? 'scanning live' : `clip ${[...clips.keys()].indexOf(e.clip) + 1} of ${clips.size}`);
  // clip chips (rebuilt only when the set of clips changes) and the progress through the current clip
  const sig = [...clips.keys()].join('|') + '#' + (sp.pin || '');
  if (sig !== sp.sig) { sp.sig = sig; q('.schips').innerHTML = [...clips.keys()].map((n) => `<button data-clip="${esc(n)}" class="${n === sp.pin ? 'pin' : ''}">${esc(n.replace(/\.[^.]+$/, ''))}</button>`).join(''); }
  box.querySelectorAll('.schips button').forEach((b) => { const on = b.dataset.clip === e.clip; b.classList.toggle('cur', on); if (on && !b.classList.contains('was')) { box.querySelectorAll('.schips .was').forEach((x) => x.classList.remove('was')); b.classList.add('was'); const sc = b.parentElement; sc.scrollLeft = b.offsetLeft - sc.offsetLeft - 8; } });
  const mine = clips.get(e.clip), dots = q('.sdots');
  if (dots.children.length !== mine.length) dots.innerHTML = mine.map(() => '<i></i>').join('');
  const at = mine.indexOf(seq[sp.idx]);
  Array.from(dots.children).forEach((d, k) => { d.className = (list[mine[k]].faces || []).length ? (k === at ? 'cur' : k < at ? 'seen' : '') : (k === at ? 'cur none' : 'none'); });
}
function frameFit(box) { return fitFrame(box); }
// the frame keeps the video's own shape and takes the largest size the card allows, so the face boxes always sit on the faces
function fitFrame(box) {
  const area = box.querySelector('.sarea'); const fr = box.querySelector('.sframe'); if (!area || !fr) return;
  const r = area.getBoundingClientRect(); const ar = parseFloat(fr.dataset.ar) || 0.5625;
  let fw = r.width, fh = fw / ar; if (fh > r.height) { fh = r.height; fw = fh * ar; }
  fr.style.width = Math.max(40, fw) + 'px'; fr.style.height = Math.max(40, fh) + 'px';
}
setInterval(() => { if (state.tab === 'scan' && state.scans.length) { sp.idx++; paintScan(); } }, 170);



// draggable bars: the middle bar sets the width of the right-hand column, the lower one the height of the Pictures card (both remembered)
const store = { get: (k, d) => { try { return Number(localStorage.getItem(k)) || d; } catch (e) { return d; } }, set: (k, v) => { try { localStorage.setItem(k, String(Math.round(v))); } catch (e) { /* not saved */ } } };
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const setVar = (k, v) => document.documentElement.style.setProperty(k, `${Math.round(v)}px`);
function restoreLayout() {
  const main = $('#main'); const w = main ? main.getBoundingClientRect().width : 1000;
  setVar('--rw', clamp(store.get('zenoRightW', 400), 250, Math.max(250, w - 40 - 380)));
  setVar('--ph', clamp(store.get('zenoPicsH', 128), 100, 360));
  setVar('--sw', clamp(store.get('zenoSideW', 232), 170, 380));
  const th = store.get('zenoTlH', 0), col = $('.col');
  if (th > 0 && col) { setVar('--tlh', clamp(th, 190, Math.max(190, col.getBoundingClientRect().height - 190))); document.documentElement.classList.add('tall'); }
  else { document.documentElement.style.removeProperty('--tlh'); document.documentElement.classList.remove('tall'); }
}
document.addEventListener('dblclick', (e) => { if (e.target.id === 'lsplit') { store.set('zenoTlH', 0); restoreLayout(); } });
let drag = null;
document.addEventListener('mousedown', (e) => {
  if (e.target.id === 'ssplit') drag = 's'; else if (e.target.id === 'vsplit') drag = 'v'; else if (e.target.id === 'hsplit') drag = 'h'; else if (e.target.id === 'lsplit') drag = 'l'; else return;
  e.target.classList.add('active'); document.body.classList.add('dragging'); e.preventDefault();
});
document.addEventListener('mousemove', (e) => {
  if (!drag) return;
  if (drag === 's') { setVar('--sw', clamp(e.clientX, 170, 380)); fitList(); }
  else if (drag === 'v') { const r = $('#main').getBoundingClientRect(); setVar('--rw', clamp(r.right - 20 - e.clientX - 7, 250, r.width - 40 - 380)); }
  else if (drag === 'l') { const col = $('.col'); if (!col) return; const r = col.getBoundingClientRect(); setVar('--tlh', clamp(e.clientY - r.top - 7, 190, Math.max(190, r.height - 190))); document.documentElement.classList.add('tall'); }
  else { const col = $('.col.right'); if (!col) return; const r = col.getBoundingClientRect(); setVar('--ph', clamp(r.bottom - e.clientY - 7, 100, Math.max(100, r.height - 200))); }
  const sv = $('#scanview'); if (sv) fitFrame(sv);
});
document.addEventListener('mouseup', () => {
  if (!drag) return;
  const cs = getComputedStyle(document.documentElement);
  if (drag === 's') store.set('zenoSideW', parseFloat(cs.getPropertyValue('--sw'))); else if (drag === 'v') store.set('zenoRightW', parseFloat(cs.getPropertyValue('--rw'))); else if (drag === 'l') store.set('zenoTlH', parseFloat(cs.getPropertyValue('--tlh'))); else store.set('zenoPicsH', parseFloat(cs.getPropertyValue('--ph')));
  document.querySelectorAll('.active').forEach((x) => x.classList.remove('active'));
  drag = null; document.body.classList.remove('dragging'); restoreLayout();
});

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
    if (d.scans && d.scans.length) { state.scans = latestRuns(state.scans.concat(d.scans)).slice(-900); state.lastScanAt = Date.now(); }
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
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeBox(); });
document.addEventListener('click', async (e) => {
  const zm = e.target.closest('[data-zoom]'); if (zm) { openBox(zm.dataset.zoom, zm.dataset.title); return; }
  if (e.target.closest('#lbClose') || e.target.id === 'lightbox') { closeBox(); return; }
  const chip = e.target.closest('.schips button'); if (chip) { sp.pin = sp.pin === chip.dataset.clip ? null : chip.dataset.clip; sp.idx = 0; sp.sig = ''; paintScan(); return; }
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
  restoreLayout();
  await loadInfo(false);
  await loadJobs();
  renderMain();
  loadDetail();
  loadInfo(true);
  setInterval(loadJobs, 2500);
  setInterval(loadDetail, 1000);
  setInterval(() => loadInfo(true), 600000);
  window.addEventListener('resize', () => { restoreLayout(); fitList(); lastMain = ''; renderMain(); });
})();
