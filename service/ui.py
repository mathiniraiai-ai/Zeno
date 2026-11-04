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

SIGNUP_STYLE = """
*{box-sizing:border-box}
body{
  margin:0;
  min-height:100vh;
  background:#181818;
  color:#ffffff;
  font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  display:flex;
  flex-direction:column;
  align-items:center;
  justify-content:center;
  padding:24px;
  -webkit-font-smoothing:antialiased;
}
.login-card{
  width:100%;
  max-width:400px;
  background:#1f1f1f;
  border:1px solid #2e2e2e;
  border-radius:14px;
  padding:40px 32px 34px;
  text-align:center;
  box-shadow:0 16px 40px rgba(0,0,0,.45);
}
h1{
  margin:0 0 12px;
  font-size:26px;
  font-weight:700;
  letter-spacing:-0.4px;
  color:#ffffff;
}
.desc{
  margin:0 0 28px;
  font-size:14px;
  line-height:1.55;
  color:#9ca3af;
}
.g-btn-wrap{
  position:relative;
  width:100%;
  height:48px;
  margin-bottom:20px;
}
.g-custom-btn{
  width:100%;
  height:48px;
  background:#222222;
  border:1px solid #383838;
  border-radius:8px;
  display:flex;
  align-items:center;
  justify-content:center;
  gap:10px;
  color:#ffffff;
  font-size:14.5px;
  font-weight:600;
  font-family:inherit;
  cursor:pointer;
  transition:background .15s ease,border-color .15s ease;
}
.g-btn-wrap:hover .g-custom-btn{
  background:#2a2a2a;
  border-color:#4a4a4a;
}
.g-gsi-overlay{
  position:absolute;
  inset:0;
  opacity:0.01;
  overflow:hidden;
  display:flex;
  align-items:center;
  justify-content:center;
  cursor:pointer;
}
.domain-note{
  margin:0;
  font-size:12.5px;
  color:#737373;
}
.domain-note span{
  color:#d4d4d4;
  font-weight:500;
}
.err{
  display:none;
  margin-top:16px;
  padding:10px 12px;
  border-radius:8px;
  background:rgba(239,68,68,.12);
  border:1px solid rgba(239,68,68,.35);
  color:#fca5a5;
  font-size:13px;
}
.ok-badge{
  display:inline-flex;
  align-items:center;
  gap:6px;
  padding:6px 14px;
  border-radius:999px;
  background:rgba(34,197,94,.14);
  color:#4ade80;
  font-size:13px;
  font-weight:600;
  margin-bottom:16px;
}
.user-pill{
  display:inline-flex;
  align-items:center;
  gap:8px;
  padding:6px 14px;
  border-radius:8px;
  background:#262626;
  border:1px solid #363636;
  color:#e5e5e5;
  font-size:13.5px;
  font-weight:500;
  margin:8px 0 18px;
}
.footer-note{
  margin-top:28px;
  font-size:12px;
  color:#666666;
}
"""

SIGNUP_PAGE = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Log in to Zeno</title>
<style>{SIGNUP_STYLE}</style>
<script src="https://accounts.google.com/gsi/client" async defer></script>
</head><body>
<a href="/usage" style="position:fixed;top:16px;right:20px;z-index:5;border:1px solid #3a3a3a;background:#1d1d1d;color:#e5e5e5;border-radius:10px;padding:7px 16px;font:600 13px -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;text-decoration:none">Admin</a>
<!-- Generate Zeno Session -->
<div class="login-card" id="form-card">
  <h1>Log in to Zeno</h1>
  <p class="desc">
    AI-powered script-led video editor for Supernova. Sign in with Google to connect your workspace and automatically save your session to <code>.env</code>.
  </p>

  <div class="g-btn-wrap" id="g-signin-wrap">
    <button type="button" class="g-custom-btn" id="g-fallback-trigger" onclick="triggerGoogleSignIn()">
      <svg width="18" height="18" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
        <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
        <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
        <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
      </svg>
      <span>Log in</span>
    </button>
    <div id="g-signin-btn" class="g-gsi-overlay"></div>
  </div>

  <p class="domain-note">Only <span>@gosupernova.live</span> accounts are permitted</p>
  <div class="err" id="err"></div>
</div>

<div class="login-card" id="result-card" style="display:none">
  <div class="ok-badge">✓ Connected to Zeno</div>
  <h1>Signed in</h1>
  <div class="user-pill" id="user-email">user@gosupernova.live</div>
  <p class="desc" style="margin-bottom:0">
    Your session has been automatically saved to your local <code>.env</code> file. This tab will close automatically…
  </p>
</div>

<div class="footer-note">Zeno Video AI · Supernova</div>

<script>
const params = new URLSearchParams(window.location.search);
const rawPort = parseInt(params.get('callback_port') || '0', 10);
const callbackPort = (rawPort >= 1024 && rawPort <= 65535) ? rawPort : 8765;
const cliState = params.get('cli_state') || '';

function showErr(msg) {{
  const err = document.getElementById('err');
  err.textContent = msg;
  err.style.display = 'block';
}}

function clearErr() {{
  document.getElementById('err').style.display = 'none';
}}

function triggerGoogleSignIn() {{
  if (window.google && google.accounts && google.accounts.id) {{
    google.accounts.id.prompt();
  }} else {{
    showErr('Google Sign-In is loading, please try again in a moment.');
  }}
}}

async function completeAuth(d) {{
  const origin = window.location.origin;
  const tok = d.key || d.token || '';
  const displayName = d.name || d.email || 'Editor';
  const email = d.email || '';
  document.getElementById('user-email').textContent = email || displayName;
  document.getElementById('form-card').style.display = 'none';
  document.getElementById('result-card').style.display = 'block';
  try {{ window.history.replaceState({{}}, '', '/'); }} catch (e) {{}}

  if (tok && navigator.clipboard && navigator.clipboard.writeText) {{
    try {{ await navigator.clipboard.writeText(tok); }} catch (e) {{}}
  }}

  if (tok && callbackPort) {{
    const cbGet = `http://127.0.0.1:${{callbackPort}}/callback?token=${{encodeURIComponent(tok)}}&email=${{encodeURIComponent(email)}}&name=${{encodeURIComponent(displayName)}}&cloud_url=${{encodeURIComponent(origin)}}`;
    try {{
      await fetch(`http://127.0.0.1:${{callbackPort}}/callback`, {{
        method: 'POST',
        mode: 'no-cors',
        headers: {{'Content-Type': 'text/plain;charset=utf-8'}},
        body: JSON.stringify({{token: tok, email: email, name: displayName, cloud_url: origin}})
      }});
    }} catch (e) {{}}
    try {{
      await fetch(cbGet, {{ method: 'GET', mode: 'no-cors' }});
    }} catch (e) {{}}
  }}

  setTimeout(() => {{
    try {{ window.open('', '_self', ''); window.close(); }} catch (e) {{}}
  }}, 1200);
}}

async function onGoogleSignIn(resp) {{
  clearErr();
  try {{
    const r = await fetch('/api/auth/google', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{credential: resp.credential, cli_state: cliState}})
    }});
    const d = await r.json();
    if (!r.ok) {{
      showErr(d.detail || 'Google sign-in failed');
      return;
    }}
    await completeAuth(d);
  }} catch (e) {{
    showErr('Network error verifying Google sign-in');
  }}
}}

fetch('/api/auth/config').then(r=>r.json()).then(cfg=>{{
  if (cfg.google_client_id) {{
    const initGsi = () => {{
      if (window.google && google.accounts && google.accounts.id) {{
        google.accounts.id.initialize({{
          client_id: cfg.google_client_id,
          callback: onGoogleSignIn,
          auto_select: true
        }});
        google.accounts.id.renderButton(document.getElementById('g-signin-btn'), {{
          theme: 'filled_black', size: 'large', width: 336, text: 'signin_with'
        }});
      }} else {{
        setTimeout(initGsi, 200);
      }}
    }};
    initGsi();
  }}
}}).catch(()=>{{}});
</script></body></html>"""

ADMIN_PAGE = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zeno — Admin, Editorial Brain &amp; Token Telemetry</title><style>{STYLE}</style></head><body><main class="wide">
  <div class="brand"><span class="dot"></span> Zeno Video AI · Admin, Editorial Brain &amp; Token Telemetry &nbsp;<a href="/usage" style="color:#7fb0ff;font-weight:600;font-size:13px">Usage log →</a></div>
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
