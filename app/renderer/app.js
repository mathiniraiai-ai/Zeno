// Minimal Zeno desktop frontend: displays centered Zeno logo, brand name, and version number while preserving Google Sign-In and Cloud OTA auto-updates.
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const state = {
  info: null,
  signingIn: false,
  signInError: '',
  updating: false,
  updateModalOpen: false,
  updateError: '',
  updateDoneMsg: '',
  autoTried: null,
  autoSetupTriggered: false,
};

function renderVersion() {
  const el = $('#ver');
  if (!el) return;
  const v = state.info && state.info.version ? String(state.info.version).trim() : '';
  el.textContent = v ? `v${v.replace(/^v/i, '')}` : '';
}

function maybeAutoStartEngineSetup() {
  if (!state.info || !state.info.signed_in || !state.info.root || state.info.engine_ready) return;
  const key = `zenoAutoSetup:${state.info.root}`;
  try {
    if (sessionStorage.getItem(key) === '1') return;
    sessionStorage.setItem(key, '1');
  } catch (e) {
    if (state.autoSetupTriggered) return;
    state.autoSetupTriggered = true;
  }
  window.zeno.runSetup();
}

function paintSignIn() {
  let el = $('#signin');
  const need = !state.info || !state.info.signed_in;
  if (!need) {
    if (el) el.remove();
    return;
  }
  if (!el) {
    el = document.createElement('div');
    el.id = 'signin';
    document.body.appendChild(el);
  }
  const sig = `${state.signingIn ? 1 : 0}|${state.signInError || ''}`;
  if (el.dataset.sig === sig) return;
  el.dataset.sig = sig;
  const gIcon = '<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M21.35 11.1h-9.18v2.92h5.27c-.23 1.38-1.56 4.05-5.27 4.05-3.17 0-5.76-2.63-5.76-5.87s2.59-5.87 5.76-5.87c1.81 0 3.02.77 3.71 1.43l2.53-2.44C16.79 3.8 14.69 2.9 12.17 2.9 6.95 2.9 2.73 7.13 2.73 12.2s4.22 9.3 9.44 9.3c5.45 0 9.06-3.83 9.06-9.22 0-.62-.07-1.09-.15-1.58z"/></svg>';
  el.innerHTML = `<div class="si-drag"></div><div class="upd-card si-card">
    <div class="si-logo"><i></i><span>Zeno</span></div>
    <h3>Sign in to Zeno</h3>
    <div class="upd-sub">Sign in with your Google account to activate Zeno and automatic Cloud updates.</div>
    ${state.signInError ? `<div class="upd-err">${esc(state.signInError)}</div>` : ''}
    <div class="upd-acts"><button class="primary si-btn" id="doSignIn" ${state.signingIn ? 'disabled' : ''}>${gIcon}<span>${state.signingIn ? 'Waiting for Google in your browser…' : 'Continue with Google'}</span></button></div>
    <div class="si-hint">${state.signingIn ? 'Complete Google sign-in with your <b class="si-dom">@gosupernova.live</b> account in the browser tab that opened.' : 'Only <b class="si-dom">@gosupernova.live</b> accounts can sign in.'}</div>
  </div>`;
}

async function doSignIn() {
  if (state.signingIn) return;
  state.signingIn = true;
  state.signInError = '';
  paintSignIn();
  const r = await window.zeno.signIn();
  state.signingIn = false;
  if (r && r.ok) {
    state.signInError = '';
    if (r.bootstrapped) {
      window.zeno.reload();
      return;
    }
    await loadInfo(false);
    loadInfo('force');
  } else {
    state.signInError = (r && r.text) || 'Sign-in failed.';
  }
  paintSignIn();
}

function paintUpdateModal() {
  let el = $('#updModal');
  const u = (state.info && state.info.update) || {};
  if (!state.updateModalOpen || (!u.available && !state.updating && !state.updateDoneMsg)) {
    if (el) el.remove();
    return;
  }
  if (!el) {
    el = document.createElement('div');
    el.id = 'updModal';
    document.body.appendChild(el);
  }
  const installerUrl = window.zeno.platform === 'darwin' ? u.dmg_url : (window.zeno.platform === 'win32' ? u.exe_url : '');
  const sizeBadge = u.size_mb ? `<span class="upd-size">${esc(String(u.size_mb))} MB</span>` : '';
  const notesHtml = u.notes ? `<div class="upd-notes">${esc(u.notes)}</div>` : '<div class="upd-notes">New editing engine improvements and fixes are ready from Zeno Cloud.</div>';
  const statusHtml = state.updateError
    ? `<div class="upd-err">${esc(state.updateError)}</div>`
    : (state.updateDoneMsg ? `<div class="upd-ok">${esc(state.updateDoneMsg)}</div>` : '');
  el.innerHTML = `<div class="upd-card">
    <div class="upd-top"><span class="upd-pill">${u.mandatory ? 'REQUIRED UPDATE' : 'NEW UPDATE ARRIVED'}</span>${sizeBadge}</div>
    <h3>Zeno v${esc(u.latest_version || '')} is ready</h3>
    <div class="upd-sub">Current version: v${esc(u.current_version || (state.info && state.info.version) || '1.0.0')}</div>
    ${notesHtml}
    ${statusHtml}
    <div class="upd-acts">
      ${!u.mandatory && !state.updating ? '<button class="btn" id="updLater">Later</button>' : ''}
      ${installerUrl && !state.updating ? `<button class="btn" data-ext-url="${esc(installerUrl)}">Download Installer</button>` : ''}
      <button class="primary" id="doCloudUpdate" ${state.updating ? 'disabled' : ''}>${state.updating ? 'Downloading from Zeno Cloud…' : 'Update & Reload Now'}</button>
    </div>
  </div>`;
}

async function triggerCloudUpdate() {
  if (state.updating) return;
  state.updating = true;
  state.updateError = '';
  state.updateDoneMsg = '';
  paintUpdateModal();
  const r = await window.zeno.update();
  state.updating = false;
  if (r && r.ok) {
    state.updateDoneMsg = `Updated to v${r.version || ((state.info && state.info.update && state.info.update.latest_version) || '')} · Reloading Zeno…`;
    paintUpdateModal();
    setTimeout(() => { window.zeno.reload(); }, 900);
  } else {
    state.updateError = (r && r.text) || 'Could not download update from Zeno Cloud. Please check your connection.';
    paintUpdateModal();
    setTimeout(() => { state.autoTried = null; }, 300000);
  }
}

async function loadInfo(check) {
  const i = await window.zeno.info(check);
  if (i && !i.error) {
    state.info = i;
    renderVersion();
    paintSignIn();
    maybeAutoStartEngineSetup();
    const u = i.update;
    if (u && u.available) {
      const key = u.bundle_sha256 || u.latest_version || 'upd';
      if (!state.updating && state.autoTried !== key) {
        state.autoTried = key;
        state.updateModalOpen = true;
        paintUpdateModal();
        triggerCloudUpdate();
      }
    } else if (state.updateModalOpen && !state.updating) {
      state.updateModalOpen = false;
      paintUpdateModal();
    }
  }
}

document.addEventListener('click', (e) => {
  const ext = e.target.closest('[data-ext-url]');
  if (ext) { window.zeno.openExternal(ext.dataset.extUrl); return; }
  if (e.target.closest('#updLater')) {
    state.updateModalOpen = false;
    paintUpdateModal();
    return;
  }
  if (e.target.closest('#doCloudUpdate')) { triggerCloudUpdate(); return; }
  if (e.target.closest('#doSignIn')) { doSignIn(); return; }
});

(function setupLogoFallback() {
  const img = $('#logoImg');
  const wrap = $('#logoWrap');
  if (img && wrap) {
    img.addEventListener('error', () => { wrap.classList.add('fallback'); });
  }
})();

(async function start() {
  paintSignIn();
  await loadInfo(false);
  loadInfo(true);
  setInterval(() => { if (state.info && state.info.signed_in && !state.info.engine_ready) loadInfo(false); }, 4000);
  setInterval(() => loadInfo('force'), 180000);
})();
