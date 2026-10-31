"""The two small web pages: key page (/) and admin (/admin). Plain HTML, no build step, no external files.
Styled to match the Supernova AI-team hub (light page, white cards, black buttons)."""

_STYLE = """
<style>
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; background: #f7f7f8; color: #0f1020; font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; display: flex; justify-content: center; padding: 56px 16px; }
  main { width: 100%; max-width: 520px; } main.wide { max-width: 1180px; }
  .brand { display: flex; align-items: center; gap: 10px; margin-bottom: 28px; font-weight: 600; }
  .logo { width: 34px; height: 34px; border-radius: 9px; background: #0f1020; color: #fff; display: grid; place-items: center; font-weight: 700; }
  h1 { font-size: 22px; margin: 0 0 4px; } h2 { font-size: 17px; margin: 0 0 12px; } p.sub { color: #62647a; margin: 0 0 20px; }
  .card { background: #fff; border: 1px solid #e6e6ee; border-radius: 14px; padding: 22px; margin-bottom: 16px; box-shadow: 0 1px 2px rgba(15,16,32,.04); overflow-x: auto; }
  label { display: block; font-size: 13px; color: #62647a; margin: 12px 0 4px; }
  input { width: 100%; padding: 10px 12px; border-radius: 9px; border: 1px solid #d9d9e4; background: #fff; color: inherit; font: inherit; }
  .field { position: relative; margin-top: 16px; } .field input { padding-right: 150px; }
  .suffix { position: absolute; right: 12px; top: 50%; transform: translateY(-50%); color: #9c9eb2; pointer-events: none; }
  input::placeholder { color: #a8aabd; }
  button { width: 100%; margin-top: 18px; padding: 12px 16px; border-radius: 10px; border: 0; background: linear-gradient(90deg, #0f1020, #24245c); color: #fff; font: inherit; font-weight: 600; cursor: pointer; }
  button.small { width: auto; margin: 0; padding: 4px 10px; font-size: 13px; background: #fdecee; color: #c0263d; }
  button.ghost { background: #eeeef5; color: #0f1020; margin-top: 10px; }
  button:disabled { opacity: .5; cursor: default; }
  pre { background: #f3f3f8; border: 1px solid #e6e6ee; border-radius: 9px; padding: 12px; overflow-x: auto; white-space: pre-wrap; word-break: break-all; margin: 8px 0 0; font-size: 13px; }
  .err { color: #c0263d; margin-top: 12px; min-height: 1em; } .warn { color: #9a6a00; font-size: 13px; }
  table { width: 100%; border-collapse: collapse; font-size: 13.5px; } th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid #ececf3; vertical-align: top; }
  th { color: #62647a; font-weight: 500; white-space: nowrap; } .muted { color: #9c9eb2; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 99px; background: #eeeef8; color: #24245c; font-size: 12px; font-weight: 600; }
</style>"""

SIGNUP_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zeno</title>""" + _STYLE + """</head><body><main>
<div class="brand"><div class="logo">Z</div>Zeno <span class="muted">Video AI</span></div>
<div class="card" id="form">
  <h1>Generate your Zeno key</h1>
  <p class="sub">One personal key lets your local Zeno use Gemini. The Gemini key itself stays hidden on our server.</p>
  <div class="field"><input id="name" autocomplete="name" maxlength="40" placeholder="Your name" aria-label="Your name"><span class="suffix" id="suffix">@gosupernova.live</span></div>
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
fetch('/health').then(r => r.json()).then(h => { $('codebox').hidden = !h.signup_code_required; if (h.email_domain) $('suffix').textContent = '@' + h.email_domain; }).catch(() => {});
$('go').onclick = async () => {
  $('err').textContent = ''; $('go').disabled = true;
  try {
    const r = await fetch('/api/keys', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: $('name').value, code: $('code').value})});
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
<p class="sub">Personal keys, transcription usage, and automated per-video Agent + Clip token telemetry.</p>
<div class="card">
  <h2>Team Keys & Transcription Totals</h2>
  <table><thead><tr><th>Name</th><th>Email</th><th>Created</th><th>Last used</th><th>Requests</th><th>Audio (min)</th><th>Gemini Tokens (In / Out / Total)</th><th></th></tr></thead><tbody id="rows"></tbody></table>
  <div class="err" id="err"></div>
</div>
<div class="card">
  <h2>Video Edit Runs (Agent + Clip-by-Clip Token Telemetry)</h2>
  <table><thead><tr><th>Time</th><th>Editor</th><th>Project</th><th>Agent & Model</th><th>Clips / Video</th><th>Transcribe Tokens (In / Out / Total)</th><th>Agent Tokens (In / Cached / Out / Total)</th><th>Grand Total</th></tr></thead><tbody id="runs"></tbody></table>
</div>
<script>
const fmt = t => t ? new Date(t * 1000).toLocaleString() : '-';
const num = n => Number(n || 0).toLocaleString();
function cell(tr, text, cls) { const td = document.createElement('td'); td.textContent = text; if (cls) td.className = cls; tr.appendChild(td); return td; }
async function load() {
  const r = await fetch('/admin/api/keys'); const d = await r.json();
  const rows = document.getElementById('rows'); rows.textContent = '';
  for (const k of (d.keys || [])) {
    const tr = document.createElement('tr');
    cell(tr, k.name); cell(tr, k.email); cell(tr, fmt(k.created_at)); cell(tr, fmt(k.last_used_at));
    cell(tr, k.requests); cell(tr, (k.audio_seconds / 60).toFixed(1));
    cell(tr, `${num(k.input_tokens)} / ${num(k.output_tokens)} / ${num(k.total_tokens)}`);
    const last = cell(tr, k.revoked_at ? 'revoked' : '', 'muted');
    if (!k.revoked_at) {
      const b = document.createElement('button'); b.className = 'small'; b.textContent = 'Revoke';
      b.onclick = async () => { if (confirm('Revoke the key for ' + k.name + '?')) { await fetch('/admin/api/keys/' + k.id + '/revoke', {method: 'POST', headers: {'X-Zeno-Admin': '1'}}); load(); } };
      last.appendChild(b);
    } else tr.className = 'muted';
    rows.appendChild(tr);
  }
  const runs = document.getElementById('runs'); runs.textContent = '';
  for (const j of (d.runs || [])) {
    const tr = document.createElement('tr');
    cell(tr, fmt(j.created_at)); cell(tr, j.editor_name); cell(tr, `${j.project} (${j.language})`);
    cell(tr, `${j.agent_name} · ${j.agent_model}`);
    cell(tr, `${j.clips_count} clips · ${(j.video_seconds || 0).toFixed(1)}s`);
    cell(tr, `${num(j.transcribe_in_tokens)} / ${num(j.transcribe_out_tokens)} / ${num(j.transcribe_total_tokens)}`);
    cell(tr, `${num(j.agent_in_tokens)} / ${num(j.agent_cached_tokens)} / ${num(j.agent_out_tokens)} / ${num(j.agent_total_tokens)}`);
    cell(tr, num(j.grand_total_tokens));
    runs.appendChild(tr);
  }
}
load();
</script></main></body></html>"""
