// popup.js — manual activation UX for the extension. Users pop this open when
// they didn't come in through the web UI's "打开马帮采集页" (which sets the
// session via URL hash) and want to paste a run_id + API base directly.

const STORAGE_KEY = 'aiListingSession';

async function renderCurrent() {
  const stored = await chrome.storage.local.get(STORAGE_KEY);
  const cur = stored[STORAGE_KEY];
  const box = document.getElementById('session');
  if (cur && cur.runId) {
    box.textContent = `已激活：run=${cur.runId}  api=${cur.api || '—'}`;
    document.getElementById('run-id').value = cur.runId;
    document.getElementById('api').value = cur.api || '';
  } else {
    box.textContent = '';
  }
}

async function saveSession() {
  const runId = document.getElementById('run-id').value.trim();
  const api = document.getElementById('api').value.trim();
  const status = document.getElementById('status');
  if (!runId || !api) {
    status.textContent = '请填写 Run ID 和 API 地址';
    return;
  }
  const session = { runId, api, expiresAt: Date.now() + 3600_000 };
  await chrome.storage.local.set({ [STORAGE_KEY]: session });
  status.textContent = '已激活，回到马帮页面点悬浮球采集';
  renderCurrent();
}

async function clearSession() {
  await chrome.storage.local.remove(STORAGE_KEY);
  document.getElementById('run-id').value = '';
  document.getElementById('api').value = '';
  document.getElementById('status').textContent = '已清除会话';
  renderCurrent();
}

document.getElementById('save-btn').addEventListener('click', saveSession);
document.getElementById('clear-btn').addEventListener('click', clearSession);
renderCurrent();
