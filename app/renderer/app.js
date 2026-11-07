// Zeno desktop app: shows every edit and what the backend is doing, step by step. It only reads job state through the bridge in preload.js.
'use strict';
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n) => (n == null ? '–' : Number(n).toLocaleString());
const mmss = (s) => { s = Math.round(s || 0); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const hms = (t) => new Date(t * 1000).toTimeString().slice(0, 8);
const ago = (t) => { const s = Math.max(0, Date.now() / 1000 - t); if (s < 60) return 'just now'; if (s < 3600) return `${Math.floor(s / 60)} min ago`; if (s < 86400) return `${Math.floor(s / 3600)} h ago`; return `${Math.floor(s / 86400)} d ago`; };

const state = { jobs: [], selected: new URLSearchParams(location.search).get('select') || null, detail: null, events: [], last: 0, filter: 'all', info: null, busy: false };
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

function statsHtml(d) {
  const p = d.plan; const reel = d.outputs.find((o) => o.kind === 'reel' && o.mb > 1);
  const v = d.verify || []; const bad = v.filter((x) => !x.ok).length;
  const cells = [];
  cells.push(`<div class="stat"><b>${p ? p.cuts : '–'}</b><span>Cuts</span></div>`);
  cells.push(`<div class="stat"><b>${p ? mmss(p.seconds) : '–'}</b><span>Length</span></div>`);
  cells.push(`<div class="stat"><b>${reel ? reel.mb + ' MB' : '–'}</b><span>Reel</span></div>`);
  const chk = v.length ? `${v.length - bad}/${v.length}` : (d.plan_check ? (d.plan_check.result === 'passed' ? 'Plan ok' : 'Blocked') : '–');
  cells.push(`<div class="stat ${bad ? 'warn' : (v.length || (d.plan_check && d.plan_check.result === 'passed') ? 'good' : '')}"><b>${chk}</b><span>Checks</span></div>`);
  if (d.tokens && d.tokens.total && !d.tokens.mixed) cells.push(`<div class="stat"><b>${(d.tokens.total / 1000).toFixed(0)}k</b><span>Tokens</span></div>`);
  if (p && p.subs) cells.push(`<div class="stat"><b>${esc(d.subs ? d.subs.style : p.subs)}</b><span>Subtitles</span></div>`);
  const out = d.outputs.find((o) => ['reel', 'video'].includes(o.kind) && o.mb > 1);
  const act = out ? `<div class="actions"><button class="btn" data-reveal="${esc(out.path)}">Show in folder</button><button class="btn" data-open="${esc(out.path)}">Play</button></div>` : '';
  return `<div class="stats">${cells.join('')}</div>${act}`;
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
      <div class="card"><h2>Timeline<span>${d.plan ? `${d.plan.cuts} cuts · ${mmss(d.plan.seconds)}` : ''}</span></h2>${timelineHtml(d)}</div>
      <div class="card fill"><h2>Live<span>${s.phase === 'working' ? 'working' : ''}</span></h2><div id="feed">${feedHtml()}</div></div>
    </div>
    <div class="col">
      <div class="card fill"><h2>Faces<span>${d.clips.length ? d.clips.length + ' clips' : ''}</span></h2>${footageHtml(d)}</div>
      <div class="card">${statsHtml(d)}</div>
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
    if (!state.selected || !r.find((j) => j.name === state.selected)) { const w = r.find((j) => j.status.phase === 'working') || r[0]; state.selected = w ? w.name : null; state.detail = null; state.events = []; state.last = 0; }
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
    state.last = d.last_event || state.last;
    state.detail = d;
    renderMain();
  } finally { state.busy = false; }
}
async function loadInfo(check) { const i = await window.zeno.info(check); if (i && !i.error) { state.info = i; renderSide(); } }

document.addEventListener('click', async (e) => {
  const li = e.target.closest('#jobs li[data-name]');
  if (li) { state.selected = li.dataset.name; state.detail = null; state.events = []; state.last = 0; lastMain = ''; renderSide(); renderMain(); loadDetail(); return; }
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
