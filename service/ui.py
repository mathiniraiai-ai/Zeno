"""The two small web pages: key page (/) and admin (/admin). Plain HTML, no build step, no external files.
Styled to match the Supernova AI-team hub (light page, white cards, black buttons)."""

_STYLE = """
<style>
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; background: #f7f7f8; color: #0f1020; font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; display: flex; justify-content: center; padding: 56px 16px; }
  main { width: 100%; max-width: 520px; } main.wide { max-width: 980px; }
  .brand { display: flex; align-items: center; gap: 10px; margin-bottom: 28px; font-weight: 600; }
  .logo { width: 34px; height: 34px; border-radius: 9px; background: #0f1020; color: #fff; display: grid; place-items: center; font-weight: 700; }
  h1 { font-size: 22px; margin: 0 0 4px; } p.sub { color: #62647a; margin: 0 0 20px; }
  .card { background: #fff; border: 1px solid #e6e6ee; border-radius: 14px; padding: 22px; margin-bottom: 16px; box-shadow: 0 1px 2px rgba(15,16,32,.04); }
  label { display: block; font-size: 13px; color: #62647a; margin: 12px 0 4px; }
  input { width: 100%; padding: 10px 12px; border-radius: 9px; border: 1px solid #d9d9e4; background: #fff; color: inherit; font: inherit; }
  button { width: 100%; margin-top: 18px; padding: 12px 16px; border-radius: 10px; border: 0; background: linear-gradient(90deg, #0f1020, #24245c); color: #fff; font: inherit; font-weight: 600; cursor: pointer; }
  button.small { width: auto; margin: 0; padding: 4px 10px; font-size: 13px; background: #fdecee; color: #c0263d; }
  button.ghost { background: #eeeef5; color: #0f1020; margin-top: 10px; }
  button:disabled { opacity: .5; cursor: default; }
  pre { background: #f3f3f8; border: 1px solid #e6e6ee; border-radius: 9px; padding: 12px; overflow-x: auto; white-space: pre-wrap; word-break: break-all; margin: 8px 0 0; font-size: 13px; }
  .err { color: #c0263d; margin-top: 12px; min-height: 1em; } .warn { color: #9a6a00; font-size: 13px; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; } th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid #ececf3; }
  th { color: #62647a; font-weight: 500; } .muted { color: #9c9eb2; }
</style>"""

SIGNUP_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zeno</title>""" + _STYLE + """</head><body><main>
<div class="brand"><div class="logo">Z</div>Zeno <span class="muted">Video AI</span></div>
<div class="card" id="form">
  <h1>Generate your Zeno key</h1>
  <p class="sub">One personal key lets your local Zeno use Gemini. The Gemini key itself stays hidden on our server.</p>
  <label for="name">Your name</label><input id="name" autocomplete="name" maxlength="40">
  <label for="email">Company email</label><input id="email" type="email" autocomplete="email" maxlength="100" placeholder="you@gosupernova.live">
  <div id="codebox" hidden><label for="code">Team code (ask the Zeno owner)</label><input id="code" type="password" autocomplete="off"></div>
  <button id="go">Generate</button><div class="err" id="err"></div>
</div>
<div class="card" id="result" hidden>
  <strong>Your key</strong>
  <pre id="key"></pre>
  <p class="warn">Copy it now. It is shown only once and cannot be recovered. If you lose it, generate a new one.</p>
  <label>Put these two lines in your own <code>.env</code> (never commit it):</label>
  <pre id="env"></pre>
  <button class="ghost" id="copy">Copy .env lines</button>
</div>
<script>
const $ = id => document.getElementById(id);
fetch('/health').then(r => r.json()).then(h => { $('codebox').hidden = !h.signup_code_required; }).catch(() => {});
$('go').onclick = async () => {
  $('err').textContent = ''; $('go').disabled = true;
  try {
    const r = await fetch('/api/keys', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: $('name').value, email: $('email').value, code: $('code').value})});
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Something went wrong');
    $('key').textContent = d.key;
    $('env').textContent = 'SCRIPTCUT_CLOUD_URL=' + location.origin + '\\nSCRIPTCUT_CLOUD_TOKEN=' + d.key;
    $('form').hidden = true; $('result').hidden = false;
  } catch (e) { $('err').textContent = e.message; }
  $('go').disabled = false;
};
$('copy').onclick = async () => { await navigator.clipboard.writeText($('env').textContent); $('copy').textContent = 'Copied'; };
</script></main></body></html>"""

ADMIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zeno admin</title>""" + _STYLE + """</head><body><main class="wide">
<div class="brand"><div class="logo">Z</div>Zeno admin</div>
<p class="sub">Who has a key and how much they use. Revoking takes effect immediately.</p>
<div class="card"><table><thead><tr><th>Name</th><th>Email</th><th>Created</th><th>Last used</th><th>Requests</th><th>Audio (min)</th><th></th></tr></thead><tbody id="rows"></tbody></table>
<div class="err" id="err"></div></div>
<script>
const fmt = t => t ? new Date(t * 1000).toLocaleString() : '-';
function cell(tr, text, cls) { const td = document.createElement('td'); td.textContent = text; if (cls) td.className = cls; tr.appendChild(td); return td; }
async function load() {
  const r = await fetch('/admin/api/keys'); const d = await r.json();
  const rows = document.getElementById('rows'); rows.textContent = '';
  for (const k of d.keys) {
    const tr = document.createElement('tr');
    cell(tr, k.name); cell(tr, k.email); cell(tr, fmt(k.created_at)); cell(tr, fmt(k.last_used_at));
    cell(tr, k.requests); cell(tr, (k.audio_seconds / 60).toFixed(1));
    const last = cell(tr, k.revoked_at ? 'revoked' : '', 'muted');
    if (!k.revoked_at) {
      const b = document.createElement('button'); b.className = 'small'; b.textContent = 'Revoke';
      b.onclick = async () => { if (confirm('Revoke the key for ' + k.name + '?')) { await fetch('/admin/api/keys/' + k.id + '/revoke', {method: 'POST', headers: {'X-Zeno-Admin': '1'}}); load(); } };
      last.appendChild(b);
    } else tr.className = 'muted';
    rows.appendChild(tr);
  }
}
load();
</script></main></body></html>"""
