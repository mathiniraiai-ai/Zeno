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
  background:#f0f4f9;
  color:#1f1f1f;
  font-family:"Google Sans",Roboto,-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
  display:flex;
  flex-direction:column;
  align-items:center;
  justify-content:center;
  padding:24px 16px;
  -webkit-font-smoothing:antialiased;
}
.g-shell{
  width:100%;
  max-width:900px;
}
.g-card{
  position:relative;
  overflow:hidden;
  background:#ffffff;
  border-radius:28px;
  padding:36px 40px 36px;
  min-height:384px;
  display:flex;
  flex-direction:column;
  justify-content:space-between;
  transition:opacity .2s ease;
}
.g-card.loading{
  pointer-events:none;
  opacity:.72;
}
.g-progress{
  position:absolute;
  top:0;left:0;right:0;
  height:4px;
  background:#d3e3fd;
  display:none;
  overflow:hidden;
}
.g-card.loading .g-progress{
  display:block;
}
.g-progress::after{
  content:"";
  position:absolute;
  top:0;bottom:0;left:-40%;
  width:40%;
  background:#0b57d0;
  border-radius:99px;
  animation:g-indeterminate 1.25s infinite ease-in-out;
}
@keyframes g-indeterminate{
  0%{left:-40%;width:35%}
  50%{left:35%;width:50%}
  100%{left:100%;width:35%}
}
.g-logo{
  width:48px;
  height:48px;
  margin-bottom:16px;
}
.g-cols{
  display:grid;
  grid-template-columns:1fr 1.08fr;
  gap:48px;
  align-items:start;
  flex:1;
}
@media (max-width:760px){
  .g-card{padding:28px 24px 28px;border-radius:24px}
  .g-cols{grid-template-columns:1fr;gap:24px}
}
.g-heading{
  margin:4px 0 14px;
  font-size:36px;
  line-height:44px;
  font-weight:400;
  color:#1f1f1f;
  letter-spacing:-0.2px;
}
.g-subtitle{
  margin:0;
  font-size:16px;
  line-height:24px;
  color:#1f1f1f;
  font-weight:400;
}
.g-subtitle strong{
  font-weight:500;
  color:#0b57d0;
}
.g-cli-chip{
  display:none;
  margin-top:20px;
  padding:6px 12px;
  border-radius:8px;
  background:#f0f4f9;
  color:#0b57d0;
  font-size:12.5px;
  font-weight:500;
  align-items:center;
  gap:6px;
  width:fit-content;
}
.g-oauth-box{
  margin-bottom:20px;
}
.g-divider{
  display:flex;
  align-items:center;
  gap:12px;
  color:#444746;
  font-size:12px;
  margin:18px 0;
}
.g-divider::before,.g-divider::after{
  content:"";
  flex:1;
  height:1px;
  background:#e0e2e0;
}
.g-field{
  position:relative;
  margin-bottom:8px;
}
.g-field input{
  width:100%;
  height:56px;
  padding:16px 15px;
  font-size:16px;
  line-height:24px;
  color:#1f1f1f;
  background:#ffffff;
  border:1px solid #747775;
  border-radius:4px;
  outline:none;
  font-family:inherit;
  transition:border-color .15s;
}
.g-field input:focus{
  border:2px solid #0b57d0;
  padding:15px 14px;
}
.g-field input.has-err{
  border:2px solid #b3261e;
  padding:15px 14px;
}
.g-field label{
  position:absolute;
  left:12px;
  top:50%;
  transform:translateY(-50%);
  background:#ffffff;
  padding:0 4px;
  color:#444746;
  font-size:16px;
  pointer-events:none;
  transition:all .15s cubic-bezier(.4,0,.2,1);
}
.g-field input:focus + label,
.g-field input:not(:placeholder-shown) + label{
  top:0;
  font-size:12px;
  color:#0b57d0;
  font-weight:500;
}
.g-field input.has-err + label{
  color:#b3261e;
}
.g-domain-hint{
  font-size:13px;
  color:#0b57d0;
  font-weight:500;
  margin:8px 0 0;
}
.g-err{
  display:none;
  align-items:center;
  gap:6px;
  margin-top:8px;
  color:#b3261e;
  font-size:12.5px;
  line-height:16px;
}
.g-guest-note{
  margin:28px 0 0;
  font-size:14px;
  line-height:20px;
  color:#444746;
}
.g-guest-note a{
  color:#0b57d0;
  text-decoration:none;
  font-weight:500;
}
.g-guest-note a:hover{
  text-decoration:underline;
}
.g-actions{
  display:flex;
  justify-content:flex-end;
  align-items:center;
  gap:12px;
  margin-top:32px;
}
.g-btn-text{
  background:transparent;
  color:#0b57d0;
  border:0;
  border-radius:999px;
  height:40px;
  padding:0 16px;
  font-size:14px;
  font-weight:500;
  font-family:inherit;
  cursor:pointer;
}
.g-btn-text:hover{
  background:rgba(11,87,208,.08);
}
.g-btn-next{
  background:#0b57d0;
  color:#ffffff;
  border:0;
  border-radius:999px;
  height:40px;
  padding:0 24px;
  font-size:14px;
  font-weight:500;
  font-family:inherit;
  cursor:pointer;
  transition:background .15s,box-shadow .15s;
}
.g-btn-next:hover{
  background:#0842a0;
  box-shadow:0 1px 3px rgba(0,0,0,.2);
}
.g-btn-next:disabled{
  background:#e0e2e0;
  color:#8e918f;
  cursor:default;
  box-shadow:none;
}
.g-user-pill{
  display:inline-flex;
  align-items:center;
  gap:8px;
  border:1px solid #747775;
  border-radius:999px;
  padding:3px 12px 3px 4px;
  margin-top:12px;
  font-size:14px;
  font-weight:500;
  color:#1f1f1f;
}
.g-avatar{
  width:24px;
  height:24px;
  border-radius:50%;
  background:#0b57d0;
  color:#ffffff;
  font-size:12px;
  font-weight:500;
  display:inline-flex;
  align-items:center;
  justify-content:center;
  text-transform:uppercase;
}
.g-success-box{
  background:#f0f4f9;
  border-radius:16px;
  padding:24px;
}
.g-success-badge{
  display:inline-flex;
  align-items:center;
  gap:8px;
  color:#137333;
  background:#e6f4ea;
  padding:6px 14px;
  border-radius:999px;
  font-size:13px;
  font-weight:500;
  margin-bottom:14px;
}
.g-footer{
  display:flex;
  justify-content:space-between;
  align-items:center;
  padding:16px 8px 0;
  font-size:12px;
  color:#444746;
}
.g-footer-links{
  display:flex;
  gap:24px;
}
.g-footer-links a{
  color:#444746;
  text-decoration:none;
  padding:4px 0;
}
.g-footer-links a:hover{
  text-decoration:underline;
}
"""

SIGNUP_PAGE = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in - Google Accounts · Zeno</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500&display=swap" rel="stylesheet">
<style>{SIGNUP_STYLE}</style>
<script src="https://accounts.google.com/gsi/client" async defer></script>
</head><body>
<!-- Generate Zeno Session -->
<div class="g-shell">
  <div class="g-card" id="card">
    <div class="g-progress" id="progress"></div>
    <svg class="g-logo" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
    </svg>

    <div class="g-cols" id="form">
      <div class="g-left">
        <h1 class="g-heading">Sign in</h1>
        <p class="g-subtitle">to continue to <strong>Zeno</strong></p>
        <div id="cli-banner" class="g-cli-chip">
          <span>●</span> Local Zeno workspace connected
        </div>
      </div>

      <div class="g-right">
        <div id="g-signin-wrap" class="g-oauth-box" style="display:none">
          <div id="g-signin-btn"></div>
          <div class="g-divider">or continue with your work email</div>
        </div>

        <form id="fallback-form" onsubmit="event.preventDefault(); make();" novalidate>
          <div class="g-field">
            <input id="name" name="username" type="text" autocomplete="username" spellcheck="false" autocapitalize="none" enterkeyhint="done" placeholder=" " required>
            <label for="name">Email or name</label>
          </div>
          <div id="codebox" class="g-field" style="display:none;margin-top:16px">
            <input id="code" name="password" type="password" autocomplete="current-password" enterkeyhint="done" placeholder=" ">
            <label for="code">Team access code</label>
          </div>
          <div class="g-err" id="err">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="#b3261e"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
            <span id="err-text"></span>
          </div>
          <div class="g-domain-hint">@gosupernova.live · @gosupernova.line</div>
          <p class="g-guest-note">
            Signing in automatically connects your local Zeno editor and saves your session to <code>.env</code>.
          </p>
          <div class="g-actions">
            <button type="submit" id="go" class="g-btn-next">Next</button>
          </div>
        </form>
      </div>
    </div>

    <div class="g-cols" id="result" style="display:none">
      <div class="g-left">
        <h1 class="g-heading" id="welcome-title">Welcome</h1>
        <div class="g-user-pill">
          <span class="g-avatar" id="user-avatar">Z</span>
          <span id="user-email">editor@gosupernova.live</span>
        </div>
      </div>
      <div class="g-right">
        <div class="g-success-box">
          <div class="g-success-badge">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="#137333"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>
            Signed in &amp; saved to .env
          </div>
          <p style="margin:0 0 8px;font-size:15px;font-weight:500;color:#1f1f1f">
            Connected as <span id="user-name">Editor</span>
          </p>
          <p id="close-msg" style="margin:0;font-size:13.5px;line-height:20px;color:#444746">
            Your Zeno session has been automatically saved to your local <code>.env</code> file. This tab will close automatically…
          </p>
        </div>
      </div>
    </div>
  </div>

  <footer class="g-footer">
    <div>English (United States)</div>
    <div class="g-footer-links">
      <a href="/health" target="_blank" rel="noopener">Status</a>
      <a href="/admin">Admin</a>
    </div>
  </footer>
</div>
<script>
const params = new URLSearchParams(window.location.search);
const rawPort = parseInt(params.get('callback_port') || '0', 10);
const callbackPort = (rawPort >= 1024 && rawPort <= 65535) ? rawPort : 8765;
const cliState = params.get('cli_state') || '';
if (params.get('cli') === '1' || cliState) {{
  document.getElementById('cli-banner').style.display = 'inline-flex';
}}

function setLoading(on) {{
  const card = document.getElementById('card');
  if (on) card.classList.add('loading');
  else card.classList.remove('loading');
}}

function showErr(msg) {{
  setLoading(false);
  const err = document.getElementById('err');
  const txt = document.getElementById('err-text');
  const inp = document.getElementById('name');
  txt.textContent = msg;
  err.style.display = 'flex';
  if (inp) inp.classList.add('has-err');
}}

function clearErr() {{
  document.getElementById('err').style.display = 'none';
  const inp = document.getElementById('name');
  if (inp) inp.classList.remove('has-err');
}}

async function completeAuth(d) {{
  setLoading(false);
  const origin = window.location.origin;
  const tok = d.key || d.token || '';
  const displayName = d.name || d.email || 'Editor';
  const email = d.email || '';
  document.getElementById('user-name').textContent = displayName;
  document.getElementById('welcome-title').textContent = 'Welcome, ' + displayName.split(' ')[0];
  document.getElementById('user-email').textContent = email;
  document.getElementById('user-avatar').textContent = (displayName[0] || 'Z').toUpperCase();
  document.getElementById('form').style.display = 'none';
  document.getElementById('result').style.display = 'grid';
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
  setLoading(true);
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
  if (cfg.signup_code_required) document.getElementById('codebox').style.display = 'block';
  if (cfg.google_client_id) {{
    document.getElementById('g-signin-wrap').style.display = 'block';
    const initGsi = () => {{
      if (window.google && google.accounts && google.accounts.id) {{
        google.accounts.id.initialize({{
          client_id: cfg.google_client_id,
          callback: onGoogleSignIn,
          auto_select: true
        }});
        google.accounts.id.renderButton(document.getElementById('g-signin-btn'), {{
          theme: 'outline', size: 'large', shape: 'pill', width: 340, text: 'continue_with', logo_alignment: 'left'
        }});
      }} else {{
        setTimeout(initGsi, 200);
      }}
    }};
    initGsi();
  }}
}}).catch(()=>{{}});

async function make(){{
  clearErr();
  const btn = document.getElementById('go');
  const raw = document.getElementById('name').value.trim();
  if (!raw) {{
    showErr('Enter an email or name');
    return;
  }}
  btn.disabled = true;
  setLoading(true);
  const payload = {{
    name: raw,
    email: raw.includes('@') ? raw : '',
    code: document.getElementById('code').value,
    cli_state: cliState
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
