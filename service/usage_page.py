"""The admin Usage log: a React page of who used what.

Every editor and every run, with the editor's e-mail: coding agent, model, tokens in / cached / out, Gemini transcription tokens, video
length and an estimated cost. The page holds no data. It asks for a company Google sign-in (any account of the allowed domain, no admin
list) and then reads the read-only `/api/usage`, which returns numbers only (no plan files, no keys).

No build step: React 18.3.1 comes pinned from cdnjs and the components are written with `React.createElement` (`h`). The calculations sit
before the `/*__DOM__*/` marker so tests can run them in Node without a browser. The cost is an estimate from list prices (USD per million
tokens, PRICES below); models not listed, such as Gemini or Antigravity models, are shown without a cost instead of with a guess.
"""
NOTION_STYLE = """
*{box-sizing:border-box}
body{margin:0;background:#191919;color:#ebebeb;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:none;width:100%;margin:0;padding:36px 48px 80px}
h1{margin:0 0 4px;font-size:30px;font-weight:700;letter-spacing:-.4px}
h2{margin:34px 0 6px;font-size:16px;font-weight:600}
a{color:#7fb2ff}
p.sub,.sub{margin:0 0 10px;color:#9b9b9b;font-size:14px}
.note{color:#9b9b9b;font-size:13px;margin:0 0 8px}
.muted,td.muted{color:#9b9b9b}
td.muted{white-space:nowrap}
.top{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin:0 0 26px;color:#9b9b9b;font-size:13px}
.top .sp{flex:1}
.brand{font-weight:600;color:#ebebeb}
button,input{font:inherit;color:inherit}
button{cursor:pointer}
.tabs{display:flex;flex-wrap:nowrap;gap:2px;margin:0 0 14px;border-bottom:1px solid #2f2f2f;overflow-x:auto;overflow-y:hidden;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tab{flex:none;white-space:nowrap;background:none;border:0;border-bottom:2px solid transparent;margin-bottom:-1px;padding:7px 12px;color:#9b9b9b;font-size:14px}
.tab:hover{color:#ebebeb}
.tab.on{color:#ebebeb;border-bottom-color:#ebebeb}
.bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0 0 6px}
.chip{background:none;border:0;border-radius:4px;padding:4px 10px;color:#9b9b9b;font-size:13px;text-decoration:none}
.chip:hover{background:#2a2a2a;color:#ebebeb}
.chip.on{background:#2f2f2f;color:#ebebeb}
.bar input{background:transparent;border:1px solid #2f2f2f;border-radius:4px;padding:4px 10px;font-size:13px;outline:none}
.bar input:focus{border-color:#5a5a5a}
.bar input[type=number]{width:70px}
.bar label{color:#9b9b9b;font-size:13px;display:flex;align-items:center;gap:6px;margin:0}
.grow{flex:1;min-width:200px}
.card{margin:0 0 6px}
.scroll{overflow-x:auto;scrollbar-width:thin;scrollbar-color:#3a3a3a transparent;padding-bottom:6px}
.scroll::-webkit-scrollbar{height:8px}.scroll::-webkit-scrollbar-thumb{background:#3a3a3a;border-radius:4px}.scroll::-webkit-scrollbar-track{background:transparent}
table{width:max-content;min-width:100%;border-collapse:collapse;font-size:14px}
th,td{white-space:nowrap}
th:first-child,td:first-child{position:sticky;left:0;background:#191919;z-index:1}
tbody tr:hover td:first-child{background:#202020}
th{text-align:left;font-weight:400;color:#9b9b9b;font-size:12.5px;padding:6px 10px;border-bottom:1px solid #2f2f2f}
td{padding:7px 10px;border-bottom:1px solid #2f2f2f;vertical-align:top}
tbody tr:hover td{background:#202020}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.mail{white-space:nowrap;color:#9b9b9b}
tr.total td{font-weight:600}
.tag{display:inline-block;background:#2a2a2a;border-radius:3px;padding:0 6px;margin:1px 4px 1px 0;font-size:12.5px;white-space:nowrap}
.pill{display:inline-block;border-radius:3px;padding:0 6px;font-size:12.5px;white-space:nowrap}
.pill.ok{background:rgba(70,150,100,.28);color:#8fd6ac}
.pill.amber{background:rgba(205,150,40,.26);color:#e9c46a}
.pill.red{background:rgba(210,80,70,.28);color:#f19a93}
.pill.gray{background:#2a2a2a;color:#9b9b9b}
.stats{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 12px}
.stat{border:1px solid #2f2f2f;border-radius:4px;padding:8px 14px;min-width:150px}
.stat b{display:block;font-size:20px;font-weight:600}
.stat span{color:#9b9b9b;font-size:12.5px}
.foot{color:#7a7a7a;font-size:12.5px;margin:30px 0 0}
.signin{padding:40px 0}
.signin h1{font-size:22px}
.dayhead{margin:26px 0 4px;font-size:15px}
.gantt{margin:6px 0 4px}
.grow2{display:grid;grid-template-columns:170px 1fr;align-items:center;gap:10px;margin:0}
.glabel{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600;padding:5px 0}
.gtrack{position:relative;height:30px;border-bottom:1px solid #2f2f2f;background:repeating-linear-gradient(90deg,transparent 0,transparent calc(var(--hw) - 1px),#262626 calc(var(--hw) - 1px),#262626 var(--hw))}
.gb{position:absolute;top:6px;height:18px;min-width:4px;border-radius:3px;opacity:.95}
.gb.long{top:12px;height:5px;opacity:.5}
.gb.good{background:#3d9a6b}.gb.rework{background:#d9a441}.gb.bad{background:#d4574b}.gb.open{background:#6b7480}
.gaxis{display:grid;grid-template-columns:170px 1fr;gap:10px;color:#9b9b9b;font-size:12px}
.gticks{position:relative;height:18px}.gticks span{position:absolute;transform:translateX(-50%)}
.legend{display:flex;flex-wrap:wrap;gap:16px;margin:10px 0 0;color:#9b9b9b;font-size:12.5px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.rw{color:#e9c46a;font-size:12px;margin-left:6px}
.dt{background:transparent;border:1px solid #2f2f2f;border-radius:4px;padding:3px 8px;color:inherit;font:inherit;font-size:13px;color-scheme:dark}
select.dt{padding:4px 8px}
.pill.ok2{background:rgba(70,150,100,.28);color:#8fd6ac}

@media(max-width:600px){main{padding:24px 14px 60px}h1{font-size:24px}}
"""

USAGE_PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zeno · Usage</title><style>__STYLE__</style></head><body>
<div id="root"><main><h1>Usage</h1><p class="sub">Loading…</p></main></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/react/18.3.1/umd/react.production.min.js" crossorigin="anonymous"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.3.1/umd/react-dom.production.min.js" crossorigin="anonymous"></script>
<script>
const PRICES = [  // [model match, USD per million tokens: input, cached read, cache write, output] from OpenAI and Anthropic list prices
  ['gpt-6.1-sol', 2, 0.1, 2.5, 10], ['gpt-5-codex', 1.25, 0.125, 1.25, 10], ['gpt-5', 1.25, 0.125, 1.25, 10], ['o3', 2, 0.5, 2, 8],
  ['opus-5-5', 4, 0.2, 5, 20], ['opus-5', 5, 0.5, 6.25, 25], ['opus-4', 15, 1.5, 18.75, 75],
  ['sonnet-5', 2, 0.2, 2.5, 10], ['sonnet-4', 3, 0.3, 3.75, 15],
  ['haiku-5', 0.1, 0.01, 0.125, 0.5], ['haiku-4', 1, 0.1, 1.25, 5], ['fable', 10, 0.25, 12.5, 50]];
function normModel(s) {
  return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}
function priceFor(model) {
  const m = normModel(model);
  if (!m) return null;
  for (const p of PRICES) {
    const k = normModel(p[0]);
    if (k.length <= 2 ? (m === k || m.startsWith(k + '-') || m.endsWith('-' + k) || m.includes('-' + k + '-')) : m.includes(k)) return p;
  }
  return null;
}
function n(v) { return Number(v || 0); }
function cacheWriteBreakdown(r) {
  const jr = (r && r.job_report) || {};
  const acc = jr.accounting || {};
  const ag = jr.agent || {};
  const cw = Math.max(0, n(acc.cache_write_tokens ?? acc.agent_cache_write_tokens ?? ag.cache_write_tokens ?? (r && (r.agent_cache_write_tokens ?? r.cache_write_tokens))));
  const e1h = Math.max(0, n(acc.ephemeral_1h_input_tokens ?? ag.ephemeral_1h_input_tokens ?? (r && r.ephemeral_1h_input_tokens)));
  const e5m = Math.max(0, n(acc.ephemeral_5m_input_tokens ?? ag.ephemeral_5m_input_tokens));
  const total = Math.max(cw, e5m + e1h);
  const w1h = Math.min(total, e1h);
  return {total, w5m: total - w1h, w1h};
}
function cacheWriteTokens(r) { return cacheWriteBreakdown(r).total; }
function agentCombinedTotal(r) {
  const acc = ((r && r.job_report) || {}).accounting || {};
  if (acc.agent_total_including_cache != null && n(acc.agent_total_including_cache) > 0) return n(acc.agent_total_including_cache);
  const cw = cacheWriteTokens(r);
  const aIn = n(r.agent_in_tokens), aCached = n(r.agent_cached_tokens), aOut = n(r.agent_out_tokens), aTot = n(r.agent_total_tokens);
  const writeSeparate = cw > 0 && (aIn < cw || (aTot > 0 && aTot === aIn + cw + aOut));
  return Math.max(aIn + (writeSeparate ? cw : 0) + aCached + aOut, aTot);
}
function costUsd(r) {
  const p = priceFor(r && r.agent_model);
  if (!p) return null;
  const aIn = n(r.agent_in_tokens), aCached = n(r.agent_cached_tokens), aOut = n(r.agent_out_tokens), aTot = n(r.agent_total_tokens);
  const {total: cw, w5m, w1h} = cacheWriteBreakdown(r);
  if (cw > 0) {
    const acc = ((r && r.job_report) || {}).accounting || {};
    const totIncl = n(acc.agent_total_including_cache);
    const writeSeparate = aIn < cw || (totIncl > 0 && totIncl === aIn + cw + aCached + aOut) || (aTot > 0 && aTot === aIn + cw + aOut);
    const freshIn = writeSeparate ? aIn : Math.max(0, aIn - cw);
    return (freshIn * p[1] + aCached * p[2] + w5m * p[3] + w1h * (p[1] * 2) + aOut * p[4]) / 1e6;
  }
  return (aIn * p[1] + aCached * p[2] + aOut * p[4]) / 1e6;
}
function who(r) { return (r.user_email || r.editor_name || 'unknown').toLowerCase(); }
function periodCutoff(p, now, from, to) {  // returns {from, to} in seconds; to = 0 means "until now"
  if (p === 'all') return {from: 0, to: 0};
  if (p === 'custom') { const a = from ? new Date(from).getTime() / 1000 : 0, b = to ? new Date(to).getTime() / 1000 : 0; return {from: a || 0, to: b || 0}; }
  if (p === 'today') { const d = new Date(now * 1000); d.setHours(0, 0, 0, 0); return {from: d.getTime() / 1000, to: 0}; }
  return {from: now - Number(p) * 86400, to: 0};
}
function inPeriod(r, rg) { const t = n(r.created_at); return t >= n(rg.from) && (!rg.to || t <= rg.to); }
const HEAVY_CACHE = 5000000;  // a run that re-read more than 5M cached tokens had a long chat with many steps
function wasteGroups(runs) {  // one row per editor and project; flagged when a run re-read a lot or the project was run several times
  const g = new Map();
  for (const r of runs) {
    if (!r.project) continue;
    const k = who(r) + '|' + r.project;
    const e = g.get(k) || {editor: r.editor_name || who(r), project: r.project, runs: 0, fresh: 0, cached: 0, maxCached: 0};
    e.runs += 1; e.fresh += n(r.agent_in_tokens) + n(r.agent_out_tokens); e.cached += n(r.agent_cached_tokens); e.maxCached = Math.max(e.maxCached, n(r.agent_cached_tokens));
    g.set(k, e);
  }
  const rows = [...g.values()].filter(e => e.runs > 1 || e.maxCached >= HEAVY_CACHE);
  rows.forEach(e => { e.flags = [e.maxCached >= HEAVY_CACHE ? 'Long chat' : '', e.runs > 1 ? 'Run ' + e.runs + ' times' : ''].filter(Boolean); });
  return rows.sort((a, b) => b.cached - a.cached);
}
function transcribeSummary(events) {
  const ev = events || [];
  const bad = ev.filter(e => e.status !== 'ok');
  const failed = ev.filter(e => e.status === 'failed').length;
  const by = {};
  ev.forEach(e => { const u = by[e.user_name] || (by[e.user_name] = {name: e.user_name, n: 0, bad: 0, last: 0}); u.n += 1; if (e.status !== 'ok') u.bad += 1; u.last = Math.max(u.last, n(e.created_at)); });
  return {calls: ev.length, bad: bad.length, failed, incomplete: bad.length - failed, rate: ev.length ? Math.round(100 * (ev.length - bad.length) / ev.length) : 100,
    users: Object.values(by).sort((a, b) => b.bad - a.bad || b.n - a.n)};
}
function dayKey(t) { const d = new Date(n(t) * 1000); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
function dayLabel(k, short) { const d = new Date(k + 'T12:00:00'); return d.toLocaleDateString('en-GB', short ? {weekday: 'short', day: 'numeric'} : {weekday: 'long', day: 'numeric', month: 'short'}); }
function byDay(runs) {  // newest day first; each day holds its runs
  const days = new Map();
  for (const r of runs) { const k = dayKey(r.created_at); if (!days.has(k)) days.set(k, []); days.get(k).push(r); }
  return [...days.entries()].sort((a, b) => a[0] < b[0] ? 1 : -1).map(([key, rs]) => ({key, runs: rs}));
}
function quality(runs) {  // how complete the recorded numbers are for these videos
  const q = {total: runs.length, noAgent: 0, noDetail: 0, mixed: 0, exact: 0, noGemini: 0};
  for (const r of runs) {
    const jr = r.job_report || {}, ag = jr.agent || {}, acc = jr.accounting || {};
    if (!n(r.agent_total_tokens) && !n(r.agent_cached_tokens)) q.noAgent += 1;
    if (ag.turns == null) q.noDetail += 1;
    if (ag.mixed_with_development) q.mixed += 1;
    if (acc.agent_token_precision === 'exact') q.exact += 1;
    if (!n(r.transcribe_total_tokens)) q.noGemini += 1;
  }
  return q;
}
function notionName(v) { return String(v || '').replace(/^(reel_|SQ_)/i, '').replace(/\\.mp4$/i, '').replace(/\\s*\\(\\d+\\)\\s*$/, '').trim(); }
function clusterRuns(rs) {  // renders of one video share the job start, so each later render already holds the earlier ones: keep the last of each window
  const sorted = rs.slice().sort((a, b) => n(a.created_at) - n(b.created_at));
  const out = [];
  for (const r of sorted) {
    const w = n(((r.job_report || {}).agent || {}).window_minutes);
    const start = w ? n(r.created_at) - w * 60 : null;
    const last = out[out.length - 1];
    if (last && start != null && last.start != null && Math.abs(start - last.start) <= 300) out[out.length - 1] = {start, row: r};
    else out.push({start, row: r});
  }
  return out.map(c => c.row);
}
function jobOutcome(j) {
  if (j.kind === 'blocked' || j.kind === 'render_failed' || j.kind === 'failed') return 'bad';
  if (j.kind === 'render') return j.idx > 0 ? 'rework' : 'good';
  if (j.kind === 'feedback') return 'rework';
  return 'open';
}
function buildEdits(jobs, runs) {  // one video edit = one editor + one script; every extra job on it is a rework
  const by = new Map(), loose = [];
  for (const j of jobs || []) {
    if (!j.project) { loose.push(Object.assign({idx: 0}, j)); continue; }
    const k = String(j.editor_name || '').toLowerCase() + '|' + j.project;
    if (!by.has(k)) by.set(k, {key: k, editor: j.editor_name, project: j.project, jobs: []});
    by.get(k).jobs.push(Object.assign({}, j));
  }
  const edits = [];
  for (const e of by.values()) {
    e.jobs.sort((a, b) => n(a.created_at) - n(b.created_at));
    e.jobs.forEach((j, i) => { j.idx = i; });
    const named = e.jobs.filter(j => j.video_name);
    e.name = notionName(named.length ? named[named.length - 1].video_name : '') || e.project;
    e.start = n(e.jobs[0].created_at);
    const ok = e.jobs.filter(j => j.kind === 'render');
    e.finished = ok.length ? n(ok[0].updated_at) : 0;
    e.last = Math.max(...e.jobs.map(j => n(j.updated_at)));
    e.nJobs = e.jobs.length; e.rendersOk = ok.length;
    e.failed = e.jobs.filter(j => j.kind === 'render_failed' || j.kind === 'failed').length;
    e.feedback = e.jobs.filter(j => j.kind === 'feedback').length;
    e.blocked = e.jobs.filter(j => j.kind === 'blocked').length;
    e.result = !ok.length ? 'open' : e.nJobs === 1 ? 'one' : 'rework';
    const mine = (runs || []).filter(r => String(r.editor_name || '').toLowerCase() === String(e.editor || '').toLowerCase() && r.project === e.project);
    const kept = clusterRuns(mine);
    e.q = kept.length ? kept.map(dataQuality).sort((a, b) => QRANK[a[0]] - QRANK[b[0]])[0] : null; e.tok = aggregate(kept).total; e.raw = mine.length; e.kept = kept.length;
    e.calls = kept.some(r => ((r.job_report || {}).agent || {}).turns != null) ? kept.reduce((a, r) => a + n(((r.job_report || {}).agent || {}).turns), 0) : null;
    e.warn = [];
    if (e.nJobs > 1) e.warn.push(e.nJobs + ' jobs');
    if (e.feedback) e.warn.push(e.feedback + (e.feedback === 1 ? ' feedback' : ' feedbacks'));
    if (e.failed) e.warn.push(e.failed + (e.failed === 1 ? ' failed render' : ' failed renders'));
    if (!ok.length) e.warn.push('not finished');
    edits.push(e);
  }
  edits.sort((a, b) => b.last - a.last);
  return {edits, loose, jobs: edits.flatMap(e => e.jobs).concat(loose)};
}
function verParts(v) { return String(v || '').replace(/^v/i, '').split('.').map(x => parseInt(x, 10) || 0); }
function verCmp(a, b) { const x = verParts(a), y = verParts(b); for (let i = 0; i < 3; i++) { if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) - (y[i] || 0); } return 0; }
function versionRows(keys) {  // one row per editor: newest version their keys report, and when any key was last used
  const by = new Map();
  for (const k of keys || []) {
    const name = String(k.name || '').trim();
    if (!name || k.revoked_at) continue;
    const id = name.toLowerCase();
    const cur = by.get(id) || {name, version: '', last: 0};
    cur.last = Math.max(cur.last, n(k.last_used_at));
    if (k.zeno_version && (!cur.version || verCmp(k.zeno_version, cur.version) > 0)) cur.version = k.zeno_version;
    by.set(id, cur);
  }
  const rows = [...by.values()].filter(r => r.last > 86400);
  let newest = '';
  rows.forEach(r => { if (r.version && (!newest || verCmp(r.version, newest) > 0)) newest = r.version; });
  rows.forEach(r => { r.state = !r.version ? 'unknown' : verCmp(r.version, newest) < 0 ? 'behind' : 'current'; });
  const rank = {behind: 0, unknown: 1, current: 2};
  rows.sort((a, b) => rank[a.state] - rank[b.state] || b.last - a.last);
  return {rows, newest};
}
function matches(r, q) {
  if (!q) return true;
  const hay = [r.editor_name, r.user_email, r.project, r.agent_name, r.agent_model, r.language].join(' ').toLowerCase();
  return q.toLowerCase().split(/\\s+/).every(w => hay.includes(w));
}
function aggregate(runs) {
  const users = new Map();
  const total = {runs: 0, seconds: 0, ain: 0, cached: 0, cwrite: 0, aout: 0, atotal: 0, acombined: 0, gin: 0, gout: 0, gtotal: 0, usd: 0, unpriced: 0, editors: 0};
  for (const r of runs) {
    const k = who(r);
    if (!users.has(k)) users.set(k, {key: k, name: r.editor_name || k, email: (r.user_email || '').toLowerCase(), agents: new Set(), models: new Set(),
      runs: 0, seconds: 0, ain: 0, cached: 0, cwrite: 0, aout: 0, atotal: 0, acombined: 0, gin: 0, gout: 0, gtotal: 0, usd: 0, unpriced: 0, last: 0});
    const u = users.get(k);
    const c = costUsd(r);
    const cw = cacheWriteTokens(r);
    const comb = agentCombinedTotal(r);
    for (const t of [u, total]) {
      t.runs += 1; t.seconds += n(r.video_seconds); t.ain += n(r.agent_in_tokens); t.cached += n(r.agent_cached_tokens);
      t.cwrite += cw; t.aout += n(r.agent_out_tokens); t.atotal += n(r.agent_total_tokens); t.acombined += comb;
      t.gin += n(r.transcribe_in_tokens); t.gout += n(r.transcribe_out_tokens); t.gtotal += n(r.transcribe_total_tokens);
      if (c === null) t.unpriced += 1; else t.usd += c;
    }
    u.agents.add(r.agent_name || 'unknown'); u.models.add(r.agent_model || 'unknown');
    u.last = Math.max(u.last, n(r.created_at));
  }
  total.editors = users.size;
  return {users: [...users.values()].sort((a, b) => b.acombined - a.acombined || b.atotal - a.atotal), total};
}
/*__DOM__*/
const {useState, useEffect, useRef} = React;
const h = React.createElement;
const TOKEN_KEY = 'zeno_usage_session';
const fmtNum = x => Number(x || 0).toLocaleString();
const fmtBig = x => { x = Number(x || 0); return x >= 1e9 ? (x / 1e9).toFixed(2) + 'B' : x >= 1e6 ? (x / 1e6).toFixed(1) + 'M' : x >= 1e4 ? Math.round(x / 1e3) + 'k' : fmtNum(x); };
const pad2 = x => String(x).padStart(2, '0');
const clock12 = d => { const h12 = d.getHours() % 12 || 12; return h12 + ':' + pad2(d.getMinutes()) + ' ' + (d.getHours() < 12 ? 'AM' : 'PM'); };
const fmtDate = t => { if (!t) return ''; const d = new Date(t * 1000); return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + ' ' + clock12(d); };
const inr = (usd, rate) => '₹' + Math.round(usd * rate).toLocaleString();

function csvOf(runs) {
  const head = ['when', 'editor', 'email', 'project', 'language', 'agent', 'model', 'clips', 'video_seconds', 'agent_in', 'agent_cached_read',
    'agent_cache_write', 'agent_out', 'agent_total', 'agent_combined_total', 'gemini_in', 'gemini_out', 'gemini_total', 'est_usd'];
  const q = v => '"' + String(v ?? '').replace(/"/g, '""') + '"';
  return [head.join(',')].concat(runs.map(r => [fmtDate(r.created_at), r.editor_name, r.user_email, r.project, r.language, r.agent_name, r.agent_model,
    r.clips_count, r.video_seconds, r.agent_in_tokens, r.agent_cached_tokens, cacheWriteTokens(r), r.agent_out_tokens, r.agent_total_tokens,
    agentCombinedTotal(r), r.transcribe_in_tokens, r.transcribe_out_tokens, r.transcribe_total_tokens, (costUsd(r) ?? '')].map(q).join(','))).join('\\n');
}

function Tags({items}) { return h('span', null, [...items].map(x => h('span', {className: 'tag', key: x}, x))); }
function Th({children, right}) { return h('th', {className: right ? 'n' : ''}, children); }

const short = s => { s = String(s || ''); return s.length > 40 ? s.slice(0, 37) + '…' : s; };

function TranscriptionHealth({data, cutoff}) {
  const ev = (data.transcriptions || []).filter(e => inPeriod(e, cutoff));
  const bad = ev.filter(e => e.status !== 'ok');
  const sm = transcribeSummary(ev);
  const avg = a => a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0;
  const models = {};
  ev.filter(e => e.model).forEach(e => { const m = models[e.model] || (models[e.model] = {n: 0, s: []}); m.n += 1; m.s.push(n(e.seconds)); });
  const fell = ev.filter(e => e.status === 'ok' && e.failed_models).length;
  const stat = (v, label) => h('div', {className: 'stat', key: label}, h('b', null, v), h('span', null, label));
  return h('div', {className: 'card'},
    h('h1', null, 'Transcription health'),
    h('p', {className: 'sub'}, 'Every Gemini transcription call: who, which model answered, how long it took, and why a call failed. Numbers and short reasons only, never audio or text.'),
    h('div', {className: 'stats'},
      stat(fmtNum(sm.calls), 'transcription calls'), stat(sm.rate + '%', 'answered fully'), stat(fmtNum(sm.failed), 'failed'),
      stat(fmtNum(sm.incomplete), 'incomplete'), stat(fmtNum(fell), 'saved by a fallback model'),
      stat(avg(ev.map(e => n(e.seconds))).toFixed(1) + ' s', 'average time')),
    Object.keys(models).length ? h('p', {className: 'sub', style: {marginTop: 14}}, 'Models that answered: ' + Object.keys(models).map(m => m + ' ' + models[m].n + ' calls, ' + avg(models[m].s).toFixed(1) + ' s').join(' · ')) : null,
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Editor'), h(Th, {right: true}, 'Calls'), h(Th, {right: true}, 'Problems'), h(Th, null, 'Last call'))),
      h('tbody', null, sm.users.length ? sm.users.map(u => h('tr', {key: u.name},
        h('td', null, h('b', null, u.name)), h('td', {className: 'n'}, fmtNum(u.n)),
        h('td', {className: 'n'}, u.bad ? h('b', null, fmtNum(u.bad)) : '0'), h('td', {className: 'muted'}, fmtDate(u.last))))
        : h('tr', null, h('td', {colSpan: 4}, 'No transcription calls recorded in this period yet'))))),
    bad.length ? h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'When'), h(Th, null, 'Editor'), h(Th, null, 'Result'), h(Th, null, 'Why'), h(Th, null, 'Models that failed'), h(Th, {right: true}, 'Audio s'), h(Th, {right: true}, 'Took s'))),
      h('tbody', null, bad.slice(0, 50).map(e => h('tr', {key: e.id},
        h('td', {className: 'muted'}, fmtDate(e.created_at)), h('td', null, e.user_name), h('td', null, h('b', null, e.status)), h('td', null, e.reason),
        h('td', {className: 'muted'}, e.failed_models), h('td', {className: 'n'}, Math.round(n(e.audio_seconds))), h('td', {className: 'n'}, n(e.seconds).toFixed(1))))))) : null);
}

const sumNew = t => t.ain + t.aout;
const tokHead = () => [['i', 'Input'], ['cw', 'of which cache write'], ['cr', 'Re-read (cache read)'], ['o', 'Output'], ['gi', 'Gemini in'], ['go', 'Gemini out'], ['gt', 'Gemini total']]
  .map(([k, label]) => h(Th, {right: true, key: k}, label));
const tokCells = u => ['ain', 'cwrite', 'cached', 'aout', 'gin', 'gout', 'gtotal'].map(k => h('td', {className: 'n' + (k === 'cwrite' ? ' muted' : ''), key: k}, fmtNum(u[k])));
const money = (t, rate) => (!t.usd && t.unpriced) ? 'No price' : inr(t.usd, rate) + (t.unpriced ? ' *' : '');
function dataQuality(r) {  // how far the numbers of one render can be trusted
  const jr = r.job_report || {}, ag = jr.agent || {}, acc = jr.accounting || {};
  if (!n(r.agent_total_tokens) && !n(r.agent_cached_tokens)) return ['red', 'No agent tokens', 'Zeno reported no agent tokens for this render'];
  if (!r.job_report || !Object.keys(jr).length) return ['gray', 'Old Zeno', 'No job report: this Zeno version does not send calls, window or precision'];
  if (ag.mixed_with_development) return ['gray', 'Mixed', 'The chat also held work on Zeno code, so tokens are more than one edit'];
  if (acc.agent_token_precision === 'exact') return ['ok', 'Exact', 'Tokens read exactly from the agent log'];
  return ['amber', 'Estimated', 'Tokens are an approximation'];
}
const QRANK = {red: 0, gray: 1, amber: 2, ok: 3};
const qPill = q => h('span', {className: 'pill ' + (q[0] === 'ok' ? 'ok2' : q[0]), title: q[2]}, q[1]);

function Daily({runs, rate}) {
  const {users, total: t} = aggregate(runs);
  const days = byDay(runs).slice(0, 31);
  const cols = days.slice(0, 7).map(d => d.key).reverse();           // up to 7 days, oldest first
  const count = new Map();
  for (const r of runs) { const e = who(r), d = dayKey(r.created_at); if (!count.has(e)) count.set(e, new Map()); count.get(e).set(d, (count.get(e).get(d) || 0) + 1); }
  const rows = users.slice().sort((a, b) => b.runs - a.runs || sumNew(b) - sumNew(a));
  const sum = (arr, k) => arr.reduce((x, r) => x + n(r[k]), 0);
  return h('div', null,
    h('p', {className: 'sub', style: {marginTop: 14}}, t.runs ? fmtNum(t.runs) + ' renders by ' + fmtNum(t.editors) + ' editors · ' + fmtNum(sumNew(t)) + ' new tokens · ' + money(t, rate) + ' estimated' : 'No renders in this period.'),
    h('h2', null, 'Renders per editor, per day'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Editor'), cols.map(c => h(Th, {right: true, key: c}, dayLabel(c, true))), h(Th, {right: true}, 'Total'))),
      h('tbody', null, rows.map(u => h('tr', {key: u.key}, h('td', null, h('b', null, u.name)),
          cols.map(c => { const v = (count.get(u.key) || new Map()).get(c) || 0; return h('td', {className: 'n' + (v ? '' : ' muted'), key: c}, v || '–'); }),
          h('td', {className: 'n'}, h('b', null, fmtNum(u.runs))))),
        rows.length ? h('tr', {className: 'total'}, h('td', null, 'All editors'), cols.map(c => h('td', {className: 'n', key: c}, fmtNum((days.find(d => d.key === c) || {runs: []}).runs.length))), h('td', {className: 'n'}, fmtNum(t.runs))) : null))),
    cols.length < days.length ? h('p', {className: 'note'}, 'The table shows the latest 7 days; every day is listed below.') : null,
    h('h2', null, 'Usage per editor, day by day (per render)'),
    days.map(d => {
      const a = aggregate(d.runs);
      return h('div', {key: d.key},
        h('div', {className: 'dayhead'}, h('b', null, dayLabel(d.key)), h('span', {className: 'muted'}, '  ' + d.runs.length + (d.runs.length === 1 ? ' render' : ' renders') + ' · ' + fmtNum(sumNew(a.total)) + ' new tokens · ' + money(a.total, rate))),
        h('div', {className: 'scroll'}, h('table', null,
          h('thead', null, h('tr', null, h(Th, null, 'Editor'), h(Th, {right: true}, 'Renders'), tokHead(), h(Th, {right: true}, 'Est. cost'), h(Th, null, 'Last render'))),
          h('tbody', null, a.users.slice().sort((x, y) => y.runs - x.runs || sumNew(y) - sumNew(x)).map(u => h('tr', {key: u.key},
            h('td', null, h('b', null, u.name)), h('td', {className: 'n'}, fmtNum(u.runs)), tokCells(u),
            h('td', {className: 'n'}, (u.usd || !u.unpriced) ? inr(u.usd, rate) + (u.unpriced ? ' *' : '') : '–'), h('td', {className: 'muted'}, fmtDate(u.last)))),
            h('tr', {className: 'total'}, h('td', null, 'Day total'), h('td', {className: 'n'}, fmtNum(a.total.runs)), tokCells(a.total), h('td', {className: 'n'}, money(a.total, rate)), h('td', null))))));
    }),
    t.unpriced ? h('p', {className: 'foot'}, '* ' + t.unpriced + ' video(s) use a model with no price here and are left out of the cost.') : null);
}

function Videos({runs, rate, showAll, setShowAll, jobs, data}) {
  const shown = showAll ? runs : runs.slice(0, 100);
  const names = new Map();
  for (const j of jobs || []) { if (j.project && j.video_name) names.set(String(j.editor_name || '').toLowerCase() + '|' + j.project, notionName(j.video_name)); }
  const jobCount = new Map();
  for (const j of jobs || []) { if (j.project) { const k = String(j.editor_name || '').toLowerCase() + '|' + j.project; jobCount.set(k, (jobCount.get(k) || 0) + 1); } }
  const tev = data.transcriptions || [];
  const keyVer = new Map();
  for (const k of data.keys || []) { if (k.zeno_version && !k.revoked_at) { const id = String(k.name || '').toLowerCase(); if (!keyVer.has(id) || verCmp(k.zeno_version, keyVer.get(id)) > 0) keyVer.set(id, k.zeno_version); } }
  const verOf = r => {  // version seen on that editor's Gemini call closest to this render; else the newest version their key reports now
    const me = String(r.editor_name || '').toLowerCase();
    let best = null, gap = 6 * 3600;
    for (const e of tev) { if (String(e.user_name || '').toLowerCase() === me && e.zeno_version) { const d = Math.abs(n(e.created_at) - n(r.created_at)); if (d < gap) { gap = d; best = e.zeno_version; } } }
    return best ? {v: best, exact: true} : keyVer.has(me) ? {v: keyVer.get(me), exact: false} : null;
  };
  const gcalls = r => {  // Gemini requests this editor made while this render's chat ran
    const w = n(((r.job_report || {}).agent || {}).window_minutes);
    if (!w) return null;
    const lo = n(r.created_at) - w * 60 - 60, hi = n(r.created_at) + 60, me = String(r.editor_name || '').toLowerCase();
    const ev = tev.filter(e => String(e.user_name || '').toLowerCase() === me && n(e.created_at) >= lo && n(e.created_at) <= hi);
    return {all: ev.length, bad: ev.filter(e => e.status !== 'ok').length, hit: ev.filter(e => n(e.cached) > 0).length};
  };
  return h('div', null,
    h('p', {className: 'sub', style: {marginTop: 14}}, 'One row per finished render, newest first. A video rendered again appears again. Zeno version * = not seen on a request near that render, so it is the newest version their key reports now. The Video edits tab counts each video once.'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'When'), h(Th, null, 'Editor'), h(Th, null, 'Notion name'), h(Th, null, 'Model'), h(Th, null, 'Zeno version'), h(Th, null, 'Language'), h(Th, {right: true}, 'Clips'), h(Th, {right: true}, 'Seconds'),
        tokHead(), h(Th, {right: true}, 'Agent calls'), h(Th, {right: true}, 'Cache re-read calls'), h(Th, {right: true}, 'Re-read per call'), h(Th, {right: true}, 'Gemini calls'), h(Th, {right: true}, 'Window (min)'), h(Th, {right: true}, 'Est. cost'), h(Th, null, 'Data'), h(Th, null, 'Notes'))),
      h('tbody', null, shown.length ? shown.map(r => {
        const c = costUsd(r), ag = (r.job_report || {}).agent || {}, fails = ((r.job_report || {}).failures || []).length;
        const u = {ain: n(r.agent_in_tokens), cwrite: cacheWriteTokens(r), cached: n(r.agent_cached_tokens), aout: n(r.agent_out_tokens), gin: n(r.transcribe_in_tokens), gout: n(r.transcribe_out_tokens), gtotal: n(r.transcribe_total_tokens)};
        return h('tr', {key: r.id},
          h('td', {className: 'muted'}, fmtDate(r.created_at)), h('td', null, h('b', null, r.editor_name)), h('td', {title: r.project, style: {maxWidth: 360, overflow: 'hidden', textOverflow: 'ellipsis'}}, names.get(String(r.editor_name || '').toLowerCase() + '|' + r.project) || short(r.project)), h('td', {className: 'muted'}, r.agent_model && r.agent_model !== 'unknown' ? r.agent_model : '–'), (() => { const v = verOf(r); return h('td', {className: 'muted', title: v && !v.exact ? 'No Gemini call near this render; this is the newest version their key reports now' : 'Version seen on the request closest to this render'}, v ? 'v' + String(v.v).replace(/^v/i, '') + (v.exact ? '' : ' *') : '–'); })(), h('td', null, r.language),
          h('td', {className: 'n'}, fmtNum(r.clips_count)), h('td', {className: 'n'}, Math.round(n(r.video_seconds))), tokCells(u),
          h('td', {className: 'n muted'}, ag.turns != null ? fmtNum(ag.turns) : '–'), h('td', {className: 'n muted', title: 'Every agent call after the first re-reads the cached chat'}, ag.turns != null ? (n(r.agent_cached_tokens) ? fmtNum(ag.turns) : 0) : '–'),
          h('td', {className: 'n muted'}, ag.turns && n(r.agent_cached_tokens) ? fmtNum(Math.round(n(r.agent_cached_tokens) / ag.turns)) : '–'),
          (() => { const g = gcalls(r); return h('td', {className: 'n muted'}, g ? fmtNum(g.all) + (g.bad ? ' · ' + g.bad + ' failed' : '') + (g.hit ? ' · ' + g.hit + ' reused' : '') : '–'); })(), h('td', {className: 'n muted'}, ag.window_minutes != null ? ag.window_minutes : '–'),
          h('td', {className: 'n'}, c === null ? '–' : inr(c, rate)),
          h('td', null, qPill(dataQuality(r))),
          h('td', null, (() => { const c = jobCount.get(String(r.editor_name || '').toLowerCase() + '|' + r.project); return h('span', {className: 'pill ok2', style: {marginRight: 4}, title: 'This render finished and the video went to the output folder'}, c == null ? 'Done' : c === 1 ? 'Done in 1 job' : 'Done after ' + c + ' jobs'); })(),
            n(r.agent_cached_tokens) >= HEAVY_CACHE ? h('span', {className: 'pill amber', style: {marginRight: 4}}, 'Long chat') : null,
            !n(r.agent_total_tokens) && !n(r.agent_cached_tokens) ? h('span', {className: 'pill red', style: {marginRight: 4}}, 'No agent tokens') : null,
            ag.mixed_with_development ? h('span', {className: 'pill gray', style: {marginRight: 4}, title: 'This window also holds work on Zeno code, so it is not the cost of one edit'}, 'Mixed with dev work') : null,
            fails ? h('span', {className: 'pill red'}, fails + (fails === 1 ? ' failure' : ' failures')) : null));
      }) : h('tr', null, h('td', {colSpan: 25}, 'No videos in this period'))))),
    runs.length > 100 ? h('p', {className: 'note', style: {marginTop: 10}}, h('a', {href: '#', onClick: e => { e.preventDefault(); setShowAll(!showAll); }}, showAll ? 'Show latest 100' : 'Show all ' + runs.length)) : null);
}

function Editors({runs, rate, vers}) {
  const {users, total: t} = aggregate(runs);
  const verOf = new Map(vers.rows.map(r => [r.name.toLowerCase(), r]));
  const dayCount = u => new Set(runs.filter(r => who(r) === u.key).map(r => dayKey(r.created_at))).size;
  return h('div', null,
    h('p', {className: 'sub', style: {marginTop: 14}}, 'Totals for the chosen period, one row per editor.'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Editor'), h(Th, null, 'Version'), h(Th, {right: true}, 'Renders'), h(Th, {right: true}, 'Active days'), h(Th, {right: true}, 'Renders per day'),
        tokHead(), h(Th, {right: true}, 'New tokens per render'), h(Th, {right: true}, 'Est. cost'), h(Th, null, 'Last render'))),
      h('tbody', null, users.slice().sort((a, b) => b.runs - a.runs).map(u => {
        const v = verOf.get(String(u.name).toLowerCase()), d = dayCount(u) || 1;
        return h('tr', {key: u.key}, h('td', null, h('b', null, u.name)),
          h('td', null, v && v.version ? h('span', {className: 'pill ' + (v.state === 'behind' ? 'amber' : 'gray')}, 'v' + String(v.version).replace(/^v/i, '')) : ''),
          h('td', {className: 'n'}, fmtNum(u.runs)), h('td', {className: 'n'}, fmtNum(d)), h('td', {className: 'n'}, (u.runs / d).toFixed(1)), tokCells(u),
          h('td', {className: 'n'}, fmtNum(Math.round(sumNew(u) / Math.max(1, u.runs)))),
          h('td', {className: 'n'}, (u.usd || !u.unpriced) ? inr(u.usd, rate) + (u.unpriced ? ' *' : '') : '–'), h('td', {className: 'muted'}, fmtDate(u.last)));
      }),
        users.length ? h('tr', {className: 'total'}, h('td', null, 'All editors'), h('td', null), h('td', {className: 'n'}, fmtNum(t.runs)), h('td', null), h('td', null), tokCells(t),
          h('td', {className: 'n'}, fmtNum(Math.round(sumNew(t) / Math.max(1, t.runs)))), h('td', {className: 'n'}, money(t, rate)), h('td', null))
          : h('tr', null, h('td', {colSpan: 17}, 'No videos in this period'))))));
}

function Problems({data, runs, cutoff}) {
  const waste = wasteGroups(runs);
  return h('div', null,
    h('h2', null, 'Waste watch'),
    h('p', {className: 'note'}, 'Projects where a video re-read over ' + fmtNum(HEAVY_CACHE) + ' cached tokens (a long chat), or that were edited more than once.'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Editor'), h(Th, null, 'Project'), h(Th, {right: true}, 'Videos'), h(Th, {right: true}, 'New tokens'), h(Th, {right: true}, 'Re-read'), h(Th, null, 'Why flagged'))),
      h('tbody', null, waste.length ? waste.slice(0, 20).map(e => h('tr', {key: e.editor + e.project},
        h('td', null, h('b', null, e.editor)), h('td', {title: e.project}, short(e.project)), h('td', {className: 'n'}, fmtNum(e.runs)), h('td', {className: 'n'}, fmtNum(e.fresh)),
        h('td', {className: 'n'}, fmtNum(e.cached)), h('td', null, e.flags.map(f => h('span', {className: 'pill amber', key: f, style: {marginRight: 4}}, f)))))
        : h('tr', null, h('td', {colSpan: 6}, 'Nothing flagged in this period'))))),
    h(TranscriptionHealth, {data, cutoff}));
}

function Health({health, vers}) {
  const ok = (v, yes, no) => h('span', {className: 'pill ' + (v ? 'ok' : 'red')}, v ? yes : no);
  return h('div', null,
    h('h2', null, 'Server'),
    h('div', {className: 'scroll'}, h('table', null, h('tbody', null, health ? [
      ['Server', ok(health.ok, 'Up', 'Problem')], ['Gemini key', ok(health.gemini_configured, 'Configured', 'Missing')],
      ['Key store', ok(health.key_store_ok, (health.key_store || '?') + ' working', (health.key_store || '?') + ' not working')],
      ['Google sign-in', ok(health.google_oauth_enabled, 'On', 'Off')]].map(r => h('tr', {key: r[0]}, h('td', {style: {width: 200}}, r[0]), h('td', null, r[1])))
      : h('tr', null, h('td', null, 'Checking…'))))),
    h(Versions, {vers}));
}

function Versions({vers}) {
  const pill = r => r.state === 'behind' ? h('span', {className: 'pill amber'}, 'Update needed') : r.state === 'current' ? h('span', {className: 'pill ok'}, 'Up to date') : h('span', {className: 'pill gray'}, 'Unknown');
  return h('div', {className: 'card'}, h('h1', null, 'Zeno versions'),
    h('p', {className: 'sub'}, 'The newest version each editor\\'s keys have reported, compared with the newest version seen across the team' + (vers.newest ? ' (v' + String(vers.newest).replace(/^v/i, '') + ')' : '') + '.'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Editor'), h(Th, null, 'Version'), h(Th, null, 'Status'), h(Th, null, 'Last used'))),
      h('tbody', null, vers.rows.length ? vers.rows.map(r => h('tr', {key: r.name},
        h('td', null, h('b', null, r.name)), h('td', null, r.version ? 'v' + String(r.version).replace(/^v/i, '') : '–'), h('td', null, pill(r)), h('td', {className: 'muted'}, fmtDate(r.last))))
        : h('tr', null, h('td', {colSpan: 4}, 'No version data yet')))))); 
}

function Notes({runs, data}) {
  const q = quality(runs);
  const pct = x => q.total ? Math.round(100 * x / q.total) + '%' : '–';
  const row = (label, v, why) => h('tr', {key: label}, h('td', null, label), h('td', {className: 'n'}, fmtNum(v)), h('td', {className: 'n muted'}, pct(v)), h('td', {className: 'muted', style: {whiteSpace: 'normal'}}, why));
  const para = (t) => h('p', {className: 'note', style: {maxWidth: 900, fontSize: 14, color: '#cfcfcf'}}, t);
  return h('div', null,
    h('h2', null, 'How each video is recorded'),
    para('One row is one video job. When the editor finishes, their Zeno reads the coding agent\\'s own log (Claude Code, Codex or Antigravity) for the time from the start of the job to its end, and sends the totals to the server.'),
    para('Input = fresh input plus cache write. Cache write is shown again in its own column. Re-read = cache read: the chat sent again on every step. Output = what the agent wrote. Every response is counted once, summed over all steps in the window, so the numbers are exact for what the agent log holds.'),
    para('Gemini in / out / total are the transcription tokens of that video\\'s clips, added up per video. Gemini calls are also logged one by one (including failed ones and retries) in the Problems tab, but those calls are not tied to a single video.'),
    h('h2', null, 'How complete is the data for this period?'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Check'), h(Th, {right: true}, 'Videos'), h(Th, {right: true}, 'Share'), h(Th, null, 'What it means'))),
      h('tbody', null,
        row('Videos in this period', q.total, 'Every row on the Videos tab.'),
        row('Marked "exact" by the agent log', q.exact, 'Newer records say how precise the token count is. Older rows do not carry this.'),
        row('Without step and window details', q.noDetail, 'Older records: no cache write, steps or window minutes, so the cache write column can show 0.'),
        row('Mixed with work on Zeno code', q.mixed, 'The same chat also edited Zeno itself, so these tokens are more than the cost of one edit.'),
        row('No agent tokens recorded', q.noAgent, 'The agent log could not be read for that job (token count shows 0).'),
        row('No Gemini tokens recorded', q.noGemini, 'No transcription tokens were reported for the video.')))),
    h('p', {className: 'note', style: {marginTop: 10}}, 'A video is a job that was reported to the server. An editor who edits the same project again creates a second row.'));
}

function dayOptions(jobs) { return [...new Set(jobs.map(j => dayKey(j.created_at)))].sort().reverse(); }

function Timeline({jobs, day, setDay, days}) {
  const list = jobs.filter(j => dayKey(j.created_at) === day);
  const hr = 3600;
  const isLong = j => n(j.updated_at) - n(j.created_at) > 3 * hr;   // a record that stayed open for hours: its real working time is unknown
  const core = list.filter(j => !isLong(j)), basis = core.length ? core : list;
  const starts = basis.map(j => n(j.created_at)), ends = basis.map(j => Math.max(n(j.updated_at), n(j.created_at)));
  const x0 = Math.floor(Math.min(...starts) / hr) * hr, x1 = Math.max(x0 + 4 * hr, Math.ceil(Math.max(...ends) / hr) * hr);
  const pos = t => Math.max(0, Math.min(100, 100 * (t - x0) / (x1 - x0)));
  const hours = []; for (let t = x0; t <= x1; t += hr) hours.push(t);
  const step = hours.length > 14 ? 2 : 1, hw = (100 / (hours.length - 1)).toFixed(4) + '%';
  const by = new Map();
  list.forEach(j => { const k = j.editor_name || '?'; if (!by.has(k)) by.set(k, []); by.get(k).push(j); });
  const rows = [...by.entries()].sort((a, b) => Math.min(...a[1].map(j => n(j.created_at))) - Math.min(...b[1].map(j => n(j.created_at))));
  const hhmm = t => { const d = new Date(t * 1000); return clock12(d); };
  const kindText = {render: 'finished', feedback: 'feedback job', render_failed: 'render failed', failed: 'render failed', blocked: 'blocked at the check', inspect: 'started, not finished', inspected: 'started, not finished', checking: 'started, not finished'};
  return h('div', null,
    h('div', {className: 'bar', style: {margin: '0 0 8px'}}, h('span', {className: 'note', style: {margin: 0}}, 'Day'),
      h('select', {className: 'dt', value: day, onChange: e => setDay(e.target.value)}, days.map(d => h('option', {key: d, value: d}, dayLabel(d))))),
    list.length ? h('div', {className: 'gantt'},
      rows.map(([name, js]) => h('div', {className: 'grow2', key: name}, h('div', {className: 'glabel'}, name),
        h('div', {className: 'gtrack', style: {'--hw': hw}}, js.map(j => h('div', {key: j.id, className: 'gb ' + jobOutcome(j) + (isLong(j) ? ' long' : ''),
          style: {left: pos(n(j.created_at)) + '%', width: Math.max(0.3, pos(Math.max(n(j.updated_at), n(j.created_at))) - pos(n(j.created_at))) + '%'},
          title: (notionName(j.video_name) || j.project || 'No video yet') + '\\n' + (kindText[j.kind] || j.kind) + (j.idx > 0 ? ' (job ' + (j.idx + 1) + ', rework)' : '') + '\\n' + hhmm(n(j.created_at)) + ' to ' + hhmm(n(j.updated_at)) + (isLong(j) ? '\\nThis record stayed open for ' + Math.round((n(j.updated_at) - n(j.created_at)) / hr) + ' h, so the real working time is unknown' : '')}))))),
      h('div', {className: 'gaxis'}, h('div', null), h('div', {className: 'gticks'}, hours.map((t, i) => i % step === 0 ? h('span', {key: t, style: {left: pos(t) + '%'}}, hhmm(t).replace(':00 ', ' ')) : null))),
      h('div', {className: 'legend'},
        h('span', null, h('i', {style: {background: '#3d9a6b'}}), 'Finished in the first job'), h('span', null, h('i', {style: {background: '#d9a441'}}), 'Rework: a second or later job on the same video'),
        h('span', null, h('i', {style: {background: '#d4574b'}}), 'Failed render or blocked at the check'), h('span', null, h('i', {style: {background: '#6b7480'}}), 'Started, never finished'),
        h('span', null, h('i', {style: {background: '#6b7480', opacity: .5, height: 4}}), 'Thin faded line: the job record stayed open for hours, real working time unknown'), h('span', null, 'Each bar runs from job start to job end. Hover for the Notion name.')))
      : h('p', {className: 'note'}, 'No jobs on this day.'));
}

function EditsView({data, jobs, jobsMsg, rg, q, rate, day, setDay}) {
  if (!jobs) return h('p', {className: 'note', style: {marginTop: 14}}, jobsMsg || 'Loading the job log…');
  const built = buildEdits(jobs, data.runs);
  const inRg = j => inPeriod({created_at: j.created_at}, rg);
  const qq = String(q || '').toLowerCase().split(/\\s+/).filter(Boolean);
  const hay = e => (String(e.editor) + ' ' + e.name + ' ' + e.project).toLowerCase();
  const edits = built.edits.filter(e => inPeriod({created_at: e.start}, rg) && qq.every(w => hay(e).includes(w)));
  const jobsIn = built.jobs.filter(inRg).filter(j => !qq.length || qq.every(w => (String(j.editor_name) + ' ' + notionName(j.video_name) + ' ' + String(j.project)).toLowerCase().includes(w)));
  const days = dayOptions(jobsIn);
  const curDay = days.includes(day) ? day : (days[0] || '');
  const names = [...new Set(edits.map(e => e.editor))];
  const cols = dayOptions(edits.map(e => ({created_at: e.start}))).slice(0, 7).reverse();
  const cell = (ed, d) => edits.filter(e => e.editor === ed && dayKey(e.start) === d);
  const score = names.map(ed => {
    const es = edits.filter(e => e.editor === ed);
    const t = aggregate([].concat(...es.map(e => clusterRuns((data.runs || []).filter(r => String(r.editor_name || '').toLowerCase() === String(ed).toLowerCase() && r.project === e.project))))).total;
    return {ed, n: es.length, one: es.filter(e => e.result === 'one').length, re: es.filter(e => e.result === 'rework').length, open: es.filter(e => e.result === 'open').length,
      failed: es.reduce((x, e) => x + e.failed, 0), blocked: jobsIn.filter(j => !j.project && j.editor_name === ed && j.kind === 'blocked').length, tok: t};
  }).sort((a, b) => b.n - a.n);
  const tot = score.reduce((x, r) => ({n: x.n + r.n, one: x.one + r.one, re: x.re + r.re, open: x.open + r.open, failed: x.failed + r.failed, blocked: x.blocked + r.blocked}), {n: 0, one: 0, re: 0, open: 0, failed: 0, blocked: 0});
  const rate1 = (a, b) => b ? Math.round(100 * a / b) + '%' : '–';
  const resultPill = e => e.result === 'one' ? h('span', {className: 'pill ok2'}, 'Finished in 1 job') : e.result === 'rework' ? h('span', {className: 'pill amber'}, 'Finished after ' + e.nJobs + ' jobs') : (n(data.now) - e.last < 1800 ? h('span', {className: 'pill gray'}, 'In progress') : h('span', {className: 'pill red'}, 'Not finished'));
  const span = e => { const end = e.finished || e.last; const m = Math.max(0, Math.round((end - e.start) / 60)); return m >= 60 ? Math.floor(m / 60) + ' h ' + (m % 60) + ' min' : m + ' min'; };
  return h('div', null,
    h('p', {className: 'sub', style: {marginTop: 14}}, edits.length ? fmtNum(edits.length) + ' video edits by ' + names.length + ' editors: ' + tot.one + ' finished in one job, ' + tot.re + ' needed more than one job, ' + tot.open + ' not finished' : 'No video edits in this range.'),
    h('h2', null, 'Timeline'),
    h('p', {className: 'note'}, 'A job starts when the editor gives the script, footage and Notion name, and ends when the video is in the output folder. A second job on the same video counts as rework.'),
    curDay ? h(Timeline, {jobs: jobsIn, day: curDay, setDay, days}) : h('p', {className: 'note'}, 'No jobs in this range.'),
    h('h2', null, 'Video edits per editor, per day'),
    h('p', {className: 'note'}, 'Number of different videos started that day. The amber number is how many of them needed more than one job.'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Editor'), cols.map(c => h(Th, {right: true, key: c}, dayLabel(c, true))), h(Th, {right: true}, 'Total'))),
      h('tbody', null, score.map(r => h('tr', {key: r.ed}, h('td', null, h('b', null, r.ed)),
        cols.map(c => { const es = cell(r.ed, c), rw = es.filter(e => e.result !== 'one').length; return h('td', {className: 'n' + (es.length ? '' : ' muted'), key: c}, es.length || '–', rw ? h('span', {className: 'rw'}, rw) : null); }),
        h('td', {className: 'n'}, h('b', null, r.n)))),
        names.length ? h('tr', {className: 'total'}, h('td', null, 'All editors'), cols.map(c => h('td', {className: 'n', key: c}, edits.filter(e => dayKey(e.start) === c).length || '–')), h('td', {className: 'n'}, tot.n)) : null))),
    h('h2', null, 'Scorecard'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Editor'), h(Th, {right: true}, 'Video edits'), h(Th, {right: true}, 'In 1 job'), h(Th, {right: true}, 'Needed rework'), h(Th, {right: true}, 'Not finished'),
        h(Th, {right: true}, 'One-job rate'), h(Th, {right: true}, 'Failed renders'), h(Th, {right: true}, 'Blocked checks'), h(Th, {right: true}, 'New tokens per edit'), h(Th, {right: true}, 'Re-read per edit'))),
      h('tbody', null, score.map(r => h('tr', {key: r.ed}, h('td', null, h('b', null, r.ed)), h('td', {className: 'n'}, r.n), h('td', {className: 'n'}, r.one),
        h('td', {className: 'n'}, r.re ? h('span', {className: 'pill amber'}, r.re) : 0), h('td', {className: 'n'}, r.open ? h('span', {className: 'pill red'}, r.open) : 0),
        h('td', {className: 'n'}, rate1(r.one, r.n)), h('td', {className: 'n'}, r.failed || 0), h('td', {className: 'n'}, r.blocked || 0),
        h('td', {className: 'n'}, fmtNum(Math.round(sumNew(r.tok) / Math.max(1, r.n)))), h('td', {className: 'n'}, fmtNum(Math.round(r.tok.cached / Math.max(1, r.n)))))),
        names.length ? h('tr', {className: 'total'}, h('td', null, 'All editors'), h('td', {className: 'n'}, tot.n), h('td', {className: 'n'}, tot.one), h('td', {className: 'n'}, tot.re), h('td', {className: 'n'}, tot.open),
          h('td', {className: 'n'}, rate1(tot.one, tot.n)), h('td', {className: 'n'}, tot.failed), h('td', {className: 'n'}, tot.blocked), h('td', null), h('td', null)) : null))),
    h('h2', null, 'Every video edit'),
    h('p', {className: 'note'}, 'One row per video, newest activity first (a video still being edited shows as In progress and has no tokens until it renders). Tokens count each video once: renders that share the same job start are not added together. Agent calls = model requests the editor chat made (each one re-sends the chat, so each one is a cache re-read); – means that Zeno version did not report it.'),
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Started'), h(Th, null, 'Finished'), h(Th, null, 'Time taken'), h(Th, null, 'Editor'), h(Th, null, 'Notion name'), h(Th, {right: true}, 'Jobs'), h(Th, null, 'Result'),
        h(Th, {right: true}, 'Agent calls'), h(Th, {right: true}, 'Input'), h(Th, {right: true}, 'Output'), h(Th, {right: true}, 'Re-read'), h(Th, {right: true}, 'Re-read per call'), h(Th, {right: true}, 'Gemini in'), h(Th, {right: true}, 'Gemini out'), h(Th, {right: true}, 'Est. cost'), h(Th, null, 'Data'), h(Th, null, 'Warnings'))),
      h('tbody', null, edits.length ? edits.map(e => h('tr', {key: e.key},
        h('td', {className: 'muted'}, fmtDate(e.start)), h('td', {className: 'muted'}, e.finished ? fmtDate(e.finished) : '–'), h('td', {className: 'muted'}, span(e)),
        h('td', null, h('b', null, e.editor)), h('td', {title: e.name + '  (' + e.project + ')', style: {maxWidth: 440, overflow: 'hidden', textOverflow: 'ellipsis'}}, e.name), h('td', {className: 'n'}, e.nJobs), h('td', null, resultPill(e)),
        h('td', {className: 'n'}, e.calls ? fmtNum(e.calls) : '–'), h('td', {className: 'n'}, e.kept ? fmtNum(e.tok.ain) : '–'), h('td', {className: 'n'}, e.kept ? fmtNum(e.tok.aout) : '–'), h('td', {className: 'n'}, e.kept ? fmtNum(e.tok.cached) : '–'), h('td', {className: 'n'}, e.calls && e.tok.cached ? fmtNum(Math.round(e.tok.cached / e.calls)) : '–'),
        h('td', {className: 'n'}, e.kept ? fmtNum(e.tok.gin) : '–'), h('td', {className: 'n'}, e.kept ? fmtNum(e.tok.gout) : '–'), h('td', {className: 'n'}, e.kept ? money(e.tok, rate) : '–'),
        h('td', null, e.q ? qPill(e.q) : '–'),
        h('td', null, e.warn.map(w => h('span', {className: 'pill amber', key: w, style: {marginRight: 4}}, w)))))
        : h('tr', null, h('td', {colSpan: 17}, 'No video edits in this range'))))));
}

const TABS = [['edits', 'Video edits'], ['daily', 'Daily'], ['videos', 'Renders'], ['editors', 'Editors'], ['problems', 'Problems'], ['health', 'Health'], ['notes', 'How it is recorded']];
function Dashboard({data, period, setPeriod, q, setQ, rate, setRate, showAll, setShowAll, tab, setTab, health, jobs, jobsMsg, cf, setCf, ct, setCt, day, setDay}) {
  const cutoff = periodCutoff(period, data.now, cf, ct);
  const runs = data.runs.filter(r => inPeriod(r, cutoff) && matches(r, q)).sort((a, b) => n(b.created_at) - n(a.created_at));
  const vers = versionRows(data.keys);
  const cur = TABS.some(t => t[0] === tab) ? tab : 'edits';
  return h('div', null,
    h('div', {className: 'tabs'}, TABS.map(([id, label]) => h('button', {key: id, className: 'tab' + (cur === id ? ' on' : ''), onClick: () => setTab(id)}, label))),
    h('div', {className: 'bar'},
      [['today', 'Today'], ['7', '7 days'], ['30', '30 days'], ['all', 'All time'], ['custom', 'Custom']].map(([d, label]) =>
        h('button', {key: d, className: 'chip' + (period === d ? ' on' : ''), onClick: () => setPeriod(d)}, label)),
      period === 'custom' ? [h('label', {key: 'f'}, 'From', h('input', {type: 'datetime-local', className: 'dt', value: cf, onChange: e => setCf(e.target.value)})), h('label', {key: 't'}, 'To', h('input', {type: 'datetime-local', className: 'dt', value: ct, onChange: e => setCt(e.target.value)}))] : null,
      h('input', {className: 'grow', placeholder: 'Search an editor, project or language', value: q, onChange: e => setQ(e.target.value)}),
      h('label', null, '₹ per $', h('input', {type: 'number', step: '0.1', value: rate, onChange: e => setRate(Number(e.target.value) || 0)})),
      h('button', {className: 'chip', onClick: () => {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([csvOf(runs)], {type: 'text/csv'}));
        a.download = 'zeno-usage.csv'; a.click();
      }}, 'Download CSV')),
    cur === 'edits' ? h(EditsView, {data, jobs, jobsMsg, rg: cutoff, q, rate, day, setDay})
      : cur === 'daily' ? h(Daily, {runs, rate})
      : cur === 'videos' ? h(Videos, {runs, rate, showAll, setShowAll, jobs, data})
      : cur === 'editors' ? h(Editors, {runs, rate, vers})
      : cur === 'problems' ? h(Problems, {data, runs, cutoff})
      : cur === 'health' ? h(Health, {health, vers})
      : cur === 'notes' ? h(Notes, {runs, data})
      : h(EditsView, {data, jobs, jobsMsg, rg: cutoff, q, rate, day, setDay}),
    h('p', {className: 'foot'},
      'Input includes cache write. Re-read = cache read: the chat sent again on every step (cheaper, but it counts toward the plan limit). ' +
      'Cost is an estimate from list prices (USD per million tokens: ' + PRICES.map(p => p[0] + ' ' + p[1] + '/' + p[2] + '/' + p[3] + '/' + p[4]).join(', ') + ' for in / cached read / cache write / out). ' +
      'Numbers are what each editor\\'s Zeno reported after a job; tokens per job are an approximation of what the provider billed. Times are in your local time.'));
}

function SignIn({message, onToken}) {
  const box = useRef(null);
  const [note, setNote] = useState(message || 'Use your company Google account.');
  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const cfg = await (await fetch('/api/auth/config')).json();
        if (!cfg.google_client_id) { setNote('Google sign-in is not set up on this server.'); return; }
        if (!(window.google && google.accounts && google.accounts.id)) {
          await new Promise((res, rej) => { const s = document.createElement('script'); s.src = 'https://accounts.google.com/gsi/client'; s.onload = res; s.onerror = rej; document.head.appendChild(s); });
        }
        if (dead) return;
        google.accounts.id.initialize({client_id: cfg.google_client_id, callback: resp => onToken(resp.credential)});
        google.accounts.id.renderButton(box.current, {theme: 'filled_blue', size: 'large', text: 'signin_with'});
      } catch (e) { setNote('Could not start Google sign-in: ' + e.message); }
    })();
    return () => { dead = true; };
  }, []);
  return h('div', {className: 'signin'}, h('h1', null, 'Sign in to see the usage log'), h('p', {className: 'sub'}, note), h('div', {ref: box, style: {display: 'inline-block'}}));
}

function App() {
  const read = k => { try { return localStorage.getItem(k) || ''; } catch (e) { return ''; } };
  const [token, setToken] = useState(read(TOKEN_KEY));
  const [data, setData] = useState(null);
  const [health, setHealth] = useState(null);
  const [msg, setMsg] = useState('');
  const [period, setPeriod] = useState('7');
  const [jobs, setJobs] = useState(null);
  const [jobsMsg, setJobsMsg] = useState('');
  const [cf, setCf] = useState('');
  const [ct, setCt] = useState('');
  const [day, setDay] = useState('');
  const [tab, setTabState] = useState(() => { const hsh = (location.hash || '').replace('#', ''); if (hsh) return hsh; try { return localStorage.getItem('zeno_tab') || 'edits'; } catch (e) { return 'edits'; } });
  const setTab = t => { setTabState(t); try { localStorage.setItem('zeno_tab', t); } catch (e) {} };
  const [q, setQ] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [rate, setRate] = useState(() => { try { return Number(localStorage.getItem('zeno_rate')) || 94.8; } catch (e) { return 94.8; } });
  useEffect(() => { try { localStorage.setItem('zeno_rate', String(rate)); } catch (e) {} }, [rate]);
  useEffect(() => { fetch('/health').then(r => r.json()).then(setHealth).catch(() => setHealth({ok: false})); }, []);
  useEffect(() => {
    if (!token) { setData(null); return; }
    const hdr = {headers: {Authorization: 'Bearer ' + token}};
    const jobsReq = fetch('/api/jobs', hdr);   // asked at the same time as the usage numbers
    fetch('/api/usage', hdr).then(async r => {
      if (r.status === 401 || r.status === 403) {
        try { localStorage.removeItem(TOKEN_KEY); } catch (e) {}
        setMsg(r.status === 403 ? 'That account is not allowed. Use your company Google account.' : 'Please sign in again.');
        setToken(''); return;
      }
      if (!r.ok) throw new Error('Could not load (' + r.status + ').');
      setData(await r.json()); setMsg('');
      jobsReq.then(async j => {
        if (j.ok) setJobs((await j.json()).jobs || []); else setJobsMsg('The job log is not available on this server yet (status ' + j.status + ').');
      }).catch(e => setJobsMsg('Could not load the job log: ' + e.message));
    }).catch(e => setMsg(e.message));
  }, [token]);
  const onToken = async t => {  // trade the one-hour Google sign-in for a 30-day session
    let keep = t;
    try { const r = await fetch('/api/usage/session', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({credential: t})}); if (r.ok) keep = (await r.json()).token || t; } catch (e) {}
    try { localStorage.setItem(TOKEN_KEY, keep); } catch (e) {}
    setToken(keep);
  };
  return h('main', null,
    h('div', {className: 'top'},
      h('div', {className: 'brand'}, 'Zeno Video AI'),
      h('span', {className: 'sp'}),
      data ? h('span', {className: 'muted', style: {fontSize: 13}}, 'Updated ' + fmtDate(data.now)) : null,
      h('a', {className: 'chip', href: '/'}, 'Zeno home')),
    h('h1', null, 'Usage'),
    h('p', {className: 'sub'}, 'Video edits per editor, how many jobs each took, and the tokens they used.'),
    data ? h(Dashboard, {data, period, setPeriod, q, setQ, rate, setRate, showAll, setShowAll, tab, setTab, health, jobs, jobsMsg, cf, setCf, ct, setCt, day, setDay})
         : token ? h('div', {className: 'card'}, h('h1', null, 'Loading…'), h('p', {className: 'sub'}, msg))
                 : h(SignIn, {message: msg, onToken}));
}
if (typeof document !== 'undefined' && document.getElementById('root')) ReactDOM.createRoot(document.getElementById('root')).render(h(App));
</script></body></html>
""".replace("__STYLE__", NOTION_STYLE)
