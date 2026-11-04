"""The admin Usage page: one simple screen of who used what.

Every editor and every run: coding agent, model, tokens in / cached / out, Gemini transcription tokens, video length and an estimated cost.
It reads `/api/usage` (no plan files, only the numbers) and does the grouping in the browser, so it needs no extra server code.
The page holds no data; it asks for a company Google sign-in (any account of the allowed domain, no admin list) and then reads the read-only `/api/usage`.

The cost is an estimate from list prices (USD per million tokens) kept in PRICES below; models not listed, such as Gemini or Antigravity
models, are shown without a cost instead of with a guess.
"""
from ui import STYLE

EXTRA_STYLE = """
main.wide{max-width:1380px}
.bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:0 0 18px}
.chip{border:1px solid rgba(148,163,184,.3);background:rgba(148,163,184,.08);color:#cfe0ff;border-radius:99px;padding:6px 14px;font:inherit;font-size:13px;cursor:pointer}
.chip.on{background:#3a63d8;border-color:#3a63d8;color:#fff}
.bar input{background:rgba(8,12,26,.6);border:1px solid rgba(148,163,184,.3);color:#e8eef7;border-radius:10px;padding:7px 12px;font:inherit;font-size:13px}
.bar input[type=number]{width:84px}
.grow{flex:1}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.stat{background:rgba(8,12,26,.5);border:1px solid rgba(148,163,184,.16);border-radius:12px;padding:14px 16px}
.stat b{display:block;font-size:22px;letter-spacing:-.3px}
.stat span{color:#9fb0cc;font-size:12px}
.scroll{overflow-x:auto}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.muted{color:#9fb0cc}
tr.total td{font-weight:700;border-top:2px solid rgba(148,163,184,.3)}
.foot{color:#9fb0cc;font-size:12.5px;margin:14px 4px}
.topbtn{position:fixed;top:16px;right:20px;z-index:5;border:1px solid rgba(148,163,184,.35);background:rgba(20,28,52,.85);color:#cfe0ff;border-radius:10px;padding:7px 16px;font:inherit;font-size:13px;font-weight:600;text-decoration:none;cursor:pointer}
.signin{text-align:center;padding:36px 10px}
.tag{display:inline-block;background:rgba(148,163,184,.14);border-radius:6px;padding:1px 7px;margin:1px 3px 1px 0;font-size:12px}
"""

USAGE_PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zeno · Usage</title><style>__STYLE__ __EXTRA__</style></head><body><main class="wide">
  <a class="topbtn" id="signout" href="/" title="Back to the Zeno sign-in page">Zeno home</a>
  <div class="brand"><span class="dot"></span> Zeno Video AI · Admin · Usage log</div>
  <div class="card signin" id="signin" style="display:none"><h1>Sign in to see the usage log</h1><p class="sub" id="signmsg">Use your company Google account.</p><div id="gbtn" style="display:inline-block"></div></div>
  <div id="content" style="display:none">
  <div class="card">
    <h1>Who is using what</h1>
    <p class="sub">Every editor and every video: the coding agent and model, tokens in and out, Gemini transcription, and an estimated cost.</p>
    <div class="bar">
      <button class="chip" data-days="1">Today</button><button class="chip" data-days="7">7 days</button>
      <button class="chip on" data-days="30">30 days</button><button class="chip" data-days="0">All time</button>
      <input id="q" class="grow" placeholder="Search an editor, email, project or model" autocomplete="off">
      <label style="color:#9fb0cc;font-size:13px">₹ per $ <input id="rate" type="number" step="0.1" value="94.8"></label>
      <button class="chip" id="csv">Download CSV</button>
    </div>
    <div id="cards" class="stats"></div>
  </div>
  <div class="card"><h1>By editor</h1>
    <div class="scroll"><table><thead><tr>
      <th>Editor</th><th>Coding agent · model</th><th class="n">Videos</th><th class="n">Minutes</th>
      <th class="n">Agent in</th><th class="n">Cached</th><th class="n">Agent out</th><th class="n">Agent total</th>
      <th class="n">Gemini in</th><th class="n">Gemini out</th><th class="n">Gemini total</th><th class="n">Est. cost</th><th>Last run</th>
    </tr></thead><tbody id="users"><tr><td colspan="13">Loading…</td></tr></tbody></table></div>
  </div>
  <div class="card"><h1>Every run <button class="chip" id="all" style="margin-left:12px">Show all</button></h1>
    <div class="scroll"><table><thead><tr>
      <th>When</th><th>Editor</th><th>Project</th><th>Coding agent · model</th><th class="n">Clips</th><th class="n">Seconds</th>
      <th class="n">Agent in</th><th class="n">Cached</th><th class="n">Agent out</th><th class="n">Agent total</th><th class="n">Gemini</th><th class="n">Est. cost</th>
    </tr></thead><tbody id="runs"><tr><td colspan="12">Loading…</td></tr></tbody></table></div>
  </div>
  <p class="foot" id="foot"></p>
  </div>
<script>
const PRICES = [  // [text found in the model name, USD per million tokens: input, cached input, output] from Anthropic list prices
  ['opus-5-5', 4, 0.2, 20], ['opus-5', 5, 0.5, 25], ['opus-4', 5, 0.5, 25],
  ['sonnet-5', 2, 0.2, 10], ['sonnet-4', 3, 0.3, 15],
  ['haiku-5', 0.1, 0.01, 0.5], ['haiku-4', 1, 0.1, 5], ['fable', 10, 0.25, 50]];
function priceFor(model) {
  const m = String(model || '').toLowerCase().replace(/[^a-z0-9]+/g, '-');
  for (const p of PRICES) if (m.includes(p[0])) return p;
  return null;
}
function costUsd(r) {
  const p = priceFor(r.agent_model);
  if (!p) return null;
  return (n(r.agent_in_tokens) * p[1] + n(r.agent_cached_tokens) * p[2] + n(r.agent_out_tokens) * p[3]) / 1e6;
}
function n(v) { return Number(v || 0); }
function who(r) { return (r.user_email || r.editor_name || 'unknown').toLowerCase(); }
function inPeriod(r, days, now) { return !days || (r.created_at >= now - days * 86400); }
function matches(r, q) {
  if (!q) return true;
  const hay = [r.editor_name, r.user_email, r.project, r.agent_name, r.agent_model, r.language].join(' ').toLowerCase();
  return q.toLowerCase().split(/\\s+/).every(w => hay.includes(w));
}
function aggregate(runs) {
  const users = new Map();
  const total = {runs: 0, seconds: 0, ain: 0, cached: 0, aout: 0, atotal: 0, gin: 0, gout: 0, gtotal: 0, usd: 0, unpriced: 0, editors: 0};
  for (const r of runs) {
    const k = who(r);
    if (!users.has(k)) users.set(k, {key: k, name: r.editor_name || k, email: r.user_email || '', agents: new Set(), models: new Set(),
      runs: 0, seconds: 0, ain: 0, cached: 0, aout: 0, atotal: 0, gin: 0, gout: 0, gtotal: 0, usd: 0, unpriced: 0, last: 0});
    const u = users.get(k);
    const c = costUsd(r);
    for (const t of [u, total]) {
      t.runs += 1; t.seconds += n(r.video_seconds); t.ain += n(r.agent_in_tokens); t.cached += n(r.agent_cached_tokens);
      t.aout += n(r.agent_out_tokens); t.atotal += n(r.agent_total_tokens);
      t.gin += n(r.transcribe_in_tokens); t.gout += n(r.transcribe_out_tokens); t.gtotal += n(r.transcribe_total_tokens);
      if (c === null) t.unpriced += 1; else t.usd += c;
    }
    u.agents.add(r.agent_name || 'unknown'); u.models.add(r.agent_model || 'unknown');
    u.last = Math.max(u.last, n(r.created_at));
  }
  total.editors = users.size;
  return {users: [...users.values()].sort((a, b) => b.atotal - a.atotal), total};
}
/*__DOM__*/
const fmtNum = x => Number(x || 0).toLocaleString();
const fmtDate = t => t ? new Date(t * 1000).toISOString().slice(0, 16).replace('T', ' ') : '';
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
let DATA = {runs: [], keys: [], now: Date.now() / 1000}, DAYS = 30, SHOW_ALL = false, CFG = null;
const TOKEN_KEY = 'zeno_usage_jwt';
const rate = () => Number(document.getElementById('rate').value) || 0;
const inr = usd => '₹' + Math.round(usd * rate()).toLocaleString();
function view() {
  const q = document.getElementById('q').value.trim();
  return DATA.runs.filter(r => inPeriod(r, DAYS, DATA.now) && matches(r, q));
}
function render() {
  const runs = view(), a = aggregate(runs), t = a.total;
  const stat = (v, label) => `<div class="stat"><b>${v}</b><span>${label}</span></div>`;
  document.getElementById('cards').innerHTML =
    stat(fmtNum(t.editors), 'editors active') + stat(fmtNum(t.runs), 'videos edited') + stat((t.seconds / 60).toFixed(1), 'video minutes') +
    stat(fmtNum(t.ain) + ' / ' + fmtNum(t.cached) + ' / ' + fmtNum(t.aout), 'agent tokens: in / cached / out') +
    stat(fmtNum(t.gin) + ' / ' + fmtNum(t.gout), 'Gemini tokens: in / out') +
    stat(inr(t.usd) + (t.unpriced ? ' *' : ''), 'estimated agent cost');
  const tag = s => [...s].map(x => `<span class="tag">${esc(x)}</span>`).join('');
  document.getElementById('users').innerHTML = a.users.map(u => `<tr>
    <td><b>${esc(u.name)}</b><br><span class="muted" style="font-size:12px">${esc(u.email)}</span></td>
    <td>${tag(u.agents)}<br>${tag(u.models)}</td>
    <td class="n">${fmtNum(u.runs)}</td><td class="n">${(u.seconds / 60).toFixed(1)}</td>
    <td class="n">${fmtNum(u.ain)}</td><td class="n muted">${fmtNum(u.cached)}</td><td class="n">${fmtNum(u.aout)}</td><td class="n"><b>${fmtNum(u.atotal)}</b></td>
    <td class="n">${fmtNum(u.gin)}</td><td class="n">${fmtNum(u.gout)}</td><td class="n"><b>${fmtNum(u.gtotal)}</b></td>
    <td class="n">${u.usd || !u.unpriced ? inr(u.usd) : '–'}${u.unpriced && u.usd ? ' *' : ''}</td><td class="muted">${fmtDate(u.last)}</td></tr>`).join('') +
    (a.users.length ? `<tr class="total"><td>All editors</td><td></td><td class="n">${fmtNum(t.runs)}</td><td class="n">${(t.seconds / 60).toFixed(1)}</td>
      <td class="n">${fmtNum(t.ain)}</td><td class="n">${fmtNum(t.cached)}</td><td class="n">${fmtNum(t.aout)}</td><td class="n">${fmtNum(t.atotal)}</td>
      <td class="n">${fmtNum(t.gin)}</td><td class="n">${fmtNum(t.gout)}</td><td class="n">${fmtNum(t.gtotal)}</td><td class="n">${inr(t.usd)}${t.unpriced ? ' *' : ''}</td><td></td></tr>`
      : '<tr><td colspan="13">No runs in this period</td></tr>');
  document.getElementById('all').style.display = runs.length > 100 ? '' : 'none';
  document.getElementById('all').textContent = SHOW_ALL ? 'Show latest 100' : 'Show all ' + runs.length;
  document.getElementById('runs').innerHTML = (SHOW_ALL ? runs : runs.slice(0, 100)).map(r => { const c = costUsd(r); return `<tr>
    <td class="muted">${fmtDate(r.created_at)}</td><td>${esc(r.editor_name || r.user_email)}</td><td>${esc(r.project)}</td>
    <td>${esc(r.agent_name)} · ${esc(r.agent_model)}</td><td class="n">${fmtNum(r.clips_count)}</td><td class="n">${Math.round(n(r.video_seconds))}</td>
    <td class="n">${fmtNum(r.agent_in_tokens)}</td><td class="n muted">${fmtNum(r.agent_cached_tokens)}</td><td class="n">${fmtNum(r.agent_out_tokens)}</td>
    <td class="n"><b>${fmtNum(r.agent_total_tokens)}</b></td><td class="n">${fmtNum(r.transcribe_total_tokens)}</td><td class="n">${c === null ? '–' : inr(c)}</td></tr>`; }).join('') ||
    '<tr><td colspan="12">No runs in this period</td></tr>';
  document.getElementById('foot').innerHTML =
    'Numbers are what each editor\\'s Zeno reported after a job (agent tokens from the coding agent\\'s log for that edit, Gemini tokens from transcription). ' +
    'Cost is an estimate from list prices per million tokens (' + PRICES.map(p => p[0] + ' $' + p[1] + '/$' + p[2] + '/$' + p[3]).join(', ') + ' for in / cached / out). ' +
    (t.unpriced ? '* ' + t.unpriced + ' run(s) use a model with no price here (for example Gemini or Antigravity models) and are left out of the cost. ' : '') +
    'Tokens counted per job are an approximation of what the provider billed.';
}
function csv() {
  const rows = view(), head = ['when', 'editor', 'email', 'project', 'language', 'agent', 'model', 'clips', 'video_seconds', 'agent_in', 'agent_cached', 'agent_out', 'agent_total', 'gemini_in', 'gemini_out', 'gemini_total', 'est_usd'];
  const q = v => '"' + String(v ?? '').replace(/"/g, '""') + '"';
  const lines = [head.join(',')].concat(rows.map(r => [fmtDate(r.created_at), r.editor_name, r.user_email, r.project, r.language, r.agent_name, r.agent_model, r.clips_count, r.video_seconds,
    r.agent_in_tokens, r.agent_cached_tokens, r.agent_out_tokens, r.agent_total_tokens, r.transcribe_in_tokens, r.transcribe_out_tokens, r.transcribe_total_tokens,
    (costUsd(r) ?? '')].map(q).join(',')));
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([lines.join('\\n')], {type: 'text/csv'}));
  a.download = 'zeno-usage.csv'; a.click();
}
function loadGoogle() {
  return new Promise(res => {
    if (window.google && google.accounts && google.accounts.id) return res();
    const sc = document.createElement('script'); sc.src = 'https://accounts.google.com/gsi/client'; sc.onload = () => res(); document.head.appendChild(sc);
  });
}
async function showSignIn(msg) {
  document.getElementById('content').style.display = 'none';
  document.getElementById('signin').style.display = '';
  document.getElementById('signmsg').textContent = msg || 'Use your company Google account.';
  try {
    CFG = CFG || await (await fetch('/api/auth/config')).json();
    if (!CFG.google_client_id) { document.getElementById('signmsg').textContent = 'Google sign-in is not set up on this server.'; return; }
    await loadGoogle();
    google.accounts.id.initialize({client_id: CFG.google_client_id, callback: resp => { try { sessionStorage.setItem(TOKEN_KEY, resp.credential); } catch (e) {} load(); }});
    const box = document.getElementById('gbtn'); box.innerHTML = '';
    google.accounts.id.renderButton(box, {theme: 'filled_blue', size: 'large', text: 'signin_with'});
  } catch (e) { document.getElementById('signmsg').textContent = 'Could not start Google sign-in: ' + e.message; }
}
async function load() {
  let token = '';
  try { token = sessionStorage.getItem(TOKEN_KEY) || ''; } catch (e) {}
  if (!token) return showSignIn();
  try {
    const r = await fetch('/api/usage', {headers: {Authorization: 'Bearer ' + token}});
    if (r.status === 401 || r.status === 403) {
      try { sessionStorage.removeItem(TOKEN_KEY); } catch (e) {}
      return showSignIn(r.status === 403 ? 'That account is not allowed. Use your company Google account.' : 'Please sign in again.');
    }
    if (!r.ok) throw new Error('Could not load (' + r.status + ').');
    DATA = await r.json();
    document.getElementById('signin').style.display = 'none';
    document.getElementById('content').style.display = '';
    try { const saved = Number(localStorage.getItem('zeno_rate')); if (saved) document.getElementById('rate').value = saved; } catch (e) {}
    render();
  } catch (e) {
    document.getElementById('signin').style.display = '';
    document.getElementById('signmsg').textContent = e.message;
  }
}
document.querySelectorAll('.chip[data-days]').forEach(b => b.onclick = () => {
  DAYS = Number(b.dataset.days);
  document.querySelectorAll('.chip[data-days]').forEach(x => x.classList.toggle('on', x === b));
  render();
});
document.getElementById('q').oninput = render;
document.getElementById('rate').oninput = () => { try { localStorage.setItem('zeno_rate', document.getElementById('rate').value); } catch (e) {} render(); };
document.getElementById('csv').onclick = csv;
document.getElementById('all').onclick = () => { SHOW_ALL = !SHOW_ALL; render(); };
load();
</script></main></body></html>
""".replace("__STYLE__", STYLE).replace("__EXTRA__", EXTRA_STYLE)
