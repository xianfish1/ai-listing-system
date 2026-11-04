/* Shopee 标题工作台：热词批次库 / 标题规则 / 生成预览。
   依赖宿主 app.js 提供的 window.api / window.escapeHtml / window.showOperationFeedback
   （scripts/verify_title_workbench_ui.cjs 用同名桩验证本文件的浏览器契约）。 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (value) => (window.escapeHtml ? window.escapeHtml(String(value ?? "")) : String(value ?? ""));
  const call = (path, options) => window.api(path, options);

  const CATEGORY_LABELS = { tshirt: "T恤", set: "套装", hoodie: "卫衣", dress: "连衣裙", other: "其他", all: "通用" };
  // 2026-11-02 起主口径：男童/女童/男装；kids/women/adult 只用于显示历史数据。
  const AUDIENCE_LABELS = { boys: "男童", girls: "女童", men: "男装", kids: "童装（旧）", women: "女装（旧）", adult: "成人（旧）", all: "通用", unknown: "待分类" };
  const AUDIENCE_OPTIONS = ["boys", "girls", "men"];
  const AUDIENCE_LEGACY = ["kids", "women", "adult"];
  const STATUS_LABELS = { enabled: "启用", disabled: "停用", pending: "待分类" };
  const EVIDENCE_LABELS = {
    PH_OFFICIAL: "官方", PH_MEASURED: "实测", MANUAL: "手动",
    PH_COMPETITOR: "竞品", TEMPLATE_ONLY: "模板",
  };
  const SOURCE_TYPE_LABELS = { official: "官方数据", measured: "实测数据", manual: "手动整理", competitor: "竞品用词", template: "模板词" };
  const POLICY_LABELS = { reference: "参考融入", prefer: "优先采用匹配词", off: "不使用" };
  const RANKING_LABELS = { volume: "搜索量优先", growth: "增长率优先" };
  const STRUCTURE_LABELS = { theme: "主题/图案词", core: "商品核心词", attributes: "属性词", audience: "人群词" };
  const STRUCTURE_ORDER = ["theme", "core", "attributes", "audience"];
  const SOURCE_LABELS = { preview: "生成预览", apply: "采用标题", import: "导入生成" };

  const state = {
    libraries: [], batches: [], libraryId: "", batchId: "",
    rows: [], total: 0, page: 1, pageSize: 50, pages: 1,
    sort: "word", direction: "asc",
    filters: { search: "", category: "", audience: "", status: "", extra: false },
    selected: new Set(),
    rules: [], presets: null, products: [], productsLoaded: false,
    historyProduct: "", previewResult: null,
    importDraft: null,
  };

  let bound = false;

  function localDate(date) {
    const value = date instanceof Date ? date : new Date();
    return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
  }

  function daysSince(iso) {
    if (!iso) return null;
    const then = new Date(`${iso}T00:00:00`);
    if (Number.isNaN(then.getTime())) return null;
    return Math.round((new Date(`${localDate()}T00:00:00`) - then) / 86400000);
  }

  function fmtNum(value) {
    if (value === null || value === undefined || value === "") return "—";
    const num = Number(value);
    return Number.isFinite(num) ? num.toLocaleString("en-US") : esc(value);
  }

  function fmtSigned(value) {
    if (value === null || value === undefined || value === "") return "—";
    const num = Number(value);
    if (!Number.isFinite(num)) return esc(value);
    return `${num > 0 ? "+" : ""}${num.toLocaleString("en-US", { maximumFractionDigits: 2 })}%`;
  }

  function fmtTime(value) {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? esc(value) : date.toLocaleString("zh-CN", { hour12: false });
  }

  function notice(message, isError) {
    const area = $("tkw-notice");
    if (!area) return;
    area.hidden = !message;
    area.textContent = message || "";
    area.classList.toggle("is-error", Boolean(isError));
  }

  function fail(error, label) {
    const text = typeof error === "string" ? error : (error?.message || String(error || ""));
    notice(`${label}：${text}`, true);
    if (window.showOperationFeedback) window.showOperationFeedback(`${label}：${text}`, "error");
  }

  // ---------------- 页签 ----------------

  function activateTab(name) {
    document.querySelectorAll("[data-tkw-tab]").forEach((tab) => {
      const active = tab.dataset.tkwTab === name;
      tab.setAttribute("aria-selected", active ? "true" : "false");
      tab.tabIndex = active ? 0 : -1;
    });
    ["keywords", "rules", "preview"].forEach((panel) => {
      const node = $(`tkw-panel-${panel}`);
      if (node) node.hidden = panel !== name;
    });
  }

  // ---------------- 热词库：词库与批次 ----------------

  async function loadLibraries() {
    const data = await call("/api/keyword-libraries");
    state.libraries = data.items || [];
    if (!state.libraries.some((row) => row.id === state.libraryId)) {
      state.libraryId = state.libraries[0]?.id || "";
      state.batchId = "";
    }
    renderLibraries();
  }

  function renderLibraries() {
    const select = $("tkw-library");
    if (!select) return;
    select.innerHTML = state.libraries.length
      ? state.libraries.map((row) => `<option value="${esc(row.id)}">${esc(row.name)}</option>`).join("")
      : '<option value="">尚无词库</option>';
    select.value = state.libraryId;
  }

  async function loadBatches() {
    const data = await call(`/api/keyword-batches?library_id=${encodeURIComponent(state.libraryId)}`);
    state.batches = data.items || [];
    if (!state.batches.some((row) => row.id === state.batchId)) state.batchId = state.batches[0]?.id || "";
    renderBatches();
  }

  function newestEnabledBatch() {
    return state.batches.find((row) => row.enabled !== false) || null;
  }

  function batchAgeBadge(batch) {
    if (!batch) return "";
    if (!batch.data_date) return '<span class="tkw-badge is-history">日期未知 · 历史参考</span>';
    const age = daysSince(batch.data_date);
    if (age === null) return "";
    return age > 30
      ? `<span class="tkw-badge is-history">距今 ${age} 天 · 历史参考</span>`
      : `<span class="tkw-badge is-current">距今 ${age} 天</span>`;
  }

  function renderBatches() {
    const list = $("tkw-batch-list");
    if (!list) return;
    if (!state.batches.length) {
      list.innerHTML = '<p class="tkw-empty">该词库还没有批次。点右上角「导入 Excel」或「批量粘贴」创建第一批数据。</p>';
      return;
    }
    const current = newestEnabledBatch();
    const groups = new Map();
    for (const batch of state.batches) {
      const month = batch.data_date ? batch.data_date.slice(0, 7) : "未知月份";
      if (!groups.has(month)) groups.set(month, []);
      groups.get(month).push(batch);
    }
    list.innerHTML = [...groups.entries()].map(([month, batches]) => `
      <div class="tkw-month">${esc(month)}</div>
      ${batches.map((batch) => `
        <div class="tkw-batch-item">
          <button type="button" class="tkw-batch-button${batch.id === state.batchId ? " is-selected" : ""}${batch.enabled === false ? " is-disabled" : ""}" data-tkw-action="select-batch" data-id="${esc(batch.id)}">
            <strong>${esc(batch.data_date || "日期未知")}</strong>
            <span>${esc(batch.name || "")} · ${fmtNum(batch.row_count)} 词${current && current.id === batch.id ? ' · <em class="tkw-current-mark">最新启用</em>' : ""}${batch.enabled === false ? " · 已停用" : ""}</span>
          </button>
          <button type="button" class="tkw-batch-toggle" data-tkw-action="toggle-batch" data-id="${esc(batch.id)}" data-enabled="${batch.enabled === false ? "false" : "true"}">${batch.enabled === false ? "启用" : "停用"}</button>
        </div>`).join("")}`).join("");
    renderSummary();
  }

  function renderSummary() {
    const node = $("tkw-batch-summary");
    if (!node) return;
    const batch = state.batches.find((row) => row.id === state.batchId);
    if (!batch) {
      node.innerHTML = '<span class="tkw-muted">选择左侧批次查看数据概况。</span>';
      return;
    }
    node.innerHTML = `
      <div><span class="tkw-summary-label">数据日期</span><span class="tkw-summary-date">${esc(batch.data_date || "未知")}</span></div>
      <div>${batchAgeBadge(batch)}</div>
      <div><span class="tkw-summary-label">关键词</span><strong>${fmtNum(batch.row_count)} 个</strong></div>
      <div class="tkw-summary-detail"><strong>${esc(batch.name || "")}${batch.enabled === false ? " · 已停用" : ""}</strong>
        来源：${esc(batch.source || "—")} · ${esc(SOURCE_TYPE_LABELS[batch.source_type] || batch.source_type || "")}<br>
        统计区间：${esc(batch.period_start || "—")} ~ ${esc(batch.period_end || "—")} · 上传：${esc(fmtTime(batch.uploaded_at).slice(0, 16) || "—")}<br>
        覆盖品类：${(batch.scope_categories || ["all"]).map((c) => esc(CATEGORY_LABELS[c] || c)).join("/")}
        · 人群：${(batch.scope_audiences || ["all"]).map((a) => esc(AUDIENCE_LABELS[a] || a)).join("/")}
      </div>`;
  }

  // ---------------- 热词库：数据表 ----------------

  function rowsQuery(includePage) {
    const params = new URLSearchParams();
    if (state.libraryId) params.set("library_id", state.libraryId);
    if (state.batchId) params.set("batch_id", state.batchId);
    if (state.filters.search) params.set("search", state.filters.search);
    if (state.filters.category) params.set("category", state.filters.category);
    if (state.filters.audience) params.set("audience", state.filters.audience);
    if (state.filters.status) params.set("status", state.filters.status);
    if (state.sort) {
      params.set("sort", state.sort);
      params.set("direction", state.direction);
    }
    if (includePage) {
      params.set("page", String(state.page));
      params.set("page_size", String(state.pageSize));
    }
    return params.toString();
  }

  async function loadRows() {
    const data = await call(`/api/keyword-rows?${rowsQuery(true)}`);
    state.rows = data.items || [];
    state.total = data.total || 0;
    state.page = data.page || 1;
    state.pageSize = data.page_size || state.pageSize;
    state.pages = Math.max(1, Math.ceil(state.total / state.pageSize));
    renderRows();
  }

  const COLUMNS = [
    { field: "word", label: "关键词", cls: "tkw-word" },
    { field: "category", label: "品类" },
    { field: "audience", label: "人群" },
    { field: "search_volume", label: "搜索量", num: true },
    { field: "avg_30d_search_volume", label: "30天日均", num: true },
    { field: "growth_rate_percent", label: "增长率", num: true },
    { field: "product_count", label: "商品数", num: true, extra: true },
    { field: "supply_demand_ratio_percent", label: "供需比", num: true, extra: true },
    { field: "data_date", label: "数据日期" },
    { field: "source", label: "来源" },
    { field: "status", label: "使用状态" },
    { field: "notes", label: "备注", cls: "tkw-notes" },
  ];

  function renderRows() {
    const grid = $("tkw-grid");
    if (!grid) return;
    if (!state.rows.length) {
      grid.innerHTML = '<p class="tkw-empty">没有符合条件的关键词。</p>';
    } else {
      const showExtra = state.filters.extra;
      const columns = COLUMNS.filter((col) => !col.extra || showExtra);
      const arrow = (field) => (state.sort === field ? (state.direction === "desc" ? " ▼" : " ▲") : "");
      const head = columns.map((col) => `<th${col.cls ? ` class="${col.cls}"` : ""}><button type="button" data-tkw-action="sort" data-field="${esc(col.field)}">${esc(col.label)}${arrow(col.field)}</button></th>`).join("");
      const body = state.rows.map((row) => {
        const checked = state.selected.has(row.id) ? " checked" : "";
        const growth = row.growth_rate_percent;
        const growthCls = typeof growth === "number" ? (growth > 0 ? " tkw-positive" : growth < 0 ? " tkw-negative" : "") : "";
        const cells = columns.map((col) => {
          if (col.field === "word") return `<td class="tkw-word">${esc(row.word)}</td>`;
          if (col.field === "category") return `<td>${esc(CATEGORY_LABELS[row.category] || row.category || "—")}</td>`;
          if (col.field === "audience") return `<td>${esc(AUDIENCE_LABELS[row.audience] || row.audience || "—")}</td>`;
          if (col.field === "growth_rate_percent") return `<td class="tkw-number${growthCls}">${fmtSigned(growth)}</td>`;
          if (col.field === "data_date") return `<td>${esc(row.data_date || "未知")}</td>`;
          if (col.field === "source") return `<td title="${esc(row.source || "")}">${esc((row.source || "—").slice(0, 18))}</td>`;
          if (col.field === "status") {
            const cls = row.status === "enabled" ? "is-current" : row.status === "pending" ? "is-pending" : "";
            return `<td><span class="tkw-badge ${cls}">${esc(STATUS_LABELS[row.status] || row.status)}</span></td>`;
          }
          if (col.field === "notes") return `<td class="tkw-notes" title="${esc(row.notes || "")}">${esc(row.notes || "")}</td>`;
          return `<td class="tkw-number">${fmtNum(row[col.field])}</td>`;
        }).join("");
        return `<tr class="${state.selected.has(row.id) ? "is-selected" : ""}"><td class="tkw-row-select"><input type="checkbox" data-tkw-action="select-row" data-id="${esc(row.id)}"${checked} /></td>${cells}<td><button type="button" data-tkw-action="edit-row" data-id="${esc(row.id)}">编辑</button></td></tr>`;
      }).join("");
      grid.innerHTML = `<table class="tkw-table">
        <thead><tr><th class="tkw-row-select"><input type="checkbox" data-tkw-action="select-page" ${state.rows.length && state.rows.every((row) => state.selected.has(row.id)) ? "checked" : ""} /></th>${head}<th></th></tr></thead>
        <tbody>${body}</tbody></table>`;
    }
    const summary = $("tkw-page-summary");
    if (summary) summary.textContent = state.total ? `第 ${state.page}/${state.pages} 页 · 共 ${state.total} 条` : "共 0 条";
    const number = $("tkw-page-number");
    if (number) number.textContent = state.page;
    const prev = $("tkw-prev");
    const next = $("tkw-next");
    if (prev) prev.disabled = state.page <= 1;
    if (next) next.disabled = state.page >= state.pages;
    renderSelection();
  }

  function renderSelection() {
    const label = $("tkw-selected-count");
    if (label) label.textContent = `已选 ${state.selected.size} 项`;
    const bulk = $("tkw-bulk-edit");
    if (bulk) bulk.disabled = state.selected.size === 0;
  }

  function clearSelection() {
    state.selected.clear();
    renderRows();
  }

  async function exportRows() {
    const query = rowsQuery(false);
    window.open(`/api/keyword-workbench/export?${query}`, "_blank");
    notice(`已按当前筛选导出${state.total ? `（${state.total} 条）` : ""}。`);
  }

  // ---------------- 弹窗骨架 ----------------

  function openDialog(html) {
    const dialog = document.createElement("dialog");
    dialog.className = "tkw-dialog";
    dialog.innerHTML = html;
    document.body.appendChild(dialog);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog || event.target.closest("[data-tkw-close]")) closeDialog(dialog);
    });
    dialog.showModal();
    return dialog;
  }

  function closeDialog(dialog) {
    if (!dialog) return;
    try { dialog.close(); } catch { /* already closed */ }
    dialog.remove();
  }

  function dialogHead(title, hint) {
    return `<div class="tkw-dialog-head"><div><h3>${esc(title)}</h3><p>${esc(hint || "")}</p></div><button type="button" data-tkw-close aria-label="关闭">×</button></div>`;
  }

  // ---------------- 导入 / 批量粘贴 ----------------

  function openImportDialog(mode) {
    if (!state.libraryId) {
      notice("请先新建词库，再导入数据。");
      return;
    }
    const isPaste = mode === "paste";
    state.importDraft = { file_base64: "", file_name: "", mapping: null, preview: null };
    const dialog = openDialog(`
      ${dialogHead(isPaste ? "批量粘贴关键词" : "导入关键词 Excel", "上传 → 列映射 → 预览校验 → 确认导入；旧文件不会顶掉更新的数据日期。")}
      <div class="tkw-form-grid">
        ${isPaste ? `<label class="tkw-wide">粘贴表格（第一行为表头，可直接从 Excel 复制）<textarea id="tkw-import-text" placeholder="关键词\t搜索量&#10;boys tee\t200"></textarea></label>`
        : `<label class="tkw-wide">选择文件（.xlsx / .csv / .tsv / .json）<input id="tkw-import-file" type="file" accept=".xlsx,.csv,.tsv,.json" /></label>`}
        <label>批次名称<input id="tkw-import-name" placeholder="例：十月第二周词表" /></label>
        <label>数据来源<input id="tkw-import-source" placeholder="例：站斧后台导出 / 运营整理" /></label>
        <label>来源类型<select id="tkw-import-source-type">
          ${Object.entries(SOURCE_TYPE_LABELS).map(([value, label]) => `<option value="${value}"${value === "measured" ? " selected" : ""}>${label}</option>`).join("")}
        </select></label>
        <label>数据日期（必填）<input id="tkw-import-date" type="date" value="${localDate()}" /></label>
        <label>统计开始日期<input id="tkw-import-period-start" type="date" /></label>
        <label>统计结束日期<input id="tkw-import-period-end" type="date" /></label>
        <label>覆盖品类<select id="tkw-import-scope-category">
          <option value="all">通用（全部品类）</option>
          ${["tshirt", "set", "hoodie", "dress", "other"].map((value) => `<option value="${value}">${CATEGORY_LABELS[value]}</option>`).join("")}
        </select></label>
        <label>覆盖人群<select id="tkw-import-scope-audience">
          <option value="all">通用（全部人群）</option>
          ${AUDIENCE_OPTIONS.map((value) => `<option value="${value}">${AUDIENCE_LABELS[value]}</option>`).join("")}
        </select></label>
      </div>
      <div id="tkw-import-mapping"></div>
      <div id="tkw-import-results"></div>
      <div class="tkw-dialog-footer">
        <button type="button" class="secondary" data-tkw-close>取消</button>
        <button type="button" class="secondary" id="tkw-import-preview">预览校验</button>
        <button type="button" id="tkw-import-confirm" disabled>确认导入</button>
      </div>`);

    const fileInput = dialog.querySelector("#tkw-import-file");
    if (fileInput) {
      fileInput.addEventListener("change", () => {
        const file = fileInput.files && fileInput.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = () => {
          state.importDraft.file_base64 = String(reader.result || "").split(",")[1] || "";
          state.importDraft.file_name = file.name;
          if (!dialog.querySelector("#tkw-import-name").value) {
            dialog.querySelector("#tkw-import-name").value = file.name.replace(/\.[^.]+$/, "");
          }
          notice(`已读取 ${file.name}，点「预览校验」继续。`);
        };
        reader.readAsDataURL(file);
      });
    }
    dialog.addEventListener("click", (event) => {
      if (event.target.id === "tkw-import-preview") void runImportPreview(dialog);
      if (event.target.id === "tkw-import-confirm") void confirmImport(dialog);
    });
  }

  function importPayload(dialog) {
    const draft = state.importDraft || {};
    const payload = {
      library_id: state.libraryId,
      source: dialog.querySelector("#tkw-import-source").value.trim(),
      source_type: dialog.querySelector("#tkw-import-source-type").value,
      data_date: dialog.querySelector("#tkw-import-date").value,
      period_start: dialog.querySelector("#tkw-import-period-start").value,
      period_end: dialog.querySelector("#tkw-import-period-end").value,
      scope_categories: [dialog.querySelector("#tkw-import-scope-category").value],
      scope_audiences: [dialog.querySelector("#tkw-import-scope-audience").value],
    };
    const name = dialog.querySelector("#tkw-import-name").value.trim();
    if (name) payload.name = name;
    if (draft.file_base64) {
      payload.file_base64 = draft.file_base64;
      payload.file_name = draft.file_name;
    } else {
      const text = dialog.querySelector("#tkw-import-text");
      if (text) payload.text = text.value;
    }
    if (draft.mapping) payload.mapping = draft.mapping;
    return payload;
  }

  function renderImportMapping(dialog, headers, mapping) {
    const node = dialog.querySelector("#tkw-import-mapping");
    const fields = [
      ["word", "关键词（必填）"], ["category", "品类"], ["audience", "人群"],
      ["search_volume", "搜索量"], ["avg_30d_search_volume", "30天日均"], ["growth_rate_percent", "增长率"],
      ["product_count", "商品数"], ["supply_demand_ratio_percent", "供需比"],
      ["status", "使用状态"], ["notes", "备注"],
    ];
    node.innerHTML = headers.length ? `
      <fieldset><legend>列映射（自动识别，可调整；改完重新点「预览校验」）</legend>
        <div class="tkw-mapping">${fields.map(([field, label]) => `
          <label>${esc(label)}<select data-tkw-map="${field}">
            <option value="">（不导入）</option>
            ${headers.map((header) => `<option value="${esc(header)}"${mapping[field] === header ? " selected" : ""}>${esc(header)}</option>`).join("")}
          </select></label>`).join("")}
        </div></fieldset>` : "";
  }

  function renderImportResults(dialog, preview) {
    const node = dialog.querySelector("#tkw-import-results");
    const errors = preview.errors || [];
    const parts = [];
    if (errors.length) {
      parts.push(`<div class="tkw-import-errors"><strong>有 ${errors.length} 个问题需修正：</strong><ul>${errors.slice(0, 30).map((item) => `<li>第 ${esc(item.row)} 行：${esc(item.message)}</li>`).join("")}</ul></div>`);
    } else {
      parts.push(`<p class="tkw-hint">校验通过：将导入 <strong>${fmtNum(preview.total)}</strong> 个关键词${preview.skipped ? `，同批次去重跳过 ${preview.skipped} 行` : ""}。</p>`);
    }
    if (preview.duplicate_batch_id) {
      parts.push('<p class="tkw-hint">⚠️ 该文件已导入过（内容相同），确认导入会直接复用已有批次，不会重复累积。</p>');
    }
    if ((preview.rows || []).length) {
      parts.push(`<div class="tkw-import-results"><table class="tkw-table"><thead><tr>${Object.keys(preview.rows[0]).slice(0, 8).map((key) => `<th>${esc(key)}</th>`).join("")}</tr></thead>
        <tbody>${preview.rows.slice(0, 10).map((row) => `<tr>${Object.values(row).slice(0, 8).map((value) => `<td>${esc(value ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
    }
    node.innerHTML = parts.join("");
    dialog.querySelector("#tkw-import-confirm").disabled = Boolean(errors.length) || !preview.total;
  }

  async function runImportPreview(dialog) {
    const button = dialog.querySelector("#tkw-import-preview");
    button.disabled = true;
    try {
      const preview = await call("/api/keyword-import/preview", { method: "POST", body: JSON.stringify(importPayload(dialog)) });
      state.importDraft.preview = preview;
      state.importDraft.headers = preview.headers || [];
      state.importDraft.mapping = preview.mapping || {};
      renderImportMapping(dialog, preview.headers || [], preview.mapping || {});
      renderImportResults(dialog, preview);
      dialog.querySelectorAll("[data-tkw-map]").forEach((select) => {
        select.addEventListener("change", () => {
          const mapping = {};
          dialog.querySelectorAll("[data-tkw-map]").forEach((node) => {
            if (node.value) mapping[node.dataset.tkwMap] = node.value;
          });
          state.importDraft.mapping = mapping;
        });
      });
    } catch (error) {
      fail(error, "导入预览失败");
    } finally {
      button.disabled = false;
    }
  }

  async function confirmImport(dialog) {
    const preview = state.importDraft && state.importDraft.preview;
    if (!preview || !preview.preview_id) return;
    const button = dialog.querySelector("#tkw-import-confirm");
    button.disabled = true;
    try {
      const result = await call("/api/keyword-import/confirm", { method: "POST", body: JSON.stringify({ preview_id: preview.preview_id }) });
      closeDialog(dialog);
      notice(result.duplicate ? "该文件已有批次，未重复导入。" : `已导入批次「${result.batch?.name || ""}」（${fmtNum(result.batch?.row_count)} 词）。`);
      await loadBatches();
      state.batchId = result.batch?.id || state.batchId;
      renderBatches();
      state.page = 1;
      state.selected.clear();
      await loadRows();
      if (window.showOperationFeedback) window.showOperationFeedback("关键词批次已导入", "success");
    } catch (error) {
      fail(error, "确认导入失败");
      button.disabled = false;
    }
  }

  async function newLibrary() {
    const name = window.prompt("新词库名称（例：菲律宾服装关键词）");
    if (!name || !name.trim()) return;
    try {
      const library = await call("/api/keyword-libraries", { method: "POST", body: JSON.stringify({ name: name.trim() }) });
      notice(`词库「${library.name}」已创建，可以开始导入批次。`);
      await loadLibraries();
      await loadBatches();
      await loadRules();
    } catch (error) {
      fail(error, "新建词库失败");
    }
  }

  // ---------------- 批量 / 单行编辑 ----------------

  function openEditDialog(rowIds) {
    const single = rowIds.length === 1 ? state.rows.find((row) => row.id === rowIds[0]) || null : null;
    const dialog = openDialog(`
      ${dialogHead(single ? "编辑关键词" : `批量编辑（${rowIds.length} 项）`, single ? "修正分类、指标或备注；同批次内关键词不可重复。" : "只提交改动的字段，未选择的字段保持不变。")}
      <div class="tkw-form-grid">
        ${single ? `<label class="tkw-wide">关键词<input id="tkw-edit-word" value="${esc(single.word)}" /></label>` : ""}
        <label>品类<select id="tkw-edit-category"><option value="">（不修改）</option>${Object.entries(CATEGORY_LABELS).map(([value, label]) => `<option value="${value}"${single && single.category === value ? " selected" : ""}>${label}</option>`).join("")}</select></label>
        <label>人群<select id="tkw-edit-audience"><option value="">（不修改）</option>${[...AUDIENCE_OPTIONS, "all", "unknown", ...AUDIENCE_LEGACY].map((value) => `<option value="${value}"${single && single.audience === value ? " selected" : ""}>${AUDIENCE_LABELS[value]}</option>`).join("")}</select></label>
        <label>使用状态<select id="tkw-edit-status"><option value="">（不修改）</option>${Object.entries(STATUS_LABELS).map(([value, label]) => `<option value="${value}"${single && single.status === value ? " selected" : ""}>${label}</option>`).join("")}</select></label>
        <label>备注<input id="tkw-edit-notes" value="${esc(single ? single.notes || "" : "")}" placeholder="${single ? "" : "（留空＝不修改）"}" /></label>
        ${single ? `<label>搜索量<input id="tkw-edit-search-volume" type="number" min="0" value="${single.search_volume ?? ""}" /></label>
        <label>30天日均<input id="tkw-edit-avg" type="number" min="0" value="${single.avg_30d_search_volume ?? ""}" /></label>
        <label>增长率 %<input id="tkw-edit-growth" type="number" step="0.01" value="${single.growth_rate_percent ?? ""}" /></label>
        <label>商品数<input id="tkw-edit-products" type="number" min="0" value="${single.product_count ?? ""}" /></label>` : ""}
      </div>
      <div class="tkw-dialog-footer">
        <button type="button" class="secondary" data-tkw-close>取消</button>
        <button type="button" id="tkw-edit-save">保存修改</button>
      </div>`);
    dialog.addEventListener("click", (event) => {
      if (event.target.id === "tkw-edit-save") void saveEdits(dialog, rowIds, Boolean(single));
    });
  }

  async function saveEdits(dialog, rowIds, isSingle) {
    const changes = {};
    const value = (id) => (dialog.querySelector(`#${id}`) || {}).value || "";
    if (isSingle) {
      const word = value("tkw-edit-word").trim();
      if (!word) { notice("关键词不能为空。", true); return; }
      changes.word = word;
      changes.search_volume = value("tkw-edit-search-volume") === "" ? null : Number(value("tkw-edit-search-volume"));
      changes.avg_30d_search_volume = value("tkw-edit-avg") === "" ? null : Number(value("tkw-edit-avg"));
      changes.growth_rate_percent = value("tkw-edit-growth") === "" ? null : Number(value("tkw-edit-growth"));
      changes.product_count = value("tkw-edit-products") === "" ? null : Number(value("tkw-edit-products"));
    }
    if (value("tkw-edit-category")) changes.category = value("tkw-edit-category");
    if (value("tkw-edit-audience")) changes.audience = value("tkw-edit-audience");
    if (value("tkw-edit-status")) changes.status = value("tkw-edit-status");
    const notes = value("tkw-edit-notes");
    if (isSingle || notes) changes.notes = notes;
    if (!Object.keys(changes).length) { notice("没有要修改的字段。", true); return; }
    try {
      const result = await call("/api/keyword-rows", { method: "POST", body: JSON.stringify({ ids: rowIds, changes }) });
      closeDialog(dialog);
      notice(`已更新 ${result.updated} 条关键词。`);
      if (!isSingle) state.selected.clear();
      await loadRows();
    } catch (error) {
      fail(error, "保存修改失败");
    }
  }

  // ---------------- 标题规则 ----------------

  async function loadRules() {
    const data = await call("/api/title-skills");
    state.rules = data.items || [];
    renderRules();
    renderPreviewSkillOptions();
  }

  function ruleScopeLabel(rule) {
    return `${CATEGORY_LABELS[rule.category] || rule.category || "通用"} · ${AUDIENCE_LABELS[rule.audience] || rule.audience || "通用"}`;
  }

  function renderRules() {
    const list = $("tkw-rule-list");
    if (!list) return;
    if (!state.rules.length) {
      list.innerHTML = '<p class="tkw-empty">还没有标题规则。点「从运营模板创建」用内置运营模板起步，或「新增规则」从零配置。</p>';
      return;
    }
    const libraryName = (id) => state.libraries.find((row) => row.id === id)?.name || "";
    list.innerHTML = state.rules.map((rule) => {
      const isTemplate = rule.generation_mode === "operator_template";
      const tpl = rule.operator_template || {};
      const templateLine = isTemplate
        ? `<p class="tkw-hint">仿运营模板 · 核心词 ${esc(tpl.core_term || "—")}${(tpl.style_words || []).length ? ` · 风格词 ${(tpl.style_words || []).join("/")}` : ""}${(tpl.companion_words || []).length ? ` · 搭配 ${(tpl.companion_words || []).join("/")}` : ""}${(tpl.trim_order || []).length ? ` · 超长先删 ${(tpl.trim_order || []).join(" → ")}` : ""}</p>`
        : "";
      return `
      <article class="tkw-rule-card">
        <h4>${esc(rule.name)}${rule.is_default ? ' <span class="tkw-badge is-default">默认</span>' : ""}${isTemplate ? ' <span class="tkw-badge is-pending">仿运营模板</span>' : ""}${rule.enabled === false ? ' <span class="tkw-badge">已停用</span>' : ""}</h4>
        <p>适用：${esc(ruleScopeLabel(rule))} · 语言 ${esc(rule.language === "en" ? "英语" : "英语/他加禄")}</p>
        ${templateLine}
        <p>${esc(rule.title_min_chars ?? 40)}–${esc(rule.title_max_chars ?? 100)} 字符 · ${esc(POLICY_LABELS[rule.hot_word_policy] || rule.hot_word_policy)} · ${esc(RANKING_LABELS[rule.ranking] || rule.ranking)} · 数据超过 ${esc(rule.max_age_days ?? 30)} 天按历史参考</p>
        <div class="tkw-rule-structure">${esc((rule.structure && rule.structure.length ? rule.structure : STRUCTURE_ORDER).map((slot) => STRUCTURE_LABELS[slot] || slot).join(" → "))}</div>
        <p>${rule.library_id ? `词库：${esc(libraryName(rule.library_id))}` : "词库：未绑定（不使用热词库）"}${(rule.fixed_terms || []).length ? ` · 固定词 ${rule.fixed_terms.length}` : ""}${(rule.banned_terms || []).length ? ` · 禁用词 ${rule.banned_terms.length}` : ""}${(rule.operator_allowances || []).length ? ` · 运营例外 ${(rule.operator_allowances || []).join("/")}` : ""}</p>
        ${rule.examples ? `<p class="tkw-hint">示例：${esc(rule.examples)}</p>` : ""}
        <div class="tkw-rule-actions">
          <button type="button" data-tkw-action="edit-rule" data-id="${esc(rule.id)}">编辑</button>
          ${rule.is_default ? "" : `<button type="button" class="secondary" data-tkw-action="default-rule" data-id="${esc(rule.id)}">设为默认</button>`}
          <button type="button" class="secondary" data-tkw-action="toggle-rule" data-id="${esc(rule.id)}">${rule.enabled === false ? "启用" : "停用"}</button>
          <button type="button" class="secondary" data-tkw-action="delete-rule" data-id="${esc(rule.id)}">删除</button>
        </div>
      </article>`;
    }).join("");
  }

  function structureSelects(structure) {
    const values = structure && structure.length === 4 ? structure : STRUCTURE_ORDER;
    return `<div class="tkw-structure">${[0, 1, 2, 3].map((index) => `
      <label>第 ${index + 1} 位<select id="tkw-rule-structure-${index}">
        ${STRUCTURE_ORDER.map((slot) => `<option value="${slot}"${values[index] === slot ? " selected" : ""}>${STRUCTURE_LABELS[slot]}</option>`).join("")}
      </select></label>`).join("")}</div>
      <small class="tkw-hint">四个位置各选一次，决定标题里词语的排列顺序。</small>`;
  }

  function templateSection(tpl) {
    tpl = tpl || {};
    return `
      <fieldset id="tkw-template-section" hidden><legend>仿运营模板配置</legend>
        <div class="tkw-form-grid">
          <label class="tkw-wide">核心词（必须完整出现在标题里，不可拆开）<input id="tkw-tpl-core" value="${esc(tpl.core_term || "")}" placeholder="例：tshirt for men" /></label>
          <label class="tkw-wide">风格词（每行一个；按图片主题和模板选择）<textarea id="tkw-tpl-styles">${esc((tpl.style_words || []).join("\n"))}</textarea></label>
          <label class="tkw-wide">待确认参考词（每行一个；仅在模板参考稿中保留并提示核对）<textarea id="tkw-tpl-reference">${esc((tpl.reference_terms || []).join("\n"))}</textarea></label>
          <label class="tkw-wide">可选搭配词（每行一个）<textarea id="tkw-tpl-companions">${esc((tpl.companion_words || []).join("\n"))}</textarea></label>
          <label class="tkw-wide">排列方式（每行一个；占位符 {theme}=主题名 {character}=角色名 {style}=风格词 {companion}=搭配词 {core}=核心词，AI 从商品图识别主题与角色）<textarea id="tkw-tpl-patterns">${esc((tpl.patterns || []).join("\n"))}</textarea></label>
          <label class="tkw-wide">超长删词顺序（每行一个；超上限时从上往下删，不切断单词）<textarea id="tkw-tpl-trim">${esc((tpl.trim_order || []).join("\n"))}</textarea></label>
          <label class="tkw-wide">模板附加规则（每行一条，注入生成提示）<textarea id="tkw-tpl-rules">${esc((tpl.rules || []).join("\n"))}</textarea></label>
          <label>模板来源<input id="tkw-tpl-source" value="${esc(tpl.source || "")}" placeholder="例：自定义模板" /></label>
          <label>模板版本<input id="tkw-tpl-version" type="number" min="1" max="999" value="${tpl.version ?? 1}" /></label>
        </div>
      </fieldset>`;
  }

  function openRuleDialog(rule, opts = {}) {
    const isNew = !rule;
    rule = rule || {};
    const heading = opts.fromPreset
      ? `从运营模板创建：${opts.fromPreset}`
      : isNew ? "新增标题规则" : `编辑规则：${rule.name}`;
    const dialog = openDialog(`
      ${dialogHead(heading, "同一适用范围只会有一套默认规则；保存不会替换当前默认，需再点「设为默认」。")}
      <div class="tkw-form-grid">
        <label>规则名称<input id="tkw-rule-name" value="${esc(rule.name || "")}" placeholder="例：童装 T 恤 · 主推款" /></label>
        <label>生成方式<select id="tkw-rule-mode">
          <option value="standard"${(rule.generation_mode || "standard") === "standard" ? " selected" : ""}>现有生成方式</option>
          <option value="operator_template"${rule.generation_mode === "operator_template" ? " selected" : ""}>仿运营模板</option>
        </select></label>
        <label>适用品类<select id="tkw-rule-category">
          <option value="all"${(rule.category || "all") === "all" ? " selected" : ""}>通用（全部品类）</option>
          ${["tshirt", "set", "hoodie", "dress", "other"].map((value) => `<option value="${value}"${rule.category === value ? " selected" : ""}>${CATEGORY_LABELS[value]}</option>`).join("")}
        </select></label>
        <label>适用人群<select id="tkw-rule-audience">
          <option value="all"${(rule.audience || "all") === "all" ? " selected" : ""}>通用（全部人群）</option>
          ${[...AUDIENCE_OPTIONS, ...(rule.audience && rule.audience !== "all" && !AUDIENCE_OPTIONS.includes(rule.audience) ? [rule.audience] : [])].map((value) => `<option value="${value}"${rule.audience === value ? " selected" : ""}>${AUDIENCE_LABELS[value] || value}</option>`).join("")}
        </select></label>
        <label>语言<select id="tkw-rule-language">
          <option value="en"${(rule.language || "en") === "en" ? " selected" : ""}>英语</option>
          <option value="taglish"${rule.language === "taglish" ? " selected" : ""}>英语为主，可夹他加禄语</option>
        </select></label>
        <label>绑定词库<select id="tkw-rule-library">
          <option value="">不绑定（不使用热词库）</option>
          ${state.libraries.map((library) => `<option value="${esc(library.id)}"${rule.library_id === library.id ? " selected" : ""}>${esc(library.name)}</option>`).join("")}
        </select></label>
        <label>标题最少字符<input id="tkw-rule-min" type="number" min="0" max="120" value="${rule.title_min_chars ?? 40}" /></label>
        <label>标题最多字符<input id="tkw-rule-max" type="number" min="10" max="120" value="${rule.title_max_chars ?? 100}" /></label>
        <label>热词策略<select id="tkw-rule-policy">
          ${Object.entries(POLICY_LABELS).map(([value, label]) => `<option value="${value}"${(rule.hot_word_policy || "reference") === value ? " selected" : ""}>${label}</option>`).join("")}
        </select></label>
        <label>选词偏好<select id="tkw-rule-ranking">
          ${Object.entries(RANKING_LABELS).map(([value, label]) => `<option value="${value}"${(rule.ranking || "volume") === value ? " selected" : ""}>${label}</option>`).join("")}
        </select></label>
        <label>数据提醒周期（天）<input id="tkw-rule-max-age" type="number" min="1" max="365" value="${rule.max_age_days ?? 30}" /></label>
      </div>
      <fieldset><legend>标题结构（排列顺序）</legend>${structureSelects(rule.structure)}</fieldset>
      ${templateSection(rule.operator_template)}
      <div class="tkw-form-grid">
        <label class="tkw-wide">固定词（每行一个，标题必须包含）<textarea id="tkw-rule-fixed">${esc((rule.fixed_terms || []).join("\n"))}</textarea></label>
        <label class="tkw-wide">禁用词（每行一个）<textarea id="tkw-rule-banned">${esc((rule.banned_terms || []).join("\n"))}</textarea></label>
        <label class="tkw-wide">运营例外词（每行一个；允许出现但不作为流量/商品事实证据）<textarea id="tkw-rule-allowances">${esc((rule.operator_allowances || []).join("\n"))}</textarea></label>
        <label class="tkw-wide">补充要求（写入生成提示）<textarea id="tkw-rule-instructions">${esc(rule.instructions || "")}</textarea></label>
        <label class="tkw-wide">示例标题（每行一个）<textarea id="tkw-rule-examples">${esc(rule.examples || "")}</textarea></label>
      </div>
      <div class="tkw-form-grid">
        <label class="tkw-check"><input id="tkw-rule-enabled" type="checkbox"${rule.enabled === false ? "" : " checked"} />启用</label>
        <label class="tkw-check"><input id="tkw-rule-default" type="checkbox"${rule.is_default ? " checked" : ""} />设为该范围默认规则</label>
      </div>
      <div class="tkw-dialog-footer">
        <button type="button" class="secondary" data-tkw-close>取消</button>
        <button type="button" id="tkw-rule-save">保存规则</button>
      </div>`);
    const section = dialog.querySelector("#tkw-template-section");
    const syncTemplateSection = () => {
      if (section) section.hidden = dialog.querySelector("#tkw-rule-mode").value !== "operator_template";
    };
    dialog.querySelector("#tkw-rule-mode").addEventListener("change", syncTemplateSection);
    syncTemplateSection();
    dialog.addEventListener("click", (event) => {
      if (event.target.id === "tkw-rule-save") void saveRule(dialog, rule.id || "");
    });
  }

  async function loadPresets() {
    if (state.presets) return state.presets;
    const data = await call("/api/title-skill-presets");
    state.presets = data.items || [];
    return state.presets;
  }

  function openRuleDialogFromPreset(preset) {
    // 预设只是预填：保存后就是一套独立规则，之后与预设互不影响。
    openRuleDialog({ ...(preset.rule || {}), name: (preset.rule || {}).name || preset.name }, { fromPreset: preset.name });
  }

  async function openPresetDialog() {
    let presets;
    try {
      presets = await loadPresets();
    } catch (error) {
      fail(error, "运营模板预设加载失败");
      return;
    }
    if (!presets.length) {
      notice("暂无内置运营模板预设。", true);
      return;
    }
    if (presets.length === 1) {
      openRuleDialogFromPreset(presets[0]);
      return;
    }
    const dialog = openDialog(`
      ${dialogHead("选择运营模板", "从内置预设创建规则；创建后可自由编辑，保存不会替换当前默认。")}
      ${presets.map((preset) => `
        <button type="button" class="tkw-batch-button" data-tkw-preset="${esc(preset.id)}">
          <strong>${esc(preset.name)}</strong>
          <span>${esc(CATEGORY_LABELS[preset.scope?.category] || "通用")} · ${esc(AUDIENCE_LABELS[preset.scope?.audience] || "通用")} · v${esc(preset.version ?? 1)}</span>
        </button>`).join("")}
      <div class="tkw-dialog-footer"><button type="button" class="secondary" data-tkw-close>取消</button></div>`);
    dialog.addEventListener("click", (event) => {
      const node = event.target.closest("[data-tkw-preset]");
      if (!node) return;
      const preset = presets.find((row) => row.id === node.dataset.tkwPreset);
      closeDialog(dialog);
      if (preset) openRuleDialogFromPreset(preset);
    });
  }

  const lines = (value) => String(value || "").split("\n").map((line) => line.trim()).filter(Boolean);

  async function saveRule(dialog, ruleId) {
    const val = (id) => (dialog.querySelector(`#${id}`) || {}).value || "";
    const structure = [0, 1, 2, 3].map((index) => val(`tkw-rule-structure-${index}`));
    if (new Set(structure).size !== 4) { notice("标题结构的四个位置必须各不相同。", true); return; }
    if (!val("tkw-rule-name").trim()) { notice("请填写规则名称。", true); return; }
    const mode = val("tkw-rule-mode") || "standard";
    let operatorTemplate = null;
    if (mode === "operator_template") {
      const patterns = lines(val("tkw-tpl-patterns"));
      if (!val("tkw-tpl-core").trim()) { notice("仿运营模板必须填写核心词。", true); return; }
      if (!patterns.length) { notice("仿运营模板至少要提供一个排列方式。", true); return; }
      operatorTemplate = {
        core_term: val("tkw-tpl-core").trim(),
        style_words: lines(val("tkw-tpl-styles")),
        companion_words: lines(val("tkw-tpl-companions")),
        patterns,
        trim_order: lines(val("tkw-tpl-trim")),
        reference_terms: lines(val("tkw-tpl-reference")),
        rules: lines(val("tkw-tpl-rules")),
        source: val("tkw-tpl-source").trim(),
        version: Number(val("tkw-tpl-version") || 1) || 1,
      };
    }
    const body = JSON.stringify({
      name: val("tkw-rule-name").trim(),
      category: val("tkw-rule-category") || "all",
      audience: val("tkw-rule-audience") || "all",
      language: val("tkw-rule-language") || "en",
      library_id: val("tkw-rule-library"),
      title_min_chars: Number(val("tkw-rule-min") || 40),
      title_max_chars: Number(val("tkw-rule-max") || 100),
      hot_word_policy: val("tkw-rule-policy") || "reference",
      ranking: val("tkw-rule-ranking") || "volume",
      max_age_days: Number(val("tkw-rule-max-age") || 30),
      structure,
      fixed_terms: lines(val("tkw-rule-fixed")),
      banned_terms: lines(val("tkw-rule-banned")),
      operator_allowances: lines(val("tkw-rule-allowances")),
      instructions: val("tkw-rule-instructions").trim(),
      examples: val("tkw-rule-examples").trim(),
      enabled: dialog.querySelector("#tkw-rule-enabled").checked,
      is_default: dialog.querySelector("#tkw-rule-default").checked,
      generation_mode: mode,
      operator_template: operatorTemplate,
    });
    try {
      if (ruleId) await call(`/api/title-skills/${encodeURIComponent(ruleId)}`, { method: "PUT", body });
      else await call("/api/title-skills", { method: "POST", body });
      closeDialog(dialog);
      notice("标题规则已保存。");
      await loadRules();
      if (window.showOperationFeedback) window.showOperationFeedback("标题规则已保存", "success");
    } catch (error) {
      fail(error, "规则保存失败");
    }
  }

  async function ruleAction(action, ruleId) {
    const rule = state.rules.find((row) => row.id === ruleId);
    try {
      if (action === "edit-rule") {
        openRuleDialog(rule);
        return;
      }
      if (action === "default-rule") {
        await call(`/api/title-skills/${encodeURIComponent(ruleId)}/set-default`, { method: "POST", body: "{}" });
        notice(`已把「${rule?.name || ""}」设为该范围默认规则。`);
      } else if (action === "toggle-rule") {
        await call(`/api/title-skills/${encodeURIComponent(ruleId)}`, {
          method: "PUT",
          body: JSON.stringify({ enabled: !(rule && rule.enabled !== false) }),
        });
        notice("规则状态已更新。");
      } else if (action === "delete-rule") {
        if (!window.confirm(`确认删除规则「${rule?.name || ruleId}」？`)) return;
        await call(`/api/title-skills/${encodeURIComponent(ruleId)}`, { method: "DELETE" });
        notice("规则已删除。");
      }
      await loadRules();
    } catch (error) {
      fail(error, "规则操作失败");
    }
  }

  // ---------------- 生成预览 ----------------

  async function loadProducts(force) {
    if (state.productsLoaded && !force) return;
    const data = await call("/api/title-workbench/products");
    state.products = data.items || [];
    state.productsLoaded = true;
    renderProducts();
  }

  function renderProducts() {
    const select = $("tkw-product");
    if (!select) return;
    select.innerHTML = '<option value="">请选择已有商品</option>' + state.products.map((product) => {
      const label = [product.main_sku, product.name || product.listing_title || ""].filter(Boolean).join(" · ");
      return `<option value="${esc(product.id)}">${esc(label || product.id)}</option>`;
    }).join("");
  }

  function selectedProduct() {
    const id = ($("tkw-product") || {}).value || "";
    return state.products.find((row) => row.id === id) || null;
  }

  function renderProductInfo() {
    const node = $("tkw-product-info");
    if (!node) return;
    const product = selectedProduct();
    if (!product) {
      node.innerHTML = "";
      return;
    }
    const attrs = Object.entries(product.recognized_attributes || {});
    node.innerHTML = `
      ${product.image_url ? `<img src="${esc(product.image_url)}" alt="商品图" />` : ""}
      <div><strong>${esc(product.main_sku || "")}</strong>
        ${product.listing_title ? `<p>当前标题：${esc(product.listing_title)}</p>` : "<p>尚无标题。</p>"}
        ${attrs.length ? `<p>${attrs.slice(0, 8).map(([key, value]) => `${esc(key)}: ${esc(value)}`).join(" · ")}</p>` : "<p>暂无已确认属性。</p>"}
      </div>`;
    const category = $("tkw-preview-category");
    const audience = $("tkw-preview-audience");
    if (category) category.value = product.title_category || "";
    if (audience) audience.value = product.title_audience || product.audience || "";
    void loadHistory();
  }

  function renderPreviewSkillOptions() {
    const select = $("tkw-preview-skill");
    if (!select) return;
    const current = select.value;
    select.innerHTML = '<option value="">自动匹配规则</option>' + state.rules
      .filter((rule) => rule.enabled !== false)
      .map((rule) => `<option value="${esc(rule.id)}">${esc(rule.name)}（${esc(ruleScopeLabel(rule))}${rule.generation_mode === "operator_template" ? " · 仿运营模板" : ""}）</option>`)
      .join("");
    if (current) select.value = current;
  }

  // 预览的「确认品类/确认人群」：默认按商品（属性和类目路径）推断；操作员
  // 手动选一项后作为本次预览的商品范围，规则匹配也按它来判。
  function renderPreviewScopeOptions() {
    const category = $("tkw-preview-category");
    if (category) {
      const current = category.value;
      category.innerHTML = '<option value="">按商品推断</option>' + ["tshirt", "set", "hoodie", "dress", "other"]
        .map((value) => `<option value="${value}">${esc(CATEGORY_LABELS[value])}</option>`).join("");
      if (current) category.value = current;
    }
    const audience = $("tkw-preview-audience");
    if (audience) {
      const current = audience.value;
      const values = [...AUDIENCE_OPTIONS];
      if (current && !values.includes(current)) values.push(current);
      audience.innerHTML = '<option value="">按商品推断</option>' + values
        .map((value) => `<option value="${value}">${esc(AUDIENCE_LABELS[value] || value)}</option>`).join("");
      if (current) audience.value = current;
    }
  }

  function renderFilterOptions() {
    const category = $("tkw-filter-category");
    if (category && !category.options.length) {
      category.innerHTML = '<option value="">全部品类</option>' + ["tshirt", "set", "hoodie", "dress", "other"]
        .map((value) => `<option value="${value}">${esc(CATEGORY_LABELS[value])}</option>`).join("");
    }
    const audience = $("tkw-filter-audience");
    if (audience && !audience.options.length) {
      audience.innerHTML = '<option value="">全部人群</option>' + [...AUDIENCE_OPTIONS, "unknown", ...AUDIENCE_LEGACY]
        .map((value) => `<option value="${value}">${esc(AUDIENCE_LABELS[value])}</option>`).join("");
    }
    const status = $("tkw-filter-status");
    if (status && !status.options.length) {
      status.innerHTML = '<option value="">全部状态</option>' + Object.entries(STATUS_LABELS)
        .map(([value, label]) => `<option value="${value}">${esc(label)}</option>`).join("");
    }
  }

  function renderPreviewBatchOptions() {
    const select = $("tkw-preview-batch");
    if (!select) return;
    const current = select.value;
    select.innerHTML = '<option value="">自动选最新匹配批次</option>' + state.batches.map((batch) =>
      `<option value="${esc(batch.id)}">${esc(batch.data_date || "日期未知")} · ${esc(batch.name || "")}</option>`).join("");
    if (current) select.value = current;
  }

  async function generatePreview() {
    const product = selectedProduct();
    if (!product) { notice("请先选择商品。", true); return; }
    const button = $("tkw-generate");
    const body = { product_id: product.id };
    const skillId = ($("tkw-preview-skill") || {}).value || "";
    const batchId = ($("tkw-preview-batch") || {}).value || "";
    const category = ($("tkw-preview-category") || {}).value || "";
    const audience = ($("tkw-preview-audience") || {}).value || "";
    if (skillId) body.skill_id = skillId;
    if (batchId) body.batch_id = batchId;
    if (category) body.category = category;
    if (audience) body.audience = audience;
    if (button) { button.disabled = true; button.textContent = "生成中…"; }
    const result = $("tkw-preview-result");
    if (result) result.innerHTML = '<p class="tkw-empty">正在按规则选词并生成标题…</p>';
    try {
      const data = await call("/api/title-workbench/preview", { method: "POST", body: JSON.stringify(body) });
      state.previewResult = data;
      renderPreviewResult(data);
      await loadHistory();
    } catch (error) {
      fail(error, "生成预览失败");
      if (result) result.innerHTML = '<p class="tkw-empty">生成失败，请检查 AI 连接与规则配置。</p>';
    } finally {
      if (button) { button.disabled = false; button.textContent = "生成标题预览"; }
    }
  }

  const TITLE_STATUS_LABELS = { PASS: "通过", REVIEW: "待确认", BLOCKED: "未通过" };

  function renderPreviewResult(data) {
    const node = $("tkw-preview-result");
    if (!node) return;
    if (data.error && !(data.titles || []).length) {
      node.innerHTML = `<div class="tkw-empty"><strong>生成未成功</strong><p>${esc(data.error)}</p></div>`;
      return;
    }
    const context = data.context || {};
    const warnings = context.warnings || [];
    const batch = context.batch;
    const skill = data.skill || {};
    const modeLabel = skill.generation_mode === "operator_template" ? "仿运营模板" : "现有生成方式";
    const tpl = skill.operator_template;
    const ordered = (data.titles || []).map((title, index) => ({ title, index }));
    ordered.sort((a, b) => (a.index === data.recommended_index ? -1 : b.index === data.recommended_index ? 1 : 0));
    node.innerHTML = `
      <div class="tkw-context">
        规则：<strong>${esc(skill.name || "")}</strong>（v${esc(skill.version ?? 1)}）· 生成模式：${esc(modeLabel)}
        · 数据批次：${batch ? `${esc(batch.name || "")}（${esc(batch.data_date || "日期未知")}）` : "无匹配批次"}
        ${context.stale ? '<span class="tkw-badge is-history">历史参考</span>' : '<span class="tkw-badge is-current">当前数据</span>'}
        ${tpl ? `<br>模板核心词：${esc(tpl.core_term || "—")}${(tpl.style_words || []).length ? ` · 风格词 ${tpl.style_words.map(esc).join("/")}` : ""}${(tpl.companion_words || []).length ? ` · 搭配 ${tpl.companion_words.map(esc).join("/")}` : ""}${(tpl.trim_order || []).length ? ` · 超长先删 ${tpl.trim_order.map(esc).join(" → ")}` : ""}` : ""}
        ${warnings.length ? `<br>${warnings.map((text) => `<span class="tkw-badge is-pending">${esc(text)}</span>`).join(" ")}` : ""}
      </div>
      ${ordered.map(({ title, index }) => `
        <article class="tkw-title-card${index === data.recommended_index ? " is-recommended" : ""}">
          <div class="tkw-title-card-header">
            <span>${index === data.recommended_index ? "推荐标题" : `备选 ${index === 0 ? 1 : index}`}</span>
            <span>${esc(title.chars ?? (title.title || "").length)} 字符
              <span class="tkw-badge ${title.status === "PASS" ? "is-current" : title.status === "REVIEW" ? "is-pending" : "is-blocked"}">${esc(TITLE_STATUS_LABELS[title.status] || title.status)}</span>
            </span>
          </div>
          <div class="tkw-title-copy">${esc(title.title)}</div>
          <div class="tkw-title-evidence">
            ${(title.accepted_terms || []).length
              ? `<div>采用关键词：</div><ul>${title.accepted_terms.map((term) => `<li>${esc(term.word)} · ${esc(term.data_date || "—")} · ${esc(EVIDENCE_LABELS[term.evidence_level] || term.evidence_level || "")}${term.search_volume != null ? ` · 搜索量 ${fmtNum(term.search_volume)}` : ""}</li>`).join("")}</ul>`
              : "<div>未采用候选热词。</div>"}
            ${(title.warnings || []).length ? `<div>提示：${title.warnings.map((text) => esc(text)).join("；")}</div>` : ""}
          </div>
          ${title.status !== "BLOCKED" ? `<div class="tkw-title-bottom"><button type="button" data-tkw-action="adopt-title" data-title="${esc(title.title)}">采用此标题</button></div>` : ""}
        </article>`).join("")}
      ${data.error ? `<p class="tkw-hint">部分候选未通过校验：${esc(data.error)}</p>` : ""}`;
  }

  async function adoptTitle(title) {
    const product = selectedProduct();
    if (!product || !title) return;
    // 点击「采用此标题」本身就是明确操作（预览从不自动改写商品），不再弹确认框。
    try {
      await call("/api/title-workbench/apply", { method: "POST", body: JSON.stringify({ product_id: product.id, title }) });
      product.listing_title = title;
      renderProductInfo();
      notice("标题已写入商品。");
      if (window.showOperationFeedback) window.showOperationFeedback("标题已采用", "success");
      await loadHistory();
    } catch (error) {
      fail(error, "采用标题失败");
    }
  }

  async function loadHistory() {
    const node = $("tkw-history-list");
    if (!node) return;
    const product = selectedProduct();
    if (!product) { node.innerHTML = "选择商品后查看记录。"; return; }
    try {
      const data = await call(`/api/title-workbench/history?product_id=${encodeURIComponent(product.id)}`);
      const rows = data.items || [];
      node.innerHTML = rows.length ? rows.map((row) => `
        <div class="tkw-history-row">
          <small>${esc(fmtTime(row.created_at))} · ${esc(SOURCE_LABELS[row.source] || row.source || "")}${row.skill?.name ? ` · 规则 ${esc(row.skill.name)}` : ""}${row.batch?.data_date ? ` · 数据 ${esc(row.batch.data_date)}` : ""}${row.stale ? " · 历史参考" : ""}</small>
          ${row.applied_title ? `<strong>已采用：${esc(row.applied_title)}</strong>` : (row.titles || []).length ? `推荐：${esc((row.titles[0] || {}).title || "")}（${esc(TITLE_STATUS_LABELS[(row.titles[0] || {}).status] || "")}）` : ""}
          ${row.error ? `<small>错误：${esc(row.error)}</small>` : ""}
        </div>`).join("") : "该商品还没有生成记录。";
    } catch (error) {
      node.innerHTML = "生成记录加载失败。";
    }
  }

  // ---------------- 事件绑定 ----------------

  let searchTimer = 0;

  function bind() {
    if (bound) return;
    bound = true;
    const section = $("skills");
    if (!section) return;

    section.addEventListener("click", (event) => {
      const target = event.target instanceof HTMLElement ? event.target : null;
      if (!target) return;
      const tab = target.closest("[data-tkw-tab]");
      if (tab) {
        activateTab(tab.dataset.tkwTab);
        if (tab.dataset.tkwTab === "preview") void loadProducts().catch((error) => fail(error, "商品列表加载失败"));
        if (tab.dataset.tkwTab === "rules") void loadRules().catch((error) => fail(error, "规则加载失败"));
        return;
      }
      const actionNode = target.closest("[data-tkw-action]");
      if (target.matches('input[data-tkw-action="select-row"]')) {
        const id = target.dataset.id;
        if (target.checked) state.selected.add(id); else state.selected.delete(id);
        target.closest("tr")?.classList.toggle("is-selected", target.checked);
        renderSelection();
        const pageRows = state.rows;
        const allBox = document.querySelector('input[data-tkw-action="select-page"]');
        if (allBox) allBox.checked = pageRows.length > 0 && pageRows.every((row) => state.selected.has(row.id));
        return;
      }
      if (target.matches('input[data-tkw-action="select-page"]')) {
        state.rows.forEach((row) => {
          if (target.checked) state.selected.add(row.id); else state.selected.delete(row.id);
        });
        renderRows();
        return;
      }
      if (!actionNode) return;
      const action = actionNode.dataset.tkwAction;
      const id = actionNode.dataset.id || "";
      const handler = {
        refresh: () => load(),
        "new-library": () => void newLibrary(),
        import: () => openImportDialog("file"),
        paste: () => openImportDialog("paste"),
        export: () => void exportRows(),
        "select-batch": async () => {
          state.batchId = id;
          state.page = 1;
          state.selected.clear();
          renderBatches();
          await loadRows().catch((error) => fail(error, "关键词加载失败"));
        },
        "toggle-batch": async () => {
          const batch = state.batches.find((row) => row.id === id);
          const enable = batch ? batch.enabled === false : true;
          try {
            await call(`/api/keyword-batches/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({ enabled: enable }) });
            notice(enable ? "批次已启用，可参与最新批次选择。" : "批次已停用，不再被自动选用。");
            await loadBatches();
            await loadRows().catch(() => {});
          } catch (error) {
            fail(error, "批次状态更新失败");
          }
        },
        sort: () => {
          const field = actionNode.dataset.field || "word";
          if (state.sort === field) state.direction = state.direction === "asc" ? "desc" : "asc";
          else { state.sort = field; state.direction = "asc"; }
          state.page = 1;
          void loadRows().catch((error) => fail(error, "排序失败"));
        },
        "prev-page": () => { if (state.page > 1) { state.page -= 1; void loadRows().catch((error) => fail(error, "翻页失败")); } },
        "next-page": () => { if (state.page < state.pages) { state.page += 1; void loadRows().catch((error) => fail(error, "翻页失败")); } },
        "clear-selection": () => clearSelection(),
        "bulk-edit": () => openEditDialog([...state.selected]),
        "edit-row": () => openEditDialog([id]),
        "new-rule": () => openRuleDialog(null),
        "new-rule-from-preset": () => void openPresetDialog(),
        "edit-rule": () => void ruleAction("edit-rule", id),
        "default-rule": () => void ruleAction("default-rule", id),
        "toggle-rule": () => void ruleAction("toggle-rule", id),
        "delete-rule": () => void ruleAction("delete-rule", id),
        generate: () => void generatePreview(),
        "adopt-title": () => void adoptTitle(actionNode.dataset.title || ""),
      }[action];
      if (handler) handler();
    });

    section.addEventListener("change", (event) => {
      const target = event.target instanceof HTMLElement ? event.target : null;
      if (!target) return;
      const reset = () => { state.page = 1; state.selected.clear(); };
      if (target.id === "tkw-library") {
        state.libraryId = target.value;
        state.batchId = "";
        reset();
        void loadBatches().then(loadRows).catch((error) => fail(error, "词库切换失败"));
      } else if (target.id === "tkw-filter-category" || target.id === "tkw-filter-audience" || target.id === "tkw-filter-status") {
        state.filters[target.id.replace("tkw-filter-", "")] = target.value;
        reset();
        void loadRows().catch((error) => fail(error, "筛选失败"));
      } else if (target.id === "tkw-extra-metrics") {
        state.filters.extra = target.checked;
        renderRows();
      } else if (target.id === "tkw-page-size") {
        state.pageSize = Number(target.value) || 50;
        reset();
        void loadRows().catch((error) => fail(error, "翻页失败"));
      } else if (target.id === "tkw-product") {
        state.previewResult = null;
        const node = $("tkw-preview-result");
        if (node) node.innerHTML = '<div class="tkw-empty"><strong>先看效果，再采用标题</strong><p>选择商品后，可检查标题长度、选词依据和数据日期。</p></div>';
        renderProductInfo();
      }
    });

    const search = $("tkw-search");
    if (search) {
      search.addEventListener("input", () => {
        window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(() => {
          state.filters.search = search.value.trim();
          state.page = 1;
          state.selected.clear();
          void loadRows().catch((error) => fail(error, "搜索失败"));
        }, 300);
      });
    }
  }

  // ---------------- 入口 ----------------

  async function load() {
    bind();
    notice("");
    try {
      await loadLibraries();
      await loadBatches();
      await loadRules();
      renderFilterOptions();
      renderPreviewScopeOptions();
      renderPreviewBatchOptions();
      await loadRows();
    } catch (error) {
      fail(error, "标题工作台加载失败");
    }
  }

  window.TitleWorkbench = { load };
})();
