"""Web UI pages for the Zeno Cloud service (Google OAuth 2.0 + Auto-Localhost Session Handoff + Admin Editorial Learning Portal)."""

STYLE = """
*{box-sizing:border-box}
body{margin:0;min-height:100vh;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#e8eef7;
  background:radial-gradient(900px 500px at 15% -10%,#2a3f7a 0%,transparent 60%),
             radial-gradient(800px 500px at 90% 10%,#1b5e5a 0%,transparent 55%),#0b1020}
main{max-width:560px;margin:0 auto;padding:52px 22px}
main.wide{max-width:1120px}
.brand{display:inline-flex;align-items:center;gap:10px;font-weight:700;letter-spacing:.3px;color:#cfe0ff;margin-bottom:18px}
.dot{width:11px;height:11px;border-radius:50%;background:linear-gradient(135deg,#6ee7b7,#60a5fa);box-shadow:0 0 16px #60a5fa}
.card{background:rgba(20,28,52,.78);border:1px solid rgba(148,163,184,.18);border-radius:16px;padding:28px;
  backdrop-filter:blur(12px);box-shadow:0 20px 50px rgba(0,0,0,.45);margin-bottom:20px}
h1{margin:0 0 6px;font-size:24px;letter-spacing:-.3px}
h2{margin:0 0 12px;font-size:18px;letter-spacing:-.2px;color:#cfe0ff}
p.sub{margin:0 0 22px;color:#9fb0cc;font-size:14px}
label{display:block;font-size:13px;color:#b8c7e0;margin:14px 0 6px;font-weight:600}
input{width:100%;padding:11px 13px;border-radius:10px;border:1px solid rgba(148,163,184,.28);
  background:#0c1326;color:#f1f5f9;font:inherit;outline:none}
input:focus{border-color:#60a5fa;box-shadow:0 0 0 3px rgba(96,165,250,.18)}
button{margin-top:18px;width:100%;padding:12px 16px;border:0;border-radius:10px;font:600 15px inherit;cursor:pointer;
  color:#071226;background:linear-gradient(135deg,#6ee7b7,#60a5fa);transition:transform .06s ease,opacity .15s}
button:hover{opacity:.94} button:active{transform:translateY(1px)}
button.small{width:auto;margin:0;padding:6px 12px;font-size:13px;background:#1e293b;color:#e2e8f0;border:1px solid #334155}
button.danger{background:#3b1d2a;color:#fda4af;border-color:#7f1d1d}
.err{margin-top:14px;padding:10px 12px;border-radius:9px;background:rgba(239,68,68,.14);border:1px solid rgba(239,68,68,.4);
  color:#fecaca;font-size:13px;display:none}
.banner-ok{margin-bottom:16px;padding:12px 14px;border-radius:10px;background:rgba(16,185,129,.15);border:1px solid rgba(16,185,129,.45);
  color:#a7f3d0;font-size:13.5px}
.banner-cli{margin-bottom:16px;padding:10px 14px;border-radius:10px;background:rgba(96,165,250,.14);border:1px solid rgba(96,165,250,.4);
  color:#bfdbfe;font-size:13px;display:none}
pre{margin:10px 0 0;padding:13px;border-radius:10px;background:#070c1a;border:1px solid #26324d;color:#a7f3d0;
  font:12.5px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow-x:auto;white-space:pre-wrap;word-break:break-all}
.warn{margin-top:14px;font-size:12.5px;color:#cbd5e1}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:14px 0}
.kv{background:#0c1326;border:1px solid #1e293b;border-radius:10px;padding:10px 12px}
.kv .k{font-size:11px;text-transform:uppercase;color:#94a3b8;letter-spacing:.5px}
.kv .v{font-size:14px;font-weight:600;color:#e2e8f0;margin-top:2px;word-break:break-all}
.divider{display:flex;align-items:center;gap:10px;color:#64748b;font-size:12px;margin:18px 0}
.divider::before,.divider::after{content:"";flex:1;height:1px;background:rgba(148,163,184,.2)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:10px 8px;border-bottom:1px solid rgba(148,163,184,.14)}
th{color:#9fb0cc;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.5px}
.pill{display:inline-block;padding:2px 8px;border-radius:99px;font-size:12px;font-weight:600}
.ok{background:rgba(16,185,129,.16);color:#6ee7b7} .rev{background:rgba(239,68,68,.16);color:#fca5a5}
.lang-pills{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
.lang-pill{background:#0c1326;border:1px solid #26324d;border-radius:10px;padding:8px 12px;font-size:12.5px;color:#cbd5e1}
"""

SIGNUP_PAGE = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zeno Cloud — Supernova SSO & Session Setup</title><style>{STYLE}</style>
<script src="https://accounts.google.com/gsi/client" async defer></script>
</head><body><main>
  <div class="brand"><span class="dot"></span> Zeno Video AI · @gosupernova.live SSO</div>
  <div class="card" id="form">
    <h1>Sign in to Zeno</h1>
    <p class="sub">Sign in with your <b>@gosupernova.live</b> / <b>@gosupernova.line</b> Google account. When launched from the Zeno installer or CLI, your session token is saved automatically into your local workspace — no copy-paste needed.</p>
    <div id="cli-banner" class="banner-cli">⚡ <b>Zeno Local CLI Detected:</b> Signing in below will automatically connect and save your session token to your computer.</div>
    <div id="g-signin-wrap" style="display:none;margin:14px 0">
      <div id="g-signin-btn"></div>
    </div>
    <div id="divider-or" class="divider" style="display:none"><span>or team name / email sign-in</span></div>
    <div id="fallback-form">
      <label for="name">Your Name or @gosupernova.live Email</label>
      <input id="name" autocomplete="name" placeholder="e.g. Mathini or mathini@gosupernova.live">
      <div id="codebox" style="display:none">
        <label for="code">Team code</label>
        <input id="code" type="password" placeholder="Ask your lead for the code">
      </div>
      <button id="go" onclick="make()">Sign In &amp; Generate Session</button>
    </div>
    <div class="err" id="err"></div>
  </div>

  <div class="card" id="result" style="display:none">
    <div id="cli-connected" class="banner-ok" style="display:none">
      ✓ <b>Connected automatically to Zeno on your computer!</b> Your session token has been saved inside Zeno (<code>.zeno_session.json</code> &amp; <code>.env</code>) — no copy-paste needed. You can return to your terminal now.
    </div>
    <h1>Editor Dashboard</h1>
    <p class="sub">Your personal Zeno Cloud session is active. Every edit job automatically logs telemetry and syncs editorial learnings.</p>
    <div class="grid2">
      <div class="kv"><div class="k">Editor</div><div class="v" id="user-name">-</div></div>
      <div class="kv"><div class="k">Verified Email</div><div class="v" id="user-email">-</div></div>
      <div class="kv"><div class="k">Session Prefix</div><div class="v" id="key-prefix">-</div></div>
      <div class="kv"><div class="k">Status</div><div class="v"><span class="pill ok">Active</span></div></div>
    </div>
    <label>Your Personal Zeno Session Token ID</label>
    <pre id="tok-box" style="margin-top:4px;font-weight:600;color:#6ee7b7"></pre>
    <button onclick="copyTok()" id="copytokbtn" style="margin-top:10px">Copy Session Token</button>
    <details style="margin-top:14px">
      <summary style="cursor:pointer;color:#9fb0cc;font-size:13px">Show full <code>.env</code> configuration lines</summary>
      <pre id="env"></pre>
      <button onclick="copyEnv()" id="copybtn" style="margin-top:10px">Copy .env lines</button>
    </details>
  </div>
</main>
<script>
const params = new URLSearchParams(window.location.search);
const rawPort = parseInt(params.get('callback_port') || '0', 10);
const callbackPort = (rawPort >= 1024 && rawPort <= 65535) ? rawPort : 0;
if (callbackPort) {{
  document.getElementById('cli-banner').style.display = 'block';
}}

function showErr(msg) {{
  const err = document.getElementById('err');
  err.textContent = msg;
  err.style.display = 'block';
}}

async function completeAuth(d) {{
  const origin = window.location.origin;
  const tok = d.key || d.token || '';
  document.getElementById('user-name').textContent = d.name || d.email || 'Editor';
  document.getElementById('user-email').textContent = d.email || '';
  document.getElementById('key-prefix').textContent = (d.prefix || tok.slice(0, 12)) + '...';
  document.getElementById('tok-box').textContent = tok;
  document.getElementById('env').textContent =
    `ZENO_CLOUD_URL=${{origin}}\\nZENO_CLOUD_TOKEN=${{tok}}\\nSCRIPTCUT_CLOUD_URL=${{origin}}\\nSCRIPTCUT_CLOUD_TOKEN=${{tok}}`;
  document.getElementById('form').style.display = 'none';
  document.getElementById('result').style.display = 'block';
  try {{ window.history.replaceState({{}}, '', '/'); }} catch (e) {{}}

  if (callbackPort && tok) {{
    const cbGet = `http://127.0.0.1:${{callbackPort}}/callback?token=${{encodeURIComponent(tok)}}&email=${{encodeURIComponent(d.email||'')}}&name=${{encodeURIComponent(d.name||'')}}&cloud_url=${{encodeURIComponent(origin)}}`;
    let delivered = false;
    try {{
      const r = await fetch(`http://127.0.0.1:${{callbackPort}}/callback`, {{
        method: 'POST',
        headers: {{'Content-Type': 'text/plain;charset=utf-8'}},
        body: JSON.stringify({{token: tok, email: d.email || '', name: d.name || '', cloud_url: origin}})
      }});
      if (r.ok) delivered = true;
    }} catch (e) {{}}
    if (!delivered) {{
      try {{
        await fetch(cbGet, {{ method: 'GET', mode: 'no-cors' }});
        delivered = true;
      }} catch (e) {{}}
    }}
    if (delivered) {{
      document.getElementById('cli-connected').style.display = 'block';
    }}
  }}
}}

async function onGoogleSignIn(resp) {{
  document.getElementById('err').style.display = 'none';
  const btn = document.getElementById('go');
  if (btn) {{ btn.disabled = true; btn.textContent = 'Verifying Google Sign-In...'; }}
  try {{
    const r = await fetch('/api/auth/google', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{credential: resp.credential}})
    }});
    const d = await r.json();
    if (!r.ok) {{
      if (btn) {{ btn.disabled = false; btn.textContent = 'Sign In & Generate Session'; }}
      showErr(d.detail || 'Google sign-in failed');
      return;
    }}
    await completeAuth(d);
  }} catch (e) {{
    if (btn) {{ btn.disabled = false; btn.textContent = 'Sign In & Generate Session'; }}
    showErr('Network error verifying Google sign-in');
  }}
}}

fetch('/api/auth/config').then(r=>r.json()).then(cfg=>{{
  if (cfg.signup_code_required) document.getElementById('codebox').style.display = 'block';
  if (cfg.google_client_id) {{
    document.getElementById('g-signin-wrap').style.display = 'block';
    document.getElementById('divider-or').style.display = 'flex';
    const initGsi = () => {{
      if (window.google && google.accounts && google.accounts.id) {{
        google.accounts.id.initialize({{
          client_id: cfg.google_client_id,
          callback: onGoogleSignIn
        }});
        google.accounts.id.renderButton(document.getElementById('g-signin-btn'), {{
          theme: 'filled_blue', size: 'large', width: 360, text: 'signin_with'
        }});
      }} else {{
        setTimeout(initGsi, 200);
      }}
    }};
    initGsi();
  }}
}}).catch(()=>{{}});

async function make(){{
  const err = document.getElementById('err'), btn = document.getElementById('go');
  err.style.display = 'none'; btn.disabled = true;
  const raw = document.getElementById('name').value.trim();
  const payload = {{
    name: raw,
    email: raw.includes('@') ? raw : '',
    code: document.getElementById('code').value
  }};
  try {{
    const r = await fetch('/api/keys', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(payload)}});
    const d = await r.json();
    if (!r.ok) {{ showErr(d.detail || 'Could not sign in'); btn.disabled = false; return; }}
    await completeAuth(d);
  }} catch (e) {{
    showErr('Network error, try again'); btn.disabled = false;
  }}
}}
function copyTok(){{
  navigator.clipboard.writeText(document.getElementById('tok-box').textContent);
  document.getElementById('copytokbtn').textContent = 'Copied Session Token ✓';
}}
function copyEnv(){{
  navigator.clipboard.writeText(document.getElementById('env').textContent);
  document.getElementById('copybtn').textContent = 'Copied ✓';
}}
</script></body></html>"""

ADMIN_PAGE = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zeno — Admin, Editorial Brain &amp; Token Telemetry</title><style>{STYLE}</style></head><body><main class="wide">
  <div class="brand"><span class="dot"></span> Zeno Video AI · Admin, Editorial Brain &amp; Token Telemetry</div>
  <div class="card">
    <h1>Team Keys &amp; Gemini Transcription Totals</h1>
    <p class="sub">Active personal keys, total clips transcribed, audio minutes, and cumulative Gemini API tokens.</p>
    <table><thead><tr><th>Name</th><th>Email</th><th>Created</th><th>Last used</th><th>Clips</th><th>Audio</th><th>Gemini In / Out / Total</th><th>Status</th><th></th></tr></thead>
    <tbody id="rows"><tr><td colspan="9">Loading…</td></tr></tbody></table>
  </div>
  <div class="card" id="brain-section">
    <h1>Editorial Brain &amp; Team Learnings</h1>
    <p class="sub">Patterns learned across Hindi, Kannada, Malayalam, Telugu, Tamil &amp; English edits (<code>plan.auto.edl → plan.edl</code> diffs &amp; shared <code>brain_memory.json</code> rules).</p>
    <h2>Language Editing Patterns</h2>
    <div id="lang-stats" class="lang-pills"><span class="lang-pill">Loading language stats…</span></div>
    <h2>Shared Brain Memory Rules</h2>
    <table><thead><tr><th>Lang</th><th>Category</th><th>Learned Rule</th><th>Author</th><th>Created</th></tr></thead>
    <tbody id="brain-rules"><tr><td colspan="5">Loading rules…</td></tr></tbody></table>
    <h2 style="margin-top:20px">Recent Auto-Plan → Final-Plan Editorial Diffs</h2>
    <table><thead><tr><th>When</th><th>Editor</th><th>Project &amp; Lang</th><th>Take Swaps</th><th>Pause/Cut Trims</th><th>Zoom Changes</th><th>Marks Pack</th></tr></thead>
    <tbody id="plan-diffs"><tr><td colspan="7">Loading diffs…</td></tr></tbody></table>
  </div>
  <div class="card">
    <h1>Video Edit Runs (Agent + Clip-by-Clip Token Telemetry)</h1>
    <p class="sub">Every completed video edit pushed from Antigravity / Claude Code, separating Agent tokens from per-clip Gemini transcription tokens.</p>
    <table><thead><tr><th>When</th><th>Editor</th><th>Project &amp; Lang</th><th>Agent &amp; Model</th><th>Clips / Dur</th><th>Transcribe Tokens</th><th>Agent Tokens</th><th>Grand Total</th><th>Per-Clip Breakdown</th></tr></thead>
    <tbody id="runs"><tr><td colspan="9">Loading…</td></tr></tbody></table>
  </div>
<script>
const fmt = t => t ? new Date(t*1000).toISOString().slice(0,16).replace('T',' ') : 'never';
const esc = s => String(s??'').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const num = n => Number(n||0).toLocaleString();
async function load(){{
  const r = await fetch('/admin/api/keys'); const d = await r.json();
  const tb = document.getElementById('rows');
  tb.innerHTML = d.keys.map(k => `<tr>
    <td><b>${{esc(k.name)}}</b></td><td>${{esc(k.email)}}</td>
    <td>${{fmt(k.created_at)}}</td><td>${{fmt(k.last_used_at)}}</td>
    <td>${{k.requests}}</td><td>${{(k.audio_seconds/60).toFixed(1)}} min</td>
    <td>${{num(k.input_tokens)}} / ${{num(k.output_tokens)}} / <b>${{num(k.total_tokens)}}</b></td>
    <td><span class="pill ${{k.revoked_at?'rev':'ok'}}">${{k.revoked_at?'Revoked':'Active'}}</span></td>
    <td>${{k.revoked_at?'':`<button class="small danger" onclick="rev(${{k.id}})">Revoke</button>`}}</td>
  </tr>`).join('') || '<tr><td colspan="9">No keys yet</td></tr>';

  const lrn = d.learnings || {{}};
  const lp = lrn.language_patterns || {{}};
  const lpKeys = Object.keys(lp);
  document.getElementById('lang-stats').innerHTML = lpKeys.length ? lpKeys.map(lang => {{
    const s = lp[lang];
    return `<div class="lang-pill"><b>${{esc(lang.toUpperCase())}}</b>: ${{s.runs}} run(s) · ${{(s.total_video_seconds||0).toFixed(0)}}s video · ${{s.take_swaps||0}} take swap(s) · ${{s.cut_trims||0}} cut trim(s) · ${{s.zoom_changes||0}} zoom tweak(s)</div>`;
  }}).join('') : '<span class="lang-pill">No language runs recorded yet</span>';

  const rules = lrn.learned_rules || [];
  document.getElementById('brain-rules').innerHTML = rules.map(rl => `<tr>
    <td><span class="pill ok">${{esc(rl.lang||'all')}}</span></td>
    <td>${{esc(rl.category||'general')}}</td>
    <td>${{esc(rl.rule_text||rl.rule||'')}}</td>
    <td>${{esc(rl.author_email||'-')}}</td>
    <td>${{esc(String(rl.created_at||'').slice(0,16))}}</td>
  </tr>`).join('') || '<tr><td colspan="5">No shared brain memory rules pushed yet</td></tr>';

  const diffs = lrn.edl_diffs || [];
  document.getElementById('plan-diffs').innerHTML = diffs.map(df => {{
    const ed = df.edl_diff || {{}};
    const swaps = (ed.take_swaps || []).map(s => `${{s.line_id}}: ${{ (s.from_clips||[]).join(',') }}→${{ (s.to_clips||[]).join(',') }}`).join('; ') || '0';
    const trims = (ed.cut_timing_deltas || []).length;
    const zooms = ((ed.zoom_changes || {{}}).changes || []).length;
    const mpack = ((ed.marks_summary || {{}}).mark_pack) || 'flash';
    return `<tr>
      <td>${{fmt(df.created_at)}}</td>
      <td><b>${{esc(df.editor_name)}}</b><br><span style="color:#9fb0cc;font-size:11px">${{esc(df.user_email||'')}}</span></td>
      <td><b>${{esc(df.project)}}</b> (${{esc(df.language)}})</td>
      <td>${{esc(swaps)}}</td>
      <td>${{trims}} segment(s)</td>
      <td>${{zooms}} segment(s)</td>
      <td><code>${{esc(mpack)}}</code></td>
    </tr>`;
  }}).join('') || '<tr><td colspan="7">No EDL diffs recorded yet</td></tr>';

  const rb = document.getElementById('runs');
  const runs = d.runs || [];
  rb.innerHTML = runs.map(x => {{
    const clipsHtml = (x.clips || []).map(c =>
      `<div><code>${{esc(c.clip)}}</code> (${{Number(c.duration_sec||0).toFixed(1)}}s, ${{c.words||0}}w, ${{esc(c.status||'')}}): in=${{num(c.in_tokens)}}, out=${{num(c.out_tokens)}}, tot=${{num(c.total_tokens)}}</div>`
    ).join('');
    return `<tr>
      <td>${{fmt(x.created_at)}}</td>
      <td><b>${{esc(x.editor_name)}}</b></td>
      <td><b>${{esc(x.project)}}</b><br><span style="color:#9fb0cc;font-size:12px">lang=${{esc(x.language)}}</span></td>
      <td><b>${{esc(x.agent_name)}}</b><br><span style="color:#9fb0cc;font-size:12px">${{esc(x.agent_model)}}</span></td>
      <td>${{x.clips_count}} clips / ${{Number(x.video_seconds||0).toFixed(1)}}s</td>
      <td>${{num(x.transcribe_in_tokens)}} in + ${{num(x.transcribe_out_tokens)}} out = <b>${{num(x.transcribe_total_tokens)}}</b></td>
      <td>${{num(x.agent_in_tokens)}} in (${{num(x.agent_cached_tokens)}} cached) + ${{num(x.agent_out_tokens)}} out = <b>${{num(x.agent_total_tokens)}}</b></td>
      <td><span class="pill ok">${{num(x.grand_total_tokens)}}</span></td>
      <td style="font-size:12px;max-width:340px">${{clipsHtml || '-'}}</td>
    </tr>`;
  }}).join('') || '<tr><td colspan="9">No video edit runs logged yet</td></tr>';
}}
async function rev(id){{
  if (!confirm('Revoke this key?')) return;
  await fetch(`/admin/api/keys/${{id}}/revoke`, {{method:'POST', headers:{{'X-Zeno-Admin':'1'}}}}); load();
}}
load();
</script></main></body></html>"""
