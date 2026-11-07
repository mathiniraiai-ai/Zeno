// The chat box: a form (script link, Notion name, clips folder) and a conversation with Claude Code, which runs behind it.
// Claude Code is started on this computer; the footage never leaves it. The first run also offers a one-time "Connect Claude" card.
const chat = { open: false, session: null, busy: false, installed: null, connected: null, msgs: [], started: false, form: { script: '', name: '', footage: '' }, skipConnect: false, lastText: '' };

function chatPrompt(f) {
  return `You are the Zeno editor. Read .claude/commands/edit.md and follow it exactly for this video.
Script (Google Doc tab link): ${f.script}
Footage folder: ${f.footage}
Notion name: ${f.name}
Rules for this run: the Zeno desktop app is open and shows everything live, so post one short step note before each stage with: python3 scripts/editor_note.py <work folder name> "<what you are doing>". Write plan.edl in layers (cuts first, then zooms, then transitions, then sound effects), then run --check-plan. Do NOT render. When the plan passes, stop and say "Plan ready" with the plain-words summary; the editor presses Render in the app.`;
}

async function checkClaude() {
  try { const s = await window.zeno.claudeStatus(); chat.installed = s.installed; chat.connected = s.connected; } catch (e) { chat.installed = false; chat.connected = false; }
  paintConnect(); const el = document.getElementById('cstat'); if (el) setStat(el);
}
function setStat(el) {
  el.className = 'cstat ' + (chat.connected ? 'ok' : 'bad');
  el.textContent = chat.installed === false ? 'Claude Code not found' : chat.connected ? 'Claude connected' : 'Claude not connected';
}
// one-time card, only while the connection is missing
function paintConnect() {
  let box = document.getElementById('conn');
  const want = chat.installed !== null && !chat.connected && !chat.skipConnect && !chat.open;
  if (!want) { if (box) box.remove(); return; }
  if (box) return;
  box = document.createElement('div'); box.id = 'conn';
  box.innerHTML = `<div class="askcard"><h3>Connect Claude</h3>
    <div class="muted">${chat.installed === false ? 'Claude Code was not found on this computer. Install Claude Code first, then open Zeno again. You can still copy the message and paste it into Claude yourself.' : 'Zeno needs to be connected to Claude Code once. One click adds it for you. Nothing leaves this computer.'}</div>
    <div class="askact"><button class="askno" data-conn-later="1">Not now</button>${chat.installed === false ? '' : '<button class="primary askyes" data-conn-go="1">Connect</button>'}</div><div id="connmsg" class="muted"></div></div>`;
  document.body.appendChild(box);
}
function openChat() {
  chat.open = true; paintConnect(); paintChat(); checkClaude();
}
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
      <div class="fact"><button class="askno" data-chat-copy="1">Copy message for Claude</button><button class="primary askyes" data-chat-start="1">Start edit</button></div>
      <div id="ferr" class="ferr"></div>
    </div>
    <div class="cmsgs" id="cmsgs"></div>
    <div class="cin" id="cin"><input id="cmsg" placeholder="Message Claude…"><button class="primary" data-chat-send="1">Send</button><button class="btn" data-chat-stop="1">Stop</button></div>
  </div>`;
  document.body.appendChild(box);
  ['script', 'name', 'footage'].forEach((k) => { const el = document.getElementById('f_' + k); el.value = chat.form[k] || ''; el.addEventListener('input', () => { chat.form[k] = el.value; }); });
  document.getElementById('cmsg').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') sendChatInput(); });
  setStat(document.getElementById('cstat')); refreshChat();
  const list = document.getElementById('cmsgs'); chat.msgs.forEach((m) => list.appendChild(msgNode(m))); list.scrollTop = list.scrollHeight;
}
function refreshChat() {
  const f = document.getElementById('cform'), m = document.getElementById('cmsgs'), i = document.getElementById('cin'); if (!f) return;
  f.style.display = chat.started ? 'none' : ''; m.style.display = chat.started ? '' : 'none'; i.style.display = chat.started ? '' : 'none';
  i.classList.toggle('busy', chat.busy);
}
function msgNode(m) {
  const d = document.createElement('div'); d.className = 'cm ' + m.who;
  d.textContent = m.who === 'tool' ? '▸ ' + m.text : m.text; return d;
}
function addMsg(who, text) {
  const m = { who, text }; chat.msgs.push(m);
  const list = document.getElementById('cmsgs'); if (list) { list.appendChild(msgNode(m)); list.scrollTop = list.scrollHeight; }
}
async function startChat() {
  const f = chat.form, err = document.getElementById('ferr');
  const bad = !f.script.trim() ? 'Paste the script link.' : (/docs\.google\.com/.test(f.script) && !/tab=/.test(f.script)) ? 'The Google Doc link must end in ?tab=… (one tab).' : !f.name.trim() ? 'Type the Notion name.' : !f.footage.trim() ? 'Choose the clips folder.' : '';
  if (bad) { err.textContent = bad; return; }
  if (chat.installed === false) { err.textContent = 'Claude Code was not found on this computer. Use "Copy message for Claude" instead.'; return; }
  err.textContent = ''; chat.started = true; refreshChat();
  addMsg('user', `Edit "${f.name}"\nScript: ${f.script}\nClips: ${f.footage}`);
  await sendToClaude(chatPrompt(f));
}
async function sendToClaude(text) {
  chat.busy = true; refreshChat();
  const r = await window.zeno.chatSend(text, chat.session);
  if (!r || !r.ok) { chat.busy = false; refreshChat(); addMsg('error', (r && r.text) || 'Could not start Claude.'); }
}
function sendChatInput() {
  const el = document.getElementById('cmsg'); const t = el && el.value.trim(); if (!t || chat.busy) return;
  el.value = ''; addMsg('user', t); sendToClaude(t);
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
  if (t.closest('#newEdit')) { openChat(); e.stopImmediatePropagation(); return; }
  if (t.closest('[data-chat-x]')) { if (chat.busy) window.zeno.chatStop(); chat.open = false; chat.started = false; chat.msgs = []; chat.session = null; chat.busy = false; paintChat(); paintConnect(); return; }
  if (t.closest('[data-chat-hide]')) { chat.open = false; paintChat(); return; }
  if (t.closest('[data-pick]')) { const p = await window.zeno.pickFolder(); if (p) { chat.form.footage = p; const el = document.getElementById('f_footage'); if (el) el.value = p; } return; }
  if (t.closest('[data-chat-start]')) { startChat(); return; }
  if (t.closest('[data-chat-send]')) { sendChatInput(); return; }
  if (t.closest('[data-chat-stop]')) { window.zeno.chatStop(); return; }
  if (t.closest('[data-chat-copy]')) { const f = chat.form; await window.zeno.copy(chatPrompt(f)); const err = document.getElementById('ferr'); if (err) err.textContent = 'Copied. Paste it into Claude Desktop or Antigravity.'; return; }
  if (t.closest('[data-conn-later]')) { chat.skipConnect = true; paintConnect(); return; }
  if (t.closest('[data-conn-go]')) {
    const msg = document.getElementById('connmsg'); if (msg) msg.textContent = 'Connecting…';
    const r = await window.zeno.claudeConnect(); await checkClaude();
    if (!chat.connected) { const m2 = document.getElementById('connmsg'); if (m2) m2.textContent = (r && r.text) || 'Could not connect.'; }
    return;
  }
}, true);
window.addEventListener('load', () => setTimeout(checkClaude, 800));
