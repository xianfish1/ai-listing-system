(function excelImportModule(root) {
  'use strict';

  const state = {
    batchId: '',
    products: [],
    selected: new Set(),
    timer: null,
    running: false,
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function setText(id, text) {
    const node = byId(id);
    if (node) node.textContent = text == null ? '' : String(text);
  }

  async function request(path, options) {
    const response = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch (error) {
      payload = {};
    }
    if (!response.ok) {
      const message = payload && (payload.error || payload.message);
      throw new Error(message || `请求失败（HTTP ${response.status}）`);
    }
    return payload;
  }

  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error('读取 Excel 文件失败'));
      reader.onload = () => {
        const text = String(reader.result || '');
        const comma = text.indexOf(',');
        resolve(comma >= 0 ? text.slice(comma + 1) : text);
      };
      reader.readAsDataURL(file);
    });
  }

  function resetPreview() {
    state.batchId = '';
    state.products = [];
    state.selected = new Set();
    state.running = false;
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
    const preview = byId('excel-import-preview');
    if (preview) preview.hidden = true;
    const progress = byId('excel-import-progress');
    if (progress) progress.hidden = true;
    const retry = byId('excel-import-retry-btn');
    if (retry) retry.hidden = true;
    const start = byId('excel-import-start-btn');
    if (start) start.disabled = true;
    setText('excel-import-status', '');
    setText('excel-import-file-name', '');
    setText('excel-import-summary', '');
    const rows = byId('excel-import-rows');
    if (rows) rows.replaceChildren();
    const warnings = byId('excel-import-warnings');
    if (warnings) warnings.replaceChildren();
    const list = byId('excel-import-progress-list');
    if (list) list.replaceChildren();
    const selectAll = byId('excel-import-select-all');
    if (selectAll) {
      selectAll.checked = false;
      selectAll.disabled = true;
    }
    const fileInput = byId('excel-import-file');
    if (fileInput) fileInput.value = '';
  }

  function openExcelImportDialog() {
    resetPreview();
    const dialog = byId('excel-import-dialog');
    if (dialog && !dialog.open) dialog.showModal();
  }

  function statusLabel(product, item) {
    if (!item) {
      return product.existing ? '已存在' : '待导入';
    }
    switch (item.status) {
      case 'pending':
        return '等待';
      case 'running':
        if (item.stage === 'creating') return '创建商品…';
        return `下载图片 ${item.downloaded || 0}/${item.total_images || 0}`;
      case 'succeeded':
        return '已导入';
      case 'skipped':
        return '已存在，跳过';
      case 'failed':
        return `失败：${item.error || '未知错误'}`;
      default:
        return item.status || '';
    }
  }

  function renderRows() {
    const tbody = byId('excel-import-rows');
    if (!tbody) return;
    tbody.replaceChildren();
    for (const product of state.products) {
      const tr = document.createElement('tr');
      tr.dataset.key = product.key || '';

      const selectCell = document.createElement('td');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.dataset.excelImportSelect = product.key || '';
      checkbox.disabled = Boolean(product.existing);
      checkbox.checked = state.selected.has(product.key);
      selectCell.appendChild(checkbox);
      tr.appendChild(selectCell);

      const status = statusLabel(product);
      const values = [
        product.main_sku || '',
        product.title || '',
        `${(product.colors || []).length}`,
        (product.sizes || []).join(' / '),
        `${product.image_count || 0}`,
        (product.source_meta && product.source_meta.store_name) || '',
      ];
      for (const value of values) {
        const td = document.createElement('td');
        td.textContent = value;
        tr.appendChild(td);
      }
      const statusCell = document.createElement('td');
      statusCell.className = 'excel-import-status-cell';
      statusCell.textContent = status;
      tr.appendChild(statusCell);
      tbody.appendChild(tr);
    }
    updateSummary();
  }

  function updateSummary() {
    const products = state.products;
    const existing = products.filter((item) => item.existing).length;
    const selected = state.selected.size;
    setText(
      'excel-import-summary',
      `共 ${products.length} 个商品 · 已存在 ${existing} 个 · 已选 ${selected} 个`,
    );
    const start = byId('excel-import-start-btn');
    if (start) start.disabled = state.running || selected === 0;
    const selectAll = byId('excel-import-select-all');
    if (selectAll) {
      selectAll.disabled = state.running || products.length === 0;
      selectAll.checked = selected > 0 && selected === products.filter((item) => !item.existing).length;
    }
  }

  function renderWarnings(warnings) {
    const list = byId('excel-import-warnings');
    if (!list) return;
    list.replaceChildren();
    for (const warning of warnings || []) {
      const li = document.createElement('li');
      li.textContent = warning;
      list.appendChild(li);
    }
    list.hidden = !(warnings || []).length;
  }

  async function onFileSelected(file) {
    if (!file) return;
    setText('excel-import-file-name', file.name || '');
    setText('excel-import-status', '正在解析 Excel…');
    try {
      const fileBase64 = await fileToBase64(file);
      const result = await request('/api/excel-import/preview', {
        method: 'POST',
        body: JSON.stringify({ file_base64: fileBase64, file_name: file.name || '' }),
      });
      state.batchId = result.batch_id || '';
      state.products = result.products || [];
      state.selected = new Set(
        state.products.filter((item) => !item.existing).map((item) => item.key),
      );
      const preview = byId('excel-import-preview');
      if (preview) preview.hidden = false;
      renderRows();
      renderWarnings(result.warnings || []);
      setText(
        'excel-import-status',
        `解析完成：${state.products.length} 个商品，图片会在点「开始导入」后才下载。`,
      );
    } catch (error) {
      setText('excel-import-status', error.message || String(error));
      const preview = byId('excel-import-preview');
      if (preview) preview.hidden = true;
    }
  }

  function renderProgress(task) {
    const list = byId('excel-import-progress-list');
    if (!list) return;
    const byKey = new Map((task.items || []).map((item) => [item.key, item]));
    list.replaceChildren();
    for (const product of state.products) {
      const item = byKey.get(product.key);
      if (!item) continue;
      const li = document.createElement('li');
      li.className = `excel-import-progress-item excel-import-progress-${item.status}`;
      li.dataset.key = item.key || '';
      const name = document.createElement('span');
      name.className = 'excel-import-progress-name';
      name.textContent = `${item.main_sku || product.main_sku} · ${item.title || product.title || ''}`.slice(0, 90);
      const status = document.createElement('span');
      status.className = 'excel-import-progress-status';
      status.textContent = statusLabel(product, item);
      li.append(name, status);
      list.appendChild(li);
    }
    const summary = task.summary || {};
    setText(
      'excel-import-progress-summary',
      `已完成 ${summary.completed || 0}/${summary.total || 0} · 成功 ${summary.succeeded || 0} · 跳过 ${summary.skipped || 0} · 失败 ${summary.failed || 0}`,
    );
  }

  function stopPolling() {
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
  }

  function pollTask() {
    stopPolling();
    state.running = true;
    updateSummary();
    const tick = async () => {
      try {
        const task = await request(`/api/excel-import/tasks/${encodeURIComponent(state.batchId)}`);
        renderProgress(task);
        if (task.status === 'running') return;
        stopPolling();
        state.running = false;
        const summary = task.summary || {};
        setText(
          'excel-import-status',
          `导入结束：成功 ${summary.succeeded || 0}，跳过 ${summary.skipped || 0}，失败 ${summary.failed || 0}。`,
        );
        const retry = byId('excel-import-retry-btn');
        if (retry) retry.hidden = !(summary.failed > 0);
        updateSummary();
        const importedSkus = (task.items || [])
          .filter((item) => item.status === 'succeeded')
          .map((item) => item.main_sku);
        try {
          if (typeof window.refreshBaseProductsAfterImport === 'function') {
            await window.refreshBaseProductsAfterImport(importedSkus);
          } else if (typeof window.loadBaseProducts === 'function') {
            await window.loadBaseProducts();
          }
        } catch (error) {
          setText(
            'excel-import-status',
            `${byId('excel-import-status')?.textContent || ''}（列表刷新失败，请手动刷新页面）`,
          );
        }
      } catch (error) {
        stopPolling();
        state.running = false;
        updateSummary();
        setText('excel-import-status', error.message || String(error));
      }
    };
    tick();
    state.timer = setInterval(tick, 800);
  }

  async function startImport(keys) {
    if (!state.batchId) return;
    const selected = keys || Array.from(state.selected);
    if (!selected.length) {
      setText('excel-import-status', '请先勾选要导入的商品');
      return;
    }
    setText('excel-import-status', '已开始导入：正在下载图片并创建基础商品…');
    const progress = byId('excel-import-progress');
    if (progress) progress.hidden = false;
    const retry = byId('excel-import-retry-btn');
    if (retry) retry.hidden = true;
    try {
      await request('/api/excel-import/start', {
        method: 'POST',
        body: JSON.stringify({ batch_id: state.batchId, keys: selected }),
      });
      pollTask();
    } catch (error) {
      setText('excel-import-status', error.message || String(error));
    }
  }

  function retryFailed() {
    const list = byId('excel-import-progress-list');
    const failed = list
      ? Array.from(list.querySelectorAll('.excel-import-progress-failed'))
        .map((node) => node.dataset.key)
        .filter(Boolean)
      : [];
    if (!failed.length) {
      setText('excel-import-status', '没有失败项需要重试');
      return;
    }
    startImport(failed);
  }

  function toggleSelect(key, checked) {
    if (!key) return;
    if (checked) state.selected.add(key);
    else state.selected.delete(key);
    updateSummary();
  }

  function init() {
    const fileInput = byId('excel-import-file');
    if (fileInput) {
      fileInput.addEventListener('change', () => {
        // Capture the File before reset() clears the input element.
        const file = fileInput.files && fileInput.files[0];
        resetPreview();
        if (file) onFileSelected(file);
      });
    }
    const tbody = byId('excel-import-rows');
    if (tbody) {
      tbody.addEventListener('change', (event) => {
        const target = event.target;
        if (target instanceof HTMLInputElement && target.dataset.excelImportSelect !== undefined) {
          toggleSelect(target.dataset.excelImportSelect, target.checked);
        }
      });
    }
    const selectAll = byId('excel-import-select-all');
    if (selectAll) {
      selectAll.addEventListener('change', () => {
        state.selected = selectAll.checked
          ? new Set(state.products.filter((item) => !item.existing).map((item) => item.key))
          : new Set();
        renderRows();
      });
    }
    const start = byId('excel-import-start-btn');
    if (start) start.addEventListener('click', () => startImport());
    const retry = byId('excel-import-retry-btn');
    if (retry) retry.addEventListener('click', retryFailed);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  root.ExcelImport = {
    open: openExcelImportDialog,
    reset: resetPreview,
  };
}(typeof globalThis !== 'undefined' ? globalThis : this));
