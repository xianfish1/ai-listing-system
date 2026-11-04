const state = {
  assets: [],
  baseProducts: [],
  editingProfileSections: null,
  storeTemplates: [],
  // 模板按马帮账号隔离：storeTemplates 是当前账号的模板（列表/选择器只用它），
  // storeTemplatesAll 是全量索引，仅供跨账号显示（例如刊登副本回显模板名）。
  storeTemplatesAll: [],
  storeTemplateAccountId: "",
  storeTemplateAccountsLoaded: false,
  mabangStoreOptions: [],
  mabangAccounts: [],
  mabangAccountsDefaultId: "",
  editingMabangAccountId: "",
  storeListingCopies: [],
  automationJobs: [],
  // 「再次尝试」串行队列：自动化页连点多个任务时一次只重排一条，避免并发唤醒
  // Worker / 准备浏览器把界面拖卡。automationRetryQueue 存等待中的任务 id（去重），
  // automationRetryActiveId 是正在处理的那条；按钮据此显示「排队中… / 重排中…」并置灰。
  automationRetryQueue: [],
  automationRetryActiveId: "",
  automationRetryRunning: false,
  categoryProfiles: [],
  categoryProfileVersions: [],
  marketplaceCategories: [],
  marketplaceCategoryPath: [],
  marketplaceCategoryMode: "browse",
  selectedMarketplaceCategory: null,
  marketplaceCategorySearchTimer: null,
  marketplaceCategoryRequestId: 0,
  baseCategorySearchTimer: null,
  baseCascaderPath: [],
  baseCascaderSearchMode: false,
  baseCategoryShowAll: false,
  baseCategoryIndex: null,   // { byId: Map, childrenOf: Map, nodes: [] } built from local JSON
  selectedBaseProductId: "", // 单选：当前维护/操作对象（点卡片选中）
  selectedBaseProducts: new Set(), // 多选：勾选框选中的批量对象
  baseProductRetryRunning: false, // 批量 AI 重识别进行中：防重复点击，按钮置灰
  baseRetrySkills: [],            // 「重新识别标题」下拉里可选的标题规则
  baseRetrySkillsLoaded: false,   // 规则列表只拉一次；失败后可重试
  baseProductCategoryFilter: "",
  baseProductTimeFilter: "today", // 默认只看今天更新的商品；点 chip 切到昨天/最近一周/全部
  selectedStoreTemplates: new Set(),
  selectedListingCopies: new Set(),
  selectedFileGroup: "",
  editingBaseProductId: "",
  editingBaseProductImages: [],
  editingBaseVariantImageMap: {},
  editingBaseColors: [],
  // 颜色图选择弹层当前打开的是哪一行（-1 = 关闭）。弹层挂在 body 上做 fixed 定位，
  // 因为维护弹窗的滚动容器会把 absolute 弹层裁掉。
  variantImagePickerIndex: -1,
  // 「颜色图顺序跟随产品图顺序」开关的编辑态（按商品保存；缺字段默认打开）。
  editingBaseColorOrderFollowsImages: true,
  editingBaseSizeChartImage: "",
  editingBaseRecognizedSizes: [],
  editingBaseSelectedSizes: [],
  editingBaseProductCategory: null,
  // 类目属性字段是异步拉的：切类目/套模板时用自增 token 丢弃过期的响应，
  // 避免慢返回把新类目的字段（和已回填的值）又盖回旧类目。
  baseCategoryAttributeRequestId: 0,
  baseCategoryQuickTemplates: [],
  baseCategoryQuickTemplateLoaded: false,
  draggingBaseImageIndex: -1,
  editingListingCopyId: "",
  editingListingCopy: null,
  editingListingCopyImages: [],
  draggingListingCopyImageIndex: -1,
  listingCopySizeChartAsset: null,
  listingCopyPromotions: [],
  listingCopyTargetConfigs: new Map(),
  listingCopyCnscPricePreview: null,
  editingStoreTemplateId: "",
  sizeChartAsset: null,
  defaultSkuImageAsset: null,
  storePromotions: [],
  storeWatermarks: [],
  watermarkChoices: { main: "", product: "" },
  // 每店水印目录的采集状态：{status, count, collected_at, attempted_at, error}。
  watermarkCatalogStatus: null,
  watermarkCatalogStoreId: "",
  watermarkOpenRole: "",
  watermarkHoverCloseTimer: 0,
  storeSizeChartTemplates: [],
  cnscPricePreview: null,
  cnscTargetStoreConfigs: new Map(),
  basePriceColorRows: [],
  basePricePreview: null,
  draggingBaseColorIndex: -1,
  descriptionProfiles: [],
  readiness: null,
  projectPlan: null,
  runtimeConfiguration: null,
  networkStatus: null,
  updateStatus: null,
  releaseJob: null,
  storageUsage: null,
  aiModelConnections: [],
  workerStatus: null,
  editingAiConnectionId: "",
  editingDescriptionProfileId: "",
  editingProfileLegacyNotice: "",
  profilePreviewSample: { name: "Classic Anime Graphic Tee", category: "Men's T-Shirts", color: "Black", size: "M" },
  mabangPageImportRunId: "",
  mabangPageImportRun: null,
  mabangPageImportPollTimer: 0,
  openAutomationJobId: "",
};

const STANDARD_SIZES = ["100", "110", "120", "130", "140", "150", "S", "M", "L", "XL", "XXL", "XXXL"];
const TASK_STATUS_LABELS = {
  ready: "待领取", claimed: "已领取", running: "执行中", saved: "已保存", saved_with_warnings: "已保存（有提醒）", failed: "执行失败",
  needs_confirmation: "需要人工确认", needs_regeneration: "需要重新生成", canceled: "已取消",
  needs_manual: "需要补充信息", blocked: "已阻止",
};

const TASK_SECTION_ORDER = [
  { id: "new", label: "新建", tone: "watch" },
  { id: "failed", label: "失败", tone: "alert" },
  { id: "completed", label: "已完成", tone: "done" },
];

const COMPLETED_AUTOMATION_STATUSES = new Set(["saved", "saved_with_warnings"]);
const FAILED_AUTOMATION_STATUSES = new Set([
  "failed", "blocked", "needs_manual", "needs_confirmation", "needs_regeneration", "canceled",
]);
// 已经排进队列（待领取/已领取）或正在执行：副本其实「已经有任务了」，不算新建。
const ACTIVE_AUTOMATION_STATUSES = new Set(["ready", "claimed", "running"]);

function automationTaskSection(job) {
  const status = String(job?.status || "");
  if (COMPLETED_AUTOMATION_STATUSES.has(status)) return "completed";
  if (FAILED_AUTOMATION_STATUSES.has(status)) return "failed";
  return "new";
}

function listingCopyTaskSection(job) {
  return job ? automationTaskSection(job) : "new";
}

// 分区标题行（新建/失败/已完成）：可点折叠，折叠后这一区的行不再渲染。
// options.key 用于区分是哪个列表的折叠状态（副本列表用 section.id，任务列表用 jobs:<id>）。
function appendTaskSectionRow(tbody, section, count, colspan, options = {}) {
  const collapsed = Boolean(options.collapsed);
  tbody.insertAdjacentHTML("beforeend", `
    <tr class="task-section-row task-section-row--${section.id}" data-copy-section-collapse="${escapeHtml(options.key || section.id)}" role="button" tabindex="0" aria-expanded="${collapsed ? "false" : "true"}">
      <td colspan="${colspan}">
        <span class="copy-bucket-caret" aria-hidden="true">${collapsed ? "▸" : "▾"}</span>
        <span class="row-glyph row-glyph--${section.tone}" aria-hidden="true"></span>
        <strong>${section.label}</strong>
        <span>${count} 项</span>
        <span class="muted">${collapsed ? "（点击展开）" : "（点击折叠）"}</span>
      </td>
    </tr>
  `);
}

const WORKER_STATUS_PRESENTATION = {
  running: { label: "运行中", tone: "running" },
  paused: { label: "已暂停", tone: "warning" },
  stopped: { label: "已停止", tone: "warning" },
  failed: { label: "运行失败", tone: "error" },
  partial: { label: "部分账号运行中", tone: "warning" },
  offline: { label: "未连接", tone: "error" },
  unknown: { label: "状态未知", tone: "error" },
};

const WORKER_ACTIVITY_PRESENTATION = {
  waiting_for_browser: { label: "等待自动化浏览器", hint: "Worker 会自动拉起浏览器并尝试用绑定的账号登录马帮；若仍在等待，请在 设置 → 马帮连接 绑定账号，或点击「启动自动化浏览器」手动登录。" },
  waiting_for_page_import: { label: "页面导入中 · 已让路", hint: "「从马帮当前页导入」正在进行：采集窗口正被使用，Worker 不重启浏览器也不领取任务，导入确认后自动恢复。" },
  idle: { label: "空闲 · 等待任务", hint: "" },
  saved: { label: "上一单已保存草稿", hint: "" },
  saved_with_warnings: { label: "上一单已保存（有提醒）", hint: "" },
  failed: { label: "上一单执行失败", hint: "" },
  blocked: { label: "上一单已阻止", hint: "" },
};

function formatWorkerActivityAt(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

const AUTOMATION_STAGE_LABELS = {
  apply_main_image_watermark: "主图水印",
  apply_product_image_watermark: "产品图水印",
  video_upload: "商品视频",
  // 执行器表单填充步骤（mabang_playwright_executor.cjs emit('stage') 事件）
  select_store: "正在选择店铺",
  enable_cnsc_auto_publish: "正在开启 CNSC 自动刊登",
  select_cnsc_target_sites: "正在选择目标站点店铺",
  select_category: "正在选择类目",
  set_category_attributes: "正在填写类目属性",
  fill_basic_info: "正在填写基础信息",
  upload_product_images: "正在上传商品主图",
  append_description_media: "正在追加描述图片（尺码表 + 产品图）",
  configure_variants: "正在配置规格（颜色/尺码）",
  disable_sku_weight: "正在关闭 SKU 独立重量",
  fill_sku_grid: "正在填写 SKU",
  fill_cnsc_site_prices_and_discounts: "正在填写站点价格与折扣",
  fill_logistics_info: "正在填写物流信息",
  select_discount_activity: "正在选择折扣活动",
  upload_variant_thumbnails: "正在上传颜色图",
  fix_description_image_widths: "正在修复描述图片尺寸",
  validate_before_save: "正在校验保存前内容",
  save_draft: "正在保存草稿",
  save_draft_rejected: "保存被马帮拒绝",
  close_saved_detail_page: "正在关闭已保存详情页",
  verify_saved_draft: "正在核验草稿保存结果",
  verify_cnsc_draft_inbox: "正在核验 CNSC 草稿箱",
  pre_save_duplicate_check: "正在检查重复父 SKU",
  auto_fill_required_attributes: "正在补齐必填类目属性",
  close_leftover_detail_pages: "正在收尾关闭遗留详情页",
};

const AUTOMATION_EVENT_LABELS = {
  claimed: "Worker 已领取任务",
  heartbeat: "执行进度更新",
  saved: "草稿已保存",
  saved_with_warnings: "草稿已保存（有提醒）",
  blocked: "任务已阻止",
  failed: "任务执行失败",
  needs_confirmation: "需要人工确认",
  lease_recovered: "已回收超时任务",
  lease_exhausted: "领取超时，任务失败",
  requeued: "已在原任务上重排（再次尝试）",
  canceled: "任务已取消",
  attempt: "开始执行",
  attempt_retrying: "重试执行",
  attempt_failed: "本次尝试失败",
  manual_confirmation: "人工确认结果",
  stage: "执行步骤",
};

const AUTOMATION_PHASE_LABELS = {
  load_payload: "加载任务内容",
  playwright_save_draft: "填写表单并保存草稿",
  finish_saved: "确认保存结果",
  finish_failed: "处理执行失败",
};

const LISTING_COPY_READINESS_LABELS = {
  listing_title: "缺少商品标题",
  description: "缺少商品描述",
  description_pending: "描述待生成",
  store_name: "缺少店铺",
  mabang_store_id: "店铺未关联马帮",
  marketplace_category_path: "缺少平台类目",
  mabang_category_id: "平台类目未关联马帮",
  mabang_discount_id: "店铺促销活动未配置",
  promotion_expired: "促销活动已过期，请重新选择",
  promotion_unresolved: "促销活动未解析（促销目录中未找到该活动），执行时可能失败",
  promotion_unconfigured: "活动未配置",
  main_image: "缺少商品主图",
  asset_root: "缺少商品素材目录",
  size_chart_image: "缺少尺码图",
  description_profile_version_id: "请选择描述生成方案",
  stock: "缺少库存",
  weight_kg: "缺少重量",
  package_length_cm: "缺少包裹长度",
  package_width_cm: "缺少包裹宽度",
  package_height_cm: "缺少包裹高度",
  listing_skus: "缺少 SKU 明细",
  listing_prices: "缺少 SKU 价格",
  base_product_cost_price: "基础商品价格表缺成本价，请在基础商品维护中补全",
  default_sku_color: "默认 SKU 缺少颜色名称",
  default_sku_code: "默认 SKU 缺少 SKU 编码",
  default_sku_image: "默认 SKU 缺少颜色图",
  default_sku_price: "默认 SKU 缺少售价或折扣价",
  default_sku_size_price: "默认 SKU 的分尺码价格需同时填写售价和折扣价",
  default_sku_color_conflict: "默认 SKU 颜色与商品颜色重名，请在店铺模板中改颜色名称",
  profile_snapshot_changed: "类目规则已更新，请重新检查副本",
  category_profile_version_expired: "类目规则版本已失效，请重新选择",
  category_profile_missing: "缺少类目方案（该类目的分类属性规则尚未采集），请先采集类目档案",
};

const LISTING_COPY_READINESS_SECTIONS = {
  listing_title: "listing-copy-basic-section",
  description: "listing-copy-basic-section",
  description_pending: "listing-copy-basic-section",
  description_profile_version_id: "listing-copy-basic-section",
  category_profile_missing: "listing-copy-basic-section",
  mabang_discount_id: "listing-copy-basic-section",
  promotion_expired: "listing-copy-basic-section",
  promotion_unresolved: "listing-copy-basic-section",
  promotion_unconfigured: "listing-copy-basic-section",
  main_image: "listing-copy-images-section",
  asset_root: "listing-copy-images-section",
  size_chart_image: "listing-copy-size-chart-section",
  listing_skus: "listing-copy-sku-section",
  listing_prices: "listing-copy-sku-section",
  default_sku_color: "listing-copy-sku-section",
  default_sku_code: "listing-copy-sku-section",
  default_sku_image: "listing-copy-sku-section",
  default_sku_price: "listing-copy-sku-section",
  default_sku_size_price: "listing-copy-sku-section",
  default_sku_color_conflict: "listing-copy-sku-section",
  stock: "listing-copy-transaction-section",
  weight_kg: "listing-copy-transaction-section",
  package_length_cm: "listing-copy-transaction-section",
  package_width_cm: "listing-copy-transaction-section",
  package_height_cm: "listing-copy-transaction-section",
};

const MABANG_NEW_PRODUCT_URL = "https://www.mabangerp.com/index.php?mod=main.gotoApp&v=v3&menuKey=M0010813&platform=shopee&version=1";
const VIDEO_ERROR_LABELS = {
  video_missing: "视频缺失，不影响保存",
  video_format_unsupported: "仅支持 MP4 视频",
  video_file_too_large: "视频文件超过 30 MB",
  video_duration_too_short: "视频时长不能少于 10 秒",
  video_duration_too_long: "视频时长不能超过 60 秒",
  video_resolution_too_large: "视频分辨率不能超过 1280×1280",
  video_metadata_unavailable: "无法读取视频时长或分辨率，将跳过视频上传",
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const contentType = res.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error(data?.error || `Request failed: ${res.status}`);
  return data;
}

// APIs the very first paint needs before the UI is usable: header status,
// deployment readiness banner, dashboard tiles, and the two entity lists
// visible on the default view (base products + store templates). Everything
// else loads in the background right after, but the user can already interact.
const FIRST_PAINT_LOADERS = [
  loadHealth,
  loadReadiness,
  loadMabangStoreOptions,
  loadBaseProducts,
  loadStoreTemplates,
  loadRuntimeSettings,
  loadProjectPlan,
  loadDashboard,
];

// Everything else — heavier lists and reference catalogs that other views need.
// Loaded in the background right after the first paint so navigating to those
// views feels instant, but their absence doesn't block the initial render.
const BACKGROUND_LOADERS = [
  loadCategoryProfiles,
  loadAssets,
  loadStoreListingCopies,
  loadAutomationJobs,
  loadDescriptionProfiles,
];

// 加载器函数名 → 操作员看得懂的名字，失败提示里直接点名是哪几块没加载上。
const LOADER_LABELS = {
  loadHealth: "服务状态",
  loadReadiness: "上架就绪检查",
  loadMabangStoreOptions: "马帮店铺列表",
  loadBaseProducts: "商品列表",
  loadStoreTemplates: "店铺模板列表",
  loadRuntimeSettings: "设置与 Worker 状态",
  loadProjectPlan: "计划",
  loadDashboard: "工作台看板",
  loadCategoryProfiles: "类目方案",
  loadAssets: "素材库",
  loadStoreListingCopies: "店铺刊登副本",
  loadAutomationJobs: "自动化任务",
  loadDescriptionProfiles: "描述模板",
};

function loaderLabel(fn, index) {
  return LOADER_LABELS[fn.name] || fn.name || `加载项 ${index + 1}`;
}

// 本地 API 是一次性突发十几条连接的 HTTP/1.0 服务，忙时个别连接会被拒/被断，
// 浏览器侧只报 "Failed to fetch"。这类失败重试一次就能补上，所以首轮失败先重试，
// 只有重试后仍然失败的才亮红条；重试成功的只在控制台留痕。
async function settleLoaders(loaders) {
  const results = await Promise.allSettled(loaders.map((fn) => fn()));
  const retryIndexes = results.map((result, index) => (result.status === "rejected" ? index : -1)).filter((index) => index >= 0);
  if (!retryIndexes.length) return { failures: [] };

  console.warn(
    "[loading] 首轮失败，1.2s 后重试：",
    retryIndexes.map((index) => `${loaderLabel(loaders[index], index)}: ${results[index].reason?.message || results[index].reason}`),
  );
  await new Promise((resolve) => window.setTimeout(resolve, 1200));

  const retried = await Promise.allSettled(retryIndexes.map((index) => loaders[index]()));
  const failures = [];
  retried.forEach((result, position) => {
    const index = retryIndexes[position];
    if (result.status === "rejected") failures.push({ label: loaderLabel(loaders[index], index), reason: result.reason });
    else console.info(`[loading] 重试成功：${loaderLabel(loaders[index], index)}`);
  });
  return { failures };
}

function reportLoadFailures(failures, prefix) {
  if (!failures.length) return;
  const firstError = failures[0].reason?.message || "数据加载失败";
  console.error(
    `[loading] ${failures.length} 项${prefix}：`,
    failures.map((item) => `${item.label}: ${item.reason?.message || item.reason}`),
  );
  showOperationFeedback(`${failures.length} 项${prefix}：${failures.map((item) => item.label).join("、")}（${firstError}）`, "error");
}

function bootstrapLocalFolderDropZone() {
  // 拖拽区依赖基础商品面板已渲染，放到 bootstrap 之后调用。
  setupLocalFolderDropZone();
}

async function bootstrapEssentials() {
  const { failures } = await settleLoaders(FIRST_PAINT_LOADERS);
  reportLoadFailures(failures, "数据加载失败");
  return { ok: failures.length === 0, failureCount: failures.length };
}

async function refreshRest() {
  const { failures } = await settleLoaders(BACKGROUND_LOADERS);
  reportLoadFailures(failures, "背景数据加载失败");
  return { ok: failures.length === 0, failureCount: failures.length };
}

async function refreshAll() {
  // Kept as-is for the explicit "全部刷新" button and post-rollback reloads:
  // both semantics still want every dataset re-fetched together.
  const essentials = await bootstrapEssentials();
  const rest = await refreshRest();
  return {
    ok: essentials.ok && rest.ok,
    failureCount: essentials.failureCount + rest.failureCount,
  };
}

async function loadDescriptionProfiles() {
  const data = await api("/api/description-profiles");
  state.descriptionProfiles = data.items || [];
  renderStoreTemplateDescriptionProfileOptions();
}

function renderStoreTemplateDescriptionProfileOptions() {
  const select = $("store-template-description-profile");
  if (!select) return;
  const storeId = $("store-template-store")?.value || "";
  const scoped = state.descriptionProfiles.filter((item) => !item.mabang_store_id || item.mabang_store_id === storeId);
  const previous = select.value;
  select.innerHTML = '<option value="">请选择描述模板版本</option>' + scoped.map((item) => {
    const tag = item.mabang_store_id ? "" : "（全店通用）";
    return `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}${tag} · v${escapeHtml(item.version || 1)}</option>`;
  }).join("");
  if (previous && scoped.some((item) => item.id === previous)) select.value = previous;
}

async function loadRuntimeSettings() {
  const [config, connections, workerStatus, profiles, accounts] = await Promise.all([
    api("/api/settings"),
    api("/api/ai/model-connections"),
    api("/api/worker/status"),
    api("/api/description-profiles"),
    // 账号列表读取失败不应该拖垮整个设置页。
    api("/api/mabang/accounts").catch(() => ({ items: [], default_account_id: "" })),
  ]);
  state.runtimeConfiguration = config;
  state.aiModelConnections = connections.items || [];
  state.workerStatus = workerStatus;
  state.descriptionProfiles = profiles.items || [];
  state.mabangAccounts = accounts.items || [];
  state.mabangAccountsDefaultId = accounts.default_account_id || "";
  state.storeTemplateAccountsLoaded = true;
  renderStoreTemplateAccountOptions();
  renderRuntimeSettings();
}

function renderNetworkUnavailable(message) {
  const stateEl = $("settings-network-state");
  if (stateEl) {
    stateEl.textContent = "需要重启 API";
    stateEl.className = "status needs_work";
  }
  const enabledInput = $("settings-network-lan-enabled");
  if (enabledInput) enabledInput.disabled = true;
  const applyButton = $("settings-network-apply-btn");
  if (applyButton) applyButton.disabled = true;
  const refreshButton = $("settings-network-refresh-btn");
  if (refreshButton) refreshButton.disabled = true;
  const firewallButton = $("settings-network-firewall-btn");
  if (firewallButton) firewallButton.disabled = true;
  const root = $("settings-network-urls");
  if (root) root.innerHTML = `<p class="muted">${escapeHtml(message)}</p>`;
  const hints = $("settings-network-hints");
  if (hints) hints.innerHTML = "";
  const bind = $("settings-network-bind");
  if (bind) bind.value = "尚未加载";
  const port = $("settings-network-port");
  if (port) port.value = "";
  const firewallHint = $("settings-network-firewall-hint");
  if (firewallHint) firewallHint.textContent = "重启 API 后再检查防火墙状态。";
}

async function loadNetworkSettings({ refresh = false } = {}) {
  const suffix = refresh ? "?refresh=1" : "";
  try {
    const status = await api(`/api/settings/network${suffix}`);
    state.networkStatus = status;
    renderNetworkSettings();
    return status;
  } catch (error) {
    // 升级后旧 API 进程还没有这个接口：不要让整个设置页报错，给一个明确的
    // “重启一次 API”提示；重启后刷新本页即可恢复。
    if (String(error?.message || error).includes("404")) {
      state.networkStatus = null;
      renderNetworkUnavailable("当前 API 进程还没有局域网接口。重启一次 API 后刷新本页即可启用。");
      return null;
    }
    throw error;
  }
}

function networkStatePresentation(status) {
  if (status.locked_by_env) return { label: "由启动参数指定", tone: "waiting_manual" };
  if (status.rebind_scheduled) return { label: "切换中", tone: "waiting_manual" };
  if (status.restart_required) return { label: "需重启 API", tone: "needs_work" };
  if (status.active) return { label: "局域网已开启", tone: "ready" };
  return { label: "仅本机访问", tone: "" };
}

function currentBrowserHost() {
  try {
    return new URL(window.location.href).host;
  } catch {
    return window.location.host || "";
  }
}

function lanAddressKindLabel(kind) {
  return {
    private: "局域网",
    shared: "共享地址",
    public: "公网/VPN 地址",
    other: "其它地址",
  }[kind] || "地址";
}

function renderNetworkSettings() {
  const status = state.networkStatus;
  if (!status) return;
  const enabledInput = $("settings-network-lan-enabled");
  const applyButton = $("settings-network-apply-btn");
  if (enabledInput) {
    enabledInput.checked = status.enabled === true;
    enabledInput.disabled = status.locked_by_env === true;
  }
  const refreshButton = $("settings-network-refresh-btn");
  if (refreshButton) refreshButton.disabled = false;
  if (applyButton) applyButton.disabled = status.locked_by_env === true || status.rebind_scheduled === true;

  const portInput = $("settings-network-port");
  if (portInput) portInput.value = String(status.port || "");
  const bindInput = $("settings-network-bind");
  if (bindInput) {
    const actual = status.active_bind_host || "-";
    bindInput.value = status.active ? `${actual} · 局域网` : `${actual} · 仅本机`;
  }

  const stateEl = $("settings-network-state");
  if (stateEl) {
    const presentation = networkStatePresentation(status);
    stateEl.textContent = presentation.label;
    stateEl.className = `status ${presentation.tone || ""}`.trim();
  }

  const addressRoot = $("settings-network-urls");
  if (addressRoot) {
    const currentHost = currentBrowserHost();
    const rows = Array.isArray(status.addresses) ? status.addresses.slice() : [];
    if (status.hostname_url && !rows.some((row) => row.url === status.hostname_url)) {
      rows.push({
        ip: "",
        url: status.hostname_url,
        interface: "主机名",
        kind: "hostname",
        reachable: status.active === true,
      });
    }
    addressRoot.innerHTML = rows.length
      ? rows.map((row) => {
        let isCurrent = false;
        try {
          isCurrent = new URL(row.url).host === currentHost;
        } catch {
          isCurrent = false;
        }
        const reachable = row.reachable === true;
        const meta = row.kind === "hostname"
          ? "主机名（局域网 DNS/NetBIOS 可解析时可用）"
          : [row.interface, lanAddressKindLabel(row.kind)].filter(Boolean).join(" · ");
        return `
          <div class="lan-address-row ${reachable ? "" : "is-unreachable"}">
            <div class="lan-address-main">
              <span class="lan-address-url">${escapeHtml(row.url)}</span>
              <span class="lan-address-meta">${escapeHtml(meta)}${reachable ? "" : " · 开启局域网后可用"}</span>
            </div>
            <div class="lan-address-actions">
              ${isCurrent ? '<span class="lan-address-badge">当前页面</span>' : ""}
              <span class="lan-address-badge is-muted">${reachable ? "可访问" : "未开放"}</span>
              <button type="button" class="secondary" data-copy-lan-url="${escapeHtml(row.url)}">复制</button>
            </div>
          </div>`;
      }).join("")
      : '<p class="muted">没有检测到可用的局域网 IPv4 地址。请确认这台机器已连接网络（有线/无线/VPN 均可）。</p>';
  }

  const hintsEl = $("settings-network-hints");
  if (hintsEl) {
    const hints = Array.isArray(status.hints) ? status.hints : [];
    hintsEl.innerHTML = hints.length
      ? `<ul class="lan-hint-list">${hints.map((hint) => `<li>${escapeHtml(hint)}</li>`).join("")}</ul>`
      : "";
  }

  renderNetworkFirewall(status);
}

function networkFirewallCommand(port, ruleName) {
  return `netsh advfirewall firewall add rule name="${ruleName}" dir=in action=allow protocol=TCP localport=${port} profile=private,domain remoteip=LocalSubnet`;
}

function renderNetworkFirewall(status) {
  const section = $("settings-network-firewall-section");
  const hint = $("settings-network-firewall-hint");
  const button = $("settings-network-firewall-btn");
  const result = $("settings-network-firewall-result");
  const firewall = status.firewall || {};
  if (!firewall.supported) {
    if (section) section.hidden = true;
    return;
  }
  if (section) section.hidden = false;
  const port = Number(status.port || firewall.port || 0);
  const ruleName = firewall.rule_name || `AI Listing System API (LAN ${port})`;
  const command = networkFirewallCommand(port, ruleName);
  const exists = firewall.rule_exists === true;
  const allowedBy = Array.isArray(firewall.port_allowed_by) ? firewall.port_allowed_by.filter(Boolean) : [];
  const portOpen = exists || firewall.port_open === true;
  if (hint) {
    if (exists) {
      hint.textContent = `已存在放行规则「${ruleName}」（仅专用/域网络和本地子网）。`;
    } else if (portOpen) {
      hint.textContent = `端口已被现有防火墙规则放行${allowedBy.length ? `（${allowedBy.join("、")}）` : ""}，无需重复添加。`;
    } else if (firewall.is_admin) {
      hint.textContent = "尚未放行。点右侧按钮自动添加规则；规则只允许本地子网访问。";
    } else {
      hint.textContent = "尚未放行。自动添加需要管理员权限；可在服务器上用管理员 PowerShell 执行下面命令。";
    }
  }
  if (button) {
    button.textContent = portOpen ? "已开放" : "开放防火墙端口";
    button.disabled = portOpen;
  }
  if (result && !portOpen && firewall.is_admin === false) {
    result.innerHTML = `<code class="mono">${escapeHtml(command)}</code>`;
  } else if (result && portOpen) {
    result.textContent = "";
  }
}

async function waitForNetworkBinding(expectedActive, timeoutMs = 12000) {
  const started = Date.now();
  // 旧监听在响应发出后约 0.45 秒才关闭；立刻探测会连上旧服务，读到切换前的
  // 状态（看起来像“需重启”）。先等过这个窗口，再按目标监听状态轮询。
  await new Promise((resolve) => window.setTimeout(resolve, 800));
  while (Date.now() - started < timeoutMs) {
    try {
      const status = await api("/api/settings/network?refresh=1");
      if (status.active === expectedActive) {
        state.networkStatus = status;
        renderNetworkSettings();
        return status;
      }
    } catch {
      // 重绑期间连接会被短暂拒绝，继续等。
    }
    await new Promise((resolve) => window.setTimeout(resolve, 350));
  }
  return null;
}

async function applyNetworkSettings() {
  const enabledInput = $("settings-network-lan-enabled");
  const result = $("settings-network-result");
  const button = $("settings-network-apply-btn");
  if (!enabledInput) return;
  const wanted = enabledInput.checked === true;
  // 远程页面关闭局域网会把自己锁在外面：先确认，避免误点。
  const hostname = window.location.hostname || "";
  const loopback = ["127.0.0.1", "localhost", "::1", "[::1]"].includes(hostname);
  if (!wanted && !loopback && !window.confirm("关闭后当前远程页面会立即断开，只能在这台机器上重新开启局域网访问。确定关闭？")) {
    return;
  }
  if (button instanceof HTMLButtonElement) button.disabled = true;
  if (result) result.textContent = "正在应用网络设置…";
  try {
    const status = await api("/api/settings/network", {
      method: "PUT",
      body: JSON.stringify({ lan_enabled: wanted }),
    });
    state.networkStatus = status;
    renderNetworkSettings();
    if (result) result.textContent = status.message || "网络设置已应用。";
    if (status.rebind_scheduled) {
      const recovered = await waitForNetworkBinding(wanted);
      if (!recovered) {
        await loadNetworkSettings({ refresh: true }).catch(() => {});
      }
      if (result) {
        result.textContent = recovered
          ? (wanted ? "局域网访问已开启，下面的地址现在可以从其它设备打开。" : "局域网访问已关闭，当前仅本机可访问。")
          : "设置已保存，但服务重新监听超时。请刷新页面或重启 API 检查端口是否被占用。";
      }
    }
  } catch (error) {
    if (result) result.textContent = `应用失败：${error.message || error}`;
  } finally {
    if (button instanceof HTMLButtonElement) {
      const current = state.networkStatus;
      button.disabled = current?.locked_by_env === true || current?.rebind_scheduled === true;
    }
  }
}

async function openNetworkFirewall() {
  const result = $("settings-network-firewall-result");
  const button = $("settings-network-firewall-btn");
  if (result) result.textContent = "正在添加防火墙规则…";
  if (button instanceof HTMLButtonElement) button.disabled = true;
  try {
    const data = await api("/api/settings/network/firewall", { method: "POST", body: "{}" });
    await loadNetworkSettings({ refresh: true });
    const current = state.networkStatus;
    const message = data.message || (data.ok ? "已开放防火墙端口。" : "添加防火墙规则失败。");
    if (result) {
      if (!data.ok && current?.firewall?.is_admin === false) {
        const port = Number(data.port || current?.port || 0);
        const ruleName = data.rule_name || current?.firewall?.rule_name || `AI Listing System API (LAN ${port})`;
        result.innerHTML = `<span>${escapeHtml(message)}</span><br><code class="mono">${escapeHtml(networkFirewallCommand(port, ruleName))}</code>`;
      } else {
        result.textContent = message;
      }
    }
  } catch (error) {
    if (result) result.textContent = `添加失败：${error.message || error}`;
  } finally {
    const current = state.networkStatus;
    if (button instanceof HTMLButtonElement) {
      button.disabled = current?.firewall?.rule_exists === true || current?.firewall?.port_open === true;
    }
  }
}

function copyTextToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  return new Promise((resolve, reject) => {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.top = "-1000px";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    try {
      const copied = document.execCommand("copy");
      if (copied) resolve();
      else reject(new Error("浏览器不允许复制"));
    } catch (error) {
      reject(error);
    } finally {
      document.body.removeChild(textarea);
    }
  });
}

async function copyLanAddress(text, button) {
  try {
    await copyTextToClipboard(text);
    if (button instanceof HTMLElement) {
      const original = button.textContent;
      button.textContent = "已复制";
      window.setTimeout(() => { button.textContent = original; }, 1200);
    }
  } catch (error) {
    operationError(error, "复制失败");
  }
}

async function loadDashboard() {
  const dashboard = await api("/api/dashboard");
  const metrics = dashboard.metrics || {};
  // Legacy metric spans still exist behind a hidden shim so old refs stay resolvable.
  const setText = (id, value) => {
    const el = $(id);
    if (!el) return;
    el.textContent = String(value);
    // 写进真实数字才算加载完成：初始的 data-loading-metric 标记同时摘掉，
    // 免得首屏前这些指标被当成「0 条」读走。
    el.removeAttribute("data-loading-metric");
  };
  setText("metric-products", metrics.base_products || 0);
  setText("metric-assets", metrics.listing_copies || 0);
  setText("metric-exportable", metrics.ready_jobs || 0);
  setText("metric-status", metrics.failed_jobs ? `${metrics.failed_jobs} 个失败` : "运行正常");
  updatePipeline(metrics);

  renderProductionStats(dashboard.production || {});
  renderProductionSparkline(dashboard.production || {});

  const batch = dashboard.current_batch || {};
  setText("dashboard-batch-status", "");
  const batchEl = $("dashboard-current-batch");
  if (batchEl) batchEl.innerHTML = `<span>活动任务：${escapeHtml(batch.active_jobs || 0)}</span><span>待领取：${escapeHtml(batch.ready_jobs || 0)}</span><span>人工确认：${escapeHtml(batch.needs_confirmation || 0)}</span>`;
  const action = dashboard.next_action || {};
  const actionEl = $("dashboard-next-action");
  if (actionEl) actionEl.innerHTML = `<strong>${escapeHtml(action.label || "暂无待办")}</strong>${action.type ? `<button type="button" data-dashboard-action="${escapeHtml(action.type)}" data-dashboard-id="${escapeHtml(action.id || "")}">打开</button>` : ""}`;
  const recentEl = $("dashboard-recent-results");
  if (recentEl) recentEl.innerHTML = (dashboard.recent_results || []).slice(0, 5).map((item) => `<span>${escapeHtml(item.store_name || item.id || "任务")} · ${escapeHtml(TASK_STATUS_LABELS[item.status] || item.status || "")}</span>`).join("") || '<span class="muted">暂无结果</span>';
  const connections = dashboard.connections || {};
  const connEl = $("dashboard-connections");
  if (connEl) connEl.innerHTML = `<span>Worker：${escapeHtml(connections.worker || "unknown")}</span><span>马帮：${escapeHtml(connections.mabang || "unknown")}</span><span>AI 连接：${escapeHtml(connections.ai_connections || 0)}</span>`;
}

function renderProductionStats(production) {
  const setText = (id, value) => { const el = $(id); if (el) el.textContent = String(value); };
  setText("production-today-date", production.today ? `${production.today} · UTC · 生产日志` : "–");
  const todaySaved = production.today_saved ?? 0;
  setText("stat-today-saved", todaySaved);
  // Hero hint text reads naturally with the big number: "0 today · 12 this week"
  const weekSaved = production.week_saved ?? 0;
  const todayNewCopies = production.today_new_copies ?? 0;
  setText("stat-today-saved-hint", weekSaved
    ? `本周累计 ${weekSaved} 单${todayNewCopies ? ` · 今日新增副本 ${todayNewCopies}` : ""}`
    : (todayNewCopies ? `今日新增副本 ${todayNewCopies} · 尚未产出草稿` : "尚未开始"));
  setText("stat-today-failed", production.today_failed ?? 0);
  setText("stat-today-failed-hint", `本周 ${production.week_failed ?? 0}`);
  setText("stat-attention", production.today_pending_attention ?? 0);
  const rate = production.week_success_rate;
  setText("stat-success-rate", rate == null ? "–" : `${rate}%`);
  setText("stat-success-rate-hint", rate == null ? "本周暂无终态任务" : `${weekSaved} / ${weekSaved + (production.week_failed ?? 0)}`);
  setText("stat-week-saved", weekSaved);
  setText("stat-week-saved-hint", `近 7 天累计`);
}

function renderProductionSparkline(production) {
  const savedSeries = Array.isArray(production.daily_saved) ? production.daily_saved : [];
  const failedSeries = Array.isArray(production.daily_failed) ? production.daily_failed : [];
  const svg = $("production-sparkline");
  const savedPolyline = $("production-sparkline-saved");
  const failedPolyline = $("production-sparkline-failed");
  const axisGroup = $("production-sparkline-axis");
  const summary = $("production-trend-summary");
  if (!svg || !savedPolyline || !failedPolyline || !axisGroup) return;
  const width = 700;
  const height = 160;
  const padding = { top: 16, right: 16, bottom: 30, left: 40 };
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;
  const points = savedSeries.length || 1;
  const maxValue = Math.max(
    1,
    ...savedSeries.map((d) => Number(d.count || 0)),
    ...failedSeries.map((d) => Number(d.count || 0)),
  );

  const toPoints = (series) => series.map((d, i) => {
    const x = padding.left + (points === 1 ? chartW / 2 : (chartW * i) / (points - 1));
    const y = padding.top + chartH - (Number(d.count || 0) / maxValue) * chartH;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");

  savedPolyline.setAttribute("points", toPoints(savedSeries));
  failedPolyline.setAttribute("points", toPoints(failedSeries));

  // Rebuild axis labels: date on x, max scale on y.
  axisGroup.innerHTML = "";
  savedSeries.forEach((d, i) => {
    const x = padding.left + (points === 1 ? chartW / 2 : (chartW * i) / (points - 1));
    const label = String(d.date || "").slice(5); // MM-DD
    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("x", x.toFixed(1));
    text.setAttribute("y", (height - 6).toFixed(1));
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("font-family", "var(--font-mono)");
    text.setAttribute("font-size", "10");
    text.setAttribute("fill", "var(--muted)");
    text.textContent = label;
    axisGroup.appendChild(text);
  });
  const maxLabel = document.createElementNS("http://www.w3.org/2000/svg", "text");
  maxLabel.setAttribute("x", (padding.left - 6).toFixed(1));
  maxLabel.setAttribute("y", (padding.top + 4).toFixed(1));
  maxLabel.setAttribute("text-anchor", "end");
  maxLabel.setAttribute("font-family", "var(--font-mono)");
  maxLabel.setAttribute("font-size", "10");
  maxLabel.setAttribute("fill", "var(--muted)");
  maxLabel.textContent = String(maxValue);
  axisGroup.appendChild(maxLabel);

  if (summary) {
    const totalSaved = savedSeries.reduce((sum, d) => sum + Number(d.count || 0), 0);
    const totalFailed = failedSeries.reduce((sum, d) => sum + Number(d.count || 0), 0);
    summary.textContent = `saved ${totalSaved} · failed ${totalFailed} · 峰值 ${maxValue}`;
  }
}

function updatePipeline(metrics = {}) {
  const scan = Number(metrics.scanned_assets ?? metrics.assets ?? 0);
  const products = Number(metrics.base_products ?? 0);
  const templates = Number(metrics.store_templates ?? 0);
  const copies = Number(metrics.listing_copies ?? 0);
  const jobs = Number(metrics.ready_jobs ?? 0);
  const counts = { scan, products, templates, copies, jobs };
  const stages = [
    ["pipeline-scan", scan],
    ["pipeline-products", products],
    ["pipeline-templates", templates],
    ["pipeline-copies", copies],
    ["pipeline-jobs", jobs],
  ];
  stages.forEach(([id, value]) => {
    const el = $(id);
    if (el) el.textContent = value;
  });
  // The active stage is the furthest one that still has items waiting to move forward.
  const order = ["scan", "products", "templates", "copies", "jobs"];
  let activeIndex = 0;
  order.forEach((key, index) => {
    if (counts[key] > 0) activeIndex = index;
  });
  document.querySelectorAll(".pipeline-stage").forEach((stage, index) => {
    stage.classList.toggle("active", index === activeIndex);
    stage.classList.toggle("done", index < activeIndex);
  });
}

function renderRuntimeSettings() {
  const config = state.runtimeConfiguration;
  if (!config) return;
  $("settings-mabang-url").value = config.mabang?.workspace_url || "";
  $("settings-mabang-status").textContent = config.mabang?.session_status === "connected" ? "会话已连接" : "登录状态将在 Worker 执行时确认";
  // 采集浏览器端口由系统按账号分配（见 马帮账号）；这里不再向操作员暴露配置入口。
  const mabangAccount = $("settings-mabang-account");
  if (mabangAccount) mabangAccount.value = config.mabang?.login_account || "";
  const mabangHeadless = $("settings-mabang-headless");
  if (mabangHeadless) mabangHeadless.checked = config.mabang?.headless === true;
  const mabangLoginStatus = $("settings-mabang-login-status");
  if (mabangLoginStatus) {
    mabangLoginStatus.textContent = config.mabang?.login_configured
      ? `已绑定自动登录账号：${config.mabang?.login_account || ""}`
      : "未绑定自动登录账号";
  }
  renderMabangAccounts();
  $("settings-worker-poll-seconds").value = config.worker?.poll_seconds || 3;
  $("settings-worker-lease-seconds").value = config.worker?.lease_seconds || 120;
  $("settings-worker-max-attempts").value = config.worker?.max_attempts || 3;
  const workerSlots = $("settings-worker-slots");
  if (workerSlots) workerSlots.value = config.worker?.slots_per_account || 1;
  $("settings-worker-auto-restart").checked = config.worker?.auto_restart !== false;
  const workerOnDemand = $("settings-worker-on-demand");
  if (workerOnDemand) workerOnDemand.checked = config.worker?.start_on_demand !== false;
  const workerIdleMinutes = $("settings-worker-idle-minutes");
  if (workerIdleMinutes) workerIdleMinutes.value = config.worker?.idle_shutdown_minutes ?? 10;
  $("settings-worker-restart").textContent = config.worker?.restart_required ? "需要重启 Worker" : "配置已加载";
  const worker = state.workerStatus || {};
  const onDemand = worker.on_demand !== false;
  // 按需启动下「已停止」是正常状态（有任务会自动唤醒），不该显示成告警。
  const presentation = WORKER_STATUS_PRESENTATION[worker.status] || WORKER_STATUS_PRESENTATION.unknown;
  const expectedOff = onDemand && worker.status === "stopped" && !worker.manual_hold_at;
  const workerPresentation = expectedOff ? { label: "已关闭（按需启动）", tone: "idle" } : presentation;
  const workerStatus = $("settings-worker-status");
  const activityPresentation = WORKER_ACTIVITY_PRESENTATION[worker.activity];
  const activitySuffix = activityPresentation ? ` · ${activityPresentation.label}` : "";
  const activityAt = formatWorkerActivityAt(worker.activity_at);
  const onDemandSuffix = onDemand
    ? ` · 按需启动${worker.idle_shutdown_minutes ? `（空闲 ${worker.idle_shutdown_minutes} 分钟自动关闭）` : "（不自动关闭）"}`
    : "";
  const holdSuffix = worker.manual_hold_at ? " · 已手动停止（不会自动唤醒，新任务或点「启动」才恢复）" : "";
  const workerDetails = `${workerPresentation.label}${worker.pid ? `（PID ${worker.pid}）` : ""}${activitySuffix}${activityAt ? ` · 最近活动 ${activityAt}` : ""}${onDemandSuffix}${holdSuffix}${worker.last_error ? ` · ${worker.last_error}` : ""}`;
  workerStatus.className = `worker-status worker-status-${workerPresentation.tone}`;
  workerStatus.innerHTML = `<span class="worker-status-dot" aria-hidden="true"></span><span>${escapeHtml(workerDetails)}</span>`;
  // 多账号/多槽位并行：每个目标一个 Worker，各自独立的进程/日志/CDP 端口。
  const workerAccounts = $("settings-worker-accounts");
  if (workerAccounts) {
    const rows = Array.isArray(worker.workers) ? worker.workers : [];
    workerAccounts.innerHTML = rows.length
      ? rows.map((row) => {
        const account = (state.mabangAccounts || []).find((item) => item.id === row.account_id);
        const presentation = WORKER_STATUS_PRESENTATION[row.status] || WORKER_STATUS_PRESENTATION.unknown;
        const rowActivity = WORKER_ACTIVITY_PRESENTATION[row.activity];
        const slot = Number(row.slot || 0);
        const label = `${account?.name || row.account_id}${slot > 0 ? ` · 并行槽位 ${slot + 1}` : ""}`;
        const details = [presentation.label, rowActivity ? rowActivity.label : "", row.pid ? `PID ${row.pid}` : "", row.manual_hold_at ? "已手动停止（不会自动唤醒）" : "", row.last_error || ""].filter(Boolean).join(" · ");
        return `<div class="settings-connection-row"><div><strong>${escapeHtml(label)}</strong><span>${escapeHtml(details)}</span></div><div class="settings-connection-actions"><button type="button" class="secondary" data-worker-logs-account="${escapeHtml(row.account_id || "")}" data-worker-logs-slot="${slot}">日志</button></div></div>`;
      }).join("")
      : '<p class="muted">单 Worker 模式：只有一个进程，按任务自动切换账号；新增第二个账号或把「每账号并行槽位」调到 2 就会按账号 × 槽位拆分进程并行运行。</p>';
  }
  const workerBrowserStatus = $("settings-worker-browser-status");
  if (workerBrowserStatus && activityPresentation && activityPresentation.hint && !workerBrowserStatus.textContent) {
    workerBrowserStatus.textContent = activityPresentation.hint;
  }
  $("settings-nas-root").value = config.nas?.asset_root || "";
  $("settings-nas-scan-mode").value = config.nas?.scan_mode || "auto";
  $("settings-nas-max-files").value = config.nas?.max_files || 3000;
  $("settings-collection-enabled").checked = config.store_collection?.enabled !== false;
  $("settings-collection-interval").value = config.store_collection?.interval_minutes || 360;
  $("settings-collection-max-attempts").value = config.store_collection?.max_attempts || 3;
  $("settings-auto-focus-import").checked = config.store_collection?.auto_focus_after_import !== false;
  $("settings-save-draft-only").checked = config.safety?.save_draft_only === true;
  $("settings-publish-allowed").checked = config.safety?.publish_allowed === true;
  const aiGen = config.ai_listing_generation || {};
  const genTitleEl = $("settings-generate-title-on-import");
  const genDescEl = $("settings-generate-description-on-import");
  const fillAttrEl = $("settings-fill-attributes-into-description");
  if (genTitleEl) genTitleEl.checked = aiGen.generate_title_on_import === true;
  if (genDescEl) genDescEl.checked = aiGen.generate_description_on_import === true;
  if (fillAttrEl) fillAttrEl.checked = aiGen.fill_attributes_into_description !== false;
  const skuCfg = config.product_sku || {};
  const skuAutoEl = $("settings-sku-auto-assign");
  const skuPrefixEl = $("settings-sku-prefix");
  const skuWidthEl = $("settings-sku-number-width");
  const skuNextEl = $("settings-sku-next-number");
  if (skuAutoEl) skuAutoEl.checked = skuCfg.auto_assign_on_import === true;
  if (skuPrefixEl) skuPrefixEl.value = skuCfg.prefix || "";
  if (skuWidthEl) skuWidthEl.value = skuCfg.number_width || 4;
  if (skuNextEl) skuNextEl.value = skuCfg.next_number || 1;
  const cleanup = config.storage_cleanup || {};
  $("settings-storage-auto-enabled").checked = cleanup.enabled !== false;
  $("settings-storage-temp-retention").value = cleanup.temp_retention_days || 7;
  $("settings-storage-screenshot-retention").value = cleanup.screenshot_retention_days || 30;
  $("settings-storage-export-retention").value = cleanup.export_retention_days || 90;
  $("settings-storage-log-retention").value = cleanup.log_retention_days || 30;
  $("settings-storage-assets-retention").value = cleanup.assets_retention_days || 30;
  $("settings-storage-compact-enabled").checked = cleanup.compact_enabled !== false;
  $("settings-storage-compact-delay").value = cleanup.compact_delay_days || 3;
  $("settings-storage-compact-edge").value = cleanup.compact_max_edge || 1000;
  $("settings-storage-compact-quality").value = cleanup.compact_jpeg_quality || 88;
  $("settings-storage-assets-cleanup-enabled").checked = cleanup.assets_cleanup_enabled !== false;
  const connections = $("settings-ai-connections");
  connections.innerHTML = state.aiModelConnections.length ? state.aiModelConnections.map((item) => {
    const balanceLine = renderAiConnectionBalanceLine(item);
    return `
    <div class="settings-connection-row">
      <div>
        <strong>${escapeHtml(item.name || item.provider || "未命名连接")}</strong>
        <span>${item.protocol === "openai_chat_completions" ? "OpenAI Chat Completions 兼容" : item.protocol === "chat_completions" ? "Chat Completions（/chat/completions）" : "Anthropic Messages"} · ${escapeHtml(item.model || item.model_id || "")}</span>
        ${balanceLine}
      </div>
      <div class="settings-connection-actions"><span class="status ${item.is_default ? "ready" : ""}">${item.is_default ? "默认模型" : item.api_key_configured ? "密钥已配置" : "未配置密钥"}</span><button type="button" data-test-ai-connection="${escapeHtml(item.id || "")}">测试</button>${aiConnectionSupportsBalance(item) ? `<button type="button" data-refresh-ai-balance="${escapeHtml(item.id || "")}">查余额</button>` : ""}<button type="button" data-edit-ai-connection="${escapeHtml(item.id || "")}">编辑</button>${item.is_default ? "" : `<button type="button" data-default-ai-connection="${escapeHtml(item.id || "")}">设为默认</button><button type="button" data-delete-ai-connection="${escapeHtml(item.id || "")}">删除</button>`}</div>
    </div>`;
  }).join("") : '<p class="muted">暂无 AI 模型连接，请先在模型连接接口中添加。</p>';
  // 颜色识别专用连接：空值 = 跟随默认连接（默认项显示默认连接名，便于确认）
  const colorSelect = $("settings-ai-color-connection");
  if (colorSelect) {
    const defaultConnection = state.aiModelConnections.find((item) => item.is_default) || {};
    const currentColorId = String(config.ai?.color_connection_id || "");
    colorSelect.innerHTML = [
      `<option value="">跟随默认连接${defaultConnection.name ? `（${escapeHtml(defaultConnection.name)}）` : ""}</option>`,
      ...state.aiModelConnections.map((item) => `<option value="${escapeHtml(item.id || "")}">${escapeHtml(item.name || item.model || item.model_id || "未命名连接")}${item.is_default ? "（默认）" : ""}</option>`),
    ].join("");
    colorSelect.value = state.aiModelConnections.some((item) => String(item.id) === currentColorId) ? currentColorId : "";
  }
  const profiles = $("settings-description-profiles");
  const DESCRIPTION_MODE_LABELS = { combined: "拼接模板", full: "AI 全文", sectioned: "分区模板" };
  profiles.innerHTML = state.descriptionProfiles.length ? state.descriptionProfiles.map((item) => {
    const scope = item.mabang_store_id
      ? `<span class="scope-tag scope-tag--store">${escapeHtml(item.store_name || item.mabang_store_id)}</span>`
      : `<span class="scope-tag scope-tag--global">全店通用</span>`;
    const modeLabel = DESCRIPTION_MODE_LABELS[item.mode] || item.mode || "?";
    const sectionCount = item.mode === "sectioned" ? ` · ${((item.sections || []).length)} 个分区` : "";
    return `
    <div class="settings-connection-row">
      <div><strong>${escapeHtml(item.name || "未命名方案")}</strong><span>${scope} · ${modeLabel}${sectionCount} · 版本 ${escapeHtml(item.version || 1)}</span></div>
      <div class="settings-connection-actions"><span class="status ${item.is_default ? "ready" : ""}">${item.is_default ? "默认方案" : item.enabled === false ? "已停用" : "可用"}</span><button type="button" data-edit-description-profile="${escapeHtml(item.id || "")}">编辑</button>${item.is_default ? "" : `<button type="button" data-default-description-profile="${escapeHtml(item.id || "")}">设为默认</button>`}<button type="button" data-delete-description-profile="${escapeHtml(item.id || "")}">删除</button></div>
    </div>`;
  }).join("") : '<p class="muted">暂无描述方案。点「新增方案」创建：推荐先用默认的「拼接模板」，固定开场+售后由你填，中间商品段 AI 每次现写。</p>';
  const collected = config.store_collection?.last_collected_at || {};
  // 采集时间按「账号|店铺」记账：拆开显示成「账号名 · 店铺」，便于分辨跨账号同 id 的店铺。
  $("settings-store-collection-status").innerHTML = Object.keys(collected).length
    ? Object.entries(collected).map(([key, time]) => {
      const [accountId, storeId] = String(key).includes("|") ? String(key).split("|") : ["", String(key)];
      const account = (state.mabangAccounts || []).find((item) => item.id === accountId);
      const label = accountId ? `${account?.name || accountId} · ${storeId}` : storeId;
      return `<div class="settings-connection-row"><strong>${escapeHtml(label)}</strong><span>${escapeHtml(time || "未采集")}</span></div>`;
    }).join("")
    : '<p class="muted">暂无店铺采集记录。</p>';
  const crossBorderToggle = $("settings-cross-border-mode");
  if (crossBorderToggle) crossBorderToggle.checked = crossBorderModeEnabled();
  // 跨境模式开关变更要当场应用到各界面（模板/副本列表、复制选择器、编辑器等）。
  applyCrossBorderUi();
}

function formatStorageBytes(value) {
  const bytes = Math.max(0, Number(value) || 0);
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let amount = bytes / 1024;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index += 1; }
  return `${amount >= 10 ? amount.toFixed(1) : amount.toFixed(2)} ${units[index]}`;
}

function renderStorageUsage() {
  const root = $("settings-storage-usage");
  const usage = state.storageUsage;
  if (!root) return;
  if (!usage) {
    root.innerHTML = '<p class="muted">尚未扫描存储占用。</p>';
    updateStorageSelectionSummary();
    return;
  }
  const categories = Object.values(usage.categories || {});
  const lastCleanup = usage.last_cleanup?.last_cleanup || usage.last_cleanup?.last_result || null;
  const lastWasAutomatic = lastCleanup?.automatic ?? Boolean(usage.last_cleanup?.last_auto_cleanup_at);
  const lastCompaction = usage.last_cleanup?.last_compaction || null;
  // 历史记录用「标签 + 数值」两列，比原来四散的三行说明句更容易扫读。
  const historyRows = [];
  if (lastCleanup) {
    const failures = lastCleanup.error_count ? ` <span class="storage-history-warn">${lastCleanup.error_count} 项失败</span>` : "";
    historyRows.push(`<div class="storage-history-row"><dt>上次${lastWasAutomatic ? "自动" : "手动"}清理</dt><dd>释放 <b>${formatStorageBytes(lastCleanup.freed_bytes)}</b> · 删除 ${lastCleanup.deleted_files || 0} 个文件${failures}</dd></div>`);
  }
  if (lastCompaction) {
    const failures = lastCompaction.error_count ? ` <span class="storage-history-warn">${lastCompaction.error_count} 项失败</span>` : "";
    const detail = lastCompaction.skipped_reason === "disabled"
      ? "设置里已关闭"
      : `${lastCompaction.files || 0} 张图（${lastCompaction.products || 0} 个商品） · 释放 <b>${formatStorageBytes(lastCompaction.saved_bytes)}</b>`;
    historyRows.push(`<div class="storage-history-row"><dt>上次压缩</dt><dd>${detail}${failures}</dd></div>`);
  }
  const historyBlock = historyRows.length
    ? historyRows.join("")
    : '<div class="storage-history-row"><dt>清理记录</dt><dd>还没有清理或压缩记录，执行后会显示在这里。</dd></div>';
  const categoryRows = categories.map((item) => {
    const reclaimable = Number(item.reclaimable_bytes || 0);
    const idle = reclaimable <= 0;
    const manualTag = item.automatic === false ? '<span class="storage-tag">仅手动</span>' : "";
    const sizeNote = idle
      ? "暂无可释放文件"
      : `可释放 ${formatStorageBytes(reclaimable)} · ${item.reclaimable_file_count || 0} 个文件`;
    return `
      <label class="storage-cleanup-item${idle ? " is-idle" : ""}">
        <input type="checkbox" data-storage-cleanup-category="${escapeHtml(item.id)}" ${idle ? "disabled" : ""} />
        <span class="storage-item-main">
          <span class="storage-item-title"><strong>${escapeHtml(item.label)}</strong>${manualTag}</span>
          <small>${escapeHtml(item.protected_note || "")}</small>
        </span>
        <span class="storage-cleanup-size">
          <strong>${formatStorageBytes(item.total_bytes)}</strong>
          <small>${sizeNote}</small>
        </span>
      </label>`;
  }).join("");
  root.innerHTML = `
    <div class="storage-summary">
      <div class="storage-summary-total">
        <span class="storage-kicker">已扫描目录占用</span>
        <strong>${formatStorageBytes(usage.total_bytes)}</strong>
        <span class="storage-reclaimable">按当前策略可释放 <b>${formatStorageBytes(usage.reclaimable_bytes)}</b></span>
      </div>
      <dl class="storage-history">${historyBlock}</dl>
    </div>
    <div class="storage-category-head">
      <h3>清理项目</h3>
      <span class="muted small">勾选后点底部「清理所选项目」，灰色项当前没有可释放文件</span>
    </div>
    <div class="storage-category-list">${categoryRows}</div>`;
  updateStorageSelectionSummary();
}

// 勾选态在按钮左侧给一句「已选几项、预计释放多少」，避免点清理前还要自己心算。
function updateStorageSelectionSummary() {
  const label = $("settings-storage-selection");
  if (!label) return;
  const selected = Array.from(document.querySelectorAll("[data-storage-cleanup-category]:checked"));
  if (!selected.length) {
    label.textContent = "勾选上方项目后再清理";
    label.classList.add("is-empty");
    return;
  }
  const rows = selected
    .map((item) => state.storageUsage?.categories?.[item.dataset.storageCleanupCategory || ""])
    .filter(Boolean);
  const reclaimable = rows.reduce((sum, item) => sum + Number(item.reclaimable_bytes || 0), 0);
  label.textContent = `已选 ${selected.length} 项 · 预计释放 ${formatStorageBytes(reclaimable)}`;
  label.classList.remove("is-empty");
}

document.addEventListener("change", (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement && target.matches("[data-storage-cleanup-category]")) {
    updateStorageSelectionSummary();
  }
});

async function loadStorageUsage() {
  const button = $("settings-storage-refresh-btn");
  if (button) button.disabled = true;
  try {
    state.storageUsage = await api("/api/settings/storage/usage");
    renderStorageUsage();
  } finally {
    if (button) button.disabled = false;
  }
}

async function cleanupSelectedStorage() {
  const button = $("settings-storage-clean-btn");
  const result = $("settings-storage-result");
  const selected = Array.from(document.querySelectorAll("[data-storage-cleanup-category]:checked")).map((item) => item.dataset.storageCleanupCategory || "");
  if (!selected.length) { result.textContent = "请先选择至少一个可清理项目。"; return; }
  const rows = selected.map((id) => state.storageUsage?.categories?.[id]).filter(Boolean);
  const reclaimable = rows.reduce((sum, item) => sum + Number(item.reclaimable_bytes || 0), 0);
  const labels = rows.map((item) => item.label).join("、");
  // 素材清理不可逆：删掉的本地素材无法再用于上款，需要重新导入。
  const assetWarning = selected.some((id) => id === "product_assets" || id === "product_assets_legacy")
    ? "\n被清理的素材无法再用于上款，该商品需要重新导入素材。"
    : "";
  if (!window.confirm(`确认清理 ${labels}？预计释放 ${formatStorageBytes(reclaimable)}。商品资料、备份和马帮登录状态会保留。${assetWarning}`)) return;
  button.disabled = true;
  result.textContent = "正在清理，请勿重复操作…";
  try {
    const cleaned = await api("/api/settings/storage/cleanup", { method: "POST", body: JSON.stringify({ categories: selected, confirm: true }) });
    const suffix = cleaned.errors?.length ? `，${cleaned.errors.length} 项失败` : cleaned.skipped?.length ? `，${cleaned.skipped.length} 项跳过` : "";
    result.textContent = `已释放 ${formatStorageBytes(cleaned.freed_bytes)}，删除 ${cleaned.deleted_files || 0} 个文件${suffix}。`;
    await loadStorageUsage();
  } finally {
    button.disabled = false;
  }
}

async function compactStorageAssets() {
  const button = $("settings-storage-compact-btn");
  const result = $("settings-storage-compact-result");
  if (!window.confirm("立即压缩已上架完成且超过延后期的商品素材？\nPNG 会转成同名 JPEG（按设置缩放长边），原图不可恢复；未完成上款、回滚过、有在途任务或素材在 NAS 的商品会自动跳过。")) return;
  button.disabled = true;
  result.textContent = "正在压缩素材，请勿离开页面…";
  try {
    const report = await api("/api/settings/storage/compact", { method: "POST", body: JSON.stringify({ confirm: true }) });
    if (report.skipped_reason === "disabled") {
      result.textContent = "设置里已关闭「上架完成后自动压缩商品素材」，请先勾选再试。";
    } else {
      const suffix = report.errors?.length ? `，${report.errors.length} 项失败` : "";
      result.textContent = `已压缩 ${report.files || 0} 张图（${report.products || 0} 个商品），释放 ${formatStorageBytes(report.saved_bytes)}${suffix}。`;
    }
    await loadStorageUsage();
  } finally {
    button.disabled = false;
  }
}

function syncAiConnectionProtocolFields() {  $("ai-connection-version-field").hidden = true;
  const baseUrl = $("ai-connection-base-url");
  if (baseUrl) {
    baseUrl.placeholder = $("ai-connection-protocol").value === "chat_completions"
      ? "https://api.example.com/v1/chat/completions（完整端点地址）"
      : "https://api.example.com/v1";
  }
}

function openAiConnectionDialog() {
  state.editingAiConnectionId = "";
  $("ai-connection-name").value = "";
  $("ai-connection-protocol").value = "anthropic_messages";
  $("ai-connection-base-url").value = "";
  $("ai-connection-model-id").value = "";
  $("ai-connection-api-version").value = "2023-06-01";
  $("ai-connection-timeout").value = "60";
  $("ai-connection-api-key").value = "";
  $("ai-connection-api-key").placeholder = "";
  $("ai-connection-result").textContent = "";
  syncAiConnectionProtocolFields();
  $("ai-connection-dialog")?.showModal();
}

function openAiConnectionEditor(connectionId) {
  const connection = state.aiModelConnections.find((item) => item.id === connectionId);
  if (!connection) return;
  state.editingAiConnectionId = connectionId;
  $("ai-connection-name").value = connection.name || "";
  $("ai-connection-protocol").value = connection.protocol || "anthropic_messages";
  $("ai-connection-base-url").value = connection.base_url || "";
  $("ai-connection-model-id").value = connection.model_id || "";
  $("ai-connection-api-version").value = connection.api_version || "2023-06-01";
  $("ai-connection-timeout").value = connection.timeout_seconds || 60;
  $("ai-connection-api-key").value = "";
  $("ai-connection-api-key").placeholder = "留空则不更换";
  $("ai-connection-result").textContent = "";
  syncAiConnectionProtocolFields();
  $("ai-connection-dialog")?.showModal();
}

async function saveAiConnection() {
  const result = $("ai-connection-result");
  const saveButton = $("save-ai-connection-btn");
  saveButton.disabled = true;
  result.textContent = "正在保存...";
  const protocol = $("ai-connection-protocol").value;
  const payload = {
    name: $("ai-connection-name").value.trim(),
    protocol,
    base_url: $("ai-connection-base-url").value.trim(),
    model_id: $("ai-connection-model-id").value.trim(),
    api_version: "",
    timeout_seconds: Number($("ai-connection-timeout").value),
  };
  try {
    const apiKey = $("ai-connection-api-key").value;
    const created = await api(state.editingAiConnectionId ? `/api/ai/model-connections/${encodeURIComponent(state.editingAiConnectionId)}` : "/api/ai/model-connections", {
      method: state.editingAiConnectionId ? "PUT" : "POST",
      body: JSON.stringify({
        ...payload,
        ...(state.editingAiConnectionId ? {} : { api_key: apiKey }),
      }),
    });
    if (state.editingAiConnectionId && apiKey) await api(`/api/ai/model-connections/${encodeURIComponent(state.editingAiConnectionId)}/rotate-key`, { method: "POST", body: JSON.stringify({ api_key: apiKey }) });
    state.aiModelConnections = state.editingAiConnectionId
      ? state.aiModelConnections.map((item) => item.id === created.id ? created : item)
      : [...state.aiModelConnections, created];
    renderRuntimeSettings();
    $("ai-connection-dialog")?.close();
    showOperationFeedback("AI 模型连接已保存", "success");
    return created;
  } catch (error) {
    result.textContent = `保存失败：${error.message || "请检查连接配置后重试"}`;
    return null;
  } finally {
    saveButton.disabled = false;
  }
}

function renderMabangAccounts() {
  const list = $("settings-mabang-account-list");
  if (!list) return;
  const accounts = state.mabangAccounts || [];
  if (!accounts.length) {
    list.innerHTML = '<p class="muted">暂无马帮账号。当前仍按上面的「自动登录账号」单账号运行；点「新增账号」把其余账号建起来后，店铺即可按账号归属。</p>';
    return;
  }
  list.innerHTML = accounts.map((item) => {
    const isDefault = Boolean(item.id) && item.id === state.mabangAccountsDefaultId;
    // 状态只保留一条，按「能不能用」排优先级：停用 > 缺密码（登录不了）> 默认 / 可用。
    // 注意：这里不能命名成 state —— 会遮蔽模块级的 state，而上面刚读过它，直接 TDZ 报错。
    const statusPill = item.enabled === false
      ? { label: "已停用", tone: "" }
      : item.has_password
        ? { label: isDefault ? "默认账号" : "可用", tone: "ready" }
        : { label: "未配置密码", tone: "waiting_manual" };
    const port = item.cdp_port ? `<span class="mono">${escapeHtml(String(item.cdp_port))}</span>` : "";
    return `
    <div class="settings-connection-row">
      <div>
        <strong>${escapeHtml(item.name || item.login_account || "未命名账号")}</strong>
        <span>登录账号 ${escapeHtml(item.login_account || "—")}${port ? ` · 端口 ${port}` : ""}</span>
      </div>
      <div class="settings-connection-actions"><span class="status ${statusPill.tone}">${statusPill.label}</span><button type="button" data-edit-mabang-account="${escapeHtml(item.id || "")}">编辑</button><button type="button" data-delete-mabang-account="${escapeHtml(item.id || "")}">删除</button></div>
    </div>`;
  }).join("");
}

async function loadMabangAccounts() {
  const payload = await api("/api/mabang/accounts");
  state.mabangAccounts = payload.items || [];
  state.mabangAccountsDefaultId = payload.default_account_id || "";
  renderMabangAccounts();
}

function openMabangAccountDialog() {
  state.editingMabangAccountId = "";
  $("mabang-account-name").value = "";
  $("mabang-account-login").value = "";
  $("mabang-account-password").value = "";
  $("mabang-account-password").placeholder = "留空则不修改";
  $("mabang-account-enabled").checked = true;
  $("mabang-account-result").textContent = "";
  $("mabang-account-dialog")?.showModal();
}

function openMabangAccountEditor(accountId) {
  const account = (state.mabangAccounts || []).find((item) => item.id === accountId);
  if (!account) return;
  state.editingMabangAccountId = accountId;
  $("mabang-account-name").value = account.name || "";
  $("mabang-account-login").value = account.login_account || "";
  $("mabang-account-password").value = "";
  $("mabang-account-password").placeholder = "留空则不修改";
  $("mabang-account-enabled").checked = account.enabled !== false;
  $("mabang-account-result").textContent = "";
  $("mabang-account-dialog")?.showModal();
}

async function saveMabangAccount() {
  const result = $("mabang-account-result");
  const saveButton = $("save-mabang-account-btn");
  if (saveButton) saveButton.disabled = true;
  if (result) result.textContent = "正在保存...";
  const payload = {
    name: $("mabang-account-name").value.trim(),
    login_account: $("mabang-account-login").value.trim(),
    login_password: $("mabang-account-password").value || "",
    // cdp_port / profile_dir 不再由界面提供：系统按账号自动分配并隔离。
    enabled: $("mabang-account-enabled").checked,
  };
  try {
    await api(state.editingMabangAccountId ? `/api/mabang/accounts/${encodeURIComponent(state.editingMabangAccountId)}` : "/api/mabang/accounts", {
      method: state.editingMabangAccountId ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    await loadMabangAccounts();
    $("mabang-account-dialog")?.close();
    showOperationFeedback("马帮账号已保存", "success");
  } catch (error) {
    if (result) result.textContent = `保存失败：${error.message || "请检查账号与端口"}`;
  } finally {
    if (saveButton) saveButton.disabled = false;
  }
}

function mabangBrowserPort(cdpUrl) {
  try {
    const port = new URL(String(cdpUrl || "")).port;
    return port || "9223";
  } catch (_error) {
    return "9223";
  }
}

function mabangBrowserCdpUrl() {
  // 端口不再由界面配置：每个账号由系统分配自己的端口。
  // 这里只保留「没有账号实体时的回退地址」，保存设置时不要把它清空或改坏。
  const configured = String(state.runtimeConfiguration?.worker?.cdp_url || "").trim();
  if (configured) return configured;
  const accountPort = (state.mabangAccounts || []).find((item) => item.cdp_port)?.cdp_port;
  return `http://127.0.0.1:${accountPort || mabangBrowserPort("")}`;
}

async function updateAiConnection(action, connectionId) {
  if (action === "default") {
    await api(`/api/ai/model-connections/${encodeURIComponent(connectionId)}/set-default`, { method: "POST", body: "{}" });
  } else {
    await api(`/api/ai/model-connections/${encodeURIComponent(connectionId)}`, { method: "DELETE" });
  }
  await loadRuntimeSettings();
}

const SECTIONED_TEMPLATE_PRESET = [
  { id: "shipping", type: "fixed", emoji: "🚀", title: "SHIPPING NOTICE", body: "🚚 100% Local Stock: Ready to ship straight to your doorstep!\n⚡ Fast Dispatch: Ships from the Philippines within 24 hours for quick delivery." },
  { id: "welcome", type: "ai", slot: "store_intro", emoji: "🌿", title: "WELCOME", hint: "One warm sentence inviting shoppers to the store. No shipping/returns." },
  { id: "product_info", type: "fields", emoji: "👕", title: "PRODUCT INFORMATION", rows: [
    { emoji: "🏷️", label: "Product Name", source: "ai", slot: "product_name", hint: "A catchy English product name for this item." },
    { emoji: "📁", label: "Category", source: "field", field: "category" },
    { emoji: "🎬", label: "Character/Theme", source: "ai", slot: "theme", hint: "The print/design theme in a few words." },
    { emoji: "👥", label: "Target Users", source: "fixed", value: "Women / Kids (Middle Age / Youth Fashion)" },
    { emoji: "🧵", label: "Material", source: "fixed", value: "100% High-Quality Cotton (Knit Fabric)" },
    // 必加行：这几行必须出现在每条描述里，和后端默认模板保持一致。
    { emoji: "📐", label: "Size Guide", source: "fixed", value: "Please refer to the size chart in the product images." },
    { emoji: "📏", label: "Size Tolerance", source: "fixed", value: "1-3 cm difference due to manual measurement." },
    { emoji: "🧒", label: "Age Range", source: "ai", slot: "age_range", hint: "Who this item fits, taken from the product facts (e.g. Kids 2-8 years / Teens / Adults). One short line." },
    { emoji: "📏", label: "Size", source: "field", field: "size" },
    { emoji: "📦", label: "Stock Availability", source: "fixed", value: "Ready stock." },
    { emoji: "🎨", label: "Color", source: "field", field: "color" },
  ] },
  { id: "highlights", type: "ai", slot: "highlights", emoji: "✨", title: "HIGHLIGHTS", hint: "3-4 short lines on comfort, print quality, styling. One per line." },
  { id: "package", type: "fixed", emoji: "📦", title: "PACKAGE CONTENTS", body: "1 x Item as shown in the listing." },
  { id: "after_sales", type: "fixed", emoji: "💌", title: "AFTER-SALES", body: "Any issue after your order? Message us first and we'll gladly help you sort it out." },
  { id: "reminder", type: "fixed", emoji: "📌", title: "FRIENDLY REMINDER", body: "Manual measurement — a 1-3cm difference is normal.\nColors may vary slightly due to different monitors.\nPlease check the size chart before ordering." },
];

const PROFILE_SECTION_TYPE_LABELS = { fixed: "固定", ai: "AI", fields: "字段" };

// Same value space as description_generation._sectioned_description rendering
// so the in-dialog preview matches what the backend produces.
const PROFILE_FIELD_OPTIONS = [
  { value: "category", label: "商品类目（Category）" },
  { value: "color", label: "商品颜色（Color）" },
  { value: "size", label: "商品尺码（Size）" },
  { value: "product_name", label: "商品品名（Product Name）" },
];

// 平台类目基础属性：基础商品 AI 识别表里最常见的键（Shopee 平台属性），
const PROFILE_PREVIEW_SAMPLE = [
  { label: "商品品名（Product Name）", value: "Classic Anime Graphic Tee" },
  { label: "商品类目（Category）", value: "Men's T-Shirts" },
  { label: "商品颜色（Color）", value: "Black" },
  { label: "商品尺码（Size）", value: "M" },
  { label: "Material", value: "Zinc Alloy" },
  { label: "Gender", value: "Women" },
  { label: "Applicable Season", value: "Four Seasons" },
];

// 基础商品详情（示例商品 = 发饰 BCDCA41）的整组 AI 识别键值，用于预览直出。
const SAMPLE_BASE_DETAIL_LIST = [
  "Item Type : Hair Ornament",
  "Material : Zinc Alloy",
  "Style : Anime",
  "Theme : Anime Character",
  "Pattern Type : Flower",
  "Applicable Scene : Cosplay",
  "Applicable Season : Four Seasons",
  "Gender : Women",
];

function sectionTypeBadge(type) {
  const typeLabel = PROFILE_SECTION_TYPE_LABELS[type] || "字段";
  const className = type === "fixed" ? "profile-type-badge profile-type-badge--fixed" : type === "ai" ? "profile-type-badge profile-type-badge--ai" : "profile-type-badge profile-type-badge--fields";
  return `<span class="${className}">${typeLabel}</span>`;
}

function renderProfileSectionsEditor(sections) {
  const root = $("description-profile-sections");
  if (!root) return;
  if (!Array.isArray(sections)) {
    const existing = collectProfileSectionsFromEditor();
    sections = existing || SECTIONED_TEMPLATE_PRESET;
  }
  state.editingProfileSections = sections;
  const previousRoot = $("description-profile-sections");
  const openIndexes = new Set(
    previousRoot
      ? Array.from(previousRoot.querySelectorAll("details.profile-section-item[data-section-index]"))
          .filter((item) => item.open)
          .map((item) => Number(item.dataset.sectionIndex))
      : []
  );
  const activeTypes = new Set(sections.map((section) => String(section.type || "fixed")));
  const availableTypes = ["fixed", "fields", "ai"].filter((type) => !activeTypes.has(type));
  root.innerHTML = sections.map((section, index) => {
    const type = String(section.type || "fixed");
    const bodyField = type === "fixed"
      ? `<textarea data-section-body="${index}" rows="3" placeholder="固定文案，所有商品相同">${escapeHtml(section.body || "")}</textarea>`
      : type === "ai"
        ? `<input data-section-hint="${index}" value="${escapeHtml(section.hint || "")}" placeholder="给 AI 的提示（英文更好），例如 One warm sentence inviting shoppers" />`
        : renderFieldRowsEditor(section.rows, index);
    return `
    <details class="profile-section-item" data-section-index="${index}"${openIndexes.has(index) ? " open" : ""}>
      <summary>
        <span class="profile-section-summary-label"><span class="profile-section-emoji">${escapeHtml(section.emoji || "")}</span><span class="profile-section-title-text">${escapeHtml(section.title || "(未命名)")}</span>${sectionTypeBadge(type)}</span>
        <span class="profile-section-moves">
          <button type="button" class="secondary mini" data-section-move-up="${index}" title="上移" aria-label="上移">△</button>
          <button type="button" class="secondary mini" data-section-move-down="${index}" title="下移" aria-label="下移">▽</button>
        </span>
        <select data-section-type="${index}">
          <option value="fixed" ${type === "fixed" ? "selected" : ""}>固定</option>
          <option value="fields" ${type === "fields" ? "selected" : ""}>字段</option>
          <option value="ai" ${type === "ai" ? "selected" : ""}>AI</option>
        </select>
        <button type="button" class="secondary mini" data-section-remove="${index}">删除</button>
      </summary>
      <div class="profile-section-body">
        <label>emoji<input data-section-emoji="${index}" value="${escapeHtml(section.emoji || "")}" maxlength="4" placeholder="🚀" /></label>
        <label>标题<input data-section-title="${index}" value="${escapeHtml(section.title || "")}" placeholder="SHIPPING NOTICE" /></label>
        ${bodyField}
      </div>
    </details>`;
  }).join("") || '<p class="muted">暂无分区，点下面「＋ 追加分区」开始搭建。</p>';
  if (availableTypes.length) {
    root.insertAdjacentHTML("beforeend", `<div class="profile-add-section-row">
      <select data-add-section-type><option value="">＋ 追加分区...</option>${availableTypes.map((type) => `<option value="${type}">${PROFILE_SECTION_TYPE_LABELS[type]}</option>`).join("")}</select>
      <button type="button" class="secondary mini" data-add-section="${availableTypes[0]}" ${availableTypes.length === 1 ? "" : "hidden"}>＋ 追加分区</button>
    </div>`);
  } else {
    root.insertAdjacentHTML("beforeend", `<p class="profile-add-section-row muted small">三种分区（固定 / 字段 / AI）都已有了，删掉一个后可再加。</p>`);
  }
}

function renderFieldRowsEditor(rows, sectionIndex) {
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) {
    return `<div class="profile-rows-empty">字段行还没有内容</div>`;
  }
  const heading = `<div class="profile-field-row profile-field-row--head"><span>数据来源</span><span>来源内容</span><span>行内容</span><span></span></div>`;
  const items = list.map((row, rowIndex) => {
    const source = String(row.source || "fixed");
    const emojiInput = `<input data-row-emoji="${sectionIndex}_${rowIndex}" maxlength="4" value="${escapeHtml(row.emoji || "")}" placeholder="🏷️" title="行首 emoji（可留空）" />`;
    const labelInput = `<input data-row-label="${sectionIndex}_${rowIndex}" value="${escapeHtml(row.label || "")}" placeholder="例如 Color" />`;
    const leftCell = `<label class="profile-row-source">来源
      <select data-row-source="${sectionIndex}_${rowIndex}">
        <option value="fixed" ${source === "fixed" ? "selected" : ""}>固定文字</option>
        <option value="field" ${source === "field" ? "selected" : ""}>商品数据</option>
        <option value="base_detail" ${source === "base_detail" ? "selected" : ""}>基础商品详情</option>
        <option value="ai" ${source === "ai" ? "selected" : ""}>AI 生成</option>
      </select></label>`;
    const centerCell = source === "fixed"
      ? `<input data-row-value="${sectionIndex}_${rowIndex}" value="${escapeHtml(row.value || "")}" placeholder="固定文案，所有商品相同" />`
      : source === "base_detail"
        ? `<div class="profile-base-detail-preview">
            <span class="profile-base-detail-hint">整组键值直接输出（不经 AI）：</span>
            <div class="profile-base-detail-sample">Item Type : Hair Ornament<br/>Material : Zinc Alloy<br/>Style : Anime<br/>…取该商品 AI 识别的全部属性</div>
          </div>`
        : source === "field"
          ? `<select data-row-field="${sectionIndex}_${rowIndex}">
              <option value="">— 选择商品字段 —</option>
              ${PROFILE_FIELD_OPTIONS.map((option) => `<option value="${option.value}" ${String(row.field || "") === option.value ? "selected" : ""}>${option.label}</option>`).join("")}
            </select>`
          : `<input data-row-hint="${sectionIndex}_${rowIndex}" value="${escapeHtml(row.hint || "")}" placeholder="给 AI 的提示，例如 A catchy English product name" />`;
    const aiName = (row.slot || "").trim();
    const rightCell = source === "ai"
      ? `<input data-row-slot="${sectionIndex}_${rowIndex}" value="${escapeHtml(aiName)}" placeholder="AI 槽位名（自动生成，可修改）" />`
      : `<span class="profile-row-placeholder"></span>`;
    const rowTools = `<span class="profile-row-tools"><button type="button" class="secondary mini" data-row-remove="${sectionIndex}_${rowIndex}" title="删除行" aria-label="删除行">×</button></span>`;
    return `<div class="profile-field-row" data-row-index="${rowIndex}">
      <span class="profile-row-emoji-cell">${emojiInput}${labelInput}</span>
      ${leftCell}
      ${centerCell}
      <span class="profile-row-slot-cell">${rightCell}</span>
      ${rowTools}
    </div>`;
  }).join("");
  return `<div class="profile-rows-editor"><div class="profile-rows-head">
    <span>行（从左到右：emoji + 行名）</span><span>数据来源</span><span>来源内容</span><span>AI 槽位名（自动）</span><span></span>
  </div>${items}<button type="button" class="secondary mini" data-row-add="${sectionIndex}">＋ 添加行</button></div>`;
}

function addFieldRow(sectionIndex) {
  const sections = collectProfileSectionsFromEditor() || [...(state.editingProfileSections || [])];
  const section = sections[sectionIndex];
  if (!section) return;
  section.rows = section.rows || [];
  section.rows.push({ emoji: "🏷️", label: "", source: "field", field: "color" });
  renderProfileSectionsEditor(sections);
  // Keep the edited section open after the re-render.
  const details = document.querySelector(`[data-section-index="${sectionIndex}"]`);
  if (details) details.open = true;
}

function removeFieldRow(sectionIndex, rowIndex) {
  const sections = collectProfileSectionsFromEditor() || [...(state.editingProfileSections || [])];
  const section = sections[sectionIndex];
  if (!section || !Array.isArray(section.rows)) return;
  section.rows.splice(rowIndex, 1);
  renderProfileSectionsEditor(sections);
  const details = document.querySelector(`[data-section-index="${sectionIndex}"]`);
  if (details) details.open = true;
}

function moveSection(sectionIndex, delta) {
  const sections = collectProfileSectionsFromEditor() || [...(state.editingProfileSections || [])];
  const targetIndex = sectionIndex + delta;
  if (!Array.isArray(sections) || targetIndex < 0 || targetIndex >= sections.length) return;
  const [section] = sections.splice(sectionIndex, 1);
  sections.splice(targetIndex, 0, section);
  renderProfileSectionsEditor(sections);
  const details = document.querySelector(`[data-section-index="${targetIndex}"]`);
  if (details) details.open = true;
}

function addProfileSection(type) {
  const sections = collectProfileSectionsFromEditor() || [...(state.editingProfileSections || [])];
  const existingTypes = new Set(sections.map((section) => String(section.type || "fixed")));
  const available = ["fixed", "fields", "ai"].filter((item) => !existingTypes.has(item));
  const nextType = available.includes(type) ? type : (available[0] || "fixed");
  const counts = sections.reduce((acc, section) => { acc[String(section.type || "fixed")] += 1; return acc; }, { fixed: 0, fields: 0, ai: 0 });
  const byType = { fixed: "固定", fields: "字段", ai: "AI" };
  let title = `${byType[nextType] || "分区"}区块`;
  let slotName = "";
  if (nextType === "fields") {
    slotName = `text_row_${counts.fields + 1}`;
    title = "TEXT INFO";
  } else if (nextType === "ai") {
    slotName = `section_${counts.ai + 1}`;
  }
  const section = nextType === "fixed"
    ? { id: `fixed_${Date.now()}`, type: "fixed", emoji: "💬", title: "", body: "" }
    : nextType === "fields"
      ? { id: `fields_${Date.now()}`, type: "fields", emoji: "📋", title, rows: [{ emoji: "🏷️", label: "", source: "fixed", value: "" }] }
      : { id: `ai_${Date.now()}`, type: "ai", slot: slotName, emoji: "✨", title, hint: "" };
  sections.push(section);
  renderProfileSectionsEditor(sections);
  const details = document.querySelector(`[data-section-index="${sections.length - 1}"]`);
  if (details) details.open = true;
}

function collectProfileSectionsFromEditor() {
  const root = $("description-profile-sections");
  if (!root || !root.children.length) return null;
  const sections = state.editingProfileSections || [];
  const result = [];
  for (let index = 0; index < sections.length; index += 1) {
    const typeEl = root.querySelector(`[data-section-type="${index}"]`);
    if (!typeEl) continue;
    const type = typeEl.value;
    const section = { ...sections[index], type };
    const emoji = root.querySelector(`[data-section-emoji="${index}"]`)?.value;
    const title = root.querySelector(`[data-section-title="${index}"]`)?.value;
    if (emoji !== undefined) section.emoji = emoji.trim();
    if (title !== undefined) section.title = title.trim();
    if (type === "fixed") {
      section.body = root.querySelector(`[data-section-body="${index}"]`)?.value ?? section.body ?? "";
      section.body = section.body.trim();
    } else if (type === "ai") {
      section.hint = (root.querySelector(`[data-section-hint="${index}"]`)?.value ?? section.hint ?? "").trim();
      section.slot = section.slot || section.id || autoAiSlotName(index, "section");
    } else {
      // Fields rows are edited through the row table — rebuild rows from it.
      const rows = [];
      const rowEls = Array.from(root.querySelectorAll(`[data-row-emoji="${index}_"], [data-row-source^="${index}_"]`).length
        ? root.querySelectorAll(`.profile-field-row[data-row-index]`) : []);
      const uniqueRowEls = rowEls.filter((el) => {
        const rowIndex = el.dataset.rowIndex;
        return !rowEls.some((other) => other !== el && other.dataset.rowIndex === rowIndex && other.parentElement === el.parentElement);
      });
      for (const rowEl of Array.isArray(uniqueRowEls) ? uniqueRowEls : []) {
        const rowIndex = Number(rowEl.dataset.rowIndex);
        if (!Number.isInteger(rowIndex) || rowIndex < 0) continue;
        const emoji = rowEl.querySelector(`[data-row-emoji]`)?.value || "";
        const label = rowEl.querySelector(`[data-row-label]`)?.value || "";
        const source = rowEl.querySelector(`[data-row-source]`)?.value || "fixed";
        const row = { emoji: emoji.trim(), label: label.trim(), source };
        if (source === "fixed") {
          row.value = (rowEl.querySelector(`[data-row-value]`)?.value ?? "").trim();
        } else if (source === "base_detail") {
          // 基础商品详情：无需额外配置，生成时整组键值直出（由后端取）。
          delete row.field;
        } else if (source === "field") {
          row.field = rowEl.querySelector(`[data-row-field]`)?.value || "";
        } else {
          row.slot = (rowEl.querySelector(`[data-row-slot]`)?.value ?? "").trim() || autoAiSlotName(index, "row");
          row.hint = (rowEl.querySelector(`[data-row-hint]`)?.value ?? "").trim();
        }
        rows.push(row);
      }
      section.rows = rows;
    }
    result.push(section);
  }
  return result;
}

function autoAiSlotName(sectionIndex, kind) {
  const titleEl = document.querySelector(`[data-section-title="${sectionIndex}"]`);
  const titleText = titleEl?.value?.trim() || "";
  const base = kind === "row" ? "row" : "section";
  if (titleText) {
    const ascii = titleText.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toLowerCase();
    if (ascii) return `${base}_${ascii}`;
  }
  return `${base}_${sectionIndex + 1}`;
}

function renderDescriptionProfileStoreOptions(selectedStoreId = "") {
  const select = $("description-profile-store");
  if (!select) return;
  select.innerHTML = `<option value="">全店通用（默认候选给所有店铺）</option>`;
  for (const option of state.mabangStoreOptions || []) {
    const item = document.createElement("option");
    item.value = option.mabang_store_id || "";
    item.dataset.storeName = option.store_name || "";
    item.textContent = option.store_name ? `${option.store_name} (${option.mabang_store_id || ""})` : (option.mabang_store_id || "");
    select.appendChild(item);
  }
  select.value = selectedStoreId || "";
}

function openDescriptionProfileDialog() {
  state.editingDescriptionProfileId = "";
  state.editingProfileLegacyNotice = "";
  applyDescriptionProfileLegacyNotice("");
  state.editingProfileSections = SECTIONED_TEMPLATE_PRESET.map((section) => ({ ...section, rows: section.rows ? section.rows.map((row) => ({ ...row })) : undefined }));
  renderDescriptionProfileStoreOptions("");
  $("description-profile-name").value = "";
  $("description-profile-instructions").value = "";
  $("description-profile-result").textContent = "";
  renderProfileSectionsEditor(state.editingProfileSections);
  renderProfilePreview();
  $("description-profile-dialog")?.showModal();
}

function applyDescriptionProfileLegacyNotice(text) {
  const notice = $("description-profile-legacy-notice");
  if (!notice) return;
  notice.textContent = text || "";
  notice.hidden = !text;
}

function openDescriptionProfileEditor(profileId) {
  const profile = state.descriptionProfiles.find((item) => item.id === profileId);
  if (!profile) return;
  state.editingDescriptionProfileId = profileId;
  const isLegacy = (profile.mode || "combined") !== "sectioned";
  const hasSections = Array.isArray(profile.sections) && profile.sections.length > 0;
  const notice = isLegacy ? "这个方案是旧格式（拼接/AI全文）。打开后自动转为分区模板，保存后原格式将不再保留。"
    : !hasSections ? "这个方案里还没有分区，下面已填入默认示例。保存后即为正式内容。" : "";
  state.editingProfileLegacyNotice = notice;
  applyDescriptionProfileLegacyNotice(notice);
  state.editingProfileSections = hasSections ? profile.sections : SECTIONED_TEMPLATE_PRESET.map((section) => ({ ...section, rows: section.rows ? section.rows.map((row) => ({ ...row })) : undefined }));
  renderDescriptionProfileStoreOptions(profile.mabang_store_id || "");
  $("description-profile-name").value = profile.name || "";
  $("description-profile-instructions").value = profile.instructions || "";
  $("description-profile-result").textContent = "";
  renderProfileSectionsEditor(state.editingProfileSections);
  renderProfilePreview();
  $("description-profile-dialog")?.showModal();
}

async function saveDescriptionProfile() {
  const result = $("description-profile-result");
  result.textContent = "正在保存...";
  const storeSelect = $("description-profile-store");
  const selectedStoreOption = storeSelect?.selectedOptions?.[0];
  const sections = collectProfileSectionsFromEditor() || state.editingProfileSections || SECTIONED_TEMPLATE_PRESET;
  if (sections.some((section) => section.type === "fields" && !(Array.isArray(section.rows) && section.rows.length))) {
    result.textContent = "存在空的「字段」分区：请删掉它或至少加一行。";
    return;
  }
  const payload = {
    name: $("description-profile-name").value.trim(),
    mode: "sectioned",
    // 长度/Token 上限不再暴露给操作者：沿用服务端默认（4096 Token 对推理模型安全）。
    max_length: 6000,
    max_tokens: 4096,
    sections,
    instructions: $("description-profile-instructions").value.trim(),
    mabang_store_id: storeSelect?.value || "",
    store_name: selectedStoreOption?.dataset.storeName || "",
    enabled: true,
  };
  const profile = await api(state.editingDescriptionProfileId ? `/api/description-profiles/${encodeURIComponent(state.editingDescriptionProfileId)}` : "/api/description-profiles", {
    method: state.editingDescriptionProfileId ? "PUT" : "POST",
    body: JSON.stringify(payload),
  });
  state.descriptionProfiles = state.editingDescriptionProfileId
    ? state.descriptionProfiles.map((item) => item.id === profile.id ? profile : item)
    : [...state.descriptionProfiles, profile];
  renderRuntimeSettings();
  $("description-profile-dialog")?.close();
  showOperationFeedback("描述生成方案已保存", "success");
}

function renderProfilePreview() {
  const target = $("description-profile-preview");
  if (!target) return;
  const sections = collectProfileSectionsFromEditor() || state.editingProfileSections || [];
  const nameEl = $("description-profile-name");
  const nameText = nameEl?.value?.trim() || "（未命名方案）";
  const sampleMap = new Map(PROFILE_PREVIEW_SAMPLE.map((row) => [row.label, row.value]));
  // 示例商品自带的属性值（供预览直接展示识别值，实际以商品数据为准）
  const sampleAttributes = new Map(
    PROFILE_PREVIEW_SAMPLE
      .filter((row) => !/^商品/.test(row.label))
      .map((row) => [row.label.toLowerCase(), row.value])
  );
  const resolveField = (fieldName, fallback) => {
    const direct = sampleFields.get(fieldName || "") ?? sampleFields.get(String(fallback || "").toLowerCase());
    if (direct !== undefined && direct !== "") return direct;
    const attr = sampleAttributes.get(String(fieldName || fallback || "").trim().toLowerCase());
    return attr ?? "";
  };
  const sampleFields = new Map([
    ["category", sampleMap.get("商品类目（Category）") || ""],
    ["color", sampleMap.get("商品颜色（Color）") || ""],
    ["size", sampleMap.get("商品尺码（Size）") || ""],
    ["product_name", sampleMap.get("商品品名（Product Name）") || ""],
    ["name", sampleMap.get("商品品名（Product Name）") || ""],
  ]);
  const blocks = [];
  for (const section of sections || []) {
    if (!section || typeof section !== "object") continue;
    const type = String(section.type || "fixed");
    const header = [section.emoji || "", section.title || ""].filter(Boolean).join(" ").trim();
    const block = [header];
    if (type === "fixed") {
      const body = String(section.body || "").trim();
      if (!body) continue;
      block.push(body);
    } else if (type === "ai") {
      block.push("(AI 自动生成)");
    } else {
      const lines = [];
      const detailLines = [];
      for (const row of section.rows || []) {
        const source = String(row.source || "fixed");
        if (source === "base_detail") {
          // 基础商品详情：整组键值直出（预览用示例商品属性表，和真实取数一致）
          const sampleDetail = SAMPLE_BASE_DETAIL_LIST.join("\n");
          if (sampleDetail) detailLines.push(sampleDetail);
          continue;
        }
        let value = "";
        if (source === "fixed") value = String(row.value || "").trim();
        else if (source === "field") value = resolveField(row.field || "", row.label || "");
        else value = "(AI 自动生成)";
        if (!value) continue;
        const label = String(row.label || "").trim();
        lines.push([row.emoji || "", label ? `${label}:` : "", value].filter(Boolean).join(" ").trim());
      }
      if (lines.length) block.push(lines.join("\n"));
      if (detailLines.length) block.push(detailLines.join("\n\n"));
      if (!lines.length && !detailLines.length) continue;
    }
    blocks.push(block.filter(Boolean).join("\n"));
  }
  const html = blocks.length
    ? blocks.map((block) => `<div class="profile-preview-block">${escapeHtml(block)}</div>`).join("")
    : '<div class="profile-preview-empty">左侧还没有内容，保存前的预览会显示在这里。</div>';
  target.innerHTML = `<div class="profile-preview-head">完成预览<span id="description-profile-preview-head-name">${escapeHtml(nameText)}</span>（示例商品）</div>${html}`;
}

async function setDefaultDescriptionProfile(profileId) {
  await api(`/api/description-profiles/${encodeURIComponent(profileId)}/set-default`, { method: "POST", body: "{}" });
  await loadRuntimeSettings();
}

function aiConnectionSupportsBalance(connection) {
  const url = String(connection?.base_url || "").toLowerCase();
  return url.includes("api.deepseek.com") || url.includes("api.deepseek.ai");
}

function renderAiConnectionBalanceLine(connection) {
  if (!aiConnectionSupportsBalance(connection)) return "";
  const cached = connection?.last_balance || null;
  const fetchedAt = connection?.last_balance_fetched_at || "";
  if (!cached) {
    return `<span class="ai-balance-line muted">余额未查询 · 点右侧「查余额」拉取</span>`;
  }
  if (cached.error) {
    return `<span class="ai-balance-line ai-balance-line--error">余额获取失败: ${escapeHtml(cached.error)}</span>`;
  }
  const balances = Array.isArray(cached.balances) ? cached.balances : [];
  if (!balances.length) {
    return `<span class="ai-balance-line muted">余额返回为空</span>`;
  }
  const primary = balances.find((b) => (b.currency || "").toUpperCase() === "USD") || balances[0];
  const total = primary.total_balance || "0";
  const currency = primary.currency || "";
  const topped = primary.topped_up_balance || "0";
  const granted = primary.granted_balance || "0";
  const at = fetchedAt ? new Date(fetchedAt).toLocaleString() : "";
  return `<span class="ai-balance-line">
    余额 <code>${escapeHtml(total)}</code> ${escapeHtml(currency)}
    <span class="muted">· 充值 ${escapeHtml(topped)} · 赠送 ${escapeHtml(granted)}${at ? ` · 更新 ${escapeHtml(at)}` : ""}</span>
  </span>`;
}

async function refreshAiConnectionBalance(connectionId) {
  if (!connectionId) return;
  const button = document.querySelector(`[data-refresh-ai-balance="${CSS.escape(connectionId)}"]`);
  if (button instanceof HTMLButtonElement) button.disabled = true;
  try {
    const updated = await api(`/api/ai/model-connections/${encodeURIComponent(connectionId)}/refresh-balance`, {
      method: "POST",
      body: "{}",
    });
    const index = state.aiModelConnections.findIndex((item) => item.id === connectionId);
    if (index >= 0) state.aiModelConnections[index] = updated;
    renderRuntimeSettings();
    if (updated.last_balance?.error) {
      showOperationFeedback(`余额获取失败：${updated.last_balance.error}`, "error");
    } else {
      showOperationFeedback("余额已更新", "success");
    }
  } finally {
    if (button instanceof HTMLButtonElement) button.disabled = false;
  }
}

async function saveRuntimeSettings() {
  const resultElement = $("settings-result");
  resultElement.textContent = "正在保存...";
  const config = await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({
      mabang: {
        workspace_url: $("settings-mabang-url").value.trim(),
        login_account: $("settings-mabang-account")?.value.trim() || "",
        login_password: $("settings-mabang-password")?.value || "",
        headless: $("settings-mabang-headless")?.checked === true,
      },
      worker: {
        cdp_url: mabangBrowserCdpUrl(),
        poll_seconds: Number($("settings-worker-poll-seconds").value),
        lease_seconds: Number($("settings-worker-lease-seconds").value),
        max_attempts: Number($("settings-worker-max-attempts").value),
        auto_restart: $("settings-worker-auto-restart").checked,
        // 按需启动：平时不跑 Worker，有任务才唤醒；空闲满 N 分钟自动关闭（0 = 不关）。
        start_on_demand: $("settings-worker-on-demand")?.checked !== false,
        idle_shutdown_minutes: Math.max(0, Number($("settings-worker-idle-minutes")?.value ?? 10) || 0),
        // 同账号并行槽位：1 = 一个账号一次一单；2 起为同一账号再开一只浏览器（独立端口/登录）。
        slots_per_account: Math.min(4, Math.max(1, Number($("settings-worker-slots")?.value || 1) || 1)),
      },
      nas: {
        asset_root: $("settings-nas-root").value.trim(),
        scan_mode: $("settings-nas-scan-mode").value,
        max_files: Number($("settings-nas-max-files").value),
      },
      store_collection: {
        auto_focus_after_import: $("settings-auto-focus-import").checked,
        enabled: $("settings-collection-enabled").checked,
        interval_minutes: Number($("settings-collection-interval").value),
        max_attempts: Number($("settings-collection-max-attempts").value),
      },
      display: {
        cross_border_enabled: $("settings-cross-border-mode")?.checked !== false,
      },
      ai: {
        color_connection_id: $("settings-ai-color-connection")?.value || "",
      },
      ai_listing_generation: {
        generate_title_on_import: $("settings-generate-title-on-import")?.checked === true,
        generate_description_on_import: $("settings-generate-description-on-import")?.checked === true,
        fill_attributes_into_description: $("settings-fill-attributes-into-description")?.checked === true,
      },
      product_sku: {
        auto_assign_on_import: $("settings-sku-auto-assign")?.checked === true,
        prefix: ($("settings-sku-prefix")?.value || "").trim(),
        number_width: Number($("settings-sku-number-width")?.value || 4),
        next_number: Number($("settings-sku-next-number")?.value || 1),
      },
      storage_cleanup: {
        enabled: $("settings-storage-auto-enabled").checked,
        temp_retention_days: Number($("settings-storage-temp-retention").value),
        screenshot_retention_days: Number($("settings-storage-screenshot-retention").value),
        export_retention_days: Number($("settings-storage-export-retention").value),
        log_retention_days: Number($("settings-storage-log-retention").value),
        assets_retention_days: Number($("settings-storage-assets-retention").value),
        compact_enabled: $("settings-storage-compact-enabled").checked,
        compact_delay_days: Number($("settings-storage-compact-delay").value),
        compact_max_edge: Number($("settings-storage-compact-edge").value),
        compact_jpeg_quality: Number($("settings-storage-compact-quality").value),
        assets_cleanup_enabled: $("settings-storage-assets-cleanup-enabled").checked,
      },
    }),
  });
  state.runtimeConfiguration = config;
  renderRuntimeSettings();
  resultElement.textContent = config.worker?.restart_required ? "设置已保存；Worker 配置将在重启后生效。" : "设置已保存。";
  // 槽位数改动是立即生效的：新槽位要开一只新浏览器并自动登录，这里如实回报结果。
  const slotChange = config.worker_slots || {};
  if (Number(slotChange.slots || 0) !== Number(slotChange.previous_slots ?? slotChange.slots ?? 0)) {
    const failures = (slotChange.started && slotChange.started.failures) || [];
    const started = (slotChange.started && slotChange.started.workers) || [];
    if (failures.length) {
      const detail = failures.map((row) => `槽位 ${Number(row.slot || 0) + 1}：${row.error || "未知原因"}`).join("；");
      resultElement.textContent = `设置已保存；新槽位启动失败（${detail}）。可在上方列表点「日志」排查，或检查该账号的马帮登录。`;
    } else if (started.length) {
      resultElement.textContent = `设置已保存；已按新的并行槽位启动 ${started.length} 个 Worker。`;
    }
  }
  const mabangPassword = $("settings-mabang-password");
  if (mabangPassword) mabangPassword.value = "";
}

async function controlWorker(action) {
  const loginGated = ["start", "resume", "restart"].includes(action);
  if (loginGated) showOperationFeedback("正在准备自动化浏览器并检查马帮登录…", "info");
  const result = await api(`/api/worker/${action}`, { method: "POST", body: "{}" });
  state.workerStatus = result;
  renderRuntimeSettings();
  const loginStatus = result.auto_login?.status || "";
  if (loginGated && ["logged_in", "already_logged_in"].includes(loginStatus)) {
    const mode = result.browser_mode === "headless" ? "（无头模式）" : "";
    showOperationFeedback(`马帮已登录，Worker 已${action === "restart" ? "重启" : action === "resume" ? "继续" : "启动"}${mode}`, "success");
    return;
  }
  showOperationFeedback(`Worker 已${action === "pause" ? "暂停" : action === "restart" ? "重启" : action === "start" ? "启动" : action === "resume" ? "继续" : "停止"}`, "success");
}

async function refreshWorkerLogs(accountId = "", slot = 0) {
  // 多账号/多槽位并行时每个目标一份日志；不指定就读第一个目标的。
  const params = new URLSearchParams({ tail: "200" });
  if (accountId) params.set("account_id", accountId);
  if (Number(slot || 0) > 0) params.set("slot", String(Number(slot)));
  const result = await api(`/api/worker/logs?${params.toString()}`);
  const output = $("settings-worker-logs");
  output.hidden = false;
  output.textContent = (result.lines || []).join("\n") || "暂无 Worker 日志。";
}

const AUTO_LOGIN_ERROR_LABELS = {
  mabang_page_not_loaded: "未找到马帮页面",
  login_entry_not_found: "未找到登录入口",
  login_did_not_complete_check_credentials: "登录未完成，请检查账号密码",
  mabang_captcha_or_verification: "出现滑块/安全验证",
  mabang_captcha_after_submit: "登录后出现滑块/安全验证",
  missing_login_environment: "未读取到绑定的账号",
  cdp_unreachable: "浏览器调试端口连接失败",
};

function autoLoginErrorDetail(autoLogin) {
  const code = String(autoLogin?.error || "");
  if (!code) return "";
  return `（${AUTO_LOGIN_ERROR_LABELS[code] || code}）`;
}

async function launchAutomationBrowser() {
  const status = $("settings-worker-browser-status");
  if (status) status.textContent = "正在启动自动化浏览器...";
  const button = $("worker-browser-btn");
  if (button) button.disabled = true;
  try {
    const launch = await api("/api/mabang-browser/start", { method: "POST", body: "{}" });
    const autoLoginStatus = launch.auto_login?.status || "";
    const modeSuffix = launch.browser_mode === "headless" ? "（无头模式，不显示窗口）" : "";
    let message;
    if (autoLoginStatus === "logged_in" || autoLoginStatus === "already_logged_in") {
      message = `自动化浏览器已就绪${modeSuffix}，已用绑定的账号自动登录马帮。Worker 会在几秒内自动领取待处理任务。`;
    } else if (autoLoginStatus === "captcha_required") {
      message = `自动化浏览器已启动${modeSuffix}，但马帮出现滑块/安全验证：请在 设置 → 马帮连接 关闭「无头启动」后用窗口模式手动完成登录。`;
    } else if (autoLoginStatus === "no_credentials") {
      message = launch.login_required
        ? `自动化浏览器已启动${modeSuffix}。未绑定自动登录账号：可在 设置 → 马帮连接 绑定，或用窗口模式手动登录一次（之后会记住）。`
        : `自动化浏览器已就绪${modeSuffix}。Worker 会在几秒内自动领取待处理任务。`;
    } else if (launch.login_required) {
      message = `自动化浏览器已启动${modeSuffix}，自动登录未成功${autoLoginErrorDetail(launch.auto_login)}：无头模式下无法人工过验证，请改用窗口模式手动登录（登录一次即可，之后会记住）。`;
    } else {
      message = `自动化浏览器已就绪${modeSuffix}。Worker 会在几秒内自动领取待处理任务。`;
    }
    if (status) status.textContent = message;
    showOperationFeedback("自动化浏览器已启动", "success");
  } finally {
    if (button) button.disabled = false;
  }
}

async function testRuntimeConnection(type) {
  const isNas = type === "nas";
  const target = $(isNas ? "settings-nas-status" : "settings-mabang-status");
  target.textContent = "正在检测...";
  const payload = isNas ? { asset_root: $("settings-nas-root").value.trim() } : {};
  const result = await api(`/api/settings/${type}/test`, { method: "POST", body: JSON.stringify(payload) });
  target.textContent = `${result.message} · ${result.tested_at}`;
}

function renderSizeOptions(selected = []) {
  const root = $("store-template-size-options");
  if (!root) return;
  root.querySelectorAll("label").forEach((item) => item.remove());
  // Selected sizes first, in the user-defined order, then unselected standard sizes.
  const ordered = [
    ...selected,
    ...[...STANDARD_SIZES].filter((size) => !selected.includes(size)),
  ];
  for (const size of ordered) {
    const isSelected = selected.includes(size);
    const label = document.createElement("label");
    label.className = `size-chip ${isSelected ? "size-chip--selected" : ""}`;
    label.draggable = isSelected;
    label.dataset.templateSizeLabel = size;
    label.innerHTML = `
      <span class="size-chip-handle" aria-hidden="true" ${isSelected ? "" : "hidden"}>⋮⋮</span>
      <input type="checkbox" data-template-size="${escapeHtml(size)}" ${isSelected ? "checked" : ""} />
      <span class="size-chip-label">${escapeHtml(size)}</span>
    `;
    root.appendChild(label);
  }
  attachSizeChipDragHandlers(root);
}

function selectedTemplateSizes() {
  const root = $("store-template-size-options");
  if (!root) return [];
  return [...root.querySelectorAll('label.size-chip--selected [data-template-size]:checked')]
    .map((item) => item.dataset.templateSize)
    .filter(Boolean);
}

// 尺码 chip 落点：光标在目标 chip 左半边插到它前面，右半边插到它后面。
// 按指针位置（而不是按索引）判断，被拖动的 chip 恰好落到光标下，dragover 反复
// 触发时不会来回跳；往前/往后拖都能走到第一位和最后一位。
function sizeChipInsertSide(clientX, target) {
  const rect = target.getBoundingClientRect();
  return clientX > rect.left + rect.width / 2 ? "after" : "before";
}

function attachSizeChipDragHandlers(root) {
  if (!root || root.dataset.sizeDragBound === "true") return;
  root.dataset.sizeDragBound = "true";
  let draggingSize = "";
  root.addEventListener("dragstart", (event) => {
    const label = event.target instanceof HTMLElement ? event.target.closest("label.size-chip--selected") : null;
    if (!label) { event.preventDefault(); return; }
    draggingSize = label.dataset.templateSizeLabel || "";
    label.classList.add("size-chip--dragging");
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", draggingSize);
    }
  });
  root.addEventListener("dragend", () => {
    root.querySelectorAll(".size-chip--dragging").forEach((el) => el.classList.remove("size-chip--dragging"));
    draggingSize = "";
  });
  root.addEventListener("dragover", (event) => {
    if (!draggingSize) return;
    const target = event.target instanceof HTMLElement ? event.target.closest("label.size-chip--selected") : null;
    const dragged = target ? root.querySelector(`label.size-chip[data-template-size-label="${CSS.escape(draggingSize)}"]`) : null;
    if (!target || !dragged || target === dragged) return;
    event.preventDefault();
    // 直接搬动那个 chip（而不是整段重排），勾选状态和价格行都留在同一个节点上。
    if (sizeChipInsertSide(event.clientX, target) === "after") target.after(dragged);
    else target.before(dragged);
    renderSizePriceOverrides(collectSizePriceOverrides());
    renderDefaultSkuSizePriceOverrides(collectDefaultSkuSizePriceOverrides());
  });
}

// 按尺码的价格覆盖有两种形态：存量数据是「尺码 → 原价」标量，新数据是
// { normal_price, sale_price } 一对；读的时候统一成一对。
function sizePriceOverride(value) {
  if (value && typeof value === "object") {
    return {
      normal_price: String(value.normal_price ?? "").trim(),
      sale_price: String(value.sale_price ?? "").trim(),
    };
  }
  return { normal_price: String(value ?? "").trim(), sale_price: "" };
}

// 尺码行留空时的售价＝自动值：按尺码设了原价就按同一折扣比例换算，否则继承
// 「交易信息」的售价（没填就是原价六折）——和后台 storage._site_row_prices 一致。
function sizeAutoSalePrice(normalOverride = "") {
  if (normalOverride) return applyStoreTemplateRatio(normalOverride);
  return storeTemplateSalePrice() || calculateFortyPercentDiscount($("store-template-normal-price")?.value) || "";
}

function sizeSalePlaceholder(normalOverride = "") {
  const auto = sizeAutoSalePrice(normalOverride);
  if (!auto) return "留空自动换算";
  return normalOverride ? `自动 ${auto}` : `继承 ${auto}`;
}

function sizeNormalPlaceholder() {
  const inherited = $("store-template-normal-price")?.value.trim() || "";
  return inherited ? `继承 ${inherited}` : "继承默认原价";
}

function renderSizePriceOverrides(values = {}) {
  const root = $("store-template-size-price-overrides");
  if (!root) return;
  const sizes = selectedTemplateSizes();
  root.innerHTML = sizes.length ? `<fieldset class="size-price-editor"><legend>按尺码设置价格</legend><small class="muted">留空＝自动：原价继承「交易信息」，售价按同一折扣比例换算；填了就按填的走。</small><div class="size-price-grid"><strong>尺码</strong><strong>原价</strong><strong>六折售价</strong>${sizes.map((size) => {
    const override = sizePriceOverride(values[size]);
    return `<span>${escapeHtml(size)}</span><input data-size-price="${escapeHtml(size)}" type="number" min="0" step="0.01" value="${escapeHtml(override.normal_price)}" placeholder="${escapeHtml(sizeNormalPlaceholder())}" /><input data-size-sale-price="${escapeHtml(size)}" type="number" min="0" step="0.01" value="${escapeHtml(override.sale_price)}" placeholder="${escapeHtml(sizeSalePlaceholder(override.normal_price))}" title="留空＝自动换算" />`;
  }).join("")}</div></fieldset>` : "";
}

// 操作员在原价格里改数字时只重算占位符（自己填过的售价不动），不做整表重建。
function refreshSizePricePlaceholders() {
  const root = $("store-template-size-price-overrides");
  if (!root) return;
  const normalPlaceholder = sizeNormalPlaceholder();
  root.querySelectorAll("[data-size-price]").forEach((input) => {
    input.placeholder = normalPlaceholder;
    const saleInput = root.querySelector(`[data-size-sale-price="${CSS.escape(input.dataset.sizePrice || "")}"]`);
    if (saleInput) saleInput.placeholder = sizeSalePlaceholder(input.value.trim());
  });
}

function collectSizePriceOverrides() {
  const overrides = {};
  for (const input of document.querySelectorAll("#store-template-size-price-overrides [data-size-price]")) {
    const size = input.dataset.sizePrice || "";
    if (!size) continue;
    const saleInput = document.querySelector(`#store-template-size-price-overrides [data-size-sale-price="${CSS.escape(size)}"]`);
    const entry = {};
    const normal = input.value.trim();
    const sale = saleInput ? saleInput.value.trim() : "";
    if (normal) entry.normal_price = normal;
    if (sale) entry.sale_price = sale;
    if (Object.keys(entry).length) overrides[size] = entry;
  }
  return overrides;
}

function priceCents(value) {
  const text = String(value ?? "").trim();
  if (!/^\d+(\.\d{0,2})?$/.test(text)) return null;
  const [whole, fraction = ""] = text.split(".");
  return Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
}

function calculateFortyPercentDiscount(value) {
  const cents = priceCents(value);
  return cents === null ? "" : (Math.round(cents * 60 / 100) / 100).toFixed(2);
}

// 模板「交易信息」的售价 = 原价 × 折扣比例。比例由操作员填的原价/六折售价决定，
// 没填（或填了 0）时退回六折——和后台 storage._site_sale_price 同一套规则。
function storeTemplateSalePrice() {
  return $("store-template-sale-price")?.value.trim() || "";
}

function storeTemplateSalePriceIsManual() {
  return $("store-template-sale-price")?.dataset.manual === "1";
}

function applyStoreTemplateRatio(value) {
  const cents = priceCents(value);
  if (cents === null) return "";
  const normalCents = priceCents($("store-template-normal-price")?.value);
  const saleCents = priceCents(storeTemplateSalePrice());
  const ratio = normalCents && saleCents ? saleCents / normalCents : 0.6;
  return (Math.round(cents * ratio) / 100).toFixed(2);
}

async function loadStorePromotions(keepActivityId = "") {
  const storeId = $("store-template-store")?.value || "";
  const select = $("store-template-promotion");
  if (!select) return;
  if (!storeId) {
    select.innerHTML = '<option value="">活动未配置</option>';
    renderStorePromotionStatus(null);
    return;
  }
  const data = await api(`/api/store-promotions?store_id=${encodeURIComponent(storeId)}&available_only=true`);
  let items = data.items || [];
  // 模板上已经绑的活动如果这一版不可用（采集时没读到活动列表 → unresolved），
  // 下拉里就没有它。只显示可用项的话，操作员一保存就会把模板上的活动写成空，
  // 所以把它作为一条不可选、只用于回显的记录补回去。
  const keep = String(keepActivityId || "").trim();
  if (keep && !items.some((item) => String(item.activity_id || item.id || "") === keep)) {
    const all = await api(`/api/store-promotions?store_id=${encodeURIComponent(storeId)}`).catch(() => ({ items: [] }));
    const retained = (all.items || []).find((item) => String(item.activity_id || item.id || "") === keep);
    if (retained) items = [...items, { ...retained, retained: true }];
  }
  state.storePromotions = items;
  select.innerHTML = items.length
    ? '<option value="">请选择店铺促销活动</option>' + items.map((item) => {
      const label = String(item.label || item.name || item.activity_id);
      const suffix = item.retained ? "（当前不可用，仅保留原值）" : "";
      return `<option value="${escapeHtml(item.activity_id || item.id)}" data-label="${escapeHtml(label)}"${item.retained ? " disabled" : ""}>${escapeHtml(label + suffix)}</option>`;
    }).join("")
    : '<option value="">活动未配置</option>';
  select.disabled = !items.some((item) => !item.retained);
  renderStorePromotionStatus(data.status);
}

/**
 * 活动目录的采集状态：目录几条、上次何时采到、上次为什么失败。
 * 页面导入没读到活动列表时目录只有名称/没有有效期，这里要把原因摆出来，
 * 操作员才知道该点「重新采集活动」。
 */
function renderStorePromotionStatus(status, options = {}) {
  const target = $("store-template-promotion-status");
  if (!target) return;
  const error = String(options.error || "").trim();
  if (error) {
    target.textContent = `采集失败：${error}`;
    return;
  }
  if (!status) {
    target.textContent = "";
    return;
  }
  const count = Number(status.count || 0);
  if (status.status === "ok") {
    const time = formatLocalTime(status.collected_at);
    target.textContent = options.justCollected
      ? `目录 ${count} 条 · 刚刚采集`
      : `目录 ${count} 条 · 采集于 ${time}`;
    return;
  }
  const reason = String(status.error || "").trim();
  target.textContent = reason ? `上次采集失败：${reason}` : "上次采集失败";
}

async function recollectStorePromotions() {
  const storeId = $("store-template-store")?.value || "";
  const button = $("store-template-promotion-recollect");
  const status = $("store-template-promotion-status");
  if (!storeId) {
    renderStorePromotionStatus(null, { error: "请先选择店铺" });
    return;
  }
  if (button?.disabled) return;
  const keep = $("store-template-promotion")?.value || "";
  if (button) {
    button.disabled = true;
    button.textContent = "采集中…";
  }
  if (status) status.textContent = "正在读取马帮「选择折扣活动」列表…";
  try {
    const result = await api("/api/store-promotions/collect", {
      method: "POST",
      body: JSON.stringify({
        store_id: storeId,
        account_id: currentStoreTemplateAccountId() || undefined,
      }),
    });
    await loadStorePromotions(keep);
    renderStorePromotionStatus(result.status, { justCollected: true });
  } catch (error) {
    renderStorePromotionStatus(null, { error: String(error?.message || error) });
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "重新采集活动";
    }
  }
}

const WATERMARK_ROLES = ["main", "product"];

function watermarkRoleInput(role) {
  return role === "main" ? $("store-template-main-watermark") : $("store-template-product-watermark");
}

function watermarkRoleTrigger(role) {
  return role === "main" ? $("store-template-main-watermark-trigger") : $("store-template-product-watermark-trigger");
}

function watermarkRoleMenu(role) {
  return document.querySelector(`.watermark-menu[data-watermark-menu="${role}"]`);
}

function watermarkRolePicker(role) {
  return document.querySelector(`.watermark-picker[data-watermark-picker="${role}"]`);
}

function watermarkRoleEmptyHint(role) {
  return document.querySelector(`.watermark-empty-hint[data-watermark-empty="${role}"]`);
}

function watermarkChoiceTemplateId(role) {
  return watermarkRoleInput(role)?.value || "";
}

// 已保存的水印名称存在隐藏输入框的 data-watermark-name 上。
// 这样即使目录没加载（换浏览器 / 没采集 / 采集失败），已配置的水印也能照原样回显和保存，
// 不会被静默清空——之前只看 state.storeWatermarks，目录一空选择就被清掉。
function watermarkChoiceName(role) {
  return watermarkRoleInput(role)?.dataset?.watermarkName || "";
}

function setWatermarkChoiceValue(role, templateId, name) {
  const input = watermarkRoleInput(role);
  if (!input) return;
  input.value = templateId || "";
  input.dataset.watermarkName = name || "";
}

function resetStoreWatermarkChoices() {
  for (const role of WATERMARK_ROLES) {
    setWatermarkChoiceValue(role, "", "");
    const manualId = document.querySelector(`[data-watermark-manual-id="${role}"]`);
    const manualName = document.querySelector(`[data-watermark-manual-name="${role}"]`);
    if (manualId) manualId.value = "";
    if (manualName) manualName.value = "";
    renderStoreWatermarkPreview(role);
  }
}

function watermarkCatalogItem(templateId) {
  const wanted = String(templateId || "");
  if (!wanted) return null;
  return state.storeWatermarks.find((item) => String(item.template_id) === wanted) || null;
}

function formatWatermarkCollectedAt(value) {
  const text = String(value || "");
  if (!text) return "";
  return typeof formatLocalTime === "function" ? formatLocalTime(text) : text;
}

// 目录状态行：让操作员一眼看出「采到了几条、什么时候采的、上次为什么没采到」。
function renderWatermarkCatalogStatus() {
  const target = $("store-template-watermark-catalog-status");
  if (!target) return;
  const status = state.watermarkCatalogStatus;
  const count = state.storeWatermarks.length;
  target.className = "watermark-catalog-status muted";
  if (!status) {
    target.textContent = count ? `已加载 ${count} 条` : "未采集";
    return;
  }
  const collectedAt = formatWatermarkCollectedAt(status.collected_at);
  const attemptedAt = formatWatermarkCollectedAt(status.attempted_at);
  if (status.status === "ok") {
    target.className = "watermark-catalog-status watermark-catalog-status--ok";
    target.textContent = `${count} 条${collectedAt ? ` · 采集于 ${collectedAt}` : ""}`;
    return;
  }
  target.className = "watermark-catalog-status watermark-catalog-status--failed";
  target.textContent = `上次采集失败：${status.error || "原因未知"}${attemptedAt ? `（${attemptedAt}）` : ""}${
    count ? ` · 目录仍是旧的 ${count} 条` : ""
  }`;
}

async function loadStoreWatermarks() {
  const storeId = $("store-template-store")?.value || "";
  if (!storeId) {
    state.storeWatermarks = [];
    state.watermarkCatalogStatus = null;
    state.watermarkCatalogStoreId = "";
    resetStoreWatermarkChoices();
    renderWatermarkPicker("main");
    renderWatermarkPicker("product");
    renderWatermarkCatalogStatus();
    return;
  }
  // 切换店铺时清空旧目录和旧选择，再加载新店铺目录。
  if (state.watermarkCatalogStoreId !== "" && state.watermarkCatalogStoreId !== storeId) {
    resetStoreWatermarkChoices();
    closeWatermarkMenu();
  }
  state.watermarkCatalogStoreId = storeId;
  const data = await api(`/api/store-watermarks?store_id=${encodeURIComponent(storeId)}`);
  state.storeWatermarks = data.items || [];
  state.watermarkCatalogStatus = data.status || null;
  // 目录里没有这个 ID 时**保留**已配置的水印（可能只是还没采集/换过浏览器）：
  // 只在界面上标「未在目录中」，让操作员自己决定是重采还是手动改。
  renderWatermarkPicker("main");
  renderWatermarkPicker("product");
  renderWatermarkCatalogStatus();
}

async function collectStoreWatermarks() {
  const storeId = $("store-template-store")?.value || "";
  const btn = $("store-template-watermark-collect-btn");
  const status = $("store-template-watermark-catalog-status");
  if (!storeId) {
    if (status) status.textContent = "请先选择店铺";
    return;
  }
  if (btn) btn.disabled = true;
  if (status) {
    status.className = "watermark-catalog-status muted";
    status.textContent = "正在采集，请让马帮 Chrome 停在商品编辑页…";
  }
  try {
    const result = await api("/api/store-watermarks/collect", {
      method: "POST",
      body: JSON.stringify({ store_id: storeId }),
    });
    await loadStoreWatermarks();
    if (status && result?.count === 0) {
      status.className = "watermark-catalog-status watermark-catalog-status--warn";
      status.textContent = "采集成功，但这个店铺的马帮水印列表是空的";
    }
  } catch (error) {
    // 采集失败：保留原目录与原选择，把原因写在状态行上（不再抛给通用错误提示）。
    await loadStoreWatermarks().catch(() => {});
    if (status) {
      status.className = "watermark-catalog-status watermark-catalog-status--failed";
      status.textContent = `采集失败：${error?.message || error}`;
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

// 手动兜底：采集不到目录时直接写模板 ID + 名称，自动化按 ID + 名称去点菜单。
function applyManualWatermark(role) {
  const idInput = document.querySelector(`[data-watermark-manual-id="${role}"]`);
  const nameInput = document.querySelector(`[data-watermark-manual-name="${role}"]`);
  const templateId = (idInput?.value || "").trim();
  const name = (nameInput?.value || "").trim();
  const status = $("store-template-watermark-catalog-status");
  if (!templateId || !name) {
    if (status) {
      status.className = "watermark-catalog-status watermark-catalog-status--warn";
      status.textContent = "手动填写要同时给「模板 ID」和「模板名称」";
    }
    return;
  }
  setWatermarkChoiceValue(role, templateId, name);
  renderWatermarkPicker(role);
  renderStoreWatermarkPreview(role);
  if (status) {
    status.className = "watermark-catalog-status muted";
    status.textContent = `${role === "main" ? "主图" : "产品图"}水印已手动设为「${name}」（执行时按 ID + 名称匹配）`;
  }
}

function watermarkMenuItemMarkup(item, selectedId) {
  const thumb = item.preview_url
    ? `<img class="watermark-menu-thumb" src="${escapeHtml(item.preview_url)}" alt="" />`
    : '<span class="watermark-menu-thumb watermark-menu-thumb-empty">无图</span>';
  const name = escapeHtml(item.name || "未命名水印");
  return `<button type="button" class="watermark-menu-item" role="option" data-watermark-template-id="${escapeHtml(String(item.template_id || ""))}" aria-selected="${String(item.template_id) === String(selectedId) ? "true" : "false"}">${thumb}<span class="watermark-menu-name">${name}</span></button>`;
}

function renderWatermarkPicker(role) {
  const menu = watermarkRoleMenu(role);
  const hint = watermarkRoleEmptyHint(role);
  const trigger = watermarkRoleTrigger(role);
  if (!menu || !trigger) return;
  const hasCatalog = state.storeWatermarks.length > 0;
  const selectedId = watermarkChoiceTemplateId(role);
  if (hasCatalog) {
    const closeMarkup = `<button type="button" class="watermark-menu-item watermark-menu-close" role="option" data-watermark-template-id="" data-watermark-close aria-selected="${selectedId === "" ? "true" : "false"}"><span class="watermark-menu-close-label">关闭水印</span></button>`;
    menu.innerHTML = closeMarkup + state.storeWatermarks.map((item) => watermarkMenuItemMarkup(item, selectedId)).join("");
    menu.hidden = true;
    if (state.watermarkOpenRole === role) state.watermarkOpenRole = "";
  } else {
    menu.innerHTML = "";
    menu.hidden = true;
  }
  trigger.disabled = !hasCatalog;
  trigger.setAttribute("aria-expanded", "false");
  trigger.title = hasCatalog ? "" : "请先采集水印";
  if (hint) hint.hidden = hasCatalog;
  renderStoreWatermarkPreview(role);
}

function renderStoreWatermarkPreview(role) {
  const input = watermarkRoleInput(role);
  const preview = role === "main"
    ? $("store-template-main-watermark-preview")
    : $("store-template-product-watermark-preview");
  const trigger = watermarkRoleTrigger(role);
  const triggerLabel = trigger?.querySelector("[data-watermark-trigger-label]");
  const triggerThumb = trigger?.querySelector("[data-watermark-trigger-thumb]");
  const templateId = input?.value || "";
  const savedName = watermarkChoiceName(role);
  const item = state.storeWatermarks.find((entry) => String(entry.template_id) === templateId);
  // 目录里没有这个 ID（还没采集 / 采集失败 / 换浏览器）时，用已保存的名称照原样显示，
  // 并标一句「未在目录中」——绝不把已配置的水印显示成「关闭」。
  const resolved = item || (templateId ? { template_id: templateId, name: savedName || templateId, preview_url: "" } : null);
  if (preview) {
    if (!resolved) {
      preview.innerHTML = '<span class="watermark-choice-none">关闭（不加水印）</span>';
    } else {
      const image = resolved.preview_url
        ? `<img src="${escapeHtml(resolved.preview_url)}" alt="${escapeHtml(resolved.name || "水印缩略图")}" />`
        : '<span class="watermark-choice-empty">无缩略图</span>';
      const warning = item ? "" : '<span class="watermark-choice-warning">未在目录中：执行时按 ID + 名称匹配，匹配不上只报警告</span>';
      preview.innerHTML = `${image}<span class="watermark-choice-resolved"><strong>${escapeHtml(resolved.name || "未命名水印")}</strong><span class="watermark-choice-id">ID ${escapeHtml(
        String(resolved.template_id || ""),
      )}</span>${warning}</span>`;
    }
  }
  if (trigger) {
    if (!resolved) {
      if (triggerLabel) triggerLabel.textContent = "选择水印模板…";
      if (triggerThumb) {
        triggerThumb.hidden = true;
        triggerThumb.removeAttribute("src");
      }
    } else {
      if (triggerLabel) triggerLabel.textContent = resolved.name || "未命名水印";
      if (triggerThumb) {
        if (resolved.preview_url) {
          triggerThumb.src = resolved.preview_url;
          triggerThumb.alt = resolved.name || "水印缩略图";
          triggerThumb.hidden = false;
        } else {
          triggerThumb.hidden = true;
          triggerThumb.removeAttribute("src");
        }
      }
    }
  }
  // 手动兜底输入框跟着当前选择走（operator 想改名称/ID 时不用重新敲一遍）。
  const manualId = document.querySelector(`[data-watermark-manual-id="${role}"]`);
  const manualName = document.querySelector(`[data-watermark-manual-name="${role}"]`);
  if (manualId && !manualId.value) manualId.value = templateId;
  if (manualName && !manualName.value) manualName.value = resolved?.name && resolved.name !== templateId ? resolved.name : savedName;
}

function selectedWatermark(selectId, role) {
  const select = $(selectId);
  const templateId = select?.value || "";
  if (!templateId) return {};
  const item = state.storeWatermarks.find((entry) => String(entry.template_id) === templateId);
  // 目录没加载时也不能丢掉已配置的水印：名称回退到隐藏输入框上保存的那份。
  const name = item?.name || watermarkChoiceName(role) || "";
  if (!name) return {};
  const ref = { template_id: templateId, name };
  if (role) {
    const modeInput = document.querySelector(`input[name="store-template-${role}-watermark-mode"]:checked`);
    const mode = modeInput?.value === "custom" ? "custom" : "auto";
    ref.mode = mode;
    if (mode === "custom") {
      const boxes = document.querySelectorAll(`.watermark-position-checks[data-watermark-role="${role}"] input[type="checkbox"]:checked`);
      ref.positions = Array.from(boxes).map((box) => Number(box.value)).filter((n) => n >= 1 && n <= 9).sort((a, b) => a - b);
    } else {
      ref.positions = [];
    }
  }
  return ref;
}

// 位置槽位（自定义范围）：按角色给真实可用的位置，别让操作员勾出执行器必然会拒的组合。
//   主图水印：只能第 1 张；产品图水印：第 2 张起。末位那张尺码表不算产品图（见 storage
//   「产品图最后一张放尺码表」），所以产品图位置最多到 9，但界面不承诺它一定存在。
const WATERMARK_POSITION_LIMIT = 9;

function watermarkAllowedPositions(role) {
  return role === "main"
    ? [1]
    : Array.from({ length: WATERMARK_POSITION_LIMIT - 1 }, (_, index) => index + 2);
}

function renderWatermarkPositionChecks(role, positions) {
  const wrap = document.querySelector(`.watermark-position-checks[data-watermark-role="${role}"]`);
  if (!wrap) return;
  const allowed = new Set(watermarkAllowedPositions(role));
  const chosen = new Set((Array.isArray(positions) ? positions.map(Number) : []).filter((n) => allowed.has(n)));
  const checks = Array.from({ length: WATERMARK_POSITION_LIMIT }, (_, i) => i + 1).map((n) => {
    const blocked = !allowed.has(n);
    const title = blocked
      ? (role === "main" ? "主图水印只能作用在第 1 张" : "产品图水印从第 2 张起，第 1 张是主图")
      : `第 ${n} 张`;
    return `<label class="watermark-position-check${blocked ? " watermark-position-check--blocked" : ""}" title="${escapeHtml(title)}">
      <input type="checkbox" value="${n}" ${chosen.has(n) ? "checked" : ""} ${blocked ? "disabled" : ""} /> 第${n}张
    </label>`;
  });
  const hint = role === "main"
    ? "主图水印只能落在第 1 张。"
    : "产品图水印落在第 2 张起的产品图（末位那张尺码表不算）。";
  wrap.innerHTML = `${checks.join("")}<p class="watermark-position-checks-hint">${escapeHtml(hint)}</p>`;
}

function applyWatermarkModeUi(role, mode) {
  const radio = document.querySelector(`input[name="store-template-${role}-watermark-mode"][value="${mode === "custom" ? "custom" : "auto"}"]`);
  if (radio) radio.checked = true;
  const wrap = document.querySelector(`.watermark-position-checks[data-watermark-role="${role}"]`);
  if (wrap) wrap.hidden = mode !== "custom";
  // 切到自定义但一个都没勾时，先把该角色的默认范围勾上，别让操作员对着空列表猜。
  if (mode === "custom" && wrap && !wrap.querySelector("input[type='checkbox']:checked")) {
    for (const box of wrap.querySelectorAll("input[type='checkbox']:not([disabled])")) box.checked = true;
  }
}

function closeWatermarkMenu(role) {
  const openRole = state.watermarkOpenRole;
  if (!openRole) return;
  if (role && role !== openRole) return;
  const menu = watermarkRoleMenu(openRole);
  const trigger = watermarkRoleTrigger(openRole);
  if (menu) menu.hidden = true;
  if (trigger) trigger.setAttribute("aria-expanded", "false");
  state.watermarkOpenRole = "";
  window.clearTimeout(state.watermarkHoverCloseTimer);
  state.watermarkHoverCloseTimer = 0;
}

function openWatermarkMenu(role) {
  if (!state.storeWatermarks.length) return;
  const menu = watermarkRoleMenu(role);
  const trigger = watermarkRoleTrigger(role);
  if (!menu || !trigger) return;
  if (state.watermarkOpenRole === role) return;
  closeWatermarkMenu();
  window.clearTimeout(state.watermarkHoverCloseTimer);
  state.watermarkHoverCloseTimer = 0;
  state.watermarkOpenRole = role;
  menu.hidden = false;
  trigger.setAttribute("aria-expanded", "true");
}

function toggleWatermarkMenu(role) {
  if (state.watermarkOpenRole === role) {
    closeWatermarkMenu(role);
    return;
  }
  openWatermarkMenu(role);
}

function scheduleWatermarkMenuClose(role) {
  window.clearTimeout(state.watermarkHoverCloseTimer);
  state.watermarkHoverCloseTimer = window.setTimeout(() => {
    if (state.watermarkOpenRole === role) closeWatermarkMenu(role);
  }, 220);
}

function setWatermarkChoice(role, templateId) {
  const input = watermarkRoleInput(role);
  if (!input) return;
  // 名称一起存到输入框上：目录还没加载时也能把已配置的水印原样保存回去。
  const item = watermarkCatalogItem(templateId);
  setWatermarkChoiceValue(role, templateId, item?.name || "");
  renderStoreWatermarkPreview(role);
  const menu = watermarkRoleMenu(role);
  if (menu) {
    for (const option of menu.querySelectorAll("[data-watermark-template-id]")) {
      const selected = String(option.dataset.watermarkTemplateId) === String(templateId || "");
      option.setAttribute("aria-selected", selected ? "true" : "false");
      option.classList.toggle("is-selected", selected);
    }
  }
  closeWatermarkMenu(role);
  const trigger = watermarkRoleTrigger(role);
  if (trigger) trigger.focus({ preventScroll: true });
}

function watermarkMenuOptionTarget(option) {
  const menu = option.closest("[data-watermark-menu]");
  const role = menu?.dataset.watermarkMenu || "";
  if (!role) return null;
  const templateId = option.dataset.watermarkTemplateId || "";
  return { role, templateId };
}

function watermarkActiveOptions(role) {
  return Array.from(watermarkRoleMenu(role)?.querySelectorAll(".watermark-menu-item:not([disabled])") || []);
}

function moveWatermarkHighlight(role, direction) {
  const options = watermarkActiveOptions(role);
  if (!options.length) return;
  const currentIndex = options.findIndex((option) => option.classList.contains("is-highlighted") || option === document.activeElement);
  const nextIndex = direction === "down"
    ? (currentIndex + 1) % options.length
    : (currentIndex <= 0 ? options.length - 1 : currentIndex - 1);
  options.forEach((option) => option.classList.remove("is-highlighted"));
  options[nextIndex].classList.add("is-highlighted");
  options[nextIndex].focus({ preventScroll: true });
}

function handleWatermarkTriggerKeydown(event, role) {
  if (!["ArrowDown", "ArrowUp", "Enter", " ", "Escape"].includes(event.key)) return;
  event.preventDefault();
  if (event.key === "Escape") {
    closeWatermarkMenu(role);
    return;
  }
  openWatermarkMenu(role);
  if (event.key === "ArrowUp" || event.key === "ArrowDown") {
    const options = watermarkActiveOptions(role);
    if (!options.length) return;
    const selectedIndex = options.findIndex((option) => option.getAttribute("aria-selected") === "true");
    options.forEach((option) => option.classList.remove("is-highlighted"));
    const targetIndex = selectedIndex >= 0 ? selectedIndex : (event.key === "ArrowDown" ? 0 : options.length - 1);
    options[targetIndex].classList.add("is-highlighted");
    options[targetIndex].focus({ preventScroll: true });
    return;
  }
  // Enter / Space: pick the highlighted option, or the current selection when none highlighted.
  const menu = watermarkRoleMenu(role);
  const highlighted = menu?.querySelector(".watermark-menu-item.is-highlighted") || null;
  const pick = highlighted || menu?.querySelector('.watermark-menu-item[aria-selected="true"]') || null;
  if (!pick) return;
  const target = watermarkMenuOptionTarget(pick);
  if (target) setWatermarkChoice(target.role, target.templateId);
}

function initWatermarkPickers() {
  for (const role of WATERMARK_ROLES) {
    const picker = watermarkRolePicker(role);
    if (!picker) continue;
    picker.addEventListener("mouseenter", () => {
      window.clearTimeout(state.watermarkHoverCloseTimer);
      state.watermarkHoverCloseTimer = 0;
      openWatermarkMenu(role);
    });
    picker.addEventListener("mouseleave", () => scheduleWatermarkMenuClose(role));
    const trigger = watermarkRoleTrigger(role);
    if (trigger) {
      trigger.addEventListener("keydown", (event) => handleWatermarkTriggerKeydown(event, role));
    }
  }
  document.addEventListener("keydown", (event) => {
    if (state.watermarkOpenRole && ["ArrowDown", "ArrowUp", "Escape", "Tab"].includes(event.key)) {
      const role = state.watermarkOpenRole;
      if (event.key === "Escape") {
        event.preventDefault();
        closeWatermarkMenu(role);
        watermarkRoleTrigger(role)?.focus({ preventScroll: true });
        return;
      }
      if (event.key === "Tab") {
        closeWatermarkMenu(role);
        return;
      }
      const activeInMenu = document.activeElement?.closest(`.watermark-menu[data-watermark-menu="${role}"]`);
      if (activeInMenu) {
        event.preventDefault();
        moveWatermarkHighlight(role, event.key === "ArrowDown" ? "down" : "up");
      }
    }
  });
}

async function loadStoreSizeChartTemplates() {
  const storeId = $("store-template-store")?.value || "";
  if (!storeId) {
    state.storeSizeChartTemplates = [];
    renderSizeChartTemplateOptions();
    return;
  }
  const data = await api(`/api/store-size-chart-templates?store_id=${encodeURIComponent(storeId)}`);
  state.storeSizeChartTemplates = data.items || [];
  renderSizeChartTemplateOptions();
}

function selectedStoreSizeChartTemplate() {
  const templateId = $("store-template-size-chart-template")?.value || "";
  return state.storeSizeChartTemplates.find((item) => item.template_id === templateId) || null;
}

function renderSizeChartSource(source = "upload") {
  const normalized = source === "template" ? "template" : "upload";
  $("store-template-size-chart-source").value = normalized;
  $("store-template-size-chart-template").hidden = normalized !== "template";
  // The native file input stays hidden by CSS (.file-input-native); the button-picker
  // is what the user clicks. 选择模板时表单页上没有可引用的尺码图地址（表单项里只有
  // 「上传图片 / 选择模板 / 同步」），自动化要靠这里上传的图插描述末尾，所以两种模式
  // 都留着「选择图片」入口。
  const picker = $("store-template-size-chart-picker-btn")?.closest(".size-chart-picker");
  if (picker instanceof HTMLElement) picker.hidden = false;
  // 选模板时页面上没有可引用的尺码图，自动化用这里上传的这张图插描述末尾——文案点明；
  // 已经选过文件就保持文件名，别覆盖。
  const hint = $("store-template-size-chart-file-name");
  if (hint && !state.sizeChartAsset?.id) {
    hint.textContent = normalized === "template"
      ? "选择模板时，这张图会插到商品描述末尾 · PNG / JPEG / WebP"
      : "PNG / JPEG / WebP · 建议 <2MB";
  }
  renderStoreTemplateSizeChartPreview();
}

function renderStoreTemplateSizeChartPreview() {
  const preview = $("store-template-size-chart-preview");
  const empty = $("store-template-size-chart-empty");
  if (!preview) return;
  const source = $("store-template-size-chart-source")?.value || "upload";
  const selectedTemplate = source === "template" ? selectedStoreSizeChartTemplate() : null;
  // 操作员上传的那张图优先：选模板时它就是自动化会插进描述末尾的尺码表图；
  // 没上传才回落到模板自己的预览图（采集到时才有）。
  const url = state.sizeChartAsset?.preview_url
    || (source === "template" ? (selectedTemplate?.preview_url || "") : "");
  preview.src = sizeChartPreviewUrl(url, 720);
  preview.hidden = !url;
  if (empty) empty.hidden = Boolean(url);
}

function renderSizeChartTemplateOptions() {
  const select = $("store-template-size-chart-template");
  if (!select) return;
  const current = select.value || "";
  const options = new Map();
  for (const item of state.storeSizeChartTemplates) {
    if (!item.template_id) continue;
    const availability = item.preview_url || item.asset_id ? "" : "（仅马帮模板）";
    options.set(item.template_id, `马帮尺码图 / ${item.name || item.template_id}${availability}`);
  }
  select.innerHTML = '<option value="">选择已有尺码图</option>' + [...options.entries()].map(([value, label]) =>
    `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`
  ).join("");
  if ([...select.options].some((option) => option.value === current)) select.value = current;
  renderStoreTemplateSizeChartPreview();
}

async function loadHealth() {
  try {
    const data = await api("/api/health");
    $("service-status").textContent = data.ok ? "服务正常，马帮导出链路运行中" : "服务异常";
  } catch (err) {
    $("service-status").textContent = `服务不可用：${err.message}`;
  }
}

async function loadReadiness() {
  const data = await api("/api/deployment/readiness");
  renderReadiness(data);
}

async function loadCategoryProfiles() {
  const data = await api("/api/category-profiles");
  state.categoryProfiles = data.items || [];
  renderCategoryProfileOptions();
  renderStoreTemplateSizeChartOptions();
}

async function loadAssets() {
  const data = await api("/api/assets");
  state.assets = data.items || [];
  renderAssets();
  renderBaseProducts();
  renderStoreTemplateSizeChartOptions();
}

// 资源图片地址：带上版本参数（更新时间/大小）。
// 现场问题：导入商品后列表图片要刷新页面才更新 —— 同 id 的图片换了字节，地址却一样，
// 浏览器（以及"渲染时文件还没写好"的那次请求）会把旧结果一直用下去。
function assetVersionToken(asset) {
  // file_hash 最准：内容变了地址就变（内容没变则地址不变，仍可命中缓存）
  const candidates = [asset?.file_hash, asset?.updated_at, asset?.created_at, asset?.file_mtime, asset?.size_bytes, asset?.file_size];
  const value = candidates.find((item) => item !== undefined && item !== null && String(item) !== "");
  return value === undefined ? "" : String(value);
}

// thumbEdge > 0 时走缩略图接口（服务端按长边缩放并磁盘缓存的 JPEG）。
// 现场素材库 2000 多张原图、合计 3.5GB（单张最大 16MB），列表/卡片直接挂原图，
// 一屏就是几十上百 MB —— 图片加载慢就慢在这里。列表/卡片一律传 thumbEdge。
function assetImageUrl(asset, thumbEdge = 0) {
  const id = String(asset?.id || "");
  if (!id) return "";
  const version = assetVersionToken(asset);
  const parsedEdge = Math.round(Number(thumbEdge) || 0);
  const edge = parsedEdge > 0 ? Math.min(1024, Math.max(64, parsedEdge)) : 0;
  const params = [];
  if (edge) params.push(`w=${edge}`);
  if (version) params.push(`v=${encodeURIComponent(version)}`);
  const suffix = params.length ? `?${params.join("&")}` : "";
  return `/api/assets/${encodeURIComponent(id)}/${edge ? "thumb" : "file"}${suffix}`;
}

// 尺码图预览：库里存的仍是原始地址（后端契约与测试都依赖它），只在渲染时挂 ?w=。
// 现场那张尺码表原图 1.1MB，而列表里只显示 72px、编辑器预览也就几百像素。
function sizeChartPreviewUrl(url, edge = 480) {
  const raw = String(url || "");
  if (!raw || !raw.startsWith("/api/uploads/size-chart/")) return raw;
  return `${raw}${raw.includes("?") ? "&" : "?"}w=${encodeURIComponent(edge)}`;
}

async function loadBaseProducts() {
  const data = await api("/api/base-products");
  state.baseProducts = data.items || [];
  renderBaseProducts();
}

async function loadStoreTemplates() {
  await ensureStoreTemplateAccountsLoaded();
  const accountId = currentStoreTemplateAccountId();
  const scopedQuery = accountId ? `?account_id=${encodeURIComponent(accountId)}` : "";
  // 两次请求：scoped 是当前账号的模板（列表、复制选择器、店铺下拉都用它）；
  // all 只做名称索引，刊登副本列表要能显示别的账号模板建的副本。
  const [scoped, all] = await Promise.all([
    api(`/api/store-templates${scopedQuery}`),
    api("/api/store-templates"),
  ]);
  state.storeTemplates = scoped.items || [];
  state.storeTemplatesAll = all.items || [];
  renderStoreTemplateAccountOptions();
  renderStoreTemplateSizeChartOptions();
  renderStoreTemplates();
}

// 首屏 loadStoreTemplates 与设置页的账号加载是并行的：账号还没到就先自己拉一次，
// 否则会先用空账号渲染一遍「全部模板」。
async function ensureStoreTemplateAccountsLoaded() {
  if (state.storeTemplateAccountsLoaded) return;
  try {
    const data = await api("/api/mabang/accounts");
    state.mabangAccounts = data.items || [];
    state.mabangAccountsDefaultId = data.default_account_id || "";
  } catch (error) {
    // 拉不到账号就退回「不隔离」的旧行为，不要挡住模板列表。
    console.warn("马帮账号列表加载失败，店铺模板暂不按账号过滤", error);
  }
  state.storeTemplateAccountsLoaded = true;
  renderStoreTemplateAccountOptions();
}

function currentStoreTemplateAccountId() {
  const accounts = state.mabangAccounts || [];
  if (!accounts.length) return "";
  const known = (id) => accounts.some((item) => String(item.id || "") === id);
  const explicit = String(state.storeTemplateAccountId || "");
  if (explicit && known(explicit)) return explicit;
  const fallback = String(state.mabangAccountsDefaultId || "");
  if (fallback && known(fallback)) return fallback;
  const first = accounts.find((item) => item.enabled !== false) || accounts[0];
  return String(first?.id || "");
}

function storeTemplateAccountLabel(accountId) {
  const wanted = String(accountId || "");
  if (!wanted) return "";
  const account = (state.mabangAccounts || []).find((item) => String(item.id || "") === wanted);
  return account ? String(account.name || account.login_account || "") : "";
}

function renderStoreTemplateAccountOptions() {
  const select = $("store-template-account");
  if (!select) return;
  const accounts = state.mabangAccounts || [];
  if (!accounts.length) {
    select.innerHTML = '<option value="">默认账号（未配置多账号）</option>';
    select.disabled = true;
    return;
  }
  select.disabled = false;
  const current = currentStoreTemplateAccountId();
  select.innerHTML = accounts.map((item) => {
    const label = String(item.name || item.login_account || "未命名账号");
    return `<option value="${escapeHtml(item.id)}">${escapeHtml(label)}${item.enabled === false ? "（已停用）" : ""}</option>`;
  }).join("");
  if (current) select.value = current;
}

// 当前账号的所有模板（含归档之外的其它账号模板）：列表回显时用，不做过滤。
function storeTemplateById(templateId) {
  const wanted = String(templateId || "");
  if (!wanted) return null;
  return (state.storeTemplates || []).find((item) => item.id === wanted)
    || (state.storeTemplatesAll || []).find((item) => item.id === wanted)
    || null;
}

// 模板编辑器里的店铺下拉只列当前账号采集到的店铺：账号之间的店铺不互相可见。
function storeTemplateScopedStoreOptions() {
  const options = state.mabangStoreOptions || [];
  const accountId = currentStoreTemplateAccountId();
  if (!accountId) return options;
  return options.filter((option) => String(option.account_id || "") === accountId);
}

async function switchStoreTemplateAccount(accountId) {
  state.storeTemplateAccountId = String(accountId || "");
  state.selectedStoreTemplates.clear();
  await loadStoreTemplates();
  renderMabangStoreOptions();
}

async function loadMabangStoreOptions() {
  const data = await api("/api/mabang/store-options?platform=shopee");
  state.mabangStoreOptions = data.items || [];
  renderMabangStoreOptions();
}

async function loadStoreListingCopies() {
  const data = await api("/api/store-listing-copies");
  state.storeListingCopies = data.items || [];
  renderStoreListingCopies();
}

async function loadAutomationJobs() {
  const data = await api("/api/automation/jobs");
  state.automationJobs = data.items || [];
  renderAutomationJobs();
}

async function loadProjectPlan() {
  const plan = await api("/api/project-plan");
  state.projectPlan = plan;
  renderProjectPlan(plan);
}

function renderReadiness(data) {
  const badge = $("readiness-badge");
  const checks = $("readiness-checks");
  const actions = $("readiness-actions");
  if (!badge || !checks || !actions) return;
  badge.textContent = data.summary || data.status || "未检查";
  badge.className = `status ${data.ready ? "ready-to-list" : "needs-work"}`;
  checks.innerHTML = "";
  for (const check of data.checks || []) {
    const item = document.createElement("div");
    item.className = `deployment-check ${check.ok ? "ok" : check.severity || "warning"}`;
    item.innerHTML = `
      <strong>${escapeHtml(check.label || check.code)}</strong>
      <span>${escapeHtml(check.message || "")}</span>
    `;
    checks.appendChild(item);
  }
  actions.innerHTML = (data.next_actions || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("");
}

function renderMabangStoreOptions() {
  const select = $("store-template-store");
  if (!select) return;
  // 账号隔离：编辑器的店铺选择器只列当前账号的店铺。
  const options = storeTemplateScopedStoreOptions();
  const current = select.value;
  select.innerHTML = `<option value="">选择马帮店铺</option>`;
  for (const option of options) {
    const item = document.createElement("option");
    item.value = option.mabang_store_id || "";
    item.dataset.storeName = option.store_name || "";
    item.dataset.platform = option.platform || "shopee";
    item.dataset.country = option.country || "";
    item.textContent = option.store_name ? `${option.store_name} (${option.mabang_store_id || ""})` : option.mabang_store_id || "";
    select.appendChild(item);
  }
  if (current && [...select.options].some((option) => option.value === current)) {
    select.value = current;
  }
  const cnscSelect = $("store-template-cnsc-store");
  if (cnscSelect) {
    const cnscCurrent = cnscSelect.value || current;
    cnscSelect.innerHTML = `<option value="">选择 CNSC 店铺</option>`;
    for (const option of options) {
      const item = document.createElement("option");
      item.value = option.mabang_store_id || "";
      item.dataset.storeName = option.store_name || "";
      item.dataset.country = option.country || "";
      item.textContent = option.store_name ? `${option.store_name} (${option.mabang_store_id || ""})` : option.mabang_store_id || "";
      cnscSelect.appendChild(item);
    }
    if (cnscCurrent && [...cnscSelect.options].some((option) => option.value === cnscCurrent)) cnscSelect.value = cnscCurrent;
  }
  const targetSelect = $("store-template-target-sites");
  if (targetSelect) {
    const selectedIds = new Set([...targetSelect.selectedOptions].map((option) => option.value));
    targetSelect.innerHTML = "";
    for (const option of options) {
      const siteCode = String(option.country || "").trim().toUpperCase();
      if (!siteCode || !option.mabang_store_id || siteCode === "GLOBAL" || siteCode === "SIP") continue;
      const item = document.createElement("option");
      item.value = option.mabang_store_id;
      item.dataset.siteCode = siteCode;
      item.dataset.storeName = option.store_name || "";
      item.textContent = `${siteCode} · ${option.store_name || option.mabang_store_id}`;
      item.selected = selectedIds.has(item.value);
      targetSelect.appendChild(item);
    }
    renderCnscTargetStoreConfigs();
  }
  const saveAsSelect = $("save-as-store-template-store");
  if (saveAsSelect) {
    saveAsSelect.innerHTML = '<option value="">选择目标店铺</option>' + options.map((option) =>
      `<option value="${escapeHtml(option.mabang_store_id || "")}">${escapeHtml(option.store_name || option.mabang_store_id || "")}</option>`
    ).join("");
  }
}

function storeTemplateSizeChartOptions(extraValue = "") {
  const options = new Map();

  const addOption = (value, label) => {
    const normalized = String(value || "").trim();
    if (!normalized || options.has(normalized)) return;
    options.set(normalized, label || normalized);
  };

  for (const asset of state.assets) {
    if ((asset.asset_type || "") !== "size_chart") continue;
    const value = asset.file_path || asset.file_name || "";
    const label = [asset.group_id, asset.file_name || value].filter(Boolean).join(" / ");
    addOption(value, label);
  }
  for (const profile of state.categoryProfiles) {
    addOption(profile.default_size_chart_path, `${profile.display_name || profile.product_line || profile.profile_id || "Category"} / ${profile.default_size_chart_path || ""}`);
  }
  for (const template of state.storeTemplates) {
    addOption(template.default_size_chart_path, `${template.store_name || template.template_name || "Template"} / ${template.default_size_chart_path || ""}`);
  }
  addOption(extraValue, extraValue);
  return options;
}

function renderStoreTemplateSizeChartOptions(preferredValue = "") {
  const select = $("store-template-size-chart-path");
  if (!select) return;
  const current = preferredValue || select.value;
  const options = storeTemplateSizeChartOptions(current);
  select.innerHTML = `<option value="">未选择模板尺码图</option>`;
  for (const [value, label] of options) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    select.appendChild(option);
  }
  if ([...select.options].some((option) => option.value === current)) {
    select.value = current;
  }
}

function categoryProfile(profileId) {
  // 严格按 id 匹配：找不到就是找不到——兜底取第一个方案会把没绑定/绑定失效
  // 的模板悄悄挂到别的规则上，保存时才在服务端炸出英文 conflict。
  return state.categoryProfiles.find((item) => (item.profile_id || item.id) === profileId || item.id === profileId) || null;
}

function categoryProfileForMarketplaceCategory(categoryId, scope = marketplaceCategoryScope()) {
  const normalized = String(categoryId || "").trim();
  if (!normalized) return null;
  return state.categoryProfiles.find((item) => (
    String(item.mabang_category_id || item.marketplace_category_id || "").trim() === normalized
    && String(item.platform || "shopee").toLowerCase() === String(scope.platform || "").toLowerCase()
    && String(item.country || "").toUpperCase() === String(scope.country || "").toUpperCase()
    && (item.active_version_id || item.published_version_id)
  )) || null;
}

function renderCategoryProfileOptions() {
  const selects = [$("base-product-category"), $("store-template-category-profile")].filter(Boolean);
  for (const select of selects) {
    const current = select.value;
    select.innerHTML = "";
    if (select.id === "store-template-category-profile") {
      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = "未绑定必填规则";
      select.appendChild(empty);
    }
    for (const profile of state.categoryProfiles) {
      if (select.id === "store-template-category-profile" && !(profile.active_version_id || profile.published_version_id)) {
        // 没有已发布版本的历史方案（如 POD菲律宾 种子方案）永远绑不上模板：
        // 选了必然保存报 requires an active category profile，不放进模板编辑器下拉。
        continue;
      }
      const option = document.createElement("option");
      option.value = profile.profile_id || profile.id || profile.product_line || "";
      option.textContent = profile.display_name || profile.product_line || profile.profile_id || "";
      select.appendChild(option);
    }
    if (current && [...select.options].some((option) => option.value === current)) {
      select.value = current;
    } else if (select.id === "base-product-category" && [...select.options].some((option) => option.value === "pod-ph-mens-tee")) {
      select.value = "pod-ph-mens-tee";
    }
  }
  applyStoreTemplateCategoryProfile();
}

function applyStoreTemplateCategoryProfile() {
  const profile = categoryProfile($("store-template-category-profile")?.value || "");
  if (!profile) return;
  if ($("store-template-line")) $("store-template-line").value = profile.product_line || "";
  if ($("store-template-category-path")) $("store-template-category-path").value = profile.marketplace_category_path || "";
  if ($("store-template-category-id")) $("store-template-category-id").value = profile.mabang_category_id || "";
  if ($("store-template-category-search")) $("store-template-category-search").value = profile.marketplace_category_path || "";
  state.selectedMarketplaceCategory = {
    platform: profile.platform || "shopee",
    country: profile.country || "",
    store_id: $("store-template-store")?.value || "",
    category_id: profile.mabang_category_id || "",
    path: profile.marketplace_category_path || "",
    name: profile.display_name || profile.product_line || "",
  };
  const collectButton = $("collect-selected-category-profile-btn");
  if (collectButton) collectButton.hidden = true;
  if ($("store-template-discount-id")) $("store-template-discount-id").value = profile.mabang_discount_id || "";
  if ($("store-template-normal-price")) $("store-template-normal-price").value = profile.normal_price ?? "";
  if ($("store-template-sale-price")) {
    $("store-template-sale-price").value = calculateFortyPercentDiscount(profile.normal_price ?? "");
    $("store-template-sale-price").dataset.manual = "";
  }
  if ($("store-template-discount")) $("store-template-discount").value = profile.discount_label || "";
  if ($("store-template-sizes")) $("store-template-sizes").value = (profile.sizes || []).join(",");
  renderStoreTemplateSizeChartOptions(profile.default_size_chart_path || "");
  if ($("store-template-title-suffix")) $("store-template-title-suffix").value = profile.title_suffix || "";
  const attrs = profile.required_attributes || profile.category_attributes || {};
  const version = profile.published_version || {};
  const fields = (version.fields || []).length ? version.fields : legacyCategoryFields(attrs);
  renderStoreTemplateCategoryFields(fields, profile.attribute_values || attrs);
  const summary = $("store-template-category-summary");
  if (summary) {
    summary.textContent = [
      `Brand: ${attrs.brand || ""}`,
      `Pattern: ${attrs.pattern || ""}`,
      `Material: ${attrs.material || ""}`,
      `Sleeve: ${attrs.sleeve_length || ""}`,
      `Plus: ${attrs.plus_size || ""}`,
      `Version: ${version.version || "legacy"} | Variation: ${profile.variation?.type || "variant"} (${(profile.variation?.axes || []).join(" + ")})`,
    ].join(" | ");
  }
  loadCategoryProfileVersions(profile.profile_id || profile.id || "");
}

function marketplaceCategoryScope() {
  const storeSelect = $("store-template-store");
  const selectedStore = storeSelect?.selectedOptions?.[0];
  return {
    platform: selectedStore?.dataset.platform || "shopee",
    country: selectedStore?.dataset.country || "",
    storeId: storeSelect?.value || "",
  };
}

function invalidateMarketplaceCategoryRequests() {
  state.marketplaceCategoryRequestId += 1;
}

function disableMarketplaceCategoryResults() {
  const results = $("store-template-category-results");
  if (!results) return;
  results.setAttribute("aria-busy", "true");
  for (const option of results.querySelectorAll("button")) option.disabled = true;
}

function clearMarketplaceCategorySelection({ clearSearch = false } = {}) {
  state.selectedMarketplaceCategory = null;
  state.marketplaceCategories = [];
  state.marketplaceCategoryPath = [];
  state.marketplaceCategoryMode = "browse";
  invalidateMarketplaceCategoryRequests();
  if (clearSearch && $("store-template-category-search")) $("store-template-category-search").value = "";
  if ($("store-template-category-id")) $("store-template-category-id").value = "";
  if ($("store-template-category-path")) $("store-template-category-path").value = "";
  if ($("store-template-category-profile")) $("store-template-category-profile").value = "";
  if ($("store-template-category-results")) $("store-template-category-results").hidden = true;
  if ($("collect-selected-category-profile-btn")) $("collect-selected-category-profile-btn").hidden = true;
  state.categoryProfileVersions = [];
  renderCategoryProfileVersions();
  renderStoreTemplateCategoryFields([], {});
  if ($("store-template-category-summary")) $("store-template-category-summary").textContent = "";
  renderMarketplaceCategoryBreadcrumbs();
}

function marketplaceCategoryScopeReady(scope, results, status) {
  if (!scope.storeId) {
    state.marketplaceCategoryRequestId += 1;
    state.marketplaceCategories = [];
    if (results) results.hidden = true;
    if (status) status.textContent = "请先选择马帮店铺";
    return false;
  }
  if (!scope.country) {
    state.marketplaceCategoryRequestId += 1;
    state.marketplaceCategories = [];
    if (results) results.hidden = true;
    if (status) status.textContent = "所选店铺缺少国家/站点";
    return false;
  }
  return true;
}

async function loadMarketplaceCategoryLevel(parentId = "0", nextPath = state.marketplaceCategoryPath) {
  const results = $("store-template-category-results");
  const status = $("store-template-category-picker-status");
  const scope = marketplaceCategoryScope();
  if (!marketplaceCategoryScopeReady(scope, results, status)) return;
  const requestId = ++state.marketplaceCategoryRequestId;
  disableMarketplaceCategoryResults();
  const params = new URLSearchParams({
    platform: scope.platform,
    country: scope.country,
    store_id: scope.storeId,
    parent_id: String(parentId || "0"),
    leaf_only: "false",
    limit: "500",
  });
  let data;
  try {
    data = await api(`/api/marketplace-categories?${params.toString()}`);
  } catch (err) {
    if (requestId === state.marketplaceCategoryRequestId) results?.removeAttribute("aria-busy");
    throw err;
  }
  if (requestId !== state.marketplaceCategoryRequestId) return;
  state.marketplaceCategoryPath = [...nextPath];
  state.marketplaceCategories = data.items || [];
  state.marketplaceCategoryMode = "browse";
  renderMarketplaceCategoryResults();
  renderMarketplaceCategoryBreadcrumbs();
}

async function loadMarketplaceCategories(query = "") {
  const normalizedQuery = query.trim();
  if (!normalizedQuery) {
    const parent = state.marketplaceCategoryPath.at(-1);
    return loadMarketplaceCategoryLevel(parent?.category_id || "0");
  }
  const results = $("store-template-category-results");
  const status = $("store-template-category-picker-status");
  const scope = marketplaceCategoryScope();
  if (!marketplaceCategoryScopeReady(scope, results, status)) return;
  const requestId = ++state.marketplaceCategoryRequestId;
  disableMarketplaceCategoryResults();
  const params = new URLSearchParams({
    platform: scope.platform,
    country: scope.country,
    store_id: scope.storeId,
    q: normalizedQuery,
    leaf_only: "true",
    limit: "100",
  });
  let data;
  try {
    data = await api(`/api/marketplace-categories?${params.toString()}`);
  } catch (err) {
    if (requestId === state.marketplaceCategoryRequestId) results?.removeAttribute("aria-busy");
    throw err;
  }
  if (requestId !== state.marketplaceCategoryRequestId) return;
  state.marketplaceCategories = data.items || [];
  state.marketplaceCategoryMode = "search";
  renderMarketplaceCategoryResults();
}

function renderMarketplaceCategoryResults() {
  const results = $("store-template-category-results");
  const status = $("store-template-category-picker-status");
  const input = $("store-template-category-search");
  const emptyState = $("store-template-category-empty");
  if (!results || !input) return;
  results.removeAttribute("aria-busy");
  results.innerHTML = "";
  if (!state.marketplaceCategories.length) {
    const isSearch = state.marketplaceCategoryMode === "search";
    // In browse mode: an empty tree means the site's category catalog wasn't collected yet.
    // Show the guided empty state pointing to the mabang-page import flow.
    // In search mode: just say "no matches" and keep the tree visible.
    if (!isSearch && emptyState) {
      emptyState.hidden = false;
      results.hidden = true;
    } else {
      if (emptyState) emptyState.hidden = true;
      results.hidden = false;
      results.innerHTML = `<div class="empty-state">${escapeHtml(isSearch ? "没有匹配的类目" : "此店铺尚无已同步的可选类目")}</div>`;
    }
    input.setAttribute("aria-expanded", "true");
    if (status) status.textContent = isSearch ? "未找到匹配类目" : "类目目录未采集";
    return;
  }
  if (emptyState) emptyState.hidden = true;
  for (const category of state.marketplaceCategories) {
    const option = document.createElement("button");
    option.type = "button";
    option.className = `marketplace-category-option${category.has_children ? " has-children" : ""}`;
    option.dataset.marketplaceCategoryId = category.category_id || "";
    if (category.has_children) option.dataset.marketplaceCategoryParentId = category.category_id || "";
    option.setAttribute("role", "option");
    const localizedLabel = state.marketplaceCategoryMode === "browse"
      ? category.name_zh || category.name || category.name_en || ""
      : category.path_zh || category.path || category.name || "";
    const secondaryLabel = state.marketplaceCategoryMode === "browse"
      ? category.name_en || category.name || ""
      : category.path_en || "";
    const secondary = secondaryLabel && secondaryLabel !== localizedLabel ? `<small>${escapeHtml(secondaryLabel)}</small>` : "";
    const branch = category.has_children ? '<span class="marketplace-category-branch" aria-hidden="true">›</span>' : "";
    option.innerHTML = `<span>${escapeHtml(localizedLabel)}${secondary}</span>${branch}`;
    results.appendChild(option);
  }
  results.hidden = false;
  input.setAttribute("aria-expanded", "true");
  if (status) status.textContent = state.marketplaceCategoryMode === "browse"
    ? `当前层级 ${state.marketplaceCategories.length} 个类目`
    : `找到 ${state.marketplaceCategories.length} 个叶子类目`;
}

function renderMarketplaceCategoryBreadcrumbs() {
  const breadcrumbs = $("store-template-category-breadcrumbs");
  if (!breadcrumbs) return;
  breadcrumbs.innerHTML = "";
  const nodes = [{ label: "全部类目", index: -1 }, ...state.marketplaceCategoryPath.map((category, index) => ({
    label: category.name_zh || category.name || category.name_en || category.category_id || "",
    title: category.name_en || category.path_en || "",
    index,
  }))];
  nodes.forEach((node, position) => {
    if (position) {
      const separator = document.createElement("span");
      separator.className = "marketplace-category-breadcrumb-separator";
      separator.textContent = "/";
      separator.setAttribute("aria-hidden", "true");
      breadcrumbs.appendChild(separator);
    }
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.marketplaceCategoryCrumbIndex = String(node.index);
    button.textContent = node.label;
    if (node.title && node.title !== node.label) button.title = node.title;
    if (position === nodes.length - 1) button.setAttribute("aria-current", "page");
    breadcrumbs.appendChild(button);
  });
}

function openMarketplaceCategoryNode(categoryId) {
  const category = state.marketplaceCategories.find((item) => String(item.category_id || "") === String(categoryId || ""));
  if (!category) return;
  if (!category.has_children) {
    selectMarketplaceCategory(categoryId);
    return;
  }
  const nextPath = [...state.marketplaceCategoryPath, category];
  loadMarketplaceCategoryLevel(category.category_id, nextPath).catch((err) => {
    $("store-template-category-picker-status").textContent = err.message;
  });
}

function navigateMarketplaceCategoryPath(index) {
  const normalizedIndex = Number(index);
  const nextPath = normalizedIndex < 0
    ? []
    : state.marketplaceCategoryPath.slice(0, normalizedIndex + 1);
  const parent = nextPath.at(-1);
  loadMarketplaceCategoryLevel(parent?.category_id || "0", nextPath).catch((err) => {
    $("store-template-category-picker-status").textContent = err.message;
  });
}

function selectMarketplaceCategory(categoryId) {
  const category = state.marketplaceCategories.find((item) => String(item.category_id || "") === String(categoryId || ""));
  if (!category) return;
  state.selectedMarketplaceCategory = category;
  $("store-template-category-search").value = category.path || category.name || "";
  $("store-template-category-path").value = category.path || category.name || "";
  $("store-template-category-id").value = category.category_id || "";
  const results = $("store-template-category-results");
  if (results) results.hidden = true;
  $("store-template-category-search").setAttribute("aria-expanded", "false");

  const profile = categoryProfileForMarketplaceCategory(category.category_id, marketplaceCategoryScope());
  const profileSelect = $("store-template-category-profile");
  const collectButton = $("collect-selected-category-profile-btn");
  const status = $("store-template-category-picker-status");
  if (profile) {
    profileSelect.value = profile.profile_id || profile.id || "";
    applyStoreTemplateCategoryProfile();
    state.selectedMarketplaceCategory = category;
    $("store-template-category-search").value = category.path || category.name || "";
    $("store-template-category-path").value = category.path || category.name || "";
    $("store-template-category-id").value = category.category_id || "";
    if (status) status.textContent = `已绑定规则版本 ${profile.active_version?.version || profile.published_version?.version || profile.active_version_id || profile.published_version_id || ""}`;
    if (collectButton) collectButton.hidden = true;
    $("store-template-category-dialog")?.close();
    return;
  }

  profileSelect.value = "";
  state.categoryProfileVersions = [];
  renderCategoryProfileVersions();
  renderStoreTemplateCategoryFields([], {});
  $("store-template-category-summary").textContent = "当前类目尚无已发布必填项规则";
  if (status) status.textContent = `已选择 ${category.path || category.name || ""}，等待采集必填项`;
  if (collectButton) collectButton.hidden = false;
}

async function createSelectedCategoryProfileCollectionRun() {
  const category = state.selectedMarketplaceCategory;
  const status = $("store-template-category-picker-status");
  const scope = marketplaceCategoryScope();
  if (!category?.category_id || !scope.storeId) {
    if (status) status.textContent = "请先选择店铺和平台类目";
    return;
  }
  const run = await api("/api/category-profiles/collection-runs", {
    method: "POST",
    body: JSON.stringify({
      platform: scope.platform,
      country: category.country || scope.country,
      store_id: scope.storeId,
      category_id: category.category_id,
      category_path: category.path || "",
      display_name: category.name || "",
    }),
  });
  if (status) status.textContent = `必填项采集任务已创建：${run.id}`;
}

// ===== 基础商品「产品类目」：三列级联（跟马帮一样逐级选择）=====

// 第一列默认只展示这几个常用大类（按用户点名保留），其余通过「显示全部」展开。
const BASE_CASCADER_PRIMARY_NAMES = ["男装", "女装", "婴儿和儿童时尚"];

function basePrimaryCategoryNames(node) {
  const zh = node.name_zh || node.name || "";
  return BASE_CASCADER_PRIMARY_NAMES.includes(zh);
}

const BASE_CATEGORY_DATA_URL = "/data/marketplace-categories.json";

async function ensureBaseCategoryIndex() {
  // Load the once-exported Mabang category tree as a local static file and build
  // an in-memory index. Cascade browsing + search then run entirely locally — no
  // per-click API calls, no 404s when the category endpoint is unavailable.
  if (state.baseCategoryIndex) return state.baseCategoryIndex;
  const res = await fetch(BASE_CATEGORY_DATA_URL);
  if (!res.ok) throw new Error("类目数据加载失败");
  const payload = await res.json();
  const nodes = Array.isArray(payload.nodes) ? payload.nodes : [];
  const byId = new Map();
  const childrenOf = new Map();
  for (const raw of nodes) {
    const node = {
      category_id: String(raw.id || ""),
      parent_id: raw.pid === "0" ? "" : String(raw.pid || ""),
      name_zh: raw.zh || "",
      name_en: raw.en || "",
      path_zh: raw.pzh || "",
      path_en: raw.pen || "",
      is_leaf: Boolean(raw.leaf),
    };
    node.has_children = false;
    node.name = node.name_zh || node.name_en || "";
    byId.set(node.category_id, node);
    const pid = node.parent_id || "";
    if (!childrenOf.has(pid)) childrenOf.set(pid, []);
    childrenOf.get(pid).push(node);
  }
  for (const node of byId.values()) {
    node.has_children = (childrenOf.get(node.category_id) || []).length > 0;
    if (!node.is_leaf && !node.has_children) node.is_leaf = true;
  }
  const index = { byId, childrenOf, nodes: [...byId.values()] };
  state.baseCategoryIndex = index;
  return index;
}

async function openBaseCategoryDialog() {
  const dialog = $("base-category-dialog");
  if (!dialog) return;
  dialog.showModal();
  resetBaseCascader();
  try {
    await ensureBaseCategoryIndex();
    renderCascaderColumn(0, "");
  } catch (error) {
    operationError(error, "类目加载失败");
  }
}

function resetBaseCascader() {
  state.baseCascaderPath = [];
  state.baseCascaderSearchMode = false;
  state.baseCategoryShowAll = false;
  const input = $("base-category-search");
  if (input) input.value = "";
  const emptyState = $("base-category-empty");
  if (emptyState) emptyState.hidden = true;
  renderCascaderBreadcrumbs();
  setCascaderColumnHead(1, "选择下级");
  setCascaderColumnHead(2, "选择叶子类目");
}

function childrenOfBase(parentId) {
  const index = state.baseCategoryIndex;
  if (!index) return [];
  const pid = String(parentId || "");
  const kids = index.childrenOf.get(pid === "0" ? "" : pid) || [];
  // Deterministic order: by Chinese name then id.
  return kids.slice().sort((a, b) => {
    const an = (a.name_zh || a.name || "").toLowerCase();
    const bn = (b.name_zh || b.name || "").toLowerCase();
    if (an !== bn) return an < bn ? -1 : 1;
    return a.category_id < b.category_id ? -1 : 1;
  });
}

function renderCascaderColumn(depth, parentId) {
  // depth 0 = root column; 1 = children of path[0]; 2+ = children of the last
  // selected node. Deeper levels keep reusing the third column. Local data only.
  const status = $("base-category-picker-status");
  const emptyState = $("base-category-empty");
  const list = document.querySelector(`[data-cascader-list="${depth}"]`);
  if (!list) return;
  let items = childrenOfBase(parentId);
  // Root column: unless the user asked to show everything (or is searching),
  // surface only the primary categories to keep the first pick short.
  const isRoot = !parentId || parentId === "0" || parentId === "";
  let showAllToggle = false;
  if (isRoot && !state.baseCascaderSearchMode) {
    if (state.baseCategoryShowAll) {
      showAllToggle = true; // offer to collapse back
    } else {
      const primary = items.filter((n) => basePrimaryCategoryNames(n));
      if (primary.length && primary.length < items.length) {
        items = primary;
        showAllToggle = true;
      }
    }
  }
  list.innerHTML = "";
  if (emptyState) emptyState.hidden = true;
  if (!items.length) {
    list.innerHTML = `<p class="category-cascader-empty-note muted">无下级类目</p>`;
    if (status) status.textContent = "";
    return;
  }
  for (const category of items) {
    const option = document.createElement("button");
    option.type = "button";
    option.className = `category-cascader-item${category.is_leaf ? " is-leaf" : ""}${category.has_children ? " has-children" : ""}`;
    option.dataset.cascaderCategoryId = category.category_id || "";
    option.dataset.cascaderIsLeaf = category.is_leaf ? "1" : "";
    option.dataset.cascaderHasChildren = category.has_children ? "1" : "";
    option.dataset.cascaderNameZh = category.name_zh || category.name || "";
    option.dataset.cascaderNameEn = category.name_en || category.name || "";
    option.dataset.cascaderPathZh = category.path_zh || "";
    option.dataset.cascaderPathEn = category.path_en || "";
    const label = category.name_zh || category.name_en || String(category.category_id || "");
    const arrow = category.has_children ? '<span class="category-cascader-arrow" aria-hidden="true">›</span>' : "";
    option.innerHTML = `<span>${escapeHtml(label)}</span>${arrow}`;
    list.appendChild(option);
  }
  if (showAllToggle) {
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "category-cascader-more";
    if (state.baseCategoryShowAll) {
      toggle.dataset.cascaderCollapse = "1";
      toggle.textContent = "收起为常用类目";
    } else {
      toggle.dataset.cascaderShowAll = "1";
      toggle.textContent = `显示全部 ${childrenOfBase("").length} 个类目`;
    }
    list.appendChild(toggle);
  }
  if (status) status.textContent = isRoot && !state.baseCascaderSearchMode && !state.baseCategoryShowAll
    ? `常用 ${items.length} 个类目，可显示全部`
    : `${items.length} 个类目`;
}

function onCascaderItemClick(depth, category) {
  // category: {category_id, is_leaf, has_children, name_zh, ...}
  const list = document.querySelector(`[data-cascader-list="${depth}"]`);
  if (list) {
    [...list.querySelectorAll(".category-cascader-item")].forEach((el) => {
      const same = String(el.dataset.cascaderCategoryId || "") === String(category.category_id || "");
      el.classList.toggle("selected", same);
    });
  }
  if (category.is_leaf || !category.has_children) {
    // Leaf (or node without children downstream) → select it.
    selectBaseCascaderLeaf(category);
    return;
  }
  // Non-leaf: descend. Trim any deeper path entries first.
  while (state.baseCascaderPath.length > depth) state.baseCascaderPath.pop();
  state.baseCascaderPath[depth] = category;
  state.baseCascaderPath.length = depth + 1;
  if (depth === 0) {
    setCascaderColumnHead(1, category.name_zh || category.name || "");
    renderCascaderColumn(1, category.category_id);
    clearCascaderColumn(2);
    setCascaderColumnHead(2, "选择叶子类目");
  } else if (depth === 1) {
    setCascaderColumnHead(2, category.name_zh || category.name || "");
    renderCascaderColumn(2, category.category_id);
  } else {
    // Deeper than 3 columns: reuse the third column for the next level.
    setCascaderColumnHead(2, category.name_zh || category.name || "");
    renderCascaderColumn(2, category.category_id);
  }
  renderCascaderBreadcrumbs();
}

function clearCascaderColumn(depth) {
  const list = document.querySelector(`[data-cascader-list="${depth}"]`);
  if (list) list.innerHTML = "";
}

function setCascaderColumnHead(depth, text) {
  const col = document.querySelector(`[data-cascader-depth="${depth}"]`);
  const head = col && col.querySelector(".category-cascader-col-head");
  if (head) head.textContent = text || "";
}

function renderCascaderBreadcrumbs() {
  const root = $("base-category-breadcrumbs");
  if (!root) return;
  const path = state.baseCascaderPath || [];
  root.hidden = path.length === 0;
  root.innerHTML = "";
  const nodes = [{ label: "全部分类", depth: -1 }, ...path.map((node, i) => ({ label: node.name_zh || node.name || "", depth: i, id: node.category_id }))];
  nodes.forEach((node, position) => {
    if (position) {
      const sep = document.createElement("span");
      sep.className = "category-cascader-breadcrumb-sep";
      sep.textContent = "/";
      root.appendChild(sep);
    }
    const crumb = document.createElement("button");
    crumb.type = "button";
    crumb.className = "category-cascader-breadcrumb";
    crumb.dataset.cascaderCrumbDepth = String(node.depth);
    crumb.textContent = node.label;
    if (position === nodes.length - 1) crumb.setAttribute("aria-current", "page");
    root.appendChild(crumb);
  });
}

function goCascaderCrumb(depth) {
  const path = state.baseCascaderPath || [];
  const targetDepth = Number(depth);
  // depth -1 = all categories root.
  if (targetDepth < 0) {
    state.baseCascaderPath = [];
    clearCascaderColumn(1);
    clearCascaderColumn(2);
    setCascaderColumnHead(1, "选择下级");
    setCascaderColumnHead(2, "选择叶子类目");
    renderCascaderColumn(0, "");
    renderCascaderBreadcrumbs();
    return;
  }
  const target = path[targetDepth];
  if (!target) return;
  // Reset deeper selections.
  state.baseCascaderPath = path.slice(0, targetDepth + 1);
  // Refresh columns to the right of this depth; the column that now shows the
  // target's children is headed by the target's name.
  if (targetDepth < 1) {
    clearCascaderColumn(1);
    setCascaderColumnHead(1, target.name_zh || target.name || "选择下级");
    clearCascaderColumn(2);
    setCascaderColumnHead(2, "选择叶子类目");
  } else if (targetDepth === 1) {
    clearCascaderColumn(2);
    setCascaderColumnHead(2, target.name_zh || target.name || "选择叶子类目");
  }
  renderCascaderColumn(targetDepth + 1, target.category_id);
  renderCascaderBreadcrumbs();
}

function selectBaseCascaderLeaf(category) {
  const path = state.baseCascaderPath || [];
  // Search results carry the node's full path; cascade selections rebuild the
  // path from the ancestor chain. Prefer an explicit full path when present.
  const explicitZh = String(category.path_zh || "").trim();
  const explicitEn = String(category.path_en || "").trim();
  const zhFull = explicitZh && explicitZh.includes(">")
    ? explicitZh
    : [...path.map((n) => n.name_zh || n.name || "").filter(Boolean), category.name_zh || category.name || ""].join(" > ");
  const enFull = explicitEn && explicitEn.includes(">")
    ? explicitEn
    : [...path.map((n) => n.name_en || n.name || "").filter(Boolean), category.name_en || category.name || ""].join(" > ");
  const stored = {
    category_id: category.category_id || "",
    path: enFull || zhFull,
    path_zh: zhFull || enFull,
    name_zh: category.name_zh || category.name || "",
  };
  // Prefer the representative store scope surfaced by the data when present.
  if (category.store_id) stored.store_id = category.store_id;
  if (category.country) stored.country = category.country;
  state.editingBaseProductCategory = stored;
  renderBaseMaintenanceCategoryInput();
  loadBaseCategoryAttributeFields();
  const dialog = $("base-category-dialog");
  if (dialog) dialog.close();
  showOperationFeedback(`已选择产品类目：${stored.path_zh || stored.path}`, "success");
}

async function searchBaseCategories(query) {
  const normalized = String(query || "").trim();
  const status = $("base-category-picker-status");
  const emptyState = $("base-category-empty");
  const list0 = document.querySelector(`[data-cascader-list="0"]`);
  if (!normalized) {
    state.baseCascaderSearchMode = false;
    resetBaseCascader();
    renderCascaderColumn(0, "");
    return;
  }
  await ensureBaseCategoryIndex();
  state.baseCascaderSearchMode = true;
  const needle = normalized.toLowerCase();
  const index = state.baseCategoryIndex;
  const scored = [];
  for (const node of index.nodes) {
    const hay = [node.name_zh, node.name_en, node.path_zh, node.path_en, node.category_id]
      .filter(Boolean).join(" ").toLowerCase();
    if (!hay.includes(needle)) continue;
    let rank = 4;
    if (node.category_id === needle) rank = 0;
    else if ((node.name_zh || "").toLowerCase() === needle || (node.name_en || "").toLowerCase() === needle) rank = 1;
    else if ((node.name_zh || "").toLowerCase().startsWith(needle) || (node.name_en || "").toLowerCase().startsWith(needle)) rank = 2;
    else rank = 3;
    scored.push({ node, rank });
  }
  scored.sort((a, b) => {
    if (a.rank !== b.rank) return a.rank - b.rank;
    const da = (a.node.path_en || "").split(">").length;
    const db = (b.node.path_en || "").split(">").length;
    if (da !== db) return da - db; // shallower (upper) levels first
    return (a.node.path_zh || a.node.name_zh || "").localeCompare(b.node.path_zh || b.node.name_zh || "", "zh");
  });
  const items = scored.slice(0, 100).map((s) => s.node);
  if (list0) list0.innerHTML = "";
  clearCascaderColumn(1);
  clearCascaderColumn(2);
  setCascaderColumnHead(1, "选择下级");
  setCascaderColumnHead(2, "选择叶子类目");
  state.baseCascaderPath = [];
  renderCascaderBreadcrumbs();
  if (emptyState) emptyState.hidden = true;
  if (!items.length) {
    if (emptyState) emptyState.hidden = false;
    if (status) status.textContent = normalized ? "没有匹配的类目" : "";
    return;
  }
  for (const category of items) {
    const option = document.createElement("button");
    option.type = "button";
    option.className = `category-cascader-item search-result${category.is_leaf ? " is-leaf" : ""}${category.has_children ? " has-children" : ""}`;
    option.dataset.cascaderCategoryId = category.category_id || "";
    option.dataset.cascaderIsLeaf = category.is_leaf ? "1" : "";
    option.dataset.cascaderHasChildren = category.has_children ? "1" : "";
    option.dataset.cascaderNameZh = category.name_zh || category.name || "";
    option.dataset.cascaderNameEn = category.name_en || category.name || "";
    option.dataset.cascaderPathZh = category.path_zh || category.path_en || "";
    option.dataset.cascaderPathEn = category.path_en || category.path_zh || "";
    // Show the full path as the search result label so users can tell which branch it sits in.
    const label = category.path_zh || category.path_en || category.name_zh || category.name_en || String(category.category_id || "");
    option.innerHTML = `<span>${escapeHtml(label)}</span>`;
    list0.appendChild(option);
  }
  if (status) status.textContent = `找到 ${items.length} 个匹配类目`;
}

// Click handler for both cascader columns and search results.
function onBaseCategoryItemActivated(target) {
  const item = target.closest("[data-cascader-category-id]");
  if (!(item instanceof HTMLElement)) return;
  const category = {
    category_id: item.dataset.cascaderCategoryId || "",
    is_leaf: item.dataset.cascaderIsLeaf === "1",
    has_children: item.dataset.cascaderHasChildren === "1",
    name_zh: item.dataset.cascaderNameZh || "",
    name: item.dataset.cascaderNameZh || "",
    name_en: item.dataset.cascaderNameEn || item.dataset.cascaderNameZh || "",
    path_zh: item.dataset.cascaderPathZh || "",
    path_en: item.dataset.cascaderPathEn || item.dataset.cascaderPathZh || "",
    store_id: item.dataset.cascaderStoreId || "",
    country: item.dataset.cascaderCountry || "",
  };
  const depth = Number(item.closest("[data-cascader-list]")?.dataset.cascaderList ?? 0);
  if (state.baseCascaderSearchMode) {
    // From search: leaf selects; a branch node becomes the new cascade root so
    // the user can keep drilling into its children.
    if (category.is_leaf || !category.has_children) {
      selectBaseCascaderLeaf(category);
    } else {
      state.baseCascaderSearchMode = false;
      const input = $("base-category-search");
      if (input) input.value = "";
      state.baseCascaderPath = [category];
      awaitCascadeFromSearched(category);
    }
    return;
  }
  onCascaderItemClick(depth, category);
}

function awaitCascadeFromSearched(category) {
  // A matched branch becomes the effective root: col0 shows it alone (or its
  // children as the new first level), col1 loads its direct children.
  const list0 = document.querySelector(`[data-cascader-list="0"]`);
  if (list0) {
    list0.innerHTML = "";
    const root = document.createElement("div");
    root.className = "category-cascader-search-root";
    root.textContent = (category.name_zh || category.name || "") + " ›";
    list0.appendChild(root);
  }
  setCascaderColumnHead(1, category.name_zh || category.name || "");
  clearCascaderColumn(2);
  setCascaderColumnHead(2, "选择叶子类目");
  renderCascaderBreadcrumbs();
  renderCascaderColumn(1, category.category_id);
}

function clearBaseProductCategory() {
  state.editingBaseProductCategory = null;
  state.editingBaseProductAttributeValues = {};
  renderBaseMaintenanceCategoryInput();
  loadBaseCategoryAttributeFields();
  showOperationFeedback("已清空产品类目（保存后生效）", "success");
}

// ===== 快速类目模板（存类目 + 类目属性值，供基础商品一键复用；不含价格/尺码） =====

async function loadBaseCategoryQuickTemplates() {
  const status = $("base-category-template-status");
  try {
    // 弹窗打开瞬间请求洪峰可能被系统直接重置连接（API burst 问题）：
    // 失败先重试一次，避免下拉一直是空占位、看起来像卡住。
    let data;
    try {
      data = await api("/api/category-quick-templates");
    } catch (first) {
      data = await api("/api/category-quick-templates").catch(() => { throw first; });
    }
    state.baseCategoryQuickTemplates = data.items || [];
    state.baseCategoryQuickTemplateLoaded = true;
    renderBaseCategoryQuickTemplateOptions();
    // 老模板只存了类目、没有属性值：提示操作员用同名「存为模板」补存，
    // 否则套用时属性还是空的，看起来像没修好。
    const missing = (state.baseCategoryQuickTemplates || []).filter(
      (template) => !Object.keys(template.attribute_values || {}).length,
    ).length;
    if (status) {
      status.textContent = missing
        ? `${missing} 个模板还没有属性值：选好商品属性后，用同名「存为模板」即可补上。`
        : "";
    }
  } catch (error) {
    state.baseCategoryQuickTemplateLoaded = true;
    if (status) status.textContent = `快速类目模板加载失败：${error.message || error}`;
  }
}

function renderBaseCategoryQuickTemplateOptions() {
  const select = $("base-category-template-select");
  if (!select) return;
  const previous = select.value;
  const templates = state.baseCategoryQuickTemplates || [];
  select.innerHTML = `<option value="">（选快速类目模板…）</option>` +
    templates.map((template) => {
      const path = (template.category && (template.category.path_zh || template.category.path)) || "";
      return `<option value="${escapeHtml(template.id)}">${escapeHtml(template.name || "")}${path ? `（${escapeHtml(path)}）` : ""}</option>`;
    }).join("");
  if (previous && [...select.options].some((option) => option.value === previous)) select.value = previous;
}

async function applyBaseCategoryQuickTemplate(templateId) {
  const resetSelect = () => {
    const select = $("base-category-template-select");
    if (select) select.value = "";
  };
  let template = (state.baseCategoryQuickTemplates || []).find((item) => String(item.id || "") === String(templateId || ""));
  // 列表还没加载好（弹窗刚开就选）或刚被删掉时：先补拉一次再找。找不到必须给出
  // 反馈并复位下拉——静默返回的话页面毫无反应，操作员会以为卡死了。
  if (!template) {
    await loadBaseCategoryQuickTemplates().catch(() => {});
    template = (state.baseCategoryQuickTemplates || []).find((item) => String(item.id || "") === String(templateId || ""));
  }
  if (!template || !template.category) {
    showOperationFeedback("快速类目模板还没加载好或已被删除，请稍后重选", "error");
    resetSelect();
    return;
  }
  state.editingBaseProductCategory = { ...template.category };
  // 模板里的类目属性值一并回填；老模板没有 attribute_values 时保留商品自己的值，
  // 不做「套模板先把已填值清空」的破坏性动作。
  const templateValues = template.attribute_values && typeof template.attribute_values === "object"
    ? template.attribute_values
    : null;
  const valueCount = templateValues ? Object.keys(templateValues).length : 0;
  if (valueCount) state.editingBaseProductAttributeValues = { ...templateValues };
  renderBaseMaintenanceCategoryInput();
  await loadBaseCategoryAttributeFields().catch(() => {});
  showOperationFeedback(
    valueCount
      ? `已套用快速类目模板「${template.name || ""}」（含 ${valueCount} 项属性值），保存后生效`
      : `已套用快速类目模板「${template.name || ""}」；该模板还没有属性值——选好属性后点「存为模板」用同名补存，以后套用就会带上选项`,
    "success",
  );
}

async function saveCurrentBaseCategoryAsQuickTemplate() {
  const category = baseProductCategorySelection();
  if (!category) {
    showOperationFeedback("请先选择产品类目，再存为快速类目模板", "error");
    return;
  }
  // 已选中/刚套用过的模板作为默认名：确认即按同名补存（老模板可借此补上属性值）。
  const selectedTemplateId = $("base-category-template-select")?.value || "";
  const selectedTemplate = (state.baseCategoryQuickTemplates || []).find(
    (item) => String(item.id || "") === String(selectedTemplateId),
  );
  const name = String(window.prompt("快速类目模板名称（例如：男装 T恤）：", selectedTemplate?.name || "") || "").trim();
  if (!name) return;
  // 「存为模板」= 保存当前类目 + 当前表单里已填的类目属性值（价格/尺码不存）。
  // 属性字段还没渲染出来（规则加载慢/该类目无已发布规则）时，采集结果恒为空，
  // 会存出「只有类目没有选项」的模板——改用商品已存的属性值兜底。
  let attributeValues = collectBaseCategoryAttributeValues();
  const categoryFieldControls = document.querySelectorAll("#base-maintenance-category-fields [data-category-field-input]");
  if (!categoryFieldControls.length) {
    attributeValues = { ...(state.editingBaseProductAttributeValues || {}) };
  }
  if (!Object.keys(attributeValues).length) {
    const proceed = window.confirm("当前没有已填的类目属性值：这个模板将只有类目、没有属性选项（套用时选项不会被选上）。确定这样保存吗？");
    if (!proceed) return;
  }
  try {
    await api("/api/category-quick-templates", {
      method: "POST",
      body: JSON.stringify({ name, category, attribute_values: attributeValues }),
    });
    await loadBaseCategoryQuickTemplates();
    const select = $("base-category-template-select");
    const saved = (state.baseCategoryQuickTemplates || []).find((item) => String(item.name || "").trim().toLowerCase() === name.toLowerCase());
    if (select && saved) select.value = saved.id;
    const valueCount = Object.keys(attributeValues || {}).length;
    showOperationFeedback(
      valueCount ? `已保存快速类目模板「${name}」（含 ${valueCount} 项属性值）` : `已保存快速类目模板「${name}」`,
      "success",
    );
  } catch (error) {
    showOperationFeedback(error.message || "快速类目模板保存失败", "error");
  }
}

async function deleteBaseCategoryQuickTemplate(templateId) {
  if (!templateId) return;
  const template = (state.baseCategoryQuickTemplates || []).find((item) => String(item.id || "") === String(templateId || ""));
  if (!window.confirm(`确认删除快速类目模板「${template?.name || ""}」？`)) return;
  try {
    await api(`/api/category-quick-templates/${encodeURIComponent(templateId)}`, { method: "DELETE" });
    await loadBaseCategoryQuickTemplates();
    showOperationFeedback("已删除快速类目模板", "success");
  } catch (error) {
    showOperationFeedback(error.message || "删除快速类目模板失败", "error");
  }
}

function renderBaseProducts() {
  const target = $("base-product-cards");
  if (!target) return;
  target.innerHTML = "";
  renderBaseProductBatchBar();
  if (!state.baseProducts.length) {
    target.innerHTML = `<div class="empty-state">暂无基础商品。点击“从 NAS 创建”选择一个商品文件夹。</div>`;
    renderBaseProductFilterBar();
    return;
  }
  renderBaseProductFilterBar();
  const visible = state.baseProducts.filter(matchesBaseProductFilters);
  if (!visible.length) {
    // 「全部」不限时间窗，空态就不再说「今天/最近一周没有更新的」。
    const presetLabel = baseProductTimeFilterLabel(state.baseProductTimeFilter);
    const filterLabel = baseProductFilterLabel(state.baseProductCategoryFilter);
    const scoped = presetLabel && presetLabel !== "全部" ? presetLabel : "";
    const categoryText = filterLabel ? `「${escapeHtml(filterLabel)}」` : "";
    target.innerHTML = `<div class="empty-state">${scoped}${categoryText}没有${scoped ? "更新的" : "符合条件的"}基础商品。</div>`;
    return;
  }
  for (const product of visible) {
    const card = document.createElement("article");
    const isChecked = state.selectedBaseProducts.has(product.id);
    const isSingle = state.selectedBaseProductId === product.id;
    card.className = `product-card ${isSingle ? "selected" : ""} ${isChecked ? "checked" : ""}`;
    const assets = assetsForBaseProduct(product);
    const mainAsset = assets.find((asset) => asset.file_name === product.main_image) || assets.find((asset) => asset.file_name === (product.product_images || [])[0]);
    const assetsCleaned = Boolean(String(product.assets_cleaned_at || "").trim());
    const imageSrc = assetsCleaned ? "" : (mainAsset ? assetImageUrl(mainAsset, 480) : "");
    const colors = (product.recognized_colors || []).length;
    const imageCount = (product.product_images || []).length;
    const glyph = product.automation_glyph || "idle";
    const summary = product.automation_status_summary || { done: 0, failed: 0, running: 0, attention: 0, total_copies: 0 };
    const glyphTitleParts = [
      `${AUTOMATION_GLYPH_LABELS[glyph] || glyph}`,
      summary.total_copies ? `${summary.done}/${summary.total_copies} 已保存` : "无刊登副本",
    ];
    if (summary.failed) glyphTitleParts.push(`${summary.failed} 失败`);
    if (summary.attention) glyphTitleParts.push(`${summary.attention} 待人工`);
    if (summary.running) glyphTitleParts.push(`${summary.running} 执行中`);
    const statusLabel = BASE_PRODUCT_STATUS_LABELS[product.status] || product.status || "";
    const stageKey = baseProductStage(product);
    const stageLabel = baseProductStageLabel(stageKey);
    const productCategoryPath = (product.product_category && (product.product_category.path_zh || product.product_category.path)) || product.category || "";
    card.innerHTML = `
      <label class="product-card-check" title="${isChecked ? "取消勾选" : "勾选以批量操作"}"><input type="checkbox" data-base-product-select="${escapeHtml(product.id)}" ${isChecked ? "checked" : ""} /></label>
      <span class="card-glyph card-glyph--${glyph}" title="${escapeHtml(glyphTitleParts.join(" · "))}" aria-label="${escapeHtml(glyphTitleParts.join(" · "))}"></span>
      <div class="product-card-thumb">${imageSrc ? `<img src="${imageSrc}" alt="${escapeHtml(product.product_name_cn || "")}" loading="lazy" decoding="async" />` : (assetsCleaned ? `<span class="product-card-thumb-note">素材已清理<br />需重新导入</span>` : "")}</div>
      <div class="product-card-body">
        <strong>${escapeHtml(product.product_name_cn || product.base_name || product.main_sku || product.group_id || "")}</strong>
        <span>主 SKU：${escapeHtml(product.main_sku || product.group_id || "")}</span>
        <span>颜色：${escapeHtml(colors)} / 图片：${escapeHtml(imageCount)}</span>
        <span class="product-card-category">类目：${escapeHtml(productCategoryPath || "未选择")}</span>
        <span>店铺副本：${escapeHtml(product.listing_copy_count || 0)}</span>
        <span class="stage-badge stage-badge--${stageKey}" title="${escapeHtml(BASE_PRODUCT_STAGE_HINTS[stageKey] || "")}">${escapeHtml(stageLabel)}</span>
      </div>
      <div class="product-card-actions">
        <span class="status ${escapeHtml(product.status || "")}">${escapeHtml(statusLabel)}</span>
        <button data-maintain-base-product="${escapeHtml(product.id)}">维护</button>
        <button type="button" class="danger" data-delete-base-product="${escapeHtml(product.id)}">删除</button>
      </div>
    `;
    card.addEventListener("click", (event) => {
      // 点卡片内按钮或勾选框时不触发单选；勾选框由 change 委托处理多选。
      if (event.target instanceof HTMLElement && event.target.closest("button, .product-card-check, input")) return;
      // 以勾选状态为准的 toggle：已勾选的卡再点 = 取消勾选（若它还是当前单选卡，
      // 单选一并取消）；未勾选的卡点一下 = 勾选并设为单选。
      if (state.selectedBaseProducts.has(product.id)) {
        state.selectedBaseProducts.delete(product.id);
        if (state.selectedBaseProductId === product.id) state.selectedBaseProductId = "";
      } else {
        state.selectedBaseProducts.add(product.id);
        state.selectedBaseProductId = product.id;
      }
      renderBaseProducts();
    });
    target.appendChild(card);
  }
}

function assetsForBaseProduct(product) {
  const group = (product.group_id || "").trim().toUpperCase();
  const candidates = state.assets.filter((asset) => (asset.group_id || "").trim().toUpperCase() === group);
  // 同一个 SKU 文件夹重新导入会留下上一批素材（分组相同、目录不同）。只按分组取素材
  // 会让商品图/颜色图选择器把两次导入的图片混在一起，所以有 asset_root 时只保留
  // 本次导入目录下的素材；操作员自上传的图片（origin=manual_upload）不在素材目录里，
  // 按来源标记保留。一张都匹配不上就退回整组，避免路径写法差异把图片全部藏掉。
  const root = String(product.asset_root || "").trim().replace(/[\\/]+$/, "").replace(/\\/g, "/").toLowerCase();
  if (!root) return candidates;
  const scoped = candidates.filter((asset) => {
    if (String(asset.origin || "") === "manual_upload") return true;
    const path = String(asset.file_path || "").replace(/\\/g, "/").toLowerCase();
    return path === root || path.startsWith(`${root}/`);
  });
  return scoped.length ? scoped : candidates;
}

function _startOfLocalDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

// 「时间」行 chip 的取值 → 文案。空态提示和 chip 共用这份表，不要再写三元表达式。
const BASE_PRODUCT_TIME_FILTER_LABELS = {
  today: "今天",
  yesterday: "昨天",
  week: "最近一周",
  all: "全部", // 不限时间窗：看历史商品 / 跨天核对
};

function baseProductTimeFilterLabel(preset) {
  return BASE_PRODUCT_TIME_FILTER_LABELS[String(preset || "")] || "";
}

function dateFallsInPreset(iso, preset) {
  if (!preset || preset === "all") return true;
  const timestamp = new Date(iso || "").getTime();
  if (Number.isNaN(timestamp)) return false;
  const dayDiff = Math.round((_startOfLocalDay(new Date()) - _startOfLocalDay(new Date(timestamp))) / 86400000);
  if (preset === "today") return dayDiff === 0;
  if (preset === "yesterday") return dayDiff === 1;
  if (preset === "week") return dayDiff >= 0 && dayDiff <= 6;
  return true;
}

function matchesBaseTimeFilter(product) {
  if (!state.baseProductTimeFilter) return true;
  const stamp = product.updated_at || product.created_at || "";
  return dateFallsInPreset(stamp, state.baseProductTimeFilter);
}

function matchesBaseProductFilters(product) {
  if (state.baseProductCategoryFilter && baseProductFilterKey(product) !== state.baseProductCategoryFilter) {
    return false;
  }
  return matchesBaseTimeFilter(product);
}

// 大分类 = 维护弹窗里选的「上架期望类目」路径的第一级（如 女装 / Men Clothes）；
// 没选过期望类目的商品统一归到「未分类」。
function baseProductCategoryKey(product) {
  const expected = product.product_category || {};
  const path = String(expected.path_zh || expected.path || "").trim();
  if (path) return path.split(">")[0].trim();
  return "未分类";
}

// 商品维护「分类」行的四段阶段分类。服务端按「有没有副本 / 编辑过没有 / 副本执行到哪」
// 算好 lifecycle_stage（见 storage._derive_lifecycle_stage），前端只负责显示和筛选；
// 后端进程还没重启、拿不到该字段时按 status + 副本数降级。
const BASE_PRODUCT_STAGE_LABELS = {
  imported: "首次导入",
  uncopied: "未复制",
  pending: "待执行",
  completed: "已完成",
};

const BASE_PRODUCT_STAGE_ORDER = ["imported", "uncopied", "pending", "completed"];

const BASE_PRODUCT_STAGE_HINTS = {
  imported: "刚导入、还没编辑过",
  uncopied: "编辑过：现有副本不含这次改动，要重新复制到店铺模板",
  pending: "已复制到店铺模板，自动化还没执行完",
  completed: "全部店铺副本都已保存完成",
};

function baseProductStage(product) {
  const stage = String(product.lifecycle_stage || "").trim();
  if (BASE_PRODUCT_STAGE_ORDER.includes(stage)) return stage;
  if (String(product.status || "").trim().toLowerCase() === "completed") return "completed";
  if (Number(product.listing_copy_count || 0) > 0) return "pending";
  return String(product.last_edited_at || "").trim() ? "uncopied" : "imported";
}

function baseProductStageKey(stage) {
  return `stage:${stage}`;
}

function baseProductStageLabel(stage) {
  return BASE_PRODUCT_STAGE_LABELS[stage] || "";
}

// 阶段 chip 和类目 chip 共用一行、共用单选的筛选状态，阶段 key 带前缀避免和类目名撞车。
function baseProductFilterKey(product) {
  if (String(state.baseProductCategoryFilter || "").startsWith("stage:")) {
    return baseProductStageKey(baseProductStage(product));
  }
  return baseProductCategoryKey(product);
}

function baseProductFilterLabel(key) {
  const value = String(key || "");
  if (!value) return "";
  if (value.startsWith("stage:")) return baseProductStageLabel(value.slice("stage:".length)) || value;
  return value;
}

function renderBaseProductFilterBar() {
  const timeRow = $("base-product-filter-time");
  if (timeRow) {
    for (const btn of timeRow.querySelectorAll("[data-base-time-filter]")) {
      btn.classList.toggle("chip--active", btn.dataset.baseTimeFilter === state.baseProductTimeFilter);
    }
  }
  const categoryRow = $("base-product-filter-category");
  if (!categoryRow) return;
  // 分类 chip 的计数按当前时间窗内的商品算，保证看到的数量点下去就一致。
  const inTime = state.baseProducts.filter(matchesBaseTimeFilter);
  const stageCounts = new Map(BASE_PRODUCT_STAGE_ORDER.map((stage) => [stage, 0]));
  const counts = new Map();
  for (const product of inTime) {
    const stage = baseProductStage(product);
    stageCounts.set(stage, (stageCounts.get(stage) || 0) + 1);
    const key = baseProductCategoryKey(product);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const entries = [...counts.entries()]
    .sort((a, b) => (b[1] - a[1]) || a[0].localeCompare(b[0], "zh-Hans-CN"))
    .map(([key]) => key);
  const active = String(state.baseProductCategoryFilter || "");
  const chips = [`<button type="button" class="chip${active === "" ? " chip--active" : ""}" data-base-category-filter="">全部<span class="chip-count">${inTime.length}</span></button>`];
  // 四段阶段 chip 紧跟「全部」：它们是操作员每天要清的活儿（未复制 → 待执行 → 已完成），
  // 排在「商品类目」第一级分类前面；两组共用同一个单选筛选状态。
  for (const stage of BASE_PRODUCT_STAGE_ORDER) {
    const key = baseProductStageKey(stage);
    chips.push(`<button type="button" class="chip chip--stage chip--stage-${stage}${active === key ? " chip--active" : ""}" data-base-category-filter="${escapeHtml(key)}" title="${escapeHtml(BASE_PRODUCT_STAGE_HINTS[stage] || "")}">${escapeHtml(baseProductStageLabel(stage))}<span class="chip-count">${stageCounts.get(stage) || 0}</span></button>`);
  }
  chips.push(`<span class="chip-divider" aria-hidden="true"></span>`);
  for (const key of entries) {
    chips.push(`<button type="button" class="chip${active === key ? " chip--active" : ""}" data-base-category-filter="${escapeHtml(key)}">${escapeHtml(key)}<span class="chip-count">${counts.get(key)}</span></button>`);
  }
  categoryRow.innerHTML = chips.join("");
}

function visibleBaseProducts() {
  return state.baseProducts.filter(matchesBaseProductFilters);
}

function renderBaseProductBatchBar() {
  const bar = $("base-product-batch-bar");
  if (!bar) return;
  const countEl = $("base-product-batch-count");
  const count = state.selectedBaseProducts.size;
  if (countEl) countEl.textContent = `已选 ${count} 个商品`;
  bar.hidden = count === 0;
  if (count > 0) void loadBaseRetrySkillOptions();
}

// 切页时清掉商品勾选（多选 + 当前单选卡片）。只改已经渲染出来的勾选框和卡片类名，
// 不整表重画：切页不该为了复位勾选再渲染几百张卡片。
function clearBaseProductSelection() {
  const hadSelection = state.selectedBaseProducts.size > 0;
  const hadFocusedCard = Boolean(state.selectedBaseProductId);
  if (!hadSelection && !hadFocusedCard) return;
  state.selectedBaseProducts.clear();
  state.selectedBaseProductId = "";
  document.querySelectorAll("[data-base-product-select]").forEach((input) => {
    input.checked = false;
    input.closest(".product-card")?.classList.remove("checked", "selected");
  });
  renderBaseProductBatchBar();
}

// 「重新识别标题」旁边的手动规则下拉：只在勾选商品后才需要，规则列表按需拉一次。
async function loadBaseRetrySkillOptions() {
  if (state.baseRetrySkillsLoaded) return;
  state.baseRetrySkillsLoaded = true;
  try {
    const data = await api("/api/title-skills");
    const rules = Array.isArray(data) ? data : data?.items || [];
    state.baseRetrySkills = rules.filter((rule) => rule && rule.enabled !== false);
  } catch (error) {
    state.baseRetrySkillsLoaded = false; // 下次勾选商品时再试
    return;
  }
  renderBaseRetrySkillOptions();
}

function renderBaseRetrySkillOptions() {
  const select = $("base-retry-skill");
  if (!select) return;
  const current = select.value;
  const options = ['<option value="">标题规则：自动匹配（默认）</option>'];
  for (const rule of state.baseRetrySkills) {
    const name = rule.name || rule.id || "";
    options.push(`<option value="${escapeHtml(rule.id || "")}">${escapeHtml(name)}</option>`);
  }
  select.innerHTML = options.join("");
  if (current && state.baseRetrySkills.some((rule) => rule.id === current)) select.value = current;
}

function selectedBaseRetrySkillId() {
  const select = $("base-retry-skill");
  return select ? String(select.value || "") : "";
}

// 批量操作栏的三个 AI 重识别按钮：只重跑指定环节，不复用导入时的开关。
const BASE_PRODUCT_RETRY_ASPECTS = {
  "retry-title": { aspect: "title", label: "标题" },
  "retry-description": { aspect: "description", label: "详情" },
  "retry-colors": { aspect: "colors", label: "颜色图" },
};

// 批量操作栏动作（全选当前列表 / 清除勾选 / 批量删除 / AI 重识别），由全局 click 委托分发。
function handleBaseProductBatchAction(action) {
  const result = $("base-product-result");
  const retry = BASE_PRODUCT_RETRY_ASPECTS[action];
  if (retry) {
    retryCheckedBaseProducts(retry.aspect, retry.label);
    return;
  }
  if (action === "select-all") {
    const visible = visibleBaseProducts();
    if (!visible.length) {
      if (result) result.textContent = "当前筛选下没有可勾选的基础商品。";
      return;
    }
    for (const product of visible) state.selectedBaseProducts.add(product.id);
    renderBaseProducts();
    return;
  }
  if (action === "clear") {
    state.selectedBaseProducts.clear();
    renderBaseProducts();
    return;
  }
  if (action === "delete") {
    deleteCheckedBaseProducts();
    return;
  }
}

function setBaseProductRetryBusy(busy) {
  state.baseProductRetryRunning = busy;
  for (const button of document.querySelectorAll("[data-base-batch-action]")) {
    if (!(button instanceof HTMLButtonElement)) continue;
    if (!BASE_PRODUCT_RETRY_ASPECTS[button.dataset.baseBatchAction || ""]) continue;
    button.disabled = busy;
    button.classList.toggle("chip--busy", busy);
  }
}

// 标题队列：批量「重新识别标题」时才用到。逐条排队、串行执行，生成结果留在行里，
// 全部跑完弹窗列出所有标题（可逐个/一次性复制）。队列只活在当前页面；标题本身已经
// 写进基础商品，刷新丢的只是这份进度清单。
const BASE_TITLE_GLYPHS = { waiting: "⏳", running: "▶", done: "✅", error: "❌" };
let baseTitleQueue = null;
let baseTitleQueueCollapsed = false;

function baseTitleProductLabel(product) {
  return product.product_name_cn || product.base_name || product.main_sku || product.group_id || "未命名商品";
}

function resetBaseTitleQueue(products) {
  baseTitleQueue = {
    items: products.map((product) => ({
      id: product.id,
      sku: product.main_sku || product.group_id || "",
      name: baseTitleProductLabel(product),
      status: "waiting",
      title: "",
      error: "",
    })),
  };
  baseTitleQueueCollapsed = false;
  renderBaseTitleQueue();
  $("base-title-queue")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function baseTitleQueueItem(productId) {
  return (baseTitleQueue?.items || []).find((item) => item.id === productId) || null;
}

function baseTitleQueueDone() {
  return (baseTitleQueue?.items || []).filter((item) => item.status === "done");
}

function renderBaseTitleQueue() {
  const items = baseTitleQueue?.items || [];
  const showBtn = $("base-title-queue-show");
  if (showBtn) {
    showBtn.hidden = !(items.length && baseTitleQueueCollapsed);
    if (!showBtn.hidden) showBtn.textContent = `显示标题队列（${items.length}）`;
  }
  const panel = $("base-title-queue");
  if (!panel) return;
  panel.hidden = !items.length || baseTitleQueueCollapsed;
  if (panel.hidden) return;
  const counts = { waiting: 0, running: 0, done: 0, error: 0 };
  for (const item of items) counts[item.status] = (counts[item.status] || 0) + 1;
  const summary = $("base-title-queue-summary");
  if (summary) {
    const parts = [`进度 ${counts.done + counts.error}/${items.length}`];
    if (counts.running) parts.push(`识别中 ${counts.running}`);
    if (counts.waiting) parts.push(`等待 ${counts.waiting}`);
    if (counts.done) parts.push(`完成 ${counts.done}`);
    if (counts.error) parts.push(`失败 ${counts.error}`);
    summary.textContent = `标题队列：${parts.join(" · ")}`;
  }
  const viewBtn = $("base-title-queue-view");
  if (viewBtn) viewBtn.hidden = !counts.done;
  const list = $("base-title-queue-list");
  if (!list) return;
  list.innerHTML = items.map((item) => {
    const label = item.sku || item.name;
    const detail = item.status === "done"
      ? (item.title || "（AI 返回空标题）")
      : item.status === "error"
        ? (item.error || "识别失败")
        : item.status === "running" ? "识别中…" : "等待中";
    const statusClass = item.status === "done" ? "succeeded"
      : item.status === "error" ? "failed"
        : item.status === "running" ? "running" : "waiting";
    return `<li class="local-import-queue-item local-import-queue-item--${statusClass}">
      <span class="local-import-queue-glyph" aria-hidden="true">${BASE_TITLE_GLYPHS[item.status] || "•"}</span>
      <span class="local-import-queue-name" title="${escapeHtml(label)}">${escapeHtml(label)}</span>
      <span class="local-import-queue-detail${item.status === "done" ? " base-title-queue-title" : ""}">${escapeHtml(detail)}</span>
    </li>`;
  }).join("");
}

function baseTitleResultText() {
  return (baseTitleQueue?.items || [])
    .filter((item) => item.status === "done" && item.title)
    .map((item) => `${item.sku || item.name}\t${item.title}`)
    .join("\n");
}

// 全部跑完的「提醒」：列出这批判识别出来的标题，原本的标题也一并显示，方便核对。
function showBaseTitleResults() {
  const dialog = $("base-title-result-dialog");
  const dialogItems = baseTitleQueue?.items || [];
  if (!dialog || !dialogItems.length) return;
  const done = dialogItems.filter((item) => item.status === "done");
  const failed = dialogItems.filter((item) => item.status === "error");
  const summary = $("base-title-result-summary");
  if (summary) {
    summary.textContent = `本批 ${dialogItems.length} 个商品：成功 ${done.length} 个${failed.length ? `，失败 ${failed.length} 个` : ""}。标题已写入基础商品，可复制后核对。`;
  }
  const tbody = $("base-title-result-rows");
  if (tbody) {
    tbody.innerHTML = (done.length ? done : dialogItems).map((item) => {
      const cell = item.status === "done"
        ? escapeHtml(item.title || "（AI 返回空标题）")
        : escapeHtml(item.error || "未生成");
      const copy = item.status === "done"
        ? `<button type="button" class="secondary-button mini" data-base-title-copy="${escapeHtml(item.id)}">复制</button>`
        : "";
      return `<tr><td>${escapeHtml(item.sku || "—")}</td><td class="base-title-result-title">${cell}</td><td>${copy}</td></tr>`;
    }).join("");
  }
  if (typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
}

// 按勾选（含「全选当前列表」）逐个重识别。串行跑：视觉模型单次就要几十秒，
// 并发只会把模型连接和本机请求一起打满；进度写在商品面板的结果行上（标题另进队列）。
async function retryCheckedBaseProducts(aspect, label) {
  if (state.baseProductRetryRunning) return;
  const result = $("base-product-result");
  const products = [...state.selectedBaseProducts]
    .map((id) => state.baseProducts.find((item) => item.id === id))
    .filter(Boolean);
  if (!products.length) {
    if (result) result.textContent = `请先勾选要重新识别${label}的基础商品。`;
    return;
  }
  setBaseProductRetryBusy(true);
  // 标题可以手动指定规则：选了就强制按该规则生成（不看商品范围推断），
  // 不选则沿用「自动匹配默认规则」。
  const skillId = aspect === "title" ? selectedBaseRetrySkillId() : "";
  const queued = aspect === "title";
  if (queued) resetBaseTitleQueue(products);
  const failures = [];
  try {
    for (let index = 0; index < products.length; index += 1) {
      const product = products[index];
      const name = baseTitleProductLabel(product);
      const queueItem = queued ? baseTitleQueueItem(product.id) : null;
      if (queueItem) {
        queueItem.status = "running";
        renderBaseTitleQueue();
      }
      if (result) result.textContent = `重新识别${label} ${index + 1}/${products.length}：${name}…`;
      try {
        const body = skillId ? { aspect, skill_id: skillId, force_skill: true } : { aspect };
        const updated = await api(`/api/base-products/${encodeURIComponent(product.id)}/regenerate-listing`, {
          method: "POST",
          body: JSON.stringify(body),
        });
        const error = String(updated?.ai_generation_error || "").trim();
        if (error) {
          failures.push(`${name}：${error}`);
          if (queueItem) {
            queueItem.status = "error";
            queueItem.error = error;
          }
        } else if (queueItem) {
          queueItem.status = "done";
          queueItem.title = String(updated?.listing_title || "").trim();
        }
      } catch (error) {
        const message = error.message || String(error);
        failures.push(`${name}：${message}`);
        if (queueItem) {
          queueItem.status = "error";
          queueItem.error = message;
        }
      }
      if (queued) renderBaseTitleQueue();
    }
    await Promise.all([loadBaseProducts(), loadAssets()]);
    const successCount = products.length - failures.length;
    let message = `重新识别${label}完成：成功 ${successCount} 个`;
    if (failures.length) {
      const shown = failures.slice(0, 3).join("；");
      message += `，失败 ${failures.length} 个（${shown}${failures.length > 3 ? "；…" : ""}）`;
    }
    if (result) result.textContent = message;
    showOperationFeedback(message, failures.length ? "warning" : "success");
    if (queued) {
      renderBaseTitleQueue();
      if (baseTitleQueueDone().length) {
        showBaseTitleResults();
        if (result) result.textContent = `${message}；标题已弹出，可核对后复制`;
      }
    }
  } catch (error) {
    operationError(error, `重新识别${label}失败`);
  } finally {
    setBaseProductRetryBusy(false);
  }
}


async function deleteCheckedBaseProducts() {
  const result = $("base-product-result");
  const ids = [...state.selectedBaseProducts];
  if (!ids.length) {
    if (result) result.textContent = "请先勾选要删除的基础商品。";
    return;
  }
  const products = state.baseProducts.filter((item) => ids.includes(item.id));
  const names = products.map((item) => item.product_name_cn || item.base_name || item.main_sku || item.group_id || "未命名商品");
  const copies = products.reduce((sum, item) => sum + Number(item.listing_copy_count || 0), 0);
  const head = names.length > 3 ? `${names.slice(0, 3).join("、")} 等 ${names.length} 个` : names.join("、");
  const confirmed = window.confirm(
    `确认删除 ${ids.length} 个基础商品：${head}？\n\n` +
    `将删除关联的 ${copies} 个店铺刊登副本、其 SKU/价格和未完成自动化任务。历史自动化记录会保留。NAS 原始素材文件不会删除。`,
  );
  if (!confirmed) return;
  if (result) result.textContent = "正在删除选中商品...";
  try {
    const data = await api("/api/base-products/batch-delete", {
      method: "POST",
      body: JSON.stringify({ ids }),
    });
    const deletedCount = Number(data.deleted_count || 0);
    const notFound = Array.isArray(data.not_found_ids) ? data.not_found_ids : [];
    for (const id of ids) state.selectedBaseProducts.delete(id);
    if (state.selectedBaseProductId && ids.includes(state.selectedBaseProductId)) {
      state.selectedBaseProductId = "";
    }
    if (state.editingBaseProductId && ids.includes(state.editingBaseProductId)) closeBaseImageEditor();
    await Promise.all([loadBaseProducts(), loadStoreListingCopies(), loadAutomationJobs()]);
    const message = `已删除 ${deletedCount} 个基础商品${deletedCount ? `：清理 ${data.deleted_listing_copy_count || 0} 个刊登副本、${data.deleted_automation_job_count || 0} 个未完成任务` : ""}${notFound.length ? `，其中 ${notFound.length} 个不存在已跳过` : ""}`;
    if (result) result.textContent = message;
    showOperationFeedback(message, notFound.length ? "warning" : "success");
  } catch (error) {
    operationError(error, "批量删除基础商品失败");
  }
}

function setBaseTimeFilter(preset) {
  // 只认白名单里的取值；未知值（含空）退回默认的「今天」。
  const value = String(preset || "");
  state.baseProductTimeFilter = BASE_PRODUCT_TIME_FILTER_LABELS[value] ? value : "today";
  renderBaseProducts();
}

function toggleBaseCategoryFilter(key) {
  // 「全部」chip 固定清除分类；其余 chip 再次点击同一分类 = 取消选中回到「全部」。
  if (key === "") {
    state.baseProductCategoryFilter = "";
  } else {
    state.baseProductCategoryFilter = state.baseProductCategoryFilter === key ? "" : key;
  }
  renderBaseProducts();
}

function legacyCategoryFields(values) {
  return Object.entries(values || {})
    .filter(([fieldId, value]) => fieldId !== "display" && typeof value !== "object")
    .map(([fieldId, value]) => ({
      field_id: fieldId,
      label: fieldId.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()),
      type: "text",
      required: true,
      default_value: value,
      options: [],
    }));
}

// 下拉里的"添加自选值"入口：选中它即弹出输入框，把值作为一个真实选项加入
// （提交时就是该文本；执行器会用马帮的「添加自填选项」把它写进页面）。
const CUSTOM_ATTRIBUTE_OPTION = "__custom_attribute_value__";

function renderStoreTemplateCategoryFields(fields, values = {}, container = null) {
  const target = container || $("store-template-category-fields");
  if (!target) return;
  target.innerHTML = "";
  for (const field of fields || []) {
    const fieldId = String(field.field_id || "").trim();
    if (!fieldId) continue;
    const label = field.label || fieldId;
    const required = Boolean(field.required || field.required_when);
    const value = values[fieldId] ?? field.default_value ?? "";
    const wrapper = document.createElement("label");
    wrapper.className = "category-field";
    const caption = document.createElement("span");
    caption.textContent = `${label}${required ? " *" : ""}`;
    wrapper.appendChild(caption);
    let control;
    // 老模板可能把自由文本属性存成了 select（条件/尺寸 这类）：完全没有枚举时，存量
    // 自由文本值 valueMissingFromOptions 自然为真，按文本框渲染并预填，别渲染成空下拉。
    // 但如果字段本身有枚举、只是所选值不在枚举里（原产地=Philippines 这类"自选值"），
    // 仍然渲染成下拉，并把该值作为一个选项补进去——执行器会走马帮的「添加自填选项」。
    const selectOptions = field.options || [];
    const isSelectField = field.type === "single_select" || field.type === "multi_select";
    const valueMissingFromOptions = (candidate) => {
      const text = candidate == null ? "" : String(candidate);
      if (!text) return false;
      return !selectOptions.some((option) => String(option.value ?? "") === text);
    };
    const needsTextFallback = isSelectField && selectOptions.length === 0;
    if (isSelectField && !needsTextFallback) {
      control = document.createElement("select");
      if (field.type === "multi_select") control.multiple = true;
      else {
        const empty = document.createElement("option");
        empty.value = "";
        empty.textContent = "请选择";
        control.appendChild(empty);
      }
      const optionValues = new Set(selectOptions.map((option) => String(option.value ?? "")));
      for (const option of selectOptions) {
        const item = document.createElement("option");
        item.value = option.value ?? "";
        item.textContent = option.label || option.value || "";
        item.selected = field.type === "multi_select" ? Array.isArray(value) && value.includes(item.value) : String(value) === item.value;
        control.appendChild(item);
      }
      // 已存的自选值（不在枚举里）补成选项，保证能回显与再次选择。
      const storedValues = field.type === "multi_select"
        ? (Array.isArray(value) ? value : [])
        : (value === "" || value == null ? [] : [value]);
      for (const stored of storedValues) {
        const text = String(stored);
        if (!valueMissingFromOptions(stored) || optionValues.has(text)) continue;
        const item = document.createElement("option");
        item.value = text;
        item.textContent = text;
        item.selected = true;
        optionValues.add(text);
        control.appendChild(item);
      }
      if (field.type === "single_select") {
        const custom = document.createElement("option");
        custom.value = CUSTOM_ATTRIBUTE_OPTION;
        custom.textContent = "＋ 添加自选值…";
        control.appendChild(custom);
        control.addEventListener("change", () => {
          if (control.value !== CUSTOM_ATTRIBUTE_OPTION) return;
          const typed = String(window.prompt(`为「${label}」输入自选值（自动化时会用马帮的「添加自填选项」写入）`, "") || "").trim();
          const customEntry = control.querySelector(`option[value="${CUSTOM_ATTRIBUTE_OPTION}"]`);
          if (!typed) {
            control.value = value == null ? "" : String(value);
            return;
          }
          let entry = [...control.options].find((option) => option.value === typed && option !== customEntry);
          if (!entry) {
            entry = document.createElement("option");
            entry.value = typed;
            entry.textContent = typed;
            control.insertBefore(entry, customEntry);
          }
          control.value = typed;
        });
      }
      if (field.type === "single_select") control.value = value == null ? "" : String(value);
      if (field.type === "multi_select") {
        // 多选（型号 就是这类）也要能加自选值：马帮侧自动化会走「添加自填选项」写入。
        const customButton = document.createElement("button");
        customButton.type = "button";
        customButton.className = "chip category-field-custom-btn";
        customButton.textContent = "＋ 添加自选值";
        customButton.addEventListener("click", () => {
          const typed = String(window.prompt(`为「${label}」输入自选值（自动化时会用马帮的「添加自填选项」写入）`, "") || "").trim();
          if (!typed) return;
          // 自选值的语义是"枚举里没有我要的" → 替换当前选择（想同时保留枚举值可以再点回去）。
          [...control.options].forEach((option) => { option.selected = false; });
          let entry = [...control.options].find((option) => option.value === typed);
          if (!entry) {
            entry = document.createElement("option");
            entry.value = typed;
            entry.textContent = typed;
            control.appendChild(entry);
          }
          entry.selected = true;
        });
        wrapper.appendChild(customButton);
      }
    } else if (field.type === "boolean") {
      control = document.createElement("input");
      control.type = "checkbox";
      control.checked = value === true || value === "true";
    } else {
      control = document.createElement("input");
      control.type = field.type === "number" ? "number" : "text";
      control.value = value == null ? "" : String(value);
    }
    control.dataset.categoryFieldInput = fieldId;
    wrapper.appendChild(control);
    target.appendChild(wrapper);
  }
}

async function loadCategoryProfileVersions(profileId) {
  if (!profileId) return;
  const data = await api(`/api/category-profiles/${encodeURIComponent(profileId)}/versions`);
  state.categoryProfileVersions = data.items || [];
  renderCategoryProfileVersions();
}

function renderCategoryProfileVersions() {
  const tbody = $("category-version-table");
  if (!tbody) return;
  tbody.innerHTML = "";
  if (!state.categoryProfileVersions.length) {
    tbody.innerHTML = `<tr><td colspan="6">暂无版本</td></tr>`;
    return;
  }
  for (const version of state.categoryProfileVersions) {
    const tr = document.createElement("tr");
    const severity = version.diff?.severity || "none";
    const source = version.source?.type || "";
    const actions = version.status === "candidate"
      ? `<button data-publish-category-version="${escapeHtml(version.id)}">发布</button><button data-reject-category-version="${escapeHtml(version.id)}">拒绝</button>`
      : "";
    tr.innerHTML = `
      <td>${escapeHtml(version.version || "")}</td>
      <td><span class="status ${escapeHtml(version.status || "")}">${escapeHtml(version.status || "")}</span></td>
      <td>${escapeHtml(source)}</td>
      <td>${escapeHtml(severity)}</td>
      <td>${escapeHtml((version.fields || []).length)}</td>
      <td><div class="actions compact">${actions}</div></td>
    `;
    tbody.appendChild(tr);
  }
}

async function reviewCategoryProfileVersion(versionId, action) {
  const result = $("category-version-result");
  const reviewer = $("category-version-reviewer")?.value.trim() || "";
  const note = $("category-version-note")?.value.trim() || "";
  if (!reviewer || !note) {
    result.textContent = "请填写审核人和备注";
    return;
  }
  result.textContent = action === "publish" ? "正在发布候选版本..." : "正在拒绝候选版本...";
  await api(`/api/category-profile-versions/${encodeURIComponent(versionId)}/${action}`, {
    method: "POST",
    body: JSON.stringify(action === "publish" ? { reviewer, note } : { reviewer, reason: note }),
  });
  result.textContent = action === "publish" ? "版本已发布" : "候选版本已拒绝";
  await loadCategoryProfiles();
}

function renderCategoryProfileVersions() {
  const tbody = $("category-version-table");
  if (!tbody) return;
  tbody.innerHTML = "";
  if (!state.categoryProfileVersions.length) {
    tbody.innerHTML = '<tr><td colspan="5">暂无版本</td></tr>';
    return;
  }
  for (const version of state.categoryProfileVersions) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(version.version || "")}</td>
      <td><span class="status ${escapeHtml(version.status || "")}">${escapeHtml(version.status || "")}</span></td>
      <td>${escapeHtml(version.source?.type || "-")}</td>
      <td>${escapeHtml(version.diff?.severity || "none")}</td>
      <td>${escapeHtml((version.fields || []).length)}</td>
    `;
    tbody.appendChild(tr);
  }
}

function collectStoreTemplateCategoryValues() {
  const values = {};
  // 限定模板编辑器容器：商品维护弹窗的属性字段共用 data-category-field-input，
  // 不加 scope 会把商品维度的值一起收进模板。
  document.querySelectorAll("#store-template-category-fields [data-category-field-input]").forEach((control) => {
    const fieldId = control.dataset.categoryFieldInput;
    if (!fieldId) return;
    if (control instanceof HTMLInputElement && control.type === "checkbox") values[fieldId] = control.checked;
    else if (control instanceof HTMLSelectElement && control.multiple) {
      const selected = [...control.selectedOptions]
        .map((item) => item.value)
        .filter((item) => item && item !== CUSTOM_ATTRIBUTE_OPTION);
      if (selected.length) values[fieldId] = selected;
    } else if (control.value !== "" && control.value !== CUSTOM_ATTRIBUTE_OPTION) values[fieldId] = control.value;
  });
  return values;
}

const baseProductImageTypes = new Set(["main", "variant", "model", "detail"]);
const fileNameCollator = new Intl.Collator("zh-Hans-CN", { numeric: true, sensitivity: "base" });
const productImageLimit = 9;
// 与后端 size_chart_uploads "product_image" 用途的上限一致；超限先在本地拦掉，
// 避免把整张图发上去才被服务端拒绝。
const productImageUploadMaxBytes = 10 * 1024 * 1024;

function baseProductImageCandidates(product) {
  return assetsForBaseProduct(product)
    .filter((asset) => baseProductImageTypes.has(asset.asset_type || "") && asset.file_name)
    .sort((left, right) => fileNameCollator.compare(left.file_name || "", right.file_name || ""));
}

function defaultBaseProductImageAssets(product) {
  const assets = baseProductImageCandidates(product);
  const main = assets.find((asset) => (asset.asset_type || "") === "main");
  const models = assets.filter((asset) => (asset.asset_type || "") === "model" && asset.file_name !== main?.file_name);
  // 与后端 _default_product_images 一致：主图 → 模特图 → 颜色图（印刷图 print 不在候选里）。
  const variants = assets.filter((asset) => (asset.asset_type || "") === "variant" && asset.file_name !== main?.file_name);
  return [main, ...models, ...variants].filter(Boolean).slice(0, productImageLimit);
}

function productImageAssets(product) {
  const assets = baseProductImageCandidates(product);
  const byName = new Map(assets.map((asset) => [asset.file_name, asset]));
  const selected = [];
  for (const fileName of product.product_images || []) {
    const asset = byName.get(fileName);
    if (asset) selected.push(asset);
  }
  if (selected.length) return selected.slice(0, productImageLimit);
  return defaultBaseProductImageAssets(product);
}

function variantImageAssets(product) {
  const imageTypes = new Set(["main", "variant", "model", "detail"]);
  return assetsForBaseProduct(product).filter((asset) => imageTypes.has(asset.asset_type || ""));
}

function normalizeBaseColors(colors) {
  const seen = new Set();
  const result = [];
  for (const color of colors || []) {
    const text = String(color || "").trim();
    const key = text.toLowerCase();
    if (!text || seen.has(key)) continue;
    seen.add(key);
    result.push(text);
  }
  return result;
}

function baseColorsFromProduct(product) {
  const colors = normalizeBaseColors(product.recognized_colors || []);
  if (colors.length) return colors;
  return normalizeBaseColors(Object.keys(product.variant_image_map || {}));
}

// ===== 颜色图顺序 ↔ 产品图顺序 联动（商品维护 → 颜色图区开关，2026-10-10）=====
// 开关打开时两边互为镜像：
//   拖动产品图 → 颜色顺序按各自颜色图在新产品图顺序里的位置重排；
//   拖动颜色   → 把它的颜色图挪到对应槽位（只动产品图里属于颜色图的那些位置，
//                主图 / 模特图的位置不变），再按产品图顺序收一次顺序。
// 未选颜色图、或那张图不在产品图顺序里的颜色，保持原有相对顺序排在最后。

function baseColorFollowEnabled() {
  return state.editingBaseColorOrderFollowsImages !== false;
}

function baseColorImageFileName(color) {
  return String((state.editingBaseVariantImageMap || {})[color] || "").trim();
}

// 产品图顺序里被颜色图占用的槽位。
function baseColorImageSlots() {
  const mapped = new Set(
    Object.values(state.editingBaseVariantImageMap || {})
      .map((name) => String(name || "").trim())
      .filter(Boolean),
  );
  return (state.editingBaseProductImages || [])
    .map((name, index) => ({ index, name: String(name || "").trim() }))
    .filter((slot) => mapped.has(slot.name));
}

// 产品图顺序 → 颜色顺序。返回颜色顺序是否真的变了。
function syncBaseColorsToImageOrder() {
  const colors = state.editingBaseColors || [];
  if (colors.length < 2) return false;
  const position = new Map();
  (state.editingBaseProductImages || []).forEach((name, index) => {
    const key = String(name || "").trim();
    if (key && !position.has(key)) position.set(key, index);
  });
  const matched = [];
  const unmatched = [];
  colors.forEach((color, index) => {
    const fileName = baseColorImageFileName(color);
    const slot = fileName ? position.get(fileName) : undefined;
    if (slot === undefined) unmatched.push({ color, index });
    else matched.push({ color, index, slot });
  });
  // 位置相同的按原来的先后（稳定），未匹配的整体排在后面。
  matched.sort((a, b) => (a.slot - b.slot) || (a.index - b.index));
  const next = [...matched, ...unmatched].map((entry) => entry.color);
  if (next.every((color, index) => color === colors[index])) return false;
  state.editingBaseColors = next;
  return true;
}

// 颜色顺序 → 产品图顺序。返回产品图顺序是否真的变了。
function syncBaseImageOrderToColors() {
  const slots = baseColorImageSlots();
  if (!slots.length) return false;
  const slotNames = new Set(slots.map((slot) => slot.name));
  const ordered = [];
  const used = new Set();
  for (const color of state.editingBaseColors || []) {
    const fileName = baseColorImageFileName(color);
    if (!fileName || used.has(fileName) || !slotNames.has(fileName)) continue;
    used.add(fileName);
    ordered.push(fileName);
  }
  // 没有被任何颜色认领的残留槽位留在原位。
  slots.forEach((slot) => {
    if (used.has(slot.name)) return;
    used.add(slot.name);
    ordered.push(slot.name);
  });
  if (ordered.every((name, index) => name === slots[index].name)) return false;
  slots.forEach((slot, index) => {
    if (ordered[index]) state.editingBaseProductImages[slot.index] = ordered[index];
  });
  return true;
}

// 拖动 / 增删产品图之后：以产品图顺序为准收颜色顺序。
function applyBaseColorFollowFromImages() {
  if (!baseColorFollowEnabled()) return;
  if (!syncBaseColorsToImageOrder()) return;
  renderBaseVariantImageMap();
  renderBasePriceColors();
}

// 拖动颜色 / 改颜色的颜色图之后：把颜色图摆到颜色顺序对应的槽位，再收一次颜色顺序。
function applyBaseColorFollowFromColors() {
  if (!baseColorFollowEnabled()) return;
  const imagesChanged = syncBaseImageOrderToColors();
  const colorsChanged = syncBaseColorsToImageOrder();
  if (imagesChanged) {
    renderBaseImageOrder();
    renderBaseImagePicker();
  }
  if (imagesChanged || colorsChanged) {
    renderBaseVariantImageMap();
    renderBasePriceColors();
  }
}

function renderBaseColorFollowToggle() {
  const toggle = $("base-color-follow-images");
  if (toggle instanceof HTMLInputElement) toggle.checked = baseColorFollowEnabled();
  const hint = $("base-color-follow-hint");
  if (hint) {
    hint.textContent = baseColorFollowEnabled()
      ? "已联动：颜色顺序跟随「产品图顺序」——拖动产品图会带动颜色顺序，拖动颜色行也会把它的颜色图移到对应位置；未选颜色图（或该图不在产品图顺序里）的颜色排在最后。"
      : "未联动：颜色顺序可以自由拖动，不会影响「产品图顺序」。";
  }
  const map = $("base-variant-image-map");
  if (map instanceof HTMLElement) map.classList.toggle("is-follow-images", baseColorFollowEnabled());
}

function nextCustomBaseColorName() {
  const existing = new Set(state.editingBaseColors.map((color) => String(color || "").trim().toLowerCase()).filter(Boolean));
  let index = 1;
  while (existing.has(`自定义颜色${index}`.toLowerCase())) index += 1;
  return `自定义颜色${index}`;
}

// 常用颜色快捷添加：与 AI 识别的 9 个固定色（Apricot/Purple/Blue/Red/Black/White/
// Pink/Brown/Green）保持同一套名字，点一下直接以该颜色名新增一行颜色图。
const BASE_QUICK_COLORS = [
  { name: "Black", swatch: "#1f2328" },
  { name: "Apricot", swatch: "#e8a25e" },
  { name: "Blue", swatch: "#3b82f6" },
  { name: "Red", swatch: "#dc2626" },
  { name: "Green", swatch: "#16a34a" },
  { name: "Brown", swatch: "#8b5e34" },
  { name: "Pink", swatch: "#ec4899" },
  { name: "Purple", swatch: "#7c3aed" },
  { name: "White", swatch: "#ffffff" },
];

function renderBaseColorQuickPick() {
  const target = $("base-color-quick-pick");
  if (!target) return;
  // 大小写不敏感地比对已有颜色（历史数据里 Darkblue/darkBlue 混存），已加过的置灰。
  const existing = new Set((state.editingBaseColors || [])
    .map((color) => String(color || "").trim().toLowerCase())
    .filter(Boolean));
  // 9 个常用色都已在列表里时「全选」没有事可做，跟着隐藏。
  const pending = BASE_QUICK_COLORS.filter(({ name }) => !existing.has(name.toLowerCase()));
  const selectAll = pending.length
    ? `<button type="button" class="color-quick-select-all" data-quick-color-select-all title="一键添加全部 ${pending.length} 个未添加的常用颜色">全选</button>`
    : "";
  target.innerHTML = `<span class="color-quick-pick-label muted">常用颜色：</span>${selectAll}` + BASE_QUICK_COLORS.map(({ name, swatch }) => {
    const added = existing.has(name.toLowerCase());
    return `<button type="button" class="color-quick-chip${added ? " is-added" : ""}" data-quick-color="${escapeHtml(name)}"${added ? " disabled" : ""} title="${added ? `${escapeHtml(name)} 已添加` : `添加 ${escapeHtml(name)}`}"><span class="color-quick-dot" style="background:${swatch}"></span><span class="color-quick-name">${escapeHtml(name)}</span>${added ? '<span class="color-quick-check" aria-hidden="true">✓</span>' : ""}</button>`;
  }).join("");
}

function addBaseColorsWithNames(names) {
  syncBaseColorInputs();
  const existing = new Set((state.editingBaseColors || [])
    .map((color) => String(color || "").trim().toLowerCase())
    .filter(Boolean));
  let addedCount = 0;
  for (const name of names || []) {
    const color = String(name || "").trim();
    if (!color || existing.has(color.toLowerCase())) continue;
    existing.add(color.toLowerCase());
    state.editingBaseColors.push(color);
    addedCount += 1;
  }
  if (!addedCount) return;
  renderBaseVariantImageMap();
  renderBasePriceColors();
}

function addBaseColorWithName(name) {
  addBaseColorsWithNames([name]);
}

function syncBaseColorInputs() {
  document.querySelectorAll("[data-base-color-index]").forEach((input) => {
    if (!(input instanceof HTMLInputElement)) return;
    renameBaseColor(Number(input.dataset.baseColorIndex || -1), input.value);
  });
}

function renameBaseColor(index, nextValue) {
  if (index < 0 || index >= state.editingBaseColors.length) return;
  const oldColor = state.editingBaseColors[index] || "";
  const nextColor = String(nextValue || "").trim();
  if (oldColor === nextColor) return;
  const fileName = state.editingBaseVariantImageMap[oldColor] || "";
  if (oldColor) delete state.editingBaseVariantImageMap[oldColor];
  state.editingBaseColors[index] = nextColor;
  if (nextColor && fileName) state.editingBaseVariantImageMap[nextColor] = fileName;
  // 价格行颜色标签跟随。
  const rows = state.basePriceColorRows || [];
  const idx = rows.findIndex((row) => row.color === oldColor);
  if (idx >= 0) rows[idx] = { ...rows[idx], color: nextColor || oldColor };
  state.basePriceColorRows = rows;
}

function addBaseColor() {
  syncBaseColorInputs();
  state.editingBaseColors.push(nextCustomBaseColorName());
  renderBaseVariantImageMap();
  renderBasePriceColors();
}

function removeBaseColor(index) {
  syncBaseColorInputs();
  const color = state.editingBaseColors[index] || "";
  if (color) delete state.editingBaseVariantImageMap[color];
  state.editingBaseColors.splice(index, 1);
  // 该颜色的价格行随之移除。
  state.basePriceColorRows = (state.basePriceColorRows || []).filter((row) => row.color !== color);
  renderBaseVariantImageMap();
  renderBasePriceColors();
}

function moveBaseColor(fromIndex, toIndex) {
  if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0) return;
  const colors = state.editingBaseColors;
  if (!colors[fromIndex] || !colors[toIndex]) return;
  const [moved] = colors.splice(fromIndex, 1);
  colors.splice(toIndex, 0, moved);
  // 价格行与图片映射同步跟随新顺序。
  const rows = state.basePriceColorRows || [];
  if (rows[fromIndex] && rows[toIndex]) {
    const [movedRow] = rows.splice(fromIndex, 1);
    rows.splice(toIndex, 0, movedRow);
  }
  state.basePriceColorRows = rows;
  // 联动打开时把颜色图挪到对应槽位，再按产品图顺序收一次（未匹配的会回到最后）。
  applyBaseColorFollowFromColors();
  renderBaseVariantImageMap();
  renderBasePriceColors();
}

function handleBaseColorRename() {
  // 颜色改名后同步价格行的颜色标签（editingBaseColors 已由 renameBaseColor 更新，
  // 重新渲染价格表即可让行名跟随）。
  renderBasePriceColors();
}

// 商品属性/旧数据里的 target_audience 可能还是 boy/girl/kids_unisex。
// 2026-11-02 起弹窗只提供 男童/女童/男装；认不出来就留「按商品」让后端推断。
const BASE_TITLE_AUDIENCE_MAP = { boy: "boys", boys: "boys", girl: "girls", girls: "girls", men: "men", man: "men", male: "men", women: "women", woman: "women", female: "women" };

function baseProductAudienceValue(product) {
  const raw = String(product?.title_audience || product?.target_audience || "").trim().toLowerCase();
  if (BASE_TITLE_AUDIENCE_MAP[raw]) return BASE_TITLE_AUDIENCE_MAP[raw];
  const attrs = product?.recognized_attributes || {};
  const text = Object.entries(attrs)
    .filter(([key]) => /gender|audience|性别|人群/i.test(key))
    .map(([, value]) => String(value || ""))
    .join(" ")
    .toLowerCase();
  if (/\bboys?\b|男童/.test(text)) return "boys";
  if (/\bgirls?\b|女童/.test(text)) return "girls";
  if (/\bmen\b|男装/.test(text)) return "men";
  return "";
}

const BASE_TITLE_STATUS_LABELS = { PASS: "通过", REVIEW: "待确认", BLOCKED: "未通过" };

function renderBaseTitleSuggestions(titles, message = "") {
  const node = $("base-maintenance-title-suggestions");
  if (!node) return;
  if (message) {
    node.hidden = false;
    node.innerHTML = `<p class="muted">${escapeHtml(message)}</p>`;
    return;
  }
  const rows = Array.isArray(titles) ? titles.filter((item) => item && item.title) : [];
  if (!rows.length) {
    node.hidden = true;
    node.innerHTML = "";
    return;
  }
  node.hidden = false;
  const hint = '<p class="base-title-suggest-hint">AI 推荐标题：点一下填入上面的标题框，再点「保存维护内容」生效。</p>';
  node.innerHTML = hint + rows.map((item, index) => {
    const blocked = item.status === "BLOCKED";
    const label = item.recommended ? "推荐" : `备选 ${index}`;
    const meta = `${Number(item.chars || (item.title || "").length)} 字符 · ${BASE_TITLE_STATUS_LABELS[item.status] || item.status || "待确认"}`;
    const tips = [...(item.warnings || []), ...((item.errors || []).filter((text) => text && !item.warnings?.includes(text)))];
    return `<button type="button" class="base-title-suggestion${blocked ? " is-blocked" : ""}" data-base-title-suggestion="${escapeHtml(item.title)}"${blocked ? " disabled" : ""} title="${escapeHtml(tips.join("；"))}">
      <span class="base-title-suggestion-head"><span class="base-title-suggestion-tag">${escapeHtml(label)}</span><span class="base-title-suggestion-meta">${escapeHtml(meta)}</span></span>
      <span class="base-title-suggestion-text">${escapeHtml(item.title)}</span>
    </button>`;
  }).join("");
}

async function generateBaseProductTitleCandidates() {
  const productId = state.editingBaseProductId || state.selectedBaseProductId;
  if (!productId) return;
  const button = $("base-maintenance-ai-title");
  const audience = $("base-maintenance-audience")?.value || "";
  if (button) { button.disabled = true; button.textContent = "生成中…"; }
  renderBaseTitleSuggestions([], "正在按标题规则生成 3 个候选标题…");
  try {
    const body = { product_id: productId };
    if (audience) body.audience = audience;
    const data = await api("/api/title-workbench/preview", { method: "POST", body: JSON.stringify(body) });
    const recommendedIndex = Number.isInteger(data.recommended_index) ? data.recommended_index : 0;
    const titles = (Array.isArray(data.titles) ? data.titles : [])
      .map((item, index) => ({ ...item, recommended: index === recommendedIndex }));
    titles.sort((a, b) => Number(b.recommended) - Number(a.recommended));
    if (!titles.length) {
      renderBaseTitleSuggestions([], data.error || "AI 没有返回可用标题，请检查标题规则与 AI 连接。");
    } else {
      renderBaseTitleSuggestions(titles);
      if (data.error) showOperationFeedback(`AI 生成完成，但有提示：${data.error}`, "info");
    }
  } catch (error) {
    renderBaseTitleSuggestions([], `生成失败：${error.message || error}`);
    showOperationFeedback(error.message || "AI 生成标题失败", "error");
  } finally {
    if (button) { button.disabled = false; button.textContent = "AI 生成标题"; }
  }
}

function openBaseProductMaintenance(productId) {
  const product = state.baseProducts.find((item) => item.id === productId);
  if (!product) return;
  state.selectedBaseProductId = productId;
  state.editingBaseProductId = productId;
  state.editingBaseProductImages = productImageAssets(product).map((asset) => asset.file_name);
  state.editingBaseVariantImageMap = { ...(product.variant_image_map || {}) };
  state.editingBaseColors = baseColorsFromProduct(product);
  // 旧商品没有这个字段 → 默认打开联动（打开弹窗时就把颜色顺序对齐到产品图顺序）。
  state.editingBaseColorOrderFollowsImages = product.color_order_follows_images !== false;
  state.editingBaseSizeChartImage = product.size_chart_image || "";
  state.editingBaseRecognizedSizes = [...(product.recognized_sizes || [])];
  // 勾选状态以商品记录为准：selected_sizes 字段存在（即使是空数组）就照用——空数组
  // 表示操作员明确一个都没选；只有旧数据缺这个字段时才回退到识别尺码。
  state.editingBaseSelectedSizes = Array.isArray(product.selected_sizes)
    ? [...product.selected_sizes]
    : [...(product.recognized_sizes || [])];
  // 还没有任何尺码时给一套常用默认尺码（XXS–XXXL）作为可点选项，但默认不勾选：
  // 勾选 = 该码会上架，必须由操作员明确点选。只改编辑态，保存维护内容才落库。
  if (!state.editingBaseRecognizedSizes.length) {
    state.editingBaseRecognizedSizes = [...BASE_COMMON_SIZES];
  }
  state.editingBaseProductCategory = (product.product_category && typeof product.product_category === "object")
    ? { ...product.product_category }
    : null;
  state.editingBaseProduct = product;
  state.editingBaseProductAttributeValues = { ...(product.attribute_values || {}) };
  const titleEl = $("base-maintenance-title");
  if (titleEl) titleEl.textContent = product.product_name_cn || product.base_name || product.main_sku || "基础商品维护";
  $("base-maintenance-name").value = product.product_name_cn || product.base_name || "";
  $("base-maintenance-main-sku").value = product.main_sku || product.group_id || "";
  $("base-maintenance-status").value = product.status || "base_ready";
  $("base-maintenance-title-input").value = product.listing_title || "";
  // 适用人群只影响 AI 生成标题；选中的值会在「保存维护内容」时写入商品。
  const titleAudienceSelect = $("base-maintenance-audience");
  if (titleAudienceSelect) titleAudienceSelect.value = baseProductAudienceValue(product);
  renderBaseTitleSuggestions([]);
  $("base-maintenance-description").value = product.description || "";
  renderBaseMaintenanceCategoryInput();
  loadBaseCategoryAttributeFields();
  renderBaseMaintenanceSummary(product);
  renderBaseProductVideo(product);
  renderCnscSiteOrderDisplays();
  applyBasePriceCollapsed();
  loadBasePriceColors(productId);
  // 换商品时把「快速类目模板」下拉复位：否则它还显示上个商品保存/套用的模板名，
  // 看起来像已套用，实际表单并没有带过来。
  const quickTemplateSelect = $("base-category-template-select");
  if (quickTemplateSelect) quickTemplateSelect.value = "";
  loadBaseCategoryQuickTemplates();
  $("base-image-editor-result").textContent = "";
  // 重新打开时按保存队列的真实状态恢复按钮（上一个商品还在保存中就继续显示「保存中…」）。
  renderBaseProductSaveButton();
  $("base-maintenance-modal").hidden = false;
  showMaintenancePanel("images");
  renderBaseProducts();
  renderBaseColorFollowToggle();
  renderBaseImageOrder();
  renderBaseImagePicker();
  renderBaseVariantImageMap();
  // 联动打开时按产品图顺序对齐一次颜色顺序（只改编辑态，保存后才落库）。
  applyBaseColorFollowFromImages();
  renderBaseSizeChartSection();
  renderBaseSizeTemplateOptions();
}

function renderBaseMaintenanceCategoryInput() {
  const pathInput = $("base-maintenance-category-path");
  const idInput = $("base-maintenance-category-id");
  const storeInput = $("base-maintenance-category-store");
  const category = state.editingBaseProductCategory || null;
  // 中文界面优先展示 path_zh（马帮中文明），英文 path 作为兜底与存储值。
  const path = (category && (category.path_zh || category.path)) || "";
  if (pathInput) pathInput.value = path;
  if (idInput) idInput.value = (category && category.category_id) || "";
  if (storeInput) storeInput.value = (category && category.store_id) || "";
}

function baseProductCategorySelection() {
  // Read the category currently chosen for the editing base product.
  const category = state.editingBaseProductCategory || null;
  if (!category || !(category.path || category.path_zh)) return null;
  return { ...category };
}

const BASE_PRODUCT_STATUS_LABELS = {
  base_ready: "编辑中",
  needs_check: "需要检查",
  completed: "已完成",
};

const AUTOMATION_GLYPH_LABELS = {
  idle: "未开始",
  watch: "执行中",
  alert: "有失败",
  done: "已完成",
};

function renderBaseMaintenanceSummary(product) {
  const summary = product.automation_status_summary || { done: 0, running: 0, failed: 0, attention: 0, total_copies: 0, not_started: 0 };
  const glyph = product.automation_glyph || "idle";
  const status = product.status || "base_ready";
  const headerGlyph = $("base-maintenance-header-glyph");
  if (headerGlyph) headerGlyph.className = `header-glyph header-glyph--${glyph}`;
  const setText = (id, value) => { const el = $(id); if (el) el.textContent = String(value); };
  setText("base-maintenance-meta-sku", product.main_sku || product.group_id || "-");
  setText("base-maintenance-meta-colors", (product.recognized_colors || []).length || 0);
  setText("base-maintenance-meta-images", (product.product_images || []).length || 0);
  setText("base-maintenance-meta-copies", summary.total_copies || 0);
  setText("base-maintenance-meta-status", BASE_PRODUCT_STATUS_LABELS[status] || status || "-");
  const aiErrorEl = $("base-maintenance-ai-error");
  if (aiErrorEl) {
    const aiError = String(product.ai_generation_error || "").trim();
    aiErrorEl.textContent = aiError ? `AI 生成失败：${aiError}` : "";
    aiErrorEl.hidden = !aiError;
  }
  const aiRetryBtn = $("base-maintenance-ai-retry");
  if (aiRetryBtn) {
    aiRetryBtn.hidden = !String(product.ai_generation_error || "").trim();
    aiRetryBtn.disabled = false;
    aiRetryBtn.textContent = "重试 AI 识别";
  }

  const autoGlyph = $("base-maintenance-auto-glyph");
  if (autoGlyph) autoGlyph.className = `row-glyph row-glyph--${glyph}`;
  const autoLabelParts = [];
  if (summary.total_copies) autoLabelParts.push(`${summary.done}/${summary.total_copies} 已完成`);
  if (summary.failed) autoLabelParts.push(`${summary.failed} 失败`);
  if (summary.attention) autoLabelParts.push(`${summary.attention} 待人工`);
  if (summary.running) autoLabelParts.push(`${summary.running} 执行中`);
  setText("base-maintenance-auto-label", autoLabelParts.length ? autoLabelParts.join(" · ") : AUTOMATION_GLYPH_LABELS[glyph] || "未开始");

  const thumb = $("base-maintenance-summary-thumb");
  if (thumb) {
    const assets = assetsForBaseProduct(product);
    const mainAsset = assets.find((asset) => asset.file_name === product.main_image) || assets.find((asset) => asset.file_name === (product.product_images || [])[0]);
    thumb.style.backgroundImage = mainAsset ? `url("${assetImageUrl(mainAsset, 480)}")` : "";
  }

  const copyList = $("base-maintenance-copy-list");
  if (copyList) {
    const copies = state.storeListingCopies.filter((copy) => copy.base_product_id === product.id);
    const jobsByCopy = new Map();
    for (const job of state.automationJobs) {
      const key = job.listing_copy_id || "";
      const existing = jobsByCopy.get(key);
      const stamp = String(job.updated_at || job.created_at || "");
      if (!existing || stamp > String(existing.updated_at || existing.created_at || "")) jobsByCopy.set(key, job);
    }
    if (!copies.length) {
      copyList.innerHTML = `<li class="muted">尚未生成刊登副本</li>`;
    } else {
      copyList.innerHTML = `<ul>${copies.map((copy) => {
        const job = jobsByCopy.get(copy.id);
        const tone = jobGlyphTone(copy, job);
        const label = jobGlyphLabel(copy, job);
        return `<li><span class="row-glyph row-glyph--${tone}" aria-hidden="true"></span><span>${escapeHtml(copy.store_name || copy.template_id || "店铺副本")}</span><span class="muted">· ${escapeHtml(label)}</span></li>`;
      }).join("")}</ul>`;
    }
  }

  const rollbackBtn = $("base-maintenance-rollback-btn");
  if (rollbackBtn) rollbackBtn.hidden = status !== "completed";
}

function jobGlyphTone(copy, job) {
  if (!job) return copy?.status === "completed" ? "done" : "idle";
  const status = String(job.status || "").toLowerCase();
  if (status === "saved" || status === "saved_with_warnings") return "done";
  if (status === "failed" || status === "blocked") return "alert";
  if (status === "needs_confirmation" || status === "needs_manual" || status === "needs_regeneration") return "watch";
  if (status === "claimed" || status === "running") return "watch";
  return "idle";
}

function jobGlyphLabel(copy, job) {
  if (!job) return copy?.status === "completed" ? "已完成" : "待生成任务";
  return TASK_STATUS_LABELS[job.status] || job.status || "未开始";
}

function showMaintenancePanel(_panelId) {
  // Panels are now shown together as a scrollable list — no-op kept as a shim so
  // openBaseProductMaintenance can call it without a branch.
}

async function rollbackBaseProduct(productId) {
  if (!productId) return;
  if (!window.confirm("回档到编辑？该商品的所有已完成刊登副本将回到 copy_ready，历史自动化任务记录会保留。")) return;
  try {
    const result = await api(`/api/base-products/${encodeURIComponent(productId)}/rollback`, { method: "POST", body: "{}" });
    showOperationFeedback(`已回档：${result.reopened_listing_copy_ids?.length || 0} 个副本恢复为编辑中`, "success");
    await refreshAll();
    const refreshed = state.baseProducts.find((item) => item.id === productId);
    if (refreshed) renderBaseMaintenanceSummary(refreshed);
  } catch (error) {
    operationError(error, "回档失败");
  }
}

function renderBaseProductVideo(product) {
  const video = product?.video || { validation_status: "missing", validation_errors: ["video_missing"] };
  const preview = $("base-product-video-preview");
  const hasFile = Boolean(video.path || video.asset_id);
  preview.src = hasFile ? `/api/base-products/${encodeURIComponent(product.id)}/video/file` : "";
  preview.hidden = !hasFile;
  $("base-product-video-status").textContent = video.validation_status === "valid"
    ? `${video.file_name || "商品视频"} · ${video.duration_seconds || 0} 秒 · ${video.width || 0}×${video.height || 0}`
    : (video.validation_errors || ["video_missing"]).map((code) => VIDEO_ERROR_LABELS[code] || code).join("；");
}

async function uploadBaseProductVideo(file) {
  if (file.size > 30 * 1024 * 1024) throw new Error("视频文件大小不能超过 30 MB");
  const updated = await api(`/api/base-products/${encodeURIComponent(state.editingBaseProductId)}/video`, {
    method: "POST",
    body: JSON.stringify({ file_name: file.name, content_type: file.type || "video/mp4", content_base64: await fileAsBase64(file) }),
  });
  const index = state.baseProducts.findIndex((item) => item.id === updated.id);
  if (index >= 0) state.baseProducts[index] = updated;
  renderBaseProductVideo(updated);
  $("base-image-editor-result").textContent = updated.video?.validation_status === "valid" ? "视频已保存" : "视频规格不合规，自动化时将跳过上传";
}

function editBaseProductImages(productId) {
  openBaseProductMaintenance(productId);
}

function renderBaseImageOrder() {
  const list = $("base-image-order-list");
  if (!list) return;
  const product = state.baseProducts.find((item) => item.id === state.editingBaseProductId) || {};
  const byName = new Map(assetsForBaseProduct(product).map((asset) => [asset.file_name, asset]));
  list.innerHTML = "";
  if (!state.editingBaseProductImages.length) {
    list.innerHTML = `<p class="muted">这个基础商品还没有可排序产品图。</p>`;
    return;
  }
  state.editingBaseProductImages.forEach((fileName, index) => {
    const asset = byName.get(fileName) || {};
    const item = document.createElement("div");
    item.className = "image-order-item";
    item.draggable = true;
    item.dataset.imageOrderIndex = String(index);
    item.innerHTML = `
      <img src="${escapeHtml(assetImageUrl(asset, 480))}" alt="${escapeHtml(fileName)}" />
      <strong>${escapeHtml(String(index + 1))}</strong>
      <span>${escapeHtml(fileName)}</span>
      <button type="button" data-remove-base-product-image="${index}">移除</button>
    `;
    list.appendChild(item);
  });
}

function renderBaseImagePicker() {
  const target = $("base-image-picker");
  if (!target) return;
  const product = state.baseProducts.find((item) => item.id === state.editingBaseProductId) || {};
  const selected = new Set(state.editingBaseProductImages);
  const candidates = baseProductImageCandidates(product).filter((asset) => !selected.has(asset.file_name || ""));
  target.innerHTML = "";
  if (!candidates.length) {
    target.innerHTML = `<p class="muted">没有更多可选产品图。</p>`;
    return;
  }
  for (const asset of candidates) {
    const item = document.createElement("div");
    item.className = "image-pick-item";
    const disabled = state.editingBaseProductImages.length >= productImageLimit ? "disabled" : "";
    item.innerHTML = `
      <img src="${escapeHtml(assetImageUrl(asset, 480))}" alt="${escapeHtml(asset.file_name || "")}" />
      <span>${escapeHtml(asset.file_name || "")}</span>
      <small>${escapeHtml(asset.asset_type || "")}</small>
      <button type="button" data-add-base-product-image="${escapeHtml(asset.file_name || "")}" ${disabled}>加入</button>
    `;
    target.appendChild(item);
  }
}

function renderBaseVariantImageMap() {
  // 快捷色块先刷新（增删/改名都会走到这里），已添加的颜色置灰防止重复。
  renderBaseColorQuickPick();
  const target = $("base-variant-image-map");
  if (!target) return;
  const product = state.baseProducts.find((item) => item.id === state.editingBaseProductId) || {};
  const variantAssets = variantImageAssets(product);
  const colors = state.editingBaseColors || [];
  if (!colors.length) {
    closeVariantImagePicker();
    target.innerHTML = `<p class="muted">还没有颜色。可以手动添加颜色并选择对应图片。</p>`;
    return;
  }
  // 选择器改成自定义按钮 + 缩略图弹层：原生 <option> 放不了图片，只能看文件名。
  const pickerLabel = (selectedFileName) => selectedFileName || "未选择图片";
  target.innerHTML = colors.map((color, index) => {
    const fileName = state.editingBaseVariantImageMap[color] || "";
    const asset = variantAssets.find((item) => item.file_name === fileName);
    const preview = asset ? `<img src="${escapeHtml(assetImageUrl(asset, 160))}" alt="${escapeHtml(color)}" />` : `<div class="variant-image-empty"></div>`;
    return `
      <div class="variant-image-row" data-color-row-index="${index}" draggable="true" title="拖动调整顺序">
        <span class="drag-handle" aria-hidden="true">⋮⋮</span>
        <input data-base-color-index="${index}" value="${escapeHtml(color)}" placeholder="颜色名称" />
        ${preview}
        <button type="button" class="variant-image-trigger${fileName ? "" : " is-empty"}" data-variant-image-index="${index}" data-variant-image-trigger="${index}" aria-haspopup="listbox" aria-expanded="false" title="选择颜色图（带缩略图）：${escapeHtml(pickerLabel(fileName))}">
          <span class="variant-image-trigger-label">${escapeHtml(pickerLabel(fileName))}</span>
          <span class="variant-image-trigger-caret" aria-hidden="true"></span>
        </button>
        <button type="button" data-remove-base-color="${index}">删除</button>
      </div>
    `;
  }).join("");
  // 重渲染会换掉触发按钮：已经打开的弹层要么跟着关掉，要么按新行号重开。
  if (state.variantImagePickerIndex >= 0) {
    if (state.variantImagePickerIndex < colors.length) renderVariantImagePickerPopup(state.variantImagePickerIndex);
    else closeVariantImagePicker();
  }
}

// ===== 颜色图缩略图选择弹层（2026-10-27）=====
// 弹层挂在 body 上并用 fixed 定位：维护弹窗的 .maintenance-scroll 是滚动容器，
// 行内 absolute 弹层会被裁掉。
function variantImagePickerPopup() {
  let popup = document.getElementById("variant-image-picker-popup");
  if (!popup) {
    popup = document.createElement("div");
    popup.id = "variant-image-picker-popup";
    popup.className = "variant-image-picker-popup";
    popup.setAttribute("role", "listbox");
    popup.hidden = true;
    document.body.appendChild(popup);
  }
  return popup;
}

function variantImagePickerOption(name, label, asset, selectedName) {
  const selected = name === selectedName;
  const thumb = asset
    ? `<img class="variant-image-option-thumb" src="${escapeHtml(assetImageUrl(asset, 160))}" alt="" loading="lazy" />`
    : `<span class="variant-image-option-thumb variant-image-option-thumb--none" aria-hidden="true">✕</span>`;
  return `
    <button type="button" class="variant-image-option${selected ? " is-selected" : ""}" role="option" aria-selected="${selected}" data-variant-image-pick="${escapeHtml(name)}">
      ${thumb}
      <span class="variant-image-option-name" title="${escapeHtml(label)}">${escapeHtml(label)}</span>
    </button>
  `;
}

function renderVariantImagePickerPopup(index) {
  const popup = variantImagePickerPopup();
  const product = state.baseProducts.find((item) => item.id === state.editingBaseProductId) || {};
  const variantAssets = variantImageAssets(product);
  const color = state.editingBaseColors[index] || "";
  const selectedName = state.editingBaseVariantImageMap[color] || "";
  popup.dataset.variantImageRow = String(index);
  const items = [
    variantImagePickerOption("", "未选择图片", null, selectedName),
    ...variantAssets.map((asset) => variantImagePickerOption(asset.file_name || "", asset.file_name || "", asset, selectedName)),
  ];
  const emptyHint = variantAssets.length
    ? ""
    : `<p class="variant-image-picker-empty muted">这个商品还没有可选的颜色图素材，先在上方「产品图」里加入图片。</p>`;
  popup.innerHTML = `${emptyHint}<div class="variant-image-option-grid">${items.join("")}</div>`;
}

function positionVariantImagePicker() {
  const popup = document.getElementById("variant-image-picker-popup");
  if (!popup || popup.hidden) return;
  const index = state.variantImagePickerIndex;
  const trigger = document.querySelector(`#base-variant-image-map [data-variant-image-trigger="${index}"]`);
  if (!(trigger instanceof HTMLElement)) return;
  const rect = trigger.getBoundingClientRect();
  const margin = 12;
  const width = Math.min(Math.max(rect.width, 340), window.innerWidth - margin * 2);
  popup.style.width = `${width}px`;
  popup.style.left = `${Math.round(Math.max(margin, Math.min(rect.left, window.innerWidth - width - margin)))}px`;
  const contentHeight = Math.min(popup.scrollHeight, 360, window.innerHeight - margin * 2);
  const spaceBelow = window.innerHeight - margin - (rect.bottom + 6);
  const spaceAbove = rect.top - 6 - margin;
  // 下面够放就往下弹；否则往上弹，并按上方剩余空间收高度，别盖住触发按钮。
  const placeBelow = spaceBelow >= Math.min(contentHeight, 200) || spaceBelow >= spaceAbove;
  const height = placeBelow
    ? Math.min(contentHeight, Math.max(140, spaceBelow))
    : Math.min(contentHeight, Math.max(140, spaceAbove));
  popup.style.maxHeight = `${Math.round(height)}px`;
  const top = placeBelow ? rect.bottom + 6 : Math.max(margin, rect.top - 6 - height);
  popup.style.top = `${Math.round(top)}px`;
}

function openVariantImagePicker(index) {
  if (!state.editingBaseColors[index]) return;
  state.variantImagePickerIndex = index;
  const popup = variantImagePickerPopup();
  renderVariantImagePickerPopup(index);
  popup.hidden = false;
  positionVariantImagePicker();
  document.querySelectorAll("#base-variant-image-map [data-variant-image-trigger]").forEach((node) => {
    node.setAttribute("aria-expanded", String(Number(node.dataset.variantImageTrigger) === index));
  });
}

function closeVariantImagePicker() {
  const popup = document.getElementById("variant-image-picker-popup");
  if (popup) popup.hidden = true;
  state.variantImagePickerIndex = -1;
  document.querySelectorAll("#base-variant-image-map [data-variant-image-trigger]").forEach((node) => {
    node.setAttribute("aria-expanded", "false");
  });
}

function toggleVariantImagePicker(index) {
  if (state.variantImagePickerIndex === index) closeVariantImagePicker();
  else openVariantImagePicker(index);
}

function selectBaseVariantImage(index, fileName) {
  syncBaseColorInputs();
  const color = state.editingBaseColors[index] || "";
  if (!color) return;
  if (fileName) state.editingBaseVariantImageMap[color] = fileName;
  else delete state.editingBaseVariantImageMap[color];
  closeVariantImagePicker();
  // 换了颜色图 = 这个颜色在产品图顺序里换了位置：联动打开时按图的位置重排颜色
  // （清空选择则变成「未匹配」，联动下排到最后）。
  applyBaseColorFollowFromImages();
  renderBaseVariantImageMap();
}

// 弹层是全局单例：点空白、按 Esc、滚动/改窗口大小都收起来。
document.addEventListener("click", (event) => {
  if (state.variantImagePickerIndex < 0) return;
  const target = event.target;
  if (!(target instanceof Element)) return;
  if (target.closest("#variant-image-picker-popup")) return;
  if (target.closest("#base-variant-image-map [data-variant-image-trigger]")) return;
  closeVariantImagePicker();
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || state.variantImagePickerIndex < 0) return;
  closeVariantImagePicker();
});

document.addEventListener("scroll", (event) => {
  if (state.variantImagePickerIndex < 0) return;
  const target = event.target;
  if (target instanceof Element && target.closest("#variant-image-picker-popup")) return;
  closeVariantImagePicker();
}, true);

// 窗口尺寸变了只需重新定位（fixed 弹层位置会过期），不必直接关掉。
window.addEventListener("resize", () => {
  if (state.variantImagePickerIndex >= 0) positionVariantImagePicker();
});


function moveBaseImage(fromIndex, toIndex) {
  if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0) return;
  const rows = state.editingBaseProductImages;
  if (!rows[fromIndex] || !rows[toIndex]) return;
  const [item] = rows.splice(fromIndex, 1);
  rows.splice(toIndex, 0, item);
  renderBaseImageOrder();
  renderBaseImagePicker();
  // 产品图是顺序的主：挪完让颜色顺序跟着走。
  applyBaseColorFollowFromImages();
}

function addBaseProductImage(fileName) {
  const name = String(fileName || "").trim();
  if (!name || state.editingBaseProductImages.includes(name)) return;
  const result = $("base-image-editor-result");
  if (state.editingBaseProductImages.length >= productImageLimit) {
    if (result) result.textContent = `产品图最多选择 ${productImageLimit} 张。`;
    return;
  }
  state.editingBaseProductImages.push(name);
  renderBaseImageOrder();
  renderBaseImagePicker();
  applyBaseColorFollowFromImages();
  // 可选图片集合变了，颜色图下拉同步（联动早退时也要刷）。
  renderBaseVariantImageMap();
}

function removeBaseProductImage(index) {
  if (index < 0 || index >= state.editingBaseProductImages.length) return;
  state.editingBaseProductImages.splice(index, 1);
  renderBaseImageOrder();
  renderBaseImagePicker();
  // 被移掉的可能正好是某个颜色的颜色图：那张颜色就变成「未匹配」，排到最后。
  applyBaseColorFollowFromImages();
  // 可选图片集合变了，颜色图下拉同步（联动早退时也要刷）。
  renderBaseVariantImageMap();
}

// 操作员自上传商品图：文件登记成该商品的素材（origin=manual_upload），并追加到
// 产品图末尾。后端已经落库，这里只把新图补进编辑态，不覆盖尚未保存的排序/移除。
async function uploadBaseProductProductImages(files) {
  const productId = state.editingBaseProductId;
  const picked = [...(files || [])];
  const result = $("base-product-image-upload-result");
  if (!productId || !picked.length) return;
  const uploaded = [];
  const failures = [];
  for (let index = 0; index < picked.length; index += 1) {
    const file = picked[index];
    if (file.size > productImageUploadMaxBytes) {
      failures.push(`${file.name}（${formatStorageBytes(file.size)}，超过 10 MiB 上限）`);
      continue;
    }
    if (result) result.textContent = `正在上传 (${index + 1}/${picked.length})：${file.name}`;
    try {
      const response = await api(`/api/base-products/${encodeURIComponent(productId)}/product-images`, {
        method: "POST",
        body: JSON.stringify({
          file_name: file.name,
          content_type: file.type || "image/png",
          content_base64: await fileAsBase64(file),
        }),
      });
      uploaded.push({ name: response?.asset?.file_name || file.name, product: response?.product });
    } catch (error) {
      const message = String(error?.message || "").trim();
      // fetch 层失败（请求体过大被断开等）只会给出 Failed to fetch，换成能看懂的说法。
      const detail = /failed to fetch|networkerror|load failed/i.test(message)
        ? "网络中断（图片可能过大或服务未响应）"
        : (message || "上传失败");
      failures.push(`${file.name}（${detail}）`);
    }
  }
  await loadAssets();
  for (const item of uploaded) {
    if (item.product) {
      const index = state.baseProducts.findIndex((entry) => entry.id === item.product.id);
      if (index >= 0) state.baseProducts[index] = item.product;
    }
    if (item.name && !state.editingBaseProductImages.includes(item.name)) {
      state.editingBaseProductImages.push(item.name);
    }
  }
  renderBaseImageOrder();
  renderBaseImagePicker();
  applyBaseColorFollowFromImages();
  // 新图要立刻出现在颜色图下拉里：联动函数在「顺序没变/开关关」时会早退，
  // 不刷颜色图区，上传的图就得等保存重开才能选（现场反馈的坑）。
  renderBaseVariantImageMap();
  if (!result) return;
  const parts = [];
  if (uploaded.length) parts.push(`已上传 ${uploaded.length} 张并加到产品图末尾：${uploaded.map((item) => item.name).join("、")}`);
  if (failures.length) parts.push(`失败 ${failures.length} 张：${failures.join("、")}`);
  result.textContent = `${parts.join("；")}。点「保存维护内容」写入商品。`;
  result.classList.toggle("upload-error", failures.length > 0 && !uploaded.length);
}

// 商品维度的类目必填属性：选了产品类目后，拉取该类目已发布方案的属性字段
// 渲染出来（值种子 = 商品上已存的 attribute_values）；没有方案时提示——
// 自动化执行时会现场读马帮页面的必填项并调 AI 自动填。
async function loadBaseCategoryAttributeFields() {
  const target = $("base-maintenance-category-fields");
  const status = $("base-maintenance-ai-attributes-status");
  if (!target) return;
  const category = state.editingBaseProductCategory || {};
  const categoryId = String(category.category_id || "").trim();
  if (!categoryId) {
    state.baseCategoryAttributeRequestId += 1;
    target.innerHTML = "";
    if (status) status.textContent = "选类目后自动列出必填项；AI 按商品信息推荐每个属性的值。";
    return;
  }
  const seed = state.editingBaseProductAttributeValues || {};
  // 快速切换类目/模板时，慢返回的旧请求不能覆盖新类目的字段与回填值。
  const requestId = ++state.baseCategoryAttributeRequestId;
  try {
    const result = await api(`/api/category-profiles/for-category?category_id=${encodeURIComponent(categoryId)}&platform=shopee${category.country ? `&country=${encodeURIComponent(category.country)}` : ""}`);
    if (requestId !== state.baseCategoryAttributeRequestId) return;
    if (!result.found) {
      target.innerHTML = `<p class="muted">该类目还没有已发布的必填项规则（可在店铺模板的类目选择器里采集）。自动化执行时会现场读取马帮页面的必填项并调 AI 自动填写。</p>`;
      if (status) status.textContent = "该类目还没有已发布的必填项规则；自动化执行时会现场读取马帮页面必填项。";
      return;
    }
    renderStoreTemplateCategoryFields(result.fields || [], seed, target);
    if (status) status.textContent = `已按类目规则列出 ${(result.fields || []).length} 项属性；点「AI 推荐必填项」自动填空项，保存维护内容后生效。`;
  } catch (error) {
    if (requestId !== state.baseCategoryAttributeRequestId) return;
    target.innerHTML = "";
    if (status) status.textContent = `读取类目属性失败：${error.message || error}`;
  }
}

function collectBaseCategoryAttributeValues() {
  const values = {};
  document.querySelectorAll("#base-maintenance-category-fields [data-category-field-input]").forEach((control) => {
    const fieldId = control.dataset.categoryFieldInput;
    if (!fieldId) return;
    if (control instanceof HTMLInputElement && control.type === "checkbox") values[fieldId] = control.checked;
    else if (control instanceof HTMLSelectElement && control.multiple) {
      const selected = [...control.selectedOptions]
        .map((item) => item.value)
        .filter((item) => item && item !== CUSTOM_ATTRIBUTE_OPTION);
      if (selected.length) values[fieldId] = selected;
    } else if (control.value !== "" && control.value !== CUSTOM_ATTRIBUTE_OPTION) values[fieldId] = control.value;
  });
  return values;
}

// 商品维护的 AI 推荐必填项：与模板编辑器同款端点，只填空属性，值随「保存维护
// 内容」一起写入商品，复制到店铺副本时随商品类目带上。
async function recommendBaseAttributesWithAI() {
  const button = $("base-maintenance-ai-attributes-btn");
  const status = $("base-maintenance-ai-attributes-status");
  const container = $("base-maintenance-category-fields");
  const controls = [...(container?.querySelectorAll("[data-category-field-input]") || [])];
  if (!controls.length) {
    showOperationFeedback("当前类目没有可填的属性规则：请先采集该类目的必填项，或依赖自动化现场 AI 判断", "error");
    return;
  }
  const targets = [];
  for (const control of controls) {
    const fieldId = control.dataset.categoryFieldInput;
    if (!fieldId) continue;
    const wrapper = control.closest("label.category-field");
    const spanText = (wrapper?.querySelector("span")?.textContent || "").trim();
    const label = spanText.replace(/\s*\*\s*$/, "").trim();
    if (control instanceof HTMLInputElement && control.type === "checkbox") continue;
    if (control instanceof HTMLSelectElement) {
      const options = [...control.options]
        .filter((option) => option.value && option.value !== CUSTOM_ATTRIBUTE_OPTION)
        .map((option) => ({ value: option.value, label: option.textContent || option.value }));
      const filled = control.multiple ? control.selectedOptions.length > 0 : control.value !== "";
      if (filled || !options.length) continue;
      targets.push({ field_id: fieldId, label, required: spanText !== label, type: control.multiple ? "multi_select" : "single_select", options });
    } else if (control instanceof HTMLInputElement && control.value.trim() === "") {
      targets.push({ field_id: fieldId, label, required: spanText !== label, type: "text", options: [] });
    }
  }
  if (!targets.length) {
    showOperationFeedback("没有空着的属性需要推荐", "info");
    return;
  }
  button.disabled = true;
  if (status) status.textContent = "AI 正在按商品信息推荐属性值…";
  try {
    const result = await api("/api/ai/attribute-recommendations", {
      method: "POST",
      body: JSON.stringify({
        fields: targets,
        context: {
          product_name: $("base-maintenance-name")?.value || "",
          description: $("base-maintenance-description")?.value || "",
          category_path: $("base-maintenance-category-path")?.value || "",
          product_line: state.editingBaseProduct?.category || "",
        },
      }),
    });
    const values = result.values || {};
    let applied = 0;
    for (const control of controls) {
      const fieldId = control.dataset.categoryFieldInput;
      if (!fieldId || !(fieldId in values)) continue;
      const value = values[fieldId];
      if (control instanceof HTMLSelectElement && control.multiple) {
        const list = Array.isArray(value) ? value.map(String) : [String(value)];
        [...control.options].forEach((option) => { option.selected = list.includes(option.value); });
        applied += 1;
      } else if (control instanceof HTMLSelectElement) {
        if ([...control.options].some((option) => option.value === String(value))) {
          control.value = String(value);
          applied += 1;
        }
      } else if (control instanceof HTMLInputElement) {
        control.value = String(value);
        applied += 1;
      }
    }
    const skipped = (result.skipped || []).length;
    if (status) status.textContent = `AI 已推荐填充 ${applied} 项属性${skipped ? `，${skipped} 项未匹配到选项` : ""}；保存维护内容后写入商品。`;
    showOperationFeedback(`AI 必填项推荐完成：填充 ${applied} 项`, "success");
  } catch (error) {
    const message = error.message || String(error);
    if (status) status.textContent = `AI 推荐失败：${message}`;
    showOperationFeedback(`AI 推荐失败：${message}`, "error");
  } finally {
    button.disabled = false;
  }
}

// ===== 基础商品「保存维护内容」队列 =====
// 点保存时只同步取一份表单快照，写入放到后台串行跑：
// ① 点完立刻能继续操作（原来保存后要重拉副本 4.5MB + 自动化任务 2.9MB 再整表重画，
//    这就是点击后卡一下的原因；现在只更新本地这一条商品并重画卡片）；
// ② 弹窗关掉了也照常保存完，但保存成功后绝不再把弹窗弹回来——操作员可能已经在看
//    别的商品了（这正是「点了保存、关掉商品，保存成功后又弹出来」的来源）。
const baseProductSaveQueue = [];        // [{ productId, label, payload }]
const baseProductSaveState = new Map(); // productId -> { status: "queued"|"saving"|"saved"|"failed", message }
let baseProductSaveDraining = false;
let baseProductRefreshTimer = 0;

function baseProductSaveLabel(productId) {
  const product = state.baseProducts.find((item) => item.id === productId);
  return String(product?.main_sku || product?.product_name_cn || product?.base_name || "").trim() || "基础商品";
}

function baseProductSaveBusy(productId) {
  const status = baseProductSaveState.get(productId);
  return status?.status === "queued" || status?.status === "saving";
}

function renderBaseProductSaveButton() {
  const button = $("save-base-image-order-btn");
  if (!button) return;
  const busy = baseProductSaveBusy(state.editingBaseProductId);
  button.disabled = busy;
  button.textContent = busy
    ? (baseProductSaveQueue.length ? `保存中…（待存 ${baseProductSaveQueue.length}）` : "保存中…")
    : "保存维护内容";
}

function setBaseProductSaveStatus(productId, status, message = "") {
  if (!productId) return;
  baseProductSaveState.set(productId, { status, message });
  if (state.editingBaseProductId !== productId) return;
  const result = $("base-image-editor-result");
  if (result && message) result.textContent = message;
  renderBaseProductSaveButton();
}

function collectBaseProductMaintenancePayload() {
  syncBaseColorInputs();
  const name = $("base-maintenance-name").value.trim();
  const recognizedColors = normalizeBaseColors(state.editingBaseColors);
  const variantImageMap = {};
  for (const color of recognizedColors) {
    const fileName = state.editingBaseVariantImageMap[color] || "";
    if (fileName) variantImageMap[color] = fileName;
  }
  // 类目没有已发布规则（或规则还在加载）时表单里没有属性字段：此时不要把商品上
  // 已存的类目属性值写成空——套了别的类目模板再保存，不能把之前保存的值清掉。
  const categoryFieldControls = document.querySelectorAll("#base-maintenance-category-fields [data-category-field-input]");
  const attributeValues = categoryFieldControls.length
    ? collectBaseCategoryAttributeValues()
    : { ...(state.editingBaseProductAttributeValues || {}) };
  return {
    product_name_cn: name,
    base_name: name,
    main_sku: $("base-maintenance-main-sku").value.trim(),
    status: $("base-maintenance-status").value.trim(),
    listing_title: $("base-maintenance-title-input").value.trim(),
    ...(($("base-maintenance-audience")?.value || "") ? { title_audience: $("base-maintenance-audience").value } : {}),
    description: $("base-maintenance-description").value.trim(),
    product_images: [...state.editingBaseProductImages],
    main_image: state.editingBaseProductImages[0] || "",
    recognized_colors: recognizedColors,
    variant_image_map: variantImageMap,
    color_order_follows_images: baseColorFollowEnabled(),
    price_tiers: saveBasePriceColorRowsToTiers(),
    size_chart_image: state.editingBaseSizeChartImage || "",
    recognized_sizes: [...(state.editingBaseRecognizedSizes || [])],
    selected_sizes: [...(state.editingBaseSelectedSizes || [])],
    attribute_values: attributeValues,
    product_category: state.editingBaseProductCategory || null,
  };
}

function enqueueBaseProductSave(task) {
  const existing = baseProductSaveQueue.findIndex((item) => item.productId === task.productId);
  // 同一商品连点只保留最新快照，避免把旧表单内容后写上去。
  if (existing >= 0) baseProductSaveQueue[existing] = task;
  else baseProductSaveQueue.push(task);
  baseProductSaveState.set(task.productId, { status: "queued", message: "" });
  renderBaseProductSaveButton();
  drainBaseProductSaveQueue().catch(() => {});
}

async function drainBaseProductSaveQueue() {
  if (baseProductSaveDraining) return;
  baseProductSaveDraining = true;
  try {
    while (baseProductSaveQueue.length) {
      const task = baseProductSaveQueue.shift();
      setBaseProductSaveStatus(task.productId, "saving", "正在保存维护内容…");
      renderBaseProductSaveButton();
      try {
        const product = await api(`/api/base-products/${encodeURIComponent(task.productId)}`, {
          method: "PUT",
          body: JSON.stringify(task.payload),
        });
        applySavedBaseProduct(task, product);
      } catch (error) {
        const message = error?.message || String(error);
        setBaseProductSaveStatus(task.productId, "failed", `保存失败：${message}`);
        showOperationFeedback(`「${task.label}」保存失败：${message}`, "error");
      }
    }
  } finally {
    baseProductSaveDraining = false;
    renderBaseProductSaveButton();
    scheduleBaseProductRefresh();
  }
}

function applySavedBaseProduct(task, product) {
  if (!product?.id) return;
  const index = state.baseProducts.findIndex((item) => item.id === product.id);
  const before = index >= 0 ? state.baseProducts[index] : null;
  // PUT 只回商品本体：副本数/生命周期/自动化图标是列表接口算出来的，先沿用旧值，
  // 稍后由后台的一次列表刷新校正（不要在这里整表重拉）。
  const merged = before ? { ...before, ...product } : product;
  if (index >= 0) state.baseProducts[index] = merged;
  else state.baseProducts.unshift(merged);
  const summary = `已保存 ${product.product_images?.length || 0} 张产品图、${product.recognized_colors?.length || 0} 个颜色。`;
  baseProductSaveState.set(product.id, { status: "saved", message: summary });
  renderBaseProducts();
  if (state.editingBaseProductId === product.id) {
    const result = $("base-image-editor-result");
    if (result) result.textContent = summary;
    renderBaseMaintenanceSummary(merged);
    renderBaseProductSaveButton();
  }
  // 弹窗已关掉或已切到别的商品：只提示一句，不重开弹窗、不动正在编辑的内容。
  showOperationFeedback(`「${task.label}」${summary}`, "success");
  scheduleBaseProductRefresh();
}

// 后台补刷商品列表（约 0.9MB）：PUT 只回商品本体，副本数/生命周期阶段/列表排序
// 这类派生字段要由列表接口校正。合并成一次，连点保存也只拉一遍。
function scheduleBaseProductRefresh() {
  window.clearTimeout(baseProductRefreshTimer);
  baseProductRefreshTimer = window.setTimeout(() => {
    loadBaseProducts().catch(() => {});
  }, 1200);
}

function saveBaseImageOrder() {
  const productId = state.editingBaseProductId;
  if (!productId) return;
  if (baseProductSaveQueue.some((item) => item.productId === productId)) return;
  // 快照要脱离编辑态：入队后操作员还会继续改（或关掉弹窗），不能写进去后改的内容。
  const payload = JSON.parse(JSON.stringify(collectBaseProductMaintenancePayload()));
  const task = { productId, label: baseProductSaveLabel(productId), payload };
  const result = $("base-image-editor-result");
  if (result) result.textContent = "已加入保存队列，正在写入…";
  enqueueBaseProductSave(task);
}

// AI 识别失败后的重试：重新跑一遍视觉生成（标题/详情/颜色），复用导入时的开关配置。
async function retryBaseProductAiListing() {
  const productId = state.editingBaseProductId || state.selectedBaseProductId;
  if (!productId) return;
  const retryBtn = $("base-maintenance-ai-retry");
  const aiErrorEl = $("base-maintenance-ai-error");
  if (retryBtn) { retryBtn.disabled = true; retryBtn.textContent = "AI 识别中…"; }
  if (aiErrorEl) { aiErrorEl.textContent = "AI 识别中：正在重新生成标题/详情/颜色…"; aiErrorEl.hidden = false; }
  try {
    const product = await api(`/api/base-products/${encodeURIComponent(productId)}/regenerate-listing`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await Promise.all([loadBaseProducts(), loadAssets()]);
    const nextError = String(product?.ai_generation_error || "").trim();
    if (aiErrorEl) {
      aiErrorEl.textContent = nextError ? `AI 识别失败：${nextError}` : "AI 识别完成：标题/详情已更新";
      aiErrorEl.hidden = false;
      if (!nextError) setTimeout(() => { aiErrorEl.hidden = true; }, 4000);
    }
    if (product?.listing_title) $("base-maintenance-title-input").value = product.listing_title;
    if (product?.description) $("base-maintenance-description").value = product.description;
    renderBaseMaintenanceSummary(product);
    renderBaseProducts();
  } catch (error) {
    if (aiErrorEl) { aiErrorEl.textContent = `AI 识别失败：${error.message || error}`; aiErrorEl.hidden = false; }
    showOperationFeedback(error.message || "AI 识别重试失败", "error");
  } finally {
    if (retryBtn) { retryBtn.disabled = false; retryBtn.textContent = "重试 AI 识别"; }
  }
}

// ===== 基础商品尺码表：从产品图选择 + AI 识别尺码 =====

function baseSizeChartCandidates() {
  // 尺码表候选图 = 该商品全部产品图（复用 productImageAssets 逻辑的文件名）。
  const product = state.baseProducts.find((item) => item.id === state.editingBaseProductId) || {};
  const assets = productImageAssets(product);
  const names = (state.editingBaseProductImages || []).slice();
  assets.forEach((asset) => {
    const name = asset.file_name || "";
    if (!names.includes(name)) names.push(name);
  });
  return names;
}

function renderBaseSizeChartSection() {
  const select = $("base-size-chart-image");
  if (!select) return;
  const previous = select.value;
  const current = state.editingBaseSizeChartImage || "";
  select.innerHTML = `<option value="">（从产品图中选择一张尺码表）</option>` +
    baseSizeChartCandidates().map((name) => `<option value="${escapeHtml(name)}" ${name === current ? "selected" : ""}>${escapeHtml(name)}</option>`).join("");
  if (previous && [...select.options].some((o) => o.value === previous)) select.value = previous;
  // 预览
  const preview = $("base-size-chart-preview");
  if (preview) {
    if (!current) {
      preview.innerHTML = `<p class="muted">尚未选择尺码表图</p>`;
    } else {
      const product = state.baseProducts.find((item) => item.id === state.editingBaseProductId) || {};
      const assets = productImageAssets(product);
      const asset = assets.find((a) => a.file_name === current);
      preview.innerHTML = asset
        ? `<img src="${escapeHtml(assetImageUrl(asset, 640))}" alt="${escapeHtml(current)}" />`
        : `<p class="muted">${escapeHtml(current)}</p>`;
    }
  }
  renderBaseRecognizedSizes();
}

function renderBaseRecognizedSizes() {
  const root = $("base-recognized-sizes");
  if (!root) return;
  const sizes = state.editingBaseRecognizedSizes || [];
  if (!sizes.length) {
    root.innerHTML = `<p class="muted">尚未识别尺码。选择尺码表图后点「AI 识别尺码」，或手动添加。</p>`;
    return;
  }
  const selected = state.editingBaseSelectedSizes || [];
  root.innerHTML = sizes.map((size) => {
    const checked = selected.some((s) => s.toLowerCase() === String(size).toLowerCase());
    // 固定选项：勾选 = 该尺码会上架；再次点击取消 = 不选。不可从列表移除。
    return `<label class="size-chip size-chip--pick ${checked ? "size-chip--selected" : ""}" title="${checked ? "已选择，点击取消" : "点击选择"}">
      <input type="checkbox" data-base-size-check="${escapeHtml(size)}" ${checked ? "checked" : ""} />
      <span class="size-chip-check" aria-hidden="true">${checked ? "✓" : ""}</span>
      <span>${escapeHtml(size)}</span>
    </label>`;
  }).join("");
}

function addBaseRecognizedSize(size) {
  const value = String(size || "").trim();
  if (!value) return;
  const list = state.editingBaseRecognizedSizes || [];
  if (!list.some((s) => s.toLowerCase() === value.toLowerCase())) list.push(value);
  // 手动添加默认勾选
  if (!(state.editingBaseSelectedSizes || []).some((s) => s.toLowerCase() === value.toLowerCase())) {
    state.editingBaseSelectedSizes = [...(state.editingBaseSelectedSizes || []), value];
  }
  state.editingBaseRecognizedSizes = list;
  renderBaseRecognizedSizes();
}

function toggleBaseSizeChecked(size) {
  const value = String(size || "").trim();
  if (!value) return;
  const selected = [...(state.editingBaseSelectedSizes || [])];
  const idx = selected.findIndex((s) => s.toLowerCase() === value.toLowerCase());
  if (idx >= 0) selected.splice(idx, 1);
  else selected.push(value);
  state.editingBaseSelectedSizes = selected;
  renderBaseRecognizedSizes();
}

function selectAllBaseSizes() {
  state.editingBaseSelectedSizes = [...(state.editingBaseRecognizedSizes || [])];
  renderBaseRecognizedSizes();
}

function clearBaseSizes() {
  state.editingBaseSelectedSizes = [];
  renderBaseRecognizedSizes();
}

// 常用默认尺码（服装 XXS–XXXL）：商品还没识别出尺码时按这套兜底，
// 也可以随时点「常用 XXS–XXXL」补齐并勾选。
const BASE_COMMON_SIZES = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL"];

function applyBaseCommonSizes() {
  for (const size of BASE_COMMON_SIZES) addBaseRecognizedSize(size);
  // 常用码全部勾选（会上架）；其它尺码保持原有勾选状态不动。
  const selected = [...(state.editingBaseSelectedSizes || [])];
  for (const size of BASE_COMMON_SIZES) {
    if (!selected.some((s) => s.toLowerCase() === size.toLowerCase())) selected.push(size);
  }
  state.editingBaseSelectedSizes = selected;
  renderBaseRecognizedSizes();
  showOperationFeedback("已应用常用尺码 XXS–XXXL（全部勾选），保存后生效", "success");
}

// ===== 尺码模板（localStorage，命名勾选组合快速复用） =====
const BASE_SIZE_TEMPLATES_KEY = "ai_listing.base_size_templates";

function loadBaseSizeTemplates() {
  try {
    const raw = JSON.parse(window.localStorage.getItem(BASE_SIZE_TEMPLATES_KEY) || "{}");
    return (raw && typeof raw === "object" && !Array.isArray(raw)) ? raw : {};
  } catch (_) {
    return {};
  }
}

function saveBaseSizeTemplates(map) {
  try {
    window.localStorage.setItem(BASE_SIZE_TEMPLATES_KEY, JSON.stringify(map || {}));
  } catch (_) {
    // 存储不可用时仅会话内生效
  }
}

function renderBaseSizeTemplateOptions() {
  const select = $("base-size-template-select");
  if (!select) return;
  const previous = select.value;
  const templates = loadBaseSizeTemplates();
  select.innerHTML = `<option value="">（选模板快速勾选）</option>` +
    Object.keys(templates).map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}（${escapeHtml(templates[name].join("/"))}）</option>`).join("");
  if (previous && [...select.options].some((o) => o.value === previous)) select.value = previous;
}

function saveCurrentSizesAsTemplate() {
  const selected = state.editingBaseSelectedSizes || [];
  if (!selected.length) { showOperationFeedback("请先勾选至少一个尺码再存为模板", "error"); return; }
  const name = String(window.prompt("模板名称（例如：女装均码 S-XL）：", "") || "").trim();
  if (!name) return;
  const templates = loadBaseSizeTemplates();
  templates[name] = [...selected];
  saveBaseSizeTemplates(templates);
  renderBaseSizeTemplateOptions();
  const select = $("base-size-template-select");
  if (select) select.value = name;
  showOperationFeedback(`已保存尺码模板「${name}」`, "success");
}

function applyBaseSizeTemplate(name) {
  const templates = loadBaseSizeTemplates();
  const sizes = templates[name];
  if (!sizes) return;
  state.editingBaseSelectedSizes = [...sizes];
  renderBaseRecognizedSizes();
}

function deleteBaseSizeTemplate(name) {
  const templates = loadBaseSizeTemplates();
  if (!templates[name]) return;
  delete templates[name];
  saveBaseSizeTemplates(templates);
  renderBaseSizeTemplateOptions();
  showOperationFeedback(`已删除尺码模板「${name}」`, "success");
}

async function recognizeBaseSizeChart() {
  const image = state.editingBaseSizeChartImage || "";
  const status = $("base-recognize-sizes-status");
  const btn = $("base-recognize-sizes");
  if (!image) {
    if (status) { status.textContent = "请先选择尺码表图"; }
    return;
  }
  if (btn) { btn.disabled = true; btn.classList.add("is-loading"); }
  if (status) status.textContent = "AI 识别尺码中…";
  try {
    const data = await api(`/api/base-products/${encodeURIComponent(state.editingBaseProductId)}/recognize-sizes`, {
      method: "POST",
      body: JSON.stringify({ size_chart_image: image }),
    });
    state.editingBaseRecognizedSizes = data.sizes || [];
    renderBaseRecognizedSizes();
    if (status) status.textContent = `识别到 ${(data.sizes || []).length} 个尺码：${(data.sizes || []).join("、")}`;
    showOperationFeedback("尺码识别成功，保存维护内容后生效", "success");
  } catch (error) {
    if (status) status.textContent = "";
    showOperationFeedback(error.message || "尺码识别失败", "error");
  } finally {
    if (btn) { btn.disabled = false; btn.classList.remove("is-loading"); }
  }
}

// ===== 基础商品站点价格（按颜色/款式分行快捷填写，最终编排在副本编辑器） =====

const BASE_PRICES_COLLAPSED_KEY = "ai_listing.base_maintenance_prices_collapsed";

function basePriceCollapsed() {
  try {
    return window.localStorage.getItem(BASE_PRICES_COLLAPSED_KEY) === "1";
  } catch (_) {
    return false;
  }
}

function applyBasePriceCollapsed() {
  const wrap = $("base-price-collapsible");
  const btn = $("base-price-collapse-btn");
  if (!wrap) return;
  const collapsed = basePriceCollapsed();
  wrap.hidden = collapsed;
  if (btn) {
    btn.textContent = collapsed ? "展开 ▼" : "收起 ▲";
    btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
  }
}

function toggleBasePriceCollapsed() {
  const wrap = $("base-price-collapsible");
  if (!wrap) return;
  const next = !wrap.hidden;
  wrap.hidden = next;
  try {
    window.localStorage.setItem(BASE_PRICES_COLLAPSED_KEY, next ? "1" : "0");
  } catch (_) {
    // 存储不可用时仅本次会话生效。
  }
  const btn = $("base-price-collapse-btn");
  if (btn) {
    btn.textContent = next ? "展开 ▼" : "收起 ▲";
    btn.setAttribute("aria-expanded", next ? "false" : "true");
  }
}

function basePriceTierSites() {
  // 站点列 = 全量 8 国，按全局识别顺序排列。
  return cnscSiteOrder();
}

async function loadBasePriceColors(productId) {
  try {
    const data = await api(`/api/base-products/${encodeURIComponent(productId)}/prices`);
    state.basePriceColorRows = (data.tiers || []).map((tier) => ({
      color: String((tier.colors || [])[0] || tier.id || "").trim(),
      cost_price: tier.cost_price ?? "",
      site_prices: tier.site_prices || {},
    })).filter((row) => row.color);
  } catch (error) {
    state.basePriceColorRows = [];
  }
  renderBasePriceColors();
}

function basePriceColorUniverse() {
  // 价格行 = 颜色图区编辑态颜色（顺序一致）；颜色图增删/排序后此处自动跟随。
  return [...(state.editingBaseColors || []).map((c) => String(c || "").trim()).filter(Boolean)];
}

function renderBasePriceColors() {
  const table = $("base-price-color-table");
  const tbody = table?.querySelector("tbody");
  if (!tbody) return;
  const colors = basePriceColorUniverse();
  if (!colors.length) {
    tbody.innerHTML = `<tr><td colspan="3"><p class="muted">商品暂无颜色/款式，请在上方「颜色图」区添加。价格行随颜色图自动增删与排序。</p></td></tr>`;
    return;
  }
  const codes = basePriceTierSites();
  const rows = state.basePriceColorRows || [];
  const colorKey = (value) => String(value || "").trim().toLowerCase();
  tbody.innerHTML = colors.map((color) => {
    const existing = rows.find((row) => colorKey(row.color) === colorKey(color));
    const cost = existing?.cost_price ?? "";
    const site = existing?.site_prices || {};
    const matrix = codes.map((code) => {
      const prices = site[code] || {};
      // 售价可手填（尊重）；留空则由目标店铺模板活动折扣（%OFF）在复制时自动计算。
      return `<div class="listing-copy-cnsc-site-price"><span>${escapeHtml(code)}</span><input data-base-price-original="${escapeHtml(color)}" data-code="${escapeHtml(code)}" type="number" min="0" step="0.01" value="${escapeHtml(prices.original_price ?? "")}" placeholder="原价" /><input data-base-price-sale="${escapeHtml(color)}" data-code="${escapeHtml(code)}" type="number" min="0" step="0.01" value="${escapeHtml(prices.sale_price ?? "")}" placeholder="售价" /></div>`;
    }).join("");
    return `<tr><td><strong>${escapeHtml(color)}</strong></td><td><input data-base-price-cost="${escapeHtml(color)}" type="number" min="0" step="0.01" value="${escapeHtml(String(cost))}" placeholder="成本价" /></td><td><div class="base-price-site-cell">${matrix}</div></td></tr>`;
  }).join("");
}

function collectBasePriceColorRows() {
  // DOM 采集：返回每颜色一行的 {color, cost_price, site_prices}（原价 + 手填售价）。
  return [...document.querySelectorAll("[data-base-price-cost]")].map((costInput) => {
    const color = costInput.dataset.basePriceCost || "";
    const site_prices = {};
    document.querySelectorAll(`[data-base-price-original="${CSS.escape(color)}"]`).forEach((input) => {
      const code = input.dataset.code || "";
      if (!code) return;
      if (!site_prices[code]) site_prices[code] = {};
      site_prices[code].original_price = input.value;
    });
    document.querySelectorAll(`[data-base-price-sale="${CSS.escape(color)}"]`).forEach((input) => {
      const code = input.dataset.code || "";
      if (!code) return;
      if (!site_prices[code]) site_prices[code] = {};
      site_prices[code].sale_price = input.value;
    });
    return { color, cost_price: costInput.value, site_prices };
  }).filter((row) => row.color);
}

// 「覆盖全部 SKU」：把第一行（首颜色/款式）的成本与站点价格复制到所有颜色行。
function applyBasePriceFirstRowToAll() {
  const rows = collectBasePriceColorRows();
  if (!rows.length) { showOperationFeedback("请先在颜色图添加颜色后填写价格", "error"); return; }
  const first = rows[0];
  const template = {
    cost_price: first.cost_price,
    site_prices: Object.fromEntries(
      Object.entries(first.site_prices || {}).map(([code, entry]) => [code, { ...entry }]),
    ),
  };
  rows.forEach((row) => {
    const cost = document.querySelector(`[data-base-price-cost="${CSS.escape(row.color)}"]`);
    if (cost) cost.value = template.cost_price || "";
    const codes = Object.keys(template.site_prices);
    codes.forEach((code) => {
      const entry = template.site_prices[code];
      const original = document.querySelector(`[data-base-price-original="${CSS.escape(row.color)}"][data-code="${CSS.escape(code)}"]`);
      if (original) original.value = entry.original_price ?? "";
      const sale = document.querySelector(`[data-base-price-sale="${CSS.escape(row.color)}"][data-code="${CSS.escape(code)}"]`);
      if (sale) sale.value = entry.sale_price ?? "";
    });
  });
  showOperationFeedback("首行价格已应用到全部颜色/款式，保存后生效", "success");
}

// 「批量填成本价」：把一个成本价应用到全部颜色行（只动成本列，站点价格不受影响）。
function applyBasePriceBatchCost() {
  const input = $("base-price-batch-cost");
  const raw = String(input?.value ?? "").trim();
  if (!raw || !(Number(raw) >= 0)) {
    showOperationFeedback("请先在批量成本价输入框里填一个不小于 0 的价格", "error");
    return;
  }
  const targets = document.querySelectorAll("[data-base-price-cost]");
  if (!targets.length) {
    showOperationFeedback("请先在「颜色图」区添加颜色后再批量填成本价", "error");
    return;
  }
  targets.forEach((el) => { el.value = raw; });
  showOperationFeedback(`成本价 ${raw} 已应用到全部 ${targets.length} 个颜色/款式，保存后生效`, "success");
}

async function previewBasePriceMatrix() {
  const text = $("base-price-paste")?.value || "";
  if (!text.trim()) throw new Error("请先粘贴价格表");
  const rows = collectBasePriceColorRows();
  if (!rows.length) throw new Error("请先添加颜色/款式行（或在「颜色图」区添加颜色）");
  // 无表头解析：每行 = 一个颜色/款式；行序与颜色行一致。解析器行↔档一一对应。
  const tiers = rows.map((row) => ({ id: row.color, sizes: [], colors: [row.color] }));
  const targetSites = basePriceTierSites();
  const result = await api("/api/cnsc-price-matrix/preview", {
    method: "POST",
    body: JSON.stringify({ text, tiers, target_sites: targetSites }),
  });
  const preview = $("base-price-preview");
  if (preview) {
    preview.textContent = cnscPriceParseSummary(result);
    preview.hidden = false;
  }
  if (result.errors?.length) throw new Error(result.errors.join("；"));
  state.basePricePreview = result;
  const parsedById = new Map((result.tiers || []).map((tier) => [String(tier.id || ""), tier]));
  const rowsWithPrices = collectBasePriceColorRows().map((row) => {
    const parsed = parsedById.get(String(row.color));
    return parsed ? { ...row, site_prices: parsed.site_prices || row.site_prices } : row;
  });
  state.basePriceColorRows = rowsWithPrices;
  renderBasePriceColors();
  showOperationFeedback("站点价格已解析并回填到对应颜色行，保存后生效", "success");
  return result;
}

function saveBasePriceColorRowsToTiers() {
  // 颜色行 → price_tiers（每颜色一档），供保存时写入。
  return collectBasePriceColorRows().map((row) => ({
    id: row.color,
    sizes: [],
    colors: [row.color],
    global_price_cny: "",
    cost_price: row.cost_price,
    site_prices: row.site_prices,
  }));
}


function closeBaseImageEditor() {
  closeVariantImagePicker();
  state.editingBaseProductId = "";
  state.editingBaseProductImages = [];
  state.editingBaseVariantImageMap = {};
  state.editingBaseColors = [];
  state.editingBaseProductCategory = null;
  const picker = $("base-image-picker");
  if (picker) picker.innerHTML = "";
  const modalFeedback = $("base-maintenance-feedback");
  if (modalFeedback) modalFeedback.hidden = true;
  const modal = $("base-maintenance-modal");
  if (modal) modal.hidden = true;
}

const CNSC_CONFIGURATION_LABELS = {
  cnsc_store: "跨境店铺",
  target_site_stores: "目标站点店铺",
  site_price_matrix: "站点价格矩阵",
  warehouse_stock_defaults: "三仓库存",
  default_site_discount_percent: "默认 %OFF",
  price_tiers: "价格档",
  cnsc_price_tier_size_coverage: "价格档尺码覆盖",
};

function cnscConfigurationLabel(field) {
  const key = String(field || "");
  const targetMatch = key.match(/^target_site_stores\.([^.]+)\.(discount_activity|discount_percent|store_identity)$/);
  if (targetMatch) {
    const kind = targetMatch[2] === "discount_activity" ? "折扣活动" : (targetMatch[2] === "store_identity" ? "店铺身份" : "%OFF");
    return `${targetMatch[1]} ${kind}`;
  }
  const matrixMatch = key.match(/^site_price_matrix\.([^.]+)\.([^.]+)(\.percent_off)?$/);
  if (matrixMatch) {
    const suffix = matrixMatch[3] ? "%OFF 不一致" : "站点价格";
    return `${matrixMatch[1]}/${matrixMatch[2]} ${suffix}`;
  }
  if (key.startsWith("price_tiers.")) {
    const tier = key.split(".")[1] || "";
    return key.endsWith(".sizes") ? `${tier} 档尺码` : `${tier} 全球价`;
  }
  return CNSC_CONFIGURATION_LABELS[key] || key;
}

function cnscTemplateMissingFields(template) {
  if (String(template?.listing_mode || "site").toLowerCase() !== "cnsc_global") return [];
  const missing = new Set((template.missing_configuration || []).map(String).filter(Boolean));
  const targets = Array.isArray(template.target_site_stores) ? template.target_site_stores : [];
  const tiers = Array.isArray(template.price_tiers) ? template.price_tiers : [];
  const warehouses = template.warehouse_stock_defaults || {};
  if (!targets.length) missing.add("target_site_stores");
  const defaultDiscount = Number(template.default_site_discount_percent ?? 40);
  if (!(defaultDiscount > 0 && defaultDiscount < 100)) missing.add("default_site_discount_percent");
  for (const target of targets) {
    const code = String(target.site_code || "UNKNOWN").toUpperCase();
    if (!String(target.mabang_discount_id || "").trim()) missing.add(`target_site_stores.${code}.discount_activity`);
    const effectiveDiscount = String(target.discount_percent ?? "").trim() ? Number(target.discount_percent) : defaultDiscount;
    if (!(effectiveDiscount > 0 && effectiveDiscount < 100)) missing.add(`target_site_stores.${code}.discount_percent`);
  }
  if (!tiers.length) missing.add("price_tiers");
  for (const tier of tiers) {
    if (!(Number(tier.global_price_cny) > 0)) missing.add(`price_tiers.${tier.id || "tier"}.global_price_cny`);
  }
  if (!["TH", "CN_GZ", "PH"].every((code) => Number.isInteger(Number(warehouses[code])) && Number(warehouses[code]) >= 0)) {
    missing.add("warehouse_stock_defaults");
  }
  const targetCodes = targets.map((item) => String(item.site_code || "").toUpperCase()).filter(Boolean);
  if (!targetCodes.length || tiers.some((tier) => targetCodes.some((code) => {
    const prices = tier.site_prices?.[code] || {};
    return !(Number(prices.sale_price) > 0)
      || !(Number(prices.original_price) > 0)
      || Number(prices.sale_price) > Number(prices.original_price);
  }))) missing.add("site_price_matrix");
  return [...missing];
}

function cnscTemplateBlockReason(template) {
  const missing = cnscTemplateMissingFields(template);
  return missing.length ? {
    reason: "template_needs_configuration",
    missing,
    labels: missing.map(cnscConfigurationLabel),
  } : null;
}

function renderStoreTemplates() {
  renderStoreTemplateTable();
  renderStoreTemplateCards();
}

function renderStoreTemplateTable() {
  const tbody = $("store-template-table");
  if (!tbody) return;
  tbody.innerHTML = "";
  // 跨境模式关闭时只显示本土模板；跨境数据保留，重新开启即恢复。
  const templates = crossBorderModeEnabled() ? state.storeTemplates : state.storeTemplates.filter((item) => !isCNSCListingCopy(item));
  if (!templates.length) {
    tbody.innerHTML = `<tr class="table-empty-row"><td colspan="9">${crossBorderModeEnabled() ? "暂无店铺模板。先保存一个店铺模板，再复制基础商品。" : "没有本土店铺模板。跨境模式已关闭，可在 设置 → 界面显示 重新开启。"}</td></tr>`;
    return;
  }
  for (const template of templates) {
    const cnsc = String(template.listing_mode || "site").toLowerCase() === "cnsc_global";
    // Decision A: never block the copy checkbox. Trust the backend-authoritative
    // missing_configuration (1d) for the non-blocking badge; recompute is offline fallback.
    const cnscMissing = cnsc
      ? (Array.isArray(template.missing_configuration) && template.missing_configuration.length
          ? template.missing_configuration.map(String).filter(Boolean)
          : cnscTemplateMissingFields(template))
      : [];
    const sizes = (template.sizes || []).join(", ");
    const price = cnsc
      ? (template.price_tiers || []).map((tier) => `${tier.id} ¥${tier.global_price_cny || "-"}`).join(" / ")
      : [template.normal_price, template.sale_price].filter((item) => item !== "").join(" / ");
    const promotion = cnsc ? `目标站点 ${(template.target_site_stores || []).length}` : (template.discount_label || (template.promotion_activity_id ? "已配置活动" : "活动未配置"));
    const transaction = cnsc
      ? `TH ${template.warehouse_stock_defaults?.TH ?? "-"} · CN_GZ ${template.warehouse_stock_defaults?.CN_GZ ?? "-"} · PH ${template.warehouse_stock_defaults?.PH ?? "-"}`
      : `库存 ${template.stock ?? "-"} · ${template.weight_kg || "-"} kg · ${template.package_length_cm || "-"}×${template.package_width_cm || "-"}×${template.package_height_cm || "-"} cm`;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="checkbox" data-store-template-select="${escapeHtml(template.id)}" ${state.selectedStoreTemplates.has(template.id) ? "checked" : ""} /></td>
      <td>${escapeHtml(template.store_name || "")} ${cnsc ? '<span class="template-mode-badge">CNSC</span>' : ""}${cnscMissing.length ? `<span class="template-readiness-badge" title="${escapeHtml(cnscMissing.map(cnscConfigurationLabel).join("、"))}">待补全配置</span>` : ""}</td>
      <td>${escapeHtml(template.template_name || "")}</td>
      <td>${escapeHtml(template.marketplace_category_path || "")}</td>
      <td>${escapeHtml(promotion)}</td>
      <td>${escapeHtml(price)}</td>
      <td>${escapeHtml(sizes)}</td>
      <td>${escapeHtml(transaction)}</td>
      <td>${template.size_chart_preview_url ? `<img class="table-thumb" src="${escapeHtml(sizeChartPreviewUrl(template.size_chart_preview_url, 192))}" alt="尺码图" />` : "-"}<button type="button" data-edit-store-template="${escapeHtml(template.id)}">编辑</button><button type="button" class="danger" data-delete-store-template="${escapeHtml(template.id)}">删除</button></td>
    `;
    tbody.appendChild(tr);
  }
}

function renderStoreTemplateCards() {
  const grid = $("store-template-card-grid");
  if (!grid) return;
  grid.innerHTML = "";
  // 跨境模式关闭时只显示本土模板；跨境数据保留，重新开启即恢复。
  const templates = crossBorderModeEnabled() ? state.storeTemplates : state.storeTemplates.filter((item) => !isCNSCListingCopy(item));
  if (!templates.length) {
    grid.innerHTML = `<div class="template-empty">${crossBorderModeEnabled() ? "还没有店铺模板。点击「新建模板」创建第一个。" : "没有本土店铺模板。跨境模式已关闭，可在 设置 → 界面显示 重新开启。"}</div>`;
    return;
  }
  for (const template of templates) {
    const cnsc = String(template.listing_mode || "site").toLowerCase() === "cnsc_global";
    // Decision A: non-blocking badge from backend-authoritative missing_configuration.
    const cnscMissing = cnsc
      ? (Array.isArray(template.missing_configuration) && template.missing_configuration.length
          ? template.missing_configuration.map(String).filter(Boolean)
          : cnscTemplateMissingFields(template))
      : [];
    const sizes = (template.sizes || []).join(" · ") || "尺码未配置";
    const price = cnsc
      ? (template.price_tiers || []).map((tier) => `${tier.id} ¥${tier.global_price_cny || "-"}`).join(" · ")
      : [template.normal_price, template.sale_price].filter((item) => item !== "").join(" → ");
    const promotion = template.discount_label || (template.promotion_activity_id ? "已配置活动" : "无活动");
    const accountLabel = storeTemplateAccountLabel(template.account_id);
    const card = document.createElement("article");
    card.className = "template-card";
    card.innerHTML = `
      <div class="template-card-head">
        <div>
          <div class="template-card-store">${escapeHtml(template.store_name || "未指定店铺")}</div>
          <div class="template-card-name">${escapeHtml(template.template_name || "未命名模板")}</div>
          ${accountLabel ? `<div class="template-card-account">账号 · ${escapeHtml(accountLabel)}</div>` : ""}
        </div>
        <div class="template-card-badges">${cnsc ? '<span class="template-mode-badge">CNSC</span>' : ""}${cnscMissing.length ? '<span class="template-readiness-badge">待补全配置</span>' : ""}</div>
      </div>
      <div class="template-card-meta">
        <span>类目 · <code>${escapeHtml(template.marketplace_category_path || "未选择")}</code></span>
        <span>价格 · <code>${escapeHtml(price || "未设置")}</code></span>
        <span>尺码 · <code>${escapeHtml(sizes)}</code></span>
        <span>${cnsc ? "站点" : "活动"} · ${escapeHtml(cnsc ? `${(template.target_site_stores || []).length} 个目标店铺` : promotion)}</span>
        ${cnscMissing.length ? `<span>待补全 · ${escapeHtml(cnscMissing.map(cnscConfigurationLabel).join("、"))}</span>` : ""}
      </div>
      <div class="template-card-actions">
        <button type="button" data-edit-store-template="${escapeHtml(template.id)}">维护</button>
        <button type="button" data-copy-store-template="${escapeHtml(template.id)}">复制</button>
        <button type="button" class="danger" data-delete-store-template="${escapeHtml(template.id)}">删除</button>
      </div>
    `;
    grid.appendChild(card);
  }
}

const LISTING_COPY_STATUS_LABELS = {
  copy_ready: "编辑中",
  completed: "已完成",
  ready: "就绪",
  needs_manual: "需要补充",
};

function listingCopyThumbUrl(copy) {
  const base = state.baseProducts.find((item) => item.id === copy.base_product_id);
  if (!base) return "";
  // 素材目录已被清理（assets_cleaned_at 有值）时不再拼缩略图地址：那是死链，
  // 列表里只会显示裂图。返回空串让调用方渲染虚线占位框。
  if (String(base.assets_cleaned_at || "").trim()) return "";
  const groupKey = String(base.group_id || "").trim().toUpperCase();
  const mainName = String(copy.main_image || base.main_image || (base.product_images || [])[0] || "").trim();
  if (!mainName) return "";
  // 先按商品自己那次导入的目录找：重复导入同一个 SKU 时素材库里有同名旧图，
  // 只按 group_id 找会取到旧导入那张，副本缩略图和商品卡片就对不上了。
  // 找不到再退回整组（副本可能是重新导入之前建的，主图只在旧目录里）。
  const scoped = assetsForBaseProduct(base).find((item) => String(item.file_name || "") === mainName);
  const asset = scoped || state.assets.find((item) =>
    String((item.group_id || "")).trim().toUpperCase() === groupKey
    && String(item.file_name || "") === mainName,
  );
  return asset ? assetImageUrl(asset, 128) : "";
}

// 副本 → 最新作业：列表分区、全选范围、已完成分桶共用同一份判断。
function latestJobByCopy() {
  const map = new Map();
  for (const job of state.automationJobs) {
    const key = job.listing_copy_id || "";
    if (!key) continue;
    const existing = map.get(key);
    const stamp = String(job.updated_at || job.created_at || "");
    if (!existing || stamp > String(existing.updated_at || existing.created_at || "")) map.set(key, job);
  }
  return map;
}

// 「全选」只覆盖**新建**（没有作业、还没跑过的副本）：现场反馈原来的全选把失败/已完成
// 的一起勾上，批量生成任务时容易误操作。失败与已完成的仍可单条勾选。
// 已经有任务在队列里（待领取/已领取）或正在执行的副本也跳过：它们不是「还没建任务」，
// 勾上再点批量生成只会拿到一串「已有活动任务」的报错。
function selectableListingCopies() {
  const jobs = latestJobByCopy();
  // 跨境模式关闭时「全选新建」只覆盖可见的本土副本，不会勾到隐藏的跨境行。
  const visible = crossBorderModeEnabled() ? state.storeListingCopies : state.storeListingCopies.filter((copy) => !isCNSCListingCopy(copy));
  return visible.filter((copy) => {
    const job = jobs.get(copy.id);
    if (listingCopyTaskSection(job) !== "new") return false;
    return !ACTIVE_AUTOMATION_STATUSES.has(String(job?.status || "").toLowerCase());
  });
}

// 已完成区按「完成时间」分桶 + 可逐桶折叠：现场反馈已完成越积越多，把列表拖得很长。
const COPY_COMPLETED_BUCKETS = [
  { id: "today", label: "今天" },
  { id: "yesterday", label: "昨天" },
  { id: "week", label: "近 7 天" },
  { id: "month", label: "近 30 天" },
  { id: "older", label: "更早" },
];
const COPY_SECTION_COLLAPSED_KEY = "ai_listing.store_copies_collapsed";

function copyCollapsedKeys() {
  try {
    const raw = JSON.parse(window.localStorage.getItem(COPY_SECTION_COLLAPSED_KEY) || "[]");
    return new Set(Array.isArray(raw) ? raw.map((item) => String(item)) : []);
  } catch (_) {
    return new Set();
  }
}

function setCopyCollapsed(key, collapsed) {
  const keys = copyCollapsedKeys();
  if (collapsed) keys.add(String(key));
  else keys.delete(String(key));
  try {
    window.localStorage.setItem(COPY_SECTION_COLLAPSED_KEY, JSON.stringify([...keys]));
  } catch (_) {
    // 存储不可用时只影响本次会话。
  }
}

function copySectionCollapsed(key) {
  return copyCollapsedKeys().has(String(key));
}

// 自动化任务列表的「已完成」区默认收起：只在第一次访问时播种一次，
// 之后完全以操作员的手动折叠/展开为准（不会每次刷新又把它收起来）。
const JOBS_COLLAPSE_SEEDED_KEY = "ai_listing.automation_jobs_collapsed_seeded";

function ensureJobsCollapseDefaults() {
  try {
    if (window.localStorage.getItem(JOBS_COLLAPSE_SEEDED_KEY)) return;
    const keys = copyCollapsedKeys();
    keys.add("jobs:completed");
    window.localStorage.setItem(COPY_SECTION_COLLAPSED_KEY, JSON.stringify([...keys]));
    window.localStorage.setItem(JOBS_COLLAPSE_SEEDED_KEY, "1");
  } catch (_) {
    // 存储不可用时退化为"默认展开"，不影响使用。
  }
}

// 完成时间 → 时间桶；时间戳缺失/解析不了时归到「更早」，不隐藏、也不误判成今天。
function completedBucketFor(stamp) {
  const at = Date.parse(String(stamp || ""));
  if (!Number.isFinite(at)) return "older";
  const now = Date.now();
  const dayMs = 86400000;
  const todayStart = new Date(now).setHours(0, 0, 0, 0);
  if (at >= todayStart) return "today";
  if (at >= todayStart - dayMs) return "yesterday";
  if (at >= now - 7 * dayMs) return "week";
  if (at >= now - 30 * dayMs) return "month";
  return "older";
}

// 按时间分桶（副本列表与自动化任务列表共用同一套桶定义/判断，只区分 key 前缀，
// 这样两个列表的折叠状态互不影响）。
function bucketByTime(items, stampOf, keyPrefix) {
  const buckets = new Map(COPY_COMPLETED_BUCKETS.map((bucket) => [bucket.id, []]));
  for (const item of items) buckets.get(completedBucketFor(stampOf(item))).push(item);
  return COPY_COMPLETED_BUCKETS
    .filter((bucket) => (buckets.get(bucket.id) || []).length)
    .map((bucket) => ({ ...bucket, key: `${keyPrefix}:${bucket.id}`, items: buckets.get(bucket.id) }));
}

function appendListingCopyRow(tbody, copy, jobs) {
  const cnsc = isCNSCListingCopy(copy);
  // 副本可能属于另一个账号的模板：用全量索引回显（列表本身只列当前账号的模板）。
  const template = storeTemplateById(copy.template_id) || {};
  const gaps = [];
  if (!copy.mabang_category_id) gaps.push("平台类目");
  // Size chart can come from any of four sources — a local file path, a
  // referenced filename, an uploaded asset (size_chart_asset_id), or a mabang
  // server-side template. The copy is only missing a size chart when ALL four
  // are empty.
  if (!cnsc && (
    !copy.size_chart_image
    && !copy.size_chart_image_path
    && !copy.size_chart_asset_id
    && !copy.mabang_size_chart_template_id
  )) gaps.push("尺码图");
  const copyStatusLabel = gaps.length
    ? `缺少 ${gaps.join(", ")}`
    : copy.description_status === "pending"
      ? "描述待生成"
      : LISTING_COPY_STATUS_LABELS[copy.status] || copy.status || "";
  const job = jobs.get(copy.id);
  const autoTone = jobGlyphTone(copy, job);
  const autoLabel = jobGlyphLabel(copy, job);
  const jobId = job?.id || "";
  const actionButtons = [
    `<button type="button" data-edit-listing-copy="${escapeHtml(copy.id)}">检查</button>`,
  ];
  if (jobId) actionButtons.push(`<button type="button" class="secondary-button" data-automation-job-details="${escapeHtml(jobId)}">任务详情</button>`);
  actionButtons.push(`<button type="button" class="danger" data-delete-listing-copy="${escapeHtml(copy.id)}">删除</button>`);
  const thumbUrl = listingCopyThumbUrl(copy);
  const thumbCell = thumbUrl
    ? `<img class="listing-copy-thumb" src="${escapeHtml(thumbUrl)}" alt="${escapeHtml(copy.listing_title || copy.main_sku || copy.group_id || "缩略图")}" loading="lazy" />`
    : `<span class="listing-copy-thumb listing-copy-thumb--empty" aria-hidden="true"></span>`;
  const tr = document.createElement("tr");
  tr.innerHTML = `
      <td><input type="checkbox" data-listing-copy-select="${escapeHtml(copy.id)}" ${state.selectedListingCopies.has(copy.id) ? "checked" : ""} /></td>
      <td class="listing-copy-thumb-cell">${thumbCell}</td>
      <td>${escapeHtml(copy.main_sku || copy.group_id || "")}</td>
      <td class="listing-copy-store-cell" title="${escapeHtml(copy.store_name || "")}"><small class="listing-copy-mode-label">${cnsc ? "跨境" : "本土"}</small> ${escapeHtml(copy.store_name || "")}</td>
      <td class="listing-copy-template-cell" title="${escapeHtml(template.template_name || copy.template_id || "")}">${escapeHtml(template.template_name || copy.template_id || "")}</td>
      <td class="listing-copy-title-cell" title="${escapeHtml(copy.listing_title || "")}">${escapeHtml(copy.listing_title || "")}</td>
      <td class="listing-copy-num-cell">${escapeHtml(copy.sku_count || 0)}</td>
      <td class="listing-copy-num-cell">${escapeHtml(copy.price_count || 0)}</td>
      <td><span class="status ${escapeHtml(copy.status || "")}">${escapeHtml(copyStatusLabel)}</span></td>
      <td><span class="row-status"><span class="row-glyph row-glyph--${autoTone}" aria-hidden="true"></span><span class="row-status-label">${escapeHtml(autoLabel)}</span></span></td>
      <td class="table-actions">${actionButtons.join("")}</td>
  `;
  tbody.appendChild(tr);
}

// 已完成区的时间分组行：点一下折叠/展开这一桶（状态存 localStorage）。
function appendCopyBucketRow(tbody, group, colspan, collapsed) {
  tbody.insertAdjacentHTML("beforeend", `
    <tr class="copy-bucket-row" data-copy-collapse="${escapeHtml(group.key)}" role="button" tabindex="0" aria-expanded="${collapsed ? "false" : "true"}">
      <td colspan="${colspan}">
        <span class="copy-bucket-caret" aria-hidden="true">${collapsed ? "▸" : "▾"}</span>
        <strong>${escapeHtml(group.label)}</strong>
        <span>${(group.items || group.copies || []).length} 项</span>
        <span class="muted">${collapsed ? "（点击展开）" : "（点击折叠）"}</span>
      </td>
    </tr>
  `);
}

function renderStoreListingCopies() {
  const tbody = $("store-listing-copy-table");
  if (!tbody) return;
  tbody.innerHTML = "";
  // 跨境模式关闭时只显示本土副本；跨境数据保留，重新开启即恢复。
  const visibleCopies = crossBorderModeEnabled() ? state.storeListingCopies : state.storeListingCopies.filter((copy) => !isCNSCListingCopy(copy));
  if (!visibleCopies.length) {
    tbody.innerHTML = `<tr class="table-empty-row"><td colspan="11">${crossBorderModeEnabled() ? "暂无店铺刊登副本。请从商品维护中选择基础商品并复制到店铺模板。" : "暂无本土店铺刊登副本。跨境模式已关闭，可在 设置 → 界面显示 重新开启。"}</td></tr>`;
    return;
  }
  const jobs = latestJobByCopy();
  const copiesBySection = new Map(TASK_SECTION_ORDER.map((section) => [section.id, []]));
  for (const copy of visibleCopies) {
    const latestJob = jobs.get(copy.id);
    const copyStatus = String(copy.status || "");
    const sectionHint = latestJob || (
      copyStatus === "completed"
        ? { status: "saved" }
        : (FAILED_AUTOMATION_STATUSES.has(copyStatus) ? { status: "failed" } : null)
    );
    const sectionId = listingCopyTaskSection(sectionHint);
    copiesBySection.get(sectionId).push(copy);
  }
  for (const section of TASK_SECTION_ORDER) {
    const copies = copiesBySection.get(section.id) || [];
    if (!copies.length) continue;
    const collapsed = copySectionCollapsed(section.id);
    appendTaskSectionRow(tbody, section, copies.length, 11, { collapsed });
    if (collapsed) continue;
    // 已完成区再按完成时间分桶，每桶可单独折叠（今天/昨天/近 7 天/近 30 天/更早）。
    const groups = section.id === "completed"
      ? bucketByTime(
        copies,
        (copy) => {
          const job = jobs.get(copy.id);
          return job ? (job.updated_at || job.created_at) : (copy.updated_at || copy.created_at);
        },
        "completed",
      )
      : [{ id: "", key: "", label: "", items: copies }];
    for (const group of groups) {
      if (group.key) {
        const groupCollapsed = copySectionCollapsed(group.key);
        appendCopyBucketRow(tbody, group, 11, groupCollapsed);
        if (groupCollapsed) continue;
      }
      for (const copy of group.items || group.copies || []) appendListingCopyRow(tbody, copy, jobs);
    }
  }
  updateListingCopySelectAllButton();
}

// 全选/取消全选当前副本列表：**只作用于「新建」区**（现场反馈：原来的全选把失败/已完成的一起勾上，
// 批量生成任务时容易误操作）。失败/已完成仍需单条勾选。
function toggleSelectAllListingCopies() {
  const targets = selectableListingCopies();
  const result = $("listing-copy-batch-result");
  if (!targets.length) {
    if (result) result.textContent = "没有新建的副本可勾选（失败/已完成/执行中的不参与全选）。";
    updateListingCopySelectAllButton();
    return;
  }
  const allSelected = targets.every((copy) => state.selectedListingCopies.has(copy.id));
  if (allSelected) {
    for (const copy of targets) state.selectedListingCopies.delete(copy.id);
  } else {
    for (const copy of targets) state.selectedListingCopies.add(copy.id);
  }
  renderStoreListingCopies();
  if (result) {
    result.textContent = allSelected
      ? `已取消勾选 ${targets.length} 个新建副本。`
      : `已勾选 ${targets.length} 个新建副本（失败/已完成/执行中的不参与全选）。`;
  }
}

// 「全选」按钮文案跟随勾选状态：部分已选显示已选数，全选时提示取消。
function updateListingCopySelectAllButton() {
  // 勾选数量变化时两个按钮一起更新（勾选还包括单条勾的失败/已完成行）。
  updateListingCopyBatchDeleteButton();
  const btn = $("select-all-listing-copies-btn");
  if (!btn) return;
  const targets = selectableListingCopies();
  if (!targets.length) {
    btn.textContent = "全选新建";
    btn.disabled = true;
    return;
  }
  btn.disabled = false;
  const selectedCount = targets.filter((copy) => state.selectedListingCopies.has(copy.id)).length;
  if (selectedCount === 0) btn.textContent = `全选新建（${targets.length}）`;
  else if (selectedCount === targets.length) btn.textContent = "取消全选新建";
  else btn.textContent = `全选新建（已选 ${selectedCount}/${targets.length}）`;
}

// 「批量删除副本」按钮：勾了才可用，文案带数量（勾选包含失败/已完成的行）。
function updateListingCopyBatchDeleteButton() {
  const btn = $("delete-selected-listing-copies-btn");
  if (!btn) return;
  const count = state.selectedListingCopies.size;
  btn.disabled = count === 0;
  btn.textContent = count ? `批量删除副本（${count}）` : "批量删除副本";
}

// 批量删除勾选的副本：一次请求、一次落库（逐条 DELETE 每条都要重写主库，很慢）。
// 正在执行/已不存在的副本由后端按条跳过并回报，不阻断同批其它副本。
async function deleteSelectedListingCopies() {
  const ids = [...state.selectedListingCopies];
  const result = $("listing-copy-batch-result");
  if (!ids.length) {
    if (result) result.textContent = "请先勾选要删除的店铺刊登副本。";
    return;
  }
  const jobs = latestJobByCopy();
  const copies = state.storeListingCopies.filter((copy) => ids.includes(copy.id));
  const jobStatus = (copy) => String(jobs.get(copy.id)?.status || "").toLowerCase();
  // 后端只拒绝 claimed/running（worker 正拿着它跑）；ready 只是排队，会连排队任务一起删。
  const live = copies.filter((copy) => jobStatus(copy) === "claimed" || jobStatus(copy) === "running");
  const queued = copies.filter((copy) => jobStatus(copy) === "ready");
  const labels = copies.slice(0, 3).map((copy) => `${copy.main_sku || copy.group_id || ""}${copy.store_name ? ` · ${copy.store_name}` : ""}`.trim());
  const head = copies.length > 3 ? `${labels.join("、")} 等 ${copies.length} 个` : labels.join("、");
  const notes = [];
  if (live.length) notes.push(`其中 ${live.length} 个正在执行，会被跳过（请先在自动化页取消任务）`);
  if (queued.length) notes.push(`其中 ${queued.length} 个已排进队列但还没开始，会连同排队任务一起删除`);
  const confirmed = window.confirm(
    `确认删除 ${ids.length} 个店铺刊登副本？\n\n${head}\n\n` +
    `将删除副本的 SKU、价格和未完成自动化任务（历史自动化记录保留）。基础商品和 NAS 素材不受影响。` +
    (notes.length ? `\n\n${notes.join("；")}。` : ""),
  );
  if (!confirmed) return;
  if (result) result.textContent = `正在删除 ${ids.length} 个副本…`;
  let data;
  try {
    data = await api("/api/store-listing-copies/batch-delete", {
      method: "POST",
      body: JSON.stringify({ ids }),
    });
  } catch (error) {
    if (result) result.textContent = `批量删除失败：${error.message || error}`;
    throw error;
  }
  for (const id of data.deleted_ids || []) state.selectedListingCopies.delete(id);
  for (const id of data.not_found_ids || []) state.selectedListingCopies.delete(id);
  await Promise.all([loadStoreListingCopies(), loadAutomationJobs(), loadBaseProducts()]).catch(() => {});
  const deleted = Number(data.deleted_count || 0);
  const skippedRunning = (data.running_job_ids || []).length;
  const skippedMissing = (data.not_found_ids || []).length;
  const parts = [`已删除 ${deleted} 个店铺刊登副本`];
  if (deleted) {
    parts.push(`清理 ${data.deleted_listing_sku_count || 0} 个 SKU、${data.deleted_listing_price_count || 0} 个价格、${data.deleted_automation_job_count || 0} 个未完成任务`);
  }
  if (skippedRunning) parts.push(`跳过 ${skippedRunning} 个（正在执行或已排进队列，请先取消）`);
  if (skippedMissing) parts.push(`跳过 ${skippedMissing} 个（已不存在）`);
  const message = `${parts.join("；")}。`;
  if (result) result.textContent = message;
  showOperationFeedback(message, (skippedRunning || skippedMissing) ? "warning" : "success");
}

// ===== 执行中卡片：实时步骤 + 人工标记 =====

// 运行中优先用 result.live_steps（worker 边跑边回报），跑完用 result.steps（执行器完整轨迹）。
function automationJobSteps(job) {
  const result = (job || {}).result || {};
  const live = Array.isArray(result.live_steps) ? result.live_steps : [];
  if (live.length) return live;
  return Array.isArray(result.steps) ? result.steps : [];
}

function automationLiveStepLabel(step) {
  const stage = String((step || {}).stage || "");
  if (stage) return AUTOMATION_STAGE_LABELS[stage] || stage;
  const event = String((step || {}).event || "");
  return AUTOMATION_EVENT_LABELS[event] || event || "执行事件";
}

// 「阶段」列：显示**当前执行到第几步**（例：步骤 3 · 正在填写类目属性）。
// 详细步骤列表只在「执行详情」弹窗里看；外面一行就够，随既有 3 秒轮询往前走。
function automationCurrentStepLabel(job) {
  const steps = automationJobSteps(job);
  if (!steps.length) return "";
  const last = steps[steps.length - 1];
  const label = automationLiveStepLabel(last);
  if (!label) return "";
  return `步骤 ${steps.length} · ${label}`;
}

// 人工确认执行结果：只写审计（result.manual_confirmation），不伪造 save_clicked/publish_clicked。
async function markAutomationJobManually(jobId, status) {
  if (!jobId) return;
  const noteInput = $("automation-manual-note");
  const note = (noteInput?.value || "").trim();
  const label = status === "saved" ? "已完成（已保存草稿）" : "失败";
  const confirmText = `确认把任务标记为「${label}」？\n\n只会写入人工确认审计，不会改动执行器的保存证据。`;
  if (!window.confirm(confirmText)) return;
  try {
    await api(`/api/automation/jobs/${encodeURIComponent(jobId)}/manual-finish`, {
      method: "POST",
      body: JSON.stringify({ status, note, operator: "operator" }),
    });
    if (noteInput) noteInput.value = "";
    await loadAutomationJobs();
    renderAutomationJobs();
    refreshOpenAutomationJobDetails();
  } catch (error) {
    operationError(error, "人工标记失败");
  }
}

function renderAutomationJobs() {
  const tbody = $("automation-job-table");
  if (!tbody) return;
  ensureJobsCollapseDefaults();
  tbody.innerHTML = "";
  if (!state.automationJobs.length) {
    tbody.innerHTML = `<tr class="table-empty-row"><td colspan="9">暂无自动化任务。检查并保存店铺刊登副本后即可生成任务。</td></tr>`;
      return;
  }
  const copiesById = new Map(state.storeListingCopies.map((copy) => [copy.id, copy]));
  const jobsBySection = new Map(TASK_SECTION_ORDER.map((section) => [section.id, []]));
  for (const job of state.automationJobs) jobsBySection.get(automationTaskSection(job)).push(job);
  for (const section of TASK_SECTION_ORDER) {
    const jobs = jobsBySection.get(section.id) || [];
    if (!jobs.length) continue;
    // 与副本列表一致：分区标题可折叠；分区内再按"最近动静"的时间分桶（今天/昨天/近 7 天/近 30 天/更早）。
    const sectionKey = `jobs:${section.id}`;
    const sectionCollapsed = copySectionCollapsed(sectionKey);
    appendTaskSectionRow(tbody, section, jobs.length, 9, { collapsed: sectionCollapsed, key: sectionKey });
    if (sectionCollapsed) continue;
    const jobGroups = bucketByTime(jobs, (job) => job.updated_at || job.created_at, sectionKey);
    for (const group of jobGroups) {
      const groupCollapsed = copySectionCollapsed(group.key);
      appendCopyBucketRow(tbody, group, 9, groupCollapsed);
      if (groupCollapsed) continue;
      for (const job of group.items) {
    const copy = copiesById.get(job.listing_copy_id) || {};
    const missing = (job.missing_fields || []).map((field) => listingCopyReadinessLabel(field, copy)).join("；");
    const warnings = (job.warnings || []).map((field) => listingCopyReadinessLabel(field, copy)).join("；");
    const result = job.result || {};
    const savedStatuses = new Set(["saved", "saved_with_warnings"]);
    const savedEvidence = savedStatuses.has(job.status) && result.save_clicked === true && result.publish_clicked === false
      ? "已保存 / 未发布"
      : "-";
    const stageWarnings = Object.entries(result.stages || {})
      .filter(([, stage]) => ["warning", "skipped"].includes(stage?.status))
      .map(([name, stage]) => {
        const stageName = AUTOMATION_STAGE_LABELS[name] || name;
        const codeText = stage.code || stage.message || stage.status || "";
        return `${stageName}：${automationErrorLabel(codeText)}`;
      })
      .join("；");
    const lastError = job.last_error || result.error || "";
    const lastErrorLabel = lastError ? automationErrorLabel(lastError) : "";
    const isRunning = ["claimed", "running"].includes(job.status);
    const canCancel = ["ready", "claimed", "needs_manual", "needs_regeneration"].includes(job.status);
    const pres = stageStatusPresentation(job.status);
    // Fold worker + lease + job_type into a single tooltip so the row is
    // scan-friendly but the operator can still get the trail via hover.
    const updatedAt = job.updated_at || job.created_at || "";
    const updatedAtLabel = formatLocalTime(updatedAt);
    const updatedRaw = updatedAt ? String(updatedAt) : "";
    const updatedTooltip = [
      job.worker_id ? `Worker ${job.worker_id}` : "",
      job.lease_expires_at ? `Lease ${formatLocalTime(job.lease_expires_at)}` : "",
      job.job_type ? `Type ${job.job_type}` : "",
      job.attempt_count ? `尝试 ${job.attempt_count} 次` : "",
      job.retry_count ? `操作员重试 ${job.retry_count} 次` : "",
      updatedRaw ? `完整时间 ${updatedRaw}` : "",
    ].filter(Boolean).join(" · ");
    const tr = document.createElement("tr");
    tr.dataset.automationJobDetails = job.id || "";
    tr.className = "automation-job-row";
    // 「店铺 / 主 SKU」旁边带一张商品主图缩略图：与副本列表共用 listingCopyThumbUrl()，
    // 取不到图时留一个虚线占位框，保持各行文字对齐。
    const thumbUrl = listingCopyThumbUrl(copy);
    const thumbHtml = thumbUrl
      ? `<img class="automation-job-thumb" src="${escapeHtml(thumbUrl)}" alt="${escapeHtml(copy.main_sku || copy.group_id || "商品主图")}" loading="lazy" />`
      : `<span class="automation-job-thumb automation-job-thumb--empty" aria-hidden="true"></span>`;
    tr.innerHTML = `
      <td class="automation-job-glyph-cell"><span class="row-glyph row-glyph--${pres.tone}" aria-hidden="true"></span></td>
      <td class="automation-job-identity-cell">
        <span class="automation-job-identity-main">
          ${thumbHtml}
          <span class="automation-job-identity-text">
            <strong>${escapeHtml(job.store_name || copy.store_name || "-")}</strong>
            <small>${escapeHtml(copy.main_sku || copy.group_id || "")}</small>
          </span>
        </span>
      </td>
      <td>
        <span class="status-pill"><span class="row-glyph row-glyph--${pres.tone}" aria-hidden="true"></span>${escapeHtml(TASK_STATUS_LABELS[job.status] || pres.label)}</span>
        ${job.attempt_count ? `<small class="readiness-warning-inline">尝试 ${escapeHtml(job.attempt_count)} 次</small>` : ""}
        ${job.retry_count ? `<small class="readiness-warning-inline">已重试 ${escapeHtml(job.retry_count)} 次</small>` : ""}
      </td>
      <td title="${escapeHtml(automationPhaseLabel(job.current_phase))}">${escapeHtml(automationCurrentStepLabel(job) || TASK_STATUS_LABELS[job.current_phase] || automationPhaseLabel(job.current_phase) || "-")}</td>
      <td class="automation-job-gap-cell">${escapeHtml(missing || "-")}${warnings ? `<small class="readiness-warning-inline">提醒：${escapeHtml(warnings)}</small>` : ""}</td>
      <td class="automation-job-error-cell" title="${escapeHtml([lastError || "", stageWarnings].filter(Boolean).join("；"))}">${escapeHtml(lastErrorLabel || "-")}${stageWarnings ? `<small class="readiness-warning-inline">${escapeHtml(stageWarnings)}</small>` : ""}</td>
      <td>${escapeHtml(savedEvidence)}</td>
      <td class="automation-job-updated-cell" title="${escapeHtml(updatedTooltip)}">${escapeHtml(updatedAtLabel)}</td>
      <td class="table-actions">
        ${job.status === "ready" ? `<button type="button" class="secondary-button" data-nudge-worker="${escapeHtml(job.id || "")}">催 Worker</button>` : ""}
        ${["blocked", "failed", "needs_manual", "needs_regeneration"].includes(job.status) ? automationRetryButtonHtml(job.id || "") : ""}
        ${canCancel ? `<button type="button" class="secondary-button" data-cancel-automation-job="${escapeHtml(job.id || "")}">取消</button>` : ""}
        ${!isRunning ? `<button type="button" class="danger" data-delete-automation-job="${escapeHtml(job.id || "")}">删除</button>` : ""}
      </td>
    `;
      tbody.appendChild(tr);
    }
    }
  }
}

async function cancelAutomationJob(jobId) {
  const job = state.automationJobs.find((item) => item.id === jobId);
  if (!job) return;
  if (!window.confirm("取消此自动化任务？Worker 将不再继续处理该任务，任务记录会保留。")) return;
  await api(`/api/automation/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    body: JSON.stringify({ reason: "operator_canceled" }),
  });
  await loadAutomationJobs();
  showOperationFeedback("自动化任务已取消", "success");
}

// Called from the "催 Worker" button on a ready job. Doesn't touch the job
// itself (it's fine as-is, worker just isn't picking it up). Inspects the
// worker's live status and picks the smallest action that would unstick it.
async function nudgeWorkerForJob(jobId) {
  const w = await api("/api/worker/status");
  const activity = String(w?.activity || "").toLowerCase();
  const status = String(w?.status || "").toLowerCase();
  const pid = w?.pid || 0;

  // Case A — worker process is dead / stopped. Start it.
  if (!pid || status === "stopped" || status === "failed") {
    showOperationFeedback("Worker 未运行，正在启动…", "info");
    await api("/api/worker/resume", { method: "POST", body: JSON.stringify({}) });
    showOperationFeedback("Worker 已启动，几秒后应会领取任务", "success");
    await loadAutomationJobs();
    return;
  }

  // Case B — worker is running but its poll loop is stuck in the internal
  // paused_reason state. Only a full process restart clears it (the flag is
  // in-memory).
  if (activity === "paused" || (activity && activity !== "idle" && w?.activity_error)) {
    if (activity === "waiting_for_browser") {
      // Special case — worker is fine, but Chrome CDP is unreachable. Restarting
      // won't help; the operator needs to bring up the browser first.
      showOperationFeedback("Worker 在等自动化浏览器。请先点「启动自动化浏览器」（设置 → 自动化 Worker）或在服务器桌面打开 Chrome。", "error");
      return;
    }
    if (activity === "waiting_for_page_import") {
      // 导入期间 Worker 主动让路：重启 Worker 解决不了，反而会打断导入。
      showOperationFeedback("Worker 在给「从马帮当前页导入」让路（采集窗口正被使用）。在导入弹窗里点「采集当前页」并确认导入后，Worker 会自动恢复领取任务。", "info");
      return;
    }
    showOperationFeedback(`Worker 被暂停（${w.activity_error?.slice(0, 60) || activity}），正在重启…`, "info");
    await api("/api/worker/restart", { method: "POST", body: JSON.stringify({}) });
    showOperationFeedback("Worker 已重启。几秒后应会领取任务；如仍未领取，请检查任务缺字段或租约状态。", "success");
    await loadAutomationJobs();
    return;
  }

  // Case C — worker is running and idle. Nothing wrong on the worker side.
  // The job might have missing_fields, an unfulfilled snapshot hash, or is
  // simply waiting for the next poll tick.
  showOperationFeedback(`Worker 正在待命（PID ${pid}）—— 应马上领取。如超过一分钟仍未领取，检查任务是否缺字段。`, "info");
}

// 「再次尝试」按钮统一入口：只入队，不直接发请求。
// 连点多个任务时串行重排——后端每次重排都要写执行快照并唤醒 Worker（起进程 /
// 准备浏览器），并发跑只会互相抢浏览器和登录、界面也跟着卡；队列跑完统一刷新一次列表。
function enqueueAutomationRetry(jobId) {
  const id = String(jobId || "").trim();
  if (!id) return;
  if (state.automationRetryActiveId === id || state.automationRetryQueue.includes(id)) {
    showOperationFeedback("该任务已在重排队列里，等前面的任务处理完就会执行", "info");
    refreshAutomationRetryButtons();
    return;
  }
  state.automationRetryQueue.push(id);
  refreshAutomationRetryButtons();
  showOperationFeedback(
    state.automationRetryRunning
      ? `已加入重排队列（前面还有 ${state.automationRetryQueue.length} 个），会按顺序执行`
      : "正在重排该任务…",
    "info",
  );
  runAutomationRetryQueue().catch((error) => operationError(error, "重试自动化任务失败"));
}

function automationRetryStateFor(jobId) {
  const id = String(jobId || "");
  if (!id) return "idle";
  if (state.automationRetryActiveId === id) return "processing";
  if (state.automationRetryQueue.includes(id)) return "queued";
  return "idle";
}

function automationRetryButtonHtml(jobId) {
  const retryState = automationRetryStateFor(jobId);
  const label = retryState === "processing" ? "重排中…" : retryState === "queued" ? "排队中…" : "再次尝试";
  const queued = retryState === "idle" ? "" : " is-queued";
  const disabled = retryState === "idle" ? "" : " disabled";
  return `<button type="button" class="secondary-button${queued}" data-self-heal-automation-job="${escapeHtml(jobId || "")}"${disabled}>${label}</button>`;
}

// 按钮文案/置灰跟着队列状态走；表格每 3 秒会重绘，这里负责点击瞬间的即时反馈。
function refreshAutomationRetryButtons() {
  document.querySelectorAll("[data-self-heal-automation-job]").forEach((button) => {
    if (!(button instanceof HTMLElement)) return;
    const retryState = automationRetryStateFor(button.dataset.selfHealAutomationJob || "");
    button.disabled = retryState !== "idle";
    button.textContent = retryState === "processing" ? "重排中…" : retryState === "queued" ? "排队中…" : "再次尝试";
    button.classList.toggle("is-queued", retryState !== "idle");
  });
}

// 队列泵：一次只发一条 self-heal；处理期间新加入的 id 会在本轮继续按序处理。
async function runAutomationRetryQueue() {
  if (state.automationRetryRunning) return;
  state.automationRetryRunning = true;
  const failures = [];
  const successMessages = [];
  let needsManual = 0;
  let done = 0;
  try {
    while (state.automationRetryQueue.length) {
      const jobId = state.automationRetryQueue.shift();
      state.automationRetryActiveId = jobId;
      refreshAutomationRetryButtons();
      const remaining = state.automationRetryQueue.length;
      showOperationFeedback(
        `正在重排任务（第 ${done + 1} 个${remaining ? `，队列还有 ${remaining} 个` : ""}）…`,
        "info",
      );
      try {
        const result = await requestSelfHealAutomationJob(jobId);
        if (result.ok) {
          successMessages.push(automationRetrySuccessText(result));
          if ((result.missing_fields || []).length) needsManual += 1;
        } else {
          failures.push({ jobId, reason: result.reason });
        }
      } catch (error) {
        failures.push({ jobId, reason: error?.message || "重排失败" });
      }
      done += 1;
      state.automationRetryActiveId = "";
      refreshAutomationRetryButtons();
    }
  } finally {
    state.automationRetryRunning = false;
    state.automationRetryActiveId = "";
    refreshAutomationRetryButtons();
    // 每条重排都会改任务行；中途不逐条整表重绘（3 秒轮询也会补），跑完只刷新一次。
    await loadAutomationJobs().catch(() => {});
    if (done) {
      if (failures.length) {
        const first = failures[0];
        showOperationFeedback(
          `重排完成：成功 ${done - failures.length} 个，失败 ${failures.length} 个（首个失败：${first.reason}）`,
          "error",
        );
      } else if (done === 1) {
        // 单条时保留原来的详细信息（重试次数 / 待补齐项 / 登录提示）。
        showOperationFeedback(successMessages[0] || "已在原任务上重排", needsManual ? "info" : "success");
      } else {
        const manual = needsManual ? `，${needsManual} 个仍待补齐信息` : "";
        showOperationFeedback(`已按队列重排 ${done} 个任务${manual}`, needsManual ? "info" : "success");
      }
    }
  }
}

// 单条重排请求（队列内部使用）：返回结构化结果，不刷新列表、不弹最终提示。
async function requestSelfHealAutomationJob(jobId) {
  const result = await api(`/api/automation/jobs/${encodeURIComponent(jobId)}/self-heal`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  if (!result.ok) {
    // Surface which step failed so the operator knows whether it's the worker
    // or the listing copy that's blocking recovery.
    const bad = (result.steps || []).find((step) => !step.ok);
    return { ok: false, jobId, reason: bad ? `${bad.error || bad.step || "失败"}` : "恢复未成功" };
  }
  return result;
}

function automationRetrySuccessText(result) {
  // 原地重排：不新建任务行，这一行自己回到待执行，重试次数 +1（历史留在详情时间线里）。
  const statusLabel = TASK_STATUS_LABELS[result.job_status] || result.job_status || "待领取";
  const retry = result.retry_count ? `（第 ${result.retry_count} 次重试）` : "";
  const missing = (result.missing_fields || []).length
    ? "。仍有待补齐项，任务停在「需要补充信息」，补齐后可以再次尝试"
    : "";
  const hint = result.hint ? `。${result.hint}` : "";
  return `已在原任务上重排${retry}，当前状态：${statusLabel}${missing}${hint}`;
}

async function deleteAutomationJob(jobId) {
  const job = state.automationJobs.find((item) => item.id === jobId);
  if (!job) return;
  if (!window.confirm("删除此自动化任务？任务步骤、错误和保存记录将永久移除。")) return;
  await api(`/api/automation/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
  await loadAutomationJobs();
  showOperationFeedback("自动化任务已删除", "success");
}

async function deleteStoreListingCopy(copyId) {
  const copy = state.storeListingCopies.find((item) => item.id === copyId);
  if (!copy) return;
  const label = copy.store_name || copy.template_id || "此副本";
  const confirmed = window.confirm(
    `删除刊登副本「${label}」？\n\n` +
    `将删除该副本的 SKU、价格和未完成自动化任务。历史自动化记录保留做审计。基础商品和 NAS 素材不受影响。`,
  );
  if (!confirmed) return;
  const result = await api(`/api/store-listing-copies/${encodeURIComponent(copyId)}`, { method: "DELETE" });
  state.selectedListingCopies.delete(copyId);
  await Promise.all([loadStoreListingCopies(), loadAutomationJobs(), loadBaseProducts()]);
  const message = `刊登副本已删除：${result.deleted_listing_sku_count || 0} 个 SKU、${result.deleted_listing_price_count || 0} 个价格、${result.deleted_automation_job_count || 0} 个未完成任务已清理`;
  showOperationFeedback(message, "success");
}

async function deleteBaseProduct(productId) {
  const product = state.baseProducts.find((item) => item.id === productId);
  if (!product) return;
  const copyCount = Number(product.listing_copy_count || 0);
  const confirmed = window.confirm(
    `删除基础商品“${product.product_name_cn || product.base_name || product.main_sku || "未命名商品"}”？\n\n` +
    `将删除 ${copyCount} 个店铺刊登副本、其 SKU/价格和未完成自动化任务。历史自动化记录会保留。NAS 原始素材文件不会删除。`,
  );
  if (!confirmed) return;
  const deleted = await api(`/api/base-products/${encodeURIComponent(productId)}`, { method: "DELETE" });
  if (state.selectedBaseProductId === productId) state.selectedBaseProductId = "";
  state.selectedBaseProducts.delete(productId);
  if (state.editingBaseProductId === productId) closeBaseImageEditor();
  await Promise.all([loadBaseProducts(), loadStoreListingCopies(), loadAutomationJobs()]);
  const message = `基础商品已删除：${deleted.deleted_listing_copy_count || 0} 个刊登副本、${deleted.deleted_automation_job_count || 0} 个未完成任务已清理`;
  $("base-product-result").textContent = message;
  showOperationFeedback(message, "success");
}

function automationJobEventDetails(event) {
  const values = [];
  if (event.phase) values.push(`阶段：${automationPhaseLabel(event.phase)}`);
  if (event.worker_id) values.push(`Worker：${event.worker_id}`);
  if (event.attempt_count) values.push(`第 ${event.attempt_count} 次尝试`);
  if (event.error) values.push(`错误：${event.error}`);
  return values.join("；");
}

function automationPhaseLabel(phase) {
  return AUTOMATION_PHASE_LABELS[phase] || TASK_STATUS_LABELS[phase] || phase || "-";
}

// Presentation for the raw English status strings the worker/adapter emit
// (e.g. "complete", "skipped", "warning") — maps every known value to one of the
// four glyph tones + a Chinese label so timeline events and stage rows read
// consistently with the card/table glyph vocabulary.
const STAGE_STATUS_PRESENTATION = {
  complete: { tone: "done", label: "已完成" },
  completed: { tone: "done", label: "已完成" },
  success: { tone: "done", label: "已完成" },
  ok: { tone: "done", label: "已完成" },
  saved: { tone: "done", label: "已保存" },
  saved_with_warnings: { tone: "done", label: "已保存（有提醒）" },
  warning: { tone: "watch", label: "有提醒" },
  warn: { tone: "watch", label: "有提醒" },
  // 媒体子步骤（视频/水印）用自己的一套状态词；不认这几种就会显示灰色的英文原词。
  attached: { tone: "done", label: "已附加" },
  uploaded: { tone: "done", label: "已上传" },
  already_attached: { tone: "done", label: "已存在" },
  fixed: { tone: "done", label: "已处理" },
  skipped: { tone: "idle", label: "已跳过" },
  running: { tone: "watch", label: "执行中" },
  claimed: { tone: "watch", label: "已领取" },
  pending: { tone: "watch", label: "等待中" },
  in_progress: { tone: "watch", label: "执行中" },
  needs_confirmation: { tone: "watch", label: "需要人工确认" },
  needs_manual: { tone: "watch", label: "需要补充" },
  needs_regeneration: { tone: "watch", label: "需要重新生成" },
  failed: { tone: "alert", label: "失败" },
  error: { tone: "alert", label: "失败" },
  blocked: { tone: "alert", label: "已阻止" },
  canceled: { tone: "idle", label: "已取消" },
};

// 媒体阶段判「这一步做成了」的状态词（执行器 runRecoverable 回的就是这些）。
const MEDIA_DONE_STATUSES = new Set(["attached", "uploaded", "already_attached", "already", "fixed", "ok", "done"]);

function stageStatusPresentation(status) {
  const key = String(status || "").trim().toLowerCase();
  if (!key) return { tone: "idle", label: "未开始" };
  return STAGE_STATUS_PRESENTATION[key] || { tone: "idle", label: status };
}

// CNSC 站点匹配失败（cnsc_target_site_not_found / _ambiguous / _not_selected 等）时，
// 执行器会把「配置目标」「弹窗实际采集到的店铺」「弹窗 DOM 快照」放进 result.details。
// 这里把它渲染出来，方便判断是脚本没采到、还是站点标错、还是数据不匹配。
function renderCnscTargetDiagnostics(details) {
  const d = details || {};
  const wanted = (d.store_id || d.store_name || d.site_code)
    ? `<div><span>配置目标</span><strong>${escapeHtml([d.site_code, d.store_name, d.store_id].filter(Boolean).join(" / ") || "-")}</strong></div>`
    : "";
  const rows = (list) => (Array.isArray(list) ? list : []).map((candidate) => {
    const ids = (candidate.identities || []).join(", ");
    const names = (candidate.names || []).join(" / ");
    const hidden = candidate.visible === false ? "（隐藏）" : "";
    return `<tr><td>${escapeHtml(candidate.site_code || "?")}</td><td>${escapeHtml(candidate.label || names || "-")}${hidden}</td><td>${escapeHtml(ids || "-")}</td></tr>`;
  }).join("");
  const table = (title, list) => (Array.isArray(list) && list.length)
    ? `<details class="automation-job-detail-raw"><summary>${escapeHtml(title)}（${list.length}）</summary><table class="cnsc-candidate-table"><thead><tr><th>站点</th><th>店铺</th><th>标识</th></tr></thead><tbody>${rows(list)}</tbody></table></details>`
    : "";
  const availableTable = table("弹窗按目标站点采集到的店铺", d.available);
  const allTable = table("弹窗全部采集到的店铺（含站点标错/未识别）", d.all_candidates);
  const snapshot = d.picker_snapshot
    ? `<details class="automation-job-detail-raw"><summary>查看站点弹窗 DOM 快照（${d.picker_snapshot.length} 字符）</summary><pre>${escapeHtml(d.picker_snapshot)}</pre></details>`
    : "";
  if (!wanted && !availableTable && !allTable && !snapshot) return "";
  return `<div class="automation-job-detail-diagnostics"><div class="automation-job-detail-diagnostics-title">站点匹配诊断</div>${wanted}${availableTable}${allTable}${snapshot}</div>`;
}

function renderAutomationJobDetails(jobId) {
  const job = state.automationJobs.find((item) => item.id === jobId);
  if (!job) return false;
  const copy = state.storeListingCopies.find((item) => item.id === job.listing_copy_id) || {};
  const result = job.result || {};
  const savedStatuses = new Set(["saved", "saved_with_warnings"]);
  const savedEvidence = savedStatuses.has(job.status) && result.save_clicked === true && result.publish_clicked === false
    ? "已保存草稿，未发布"
    : "未取得保存证据";
  $("automation-job-detail-meta").textContent = `${job.store_name || copy.store_name || "未关联店铺"} / ${copy.main_sku || copy.group_id || "未关联商品"} / ${job.id || ""}`;
  const headerPres = stageStatusPresentation(job.status);
  const detailError = job.last_error || result.error || "";
  const detailErrorLabel = detailError ? automationErrorLabel(detailError) : "";
  const detailNodeHint = [result.node_path && `Node ${result.node_path}`, result.node_version && result.node_version].filter(Boolean).join(" ");
  const rawTail = result.raw_output_tail || "";
  $("automation-job-detail-summary").innerHTML = `
    <div><span>当前状态</span><strong class="status-pill"><span class="row-glyph row-glyph--${headerPres.tone}" aria-hidden="true"></span>${escapeHtml(TASK_STATUS_LABELS[job.status] || headerPres.label)}</strong></div>
    <div><span>当前阶段</span><strong>${escapeHtml(automationPhaseLabel(job.current_phase))}</strong></div>
    <div><span>尝试次数</span><strong>${escapeHtml(job.attempt_count || 0)}</strong></div>
    <div><span>操作员重试</span><strong>${escapeHtml(job.retry_count || 0)} 次</strong></div>
    <div><span>保存结果</span><strong>${escapeHtml(savedEvidence)}</strong></div>
    ${result.manual_confirmation ? `<div><span>人工确认</span><strong>${escapeHtml(result.manual_confirmation.status === "saved" ? "已完成（人工）" : "失败（人工）")} · ${escapeHtml(formatLocalTime(result.manual_confirmation.at))}${result.manual_confirmation.note ? " · " + escapeHtml(result.manual_confirmation.note) : ""}</strong></div>` : ""}
    ${detailError ? `<div class="automation-job-detail-error" title="${escapeHtml(detailError)}"><span>最后错误</span><strong>${escapeHtml(detailErrorLabel)}</strong></div>` : ""}
    ${detailNodeHint ? `<div><span>执行 Node</span><strong>${escapeHtml(detailNodeHint)}</strong></div>` : ""}
    ${rawTail ? `<details class="automation-job-detail-raw"><summary>查看执行器原始输出（末尾 ${rawTail.length} 字符）</summary><pre>${escapeHtml(rawTail)}</pre></details>` : ""}
    ${renderCnscTargetDiagnostics(result.details)}
  `;

  const timeline = $("automation-job-detail-timeline");
  const events = job.run_log || [];
  timeline.innerHTML = events.length
    ? events.map((event) => {
        const eventPres = stageStatusPresentation(event.event || event.status);
        const eventAtLabel = formatLocalTime(event.at);
        return `<li class="automation-timeline-item"><span class="row-glyph row-glyph--${eventPres.tone}" aria-hidden="true"></span><time title="${escapeHtml(event.at || "")}">${escapeHtml(eventAtLabel)}</time><div><strong>${escapeHtml(AUTOMATION_EVENT_LABELS[event.event] || event.event || "执行事件")}</strong>${automationJobEventDetails(event) ? `<p>${escapeHtml(automationJobEventDetails(event))}</p>` : ""}</div></li>`;
      }).join("")
    : "<li class=\"empty-state\">任务尚未被 Worker 领取，因此还没有执行步骤。</li>";

  const stages = Object.entries(result.stages || {});

  // 执行步骤（表单填充顺序）：executor 的 stage 事件按序解析而来，
  // 展示「正在选择店铺 → 正在填写 SKU → 正在填写价格 → 保存」的完整过程。
  // 运行中优先用 result.live_steps（worker 边跑边回报），跑完再用 result.steps（完整轨迹）。
  const liveSteps = Array.isArray(result.live_steps) ? result.live_steps : [];
  const steps = liveSteps.length ? liveSteps : (Array.isArray(result.steps) ? result.steps : []);
  const stepsSection = $("automation-job-detail-steps-section");
  const stepsEl = $("automation-job-detail-steps");
  if (stepsSection && stepsEl) {
    const saveProven = savedStatuses.has(job.status) && result.save_clicked === true && result.publish_clicked === false;
    const isFinishedWithoutEvidence = ["failed", "blocked", "needs_manual", "needs_confirmation", "needs_regeneration", "canceled"].includes(job.status);
    const evidenceGlyph = saveProven ? "done" : (isFinishedWithoutEvidence ? "alert" : "running");
    const evidenceText = saveProven
      ? "已确认保存草稿，且未触发发布"
      : (isFinishedWithoutEvidence ? "任务已停止，未取得保存证据" : "等待执行到保存并核验");
    stepsSection.hidden = false;
    const stepRows = steps.map((step, index) => {
          const label = automationLiveStepLabel(step);
          // 媒体阶段（商品视频/水印）用 status 表达结果：只看 ok 会把「视频已按模板
          // 默认视频附加」画成灰点未完成，操作员以为没选上。
          // 老任务的 steps 里没有 status，回退到同名的媒体子步骤状态（照样显示正确）。
          const stageInfo = (result.stages || {})[step.stage] || {};
          const stepStatus = String(step.status || stageInfo.status || "").trim().toLowerCase();
          const skipped = stepStatus === "skipped";
          const ok = step.ok === true || MEDIA_DONE_STATUSES.has(stepStatus);
          const failed = step.ok === false || ["warning", "warn", "failed", "error", "blocked"].includes(stepStatus);
          const glyph = ok ? "done" : (failed ? "alert" : (skipped ? "idle" : "running"));
          const stepTime = formatLocalTime(step.at);
          // 失败/跳过的步骤要直接写出原因，否则界面上只有「步骤」+ 点，运营看不出为什么。
          const stepCode = step.code || stageInfo.code || "";
          const reason = (failed || skipped) && stepCode ? automationErrorLabel(stepCode) : "";
          return `<li class="automation-step-item"><span class="automation-step-index">${index + 1}</span><span class="row-glyph row-glyph--${glyph}" aria-hidden="true"></span><strong>${escapeHtml(label)}</strong>${reason ? `<span class="automation-step-reason">${escapeHtml(reason)}</span>` : ""}${stepTime !== "-" ? `<time title="${escapeHtml(step.at || "")}">${escapeHtml(stepTime)}</time>` : ""}</li>`;
        }).join("");
    stepsEl.innerHTML = `${stepRows}<li class="automation-step-item automation-step-item--evidence"><span class="automation-step-index">${steps.length + 1}</span><span class="row-glyph row-glyph--${evidenceGlyph}" aria-hidden="true"></span><strong>保存证据</strong><span>${escapeHtml(evidenceText)}</span></li>`;
  }

  $("automation-job-detail-stages").innerHTML = stages.length
    ? stages.map(([name, stage]) => {
        const pres = stageStatusPresentation(stage.status);
        const stageText = automationErrorLabel(stage.code || stage.message || "");
        return `<article class="automation-job-stage"><div><strong>${escapeHtml(AUTOMATION_STAGE_LABELS[name] || name)}</strong><p title="${escapeHtml(stage.code || stage.message || "")}">${escapeHtml(stageText || "无附加信息")}</p></div><span class="status-pill"><span class="row-glyph row-glyph--${pres.tone}" aria-hidden="true"></span>${escapeHtml(pres.label)}</span></article>`;
      }).join("")
    : "<p class=\"empty-state\">本次任务没有可展示的图片或视频子步骤。</p>";
  return true;
}

function openAutomationJobDetails(jobId) {
  if (!renderAutomationJobDetails(jobId)) return;
  state.openAutomationJobId = jobId;
  const dialog = $("automation-job-detail-dialog");
  if (dialog && !dialog.open) dialog.showModal();
}

function refreshOpenAutomationJobDetails() {
  const dialog = $("automation-job-detail-dialog");
  if (!state.openAutomationJobId || !dialog?.open) {
    state.openAutomationJobId = "";
    return;
  }
  if (!renderAutomationJobDetails(state.openAutomationJobId)) {
    state.openAutomationJobId = "";
    dialog.close();
  }
}

function renderAssets() {
  const groupsTarget = $("file-group-list");
  const filesTarget = $("file-grid");
  if (!groupsTarget || !filesTarget) return;
  const groups = [...new Set(state.assets.map((asset) => asset.group_id || "未分组"))].sort();
  if (!groups.includes(state.selectedFileGroup)) state.selectedFileGroup = groups[0] || "";
  groupsTarget.innerHTML = groups.map((group) => `
    <button class="file-group ${group === state.selectedFileGroup ? "active" : ""}" data-file-group="${escapeHtml(group)}">${escapeHtml(group)}</button>
  `).join("");
  if (!groups.length) {
    filesTarget.innerHTML = `<div class="empty-state">暂无文件。可以先扫描 sample_assets 或 NAS 目录。</div>`;
    return;
  }
  const files = state.assets.filter((asset) => (asset.group_id || "未分组") === state.selectedFileGroup);
  filesTarget.innerHTML = files.map((asset) => {
    const previewable = ["main", "variant", "model", "detail", "size_chart", "print"].includes(asset.asset_type || "");
    const image = previewable ? `<img src="${escapeHtml(assetImageUrl(asset, 480))}" alt="${escapeHtml(asset.file_name || "")}" loading="lazy" decoding="async" />` : "";
    return `
      <article class="file-card">
        <div class="file-card-preview">${image}</div>
        <strong title="${escapeHtml(asset.file_path || "")}">${escapeHtml(asset.file_name || "")}</strong>
        <span>${escapeHtml(asset.asset_type || "")}</span>
        <span>${escapeHtml(asset.status || "")}</span>
      </article>
    `;
  }).join("");
}

function renderProjectPlan(plan) {
  const summary = $("project-plan-summary");
  const chain = $("project-runtime-chain");
  if (!summary || !chain) return;
  summary.innerHTML = (plan.summary || []).map((item) => `<div class="plan-summary-item">${escapeHtml(item)}</div>`).join("");
  chain.innerHTML = (plan.runtime_chain || []).map((item) => `
    <div class="plan-step">
      <strong>${escapeHtml(item.name || "")}</strong>
      <span class="status ready">${escapeHtml(item.status || "")}</span>
      <small>${escapeHtml(item.notes || "")}</small>
    </div>
  `).join("");
}

// 服务端 tkinter 文件夹选择器：仅在服务运行在有桌面的本机时可用。
// 它是可选的“便捷”入口，主路径始终是输入框里手动粘贴路径。
// 这里带客户端超时，避免对话框弹在别处 / 无桌面时前端一直挂起。
async function pickNasProductFolder({ timeoutMs = 60000 } = {}) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const data = await api("/api/system/folder-picker", {
      method: "POST",
      body: JSON.stringify({ title: "选择 NAS 商品文件夹" }),
      signal: controller.signal,
    });
    if (data.mode === "unavailable") {
      throw new Error(data.error || "服务端未能打开文件夹选择器");
    }
    return data.path || "";
  } finally {
    window.clearTimeout(timer);
  }
}

// NAS folder browser state — read via server-side /api/nas/browse so every LAN
// client works without needing a desktop dialog on the server. `pickNasProductFolder`
// above is kept as an internal fallback for RDP scenarios; it is no longer wired
// to the "浏览…" button because it wedges on machines without a display.
// Multi-select folder picker. Operators can drill in with a single click; when
// they arrive at a leaf (a product folder with no subdirs) it auto-nudges to
// "add to selection". Multiple products can be queued up before hitting
// 确认创建, which fires POST /api/base-products/from-folder N times.
// 首批渲染行数：其余行在列表滚动接近底部时按同样步长追加，
// 避免一次性往 DOM 里塞几千行按钮。
const NAS_BROWSER_RENDER_PAGE = 300;

// 滚动到距底部多少像素内触发追加下一页。
const NAS_BROWSER_LOAD_MORE_THRESHOLD_PX = 120;

const nasBrowserState = {
  currentPath: "",
  root: "",
  isLeaf: false,
  selected: [],   // list of absolute paths
  entries: [],    // last-loaded child folder list (for local filtering)
  filter: "",     // case-insensitive substring applied on top of entries
  requestId: 0,
  requestController: null,
  renderLimit: NAS_BROWSER_RENDER_PAGE, // 已渲染行数（滚动到底自动追加下一页）
};

function nasBrowserFilteredEntries() {
  const all = nasBrowserState.entries || [];
  const needle = String(nasBrowserState.filter || "").trim().toLowerCase();
  if (!needle) return all;
  return all.filter((entry) => String(entry.name || "").toLowerCase().includes(needle));
}

async function browseNasProductFolder() {
  const dialog = $("nas-browser-dialog");
  if (!dialog) return;
  nasBrowserState.selected = [];
  renderNasBrowserSelection();
  if (typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
  // Paint the dialog before touching the NAS. A UNC share can take seconds to
  // answer, but the operator should immediately see the loading state and can
  // close the dialog without waiting for the request.
  nasBrowserNavigate("").catch(() => {});
}

async function nasBrowserNavigate(path, { refresh = false, full = false } = {}) {
  const status = $("nas-browser-status");
  const rootDisplay = $("nas-browser-root-display");
  const requestId = ++nasBrowserState.requestId;
  if (nasBrowserState.requestController) nasBrowserState.requestController.abort();
  const controller = new AbortController();
  nasBrowserState.requestController = controller;
  if (status) status.innerHTML = `<span class="with-spinner"><span class="loading-spinner loading-spinner--sm"></span>${refresh ? "正在刷新…" : "读取中…"}</span>`;
  try {
    const params = new URLSearchParams();
    if (path) params.set("path", path);
    if (refresh) params.set("refresh", "1");
    // 默认「浅」打开:一次列目录立刻返回,不等逐子目录探测叶子标记。
    // 叶子标记由后台补全(full=1 走完整探测/读完整缓存)。
    if (!full) params.set("shallow", "1");
    const data = await api(`/api/nas/browse?${params.toString()}`, { signal: controller.signal });
    // A slow NAS response must never overwrite a newer directory selection.
    if (requestId !== nasBrowserState.requestId) return false;
    nasBrowserState.currentPath = data.path || "";
    nasBrowserState.root = data.root || "";
    nasBrowserState.entries = data.entries || [];
    nasBrowserState.renderLimit = NAS_BROWSER_RENDER_PAGE;
    nasBrowserState.filter = "";
    const filterInput = $("nas-browser-filter");
    if (filterInput) filterInput.value = "";
    nasBrowserState.isLeaf = !data.is_root && !nasBrowserState.entries.length;
    if (rootDisplay) rootDisplay.textContent = data.root || "(未配置)";
    renderNasBrowserBreadcrumbs(data);
    renderNasBrowserList();
    updateNasBrowserActionButtons();
    const upBtn = $("nas-browser-up-btn");
    if (upBtn) upBtn.disabled = Boolean(data.is_root);
    // 行数在状态栏与函数返回值(探测是否完成)两处都要用,提到块外以免
    // status 元素缺失时 return 处引用未定义变量。
    const leafCount = (nasBrowserState.entries || []).filter((entry) => entry.has_subdirs === false).length;
    const unknownCount = (nasBrowserState.entries || []).filter((entry) => entry.has_subdirs == null).length;
    if (status) {
      // Second visit within the cache window is served from the server-side cache;
      // an entry older than the fresh threshold still returns instantly (stale-
      // while-revalidate) while the server re-scans it in the background.
      let cacheHint = "";
      if (data.from_cache) {
        cacheHint = data.stale ? "（已缓存，秒开；后台刷新中，稍后自动更新）" : "（已缓存，秒开）";
      }
      if (nasBrowserState.isLeaf) {
        status.textContent = `这是叶子文件夹（无子目录）—— 是商品文件夹的候选。点「加入已选」把它排队。${cacheHint}`;
      } else if (!(data.entries || []).length) {
        status.textContent = `此文件夹下没有子目录${cacheHint}`;
      } else if (unknownCount || data.probe_running) {
        status.textContent = `本层 ${nasBrowserState.entries.length} 个子目录已显示，后台正在探测哪些是款式叶子…`;
        if (!full) scheduleNasLeafProbe(); // 用户打开的浅列表:由轮询负责补全
      } else if (leafCount) {
        status.textContent = `本层有 ${leafCount} 个款式文件夹（无子目录），直接点行即可加入已选；点 📂 进入下一层。${cacheHint}`;
      } else {
        status.textContent = `本层 ${nasBrowserState.entries.length} 个子目录。${cacheHint}`;
      }
    }
    return !unknownCount && !data.probe_running;
  } catch (err) {
    if (err?.name === "AbortError" || requestId !== nasBrowserState.requestId) return false;
    if (rootDisplay) rootDisplay.textContent = "(读取失败)";
    if (status) status.textContent = `读取失败：${err.message || err}。点右上「改默认路径」去设置里配置 NAS 根目录。`;
    return false;
  } finally {
    if (requestId === nasBrowserState.requestId) nasBrowserState.requestController = null;
  }
}

// 浅打开后,目录名已可用但叶子标记未知。后台探测线程会把完整结果写进服务端
// 缓存;这里隔一会儿再请求一次完整列表(full=1),命中探测完成的缓存则瞬间
// 返回并刷新出行上的「款式」标记。探测未完成则保留可交互的浅列表,状态栏
// 继续提示,稍后会自动再试(每个目录最多约 6 次)。
let nasLeafProbeTimer = 0;
const nasLeafProbeAttempts = new Map(); // path -> 已尝试次数
function scheduleNasLeafProbe() {
  const targetPath = nasBrowserState.currentPath;
  if (!targetPath) return;
  if (nasLeafProbeTimer) clearTimeout(nasLeafProbeTimer);
  nasLeafProbeTimer = setTimeout(() => {
    nasLeafProbeTimer = 0;
    // 用户可能已切走:只为当初安排的那个目录补探测,避免对用户当前
    // 所在的新目录发一次可能很慢的 full 请求。
    if (nasBrowserState.currentPath !== targetPath) return;
    nasBrowserNavigate(targetPath, { full: true }).then((complete) => {
      if (nasBrowserState.currentPath !== targetPath) return;
      if (complete) {
        nasLeafProbeAttempts.delete(targetPath);
        const status = $("nas-browser-status");
        if (status && !nasBrowserState.isLeaf) {
          const leafCount = (nasBrowserState.entries || []).filter((entry) => entry.has_subdirs === false).length;
          if (leafCount) status.textContent = `本层 ${leafCount} 个款式文件夹已识别，直接点行即可加入已选。`;
        }
      } else {
        const tried = (nasLeafProbeAttempts.get(targetPath) || 0) + 1;
        if (tried < 6) {
          // Probe still running server-side (slow NAS): try again in a few seconds.
          nasLeafProbeAttempts.set(targetPath, tried);
          scheduleNasLeafProbe();
        } else {
          nasLeafProbeAttempts.delete(targetPath);
        }
      }
    }).catch(() => { /* 探测失败不打断浏览 */ });
  }, 1200);
}

function updateNasBrowserActionButtons() {
  const addBtn = $("nas-browser-add-btn");
  const confirmBtn = $("nas-browser-confirm-btn");
  const cur = nasBrowserState.currentPath;
  const isRoot = cur && cur === nasBrowserState.root;
  const already = nasBrowserState.selected.includes(cur);
  if (addBtn) {
    addBtn.disabled = !cur || isRoot || already;
    addBtn.textContent = already ? "已加入" : (nasBrowserState.isLeaf ? "★ 加入已选（叶子）" : "加入已选");
  }
  if (confirmBtn) {
    const n = nasBrowserState.selected.length;
    confirmBtn.disabled = n === 0;
    confirmBtn.textContent = n ? `确认创建 ${n} 个商品` : "确认创建";
  }
}

function nasBrowserAddCurrent() {
  const cur = nasBrowserState.currentPath;
  if (!cur || cur === nasBrowserState.root) return;
  if (nasBrowserState.selected.includes(cur)) return;
  nasBrowserState.selected.push(cur);
  renderNasBrowserSelection();
  renderNasBrowserList();
  updateNasBrowserActionButtons();
}

// Leaf row click: toggle the folder in/out of the selection queue. The row keeps
// its place so the operator can click the next leaf immediately.
function nasBrowserToggleLeaf(name) {
  const cur = nasBrowserState.currentPath;
  if (!cur || !name) return;
  const sep = cur.includes("\\") ? "\\" : (cur.includes("/") ? "/" : "\\");
  const full = `${cur.replace(/[\\/]+$/, "")}${sep}${name}`;
  const idx = nasBrowserState.selected.indexOf(full);
  if (idx >= 0) {
    nasBrowserState.selected.splice(idx, 1);
  } else {
    nasBrowserState.selected.push(full);
  }
  renderNasBrowserSelection();
  renderNasBrowserList();
  updateNasBrowserActionButtons();
}

function nasBrowserRemoveSelected(path) {
  nasBrowserState.selected = nasBrowserState.selected.filter((p) => p !== path);
  renderNasBrowserSelection();
  renderNasBrowserList();
  updateNasBrowserActionButtons();
}

function nasBrowserClearSelected() {
  nasBrowserState.selected = [];
  renderNasBrowserSelection();
  renderNasBrowserList();
  updateNasBrowserActionButtons();
}

function renderNasBrowserSelection() {
  const wrap = $("nas-browser-selection");
  const list = $("nas-browser-selection-list");
  const count = $("nas-browser-selection-count");
  if (!wrap || !list || !count) return;
  const items = nasBrowserState.selected;
  count.textContent = String(items.length);
  wrap.hidden = items.length === 0;
  list.innerHTML = items.map((p) => {
    const name = p.split(/[\\/]/).filter(Boolean).at(-1) || p;
    return `<li><span class="nas-selection-name" title="${escapeHtml(p)}">${escapeHtml(name)}</span><button type="button" class="link-button" data-nas-remove="${escapeHtml(p)}">移除</button></li>`;
  }).join("");
}

// AI 识别分阶段轮播：导入请求是同步的，等待期间无法拿到服务端真实阶段边界，
// 所以按固定顺序轮播阶段文案；请求返回后用 ai_stages 显示真实结果。
const AI_IMPORT_STAGE_MESSAGES = [
  "AI 识别中：正在判断款式适合的人群…",
  "AI 识别中：正在识别标题和详情…",
  "AI 识别中：正在识别颜色图…",
];

function startAiImportStageTicker(renderFn) {
  let index = 0;
  renderFn(AI_IMPORT_STAGE_MESSAGES[0]);
  return setInterval(() => {
    index = (index + 1) % AI_IMPORT_STAGE_MESSAGES.length;
    renderFn(AI_IMPORT_STAGE_MESSAGES[index]);
  }, 4000);
}

function aiImportStageSummary(product) {
  const aiError = String(product?.ai_generation_error || "").trim();
  if (aiError) return { error: aiError };
  const stages = product?.ai_stages || {};
  const parts = [];
  if (stages.audience === "done") parts.push("已按 AI 识别人群选择标题规则");
  if (stages.title === "done" && (product?.listing_title || product?.description)) parts.push("标题/详情已生成");
  else if (stages.title === "skipped") parts.push("标题/详情未启用");
  if (stages.colors === "done") parts.push("颜色图已识别");
  else if (stages.colors === "skipped") parts.push("颜色图沿用文件名规则");
  return { text: parts.join(" · ") };
}

async function nasBrowserConfirmCreate() {
  const items = nasBrowserState.selected.slice();
  if (!items.length) return;
  // 确认后立即关闭选择器、回到商品维护视图；导入在后台逐个进行，
  // 进度与结果实时显示在主界面的 result 区域，操作员可以一边等一边做别的。
  nasBrowserState.selected = [];
  renderNasBrowserSelection();
  updateNasBrowserActionButtons();
  $("nas-browser-dialog")?.close();
  showView("product-maintenance");
  const result = $("base-product-result");
  if (result) result.innerHTML = `<span class="with-spinner"><span class="loading-spinner"></span>正在扫描并创建 ${items.length} 个基础商品，请稍候…</span>`;
  runNasBatchImport(items).then((summary) => {
    if (result) result.textContent = summary.text;
    showOperationFeedback(summary.text, (summary.failed.length || summary.aiFailed.length) ? "error" : "success");
  }).catch((err) => {
    const message = `批量创建异常：${(err && err.message) || err}`;
    if (result) result.textContent = message;
    showOperationFeedback(message, "error");
  });
}

// 创建后台 NAS 导入任务。服务端以受限并发执行“扫盘 + AI 识别”，前端只
// 轮询轻量状态，因此确认后不会再被单个慢 NAS 文件夹或视觉请求卡住。
async function runNasBatchImport(items, { keepPanel = false } = {}) {
  const result = $("base-product-result");
  const task = await api("/api/base-products/from-folders", {
    method: "POST",
    body: JSON.stringify({
      paths: items,
      category: categoryProfile($("base-product-category").value)?.product_line || $("base-product-category").value,
    }),
  });
  // NAS 导入也显示在「导入队列」面板里：新批次先清掉上一批的旧行（重试单个目录时保留其他行）。
  setNasImportPanel(task.id, keepPanel ? [...nasImportPanelState.items.filter((item) => !items.includes(item.path)), ...(task.items || items.map((path) => ({ path, status: "queued" })))] : (task.items || items.map((path) => ({ path, status: "queued" }))));
  let state = task;
  while (state.status !== "completed") {
    if (Array.isArray(state.items)) setNasImportPanel(task.id, state.items);
    const current = state.items?.find((item) => item.status === "running") || {};
    const name = current.path?.split(/[\\/]/).filter(Boolean).at(-1) || "准备中";
    if (result) {
      result.innerHTML = `<span class="with-spinner"><span class="loading-spinner"></span>后台创建中 (${state.completed || 0}/${state.total || items.length})：${escapeHtml(name)} · ${escapeHtml(current.stage === "ai" ? "AI 识别" : "扫描 NAS")}</span>`;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 700));
    state = await api(`/api/base-products/import-tasks/${encodeURIComponent(task.id)}`);
    if (Array.isArray(state.items)) setNasImportPanel(task.id, state.items);
  }
  if (Array.isArray(state.items)) setNasImportPanel(task.id, state.items);
  const success = (state.items || []).filter((item) => item.status === "succeeded").map((item) => ({
    path: item.path,
    product: item.product,
    aiError: item.ai_error || "",
  }));
  const failed = (state.items || []).filter((item) => item.status === "failed").map((item) => ({
    path: item.path,
    error: item.error || "创建失败",
  }));
  await Promise.all([loadBaseProducts(), loadAssets(), loadReadiness()]);
  if (success.length && success[0].product?.id) {
    state.selectedBaseProductId = success[0].product.id;
    // loadBaseProducts 的渲染发生在赋值之前，这里补一次让选中高亮即时出现。
    renderBaseProducts();
  }
  const parts = [`已创建 ${success.length} 个基础商品`];
  const aiFailed = success.filter((item) => item.aiError);
  if (aiFailed.length) parts.push(`其中 ${aiFailed.length} 个 AI 识别失败（可在商品维护中重试）：${aiFailed.map((item) => item.path.split(/[\\/]/).at(-1)).join("、")}`);
  if (failed.length) parts.push(`失败 ${failed.length} 个：${failed.map((f) => `${f.path.split(/[\\/]/).at(-1)}(${f.error})`).join("；")}`);
  return { text: parts.join("；"), success, failed, aiFailed };
}

// Quick jump: close the NAS browser, switch to settings, focus the NAS root
// input, and select its text so the operator can just paste a new path.
function openSettingsForNasRoot() {
  $("nas-browser-dialog")?.close();
  showView("settings", "settings");
  const input = $("settings-nas-root");
  if (input instanceof HTMLInputElement) {
    // Wait for the view transition to complete so scrollIntoView + focus stick.
    setTimeout(() => {
      input.scrollIntoView({ block: "center", behavior: "smooth" });
      input.focus();
      input.select();
    }, 80);
  }
}

function renderNasBrowserBreadcrumbs(data) {
  const nav = $("nas-browser-breadcrumbs");
  if (!nav) return;
  const root = data.root || "";
  const current = data.path || root;
  // Build segments from root down to current so each is clickable.
  const rel = current.slice(root.length).replace(/^[\\/]+/, "");
  const parts = rel ? rel.split(/[\\/]+/) : [];
  const crumbs = [{ label: "根", path: root }];
  let acc = root;
  for (const part of parts) {
    acc = acc.endsWith("\\") || acc.endsWith("/") ? acc + part : `${acc}\\${part}`;
    crumbs.push({ label: part, path: acc });
  }
  nav.innerHTML = crumbs.map((c, i) => (
    i === crumbs.length - 1
      ? `<span class="nas-crumb current">${escapeHtml(c.label)}</span>`
      : `<button type="button" class="nas-crumb" data-nas-crumb="${escapeHtml(c.path)}">${escapeHtml(c.label)}</button><span class="nas-crumb-sep">›</span>`
  )).join("");
}

function renderNasBrowserList() {
  const list = $("nas-browser-list");
  if (!list) return;
  const all = nasBrowserState.entries || [];
  if (!all.length) {
    list.innerHTML = `<div class="nas-browser-empty muted">（无子目录）</div>`;
    return;
  }
  const shown = nasBrowserFilteredEntries();
  if (!shown.length) {
    list.innerHTML = `<div class="nas-browser-empty muted">没有匹配「${escapeHtml(nasBrowserState.filter)}」的文件夹</div>`;
    return;
  }
  // Leaf folders (no subdirectories) are product-folder candidates: clicking one
  // selects it immediately so operators can queue many styles quickly. Folders
  // that still contain subdirectories keep the drill-in behavior. Rows whose
  // `has_subdirs` is null (from a fast shallow open) are not yet classified:
  // treat them as drill-in rows until the background probe fills the marker in.
  const sep = nasBrowserState.currentPath.includes("\\") ? "\\" : (nasBrowserState.currentPath.includes("/") ? "/" : "\\");
  const rowFor = (entry) => {
    const full = `${nasBrowserState.currentPath.replace(/[\\/]+$/, "")}${sep}${entry.name}`;
    const isSelected = nasBrowserState.selected.includes(full);
    const safeName = escapeHtml(entry.name);
    if (entry.has_subdirs === false) {
      return `<button type="button" class="nas-browser-item nas-browser-item--leaf${isSelected ? " is-selected" : ""}" data-nas-select="${escapeHtml(entry.name)}" title="点击即加入已选（无子目录）">${isSelected ? "✅" : "📁"} <span class="nas-browser-item-name">${safeName}</span><span class="nas-browser-leaf-tag">${isSelected ? "已选" : "款式"}</span></button>`;
    }
    return `<button type="button" class="nas-browser-item" data-nas-enter="${escapeHtml(entry.name)}" title="${entry.has_subdirs == null ? "正在识别是否为款式文件夹，可先点进下一层" : "点进下一层"}">📂 <span class="nas-browser-item-name">${safeName}</span>${entry.has_subdirs == null ? '<span class="nas-browser-unknown-tag">识别中…</span>' : '<span class="nas-browser-branch">›</span>'}</button>`;
  };
  if (!Number.isFinite(nasBrowserState.renderLimit) || nasBrowserState.renderLimit <= 0) {
    nasBrowserState.renderLimit = NAS_BROWSER_RENDER_PAGE;
  }
  const visible = shown.slice(0, nasBrowserState.renderLimit);
  list.innerHTML = visible.map(rowFor).join("");
  if (shown.length > visible.length) {
    list.insertAdjacentHTML(
      "beforeend",
      `<p class="nas-browser-render-limit muted">已显示 ${visible.length} / 共 ${shown.length} 个，下滑到底自动加载更多…</p>`,
    );
  }
}

// 列表滚动接近底部时追加下一页（无限滚动，替代旧的「只显示前 300 个」截断）。
function nasBrowserMaybeLoadMoreRows() {
  const list = $("nas-browser-list");
  if (!list) return;
  const shownCount = nasBrowserFilteredEntries().length;
  if ((nasBrowserState.renderLimit || 0) >= shownCount) return;
  if (list.scrollTop + list.clientHeight < list.scrollHeight - NAS_BROWSER_LOAD_MORE_THRESHOLD_PX) return;
  nasBrowserState.renderLimit += NAS_BROWSER_RENDER_PAGE;
  // innerHTML 重建后内容只会变长，浏览器会保留原 scrollTop，不会跳回顶部。
  renderNasBrowserList();
}

// 对话框标记在页面里（script 位于 body 末尾），加载时元素已存在，直接挂一次监听。
const nasBrowserListElement = document.getElementById("nas-browser-list");
if (nasBrowserListElement) {
  nasBrowserListElement.addEventListener("scroll", nasBrowserMaybeLoadMoreRows, { passive: true });
}

function nasBrowserSelectCurrent() {
  const chosen = nasBrowserState.currentPath;
  if (!chosen) return;
  const input = $("base-product-folder");
  if (input) input.value = chosen;
  const result = $("base-product-result");
  if (result) result.textContent = `已选择：${chosen}`;
  $("nas-browser-dialog")?.close();
}

function nasBrowserGoUp() {
  const cur = nasBrowserState.currentPath;
  if (!cur || cur === nasBrowserState.root) return;
  const trimmed = cur.replace(/[\\/]+$/, "");
  const idx = Math.max(trimmed.lastIndexOf("\\"), trimmed.lastIndexOf("/"));
  if (idx <= 0) return;
  nasBrowserNavigate(trimmed.slice(0, idx));
}

async function createBaseProductFromFolder() {
  const result = $("base-product-result");
  const rootPath = $("base-product-folder").value.trim();
  // New default: if the input box is empty, jump straight into the NAS browser
  // so the operator can pick one or more product folders visually. If a path is
  // already pasted in, keep the one-shot legacy path (single folder, no dialog).
  if (!rootPath) {
    await browseNasProductFolder();
    return;
  }
  const createBtn = $("create-base-product-btn");
  if (createBtn) { createBtn.disabled = true; createBtn.classList.add("is-loading"); }
  result.innerHTML = `<span class="with-spinner"><span class="loading-spinner"></span>正在扫描并创建基础商品…</span>`;
  setNasImportPanel("", [{ path: rootPath, status: "running", stage: "scan" }]);
  try {
    const product = await api("/api/base-products/from-folder", {
      method: "POST",
      body: JSON.stringify({
        root_path: rootPath,
        category: categoryProfile($("base-product-category").value)?.product_line || $("base-product-category").value,
      }),
    });
    state.selectedBaseProductId = product.id;
    setNasImportPanel("", [{ path: rootPath, status: "succeeded", product }]);
    result.textContent = `已创建基础商品 ${product.main_sku || product.group_id || ""}，识别颜色 ${(product.recognized_colors || []).join(", ") || "无"}`;
    await Promise.all([loadBaseProducts(), loadAssets(), loadReadiness()]);
  } catch (error) {
    setNasImportPanel("", [{ path: rootPath, status: "failed", error: String(error?.message || error) }]);
    throw error;
  } finally {
    if (createBtn) { createBtn.disabled = false; createBtn.classList.remove("is-loading"); }
  }
}

function selectedCnscTargetSiteStores() {
  const select = $("store-template-target-sites");
  for (const row of document.querySelectorAll("[data-cnsc-target-store-row]")) {
    const storeId = row.dataset.cnscTargetStoreRow || "";
    const current = state.cnscTargetStoreConfigs.get(storeId) || {};
    const activity = row.querySelector("[data-cnsc-target-activity]");
    const selectedActivity = activity?.selectedOptions?.[0];
    state.cnscTargetStoreConfigs.set(storeId, {
      ...current,
      mabang_discount_id: activity?.value || "",
      discount_name: selectedActivity?.dataset.discountName || selectedActivity?.textContent || "",
      discount_label: selectedActivity?.dataset.discountLabel || selectedActivity?.textContent || "",
      discount_percent: row.querySelector("[data-cnsc-target-discount-percent]")?.value || "",
    });
  }
  return [...(select?.selectedOptions || [])].map((option) => {
    const config = state.cnscTargetStoreConfigs.get(option.value) || {};
    return {
      site_code: String(option.dataset.siteCode || "").trim().toUpperCase(),
      store_id: option.value || "",
      store_name: option.dataset.storeName || option.textContent || "",
      mabang_discount_id: config.mabang_discount_id || "",
      discount_name: config.discount_name || "",
      discount_label: config.discount_label || "",
      discount_percent: config.discount_percent ?? "",
      discount_catalog_loaded: config.discount_catalog_loaded === true,
      discount_catalog: config.discount_catalog || [],
    };
  }).filter((item) => item.site_code && item.store_id);
}

function renderCnscTargetStoreConfigs() {
  const body = $("store-template-target-store-config-body");
  const select = $("store-template-target-sites");
  if (!body || !select) return;
  const defaultDiscount = $("store-template-default-site-discount-percent")?.value || 40;
  const targets = [...select.selectedOptions];
  if (!targets.length) {
    body.innerHTML = '<tr><td colspan="5" class="muted">请先选择目标站点店铺</td></tr>';
    return;
  }
  const tierPrice = (tierId, code) => {
    const prices = state.cnscPricePreview?.tiers?.find((tier) => tier.id === tierId)?.site_prices?.[code] || {};
    return prices.original_price && prices.sale_price ? `${prices.original_price} / ${prices.sale_price}` : "待配置";
  };
  body.innerHTML = targets.map((option) => {
    const storeId = option.value || "";
    const code = String(option.dataset.siteCode || "").trim().toUpperCase();
    const config = state.cnscTargetStoreConfigs.get(storeId) || {};
    const catalog = [...(config.discount_catalog || [])];
    if (config.mabang_discount_id && !catalog.some((item) => (item.mabang_discount_id || item.activity_id) === config.mabang_discount_id)) {
      catalog.unshift({ mabang_discount_id: config.mabang_discount_id, name: config.discount_name || config.discount_label || "当前活动" });
    }
    const options = ['<option value="">选择折扣活动</option>', ...catalog.map((item) => {
      const id = item.mabang_discount_id || item.activity_id || "";
      const name = item.name || item.discount_name || id;
      return `<option value="${escapeHtml(id)}" data-discount-name="${escapeHtml(name)}" data-discount-label="${escapeHtml(item.discount_label || name)}" ${id === config.mabang_discount_id ? "selected" : ""}>${escapeHtml(name)}</option>`;
    })].join("");
    return `<tr data-cnsc-target-store-row="${escapeHtml(storeId)}"><td><strong>${escapeHtml(code)}</strong><small>${escapeHtml(option.dataset.storeName || option.textContent || storeId)}</small></td><td><select data-cnsc-target-activity>${options}</select></td><td><input data-cnsc-target-discount-percent type="number" min="0.01" max="99.99" step="0.01" value="${escapeHtml(config.discount_percent ?? "")}" placeholder="继承 ${escapeHtml(defaultDiscount)}" /></td><td>${escapeHtml(tierPrice("standard", code))}</td><td>${escapeHtml(tierPrice("large", code))}</td></tr>`;
  }).join("");
}

function recalculateCnscSitePrices(siteCode, rawPercent) {
  const code = String(siteCode || "").trim().toUpperCase();
  const fallback = Number($("store-template-default-site-discount-percent")?.value || 40);
  const percent = String(rawPercent ?? "").trim() ? Number(rawPercent) : fallback;
  if (!code || !Number.isFinite(percent) || percent <= 0 || percent >= 100) return;
  for (const tier of state.cnscPricePreview?.tiers || []) {
    const prices = tier.site_prices?.[code];
    const original = Number(prices?.original_price);
    if (!prices || !Number.isFinite(original) || original <= 0) continue;
    prices.sale_price = Number((original * (1 - percent / 100)).toFixed(2));
  }
}

function cnscTierSizes(tierId) {
  return String($(`store-template-cnsc-${tierId}-sizes`)?.value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function cnscPriceTiers() {
  return [
    { id: "standard", sizes: cnscTierSizes("standard"), global_price_cny: $("store-template-cnsc-global-standard")?.value || "", site_prices: state.cnscPricePreview?.tiers?.find((tier) => tier.id === "standard")?.site_prices || {} },
    { id: "large", sizes: cnscTierSizes("large"), global_price_cny: $("store-template-cnsc-global-large")?.value || "", site_prices: state.cnscPricePreview?.tiers?.find((tier) => tier.id === "large")?.site_prices || {} },
  ];
}

const LOCAL_UPLOAD_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".avi", ".mkv"]);
const {
  planLocalFolderImport,
  createImportQueue,
  splitDuplicateLocalImportGroups,
  dropContainedLocalImportBatches,
  folderSkuToken,
} = window.LocalFolderBatch || {};

function localUploadStatus(message, { error = false } = {}) {
  const target = $("local-base-product-upload-status");
  if (!target) return;
  target.hidden = !message;
  target.textContent = message || "";
  target.classList.toggle("upload-error", error);
}

// 本地导入队列：连续选择多个目录时，每个商品目录排队、串行执行，操作员不必等
// 上一次导入结束。队列只活在当前页面——File 句柄无法持久化，刷新会丢弃等待项。
let localImportQueue = null;
const localImportDetails = new Map();  // queue_id -> 该商品的实时/最终文案
let localImportActiveDetailEl = null;  // 正在跑那一行的明细节点（避免整表重画）
let localImportDrainedResults = 0;     // 已收尾过的结果条数，避免重复刷新资产/就绪
let localImportQueueCollapsed = false; // 手动关闭或空闲自动收起；有新任务时自动再展开
let localImportIdleTimer = 0;          // 空闲收起计时器
const LOCAL_IMPORT_IDLE_COLLAPSE_MS = 120000; // 跑完 2 分钟没动静就自动收起（可随时点关闭/显示）

const LOCAL_IMPORT_GLYPHS = { running: "▶", waiting: "⏳", succeeded: "✅", failed: "❌" };

// NAS 导入（服务端批量任务）也显示在同一个导入队列里：这里镜像它的每个目录状态，
// 让"本地 / NAS"两种来源在界面上是同一套队列 + 同一套重试入口。
let nasImportPanelState = { taskId: "", items: [] };

function localImportTaskLabel(task) {
  const folderName = task?.folderName || "";
  return task?.rootName && task.rootName !== folderName ? `${task.rootName} / ${folderName}` : folderName;
}

function localImportQueueDepth(snapshot) {
  return (snapshot?.pending?.length || 0) + (snapshot?.active ? 1 : 0);
}

function setLocalImportDetail(queueId, message) {
  if (!queueId) return;
  localImportDetails.set(queueId, message);
  if (localImportActiveDetailEl) localImportActiveDetailEl.textContent = message;
}

function localImportQueueRow(task, status, detail, options = {}) {
  const label = options.label !== undefined ? options.label : localImportTaskLabel(task);
  const activeAttr = status === "running" ? " data-local-import-active-detail" : "";
  const sourceBadge = options.source ? `<small class="local-import-queue-source">${escapeHtml(options.source)}</small>` : "";
  const actions = [];
  if (status === "waiting" && task?.queue_id) {
    actions.push(`<button type="button" class="local-import-queue-remove" data-local-import-remove="${escapeHtml(task.queue_id)}">移除</button>`);
  }
  if (options.retryKind === "import") {
    actions.push(`<button type="button" class="local-import-queue-retry" data-import-retry="${escapeHtml(options.retryValue || "")}" data-import-retry-kind="${escapeHtml(options.retryKind)}">重试导入</button>`);
  }
  if (options.retryKind === "ai") {
    actions.push(`<button type="button" class="local-import-queue-retry" data-import-retry="${escapeHtml(options.retryValue || "")}" data-import-retry-kind="ai">重试 AI 识别</button>`);
  }
  actions.push(...(options.extraActions || []));
  return `<li class="local-import-queue-item local-import-queue-item--${status}">
    <span class="local-import-queue-glyph" aria-hidden="true">${LOCAL_IMPORT_GLYPHS[status] || "•"}</span>
    <span class="local-import-queue-name" title="${escapeHtml(label)}">${escapeHtml(label)}</span>
    ${sourceBadge}
    <span class="local-import-queue-detail"${activeAttr}>${escapeHtml(detail)}</span>
    ${actions.join("")}
  </li>`;
}

// 结果行共用：导入失败给「重试导入」，AI 识别失败给「重试 AI 识别」。
function importResultActions(status, { retryValue = "", productId = "", aiError = "", source = "local" } = {}) {
  if (status === "failed") return { retryKind: "import", retryValue };
  if (String(aiError || "").trim() && productId) return { retryKind: "ai", retryValue: productId };
  return {};
}

function nasImportItems() {
  return (nasImportPanelState.items || []).map((item) => ({ ...item }));
}

function nasImportDetail(item) {
  const status = String(item.status || "");
  if (status === "failed") return String(item.error || "导入失败");
  if (status === "succeeded") {
    return String(item.product?.ai_generation_error || "").trim()
      ? `AI 识别失败：${item.product.ai_generation_error}`
      : "已创建基础商品";
  }
  if (status === "running") return item.stage === "ai" ? "AI 识别中…" : "扫描 NAS…";
  return "等待中";
}

function setNasImportPanel(taskId, items) {
  nasImportPanelState = { taskId: String(taskId || ""), items: Array.from(items || []) };
  onLocalImportQueueChange({ running: false, active: null, pending: [], results: [] });
}

function clearNasImportPanel() {
  nasImportPanelState = { taskId: "", items: [] };
  renderLocalImportQueue();
}

function renderLocalImportQueue(snapshot = localImportQueue?.snapshot()) {
  const panel = $("local-import-queue");
  if (!panel) return;
  localImportActiveDetailEl = null;
  const nasItems = nasImportItems();
  const depth = localImportQueueDepth(snapshot) + (snapshot?.results?.length || 0) + nasItems.length;
  const showBtn = $("local-import-queue-show");
  // 收起（手动关闭或空闲自动收起）时不渲染面板，但给一个恢复入口，队列内容不丢。
  if (showBtn) {
    showBtn.hidden = !(depth > 0 && localImportQueueCollapsed);
    if (!showBtn.hidden) showBtn.textContent = `显示导入队列（${depth}）`;
  }
  panel.hidden = depth === 0 || localImportQueueCollapsed;
  if (!depth) return;

  const succeeded = (snapshot.results || []).filter((entry) => entry.status === "succeeded");
  const failed = (snapshot.results || []).filter((entry) => entry.status === "failed");
  const summary = $("local-import-queue-summary");
  if (summary) {
    const nasFailed = nasItems.filter((item) => String(item.status || "") === "failed"
      || String(item.product?.ai_generation_error || "").trim()).length;
    const running = (snapshot.active ? 1 : 0) + nasItems.filter((item) => String(item.status || "") === "running").length;
    const waiting = snapshot.pending.length + nasItems.filter((item) => !item.status || item.status === "queued").length;
    const done = snapshot.results.length + nasItems.filter((item) => item.status === "succeeded" || item.status === "failed").length;
    const parts = [];
    if (running) parts.push(`进行中 ${running}`);
    if (waiting) parts.push(`等待 ${waiting}`);
    if (done) parts.push(`已完成 ${done}${(failed.length + nasFailed) ? `（需重试 ${failed.length + nasFailed}）` : ""}`);
    summary.textContent = `导入队列：${parts.join(" · ")}`;
  }
  const clearBtn = $("local-import-queue-clear");
  if (clearBtn) clearBtn.hidden = !snapshot.pending.length;

  const list = $("local-import-queue-list");
  if (!list) return;
  const rows = [];
  if (snapshot.active) {
    rows.push(localImportQueueRow(snapshot.active, "running", localImportDetails.get(snapshot.active.queue_id) || "上传中…"));
  }
  for (const task of snapshot.pending) rows.push(localImportQueueRow(task, "waiting", "等待中"));
  // 最新完成的排在最前，操作员一眼能看到刚结束的目录。
  for (const entry of [...snapshot.results].reverse()) {
    const detail = entry.status === "succeeded"
      ? (localImportDetails.get(entry.task.queue_id) || "已创建基础商品")
      : String(entry.error?.message || entry.error || localImportDetails.get(entry.task.queue_id) || "导入失败");
    // 本地队列的重试：失败 → 重新入队同一个目录；AI 识别失败 → 重跑该商品的 AI 识别。
    const product = entry.value?.product || {};
    const actions = importResultActions(entry.status, {
      retryValue: entry.task?.queue_id || "",
      productId: product.id || "",
      aiError: entry.value?.aiSummary?.error || product.ai_generation_error || "",
    });
    rows.push(localImportQueueRow(entry.task, entry.status, detail, actions));
  }
  // NAS 导入（服务端任务）的每个目录同样一行；失败/AI 失败给重试按钮。
  for (const item of [...nasItems].reverse()) {
    const status = String(item.status || "");
    const rowStatus = status === "running" ? "running" : (status === "failed" ? "failed" : (status === "succeeded" ? "succeeded" : "waiting"));
    const folderName = String(item.path || "").split(/[\\/]/).filter(Boolean).at(-1) || item.path || "NAS 目录";
    const actions = importResultActions(rowStatus, {
      retryValue: String(item.path || ""),
      productId: item.product?.id || "",
      aiError: item.product?.ai_generation_error || "",
    });
    rows.push(localImportQueueRow({ folderName }, rowStatus, nasImportDetail(item), {
      label: folderName,
      source: "NAS",
      ...actions,
    }));
  }
  list.innerHTML = rows.join("");
  if (snapshot.active) localImportActiveDetailEl = list.querySelector("[data-local-import-active-detail]");
}

function updateLocalImportStatusLine(snapshot) {
  // 运行中这一行归当前商品的进度文案使用；队列跑空后才写总结。
  if (!snapshot || snapshot.running || !snapshot.results.length) return;
  const succeeded = snapshot.results.filter((entry) => entry.status === "succeeded");
  const failed = snapshot.results.filter((entry) => entry.status === "failed");
  const failureNames = failed
    .map((entry) => `${entry.task.folderName}(${entry.error?.message || entry.error || "创建失败"})`)
    .join("；");
  const aiFailures = succeeded.filter((entry) => entry.value?.aiSummary?.error);
  const aiHint = aiFailures.length ? `；其中 ${aiFailures.length} 个 AI 识别失败，可在商品维护中重试` : "";
  localUploadStatus(
    `本地批量导入完成：成功 ${succeeded.length} 个，失败 ${failed.length} 个${failureNames ? `：${failureNames}` : ""}${aiHint}`,
    { error: failed.length > 0 || aiFailures.length > 0 },
  );
}

function scheduleLocalImportIdleCollapse(snapshot) {
  if (localImportIdleTimer) {
    window.clearTimeout(localImportIdleTimer);
    localImportIdleTimer = 0;
  }
  // 还有在跑/等待的项时不收起；跑完且没有新动静才排一次自动收起（NAS 行同样算数）。
  const nasBusy = (nasImportPanelState.items || []).some((item) => !item.status || item.status === "queued" || item.status === "running");
  if (localImportQueueDepth(snapshot) || nasBusy) return;
  if (!(snapshot?.results?.length || 0) && !(nasImportPanelState.items || []).length) return;
  localImportIdleTimer = window.setTimeout(() => {
    localImportIdleTimer = 0;
    localImportQueueCollapsed = true;
    renderLocalImportQueue();
  }, LOCAL_IMPORT_IDLE_COLLAPSE_MS);
}

function onLocalImportQueueChange(snapshot) {
  // 有新任务（本地或 NAS 的等待/进行中项）就自动展开，操作员不用去点「显示导入队列」。
  if (localImportQueueDepth(snapshot)
    || (nasImportPanelState.items || []).some((item) => !item.status || item.status === "queued" || item.status === "running")) {
    localImportQueueCollapsed = false;
  }
  scheduleLocalImportIdleCollapse(snapshot);
  renderLocalImportQueue(snapshot);
  updateLocalImportStatusLine(snapshot);
  if (snapshot.running || !snapshot.results.length) return;
  if (snapshot.results.length === localImportDrainedResults) return;
  localImportDrainedResults = snapshot.results.length;
  Promise.all([loadAssets(), loadReadiness()]).catch(() => {});
}

function localImportModuleReady() {
  return typeof planLocalFolderImport === "function"
    && typeof createImportQueue === "function"
    && typeof splitDuplicateLocalImportGroups === "function"
    && typeof dropContainedLocalImportBatches === "function"
    && typeof folderSkuToken === "function";
}

// 队列里的一项 = 一个商品目录：上传完成后走原有的基础商品创建接口，成功后
// 立刻刷新商品列表，让操作员一边等后面的项一边处理刚建好的商品。
async function runLocalImportQueueItem(task) {
  const product = await uploadLocalProductGroup(task, {
    category: task.category,
    statusPrefix: `本地导入队列 · ${localImportTaskLabel(task)} · `,
    onDetail: (message) => setLocalImportDetail(task.queue_id, message),
  });
  state.selectedBaseProductId = product.id;
  // 商品和素材一起刷新：只刷商品列表时 state.assets 还是旧的，卡片会渲染成"没有图"，
  // 操作员要等整队跑完（或手动刷新页面）才看到图。
  await Promise.all([loadBaseProducts(), loadAssets()]).catch(() => {});
  const aiSummary = aiImportStageSummary(product);
  setLocalImportDetail(task.queue_id, aiSummary.error ? `AI 识别失败：${aiSummary.error}` : (aiSummary.text || "已创建基础商品"));
  return { folderName: task.folderName, product, aiSummary };
}

// 队列项重试：① 导入失败 → 重新入队/重新提交；② AI 识别出错 → 只重跑该商品的 AI 识别。
async function retryImportQueueItem(value, kind) {
  if (!value) return;
  const queue = ensureLocalImportQueue();
  if (kind === "ai") {
    await retryBaseProductAiFor(value);
    return;
  }
  // 本地目录：把失败结果清掉再入队同一个任务（文件句柄还在内存里）。
  const localTask = queue?.snapshot().results
    .filter((entry) => entry.status === "failed")
    .map((entry) => entry.task)
    .find((task) => String(task?.queue_id || "") === value);
  if (localTask && queue) {
    queue.removeResult?.(value);
    queue.enqueue([{ ...localTask, queue_id: undefined }]);
    localImportQueueCollapsed = false;
    renderLocalImportQueue();
    return;
  }
  // NAS 目录：单独重投这一个路径（服务端仍走同一套扫描 + AI 识别）。
  const path = String(value || "");
  if (path) {
    const existing = nasImportPanelState.items.filter((item) => String(item.path || "") !== path);
    setNasImportPanel(nasImportPanelState.taskId, [...existing, { path, status: "running", stage: "scan" }]);
    await runNasBatchImport([path], { keepPanel: true });
  }
}

// AI 识别重试：复用商品维护里那条 regenerate-listing，成功/失败都回写到队列行。
async function retryBaseProductAiFor(productId) {
  try {
    const product = await api(`/api/base-products/${encodeURIComponent(productId)}/regenerate-listing`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    const items = nasImportPanelState.items.map((item) => (item.product?.id === productId ? { ...item, product } : item));
    nasImportPanelState = { ...nasImportPanelState, items };
    const localEntries = localImportQueue?.snapshot().results || [];
    for (const entry of localEntries) {
      if (entry.value?.product?.id === productId) {
        entry.value = { ...entry.value, product, aiSummary: aiImportStageSummary(product) };
      }
    }
    renderLocalImportQueue();
    await loadBaseProducts().catch(() => {});
    showOperationFeedback(
      String(product?.ai_generation_error || "").trim() ? `AI 识别仍失败：${product.ai_generation_error}` : "AI 识别已重跑完成",
      String(product?.ai_generation_error || "").trim() ? "error" : "success",
    );
  } catch (error) {
    operationError(error, "重试 AI 识别失败");
  }
}

function ensureLocalImportQueue() {
  if (localImportQueue) return localImportQueue;
  if (!localImportModuleReady()) return null;
  localImportQueue = createImportQueue({ run: runLocalImportQueueItem, onChange: onLocalImportQueueChange });
  return localImportQueue;
}

// 队列里还有没跑完的商品时拦一下刷新：File 句柄不能持久化，刷新会丢掉等待项。
window.addEventListener("beforeunload", (event) => {
  if (!localImportQueue || !localImportQueueDepth(localImportQueue.snapshot())) return;
  event.preventDefault();
  event.returnValue = "";
});

function uploadLocalFile(uploadId, relativePath, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/local-folder-uploads/${encodeURIComponent(uploadId)}/files?path=${encodeURIComponent(relativePath)}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded);
    };
    xhr.onload = () => {
      let payload = {};
      try { payload = JSON.parse(xhr.responseText || "{}"); } catch (_) { /* handled below */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(payload);
      else reject(new Error(payload.error || `上传失败 (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("网络错误，文件上传失败"));
    xhr.send(file);
  });
}

async function uploadLocalProductGroup(group, { category = "", statusPrefix = "", onDetail } = {}) {
  const files = group.files || [];
  const manifest = group.manifest || [];
  const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
  // 进度同时写进页面状态行和队列行：状态行只跟当前商品，队列行留作该商品的明细。
  const report = (message) => {
    localUploadStatus(message);
    if (typeof onDetail === "function") onDetail(message);
  };
  let uploadId = "";
  try {
    report(`${statusPrefix}准备上传 ${files.length} 个文件…`);
    const session = await api("/api/local-folder-uploads", {
      method: "POST",
      body: JSON.stringify({ folder_name: group.folderName, files: manifest }),
    });
    uploadId = session.upload_id;
    let completedBytes = 0;
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      const relativePath = manifest[index].path;
      let currentLoaded = 0;
      report(`${statusPrefix}上传中 (${index + 1}/${files.length})：${relativePath} · 0%`);
      await uploadLocalFile(uploadId, relativePath, file, (loaded) => {
        completedBytes += loaded - currentLoaded;
        currentLoaded = loaded;
        const percent = totalBytes ? Math.floor((completedBytes / totalBytes) * 100) : 100;
        report(`${statusPrefix}上传中 (${index + 1}/${files.length})：${relativePath} · ${percent}%`);
      });
    }
    report(`${statusPrefix}上传完成，正在创建基础商品…`);
    const requestPromise = api(`/api/local-folder-uploads/${encodeURIComponent(uploadId)}/complete`, {
      method: "POST",
      body: JSON.stringify({ category }),
    });
    const ticker = startAiImportStageTicker((message) => report(`${statusPrefix}${message}`));
    try {
      return await requestPromise;
    } finally {
      clearInterval(ticker);
    }
  } catch (error) {
    if (uploadId) await api(`/api/local-folder-uploads/${encodeURIComponent(uploadId)}`, { method: "DELETE" }).catch(() => {});
    throw error;
  }
}

// 本地选目录：优先 File System Access API —— 它不会像 <input webkitdirectory> 那样
// 每次都弹 Chrome 的「上传 N 个文件」确认框；不支持时回退到老 input（会弹浏览器确认）。
async function pickLocalProductFolder() {
  const input = $("local-base-product-folder-upload");
  if (typeof window.showDirectoryPicker === "function") {
    let handle = null;
    try {
      handle = await window.showDirectoryPicker({ id: "local-base-products", mode: "read" });
    } catch (error) {
      if (error && error.name === "AbortError") return;  // 操作员取消
      handle = null;                                     // 其它错误 → 回退老方式
    }
    if (handle) {
      // 前缀带上所选文件夹名：与 <input webkitdirectory> 的层级一致（根目录/子目录/文件），
      // 规划器靠这个判断"多商品文件夹"还是"单个商品"。
      const files = await collectDirectoryHandleFiles(handle, String(handle.name || ""));
      if (!files.length) {
        showOperationFeedback(`「${handle.name}」里没有可导入的图片 / 视频 / 源文件`, "info");
        return;
      }
      await createBaseProductFromLocalFiles(files);
      return;
    }
  }
  input?.click();
}

// 拖拽多个文件夹批量导入：系统选文件夹窗口不支持多选，但拖拽可以一次给多个目录，
// 而且走的是目录条目（webkitGetAsEntry），同样不会触发浏览器的「上传 N 个文件」确认。
async function importDroppedFolders(dataTransfer) {
  const items = [...((dataTransfer && dataTransfer.items) || [])];
  const entries = items
    .map((item) => (typeof item.webkitGetAsEntry === "function" ? item.webkitGetAsEntry() : null))
    .filter(Boolean);
  if (!entries.length) {
    showOperationFeedback("没有识别到可导入的文件夹，请从文件管理器直接拖入文件夹", "info");
    return;
  }
  const batches = [];
  for (const entry of entries) {
    if (entry.isDirectory) {
      // 前缀带上拖入的文件夹名：层级必须是 根目录/子目录/文件，规划器才能分辨商品文件夹。
      const files = await collectFileSystemEntries(entry, String(entry.name || ""));
      if (files.length) batches.push({ name: entry.name, files });
    } else if (entry.isFile) {
      const file = await fileFromFileSystemEntry(entry);
      if (file) {
        try { Object.defineProperty(file, "__relativePath", { value: file.name, enumerable: false }); } catch (_error) { /* 退化成文件名 */ }
        batches.push({ name: file.name, files: [file] });
      }
    }
  }
  if (!batches.length) {
    showOperationFeedback("拖入的文件夹里没有可导入的图片 / 视频 / 源文件", "info");
    return;
  }
  // 同一批拖入里若同时有父目录和它的子目录，只保留父目录（子目录的文件已在上传清单里）。
  const dedupedBatches = dropContainedLocalImportBatches(batches);
  const batchesToImport = dedupedBatches.batches;
  // 每个拖入的顶层文件夹单独规划后入队（规划器是"单根目录"语义：一次一个根）。
  // 逐个结果汇总：重复的目录会被 createBaseProductFromLocalFiles 跳过，这里把
  // 跳过的目录和「会覆盖已有商品」的风险一起说清楚，避免操作员以为全都进去了。
  let queued = 0;
  let fileCount = 0;
  const duplicates = dedupedBatches.dropped.map((batch) => ({ folderName: batch?.name || "", reason: "nested" }));
  const conflicts = [];
  const sameSkuHints = [];
  for (const batch of batchesToImport) {
    const result = await createBaseProductFromLocalFiles(batch.files, { quiet: true });
    queued += Number(result?.added || 0);
    fileCount += Number(result?.fileCount || 0);
    duplicates.push(...(result?.duplicates || []));
    for (const name of result?.conflicts || []) if (!conflicts.includes(name)) conflicts.push(name);
    if (result?.sameSkuHint && !sameSkuHints.includes(result.sameSkuHint)) sameSkuHints.push(result.sameSkuHint);
  }
  const duplicateHint = localImportDuplicateHint(duplicates);
  const riskHint = localImportConflictHint(conflicts) + sameSkuHints.join("");
  if (!queued) {
    showOperationFeedback(`没有新商品加入队列：${duplicateHint || "没有可导入的图片 / 视频 / 源文件"}`, "info");
    return;
  }
  showOperationFeedback(`已把 ${queued} 个文件夹加入导入队列（共 ${fileCount} 个文件）。${duplicateHint}${riskHint}`, "info");
}

// webkitGetAsEntry 的目录条目递归（层级：根目录名/子目录/文件，与 webkitdirectory 一致）。
async function collectFileSystemEntries(directoryEntry, prefix = "") {
  if (!directoryEntry) return [];
  const reader = directoryEntry.createReader();
  const entries = [];
  while (true) {
    const batch = await new Promise((resolve) => reader.readEntries(resolve, () => resolve([])));
    if (!batch.length) break;
    entries.push(...batch);
  }
  const files = [];
  for (const entry of entries) {
    const name = String(entry.name || "");
    if (!name || name.startsWith(".")) continue;
    const path = prefix ? `${prefix}/${name}` : name;
    if (entry.isDirectory) {
      files.push(...(await collectFileSystemEntries(entry, path)));
    } else if (entry.isFile) {
      const file = await fileFromFileSystemEntry(entry);
      if (!file) continue;
      try { Object.defineProperty(file, "__relativePath", { value: path, enumerable: false }); } catch (_error) { /* 退化成文件名 */ }
      files.push(file);
    }
  }
  return files;
}

function fileFromFileSystemEntry(entry) {
  return new Promise((resolve) => {
    try {
      entry.file((file) => resolve(file), () => resolve(null));
    } catch (_error) {
      resolve(null);
    }
  });
}

function setupLocalFolderDropZone() {
  const zone = $("product-maintenance") || document.body;
  if (!zone || zone.dataset.dropZoneReady === "1") return;
  zone.dataset.dropZoneReady = "1";
  const hasFolders = (event) => [...((event.dataTransfer && event.dataTransfer.types) || [])].includes("Files");
  zone.addEventListener("dragover", (event) => {
    if (!hasFolders(event)) return;
    event.preventDefault();
    zone.classList.add("is-drop-target");
  });
  zone.addEventListener("dragleave", (event) => {
    if (event.target === zone) zone.classList.remove("is-drop-target");
  });
  zone.addEventListener("drop", (event) => {
    if (!hasFolders(event)) return;
    event.preventDefault();
    zone.classList.remove("is-drop-target");
    importDroppedFolders(event.dataTransfer).catch((error) => operationError(error, "拖拽导入失败"));
  });
}

// 递归读目录句柄：给每个 File 挂上相对路径（层级与 webkitdirectory 一致：根目录名/子目录/文件）。
async function collectDirectoryHandleFiles(directoryHandle, prefix = "") {
  const files = [];
  for await (const entry of directoryHandle.values()) {
    const name = String(entry?.name || "");
    if (!name || name.startsWith(".")) continue;
    const path = prefix ? `${prefix}/${name}` : name;
    if (entry.kind === "directory") {
      files.push(...(await collectDirectoryHandleFiles(entry, path)));
    } else if (entry.kind === "file") {
      const file = await entry.getFile();
      try {
        Object.defineProperty(file, "__relativePath", { value: path, enumerable: false });
      } catch (_error) {
        // 挂不上就退化成文件名（规划器仍能用，只是少了目录层级）
      }
      files.push(file);
    }
  }
  return files;
}

// 重复导入的原因要让人一眼分清：是「已经导过」还是「拖重了」。
const LOCAL_IMPORT_DUPLICATE_REASONS = {
  existing: "已在队列或本次会话导入",
  repeat: "本次拖入里重复",
  nested: "已包含在本次拖入的上级目录里",
};

function localImportDuplicateHint(duplicates) {
  if (!duplicates.length) return "";
  const items = duplicates.map((item) => `${item.folderName || "未命名"}（${LOCAL_IMPORT_DUPLICATE_REASONS[item.reason] || "重复"}）`);
  return `跳过 ${duplicates.length} 个重复导入：${items.join("、")}。`;
}

// 队列里排队/正在跑 + 本次会话已经导入成功的项：用来识别「同一个目录又拖了一次」。
function knownLocalImportGroups(snapshot) {
  const groups = [];
  if (snapshot?.active) groups.push(snapshot.active);
  groups.push(...(snapshot?.pending || []));
  const liveIds = new Set((state.baseProducts || []).map((item) => String(item?.id || "")).filter(Boolean));
  for (const entry of snapshot?.results || []) {
    if (entry?.status !== "succeeded" || !entry.task) continue;
    // 那次导入的商品已经被删掉了：同一目录要能重新导入，否则「删了再拖」还是被跳过。
    // liveIds 为空多半是列表没加载，不当成「全被删了」。
    const productId = String(entry.value?.product?.id || "");
    if (productId && liveIds.size && !liveIds.has(productId)) continue;
    groups.push(entry.task);
  }
  return groups;
}

// 商品库里已经有同名主 SKU：后端按 group_id 覆盖旧记录（旧副本会失去关联），
// 所以导入前提醒一句——不拦截，操作员可能就是要刷新素材。主 SKU 的推导规则
// 与后端 / 重复导入指纹共用 folderSkuToken。
function localImportLibraryConflicts(groups) {
  const known = new Set();
  for (const product of state.baseProducts || []) {
    for (const value of [product?.main_sku, product?.group_id]) {
      const token = String(value || "").trim().toUpperCase();
      if (token) known.add(token);
    }
  }
  if (!known.size) return [];
  const seen = new Set();
  const conflicts = [];
  for (const group of groups) {
    const token = folderSkuToken(group?.folderName);
    if (!token || !known.has(token) || seen.has(token)) continue;
    seen.add(token);
    conflicts.push(String(group.folderName || token));
  }
  return conflicts;
}

function localImportConflictHint(conflicts) {
  if (!conflicts.length) return "";
  return `${conflicts.join("、")} 在商品库里已有同名主 SKU，导入会覆盖原商品记录（已建副本会失去关联）。`;
}

// 同一批里出现多个「同一主 SKU 但内容不同」的目录：后端按 group_id 归类，
// 后导入的会顶掉先导入的那个商品，得提醒一句（内容完全相同的已被按重复跳过）。
function localImportSameSkuHint(groups) {
  const counts = new Map();
  for (const group of groups) {
    const token = folderSkuToken(group?.folderName);
    if (token) counts.set(token, (counts.get(token) || 0) + 1);
  }
  const repeated = [...counts].filter(([, count]) => count > 1).map(([token]) => token);
  if (!repeated.length) return "";
  return `${repeated.join("、")} 在这批里对应多个目录：商品库按主 SKU 归类，后导入的会覆盖先导入的那个。`;
}

async function createBaseProductFromLocalFiles(allFiles, options = {}) {
  if (!localImportModuleReady()) {
    throw new Error("本地批量导入组件未加载，请刷新页面后重试");
  }
  const queue = ensureLocalImportQueue();
  if (!queue) throw new Error("本地批量导入队列未加载，请刷新页面后重试");
  const plan = planLocalFolderImport(allFiles, LOCAL_UPLOAD_EXTENSIONS);
  const snapshot = queue.snapshot();
  const waiting = localImportQueueDepth(snapshot);
  // 重复导入防护：同一个商品文件夹（同名 + 同一批文件清单）在队列、本次会话、本次
  // 拖入里只入队一次；同时拖了父目录和子目录时只留父目录。后端按 group_id 覆盖同名
  // 商品、素材还会和旧素材混在一起，重复入队会把商品真的导两次/把旧副本顶掉，
  // 所以这里直接跳过，并在提示里点名跳过了哪些目录、为什么。
  const { groups, duplicates } = splitDuplicateLocalImportGroups(
    plan.groups,
    knownLocalImportGroups(snapshot),
    { rootName: plan.rootName },
  );
  const validFileCount = groups.reduce((total, group) => total + group.files.length, 0);
  const ignoredFileCount = plan.ignoredRootFileCount + plan.filteredFileCount;
  const duplicateHint = localImportDuplicateHint(duplicates);
  const conflictNames = localImportLibraryConflicts(groups);
  const conflictHint = localImportConflictHint(conflictNames);
  const sameSkuHint = localImportSameSkuHint(groups);
  // 不再弹确认框（操作员反馈「总要再点一次」）：直接入队，队列行上可随时移除；
  // 忽略文件数、重复目录等提示改在提示行里显示。单目录导入沿用原来的安静行为，
  // 只有出现重复/覆盖风险时才提示。
  if (options.quiet !== true && (plan.mode === "batch" || duplicates.length || conflictNames.length)) {
    if (!groups.length) {
      showOperationFeedback(`没有新商品加入队列：${duplicateHint || "没有可导入的图片 / 视频 / 源文件"}`, "info");
    } else {
      const waitingHint = waiting ? `当前队列还有 ${waiting} 个商品在导入，新目录会排在它们之后。` : "";
      showOperationFeedback(
        `已加入 ${groups.length} 个商品文件夹（${validFileCount} 个有效文件，忽略 ${ignoredFileCount} 个）。${waitingHint}${duplicateHint}${conflictHint}${sameSkuHint}`,
        "info",
      );
    }
  }
  const category = categoryProfile($("base-product-category").value)?.product_line || $("base-product-category").value;
  // 入队后立刻返回：上传与 AI 识别在后台串行跑，操作员可以继续选下一个目录。
  if (groups.length) {
    queue.enqueue(groups.map((group) => ({
      folderName: group.folderName,
      rootName: plan.rootName,
      files: group.files,
      manifest: group.manifest,
      category,
      mode: plan.mode,
    })));
  }
  return {
    plan,
    added: groups.length,
    fileCount: validFileCount,
    duplicates,
    conflicts: conflictNames,
    sameSkuHint,
  };
}

function validateStoreTemplateForm() {
  const listingMode = $("store-template-listing-mode")?.value || "site";
  const required = [
    ["store-template-store", "店铺"],
    ["store-template-name", "模板名"],
    ["store-template-category-id", "平台类目"],
    ["store-template-normal-price", "原价"],
    ["store-template-stock", "库存"],
    ["store-template-weight-kg", "重量"],
    ["store-template-package-length-cm", "包裹长度"],
    ["store-template-package-width-cm", "包裹宽度"],
    ["store-template-package-height-cm", "包裹高度"],
  ];
  if (listingMode === "cnsc_global") {
    required.splice(required.findIndex(([id]) => id === "store-template-store"), 1);
    required.splice(required.findIndex(([id]) => id === "store-template-normal-price"), 1);
    required.splice(required.findIndex(([id]) => id === "store-template-stock"), 1);
    required.push(["store-template-cnsc-store", "CNSC 店铺"]);
    required.push(["store-template-cnsc-global-standard", "standard 全球价"]);
    required.push(["store-template-cnsc-global-large", "large 全球价"]);
    const targetSites = selectedCnscTargetSiteStores();
    if (!targetSites.length) return "请至少配置一个目标站点店铺";
    if (new Set(targetSites.map((item) => item.site_code)).size !== targetSites.length) return "同一站点只能选择一个目标店铺";
    const defaultDiscount = Number($("store-template-default-site-discount-percent")?.value);
    if (!Number.isFinite(defaultDiscount) || defaultDiscount <= 0 || defaultDiscount >= 100) return "默认折扣必须大于 0 且小于 100";
    const missingActivity = targetSites.find((item) => !item.mabang_discount_id);
    if (missingActivity) return `${missingActivity.site_code} ${missingActivity.store_name} 缺少折扣活动`;
    const invalidOverride = targetSites.find((item) => String(item.discount_percent || "").trim() && !(Number(item.discount_percent) > 0 && Number(item.discount_percent) < 100));
    if (invalidOverride) return `${invalidOverride.site_code} %OFF 必须大于 0 且小于 100`;
    const tiers = cnscPriceTiers();
    const selectedSizes = selectedTemplateSizes();
    const assignedSizes = tiers.flatMap((tier) => tier.sizes);
    if (new Set(assignedSizes).size !== assignedSizes.length || selectedSizes.some((size) => !assignedSizes.includes(size)) || assignedSizes.some((size) => !selectedSizes.includes(size))) {
      return "CNSC 价格档位必须覆盖全部已选尺码且不能重复";
    }
    if (!state.cnscPricePreview) return "请先解析并应用站点价格表";
  }
  for (const [id, label] of required) {
    const control = $(id);
    const value = String(control?.value || "").trim();
    control?.removeAttribute("aria-invalid");
    if (value) continue;
    control?.setAttribute("aria-invalid", "true");
    control?.focus();
    return `${label}为必填项`;
  }
  if (!selectedTemplateSizes().length) return "请至少选择一个尺码";
  // 选了类目但还没绑定必填规则：服务端会报 "requires an active category profile"，
  // 这里提前用中文说清怎么解（采集必填项或改选已采集的类目）。
  if ($("store-template-category-id")?.value.trim() && !$("store-template-category-profile")?.value.trim()) {
    return "已选择类目但还没有该类目的必填项规则：请在「平台类目」选择器里点「采集必填项」并确认导入，或改选已采集过规则的类目";
  }
  if (listingMode !== "cnsc_global" && $("store-template-default-sku-enabled")?.checked) {
    const defaultSkuRequirements = [
      ["store-template-default-sku-color", "默认 SKU 颜色名称"],
      ["store-template-default-sku-code", "默认 SKU 编码"],
      ["store-template-default-sku-normal-price", "默认 SKU 售价"],
      ["store-template-default-sku-sale-price", "默认 SKU 折扣价"],
    ];
    for (const [id, label] of defaultSkuRequirements) {
      const control = $(id);
      const value = String(control?.value || "").trim();
      control?.removeAttribute("aria-invalid");
      if (value) continue;
      control?.setAttribute("aria-invalid", "true");
      control?.focus();
      return `${label}为必填项`;
    }
    const hasImage = Boolean(state.defaultSkuImageAsset?.id) || Boolean($("store-template-default-sku-path")?.value.trim());
    if (!hasImage) return "默认 SKU 需要上传图片或填写图片路径";
    // 分尺码覆盖价允许只填一边（另一边自动换算/继承，与主表格同一套语义），无需成对校验。
  }
  return "";
}

// 默认 SKU（白T恤）的「按尺码设置价格」：与主表格 size_price_overrides 同一套
// 语义——原价留空＝继承默认 SKU 的售价；折扣价留空＝按默认 SKU 自己的折扣比例
// 换算（默认 SKU 没填比例按六折）；两项都填按填的走。行随上方勾选的尺码自动增减。
function defaultSkuNormalPrice() {
  return $("store-template-default-sku-normal-price")?.value.trim() || "";
}

function defaultSkuSalePrice() {
  return $("store-template-default-sku-sale-price")?.value.trim() || "";
}

// 只填原价时的折扣价＝按默认 SKU 自己的 折扣价/售价 比例换算（后台
// storage._default_sku_sale_price_at_ratio 同一套规则），这里只用于占位提示。
function applyDefaultSkuRatio(value) {
  const cents = priceCents(value);
  if (cents === null) return "";
  const normalCents = priceCents(defaultSkuNormalPrice());
  const saleCents = priceCents(defaultSkuSalePrice());
  const ratio = normalCents && saleCents ? saleCents / normalCents : 0.6;
  return (Math.round(cents * ratio) / 100).toFixed(2);
}

function defaultSkuNormalPlaceholder() {
  const inherited = defaultSkuNormalPrice();
  return inherited ? `继承 ${inherited}` : "继承默认 SKU 售价";
}

function defaultSkuSalePlaceholder(normalOverride = "") {
  if (normalOverride) {
    const auto = applyDefaultSkuRatio(normalOverride);
    return auto ? `自动 ${auto}` : "留空自动换算";
  }
  const inherited = defaultSkuSalePrice();
  return inherited ? `继承 ${inherited}` : "继承默认 SKU 折扣价";
}

function renderDefaultSkuSizePriceOverrides(values = {}) {
  const root = $("store-template-default-sku-size-prices");
  if (!root) return;
  const sizes = selectedTemplateSizes();
  if (!sizes.length) {
    root.innerHTML = "";
    return;
  }
  // 存量覆盖的键可能是不带括号补充值的写法（90cm vs 90cm(10-12kg)）：
  // 先按原值精确匹配，再按去括号后的尺码匹配（与后端 _default_sku_size_price 一致）。
  const stripParens = (value) => String(value || "").replace(/（[^）]*）/g, "").replace(/\([^)]*\)/g, "").trim();
  const overrideFor = (size) => {
    if (values[size]) return sizePriceOverride(values[size]);
    const stripped = stripParens(size);
    if (stripped) {
      for (const [key, value] of Object.entries(values || {})) {
        if (stripParens(key) === stripped) return sizePriceOverride(value);
      }
    }
    return { normal_price: "", sale_price: "" };
  };
  root.innerHTML = `<fieldset class="size-price-editor"><legend>按尺码设置价格</legend><small class="muted">留空＝自动：原价继承默认 SKU 售价，折扣价按默认 SKU 的折扣比例换算；填了就按填的走，只作用于默认 SKU。</small><div class="size-price-grid"><strong>尺码</strong><strong>原价</strong><strong>折扣价</strong>${sizes.map((size) => {
    const override = overrideFor(size);
    return `<span>${escapeHtml(size)}</span><input data-default-sku-size-price="${escapeHtml(size)}" type="number" min="0" step="0.01" value="${escapeHtml(override.normal_price)}" placeholder="${escapeHtml(defaultSkuNormalPlaceholder())}" /><input data-default-sku-size-sale-price="${escapeHtml(size)}" type="number" min="0" step="0.01" value="${escapeHtml(override.sale_price)}" placeholder="${escapeHtml(defaultSkuSalePlaceholder(override.normal_price))}" title="留空＝自动换算" />`;
  }).join("")}</div></fieldset>`;
}

function refreshDefaultSkuSizePricePlaceholders() {
  const root = $("store-template-default-sku-size-prices");
  if (!root) return;
  const normalPlaceholder = defaultSkuNormalPlaceholder();
  root.querySelectorAll("[data-default-sku-size-price]").forEach((input) => {
    input.placeholder = normalPlaceholder;
    const saleInput = root.querySelector(`[data-default-sku-size-sale-price="${CSS.escape(input.dataset.defaultSkuSizePrice || "")}"]`);
    if (saleInput) saleInput.placeholder = defaultSkuSalePlaceholder(input.value.trim());
  });
}

function collectDefaultSkuSizePriceOverrides() {
  const overrides = {};
  for (const input of document.querySelectorAll("#store-template-default-sku-size-prices [data-default-sku-size-price]")) {
    const size = input.dataset.defaultSkuSizePrice || "";
    if (!size) continue;
    const saleInput = document.querySelector(`#store-template-default-sku-size-prices [data-default-sku-size-sale-price="${CSS.escape(size)}"]`);
    const entry = {};
    const normal = input.value.trim();
    const sale = saleInput ? saleInput.value.trim() : "";
    if (normal) entry.normal_price = normal;
    if (sale) entry.sale_price = sale;
    if (Object.keys(entry).length) overrides[size] = entry;
  }
  return overrides;
}

function collectStoreTemplateDefaultSku() {
  const enabled = $("store-template-default-sku-enabled")?.checked === true;
  return {
    enabled,
    color_name: $("store-template-default-sku-color")?.value.trim() || "",
    sku: $("store-template-default-sku-code")?.value.trim() || "",
    image_asset_id: state.defaultSkuImageAsset?.id || "",
    image_file_name: state.defaultSkuImageAsset?.file_name || "",
    image_preview_url: state.defaultSkuImageAsset?.preview_url || "",
    image_path: $("store-template-default-sku-path")?.value.trim() || "",
    normal_price: $("store-template-default-sku-normal-price")?.value.trim() || "",
    sale_price: $("store-template-default-sku-sale-price")?.value.trim() || "",
    // 分尺码价格覆盖（每尺码一对 原价/折扣价，可只填一项＝另一项自动），
    // 只作用于默认 SKU 的该尺码；两个都空表示不设置覆盖。
    size_price_overrides: collectDefaultSkuSizePriceOverrides(),
  };
}

function fillStoreTemplateDefaultSku(defaultSku) {
  const config = defaultSku || {};
  if ($("store-template-default-sku-enabled")) $("store-template-default-sku-enabled").checked = config.enabled === true;
  if ($("store-template-default-sku-color")) $("store-template-default-sku-color").value = config.color_name || "";
  if ($("store-template-default-sku-code")) $("store-template-default-sku-code").value = config.sku || "";
  if ($("store-template-default-sku-normal-price")) $("store-template-default-sku-normal-price").value = config.normal_price ?? "";
  if ($("store-template-default-sku-sale-price")) $("store-template-default-sku-sale-price").value = config.sale_price ?? "";
  renderDefaultSkuSizePriceOverrides(config.size_price_overrides || {});
  if ($("store-template-default-sku-path")) $("store-template-default-sku-path").value = config.image_path || "";
  state.defaultSkuImageAsset = config.image_asset_id ? {
    id: config.image_asset_id,
    file_name: config.image_file_name || "",
    preview_url: config.image_preview_url || `/api/uploads/default-sku-image/${encodeURIComponent(config.image_asset_id)}`,
  } : null;
  const fileNameEl = $("store-template-default-sku-file-name");
  if (fileNameEl) fileNameEl.textContent = config.image_file_name || "PNG / JPEG / WebP · 建议 <2MB";
  renderDefaultSkuImagePreview();
}

// AI 推荐类目属性值：把当前渲染的空属性（label + 选项枚举）交给 AI，按模板上下文
// 每个属性挑一个值回填。只填空属性——操作员手填/已有的值保留；枚举属性只从选项里
// 选（服务端会把枚举外的值丢弃），文本属性用简短英文。
async function recommendCategoryAttributesWithAI() {
  const button = $("store-template-ai-attributes-btn");
  const status = $("store-template-ai-attributes-status");
  const controls = [...document.querySelectorAll("#store-template-category-fields [data-category-field-input]")];
  if (!controls.length) {
    showOperationFeedback("请先选择类目，渲染出必填属性后再用 AI 推荐", "error");
    return;
  }
  const targets = [];
  for (const control of controls) {
    const fieldId = control.dataset.categoryFieldInput;
    if (!fieldId) continue;
    const wrapper = control.closest("label.category-field");
    const spanText = (wrapper?.querySelector("span")?.textContent || "").trim();
    const label = spanText.replace(/\s*\*\s*$/, "").trim();
    if (control instanceof HTMLInputElement && control.type === "checkbox") continue; // 布尔属性交给操作员
    if (control instanceof HTMLSelectElement) {
      const options = [...control.options]
        .filter((option) => option.value && option.value !== CUSTOM_ATTRIBUTE_OPTION)
        .map((option) => ({ value: option.value, label: option.textContent || option.value }));
      const filled = control.multiple ? control.selectedOptions.length > 0 : control.value !== "";
      if (filled || !options.length) continue;
      targets.push({ field_id: fieldId, label, required: spanText !== label, type: control.multiple ? "multi_select" : "single_select", options });
    } else if (control instanceof HTMLInputElement && control.value.trim() === "") {
      targets.push({ field_id: fieldId, label, required: spanText !== label, type: "text", options: [] });
    }
  }
  if (!targets.length) {
    showOperationFeedback("没有空着的属性需要推荐", "info");
    return;
  }
  button.disabled = true;
  if (status) status.textContent = "AI 正在推荐属性值…";
  try {
    const result = await api("/api/ai/attribute-recommendations", {
      method: "POST",
      body: JSON.stringify({
        fields: targets,
        context: {
          product_name: $("store-template-name")?.value || "",
          category_path: $("store-template-category-path")?.value || "",
          store_name: $("store-template-store")?.selectedOptions?.[0]?.textContent || "",
          description: $("store-template-description")?.value || "",
        },
      }),
    });
    const values = result.values || {};
    let applied = 0;
    for (const control of controls) {
      const fieldId = control.dataset.categoryFieldInput;
      if (!fieldId || !(fieldId in values)) continue;
      const value = values[fieldId];
      if (control instanceof HTMLSelectElement && control.multiple) {
        const list = Array.isArray(value) ? value.map(String) : [String(value)];
        [...control.options].forEach((option) => { option.selected = list.includes(option.value); });
        applied += 1;
      } else if (control instanceof HTMLSelectElement) {
        if ([...control.options].some((option) => option.value === String(value))) {
          control.value = String(value);
          applied += 1;
        }
      } else if (control instanceof HTMLInputElement) {
        control.value = String(value);
        applied += 1;
      }
    }
    const skipped = (result.skipped || []).length;
    if (status) status.textContent = `AI 已推荐填充 ${applied} 项属性${skipped ? `，${skipped} 项未匹配到选项` : ""}；保存模板后生效。`;
    showOperationFeedback(`AI 属性推荐完成：填充 ${applied} 项`, "success");
  } catch (error) {
    const message = error.message || String(error);
    if (status) status.textContent = `AI 推荐失败：${message}`;
    showOperationFeedback(`AI 推荐失败：${message}`, "error");
  } finally {
    button.disabled = false;
  }
}

async function createStoreTemplate() {
  const result = $("store-template-result");
  const validationError = validateStoreTemplateForm();
  if (validationError) {
    result.textContent = validationError;
    showOperationFeedback(validationError, "error");
    return false;
  }
  const selectedProfile = categoryProfile($("store-template-category-profile")?.value || "");
  const currentVersionId = selectedProfile?.active_version_id || selectedProfile?.published_version_id || "";
  if ($("store-template-category-id")?.value.trim() && !currentVersionId) {
    result.textContent = "当前平台类目尚未采集必填项规则。";
    return false;
  }
  result.textContent = "正在保存店铺模板...";
  const storeSelect = $("store-template-store");
  const selectedStore = storeSelect?.selectedOptions?.[0];
  const listingMode = $("store-template-listing-mode")?.value || "site";
  const targetSiteStores = selectedCnscTargetSiteStores();
  const priceTiers = listingMode === "cnsc_global" ? cnscPriceTiers() : [];
  const cnscStoreSelect = $("store-template-cnsc-store");
  const selectedCnscStore = cnscStoreSelect?.selectedOptions?.[0];
  const effectiveStoreId = listingMode === "cnsc_global" ? (cnscStoreSelect?.value || "") : (storeSelect?.value || "");
  const effectiveStoreName = listingMode === "cnsc_global" ? (selectedCnscStore?.dataset.storeName || "") : (selectedStore?.dataset.storeName || "");
  const sizeChartSource = $("store-template-size-chart-source")?.value === "template" ? "template" : "upload";
  const selectedSizeChartTemplate = sizeChartSource === "template" ? selectedStoreSizeChartTemplate() : null;
  const endpoint = state.editingStoreTemplateId
    ? `/api/store-templates/${encodeURIComponent(state.editingStoreTemplateId)}`
    : "/api/store-templates";
  const template = await api(endpoint, {
    method: state.editingStoreTemplateId ? "PUT" : "POST",
    body: JSON.stringify({
      store_name: effectiveStoreName,
      mabang_store_id: effectiveStoreId,
      // 模板按马帮账号隔离：保存时带上当前账号（店铺下拉已经只列这个账号的店铺）。
      account_id: currentStoreTemplateAccountId(),
      template_name: $("store-template-name").value.trim(),
      category_profile_id: $("store-template-category-profile")?.value || "",
      category_profile_version_id: currentVersionId,
      attribute_values: collectStoreTemplateCategoryValues(),
      marketplace_category_path: $("store-template-category-path").value.trim(),
      mabang_category_id: $("store-template-category-id")?.value.trim() || "",
      normal_price: $("store-template-normal-price").value.trim(),
      sale_price: $("store-template-sale-price").value.trim(),
      size_price_overrides: collectSizePriceOverrides(),
      promotion_activity_id: $("store-template-promotion")?.value || "",
      mabang_discount_id: $("store-template-promotion")?.value || "",
      // 不可用但需要保留的那条选项文案带后缀，label 用原始目录文案，别把后缀存进去。
      discount_label: (() => {
        const option = $("store-template-promotion")?.selectedOptions?.[0];
        return option?.dataset?.label || option?.textContent || "";
      })(),
      sizes: selectedTemplateSizes(),
      stock: $("store-template-stock")?.value || "",
      weight_kg: $("store-template-weight-kg")?.value || "",
      package_length_cm: $("store-template-package-length-cm")?.value || "",
      package_width_cm: $("store-template-package-width-cm")?.value || "",
      package_height_cm: $("store-template-package-height-cm")?.value || "",
      description_profile_version_id: $("store-template-description-profile")?.value || "",
      description: $("store-template-description")?.value.trim() || "",
      variation_rule: $("store-template-variation-rule")?.value || "color_size",
      sku_template: $("store-template-sku-template")?.value.trim() || "{main_sku}_{color}||{size}",
      sku_strip_parentheses: listingMode === "cnsc_global" ? true : ($("store-template-strip-parentheses")?.checked ?? true),
      // 默认 SKU(白T恤) 是本土专属配置：跨境保存时保持空配置。
      default_sku: listingMode === "cnsc_global" ? { enabled: false } : collectStoreTemplateDefaultSku(),
      // 默认视频 ID（本土专属）：跨境保存时强制为空，执行器照旧每次上传。
      default_video_id: listingMode === "cnsc_global" ? "" : ($("store-template-default-video-id")?.value.trim() || ""),
      // 选择模板时页面上没有可引用的尺码图地址，自动化要拿这里上传的那张图插到
      // 描述末尾；没上传才回落到模板自带的预览图（采集到的模板才有）。
      size_chart_asset_id: state.sizeChartAsset?.id || selectedSizeChartTemplate?.asset_id || "",
      size_chart_preview_url: state.sizeChartAsset?.preview_url || selectedSizeChartTemplate?.preview_url || "",
      size_chart_source: sizeChartSource,
      default_size_chart_path: state.sizeChartAsset?.file_path || selectedSizeChartTemplate?.file_path || "",
      mabang_size_chart_template_id: selectedSizeChartTemplate?.template_id || "",
      mabang_size_chart_template_name: selectedSizeChartTemplate?.name || "",
      // 产品图最后一张放尺码表（本土专属，默认开）：跨境保存时强制关闭。
      size_chart_as_last_product_image: listingMode === "cnsc_global"
        ? false
        : ($("store-template-size-chart-last-image")?.checked ?? true),
      main_image_watermark: selectedWatermark("store-template-main-watermark", "main"),
      product_image_watermark: selectedWatermark("store-template-product-watermark", "product"),
      listing_mode: listingMode,
      cnsc_store: listingMode === "cnsc_global" ? { id: cnscStoreSelect?.value || storeSelect?.value || "", name: selectedCnscStore?.dataset.storeName || selectedStore?.dataset.storeName || "" } : {},
      target_site_stores: listingMode === "cnsc_global" ? targetSiteStores : [],
      site_price_order: listingMode === "cnsc_global" ? cnscSiteOrder() : [],
      default_site_discount_percent: listingMode === "cnsc_global" ? ($("store-template-default-site-discount-percent")?.value || 40) : null,
      price_tiers: priceTiers,
      warehouse_stock_defaults: listingMode === "cnsc_global" ? {
        TH: $("store-template-warehouse-th")?.value || 0,
        CN_GZ: $("store-template-warehouse-cn-gz")?.value || 0,
        PH: $("store-template-warehouse-ph")?.value || 0,
      } : {},
    }),
  });
  state.selectedStoreTemplates.add(template.id);
  state.editingStoreTemplateId = "";
  if ($("save-as-store-template-btn")) $("save-as-store-template-btn").disabled = true;
  $("create-store-template-btn").textContent = "保存模板";
  result.textContent = `已保存模板 ${template.store_name || ""} / ${template.template_name || ""}`;
  await loadStoreTemplates();
  return true;
}

// 跨境模式（CNSC）界面开关：关闭后只显示本土版本，跨境相关界面隐藏。
// 纯前端显示过滤：后端数据、自动化任务与 Worker 行为完全不受影响，重新开启即恢复。
function crossBorderModeEnabled() {
  return state.runtimeConfiguration?.display?.cross_border_enabled !== false;
}

// 把跨境模式显示状态应用到所有相关界面：设置加载/保存后调用一次；各渲染函数
// 内部也有同样的判断（多标签页/局域网多端 3 秒轮询重渲染时保持一致）。
function applyCrossBorderUi() {
  const enabled = crossBorderModeEnabled();
  // 复制到店铺模板：跨境卡片只在开启时显示，关闭时强制回到本土模式。
  $("copy-template-mode-cnsc")?.toggleAttribute("hidden", !enabled);
  if (!enabled && copyTemplatePickerMode() === "cnsc") setCopyTemplatePickerMode("site");
  // 模板编辑器：刊登模式下拉的 CNSC 选项随开关显示/隐藏。
  updateCnscTemplateVisibility();
  // 基础商品维护：「站点价格」区（产品类目 / 尺码表 / 站点价格表）是跨境专属，
  // 关闭时整节隐藏。本土走店铺模板侧兜底（类目、尺码、价格都在模板维护）。
  // 纯显示过滤：数据与保存路径不受影响，重新开启即恢复。
  $("maintenance-section-prices")?.toggleAttribute("hidden", !enabled);
  document.querySelector(".maintenance-note--cnsc")?.toggleAttribute("hidden", !enabled);
  $("base-price-cnsc-site-order")?.toggleAttribute("hidden", !enabled);
  $("base-price-cnsc-site-order-display")?.toggleAttribute("hidden", !enabled);
  // 模板/副本列表与复制选择器按模式过滤后重渲染。
  renderStoreTemplates();
  renderStoreListingCopies();
  renderCopyTemplatePicker();
}

function updateCnscTemplateVisibility() {
  const global = $("store-template-listing-mode")?.value === "cnsc_global";
  // 跨境模式关闭：刊登模式下拉隐藏 CNSC 选项。用 hidden 而不是移除，已打开的
  // 跨境模板仍能程序化选中并正常保存，不会损坏数据。
  $("store-template-listing-mode")?.querySelector('option[value="cnsc_global"]')?.toggleAttribute("hidden", !crossBorderModeEnabled());
  $("cnsc-global-section")?.toggleAttribute("hidden", !global);
  // 默认 SKU(白T恤)/SKU 去括号是本土专属：跨境店铺不发生成，直接隐藏。
  document.querySelector(".default-sku-head")?.toggleAttribute("hidden", global);
  $("store-template-default-sku-block")?.toggleAttribute("hidden", global);
  $("store-template-strip-parentheses-line")?.toggleAttribute("hidden", global);
  $("store-template-promotion")?.closest("label")?.toggleAttribute("hidden", global);
  // 「重新采集活动」只对本土单店有效：跨境的活动目录走目标站点店铺，整块一起隐藏。
  $("store-promotion-field")?.toggleAttribute("hidden", global);
  $("store-template-default-video-id")?.closest("[data-template-default-video]")?.toggleAttribute("hidden", global);
  // 「产品图最后一张放尺码表」是本土专属：跨境模板没有这条规则，直接隐藏。
  $("store-template-size-chart-last-image-line")?.toggleAttribute("hidden", global);
  $("store-template-normal-price")?.closest("label")?.toggleAttribute("hidden", global);
  $("store-template-size-price-overrides")?.toggleAttribute("hidden", global);
  $("store-template-sale-price")?.closest("label")?.toggleAttribute("hidden", global);
  $("store-template-stock")?.closest("label")?.toggleAttribute("hidden", global);
}

const CNSC_KNOWN_SITES = ["TW", "PH", "SG", "TH", "MY", "MX", "VN", "BR"];
// CNSC 页面站点显示顺序（台湾、新加坡、泰国、马来、墨西哥、越南、菲律宾、巴西），
// 作为无表头粘贴的默认识别顺序；用户可在“识别顺序”对话框中修改。
const CNSC_DEFAULT_SITE_ORDER = ["TW", "SG", "TH", "MY", "MX", "VN", "PH", "BR"];
const CNSC_SITE_ORDER_STORAGE_KEY = "ai_listing.cnsc_site_order";
const CNSC_SITE_NAMES = {
  TW: "台湾", SG: "新加坡", TH: "泰国", MY: "马来西亚",
  MX: "墨西哥", VN: "越南", PH: "菲律宾", BR: "巴西",
};

function cnscSiteOrder() {
  // Server-side order (runtime_configuration.cnsc.price_site_order) is the source
  // of truth so the order survives project restarts and browser cache clears;
  // localStorage is only a same-browser cache kept until settings load.
  const serverOrder = Array.isArray(state.runtimeConfiguration?.cnsc?.price_site_order)
    ? state.runtimeConfiguration.cnsc.price_site_order
    : null;
  if (serverOrder) {
    const cleaned = [...new Set(serverOrder
      .map((code) => String(code || "").trim().toUpperCase())
      .filter((code) => CNSC_KNOWN_SITES.includes(code)))];
    return [...cleaned, ...CNSC_DEFAULT_SITE_ORDER.filter((code) => !cleaned.includes(code))];
  }
  let stored = [];
  try {
    stored = JSON.parse(window.localStorage.getItem(CNSC_SITE_ORDER_STORAGE_KEY) || "[]");
  } catch (_) {
    stored = [];
  }
  const cleaned = [...new Set((Array.isArray(stored) ? stored : [])
    .map((code) => String(code || "").trim().toUpperCase())
    .filter((code) => CNSC_KNOWN_SITES.includes(code)))];
  return [...cleaned, ...CNSC_DEFAULT_SITE_ORDER.filter((code) => !cleaned.includes(code))];
}

function orderedCnscSites(targetSites) {
  const order = cnscSiteOrder();
  const targets = [...new Set((targetSites || []).map((code) => String(code || "").trim().toUpperCase()).filter(Boolean))];
  const ordered = order.filter((code) => targets.includes(code));
  return [...ordered, ...targets.filter((code) => !ordered.includes(code))];
}

function renderCnscSiteOrderDisplays() {
  // 跨境模式关闭：基础商品维护的「站点价格」区整节隐藏（产品类目/尺码表/价格表
  // 都是跨境专属，本土在店铺模板维护）；维护弹窗每次打开都会走这里，另一标签页
  // 改了开关也能在下一次打开时生效。
  const cbEnabled = crossBorderModeEnabled();
  $("maintenance-section-prices")?.toggleAttribute("hidden", !cbEnabled);
  document.querySelector(".maintenance-note--cnsc")?.toggleAttribute("hidden", !cbEnabled);
  $("base-price-cnsc-site-order")?.toggleAttribute("hidden", !cbEnabled);
  $("base-price-cnsc-site-order-display")?.toggleAttribute("hidden", !cbEnabled);
  const text = `识别顺序：${cnscSiteOrder().join(" → ")}`;
  for (const nodeId of ["listing-copy-cnsc-site-order-display", "store-template-cnsc-site-order-display", "base-price-cnsc-site-order-display"]) {
    const node = $(nodeId);
    if (node) node.textContent = text;
  }
}

function renderCnscSiteOrderChips(order = cnscSiteOrder()) {
  const root = $("cnsc-site-order-chips");
  if (!root) return;
  root.innerHTML = order.map((code) => `
    <span class="size-chip size-chip--selected" draggable="true" data-cnsc-site="${escapeHtml(code)}">
      <span class="size-chip-handle" aria-hidden="true">⋮⋮</span>
      <span class="size-chip-label">${escapeHtml(code)} ${escapeHtml(CNSC_SITE_NAMES[code] || "")}</span>
    </span>`).join("");
  attachCnscSiteChipDragHandlers(root);
}

function cnscSiteOrderChipsOrder() {
  const root = $("cnsc-site-order-chips");
  if (!root) return [];
  return [...root.querySelectorAll("[data-cnsc-site]")]
    .map((chip) => String(chip.dataset.cnscSite || "").trim().toUpperCase())
    .filter(Boolean);
}

// 站点块拖动排序：dragover 时按指针左右半区实时插入，drop/dragend 只负责收尾。
function attachCnscSiteChipDragHandlers(root) {
  if (!root || root.dataset.siteOrderDragBound === "true") return;
  root.dataset.siteOrderDragBound = "true";
  let draggingCode = "";
  const chipFrom = (target) => (target instanceof HTMLElement ? target.closest("[data-cnsc-site]") : null);
  root.addEventListener("dragstart", (event) => {
    const chip = chipFrom(event.target);
    if (!chip) { event.preventDefault(); return; }
    draggingCode = chip.dataset.cnscSite || "";
    chip.classList.add("size-chip--dragging");
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", draggingCode);
    }
  });
  root.addEventListener("dragend", (event) => {
    const chip = chipFrom(event.target);
    if (chip) chip.classList.remove("size-chip--dragging");
    draggingCode = "";
  });
  root.addEventListener("dragover", (event) => {
    if (!draggingCode) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    const target = chipFrom(event.target);
    const dragged = root.querySelector(`[data-cnsc-site="${draggingCode}"]`);
    if (!target || !dragged || target === dragged) return;
    const rect = target.getBoundingClientRect();
    if (event.clientX > rect.left + rect.width / 2) target.after(dragged);
    else target.before(dragged);
  });
  root.addEventListener("drop", (event) => {
    if (draggingCode) event.preventDefault();
  });
}

function openCnscSiteOrderDialog() {
  renderCnscSiteOrderChips();
  const error = $("cnsc-site-order-error");
  if (error) error.textContent = "";
  const dialog = $("cnsc-site-order-dialog");
  if (dialog && typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
}

function resetCnscSiteOrderInput() {
  renderCnscSiteOrderChips([...CNSC_DEFAULT_SITE_ORDER]);
  const error = $("cnsc-site-order-error");
  if (error) error.textContent = "";
}

async function saveCnscSiteOrder() {
  const codes = [...new Set(cnscSiteOrderChipsOrder())];
  const invalid = codes.filter((code) => !CNSC_KNOWN_SITES.includes(code));
  const missing = CNSC_KNOWN_SITES.filter((code) => !codes.includes(code));
  const error = $("cnsc-site-order-error");
  if (invalid.length || missing.length) {
    if (error) error.textContent = invalid.length ? `无法识别的站点代码：${invalid.join("、")}` : `缺少站点：${missing.join("、")}`;
    return;
  }
  try {
    // Persist to the server so the order survives restarts and cache clears.
    const config = await api("/api/settings", { method: "PUT", body: JSON.stringify({ cnsc: { price_site_order: codes } }) });
    state.runtimeConfiguration = config;
  } catch (err) {
    if (error) error.textContent = `保存到服务端失败：${(err && err.message) || String(err)}`;
    return;
  }
  try {
    window.localStorage.setItem(CNSC_SITE_ORDER_STORAGE_KEY, JSON.stringify(codes));
  } catch (_) {
    // Storage unavailable (private mode etc.): server copy still persists.
  }
  // 模板编辑上下文：同时把顺序持久化到当前编辑的模板，复制时按模板裁剪站点价格。
  if (state.editingStoreTemplateId) {
    try {
      await api(`/api/store-templates/${encodeURIComponent(state.editingStoreTemplateId)}`, {
        method: "PUT",
        body: JSON.stringify({ site_price_order: codes }),
      });
      await loadStoreTemplates();
    } catch (err) {
      if (error) error.textContent = `保存到模板失败：${(err && err.message) || String(err)}`;
      return;
    }
  }
  renderCnscSiteOrderDisplays();
  $("cnsc-site-order-dialog")?.close();
  showOperationFeedback("价格表识别顺序已保存", "success");
}

async function previewCnscPriceMatrix() {
  const text = $("store-template-price-matrix")?.value || "";
  const tiers = cnscPriceTiers().map(({ id, sizes }) => ({ id, sizes }));
  const targetSites = orderedCnscSites(selectedCnscTargetSiteStores().map((item) => item.site_code));
  const result = await api("/api/cnsc-price-matrix/preview", { method: "POST", body: JSON.stringify({ text, tiers, target_sites: targetSites }) });
  const preview = $("store-template-price-preview");
  if (preview) preview.textContent = cnscPriceParseSummary(result);
  if (result.errors?.length) throw new Error(result.errors.join("；"));
  state.cnscPricePreview = result;
  for (const tier of result.tiers || []) {
    const input = $(`store-template-cnsc-global-${tier.id}`);
    if (input && !String(input.value || "").trim()) input.value = tier.global_price_cny || "";
  }
  renderCnscTargetStoreConfigs();
  return result;
}

function openSaveAsStoreTemplateDialog() {
  if (!state.editingStoreTemplateId) return;
  const source = storeTemplateById(state.editingStoreTemplateId);
  if (!source) return;
  renderMabangStoreOptions();
  $("save-as-store-template-store").value = "";
  $("save-as-store-template-name").value = `${source.template_name || "店铺模板"} - 副本`;
  $("save-as-store-template-summary").textContent = "切换店铺后将清空促销活动和水印；跨站点还会清空类目、价格和必填属性。";
  $("save-as-store-template-result").textContent = "";
  $("save-as-store-template-dialog")?.showModal();
}

async function saveStoreTemplateAsCopy() {
  const storeId = $("save-as-store-template-store")?.value || "";
  const templateName = $("save-as-store-template-name")?.value.trim() || "";
  const result = $("save-as-store-template-result");
  if (!storeId || !templateName) {
    if (result) result.textContent = "请选择目标店铺并填写新模板名";
    return false;
  }
  if (result) result.textContent = "正在另存模板...";
  const copied = await api(`/api/store-templates/${encodeURIComponent(state.editingStoreTemplateId)}/copy`, {
    method: "POST",
    body: JSON.stringify({ mabang_store_id: storeId, template_name: templateName }),
  });
  await loadStoreTemplates();
  $("save-as-store-template-dialog")?.close();
  await openStoreTemplateEditor(copied.id);
  showOperationFeedback(`已另存模板 ${copied.store_name || ""} / ${copied.template_name || ""}`, "success");
  return true;
}

async function openStoreTemplateEditor(templateId) {
  let template = storeTemplateById(templateId);
  if (!template) {
    return openNewStoreTemplateEditor();
  }
  // 账号隔离：模板属于别的账号时先把页面切到那个账号，否则店铺下拉里没有它的店铺。
  const templateAccount = String(template.account_id || "");
  if (templateAccount && templateAccount !== currentStoreTemplateAccountId()) {
    state.storeTemplateAccountId = templateAccount;
    await loadStoreTemplates();
    renderMabangStoreOptions();
    template = storeTemplateById(templateId) || template;
  }
  state.editingStoreTemplateId = template.id;
  const editorTitle = $("store-template-editor-title");
  if (editorTitle) editorTitle.textContent = `${template.store_name || ""} · ${template.template_name || "编辑店铺模板"}`.trim();
  if ($("save-as-store-template-btn")) $("save-as-store-template-btn").disabled = false;
  $("store-template-store").value = template.mabang_store_id || "";
  $("store-template-listing-mode").value = template.listing_mode || "site";
  await Promise.all([
    loadStorePromotions(template.promotion_activity_id || template.mabang_discount_id || ""),
    loadStoreWatermarks(),
    loadStoreSizeChartTemplates(),
  ]);
  renderStoreTemplateDescriptionProfileOptions();
  $("store-template-name").value = template.template_name || "";
  $("store-template-category-profile").value = template.category_profile_id || "";
  $("store-template-category-path").value = template.marketplace_category_path || "";
  $("store-template-category-id").value = template.mabang_category_id || "";
  $("store-template-normal-price").value = template.normal_price ?? "";
  const templateSale = template.sale_price ?? "";
  const derivedSale = calculateFortyPercentDiscount(template.normal_price ?? "");
  // 存量模板存的还是自动六折价时按"自动"处理，改原价继续跟着变；存的是自定义价
  // 就别被六折预填顶掉。
  const saleIsAutomatic = templateSale === "" || (derivedSale !== "" && Number(templateSale) === Number(derivedSale));
  $("store-template-sale-price").value = saleIsAutomatic ? (derivedSale || templateSale) : String(templateSale);
  $("store-template-sale-price").dataset.manual = saleIsAutomatic ? "" : "1";
  $("store-template-promotion").value = template.promotion_activity_id || template.mabang_discount_id || "";
  if ($("store-template-cnsc-store")) $("store-template-cnsc-store").value = template.cnsc_store?.id || template.mabang_store_id || "";
  if ($("store-template-default-site-discount-percent")) $("store-template-default-site-discount-percent").value = template.default_site_discount_percent ?? 40;
  state.cnscTargetStoreConfigs = new Map((template.target_site_stores || []).map((item) => [item.store_id || item.store_name || item.site_code, { ...item }]));
  const targetIds = new Set((template.target_site_stores || []).map((item) => item.store_id).filter(Boolean));
  const targetSelect = $("store-template-target-sites");
  for (const target of template.target_site_stores || []) {
    if (!target.store_id || [...(targetSelect?.options || [])].some((option) => option.value === target.store_id)) continue;
    const option = document.createElement("option");
    option.value = target.store_id;
    option.dataset.siteCode = target.site_code || "";
    option.dataset.storeName = target.store_name || "";
    option.textContent = `${target.site_code || ""} · ${target.store_name || target.store_id}`;
    targetSelect?.appendChild(option);
  }
  for (const option of $("store-template-target-sites")?.options || []) option.selected = targetIds.has(option.value);
  if ($("store-template-cnsc-global-standard")) $("store-template-cnsc-global-standard").value = template.price_tiers?.find((tier) => tier.id === "standard")?.global_price_cny ?? "";
  if ($("store-template-cnsc-global-large")) $("store-template-cnsc-global-large").value = template.price_tiers?.find((tier) => tier.id === "large")?.global_price_cny ?? "";
  if ($("store-template-cnsc-standard-sizes")) $("store-template-cnsc-standard-sizes").value = (template.price_tiers?.find((tier) => tier.id === "standard")?.sizes || ["XS", "S", "M", "L"]).join(",");
  if ($("store-template-cnsc-large-sizes")) $("store-template-cnsc-large-sizes").value = (template.price_tiers?.find((tier) => tier.id === "large")?.sizes || ["XL", "XXL", "XXXL"]).join(",");
  state.cnscPricePreview = template.price_tiers?.length ? { tiers: template.price_tiers } : null;
  $("store-template-warehouse-th").value = template.warehouse_stock_defaults?.TH ?? "";
  $("store-template-warehouse-cn-gz").value = template.warehouse_stock_defaults?.CN_GZ ?? "";
  $("store-template-warehouse-ph").value = template.warehouse_stock_defaults?.PH ?? "";
  renderCnscTargetStoreConfigs();
  updateCnscTemplateVisibility();
  $("store-template-stock").value = template.stock ?? "";
  $("store-template-weight-kg").value = template.weight_kg ?? "";
  $("store-template-package-length-cm").value = template.package_length_cm ?? "";
  $("store-template-package-width-cm").value = template.package_width_cm ?? "";
  $("store-template-package-height-cm").value = template.package_height_cm ?? "";
  $("store-template-description-profile").value = template.description_profile_version_id || "";
  const descriptionInput = $("store-template-description");
  if (descriptionInput) descriptionInput.value = template.description || "";
  $("store-template-sku-template").value = template.sku_template || "{main_sku}_{color}||{size}";
  if ($("store-template-default-video-id")) $("store-template-default-video-id").value = template.default_video_id || "";
  if ($("store-template-strip-parentheses")) $("store-template-strip-parentheses").checked = template.sku_strip_parentheses !== false;
  // 缺字段的存量模板按「开」显示（与后端 _size_chart_last_product_image_enabled 一致）。
  if ($("store-template-size-chart-last-image")) {
    $("store-template-size-chart-last-image").checked = template.size_chart_as_last_product_image !== false;
  }
  fillStoreTemplateDefaultSku(template.default_sku);
  state.sizeChartAsset = template.size_chart_asset_id ? {
    id: template.size_chart_asset_id,
    file_path: template.default_size_chart_path || "",
    preview_url: template.size_chart_preview_url || `/api/uploads/size-chart/${encodeURIComponent(template.size_chart_asset_id)}`,
  } : null;
  renderSizeChartSource(template.size_chart_source || "upload");
  renderSizeChartTemplateOptions();
  $("store-template-size-chart-template").value = template.mabang_size_chart_template_id || "";
  renderStoreTemplateSizeChartPreview();
  // 已保存的水印选择：ID 和名称一起回显（目录没加载时也能照原样保存回去）。
  setWatermarkChoiceValue("main", template.main_image_watermark?.template_id || "", template.main_image_watermark?.name || "");
  setWatermarkChoiceValue("product", template.product_image_watermark?.template_id || "", template.product_image_watermark?.name || "");
  renderWatermarkPicker("main");
  renderWatermarkPicker("product");
  renderWatermarkCatalogStatus();
  renderStoreWatermarkPreview("main");
  renderStoreWatermarkPreview("product");
  const mainRef = template.main_image_watermark || {};
  const productRef = template.product_image_watermark || {};
  renderWatermarkPositionChecks("main", mainRef.positions || []);
  renderWatermarkPositionChecks("product", productRef.positions || []);
  applyWatermarkModeUi("main", mainRef.mode === "custom" ? "custom" : "auto");
  applyWatermarkModeUi("product", productRef.mode === "custom" ? "custom" : "auto");
  const sizeChartPreview = $("store-template-size-chart-preview");
  if (sizeChartPreview) {
    sizeChartPreview.src = sizeChartPreviewUrl(template.size_chart_preview_url, 720);
    sizeChartPreview.hidden = !template.size_chart_preview_url;
  }
  renderSizeOptions(template.sizes || []);
  renderSizePriceOverrides(template.size_price_overrides || {});
  // 默认 SKU 分尺码价格表与主表格同拍渲染（fillStoreTemplateDefaultSku 跑在
  // 尺码 chip 渲染之前，那里画不出行）。
  renderDefaultSkuSizePriceOverrides(template.default_sku?.size_price_overrides || {});
  const profile = categoryProfile(template.category_profile_id || "");
  const profileFields = profile?.published_version?.fields || profile?.active_version?.fields || [];
  renderStoreTemplateCategoryFields(profileFields, template.attribute_values || template.category_attributes || {});
  $("create-store-template-btn").textContent = "更新模板";
  $("store-template-result").textContent = `正在编辑 ${template.store_name || ""} / ${template.template_name || ""}`;
  const dialog = $("store-template-editor-dialog");
  if (dialog && typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
}

async function openNewStoreTemplateEditor() {
  state.editingStoreTemplateId = "";
  const editorTitle = $("store-template-editor-title");
  if (editorTitle) editorTitle.textContent = "新建店铺模板";
  const saveAs = $("save-as-store-template-btn");
  if (saveAs) saveAs.disabled = true;
  const createBtn = $("create-store-template-btn");
  if (createBtn) createBtn.textContent = "保存模板";
  const result = $("store-template-result");
  if (result) result.textContent = "";
  // Reset form fields to sane defaults so the editor doesn't carry the last template's values.
  const resetInputs = [
    "store-template-name", "store-template-category-path", "store-template-category-id",
    "store-template-normal-price", "store-template-sale-price", "store-template-stock",
    "store-template-weight-kg", "store-template-package-length-cm", "store-template-package-width-cm",
    "store-template-package-height-cm", "store-template-description", "store-template-default-video-id",
  ];
  for (const id of resetInputs) { const el = $(id); if (el) el.value = ""; }
  const name = $("store-template-name"); if (name) name.value = "Mens Tee";
  $("store-template-listing-mode").value = "site";
  if ($("store-template-cnsc-store")) $("store-template-cnsc-store").value = "";
  if ($("store-template-default-site-discount-percent")) $("store-template-default-site-discount-percent").value = "40";
  state.cnscTargetStoreConfigs = new Map();
  for (const option of $("store-template-target-sites")?.options || []) option.selected = false;
  $("store-template-warehouse-th").value = "";
  $("store-template-warehouse-cn-gz").value = "";
  $("store-template-warehouse-ph").value = "";
  $("store-template-price-matrix").value = "";
  if ($("store-template-cnsc-global-standard")) $("store-template-cnsc-global-standard").value = "";
  if ($("store-template-cnsc-global-large")) $("store-template-cnsc-global-large").value = "";
  if ($("store-template-cnsc-standard-sizes")) $("store-template-cnsc-standard-sizes").value = "XS,S,M,L";
  if ($("store-template-cnsc-large-sizes")) $("store-template-cnsc-large-sizes").value = "XL,XXL,XXXL";
  state.cnscPricePreview = null;
  updateCnscTemplateVisibility();
  const sku = $("store-template-sku-template"); if (sku) sku.value = "{main_sku}_{color}||{size}";
  if ($("store-template-strip-parentheses")) $("store-template-strip-parentheses").checked = true;
  fillStoreTemplateDefaultSku(null);
  await Promise.all([loadStorePromotions(), loadStoreWatermarks(), loadStoreSizeChartTemplates()]).catch(() => {});
  renderStoreTemplateDescriptionProfileOptions();
  resetStoreWatermarkChoices();
  renderWatermarkPicker("main");
  renderWatermarkPicker("product");
  renderWatermarkCatalogStatus();
  renderWatermarkPositionChecks("main", []);
  renderWatermarkPositionChecks("product", []);
  applyWatermarkModeUi("main", "auto");
  applyWatermarkModeUi("product", "auto");
  const dialog = $("store-template-editor-dialog");
  if (dialog && typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
}

async function copyStoreTemplate(templateId) {
  const source = storeTemplateById(templateId);
  if (!source) return false;
  // 同店复制：保留促销活动/水印/类目与价格，生成「- 副本」后直接进编辑器改差异项。
  const copied = await api(`/api/store-templates/${encodeURIComponent(templateId)}/duplicate`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  await loadStoreTemplates();
  await openStoreTemplateEditor(copied.id);
  showOperationFeedback(`已复制模板 ${copied.store_name || ""} / ${copied.template_name || ""}，可继续修改后保存`, "success");
  return true;
}

async function deleteStoreTemplate(templateId) {
  const template = storeTemplateById(templateId);
  if (!template) return;
  const confirmed = window.confirm(`删除模板“${template.template_name || "未命名模板"}”？已有刊登副本的模板会归档，历史副本不会受影响。`);
  if (!confirmed) return;
  const result = await api(`/api/store-templates/${encodeURIComponent(templateId)}`, { method: "DELETE" });
  state.selectedStoreTemplates.delete(templateId);
  if (state.editingStoreTemplateId === templateId) {
    state.editingStoreTemplateId = "";
    if ($("save-as-store-template-btn")) $("save-as-store-template-btn").disabled = true;
    $("create-store-template-btn").textContent = "保存模板";
  }
  await loadStoreTemplates();
  showOperationFeedback(result.archived ? "模板已归档，已有刊登副本已保留" : "模板已删除", "success");
}

let _mabangImportExpectedStoreId = "";

// 采集必须落到正确的马帮账号：账号决定用哪个 CDP 端口与 Chrome Profile（登录态）。
function syncMabangPageImportAccountOptions() {
  const select = $("mabang-page-import-account");
  if (!select) return;
  const accounts = state.mabangAccounts || [];
  const previous = select.value;
  select.innerHTML = accounts.length
    ? accounts.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name || item.login_account || "未命名账号")}${item.enabled === false ? "（已停用）" : ""}</option>`).join("")
    : '<option value="">默认账号（未配置多账号）</option>';
  const preferred = previous || state.mabangAccountsDefaultId;
  if (preferred && accounts.some((item) => item.id === preferred)) select.value = preferred;
}

function openMabangPageImportDialog(expectedStoreId = "") {
  _mabangImportExpectedStoreId = expectedStoreId || "";
  syncMabangPageImportAccountOptions();
  const accountSelect = $("mabang-page-import-account");
  if (accountSelect && !accountSelect.dataset.bound) {
    accountSelect.dataset.bound = "1";
    // 换账号 = 换一套登录态，导入任务要按新账号重建。
    accountSelect.addEventListener("change", () => { startMabangPageImportRun(); });
  }
  const link = $("open-mabang-new-product-link");
  if (link) link.href = MABANG_NEW_PRODUCT_URL;
  $("mabang-page-import-dialog")?.showModal();
  startMabangPageImportRun();
}

function startMabangPageImportRun() {
  const result = $("mabang-page-import-result");
  const collectButton = $("collect-mabang-page-import-btn");
  const confirmButton = $("confirm-mabang-page-import-btn");
  const runIdBox = $("mabang-page-import-run-id-box");
  const runIdValue = $("mabang-page-import-run-id-value");
  state.mabangPageImportRunId = "";
  state.mabangPageImportRun = null;
  stopMabangPageImportPolling();
  if (result) result.textContent = "正在创建导入任务...";
  if (collectButton) collectButton.disabled = true;
  if (confirmButton) confirmButton.disabled = true;
  if (runIdBox) runIdBox.hidden = true;
  if (runIdValue) runIdValue.textContent = "—";
  $("mabang-import-preview")?.setAttribute("hidden", "");
  if ($("mabang-import-warnings")) $("mabang-import-warnings").textContent = "";
  const store = marketplaceCategoryScope();
  return api("/api/mabang-page-imports", {
    method: "POST",
    body: JSON.stringify({
      platform: store.platform || "shopee",
      country: store.country || "PH",
      conflict_action: "update",
      expected_store_id: _mabangImportExpectedStoreId,
      account_id: $("mabang-page-import-account")?.value || "",
    }),
  }).then((run) => {
    state.mabangPageImportRunId = run.id;
    state.mabangPageImportRun = run;
    if (runIdBox) runIdBox.hidden = false;
    if (runIdValue) runIdValue.textContent = run.id;
    if (result) result.textContent = "已创建导入任务。点\"服务器 Chrome 打开\"进入马帮商品页，展开所选店铺 picker 后回到此弹窗点\"服务器采集\"。";
    if (collectButton) collectButton.disabled = false;
  }).catch((error) => {
    if (result) result.textContent = `导入任务创建失败：${error.message || "服务不可用"}`;
  });
}

// Short-poll the run until the server-side collector reports
// a snapshot. Stops on any non-pending status.
function startMabangPageImportPolling() {
  stopMabangPageImportPolling();
  if (!state.mabangPageImportRunId) return;
  state.mabangPageImportPollTimer = window.setInterval(async () => {
    if (!state.mabangPageImportRunId) { stopMabangPageImportPolling(); return; }
    try {
      const run = await api(`/api/mabang-page-imports/${encodeURIComponent(state.mabangPageImportRunId)}`);
      if (!run || !run.status) return;
      if (["collected", "collected_with_warnings"].includes(run.status)) {
        state.mabangPageImportRun = run;
        renderMabangImportPreview(run);
        const confirmButton = $("confirm-mabang-page-import-btn");
        if (confirmButton) confirmButton.disabled = !canConfirmMabangPageImport(run);
        const result = $("mabang-page-import-result");
        if (result) result.textContent = "已收到插件推送的数据。检查预览后点“确认导入”。";
        stopMabangPageImportPolling();
      } else if (run.status === "failed" || run.status === "cancelled") {
        stopMabangPageImportPolling();
      }
    } catch (err) {
      // Silent — polling will retry on the next tick.
    }
  }, 2000);
}

function stopMabangPageImportPolling() {
  if (state.mabangPageImportPollTimer) {
    window.clearInterval(state.mabangPageImportPollTimer);
    state.mabangPageImportPollTimer = 0;
  }
}

async function copyMabangPageImportRunId() {
  if (!state.mabangPageImportRunId) return;
  try {
    await navigator.clipboard.writeText(state.mabangPageImportRunId);
    const result = $("mabang-page-import-result");
    if (result) result.textContent = `Run ID 已复制到剪贴板：${state.mabangPageImportRunId}`;
  } catch {
    // Fallback for older browsers or restricted contexts.
    const dummy = document.createElement("textarea");
    dummy.value = state.mabangPageImportRunId;
    document.body.appendChild(dummy);
    dummy.select();
    try { document.execCommand("copy"); } catch {}
    dummy.remove();
  }
}

async function openMabangNewProductPage() {
  const result = $("mabang-page-import-result");
  const link = $("open-mabang-new-product-link");
  if (link) link.setAttribute("aria-busy", "true");
  if (result) result.textContent = "正在启动采集浏览器并打开马帮新建商品页（本次会显示窗口）...";
  const accountId = $("mabang-page-import-account")?.value || "";
  try {
    // 页面导入要操作员在窗口里手动进商品编辑页，所以这一步强制显示窗口；
    // 采集完成后服务端会自动切回设置里的模式（默认无头）。
    const launch = await api("/api/mabang-browser/start", {
      method: "POST",
      body: JSON.stringify({ account_id: accountId, headless: false }),
    });
    const accountLabel = $("mabang-page-import-account")?.selectedOptions?.[0]?.textContent || "默认账号";
    if (result) result.textContent = launch.login_required
      ? `已用「${accountLabel}」打开采集浏览器（窗口已显示）。若停在登录页请手动登录，然后进入商品编辑页，回到本弹窗点“采集当前页”。`
      : `已用「${accountLabel}」打开马帮新建商品页（窗口已显示）。进入商品编辑页后回到本弹窗点“采集当前页”；采集完成后窗口自动恢复无头。`;
  } catch (error) {
    if (result) result.textContent = `无法启动采集浏览器：${error.message || "请检查 Chrome 与账号端口设置"}`;
  } finally {
    link?.removeAttribute("aria-busy");
  }
}

async function collectMabangPageImport() {
  const result = $("mabang-page-import-result");
  const collectButton = $("collect-mabang-page-import-btn");
  const confirmButton = $("confirm-mabang-page-import-btn");
  if (!state.mabangPageImportRunId || !collectButton) return;
  collectButton.disabled = true;
  if (confirmButton) confirmButton.disabled = true;
  if (result) result.textContent = "正在只读采集马帮当前页...";
  try {
    const run = await api(`/api/mabang-page-imports/${encodeURIComponent(state.mabangPageImportRunId)}/collect`, {
      method: "POST", body: JSON.stringify({}),
    });
    state.mabangPageImportRun = run;
    renderMabangImportPreview(run);
    if (result) result.textContent = "已完成只读采集，采集窗口已恢复无头。请检查预览后确认导入。";
    if (confirmButton) confirmButton.disabled = !canConfirmMabangPageImport(run);
    collectButton.disabled = false;
  } catch (error) {
    if (result) result.textContent = `采集失败：${error.message || "请确认马帮新建商品页已打开并完成填写"}`;
    collectButton.disabled = false;
  }
}

function canConfirmMabangPageImport(run) {
  const snapshot = run?.snapshot || {};
  const store = snapshot.store || {};
  const category = snapshot.category || {};
  return ["collected", "collected_with_warnings"].includes(run?.status)
    && Boolean(String(store.id || "").trim() && String(store.name || "").trim())
    && Boolean(String(category.id || "").trim() && String(category.path || "").trim());
}

function renderMabangImportPreview(run) {
  const snapshot = run?.snapshot || {};
  const preview = $("mabang-import-preview");
  if (!preview) return;
  const cnsc = String(snapshot.listing_mode || "site").toLowerCase() === "cnsc_global";
  const mode = $("mabang-import-mode");
  if (mode) mode.innerHTML = `<span class="template-mode-badge">${cnsc ? "已识别：CNSC 全球商品" : "已识别：站点商品"}</span>`;
  const targetSiteStores = Array.isArray(snapshot.target_site_stores) ? snapshot.target_site_stores : [];
  const promotionsGroup = $("mabang-import-promotions-group");
  if (promotionsGroup) promotionsGroup.hidden = cnsc && targetSiteStores.length > 0;
  const selectedShopsGroup = $("mabang-import-selected-shops-group");
  if (selectedShopsGroup) selectedShopsGroup.hidden = targetSiteStores.length === 0;
  const coverage = snapshot.coverage || {};
  const setGroup = (id, content, status) => {
    const root = $(id);
    if (!root) return;
    root.innerHTML = `<span class="mabang-import-status ${escapeHtml(status || "missing")}">${escapeHtml(status || "missing")}</span>${content}`;
  };
  const store = snapshot.store || {};
  const category = snapshot.category || {};
  setGroup(
    "mabang-import-store-category",
    `<p>${escapeHtml(store.name || "未识别店铺")} · ${escapeHtml(category.path || "未识别类目")}</p>`,
    coverage.store_category?.status,
  );
  const attributes = Object.values(snapshot.selected_attributes || {});
  setGroup(
    "mabang-import-attributes",
    attributes.length
      ? `<ul>${attributes.map((field) => `<li>${escapeHtml(field.label || field.field_id)}：${escapeHtml((field.value_labels || [field.value]).join("、"))}</li>`).join("")}</ul>`
      : "<p>未采集到已选属性</p>",
    coverage.attributes?.status,
  );
  const variation = snapshot.variation || {};
  setGroup(
    "mabang-import-variation",
    `<p>颜色：${escapeHtml((variation.colors || []).join("、") || "未采集")}</p><p>尺码：${escapeHtml((variation.sizes || []).join("、") || "未采集")}</p><p>SKU：${escapeHtml(variation.sku_count || 0)}</p>`,
    coverage.variation?.status,
  );
  const transaction = snapshot.transaction_defaults || {};
  const priceDefaults = snapshot.price_defaults || {};
  const transactionLabels = {
    stock: "库存",
    weight_kg: "重量(kg)",
    package_length_cm: "包裹长度(cm)",
    package_width_cm: "包裹宽度(cm)",
    package_height_cm: "包裹高度(cm)",
    normal_price: "原价(PHP)",
    sale_price: "售价(PHP)",
  };
  const transactionPreview = { ...transaction, ...priceDefaults };
  if (cnsc) {
    const tiers = snapshot.price_tiers || [];
    const warehouses = snapshot.warehouse_stock_defaults || {};
    const targets = snapshot.target_site_stores || [];
    const cnscLines = [
      ...tiers.map((tier) => `${tier.id} 全球价（CNY）：${tier.global_price_cny || "待补充"}`),
      `泰国仓：${warehouses.TH ?? "待补充"}`,
      `广州仓：${warehouses.CN_GZ ?? "待补充"}`,
      `菲律宾仓：${warehouses.PH ?? "待补充"}`,
      `目标店铺：${targets.map((item) => {
        const site = item.site_code || "";
        const store = item.store_name || item.store_id || "";
        if (!item.discount_catalog_loaded) return `${site} ${store} · 活动未采集`.trim();
        const activity = item.discount_name || item.discount_label || "活动未识别";
        const percent = item.discount_percent ? ` · ${item.discount_percent}%OFF` : "";
        return `${site} ${store} · ${activity}${percent}`.trim();
      }).join("、") || "待补充"}`,
      ...tiers.flatMap((tier) => targets.map((item) => {
        const prices = tier.site_prices?.[item.site_code] || {};
        return `${item.site_code} ${tier.id} 价格 / 折扣价：${prices.original_price ?? "待补充"} / ${prices.sale_price ?? "待补充"}`;
      })),
    ];
    setGroup(
      "mabang-import-transaction",
      `<ul>${cnscLines.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>`,
      (snapshot.missing_configuration || []).length ? "missing" : "complete",
    );
  } else {
    setGroup(
      "mabang-import-transaction",
      `<ul>${Object.entries(transactionLabels).map(([key, label]) => `<li>${label}：${escapeHtml(transactionPreview[key]?.value ?? "待补充")}</li>`).join("")}</ul>`,
      coverage.transaction?.status,
    );
  }
  const selectedPromotion = (snapshot.promotion_catalog || []).find((item) => item.activity_id === snapshot.selected_promotion_id);
  if (targetSiteStores.length > 0) {
    const loaded = snapshot.target_site_stores_loaded === true;
    const shopsLines = targetSiteStores.map((item) => {
      const site = escapeHtml(item.site_code || "?");
      const store = escapeHtml(item.store_name || item.store_id || "未识别店铺");
      const activity = item.discount_catalog_loaded
        ? `${escapeHtml(item.discount_name || item.discount_label || "活动未识别")}${item.discount_percent ? ` · ${escapeHtml(item.discount_percent)}%OFF` : ""}`
        : "活动未采集";
      return `<li><strong>${site}</strong> · ${store} · ${activity}</li>`;
    });
    setGroup(
      "mabang-import-selected-shops",
      `<p>${escapeHtml(targetSiteStores.length)} 家站点${loaded ? "（已完整解析）" : "（部分未解析）"}</p><ul>${shopsLines.join("")}</ul>`,
      loaded ? "complete" : "missing",
    );
    if (!cnsc) {
      const perShopLines = targetSiteStores.map((item) => {
        const site = escapeHtml(item.site_code || "?");
        const label = item.discount_catalog_loaded
          ? `${escapeHtml(item.discount_name || "活动未识别")}${item.discount_percent ? ` · ${escapeHtml(item.discount_percent)}%OFF` : ""}${item.mabang_discount_id ? ` <code>${escapeHtml(item.mabang_discount_id)}</code>` : ""}`
          : "活动未采集";
        return `<li>${site}：${label}</li>`;
      });
      setGroup(
        "mabang-import-promotions",
        `<p>${escapeHtml(selectedPromotion?.name || "主活动未配置")}</p><ul>${perShopLines.join("")}</ul>`,
        targetSiteStores.every((item) => item.discount_catalog_loaded) ? "complete" : "missing",
      );
    }
  } else if (!cnsc) {
    const startAt = String(selectedPromotion?.start_at || "").slice(0, 10);
    const endAt = String(selectedPromotion?.end_at || "").slice(0, 10);
    const validity = startAt && endAt ? `（有效期 ${startAt} 至 ${endAt}）` : "";
    // 活动列表没读到时只有名称、没有有效期；提前说清楚补救入口，别让人以为活动坏了。
    const hint = selectedPromotion && !validity
      ? '<p class="muted">活动有效期没采到（「选择折扣活动」列表未读取）；导入后可在店铺模板里点「重新采集活动」补齐。</p>'
      : "";
    setGroup(
      "mabang-import-promotions",
      `<p>${escapeHtml((selectedPromotion?.name || "活动未配置") + validity)}</p>${hint}`,
      coverage.promotions?.status,
    );
  }
  const sizeChart = snapshot.size_chart || {};
  setGroup(
    "mabang-import-size-chart-upload",
    sizeChart.url ? `<p>已读取图片（${escapeHtml(sizeChart.width || "?")} × ${escapeHtml(sizeChart.height || "?")}）</p>` : "<p>未读取到已上传尺码图</p>",
    sizeChart.url ? "complete" : "missing",
  );
  const sizeChartTemplates = snapshot.size_chart_template_catalog || [];
  setGroup(
    "mabang-import-size-chart-templates",
    sizeChartTemplates.length
      ? `<ul>${sizeChartTemplates.map((item) => `<li>${escapeHtml(item.name || "未命名模板")}</li>`).join("")}</ul>`
      : "<p>未采集到尺码图模板</p>",
    snapshot.size_chart_template_catalog_loaded ? "complete" : "missing",
  );
  const watermarks = snapshot.watermark_catalog || [];
  setGroup(
    "mabang-import-watermarks",
    watermarks.length
      ? `<ul>${watermarks.map((item) => `<li>${escapeHtml(item.name || "未命名水印")}</li>`).join("")}</ul>`
      : `<p>${snapshot.watermark_catalog_loaded ? "该店铺没有可用水印" : "水印菜单未读取到，确认导入不会覆盖已有水印"}</p>`,
    snapshot.watermark_catalog_loaded ? "complete" : "missing",
  );
  const warnings = [
    ...(snapshot.missing_required_attributes || []).map((item) => `${item.label || item.field_id}为必填项，尚未选择`),
    ...(cnsc
      ? (snapshot.missing_configuration || []).map((field) => `${cnscConfigurationLabel(field)}待补充`)
      : Object.entries(transactionPreview).filter(([, item]) => item?.status !== "complete").map(([key]) => `${transactionLabels[key]}待补充`)),
    ...(!cnsc ? Object.entries(priceDefaults)
      .filter(([, item]) => item?.status === "complete" && item.uniform === false)
      .map(([key, item]) => {
        const labels = { normal_price: "原价", sale_price: "售价" };
        return `${labels[key] || transactionLabels[key]}部分 SKU 价格不同，默认按多数价 ${item.value}（另有 ${item.differing_count} 行不同）`;
      }) : []),
    ...(!cnsc && !snapshot.selected_promotion_id ? ["活动未配置"] : []),
  ];
  if ($("mabang-import-warnings")) $("mabang-import-warnings").textContent = warnings.join("；");
  preview.removeAttribute("hidden");
}

function focusFirstMissingStoreTemplateField(template) {
  const missingAttribute = (template?.missing_required_attributes || [])[0]?.field_id || "";
  const categoryControl = missingAttribute
    ? document.querySelector(`[data-category-field-input="${CSS.escape(String(missingAttribute))}"]`)
    : null;
  const transactionControls = [
    "store-template-stock",
    "store-template-weight-kg",
    "store-template-package-length-cm",
    "store-template-package-width-cm",
    "store-template-package-height-cm",
  ].map($).filter((control) => control && !String(control.value || "").trim());
  const cnscMissing = cnscTemplateMissingFields(template);
  const cnscControl = String(template?.listing_mode || "site") === "cnsc_global"
    ? (cnscMissing.includes("target_site_stores")
      ? $("store-template-target-sites")
      : cnscMissing.includes("site_price_matrix")
        ? $("store-template-price-paste")
        : cnscMissing.some((field) => field.startsWith("price_tiers.standard"))
          ? $("store-template-cnsc-global-standard")
          : cnscMissing.some((field) => field.startsWith("price_tiers.large"))
            ? $("store-template-cnsc-global-large")
            : cnscMissing.includes("warehouse_stock_defaults")
              ? $("store-template-warehouse-th")
              : null)
    : null;
  const control = categoryControl || cnscControl || transactionControls[0];
  control?.scrollIntoView({ behavior: "smooth", block: "center" });
  control?.focus({ preventScroll: true });
}

async function confirmMabangPageImport() {
  const result = $("mabang-page-import-result");
  const collectButton = $("collect-mabang-page-import-btn");
  const confirmButton = $("confirm-mabang-page-import-btn");
  if (!state.mabangPageImportRunId) {
    if (result) result.textContent = "确认导入失败：导入任务已失效，请重新创建导入任务。";
    return;
  }
  if (!canConfirmMabangPageImport(state.mabangPageImportRun)) {
    try {
      const latest = await api(`/api/mabang-page-imports/${encodeURIComponent(state.mabangPageImportRunId)}`);
      state.mabangPageImportRun = latest;
      renderMabangImportPreview(latest);
    } catch (error) {
      if (result) result.textContent = `确认导入失败：无法读取导入任务状态：${error.message || "服务不可用"}`;
      return;
    }
  }
  if (!canConfirmMabangPageImport(state.mabangPageImportRun)) {
    const snapshot = state.mabangPageImportRun?.snapshot || {};
    const missing = [];
    if (!snapshot.store?.id || !snapshot.store?.name) missing.push("店铺");
    if (!snapshot.category?.id || !snapshot.category?.path) missing.push("类目");
    if (!["collected", "collected_with_warnings"].includes(state.mabangPageImportRun?.status)) missing.push("采集快照");
    if (result) result.textContent = `确认导入失败：${missing.length ? `缺少${missing.join("、")}信息` : "当前采集状态不可确认"}。请重新采集后重试。`;
    return;
  }
  if (collectButton) collectButton.disabled = true;
  if (confirmButton) confirmButton.disabled = true;
  if (result) result.textContent = "正在确认导入店铺模板...";
  let confirmed;
  try {
    confirmed = await api(`/api/mabang-page-imports/${encodeURIComponent(state.mabangPageImportRunId)}/confirm`, {
      method: "POST", body: JSON.stringify({}),
    });
  } catch (error) {
    if (result) result.textContent = `确认导入失败：${error.message || "请检查预览后重试"}`;
    if (collectButton) collectButton.disabled = false;
    if (confirmButton) confirmButton.disabled = !canConfirmMabangPageImport(state.mabangPageImportRun);
    return;
  }

  state.mabangPageImportRun = confirmed.run;
  try {
    await Promise.all([loadMabangStoreOptions(), loadStoreTemplates(), loadCategoryProfiles(), loadReadiness()]);
    $("store-template-store").value = confirmed.template.mabang_store_id || "";
    await loadStorePromotions();
    await openStoreTemplateEditor(confirmed.template.id);
    focusFirstMissingStoreTemplateField(confirmed.template);
    if (result) result.textContent = `已导入模板：${confirmed.template.store_name || ""} / ${confirmed.template.template_name || ""}`;
    $("mabang-page-import-dialog")?.close();
  } catch (error) {
    // The confirm request already succeeded. A catalog refresh failure must not
    // make the operator retry confirmation and create a duplicate template.
    if (result) result.textContent = `模板已导入，但页面刷新失败：${error.message || "请刷新页面查看"}`;
    $("mabang-page-import-dialog")?.close();
  }
}

// 复制到店铺模板的商品来源：优先「勾选的多商品」，其次「点卡片选中的单个」。
function baseProductsToCopy() {
  const ids = [...state.selectedBaseProducts];
  const items = ids.length
    ? ids.map((id) => state.baseProducts.find((item) => item.id === id)).filter(Boolean)
    : (state.selectedBaseProductId ? (state.baseProducts.filter((item) => item.id === state.selectedBaseProductId)) : []);
  return items;
}

// 「复制到店铺模板」的两张卡片：本土默认选中，点右边切到跨境；两边各自按店铺分组。
function copyTemplatePickerMode() {
  return state.copyTemplatePickerMode === "cnsc" ? "cnsc" : "site";
}

function setCopyTemplatePickerMode(mode) {
  // 跨境模式关闭时不允许切到跨境卡片，保持本土模式。
  if (mode === "cnsc" && !crossBorderModeEnabled()) mode = "site";
  state.copyTemplatePickerMode = mode === "cnsc" ? "cnsc" : "site";
  renderCopyTemplatePicker($("copy-template-search")?.value || "");
}

function templateMatchesPickerMode(template, mode) {
  const cnsc = isCNSCListingCopy(template);
  return mode === "cnsc" ? cnsc : !cnsc;
}

function openCopyTemplatePicker() {
  const items = baseProductsToCopy();
  if (!items.length) { $("base-product-result").textContent = "请先勾选或选择一个基础商品。"; return; }
  const titleEl = $("copy-template-picker-title");
  if (titleEl) titleEl.textContent = items.length > 1 ? `复制 ${items.length} 个商品到店铺模板` : "复制到店铺模板";
  renderCopyTemplatePicker();
  $("copy-template-picker-dialog")?.showModal();
}

function renderCopyTemplatePicker(query = "") {
  const root = $("copy-template-picker-list");
  if (!root) return;
  const text = query.trim().toLowerCase();
  // 跨境模式关闭：强制本土模式并隐藏跨境卡片。
  const mode = crossBorderModeEnabled() ? copyTemplatePickerMode() : "site";
  if (mode === "site" && state.copyTemplatePickerMode === "cnsc") state.copyTemplatePickerMode = "site";
  $("copy-template-mode-cnsc")?.toggleAttribute("hidden", !crossBorderModeEnabled());
  const byMode = state.storeTemplates.filter((item) => templateMatchesPickerMode(item, mode));
  const templates = byMode.filter((item) => !text || [item.store_name, item.template_name, item.marketplace_category_path].some((value) => String(value || "").toLowerCase().includes(text)));

  // 卡片高亮与计数（计数是各模式下的模板总数，不受搜索影响）
  const siteCount = state.storeTemplates.filter((item) => templateMatchesPickerMode(item, "site")).length;
  const cnscCount = state.storeTemplates.filter((item) => templateMatchesPickerMode(item, "cnsc")).length;
  const siteCard = $("copy-template-mode-site");
  const cnscCard = $("copy-template-mode-cnsc");
  if (siteCard) {
    siteCard.classList.toggle("is-active", mode === "site");
    siteCard.setAttribute("aria-selected", mode === "site" ? "true" : "false");
  }
  if (cnscCard) {
    cnscCard.classList.toggle("is-active", mode === "cnsc");
    cnscCard.setAttribute("aria-selected", mode === "cnsc" ? "true" : "false");
  }
  const siteCountEl = $("copy-template-mode-site-count");
  const cnscCountEl = $("copy-template-mode-cnsc-count");
  if (siteCountEl) siteCountEl.textContent = `${siteCount} 个模板`;
  if (cnscCountEl) cnscCountEl.textContent = `${cnscCount} 个模板`;

  // 同一张卡片里再按店铺分组：一眼看出"这个店里有哪些模板"。
  const groups = new Map();
  for (const item of templates) {
    const key = String(item.store_name || "未指定店铺");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  const renderItem = (item) => {
    const cnsc = isCNSCListingCopy(item);
    const missing = [];
    if (!item.category_profile_version_id) missing.push("类目规则");
    if (!cnsc && !item.promotion_activity_id && !item.mabang_discount_id) missing.push("活动未配置");
    if (!cnsc && !item.stock) missing.push("库存");
    if (cnsc) missing.push(...cnscTemplateMissingFields(item).map(cnscConfigurationLabel));
    if (!item.weight_kg) missing.push("重量");
    const blockReason = cnsc && missing.length ? "template_needs_configuration" : "";
    // 本土复制以模板尺码为准（模板 chip 就是店铺刊登的尺码轴）；跨境仍按商品尺码
    // （勾选 → 识别 → 模板）。多商品复制时本土共用一个模板尺码轴。
    const copyItems = baseProductsToCopy();
    const firstBase = copyItems[0] || {};
    const baseSizes = ((firstBase.selected_sizes && firstBase.selected_sizes.length) ? firstBase.selected_sizes : (firstBase.recognized_sizes || [])).filter(Boolean);
    const templateSizes = (item.sizes || []).filter(Boolean);
    let sizeNote = "";
    if (cnsc) {
      if (copyItems.length > 1) {
        sizeNote = `<small class="template-size-mismatch">批量复制 ${copyItems.length} 个商品，各按自身尺码生成 SKU（示例：${escapeHtml(firstBase.product_name_cn || firstBase.main_sku || "商品")}${baseSizes.length ? ` 尺码 ${escapeHtml(baseSizes.join("/"))}` : " 未配置尺码"}）。</small>`;
      } else if (baseSizes.length && templateSizes.length && baseSizes.join(",").toLowerCase() !== templateSizes.join(",").toLowerCase()) {
        sizeNote = `<small class="template-size-mismatch">复制将以商品尺码（${escapeHtml(baseSizes.join("/"))}）生成 SKU（模板尺码 ${escapeHtml(templateSizes.join("/"))} 将被商品尺码覆盖）。</small>`;
      } else if (baseSizes.length && !templateSizes.length) {
        sizeNote = `<small class="template-size-mismatch">该模板未配置尺码，复制将以商品尺码（${escapeHtml(baseSizes.join("/"))}）生成 SKU。</small>`;
      } else if (!baseSizes.length && !templateSizes.length) {
        sizeNote = `<small class="template-size-mismatch">商品与模板均未配置尺码，将按默认尺码生成 SKU。</small>`;
      }
    } else if (templateSizes.length) {
      const baseDiffers = baseSizes.length && baseSizes.join(",").toLowerCase() !== templateSizes.join(",").toLowerCase();
      const batch = copyItems.length > 1 ? `（${copyItems.length} 个商品共用模板尺码）` : "";
      sizeNote = `<small class="template-size-mismatch">复制按模板尺码（${escapeHtml(templateSizes.join("/"))}）生成 SKU${baseDiffers ? `；商品尺码（${escapeHtml(baseSizes.join("/"))}）不参与` : ""}${batch}。</small>`;
    } else if (baseSizes.length) {
      sizeNote = `<small class="template-size-mismatch">该模板未配置尺码，复制将以商品尺码（${escapeHtml(baseSizes.join("/"))}）生成 SKU。</small>`;
    } else {
      sizeNote = `<small class="template-size-mismatch">商品与模板均未配置尺码，将按默认尺码生成 SKU。</small>`;
    }
    return `<label class="template-picker-item" data-block-reason="${blockReason}"><input type="checkbox" data-store-template-select="${escapeHtml(item.id)}" ${state.selectedStoreTemplates.has(item.id) ? "checked" : ""} ${missing.length ? "disabled" : ""} /><span><strong>${cnsc ? '<span class="template-mode-badge">CNSC</span> ' : ""}${escapeHtml(item.store_name)} · ${escapeHtml(item.template_name)}</strong><small>${escapeHtml(item.marketplace_category_path || "未选类目")} · ${escapeHtml(missing.join("、") || "配置完整")}</small>${sizeNote}</span></label>`;
  };
  root.innerHTML = groups.size
    ? [...groups.entries()].map(([store, list]) => `
        <div class="template-picker-group">
          <div class="template-picker-group-head">${escapeHtml(store)}<small>${list.length} 个模板</small></div>
          ${list.map(renderItem).join("")}
        </div>`).join("")
    : `<div class="empty-state">${mode === "cnsc" ? "没有跨境（CNSC）模板" : "没有本土店铺模板"}</div>`;
}

async function uploadSizeChart(file) {
  const contentBase64 = await new Promise((resolve, reject) => {
    const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1] || ""); reader.onerror = reject; reader.readAsDataURL(file);
  });
  state.sizeChartAsset = await api("/api/uploads/size-chart", { method: "POST", body: JSON.stringify({ file_name: file.name, content_type: file.type, content_base64: contentBase64 }) });
  const preview = $("store-template-size-chart-preview");
  if (preview) {
    preview.src = sizeChartPreviewUrl(state.sizeChartAsset.preview_url, 720);
    preview.hidden = !state.sizeChartAsset.preview_url;
  }
}

async function uploadDefaultSkuImage(file) {
  const contentBase64 = await new Promise((resolve, reject) => {
    const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1] || ""); reader.onerror = reject; reader.readAsDataURL(file);
  });
  state.defaultSkuImageAsset = await api("/api/uploads/default-sku-image", { method: "POST", body: JSON.stringify({ file_name: file.name, content_type: file.type, content_base64: contentBase64 }) });
  renderDefaultSkuImagePreview();
}

function renderDefaultSkuImagePreview() {
  const preview = $("store-template-default-sku-preview");
  const empty = $("store-template-default-sku-empty");
  const path = $("store-template-default-sku-path")?.value.trim() || "";
  const src = state.defaultSkuImageAsset?.preview_url || "";
  if (preview) {
    preview.src = src;
    preview.hidden = !src;
  }
  if (empty) empty.hidden = Boolean(src) || Boolean(path);
}

// 副本编辑器里的状态行：成功是普通灰字，失败标红并把弹窗滚到那一行，
// 避免"错误只出现在页面顶部提示里、副本里看不到"（现场反馈 2026-09-28）。
function setListingCopyEditorStatus(message, { error = false } = {}) {
  const target = $("listing-copy-save-result");
  if (!target) return;
  target.textContent = message || "";
  target.classList.toggle("listing-copy-status-error", Boolean(error && message));
  if (error && message) {
    const dialog = $("listing-copy-editor-dialog");
    if (dialog) dialog.scrollTop = dialog.scrollHeight;
  }
}

// 上次自动化执行的结果：点「检查」时把失败原因带进副本里，操作员知道要改什么。
function renderListingCopyLastRun(copy) {
  const target = $("listing-copy-last-run");
  if (!target) return;
  const job = (state.automationJobs || [])
    .filter((item) => item.listing_copy_id === copy?.id)
    .sort((left, right) => String(right.updated_at || right.created_at || "").localeCompare(String(left.updated_at || left.created_at || "")))[0];
  if (!job) {
    target.hidden = true;
    target.textContent = "";
    return;
  }
  const result = job.result || {};
  const failed = ["failed", "blocked", "needs_manual", "needs_confirmation", "needs_regeneration", "canceled"].includes(String(job.status || ""));
  const manual = result.manual_confirmation || null;
  const rawError = job.last_error || result.error || "";
  const parts = [`上次执行：${TASK_STATUS_LABELS[job.status] || job.status || "未知"}`];
  if (failed && rawError) parts.push(automationErrorLabel(rawError));
  if (manual) parts.push(`人工确认：${manual.status === "saved" ? "已完成" : "失败"}${manual.at ? `（${formatLocalTime(manual.at)}）` : ""}`);
  if (job.current_phase) parts.push(`阶段：${automationPhaseLabel(job.current_phase)}`);
  target.hidden = false;
  target.textContent = parts.join(" · ");
  target.classList.toggle("listing-copy-last-run--failed", failed);
}

async function loadListingCopyDetail(copyId) {
  return api(`/api/store-listing-copies/${encodeURIComponent(copyId)}`);
}

async function loadListingCopyPromotions(copy) {
  if (isCNSCListingCopy(copy)) {
    state.listingCopyPromotions = [];
    return;
  }
  if (!copy?.mabang_store_id) {
    state.listingCopyPromotions = [];
    return;
  }
  const data = await api(`/api/store-promotions?store_id=${encodeURIComponent(copy.mabang_store_id)}`);
  state.listingCopyPromotions = data.items || [];
}

async function openListingCopyEditor(copyId) {
  if (!copyId) return;
  state.editingListingCopyId = copyId;
  state.editingListingCopy = null;
  state.editingListingCopyImages = [];
  state.listingCopySizeChartAsset = null;
  setListingCopyEditorStatus("正在加载副本详情...");
  const dialog = $("listing-copy-editor-dialog");
  dialog?.showModal();
  if (dialog) dialog.scrollTop = 0;
  const detail = await loadListingCopyDetail(copyId);
  state.editingListingCopy = detail;
  state.editingListingCopyImages = [...(detail.product_images || [])];
  if (isCNSCListingCopy(detail)) {
    await loadListingCopyCnscConfig(detail);
  } else {
    await loadListingCopyPromotions(detail);
  }
  renderListingCopyEditor(detail);
  if (dialog) dialog.scrollTop = 0;
}

function renderListingCopyEditor(copy) {
  const cnsc = isCNSCListingCopy(copy);
  $("listing-copy-editor-meta").textContent = [cnsc ? "跨境 CNSC" : "本土店铺", copy.store_name, copy.main_sku || copy.group_id, copy.marketplace_category_path].filter(Boolean).join(" · ");
  $("listing-copy-title").value = copy.listing_title || copy.title || "";
  $("listing-copy-description").value = copy.description || "";
  $("listing-copy-stock").value = copy.stock ?? "";
  $("listing-copy-weight-kg").value = copy.weight_kg ?? "";
  $("listing-copy-package-length-cm").value = copy.package_length_cm ?? "";
  $("listing-copy-package-width-cm").value = copy.package_width_cm ?? "";
  $("listing-copy-package-height-cm").value = copy.package_height_cm ?? "";
  $("listing-copy-promotion-section").hidden = cnsc;
  $("listing-copy-stock-section").hidden = cnsc;
  $("listing-copy-apply-stock").hidden = cnsc;
  renderListingCopyDescriptionProfiles(copy);
  if (!cnsc) renderListingCopyPromotions(copy);
  renderListingCopyProductImages();
  renderListingCopySizeChart(copy);
  renderListingCopyVideo(copy);
  renderListingCopyCnscConfig(copy);
  renderListingCopySkuTable(copy);
  renderListingCopyReadiness(copy);
  renderListingCopyLastRun(copy);
  setListingCopyEditorStatus("");
  $("listing-copy-save-result").textContent = "";
}

function renderListingCopyDescriptionProfiles(copy) {
  const select = $("listing-copy-description-profile");
  const selected = copy.description_profile_version_id || "";
  const options = state.descriptionProfiles.map((item) =>
    `<option value="${escapeHtml(item.id)}" ${item.id === selected ? "selected" : ""}>${escapeHtml(item.name)} · v${escapeHtml(item.version || 1)}</option>`
  );
  if (selected && !state.descriptionProfiles.some((item) => item.id === selected)) {
    options.unshift(`<option value="${escapeHtml(selected)}" selected>当前描述方案</option>`);
  }
  select.innerHTML = '<option value="">请选择描述生成方案</option>' + options.join("");
}

function promotionActivityId(item) {
  return String(item?.activity_id || item?.id || "");
}

function promotionActivityLabel(item) {
  const name = item?.label || item?.name || item?.activity_name || promotionActivityId(item);
  // 「不可用」只应表示确定不可用（已停用/已过期/未开始）；状态没采到（unresolved）
  // 是另一回事，要引导去补齐采集，而不是让操作员以为活动坏了。
  const status = String(item?.status || "");
  if (!status || status === "available") return name;
  if (status === "unresolved") return `${name}（未采集到活动状态，请先在本店铺采集该活动）`;
  if (status === "expired") return `${name}（已过期）`;
  if (status === "not_started") return `${name}（未开始）`;
  return `${name}（不可用）`;
}

function renderListingCopyPromotions(copy) {
  const select = $("listing-copy-promotion");
  const selected = copy.promotion_activity_id || copy.mabang_discount_id || "";
  const options = state.listingCopyPromotions.map((item) => {
    const activityId = promotionActivityId(item);
    return `<option value="${escapeHtml(activityId)}" data-promotion-label="${escapeHtml(promotionActivityLabel(item))}" ${activityId === selected ? "selected" : ""}>${escapeHtml(promotionActivityLabel(item))}</option>`;
  });
  if (selected && !state.listingCopyPromotions.some((item) => promotionActivityId(item) === selected)) {
    options.unshift(`<option value="${escapeHtml(selected)}" data-promotion-label="${escapeHtml(copy.discount_label || "当前促销活动")}" selected>${escapeHtml(copy.discount_label || "当前促销活动（未在活动列表中）")}</option>`);
  }
  select.innerHTML = '<option value="">活动未配置</option>' + options.join("");
}

function listingCopyAssetByName(fileName) {
  const copy = state.editingListingCopy || {};
  const wanted = String(fileName || "").toLowerCase();
  if (!wanted) return undefined;
  const matches = state.assets.filter((asset) =>
    String(asset.group_id || "").toUpperCase() === String(copy.group_id || "").toUpperCase()
    && String(asset.file_name || "").toLowerCase() === wanted
  );
  if (!matches.length) return undefined;
  // 重复导入同一个 SKU 后素材库里有同名旧图（HN0244 两次导入的「服装颜色替换生成图片
  // (1).png」名字一样、图不一样）：先认副本自己那次导入的目录，找不到再退回旧目录。
  const root = String(copy.asset_root || "").replace(/[\\/]+$/, "").replace(/\\/g, "/").toLowerCase();
  if (!root) return matches[0];
  return matches.find((asset) => {
    const path = String(asset.file_path || "").replace(/\\/g, "/").toLowerCase();
    return path === root || path.startsWith(`${root}/`);
  }) || matches[0];
}

function renderListingCopyProductImages() {
  const root = $("listing-copy-product-images");
  if (!root) return;
  root.innerHTML = state.editingListingCopyImages.map((fileName, index) => {
    const asset = listingCopyAssetByName(fileName);
    const image = asset
      ? `<img src="${escapeHtml(assetImageUrl(asset, 640))}" alt="${escapeHtml(fileName)}" />`
      : `<div class="listing-copy-image-placeholder">${escapeHtml(fileName)}</div>`;
    return `<div class="listing-copy-image-item" draggable="true" data-listing-copy-image-index="${index}">${image}<div><strong>${index === 0 ? "主图" : `第 ${index + 1} 张`}</strong><span title="${escapeHtml(fileName)}">${escapeHtml(fileName)}</span></div></div>`;
  }).join("") || '<div class="empty-state">暂无商品图</div>';
}

function moveListingCopyImage(fromIndex, toIndex) {
  if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) return;
  if (fromIndex >= state.editingListingCopyImages.length || toIndex >= state.editingListingCopyImages.length) return;
  const [moved] = state.editingListingCopyImages.splice(fromIndex, 1);
  state.editingListingCopyImages.splice(toIndex, 0, moved);
  renderListingCopyProductImages();
}

function renderListingCopySizeChart(copy) {
  const preview = $("listing-copy-size-chart-preview");
  const empty = $("listing-copy-size-chart-empty");
  const asset = listingCopyAssetByName(copy.size_chart_image || "");
  const src = copy.size_chart_preview_url
    || (copy.size_chart_asset_id ? `/api/uploads/size-chart/${encodeURIComponent(copy.size_chart_asset_id)}` : "")
    || (asset ? assetImageUrl(asset, 640) : "");
  preview.src = sizeChartPreviewUrl(src, 720);
  preview.hidden = !src;
  empty.hidden = !!src;
  $("listing-copy-size-chart-file-name").textContent = src ? (copy.size_chart_image || "当前尺码图") : "支持 PNG、JPEG、WebP";
  // 「产品图最后一张放尺码表」：跨境隐藏；本土按副本快照回显（缺字段＝开）。
  $("listing-copy-size-chart-last-image-line")?.toggleAttribute("hidden", isCNSCListingCopy(copy));
  if ($("listing-copy-size-chart-last-image")) {
    $("listing-copy-size-chart-last-image").checked = copy.size_chart_as_last_product_image !== false;
  }
}

function openListingCopyFilePicker(inputId) {
  const input = $(inputId);
  if (!(input instanceof HTMLInputElement) || input.disabled) return;
  input.value = "";
  input.click();
}

function showListingCopyUploadFileName(kind, file) {
  const target = $(kind === "video" ? "listing-copy-video-file-name" : "listing-copy-size-chart-file-name");
  if (target) target.textContent = file?.name || "未选择文件";
}

async function uploadListingCopySizeChart(file) {
  const contentBase64 = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
  state.listingCopySizeChartAsset = await api("/api/uploads/size-chart", {
    method: "POST",
    body: JSON.stringify({ file_name: file.name, content_type: file.type, content_base64: contentBase64 }),
  });
  const preview = $("listing-copy-size-chart-preview");
  preview.src = sizeChartPreviewUrl(state.listingCopySizeChartAsset.preview_url, 720);
  preview.hidden = false;
  $("listing-copy-size-chart-empty").hidden = true;
  $("listing-copy-save-result").textContent = "尺码图已上传，保存副本后生效";
}

function renderListingCopyVideo(copy) {
  const video = copy?.video || { validation_status: "missing", validation_errors: ["video_missing"] };
  const preview = $("listing-copy-video-preview");
  const status = $("listing-copy-video-status");
  const hasFile = Boolean(video.path || video.asset_id);
  preview.src = hasFile ? `/api/store-listing-copies/${encodeURIComponent(copy.id)}/video/file` : "";
  preview.hidden = !hasFile;
  $("listing-copy-video-remove").disabled = !hasFile;
  const metadata = video.validation_status === "valid"
    ? `${video.file_name || "商品视频"} · ${video.duration_seconds || 0} 秒 · ${video.width || 0}×${video.height || 0} · ${(Number(video.size_bytes || 0) / 1024 / 1024).toFixed(1)} MB`
    : (video.validation_errors || ["video_missing"]).map((code) => VIDEO_ERROR_LABELS[code] || code).join("；");
  status.textContent = metadata;
  status.className = `muted video-status ${video.validation_status || "missing"}`;
  $("listing-copy-video-file-name").textContent = hasFile ? (video.file_name || "当前视频") : "10-60 秒，最大 30 MB";
}

async function fileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function uploadListingCopyVideo(file) {
  if (file.size > 30 * 1024 * 1024) throw new Error("视频文件大小不能超过 30 MB");
  const updated = await api(`/api/store-listing-copies/${encodeURIComponent(state.editingListingCopyId)}/video`, {
    method: "POST",
    body: JSON.stringify({ file_name: file.name, content_type: file.type || "video/mp4", content_base64: await fileAsBase64(file) }),
  });
  state.editingListingCopy = updated;
  renderListingCopyVideo(updated);
  $("listing-copy-save-result").textContent = updated.video?.validation_status === "valid" ? "视频已保存" : "视频规格不合规，自动化时将跳过上传";
}

async function removeListingCopyVideo() {
  const updated = await api(`/api/store-listing-copies/${encodeURIComponent(state.editingListingCopyId)}/video/remove`, {
    method: "POST", body: JSON.stringify({}),
  });
  state.editingListingCopy = updated;
  renderListingCopyVideo(updated);
  $("listing-copy-save-result").textContent = "视频已删除，不影响保存商品";
}

function renderListingCopySkuTable(copy) {
  const tbody = $("listing-copy-sku-table")?.querySelector("tbody");
  const table = $("listing-copy-sku-table");
  if (!tbody || !table) return;
  const cnsc = isCNSCListingCopy(copy);
  // CNSC 副本显示仓库批量库存条，普通副本显示统一库存按钮，二者互斥。
  const warehouseBatch = $("listing-copy-warehouse-batch");
  if (warehouseBatch) warehouseBatch.hidden = !cnsc;
  const applyStockButton = $("listing-copy-apply-stock");
  if (applyStockButton) applyStockButton.hidden = cnsc;
  table.querySelector("thead").innerHTML = cnsc
    ? "<tr><th>Seller SKU</th><th>颜色</th><th>尺码</th><th>价格档位</th><th>全球价（CNY）</th><th>泰国仓</th><th>广州仓</th><th>菲律宾仓</th><th>站点价格矩阵</th></tr>"
    : "<tr><th>SKU</th><th>颜色</th><th>尺码</th><th>库存</th><th>原价</th><th>六折售价</th></tr>";
  tbody.innerHTML = (copy.skus || []).map((sku) => {
    if (cnsc) {
      const warehouses = sku.warehouse_stocks || {};
      const matrix = JSON.stringify(sku.site_price_matrix || {});
      const warehouseInput = (code) => `<input data-listing-copy-cnsc-warehouse="${escapeHtml(code)}" data-sku-id="${escapeHtml(sku.id)}" type="number" min="0" step="1" value="${escapeHtml(warehouses[code] ?? 0)}" />`;
      return `<tr><td title="${escapeHtml(sku.sku_code || "")}">${escapeHtml(sku.sku_code || "-")}</td><td>${escapeHtml(sku.color_cn || sku.color_code || "-")}</td><td>${escapeHtml(sku.size || "-")}</td><td>${escapeHtml(sku.price_tier_id || "-")}</td><td>${escapeHtml(sku.global_price_cny ?? "-")}</td><td>${warehouseInput("TH")}</td><td>${warehouseInput("CN_GZ")}</td><td>${warehouseInput("PH")}</td><td><details><summary>查看</summary><code>${escapeHtml(matrix)}</code></details></td></tr>`;
    }
    const price = sku.price || {};
    return `<tr><td title="${escapeHtml(sku.sku_code || "")}">${escapeHtml(sku.sku_code || "-")}</td><td>${escapeHtml(sku.color_cn || sku.color_code || "-")}</td><td>${escapeHtml(sku.size || "-")}</td><td><input data-listing-copy-sku-stock="${escapeHtml(sku.id)}" type="number" min="0" step="1" value="${escapeHtml(sku.stock ?? "")}" /></td><td><input data-listing-copy-normal-price="${escapeHtml(price.id || "")}" data-sku-id="${escapeHtml(sku.id)}" type="number" min="0" step="0.01" value="${escapeHtml(price.normal_price ?? "")}" /></td><td><input data-listing-copy-sale-price="${escapeHtml(price.id || "")}" data-sku-id="${escapeHtml(sku.id)}" type="number" min="0" step="0.01" value="${escapeHtml(price.sale_price ?? "")}" /></td></tr>`;
  }).join("") || `<tr><td colspan="${cnsc ? 9 : 6}" class="muted">暂无 SKU 明细</td></tr>`;
}

function isCNSCListingCopy(copy) {
  return String(copy?.listing_mode || "site").toLowerCase() === "cnsc_global";
}

// Decision B: seed the copy editor's per-target discount state from the copy's
// own target_site_stores, then pull each store's live activity catalog so the
// operator can pick a 折扣活动 directly in the copy editor (mirrors the template
// editor's cnscTargetStoreConfigs). Failures leave discount_catalog_loaded=false
// so the UI can surface an "活动未采集" hint instead of an empty dropdown.
async function loadListingCopyCnscConfig(copy) {
  state.listingCopyTargetConfigs = new Map();
  for (const target of Array.isArray(copy.target_site_stores) ? copy.target_site_stores : []) {
    const code = String(target.site_code || "").trim().toUpperCase();
    if (!code) continue;
    state.listingCopyTargetConfigs.set(code, {
      site_code: code,
      store_id: String(target.store_id || "").trim(),
      store_name: target.store_name || "",
      mabang_discount_id: target.mabang_discount_id || "",
      discount_name: target.discount_name || "",
      discount_label: target.discount_label || "",
      discount_percent: target.discount_percent ?? "",
      discount_catalog: Array.isArray(target.discount_catalog) ? target.discount_catalog : [],
      discount_catalog_loaded: target.discount_catalog_loaded === true,
    });
  }
  await Promise.all([...state.listingCopyTargetConfigs.values()].map(async (config) => {
    if (!config.store_id) return;
    try {
      const data = await api(`/api/store-promotions?store_id=${encodeURIComponent(config.store_id)}&available_only=true`);
      config.discount_catalog = (data.items || []).map((item) => ({
        mabang_discount_id: promotionActivityId(item),
        name: promotionActivityLabel(item),
        discount_percent: item.discount_percent ?? "",
      }));
      config.discount_catalog_loaded = true;
    } catch (_) {
      config.discount_catalog_loaded = config.discount_catalog.length > 0;
    }
  }));
}

function renderListingCopyCnscConfig(copy) {
  const section = $("listing-copy-cnsc-section");
  if (!section) return;
  const cnsc = isCNSCListingCopy(copy);
  section.hidden = !cnsc;
  if (!cnsc) return;
  $("listing-copy-cnsc-default-discount").value = copy.default_site_discount_percent ?? "";
  const modeSelect = $("listing-copy-cnsc-binding-mode");
  if (modeSelect) modeSelect.value = listingCopyCnscBindingMode(copy);
  const matrixInput = $("listing-copy-cnsc-price-matrix");
  if (matrixInput) matrixInput.value = "";
  const preview = $("listing-copy-cnsc-price-preview");
  if (preview) {
    preview.textContent = "";
    preview.hidden = true;
  }
  state.listingCopyCnscPricePreview = null;
  // Only the target-site discount block collapses; the price tier area stays
  // visible. Reset the collapse so each editor session starts folded.
  const targetDetails = $("listing-copy-cnsc-target-details");
  if (targetDetails) targetDetails.open = false;
  renderListingCopyCnscTargets();
  renderListingCopyCnscTiers(copy);
}

function renderListingCopyCnscTargets() {
  const tbody = $("listing-copy-cnsc-target-table")?.querySelector("tbody");
  if (!tbody) return;
  const configs = [...state.listingCopyTargetConfigs.values()];
  if (!configs.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="muted">未配置目标站点店铺，请在店铺模板补充</td></tr>';
    return;
  }
  const defaultDiscount = $("listing-copy-cnsc-default-discount")?.value || "默认";
  tbody.innerHTML = configs.map((config) => {
    const catalog = [...(config.discount_catalog || [])];
    if (config.mabang_discount_id && !catalog.some((item) => (item.mabang_discount_id || item.activity_id) === config.mabang_discount_id)) {
      catalog.unshift({ mabang_discount_id: config.mabang_discount_id, name: config.discount_name || config.discount_label || "当前活动" });
    }
    const activityCell = (config.discount_catalog_loaded || catalog.length)
      ? `<select data-listing-copy-cnsc-activity="${escapeHtml(config.site_code)}">${['<option value="">选择折扣活动</option>', ...catalog.map((item) => {
          const id = item.mabang_discount_id || item.activity_id || "";
          const name = item.name || item.discount_name || id;
          return `<option value="${escapeHtml(id)}" data-discount-name="${escapeHtml(name)}" ${id === config.mabang_discount_id ? "selected" : ""}>${escapeHtml(name)}</option>`;
        })].join("")}</select>`
      : '<span class="muted">活动未采集</span>';
    return `<tr><td><strong>${escapeHtml(config.site_code)}</strong></td><td>${escapeHtml(config.store_name || config.store_id || "-")}</td><td>${activityCell}</td><td><input data-listing-copy-cnsc-discount="${escapeHtml(config.site_code)}" type="number" min="0.01" max="99.99" step="0.01" value="${escapeHtml(config.discount_percent ?? "")}" placeholder="继承 ${escapeHtml(defaultDiscount)}" /></td></tr>`;
  }).join("");
  renderListingCopyCnscSummary();
}

// Compact at-a-glance summary for the collapsed target-site discount block.
function renderListingCopyCnscSummary() {
  const summary = $("listing-copy-cnsc-summary");
  if (!summary) return;
  const codes = [...state.listingCopyTargetConfigs.values()].map((config) => config.site_code).filter(Boolean);
  if (!codes.length) {
    summary.textContent = "未配置目标站点店铺，请在店铺模板补充";
    return;
  }
  const configured = codes.filter((code) => {
    const activity = document.querySelector(`[data-listing-copy-cnsc-activity="${CSS.escape(code)}"]`);
    return activity ? String(activity.value || "").trim() !== "" : String(state.listingCopyTargetConfigs.get(code)?.mabang_discount_id || "").trim() !== "";
  });
  summary.textContent = configured.length === codes.length
    ? `${codes.length} 站点 · ✅ 折扣活动已配`
    : `${codes.length} 站点 · ⚠ ${configured.length}/${codes.length} 已选折扣活动`;
}

// Binding mode is persisted on the copy as cnsc_binding_mode ("colors" = one
// row per color covering that color's sizes; "sizes" = exclusive size tiers).
// Legacy copies without the field fall back to shape inference: any tier with
// colors → colors; otherwise sizes (a lone unconstrained uniform tier renders
// as one "全部 SKU" colors row, keeping the old 全覆盖统一价 behaviour).
function listingCopyCnscBindingMode(copy) {
  const tiers = Array.isArray(copy?.price_tiers) ? copy.price_tiers : [];
  const stored = String(copy?.cnsc_binding_mode || "").trim().toLowerCase();
  if (stored === "colors" || stored === "sizes") return stored;
  if (tiers.some((tier) => (tier.colors || []).length)) return "colors";
  if (tiers.some((tier) => (tier.sizes || []).length)) return "sizes";
  return "colors";
}

function emptyListingCopyCnscTier() {
  return { id: "uniform", sizes: [], colors: [], global_price_cny: "", site_prices: {} };
}

function renderListingCopyCnscTiers(copy) {
  const mode = listingCopyCnscBindingMode(copy);
  const modeSelect = $("listing-copy-cnsc-binding-mode");
  if (modeSelect) modeSelect.value = mode;
  // 「新增价格档」只在按尺码分档下有意义（colors 行随颜色变体自动增减）。
  const addTierBtn = $("listing-copy-cnsc-add-tier");
  if (addTierBtn) addTierBtn.hidden = mode !== "sizes";
  let tiers = Array.isArray(copy.price_tiers) ? copy.price_tiers : [];
  // colors 模式：行随副本颜色变体自动生成（不持久化到 copy.price_tiers，保存时重建）。
  if (mode === "colors") {
    renderListingCopyCnscColorRows(copy);
    return;
  }
  if (!tiers.length) tiers = [emptyListingCopyCnscTier()];
  renderListingCopyCnscTierRows(tiers, "sizes");
}

function renderListingCopyCnscTierRows(tiers, mode) {
  const table = $("listing-copy-cnsc-tier-table");
  const tbody = table?.querySelector("tbody");
  if (!tbody) return;
  const m = mode || $("listing-copy-cnsc-binding-mode")?.value || "colors";
  const showSizes = m === "sizes";
  const showGlobal = m === "sizes";
  table?.querySelectorAll('[data-cnsc-tier-col="sizes"]').forEach((cell) => cell.toggleAttribute("hidden", !showSizes));
  table?.querySelectorAll('[data-cnsc-tier-col="global"]').forEach((cell) => cell.toggleAttribute("hidden", !showGlobal));
  const codes = [...state.listingCopyTargetConfigs.values()].map((config) => config.site_code).filter(Boolean);
  tbody.innerHTML = tiers.map((tier) => {
    const tierId = String(tier.id || "");
    const site = tier.site_prices || {};
    const priceCodes = codes.length ? codes : Object.keys(site);
    const matrix = priceCodes.length ? priceCodes.map((code) => {
      const prices = site[code] || {};
      return `<div class="listing-copy-cnsc-site-price"><span>${escapeHtml(code)}</span><input data-listing-copy-cnsc-price="${escapeHtml(tierId)}" data-code="${escapeHtml(code)}" data-kind="original" type="number" min="0" step="0.01" value="${escapeHtml(prices.original_price ?? "")}" placeholder="原价" /><input data-listing-copy-cnsc-price="${escapeHtml(tierId)}" data-code="${escapeHtml(code)}" data-kind="sale" type="number" min="0" step="0.01" value="${escapeHtml(prices.sale_price ?? "")}" placeholder="售价" /></div>`;
    }).join("") : '<span class="muted">先选择目标站点</span>';
    const sizesCell = showSizes ? `<td><div class="cnsc-tier-size-chips" data-listing-copy-cnsc-tier-sizes="${escapeHtml(tierId)}">${listingCopyCnscSizeChips(tierId, tier.sizes || [])}</div></td>` : "";
    const globalCell = showGlobal ? `<td><input data-listing-copy-cnsc-tier-global="${escapeHtml(tierId)}" type="number" min="0" step="0.01" value="${escapeHtml(tier.global_price_cny ?? "")}" /></td>` : "";
    return `<tr><td><strong>${escapeHtml(tierId)}</strong></td>${sizesCell}${globalCell}<td>${matrix}</td><td><button type="button" class="secondary-button" data-listing-copy-cnsc-remove-tier="${escapeHtml(tierId)}">删除</button></td></tr>`;
  }).join("");
  renderListingCopyCnscTierCoverage();
}

// colors 模式（一行一颜色，覆盖该颜色全部尺码）：行随副本颜色变体自动生成。
// copy.price_tiers 保留每颜色一档（colors=[颜色]）作为数据源，站点价与成本价存入档内。
function listingCopyCnscColorUniverse(copy) {
  const colors = [...new Set((copy?.recognized_colors || []).filter((c) => String(c || "").trim()))];
  if (colors.length) return colors;
  // 无识别颜色变体 → 单行「全部 SKU」（旧 uniform 全覆盖形态），用 __all__ 占位 key。
  return ["__all__"];
}

function renderListingCopyCnscColorRows(copy) {
  const table = $("listing-copy-cnsc-tier-table");
  const tbody = table?.querySelector("tbody");
  if (!tbody) return;
  table?.querySelectorAll('[data-cnsc-tier-col="sizes"]').forEach((cell) => cell.toggleAttribute("hidden", true));
  table?.querySelectorAll('[data-cnsc-tier-col="global"]').forEach((cell) => cell.toggleAttribute("hidden", true));
  const coverage = $("listing-copy-cnsc-tier-coverage");
  if (coverage) {
    coverage.hidden = true;
    coverage.textContent = "";
  }
  const codes = [...state.listingCopyTargetConfigs.values()].map((config) => config.site_code).filter(Boolean);
  const tiersList = Array.isArray(copy.price_tiers) ? copy.price_tiers : [];
  // 颜色档按颜色名挂接；颜色大小写漂移（识别重跑改大小写）后仍能对上原档位。
  const tierKey = (value) => String(value || "").trim().toLowerCase();
  const existing = new Map(tiersList.map((tier) => [tierKey(tier.colors?.[0] ?? tier.id ?? ""), tier]));
  // 旧 uniform 全覆盖副本（单个无 colors/无 sizes 的无约束档）：一价覆盖全部 SKU，
  // 迁移到 colors 模式时把该档价格作为每个颜色行的初始值。
  const legacyUniform = tiersList.length === 1
    && !(tiersList[0].colors || []).length
    && !(tiersList[0].sizes || []).length
    ? tiersList[0]
    : null;
  const colors = listingCopyCnscColorUniverse(copy);
  tbody.innerHTML = colors.map((color) => {
    const key = String(color || "");
    const tier = existing.get(tierKey(key)) || (legacyUniform && key !== "__all__" ? legacyUniform : {});
    const site = tier.site_prices || {};
    const tierId = String(tier.id || key || "uniform");
    const label = key === "__all__" ? "全部 SKU" : escapeHtml(key);
    const priceCodes = codes.length ? codes : Object.keys(site);
    const matrix = priceCodes.length ? priceCodes.map((code) => {
      const prices = site[code] || {};
      return `<div class="listing-copy-cnsc-site-price"><span>${escapeHtml(code)}</span><input data-listing-copy-cnsc-color-original="${escapeHtml(key)}" data-code="${escapeHtml(code)}" type="number" min="0" step="0.01" value="${escapeHtml(prices.original_price ?? "")}" placeholder="原价" /><input data-listing-copy-cnsc-color-sale="${escapeHtml(key)}" data-code="${escapeHtml(code)}" type="number" min="0" step="0.01" value="${escapeHtml(prices.sale_price ?? "")}" placeholder="售价" /></div>`;
    }).join("") : '<span class="muted">先选择目标站点</span>';
    return `<tr><td><strong>${label}</strong></td><td colspan="2"></td><td>${matrix}</td><td><button type="button" class="secondary-button mini" data-listing-copy-cnsc-apply-row="${escapeHtml(key)}" title="把本行价格复制到所有行">全覆盖</button></td></tr>`;
  }).join("");
}

function applyListingCopyCnscRowToAll(colorKey) {
  const rows = [...document.querySelectorAll("[data-listing-copy-cnsc-color-original]")];
  const colorKeys = [...new Set(rows.map((input) => input.dataset.listingCopyCnscColorOriginal || ""))];
  if (!colorKeys.length) return;
  const source = String(colorKey || "");
  const template = {
    original: {},
    sale: {},
  };
  if (colorKeys.includes(source)) {
    rows.filter((input) => String(input.dataset.listingCopyCnscColorOriginal || "") === source).forEach((input) => {
      const code = input.dataset.code || "";
      if (!code) return;
      template.original[code] = input.value;
    });
    document.querySelectorAll(`[data-listing-copy-cnsc-color-sale="${CSS.escape(source)}"]`).forEach((input) => {
      const code = input.dataset.code || "";
      if (!code) return;
      template.sale[code] = input.value;
    });
  } else {
    // 无颜色变体行（全部 SKU）：fallback 到第一行作为模板。
    const first = rows[0];
    if (!first) return;
    const firstKey = first.dataset.listingCopyCnscColorOriginal || "";
    rows.filter((input) => String(input.dataset.listingCopyCnscColorOriginal || "") === firstKey).forEach((input) => {
      const code = input.dataset.code || "";
      if (!code) return;
      template.original[code] = input.value;
    });
    document.querySelectorAll(`[data-listing-copy-cnsc-color-sale="${CSS.escape(firstKey)}"]`).forEach((input) => {
      const code = input.dataset.code || "";
      if (!code) return;
      template.sale[code] = input.value;
    });
  }
  const hasTemplate = Object.keys(template.original).length > 0;
  colorKeys.forEach((key) => {
    const originals = document.querySelectorAll(`[data-listing-copy-cnsc-color-original="${CSS.escape(key)}"]`);
    const sales = document.querySelectorAll(`[data-listing-copy-cnsc-color-sale="${CSS.escape(key)}"]`);
    originals.forEach((input) => {
      const code = input.dataset.code || "";
      if (hasTemplate && template.original[code] !== undefined) input.value = template.original[code];
    });
    sales.forEach((input) => {
      const code = input.dataset.code || "";
      if (hasTemplate && template.sale[code] !== undefined) input.value = template.sale[code];
    });
  });
  $("listing-copy-save-result").textContent = "本行价格已应用到全部颜色/款式行，保存副本后生效";
}

// 按尺码分档：尺码取自副本 SKU 明细，每个尺码只能归属一个价格档（点选块互斥分配）。
function listingCopyCnscSizeUniverse() {
  const skus = state.editingListingCopy?.skus || [];
  return [...new Set(skus.map((sku) => String(sku.size || "").trim()).filter(Boolean))];
}

function listingCopyCnscSizeChips(tierId, assigned) {
  const sizes = listingCopyCnscSizeUniverse();
  if (!sizes.length) return '<span class="muted">暂无尺码</span>';
  return sizes.map((size) => `
    <button type="button" class="cnsc-size-chip${assigned.includes(size) ? " cnsc-size-chip--on" : ""}" data-cnsc-tier-size="${escapeHtml(tierId)}" data-size="${escapeHtml(size)}">${escapeHtml(size)}</button>`).join("");
}

function toggleListingCopyCnscTierSize(tierId, size) {
  const chips = [...document.querySelectorAll("[data-cnsc-tier-size]")].filter((chip) => chip.dataset.size === size);
  if (!chips.length) return;
  const wasOn = chips.some((chip) => chip.dataset.cnscTierSize === tierId && chip.classList.contains("cnsc-size-chip--on"));
  chips.forEach((chip) => chip.classList.toggle("cnsc-size-chip--on", !wasOn && chip.dataset.cnscTierSize === tierId));
  renderListingCopyCnscTierCoverage();
}

function renderListingCopyCnscTierCoverage() {
  const node = $("listing-copy-cnsc-tier-coverage");
  if (!node) return;
  const mode = $("listing-copy-cnsc-binding-mode")?.value || "colors";
  const sizes = listingCopyCnscSizeUniverse();
  if (mode !== "sizes" || !sizes.length) {
    node.hidden = true;
    node.textContent = "";
    return;
  }
  const assigned = new Set([...document.querySelectorAll(".cnsc-size-chip--on")].map((chip) => chip.dataset.size || ""));
  const missing = sizes.filter((size) => !assigned.has(size));
  node.hidden = false;
  node.textContent = missing.length
    ? `未分配尺码：${missing.join("、")}（每个尺码只能归属一个价格档，点选下方尺码块分配）`
    : `尺码已全部分配到价格档（${assigned.size}/${sizes.length}）`;
  node.classList.toggle("listing-copy-cnsc-coverage--ok", !missing.length);
}

function listingCopyCnscTierCoverageError() {
  if (($("listing-copy-cnsc-binding-mode")?.value || "colors") !== "sizes") return "";
  if (!isCNSCListingCopy(state.editingListingCopy || {})) return "";
  const missing = listingCopyCnscSizeUniverse().filter((size) => {
    const chip = document.querySelector(`.cnsc-size-chip--on[data-size="${CSS.escape(size)}"]`);
    return !chip;
  });
  return missing.length ? `按尺码分档还有未分配的尺码：${missing.join("、")}。请在价格档表格中点选尺码块完成分配。` : "";
}

function collectListingCopyCnscTiers() {
  const mode = $("listing-copy-cnsc-binding-mode")?.value || "colors";
  if (mode === "colors") {
    // 每行一颜色/款式 → 每颜色一档（colors=[颜色]）；无识别颜色（__all__ 行）→
    // 单个 uniform 无约束档（一价覆盖全部 SKU）。
    const rows = [...document.querySelectorAll("[data-listing-copy-cnsc-color-original]")];
    const keys = [...new Set(rows.map((input) => input.dataset.listingCopyCnscColorOriginal || ""))];
    const tiers = [];
    const collectSite = (key) => {
      const site = {};
      rows.filter((input) => String(input.dataset.listingCopyCnscColorOriginal || "") === key).forEach((input) => {
        const code = input.dataset.code || "";
        if (!code) return;
        if (!site[code]) site[code] = {};
        site[code].original_price = input.value;
      });
      document.querySelectorAll(`[data-listing-copy-cnsc-color-sale="${CSS.escape(key)}"]`).forEach((input) => {
        const code = input.dataset.code || "";
        if (!code) return;
        if (!site[code]) site[code] = {};
        site[code].sale_price = input.value;
      });
      return site;
    };
    const nonAllKeys = keys.filter((key) => key && key !== "__all__");
    for (const key of nonAllKeys) {
      tiers.push({ id: key, sizes: [], colors: [key], global_price_cny: "", site_prices: collectSite(key) });
    }
    if (!tiers.length) {
      const key = keys.includes("__all__") ? "__all__" : "";
      const allSite = keys.length && !key ? collectSite(keys[0]) : collectSite(key);
      tiers.push({ id: "uniform", sizes: [], colors: [], global_price_cny: "", site_prices: allSite });
    }
    return tiers;
  }
  return [...document.querySelectorAll("[data-listing-copy-cnsc-tier-global]")].map((globalInput) => {
    const tierId = globalInput.dataset.listingCopyCnscTierGlobal || "";
    const sizesRoot = document.querySelector(`[data-listing-copy-cnsc-tier-sizes="${CSS.escape(tierId)}"]`);
    const site_prices = {};
    document.querySelectorAll(`[data-listing-copy-cnsc-price="${CSS.escape(tierId)}"]`).forEach((input) => {
      const code = input.dataset.code || "";
      if (!code) return;
      if (!site_prices[code]) site_prices[code] = {};
      site_prices[code][input.dataset.kind === "sale" ? "sale_price" : "original_price"] = input.value;
    });
    return {
      id: tierId,
      sizes: sizesRoot ? [...sizesRoot.querySelectorAll(".cnsc-size-chip--on")].map((chip) => chip.dataset.size || "").filter(Boolean) : [],
      colors: [],
      global_price_cny: globalInput.value,
      site_prices,
    };
  });
}

function setListingCopyCnscBindingMode(mode) {
  const modeSelect = $("listing-copy-cnsc-binding-mode");
  if (modeSelect) modeSelect.value = mode;
  const addTierBtn = $("listing-copy-cnsc-add-tier");
  if (addTierBtn) addTierBtn.hidden = mode !== "sizes";
  state.listingCopyCnscPricePreview = null;
  const preview = $("listing-copy-cnsc-price-preview");
  const copy = state.editingListingCopy || {};
  if (preview) {
    preview.textContent = "绑定模式已切换，价格表行序需与当前价格档一致，请重新解析。";
    preview.hidden = false;
  }
  if (mode === "colors") {
    renderListingCopyCnscColorRows(copy);
  } else {
    // sizes：从现有档位继承（colors 档转成按 id 命名的无尺码占位，用户再点选尺码分配）。
    const tiers = collectListingCopyCnscTiers();
    const next = (tiers.length ? tiers : [emptyListingCopyCnscTier()]).map((tier, index) => ({
      id: tier.id === "uniform" ? `tier_${index + 1}` : tier.id,
      sizes: tier.sizes || [],
      colors: [],
      global_price_cny: tier.global_price_cny ?? "",
      site_prices: tier.site_prices || {},
    }));
    renderListingCopyCnscTierRows(next, "sizes");
  }
}

function addListingCopyCnscTier() {
  const modeSelect = $("listing-copy-cnsc-binding-mode");
  if (modeSelect?.value !== "sizes") return;
  const tiers = collectListingCopyCnscTiers();
  const existing = new Set(tiers.map((tier) => String(tier.id || "")));
  let index = tiers.length + 1;
  while (existing.has(`tier_${index}`)) index += 1;
  renderListingCopyCnscTierRows([...tiers, { id: `tier_${index}`, sizes: [], colors: [], global_price_cny: "", site_prices: {} }], "sizes");
  $("listing-copy-save-result").textContent = "已新增价格档，保存副本后生效";
}

function removeListingCopyCnscTier(tierId) {
  const tiers = collectListingCopyCnscTiers().filter((tier) => String(tier.id || "") !== String(tierId));
  renderListingCopyCnscTierRows(tiers.length ? tiers : [emptyListingCopyCnscTier()], "sizes");
  $("listing-copy-save-result").textContent = "已删除价格档，保存副本后生效";
}

function cnscPriceWarningText(warning) {
  const text = String(warning || "");
  const rows = text.match(/^tier_row_count:(\d+):(\d+)$/);
  if (rows) return `粘贴 ${rows[1]} 行、共 ${rows[2]} 个价格档，最后一行已自动填充剩余档位`;
  const site = text.match(/^unselected_site:(.+)$/);
  if (site) return `站点 ${site[1]} 不在目标站点中，已跳过`;
  return text;
}

function cnscPriceParseSummary(result) {
  const parts = [`已解析 ${(result.tiers || []).length} 个价格档 × ${(result.site_order || []).length} 个站点`];
  const warnings = (result.warnings || []).map(cnscPriceWarningText).filter(Boolean);
  if (warnings.length) parts.push(warnings.join("；"));
  return `${parts.join("；")}。`;
}

async function previewListingCopyCnscPriceMatrix() {
  const text = $("listing-copy-cnsc-price-matrix")?.value || "";
  const mode = $("listing-copy-cnsc-binding-mode")?.value || "colors";
  const tiers = collectListingCopyCnscTiers().map(({ id, sizes, colors }) => ({ id, sizes, colors }));
  const targetSites = orderedCnscSites([...state.listingCopyTargetConfigs.values()].map((config) => config.site_code));
  const result = await api("/api/cnsc-price-matrix/preview", { method: "POST", body: JSON.stringify({ text, tiers, target_sites: targetSites }) });
  const preview = $("listing-copy-cnsc-price-preview");
  if (preview) {
    preview.textContent = cnscPriceParseSummary(result);
    preview.hidden = false;
  }
  if (result.errors?.length) throw new Error(result.errors.join("；"));
  state.listingCopyCnscPricePreview = result;
  const parsedById = new Map((result.tiers || []).map((tier) => [String(tier.id || ""), tier]));
  if (mode === "colors") {
    // colors 模式：解析行按颜色 id 一一对应，回填到对应颜色行的原价/售价输入。
    const rows = collectListingCopyCnscTiers();
    rows.forEach((tier) => {
      const parsed = parsedById.get(String(tier.id));
      if (!parsed) return;
      Object.entries(parsed.site_prices || {}).forEach(([code, entry]) => {
        const original = document.querySelector(`[data-listing-copy-cnsc-color-original="${CSS.escape(String(tier.colors?.[0] ?? tier.id))}"][data-code="${CSS.escape(code)}"]`);
        const sale = document.querySelector(`[data-listing-copy-cnsc-color-sale="${CSS.escape(String(tier.colors?.[0] ?? tier.id))}"][data-code="${CSS.escape(code)}"]`);
        if (original && entry.original_price !== undefined) original.value = entry.original_price;
        if (sale && entry.sale_price !== undefined) sale.value = entry.sale_price;
      });
    });
  } else {
    renderListingCopyCnscTierRows(collectListingCopyCnscTiers().map((tier) => {
      const parsed = parsedById.get(String(tier.id));
      return parsed ? { ...tier, site_prices: parsed.site_prices || tier.site_prices } : tier;
    }), "sizes");
  }
  $("listing-copy-save-result").textContent = "站点价格已解析并回填，保存副本后生效";
  return result;
}

function collectListingCopyCnscConfig(copy) {
  const target_site_stores = [...state.listingCopyTargetConfigs.values()].map((config) => {
    const activity = document.querySelector(`[data-listing-copy-cnsc-activity="${CSS.escape(config.site_code)}"]`);
    const selected = activity?.selectedOptions?.[0];
    const discountInput = document.querySelector(`[data-listing-copy-cnsc-discount="${CSS.escape(config.site_code)}"]`);
    const discountName = selected?.dataset.discountName || config.discount_name || "";
    return {
      site_code: config.site_code,
      store_id: config.store_id,
      store_name: config.store_name,
      mabang_discount_id: activity ? activity.value : config.mabang_discount_id,
      discount_name: discountName,
      discount_label: discountName || config.discount_label || "",
      discount_percent: discountInput ? discountInput.value : config.discount_percent,
      discount_catalog_loaded: config.discount_catalog_loaded === true,
      discount_catalog: config.discount_catalog || [],
    };
  });
  const price_tiers = collectListingCopyCnscTiers();
  const bindingMode = $("listing-copy-cnsc-binding-mode")?.value || "colors";
  const payload = { target_site_stores, price_tiers, cnsc_binding_mode: bindingMode, default_site_discount_percent: $("listing-copy-cnsc-default-discount")?.value ?? "" };
  if (copy.cnsc_store && typeof copy.cnsc_store === "object") payload.cnsc_store = copy.cnsc_store;
  return payload;
}

function isCnscReadinessField(field) {
  const key = String(field || "");
  return key.startsWith("target_site_stores")
    || key.startsWith("site_price_matrix.")
    || key.startsWith("price_tiers")
    || key === "cnsc_store"
    || key === "default_site_discount_percent"
    || key === "warehouse_stock_defaults"
    || key === "cnsc_price_tier_size_coverage";
}

function listingCopyReadinessLabel(field, copy) {
  if (LISTING_COPY_READINESS_LABELS[field]) return LISTING_COPY_READINESS_LABELS[field];
  const key = String(field || "");
  const categoryFieldName = categoryAttributeName(copy, key);
  if (key.startsWith("category_custom_option.")) {
    const fieldId = key.split(".").slice(1).join(".");
    return `类目属性「${categoryFieldName || fieldId}」使用了枚举外的自选值，执行时按马帮「自填选项」写入`;
  }
  if (key.startsWith("category_attributes.")) {
    return `必填类目属性未填写：${String(field).split(".").slice(1).join(".")}${categoryFieldName ? `（${categoryFieldName}）` : ""}`;
  }
  if (isCnscReadinessField(field)) return `待补全：${cnscConfigurationLabel(field)}`;
  return `需要补充：${field}`;
}

// 就绪字段里的类目属性只带 field_id（100037）：从副本快照取中文名（原产地），
// 让操作员一眼知道该改哪一项；取不到时调用方回退成 field_id。
function categoryAttributeName(copy, field) {
  const fieldId = String(field || "").split(".").slice(1).join(".");
  const fields = copy?.category_profile_snapshot?.fields;
  if (!fieldId || !Array.isArray(fields)) return "";
  const match = fields.find((item) => String(item?.field_id ?? "") === fieldId);
  return String(match?.label || "").trim();
}

function listingCopyReadinessSection(field) {
  if (String(field).startsWith("category_attributes.")) return "listing-copy-basic-section";
  if (LISTING_COPY_READINESS_SECTIONS[field]) return LISTING_COPY_READINESS_SECTIONS[field];
  // Per-SKU warehouse stock is edited in the SKU table; discount activity, %OFF,
  // price tiers and the site price matrix live in the dedicated CNSC config section.
  if (String(field) === "warehouse_stock_defaults") return "listing-copy-sku-section";
  if (isCnscReadinessField(field)) return "listing-copy-cnsc-section";
  return "listing-copy-basic-section";
}

// 模板里配了、本副本没值的类目属性字段（与后端 _missing_template_attribute_fields 同口径：
// 只看副本快照里存在的字段，避免拿错类目的属性）。
function listingCopyAttributeBlank(value) {
  if (value === undefined || value === null || value === "") return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value).length === 0;
  return false;
}

function listingCopyTemplateGaps(copy) {
  const template = storeTemplateById(copy?.template_id) || {};
  const templateValues = template.category_attributes || template.attribute_values || {};
  const snapshotFields = ((copy?.category_profile_snapshot || {}).fields || []).map((field) => String(field.field_id || ""));
  const current = copy?.attribute_values || copy?.category_attributes || {};
  return snapshotFields.filter((fieldId) => (
    fieldId
    && !listingCopyAttributeBlank(templateValues[fieldId])
    && listingCopyAttributeBlank(current[fieldId])
  ));
}

// 从模板补全缺失属性（只补缺，不覆盖副本已有值）。
async function fillListingCopyMissingAttributes(copyId = "") {
  const id = copyId || state.editingListingCopyId || "";
  const result = await api(id
    ? `/api/store-listing-copies/${encodeURIComponent(id)}/fill-missing-attributes`
    : "/api/store-listing-copies/fill-missing-attributes", {
    method: "POST",
    body: JSON.stringify(id ? {} : { listing_copy_ids: [...state.selectedListingCopies] }),
  });
  const filled = Number(result.filled_count || 0);
  const scanned = Number(result.scanned_count || 0);
  const fields = [...new Set((result.filled || []).flatMap((item) => item.filled_fields || []))];
  if (id && state.editingListingCopyId === id) {
    const detail = await loadListingCopyDetail(id);
    state.editingListingCopy = detail;
    renderListingCopyEditor(detail);
    setListingCopyEditorStatus(filled ? `已从模板补全 ${fields.length} 项属性值` : "没有需要补全的属性值");
  }
  await Promise.all([loadStoreListingCopies(), loadBaseProducts()]).catch(() => {});
  const summary = filled
    ? `已补全 ${filled} 个副本的缺失属性值${fields.length ? `（共 ${fields.length} 个字段）` : ""}`
    : (scanned ? `检查了 ${scanned} 个副本，没有需要补全的属性值` : "没有需要补全的属性值");
  showOperationFeedback(summary, filled ? "success" : "info");
  return result;
}

function renderListingCopyReadiness(copy) {
  const root = $("listing-copy-readiness-errors");
  const missing = copy.readiness?.missing_fields || copy.readiness_errors || [];
  const warnings = copy.readiness?.warnings || [];
  const templateGaps = listingCopyTemplateGaps(copy);
  const templateGapMarkup = templateGaps.length
    ? `<span class="readiness-warning-inline">模板里配了 ${templateGaps.length} 项属性值（${escapeHtml(templateGaps.map((field) => categoryAttributeName(copy, `category_attributes.${field}`) || field).join("、"))}），本副本没有值 → 刊登上会是空`
    : "";
  const warningMarkup = warnings.length
    ? `<span class="readiness-warning-inline">提醒：${escapeHtml(warnings.map((field) => listingCopyReadinessLabel(field, copy)).join("；"))}</span>`
    : "";
  const modeMarkup = `<span class="readiness-mode-label">当前模式：${escapeHtml(isCNSCListingCopy(copy) ? "跨境 CNSC" : "本土店铺")}</span>`;
  const fillButton = templateGaps.length
    ? ` <button type="button" class="chip" data-fill-missing-attributes="${escapeHtml(copy.id || "")}">从模板补全缺失属性</button>`
    : "";
  if (!missing.length) {
    root.className = `readiness-errors ${(warnings.length || templateGaps.length) ? "ready-with-warnings" : "ready"}`;
    root.innerHTML = `${modeMarkup}${warningMarkup}${templateGapMarkup}${fillButton}${templateGaps.length ? "" : "<span>配置检查通过，可以生成自动化任务</span>"}`;
  } else {
    root.className = "readiness-errors";
    root.innerHTML = `${modeMarkup}${warningMarkup}${templateGapMarkup}${fillButton}${missing.map((field) => `<button type="button" data-listing-copy-focus="${escapeHtml(listingCopyReadinessSection(field))}">${escapeHtml(listingCopyReadinessLabel(field, copy))}</button>`).join("")}`;
  }
  $("listing-copy-create-job").disabled = !!missing.length;
}

function collectListingCopyPayload() {
  const copy = state.editingListingCopy || {};
  const cnsc = isCNSCListingCopy(copy);
  const promotion = $("listing-copy-promotion");
  const selectedPromotion = promotion.options[promotion.selectedIndex];
  const skus = [...document.querySelectorAll("[data-listing-copy-sku-stock]")].map((input) => ({
    id: input.dataset.listingCopySkuStock,
    stock: input.value,
  }));
  const prices = [...document.querySelectorAll("[data-listing-copy-normal-price]")].map((input) => {
    const priceId = input.dataset.listingCopyNormalPrice;
    const sale = document.querySelector(`[data-listing-copy-sale-price="${CSS.escape(priceId)}"]`);
    return { id: priceId, sku_id: input.dataset.skuId || "", normal_price: input.value, sale_price: sale?.value || "" };
  }).filter((item) => item.id);
  const warehouseStocksBySku = new Map();
  document.querySelectorAll("[data-listing-copy-cnsc-warehouse]").forEach((input) => {
    const skuId = input.dataset.skuId || "";
    const code = input.dataset.listingCopyCnscWarehouse || "";
    if (!warehouseStocksBySku.has(skuId)) warehouseStocksBySku.set(skuId, {});
    warehouseStocksBySku.get(skuId)[code] = input.value;
  });
  const cnscSkus = [...warehouseStocksBySku.entries()].map(([id, warehouse_stocks]) => ({ id, warehouse_stocks }));
  const payload = {
    title: $("listing-copy-title").value.trim(),
    description: $("listing-copy-description").value.trim(),
    description_status: $("listing-copy-description").value.trim() ? "ready" : "pending",
    description_profile_version_id: $("listing-copy-description-profile").value,
    product_images: [...state.editingListingCopyImages],
    main_image: state.editingListingCopyImages[0] || "",
    stock: $("listing-copy-stock").value,
    weight_kg: $("listing-copy-weight-kg").value,
    package_length_cm: $("listing-copy-package-length-cm").value,
    package_width_cm: $("listing-copy-package-width-cm").value,
    package_height_cm: $("listing-copy-package-height-cm").value,
    skus: cnsc ? cnscSkus : skus,
    prices: cnsc ? [] : prices,
  };
  if (!cnsc) {
    payload.promotion_activity_id = promotion.value;
    payload.mabang_discount_id = promotion.value;
    payload.discount_label = selectedPromotion?.dataset.promotionLabel || selectedPromotion?.textContent || "";
  } else {
    Object.assign(payload, collectListingCopyCnscConfig(copy));
  }
  if (state.listingCopySizeChartAsset) {
    payload.size_chart_asset_id = state.listingCopySizeChartAsset.id;
    payload.size_chart_preview_url = state.listingCopySizeChartAsset.preview_url;
    payload.size_chart_image = state.listingCopySizeChartAsset.file_name;
    payload.size_chart_image_path = "";
  } else {
    payload.size_chart_asset_id = copy.size_chart_asset_id || "";
    payload.size_chart_preview_url = copy.size_chart_preview_url || "";
    payload.size_chart_image = copy.size_chart_image || "";
    payload.size_chart_image_path = copy.size_chart_image_path || "";
  }
  // 产品图最后一张放尺码表：跨境恒关；本土以副本检查弹窗里的开关为准。
  payload.size_chart_as_last_product_image = cnsc
    ? false
    : ($("listing-copy-size-chart-last-image")?.checked ?? true);
  return payload;
}

async function saveListingCopy() {
  const id = state.editingListingCopyId;
  if (!id) throw new Error("未选择店铺刊登副本");
  $("listing-copy-save").disabled = true;
  setListingCopyEditorStatus("正在保存...");
  try {
    const coverageError = listingCopyCnscTierCoverageError();
    if (coverageError) {
      $("listing-copy-save-result").textContent = coverageError;
      throw new Error(coverageError);
    }
    const detail = await api(`/api/store-listing-copies/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(collectListingCopyPayload()),
    });
    state.editingListingCopy = detail;
    state.editingListingCopyImages = [...(detail.product_images || [])];
    state.listingCopySizeChartAsset = null;
    if (isCNSCListingCopy(detail)) await loadListingCopyCnscConfig(detail);
    renderListingCopyEditor(detail);
    setListingCopyEditorStatus(`已保存，副本版本 ${detail.revision || "-"}`);
    await loadStoreListingCopies();
    return detail;
  } catch (error) {
    setListingCopyEditorStatus(`保存失败：${error?.message || error}`, { error: true });
    throw error;
  } finally {
    $("listing-copy-save").disabled = false;
  }
}

async function retryListingCopyDescription() {
  const copy = state.editingListingCopy;
  const profileId = $("listing-copy-description-profile").value;
  const connections = await api("/api/ai/model-connections");
  const connection = connections.items.find((item) => item.is_default) || connections.items[0];
  if (!profileId || !connection) throw new Error("未配置描述生成方案或 AI 模型连接");
  $("listing-copy-description-retry").disabled = true;
  $("listing-copy-save-result").textContent = "正在生成描述...";
  try {
    const result = await api(`/api/store-listing-copies/${encodeURIComponent(copy.id)}/description/generate`, {
      method: "POST",
      body: JSON.stringify({ profile_id: profileId, connection_id: connection.id }),
    });
    if (result.status !== "ready") throw new Error(result.error || "描述生成失败，请检查描述方案配置后重试");
    const detail = await loadListingCopyDetail(copy.id);
    state.editingListingCopy = detail;
    renderListingCopyEditor(detail);
    $("listing-copy-save-result").textContent = "描述已生成并保存";
    await loadStoreListingCopies();
  } finally {
    $("listing-copy-description-retry").disabled = false;
  }
}

// 生成自动化任务时的可见状态：按钮变成「生成中… 已 Ns」并禁用，避免操作员以为没反应。
// 返回一个收尾函数，调用后恢复按钮文本/可用状态并清掉计时器。
function startJobGenerationFeedback(button, { label = "生成自动化任务" } = {}) {
  const target = button || null;
  const originalText = target ? target.textContent : "";
  const startedAt = Date.now();
  if (target) {
    target.disabled = true;
    target.classList.add("is-loading");
    target.dataset.originalText = originalText;
  }
  const tick = () => {
    if (!target) return;
    const seconds = ((Date.now() - startedAt) / 1000).toFixed(1);
    target.textContent = `${label}… 已 ${seconds}s`;
  };
  tick();
  const timer = window.setInterval(tick, 200);
  return () => {
    window.clearInterval(timer);
    if (target) {
      target.textContent = target.dataset.originalText || originalText;
      target.disabled = false;
      target.classList.remove("is-loading");
      delete target.dataset.originalText;
    }
  };
}

// 手动刷新复制与任务列表（生成过程中也可以点，看已经生成了哪些）。
async function refreshAutomationView() {
  const buttons = [$("refresh-automation-view-btn"), $("refresh-automation-jobs-btn")].filter(Boolean);
  buttons.forEach((button) => { button.disabled = true; });
  try {
    await Promise.all([loadStoreListingCopies(), loadAutomationJobs()]);
    const stamp = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    const line = $("listing-copy-batch-result") || $("automation-job-result");
    if (line) line.textContent = `已刷新（${stamp}）`;
  } catch (error) {
    operationError(error, "刷新失败");
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

async function createAutomationJobForListingCopy() {
  const detail = await saveListingCopy();
  if (!detail.readiness?.ready) throw new Error("副本仍有必填项未完成，暂不能生成自动化任务");
  const stopFeedback = startJobGenerationFeedback($("listing-copy-create-job"), { label: "生成自动化任务" });
  setListingCopyEditorStatus("正在生成自动化任务…（任务列表里可随时点「刷新」查看）");
  let data;
  try {
    data = await api("/api/automation/jobs/from-listing-copies", {
      method: "POST",
      body: JSON.stringify({ listing_copy_ids: [state.editingListingCopyId] }),
    });
  } finally {
    stopFeedback();
  }
  if (!data.created_count) throw new Error(data.errors?.[0]?.error || "任务未生成");
  let message = "自动化任务已生成";
  if (data.worker_start?.status === "waiting_for_login") {
    message += `，任务尚未执行：${data.worker_start.error || "马帮登录未确认"}`;
  } else if (data.worker_start?.ok) {
    message += data.worker_start.browser_mode === "headless"
      ? "，马帮已登录，Worker 已在无头模式开始处理"
      : "，马帮已登录，Worker 已开始处理";
  }
  $("automation-job-result").textContent = message;
  await loadAutomationJobs();
  $("listing-copy-editor-dialog")?.close();
  return data;
}

async function saveStoreTemplateIds(templateId) {
  const result = $("store-template-result");
  const fields = [...document.querySelectorAll(`[data-template-id-field="${CSS.escape(templateId)}"]`)];
  const payload = {};
  for (const field of fields) {
    if ((field instanceof HTMLInputElement || field instanceof HTMLSelectElement) && field.dataset.field) {
      payload[field.dataset.field] = field.value.trim();
    }
  }
  result.textContent = "正在保存店铺模板配置...";
  const template = await api(`/api/store-templates/${encodeURIComponent(templateId)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  result.textContent = `已保存 ${template.store_name || template.template_name || ""} 的店铺模板配置`;
  await Promise.all([loadStoreTemplates(), loadStoreListingCopies(), loadAutomationJobs()]);
}

async function copySelectedBaseProductToTemplates() {
  const result = $("base-product-result");
  const templateIds = [...state.selectedStoreTemplates];
  const copyItems = baseProductsToCopy();
  if (!copyItems.length) {
    result.textContent = "请先勾选或选择一个基础商品。";
    return false;
  }
  if (!templateIds.length) {
    result.textContent = "请至少选择一个店铺模板。";
    return false;
  }
  result.textContent = `正在复制 ${copyItems.length} 个商品到店铺模板...`;
  const body = copyItems.length > 1
    ? { base_product_ids: copyItems.map((item) => item.id), template_ids: templateIds }
    : { base_product_id: copyItems[0].id, template_ids: templateIds };
  const data = await api("/api/store-listing-copies/from-base", {
    method: "POST",
    body: JSON.stringify(body),
  });
  const createdCount = Number(data.created_count || 0);
  const withIssues = Array.isArray(data.created_with_issues) ? data.created_with_issues : [];
  const skippedCount = Number(data.skipped_count || 0);
  const errorCount = Number(data.error_count || 0);
  const parts = [`已创建 ${createdCount} 个店铺刊登副本`];
  if (withIssues.length) parts.push(`其中 ${withIssues.length} 个待补全配置`);
  parts.push(`跳过 ${skippedCount} 个`);
  if (errorCount) parts.push(`失败 ${errorCount} 个`);
  let message = `${parts.join("，")}。`;
  // Category rule: the base product's own category is used when filled; the
  // store template's category is the default when the product has none. Tell the
  // operator which source each copy took so the result is not surprising.
  const createdCopies = Array.isArray(data.created) ? data.created : [];
  if (createdCopies.length) {
    const fromBase = createdCopies.filter((copy) => copy?.category_source === "base_product").length;
    const fromTemplate = createdCopies.length - fromBase;
    const bits = [];
    if (fromBase) bits.push(`${fromBase} 个按商品所选类目`);
    if (fromTemplate) bits.push(`${fromTemplate} 个按店铺模板默认类目`);
    if (bits.length) message += ` 类目来源：${bits.join("，")}。`;
  }
  if (withIssues.length) {
    const detail = withIssues
      .map((item) => (item.missing_configuration || []).map(cnscConfigurationLabel).join("、"))
      .filter(Boolean)
      .join("；");
    if (detail) message += ` 待补全：${detail}（请在「检查店铺刊登副本」里补齐）。`;
  }
  if (errorCount && Array.isArray(data.errors)) {
    const reasons = data.errors.map((item) => item.reason).filter(Boolean).join("、");
    if (reasons) message += ` 失败原因：${reasons}。`;
  }
  result.textContent = message;
  showOperationFeedback(message, errorCount ? "error" : (withIssues.length ? "warning" : "success"));
  await Promise.all([loadBaseProducts(), loadStoreListingCopies()]);
  showView("automation", "copies");
  showAutomationPanel("copies");
  return true;
}

async function createAutomationJobsFromSelection() {
  // Prefer the copies-panel result line since that's the panel the button lives in;
  // fall back to the automation-job panel's result line when called from there.
  const result = $("listing-copy-batch-result") || $("automation-job-result");
  const ids = [...state.selectedListingCopies];
  if (!ids.length) {
    if (result) result.textContent = "请至少勾选一个店铺刊登副本。";
    return;
  }
  const stopFeedback = startJobGenerationFeedback($("batch-create-automation-jobs-btn"), { label: "批量生成" });
  if (result) result.textContent = `正在生成 ${ids.length} 个自动化任务…（可点「刷新」查看已生成的任务）`;
  let data;
  try {
    data = await api("/api/automation/jobs/from-listing-copies", {
      method: "POST",
      body: JSON.stringify({ listing_copy_ids: ids }),
    });
  } finally {
    stopFeedback();
  }
  const created = Number(data.created_count || 0);
  const failed = Number(data.error_count || 0);
  // Decision C: incomplete copies still create a job, marked needs_manual with the
  // outstanding fields listed — surface those so the operator knows why the job
  // won't produce a draft yet (it stays fail-closed downstream).
  const createdJobs = Array.isArray(data.created) ? data.created : [];
  const needsManual = createdJobs.filter((job) => job && job.status === "needs_manual");
  const parts = [`已生成 ${created} 个自动化任务`];
  if (needsManual.length) parts.push(`其中 ${needsManual.length} 个待人工补全`);
  if (failed) parts.push(`${failed} 个失败`);
  let message = `${parts.join("，")}。`;
  if (needsManual.length) {
    const detail = needsManual
      .map((job) => (job.missing_fields || []).map((field) => listingCopyReadinessLabel(field)).join("、"))
      .filter(Boolean)
      .join("；");
    if (detail) message += ` 待补全：${detail}（请在「检查店铺刊登副本」里补齐后重新生成）。`;
  }
  if (data.worker_start?.status === "waiting_for_login") {
    message += ` 任务尚未执行：${data.worker_start.error || "马帮登录未确认"}。`;
  } else if (data.worker_start?.ok) {
    message += data.worker_start.browser_mode === "headless"
      ? " 马帮已登录，Worker 已在无头模式开始处理。"
      : " 马帮已登录，Worker 已开始处理。";
  }
  if (result) result.textContent = message;
  await Promise.all([loadAutomationJobs(), loadStoreListingCopies(), loadBaseProducts()]);
  // 批量生成后清空勾选，避免同一批副本被重复生成。
  state.selectedListingCopies.clear();
  renderStoreListingCopies();
  showOperationFeedback(message, failed ? "error" : (needsManual.length ? "warning" : "success"));
}

async function scanAssets() {
  const result = $("scan-result");
  result.textContent = "正在扫描...";
  const data = await api("/api/assets/scan", {
    method: "POST",
    body: JSON.stringify({
      root_path: $("asset-root").value.trim(),
      category: $("asset-category").value.trim(),
      max_files: Number($("asset-max-files").value || 3000),
      mode: $("asset-scan-mode").value,
    }),
  });
  result.textContent = `扫描完成：${data.assets?.length || 0} 个素材。`;
  await Promise.all([loadAssets(), loadReadiness()]);
}

const VIEW_TITLES = {
  dashboard: "工作台总览",
  "product-maintenance": "商品维护",
  templates: "店铺模板",
  automation: "自动化",
  "file-management": "文件管理",
  plan: "项目计划",
  skills: "Shopee 标题工作台",
  settings: "设置",
};

function showView(id, navKey = "") {
  document.querySelectorAll(".view").forEach((el) => el.classList.remove("active"));
  document.querySelectorAll(".rail-item").forEach((el) => el.classList.toggle("active", navKey ? el.dataset.navKey === navKey : el.dataset.view === id));
  $(id)?.classList.add("active");
  const title = $("view-title");
  if (title && VIEW_TITLES[id]) title.textContent = VIEW_TITLES[id];
  // Errors and success notices belong to whatever view raised them — clear the
  // global feedback banner on navigation so a stale error from another view
  // doesn't follow the user around.
  const feedback = $("global-operation-feedback");
  if (feedback && !feedback.hidden) {
    window.clearTimeout(operationFeedbackTimer);
    feedback.hidden = true;
    feedback.textContent = "";
  }
  // 勾选同样属于它所在的页面：离开「商品维护」就清掉，回来时不会带着上次的
  // 勾选去点「批量删除 / 重新识别」（和上面的提示条同一条规则）。
  if (id !== "product-maintenance") clearBaseProductSelection();
  // Refresh the datasets each view actually depends on so switching in shows
  // the current state (not what was cached from the last bootstrap). API cost
  // is small — endpoints return in ~3ms thanks to the load_store mtime cache.
  refreshDataForView(id);
  // Toggle the automation live-poll on/off with the view — only pay the /jobs
  // + /worker/status cost when the operator is actually looking at that view.
  if (id === "automation") startAutomationLivePoll();
  else stopAutomationLivePoll();
}

// ============================================================
// 版本更新（GitHub Release：检查 / 下载安装 / 发布）
// 接口契约见 .scratch/github-updates/PRD.md「API 契约」。版本判断全部交给
// 后端（/api/updates/status），前端只负责展示、确认和重启等待。
// ============================================================
const UPDATE_HEALTH_POLL_MS = 2000;
// 更新器的动作序列：API 退出 -> 替换文件 -> 重新拉起；给整条链路 120 秒。
const UPDATE_HEALTH_TIMEOUT_MS = 120000;
const RELEASE_JOB_POLL_MS = 2000;
// 2s * 300 = 10 分钟。打包 + 推送 + 上传资产一般 1~3 分钟，留足余量。
const RELEASE_JOB_MAX_POLLS = 300;
const RELEASE_JOB_LOG_TAIL = 60;

let releasePollToken = 0;

function updateDelay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

// 按钮忙碌态：记住原始文案，结束后恢复；按钮是否该禁用由 renderUpdateStatus 决定。
function setUpdateButtonBusy(button, busy, busyLabel = "") {
  if (!(button instanceof HTMLButtonElement)) return;
  if (busy) {
    if (!button.dataset.idleLabel) button.dataset.idleLabel = button.textContent || "";
    button.disabled = true;
    if (busyLabel) button.textContent = busyLabel;
    return;
  }
  if (button.dataset.idleLabel) {
    button.textContent = button.dataset.idleLabel;
    delete button.dataset.idleLabel;
  }
  button.disabled = false;
}

function setUpdatesStateLabel(label, tone = "") {
  const stateEl = $("settings-updates-state");
  if (!stateEl) return;
  stateEl.textContent = label;
  stateEl.className = ("status " + (tone || "")).trim();
}

function updateStatePresentation(status) {
  if (!status) return { label: "读取中", tone: "" };
  if (status.supported === false) return { label: "需要重启 API", tone: "needs_work" };
  if (status.pending && status.pending.version) return { label: "待安装 v" + status.pending.version, tone: "waiting_manual" };
  if (status.restart_required) return { label: "需重启 API", tone: "needs_work" };
  if (status.error) return { label: "检查失败", tone: "needs_work" };
  if (status.update_available && status.latest) return { label: "有新版本 v" + (status.latest.version || ""), tone: "stale" };
  if (!status.configured) return { label: "未配置仓库", tone: "" };
  if (status.checked_at) return { label: "已是最新", tone: "ready" };
  return { label: "已配置", tone: "" };
}

function updateStatusMessage(status) {
  if (!status) return "正在读取更新状态…";
  if (status.error) return "检查失败：" + status.error;
  if (status.pending && status.pending.version) return "v" + status.pending.version + " 已下载并校验完成，重启后安装。";
  const last = status.last_update || null;
  if (last && last.status === "failed") return "上次更新失败：" + (last.error || last.version || "") + "（已尝试回滚，日志在 storage/updates/）";
  if (last && last.status === "success" && last.version) return "上次更新到 v" + last.version + " 已于 " + formatLocalTime(last.applied_at) + " 完成。";
  if (!status.configured) return "还没有配置 GitHub 仓库：填 owner/仓库名（或仓库地址）后点「保存更新配置」。";
  if (status.update_available && status.latest) return "发现新版本 v" + status.latest.version + "（当前 v" + (status.current_version || "-") + "）。";
  if (status.checked_at) return "已是最新版本（检查于 " + formatLocalTime(status.checked_at) + "）。";
  return "点「检查更新」从 GitHub 读取最新 Release。";
}

async function loadUpdateStatus() {
  try {
    const status = await api("/api/updates/status");
    renderUpdateStatus(status);
    return status;
  } catch (error) {
    // 升级后旧 API 进程还没有这套接口：不要让整个设置页报错，明确提示重启。
    if (String(error?.message || error).includes("404")) {
      state.updateStatus = null;
      setUpdatesStateLabel("需要重启 API", "needs_work");
      const resultEl = $("settings-updates-result");
      if (resultEl) resultEl.textContent = "当前 API 进程还没有版本更新接口。重启一次 API 后刷新本页即可使用。";
      ["settings-updates-check-btn", "settings-updates-apply-btn", "settings-release-publish-btn"].forEach((id) => {
        const button = $(id);
        if (button instanceof HTMLButtonElement) button.disabled = true;
      });
      return null;
    }
    throw error;
  }
}

function renderUpdateStatus(status) {
  const current = status && typeof status === "object" ? status : null;
  state.updateStatus = current;

  const currentEl = $("settings-updates-current-version");
  if (currentEl) {
    const version = String(current?.current_version || "").replace(/^v/i, "");
    currentEl.textContent = version ? "v" + version : "-";
  }
  const presentation = updateStatePresentation(current);
  setUpdatesStateLabel(presentation.label, presentation.tone);

  // 输入框只在没有焦点时回填，避免打断操作员正在编辑的仓库地址。
  // 未配置时后端可能只给出占位 slug（"owner/repo" 两边都空就是 "/"），
  // 这种情况按空值显示，免得操作员以为配了个叫 "/" 的仓库。
  const repoInput = $("settings-updates-repo");
  if (repoInput && document.activeElement !== repoInput) {
    repoInput.value = current?.configured ? String(current.repo || "") : "";
  }
  const apiInput = $("settings-updates-api-url");
  if (apiInput && document.activeElement !== apiInput) apiInput.value = String(current?.api_url || "") || "https://api.github.com";
  const tokenInput = $("settings-updates-token");
  if (tokenInput && document.activeElement !== tokenInput) {
    tokenInput.value = "";
    tokenInput.placeholder = current?.token_configured ? "已配置（留空不修改）" : "留空则不修改";
  }
  const prereleaseInput = $("settings-updates-include-prerelease");
  if (prereleaseInput) prereleaseInput.checked = current?.include_prerelease === true;
  const autoCheckInput = $("settings-updates-auto-check");
  if (autoCheckInput) autoCheckInput.checked = current ? current.auto_check !== false : true;

  const configured = current?.configured === true;
  const latest = current?.latest || null;
  const pending = Boolean(current?.pending && current.pending.version);
  const checkButton = $("settings-updates-check-btn");
  if (checkButton instanceof HTMLButtonElement) checkButton.disabled = !configured || pending;
  const applyButton = $("settings-updates-apply-btn");
  if (applyButton instanceof HTMLButtonElement) {
    applyButton.disabled = pending || !(configured && current?.update_available === true && Boolean(latest?.version));
  }
  const publishButton = $("settings-release-publish-btn");
  if (publishButton instanceof HTMLButtonElement) publishButton.disabled = !configured;

  const latestRoot = $("settings-updates-latest");
  if (latestRoot) latestRoot.hidden = !latest;
  if (latest) {
    const versionEl = $("settings-updates-latest-version");
    if (versionEl) versionEl.textContent = "v" + (latest.version || "");
    const metaEl = $("settings-updates-latest-meta");
    if (metaEl) {
      const bits = [];
      // version 本身已经按 "v" + version 显示，tag 相同就不再重复。
      if (latest.tag && String(latest.tag) !== "v" + String(latest.version || "")) bits.push(String(latest.tag));
      if (latest.published_at) bits.push("发布于 " + formatLocalTime(latest.published_at));
      if (latest.prerelease) bits.push("预发布");
      const assets = Array.isArray(latest.assets) ? latest.assets.length : 0;
      if (assets) bits.push(assets + " 个资产");
      metaEl.textContent = bits.join(" · ");
    }
    const linkEl = $("settings-updates-latest-link");
    if (linkEl instanceof HTMLAnchorElement) {
      linkEl.hidden = !latest.html_url;
      if (latest.html_url) linkEl.href = String(latest.html_url);
    }
    const notesEl = $("settings-updates-notes");
    if (notesEl) notesEl.textContent = latest.body ? String(latest.body) : "（这次发布没有填写说明）";
  }

  const resultEl = $("settings-updates-result");
  if (resultEl) resultEl.textContent = updateStatusMessage(current);
  // 顶栏的全局提醒跟设置页共用同一份状态，避免两处判断不一致。
  renderUpdateReminder(current);
}

async function saveUpdateSettings() {
  const button = $("settings-updates-save-btn");
  setUpdateButtonBusy(button, true, "保存中…");
  try {
    const repo = ($("settings-updates-repo")?.value || "").trim();
    const apiUrl = ($("settings-updates-api-url")?.value || "").trim() || "https://api.github.com";
    // token 留空 = 传 ""，后端保留已存的加密值。
    const token = $("settings-updates-token")?.value || "";
    const config = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        updates: {
          repo,
          api_url: apiUrl,
          token,
          include_prerelease: $("settings-updates-include-prerelease")?.checked === true,
          auto_check: $("settings-updates-auto-check")?.checked === true,
        },
      }),
    });
    state.runtimeConfiguration = config;
    const tokenInput = $("settings-updates-token");
    if (tokenInput) tokenInput.value = "";
    // 保存后立刻回读状态：repo 归一化结果和 token_configured 以服务端为准。
    await loadUpdateStatus();
    showOperationFeedback("更新配置已保存", "success");
    return config;
  } finally {
    setUpdateButtonBusy(button, false);
    if (state.updateStatus) renderUpdateStatus(state.updateStatus);
  }
}

async function checkForUpdates() {
  const button = $("settings-updates-check-btn");
  const resultEl = $("settings-updates-result");
  if (resultEl) resultEl.textContent = "正在从 GitHub 读取最新 Release…";
  setUpdateButtonBusy(button, true, "检查中…");
  try {
    // 检查失败时后端仍返回 200 + error 字段（不抛异常），按 error 显示。
    const status = await api("/api/updates/check", { method: "POST", body: "{}" });
    renderUpdateStatus(status);
    if (status?.error) {
      showOperationFeedback("检查更新失败：" + status.error, "error");
    } else if (status?.update_available) {
      showOperationFeedback("发现新版本 v" + (status.latest?.version || ""), "success");
    } else {
      showOperationFeedback("已是最新版本", "success");
    }
    return status;
  } finally {
    setUpdateButtonBusy(button, false);
    if (state.updateStatus) renderUpdateStatus(state.updateStatus);
  }
}

// API 重启期间 /api/health 会先失败、再成功。必须观察到一次失败，否则可能
// 命中还没退出旧的进程、直接 reload 回旧版本页面。
async function waitForUpdateRestart({ timeoutMs = UPDATE_HEALTH_TIMEOUT_MS, intervalMs = UPDATE_HEALTH_POLL_MS } = {}) {
  const deadline = Date.now() + timeoutMs;
  let downObserved = false;
  while (Date.now() < deadline) {
    await updateDelay(intervalMs);
    try {
      await api("/api/health");
      if (downObserved) return true;
      setUpdatesStateLabel("正在重启…", "waiting_manual");
    } catch {
      downObserved = true;
      setUpdatesStateLabel("正在安装更新…", "waiting_manual");
    }
  }
  return false;
}

async function applyUpdate(versionOverride = "", triggerButton = null) {
  const status = state.updateStatus;
  const version = String(versionOverride || status?.latest?.version || "");
  if (!version) {
    showOperationFeedback("没有可更新的版本：先点「检查更新」。", "error");
    return null;
  }
  const confirmed = window.confirm(
    "立即更新到 v" + version + "？\n\n" +
    "系统会下载安装包、按清单校验后备份旧文件并自动重启。\n" +
    "更新不会改动 data/ 与 storage/ 里的业务数据；失败会自动回滚到当前版本。",
  );
  if (!confirmed) return null;
  const resultEl = $("settings-updates-result");
  const applyButton = triggerButton instanceof HTMLButtonElement ? triggerButton : $("settings-updates-apply-btn");
  const checkButton = $("settings-updates-check-btn");
  setUpdateButtonBusy(applyButton, true, "更新中…");
  if (checkButton instanceof HTMLButtonElement) checkButton.disabled = true;
  if (resultEl) resultEl.textContent = "正在下载并暂存 v" + version + "…";
  // 从顶栏提醒点进来时设置页是隐藏的，全局提示条也要说一声。
  showOperationFeedback("正在下载并安装 v" + version + "，完成后页面会自动刷新…", "success");
  try {
    const response = await api("/api/updates/apply", {
      method: "POST",
      body: JSON.stringify({ version, restart: true }),
    });
    setUpdatesStateLabel("正在重启…", "waiting_manual");
    if (resultEl) resultEl.textContent = response?.message || "正在重启并安装更新，页面稍后自动刷新…";
    const restored = await waitForUpdateRestart();
    if (restored) {
      window.location.reload();
      return response;
    }
    setUpdatesStateLabel("等待重启超时", "needs_work");
    if (resultEl) {
      resultEl.textContent = "等待 API 恢复超时（120 秒）。请检查 API 窗口或重启服务后再刷新本页；安装日志在 storage/updates/，必要时可回滚。";
    }
    showOperationFeedback("等待 API 恢复超时（120 秒）", "error");
    return response;
  } finally {
    setUpdateButtonBusy(applyButton, false);
  }
}

// ------------------------------------------------------------
// 侧栏全局更新提醒（「设置」上面，所有视图可见）
// 状态来自 /api/updates/status：API 启动时后台检查过一次并缓存；缓存超过
// 半小时（或从没检查过）时，打开页面静默补查一次，避免错过新 Release。
// ------------------------------------------------------------
// 缓存新鲜度：30 分钟内的检查结果直接复用，不重复打 GitHub。
const UPDATE_REMINDER_STALE_MS = 30 * 60 * 1000;
// 页面长期开着时，每小时回读一次（真正触发 GitHub 检查的前提是缓存过期）。
const UPDATE_REMINDER_POLL_MS = 60 * 60 * 1000;

let updateReminderCheckInFlight = null;

function renderUpdateReminder(status) {
  const button = $("update-reminder-btn");
  if (!(button instanceof HTMLButtonElement)) return;
  const textEl = $("update-reminder-text");
  const current = status && typeof status === "object" ? status : null;
  const latest = current?.latest || null;
  const pendingVersion = current?.pending && current.pending.version ? String(current.pending.version) : "";
  let mode = "";
  let version = "";
  let label = "";
  let title = "";
  if (pendingVersion) {
    // 已下载并校验完成，只差重启：这时「更新」其实是重启安装。
    mode = "restart";
    version = pendingVersion;
    label = "v" + version + " 待重启安装";
    title = "v" + version + " 已下载并校验完成，点击重启安装";
  } else if (current?.update_available && latest?.version) {
    mode = "install";
    version = String(latest.version);
    label = "有新版本 v" + version;
    title = "点击下载并安装 v" + version;
  }
  button.hidden = !mode;
  button.dataset.updateMode = mode;
  button.dataset.updateVersion = version;
  button.title = title;
  if (textEl) textEl.textContent = label || "发现新版本";
}

function updateCheckIsStale(status) {
  const checkedAt = Date.parse(String(status?.checked_at || ""));
  if (!Number.isFinite(checkedAt)) return true;
  return Date.now() - checkedAt > UPDATE_REMINDER_STALE_MS;
}

async function refreshUpdateReminder({ forceCheck = false } = {}) {
  let status = null;
  try {
    status = await loadUpdateStatus();
  } catch {
    // 更新接口不可用（旧 API 进程）或网络抖动：藏起提醒即可，不打扰操作员。
    renderUpdateReminder(null);
    return null;
  }
  renderUpdateReminder(status);
  if (!status?.configured || status.pending) return status;
  if (!forceCheck && !updateCheckIsStale(status)) return status;
  // 并发保护：同一时刻只发一次检查请求（启动 + 定时器可能撞上）。
  if (!updateReminderCheckInFlight) {
    updateReminderCheckInFlight = api("/api/updates/check", { method: "POST", body: "{}" })
      .catch(() => null)
      .then((fresh) => {
        updateReminderCheckInFlight = null;
        if (!fresh || typeof fresh !== "object") return null;
        renderUpdateStatus(fresh);
        renderUpdateReminder(fresh);
        return fresh;
      });
  }
  return updateReminderCheckInFlight;
}

async function handleUpdateReminderClick() {
  const button = $("update-reminder-btn");
  if (!(button instanceof HTMLButtonElement)) return;
  const version = String(button.dataset.updateVersion || "");
  if (!version) {
    // 没有可安装版本时点它 = 手动检查一次。
    const status = await refreshUpdateReminder({ forceCheck: true });
    if (status && !status.error) {
      showOperationFeedback(
        status.update_available ? "发现新版本 v" + (status.latest?.version || "") : "已是最新版本",
        "success",
      );
    }
    return;
  }
  const textEl = $("update-reminder-text");
  const originalLabel = textEl ? textEl.textContent : "";
  button.disabled = true;
  if (textEl) textEl.textContent = "正在更新…";
  try {
    await applyUpdate(version);
  } finally {
    button.disabled = false;
    // 成功后页面会整体刷新；这里只兜住取消确认/失败的情况。
    if (textEl && textEl.textContent === "正在更新…") textEl.textContent = originalLabel;
  }
}

function releaseJobLabel(job) {
  const raw = String(job?.status || "").toLowerCase();
  if (!raw) return { text: "", tone: "" };
  if (raw === "running" || raw === "queued" || raw === "pending") {
    return { text: job?.dry_run ? "正在构建…" : "正在发布…", tone: "waiting_manual" };
  }
  if (raw === "success" || raw === "completed") return { text: job?.dry_run ? "构建完成" : "发布完成", tone: "ready" };
  if (raw === "failed" || raw === "error") return { text: "失败", tone: "needs_work" };
  return { text: raw, tone: "" };
}

function renderReleaseJob(job) {
  const current = job && typeof job === "object" ? job : null;
  state.releaseJob = current;
  const statusEl = $("settings-release-status");
  if (statusEl) {
    const presentation = releaseJobLabel(current);
    statusEl.textContent = presentation.text;
    statusEl.className = ("status " + (presentation.tone || "")).trim();
  }
  const logEl = $("settings-release-log");
  if (logEl) {
    const lines = Array.isArray(current?.log) ? current.log.slice(-RELEASE_JOB_LOG_TAIL) : [];
    logEl.hidden = lines.length === 0;
    logEl.textContent = lines.join("\n");
    logEl.scrollTop = logEl.scrollHeight;
  }
}

async function pollReleaseJob(jobId, { dryRun = false } = {}) {
  releasePollToken += 1;
  const token = releasePollToken;
  let job = state.releaseJob || null;
  for (let attempt = 0; attempt < RELEASE_JOB_MAX_POLLS; attempt += 1) {
    await updateDelay(RELEASE_JOB_POLL_MS);
    if (token !== releasePollToken) return job; // 已有新的发布/构建任务接管轮询
    const suffix = jobId ? "?job_id=" + encodeURIComponent(jobId) : "";
    const payload = await api("/api/releases/publish/status" + suffix);
    job = payload?.job || null;
    renderReleaseJob(job);
    const raw = String(job?.status || "").toLowerCase();
    if (raw === "success" || raw === "completed" || raw === "failed" || raw === "error") break;
  }
  const raw = String(job?.status || "").toLowerCase();
  const finished = raw === "success" || raw === "completed" || raw === "failed" || raw === "error";
  if (!finished) {
    showOperationFeedback("发布任务超过 10 分钟仍未结束，请稍后点「检查更新」确认结果。", "error");
  } else if (raw === "failed" || raw === "error") {
    showOperationFeedback((dryRun ? "构建" : "发布") + "失败：" + (job?.error || "详见发布日志"), "error");
  } else if (dryRun) {
    showOperationFeedback("安装包已构建完成（dry-run，未提交、未推送）", "success");
  } else {
    const url = job?.result?.release_url || "";
    showOperationFeedback("版本 v" + (job?.version || "") + " 已发布" + (url ? "：" + url : ""), "success");
    // 发布成功后本地 VERSION 已经变了，回读一次让「当前版本」立刻对齐。
    loadUpdateStatus().catch(() => {});
  }
  return job;
}

async function startReleaseJob(body, { label = "发布" } = {}) {
  const publishButton = $("settings-release-publish-btn");
  const buildButton = $("settings-release-build-btn");
  const dryRun = body?.dry_run === true;
  renderReleaseJob({ status: "running", dry_run: dryRun, version: body?.version || "按 " + label, log: ["正在提交任务…"] });
  setUpdateButtonBusy(publishButton, true);
  setUpdateButtonBusy(buildButton, true);
  try {
    const response = await api("/api/releases/publish", {
      method: "POST",
      body: JSON.stringify(body),
    });
    const jobId = String(response?.job_id || "");
    renderReleaseJob({ id: jobId, status: "running", dry_run: dryRun, version: body?.version || "", log: ["任务已提交，等待日志…"] });
    return await pollReleaseJob(jobId, { dryRun });
  } finally {
    setUpdateButtonBusy(publishButton, false);
    setUpdateButtonBusy(buildButton, false);
    if (state.updateStatus) renderUpdateStatus(state.updateStatus);
  }
}

async function publishRelease() {
  const version = ($("settings-release-version")?.value || "").trim().replace(/^v/i, "");
  const bump = $("settings-release-bump")?.value || "patch";
  const notes = $("settings-release-notes")?.value || "";
  const prerelease = $("settings-release-prerelease")?.checked === true;
  const dryRun = $("settings-release-dry-run")?.checked === true;
  const target = version || "按 " + bump + " 递增";
  const confirmed = window.confirm(
    "发布版本（" + target + "）？\n\n" +
    "会提交 VERSION、打 tag 并推送到 GitHub，然后创建 Release 并上传安装包与 sha256。\n" +
    "预发布：" + (prerelease ? "是" : "否") + (dryRun ? "\n已勾选「只构建安装包」：只打包，不提交、不推送。" : ""),
  );
  if (!confirmed) return null;
  return startReleaseJob({
    version: version || null,
    bump,
    notes,
    prerelease,
    push: true,
    dry_run: dryRun,
  });
}

async function buildRelease() {
  // 次要按钮 = dry-run：只构建安装包，先验证打包结果再正式发布。
  const version = ($("settings-release-version")?.value || "").trim().replace(/^v/i, "");
  const bump = $("settings-release-bump")?.value || "patch";
  const notes = $("settings-release-notes")?.value || "";
  return startReleaseJob({
    version: version || null,
    bump,
    notes,
    prerelease: $("settings-release-prerelease")?.checked === true,
    push: false,
    dry_run: true,
  });
}

// bump 下拉变化时自动填新版本号，规则与后端 bump_version 一致：
// patch 只加末位；minor 加中间位并清零末位；major 加首位并清零后两位。
function suggestNextVersion(currentVersion = "", part = "") {
  const input = $("settings-release-version");
  if (!input) return "";
  const current = String(currentVersion || $("settings-updates-current-version")?.textContent || "").trim().replace(/^v/i, "");
  const kind = String(part || $("settings-release-bump")?.value || "patch").toLowerCase();
  const match = /^(\d+)\.(\d+)\.(\d+)/.exec(current);
  if (!match) {
    input.value = "";
    input.placeholder = "形如 1.2.0";
    return "";
  }
  let major = Number(match[1]);
  let minor = Number(match[2]);
  let patch = Number(match[3]);
  if (kind === "major") {
    major += 1;
    minor = 0;
    patch = 0;
  } else if (kind === "minor") {
    minor += 1;
    patch = 0;
  } else {
    patch += 1;
  }
  const next = major + "." + minor + "." + patch;
  input.value = next;
  input.placeholder = "1.2.0";
  return next;
}

// Loader mapping per view. Refreshed on every switch-in so datasets stay
// current after a user comes back from another view (or another operator's
// change). Runs fire-and-forget in parallel — failures show through each
// loader's own error handler; we don't wait or block navigation.
const VIEW_LOADERS = {
  "dashboard": () => [loadDashboard(), loadReadiness()],
  "product-maintenance": () => [loadBaseProducts(), loadAssets(), loadReadiness()],
  "templates": () => [loadStoreTemplates(), loadMabangStoreOptions(), loadCategoryProfiles()],
  "automation": () => [loadAutomationJobs(), loadStoreListingCopies()],
  "file-management": () => [loadAssets()],
  "plan": () => [loadProjectPlan()],
  "skills": () => [window.TitleWorkbench?.load()],
  // 版本更新状态只在设置页用到；基础 loader 的数组字面量被
  // tests/test_lan_access_ui.py 原样断言，所以保留后再 concat 追加。
  "settings": () => [loadRuntimeSettings(), loadNetworkSettings()].concat(loadUpdateStatus()),
};

function refreshDataForView(id) {
  const build = VIEW_LOADERS[id];
  if (!build) return;
  const tasks = build();
  Promise.allSettled(Array.isArray(tasks) ? tasks : []).then((results) => {
    const failed = results.filter((r) => r.status === "rejected");
    if (failed.length) {
      console.warn(`[view ${id}] ${failed.length} loader(s) failed`, failed.map((r) => r.reason?.message));
    }
  });
}


// --- Automation live-polling (3s while on the automation view) ---
const AUTOMATION_POLL_INTERVAL_MS = 3000;
let automationLivePollTimer = 0;

async function pollAutomationLive() {
  try {
    const [jobs, worker] = await Promise.all([
      api("/api/automation/jobs"),
      api("/api/worker/status"),
    ]);
    state.automationJobs = jobs.items || [];
    state.workerStatus = worker || null;
    renderAutomationJobs();
    renderStoreListingCopies();
    refreshOpenAutomationJobDetails();
    // Reuse the settings-view worker chip renderer if we have one.
    if (typeof renderWorkerStatus === "function") renderWorkerStatus();
  } catch {
    // Silent — next tick will retry. Avoid flooding the feedback banner.
  }
}

function startAutomationLivePoll() {
  if (automationLivePollTimer) return;
  pollAutomationLive();  // immediate refresh so the switch-in feels snappy
  automationLivePollTimer = window.setInterval(pollAutomationLive, AUTOMATION_POLL_INTERVAL_MS);
}

function stopAutomationLivePoll() {
  if (!automationLivePollTimer) return;
  window.clearInterval(automationLivePollTimer);
  automationLivePollTimer = 0;
}

function showMaintenanceTab(id) {
  document.querySelectorAll(".maintenance-tab").forEach((el) => el.classList.remove("active"));
  document.querySelectorAll(".subnav-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.maintenanceTab === id);
  });
  $(`${id}-tab`)?.classList.add("active");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// 自动化/副本表把后端 ISO 时间戳显示成本地可读时间（yyyy-MM-dd HH:mm），
// 原始完整时间靠调用方放进 title 提示。空值/非法时间原样返回。
function formatLocalTime(iso) {
  if (!iso) return "-";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// 执行器/后端的稳定英文错误码 → 中文可读说明。未收录的码保持原文，
// 鼠标悬停仍能看到原始错误码（title 由调用方拼）。
const AUTOMATION_ERROR_LABELS = {
  browser_context_missing: "自动化浏览器没有可用上下文（通常是浏览器刚被重启）——重试即可",
  needs_manual_login_or_verification: "马帮需要重新登录或过验证码，请在自动化浏览器里登录后重试",
  save_result_ambiguous: "保存结果不明确",
  save_result_not_proven: "无法确认草稿已保存",
  // 马帮保存时的描述图宽度校验：提示只弹在页面顶部，不落草稿。执行器已能认出
  // 这条提示（BlockedError code），这里给出可直接照做的处理办法。
  description_image_width_too_small: "商品描述里的图片宽度小于 700，马帮拒绝保存：请在「商品描述」上点「编辑图片」，全选图片把宽度改成 1000 后点「更改」，再点「再次尝试」",
  description_image_editor_not_found: "描述图片宽度不足 700，但没找到「编辑图片」入口/弹窗，未能自动修复",
  description_image_width_fix_failed: "描述图片自动修复没成功（宽度仍小于 700）：请手动点「编辑图片」→ 全选 → 尺寸改 1000 后点「更改」，再点「再次尝试」",
  saved_success_dialog_not_unique: "保存成功弹窗不唯一",
  pre_save_validation_failed: "保存前校验未通过（价格/必填缺失）",
  field_not_editable: "字段不可编辑（类目属性多半是存成了文本、实际是下拉）",
  field_value_not_verified: "字段值没有写进马帮",
  field_not_unique: "页面上该字段不唯一",
  select_field_not_found: "页面上找不到该属性",
  select_option_not_found: "下拉里没有这个选项",
  select_value_not_verified: "下拉选项没选中",
  cnsc_payload_invalid: "CNSC 刊登内容校验未通过",
  cnsc_draft_inbox_parent_sku_not_unique: "草稿箱父 SKU 不唯一",
  watermark_unconfigured: "水印未配置",
  watermark_trigger_not_unique: "页面上「添加水印」入口不唯一（页面结构变了？）",
  watermark_menu_not_visible: "点开「添加水印」后水印菜单没出来",
  watermark_template_ambiguous: "菜单里有多个同名同 ID 的水印，不敢乱点",
  watermark_template_not_found: "菜单里有这个 ID，但名称对不上（可能马帮那边改过名）",
  watermark_template_unavailable: "菜单里没有配置的这个水印模板（可能已被删除或换店）",
  watermark_selection_not_verified: "点了水印模板但页面没确认选中",
  watermark_position_out_of_role: "水印范围越界：主图水印只能第 1 张，产品图水印从第 2 张起",
  video_store_not_found: "未找到店铺视频",
  video_missing: "缺少商品视频",
  parent_sku_not_found: "未找到父 SKU",
  save_click_failed: "点击保存失败",
  publish_clicked_unexpectedly: "意外触发了发布",
  timeout: "执行超时",
  page_navigation_failed: "页面跳转失败",
  login_required: "需要重新登录马帮",
  captcha_detected: "遇到验证码",
  // 尺码图表的「选择模板」：模板 ID 来自店铺模板里锁定的马帮模板，页面上选不中
  // 就是该店铺的模板列表里已经没有它（换店/模板被删）——要么重新采集该店铺的
  // 尺码图模板再改店铺模板，要么把模板改成上传尺码图。
  size_chart_template_unresolved: "尺码图模板没选中（该店铺模板列表里可能已没有它，请重新采集尺码图模板或改用上传图片）",
  size_chart_template_mode_not_found: "尺码图表页面上没有「选择模板」选项",
  // 描述末尾插图（尺码表 + 产品图）：失败时执行器会把这批插进去的图撤掉，
  // 描述不会被插到中间切成两半，运营按提示人工补图即可。
  description_media_failed: "描述插图失败，请检查描述末尾并人工补图",
  description_media_not_verified: "描述插图没插全，已尝试撤回本次插入，请检查描述末尾",
  description_media_position_not_at_tail: "描述插图没落在末尾，已尝试撤回本次插入，请检查描述末尾",
  description_media_order_unexpected: "描述插图顺序不对，已尝试撤回本次插入，请检查描述末尾",
  description_size_chart_not_inserted: "尺码图没插进描述，已尝试撤回本次插入",
  description_product_images_not_inserted: "产品图没插进描述，已尝试撤回本次插入",
  description_caret_not_at_end: "描述正文没滚到底，本次未插图（描述未改动）",
  description_media_source_missing: "没取到可插的图（尺码表图与产品图都为空）",
  description_size_chart_source_missing: "没取到尺码表图，只插了产品图",
  description_size_chart_upload_failed: "本地尺码图没传进描述，只插了产品图（请检查店铺模板里上传的尺码图）",
  description_product_images_empty: "「引用产品图」弹窗里没有产品图",
  description_product_images_not_listed: "「引用产品图」弹窗里找不到要插的图",
  description_editor_not_found: "找不到描述编辑器，已跳过插图",
  description_editor_id_missing: "描述编辑器缺少 id，已跳过插图",
  product_image_upload_did_not_land: "产品图没有出现在马帮页面上（上传被拒或页面结构变了）",
  product_image_slots_already_filled: "表单里已经有产品图（上次运行残留的详情页），已自动重开新表单重试",
};

function automationErrorLabel(code) {
  const key = String(code || "").trim();
  if (!key) return "";
  return AUTOMATION_ERROR_LABELS[key] || key;
}

function showAutomationPanel(id = "copies") {
  document.querySelectorAll("[data-automation-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.automationPanel !== id;
  });
  const title = $("view-title");
  if (title) title.textContent = id === "copies" ? "店铺刊登副本" : "自动化";
}

let operationFeedbackTimer = 0;

function showOperationFeedback(message, tone = "info") {
  window.clearTimeout(operationFeedbackTimer);
  // 基础商品维护弹窗打开时，把提示显示在弹窗内部（全局 toast 会被 backdrop
  // 模糊盖住、看不清）。
  const modal = $("base-maintenance-modal");
  const modalFeedback = $("base-maintenance-feedback");
  if (modal && !modal.hidden && modalFeedback) {
    modalFeedback.hidden = false;
    modalFeedback.className = `operation-feedback ${tone}`;
    modalFeedback.textContent = String(message || "");
    operationFeedbackTimer = window.setTimeout(() => { modalFeedback.hidden = true; }, tone === "success" ? 5000 : 9000);
    return;
  }
  const feedback = $("global-operation-feedback");
  if (!feedback) return;
  feedback.hidden = false;
  feedback.className = `operation-feedback ${tone}`;
  feedback.textContent = String(message || "");
  if (tone === "success") operationFeedbackTimer = window.setTimeout(() => { feedback.hidden = true; }, 5000);
}

function operationError(error, fallback = "操作失败") {
  const message = error?.message || fallback;
  showOperationFeedback(message, "error");
  // 副本编辑器开着的时候，错误必须同时写进副本里：操作员正盯着这个弹窗，
  // 页面顶部的提示容易被弹窗挡掉（现场反馈 2026-09-28「出现错误没有在副本中显示出来」）。
  if ($("listing-copy-editor-dialog")?.open) setListingCopyEditorStatus(`错误：${message}`, { error: true });
  return message;
}

function openImageLightbox(src, alt = "") {
  if (!src) return;
  const overlay = $("image-lightbox");
  const img = $("image-lightbox-img");
  if (!overlay || !img) return;
  img.src = src;
  img.alt = alt || "";
  overlay.classList.add("open");
}

function closeImageLightbox() {
  const overlay = $("image-lightbox");
  const img = $("image-lightbox-img");
  if (!overlay) return;
  overlay.classList.remove("open");
  if (img) { img.src = ""; img.alt = ""; }
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && $("image-lightbox")?.classList.contains("open")) closeImageLightbox();
  // 副本列表的分区/时间分桶标题行用 role=button：Enter / 空格 也要能折叠。
  if (event.key === "Enter" || event.key === " ") {
    const toggle = event.target instanceof Element
      ? event.target.closest("[data-copy-section-collapse], [data-copy-collapse]")
      : null;
    if (toggle instanceof HTMLElement) {
      event.preventDefault();
      const key = toggle.dataset.copySectionCollapse || toggle.dataset.copyCollapse || "";
      if (key) {
        setCopyCollapsed(key, !copySectionCollapsed(key));
        if (String(key).startsWith("jobs:")) renderAutomationJobs();
        else renderStoreListingCopies();
      }
    }
  }
});

document.addEventListener("click", async (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;

  // 点击 summary 内的下拉框/按钮时不触发 details 折叠切换
  if (target.closest(".profile-sections summary select, .profile-sections summary button")) {
    event.preventDefault();
  }

  const lightbox = $("image-lightbox");
  if (lightbox && lightbox.classList.contains("open")) {
    // Clicking anywhere on the overlay closes it. Clicking the image itself does nothing
    // (the img has cursor:default and pointer-events pass to it, but we still want it kept open
    // when the user clicks the image to inspect detail).
    if (target === lightbox) { closeImageLightbox(); return; }
    if (target.id === "image-lightbox-img") return;
  }

  const maintenanceModal = $("base-maintenance-modal");
  if (maintenanceModal && !maintenanceModal.hidden && !target.closest("button, a, input, select, textarea, label")) {
    // Zoom the summary thumb (background-image) or any inline <img> inside the modal.
    const thumb = target.closest("#base-maintenance-summary-thumb");
    if (thumb instanceof HTMLElement) {
      const url = /url\(["']?(.*?)["']?\)/.exec(thumb.style.backgroundImage || "");
      if (url && url[1]) { openImageLightbox(url[1], "商品主图"); return; }
    }
    if (target instanceof HTMLImageElement && maintenanceModal.contains(target) && target.src) {
      openImageLightbox(target.src, target.alt || "");
      return;
    }
  }

  const mabangNewProductLink = target.closest("#open-mabang-new-product-link");
  if (mabangNewProductLink instanceof HTMLAnchorElement) {
    event.preventDefault();
    await openMabangNewProductPage();
    return;
  }
  const filePicker = target.closest("[data-open-file-picker]");
  if (filePicker instanceof HTMLElement) {
    openListingCopyFilePicker(filePicker.dataset.openFilePicker || "");
    return;
  }
  const nav = target.closest(".rail-item, .pipeline-stage");
  if (nav instanceof HTMLElement && nav.dataset.view) showView(nav.dataset.view, nav.dataset.navKey || "");
  if (nav instanceof HTMLElement && nav.dataset.maintenanceTab) showMaintenanceTab(nav.dataset.maintenanceTab);
  if (nav instanceof HTMLElement && nav.dataset.automationTab) {
    showAutomationPanel(nav.dataset.automationTab);
  }
  const settingsNav = target.closest("[data-settings-group]");
  if (settingsNav instanceof HTMLElement) {
    document.querySelectorAll("[data-settings-group]").forEach((item) => item.classList.toggle("active", item === settingsNav));
    document.querySelectorAll("[data-settings-panel]").forEach((item) => item.classList.toggle("active", item.dataset.settingsPanel === settingsNav.dataset.settingsGroup));
    if (settingsNav.dataset.settingsGroup === "storage-cleanup") await loadStorageUsage().catch((error) => operationError(error, "存储占用读取失败"));
    if (settingsNav.dataset.settingsGroup === "network") await loadNetworkSettings().catch((error) => operationError(error, "局域网状态读取失败"));
    if (settingsNav.dataset.settingsGroup === "updates") await loadUpdateStatus().catch((error) => operationError(error, "更新状态读取失败"));
  }
  const tab = target.closest("[data-maintenance-tab]");
  if (tab instanceof HTMLElement && tab.dataset.maintenanceTab) showMaintenanceTab(tab.dataset.maintenanceTab);
  const copySectionToggle = target.closest("[data-copy-section-collapse], [data-copy-collapse]");
  if (copySectionToggle instanceof HTMLElement) {
    const key = copySectionToggle.dataset.copySectionCollapse || copySectionToggle.dataset.copyCollapse || "";
    if (key) {
      setCopyCollapsed(key, !copySectionCollapsed(key));
      // jobs: 前缀属于自动化任务列表，其余属于店铺刊登副本列表。
      if (String(key).startsWith("jobs:")) renderAutomationJobs();
      else renderStoreListingCopies();
    }
    return;
  }
  const baseTimeChip = target.closest("[data-base-time-filter]");
  if (baseTimeChip instanceof HTMLElement) {
    setBaseTimeFilter(baseTimeChip.dataset.baseTimeFilter || "today");
    return;
  }
  const baseCategoryChip = target.closest("[data-base-category-filter]");
  if (baseCategoryChip instanceof HTMLElement) {
    toggleBaseCategoryFilter(baseCategoryChip.dataset.baseCategoryFilter || "");
    return;
  }
  const baseBatchAction = target.closest("[data-base-batch-action]");
  if (baseBatchAction instanceof HTMLElement && baseBatchAction.dataset.baseBatchAction) {
    handleBaseProductBatchAction(baseBatchAction.dataset.baseBatchAction);
    return;
  }
  if (target.id === "update-reminder-btn") await handleUpdateReminderClick().catch((error) => operationError(error, "更新失败"));
  if (target.id === "refresh-btn") {
    target.disabled = true;
    await refreshAll().then((result) => {
      if (result.ok) showOperationFeedback("页面数据已刷新", "success");
    }).finally(() => { target.disabled = false; });
  }
  if (target.dataset.dashboardAction) {
    if (target.dataset.dashboardAction === "create_base_product") showView("product-maintenance");
    if (target.dataset.dashboardAction === "copy_base_product") showView("product-maintenance");
    if (target.dataset.dashboardAction === "open_listing_copy") { showView("automation", "copies"); showAutomationPanel("copies"); }
    if (target.dataset.dashboardAction === "open_automation" || target.dataset.dashboardAction === "open_automation_job") { showView("automation", "jobs"); showAutomationPanel("jobs"); }
  }
  if (target.id === "save-settings-btn") await saveRuntimeSettings().catch((error) => operationError(error, "设置保存失败"));
  if (target.id === "settings-network-apply-btn") await applyNetworkSettings().catch((error) => operationError(error, "网络设置应用失败"));
  if (target.id === "settings-network-refresh-btn") {
    const button = target;
    const originalLabel = button.textContent;
    button.disabled = true;
    button.textContent = "刷新中…";
    await loadNetworkSettings({ refresh: true })
      .catch((error) => operationError(error, "局域网地址刷新失败"))
      .finally(() => {
        button.textContent = originalLabel;
        button.disabled = state.networkStatus == null;
      });
  }
  if (target.id === "settings-network-firewall-btn") await openNetworkFirewall().catch((error) => operationError(error, "防火墙规则添加失败"));
  const copyLanButton = target.closest("[data-copy-lan-url]");
  if (copyLanButton instanceof HTMLElement) {
    await copyLanAddress(copyLanButton.dataset.copyLanUrl || "", copyLanButton);
    return;
  }
  if (target.id === "settings-storage-refresh-btn") await loadStorageUsage().catch((error) => operationError(error, "存储占用读取失败"));
  if (target.id === "settings-storage-clean-btn") await cleanupSelectedStorage().catch((error) => operationError(error, "存储清理失败"));
  if (target.id === "settings-storage-compact-btn") await compactStorageAssets().catch((error) => operationError(error, "素材压缩失败"));
  if (target.id === "settings-updates-save-btn") await saveUpdateSettings().catch((error) => operationError(error, "更新配置保存失败"));
  if (target.id === "settings-updates-check-btn") await checkForUpdates().catch((error) => operationError(error, "检查更新失败"));
  if (target.id === "settings-updates-apply-btn") await applyUpdate().catch((error) => operationError(error, "更新失败"));
  if (target.id === "settings-release-publish-btn") await publishRelease().catch((error) => operationError(error, "发布版本失败"));
  if (target.id === "settings-release-build-btn") await buildRelease().catch((error) => operationError(error, "构建安装包失败"));
  if (target.id === "test-nas-settings-btn") await testRuntimeConnection("nas").catch((error) => operationError(error, "NAS 检测失败"));
  if (target.id === "test-mabang-settings-btn") await testRuntimeConnection("mabang").catch((error) => operationError(error, "马帮配置检测失败"));
  if (target.id === "worker-start-btn") await controlWorker("start").catch((error) => operationError(error, "Worker 启动失败"));
  if (target.id === "worker-pause-btn") await controlWorker("pause").catch((error) => operationError(error, "Worker 暂停失败"));
  if (target.id === "worker-restart-btn") await controlWorker("restart").catch((error) => operationError(error, "Worker 重启失败"));
  if (target.id === "worker-logs-btn") await refreshWorkerLogs().catch((error) => operationError(error, "Worker 日志读取失败"));
  const workerLogsAccount = target.closest("[data-worker-logs-account]");
  if (workerLogsAccount instanceof HTMLElement) await refreshWorkerLogs(workerLogsAccount.dataset.workerLogsAccount || "", Number(workerLogsAccount.dataset.workerLogsSlot || 0)).catch((error) => operationError(error, "Worker 日志读取失败"));
  if (target.id === "worker-browser-btn") await launchAutomationBrowser().catch((error) => { $("settings-worker-browser-status").textContent = `无法启动自动化浏览器：${error.message || "请检查 Chrome 与端口设置"}`; operationError(error, "启动自动化浏览器失败"); });
  if (target.id === "add-ai-connection-btn") openAiConnectionDialog();
  if (target.id === "save-ai-connection-btn") await saveAiConnection().catch((error) => operationError(error, "AI 连接保存失败"));
  if (target.id === "add-mabang-account-btn") openMabangAccountDialog();
  if (target.id === "save-mabang-account-btn") await saveMabangAccount().catch((error) => operationError(error, "马帮账号保存失败"));
  const mabangAccountEdit = target.closest("[data-edit-mabang-account]");
  if (mabangAccountEdit instanceof HTMLElement) openMabangAccountEditor(mabangAccountEdit.dataset.editMabangAccount || "");
  const mabangAccountDelete = target.closest("[data-delete-mabang-account]");
  if (mabangAccountDelete instanceof HTMLElement) {
    const accountId = mabangAccountDelete.dataset.deleteMabangAccount || "";
    const account = (state.mabangAccounts || []).find((item) => item.id === accountId);
    if (!window.confirm(`确认删除马帮账号「${account?.name || accountId}」？名下仍有店铺时不允许删除。`)) return;
    await api(`/api/mabang/accounts/${encodeURIComponent(accountId)}`, { method: "DELETE" })
      .then(() => loadMabangAccounts())
      .catch((error) => operationError(error, "马帮账号删除失败"));
  }
  if (target.id === "add-description-profile-btn") openDescriptionProfileDialog();
  if (target.id === "save-description-profile-btn") await saveDescriptionProfile().catch((error) => operationError(error, "描述方案保存失败"));
  const sectionRemove = target.closest("[data-section-remove]");
  if (sectionRemove) {
    const index = Number(sectionRemove.dataset.sectionRemove);
    const sections = collectProfileSectionsFromEditor() || [...(state.editingProfileSections || [])];
    sections.splice(index, 1);
    renderProfileSectionsEditor(sections);
    renderProfilePreview();
  }
  const sectionMove = target.closest("[data-section-move-up], [data-section-move-down]");
  if (sectionMove) {
    moveSection(Number(sectionMove.dataset.sectionMoveUp || sectionMove.dataset.sectionMoveDown), sectionMove.dataset.sectionMoveUp !== undefined ? -1 : 1);
    renderProfilePreview();
  }
  const addSection = target.closest("[data-add-section]");
  if (addSection) {
    addProfileSection(addSection.dataset.addSection || "");
    renderProfilePreview();
  }
  const addSectionSelect = target.closest("[data-add-section-type]");
  if (addSectionSelect) {
    if (addSectionSelect.value) {
      addProfileSection(addSectionSelect.value);
      renderProfilePreview();
    }
  }
  const rowAdd = target.closest("[data-row-add]");
  if (rowAdd) {
    addFieldRow(Number(rowAdd.dataset.rowAdd));
    renderProfilePreview();
  }
  const rowRemove = target.closest("[data-row-remove]");
  if (rowRemove) {
    const [sectionIndex, rowIndex] = String(rowRemove.dataset.rowRemove || "").split("_").map(Number);
    removeFieldRow(sectionIndex, rowIndex);
    renderProfilePreview();
  }
  const aiTest = target.closest("[data-test-ai-connection]");
  if (aiTest instanceof HTMLElement) {
    const result = await api(`/api/ai/model-connections/${encodeURIComponent(aiTest.dataset.testAiConnection || "")}/test`, { method: "POST", body: "{}" }).catch((error) => ({ ok: false, message: operationError(error, "AI 连接检测失败") }));
    if (result?.message) showOperationFeedback(result.message, result.ok ? "success" : "error");
  }
  const aiBalance = target.closest("[data-refresh-ai-balance]");
  if (aiBalance instanceof HTMLElement) {
    await refreshAiConnectionBalance(aiBalance.dataset.refreshAiBalance || "").catch((error) => operationError(error, "余额获取失败"));
  }
  const aiEdit = target.closest("[data-edit-ai-connection]");
  if (aiEdit instanceof HTMLElement) openAiConnectionEditor(aiEdit.dataset.editAiConnection || "");
  const aiDefault = target.closest("[data-default-ai-connection]");
  if (aiDefault instanceof HTMLElement) await updateAiConnection("default", aiDefault.dataset.defaultAiConnection || "").catch((error) => operationError(error, "设置默认 AI 连接失败"));
  const aiDelete = target.closest("[data-delete-ai-connection]");
  if (aiDelete instanceof HTMLElement) await updateAiConnection("delete", aiDelete.dataset.deleteAiConnection || "").catch((error) => operationError(error, "删除 AI 连接失败"));
  const descriptionDefault = target.closest("[data-default-description-profile]");
  if (descriptionDefault instanceof HTMLElement) await setDefaultDescriptionProfile(descriptionDefault.dataset.defaultDescriptionProfile || "").catch((error) => operationError(error, "设置默认描述方案失败"));
  const descriptionEdit = target.closest("[data-edit-description-profile]");
  if (descriptionEdit instanceof HTMLElement) openDescriptionProfileEditor(descriptionEdit.dataset.editDescriptionProfile || "");
  const descriptionDelete = target.closest("[data-delete-description-profile]");
  if (descriptionDelete instanceof HTMLElement) {
    const profileId = descriptionDelete.dataset.deleteDescriptionProfile || "";
    const profile = state.descriptionProfiles.find((item) => item.id === profileId);
    if (!window.confirm(`确认删除描述方案「${profile?.name || profileId}」？已生成的商品描述不受影响。`)) return;
    await api(`/api/description-profiles/${encodeURIComponent(profileId)}`, { method: "DELETE" })
      .then(() => loadRuntimeSettings())
      .catch((error) => operationError(error, "描述方案删除失败"));
  }
  if (target.id === "open-store-template-category-dialog") {
    $("store-template-category-dialog")?.showModal();
    await loadMarketplaceCategories("").catch((error) => operationError(error, "类目加载失败"));
  }
  if (target.id === "store-template-promotion-recollect") {
    await recollectStorePromotions();
  }
  if (target.id === "mabang-page-import-btn") {
    openMabangPageImportDialog();
  }
  if (target.id === "category-dialog-import-from-mabang-btn") {
    // From the empty-state inside the category picker, close the picker and open the
    // mabang page import flow — the same one that lives at the top of the templates view.
    $("store-template-category-dialog")?.close();
    openMabangPageImportDialog($("store-template-store")?.value || "");
  }
  if (target.id === "manage-description-profiles-btn") {
    // Jump to settings → description profiles panel so users can add or edit the profile
    // that will populate this dropdown after they reopen the template editor.
    $("store-template-editor-dialog")?.close();
    showView("settings", "settings");
    const nav = document.querySelector('[data-settings-group="description"]');
    if (nav instanceof HTMLElement) {
      document.querySelectorAll("[data-settings-group]").forEach((item) => item.classList.toggle("active", item === nav));
      document.querySelectorAll("[data-settings-panel]").forEach((item) => item.classList.toggle("active", item.dataset.settingsPanel === "description"));
    }
  }
  if (target.id === "save-as-store-template-btn") openSaveAsStoreTemplateDialog();
  if (target.id === "confirm-save-as-store-template") {
    await saveStoreTemplateAsCopy().catch((error) => operationError(error, "另存模板失败"));
  }
  if (target.id === "collect-mabang-page-import-btn") await collectMabangPageImport();
  if (target.id === "confirm-mabang-page-import-btn") await confirmMabangPageImport();
  if (target.id === "mabang-page-import-copy-run-id-btn") { copyMabangPageImportRunId().catch(() => {}); return; }
  const closeDialog = target.closest("[data-close-dialog]");
  if (closeDialog instanceof HTMLElement) $(closeDialog.dataset.closeDialog)?.close();
  if (target.id === "collect-selected-category-profile-btn") await createSelectedCategoryProfileCollectionRun();
  if (target.id === "store-template-ai-attributes-btn") await recommendCategoryAttributesWithAI();
  if (target.id === "base-maintenance-ai-attributes-btn") await recommendBaseAttributesWithAI();
  const categoryCrumb = target.closest("[data-marketplace-category-crumb-index]");
  if (categoryCrumb instanceof HTMLElement) {
    navigateMarketplaceCategoryPath(categoryCrumb.dataset.marketplaceCategoryCrumbIndex || "-1");
  }
  const categoryOption = target.closest("[data-marketplace-category-id]");
  if (categoryOption instanceof HTMLElement) {
    if (categoryOption.dataset.marketplaceCategoryParentId) {
      openMarketplaceCategoryNode(categoryOption.dataset.marketplaceCategoryParentId);
    } else {
      selectMarketplaceCategory(categoryOption.dataset.marketplaceCategoryId || "");
    }
  }
  if (target.id === "create-base-product-btn") createBaseProductFromFolder().catch((error) => operationError(error, "创建基础商品失败"));
  if (target.id === "create-local-base-product-btn") {
    pickLocalProductFolder().catch((error) => operationError(error, "选择本地文件夹失败"));
    return;
  }
  if (target.id === "local-import-queue-clear") { localImportQueue?.clearPending(); return; }
  if (target.id === "local-import-queue-close") {
    localImportQueueCollapsed = true;
    if (localImportIdleTimer) { window.clearTimeout(localImportIdleTimer); localImportIdleTimer = 0; }
    renderLocalImportQueue();
    return;
  }
  if (target.id === "local-import-queue-show") {
    localImportQueueCollapsed = false;
    renderLocalImportQueue();
    return;
  }
  if (target.id === "base-title-queue-close") {
    baseTitleQueueCollapsed = true;
    renderBaseTitleQueue();
    return;
  }
  if (target.id === "base-title-queue-show") {
    baseTitleQueueCollapsed = false;
    renderBaseTitleQueue();
    return;
  }
  if (target.id === "base-title-queue-view") {
    showBaseTitleResults();
    return;
  }
  if (target.id === "base-title-result-copy-all") {
    const text = baseTitleResultText();
    if (!text) {
      showOperationFeedback("这批判识别还没有成功的标题可复制", "info");
      return;
    }
    try {
      await copyTextToClipboard(text);
      showOperationFeedback(`已复制 ${baseTitleQueueDone().length} 条标题（SKU + 标题）`, "success");
    } catch (error) {
      showOperationFeedback(error.message || "复制失败，请手动选择标题复制", "error");
    }
    return;
  }
  const titleCopy = target.closest("[data-base-title-copy]");
  if (titleCopy instanceof HTMLElement) {
    const item = baseTitleQueueItem(titleCopy.dataset.baseTitleCopy || "");
    if (!item?.title) return;
    try {
      await copyTextToClipboard(item.title);
      showOperationFeedback(`已复制 ${item.sku || item.name} 的标题`, "success");
    } catch (error) {
      showOperationFeedback(error.message || "复制失败，请手动选择标题复制", "error");
    }
    return;
  }
  const importRetry = target.closest("[data-import-retry]");
  if (importRetry instanceof HTMLElement) {
    await retryImportQueueItem(importRetry.dataset.importRetry || "", importRetry.dataset.importRetryKind || "");
    return;
  }
  const localImportRemove = target.closest("[data-local-import-remove]");
  if (localImportRemove instanceof HTMLElement) {
    localImportQueue?.removePending(localImportRemove.dataset.localImportRemove || "");
    return;
  }
  if (target.id === "browse-nas-folder-btn") browseNasProductFolder().catch((error) => operationError(error, "打开文件夹选择器失败"));
  if (target.id === "nas-browser-up-btn") { nasBrowserGoUp(); return; }
  if (target.id === "nas-browser-add-btn") { nasBrowserAddCurrent(); return; }
  if (target.id === "nas-browser-confirm-btn") { nasBrowserConfirmCreate().catch((err) => operationError(err, "批量创建失败")); return; }
  if (target.id === "nas-browser-clear-btn") { nasBrowserClearSelected(); return; }
  if (target.id === "nas-browser-edit-root-btn") { openSettingsForNasRoot(); return; }
  if (target.id === "nas-browser-refresh-btn") {
    // 手动刷新绕缓存重扫;浅列表带未识别行时直接要完整结果(含叶子标记)。
    const needFull = (nasBrowserState.entries || []).some((entry) => entry.has_subdirs == null);
    nasBrowserNavigate(nasBrowserState.currentPath, { refresh: true, full: needFull }).catch((err) => operationError(err, "刷新 NAS 目录失败"));
    return;
  }
  const nasRemove = target.closest("[data-nas-remove]");
  if (nasRemove instanceof HTMLElement) {
    nasBrowserRemoveSelected(nasRemove.dataset.nasRemove || "");
    return;
  }
  const nasCrumb = target.closest("[data-nas-crumb]");
  if (nasCrumb instanceof HTMLElement) {
    nasBrowserNavigate(nasCrumb.dataset.nasCrumb || "");
    return;
  }
  const nasSelect = target.closest("[data-nas-select]");
  if (nasSelect instanceof HTMLElement) {
    nasBrowserToggleLeaf(nasSelect.dataset.nasSelect || "");
    return;
  }
  const nasEnter = target.closest("[data-nas-enter]");
  if (nasEnter instanceof HTMLElement) {
    const name = nasEnter.dataset.nasEnter || "";
    const sep = nasBrowserState.currentPath.includes("\\") ? "\\" : (nasBrowserState.currentPath.includes("/") ? "/" : "\\");
    nasBrowserNavigate(`${nasBrowserState.currentPath}${sep}${name}`);
    return;
  }
  if (target.id === "add-base-color-btn") addBaseColor();
  const quickColorChip = target.closest("[data-quick-color]");
  if (quickColorChip) addBaseColorWithName(quickColorChip.dataset.quickColor || "");
  if (target.closest("[data-quick-color-select-all]")) addBaseColorsWithNames(BASE_QUICK_COLORS.map((entry) => entry.name));
  if (target.id === "save-base-image-order-btn") saveBaseImageOrder();
  if (target.id === "close-base-image-editor-btn" || target.id === "close-base-maintenance-btn") closeBaseImageEditor();
  if (target.id === "base-maintenance-rollback-btn") await rollbackBaseProduct(state.editingBaseProductId || state.selectedBaseProductId);
  if (target.id === "base-maintenance-ai-retry") await retryBaseProductAiListing();
  if (target.id === "base-maintenance-ai-title") await generateBaseProductTitleCandidates();
  const titleSuggestion = target.closest("[data-base-title-suggestion]");
  if (titleSuggestion instanceof HTMLElement) {
    const titleInput = $("base-maintenance-title-input");
    if (titleInput) {
      titleInput.value = titleSuggestion.dataset.baseTitleSuggestion || "";
      titleInput.dispatchEvent(new Event("input", { bubbles: true }));
      titleInput.focus();
    }
    return;
  }
  if (target.id === "base-price-collapse-btn") toggleBasePriceCollapsed();
  if (target.id === "base-price-refresh-btn") await loadBasePriceColors(state.editingBaseProductId);
  if (target.id === "base-recognize-sizes") await recognizeBaseSizeChart();
  if (target.id === "base-clear-size-chart") { state.editingBaseSizeChartImage = ""; renderBaseSizeChartSection(); }
  if (target.id === "base-sizes-select-all") selectAllBaseSizes();
  if (target.id === "base-sizes-clear") clearBaseSizes();
  if (target.id === "base-sizes-common") applyBaseCommonSizes();
  if (target.id === "base-size-template-save") saveCurrentSizesAsTemplate();
  if (target.id === "base-size-template-delete") deleteBaseSizeTemplate($("base-size-template-select")?.value || "");

  if (target.id === "open-base-category-dialog") await openBaseCategoryDialog().catch((error) => operationError(error, "类目加载失败"));
  if (target.id === "clear-base-category-btn") clearBaseProductCategory();
  if (target.id === "base-category-template-save") await saveCurrentBaseCategoryAsQuickTemplate();
  if (target.id === "base-category-template-delete") await deleteBaseCategoryQuickTemplate($("base-category-template-select")?.value || "");
  const showAllToggle = target.closest("[data-cascader-show-all]");
  if (showAllToggle instanceof HTMLElement) {
    state.baseCategoryShowAll = true;
    renderCascaderColumn(0, "");
    return;
  }
  const collapseToggle = target.closest("[data-cascader-collapse]");
  if (collapseToggle instanceof HTMLElement) {
    state.baseCategoryShowAll = false;
    renderCascaderColumn(0, "");
    return;
  }
  const cascaderItem = target.closest("[data-cascader-category-id]");
  if (cascaderItem instanceof HTMLElement) {
    onBaseCategoryItemActivated(cascaderItem);
    return;
  }
  const cascaderCrumb = target.closest("[data-cascader-crumb-depth]");
  if (cascaderCrumb instanceof HTMLElement) {
    goCascaderCrumb(cascaderCrumb.dataset.cascaderCrumbDepth || "-1");
    return;
  }

  if (target.id === "base-add-size-btn") {
    const input = $("base-add-size-input");
    addBaseRecognizedSize(input?.value || "");
    if (input) input.value = "";
  }
  if (target.id === "base-price-parse-btn") await previewBasePriceMatrix().catch((error) => showOperationFeedback(error.message || "解析失败", "error"));
  if (target.id === "base-price-cnsc-site-order") openCnscSiteOrderDialog();
  if (target.id === "base-price-apply-all") applyBasePriceFirstRowToAll();
  if (target.id === "base-price-batch-cost-apply") applyBasePriceBatchCost();

  if (target.id === "new-store-template-btn") await openNewStoreTemplateEditor().catch((error) => operationError(error, "打开模板编辑器失败"));
  const watermarkTrigger = target.closest("[data-watermark-trigger]");
  if (watermarkTrigger instanceof HTMLElement) {
    const role = watermarkTrigger.dataset.watermarkTrigger || "";
    if (state.storeWatermarks.length) toggleWatermarkMenu(role);
    return;
  }
  const watermarkOption = target.closest(".watermark-menu-item");
  if (watermarkOption instanceof HTMLElement) {
    const targetChoice = watermarkMenuOptionTarget(watermarkOption);
    if (targetChoice) setWatermarkChoice(targetChoice.role, targetChoice.templateId);
    return;
  }
  if (state.watermarkOpenRole) {
    const picker = target.closest(".watermark-picker");
    const stillInside = picker?.dataset.watermarkPicker === state.watermarkOpenRole;
    if (!stillInside) closeWatermarkMenu();
  }
  if (target.id === "store-template-watermark-collect-btn") {
    // collectStoreWatermarks() 自己把失败原因写进状态行，不再抛给通用错误提示。
    await collectStoreWatermarks();
    return;
  }
  const watermarkManualApply = target.closest("[data-watermark-manual-apply]");
  if (watermarkManualApply) {
    applyManualWatermark(watermarkManualApply.dataset.watermarkManualApply || "");
    return;
  }
  const watermarkManualClear = target.closest("[data-watermark-manual-clear]");
  if (watermarkManualClear) {
    const role = watermarkManualClear.dataset.watermarkManualClear || "";
    setWatermarkChoiceValue(role, "", "");
    const manualId = document.querySelector(`[data-watermark-manual-id="${role}"]`);
    const manualName = document.querySelector(`[data-watermark-manual-name="${role}"]`);
    if (manualId) manualId.value = "";
    if (manualName) manualName.value = "";
    renderWatermarkPicker(role);
    renderStoreWatermarkPreview(role);
    return;
  }
  if (target.id === "batch-create-automation-jobs-btn") await createAutomationJobsFromSelection().catch((error) => operationError(error, "批量生成自动化任务失败"));
  if (target.id === "refresh-automation-view-btn" || target.id === "refresh-automation-jobs-btn") { await refreshAutomationView(); return; }
  if (target.id === "select-all-listing-copies-btn") { toggleSelectAllListingCopies(); return; }
  if (target.id === "delete-selected-listing-copies-btn") {
    await deleteSelectedListingCopies().catch((error) => operationError(error, "批量删除副本失败"));
    return;
  }
  // 人工标记按钮在执行详情弹窗里：作用于当前打开的那条作业。
  if (target.id === "automation-manual-saved-btn") {
    await markAutomationJobManually(state.openAutomationJobId || "", "saved");
    return;
  }
  if (target.id === "automation-manual-failed-btn") {
    await markAutomationJobManually(state.openAutomationJobId || "", "failed");
    return;
  }
  if (target.id === "create-store-template-btn") createStoreTemplate().then((saved) => {
    if (saved) {
      showOperationFeedback("店铺模板已保存", "success");
      $("store-template-editor-dialog")?.close();
    }
  }).catch((error) => operationError(error, "保存店铺模板失败"));
  if (target.id === "store-template-price-preview-btn") {
    previewCnscPriceMatrix().catch((error) => showOperationFeedback(error.message || "价格表解析失败", "error"));
  }
  if (target.id === "refresh-category-versions-btn") {
    loadCategoryProfileVersions($("store-template-category-profile")?.value || "");
  }
  const saveTemplateIdsButton = target.closest("[data-save-template-ids]");
  if (saveTemplateIdsButton instanceof HTMLElement && saveTemplateIdsButton.dataset.saveTemplateIds) {
    saveStoreTemplateIds(saveTemplateIdsButton.dataset.saveTemplateIds);
  }
  if (target.id === "copy-to-store-templates-btn") openCopyTemplatePicker();
  const copyModeCard = target.closest("[data-copy-template-mode]");
  if (copyModeCard instanceof HTMLElement) {
    setCopyTemplatePickerMode(copyModeCard.dataset.copyTemplateMode || "site");
    return;
  }
  if (target.id === "confirm-copy-template-picker") {
    await copySelectedBaseProductToTemplates()
      .then((created) => {
        if (!created) return;
        $("copy-template-picker-dialog")?.close();
        showOperationFeedback("店铺刊登副本已生成", "success");
      })
      .catch((error) => operationError(error, "生成店铺刊登副本失败"));
  }
  const fillMissing = target.closest("[data-fill-missing-attributes]");
  if (fillMissing instanceof HTMLElement) {
    await fillListingCopyMissingAttributes(fillMissing.dataset.fillMissingAttributes || "").catch((error) => operationError(error, "从模板补全属性失败"));
    return;
  }
  const editListingCopy = target.closest("[data-edit-listing-copy]");
  if (editListingCopy instanceof HTMLElement) {
    await openListingCopyEditor(editListingCopy.dataset.editListingCopy || "").catch((error) => {
      $("listing-copy-editor-dialog")?.close();
      operationError(error, "加载店铺刊登副本失败");
    });
  }
  const deleteListingCopyBtn = target.closest("[data-delete-listing-copy]");
  if (deleteListingCopyBtn instanceof HTMLElement) {
    await deleteStoreListingCopy(deleteListingCopyBtn.dataset.deleteListingCopy || "").catch((error) => operationError(error, "删除店铺刊登副本失败"));
  }
  const automationJobDetails = target.closest("[data-automation-job-details]");
  if (automationJobDetails instanceof HTMLElement) {
    // Row-level details open — but never as a side effect of an action button.
    // 任务表把 data-automation-job-details 挂在 <tr> 上，所以「再次尝试 / 催 Worker /
    // 取消 / 删除」这些按钮的点击也会 closest() 到整行；放行条件必须是"没点到交互
    // 控件"或"点到的是详情触发器本身"，否则点一次重试会先弹一遍执行详情。
    const control = target.closest("button, a, input, select");
    if (!control || control === automationJobDetails) {
      openAutomationJobDetails(automationJobDetails.dataset.automationJobDetails || "");
    }
  }
  const cancelAutomationJobButton = target.closest("[data-cancel-automation-job]");
  if (cancelAutomationJobButton instanceof HTMLElement) {
    await cancelAutomationJob(cancelAutomationJobButton.dataset.cancelAutomationJob || "").catch((error) => operationError(error, "取消自动化任务失败"));
  }
  const deleteAutomationJobButton = target.closest("[data-delete-automation-job]");
  if (deleteAutomationJobButton instanceof HTMLElement) {
    await deleteAutomationJob(deleteAutomationJobButton.dataset.deleteAutomationJob || "").catch((error) => operationError(error, "删除自动化任务失败"));
  }
  const selfHealButton = target.closest("[data-self-heal-automation-job]");
  if (selfHealButton instanceof HTMLElement) {
    // 入队而不是直接发请求：连点多个任务时串行重排，避免同时唤醒 Worker 把界面拖卡。
    enqueueAutomationRetry(selfHealButton.dataset.selfHealAutomationJob || "");
  }
  const nudgeButton = target.closest("[data-nudge-worker]");
  if (nudgeButton instanceof HTMLElement) {
    await nudgeWorkerForJob(nudgeButton.dataset.nudgeWorker || "").catch((error) => operationError(error, "唤醒 Worker 失败"));
  }
  const editStoreTemplate = target.closest("[data-edit-store-template]");
  if (editStoreTemplate instanceof HTMLElement) {
    await openStoreTemplateEditor(editStoreTemplate.dataset.editStoreTemplate || "").catch((error) => operationError(error, "加载店铺模板失败"));
  }
  const copyStoreTemplateButton = target.closest("[data-copy-store-template]");
  if (copyStoreTemplateButton instanceof HTMLElement) {
    await copyStoreTemplate(copyStoreTemplateButton.dataset.copyStoreTemplate || "").catch((error) => operationError(error, "复制店铺模板失败"));
  }
  const deleteStoreTemplateButton = target.closest("[data-delete-store-template]");
  if (deleteStoreTemplateButton instanceof HTMLElement) {
    await deleteStoreTemplate(deleteStoreTemplateButton.dataset.deleteStoreTemplate || "").catch((error) => operationError(error, "删除店铺模板失败"));
  }
  if (target.id === "listing-copy-save") await saveListingCopy().then(() => showOperationFeedback("刊登副本已保存", "success")).catch((error) => operationError(error, "保存刊登副本失败"));
  if (target.id === "listing-copy-description-retry") await retryListingCopyDescription().catch((error) => operationError(error, "重新生成描述失败"));
  if (target.id === "listing-copy-create-job") await createAutomationJobForListingCopy().then(() => showOperationFeedback("自动化任务已生成", "success")).catch((error) => operationError(error, "生成自动化任务失败"));
  if (target.id === "listing-copy-video-remove") await removeListingCopyVideo().catch((error) => operationError(error, "删除视频失败"));
  if (target.id === "listing-copy-apply-stock") {
    const stock = $("listing-copy-stock").value;
    document.querySelectorAll("[data-listing-copy-sku-stock]").forEach((input) => { input.value = stock; });
    $("listing-copy-save-result").textContent = "统一库存已应用到全部 SKU，保存副本后生效";
  }
  if (target.id === "listing-copy-warehouse-batch-apply") {
    // 只应用填了值的仓库，留空的仓库保持每行原值。
    const applied = [];
    for (const code of ["TH", "CN_GZ", "PH"]) {
      const value = String($(`listing-copy-batch-stock-${code}`)?.value ?? "").trim();
      if (value === "") continue;
      document.querySelectorAll(`[data-listing-copy-cnsc-warehouse="${CSS.escape(code)}"]`).forEach((input) => { input.value = value; });
      applied.push(code);
    }
    $("listing-copy-save-result").textContent = applied.length
      ? `已批量更新仓库库存（${applied.join("、")}），保存副本后生效`
      : "请先填写要批量应用的仓库库存";
  }
  if (target.id === "listing-copy-cnsc-parse") {
    previewListingCopyCnscPriceMatrix().catch((error) => showOperationFeedback(error.message || "价格表解析失败", "error"));
  }
  if (target.id === "listing-copy-cnsc-site-order" || target.id === "store-template-cnsc-site-order") openCnscSiteOrderDialog();
  if (target.id === "cnsc-site-order-save") saveCnscSiteOrder().catch((error) => showOperationFeedback(error.message || "识别顺序保存失败", "error"));
  if (target.id === "cnsc-site-order-reset") resetCnscSiteOrderInput();
  if (target.id === "listing-copy-cnsc-add-tier") addListingCopyCnscTier();
  const applyRowButton = target.closest("[data-listing-copy-cnsc-apply-row]");
  if (applyRowButton instanceof HTMLElement) {
    applyListingCopyCnscRowToAll(applyRowButton.dataset.listingCopyCnscApplyRow || "");
  }
  const removeCnscTierButton = target.closest("[data-listing-copy-cnsc-remove-tier]");
  if (removeCnscTierButton instanceof HTMLElement) {
    removeListingCopyCnscTier(removeCnscTierButton.dataset.listingCopyCnscRemoveTier || "");
  }
  const tierSizeChip = target.closest("[data-cnsc-tier-size]");
  if (tierSizeChip instanceof HTMLElement) {
    toggleListingCopyCnscTierSize(tierSizeChip.dataset.cnscTierSize || "", tierSizeChip.dataset.size || "");
  }
  const listingCopyFocus = target.closest("[data-listing-copy-focus]");
  if (listingCopyFocus instanceof HTMLElement) {
    const section = $(listingCopyFocus.dataset.listingCopyFocus || "");
    // Discount-activity fields live inside the collapsed target-site block;
    // expand it first so the scroll-into-view + focus below can land on them.
    if (section?.id === "listing-copy-cnsc-section") {
      const targetDetails = $("listing-copy-cnsc-target-details");
      if (targetDetails) targetDetails.open = true;
    }
    section?.scrollIntoView({ behavior: "smooth", block: "start" });
    section?.querySelector("input, select, textarea")?.focus({ preventScroll: true });
  }
  if (target.id === "scan-assets-btn") scanAssets().catch((error) => operationError(error, "扫描素材失败"));
  const editImagesButton = target.closest("[data-edit-base-images]");
  if (editImagesButton instanceof HTMLElement && editImagesButton.dataset.editBaseImages) {
    openBaseProductMaintenance(editImagesButton.dataset.editBaseImages);
  }
  const maintainButton = target.closest("[data-maintain-base-product]");
  if (maintainButton instanceof HTMLElement && maintainButton.dataset.maintainBaseProduct) {
    openBaseProductMaintenance(maintainButton.dataset.maintainBaseProduct);
  }
  const deleteBaseProductButton = target.closest("[data-delete-base-product]");
  if (deleteBaseProductButton instanceof HTMLElement && deleteBaseProductButton.dataset.deleteBaseProduct) {
    await deleteBaseProduct(deleteBaseProductButton.dataset.deleteBaseProduct).catch((error) => operationError(error, "删除基础商品失败"));
  }
  const removeColorButton = target.closest("[data-remove-base-color]");
  if (removeColorButton instanceof HTMLElement && removeColorButton.dataset.removeBaseColor) {
    removeBaseColor(Number(removeColorButton.dataset.removeBaseColor));
  }
  const addProductImageButton = target.closest("[data-add-base-product-image]");
  if (addProductImageButton instanceof HTMLElement && addProductImageButton.dataset.addBaseProductImage) {
    addBaseProductImage(addProductImageButton.dataset.addBaseProductImage);
  }
  const removeProductImageButton = target.closest("[data-remove-base-product-image]");
  if (removeProductImageButton instanceof HTMLElement && removeProductImageButton.dataset.removeBaseProductImage) {
    removeBaseProductImage(Number(removeProductImageButton.dataset.removeBaseProductImage));
  }
  const variantImageTrigger = target.closest("#base-variant-image-map [data-variant-image-trigger]");
  if (variantImageTrigger instanceof HTMLElement) {
    toggleVariantImagePicker(Number(variantImageTrigger.dataset.variantImageTrigger || -1));
  }
  const variantImagePick = target.closest("#variant-image-picker-popup [data-variant-image-pick]");
  if (variantImagePick instanceof HTMLElement) {
    const popup = variantImagePick.closest("#variant-image-picker-popup");
    selectBaseVariantImage(Number(popup?.dataset.variantImageRow || -1), variantImagePick.dataset.variantImagePick || "");
  }
  const fileGroup = target.closest("[data-file-group]");
  if (fileGroup instanceof HTMLElement && fileGroup.dataset.fileGroup) {
    state.selectedFileGroup = fileGroup.dataset.fileGroup;
    renderAssets();
  }
});

document.addEventListener("dragstart", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const listingItem = target.closest("[data-listing-copy-image-index]");
  if (listingItem instanceof HTMLElement) {
    state.draggingListingCopyImageIndex = Number(listingItem.dataset.listingCopyImageIndex || -1);
    listingItem.classList.add("dragging");
    event.dataTransfer?.setData("text/listing-copy-image-index", String(state.draggingListingCopyImageIndex));
    event.dataTransfer?.setDragImage(listingItem, 20, 20);
    return;
  }
  const colorItem = target.closest("[data-color-row-index]");
  if (colorItem instanceof HTMLElement && !(target instanceof HTMLInputElement) && !(target instanceof HTMLSelectElement) && !(target instanceof HTMLButtonElement)) {
    state.draggingBaseColorIndex = Number(colorItem.dataset.colorRowIndex || -1);
    colorItem.classList.add("dragging");
    event.dataTransfer?.setData("text/plain", String(state.draggingBaseColorIndex));
    event.dataTransfer?.setDragImage(colorItem, 20, 20);
    return;
  }
  const item = target.closest("[data-image-order-index]");
  if (!(item instanceof HTMLElement)) return;
  state.draggingBaseImageIndex = Number(item.dataset.imageOrderIndex || -1);
  item.classList.add("dragging");
  event.dataTransfer?.setData("text/plain", String(state.draggingBaseImageIndex));
  event.dataTransfer?.setDragImage(item, 20, 20);
});

document.addEventListener("dragover", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const listingItem = target.closest("[data-listing-copy-image-index]");
  if (listingItem instanceof HTMLElement) {
    event.preventDefault();
    document.querySelectorAll(".listing-copy-image-item.drag-over").forEach((node) => node.classList.remove("drag-over"));
    listingItem.classList.add("drag-over");
    return;
  }
  const colorItem = target.closest("[data-color-row-index]");
  if (colorItem instanceof HTMLElement) {
    event.preventDefault();
    document.querySelectorAll(".variant-image-row.drag-over").forEach((node) => node.classList.remove("drag-over"));
    colorItem.classList.add("drag-over");
    return;
  }
  const item = target.closest("[data-image-order-index]");
  if (item instanceof HTMLElement) {
    event.preventDefault();
    document.querySelectorAll(".image-order-item.drag-over").forEach((node) => node.classList.remove("drag-over"));
    item.classList.add("drag-over");
  }
});

document.addEventListener("drop", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const listingItem = target.closest("[data-listing-copy-image-index]");
  if (listingItem instanceof HTMLElement) {
    event.preventDefault();
    const toIndex = Number(listingItem.dataset.listingCopyImageIndex || -1);
    const transferIndex = event.dataTransfer?.getData("text/listing-copy-image-index");
    const fromIndex = Number(transferIndex || state.draggingListingCopyImageIndex);
    moveListingCopyImage(fromIndex, toIndex);
    state.draggingListingCopyImageIndex = -1;
    return;
  }
  const colorItem = target.closest("[data-color-row-index]");
  if (colorItem instanceof HTMLElement) {
    event.preventDefault();
    const toIndex = Number(colorItem.dataset.colorRowIndex || -1);
    const fromIndex = Number(event.dataTransfer?.getData("text/plain") || state.draggingBaseColorIndex);
    moveBaseColor(fromIndex, toIndex);
    state.draggingBaseColorIndex = -1;
    return;
  }
  const item = target.closest("[data-image-order-index]");
  if (!(item instanceof HTMLElement)) return;
  event.preventDefault();
  const toIndex = Number(item.dataset.imageOrderIndex || -1);
  const fromIndex = Number(event.dataTransfer?.getData("text/plain") || state.draggingBaseImageIndex);
  moveBaseImage(fromIndex, toIndex);
  state.draggingBaseImageIndex = -1;
});

document.addEventListener("dragend", () => {
  document.querySelectorAll(".image-order-item").forEach((item) => item.classList.remove("dragging", "drag-over"));
  document.querySelectorAll(".listing-copy-image-item").forEach((item) => item.classList.remove("dragging", "drag-over"));
  document.querySelectorAll(".variant-image-row").forEach((item) => item.classList.remove("dragging", "drag-over"));
  state.draggingBaseImageIndex = -1;
  state.draggingListingCopyImageIndex = -1;
  state.draggingBaseColorIndex = -1;
});

document.addEventListener("change", (event) => {
  const target = event.target;
  if (target instanceof HTMLSelectElement && target.id === "store-template-account") {
    // 切账号 = 换一套模板与店铺：重拉该账号的模板并重画店铺下拉。
    switchStoreTemplateAccount(target.value).catch((error) => operationError(error, "切换马帮账号失败"));
    return;
  }
  if (target instanceof HTMLSelectElement && target.id === "store-template-listing-mode") {
    updateCnscTemplateVisibility();
    return;
  }
  if (target instanceof HTMLSelectElement && target.id === "store-template-target-sites") {
    state.cnscPricePreview = null;
    const preview = $("store-template-price-preview");
    if (preview) preview.textContent = "目标站点已变更，请重新解析站点价格表。";
    renderCnscTargetStoreConfigs();
    return;
  }
  if (target instanceof HTMLSelectElement && target.id === "listing-copy-cnsc-binding-mode") {
    setListingCopyCnscBindingMode(target.value);
    return;
  }
  if (target instanceof HTMLSelectElement && target.matches("[data-listing-copy-cnsc-activity]")) {
    renderListingCopyCnscSummary();
    return;
  }
  if (target instanceof HTMLSelectElement && target.matches("[data-cnsc-target-activity]")) {
    selectedCnscTargetSiteStores();
    return;
  }
  if (target instanceof HTMLInputElement && target.id === "base-color-follow-images") {
    state.editingBaseColorOrderFollowsImages = target.checked;
    renderBaseColorFollowToggle();
    applyBaseColorFollowFromImages();
    return;
  }
  if (target instanceof HTMLSelectElement && target.id === "base-size-chart-image") {
    state.editingBaseSizeChartImage = target.value || "";
    renderBaseSizeChartSection();
    return;
  }
  if (target instanceof HTMLSelectElement && target.id === "base-size-template-select") {
    if (target.value) applyBaseSizeTemplate(target.value);
    return;
  }
  if (target instanceof HTMLSelectElement && target.id === "base-category-template-select") {
    if (target.value) applyBaseCategoryQuickTemplate(target.value);
    return;
  }
  if (target instanceof HTMLInputElement && target.matches("[data-base-size-check]")) {
    const size = target.dataset.baseSizeCheck || "";
    const selected = (state.editingBaseSelectedSizes || []).filter((s) => s.toLowerCase() !== size.toLowerCase());
    if (target.checked) selected.push(size);
    state.editingBaseSelectedSizes = selected;
    renderBaseRecognizedSizes();
    return;
  }
  if (target instanceof HTMLInputElement && target.matches("[data-base-color-index]")) {
    const index = Number(target.dataset.baseColorIndex || -1);
    renameBaseColor(index, target.value);
    renderBaseVariantImageMap();
    renderBasePriceColors();
    return;
  }
  if (target instanceof HTMLSelectElement && target.id === "store-template-category-profile") {
    if (target.value) applyStoreTemplateCategoryProfile();
    return;
  }
  if (target instanceof HTMLSelectElement && target.id === "store-template-store") {
    // 切换店铺：清空旧目录和旧选择后再加载新店铺目录。
    state.storeWatermarks = [];
    state.watermarkCatalogStatus = null;
    state.watermarkCatalogStoreId = "";
    closeWatermarkMenu();
    resetStoreWatermarkChoices();
    renderWatermarkPicker("main");
    renderWatermarkPicker("product");
    renderWatermarkCatalogStatus();
    clearMarketplaceCategorySelection({ clearSearch: true });
    renderStoreTemplateDescriptionProfileOptions();
    loadStorePromotions().catch((err) => { $("store-template-result").textContent = err.message; });
    loadStoreWatermarks().catch((err) => { $("store-template-result").textContent = err.message; });
    loadStoreSizeChartTemplates().catch((err) => { $("store-template-result").textContent = err.message; });
    loadMarketplaceCategories("").catch((err) => {
      $("store-template-category-picker-status").textContent = err.message;
    });
    return;
  }
  if (target instanceof HTMLInputElement && target.type === "radio" && target.name === "store-template-main-watermark-mode") {
    applyWatermarkModeUi("main", target.value);
    return;
  }
  if (target instanceof HTMLInputElement && target.type === "radio" && target.name === "store-template-product-watermark-mode") {
    applyWatermarkModeUi("product", target.value);
    return;
  }
  if (target.id === "store-template-default-sku-path") {
    renderDefaultSkuImagePreview();
    return;
  }
  if (target instanceof HTMLSelectElement && target.id === "store-template-size-chart-source") {
    renderSizeChartSource(target.value);
    if (target.value === "template") renderSizeChartTemplateOptions();
    return;
  }
  if (target instanceof HTMLSelectElement && target.id === "store-template-size-chart-template") {
    renderStoreTemplateSizeChartPreview();
    return;
  }
  if (target instanceof HTMLInputElement && target.matches("[data-cnsc-target-discount-percent]")) {
    const row = target.closest("[data-cnsc-target-store-row]");
    const code = row?.querySelector("strong")?.textContent || "";
    selectedCnscTargetSiteStores();
    recalculateCnscSitePrices(code, target.value);
    renderCnscTargetStoreConfigs();
    return;
  }
  if (!(target instanceof HTMLInputElement)) return;
  if (target.matches("[data-cnsc-target-discount-percent]")) {
    selectedCnscTargetSiteStores();
    return;
  }
  if (["store-template-cnsc-standard-sizes", "store-template-cnsc-large-sizes"].includes(target.id)) {
    state.cnscPricePreview = null;
    const preview = $("store-template-price-preview");
    if (preview) preview.textContent = "价格档位已变更，请重新解析站点价格表。";
    return;
  }
  if (target.id === "local-base-product-folder-upload" && target.files?.length) {
    const picked = [...target.files];
    target.value = "";
    createBaseProductFromLocalFiles(picked).catch((error) => operationError(error, "从本地创建基础商品失败"));
    return;
  }
  if (target.id === "listing-copy-size-chart-upload" && target.files?.[0]) {
    showListingCopyUploadFileName("size_chart", target.files[0]);
    uploadListingCopySizeChart(target.files[0]).catch((error) => operationError(error, "上传尺码图失败"));
    return;
  }
  if (target.id === "listing-copy-video-upload" && target.files?.[0]) {
    showListingCopyUploadFileName("video", target.files[0]);
    uploadListingCopyVideo(target.files[0]).catch((error) => operationError(error, "上传视频失败"));
    target.value = "";
    return;
  }
  if (target.id === "base-product-video-upload" && target.files?.[0]) {
    uploadBaseProductVideo(target.files[0]).catch((error) => operationError(error, "上传视频失败"));
    target.value = "";
    return;
  }
  if (target.id === "base-product-image-upload" && target.files?.length) {
    uploadBaseProductProductImages([...target.files]).catch((error) => operationError(error, "上传商品图失败"));
    target.value = "";
    return;
  }
  if (target.id === "store-template-size-chart-upload" && target.files?.[0]) {
    const fileNameEl = $("store-template-size-chart-file-name");
    if (fileNameEl) fileNameEl.textContent = target.files[0].name || "已选择尺码图";
    uploadSizeChart(target.files[0]).catch((err) => { $("store-template-result").textContent = err.message; });
    return;
  }
  if (target.id === "store-template-default-sku-upload" && target.files?.[0]) {
    const fileNameEl = $("store-template-default-sku-file-name");
    if (fileNameEl) fileNameEl.textContent = target.files[0].name || "已选择图片";
    uploadDefaultSkuImage(target.files[0]).catch((err) => { $("store-template-result").textContent = err.message; });
    return;
  }
  if (target.dataset.baseProductSelect) {
    // 勾选框 = 多选（批量复制/删除对象），与单选 selectedBaseProductId 相互独立。
    const id = target.dataset.baseProductSelect;
    if (target.checked) state.selectedBaseProducts.add(id);
    else state.selectedBaseProducts.delete(id);
    renderBaseProductBatchBar();
    renderBaseProducts();
    return;
  }
  if (target.dataset.storeTemplateSelect) {
    if (target.checked) state.selectedStoreTemplates.add(target.dataset.storeTemplateSelect);
    else state.selectedStoreTemplates.delete(target.dataset.storeTemplateSelect);
  }
  if (target.dataset.listingCopySelect) {
    if (target.checked) state.selectedListingCopies.add(target.dataset.listingCopySelect);
    else state.selectedListingCopies.delete(target.dataset.listingCopySelect);
    updateListingCopySelectAllButton();
  }
});

document.addEventListener("input", (event) => {
  const target = event.target;
  if (target instanceof HTMLElement && target.matches("#description-profile-dialog [data-section-body], #description-profile-dialog [data-section-hint], #description-profile-dialog [data-section-title], #description-profile-dialog [data-section-emoji], #description-profile-dialog [data-row-emoji], #description-profile-dialog [data-row-label], #description-profile-dialog [data-row-value], #description-profile-dialog [data-row-hint], #description-profile-dialog [data-row-slot], #description-profile-dialog [data-row-field], #description-profile-dialog [data-row-source]")) {
    renderProfilePreview();
    return;
  }
  if (target instanceof HTMLInputElement && target.id === "nas-browser-filter") {
    nasBrowserState.filter = target.value || "";
    nasBrowserState.renderLimit = NAS_BROWSER_RENDER_PAGE;
    renderNasBrowserList();
    return;
  }
  if (target instanceof HTMLInputElement && target.dataset.listingCopyNormalPrice) {
    const sale = document.querySelector(`[data-listing-copy-sale-price="${CSS.escape(target.dataset.listingCopyNormalPrice)}"]`);
    if (sale instanceof HTMLInputElement) sale.value = calculateFortyPercentDiscount(target.value);
    return;
  }
  if (target instanceof HTMLInputElement && target.id === "store-template-default-site-discount-percent") {
    selectedCnscTargetSiteStores();
    for (const option of $("store-template-target-sites")?.selectedOptions || []) {
      const config = state.cnscTargetStoreConfigs.get(option.value) || {};
      if (!String(config.discount_percent ?? "").trim()) recalculateCnscSitePrices(option.dataset.siteCode || "", target.value);
    }
    renderCnscTargetStoreConfigs();
    return;
  }
  if (target instanceof HTMLInputElement && target.id === "listing-copy-cnsc-default-discount") {
    // Persist the edited per-site %OFF values before re-rendering the placeholder hints.
    for (const [code, config] of state.listingCopyTargetConfigs) {
      const discountInput = document.querySelector(`[data-listing-copy-cnsc-discount="${CSS.escape(code)}"]`);
      if (discountInput instanceof HTMLInputElement) config.discount_percent = discountInput.value;
    }
    renderListingCopyCnscTargets();
    return;
  }
  if (target instanceof HTMLInputElement && target.id === "store-template-normal-price") {
    // 六折售价是预填建议：操作员自己改过就不再覆盖，除非把那一栏清空。
    if (!storeTemplateSalePriceIsManual()) {
      $("store-template-sale-price").value = calculateFortyPercentDiscount(target.value);
    }
    renderSizePriceOverrides(collectSizePriceOverrides());
    return;
  }
  if (target instanceof HTMLInputElement && target.id === "store-template-sale-price") {
    // 手填后不再被原价自动覆盖；清空即回到"按原价六折"的自动预填。
    target.dataset.manual = target.value.trim() ? "1" : "";
    renderSizePriceOverrides(collectSizePriceOverrides());
    return;
  }
  if (target instanceof HTMLInputElement && ["store-template-default-sku-normal-price", "store-template-default-sku-sale-price"].includes(target.id)) {
    // 默认 SKU 售价/折扣价变化：只刷新分尺码行的占位符，操作员自己填过的值不动。
    refreshDefaultSkuSizePricePlaceholders();
    return;
  }
  if (target instanceof HTMLInputElement && target.dataset.templateSize) {
    // Rebuild chip list preserving user-defined order for selected sizes.
    const root = $("store-template-size-options");
    const selectedNow = [...(root?.querySelectorAll("label.size-chip") || [])]
      .map((el) => {
        const cb = el.querySelector("[data-template-size]");
        return cb instanceof HTMLInputElement && cb.checked ? cb.dataset.templateSize : "";
      })
      .filter(Boolean);
    const overrides = collectSizePriceOverrides();
    renderSizeOptions(selectedNow);
    renderSizePriceOverrides(overrides);
    renderDefaultSkuSizePriceOverrides(collectDefaultSkuSizePriceOverrides());
    return;
  }
  if (target instanceof HTMLInputElement && target.id === "description-profile-name") {
    const output = $("description-profile-preview-head-name");
    if (output) output.textContent = target.value.trim() || "（未命名方案）";
    return;
  }
  if (target instanceof HTMLInputElement && target.dataset.sizePrice) {
    // 改原价只刷新右侧「自动」占位符，操作员自己填过的售价保持不动。
    refreshSizePricePlaceholders();
    return;
  }
  if (target instanceof HTMLInputElement && target.dataset.defaultSkuSizePrice) {
    // 默认 SKU 分尺码行改原价：只刷新折扣价「自动」占位符。
    refreshDefaultSkuSizePricePlaceholders();
    return;
  }
  if (target instanceof HTMLInputElement && target.id === "copy-template-search") {
    renderCopyTemplatePicker(target.value);
    return;
  }
  if (target instanceof HTMLInputElement && target.id === "base-category-search") {
    window.clearTimeout(state.baseCategorySearchTimer);
    state.baseCategorySearchTimer = window.setTimeout(() => {
      searchBaseCategories(target.value).catch((err) => {
        const status = $("base-category-picker-status");
        if (status) status.textContent = err.message;
      });
    }, 200);
    return;
  }
  if (!(target instanceof HTMLInputElement) || target.id !== "store-template-category-search") return;
  const selected = state.selectedMarketplaceCategory;
  const selectedLabels = [selected?.path, selected?.path_en, selected?.path_zh, selected?.name].filter(Boolean);
  if (selected && !selectedLabels.includes(target.value)) {
    clearMarketplaceCategorySelection();
  }
  invalidateMarketplaceCategoryRequests();
  disableMarketplaceCategoryResults();
  window.clearTimeout(state.marketplaceCategorySearchTimer);
  state.marketplaceCategorySearchTimer = window.setTimeout(() => {
    loadMarketplaceCategories(target.value).catch((err) => {
      $("store-template-category-picker-status").textContent = err.message;
    });
  }, 180);
});

document.addEventListener("change", (event) => {
  const target = event.target;
  if (target instanceof HTMLSelectElement && target.matches("[data-section-type]")) {
    const sections = collectProfileSectionsFromEditor() || [...(state.editingProfileSections || [])];
    const index = Number(target.dataset.sectionType);
    const previous = sections[index] || {};
    if (target.value === "fixed") previous.body = previous.body || "";
    if (target.value === "ai") { previous.slot = previous.slot || previous.id || autoAiSlotName(index, "section"); previous.hint = previous.hint || ""; }
    if (target.value === "fields" && !Array.isArray(previous.rows)) {
      previous.rows = [{ emoji: "🏷️", label: "", source: "field", field: "color" }];
    }
    sections[index] = previous;
    renderProfileSectionsEditor(sections);
    const details = document.querySelector(`[data-section-index="${index}"]`);
    if (details) details.open = true;
    renderProfilePreview();
    return;
  }
  if (target instanceof HTMLSelectElement && target.matches("[data-add-section-type]")) {
    if (target.value) {
      addProfileSection(target.value);
      renderProfilePreview();
      target.value = "";
    }
    return;
  }
  if (target instanceof HTMLSelectElement && target.matches("[data-row-source]")) {
    const [sectionIndex, rowIndex] = String(target.dataset.rowSource || "").split("_").map(Number);
    const sections = collectProfileSectionsFromEditor() || [...(state.editingProfileSections || [])];
    const row = (sections[sectionIndex]?.rows || [])[rowIndex];
    if (row) {
      if (target.value === "fixed" && !("value" in row)) row.value = "";
      if (target.value === "field" && !("field" in row)) row.field = "color";
      if (target.value === "base_detail" && "field" in row) delete row.field;
      if (target.value === "ai") { row.slot = row.slot || autoAiSlotName(sectionIndex, "row"); row.hint = row.hint || ""; }
    }
    renderProfileSectionsEditor(sections);
    const details = document.querySelector(`[data-section-index="${sectionIndex}"]`);
    if (details) details.open = true;
    renderProfilePreview();
    return;
  }
  if (target instanceof HTMLSelectElement && target.id === "settings-release-bump") {
    suggestNextVersion("", target.value);
    return;
  }
  if (target instanceof HTMLSelectElement && target.id === "ai-connection-protocol") syncAiConnectionProtocolFields();
});

document.addEventListener("keydown", (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement && target.id === "base-add-size-input" && event.key === "Enter") {
    event.preventDefault();
    addBaseRecognizedSize(target.value);
    target.value = "";
    return;
  }
  if (!(target instanceof HTMLInputElement) || target.id !== "store-template-custom-size" || event.key !== "Enter") return;
  event.preventDefault();
  const size = target.value.trim().toUpperCase();
  if (!size) return;
  const selected = [...new Set([...selectedTemplateSizes(), size])];
  const overrides = collectSizePriceOverrides();
  renderSizeOptions(selected);
  renderSizePriceOverrides(overrides);
  renderDefaultSkuSizePriceOverrides(collectDefaultSkuSizePriceOverrides());
  target.value = "";
});

document.addEventListener("focusin", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLInputElement) || target.id !== "store-template-category-search") return;
  loadMarketplaceCategories(target.value).catch((err) => {
    $("store-template-category-picker-status").textContent = err.message;
  });
});

renderSizeOptions([]);
renderCnscSiteOrderDisplays();
initWatermarkPickers();

// 127.0.0.1:8788 上以前跑过别的本地应用（ilab-conjure-shell），它在浏览器里注册过
// service worker 且至今仍是 active：脚本早已 404，但它会接管本源的导航请求 —— API 短暂
// 不可用时会把旧应用的页面缓存顶上来，也会让排查失去线索。本项目不用 SW，直接注销。
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.getRegistrations()
    .then((registrations) => registrations.forEach((registration) => {
      registration.unregister().then((removed) => {
        if (removed) console.info(`[sw] 已注销残留 service worker：${registration.scope}`);
      });
    }))
    .catch(() => {});
}

// Two-phase boot: await the essentials so the first paint has header status,
// dashboard tiles, base products and store templates ready; then fire the rest
// in the background so switching to heavier views (副本 / 自动化 / 类目目录)
// is immediate. The user can interact as soon as essentials resolve.
bootstrapEssentials()
  .then(() => {
    bootstrapLocalFolderDropZone();
    // 顶栏更新提醒：不阻塞首屏，先读缓存状态；缓存过期时静默向 GitHub 查一次。
    refreshUpdateReminder().catch(() => {});
    window.setInterval(() => refreshUpdateReminder().catch(() => {}), UPDATE_REMINDER_POLL_MS);
    return refreshRest();
  })
  .catch((err) => {
    console.error(err);
    $("service-status").textContent = `初始化失败：${err.message}`;
  });
