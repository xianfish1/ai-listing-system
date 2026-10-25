// content.js — runs in an isolated world on every mabangerp.com page, in every frame
// (all_frames: true). The Mabang product detail form lives inside a cross-origin
// publish.mabangerp.com iframe under the www.mabangerp.com app shell, so a capture that
// only scans the top frame can never reach the Vue tree. This script:
//
//   top frame  — session + FAB. On FAB click it posts ONE capture request that floods
//                down the whole frame tree, then collects the frame results and POSTs
//                the winner (the capture that resolved a store id) to the API.
//   every frame — injects page_capture.js into ITS OWN document's MAIN world (so Vue
//                __vue__ inside each iframe is visible), forwards capture requests to
//                descendant frames, runs the capture for its own document, and relays
//                capture results up to window.top.
//
// The FAB stays hidden unless a session is active — matches the "平时不启动" expectation.

const STORAGE_KEY = 'aiListingSession';
const HASH_KEY = 'ai_listing_run_id';
const API_HASH_KEY = 'api';
const REQUEST_TYPE = '__AI_LISTING_CAPTURE_REQUEST__';
const RESULT_TYPE = '__AI_LISTING_CAPTURE_RESULT__';
const READY_TYPE = '__AI_LISTING_CAPTURE_READY__';

const isTopFrame = () => window.top === window;

let session = null;                 // top frame only: { runId, api, expiresAt }
let fabEl = null;
let statusEl = null;
let captureReady = false;           // this frame's page_capture.js bridge is live
const seenRequestIds = new Set();   // dedup the recursive request flood
let activeCollector = null;         // top frame only: { requestId, results, finish }

function trustedOrigin(origin) {
  try {
    return /(^|\.)mabangerp\.com$/.test(new URL(String(origin)).hostname);
  } catch {
    return false;
  }
}

function postMessageTo(windowTarget, message) {
  try { windowTarget.postMessage(message, '*'); } catch { /* some cross-origin frames drop */ }
}

// ---------- session lifecycle (top frame only) ----------

async function readHash() {
  // Hash arrives as "#ai_listing_run_id=xxx&api=http://10.144.0.3:8787" (or "&"
  // starting) — parse both `?` and hash-fragment style.
  const raw = location.hash.replace(/^#/, '');
  if (!raw) return null;
  const params = new URLSearchParams(raw);
  const runId = params.get(HASH_KEY);
  const api = params.get(API_HASH_KEY);
  if (!runId || !api) return null;
  return { runId, api };
}

async function loadSession() {
  const fromHash = await readHash();
  if (fromHash) {
    session = { ...fromHash, expiresAt: Date.now() + 3600_000 };
    await chrome.storage.local.set({ [STORAGE_KEY]: session });
    // Strip the hash so a refresh doesn't leak the run_id in the URL bar.
    try { history.replaceState(null, '', location.pathname + location.search); } catch {}
    return session;
  }
  const stored = await chrome.storage.local.get(STORAGE_KEY);
  const cached = stored[STORAGE_KEY];
  if (cached && cached.runId && cached.api && (!cached.expiresAt || cached.expiresAt > Date.now())) {
    session = cached;
    return session;
  }
  session = null;
  return null;
}

async function clearSession() {
  session = null;
  await chrome.storage.local.remove(STORAGE_KEY);
}

// ---------- floating action button (top frame only) ----------

function ensureFab() {
  if (fabEl) return fabEl;
  const host = document.createElement('div');
  host.id = 'ai-listing-fab-host';
  host.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:2147483647;';
  const shadow = host.attachShadow({ mode: 'open' });
  shadow.innerHTML = `
    <style>
      .fab { display:flex; flex-direction:column; align-items:flex-end; gap:8px; font-family:-apple-system,Segoe UI,Roboto,sans-serif; }
      .btn { padding:12px 18px; border-radius:999px; border:none; background:#ff8a00; color:#fff; font-weight:600; font-size:14px; cursor:pointer; box-shadow:0 4px 16px rgba(0,0,0,.2); transition:background .15s; }
      .btn:hover { background:#ff9d24; }
      .btn:disabled { background:#aaa; cursor:not-allowed; }
      .btn.success { background:#3ba55c; }
      .btn.error { background:#d64545; }
      .status { padding:6px 12px; background:rgba(30,30,30,.85); color:#fff; font-size:12px; border-radius:6px; max-width:260px; }
      .status:empty { display:none; }
    </style>
    <div class="fab">
      <div class="status" id="ai-listing-fab-status"></div>
      <button class="btn" id="ai-listing-fab-btn">📸 采集当前页</button>
    </div>
  `;
  document.documentElement.appendChild(host);
  fabEl = shadow.getElementById('ai-listing-fab-btn');
  statusEl = shadow.getElementById('ai-listing-fab-status');
  fabEl.addEventListener('click', onFabClick);
  return fabEl;
}

function hideFab() {
  const host = document.getElementById('ai-listing-fab-host');
  if (host) host.remove();
  fabEl = null;
  statusEl = null;
}

function setStatus(text, kind) {
  if (!fabEl) return;
  statusEl.textContent = text || '';
  fabEl.classList.remove('success', 'error');
  if (kind === 'success') fabEl.classList.add('success');
  if (kind === 'error') fabEl.classList.add('error');
}

// ---------- MAIN-world bridge injection (this frame) ----------

function injectCaptureBridge() {
  if (captureReady) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      window.removeEventListener('message', onReady);
      reject(new Error('注入采集脚本超时'));
    }, 5000);
    function onReady(event) {
      if (event.source !== window) return;
      if (event.data && event.data.type === READY_TYPE) {
        clearTimeout(timer);
        window.removeEventListener('message', onReady);
        captureReady = true;
        resolve();
      }
    }
    window.addEventListener('message', onReady);
    const script = document.createElement('script');
    script.src = chrome.runtime.getURL('page_capture.js');
    script.onerror = () => {
      clearTimeout(timer);
      window.removeEventListener('message', onReady);
      reject(new Error('无法注入 page_capture.js — 检查 web_accessible_resources'));
    };
    (document.head || document.documentElement).appendChild(script);
  });
}

// ---------- request flood + result collection ----------

async function handleCaptureRequest(message) {
  if (seenRequestIds.has(message.requestId)) return;
  seenRequestIds.add(message.requestId);
  // Flood to my own child frames; each frame forwards to its children in turn.
  for (let i = 0; i < window.frames.length; i += 1) {
    postMessageTo(window.frames[i], message);
  }
  // Run the capture in THIS document's main world. The bridge posts its result to this
  // frame's window, and handleCaptureResult relays it upward (this frame's own result
  // will come back through the self-repost below).
  try {
    await injectCaptureBridge();
    postMessageTo(window, message);
  } catch (err) {
    // This frame can't capture (bridge failed) — descendants may still answer.
  }
}

function handleCaptureResult(message) {
  if (activeCollector && activeCollector.requestId === message.requestId) {
    const capture = message.ok === true ? (message.capture || null) : null;
    activeCollector.results.push(message);
    const storeId = String((((capture || {}).store_category_state || {}).store || {}).id || '').trim();
    // The editor frame is the one that resolved a store id — stop early on it.
    if (storeId) activeCollector.finish(message);
    return;
  }
  // Not the collector: relay the result up to the top frame (cross-origin allowed).
  if (!isTopFrame()) postMessageTo(window.top, message);
}

window.addEventListener('message', (event) => {
  const message = event.data;
  if (!message || typeof message !== 'object' || typeof message.type !== 'string') return;
  // Own-window events (page_capture.js bridge) always pass; cross-frame floods/relays
  // must come from a mabangerp.com frame.
  if (event.source !== window && !trustedOrigin(event.origin)) return;
  if (message.type === REQUEST_TYPE) {
    handleCaptureRequest(message);
  } else if (message.type === RESULT_TYPE) {
    handleCaptureResult(message);
  }
});

// ---------- capture orchestration (top frame only) ----------

function pickBestCapture(results) {
  const okResults = (results || []).filter((r) => r && r.ok === true);
  if (!okResults.length) return null;
  const storeIdOf = (r) => String((((r.capture || {}).store_category_state || {}).store || {}).id || '').trim();
  return okResults.find((r) => storeIdOf(r)) || okResults[0];
}

function requestCapture(plan) {
  return new Promise((resolve, reject) => {
    const requestId = `cap-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const collector = {
      requestId,
      results: [],
      settled: false,
      timer: 0,
      finish: (winner) => {
        if (collector.settled) return;
        collector.settled = true;
        clearTimeout(collector.timer);
        activeCollector = null;
        if (winner && winner.ok === true) {
          resolve(winner.capture);
        } else if (winner && winner.ok === false) {
          reject(new Error(winner.error || '采集失败'));
        } else {
          const formFound = (collector.results || []).some(
            (r) => r.ok === true && r.capture && r.capture.capture_debug && r.capture.capture_debug.detail_vm === true,
          );
          reject(new Error(formFound
            ? '已在页面找到编辑表单，但未读取到店铺信息'
            : '当前页面未识别到马帮商品编辑表单，请确认已打开马帮商品编辑页（选择店铺后）再点击采集'));
        }
      },
    };
    collector.timer = setTimeout(() => collector.finish(pickBestCapture(collector.results)), 8000);
    activeCollector = collector;
    // ONE post starts the recursive flood: this frame's message handler forwards it to
    // every descendant frame and runs the bridge in its own document.
    postMessageTo(window, { type: REQUEST_TYPE, requestId, plan: plan || {} });
  });
}

// ---------- FAB click handler (top frame only) ----------

async function onFabClick() {
  if (!session) return;
  fabEl.disabled = true;
  setStatus('采集中…');
  try {
    const capture = await requestCapture({});  // plan can stay empty; backend only asserts source
    setStatus('推送到服务器…');
    const url = `${session.api.replace(/\/$/, '')}/api/mabang-page-imports/${encodeURIComponent(session.runId)}/complete`;
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ raw_capture: capture }),
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
    }
    setStatus('√ 已推送', 'success');
    await clearSession();
    setTimeout(hideFab, 2000);
  } catch (err) {
    console.error('[AI Listing] capture failed:', err);
    setStatus(String((err && err.message) || err), 'error');
    fabEl.disabled = false;  // let user retry
  }
}

// ---------- boot (top frame only; subframes only route capture messages) ----------

(async () => {
  if (!isTopFrame()) return;
  await loadSession();
  if (session) ensureFab();
  // Also react to storage updates (e.g. popup manually pastes a new run_id).
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local' || !changes[STORAGE_KEY]) return;
    session = changes[STORAGE_KEY].newValue || null;
    if (session) ensureFab();
    else hideFab();
  });
  // Re-check hash on navigation (SPA route change may swap it in).
  window.addEventListener('hashchange', async () => {
    const before = session;
    await loadSession();
    if (!before && session) ensureFab();
  });
})();
