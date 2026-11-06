"""The admin Usage log: a React page of who used what.

Every editor and every run, with the editor's e-mail: coding agent, model, tokens in / cached / out, Gemini transcription tokens, video
length and an estimated cost. The page holds no data. It asks for a company Google sign-in (any account of the allowed domain, no admin
list) and then reads the read-only `/api/usage`, which returns numbers only (no plan files, no keys).

No build step: React 18.3.1 comes pinned from cdnjs and the components are written with `React.createElement` (`h`). The calculations sit
before the `/*__DOM__*/` marker so tests can run them in Node without a browser. The cost is an estimate from list prices (USD per million
tokens, PRICES below); models not listed, such as Gemini or Antigravity models, are shown without a cost instead of with a guess.
"""
from ui import STYLE

EXTRA_STYLE = """
main.wide{max-width:1500px}
.bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:0 0 18px}
.chip{width:auto;display:inline-block;margin:0;border:1px solid rgba(148,163,184,.3);background:rgba(148,163,184,.08);color:#cfe0ff;border-radius:99px;padding:6px 14px;font:inherit;font-size:13px;cursor:pointer}
.chip.on{background:#3a63d8;border-color:#3a63d8;color:#fff}
.bar input{background:rgba(8,12,26,.6);border:1px solid rgba(148,163,184,.3);color:#e8eef7;border-radius:10px;padding:7px 12px;font:inherit;font-size:13px}
.bar input[type=number]{width:84px}
.grow{flex:1;min-width:220px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
.stat{background:rgba(8,12,26,.5);border:1px solid rgba(148,163,184,.16);border-radius:12px;padding:14px 16px}
.stat b{display:block;font-size:22px;letter-spacing:-.3px}
.stat span{color:#9fb0cc;font-size:12px}
.scroll{overflow-x:auto}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.muted,.muted{color:#9fb0cc}
td.muted{white-space:nowrap}
td.mail{white-space:nowrap}
tr.total td{font-weight:700;border-top:2px solid rgba(148,163,184,.3)}
.foot{color:#9fb0cc;font-size:12.5px;margin:14px 4px}
table td{vertical-align:top}
.tag{white-space:nowrap;display:inline-block;background:rgba(148,163,184,.14);border-radius:6px;padding:1px 7px;margin:1px 3px 1px 0;font-size:12px}
.topbtn{position:fixed;top:16px;right:20px;z-index:5;border:1px solid rgba(148,163,184,.35);background:rgba(20,28,52,.85);color:#cfe0ff;border-radius:10px;padding:7px 16px;font:inherit;font-size:13px;font-weight:600;text-decoration:none;cursor:pointer}
.signin{text-align:center;padding:36px 10px}
"""

USAGE_PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zeno · Admin · Usage log</title><style>__STYLE__ __EXTRA__</style></head><body>
<div id="root"><main class="wide"><div class="card"><h1>Zeno · Usage log</h1><p class="sub">Loading…</p></div></main></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/react/18.3.1/umd/react.production.min.js" crossorigin="anonymous"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.3.1/umd/react-dom.production.min.js" crossorigin="anonymous"></script>
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
function n(v) { return Number(v || 0); }
function costUsd(r) {
  const p = priceFor(r.agent_model);
  if (!p) return null;
  return (n(r.agent_in_tokens) * p[1] + n(r.agent_cached_tokens) * p[2] + n(r.agent_out_tokens) * p[3]) / 1e6;
}
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
    if (!users.has(k)) users.set(k, {key: k, name: r.editor_name || k, email: (r.user_email || '').toLowerCase(), agents: new Set(), models: new Set(),
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
const {useState, useEffect, useRef} = React;
const h = React.createElement;
const TOKEN_KEY = 'zeno_usage_jwt';
const fmtNum = x => Number(x || 0).toLocaleString();
const fmtDate = t => t ? new Date(t * 1000).toISOString().slice(0, 16).replace('T', ' ') : '';
const inr = (usd, rate) => '₹' + Math.round(usd * rate).toLocaleString();

function csvOf(runs) {
  const head = ['when', 'editor', 'email', 'project', 'language', 'agent', 'model', 'clips', 'video_seconds', 'agent_in', 'agent_cached', 'agent_out',
    'agent_total', 'gemini_in', 'gemini_out', 'gemini_total', 'est_usd'];
  const q = v => '"' + String(v ?? '').replace(/"/g, '""') + '"';
  return [head.join(',')].concat(runs.map(r => [fmtDate(r.created_at), r.editor_name, r.user_email, r.project, r.language, r.agent_name, r.agent_model,
    r.clips_count, r.video_seconds, r.agent_in_tokens, r.agent_cached_tokens, r.agent_out_tokens, r.agent_total_tokens,
    r.transcribe_in_tokens, r.transcribe_out_tokens, r.transcribe_total_tokens, (costUsd(r) ?? '')].map(q).join(','))).join('\\n');
}

function Tags({items}) { return h('span', null, [...items].map(x => h('span', {className: 'tag', key: x}, x))); }
function Th({children, right}) { return h('th', {className: right ? 'n' : ''}, children); }

function TranscriptionHealth({data, days}) {
  const ev = (data.transcriptions || []).filter(e => inPeriod(e, days, data.now));
  const bad = ev.filter(e => e.status !== 'ok');
  const failed = ev.filter(e => e.status === 'failed').length;
  const avg = a => a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0;
  const rate = ev.length ? Math.round(100 * (ev.length - bad.length) / ev.length) : 100;
  const models = {};
  ev.filter(e => e.model).forEach(e => { const m = models[e.model] || (models[e.model] = {n: 0, s: []}); m.n += 1; m.s.push(n(e.seconds)); });
  const by = {};
  ev.forEach(e => { const u = by[e.user_name] || (by[e.user_name] = {name: e.user_name, n: 0, bad: 0, last: 0}); u.n += 1; if (e.status !== 'ok') u.bad += 1; u.last = Math.max(u.last, e.created_at); });
  const users = Object.values(by).sort((a, b) => b.bad - a.bad || b.n - a.n);
  const stat = (v, label) => h('div', {className: 'stat', key: label}, h('b', null, v), h('span', null, label));
  const fell = ev.filter(e => e.status === 'ok' && e.failed_models).length;
  return h('div', {className: 'card'},
    h('h1', null, 'Transcription health'),
    h('p', {className: 'sub'}, 'Every Gemini transcription call: who, which model answered, how long it took, and why a call failed. Numbers and short reasons only, never audio or text.'),
    h('div', {className: 'stats'},
      stat(fmtNum(ev.length), 'transcription calls'), stat(rate + '%', 'answered fully'), stat(fmtNum(failed), 'failed'),
      stat(fmtNum(bad.length - failed), 'incomplete'), stat(fmtNum(fell), 'saved by a fallback model'),
      stat(avg(ev.map(e => n(e.seconds))).toFixed(1) + ' s', 'average time')),
    Object.keys(models).length ? h('p', {className: 'sub'}, 'Models that answered: ' + Object.keys(models).map(m => m + ' ' + models[m].n + ' calls, ' + avg(models[m].s).toFixed(1) + ' s').join(' · ')) : null,
    h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'Editor'), h(Th, {right: true}, 'Calls'), h(Th, {right: true}, 'Problems'), h(Th, null, 'Last call'))),
      h('tbody', null, users.length ? users.map(u => h('tr', {key: u.name},
        h('td', null, h('b', null, u.name)), h('td', {className: 'n'}, fmtNum(u.n)),
        h('td', {className: 'n'}, u.bad ? h('b', null, fmtNum(u.bad)) : '0'), h('td', {className: 'muted'}, fmtDate(u.last))))
        : h('tr', null, h('td', {colSpan: 4}, 'No transcription calls recorded in this period yet'))))),
    bad.length ? h('div', {className: 'scroll'}, h('table', null,
      h('thead', null, h('tr', null, h(Th, null, 'When'), h(Th, null, 'Editor'), h(Th, null, 'Result'), h(Th, null, 'Why'), h(Th, null, 'Models that failed'), h(Th, {right: true}, 'Audio s'), h(Th, {right: true}, 'Took s'))),
      h('tbody', null, bad.slice(0, 50).map(e => h('tr', {key: e.id},
        h('td', {className: 'muted'}, fmtDate(e.created_at)), h('td', null, e.user_name), h('td', null, h('b', null, e.status)), h('td', null, e.reason),
        h('td', {className: 'muted'}, e.failed_models), h('td', {className: 'n'}, Math.round(n(e.audio_seconds))), h('td', {className: 'n'}, n(e.seconds).toFixed(1))))))) : null);
}

function Dashboard({data, days, setDays, q, setQ, rate, setRate, showAll, setShowAll}) {
  const runs = data.runs.filter(r => inPeriod(r, days, data.now) && matches(r, q));
  const {users, total: t} = aggregate(runs);
  const stat = (v, label) => h('div', {className: 'stat', key: label}, h('b', null, v), h('span', null, label));
  const shown = showAll ? runs : runs.slice(0, 100);
  const star = t.unpriced ? ' *' : '';
  return h('div', null,
    h('div', {className: 'card'},
      h('h1', null, 'Who is using what'),
      h('p', {className: 'sub'}, 'Every editor and every video: e-mail, coding agent and model, tokens in and out, Gemini transcription, and an estimated cost.'),
      h('div', {className: 'bar'},
        [[1, 'Today'], [7, '7 days'], [30, '30 days'], [0, 'All time']].map(([d, label]) =>
          h('button', {key: d, className: 'chip' + (days === d ? ' on' : ''), onClick: () => setDays(d)}, label)),
        h('input', {className: 'grow', placeholder: 'Search an editor, e-mail, project or model', value: q, onChange: e => setQ(e.target.value)}),
        h('label', {style: {color: '#9fb0cc', fontSize: 13}}, '₹ per $ ', h('input', {type: 'number', step: '0.1', value: rate, onChange: e => setRate(Number(e.target.value) || 0)})),
        h('button', {className: 'chip', onClick: () => {
          const a = document.createElement('a');
          a.href = URL.createObjectURL(new Blob([csvOf(runs)], {type: 'text/csv'}));
          a.download = 'zeno-usage.csv'; a.click();
        }}, 'Download CSV')),
      h('div', {className: 'stats'},
        stat(fmtNum(t.editors), 'editors active'), stat(fmtNum(t.runs), 'videos edited'), stat((t.seconds / 60).toFixed(1), 'video minutes'),
        stat(fmtNum(t.ain) + ' / ' + fmtNum(t.cached) + ' / ' + fmtNum(t.aout), 'agent tokens: in / cached / out'),
        stat(fmtNum(t.gin) + ' / ' + fmtNum(t.gout), 'Gemini tokens: in / out'),
        stat(inr(t.usd, rate) + star, 'estimated agent cost'))),
    h('div', {className: 'card'}, h('h1', null, 'By editor'),
      h('div', {className: 'scroll'}, h('table', null,
        h('thead', null, h('tr', null, h(Th, null, 'Editor'), h(Th, null, 'Email'), h(Th, null, 'Coding agent'), h(Th, null, 'Model'),
          h(Th, {right: true}, 'Videos'), h(Th, {right: true}, 'Minutes'), h(Th, {right: true}, 'Agent in'), h(Th, {right: true}, 'Cached'),
          h(Th, {right: true}, 'Agent out'), h(Th, {right: true}, 'Agent total'), h(Th, {right: true}, 'Gemini in'), h(Th, {right: true}, 'Gemini out'),
          h(Th, {right: true}, 'Gemini total'), h(Th, {right: true}, 'Est. cost'), h(Th, null, 'Last run'))),
        h('tbody', null,
          users.map(u => h('tr', {key: u.key},
            h('td', null, h('b', null, u.name)), h('td', {className: 'mail'}, u.email),
            h('td', null, h(Tags, {items: u.agents})), h('td', null, h(Tags, {items: u.models})),
            h('td', {className: 'n'}, fmtNum(u.runs)), h('td', {className: 'n'}, (u.seconds / 60).toFixed(1)),
            h('td', {className: 'n'}, fmtNum(u.ain)), h('td', {className: 'n muted'}, fmtNum(u.cached)), h('td', {className: 'n'}, fmtNum(u.aout)),
            h('td', {className: 'n'}, h('b', null, fmtNum(u.atotal))),
            h('td', {className: 'n'}, fmtNum(u.gin)), h('td', {className: 'n'}, fmtNum(u.gout)), h('td', {className: 'n'}, h('b', null, fmtNum(u.gtotal))),
            h('td', {className: 'n'}, (u.usd || !u.unpriced) ? inr(u.usd, rate) + (u.unpriced ? ' *' : '') : '–'),
            h('td', {className: 'muted'}, fmtDate(u.last)))),
          users.length ? h('tr', {className: 'total'}, h('td', null, 'All editors'), h('td', null), h('td', null), h('td', null),
            h('td', {className: 'n'}, fmtNum(t.runs)), h('td', {className: 'n'}, (t.seconds / 60).toFixed(1)),
            h('td', {className: 'n'}, fmtNum(t.ain)), h('td', {className: 'n'}, fmtNum(t.cached)), h('td', {className: 'n'}, fmtNum(t.aout)), h('td', {className: 'n'}, fmtNum(t.atotal)),
            h('td', {className: 'n'}, fmtNum(t.gin)), h('td', {className: 'n'}, fmtNum(t.gout)), h('td', {className: 'n'}, fmtNum(t.gtotal)),
            h('td', {className: 'n'}, inr(t.usd, rate) + star), h('td', null))
            : h('tr', null, h('td', {colSpan: 15}, 'No runs in this period')))))),
    h(TranscriptionHealth, {data, days}),
    h('div', {className: 'card'},
      h('h1', null, 'Every run ', runs.length > 100 ? h('button', {className: 'chip', style: {marginLeft: 12}, onClick: () => setShowAll(!showAll)},
        showAll ? 'Show latest 100' : 'Show all ' + runs.length) : null),
      h('div', {className: 'scroll'}, h('table', null,
        h('thead', null, h('tr', null, h(Th, null, 'When'), h(Th, null, 'Editor'), h(Th, null, 'Email'), h(Th, null, 'Project'), h(Th, null, 'Lang'),
          h(Th, null, 'Coding agent'), h(Th, null, 'Model'), h(Th, {right: true}, 'Clips'), h(Th, {right: true}, 'Seconds'),
          h(Th, {right: true}, 'Agent in'), h(Th, {right: true}, 'Cached'), h(Th, {right: true}, 'Agent out'), h(Th, {right: true}, 'Agent total'),
          h(Th, {right: true}, 'Gemini in'), h(Th, {right: true}, 'Gemini out'), h(Th, {right: true}, 'Gemini total'), h(Th, {right: true}, 'Est. cost'))),
        h('tbody', null, shown.length ? shown.map(r => { const c = costUsd(r); return h('tr', {key: r.id},
          h('td', {className: 'muted'}, fmtDate(r.created_at)), h('td', null, r.editor_name), h('td', {className: 'mail'}, r.user_email), h('td', null, r.project),
          h('td', null, r.language), h('td', null, r.agent_name), h('td', null, r.agent_model),
          h('td', {className: 'n'}, fmtNum(r.clips_count)), h('td', {className: 'n'}, Math.round(n(r.video_seconds))),
          h('td', {className: 'n'}, fmtNum(r.agent_in_tokens)), h('td', {className: 'n muted'}, fmtNum(r.agent_cached_tokens)), h('td', {className: 'n'}, fmtNum(r.agent_out_tokens)),
          h('td', {className: 'n'}, h('b', null, fmtNum(r.agent_total_tokens))),
          h('td', {className: 'n'}, fmtNum(r.transcribe_in_tokens)), h('td', {className: 'n'}, fmtNum(r.transcribe_out_tokens)), h('td', {className: 'n'}, fmtNum(r.transcribe_total_tokens)),
          h('td', {className: 'n'}, c === null ? '–' : inr(c, rate))); })
          : h('tr', null, h('td', {colSpan: 17}, 'No runs in this period')))))),
    h('p', {className: 'foot'},
      'Numbers are what each editor\\'s Zeno reported after a job (agent tokens from the coding agent\\'s log for that edit, Gemini tokens from transcription). ' +
      'Cost is an estimate from list prices per million tokens (' + PRICES.map(p => p[0] + ' $' + p[1] + '/$' + p[2] + '/$' + p[3]).join(', ') + ' for in / cached / out). ' +
      (t.unpriced ? '* ' + t.unpriced + ' run(s) use a model with no price here (for example Gemini or Antigravity models) and are left out of the cost. ' : '') +
      'Tokens counted per job are an approximation of what the provider billed.'));
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
  return h('div', {className: 'card signin'}, h('h1', null, 'Sign in to see the usage log'), h('p', {className: 'sub'}, note), h('div', {ref: box, style: {display: 'inline-block'}}));
}

function App() {
  const read = k => { try { return sessionStorage.getItem(k) || ''; } catch (e) { return ''; } };
  const [token, setToken] = useState(read(TOKEN_KEY));
  const [data, setData] = useState(null);
  const [msg, setMsg] = useState('');
  const [days, setDays] = useState(30);
  const [q, setQ] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [rate, setRate] = useState(() => { try { return Number(localStorage.getItem('zeno_rate')) || 94.8; } catch (e) { return 94.8; } });
  useEffect(() => { try { localStorage.setItem('zeno_rate', String(rate)); } catch (e) {} }, [rate]);
  useEffect(() => {
    if (!token) { setData(null); return; }
    fetch('/api/usage', {headers: {Authorization: 'Bearer ' + token}}).then(async r => {
      if (r.status === 401 || r.status === 403) {
        try { sessionStorage.removeItem(TOKEN_KEY); } catch (e) {}
        setMsg(r.status === 403 ? 'That account is not allowed. Use your company Google account.' : 'Please sign in again.');
        setToken(''); return;
      }
      if (!r.ok) throw new Error('Could not load (' + r.status + ').');
      setData(await r.json()); setMsg('');
    }).catch(e => setMsg(e.message));
  }, [token]);
  const onToken = t => { try { sessionStorage.setItem(TOKEN_KEY, t); } catch (e) {} setToken(t); };
  return h('main', {className: 'wide'},
    h('a', {className: 'topbtn', href: '/'}, 'Zeno home'),
    h('div', {className: 'brand'}, h('span', {className: 'dot'}), ' Zeno Video AI · Admin · Usage log'),
    data ? h(Dashboard, {data, days, setDays, q, setQ, rate, setRate, showAll, setShowAll})
         : token ? h('div', {className: 'card'}, h('h1', null, 'Loading…'), h('p', {className: 'sub'}, msg))
                 : h(SignIn, {message: msg, onToken}));
}
if (typeof document !== 'undefined' && document.getElementById('root')) ReactDOM.createRoot(document.getElementById('root')).render(h(App));
</script></body></html>
""".replace("__STYLE__", STYLE).replace("__EXTRA__", EXTRA_STYLE)
