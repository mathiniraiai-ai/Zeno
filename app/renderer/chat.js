// The chat box: a form (script link, Notion name, clips folder, optional instructions) and a conversation with Claude Code, which runs behind it.
// Claude Code is started on this computer; the footage never leaves it. The sidebar pill shows the Claude account and connector, and
// a click on it opens the sign-in (or the connect card) when something is missing.
const OFFICE_DOMAIN = '@gosupernova.live';
const chat = { open: false, session: null, busy: false, acct: null, msgs: [], started: false, form: { script: '', name: '', footage: '', extra: '' }, skipConnect: false, lastText: '', waiting: false };
const QUICK = ['What is the status of this edit?', 'Explain the plan in plain words', 'What did the checks say?', 'Make the hook faster'];

function chatPrompt(f) {
  return `You are the Zeno editor. Read .claude/commands/edit.md and follow it exactly for this video.
Script (Google Doc tab link): ${f.script}
Footage folder: ${f.footage}
Notion name: ${f.name}
${f.extra && f.extra.trim() ? `Extra instructions from the editor: ${f.extra.trim()}\n` : ''}Rules for this run: the Zeno desktop app is open and shows everything live, so post one short step note before each stage with: python3 scripts/editor_note.py <work folder name> "<what you are doing>". Write plan.edl in layers (cuts first, then zooms, then transitions, then sound effects${/subtitle/i.test(f.extra || '') ? ', then #subs auto' : ''}), then run --check-plan. Do NOT render. When the plan passes, stop and say "Plan ready" with the plain-words summary; the editor presses Render in the app.`;
}

// the state of the Claude side: not installed, signed out, wrong account, connector missing, or fine
function claudeState() {
  const a = chat.acct; if (!a) return 'checking';
  if (!a.installed) return 'missing';
  if (!a.loggedIn) return 'signedout';
  if (!String(a.email || '').toLowerCase().endsWith(OFFICE_DOMAIN)) return 'wrong';
  if (!a.connected) return 'connect';
  return 'ok';
}
const PILL = { checking: ['Claude…', ''], missing: ['Claude Code missing', 'bad'], signedout: ['Sign in to Claude', 'bad'], wrong: ['Wrong Claude account', 'bad'], connect: ['Connect Claude', 'bad'], ok: ['Claude connected', 'ok'] };

async function checkClaude() {
  try { chat.acct = await window.zeno.claudeStatus(); } catch (e) { chat.acct = { installed: false }; }
  paintPill(); paintConnect(); const el = document.getElementById('cstat'); if (el) setStat(el);
}
function paintPill() {
  const el = document.getElementById('claudeStat'); if (!el) return;
  const [txt, cls] = PILL[claudeState()]; el.textContent = txt; el.className = 'cpill ' + cls;
  el.title = chat.acct && chat.acct.email ? `Signed in as ${chat.acct.email}` : 'Claude connection';
}
function setStat(el) { const [txt, cls] = PILL[claudeState()]; el.className = 'cstat ' + cls; el.textContent = txt; }

// the card: sign-in, wrong account, connector or the account details, depending on what is missing
function cardHtml(st, full) {
  const a = chat.acct || {}, mail = a.email ? `<b>${esc(a.email)}</b>` : '';
  if (st === 'missing') return `<h3>Claude Code not found</h3><div class="muted">Install Claude Code on this computer, then open Zeno again. You can still use “Copy message for Claude” and paste it into Claude yourself.</div><div class="askact"><button class="askno" data-conn-later="1">Close</button></div>`;
  if (st === 'signedout') return `<h3>Sign in to Claude</h3><div class="muted">Use your Supernova account (${OFFICE_DOMAIN}). Your browser opens to sign in; come back here when you are done.</div><div class="askact"><button class="askno" data-conn-later="1">Not now</button><button class="primary askyes" data-login="1">Sign in</button></div><div id="connmsg" class="muted"></div>`;
  if (st === 'wrong') return `<h3>Use your Supernova account</h3><div class="muted">Claude is signed in as ${mail}. Zeno needs a ${OFFICE_DOMAIN} account.</div><div class="askact"><button class="askno" data-conn-later="1">Not now</button><button class="primary askyes" data-login-switch="1">Switch account</button></div><div id="connmsg" class="muted"></div>`;
  if (st === 'connect') return `<h3>Connect Claude</h3><div class="muted">Signed in as ${mail}. Zeno needs to be connected to Claude Code once. One click adds it for you. Nothing leaves this computer.</div><div class="askact"><button class="askno" data-conn-later="1">Not now</button><button class="primary askyes" data-conn-go="1">Connect</button></div><div id="connmsg" class="muted"></div>`;
  if (st === 'ok' && full) return `<h3>Claude is connected</h3><div class="askrows"><div>Account <b>${esc(a.email)}</b></div><div>Plan <b>${esc(a.plan || '—')}</b></div><div>Zeno connector <b>on</b></div></div><div class="askact"><button class="askno" data-login-switch="1">Switch account</button><button class="primary askyes" data-conn-later="1">Close</button></div>`;
  return '';
}
function paintConnect(force) {
  let box = document.getElementById('conn');
  const st = claudeState();
  const want = force ? true : (st !== 'checking' && st !== 'ok' && !chat.skipConnect && !chat.open);
  const body = want ? cardHtml(st, !!force) : '';
  if (!want || !body) { if (box) box.remove(); return; }
  if (!box) { box = document.createElement('div'); box.id = 'conn'; document.body.appendChild(box); }
  box.innerHTML = `<div class="askcard">${body}</div>`;
}
async function waitForSignIn() {
  if (chat.waiting) return; chat.waiting = true;
  for (let i = 0; i < 45 && claudeState() !== 'ok' && claudeState() !== 'connect'; i++) { await new Promise((r) => setTimeout(r, 4000)); await checkClaude(); }
  chat.waiting = false;
}
function openChat() { chat.open = true; paintConnect(); paintChat(); checkClaude(); }
function paintChat() {
  let box = document.getElementById('chat');
  if (!chat.open) { if (box) box.remove(); return; }
  if (box) return;
  box = document.createElement('div'); box.id = 'chat';
  box.innerHTML = `<div class="chatcard">
    <div class="chead"><b>New edit</b><span id="cstat" class="cstat"></span><span class="cx"><button data-chat-hide="1" title="Hide (Claude keeps working)">Hide</button><button data-chat-x="1" title="Close">×</button></span></div>
    <div class="cform" id="cform">
      <label>Script link (Google Doc tab)<input id="f_script" placeholder="https://docs.google.com/document/d/…?tab=t.…"></label>
      <label>Notion name<input id="f_name" placeholder="Name of the video in Notion"></label>
      <label>Clips folder<span class="frow"><input id="f_footage" placeholder="Choose the folder with the raw clips"><button class="btn" data-pick="1">Choose…</button></span></label>
      <label>Instructions for Claude (optional)<textarea id="f_extra" rows="3" placeholder="For example: add subtitles. Keep the split completely still. Make the hook faster."></textarea></label>
      <div class="fact"><button class="askno" data-chat-copy="1">Copy message for Claude</button><button class="primary askyes" data-chat-start="1">Start edit</button></div>
      <div id="ferr" class="ferr"></div>
    </div>
    <div class="cmsgs" id="cmsgs"></div>
    <div class="cchips" id="cchips">${QUICK.map((q) => `<button data-quick="${esc(q)}">${esc(q)}</button>`).join('')}</div>
    <div class="cin" id="cin"><input id="cmsg" placeholder="Message Claude…"><button class="primary" data-chat-send="1">Send</button><button class="btn" data-chat-stop="1">Stop</button></div>
  </div>`;
  document.body.appendChild(box);
  ['script', 'name', 'footage', 'extra'].forEach((k) => { const el = document.getElementById('f_' + k); el.value = chat.form[k] || ''; el.addEventListener('input', () => { chat.form[k] = el.value; }); });
  document.getElementById('cmsg').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') sendChatInput(); });
  setStat(document.getElementById('cstat')); refreshChat();
  const list = document.getElementById('cmsgs'); chat.msgs.forEach((m) => list.appendChild(msgNode(m))); list.scrollTop = list.scrollHeight;
}
function refreshChat() {
  const f = document.getElementById('cform'), m = document.getElementById('cmsgs'), i = document.getElementById('cin'), c = document.getElementById('cchips'); if (!f) return;
  f.style.display = chat.started ? 'none' : ''; m.style.display = chat.started ? '' : 'none'; i.style.display = chat.started ? '' : 'none'; c.style.display = chat.started ? '' : 'none';
  i.classList.toggle('busy', chat.busy);
}
function msgNode(m) { const d = document.createElement('div'); d.className = 'cm ' + m.who; d.textContent = m.who === 'tool' ? '▸ ' + m.text : m.text; return d; }
function addMsg(who, text) {
  const m = { who, text }; chat.msgs.push(m);
  const list = document.getElementById('cmsgs'); if (list) { list.appendChild(msgNode(m)); list.scrollTop = list.scrollHeight; }
}
async function startChat() {
  const f = chat.form, err = document.getElementById('ferr');
  const bad = !f.script.trim() ? 'Paste the script link.' : (/docs\.google\.com/.test(f.script) && !/tab=/.test(f.script)) ? 'The Google Doc link must end in ?tab=… (one tab).' : !f.name.trim() ? 'Type the Notion name.' : !f.footage.trim() ? 'Choose the clips folder.' : '';
  if (bad) { err.textContent = bad; return; }
  const st = claudeState();
  if (st === 'missing') { err.textContent = 'Claude Code was not found on this computer. Use “Copy message for Claude” instead.'; return; }
  if (st === 'signedout' || st === 'wrong') { err.textContent = 'Sign in to Claude with your Supernova account first (click the Claude pill at the bottom left).'; return; }
  err.textContent = ''; chat.started = true; refreshChat();
  addMsg('user', `Edit "${f.name}"\nScript: ${f.script}\nClips: ${f.footage}${f.extra && f.extra.trim() ? '\nInstructions: ' + f.extra.trim() : ''}`);
  await sendToClaude(chatPrompt(f));
}
async function sendToClaude(text) {
  chat.busy = true; refreshChat();
  const r = await window.zeno.chatSend(text, chat.session);
  if (!r || !r.ok) { chat.busy = false; refreshChat(); addMsg('error', (r && r.text) || 'Could not start Claude.'); }
}
function sendChatInput(text) {
  const el = document.getElementById('cmsg'); const t = (text || (el && el.value) || '').trim(); if (!t || chat.busy) return;
  if (el) el.value = ''; addMsg('user', t); sendToClaude(t);
}
if (window.zeno && window.zeno.onChat) window.zeno.onChat((evt) => {
  if (evt.kind === 'session') chat.session = evt.id;
  else if (evt.kind === 'text') { if (evt.text !== chat.lastText) addMsg('claude', evt.text); chat.lastText = evt.text; }
  else if (evt.kind === 'tool') addMsg('tool', `${evt.name}${evt.input ? ' ' + String(evt.input).replace(/\s+/g, ' ').slice(0, 110) : ''}`);
  else if (evt.kind === 'result') { if (evt.session) chat.session = evt.session; }
  else if (evt.kind === 'done') { chat.busy = false; refreshChat(); if (evt.code) addMsg('error', evt.error || 'Claude stopped unexpectedly.'); }
});
document.addEventListener('click', async (e) => {
  const t = e.target;
  if (t.closest('[data-open-chat]')) { openChat(); e.stopImmediatePropagation(); return; }
  if (t.closest('#claudeStat')) { chat.skipConnect = false; const st = claudeState(); paintConnect(st === 'ok' || st === 'checking'); if (st === 'checking') checkClaude(); return; }
  if (t.closest('[data-chat-x]')) { if (chat.busy) window.zeno.chatStop(); chat.open = false; chat.started = false; chat.msgs = []; chat.session = null; chat.busy = false; paintChat(); paintConnect(); return; }
  if (t.closest('[data-chat-hide]')) { chat.open = false; paintChat(); return; }
  if (t.closest('[data-pick]')) { const p = await window.zeno.pickFolder(); if (p) { chat.form.footage = p; const el = document.getElementById('f_footage'); if (el) el.value = p; } return; }
  if (t.closest('[data-chat-start]')) { startChat(); return; }
  if (t.closest('[data-chat-send]')) { sendChatInput(); return; }
  const q = t.closest('[data-quick]'); if (q) { sendChatInput(q.dataset.quick); return; }
  if (t.closest('[data-chat-stop]')) { window.zeno.chatStop(); return; }
  if (t.closest('[data-chat-copy]')) { await window.zeno.copy(chatPrompt(chat.form)); const err = document.getElementById('ferr'); if (err) err.textContent = 'Copied. Paste it into Claude Desktop or Antigravity.'; return; }
  if (t.closest('[data-conn-later]')) { chat.skipConnect = true; paintConnect(); return; }
  if (t.closest('[data-login]') || t.closest('[data-login-switch]')) {
    const sw = !!t.closest('[data-login-switch]'); const msg = document.getElementById('connmsg') || document.querySelector('#conn .askact');
    await window.zeno.claudeLogin({ switch: sw });
    const note = document.createElement('div'); note.className = 'muted'; note.textContent = 'Your browser opened. Sign in there with your Supernova account, then come back.'; if (msg && msg.parentNode) msg.parentNode.appendChild(note);
    waitForSignIn(); return;
  }
  if (t.closest('[data-conn-go]')) {
    const msg = document.getElementById('connmsg'); if (msg) msg.textContent = 'Connecting…';
    const r = await window.zeno.claudeConnect(); await checkClaude();
    if (claudeState() !== 'ok') { const m2 = document.getElementById('connmsg'); if (m2) m2.textContent = (r && r.text) || 'Could not connect.'; }
    return;
  }
}, true);
window.addEventListener('load', () => setTimeout(checkClaude, 800));
