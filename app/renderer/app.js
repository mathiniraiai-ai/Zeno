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
const FILTERS = [['all', 'All'], ['face', 'Faces'], ['speech', 'Voice'], ['render', 'Cuts and render'], ['check', 'Checks']];
const inFilter = (e) => state.filter === 'all' || (state.filter === 'render' ? ['render', 'cut', 'subs'].includes(e.kind) : state.filter === 'check' ? ['check', 'verify'].includes(e.kind) : e.kind === state.filter);

// ---------------------------------------------------------------- sidebar
function renderSide() {
  $('#count').textContent = state.jobs.length || '';
  $('#jobs').innerHTML = state.jobs.map((j) => {
    const meta = j.status.phase === 'working' ? `${esc(j.stage_name || 'Working')} · ${j.pct || 0}%` : esc(j.status.label);
    return `<li data-name="${esc(j.name)}" class="${j.name === state.selected ? 'sel' : ''}"><span class="dot ${j.status.phase}"></span><div><div class="j-title">${esc(j.title)}</div><div class="j-meta">${meta} · ${ago(j.updated)}</div></div></li>`;
  }).join('') || '<li class="muted" style="cursor:default">No edits yet</li>';
  const i = state.info;
  if (i) {
    $('#ver').textContent = i.version ? `v${i.version}` : '';
    $('#who').textContent = i.signed_in ? `Signed in · ${i.signed_in}` : 'Not signed in';
    $('#upd').innerHTML = i.behind > 0 ? `<span class="chip" id="doUpdate">Update available (${i.behind}) · install</span>` : (i.behind === 0 ? '<span class="chip ok">Up to date</span>' : '<span class="chip ok">Update check offline</span>');
  }
}

// ---------------------------------------------------------------- main view
function pipeHtml(d) {
  const stages = d.stages || ['Mapping', 'Transcribing', 'Matching', 'Editing', 'Mastering'];
  const ph = d.status.phase;
  let cur = d.stage || 0; // 1-based stage the engine reports
  let done = 0, waitAt = -1;
  if (ph === 'done') done = 5; else if (ph === 'plan' || ph === 'measured') { done = 3; waitAt = 3; } else if (ph === 'working' || ph === 'stalled' || ph === 'failed') done = Math.max(0, cur - 1);
  const sub = ['Scan the clips', 'Words and faces', 'Match to the script', ph === 'plan' ? 'Waiting for your Yes' : ph === 'measured' ? "Editor's plan" : 'Cut and compose', 'Loudness and checks'];
  const out = [];
  stages.forEach((name, i) => {
    const cls = i < done ? 'done' : (i === waitAt ? 'wait' : (i === cur - 1 && (ph === 'working' || ph === 'stalled') ? 'active' : ''));
    out.push(`<div class="step ${cls}"><div class="c">${cls === 'done' ? '✓' : i + 1}</div><div><div class="t">${esc(name)}</div><div class="s">${esc(sub[i])}</div></div></div>`);
    if (i < stages.length - 1) out.push(`<div class="bar"><i style="width:${i < done ? 100 : (i === cur - 1 && ph === 'working' ? Math.min(100, ((d.pct || 0) % 20) * 5) : 0)}%"></i></div>`);
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
    return `<div class="seg ${split ? 'split' : other ? 'other' : ''}" title="${esc(tip)}" style="left:${(s.start / total) * 100}%;width:${(s.dur / total) * 100}%">${s.dur / total > 0.035 ? `<b>${esc(s.id)}</b>` : ''}</div>` +
      (s.transition !== 'cut' ? `<div class="tick" style="left:${(s.start / total) * 100}%"></div>` : '');
  }).join('');
  return `<div class="body"><div class="tl">${segs}</div>${p.subs ? `<div class="tl-sub" title="Subtitles: ${esc(p.subs)}"><i></i></div>` : ''}
    <div class="tl-scale"><span>0:00</span><span>${mmss(total / 2)}</span><span>${mmss(total)}</span></div>
    <div class="legend"><span>Full frame</span><span class="l2">Split screen</span><span class="l3">Transition</span></div></div>`;
}

function feedText(e) {
  let tx = String(e.text || '').replace(/(\/Users|\/home|[A-Za-z]:\\)[^\s]*/g, '').replace(/\s+/g, ' ').trim();
  if (e.kind === 'stage') tx = tx.replace(/^[^:]*:\s*/, '');
  return tx.slice(0, 170);
}
function feedHtml() {
  const rows = state.events.filter(inFilter).map((e) => ({ e, tx: feedText(e) })).filter((r) => r.tx);
  if (!rows.length) return '<div class="empty">Nothing yet. Steps appear here as the engine works: which clip it scans for faces, who speaks, which cut it renders, and every check.</div>';
  return rows.slice(-300).map(({ e, tx }) => {
    const [ic] = KIND[e.kind] || KIND.info;
    const bad = e.ok === false ? ' bad' : (e.ok === true ? ' ok' : '');
    return `<div class="ev ${e.kind}${bad}"><span class="tm">${hms(e.t)}</span><span class="ic">${ic}</span><span class="tx" title="${esc(tx)}">${esc(tx)}</span></div>`;
  }).join('');
}

function footageHtml(d) {
  if (!d.clips.length) return '<div class="empty">Face scan results appear here as each clip is measured.</div>';
  return d.clips.map((c) => {
    const blocks = c.spans.map((s) => `<i style="left:${Math.max(0, s[0]) * 100}%;width:${Math.max(1, (s[1] - s[0]) * 100)}%"></i>`).join('');
    const meta = c.silent ? 'silent clip' : `${c.faces} face${c.faces === 1 ? '' : 's'}${c.shot && c.shot !== 'single' ? ' · ' + String(c.shot).replace('_', ' ') : ''}`;
    return `<div class="clip"><div class="n" title="${esc(c.clip)}">${esc(c.clip)}</div><div class="m">${esc(meta)}</div><div class="fbar ${c.silent ? 'silent' : ''}" title="Where the faces sit across the frame width">${blocks}</div></div>`;
  }).join('');
}

function planHtml(d) {
  const p = d.plan;
  if (!p) return '<div class="empty">No plan yet.</div>';
  const colours = { single: '#2a6b50', split_h: '#3ddc97', split_v: '#3ddc97' };
  const lay = Object.entries(p.layouts).map(([k, v]) => `<i title="${esc(k)}: ${v}" style="width:${(v / p.cuts) * 100}%;background:${colours[k] || '#e4b24c'}"></i>`).join('');
  const layTxt = Object.entries(p.layouts).map(([k, v]) => `${v} ${k.replace('_', ' ')}`).join(' · ');
  return `<div class="body"><div class="kv"><div>Cuts</div><div>${p.cuts}</div><div>Length</div><div>${mmss(p.seconds)}</div><div>Transitions</div><div>${p.transitions}</div><div>Subtitles</div><div>${esc(p.subs ? (d.subs ? d.subs.style : p.subs) : 'off')}</div>${d.format ? `<div>Format</div><div>${esc(d.format)}</div>` : ''}</div>
    <div class="lay">${lay}</div><div class="muted" style="font-size:11.5px">${esc(layTxt)}</div></div>`;
}

function checksHtml(d) {
  const rows = [];
  const pc = d.plan_check;
  if (pc) rows.push(`<div class="row"><span class="${pc.result === 'passed' ? 'ok' : 'no'}">${pc.result === 'passed' ? '✓' : '✗'}</span><span class="t">Plan check ${esc(pc.result)}${pc.problem_count ? ` · ${pc.problem_count} problem(s)` : ''}</span></div>`);
  d.verify.forEach((v) => rows.push(`<div class="row"><span class="${v.ok ? 'ok' : 'no'}">${v.ok ? '✓' : '✗'}</span><span class="t" title="${esc(v.text)}">${esc(v.text)}</span></div>`));
  if (d.failures && d.failures.length) rows.push(`<div class="warn">${d.failures.length} stop(s) so far: ${esc(d.failures[d.failures.length - 1].detail)}</div>`);
  return rows.length ? `<div class="body">${rows.join('')}</div>` : '<div class="empty">Checks appear after the plan is written and after the render.</div>';
}

function outputHtml(d) {
  const vids = d.outputs.filter((o) => ['reel', 'square', 'video'].includes(o.kind));
  if (!vids.length) return '<div class="empty">No video yet.</div>';
  const files = d.outputs.filter((o) => ['prproj', 'srt'].includes(o.kind)).map((o) => o.kind === 'prproj' ? 'Premiere project' : 'Subtitles file');
  return `<div class="body">${vids.map((o) => `<div class="out"><span title="${esc(o.name)}">${o.kind === 'reel' ? 'Reel 9:16' : o.kind === 'square' ? 'Square 1:1' : esc(o.name)}</span><span>${o.mb} MB</span></div>`).join('')}
    ${files.length ? `<div class="muted" style="font-size:11.5px;margin-top:4px">${files.join(' · ')}</div>` : ''}
    <div style="margin-top:10px;display:flex;gap:8px"><button class="btn" data-reveal="${esc(vids[0].path)}">Show in folder</button>${vids[0] ? `<button class="btn" data-open="${esc(vids[0].path)}">Play</button>` : ''}</div></div>`;
}

function tokensHtml(d) {
  const t = d.tokens;
  if (!t) return '<div class="empty">Token numbers are saved when the edit finishes.</div>';
  return `<div class="body"><div class="kv"><div>Agent total</div><div><b>${fmt(t.total)}</b></div><div>input / cached / output</div><div>${fmt(t.in)} / ${fmt(t.cached)} / ${fmt(t.out)}</div><div>Transcription (Gemini)</div><div>${fmt(t.gemini)}</div>${t.turns ? `<div>Model calls</div><div>${t.turns}</div>` : ''}</div>
    <div class="muted" style="font-size:11.5px;margin-top:6px">${esc(t.agent || '')} ${esc(t.model || '')}</div>${t.mixed ? '<div class="warn">This window also holds work on Zeno code, so it is not the cost of one edit.</div>' : ''}</div>`;
}

function picsHtml(d) {
  const im = d.images || {}; const items = [['clip_sheet', 'Clips'], ['review_sheet', 'Every cut'], ['marks', 'Marks']].filter(([k]) => im[k]);
  if (!items.length) return '<div class="empty">Pictures appear as they are made.</div>';
  return `<div class="body pics">${items.map(([k, n]) => `<div><img loading="lazy" src="file://${encodeURI(im[k])}?v=${Math.floor(Date.now() / 60000)}" data-open="${esc(im[k])}" alt="${esc(n)}"><div>${n}</div></div>`).join('')}</div>`;
}

function mainHtml(d) {
  if (!d) {
    return `<div class="top"><h1>Zeno</h1></div><div class="scroll" style="display:flex"><div class="welcome"><h1>No edit selected</h1><div>${state.jobs.length ? 'Pick an edit on the left to watch it.' : 'Press New edit to start one. It opens a lean Claude Code session; this window then shows every step.'}</div></div></div>`;
  }
  const s = d.status;
  const lastEv = state.events.length ? feedText(state.events[state.events.length - 1]) : '';
  const nowText = s.phase === 'working' ? (lastEv || s.detail) : (s.detail || s.label);
  return `<div class="top"><h1 title="${esc(d.title)}">${esc(d.title)}</h1><span class="pill ${s.phase}">${esc(s.label)}${d.elapsed && s.phase === 'working' ? ' · ' + esc(d.elapsed) : ''}</span></div>
  <div class="scroll">
    <div class="pipe">${pipeHtml(d)}</div>
    <div class="now ${s.phase === 'working' ? '' : 'idle'}"><span class="lbl">${s.phase === 'working' ? 'Now' : 'Status'}</span><span class="txt">${esc(nowText)}</span></div>
    <div class="grid">
      <div class="col">
        <div class="card"><h2>Cut timeline<span class="sp">${d.plan ? `${d.plan.cuts} cuts · ${mmss(d.plan.seconds)}` : ''}</span></h2>${timelineHtml(d)}</div>
        <div class="card"><h2>Live activity<span class="chips">${FILTERS.map(([k, n]) => `<button class="fchip ${state.filter === k ? 'on' : ''}" data-filter="${k}">${n}</button>`).join('')}</span></h2><div id="feed">${feedHtml()}</div></div>
      </div>
      <div class="col">
        <div class="card"><h2>Footage<span class="sp">${d.clips.length ? d.clips.length + ' clips' : ''}</span></h2>${footageHtml(d)}</div>
        <div class="card"><h2>Plan</h2>${planHtml(d)}</div>
        <div class="card"><h2>Checks</h2>${checksHtml(d)}</div>
        <div class="card"><h2>Output</h2>${outputHtml(d)}</div>
        <div class="card"><h2>Tokens</h2>${tokensHtml(d)}</div>
        <div class="card"><h2>Pictures</h2>${picsHtml(d)}</div>
      </div>
    </div>
  </div>`;
}

let lastMain = '';
function renderMain() {
  const feed = $('#feed');
  const keep = feed ? { top: feed.scrollTop, atEnd: feed.scrollHeight - feed.scrollTop - feed.clientHeight < 40 } : null;
  const scroller = $('.scroll'); const sTop = scroller ? scroller.scrollTop : 0;
  const html = mainHtml(state.detail);
  if (html === lastMain) return;
  lastMain = html;
  $('#main').innerHTML = html;
  const f2 = $('#feed'); if (f2) f2.scrollTop = keep && !keep.atEnd ? keep.top : f2.scrollHeight;
  const s2 = $('.scroll'); if (s2) s2.scrollTop = sTop;
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
  const f = e.target.closest('[data-filter]'); if (f) { state.filter = f.dataset.filter; lastMain = ''; renderMain(); return; }
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
})();
