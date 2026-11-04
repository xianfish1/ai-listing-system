'use strict';

const fs = require('fs');
const { chromium } = require('playwright');

const TEXT = Object.freeze({
  pending: '\u5f85\u53d1\u5e03\u5546\u54c1',
  cnscPending: 'CNSC\u5F85\u53D1\u5E03\u5546\u54C1',
  newListing: '\u65b0\u589e\u520a\u767b',
  save: '\u4fdd\u5b58',
  saveAndList: '\u4fdd\u5b58\u5e76\u520a\u767b',
  list: '\u520a\u767b',
  publish: '\u53d1\u5e03',
  submitReview: '\u63d0\u4ea4\u5ba1\u6838',
  storeAndCategory: '\u5e97\u94fa\u53ca\u7c7b\u76ee',
  storeName: '\u5e97\u94fa\u540d\u79f0',
  productCategory: '\u4ea7\u54c1\u7c7b\u76ee',
  chooseCategory: '\u9009\u62e9\u7c7b\u76ee',
  basicInfo: '\u57fa\u672c\u4fe1\u606f',
  title: '\u5546\u54c1\u6807\u9898',
  description: '\u5546\u54c1\u63cf\u8ff0',
  categoryAttributes: '\u5206\u7c7b\u5c5e\u6027',
  productMaterial: '\u5546\u54c1\u7d20\u6750',
  productImages: '\u4ea7\u54c1\u56fe',
  productVideo: '\u5546\u54c1\u89c6\u9891',
  addWatermark: '\u6dfb\u52a0\u6c34\u5370',
  sizeChart: '\u5c3a\u7801\u56fe\u8868',
  productImageMenu: '\u4ea7\u54c1\u56fe\u7247',
  networkImage: '\u7f51\u7edc\u56fe\u7247',
  uploadImage: '\u4e0a\u4f20\u56fe\u7247',
  submitImage: '\u63d0\u4ea4',
  referenceProductImage: '\u5f15\u7528\u4ea7\u54c1\u56fe',
  variantInfo: '\u53d8\u79cd\u4fe1\u606f',
  variant: '\u53d8\u79cd',
  variantProperty: '\u53d8\u79cd\u5c5e\u6027',
  addCustom: '\u6dfb\u52a0\u81ea\u5b9a\u4e49',
  customThumbnail: '\u81ea\u5b9a\u4e49\u7f29\u7565\u56fe',
  tradeInfo: '\u4ea4\u6613\u4fe1\u606f',
  discountActivity: '\u6298\u6263\u6d3b\u52a8',
  selectActivity: '\u9009\u62e9\u6d3b\u52a8',
  selectDiscountActivity: '\u9009\u62e9\u6298\u6263\u6d3b\u52a8',
  choose: '\u9009\u62e9',
  batchModify: '\u6279\u91cf\u4fee\u6539',
  percentOff: '%OFF',
  logisticsInfo: '\u7269\u6d41\u4fe1\u606f',
  confirm: '\u786e\u5b9a',
  cancel: '\u53d6\u6d88',
  autoPublishToSites: '\u4e0a\u67b6\u540e\u81ea\u52a8\u53d1\u5e03\u5230\u7ad9\u70b9\u5e97\u94fa',
  search: '\u641c\u7d22',
  savedSuccess: '\u520a\u767b\u4fe1\u606f\u4fdd\u5b58\u6210\u529f',
  closeThisPage: '\u5173\u95ed\u6b64\u9875\u9762',
  listingDraftBox: '\u520a\u767b\u8349\u7a3f\u7bb1',
  editDescriptionImages: '\u7f16\u8f91\u56fe\u7247',
  selectAllImages: '\u5168\u9009',
  resizeSection: '\u4fee\u6539\u5c3a\u5bf8',
  proportionalResize: '\u7b49\u6bd4\u4f8b\u8c03\u6574',
  smallEdgeResize: '\u56fe\u7247\u5c0f\u8fb9',
  changeImageSize: '\u66f4\u6539',
});

const PUBLISH_TEXTS = [TEXT.saveAndList, TEXT.list, TEXT.publish, TEXT.submitReview];
const CDP_DEFAULT = 'http://127.0.0.1:9223';
// Playwright 默认 30s 的连接预算对「已经开了几小时、DOM 很大的马帮刊登页」不够：
// 现场 2026-09-27 19:0x 实测 connectOverCDP 要 34s，30s 直接报
// 「browserType.connectOverCDP: Timeout 30000ms exceeded」——这是连接超时，不是页面问题。
// 放宽到 90s，让慢浏览器也能接上；失败仍然照原样终止。
const CDP_CONNECT_TIMEOUT_MS = 90000;
const RESULT_MARKER = 'MABANG_PLAYWRIGHT_RESULT ';
const EVENT_MARKER = 'MABANG_PLAYWRIGHT_EVENT ';
const ACTION_SELECTOR = 'button,[role="button"],a,.el-button,input[type="button"],input[type="submit"]';

// Maximum times the pre-save fill flow may be retried on a page-structure failure.
// A failed attempt closes the current detail tab so the retry re-opens "待发布 → 新增刊登",
// giving stuck dialogs / loading masks / stale frames a fresh start.
const MAX_PRE_SAVE_ATTEMPTS = 3;

// Errors that stem from page structure / timing and are worth reopening the form for.
// Anything not listed here is treated as terminal (e.g. auth, plan validation, publish
// guard) and fails immediately without consuming another attempt.
const RETRYABLE_ERRORS = new Set([
  'browser_context_missing',
  'category_segment_timeout',
  'category_path_segment_not_unique',
  'category_dialog_not_visible',
  'category_id_not_verified',
  'workspace_frame_not_unique',
  'mabang_detail_iframe_not_found',
  'exact_action_not_unique',
  'field_not_unique',
  'select_field_not_found',
  'select_value_not_verified',
  'select_option_not_found',
  'button_not_found_with_any_selector',
  'save_button_not_unique',
  'trade_info_table_not_unique',
  'discount_dialog_not_visible',
  'variant_axis_not_selected',
  'variant_axis_value_controls_not_unique',
  'variant_axis_value_not_added',
  'sku_weight_checkbox_not_unique',
  'page_structure_changed',
  'pre_save_validation_failed',
  'playwright_timeout',
  'product_image_upload_did_not_land',
  'product_image_slots_already_filled',
  'cnsc_site_picker_not_ready',
]);

class BlockedError extends Error {
  constructor(reason, details = {}) {
    super(reason);
    this.name = 'BlockedError';
    this.reason = reason;
    this.details = details;
  }
}

// 进度实时回报：worker 在任务计划里注入 runtime = {job_id, worker_id, lease_token, api_url}
// （见 automation_worker 注入 + storage.record_automation_job_progress）。没有这段上下文时
// 一个请求都不发 —— 手动干跑（.scratch 脚本）行为完全不变。上报是尽力而为的旁路：串行排队
// 保证顺序、单条 8s 超时、失败只降级记一行，绝不影响执行本身。
let progressRuntime = null;
let progressChain = Promise.resolve();
let progressFailures = 0;

function setProgressRuntime(runtime) {
  if (!runtime || !runtime.job_id || !runtime.lease_token) return;
  progressRuntime = {
    jobId: String(runtime.job_id),
    workerId: String(runtime.worker_id || ''),
    leaseToken: String(runtime.lease_token),
    apiUrl: String(runtime.api_url || process.env.MABANG_TASK_API_URL || 'http://127.0.0.1:8788').replace(/\/+$/, ''),
  };
}

function reportProgress(event, payload) {
  if (!progressRuntime) return;
  const body = JSON.stringify({
    worker_id: progressRuntime.workerId,
    lease_token: progressRuntime.leaseToken,
    event: { event, ...payload, at: new Date().toISOString() },
  });
  progressChain = progressChain
    .then(() => fetch(`${progressRuntime.apiUrl}/api/automation/jobs/${progressRuntime.jobId}/progress`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      signal: AbortSignal.timeout(8000),
    }))
    .then((response) => {
      if (!response.ok) progressFailures += 1;
    })
    .catch(() => {
      progressFailures += 1;
      if (progressFailures === 3) process.stderr.write('progress reporting degraded\n');
    });
}

function emit(event, data = {}) {
  process.stdout.write(EVENT_MARKER + JSON.stringify({ event, ...data, at: new Date().toISOString() }) + '\n');
  reportProgress(event, data);
}

// 取 CDP 浏览器的默认上下文。connectOverCDP 连到的 Chrome 只允许用 contexts()[0]
// （不能再 newContext），浏览器刚被重启/上下文被关掉时这里会是空的：
// 直接 newPage() 会抛 "Cannot read properties of undefined (reading 'newPage')"，
// 操作员完全看不懂。这里换成明确的可重试错误。
function requireBrowserContext(browser) {
  const context = browser && typeof browser.contexts === 'function' ? browser.contexts()[0] : null;
  if (!context) throw new BlockedError('browser_context_missing');
  return context;
}

function cleanText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

// 文件路径只能裁剪首尾空白，绝不能折叠中间的连续空格：NAS 素材里真实存在
// 「卫衣  1」「SKU-04-White  03.jpg」这种两个空格的目录名/文件名，走 cleanText
// 会被折成一个空格、指向不存在的路径，上传阶段直接 upload_file_missing 把整单
// 卡成 blocked（2026-10-23 现场任务 49d8ebc2）。
function cleanFilePath(value) {
  return String(value || '').trim();
}

// Mabang mixes Chinese labels and English display labels in category options,
// e.g. the payload carries "不 (No)" while the live select renders "No".
// Compare structured variants so we neither add a duplicate custom option nor
// report a valid selection as unverified.
function selectTextVariants(value) {
  const text = cleanText(value);
  if (!text) return [];
  const variants = [text];
  const withoutParentheses = cleanText(text.replace(/\s*\([^)]*\)\s*/g, ' '));
  if (withoutParentheses && !variants.includes(withoutParentheses)) variants.push(withoutParentheses);
  const beforeParentheses = cleanText(text.split('(')[0]);
  if (beforeParentheses && !variants.includes(beforeParentheses)) variants.push(beforeParentheses);
  const insideParentheses = text.match(/\(([^)]*)\)/)?.[1];
  const inside = cleanText(insideParentheses || '');
  if (inside && !variants.includes(inside)) variants.push(inside);
  return variants;
}

function selectTextMatches(actual, expected) {
  const actualVariants = selectTextVariants(actual).map((value) => value.toLocaleLowerCase());
  const expectedVariants = selectTextVariants(expected).map((value) => value.toLocaleLowerCase());
  return expectedVariants.some((target) => actualVariants.some((candidate) => candidate === target || candidate.includes(target) || target.includes(candidate)));
}

function selectionEvidenceMatches(actualTexts, target, optionClass = '', optionAriaSelected = '') {
  const texts = Array.isArray(actualTexts) ? actualTexts : [];
  return texts.some((selected) => selectTextMatches(selected, target))
    || String(optionClass || '').split(/\s+/).includes('selected')
    || String(optionAriaSelected || '').toLowerCase() === 'true';
}

// Mabang's description control is a contenteditable wangEditor. Playwright's
// fill() maps each source line break to exactly one block element, so "A\n\nB"
// lands as A / empty paragraph / B — i.e. one blank line, which is the spacing
// the operator writes into 「描述模板（固定描述）」 on purpose.
// 只规范换行符，不要再去压连续空行：早先那版会把空行折成单换行（"紧凑段落"），
// 结果是每次刊登都把模板里排版用的空行悄悄删掉。
function normalizeContentEditableText(value) {
  return String(value || '').replace(/\r\n?/g, '\n');
}

function compactText(value) {
  return String(value || '').replace(/\s+/g, '').trim();
}

// Stronger than cleanText for matching shop names: normalize NBSP, fold full-width
// ASCII to half-width, case-fold, and strip Mabang's selection/status suffixes
// (e.g. "（已选）"/"默认") that decorate the visible label on only one side.
function normalizeName(value) {
  let text = String(value || '')
    .replace(/ /g, ' ')
    .replace(/[！-～]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xfee0))
    .replace(/\s+/g, ' ')
    .trim()
    .toLocaleLowerCase();
  text = text.replace(/[（(]\s*(已选|默认|selected|default)\s*[)）]\s*$/g, '').trim();
  text = text.replace(/\s*(已选|默认)\s*$/g, '').trim();
  return text;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function parseArgs(argv) {
  const result = { cdpUrl: CDP_DEFAULT, stopBeforeSave: false, preserveDetailOnFailure: false, resumeFromStage: '', closeDetailPages: false };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === '--cdp-url' && argv[index + 1]) {
      result.cdpUrl = argv[index + 1];
      index += 1;
    }
    if (argv[index] === '--stop-before-save') result.stopBeforeSave = true;
    if (argv[index] === '--preserve-detail-on-failure') result.preserveDetailOnFailure = true;
    if (argv[index] === '--close-detail-pages') result.closeDetailPages = true;
    if (argv[index] === '--resume-from-stage' && argv[index + 1]) {
      result.resumeFromStage = argv[index + 1];
      index += 1;
    }
  }
  return result;
}

async function readTaskPlan() {
  let input = '';
  process.stdin.setEncoding('utf8');
  for await (const chunk of process.stdin) input += chunk;
  const task = JSON.parse(input || '{}');
// 任务计划里带上 runtime 时，执行过程中的 stage/attempt/warning 事件会实时回传到本地 API，
// 自动化面板因此能显示"正在执行到哪一步"。
setProgressRuntime(task.runtime);
  const safety = task.safety || {};
  if (
    !task.ok ||
    task.mode !== 'save_draft' ||
    safety.allow_save !== true ||
    safety.allow_publish !== false ||
    safety.allow_submit !== false ||
    !safety.save_token
  ) {
    throw new BlockedError('unsafe_or_incomplete_save_plan');
  }
  const actionTypes = (task.actions || []).map((action) => action.type);
  if (actionTypes[actionTypes.length - 1] !== 'save_draft') {
    throw new BlockedError('save_draft_must_be_the_final_action');
  }
  if (actionTypes.some((type) => ['publish', 'submit', 'save_and_publish'].includes(type))) {
    throw new BlockedError('publish_action_present');
  }
  return task;
}

function allPages(browser) {
  return browser.contexts().flatMap((context) => context.pages());
}

async function isVisible(locator) {
  return locator.isVisible().catch(() => false);
}

// Element-UI shows an .el-loading-mask while a dialog or panel loads its data.
// A lingering mask (e.g. the category picker) can sit on top of a button we are
// about to click and make Playwright wait for the full click timeout. Wait until
// no mask inside `root` is visible before proceeding.
async function waitForNoLoadingMask(root, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const masks = root.locator('.el-loading-mask');
    let visible = false;
    for (let index = 0, count = await masks.count().catch(() => 0); index < count; index += 1) {
      if (await isVisible(masks.nth(index))) {
        visible = true;
        break;
      }
    }
    if (!visible) return true;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return false;
}

async function exactVisibleLocator(frame, selector, label) {
  const matches = await exactVisibleLocators(frame, selector, label);
  if (matches.length !== 1) {
    throw new BlockedError('exact_action_not_unique', { label, count: matches.length });
  }
  return matches[0];
}

async function exactVisibleLocators(frame, selector, label) {
  const locator = frame.locator(selector);
  const matches = [];
  for (let index = 0, count = await locator.count(); index < count; index += 1) {
    const candidate = locator.nth(index);
    if (!(await isVisible(candidate))) continue;
    const text = compactText(await candidate.innerText().catch(() => candidate.inputValue().catch(() => '')));
    if (text === compactText(label)) matches.push(candidate);
  }
  return matches;
}

// Positional locators go stale when Vue re-renders between the scan and the
// click: .nth(i) then resolves to whatever took the old index and the click
// burns its whole budget on "element is not visible". Callers pass a rescan()
// that re-finds the target; every attempt clicks with a short budget so a
// re-render is absorbed instead of exhausting one long wait.
async function clickWithRescan(rescan, options = {}) {
  const attempts = options.attempts ?? 3;
  const timeoutMs = options.timeoutMs ?? 8000;
  let lastError = null;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const target = await rescan();
    if (!target) throw new BlockedError('exact_click_target_not_found');
    try {
      await target.click({ timeout: timeoutMs });
      return;
    } catch (error) {
      lastError = error;
      await sleep(150);
    }
  }
  throw lastError;
}

async function clickExact(frame, label, options = {}) {
  if (PUBLISH_TEXTS.includes(label)) throw new BlockedError('publish_click_refused');
  return clickWithRescan(() => exactVisibleLocator(frame, ACTION_SELECTOR, label), options);
}

async function activateTab(frame, label) {
  // Element-UI tabs (.el-tabs__item) are DIVs, not buttons, so they don't match
  // ACTION_SELECTOR. Click the tab header directly; if it's already active, do
  // nothing. Never treated as a publish action.
  const tabs = frame.locator('.el-tabs__item');
  for (let index = 0, count = await tabs.count().catch(() => 0); index < count; index += 1) {
    const tab = tabs.nth(index);
    if (!(await isVisible(tab))) continue;
    if (compactText(await tab.innerText().catch(() => '')) !== compactText(label)) continue;
    const active = await tab.evaluate((node) => node.classList.contains('is-active')).catch(() => false);
    if (!active) await tab.click();
    return true;
  }
  return false;
}

// 扫描一个页面自己的所有 frame，返回含该精确文案的 frame 列表。
async function scanFramesWithExactText(page, label) {
  const matches = [];
  for (const frame of page.frames()) {
    const count = await frame.getByText(label, { exact: true }).count().catch(() => 0);
    if (count) matches.push(frame);
  }
  return matches;
}

async function findFrameWithExactText(page, label) {
  // Scope the search to a single page's frames. The environment can hold several
  // identical Mabang tabs; searching every page would match the same label twice
  // and wrongly report workspace_frame_not_unique.
  const matches = await scanFramesWithExactText(page, label);
  if (matches.length !== 1) {
    throw new BlockedError('workspace_frame_not_unique', { label, count: matches.length });
  }
  return matches[0];
}

async function hasLoginOrVerification(page) {
  // Mabang keeps the login form in the DOM (hidden) after sign-in, so a mere
  // count() > 0 falsely reports "needs login". Only treat it as a login gate when
  // a login field is actually visible.
  const loginFields = page.locator('#account,#password,#account-btn');
  for (let index = 0, count = await loginFields.count().catch(() => 0); index < count; index += 1) {
    if (await isVisible(loginFields.nth(index))) return true;
  }
  // Signed-out gotoApp can land on the marketing homepage where the login form is
  // not in the DOM at all; a visible standalone 「登录」 entry there (and no loaded
  // workspace content) is equivalent to a login gate.
  if (/mabangerp\.com/i.test(page.url())) {
    const loginEntry = page.getByText('\u767b\u5f55', { exact: true }).first();
    if ((await isVisible(loginEntry)) && !(await hasWorkspaceMarker(page))) return true;
  }
  for (const frame of page.frames()) {
    // 只扫描马帮自己域名的框架；第三方 iframe（客服组件等）的文案可能含
    // “验证/captcha”等字样，会造成登录拦截误报。
    if (!/mabangerp\.com/i.test(frame.url()) && frame !== page.mainFrame()) continue;
    const body = cleanText(await frame.locator('body').innerText({ timeout: 1000 }).catch(() => ''));
    if (/\u5b89\u5168\u9a8c\u8bc1|\u6ed1\u5757|captcha|verification/i.test(body)) return true;  // 安全验证|滑块|captcha|verification
  }
  return false;
}

// Non-throwing counterpart of findFrameWithExactText(TEXT.pending): true when the
// workspace app actually rendered its 待发布商品 tab.
async function hasWorkspaceMarker(page) {
  for (const frame of page.frames()) {
    const count = await frame.getByText(TEXT.pending, { exact: true }).count().catch(() => 0);
    if (count) return true;
  }
  return false;
}

async function isDetailFormFrame(frame) {
  // The listing page always has an #iframeContent shell whose inner frame shows
  // the pending-products LIST, not the new-listing FORM. Only treat a frame as the
  // detail form when it exposes the form's own markers, so we never skip the
  // "待发布商品 → 新增刊登" navigation that actually opens the form.
  if (!frame) return false;
  if (/productDetailShopee/i.test(frame.url())) return true;
  const sections = await frame.getByText(TEXT.basicInfo, { exact: true }).count().catch(() => 0);
  const storeSection = await frame.getByText(TEXT.storeAndCategory, { exact: true }).count().catch(() => 0);
  return Boolean(sections && storeSection);
}

async function detailFrameFromPage(page) {
  const outer = page.locator('#iframeContent');
  if (await outer.count().catch(() => 0)) {
    const handle = await outer.elementHandle().catch(() => null);
    const content = handle ? await handle.contentFrame().catch(() => null) : null;
    if (content && (await isDetailFormFrame(content))) return content;
  }
  for (const frame of page.frames()) {
    if (await isDetailFormFrame(frame)) return frame;
  }
  return null;
}

async function waitForDetailFrame(browser, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    for (const page of allPages(browser)) {
      const frame = await detailFrameFromPage(page);
      if (frame) return { page, frame };
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  throw new BlockedError('mabang_detail_iframe_not_found');
}

function detailPageMatchesTask(detailUrl, task = {}) {
  const url = String(detailUrl || '');
  if (!/productDetailShopee/i.test(url)) return false;
  const globalFlag = url.match(/[?&]global=([^&#]+)/i);
  const actualGlobal = globalFlag ? decodeURIComponent(globalFlag[1]).toLowerCase() : '';
  const isGlobalPage = ['1', 'true', 'yes'].includes(actualGlobal);
  const wantsGlobal = String(task?.store?.listing_mode || '') === 'cnsc_global';
  return isGlobalPage === wantsGlobal;
}

async function openListingWorkspace(browser, task) {
  for (const candidate of allPages(browser)) {
    const existingDetail = await detailFrameFromPage(candidate);
    if (!existingDetail) continue;
    // A detail page is stateful and its route encodes whether it is the CNSC
    // (global=1) or local listing flow. Reusing a page from the other flow
    // leaves the runner on an empty 店铺/类目 form and makes the task appear
    // stuck before it can select the requested category.
    if (detailPageMatchesTask(existingDetail.url(), task)) {
      return { page: candidate, frame: existingDetail };
    }
    await candidate.close({ runBeforeUnload: false }).catch(() => {});
  }
  const existing = allPages(browser).find((page) => /mabangerp\.com/i.test(page.url()));
  const page = existing || (await requireBrowserContext(browser).newPage());
  if (await hasLoginOrVerification(page)) throw new BlockedError('needs_manual_login_or_verification');
  const currentDetail = await detailFrameFromPage(page);
  if (currentDetail) return { page, frame: currentDetail };

  if (!/mabangerp\.com/i.test(page.url())) {
    await page.goto(task.source_url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  }
  if (await hasLoginOrVerification(page)) throw new BlockedError('needs_manual_login_or_verification');

  const pendingFrame = await findFrameWithExactText(page, TEXT.pending);
  if (!(await activateTab(pendingFrame, TEXT.pending))) {
    await clickExact(pendingFrame, TEXT.pending);
  }
  const isGlobal = String((task.store || {}).listing_mode || '') === 'cnsc_global';
  // 马帮真机现场（2026-09-27，CDP 9223，本土 HN_POD 任务 71181e90）：
  //   待发布商品 → 内层 草稿箱/定时任务/刊登中/刊登失败/刊登成功 + 「新增刊登」
  //   站点商品   → 内层 正在销售/已售完/已下架/已禁卖/疑似删除，**没有**「新增刊登」
  // 「站点商品」是线上商品库，不是本土待发布列表。本土作业切过去只会拿到
  // count=0 的 workspace_frame_not_unique（3 次 attempt 全部 3~4 秒即失败）。
  // 本土作业因此停在默认的 待发布商品 上，只有跨境 CNSC 才切 CNSC待发布商品。
  if (isGlobal) {
    const cnscTab = pendingFrame.getByRole('tab', { name: TEXT.cnscPending, exact: true });
    if (await cnscTab.count()) {
      await cnscTab.click();
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  // 「新增刊登」是列表页渲染完成后才进 DOM 的：冷启动或刚切完页签时单次
  // getByText 会把它读成 0。这里按截止时间轮询，按钮出现后再点。
  const newListingDeadline = Date.now() + 15000;
  let newListingFrame = null;
  let newListingFrameCount = 0;
  while (Date.now() < newListingDeadline) {
    const matches = await scanFramesWithExactText(page, TEXT.newListing);
    newListingFrameCount = matches.length;
    if (matches.length === 1) {
      newListingFrame = matches[0];
      break;
    }
    if (matches.length > 1) {
      throw new BlockedError('workspace_frame_not_unique', { label: TEXT.newListing, count: matches.length });
    }
    await sleep(300);
  }
  if (!newListingFrame) {
    throw new BlockedError('workspace_frame_not_unique', { label: TEXT.newListing, count: newListingFrameCount });
  }
  await clickExact(newListingFrame, TEXT.newListing);
  return waitForDetailFrame(browser);
}

function actionableCNSCTargets(task) {
  return ((task.store || {}).target_site_stores || []).filter((target) => String(target.site_code || '').trim().toUpperCase() !== 'SIP');
}

function automaticCNSCSIPCandidates(candidates, task) {
  const sipTargets = ((task.store || {}).target_site_stores || [])
    .filter((target) => String(target.site_code || '').trim().toUpperCase() === 'SIP');
  const automatic = new Set();
  for (const target of sipTargets) {
    try {
      automatic.add(resolveCNSCTargetCandidate(candidates, target));
    } catch (error) {
      // SIP is selected by Mabang, not by this runner. Only whitelist it when its
      // configured identity can be resolved uniquely; otherwise fail closed if it
      // is actually selected as an unexpected shop.
      if (!(error instanceof BlockedError)) throw error;
    }
  }
  return automatic;
}

async function openCNSCSitePicker(frame) {
  const picker = frame.locator('#shops-wap');
  if (await picker.count() !== 1) throw new BlockedError('cnsc_site_picker_not_found');
  const box = picker.locator('.shop-box');
  if (await box.count() !== 1) throw new BlockedError('cnsc_site_picker_trigger_not_found');
  const hasVisibleOptions = async () => {
    const siteGroups = picker.locator('.site');
    const groupCount = await siteGroups.count().catch(() => 0);
    let visibleGroup = false;
    for (let index = 0; index < groupCount; index += 1) {
      if (await siteGroups.nth(index).isVisible().catch(() => false)) {
        visibleGroup = true;
        break;
      }
    }
    let shopCount = await picker.locator('label.shops-checkbox').count().catch(() => 0);
    let legacyCount = await picker.locator('label.el-radio').count().catch(() => 0);
    // Some Playwright wrappers only resolve descendant selectors after the
    // parent group is materialized. Check each visible `.site` as well.
    if (visibleGroup && (shopCount === 0 || legacyCount === 0)) {
      for (let index = 0; index < groupCount; index += 1) {
        const group = siteGroups.nth(index);
        if (!(await group.isVisible().catch(() => false))) continue;
        shopCount = Math.max(shopCount, await group.locator('label.shops-checkbox').count().catch(() => 0));
        legacyCount = Math.max(legacyCount, await group.locator('label.el-radio').count().catch(() => 0));
      }
    }
    return visibleGroup && (shopCount > 0 || legacyCount > 0);
  };
  if (!(await hasVisibleOptions())) await box.click();
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (await hasVisibleOptions()) return picker;
    await sleep(100);
  }
  throw new BlockedError('cnsc_site_picker_not_ready', {
    timeout_ms: 5000,
    site_groups: await picker.locator('.site').count().catch(() => 0),
    shop_labels: await picker.locator('label.shops-checkbox').count().catch(() => 0),
  });
}

async function collectCNSCSitePickerCandidates(picker) {
  const candidates = [];
  const siteAliases = { '\u53f0\u6e7e': 'TW', '\u83f2\u5f8b\u5bbe': 'PH', '\u65b0\u52a0\u5761': 'SG', '\u6cf0\u56fd': 'TH', '\u9a6c\u6765\u897f\u4e9a': 'MY', '\u58a8\u897f\u54e5': 'MX', '\u8d8a\u5357': 'VN', '\u5df4\u897f': 'BR', '\u54e5\u4f26\u6bd4\u4e9a': 'CO', '\u667a\u5229': 'CL', SIP: 'SIP' };
  const SITE_CODE_PATTERN = /^(TW|PH|SG|TH|MY|MX|VN|BR|CO|CL|SIP)$/;
  const appendCandidate = async ({ siteCode = '', siteLabel, label, input, siteInput = null }) => {
    // Collect present-but-collapsed/offscreen rows too: matching only needs the
    // identity, and clicking (which does need visibility) re-collects and expands
    // later. Record visibility for diagnostics instead of dropping the candidate.
    let labelText = cleanText(await label.innerText().catch(() => ''));
    if (!labelText) labelText = cleanText(await label.evaluate((el) => el.textContent || '').catch(() => ''));
    const visible = await label.isVisible().catch(() => true);
    const storeMetadata = label.locator(
      'xpath=ancestor::*[@data-store-id or @data-shop-id or @data-store-name or @data-shop-name or @data-value or @data-key][1]',
    );
    const genericMetadata = label.locator('xpath=ancestor::*[@data-id or @data-name][1]');
    const metadataNodes = [input, label, storeMetadata, genericMetadata];
    const readMetadata = async (attribute) => {
      const values = [];
      for (const node of metadataNodes) {
        // The metadata ancestors usually do not exist in Mabang's real picker DOM.
        // getAttribute auto-waits ~30s for a missing element before throwing, which
        // cost ~1min per shop (16 shops ≫ the job's 8min budget); count() is instant.
        if (!await node.count().catch(() => 0)) continue;
        const value = await node.getAttribute(attribute).catch(() => '');
        if (value) values.push(String(value).trim());
      }
      return values;
    };
    const [values, storeIds, storeNames] = await Promise.all([
      readMetadata('value'),
      Promise.all(['data-store-id', 'data-shop-id', 'data-id', 'data-value', 'data-key', 'id'].map((attribute) => readMetadata(attribute)))
        .then((groups) => groups.flat()),
      Promise.all(['data-store-name', 'data-shop-name', 'data-name', 'title', 'aria-label'].map((attribute) => readMetadata(attribute)))
        .then((groups) => groups.flat()),
    ]);
    const identities = [...new Set([...values, ...storeIds].filter(Boolean))];
    const uniqueStoreIds = [...new Set(storeIds.filter(Boolean))];
    const names = [...new Set([labelText, ...storeNames].filter(Boolean))];
    candidates.push({
      siteCode,
      siteLabel,
      siteInput,
      label,
      input,
      labelText,
      visible,
      identities,
      storeIds: uniqueStoreIds,
      storeId: uniqueStoreIds[0] || '',
      names,
      storeName: names[0] || '',
    });
  };

  // Real CNSC renders one checkbox per shop under each `.site` group. The
  // country-level checkbox is not a shop identity, so only collect `.shops-checkbox`
  // labels (with a guarded generic fallback for older markup).
  const resolveShopLabels = (group) => {
    const primary = group.locator('label.shops-checkbox');
    return primary;
  };
  const siteGroups = picker.locator('.site');
  for (let siteIndex = 0; siteIndex < await siteGroups.count(); siteIndex += 1) {
    const siteGroup = siteGroups.nth(siteIndex);
    if (!(await siteGroup.isVisible().catch(() => false))) continue;
    const titleText = cleanText(await siteGroup.innerText().catch(() => ''));
    const rawCode = String(
      await siteGroup.getAttribute('data-site-code').catch(() => '')
      || await siteGroup.getAttribute('data-site').catch(() => '')
      || await siteGroup.getAttribute('data-country').catch(() => '')
      || await siteGroup.getAttribute('data-code').catch(() => '')
      || await siteGroup.getAttribute('data-key').catch(() => '')
      || await siteGroup.getAttribute('aria-label').catch(() => '')
      || '',
    ).trim().toUpperCase();
    // An undeliverable site code no longer drops the whole group: keep siteCode
    // empty and still collect its shops so they stay matchable by id/name.
    const siteCode = SITE_CODE_PATTERN.test(rawCode)
      ? rawCode
      : Object.entries(siteAliases).find(([name]) => titleText.includes(name))?.[1] || '';
    let shopLabels = resolveShopLabels(siteGroup);
    if (await shopLabels.count() === 0) shopLabels = siteGroup.locator('label.el-checkbox');
    // Expand a collapsed accordion group so its shop rows render before reading.
    // Best-effort: target header/arrow toggles only (never a checkbox input) so we
    // cannot accidentally tick the country-level "select all" box.
    const firstVisible = (await shopLabels.count()) > 0
      ? await shopLabels.first().isVisible().catch(() => false)
      : false;
    if ((await shopLabels.count()) === 0 || !firstVisible) {
      const toggle = siteGroup
        .locator('.el-collapse-item__header, .el-collapse-item__arrow, .site-title, .site__title, .site-name, .site-header, .site-arrow, .arrow, .caret')
        .first();
      if ((await toggle.count().catch(() => 0)) > 0) {
        await toggle.click({ timeout: 2000 }).catch(() => {});
        await sleep(150);
        let refreshed = resolveShopLabels(siteGroup);
        if ((await refreshed.count()) === 0) refreshed = siteGroup.locator('label.el-checkbox');
        shopLabels = refreshed;
      }
    }
    const siteInput = siteGroup.locator('.title-wap-checkbox input[type="checkbox"]').first();
    for (let shopIndex = 0; shopIndex < await shopLabels.count(); shopIndex += 1) {
      const label = shopLabels.nth(shopIndex);
      const className = String(await label.getAttribute('class').catch(() => '') || '');
      if (className.split(/\s+/).includes('title-wap-checkbox')) continue;
      const input = label.locator('input[type="checkbox"]').first();
      if (await input.count() !== 1) continue;
      await appendCandidate({ siteCode, siteLabel: siteGroup, label, input });
    }
  }

  // Compatibility path for an older Mabang build that rendered store radios.
  if (candidates.length === 0) {
    const labels = picker.locator('label.el-checkbox');
    for (let index = 0; index < await labels.count(); index += 1) {
      const siteLabel = labels.nth(index);
      if (!(await siteLabel.isVisible())) continue;
      const siteInput = siteLabel.locator('input[type="checkbox"]').first();
      const siteText = cleanText(await siteLabel.innerText().catch(() => ''));
      const siteMetadata = siteLabel.locator(
        'xpath=ancestor::*[@data-site-code or @data-site or @data-country][1]',
      );
      // Same missing-ancestor trap as readMetadata above: guard with count().
      const siteMetadataPresent = await siteMetadata.count().catch(() => 0);
      const rawCode = String(
        await siteInput.getAttribute('data-site-code').catch(() => '')
        || await siteLabel.getAttribute('data-site-code').catch(() => '')
        || (siteMetadataPresent
          ? await siteMetadata.getAttribute('data-site-code').catch(() => '')
            || await siteMetadata.getAttribute('data-site').catch(() => '')
            || await siteMetadata.getAttribute('data-country').catch(() => '')
          : '')
        || await siteInput.getAttribute('value').catch(() => '')
        || '',
      ).trim().toUpperCase();
      const siteCode = SITE_CODE_PATTERN.test(rawCode)
        ? rawCode
        : Object.entries(siteAliases).find(([name]) => siteText.includes(name))?.[1] || '';
      if (!siteCode) continue;
      const group = siteLabel.locator('xpath=ancestor::*[.//label[contains(@class,"el-radio")]][1]');
      if (await group.count() !== 1) continue;
      const radios = group.locator('label.el-radio');
      for (let radioIndex = 0; radioIndex < await radios.count(); radioIndex += 1) {
        const label = radios.nth(radioIndex);
        const input = label.locator('input[type="radio"]').first();
        if (await input.count() !== 1) continue;
        await appendCandidate({ siteCode, siteLabel, label, input, siteInput });
      }
    }
  }
  return candidates;
}

// Best-effort: attach the live picker DOM to a BlockedError so a CNSC site-select
// failure is diagnosable from the job result. Never masks the real block.
async function attachPickerSnapshot(picker, error) {
  if (!(error instanceof BlockedError)) return error;
  if (error.details && error.details.picker_snapshot) return error;
  try {
    const html = await picker.evaluate((el) => el.outerHTML).catch(() => '');
    if (html) error.details = { ...(error.details || {}), picker_snapshot: String(html).slice(0, 20000) };
  } catch (_) {
    // diagnostics are best-effort; swallow so the original reason propagates
  }
  return error;
}

function resolveCNSCTargetCandidate(candidates, target) {
  const wantedSite = String(target.site_code || target.country || '').trim().toUpperCase();
  // Site-scope by exact code, but also let candidates whose site code could not be
  // derived through (siteCode === ''): a present shop must stay matchable by
  // id/name even when its `.site` group carried no recognizable country tag.
  // Cross-site same-name shops still fail closed on ambiguity below.
  const scoped = wantedSite
    ? candidates.filter((candidate) => {
      const code = String(candidate.siteCode || '').toUpperCase();
      return code === wantedSite || code === '';
    })
    : candidates;
  const wantedId = String(target.store_id || target.mabang_store_id || '').trim();
  const wanted = String(target.store_name || target.name || '').trim();
  const idMatches = wantedId
    ? scoped.filter((candidate) => [
      ...(candidate.identities || []),
      ...(candidate.storeIds || []),
      candidate.storeId,
    ].map((value) => String(value || '').trim()).includes(wantedId))
    : [];
  const exactName = normalizeName(wanted);
  const candidateNames = (candidate) => [candidate.labelText, candidate.storeName, ...(candidate.names || [])]
    .map((value) => normalizeName(value))
    .filter(Boolean);
  const exactNameMatches = wanted
    ? scoped.filter((candidate) => candidateNames(candidate).some((name) => name === exactName))
    : [];
  // Mabang may append selection/status text to the visible shop label. Only use
  // this relaxed comparison within the already site-scoped list; duplicates still
  // fail closed below instead of silently choosing a shop.
  // Prefix kin: sibling shops often differ by one character (configured
  // 海宁店 vs on-page 海宁东店/海宁西店). They join the fuzzy tier so several
  // hits surface as cnsc_target_site_ambiguous (operator refines the name)
  // instead of a misleading cnsc_target_site_not_found.
  const sharesDistinguishingPrefix = (left, right) => {
    let common = 0;
    while (common < left.length && common < right.length && left[common] === right[common]) common += 1;
    // The shared prefix needs real distinguishing weight: at least 2 characters
    // AND more than half of the shorter side, so shops that merely share a
    // country tag (ph 一号店/二号店) stay unrelated.
    return common >= 2 && common * 2 >= Math.min(left.length, right.length);
  };
  const fuzzyNameMatches = wanted
    ? scoped.filter((candidate) => candidateNames(candidate).some(
      (name) => name.includes(exactName) || exactName.includes(name) || sharesDistinguishingPrefix(name, exactName),
    ))
    : [];
  const matches = idMatches.length ? idMatches : (exactNameMatches.length ? exactNameMatches : fuzzyNameMatches);
  const summarize = (list) => list.map((candidate) => ({
    site_code: candidate.siteCode || '',
    label: candidate.labelText || '',
    visible: candidate.visible !== false,
    identities: [...new Set([...(candidate.identities || []), ...(candidate.storeIds || []), candidate.storeId].filter(Boolean))],
    names: candidateNames(candidate),
  }));
  // `available` stays scoped for back-compat; `all_candidates` exposes the FULL
  // picker scrape so an operator can see a shop that was collected but tagged
  // under a different/empty site code (the usual cause of a false "not_found").
  const available = summarize(scoped);
  const all_candidates = summarize(candidates);
  if (!matches.length) {
    throw new BlockedError('cnsc_target_site_not_found', {
      store_id: wantedId,
      store_name: wanted,
      site_code: target.site_code || '',
      available,
      all_candidates,
    });
  }
  if (matches.length !== 1) {
    throw new BlockedError('cnsc_target_site_ambiguous', {
      store_id: wantedId,
      store_name: wanted,
      site_code: target.site_code || '',
      count: matches.length,
      available,
      all_candidates,
    });
  }
  return matches[0];
}

function discountDisplayTolerance(value) {
  const text = String(value ?? '').trim();
  const decimals = text.includes('.') ? text.split('.')[1].length : 0;
  return 0.5 * (10 ** -decimals) + Number.EPSILON;
}

function buildCNSCSitePricePlan(task) {
  if (String(task.store?.listing_mode || '') !== 'cnsc_global') return [];
  const defaultPercent = Number(task.store?.default_site_discount_percent ?? task.cnsc?.default_site_discount_percent ?? 40);
  const skus = Array.isArray(task.sku_grid?.skus) ? task.sku_grid.skus : [];
  return actionableCNSCTargets(task).map((target) => {
    const siteCode = String(target.site_code || target.country || '').trim().toUpperCase();
    const discountPercent = String(target.discount_percent ?? '').trim() ? Number(target.discount_percent) : defaultPercent;
    if (!siteCode || !String(target.mabang_discount_id || '').trim()) throw new BlockedError('cnsc_site_discount_identity_missing', { site_code: siteCode });
    if (!Number.isFinite(discountPercent) || discountPercent <= 0 || discountPercent >= 100) throw new BlockedError('cnsc_site_discount_percent_invalid', { site_code: siteCode, discount_percent: discountPercent });
    const rows = skus.map((sku) => {
      const prices = (sku.site_price_matrix || {})[siteCode] || {};
      const original = Number(prices.original_price);
      const sale = Number(prices.sale_price);
      if (!Number.isFinite(original) || original <= 0 || !Number.isFinite(sale) || sale <= 0 || sale > original) {
        throw new BlockedError('cnsc_site_price_missing', { site_code: siteCode, seller_sku: sku.sku_code || '' });
      }
      return {
        seller_sku: String(sku.sku_code || ''),
        color: String(sku.color_code || sku.color_cn || ''),
        size: String(sku.size || ''),
        original_price: original,
        sale_price: sale,
      };
    });
    return {
      site_code: siteCode,
      store_id: String(target.store_id || target.mabang_store_id || ''),
      store_name: String(target.store_name || target.name || ''),
      mabang_discount_id: String(target.mabang_discount_id || ''),
      discount_name: String(target.discount_name || target.discount_label || ''),
      discount_percent: discountPercent,
      rows,
    };
  });
}

function orderCNSCSiteDiscountPlan(pageSiteCodes, plan) {
  const normalizedCodes = [...new Set((pageSiteCodes || []).map((code) => String(code || '').trim().toUpperCase()).filter(Boolean))];
  const bySite = new Map();
  for (const site of plan || []) {
    const code = String(site.site_code || '').trim().toUpperCase();
    if (!code || bySite.has(code)) throw new BlockedError('cnsc_site_discount_header_site_mismatch', { site_code: code, reason: 'plan_site_not_unique' });
    bySite.set(code, site);
  }
  const ordered = normalizedCodes.filter((code) => bySite.has(code)).map((code) => bySite.get(code));
  if (ordered.length !== bySite.size) {
    throw new BlockedError('cnsc_site_discount_header_site_mismatch', {
      page_site_codes: normalizedCodes,
      missing_site_codes: [...bySite.keys()].filter((code) => !normalizedCodes.includes(code)),
    });
  }
  return ordered;
}

function validateCNSCSitePriceReadback(snapshot, plan, targets) {
  const mismatches = [];
  let rowCount = 0;
  for (const expectedSite of plan || []) {
    const actualSites = (snapshot?.sites || []).filter((site) => String(site.site_code || '').toUpperCase() === expectedSite.site_code);
    if (actualSites.length !== 1) {
      mismatches.push({ site_code: expectedSite.site_code, reason: 'site_not_unique' });
      continue;
    }
    const actualSite = actualSites[0];
    const target = (targets || []).find((item) => String(item.site_code || '').toUpperCase() === expectedSite.site_code) || {};
    const actualActivityId = String(actualSite.activity_id || '');
    const expectedActivityId = String(target.mabang_discount_id || expectedSite.mabang_discount_id || '');
    const expectedActivityName = cleanText(target.discount_name || target.discount_label || expectedSite.discount_name || '');
    const actualActivityName = cleanText(actualSite.activity_name || '');
    const activityMatches = actualActivityId
      ? actualActivityId === expectedActivityId
      : Boolean(expectedActivityName && actualActivityName.includes(expectedActivityName));
    if (!activityMatches) {
      mismatches.push({ site_code: expectedSite.site_code, reason: 'activity_mismatch' });
    }
    if ((actualSite.rows || []).length !== expectedSite.rows.length) {
      mismatches.push({ site_code: expectedSite.site_code, reason: 'row_count_mismatch' });
      continue;
    }
    for (let index = 0; index < expectedSite.rows.length; index += 1) {
      const expected = expectedSite.rows[index];
      const actual = (actualSite.rows || []).find((row) => String(row.seller_sku || '') === expected.seller_sku) || actualSite.rows[index] || {};
      rowCount += 1;
      const fields = [];
      if (String(actual.seller_sku || '') !== expected.seller_sku) fields.push('seller_sku');
      if (Math.abs(Number(actual.original_price) - expected.original_price) > discountDisplayTolerance(expected.original_price)) fields.push('original_price');
      if (Math.abs(Number(actual.sale_price) - expected.sale_price) > discountDisplayTolerance(expected.sale_price)) fields.push('sale_price');
      // No discount_percent comparison: plan sale prices are display-rounded, so a
      // percent re-derived from (original, sale) deviates from the plan percent by
      // up to ±0.5 — the exact original+sale pair above is the stronger check.
      if (fields.length) mismatches.push({ site_code: expectedSite.site_code, seller_sku: expected.seller_sku, fields });
    }
  }
  return mismatches.length
    ? { ok: false, reason: 'cnsc_site_price_mismatch', sites: (plan || []).length, rows: rowCount, mismatches }
    : { ok: true, sites: (plan || []).length, rows: rowCount, mismatches: [] };
}

async function assertExactCNSCTargetSites(candidates, targets, automaticCandidates = new Set()) {
  const expected = new Set();
  for (const target of targets) {
    const matched = resolveCNSCTargetCandidate(candidates, target);
    const inputCount = await matched.input.count().catch(() => 0);
    if (inputCount !== 1 || !(await matched.input.isChecked().catch(() => false))) {
      throw new BlockedError('cnsc_target_site_not_selected', { store_id: target.store_id || target.mabang_store_id || '', store_name: target.store_name || target.name || '', site_code: target.site_code || '' });
    }
    expected.add(matched);
  }
  const selected = [];
  for (const candidate of candidates) {
    if (await candidate.input.isChecked().catch(() => false)) selected.push(candidate);
  }
  const unexpected = selected.filter((candidate) => !expected.has(candidate) && !automaticCandidates.has(candidate));
  if (unexpected.length) {
    throw new BlockedError('cnsc_unexpected_target_site_selected', { selected: selected.map((candidate) => candidate.labelText), expected: targets.length });
  }
  return selected.filter((candidate) => expected.has(candidate));
}

// Vue replaces shop rows when the picker loads or when a checkbox changes. A
// locator captured before that render can remain visible during collection but
// resolve to zero nodes by the time click() runs. Re-collect and resolve by the
// stable store identity after every failed click, then verify the new DOM state.
async function clickCNSCTargetCandidate(picker, candidates, target) {
  let current = candidates;
  let lastError = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const matched = resolveCNSCTargetCandidate(current, target);
      if (await matched.input.isChecked().catch(() => false)) return current;
      await waitForNoLoadingMask(picker, 5000);
      await matched.label.click({ timeout: 5000 });

      // The click itself may trigger another Vue render, so validate against a
      // freshly collected candidate instead of the pre-click locator.
      await sleep(100);
      const refreshed = await collectCNSCSitePickerCandidates(picker);
      const selected = resolveCNSCTargetCandidate(refreshed, target);
      if (await selected.input.isChecked().catch(() => false)) return refreshed;
      lastError = new BlockedError('cnsc_target_site_not_selected', {
        store_id: target.store_id || target.mabang_store_id || '',
        store_name: target.store_name || target.name || '',
        site_code: target.site_code || '',
      });
      current = refreshed;
    } catch (error) {
      lastError = error;
      await sleep(100);
      current = await collectCNSCSitePickerCandidates(picker).catch(() => current);
    }
  }
  throw await attachPickerSnapshot(picker, lastError || new BlockedError('cnsc_target_site_not_selected', {
    store_id: target.store_id || target.mabang_store_id || '',
    store_name: target.store_name || target.name || '',
    site_code: target.site_code || '',
  }));
}

async function closeCNSCSitePicker(picker) {
  const confirm = picker.getByRole('button', { name: TEXT.confirm, exact: true });
  if (await confirm.count() !== 1) throw new BlockedError('cnsc_site_picker_confirm_not_found');
  await confirm.click();
}

async function selectCNSCTargetSites(frame, task) {
  if ((task.store || {}).listing_mode !== 'cnsc_global') return { skipped: true };
  const targets = actionableCNSCTargets(task);
  if (!targets.length) throw new BlockedError('cnsc_target_sites_missing');
  const picker = await openCNSCSitePicker(frame);
  try {
    let candidates = await collectCNSCSitePickerCandidates(picker);
    emit('stage', { stage: 'select_cnsc_target_sites', ok: true, method: 'picker_open', candidates: candidates.length });
    for (const target of targets) {
      const matched = resolveCNSCTargetCandidate(candidates, target);
      if (matched.siteInput && !(await matched.siteInput.isChecked().catch(() => false))) await matched.siteLabel.click();
      if (!(await matched.input.isChecked().catch(() => false))) {
        candidates = await clickCNSCTargetCandidate(picker, candidates, target);
      }
      emit('stage', { stage: 'select_cnsc_target_sites', ok: true, method: 'target_selected', site_code: target.site_code, store_name: target.store_name || target.name || '' });
    }
    const selected = await assertExactCNSCTargetSites(candidates, targets, automaticCNSCSIPCandidates(candidates, task));
    await closeCNSCSitePicker(picker);
    emit('stage', { stage: 'select_cnsc_target_sites', ok: true, method: 'confirmed', selected: selected.length });
    return { selected: selected.map((candidate) => candidate.storeIds?.[0] || candidate.identities[0] || candidate.labelText) };
  } catch (error) {
    throw await attachPickerSnapshot(picker, error);
  }
}

async function verifyCNSCTargetSites(frame, task) {
  if ((task.store || {}).listing_mode !== 'cnsc_global') return { skipped: true, selected: [] };
  const targets = actionableCNSCTargets(task);
  if (!targets.length) throw new BlockedError('cnsc_target_sites_missing');
  const picker = await openCNSCSitePicker(frame);
  try {
    const candidates = await collectCNSCSitePickerCandidates(picker);
    const selected = await assertExactCNSCTargetSites(candidates, targets, automaticCNSCSIPCandidates(candidates, task));
    await closeCNSCSitePicker(picker);
    return { selected: selected.map((candidate) => candidate.storeIds?.[0] || candidate.identities[0] || candidate.labelText) };
  } catch (error) {
    throw await attachPickerSnapshot(picker, error);
  }
}

async function findCNSCSitePriceTable(frame, plan) {
  const tables = frame.locator('.vxe-table:visible').filter({ hasText: TEXT.discountActivity });
  const matches = [];
  for (let index = 0; index < await tables.count(); index += 1) {
    const table = tables.nth(index);
    const text = cleanText(await table.innerText().catch(() => ''));
    if ((plan || []).every((site) => new RegExp(`(^|\\s)${site.site_code}(\\s|$)`).test(text))) matches.push(table);
  }
  if (matches.length !== 1) throw new BlockedError('cnsc_site_price_table_not_unique', { count: matches.length });
  return matches[0];
}

// The site price matrix is a click-to-edit vxe-table: cells render text until
// clicked (editConfig trigger:'click'), so per-cell Playwright edits would cost
// hundreds of round-trips. The per-SKU values live in row.diff_info
// [{site, price, discountType, discountPrice, discountPercent}] — the same model
// the save payload serializes — so write it directly. discountType 1 is the fixed
// discounted price (verified on the real page: typing into a discount cell lands
// in discountPrice with discountType 1 and the row renders the sale price).
async function writeCNSCSitePricesByModel(table, plan) {
  const result = await table.evaluate((element, sites) => {
    const vm = element.__vue__ || (element.__vueParentComponent && element.__vueParentComponent.proxy);
    const rows = vm && (vm.getTableData ? vm.getTableData().fullData : vm.tableData);
    if (!Array.isArray(rows) || !rows.length) return { ok: false, reason: 'cnsc_site_price_rows_missing' };
    const expectedRows = sites[0]?.rows || [];
    if (rows.length !== expectedRows.length) {
      return { ok: false, reason: 'cnsc_site_price_row_count_mismatch', rows: rows.length };
    }
    const written = [];
    const missing = [];
    for (const row of rows) {
      const diff = Array.isArray(row.diff_info) ? row.diff_info : null;
      if (!diff) return { ok: false, reason: 'cnsc_site_price_diff_info_missing', sku: String(row.sku || '') };
      for (const site of sites) {
        const expected = (site.rows || []).find((candidate) => String(candidate.seller_sku) === String(row.sku));
        if (!expected) {
          missing.push({ sku: String(row.sku || ''), site_code: site.site_code });
          continue;
        }
        const entry = diff.find((d) => String(d.site || '').trim().toUpperCase() === site.site_code);
        if (!entry) {
          missing.push({ sku: String(row.sku || ''), site_code: site.site_code });
          continue;
        }
        entry.price = String(expected.original_price);
        entry.discountType = 1;
        entry.discountPrice = String(expected.sale_price);
        entry.discountPercent = '';
        written.push({ sku: String(row.sku || ''), site_code: site.site_code });
      }
    }
    if (missing.length) return { ok: false, reason: 'cnsc_site_price_site_missing', missing: missing.slice(0, 10) };
    return { ok: true, written: written.length, rows: rows.length };
  }, plan);
  if (!result.ok) throw new BlockedError(result.reason, result);
  return result;
}

async function siteDiscountHeaders(table) {
  const headers = table.locator('.vxe-header--column').filter({ hasText: TEXT.discountActivity });
  const result = [];
  for (let index = 0; index < await headers.count(); index += 1) {
    const header = headers.nth(index);
    if (!(await header.isVisible().catch(() => false))) continue;
    // vxe-table clones headers into the frozen left/right wrappers; one site must
    // map to exactly one (main) header or the per-site pairing misaligns.
    const isClone = await header
      .evaluate((node) => !!node.closest('.vxe-table--fixed-left-wrapper, .vxe-table--fixed-right-wrapper'))
      .catch(() => false);
    if (isClone) continue;
    result.push(header);
  }
  return result;
}

async function siteDiscountRegions(table, plan) {
  const allHeaders = table.locator('.vxe-header--column');
  const pageSiteCodes = [];
  for (let index = 0; index < await allHeaders.count(); index += 1) {
    const header = allHeaders.nth(index);
    if (!(await header.isVisible().catch(() => false))) continue;
    const text = cleanText(await header.innerText().catch(() => '')).toUpperCase();
    if (/^(TW|PH|SG|TH|MY|MX|VN|BR)$/.test(text) && !pageSiteCodes.includes(text)) pageSiteCodes.push(text);
  }
  const headers = await siteDiscountHeaders(table);
  if (headers.length !== pageSiteCodes.length) {
    throw new BlockedError('cnsc_site_discount_header_site_mismatch', {
      page_site_codes: pageSiteCodes,
      discount_header_count: headers.length,
    });
  }
  return orderCNSCSiteDiscountPlan(pageSiteCodes, plan).map((site) => ({
    site,
    header: headers[pageSiteCodes.indexOf(site.site_code)],
  }));
}

async function selectCNSCSiteDiscountActivity(frame, header, site) {
  if (!header) throw new BlockedError('cnsc_site_discount_header_missing', { site_code: site.site_code });
  const currentText = cleanText(await header.innerText().catch(() => ''));
  if (site.discount_name && currentText.includes(cleanText(site.discount_name))) {
    await frame.evaluate(({ code, id }) => {
      window.__AI_LISTING_CNSC_EXECUTION_STATE__ ||= { activities: {} };
      window.__AI_LISTING_CNSC_EXECUTION_STATE__.activities[code] = id;
    }, { code: site.site_code, id: site.mabang_discount_id });
    return { method: 'already_selected' };
  }
  const action = header.getByText(TEXT.selectActivity, { exact: true });
  const fallback = header.locator('.change-discount');
  const trigger = await action.count() === 1 ? action : fallback;
  if (await trigger.count() !== 1) throw new BlockedError('cnsc_site_discount_select_action_not_unique', { site_code: site.site_code, count: await trigger.count() });
  await trigger.click();
  let dialogs = frame.locator('.el-dialog:visible').filter({ hasText: TEXT.selectDiscountActivity });
  const deadline = Date.now() + 5000;
  while (!(await dialogs.count()) && Date.now() < deadline) await sleep(100);
  if (await dialogs.count() !== 1) throw new BlockedError('cnsc_site_discount_dialog_not_visible', { site_code: site.site_code });
  const dialog = dialogs.first();
  const discountTable = dialog.locator('.vxe-table:visible');
  if (await discountTable.count() !== 1) throw new BlockedError('cnsc_site_discount_table_not_unique', { site_code: site.site_code });
  // The dialog renders before its shop/list request fills tableData — reading it
  // immediately sees zero rows (a race, not an absence). Poll until the rows load
  // or the dialog proves empty.
  const matchDeadline = Date.now() + 8000;
  let match = { count: 0 };
  while (Date.now() < matchDeadline) {
    match = await discountTable.evaluate((element, expected) => {
      const rows = element.__vue__?.tableData || element.__vueParentComponent?.proxy?.tableData || [];
      const matches = rows.map((row, index) => ({ row, index })).filter(({ row }) => {
        const id = String(row.discount_id || row.discountId || row.activity_id || row.id || '');
        const store = String(row.shopId || row.shop_id || row.store_id || '');
        return id === expected.id && (!store || store === expected.storeId);
      });
      if (matches.length !== 1) return { count: matches.length };
      const { row, index } = matches[0];
      return { count: 1, index, name: String(row.discount_name || row.discountName || row.name || '') };
    }, { id: site.mabang_discount_id, storeId: site.store_id });
    if (match.count !== 0) break;
    await sleep(200);
  }
  if (match.count !== 1) throw new BlockedError('cnsc_site_discount_activity_not_unique', { site_code: site.site_code, count: match.count });
  const row = discountTable.locator('.vxe-body--row').nth(match.index);
  const choose = row.getByText(TEXT.choose, { exact: true });
  if (await choose.count() !== 1) throw new BlockedError('cnsc_site_discount_choose_not_unique', { site_code: site.site_code });
  await choose.click();
  await dialog.waitFor({ state: 'hidden', timeout: 5000 });
  await frame.evaluate(({ code, id }) => {
    window.__AI_LISTING_CNSC_EXECUTION_STATE__ ||= { activities: {} };
    window.__AI_LISTING_CNSC_EXECUTION_STATE__.activities[code] = id;
  }, { code: site.site_code, id: site.mabang_discount_id });
  return { method: 'selected_by_id', name: match.name };
}

async function readCNSCSitePriceSnapshot(frame, table, plan) {
  const snapshot = await table.evaluate((element, sites) => {
    const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
    const visible = (node) => node && node.getClientRects().length > 0;
    const mainOnly = (node) => !node.closest('.vxe-table--fixed-left-wrapper, .vxe-table--fixed-right-wrapper');
    const vm = element.__vue__ || (element.__vueParentComponent && element.__vueParentComponent.proxy);
    const rows = (vm && (vm.getTableData ? vm.getTableData().fullData : vm.tableData)) || [];
    // One discount sub-header per site in the main header; post-selection it reads
    // "折扣活动 <activity name>", which is the readback's activity identity.
    const activityHeaders = [...element.querySelectorAll('.vxe-header--column')]
      .filter((node) => visible(node) && mainOnly(node) && clean(node.textContent).includes('折扣活动'));
    // 站点组表头（整段文本就是站点码）的 DOM 顺序 = 页面列序。折扣活动叶子列必须
    // 按"页面站点顺序"配对，不能按 plan 下标：页面列序是马帮固定的（TW SG TH MY MX
    // VN PH BR），任务 target_site_stores 顺序是另一回事——按下标配对会把别的站的
    // 活动读过来（2026-09-27 真机：BR/MX/TW/VN activity_mismatch，MY/PH/SG/TH 因
    // 活动名都是「40%」被 name 兜底碰巧放过）。与 siteDiscountRegions 同一套配对。
    const pageSiteCodes = [...element.querySelectorAll('.vxe-header--column')]
      .filter((node) => visible(node) && mainOnly(node))
      .map((node) => clean(node.textContent).toUpperCase())
      .filter((text, index, all) => /^(TW|PH|SG|TH|MY|MX|VN|BR)$/.test(text) && all.indexOf(text) === index);
    const activityIdentity = (header) => {
      if (!header) return { id: '', name: '' };
      const identityNode = header.matches('[data-discount-id],[data-activity-id]')
        ? header
        : header.querySelector('[data-discount-id],[data-activity-id]');
      let id = clean(identityNode?.dataset?.discountId || identityNode?.dataset?.activityId);
      const nodes = [header, ...header.querySelectorAll('*')];
      for (const node of nodes) {
        for (let inner = node.__vue__ || (node.__vueParentComponent && node.__vueParentComponent.proxy), depth = 0; !id && inner && depth < 5; inner = inner.$parent, depth += 1) {
          id = clean(inner.currentDiscountId || inner.selectedDiscountId || inner.discountId || inner.activityId
            || (inner.$data && (inner.$data.currentDiscountId || inner.$data.selectedDiscountId)));
        }
        if (id) break;
      }
      return { id, name: clean(header.textContent) };
    };
    // %OFF implied by the written pair (sale = original × (1 - percent/100)),
    // rounded to display precision so the validator's 0.005 tolerance holds.
    const impliedPercent = (original, sale) => {
      const base = Number(original);
      const discounted = Number(sale);
      if (!Number.isFinite(base) || base <= 0 || !Number.isFinite(discounted) || discounted <= 0) return '';
      return String(Math.round((1 - discounted / base) * 10000) / 100);
    };
    return { sites: sites.map((site) => {
      const siteCode = String(site.site_code || '').trim().toUpperCase();
      const pageIndex = pageSiteCodes.indexOf(siteCode);
      const activity = activityIdentity(pageIndex >= 0 ? activityHeaders[pageIndex] : undefined);
      return {
        site_code: site.site_code,
        activity_id: activity.id,
        activity_name: activity.name,
        rows: rows.map((row) => {
          const entry = (Array.isArray(row.diff_info) ? row.diff_info : [])
            .find((d) => String(d.site || '').trim().toUpperCase() === site.site_code) || {};
          return {
            seller_sku: String(row.sku || ''),
            original_price: String(entry.price ?? ''),
            sale_price: String(entry.discountPrice ?? ''),
            discount_percent: impliedPercent(entry.price, entry.discountPrice),
          };
        }),
      };
    }) };
  }, plan);
  return snapshot;
}

async function fillCNSCSitePricesAndDiscounts(frame, task) {
  if (String(task.store?.listing_mode || '') !== 'cnsc_global') return { skipped: true };
  const plan = buildCNSCSitePricePlan(task);
  const table = await findCNSCSitePriceTable(frame, plan);
  // Activity selection first: choosing an activity is the UI flow the real page
  // exposes (选择活动 → 选择折扣活动 dialog), and writing the price model
  // afterwards survives it; the reverse order risks the dialog resetting values.
  const regions = await siteDiscountRegions(table, plan);
  for (const region of regions) {
    await selectCNSCSiteDiscountActivity(frame, region.header, region.site);
  }
  await writeCNSCSitePricesByModel(table, plan);
  await sleep(300);
  const readback = validateCNSCSitePriceReadback(
    await readCNSCSitePriceSnapshot(frame, table, plan),
    plan,
    actionableCNSCTargets(task),
  );
  if (!readback.ok) throw new BlockedError(readback.reason, readback);
  emit('stage', { stage: 'fill_cnsc_site_prices_and_discounts', ok: true, sites: readback.sites, rows: readback.rows });
  return readback;
}

// auto-publish ≠ publish: this toggles Mabang's "上架后自动发布到站点店铺" setting,
// which governs what Mabang does *after a listing is later published from the draft*.
// It is NOT a publish action — it only checks a checkbox, never clicks any of
// PUBLISH_TEXTS, so the publish guard is untouched and the run still only saves a
// draft. The parity test in tests/mabang_playwright_executor.test.cjs asserts the
// toggle label is disjoint from PUBLISH_TEXTS so it can never be treated as publish.
async function enableCNSCAutoPublish(frame, task) {
  if ((task.store || {}).listing_mode !== 'cnsc_global') return { skipped: true };
  const toggle = frame.getByRole('checkbox', { name: TEXT.autoPublishToSites, exact: true });
  if (await toggle.count() !== 1) throw new BlockedError('cnsc_auto_publish_toggle_not_found');
  // Element UI hides the native input (opacity:0), so check() never sees it visible;
  // read state from the input but click the visible label, like the site pickers above.
  const toggleLabel = toggle.locator('xpath=ancestor::label[1]');
  if (await toggleLabel.count() !== 1) throw new BlockedError('cnsc_auto_publish_toggle_not_found');
  if (!(await toggle.isChecked().catch(() => false))) await toggleLabel.click();
  if (!(await toggle.isChecked().catch(() => false))) throw new BlockedError('cnsc_auto_publish_not_enabled');
  emit('stage', { stage: 'enable_cnsc_auto_publish', ok: true, checked: true });
  return { checked: true };
}

function publishGuardSource() {
  return ({ labels: blockedLabels, listLabel, saveAndPrefix, publishSuffix }) => {
    const normalize = (value) => String(value || '').replace(/\s+/g, '');
    const state = window.__AI_LISTING_PUBLISH_GUARD__ || { blockedClicks: 0, publishClicked: false };
    const legacyLabels = Array.isArray(state.labels) ? state.labels : null;
    if (window.__AI_LISTING_PUBLISH_GUARD_INSTALLED__ && legacyLabels) {
      for (let index = legacyLabels.length - 1; index >= 0; index -= 1) {
        if (normalize(legacyLabels[index]) === normalize(listLabel)) legacyLabels.splice(index, 1);
      }
    }
    state.labels = blockedLabels;
    window.__AI_LISTING_PUBLISH_GUARD__ = state;
    if (window.__AI_LISTING_PUBLISH_GUARD_V2_INSTALLED__) return;
    document.addEventListener(
      'click',
      (event) => {
        const action = event.target && event.target.closest
          ? event.target.closest('button,[role="button"],a,.el-button,input[type="button"],input[type="submit"]')
          : null;
        const text = String((action && (action.innerText || action.textContent || action.value)) || '').trim();
        const normalized = normalize(text);
        const normalizedLabels = blockedLabels.map(normalize);
        const forbidden = normalizedLabels.some((label) => normalized === label)
          || normalized.startsWith(saveAndPrefix)
          || normalized.endsWith(publishSuffix);
        if (forbidden) {
          state.blockedClicks += 1;
          state.publishClicked = true;
          event.preventDefault();
          event.stopImmediatePropagation();
        }
      },
      true,
    );
    window.__AI_LISTING_PUBLISH_GUARD_V2_INSTALLED__ = true;
  };
}

async function installPublishGuard(browser) {
  const source = publishGuardSource();
  const guardArgs = {
    labels: PUBLISH_TEXTS,
    listLabel: TEXT.list,
    saveAndPrefix: TEXT.save + '\u5e76',
    publishSuffix: TEXT.publish,
  };
  for (const context of browser.contexts()) {
    await context.addInitScript(source, guardArgs);
  }
  for (const page of allPages(browser)) {
    for (const frame of page.frames()) {
      await frame.evaluate(source, guardArgs).catch(() => null);
    }
    page.on('framenavigated', (frame) => {
      frame.evaluate(source, guardArgs).catch(() => null);
    });
  }
}

// 表单项自身 label 的规范化文本：去掉必填星号与结尾冒号，压空白、全角转半角。
// options.loose（本土站点属性）：再剥掉标签里的括号英文后缀——马帮把类目属性「尺寸」
// 渲染成「尺寸（Size）：」，不剥的话精确匹配永远落空，只能靠子串回退撞运气。
function ownLabelText(value, options = {}) {
  const normalized = String(value || '')
    .replace(/[：:]/g, '')
    .replace(/^[*＊]+/, '')
    .replace(/[！-～]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xfee0));
  const text = options.loose === true ? normalized.replace(/[（(][^）)]*[）)]/g, '') : normalized;
  return compactText(text);
}

// 按"表单项自己的 label"精确匹配。子串匹配会让类目属性「尺寸」撞上物流的
// 「包裹尺寸」（长/宽/高三个输入框），轻则 field_not_unique，重则把值填错字段。
async function formItemByOwnLabel(frame, label, options = {}) {
  const wanted = ownLabelText(label, options);
  if (!wanted) return null;
  const items = frame.locator('.el-form-item');
  const matches = [];
  for (let index = 0, count = await items.count().catch(() => 0); index < count; index += 1) {
    const item = items.nth(index);
    const own = item.locator(':scope > .el-form-item__label').first();
    if (!(await own.count().catch(() => 0))) continue;
    const text = ownLabelText(await own.innerText().catch(() => ''), options);
    if (text !== wanted) continue;
    if (!(await isVisible(item))) continue;
    matches.push(item);
  }
  if (matches.length === 1) return matches[0];
  if (matches.length > 1) {
    const measured = await Promise.all(matches.map(async (locator) => {
      const box = await locator.boundingBox();
      return { locator, area: (box?.width || 99999) * (box?.height || 99999) };
    }));
    measured.sort((left, right) => left.area - right.area);
    return measured[0].locator;
  }
  return null;
}

// 子串回退的收窄版：只保留"自身 label 以目标文案开头"的表单项。本土属性页上
// 「尺寸」的子串命中还有「包裹尺寸」和「*尺码图表」（都是 3 输入框），只按面积挑
// 一个必然出错；按前缀锚定后「尺寸（Size）」命中、这两个干扰项被排除。
async function prefixAnchoredItems(items, label, options = {}) {
  const wanted = ownLabelText(label, options);
  if (!wanted) return [];
  const anchored = [];
  for (const item of items) {
    const own = item.locator(':scope > .el-form-item__label').first();
    if (!(await own.count().catch(() => 0))) continue;
    const text = ownLabelText(await own.innerText().catch(() => ''), options);
    if (text.startsWith(wanted)) anchored.push(item);
  }
  return anchored;
}

async function formItemForLabels(frame, labels, options = {}) {
  // 先按自身 label 精确匹配；只有匹配不到时才退回子串扫描（兼容结构不同的表单）。
  for (const label of labels) {
    const exact = await formItemByOwnLabel(frame, label, options);
    if (exact) return exact;
  }
  for (const label of labels) {
    const items = frame.locator('.el-form-item').filter({ hasText: label });
    const visible = [];
    for (let index = 0, count = await items.count(); index < count; index += 1) {
      const item = items.nth(index);
      if (await isVisible(item)) visible.push(item);
    }
    const candidates = options.loose === true ? await prefixAnchoredItems(visible, label, options) : visible;
    if (candidates.length === 1) return candidates[0];
    if (candidates.length > 1) {
      const measured = await Promise.all(candidates.map(async (locator) => {
        const box = await locator.boundingBox();
        return { locator, area: (box?.width || 99999) * (box?.height || 99999) };
      }));
      measured.sort((left, right) => left.area - right.area);
      return measured[0].locator;
    }
  }
  return null;
}

async function waitForFormItemForLabels(frame, labels, timeoutMs = 5000, options = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const item = await formItemForLabels(frame, labels, options);
    if (item) return item;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new BlockedError('select_field_not_found', { field: labels[0] });
}

async function uniqueField(frame, labels, selector = 'input:not([type="hidden"]),textarea,[contenteditable="true"]', options = {}) {
  const item = await formItemForLabels(frame, labels, options);
  let candidates = item ? item.locator(selector) : frame.locator(selector);
  const visible = [];
  for (let index = 0, count = await candidates.count(); index < count; index += 1) {
    const candidate = candidates.nth(index);
    if (await isVisible(candidate)) visible.push(candidate);
  }
  if (visible.length !== 1) {
    throw new BlockedError('field_not_unique', { field: labels[0], count: visible.length });
  }
  return visible[0];
}

// 表单项里"真正带值"的那个输入框。尺码图表的表单项把「上传图片 / 选择模板」两个模式
// 单选也渲染在同一块里（radio 是 opacity:0 但仍有包围盒，Playwright 判定可见），于是
// 宽松选择器 `input` 的第一个命中就是那个 radio——它没有值、readonly 也不成立。结果是
// 模板选完回读恒为空：页面上明明已经选好，却被判成 select_value_not_verified（现场：
// 任务 9c9effcb / 86d59af8 因此报 size_chart_template_unresolved）。先按"带值的输入框"
// 取，一个都没有时才退回原来的宽松选择器，其它表单保持老行为。
const CONTROL_INPUT_SELECTOR = '.el-select input, .el-input input, input:not([type="radio"]):not([type="checkbox"]):not([type="file"]):not([type="hidden"])';

async function valueInputForItem(item) {
  const control = item.locator(CONTROL_INPUT_SELECTOR);
  if (await control.count().catch(() => 0)) return control.first();
  return item.locator('.el-select input,.el-input input,input').first();
}

async function selectedTextsForItem(item) {
  // Element UI 的可搜索下拉会把用户正在输入的搜索词放进 input.value；
  // 这不是已提交选项，不能用于 already_selected 或点击后的回读。
  // 已提交值优先从 tag/selected-item/aria-selected 读取。
  const selectedTagTexts = (await item.locator('.el-select__tags .el-tag, .el-select__tags-text, .el-select__selected-item, [aria-selected="true"]').allInnerTexts().catch(() => []))
    .map(cleanText)
    .filter(Boolean);
  const input = await valueInputForItem(item);
  const readOnly = (await input.getAttribute('readonly').catch(() => null)) !== null;
  const inputValue = readOnly ? cleanText(await input.inputValue().catch(() => '')) : '';
  // 马帮店铺下拉的 Element UI 控件在选中后会把显示名放到 placeholder，
  // 同时保持 input.value 为空（例如 placeholder="HN_POD"），并在选中后移除
  // readonly。只对明确的 .el-select 控件读取这个占位文本，并排除未选择时的
  // 通用提示，避免把普通文本框或搜索词当成已选值。
  const isSelect = (await item.locator('.el-select').count().catch(() => 0)) > 0;
  const placeholder = isSelect ? cleanText(await input.getAttribute('placeholder').catch(() => '')) : '';
  const genericPlaceholder = /^(请选择|选择|请输入|搜索)(?:\.\.\.)?$/i.test(placeholder);
  const selectedPlaceholder = isSelect && placeholder && !genericPlaceholder ? placeholder : '';
  return [inputValue, ...selectedTagTexts, selectedPlaceholder].filter(Boolean);
}

// 页面上的控件到底是下拉还是文本框：马帮把两类属性都渲染成 el-input，只有
// .el-select 结构和 readonly 能区分（下拉的触发器是 readonly 的 el-input__inner，
// placeholder 通常是「请选择」）。档案里存的 type 只说明"值怎么存"，不能当控件类型用。
async function isSelectControl(item) {
  if (!item) return false;
  if ((await item.locator('.el-select').count().catch(() => 0)) > 0) return true;
  const input = item.locator('.el-input input,input,textarea').first();
  return (await input.getAttribute('readonly').catch(() => null)) !== null;
}

// 文本分支的前置检查：readonly 的控件永远不会变可编辑，对它 fill() 只会白等到
// 超时（30s 的 Playwright 堆栈，操作员看不懂）；disabled 可能是表单刚渲染时的
// 暂时态，给它一点时间恢复。readonly 立即判否。
async function isFillableControl(locator, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const readOnly = (await locator.getAttribute('readonly').catch(() => null)) !== null;
    const disabled = (await locator.getAttribute('disabled').catch(() => null)) !== null;
    if (!readOnly && !disabled) return true;
    if (readOnly || Date.now() >= deadline) return false;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
}

async function setField(frame, labels, value, options = {}) {
  const field = await uniqueField(frame, labels, undefined, options);
  if ((await field.getAttribute('contenteditable')) === 'true') {
    const editorValue = normalizeContentEditableText(value);
    await field.click();
    await field.press('Control+A');
    await field.fill(editorValue).catch(async () => field.pressSequentially(editorValue));
    const actual = await field.inputValue().catch(() => field.innerText().catch(() => ''));
    if (cleanText(actual) !== cleanText(editorValue)) {
      throw new BlockedError('field_value_not_verified', { field: labels[0] });
    }
    return;
  } else {
    if (!(await isFillableControl(field))) {
      throw new BlockedError('field_not_editable', { field: labels[0] });
    }
    await field.fill(String(value));
  }
  const actual = await field.inputValue().catch(() => field.innerText().catch(() => ''));
  if (cleanText(actual) !== cleanText(value)) {
    throw new BlockedError('field_value_not_verified', { field: labels[0] });
  }
}

// 马帮/Element UI 的下拉是 toggle：已经展开时再点一次 trigger 会把它关掉。
// 展开状态看该控件自己的箭头（.el-select__caret.is-reverse），逐个控件判断，
// 不会和别的下拉搞混。selectElementOption 找不到选项抛错时面板还开着，
// 之前的实现又点一次 trigger 把它关掉，于是"自填选项"永远等不到面板。
async function isSelectPanelOpen(item) {
  return (await item.locator('.el-select__caret.is-reverse').count().catch(() => 0)) > 0;
}

// 确保下拉展开并返回带输入框的面板；返回 null 表示这个控件不是这种下拉。
// 注意返回的是**会重新解析**的复合 locator（.first() + filter），不是按序号取的 nth(i)：
// element-ui 的 popper 会在 DOM 里换位，nth(i) 之后再解析可能落到另一个（隐藏的）面板上，
// 后续点击就会卡在 actionability 上直到超时。
async function openSelectPanel(frame, item) {
  const visiblePanel = () => frame
    .locator('.el-select-dropdown:visible')
    .filter({ has: frame.locator('input') })
    .first();
  if (!(await isSelectPanelOpen(item))) {
    // 优先点输入框本体：原生 el-select 点输入框一样能展开面板，而且**不会**碰到 Element UI
    // 的"清除"图标。控件里已经有值时（强制自选通道刚把值写进页面模型，控件就渲染出来了），
    // hover 到触发箭头那一小块会露出清除图标：点它既打不开面板，还会把待填的值清掉
    // （现场 2026-10-06：型号自选通道点箭头，tag 与页面模型里的值一起没了）。
    const input = await valueInputForItem(item);
    if (await input.count().catch(() => 0)) {
      await input.click({ timeout: 5000 }).catch(() => {});
      const inputDeadline = Date.now() + 1200;
      while (Date.now() < inputDeadline && !(await visiblePanel().count().catch(() => 0))) {
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
    }
    if (!(await visiblePanel().count().catch(() => 0))) {
      const arrow = item.locator('.el-select .el-input__suffix:visible').first();
      if (await arrow.count().catch(() => 0)) await arrow.click({ timeout: 5000 }).catch(() => {});
      else await item.locator('.el-select').first().click({ timeout: 5000 }).catch(() => {});
    }
  }
  const panelDeadline = Date.now() + 5000;
  while (Date.now() < panelDeadline) {
    const candidate = visiblePanel();
    if (await candidate.count().catch(() => 0)) return candidate;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return null;
}

// 收起下拉面板的策略：本土(site) 用 Esc，跨境(CNSC) 沿用原来的「点触发箭头」。
// 现场实证（2026-09-27，CDP 9223，本土 HN_POD 详情页「材料」，面板 class 含 is-multiple）：
//   点选项 → tags=["棉花 (Cotton)"]；再点触发箭头关闭 → tags=[]（待提交值被丢掉）；
//   点选项 → 按 Esc 关闭 → tags 仍是 ["棉花 (Cotton)"]，重开面板该选项仍是 selected。
// 所以多选下拉绝对不能用触发箭头收尾——那正是「材料/图案」被填成空值的原因。
function panelCloseStrategy(options = {}) {
  return options.siteMultiSelectHandling === true ? 'escape' : 'arrow';
}

// 多选的「值真的进控件了吗」只看控件自身的标签元素（.el-select__tags 里的 chip）。
// 不能用 input.value / placeholder：马帮多选在待提交阶段就会把标签文案回显到那里，
// 现场因此把空控件判成"已填好"（阶段报 count=15，表单里 材料/图案 全空）。
async function tagTextsForItem(item) {
  if (!item) return [];
  return (await item.locator('.el-select__tags .el-tag, .el-select__tags-text').allInnerTexts().catch(() => []))
    .map((text) => cleanText(text))
    .filter(Boolean);
}

// 多选（tags 控件）的取值判定：只看标签。
function tagBasedEvidence(actualTexts, target) {
  const texts = Array.isArray(actualTexts) ? actualTexts : [];
  return texts.some((selected) => selectTextMatches(selected, target));
}

// 等面板稳定：马帮多选 popper 进场时点击会被组件丢掉（现场只见选项行翻选中态、
// 控件里始终没有标签）。连续两次采样都可见（约 300ms）才认为它准备好了。
async function waitForSettledSelectPanel(frame, stablePolls = 2, timeoutMs = 3000) {
  let stable = 0;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline && stable < stablePolls) {
    const count = await frame.locator('.el-select-dropdown:visible').count().catch(() => 0);
    stable = count > 0 ? stable + 1 : 0;
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  return stable >= stablePolls;
}

// 本土多选下拉的提交循环：点选项 → 等提交落定 → 收面板 → 校验标签；最多点 maxClicks 次。
// 现场（2026-09-27 CDP 9223，高频采样 .scratch/multiselect_life.log）：
//   12:52:26 面板打开 → 12:52:28.339 tags=["棉花 (Cotton)"] → 12:52:29.358 tags=[]、placeholder 回到「请选择」。
//   手动对照：点选项后 1.5s 再按 Esc → 标签保留且扛得住后续属性变更；点触发箭头关闭 → 必丢。
// 结论：马帮多选的提交是异步的，标签刚出现就收面板会把待提交值丢掉；点箭头关闭更是直接取消。
async function clickOptionUntilTagCommitted(frame, item, labels, value, optionLocators, options = {}, maxClicks = 3) {
  let clicks = 0;
  for (;;) {
    const fresh = await formItemForLabels(frame, labels, options);
    const freshTags = await tagTextsForItem(fresh);
    if (tagBasedEvidence(freshTags, value)) return { value: freshTags.join(', '), clicks };
    if (clicks >= maxClicks) return { value: '', clicks };
    clicks += 1;
    await waitForSettledSelectPanel(frame);
    const option = optionLocators.first();
    if (!(await option.count().catch(() => 0))) {
      await new Promise((resolve) => setTimeout(resolve, 200));
      continue;
    }
    await option.click({ timeout: 5000 }).catch(() => {});
    // 给马帮的异步提交留足时间（约 3s），期间只读控件标签，不做任何点击。
    const commitDeadline = Date.now() + 3000;
    while (Date.now() < commitDeadline) {
      await new Promise((resolve) => setTimeout(resolve, 200));
      await tagTextsForItem(await formItemForLabels(frame, labels, options));
    }
    await closeOpenSelectPanels(frame, (await formItemForLabels(frame, labels, options)) || item, options);
    await new Promise((resolve) => setTimeout(resolve, 400));
    const settled = await formItemForLabels(frame, labels, options);
    const settledTags = await tagTextsForItem(settled);
    if (tagBasedEvidence(settledTags, value)) return { value: settledTags.join(', '), clicks };
  }
}

// 现场排查通道：类目属性下拉每一步的「证据」写进 tmp_upload/auto-fill-debug.log
// （与 AI 补齐同一份日志，best-effort，不参与业务判断）。多选属性「报成功但控件里
// 没值」这类问题只有把 option class / aria / 回读值摆在一起才能定位。
function attrPickDebug(record) {
  try {
    fs.mkdirSync('tmp_upload', { recursive: true });
    fs.appendFileSync('tmp_upload/auto-fill-debug.log', JSON.stringify({ at: new Date().toISOString(), kind: 'attr_pick', ...record }) + '\n');
  } catch (_e) { /* debug log best-effort */ }
}

// 关掉还开着的下拉面板。用 Esc 的策略能保住多选待提交值；没有 item 时（上一格残留的
// 面板）也能关，避免它的 popper 盖住下一格的触发箭头——点击会被它的选项 <li> 拦下，
// 一路重试到 30s 超时（现场：任务 71181e90 attempt 1/2 的 set_category_attributes）。
async function closeOpenSelectPanels(frame, item = null, options = {}) {
  const panelSelector = '.el-select-dropdown:visible';
  if (!(await frame.locator(panelSelector).count().catch(() => 0))) return false;
  if (panelCloseStrategy(options) === 'escape') {
    await frame.locator('body').press('Escape').catch(() => {});
    await new Promise((resolve) => setTimeout(resolve, 200));
    if (!(await frame.locator(panelSelector).count().catch(() => 0))) return true;
  }
  if (item) {
    const closeArrow = item.locator('.el-select .el-input__suffix').first();
    if (await isVisible(closeArrow).catch(() => false)) {
      await closeArrow.click({ timeout: 2000 }).catch(() => {});
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  return !(await frame.locator(panelSelector).count().catch(() => 0));
}

// 一次 evaluate 把多个类目属性写进页面表单模型（storeCategory.ProductAttributeInfo.attributesNew）。
// 逐个写 = 每个属性一次 CDP evaluate + 一次渲染校验（现场 15 个属性 30.4s）；批量写只需一次往返。
async function applyAttributeValuesInModel(frame, entries) {
  const items = (Array.isArray(entries) ? entries : [])
    .map((entry) => ({
      field_id: String(entry && entry.field_id || '').trim(),
      values: (Array.isArray(entry && entry.values) ? entry.values : [entry && entry.values])
        .map((value) => cleanText(value)).filter(Boolean),
    }))
    .filter((entry) => entry.field_id && entry.values.length);
  if (!items.length) return { results: [] };
  const outcome = await frame.evaluate(({ wanted }) => {
    const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
    let storeCategory = null;
    for (const element of document.querySelectorAll('*')) {
      for (let vm = element.__vue__, depth = 0; vm && depth < 12; vm = vm.$parent, depth += 1) {
        const candidate = vm.$refs && vm.$refs.storeCategory;
        if (candidate) { storeCategory = candidate; break; }
      }
      if (storeCategory) break;
    }
    const attributes = (storeCategory && storeCategory.ProductAttributeInfo && storeCategory.ProductAttributeInfo.attributesNew) || [];
    const results = [];
    for (const item of wanted) {
      const attribute = attributes.find((entry) => String(entry.attribute_id ?? entry.id) === String(item.field_id));
      if (!attribute) { results.push({ field_id: item.field_id, ok: false, reason: 'attribute_not_found' }); continue; }
      const groups = Array.isArray(attribute.values) ? attribute.values : [];
      const options = groups.flatMap((group) => (Array.isArray(group.options) ? group.options : [group]));
      const resolve = (raw) => {
        const text = clean(raw).toLowerCase();
        const hit = options.find((option) => clean(option.value) === clean(raw)
          || clean(option.label).toLowerCase() === text
          || clean(option.name_cn).toLowerCase() === text
          || clean(option.value).toLowerCase() === text);
        if (hit) return { value: clean(hit.value), label: clean(hit.label ?? hit.name_cn ?? hit.value), from_options: true };
        // 枚举里没有：马帮自己支持「添加自填选项」（原产地 = Philippines 走的正是这条路），
        // 照写原值并标记 custom；页面不认的话由 attributeValueRendered 兜底。
        return { value: clean(raw), label: clean(raw), from_options: false };
      };
      const resolved = [];
      let missing = '';
      for (const raw of item.values) {
        const hit = resolve(raw);
        if (!hit) { missing = clean(raw); break; }
        if (!resolved.some((entry) => entry.value === hit.value)) resolved.push(hit);
      }
      if (missing) { results.push({ field_id: item.field_id, ok: false, reason: 'value_not_in_options', value: missing }); continue; }
      const isArray = Array.isArray(attribute.value);
      attribute.value = isArray ? resolved.map((entry) => entry.value) : resolved[0].value;
      results.push({
        field_id: item.field_id,
        ok: true,
        applied: attribute.value,
        labels: resolved.map((entry) => entry.label),
        is_array: isArray,
        custom: resolved.some((entry) => entry.from_options === false),
      });
    }
    return { results };
  }, { wanted: items }).catch((error) => ({ results: items.map((item) => ({ field_id: item.field_id, ok: false, reason: 'evaluate_failed', value: cleanText(error && error.message).slice(0, 120) })) }));
  return outcome;
}

// 单个属性写模型（保留原签名，内部走批量实现，避免两份解析逻辑）。
async function applyAttributeValueInModel(frame, fieldId, values) {
  const outcome = await applyAttributeValuesInModel(frame, [{ field_id: fieldId, values }]);
  return (outcome.results || [])[0] || { ok: false, reason: 'no_value' };
}

// 一次 evaluate 批量校验多个属性是否已按模型渲染出来。
// 逐个校验 = 每个属性一次 formItemForLabels + 若干次读值（现场 15 个属性 27.6s）；
// 批量读一次 DOM 就够。多选只认标签，单选/文本认回读值或控件显示值（与
// attributeValueRendered 同一套判据，只是搬进浏览器里一次算完）。
async function attributeValuesRendered(frame, entries) {
  const wanted = (Array.isArray(entries) ? entries : []).map((entry) => ({
    field_id: String(entry && entry.field_id || ''),
    labels: (Array.isArray(entry && entry.labels) ? entry.labels : [entry && entry.labels]).map((value) => cleanText(value)).filter(Boolean),
    expected: (Array.isArray(entry && entry.expected) ? entry.expected : [entry && entry.expected]).map((value) => cleanText(value)).filter(Boolean),
    // 页面上没有枚举的属性（条件 100413、型号 101639…）：马帮那个控件不会给"枚举外的值"
    // 渲染 tag，所以判定要跟条件一样——认控件回读值/控件里显示的文本，**外加**页面表单模型
    // 里那一格的值（页面保存时用的就是它），而不是死等 tag。
    model_evidence: Boolean(entry && entry.model_evidence),
  })).filter((entry) => entry.field_id && entry.expected.length);
  if (!wanted.length) return [];
  return frame.evaluate(({ entries: targets }) => {
    const norm = (value) => String(value ?? '').replace(/\s+/g, '').replace(/[：:]+$/, '');
    const loose = (value) => norm(value).replace(/[（(][^）)]*[）)]/g, '');
    const sameText = (actual, expected) => {
      const a = norm(actual).toLowerCase();
      const b = norm(expected).toLowerCase();
      if (!a || !b) return false;
      return a === b || a.includes(b) || b.includes(a);
    };
    const items = [];
    for (const item of document.querySelectorAll('.el-form-item')) {
      const own = item.querySelector(':scope > .el-form-item__label');
      if (!own) continue;
      items.push({ item, label: norm(own.innerText), looseLabel: loose(own.innerText) });
    }
    // 只有需要"模型也算证据"的字段才去读一次页面表单模型（无枚举属性专用）。
    const modelValues = {};
    if (targets.some((target) => target.model_evidence)) {
      let storeCategory = null;
      for (const element of document.querySelectorAll('*')) {
        for (let vm = element.__vue__, depth = 0; vm && depth < 12; vm = vm.$parent, depth += 1) {
          if (vm.$refs && vm.$refs.storeCategory) { storeCategory = vm.$refs.storeCategory; break; }
        }
        if (storeCategory) break;
      }
      const attributes = (storeCategory && storeCategory.ProductAttributeInfo && storeCategory.ProductAttributeInfo.attributesNew) || [];
      for (const attribute of attributes) {
        modelValues[String(attribute.attribute_id ?? attribute.id)] = attribute.value;
      }
    }
    return targets.map((target) => {
      const wantedLabels = [...target.labels, ...target.expected].map((value) => loose(value)).filter(Boolean);
      const hit = items.find((candidate) => wantedLabels.some((label) => candidate.looseLabel.startsWith(label) || candidate.label.startsWith(label)));
      if (!hit) return { field_id: target.field_id, rendered: false, reason: 'item_not_found' };
      const tagBased = !!hit.item.querySelector('.el-select__tags');
      const tags = [...hit.item.querySelectorAll('.el-select__tags .el-tag, .el-select__tags-text')]
        .map((node) => String(node.innerText || '').trim()).filter(Boolean);
      const input = hit.item.querySelector('input');
      const inputValue = input ? String(input.value || '').trim() : '';
      const placeholder = input ? String(input.placeholder || '').trim() : '';
      const genericPlaceholder = /^(请选择|选择|请输入|搜索)(\.\.\.)?$/i.test(placeholder);
      // 多选永远不看 input/placeholder（"待提交"阶段它们就会回显文案）。
      const sources = tagBased
        ? tags
        : [...tags, inputValue, genericPlaceholder ? '' : placeholder].filter(Boolean);
      const modelValue = modelValues[target.field_id];
      const modelText = Array.isArray(modelValue) ? modelValue.join(', ') : String(modelValue ?? '');
      const rendered = target.expected.every((expected) => (
        sources.some((source) => sameText(source, expected))
        || (target.model_evidence && modelText && sameText(modelText, expected))
      ));
      return { field_id: target.field_id, rendered, tag_based: tagBased, sources: sources.slice(0, 4), model: target.model_evidence ? modelText.slice(0, 80) : undefined };
    });
  }, { entries: wanted }).catch(() => wanted.map((entry) => ({ field_id: entry.field_id, rendered: false, reason: 'evaluate_failed' })));
}

// 写模型之后，等页面把值渲染出来：多选（tags 控件）只认控件自己的标签，
// 单选认控件回读值，纯文本再退一步比控件里显示的值——多选的 input/placeholder
// 在"待提交"阶段就会回显文案，所以多选永远不看这两处。
async function attributeValueRendered(frame, labels, expected, timeoutMs = 3000) {
  const wanted = (Array.isArray(expected) ? expected : [expected]).map((value) => cleanText(value)).filter(Boolean);
  if (!wanted.length) return false;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const item = await formItemForLabels(frame, labels, {});
    if (item) {
      const tagBased = (await item.locator('.el-select__tags').count().catch(() => 0)) > 0;
      const texts = tagBased ? await tagTextsForItem(item) : await selectedTextsForItem(item);
      const rawInput = tagBased
        ? ''
        : cleanText(await (await valueInputForItem(item)).inputValue().catch(() => ''));
      const matches = (value) => texts.some((text) => selectTextMatches(text, value))
        || (rawInput ? selectTextMatches(rawInput, value) : false);
      if (wanted.every(matches)) return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return false;
}

// 往马帮的自填/搜索输入框写值：必须**逐字键入**——组件靠真实按键事件把值同步进自己的
// state，playwright 的 fill() 只改 DOM value 并派发 input 事件，组件里的值仍是空的，
// 随后点 ✔ 什么也加不进去（表现：自填选项分组一直是空的，任务只报 select_option_not_found）。
async function typeIntoField(field, target) {
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    // 显式给短超时：控件不可点时不要按 30s 默认值空等（一次字段能白等几分钟）。
    await field.click({ timeout: 5000 }).catch(() => {});
    await field.fill('').catch(() => {});
    if (typeof field.pressSequentially === 'function') {
      await field.pressSequentially(target, { delay: 25, timeout: 10000 }).catch(() => {});
    } else {
      await field.type(target, { delay: 25, timeout: 10000 }).catch(() => {});
    }
    if ((await field.inputValue().catch(() => '')) === target) return true;
    await field.fill(target).catch(() => {});
    if ((await field.inputValue().catch(() => '')) === target) return true;
  }
  return false;
}

// 把自填值提交到马帮的下拉面板。按 DOM 判断，不靠猜：
//   现网 UI 是两步——面板先给「搜索框 + 添加」；在搜索框键入值并点「添加」后，同一行才切换成
//   自填输入行（.addCustomBox，placeholder「请输入自定义值」+ ✔ / ✕），在自填框里键入值再点 ✔
//   才真正加入。旧 UI 则是搜索框 + 「添加」一步到位。
// 返回 false 表示这个面板两种都不是（例如普通可编辑文本框）。
async function submitCustomSelectValue(panel, target) {
  // 一律用 `:visible` 的复合 locator：面板里同一控件常有多份（隐藏模板行 + 真正可点的那份），
  // 按序号取会在 DOM 换位后落到隐藏元素上，点击随即卡在 actionability 直到超时。
  const search = panel.locator('.searchBox input:visible, input:visible').first();
  if ((await search.count().catch(() => 0)) > 0 && !(await typeIntoField(search, target))) {
    return false;
  }
  const addButton = panel.locator('button:visible, .el-button:visible').filter({ hasText: /^\s*添\s*加\s*$/ }).first();
  const addFound = (await addButton.count().catch(() => 0)) > 0;
  // 现场 UI（截图 2026-10-02）：「添加」常常是搜索框右边的一个文字链接/span，**不是 <button>**——
  // 只按 button/.el-button 找会找不到它，于是值只被打进搜索框、从没真正添加过。找不到按钮时
  // 再按 a/span/i 找一遍。
  const addLink = addFound
    ? addButton
    : panel.locator('a:visible, span:visible, i:visible, .add-btn:visible').filter({ hasText: /^\s*添\s*加\s*$/ }).first();
  const addClickable = addFound || (await addLink.count().catch(() => 0)) > 0;
  if (addClickable) {
    await addLink.click({ timeout: 5000 }).catch(() => {});
    // 现网 UI：点完「添加」同一行才换成自填输入行，等它出现。
    const boxDeadline = Date.now() + 3000;
    while (Date.now() < boxDeadline && !(await panel.locator('.addCustomBox').count().catch(() => 0))) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  const box = panel.locator('.addCustomBox');
  if (await box.count().catch(() => 0)) {
    const input = box.locator('input:visible').first();
    if (!(await input.count().catch(() => 0))) return false;
    if (!(await typeIntoField(input, target))) return false;
    const check = box.locator('.el-icon-check:visible').first();
    const confirm = (await check.count().catch(() => 0)) ? check : box.locator('button:visible, .el-button:visible').filter({ hasText: /确认|确定|添加/ }).first();
    if (await confirm.count().catch(() => 0)) {
      await confirm.click({ timeout: 5000 }).catch(() => {});
    } else {
      await input.press('Enter').catch(() => {});
    }
    return true;
  }
  // Some Mabang builds expose only a visible editable row, without .addCustomBox.
  // Commit it with the same keyboard/button protocol instead of treating the value
  // as a plain fill-only field.
  const editable = panel.locator('input:visible').last();
  if (await editable.count().catch(() => 0) && (await editable.getAttribute('readonly').catch(() => null)) === null) {
    if (!(await typeIntoField(editable, target))) return false;
    const check = panel.locator('.el-icon-check:visible').last();
    const confirmButton = panel.locator('button:visible, .el-button:visible').filter({ hasText: /确认|确定|添加/ }).last();
    // 同一条 UI 上「添加/确定」也可能是文字链接：按钮找不到时再按 a/span/i 找一遍。
    const confirmLink = panel.locator('a:visible, span:visible, i:visible, .add-btn:visible').filter({ hasText: /^\s*添\s*加\s*$|确认|确定/ }).last();
    const confirm = (await check.count().catch(() => 0))
      ? check
      : ((await confirmButton.count().catch(() => 0)) ? confirmButton : confirmLink);
    if (await confirm.count().catch(() => 0)) await confirm.click({ timeout: 5000 }).catch(() => {});
    else await editable.press('Enter').catch(() => {});
    return true;
  }
  return addClickable;
}

// 马帮的下拉（如「原产地（Region of Origin）」）支持把枚举里没有的值加成"自填选项"：
// 打开下拉 → 提交自填值（见 submitCustomSelectValue）→ 新值出现在「自填选项」分组 → 点它
// （多选面板再点「确认选择」）。返回 null 表示这个控件不是这种下拉（例如普通可编辑
// 文本框），调用方应改用打字填写。
async function addCustomSelectOption(frame, item, labels, value, options = {}) {
  const target = String(value || '').trim();
  if (!target) return null;
  // 纯文本框：交给打字路径，别去开一个不存在的下拉。
  if (!(await isSelectControl(item))) return null;
  const panel = await openSelectPanel(frame, item);
  if (!panel) return null;
  if (!(await submitCustomSelectValue(panel, target))) return null;
  // 自填行的 ✔ 常常已经把值提交进控件了（现场 2026-10-06：多选下拉「型号」101639 的 ✔
  // 直接把标签填好、页面模型里也换成了马帮生成的选项 id）。这种情况**不能**再去点列表里
  // 那一行——多选是 toggle，点一下等于取消选择。所以先回读控件：已经有这个值就收工。
  await new Promise((resolve) => setTimeout(resolve, 300));
  const echoed = await formItemForLabels(frame, labels, options);
  const echoedTexts = echoed ? await selectedTextsForItem(echoed) : [];
  if (echoedTexts.some((selected) => selectTextMatches(selected, target))) {
    await closeOpenSelectPanels(frame, echoed || item, options);
    return { value: echoedTexts.join(', '), method: 'custom_option' };
  }
  // 新增项通常挂到「自填选项」分组下；等它出现再点，找不到就退回整面板匹配。
  const deadline = Date.now() + 8000;
  let loopIteration = 0;
  while (Date.now() < deadline) {
    loopIteration += 1;
    const visiblePanels = frame.locator('.el-select-dropdown:visible, .el-popper:visible, .el-dropdown-menu:visible');
    const panelCount = await visiblePanels.count().catch(() => 0);
    const searchRoots = [];
    for (let index = 0; index < panelCount; index += 1) searchRoots.push(visiblePanels.nth(index));
    const roots = searchRoots.length ? searchRoots : [panel];
    let matches = null;
    let matchCount = 0;
    for (const root of roots) {
      const group = root.locator('.el-select-group__wrap').filter({ hasText: '自填选项' }).first();
      const scoped = (await group.count()) ? group.locator('.el-select-dropdown__item') : root.locator('.el-select-dropdown__item, [role="option"]');
      let candidate = scoped.filter({ hasText: target });
      let count = await candidate.count().catch(() => 0);
      if (!count) {
        for (const variant of selectTextVariants(target).slice(1)) {
          candidate = scoped.filter({ hasText: variant });
          count = await candidate.count().catch(() => 0);
          if (count) break;
        }
      }
      if (count) { matches = candidate; matchCount += count; }
    }
    if (!matches) { await new Promise((resolve) => setTimeout(resolve, 100)); continue; }
    if (matchCount > 1) throw new BlockedError('select_custom_option_not_unique', { field: labels[0], count: matchCount });
    if (matchCount === 1) {
      // 自填行的 ✔ 有时已经把值提交进了控件（现场：多选下拉「型号」101639）。
      // 这种控件里再点一次列表项是"取消选择"，会把刚填好的值原样点掉，校验随即
      // 读到空值并误报 select_custom_value_not_verified。所以点击前先回读一次：
      // 值已经在控件里就算这一格填好了，别再点。
      const beforeClick = await formItemForLabels(frame, labels, options);
      const alreadyPicked = beforeClick ? await selectedTextsForItem(beforeClick) : [];
      if (alreadyPicked.some((selected) => cleanText(selected).toLocaleLowerCase().includes(target.toLocaleLowerCase()))) {
        await closeOpenSelectPanels(frame, beforeClick || item, options);
        return { value: target, method: 'custom_option' };
      }
      await matches.first().click({ timeout: 5000 });
      const confirm = panel.locator('button, .el-button').filter({ hasText: /确认选择|确\s*定/ }).first();
      if (await confirm.count() && await isVisible(confirm)) await confirm.click({ timeout: 5000 }).catch(() => {});
      const verifyDeadline = Date.now() + 5000;
      let stableMatches = 0;
      while (Date.now() < verifyDeadline) {
        const refreshed = await formItemForLabels(frame, labels, options);
        const texts = refreshed ? await selectedTextsForItem(refreshed) : [];
        const matched = texts.some((selected) => cleanText(selected).toLocaleLowerCase().includes(target.toLocaleLowerCase()));
        stableMatches = matched ? stableMatches + 1 : 0;
        if (stableMatches >= 5) {
          await closeOpenSelectPanels(frame, refreshed || item, options);
          return { value: target, method: 'custom_option' };
        }
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      throw new BlockedError('select_custom_value_not_verified', { field: labels[0] });
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return null;
}

// 本土（site）选下拉的容错参数：松散标签匹配 + 多选按现场交互收尾 + 点完只读控件值确认。
// 跨境（cnsc_global）一律不传，保持已验证过的老路径。
function siteSelectOptions() {
  return { loose: true, siteMultiSelectHandling: true };
}

async function selectElementOption(frame, labels, values, options = {}) {
  // 细粒度耗时（>300ms 才记）：定位"某个下拉为什么慢"。选店铺现场要 62s，需要知道
  // 是等表单项、开面板、找选项、点击还是回读在花时间。
  const phaseStartedAt = Date.now();
  let phaseMarkAt = phaseStartedAt;
  const phase = (step) => {
    const now = Date.now();
    if (now - phaseMarkAt >= 300) {
      attrPickDebug({ phase: 'select_phase', field: labels[0], step, ms: now - phaseMarkAt, total: now - phaseStartedAt });
    }
    phaseMarkAt = now;
  };
  const item = await waitForFormItemForLabels(frame, labels, 5000, options);
  phase('form_item');
  const targets = values.map((value) => String(value || '')).filter(Boolean);
  const matchesTarget = (selected, target) => selectTextMatches(selected, target);
  const native = item.locator('select');
  if ((await native.count()) === 1) {
    for (const value of targets) {
      const selected = await native.selectOption({ value: String(value) }).catch(() => []);
      if (selected.length) return { value: String(value), method: 'native' };
      const byLabel = await native.selectOption({ label: String(value) }).catch(() => []);
      if (byLabel.length) return { value: String(value), method: 'native_label' };
    }
  }
  const trigger = await valueInputForItem(item);
  if (!(await isVisible(trigger))) throw new BlockedError('select_trigger_not_visible', { field: labels[0] });
  // 本土多选（tags 控件）走专门的提交路径：只要控件自己的标签当证据，点完不落值就重试。
  const tagBasedSite = panelCloseStrategy(options) === 'escape'
    && (await item.locator('.el-select__tags').count().catch(() => 0)) > 0;
  const selectedTexts = await selectedTextsForItem(item);
  const selectedValue = selectedTexts.join(', ');
  if (targets.some((value) => selectedTexts.some((selected) => matchesTarget(selected, value)))) {
    return { value: selectedValue, method: 'already_selected' };
  }
  phase('already_check');
  const arrow = item.locator('.el-select .el-input__suffix').first();
  // 本土：先把上一格残留的下拉面板收干净，否则它的 popper 会盖住这一格的触发箭头，
  // 点击被它的选项 <li> 拦下并重试到默认 30s 超时（现场 71181e90）。
  if (panelCloseStrategy(options) === 'escape') {
    await closeOpenSelectPanels(frame, null, options);
  }
  // 同上：面板已经开着就别再点，否则会把它 toggle 掉，后面永远找不到选项。
  // 显式短超时：控件被面板/遮罩盖住时不要按 30s 默认值空等。
  if (!(await isSelectPanelOpen(item))) {
    if (await isVisible(arrow)) await arrow.click({ timeout: 5000 });
    else await item.locator('.el-select').first().click({ timeout: 5000 });
  }
  phase('open_panel');
  const optionDeadline = Date.now() + 5000;
  while (Date.now() < optionDeadline) {
    for (const value of targets) {
      for (const optionText of selectTextVariants(value)) {
        const optionLocators = frame.locator('.el-select-dropdown:visible .el-select-dropdown__item').filter({ hasText: optionText });
        const optionCount = await optionLocators.count();
        if (optionCount > 1) throw new BlockedError('select_option_not_unique', { field: labels[0], count: optionCount });
        if (optionCount === 1 && await isVisible(optionLocators.first())) {
          // 马帮多选下拉是 toggle：选项已勾选（class 含 selected）时再点一次会把
          // 刚选好的值点掉。现场：重试复用同一详情页（openListingWorkspace 先复用
          // 已有详情页），上一轮留下的已勾选项被再点一下 → ON→OFF 振荡，验证永远
          // 追不上 → select_value_not_verified。与「自填选项」分支同款保护：点击前
          // 先读勾选态/标签，已选中就不再点。
          const optionClass = String(await optionLocators.first().getAttribute('class').catch(() => '') || '');
          const optionAriaSelected = String(await optionLocators.first().getAttribute('aria-selected').catch(() => '') || '').toLowerCase();
          const currentItem = await formItemForLabels(frame, labels, {});
          const preTexts = currentItem ? await selectedTextsForItem(currentItem) : [];
          const alreadyPicked = tagBasedSite
            ? tagBasedEvidence(preTexts, value)
            : selectionEvidenceMatches(preTexts, value, optionClass, optionAriaSelected);
          attrPickDebug({
            phase: 'before_click', field: labels[0], target: value, option_class: optionClass,
            option_aria_selected: optionAriaSelected, already_picked: alreadyPicked, pre_texts: preTexts,
            tag_based_site: tagBasedSite,
          });
          if (tagBasedSite) {
            // 本土多选：只认控件标签，点不进去就重试（最多 3 次），三次都空就显式报错，
            // 绝不再让「选项行带 selected、控件里没值」被当成填好了。
            const committed = await clickOptionUntilTagCommitted(frame, item, labels, value, optionLocators, options);
            attrPickDebug({
              phase: 'tag_commit', field: labels[0], target: value, value: committed.value, clicks: committed.clicks,
            });
            if (!committed.value) {
              throw new BlockedError('select_value_not_verified', {
                field: labels[0],
                target: value,
                actual_texts: [],
                tag_based: true,
                clicks: committed.clicks,
              });
            }
            await closeOpenSelectPanels(frame, (await formItemForLabels(frame, labels, options)) || item, options);
            return { value: committed.value, method: 'element_ui_tag' };
          }
          if (!alreadyPicked) {
            await optionLocators.first().click({ timeout: 5000 });
            phase('option_click');
            // 多选属性有时要点面板内的「确认选择」才会提交到表单；单选没有该按钮，
            // 找不到时继续依靠标签/选中态回读。
            const confirm = frame.locator('.el-select-dropdown:visible button, .el-select-dropdown:visible .el-button')
              .filter({ hasText: /确认选择|确\s*定/ }).last();
            if (await confirm.count().catch(() => 0) && await isVisible(confirm).catch(() => false)) {
              await confirm.click({ timeout: 5000 }).catch(() => {});
            }
          } else {
            phase('option_click');
          }
          // 本土：点完直接读控件自身的值，命中就收工——不再走"读选项行 class + 5s 稳定回读 +
          // 诊断兜底"那套（选店铺现场 62s 全在那里）。跨境路径保持原样。
          if (panelCloseStrategy(options) === 'escape') {
            const controlValue = await waitForControlValue(frame, labels, value, 1500, options);
            if (controlValue) {
              if ((await item.locator('.el-select__tags').count().catch(() => 0))
                && (await isSelectPanelOpen(item))) {
                await closeOpenSelectPanels(frame, item, options);
              }
              phase('control_value');
              return { value: controlValue, method: 'control_value' };
            }
          }
          const deadline = Date.now() + 5000;
          let stableMatches = 0;
          while (Date.now() < deadline) {
            const refreshedItem = await formItemForLabels(frame, labels, options);
            const actualTexts = refreshedItem ? await selectedTextsForItem(refreshedItem) : [];            // 标签是用户可见的真值；选项的勾选 class 是同一状态的第二来源
            // （标签渲染偶发滞后时，class 仍能证明值已提交进控件）。
            const refreshedClass = String(await optionLocators.first().getAttribute('class').catch(() => '') || '');
            const refreshedAriaSelected = String(await optionLocators.first().getAttribute('aria-selected').catch(() => '') || '').toLowerCase();
            const matched = selectionEvidenceMatches(actualTexts, value, refreshedClass, refreshedAriaSelected);
            stableMatches = matched ? stableMatches + 1 : 0;
            if (stableMatches >= 5) {
              attrPickDebug({
                phase: 'verified', field: labels[0], target: value, method: 'element_ui',
                actual_texts: actualTexts, refreshed_class: refreshedClass, refreshed_aria: refreshedAriaSelected,
              });
              // 多选的面板不会自动收起：留着它会和后续属性的下拉同时可见，
              // 「.el-select-dropdown:visible」的选项搜索就会跨面板歧义。顺手收起。
              // 本土多选必须用 Esc 收：点触发箭头会把刚选好的值丢掉（现场实证）。
              const panelItem = refreshedItem || item;
              if ((await panelItem.locator('.el-select__tags').count().catch(() => 0))
                && (await isSelectPanelOpen(panelItem))) {
                await closeOpenSelectPanels(frame, panelItem, options);
              }
              phase('verified');
              return { value: actualTexts.join(', ') || value, method: 'element_ui' };
            }
            await new Promise((resolve) => setTimeout(resolve, 100));
          }
          const diagnosticItem = await formItemForLabels(frame, labels, options);
          const diagnosticInput = diagnosticItem ? await valueInputForItem(diagnosticItem) : null;
          const diagnosticTexts = diagnosticItem ? await selectedTextsForItem(diagnosticItem) : [];
          const diagnosticOptions = frame.locator('.el-select-dropdown:visible .el-select-dropdown__item').filter({ hasText: value });
          const diagnosticOption = (await diagnosticOptions.count().catch(() => 0)) === 1 ? diagnosticOptions.first() : null;
          const diagnosticClass = diagnosticOption
            ? String(await diagnosticOption.getAttribute('class').catch(() => '') || '')
            : '';
          const diagnosticAriaSelected = diagnosticOption
            ? String(await diagnosticOption.getAttribute('aria-selected').catch(() => '') || '').toLowerCase()
            : '';
          if (selectionEvidenceMatches(diagnosticTexts, value, diagnosticClass, diagnosticAriaSelected)) {
            // 走诊断兜底时面板同样是开着的：本土多选照样要用 Esc 收，否则值会被下一格
            // 的触发箭头点击连带丢掉。
            if ((await item.locator('.el-select__tags').count().catch(() => 0))
              && (await isSelectPanelOpen(item))) {
              await closeOpenSelectPanels(frame, item, options);
            }
            phase('diagnostic_return');
            return { value: diagnosticTexts.join(', ') || value, method: 'element_ui_diagnostic' };
          }
          phase('diagnostic_failed');
          throw new BlockedError('select_value_not_verified', {
            field: labels[0],
            target: value,
            actual_texts: diagnosticTexts,
            input_value: diagnosticInput ? cleanText(await diagnosticInput.inputValue().catch(() => '')) : '',
            placeholder: diagnosticInput ? cleanText(await diagnosticInput.getAttribute('placeholder').catch(() => '')) : '',
            option_class: diagnosticOption ? String(await diagnosticOption.getAttribute('class').catch(() => '') || '') : '',
            option_aria_selected: diagnosticOption ? String(await diagnosticOption.getAttribute('aria-selected').catch(() => '') || '').toLowerCase() : '',
          });
        }
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new BlockedError('select_option_not_found', { field: labels[0] });
}

// 点完选项后只读**控件自身的值**（多选=标签，单选/文本=输入值或显示值），命中即算提交成功。
// 现场（2026-09-27）：「选店铺」这一步 62s 几乎全耗在"读选项行 class + 5s 稳定回读 + 诊断兜底"
// 上（一次迭代就 60.8s，因为页面里累积了大量 popper，`:visible`+hasText 的选项定位器很贵）。
// 控件自己的值才是硬证据，也便宜得多。
async function waitForControlValue(frame, labels, target, timeoutMs = 1500, options = {}) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const item = await formItemForLabels(frame, labels, options);
    if (item) {
      const tagBased = (await item.locator('.el-select__tags').count().catch(() => 0)) > 0;
      const texts = tagBased ? await tagTextsForItem(item) : await selectedTextsForItem(item);
      const rawInput = tagBased ? '' : cleanText(await (await valueInputForItem(item)).inputValue().catch(() => ''));
      if (texts.some((source) => selectTextMatches(source, target)) || (rawInput && selectTextMatches(rawInput, target))) {
        return texts.length ? texts.join(', ') : rawInput;
      }
    }
    if (Date.now() >= deadline) return '';
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
}

// 选中项不在枚举里时（例如「原产地」要填 Philippines），走 Mabang 的"自填选项"：
// 搜索 → 添加 → 点新增项（→ 确认选择）。失败再抛原来的"选项不存在"。
async function selectElementOptionOrCustom(frame, item, labels, values, options = {}) {
  // 值不在枚举里时，马帮有两种渲染：
  //   a) 可编辑输入框（衣领类型 "Round Neck" 这类）→ 直接打字回填；
  //   b) readonly 下拉 + 「添加自填选项」（原产地 "Philippines"）→ 搜索→添加→点自填项。
  // 只能看控件本身才能区分，所以按 DOM 决定，而不是靠枚举猜。
  const targets = values.map((value) => String(value || '')).filter(Boolean);
  // 上一格残留的下拉面板会盖住这一格的控件：点击被它的选项 <li> 拦下并重试到 30s 超时。
  // 本土先把残留面板收干净（Esc 保住多选待提交值）；跨境保持原样。
  if (panelCloseStrategy(options) === 'escape') {
    await closeOpenSelectPanels(frame, null, options);
  }
  if (targets.length && !(await isSelectControl(item))) {
    const text = targets.join(', ');
    await setField(frame, labels, text, options);
    return { value: text, method: 'text_input' };
  }
  try {
    return await selectElementOption(frame, labels, values, options);
  } catch (error) {
    if (!(error instanceof BlockedError) || error.message !== 'select_option_not_found') throw error;
    for (const value of values) {
      const added = await addCustomSelectOption(frame, item, labels, value, options).catch((customError) => {
        if (customError instanceof BlockedError && customError.message === 'select_custom_value_not_verified') throw customError;
        return null;
      });
      if (added) return added;
    }
    throw error;
  }
}

// 把这一格在页面表单模型里的值清空，返回清空前的值。
// 强制自选通道前要先撤掉"只写页面表单模型"造出来的那个假值：现场 2026-10-06 真机证明，
// 控件里已经渲染着这个值（模型里是 ["T-shirt"]、自填分组是空的）时，走「搜索 → 添加 →
// 请输入自定义值 → ✔」不会把它注册成自选项，反而会把控件里的值清掉（执行器记
// custom_option_add_missed，before=["T-shirt"]、after=[]、registered=false）；
// 控件是空的时同一条通道能正确注册（模型变成 ["1791279221482"]、自填分组里有 T-shirt）。
async function clearAttributeValueInModel(frame, fieldId) {
  const id = String(fieldId || '').trim();
  if (!id) return null;
  return frame.evaluate(({ fieldId: wantedId }) => {
    let storeCategory = null;
    for (const element of document.querySelectorAll('*')) {
      for (let vm = element.__vue__, depth = 0; vm && depth < 12; vm = vm.$parent, depth += 1) {
        const candidate = vm.$refs && vm.$refs.storeCategory;
        if (candidate) { storeCategory = candidate; break; }
      }
      if (storeCategory) break;
    }
    const attributes = (storeCategory && storeCategory.ProductAttributeInfo && storeCategory.ProductAttributeInfo.attributesNew) || [];
    const attribute = attributes.find((entry) => String(entry.attribute_id ?? entry.id) === String(wantedId));
    if (!attribute) return { ok: false, reason: 'attribute_not_found' };
    const previous = attribute.value;
    const isArray = Array.isArray(previous);
    attribute.value = isArray ? [] : '';
    return { ok: true, previous, is_array: isArray };
  }, { fieldId: id }).catch(() => null);
}

// 页面模型里这一格是不是已经"注册过"同名自填选项（custom 分组里有它的选项 id）。
// 现场（2026-10-06）：自填行的 ✔ 一提交，马帮就把值注册成自选项并给一个平台 id
// （1791278718240），页面模型里 value 也换成那个 id。已经注册过就别再加一遍，避免同名重复项。
async function registeredCustomOption(frame, fieldId, values) {
  const id = String(fieldId || '').trim();
  const wanted = (values || []).map((value) => cleanText(value)).filter(Boolean);
  if (!id || !wanted.length) return null;
  return frame.evaluate(({ fieldId: wantedId, targets }) => {
    const norm = (value) => String(value ?? '').replace(/\s+/g, '').toLowerCase();
    let storeCategory = null;
    for (const element of document.querySelectorAll('*')) {
      for (let vm = element.__vue__, depth = 0; vm && depth < 12; vm = vm.$parent, depth += 1) {
        const candidate = vm.$refs && vm.$refs.storeCategory;
        if (candidate) { storeCategory = candidate; break; }
      }
      if (storeCategory) break;
    }
    const attributes = (storeCategory && storeCategory.ProductAttributeInfo && storeCategory.ProductAttributeInfo.attributesNew) || [];
    const attribute = attributes.find((entry) => String(entry.attribute_id ?? entry.id) === String(wantedId));
    if (!attribute || !Array.isArray(attribute.values)) return null;
    const custom = attribute.values.find((group) => String((group && group.id) || '') === 'custom')
      || attribute.values.find((group) => String((group && group.label) || '').includes('自填'));
    const options = (custom && Array.isArray(custom.options)) ? custom.options : [];
    for (const option of options) {
      const label = String((option && (option.label ?? option.name_cn ?? option.value)) ?? '');
      const value = String((option && option.value) ?? '');
      if (targets.some((target) => (label && norm(label) === norm(target)) || (value && norm(value) === norm(target)))) {
        return { value, label };
      }
    }
    return null;
  }, { fieldId: id, targets: wanted }).catch(() => null);
}

// 多选（tags 控件）判定：型号这类"页面上没有枚举、值又存成数组"的属性，控件是 tag 型 el-select。
// 只写页面模型会让它渲染出一个标签，但那个标签不是马帮登记过的选项。
async function isTagBasedControl(item) {
  if (!item) return false;
  return (await item.locator('.el-select__tags').count().catch(() => 0)) > 0;
}

// 自选值必须落到 UI 上：打开下拉 → 搜索框键入 → 点「添加」→ 自填行「请输入自定义值」→ ✔。
// 只写页面表单模型时，现场（2026-10-02）保存出来的草稿里型号是空的——所以这条路是必需的，
// 不是"锦上添花"。失败只记录不抛（整单不该因为一个可选属性被打死），由 setCategoryAttributes
// 汇总成 custom_option_ui_failed 报给操作员。
// settings.forceAdd（多选/tag 控件）：**不允许**拿"模型写完之后控件渲染出来的值"当成功——
//   现场 2026-10-06：执行器记了 already_selected（字段级 method 显示 custom_option_ui），
//   可页面模型里 101639 还是原始文字 ["T-shirt"]、自填分组是空的，马帮保存出来的草稿
//   型号为空；手工走一遍「搜索 → 添加 → 请输入自定义值 → ✔」后，模型变成
//   ["1791278718240"]、自填分组里有了 T-shirt。所以这类字段必须真的加一遍。
let customUiCallSeq = 0;
async function applyCustomValueThroughUi(frame, labels, values, options = {}, item = null, settings = {}) {
  const targets = (values || []).map((value) => String(value || '')).filter(Boolean);
  if (!targets.length) return null;
  let present = item || null;
  if (!present) present = await formItemForLabels(frame, labels, options).catch(() => null);
  if (!present) return { failed: true, error: 'custom_option_item_not_found' };
  const fieldId = String((settings && settings.fieldId) || '');
  const callSeq = ++customUiCallSeq;
  if (settings && settings.forceAdd) {
    attrPickDebug({ phase: 'custom_option_ui_enter', field: labels[0], call: callSeq, pid: process.pid, targets });
  }
  try {
    if (settings && settings.forceAdd) {
      // 自选项的最终目标：让**页面表单模型**持有马帮登记过的那个选项 id（保存时用的就是它）。
      // 现场 2026-10-06 真机：✔ 之后自填分组里有了 T-shirt 和它的 id，但控件/模型里的值
      // 会被随后的收面板（Esc / 点箭头）清掉；只要把 id 写回模型，控件立刻重新渲染出标签，
      // 保存也就带上型号了。所以每一步都以"注册结果 + 回写模型"收尾，而不是只看控件标签。
      const writeRegistered = async (option) => {
        if (!fieldId || !option || !option.value) return false;
        const written = await applyAttributeValueInModel(frame, fieldId, [option.value]).catch(() => null);
        return Boolean(written && written.ok);
      };
      const registeredBefore = fieldId ? await registeredCustomOption(frame, fieldId, targets) : null;
      if (registeredBefore) {
        const ok = await writeRegistered(registeredBefore);
        return {
          value: registeredBefore.label || targets.join(', '),
          method: ok ? 'custom_option_registered' : 'custom_option_registered_model_failed',
        };
      }
      // 先把"只写页面模型"造出来的值撤掉，让控件回到空态：控件里已经有这个值时，
      // 「添加」那一步不会把它注册成自选项，反而会把值清掉（见 clearAttributeValueInModel）。
      const cleared = await clearAttributeValueInModel(frame, fieldId);
      if (cleared && cleared.ok) {
        const emptyDeadline = Date.now() + 1500;
        while (Date.now() < emptyDeadline) {
          const texts = await selectedTextsForItem(present).catch(() => []);
          if (!texts.some((selected) => selectTextMatches(selected, targets[0]))) break;
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
      }
      for (const target of targets) {
        const before = await selectedTextsForItem(present).catch(() => []);
        const added = await addCustomSelectOption(frame, present, labels, target, options).catch((error) => {
          if (error instanceof BlockedError && error.message === 'select_custom_value_not_verified') throw error;
          attrPickDebug({
            phase: 'custom_option_add_threw', field: labels[0], target, before,
            reason: cleanText((error && error.message) || error).slice(0, 160),
          });
          return null;
        });
        // 不管 addCustomSelectOption 有没有认出"加好了"，都以页面模型里那份注册结果为准：
        // 有就回写模型收工（这才是保存真正读的值），没有才继续找别的方法。
        const registeredAfter = fieldId ? await registeredCustomOption(frame, fieldId, [target]) : null;
        if (registeredAfter) {
          const ok = await writeRegistered(registeredAfter);
          attrPickDebug({
            phase: 'custom_option_add_ok', field: labels[0], target, before, call: callSeq,
            method: (added && added.method) || '', model_written: ok,
          });
          return {
            value: registeredAfter.label || target,
            method: ok ? (added ? 'custom_option' : 'custom_option_registered') : 'custom_option_model_write_failed',
          };
        }
        // 有的版本加完就把值提交进控件、自填项又不带 .el-select-dropdown__item 类：
        // 回读一次控件确认这格真的带上了这个值就收工（别去点那行）。
        const echoed = await customValueEchoed(frame, present, labels, fieldId, target);
        attrPickDebug({
          phase: 'custom_option_add_missed', field: labels[0], target, before, call: callSeq,
          echo: echoed || '', after: await selectedTextsForItem(present).catch(() => []),
        });
        if (echoed) return { value: target, method: echoed };
      }
      // 一路失败：把刚才清掉的值写回页面模型（等于退回"至少模型里有值"的旧行为），
      // 再如实报失败，由调用方记成 custom_option_ui_failed。
      if (cleared && cleared.ok && targets.length) {
        await applyAttributeValueInModel(frame, fieldId, targets).catch(() => null);
      }
      return { failed: true, error: 'custom_option_add_failed' };
    }
    const applied = await selectElementOptionOrCustom(frame, present, labels, targets, options);
    if (!applied) return { failed: true, error: 'custom_option_not_applied' };
    return applied;
  } catch (error) {
    const reason = cleanText((error && (error.reason || error.message)) || 'custom_option_failed');
    attrPickDebug({ phase: 'custom_option_blocked', field: labels[0], reason });
    return { failed: true, error: reason };
  }
}

// 强制自选通道的"这格真的带上了值吗"回读：控件里有它（tag/已选文本）或页面模型的自填分组里
// 注册了它，都算成功。返回方法名（拿不到证据返回空串）。
async function customValueEchoed(frame, item, labels, fieldId, target) {
  const texts = item ? await selectedTextsForItem(item).catch(() => []) : [];
  if (texts.some((selected) => selectTextMatches(selected, target))) return 'custom_option_ui';
  if (fieldId) {
    const registered = await registeredCustomOption(frame, fieldId, [target]);
    if (registered) return 'custom_option_registered';
  }
  // 控件定位器可能已经过期（属性区重渲染）：重新找一次表单项再回读。
  const fresh = await formItemForLabels(frame, labels, {}).catch(() => null);
  const freshTexts = fresh ? await selectedTextsForItem(fresh).catch(() => []) : [];
  if (freshTexts.some((selected) => selectTextMatches(selected, target))) return 'custom_option_ui';
  return '';
}

async function selectStore(frame, task) {
  const store = task.store || {};
  const globalMode = String(store.listing_mode || '') === 'cnsc_global';
  const cnscStore = store.cnsc_store || {};
  const labels = globalMode
    ? ['CNSC店铺', '全球商品店铺', TEXT.storeName, '\u5e97\u94fa']
    : [TEXT.storeName, '\u5e97\u94fa'];
  const values = globalMode
    ? [cnscStore.name, cnscStore.id, store.name, store.mabang_store_id].filter(Boolean)
    : [store.name, store.mabang_store_id].filter(Boolean);
  // 本土要带上站点容错标志：选完直接读控件自身的值就收工（现场这一步 62s 全耗在
  // "读选项行 class + 5s 稳定回读 + 诊断兜底"上）；跨境保持原样。
  const result = await selectElementOption(frame, labels, values, globalMode ? {} : siteSelectOptions());
  emit('stage', { stage: 'select_store', ok: true });
  return result;
}

async function waitForCategoryPathSegment(dialog, segment, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  const expected = cleanText(segment).toLocaleLowerCase();
  // The category tree loads asynchronously behind an .el-loading-mask. Polling the
  // option list while it is still loading burns the whole budget on empty DOM, so
  // wait for the mask to clear first. If it never clears we fall through and the
  // poll loop reports the usual segment timeout.
  await waitForNoLoadingMask(dialog, 10000).catch(() => {});
  while (Date.now() < deadline) {
    const options = dialog.locator('label.el-transfer-panel-item');
    const matches = [];
    for (let index = 0, count = await options.count(); index < count; index += 1) {
      const option = options.nth(index);
      if (!(await isVisible(option))) continue;
      const text = cleanText(await option.innerText()).toLocaleLowerCase();
      if (text === expected || text.includes(`(${expected})`)) {
        matches.push({ option, text });
      }
    }
    if (matches.length === 1) return matches[0].option;
    if (matches.length > 1) {
      // The Mabang category picker renders the full option list twice within one
      // column, so a segment legitimately resolves to several identical entries.
      // When every match is the same category label, they are duplicates of one
      // node — pick the first. Only differing labels are a real ambiguity.
      const distinct = new Set(matches.map((match) => match.text));
      if (distinct.size === 1) return matches[0].option;
      throw new BlockedError('category_path_segment_not_unique', {
        segment,
        count: matches.length,
        texts: Array.from(distinct),
      });
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new BlockedError('category_segment_timeout', { segment });
}

async function selectedCategoryId(frame) {
  const item = await formItemForLabels(frame, [TEXT.productCategory]);
  if (!item) return '';
  return item.evaluate((element) => {
    let vm = element.__vue__;
    while (vm && vm.$options && vm.$options._componentTag !== 'storeCategoryModule') vm = vm.$parent;
    const categorySettings = vm && (vm.categorySettings || (vm.$data && vm.$data.categorySettings));
    const values = categorySettings && Array.isArray(categorySettings.categoryIdAll)
      ? categorySettings.categoryIdAll
      : [];
    return String(values.at(-1) || '');
  });
}

async function selectCategory(frame, task) {
  const category = task.category || {};
  const currentCategoryId = await selectedCategoryId(frame);
  if (currentCategoryId && currentCategoryId === String(category.mabang_category_id || '')) {
    emit('stage', { stage: 'select_category', ok: true, method: 'already_selected_id' });
    return { method: 'already_selected_id', category_id: currentCategoryId };
  }
  const body = cleanText(await frame.locator('body').innerText());
  if (category.path && body.includes(cleanText(category.path))) {
    emit('stage', { stage: 'select_category', ok: true, method: 'already_selected' });
    return { method: 'already_selected' };
  }
  await clickExact(frame, TEXT.chooseCategory);
  const dialogDeadline = Date.now() + 5000;
  let dialog = frame.locator('.el-dialog:visible,.el-message-box:visible').last();
  while (Date.now() < dialogDeadline && !(await isVisible(dialog))) {
    await sleep(100);
    dialog = frame.locator('.el-dialog:visible,.el-message-box:visible').last();
  }
  if (!(await isVisible(dialog))) throw new BlockedError('category_dialog_not_visible');
  const segments = String(category.path || '').split('>').map((value) => value.trim()).filter(Boolean);
  if (!segments.length) throw new BlockedError('category_path_missing');
  for (const segment of segments) {
    const option = await waitForCategoryPathSegment(dialog, segment);
    await option.click();
  }
  const categoryWrapper = frame.locator('.el-dialog__wrapper.category').first();
  const idVerified = await categoryWrapper.evaluate((element, expectedId) => {
    let vm = element.__vue__;
    while (vm && vm.$options && vm.$options._componentTag !== 'selectCategoryDialog') vm = vm.$parent;
    const values = Array.isArray(vm && vm.categoryIdAll) ? vm.categoryIdAll : [];
    return String(values.at(-1) || '') === String(expectedId || '');
  }, category.mabang_category_id);
  if (!idVerified) throw new BlockedError('category_id_not_verified');
  // After the last segment click the category dialog re-renders with a loading
  // mask while it persists the selection; clicking 确定 through that mask has
  // caused the entire 30s Playwright click budget to burn on retries. Wait for
  // the dialog to be interactive again before confirming.
  await waitForNoLoadingMask(dialog, 15000);
  await clickExact(frame, TEXT.confirm);
  await dialog.waitFor({ state: 'hidden', timeout: 5000 });
  emit('stage', { stage: 'select_category', ok: true });
  return { method: 'category_path', category_id: String(category.mabang_category_id) };
}

const ATTRIBUTE_LABELS = Object.freeze({
  brand: ['\u54c1\u724c'],
  material: ['\u6750\u6599', 'Material'],
  pattern: ['\u56fe\u6848', 'Pattern'],
  sleeve_length: ['\u5957\u7b52\u957f\u5ea6', '\u8896\u957f', 'Sleeve'],
  plus_size: ['\u52a0\u53f7', '\u52a0\u5927\u7801', 'Plus Size'],
});

async function findFormItemPresent(frame, labels, timeoutMs = 2000, options = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const item = await formItemForLabels(frame, labels, options);
    if (item) return item;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return null;
}

// 本土类目属性页默认只渲染必填属性，其余（推荐年龄/图案/季节/风格/顶部长度/定制产品/
// 尺寸…）折在「显示所有属性」按钮后面。计划里的属性在表单上找不到时先点开一次再找，
// 否则这些值会被当成"页面没有该属性"静默跳过，刊登里其实是空的。
// 展开后同一个按钮的文案变成「隐藏所有属性」，用它还原。
async function expandHiddenCategoryAttributes(frame, timeoutMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const toggles = frame.locator('button.el-button--text', { hasText: '显示所有属性' });
    for (let index = 0, count = await toggles.count().catch(() => 0); index < count; index += 1) {
      const toggle = toggles.nth(index);
      if (!(await isVisible(toggle))) continue;
      await toggle.click({ timeout: 5000 }).catch(() => null);
      await new Promise((resolve) => setTimeout(resolve, 500));
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return false;
}

// 现场读取马帮页面的类目属性（含必填标记与选项枚举）：照 page_capture.js 的
// readCategoryAttributes 走 Vue（storeCategory.ProductAttributeInfo.attributesNew），
// 比逐个开下拉面板稳得多。返回 [{field_id, label, type, required, options}]。
async function readLiveCategoryAttributes(frame) {
  return frame.evaluate(() => {
    const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
    let storeCategory = null;
    for (const element of document.querySelectorAll('*')) {
      for (let vm = element.__vue__, depth = 0; vm && depth < 12; vm = vm.$parent, depth += 1) {
        const candidate = vm.$refs && vm.$refs.storeCategory;
        if (candidate) { storeCategory = candidate; break; }
      }
      if (storeCategory) break;
    }
    const attributes = Array.isArray(storeCategory && storeCategory.ProductAttributeInfo && storeCategory.ProductAttributeInfo.attributesNew)
      ? storeCategory.ProductAttributeInfo.attributesNew : [];
    const asList = (value) => (Array.isArray(value) ? value : value == null || value === '' ? [] : [value]);
    const normalizeOptions = (attribute) => {
      const groups = Array.isArray(attribute && attribute.values) ? attribute.values : [];
      const candidates = groups.flatMap((group) => (Array.isArray(group && group.options) ? group.options : [group]));
      const legacy = Array.isArray(attribute && attribute.attribute_value_list) ? attribute.attribute_value_list : [];
      const seen = new Set();
      return [...candidates, ...legacy].map((option) => ({
        value: clean(option && (option.value ?? option.value_id ?? option.id)),
        label: clean(option && (option.label ?? option.name_cn ?? option.name ?? option.value)),
      })).filter((option) => option.value && !seen.has(option.value) && seen.add(option.value));
    };
    const inputTypeOf = (attribute) => {
      let raw = attribute && attribute.attribute_info;
      if (typeof raw === 'string') { try { raw = JSON.parse(raw); } catch (_e) { raw = {}; } }
      const value = clean((raw && (raw.input_type ?? raw.inputType)) ?? '');
      return /^\d+$/.test(value) ? value : '';
    };
    const typeOf = (attribute) => {
      const inputType = inputTypeOf(attribute);
      const multiValue = Array.isArray(attribute && attribute.value);
      const options = normalizeOptions(attribute);
      if (inputType === '3') return 'text';
      if (multiValue) return (options.length || asList(attribute && attribute.value).length > 1) ? 'multi_select' : 'text';
      if (options.length) return 'single_select';
      return 'text';
    };
    return attributes.map((attribute) => ({
      field_id: clean(attribute && (attribute.attribute_id ?? attribute.id)),
      label: clean(attribute && (attribute.name_cn ?? attribute.name ?? attribute.attribute_name)),
      type: typeOf(attribute),
      required: String((attribute && attribute.mandatory) ?? '') === '1' || (attribute && attribute.required) === true,
      options: normalizeOptions(attribute),
    })).filter((field) => field.field_id && field.label);
  }).catch(() => []);
}

// 类目属性定义是在选完类目之后由页面异步拉取的：选完立刻读经常拿到空数组，
// 于是快照里的属性值没法映射成当前页面标签，页面必填属性也不会进 AI 补齐
// （现场 2026-09-27 本土任务 71181e90：3 次 attempt 有 2 次 live=[]，另一格
//  attributesNew 只到 11 条）。本土(site) 等属性定义就绪再返回；跨境
//  (cnsc_global) 保持原来的单次读取，改动需要单独验证。
async function readLiveCategoryAttributesWhenReady(frame, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const fields = await readLiveCategoryAttributes(frame);
    if (fields.length || Date.now() >= deadline) return fields;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
}

// 副本快照里的「选项 id → 标签」对照表。
// 现场（2026-10-02）：店铺模板把「型号(101639)」存成的是下拉选项 id（1790002081766），而该类目
// 页面上的型号下拉是空的（暂无数据）→ 值匹配不上任何选项，执行器就只好把这串 id 当"自定义值"
// 打进「搜索 → 添加 → 请输入自定义值」那条通道，刊登上型号就成了 1790002081766。
// 快照里那份 options 正是当初从页面采到的 id→标签对照，用它把 id 翻回人能读的文字。
function snapshotAttributeOptionLabels(snapshot) {
  const map = new Map();
  for (const field of (snapshot && Array.isArray(snapshot.fields)) ? snapshot.fields : []) {
    const fieldId = String((field && field.field_id) || '').trim();
    const options = Array.isArray(field && field.options) ? field.options : [];
    if (!fieldId || !options.length) continue;
    const perField = map.get(fieldId) || new Map();
    for (const option of options) {
      const value = String((option && option.value) ?? '').trim();
      const label = String((option && option.label) ?? '').trim();
      if (value && label && value !== label) perField.set(value, label);
    }
    if (perField.size) map.set(fieldId, perField);
  }
  return map;
}

// 看起来像平台内部选项 id 的值（型号那串 1790002081766 = 13 位纯数字）。
// 这种值**不能**当"自定义值"打到页面上——打上去就是错的内容。
function looksLikeOptionId(value) {
  return /^\d{10,}$/.test(String(value ?? '').trim());
}

// 将店铺模板保存的数字属性值映射成当前马帮页面能识别的标签。类目快照可能
// 比页面旧，现场 options 才是填写时的真值；模板值优先于 AI，避免每次重试都
// 重新猜测一个已经由运营确认过的属性。
// snapshotLabels：副本快照的 id→标签对照（见 snapshotAttributeOptionLabels），
// 用来把"存成 id"的模板值（型号）翻成人能读的文字。
function resolveTemplateAttributeFields(liveFields, categoryAttributes, snapshotLabels = null) {
  const attributes = categoryAttributes && typeof categoryAttributes === 'object'
    ? categoryAttributes : {};
  const snapshotLabelMap = snapshotLabels instanceof Map ? snapshotLabels : new Map();
  const resolved = [];
  for (const field of Array.isArray(liveFields) ? liveFields : []) {
    const fieldId = String(field && field.field_id || '').trim();
    if (!fieldId || !Object.prototype.hasOwnProperty.call(attributes, fieldId)) continue;
    const rawValues = Array.isArray(attributes[fieldId]) ? attributes[fieldId] : [attributes[fieldId]];
    const values = rawValues.map((value) => String(value ?? '').trim()).filter(Boolean);
    if (!values.length) continue;
    const options = Array.isArray(field.options) ? field.options : [];
    const optionFor = (value) => options.find((option) => (
      selectTextMatches(option && option.value, value)
      || selectTextMatches(option && option.label, value)
    ));
    const matchedOptions = values.map(optionFor);
    const hasEnum = options.length > 0;
    const allMatch = hasEnum && matchedOptions.every(Boolean);
    const perFieldSnapshotLabels = snapshotLabelMap.get(fieldId) || new Map();
    // 每个值算一处"能安全写进页面"的文本与 id：
    //   - 页面现场有枚举且匹配上 → 用它的 id + 标签（老路径，不动）；
    //   - 匹配不上但快照里有 id→标签 → 用**标签**（型号 1790002081766 → T-shirt）；
    //   - 两边都没有、值本身又长得像选项 id → 不安全，整格不填、如实报错。
    const entries = values.map((value, index) => {
      const live = matchedOptions[index];
      const snapshotLabel = perFieldSnapshotLabels.get(String(value)) || '';
      const text = String((live && live.label) || snapshotLabel || value).trim();
      return {
        text,
        id: live ? String(live.value || value).trim() : '',
        safe: Boolean(live || snapshotLabel) || !looksLikeOptionId(text),
      };
    }).filter((entry) => entry.text);
    const safeEntries = entries.filter((entry) => entry.safe);
    const blockedByOptionId = entries.length > 0 && safeEntries.length === 0;
    const optionLabels = (blockedByOptionId ? [] : safeEntries).map((entry) => entry.text);
    const valueIds = values.map((value, index) => String(
      (matchedOptions[index] && matchedOptions[index].value) || value,
    ).trim()).filter(Boolean);
    // 只有真的匹配到页面枚举的 id 才允许按 id 写进页面表单模型；匹配不上的原值
    //（典型就是那串选项 id）混进去会在页面模型里留下 id 和文字两个值。
    const matchedValueIds = (blockedByOptionId ? [] : safeEntries)
      .map((entry) => entry.id).filter(Boolean);
    resolved.push({
      field_id: fieldId,
      label: String(field.label || fieldId),
      type: String(field.type || ''),
      required: Boolean(field.required),
      value_ids: valueIds,
      matched_value_ids: matchedValueIds,
      option_labels: optionLabels,
      unresolved_option_id: blockedByOptionId,
      input_kind: String(field.type || '').toLowerCase() === 'text' || !hasEnum
        ? 'text'
        : allMatch ? 'select' : 'select_custom',
    });
  }
  return resolved;
}

// 现场自动判断必填项：快照没覆盖的页面必填属性 → 调本地 AI 推荐端点拿值 →
// value 经现场 options 映射成 label 合并进 resolvedFields 走既有填写路径。
// AI 失败/超时只发 stage 记录，不中断（马帮保存闸是最后兜底）。
async function autoFillMissingRequiredAttributes(frame, task, page, resolvedFields) {
  const apiUrl = cleanText(process.env.MABANG_TASK_API_URL || 'http://127.0.0.1:8788');
  if (!page || !apiUrl) return resolvedFields;
  let preferredFields = resolvedFields;
  try {
    // 本土：等类目属性定义加载完再读；跨境保持单次读取（未单独验证前不改动）。
    const siteMode = String(task.store?.listing_mode || '') !== 'cnsc_global';
    const live = siteMode
      ? await readLiveCategoryAttributesWhenReady(frame)
      : await readLiveCategoryAttributes(frame);
    const required = (live || []).filter((field) => field.required);
    try {
      fs.mkdirSync('tmp_upload', { recursive: true });
      fs.appendFileSync('tmp_upload/auto-fill-debug.log', JSON.stringify({
        at: new Date().toISOString(), listing_mode: task.store && task.store.listing_mode,
        live: (live || []).map((f) => ({ id: f.field_id, label: f.label, required: f.required, opts: (f.options || []).length })),
      }) + '\n');
    } catch (_e) { /* debug log best-effort */ }
    const templateFields = resolveTemplateAttributeFields(
      live,
      task.category_attributes || task.attribute_values || task.listing_copy?.attribute_values || {},
      // 副本快照自带 id→标签对照：模板把「型号」存成选项 id 时靠它翻回文字。
      snapshotAttributeOptionLabels(task.category_profile_snapshot || task.listing_copy?.category_profile_snapshot),
    );
    const templateIds = new Set(templateFields.map((field) => String(field.field_id)));
    const mergedSnapshot = [
      ...templateFields,
      ...resolvedFields.filter((field) => !templateIds.has(String(field.field_id))),
    ];
    preferredFields = mergedSnapshot;
    emit('stage', {
      stage: 'auto_fill_required_attributes', ok: true, method: 'template_first',
      live_attrs: (live || []).length, page_required: required.length,
      snapshot_fields: resolvedFields.length, template_fields: templateFields.length,
    });
    if (!required.length) return mergedSnapshot;
    const known = new Set(mergedSnapshot.map((field) => String(field.field_id)));
    const missing = required.filter((field) => !known.has(String(field.field_id)));
    if (!missing.length) {
      emit('stage', { stage: 'auto_fill_required_attributes', ok: true, filled: 0, page_required: required.length, snapshot_missing: 0, method: 'template_covers' });
      return mergedSnapshot;
    }
    const basic = task.basic_info || {};
    const listingCopy = task.listing_copy || {};
    const response = await page.context().request.post(`${apiUrl}/api/ai/attribute-recommendations`, {
      headers: { 'content-type': 'application/json' },
      data: {
        fields: missing.map((field) => ({ field_id: field.field_id, label: field.label, required: true, type: field.type, options: field.options })),
        context: {
          product_name: cleanText(basic.title),
          description: cleanText(basic.description).slice(0, 600),
          category_path: cleanText((task.category || {}).path),
          recognized_attributes: listingCopy.recognized_attributes || task.recognized_attributes || {},
          recognized_colors: listingCopy.recognized_colors || task.recognized_colors || [],
        },
      },
      timeout: 120000,
    });
    if (!response.ok()) {
      emit('stage', { stage: 'auto_fill_required_attributes', ok: false, error: `ai_http_${response.status()}` });
      return mergedSnapshot;
    }
    const body = await response.json().catch(() => ({}));
    const values = (body && body.values) || {};
    try {
      fs.appendFileSync('tmp_upload/auto-fill-debug.log', JSON.stringify({
        at: new Date().toISOString(), ai_status: response.status(), missing: missing.map((f) => f.field_id), values,
      }) + '\n');
    } catch (_e) { /* debug log best-effort */ }
    const byId = new Map(missing.map((field) => [String(field.field_id), field]));
    const merged = [...mergedSnapshot];
    let filled = 0;
    let skipped = 0;
    for (const [fieldId, value] of Object.entries(values)) {
      const liveField = byId.get(String(fieldId));
      if (!liveField) continue;
      const rawValues = (Array.isArray(value) ? value : [value]).map((item) => String(item || '').trim()).filter(Boolean);
      const liveOptions = Array.isArray(liveField.options) ? liveField.options : [];
      const matchedOptions = rawValues.map((raw) => liveOptions.find((option) => (
        selectTextMatches(option && option.value, raw)
        || selectTextMatches(option && option.label, raw)
      )));
      // AI 只能补页面已有枚举，未知值丢弃；文本属性保留原始字符串。
      if (liveOptions.length && matchedOptions.some((option) => !option)) { skipped += 1; continue; }
      const valueIds = rawValues.map((raw, index) => String((matchedOptions[index] && matchedOptions[index].value) || raw).trim()).filter(Boolean);
      if (!valueIds.length) { skipped += 1; continue; }
      // 只有真的匹配到页面枚举的值才按 id 写；其余（AI 给的文字值）只走 option_labels。
      const matchedValueIds = rawValues
        .map((raw, index) => (matchedOptions[index] ? String(matchedOptions[index].value || raw).trim() : ''))
        .filter(Boolean);
      const labelByValue = new Map(liveOptions.map((option) => [String(option.value), String(option.label || option.value)]));
      merged.push({
        field_id: String(fieldId),
        label: liveField.label,
        type: liveField.type,
        required: true,
        value_ids: valueIds,
        matched_value_ids: matchedValueIds,
        option_labels: rawValues.map((raw, index) => String((matchedOptions[index] && matchedOptions[index].label) || labelByValue.get(valueIds[index]) || raw)),
        input_kind: liveField.type === 'text' || !liveOptions.length ? 'text' : 'select',
      });
      filled += 1;
    }
    // 写回 task：重试 attempt 会重建 resolvedFields（现场抓取偶发为空），
    // 把 AI 补的必填项留在 task 上，重试时不再依赖现场抓取。
    for (const field of merged.slice(mergedSnapshot.length)) {
      if (!Array.isArray(task.category_attribute_fields)) task.category_attribute_fields = [];
      if (!task.category_attribute_fields.some((existing) => String(existing.field_id) === String(field.field_id))) {
        task.category_attribute_fields.push(field);
      }
    }
    emit('stage', { stage: 'auto_fill_required_attributes', ok: true, filled, skipped, page_required: required.length, snapshot_missing: missing.length });
    return merged;
  } catch (error) {
    emit('stage', { stage: 'auto_fill_required_attributes', ok: false, error: cleanText(error && error.message) || 'auto_fill_failed' });
    return preferredFields;
  }
}

async function setCategoryAttributes(frame, task, page) {
  // 现场自动判断必填项：页面必填属性里快照没覆盖的，先调 AI 补齐再进入逐项填写。
  const resolvedFields = await autoFillMissingRequiredAttributes(
    frame,
    task,
    page,
    Array.isArray(task.category_attribute_fields) ? task.category_attribute_fields : [],
  );
  const results = {};
  if (resolvedFields.length) {
    // Preferred path: match by the human-readable field label and option labels
    // resolved from the category profile snapshot. The raw Shopee attribute IDs
    // (e.g. 100037) never appear on the form, so they can't be matched directly.
    // Mabang renders only the REQUIRED attributes by default; the rest sit behind the
    // 「显示所有属性」 toggle. Optional attributes that are still absent afterwards are
    // skipped rather than blocking the whole draft.
    // 本土（site）与跨境的匹配策略分开：下面这些容错只作用于本土站点刊登，
    // 跨境（cnsc_global）保持原样，避免影响已验证过的 CNSC 填单路径。
    const siteMode = String(task.store?.listing_mode || '') !== 'cnsc_global';
    // 本土：类目属性下拉按现场证实的交互收尾（多选面板用 Esc 收，保住待提交值）；
    // 跨境(cnsc_global) 保持原样，改动需要单独验证。
    const matchOptions = siteMode ? { loose: true, siteMultiSelectHandling: true } : {};
    let hiddenExpanded = false;
    const skipped = [];
    // 值只能查到"选项 id"、查不到文字的属性（现场：型号被存成 1790002081766）：不填、报出来。
    const unresolvedOptionIdFields = [];
    // 自选值走 UI「搜索 → 添加」失败的字段（失败不阻断整单，但要报出来）。
    const customOptionUiFailures = [];
    // 自选值真的走 UI 添加成功的字段（执行详情里能看见"这个值是添加出来的"）。
    const customOptionUiApplied = [];
    // 页面上没有枚举的属性（条件 100413、型号 101639…）：判定方式跟"条件"保持一致——
    // 认控件回读值 + 页面表单模型的值，不死等多选 tag，也不去点那个根本不存在的
    // 「添加自选」入口（现场：型号 的下拉里没有这个入口，一点就整条流程失败）。
    const modelEvidenceFields = new Set(
      resolvedFields
        .filter((field) => !(field.option_labels || []).map((value) => String(value || '')).filter(Boolean).length)
        .map((field) => String(field.field_id)),
    );
    // 本土：先**一次 evaluate** 把所有能解析到的值写进页面表单模型，循环里只做渲染校验。
    // 逐个写=每个属性一次往返（现场 15 个属性 30.4s）；批量写后整体校验 ≈ 5s。
    const prewritten = new Map();
    const prewrittenLabels = new Map();
    if (siteMode) {
      // 先把隐藏属性展开（马帮默认只渲染必填属性，其余折在「显示所有属性」后面）：
      // 现场 15 个属性里只有 4 个必填可见，另外 11 个在批量校验里全是 item_not_found，
      // 于是又掉回逐个等待（每个 ~1.7s）。
      const expandStartedAt = Date.now();
      if (await expandHiddenCategoryAttributes(frame, 3000)) {
        hiddenExpanded = true;
        attrPickDebug({ phase: 'attributes_expanded', ms: Date.now() - expandStartedAt });
      }
      const batch = [];
      for (const field of resolvedFields) {
        if (field.unresolved_option_id) continue;
        const fieldLabels = (field.option_labels || []).map((value) => String(value || '')).filter(Boolean);
        // 只把"真的匹配到页面枚举的 id"加进去：匹配不上的原值（模板存的那串选项 id）混进来
        // 会在页面表单模型里同时留下 id 和文字两个值。文字由 option_labels 提供。
        const values = [...(field.matched_value_ids ?? field.value_ids ?? []), ...fieldLabels].filter(Boolean);
        if (values.length) {
          batch.push({ field_id: field.field_id, values });
          prewrittenLabels.set(String(field.field_id), [field.label, String(field.field_id)].filter(Boolean));
        }
      }
      if (batch.length) {
        const batchStartedAt = Date.now();
        const outcome = await applyAttributeValuesInModel(frame, batch);
        for (const item of outcome.results || []) {
          if (item.ok) prewritten.set(String(item.field_id), item);
          else attrPickDebug({ phase: 'model_write_failed', field: item.field_id, reason: item.reason, detail: item.value || '' });
        }
        attrPickDebug({ phase: 'model_batch_write', fields: batch.length, written: prewritten.size, ms: Date.now() - batchStartedAt });
      }
    }
    // 批量校验渲染（一次 evaluate 读完全部字段）：命中的字段在下面循环里直接跳过，
    // 没命中的才走逐字段等待 + 点选兜底。逐个校验的代价是现场 15 个属性 27.6 秒。
    const confirmed = new Set();
    if (prewritten.size) {
      const verifyStartedAt = Date.now();
      const targets = [...prewritten.values()].map((entry) => ({
        field_id: String(entry.field_id),
        labels: prewrittenLabels.get(String(entry.field_id)) || [],
        expected: (entry.labels && entry.labels.length) ? entry.labels : [],
        // 页面上没有枚举的属性（条件 100413、型号 101639…）按"条件那套逻辑"判定：
        // 认控件回读值 + 页面表单模型里的值，不死等 tag。
        model_evidence: modelEvidenceFields.has(String(entry.field_id)),
      })).filter((entry) => entry.expected.length);
      for (let attempt = 0; attempt < 12 && confirmed.size < targets.length; attempt += 1) {
        const outcome = await attributeValuesRendered(frame, targets.filter((entry) => !confirmed.has(entry.field_id)));
        for (const item of outcome || []) {
          if (item.rendered) confirmed.add(String(item.field_id));
        }
        if (confirmed.size >= targets.length) break;
        // 富表单的 Vue 重渲染要时间：多等几轮（每轮 ~400ms）比逐个字段各等 3s 划算得多。
        if (attempt === 5 || attempt === 11) {
          const pending = (outcome || []).filter((item) => !item.rendered).slice(0, 3)
            .map((item) => ({ field: item.field_id, reason: item.reason || '', sources: item.sources || [] }));
          attrPickDebug({ phase: 'model_batch_verify_pending', attempt, confirmed: confirmed.size, pending });
        }
        if (attempt >= 5) await sleep(400);
        else await sleep(200);
      }
      attrPickDebug({ phase: 'model_batch_verify', fields: targets.length, confirmed: confirmed.size, ms: Date.now() - verifyStartedAt });
    }
    for (const field of resolvedFields) {
      const optionLabels = (field.option_labels || []).map((value) => String(value || '')).filter(Boolean);
      const labels = [field.label, String(field.field_id)].filter(Boolean);
      // 值是平台内部选项 id、页面枚举和副本快照里都查不到对应文字（历史数据把「型号」存成了
      // 1790002081766 这种 id）：绝不把这串数字当"自定义值"打进刊登，如实报在结果里等人处理。
      if (field.unresolved_option_id) {
        unresolvedOptionIdFields.push({ field: labels[0], field_id: String(field.field_id), values: field.value_ids || [] });
        results[field.field_id] = { value: '', method: 'model_failed', note: 'option_id_not_resolved' };
        continue;
      }
      // 表单里没有这个属性：可选属性直接跳过（马帮默认只渲染必填属性）；
      // 必填属性则明确报"字段未找到"，而不是退回整页扫描后报一个误导性的
      // field_not_unique（count 会是全页输入框数量）。
      let present = await findFormItemPresent(frame, labels, 2000, matchOptions);
      if (!present && siteMode && !hiddenExpanded) {
        // 只尝试展开一次：展开后仍在的属性才是真的不在表单上。
        hiddenExpanded = true;
        if (await expandHiddenCategoryAttributes(frame)) {
          present = await findFormItemPresent(frame, labels, 2000, matchOptions);
        }
      }
      if (!present) {
        // 页面上没渲染出来（马帮默认只渲染必填项，可选项折在「显示所有属性」后面）：
        // 这类"没有枚举"的属性（条件/型号）值其实已经写进页面表单模型了——页面保存时用的
        // 就是模型，所以跟条件一样按已填处理；旧代码会在这里当"可选属性缺失"直接跳过，
        // 接着又掉进型号根本没有的「添加自选」流程，把整条链路带崩。
        if (siteMode && modelEvidenceFields.has(String(field.field_id)) && prewritten.has(String(field.field_id))) {
          const applied = prewritten.get(String(field.field_id));
          results[field.field_id] = {
            value: (applied.labels || []).join(', '),
            method: applied.custom ? 'model_custom' : 'model',
            note: 'model_value_item_hidden',
          };
          continue;
        }
        if (!field.required) {
          skipped.push(field.field_id);
          continue;
        }
        throw new BlockedError('select_field_not_found', { field: labels[0] });
      }
      // 本土属性已在上面的批量写入里进了页面表单模型（attributesNew[i].value）——它既是页面
      // 保存时真正使用的数据源，也是唯一能可靠落值的通道（点选的多选值只停在"待提交"态，
      // 收面板或属性区重渲染就丢）。这里只等 UI 把它渲染出来；渲染不出来才退回点选/打字路径。
      const candidateValues = [...(field.matched_value_ids ?? field.value_ids ?? []), ...optionLabels].filter(Boolean);
      if (siteMode && candidateValues.length) {
        const applied = prewritten.get(String(field.field_id)) || await applyAttributeValueInModel(frame, field.field_id, candidateValues);
        // 自选值（页面枚举里没有这个值——型号就是）**必须**在 UI 上真的"添加"出来。
        // 现场（2026-10-02）：写页面表单模型时页面上有 tag、模型里也是 ['T-shirt']，但马帮保存
        // 出来的草稿里型号是空的——那次只写了模型，没走「搜索 → 添加 → 请输入自定义值 → ✔」。
        // 所以这里对 custom 值不再认"模型写成功"这条捷径，交给 UI 自选通道；UI 失败才退回模型结果
        // （并写成 warning 让人看见，不再假装成功）。
        if (applied.ok && applied.custom) {
          // 多选（tags 控件）上的自选值：模型写进去只让控件"看起来有值"（页面 tag 是渲染出来的，
          // 不是马帮登记过的选项）——现场 2026-10-06 真机：执行器记 already_selected、草稿里型号为空，
          // 手工走一遍「搜索 → 添加 → 请输入自定义值 → ✔」后页面模型才变成马帮给的选项 id。
          // 所以这类字段强制走 UI 自选通道；单选/文本属性的老路径不动（它们的模型值本来就能存下来）。
          const forceAdd = await isTagBasedControl(present);
          const viaUi = await applyCustomValueThroughUi(
            frame,
            labels,
            optionLabels.length ? optionLabels : candidateValues,
            matchOptions,
            present,
            { forceAdd, fieldId: String(field.field_id) },
          );
          if (viaUi && !viaUi.failed) {
            customOptionUiApplied.push({
              field: labels[0],
              field_id: String(field.field_id),
              method: viaUi.method || '',
              value: (applied.labels || []).join(', '),
            });
            results[field.field_id] = {
              value: (applied.labels || []).join(', '),
              method: 'custom_option_ui',
              note: viaUi.method || '',
            };
            continue;
          }
          const reason = (viaUi && viaUi.error) || 'custom_option_ui_failed';
          customOptionUiFailures.push({ field: labels[0], field_id: String(field.field_id), reason });
          attrPickDebug({ phase: 'custom_option_ui_failed', field: labels[0], reason });
          results[field.field_id] = {
            value: (applied.labels || []).join(', '),
            method: 'model_custom',
            note: 'custom_option_ui_failed',
          };
          continue;
        }
        if (applied.ok && confirmed.has(String(field.field_id))) {
          // 批量校验已经确认这一格按模型渲染出来了：无需再等、也不用点选。
          results[field.field_id] = {
            value: (applied.labels || []).join(', '),
            method: 'model',
          };
          continue;
        }
        if (applied.ok) {
          const expected = (applied.labels && applied.labels.length) ? applied.labels : candidateValues;
          const rendered = await attributeValueRendered(frame, labels, expected);
          attrPickDebug({
            phase: 'model_verify', field: labels[0], rendered,
            applied: applied.applied, custom: Boolean(applied.custom),
          });
          if (rendered) {
            results[field.field_id] = {
              value: (applied.labels || []).join(', '),
              method: applied.custom ? 'model_custom' : 'model',
            };
            continue;
          }
          // 没有枚举的属性（条件/型号）跟"条件"一个逻辑：模型写进去了就算填好，
          // 不再掉进 DOM 点选——型号的下拉里没有「添加自选」，那条路只会失败。
          if (modelEvidenceFields.has(String(field.field_id))) {
            results[field.field_id] = {
              value: (applied.labels || []).join(', '),
              method: applied.custom ? 'model_custom' : 'model',
              note: 'model_value_without_enum',
            };
            continue;
          }
        } else {
          attrPickDebug({ phase: 'model_write_failed', field: labels[0], reason: applied.reason, detail: applied.value || applied.error || '' });
          // 没有枚举、页面上又没有「添加自选」入口（型号）的属性：点选那条路必然失败，
          // 与其让它把整单打成 blocked/select_option_not_found，不如把原因如实报在结果里。
          if (modelEvidenceFields.has(String(field.field_id))) {
            results[field.field_id] = {
              value: '',
              method: 'model_failed',
              note: applied.reason || 'model_write_failed',
            };
            continue;
          }
        }
      }
      // 档案里存成 text 的属性（尺寸 100-150cm、条件、型号…）不代表页面上就是文本框：
      // 条件(100413, input_type=2)、型号(101639, input_type=5) 在页面上仍是下拉，
      // 触发器是 readonly 的 el-input__inner。对 readonly 控件 fill() 只会白等到超时
      // （现场：30s 后任务整单失败）。所以交给 selectElementOptionOrCustom 按控件
      // 本身决定——真文本框打字，下拉则选枚举、枚举里没有就走「添加自填选项」。
      const isTextInput = String(field.input_kind || field.type || '').toLowerCase() === 'text' || !optionLabels.length;
      if (isTextInput) {
        // 文本/自定义值通道要的是**人能读的文字**：模板存成选项 id 的（型号）已经在
        // resolveTemplateAttributeFields 里翻成标签，这里再退回 value_ids 就等于把 id 打上去。
        const value = optionLabels.join(', ');
        if (!value) continue;
        results[field.field_id] = await selectElementOptionOrCustom(
          frame,
          present,
          labels,
          [value],
          matchOptions,
        );
        continue;
      }
      // 枚举属性里"值不在选项中"的情况（原产地=Philippines 这类自选值）：
      // selectElementOption 会在找不到选项时改走「添加自填选项」。
      results[field.field_id] = await selectElementOptionOrCustom(
        frame,
        await waitForFormItemForLabels(frame, labels, 5000, matchOptions),
        labels,
        optionLabels,
        matchOptions,
      );
    }
    emit('stage', {
      stage: 'set_category_attributes',
      ok: true,
      count: Object.keys(results).length,
      skipped_optional: skipped,
      ...(unresolvedOptionIdFields.length ? { unresolved_option_id: unresolvedOptionIdFields } : {}),
      ...(customOptionUiFailures.length ? { custom_option_ui_failed: customOptionUiFailures } : {}),
      ...(customOptionUiApplied.length ? { custom_option_ui: customOptionUiApplied } : {}),
    });
    return results;
  }
  const attributes = task.category_attributes || {};
  for (const [key, value] of Object.entries(attributes)) {
    if (value === '' || value == null) continue;
    const labels = ATTRIBUTE_LABELS[key] || [key];
    results[key] = await selectElementOption(frame, labels, [String(value)]);
  }
  emit('stage', { stage: 'set_category_attributes', ok: true, count: Object.keys(results).length });
  return results;
}

async function fillBasicInfo(frame, task) {
  const basic = task.basic_info || {};
  await setField(frame, ['Parent SKU'], basic.parent_sku);
  await setField(frame, [TEXT.title], basic.title);
  await setField(frame, [TEXT.description], basic.description);
  emit('stage', { stage: 'fill_basic_info', ok: true });
  return { parent_sku: true, title: true, description: true };
}

async function uploadFiles(input, paths) {
  for (const path of paths) {
    if (!fs.existsSync(path)) throw new BlockedError('upload_file_missing', { file: require('path').basename(path) });
  }
  const multiple = await input.getAttribute('multiple');
  if (multiple !== null) {
    await input.setInputFiles(paths);
  } else {
    for (const path of paths) await input.setInputFiles(path);
  }
}

async function sectionForText(frame, label) {
  const headings = frame.getByText(label, { exact: true });
  if (!(await headings.count())) return null;
  const heading = headings.first();
  for (const selector of ['xpath=ancestor::section[1]', 'xpath=ancestor::*[contains(@class,"panel")][1]', 'xpath=ancestor::*[contains(@class,"content")][1]']) {
    const section = heading.locator(selector);
    if (await section.count()) return section.first();
  }
  return null;
}

async function selectSizeChartTemplate(frame, sizeChart, options = {}) {
  const templateId = cleanText(sizeChart && sizeChart.template_id);
  const templateName = cleanText(sizeChart && sizeChart.template_name);
  if (!templateId) throw new BlockedError('size_chart_template_unresolved', { template_id: templateId });
  const sizeItem = await waitForFormItemForLabels(frame, [TEXT.sizeChart]);
  const templateMode = sizeItem.getByText('选择模板', { exact: true }).first();
  if (!(await isVisible(templateMode))) throw new BlockedError('size_chart_template_mode_not_found');
  await templateMode.click();
  try {
    // 本土带上站点容错：点完直接读控件值（现场这一步同样吃过 60.7s 的诊断兜底）。
    return await selectElementOption(frame, [TEXT.sizeChart], [templateId, templateName].filter(Boolean), options);
  } catch (error) {
    if (error instanceof BlockedError) {
      throw new BlockedError('size_chart_template_unresolved', {
        template_id: templateId,
        template_name: templateName,
        cause: error.reason,
      });
    }
    throw error;
  }
}

// 图片满 9 张（Shopee 上限）后，马帮会把整个 <input type="file" multiple> 从 DOM 里
// 移除。计数不能以 input 为锚点：否则最后一张刚落地 input 就消失，计数返回 -1，
// 循环判定"没落地"干等满 90s —— 而且是否抢在移除前读到 9 全看时序，表现为时灵时不灵
// （2026-10-27 任务 9da6b384：第 1 次 attempt 在 index 8 / expected_after 9 卡死，
//  第 2 次 attempt 抢到 9 就过了；实页复现见 .scratch/probe_nine_clean.cjs）。
async function countProductImageCards(frame) {
  return frame.evaluate(() => {
    const input = document.querySelector('input[type="file"][multiple]');
    // 上传控件自己的分组：卡片和 input 同一个 .picture-group，范围最小、最稳。
    const group = input ? input.closest('.picture-group') : null;
    if (group) return group.querySelectorAll('.images-item').length;
    // input 已经不在了（图片满 9 张）或不在分组里：页面只有一个产品图分组时按它数。
    const groups = document.querySelectorAll('.picture-group');
    if (groups.length === 1) return groups[0].querySelectorAll('.images-item').length;
    // 结构微调时仍只数图片卡片类，绝不数 <li>（整页 li 曾产生 160 噪声）。
    if (input) {
      const holder = input.closest('.picture-box') || input.closest('.upload-img');
      if (holder) return holder.querySelectorAll('.images-item').length;
      return 0;
    }
    return -1;
  });
}

async function uploadProductImages(frame, task) {
  const images = task.images || {};
  // 「产品图最后一张放尺码表」（本土开关）：真正要上传的是 form 列表，末位可能是尺码表；
  // 没有 form 列表（开关关掉、跨境、旧任务）时按真实产品图上传，行为与改动前一致。
  const formPaths = Array.isArray(images.product_image_form_paths) && images.product_image_form_paths.length
    ? images.product_image_form_paths
    : (images.product_image_paths || []);
  const paths = formPaths
    .map((path) => cleanFilePath(path))
    .filter(Boolean);
  if (!paths.length) throw new BlockedError('product_images_missing');

  // 表单里已经带了图片（上一次运行卡住/被超时杀掉后留下的详情页被复用）时，
  // 继续按 0 张起算会把整套图再传一遍：页面上出现两份、顺序也乱。
  // 这里直接判成可重试错误，让 attempt 重开一张干净的「新增刊登」再传。
  // 必须排在 input 唯一性检查之前：图片满 9 张时马帮会把 input 整个移除，
  // 先查 input 会把这种残留页误报成不可重试的 product_image_input_not_unique。
  const existingCount = await countProductImageCards(frame).catch(() => 0);
  if (existingCount > 0) {
    throw new BlockedError('product_image_slots_already_filled', { count: existingCount });
  }

  const productInputs = frame.locator('input[type="file"][multiple]');
  if ((await productInputs.count()) !== 1) {
    throw new BlockedError('product_image_input_not_unique', { count: await productInputs.count() });
  }
  const productInput = productInputs.first();

  // 图片满 9 张后 input 会被马帮移除，循环里不能再靠 input 定位卡片：
  // 上传前先抓住 .picture-group 句柄，句柄失效时再退回按页面分组计数。
  let cardGroup = null;
  try {
    cardGroup = await productInput.evaluateHandle((el) => el.closest('.picture-group'));
  } catch {
    cardGroup = null;
  }
  const countCards = async () => {
    if (cardGroup) {
      const value = await cardGroup
        .evaluate((el) => (el && el.isConnected ? el.querySelectorAll('.images-item').length : -1))
        .catch(() => -1);
      if (value >= 0) return value;
    }
    return countProductImageCards(frame);
  };

  // Upload files ONE AT A TIME and wait for each to land in the picture list
  // before feeding the next. A single setInputFiles([...paths]) triggers the
  // Mabang uploader to fire N concurrent POST /upload requests; the responses
  // land out of order and the app pushes them into productPictureList in
  // arrival order — which is exactly why the operator sees the main image in
  // slot 5 instead of slot 1. Sequencing is the only way to lock the order.
  for (let index = 0; index < paths.length; index += 1) {
    const path = paths[index];
    if (!fs.existsSync(path)) {
      throw new BlockedError('upload_file_missing', { file: require('path').basename(path) });
    }
    const beforeCount = Math.max(0, await countCards().catch(() => 0));
    await productInput.setInputFiles(path);
    // Wait for this file's card to appear before starting the next upload. Give
    // each image up to 90s — large images (1.9MB PNG) on flaky NAS have been observed here.
    const deadline = Date.now() + 90000;
    let landed = false;
    while (Date.now() < deadline) {
      const now = await countCards().catch(() => beforeCount);
      if (now > beforeCount) { landed = true; break; }
      await sleep(150);
    }
    if (!landed) {
      throw new BlockedError('product_image_upload_did_not_land', {
        index, file: require('path').basename(path), expected_after: beforeCount + 1,
      });
    }
  }

  const sizeChart = (task.images || {}).size_chart || {};
  const sizePath = sizeChart.source === 'template' ? '' : (sizeChart.image_path || (task.images || {}).size_chart_image_path);
  const siteOptions = String(task.store?.listing_mode || '') === 'cnsc_global' ? {} : siteSelectOptions();
  let sizeChartMethod = 'not_requested';
  if (sizeChart.source === 'template') {
    await selectSizeChartTemplate(frame, sizeChart, siteOptions);
    sizeChartMethod = 'template_selected';
  } else if (sizePath) {
    const sizeItem = await waitForFormItemForLabels(frame, [TEXT.sizeChart]);
    const previewReady = await sizeItem.locator('img.img').evaluateAll(
      (images) => images.some((image) => image.naturalWidth > 0 && image.naturalHeight > 0),
    );
    if (previewReady) {
      sizeChartMethod = 'size_chart_already_uploaded';
    } else {
      const references = sizeItem.locator('.upload-icon.el-popover__reference[aria-describedby]');
      if ((await references.count()) !== 1) {
        throw new BlockedError('size_chart_upload_reference_not_unique', { count: await references.count() });
      }
      const popoverId = await references.first().getAttribute('aria-describedby');
      const sizeInputs = frame.locator(`[id="${popoverId}"] input[type="file"]`);
      if (!popoverId || (await sizeInputs.count()) !== 1) {
        throw new BlockedError('size_chart_input_not_found');
      }
      const sizeInput = sizeInputs.first();
      await uploadFiles(sizeInput, [sizePath]);
      sizeChartMethod = 'uploaded';
    }
  }
  emit('stage', { stage: 'upload_product_images', ok: true, count: paths.length });
  return {
    count: paths.length,
    size_chart: Boolean(sizePath),
    size_chart_method: sizeChartMethod,
    // 本土开关：末位那张就是尺码表（详情插图要跳过它，见 descriptionProductImageUrls）。
    size_chart_last_product_image: Boolean(images.size_chart_last_product_image),
    size_chart_slot_skipped: cleanText(images.size_chart_slot_skipped),
  };
}

// ---------------------------------------------------------------------------
// 本土商品描述末尾插图（尺码表 + 全部产品图）
//
// 本土 Shopee 的「商品描述」是 wangEditor：正文是 `.wangEditor-txt[contenteditable]`，
// 工具栏上挂了两套插图入口——
//   #<editorId>_product_img →「引用产品图」弹窗，列表就是本商品已上传的产品图，
//                             勾选后确定，图片按列表顺序追加到光标处；
//   「图片 → 网络图片」     → 按 URL 直接插入一张图。
// 两条路都由马帮前端自己写进描述正文（<p><img src="..." style="max-width:100%;"></p>），
// 描述模型与保存取值都不需要我们插手，所以这里全部走页面自己的交互。
//
// 这一步必须排在 upload_product_images 之后：引用产品图弹窗列的是已经上传到马帮的图，
// 产品图没传完时弹窗里没有可勾的图。
// ---------------------------------------------------------------------------
// 产品图有多少插多少（商品图上限 9 张，见 storage.PRODUCT_IMAGE_LIMIT）；
// 这个数只是防止页面结构异常时无限收集的护栏，不是业务上限。
const DESCRIPTION_MEDIA_MAX_IMAGES = 30;
const DESCRIPTION_MEDIA_TIMEOUT = 20000;
const DESCRIPTION_CONTENT_TIMEOUT = 10000;
const DESCRIPTION_MEDIA_ATTEMPTS = 3;
// 「上传图片」要把本地文件发给马帮、等它回地址再落到正文，比按地址引用慢一截。
const DESCRIPTION_SIZE_CHART_UPLOAD_TIMEOUT = 60000;

function sizeChartRequestedByTask(task) {
  const images = task.images || {};
  const sizeChart = images.size_chart || {};
  return Boolean(sizeChart.source || sizeChart.image_path || sizeChart.template_id || images.size_chart_image_path);
}

// 描述末尾要插的尺码表图从哪来：
//  - 尺码图表用「选择模板」时页面上没有预览图可引用（2026-09-23 现场：表单项里只有
//    「上传图片 / 选择模板 / 同步」三个控件，没有 img），所以用店铺模板里操作员一并
//    上传的那张本地尺码图，走描述编辑器的「上传图片」传到马帮再插进正文，插入后拿回
//    马帮给的地址，后面按地址走原有流程（幂等、顺序、位置校验都不变）；
//  - 其余情况沿用尺码图表预览图的地址（上传的马帮 COS 图与模板预览图都能直接引用）。
function descriptionSizeChartSource(task) {
  const images = task.images || {};
  const sizeChart = images.size_chart || {};
  const localPath = cleanFilePath(sizeChart.image_path || images.size_chart_image_path);
  if (cleanText(sizeChart.source) === 'template' && localPath && fs.existsSync(localPath)) {
    return { kind: 'file', path: localPath };
  }
  return { kind: 'url' };
}

// 图片身份 = 地址里最后一段文件名（丢掉主机名、查询串与哈希）。同一张 Shopee 图会在
// cf.shopee.*/file/<id>、down-*.img.susercontent.com/file/<id>、带 ?x-oss-process
// 参数等多个地址下出现，整串比较会把已经插进描述里的图判成「缺图」，于是这一批
// 被当成失败处理——插图与校验一律按这个键比。末尾没有文件名（页面地址这类）返回 ''。
function descriptionImageKey(url) {
  const text = String(url || '').trim();
  if (!text) return '';
  let pathname = text;
  try {
    pathname = new URL(text).pathname;
  } catch (_error) {
    pathname = text.split('#')[0].split('?')[0];
  }
  const segments = String(pathname).split('/').filter(Boolean);
  return segments.length ? segments[segments.length - 1].toLowerCase() : '';
}

// 产品图区里已上传图片的地址，按页面顺序取回。
//
// 定位产品图区有两条路，必须都留着：
//  - 不满 9 张时上传 input（<input type=file multiple>）还在，用它的表单项；
//  - **满 9 张后马帮会把 input 整个移除**（现场 2026-10-27，任务 6f4ff5af/78fce435），
//    这时只能按图片卡片找：有 .images-item 的 .picture-group 就是产品图区
//    （主图区那块 .picture-group 里没有卡片）。原先只认 input，满 9 张时恒返回 []，
//    插图计划里只剩尺码表，函数随即 already:true 提前返回 —— 产品图一张都不插，
//    尺码表也不再校验位置。
async function collectProductImageUrls(frame, limit = DESCRIPTION_MEDIA_MAX_IMAGES) {
  return frame.evaluate((cap) => {
    const input = document.querySelector('input[type="file"][multiple]');
    const section = input ? input.closest('.el-form-item, .form-item, .panel, section') : null;
    const cardGroups = typeof document.querySelectorAll === 'function'
      ? [...document.querySelectorAll('.picture-group')]
        .map((group) => ({ group, cards: group.querySelectorAll('.images-item').length }))
        .filter((entry) => entry.cards > 0)
        .sort((left, right) => right.cards - left.cards)
      : [];
    const roots = [];
    if (section) roots.push(section);
    if (cardGroups.length) roots.push(cardGroups[0].group);
    if (!roots.length) return [];
    const seen = new Set();
    const urls = [];
    for (const root of roots) {
      for (const image of root.querySelectorAll('img')) {
        // 读原始属性而不是 .src：src 缺失/为空时浏览器会把 .src 解析成当前页面地址。
        // 现场 2026-09-23：产品图区里 <div class="img-content"><img src=""></div> 这种占位图
        // 让 .src === https://publish.mabangerp.com/publish-ui/ 混进插图计划，它既插不进描述、
        // 也不在「引用产品图」弹窗里，校验时永远缺一张 → 整批插图被判「没插全」后回滚。
        const src = String(image.getAttribute('src') || '').trim();
        if (!/^https?:/i.test(src) || /\/static\//.test(src) || seen.has(src)) continue;
        // 末尾没有文件名（页面地址这类）的地址在「引用产品图」弹窗里根本列不出来，
        // 留着只会让整批插图被判成「有图没插上」。
        let pathname = src;
        try {
          pathname = new URL(src).pathname;
        } catch (_error) {
          pathname = String(src).split('#')[0].split('?')[0];
        }
        if (!String(pathname).split('/').filter(Boolean).length) continue;
        seen.add(src);
        urls.push(src);
        if (urls.length >= cap) return urls;
      }
    }
    return urls;
  }, limit);
}

// 「尺码图表」预览图的地址：上传的马帮 COS 图与模板预览图都能直接引用。
async function collectSizeChartPreviewUrl(frame) {
  return frame.evaluate((label) => {
    const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
    const item = [...document.querySelectorAll('.el-form-item')].find((candidate) => (
      clean(candidate.querySelector(':scope > .el-form-item__label')?.textContent).includes(label)
    ));
    const src = item?.querySelector('img.img')?.src || '';
    return /^https?:/i.test(src) ? src : '';
  }, TEXT.sizeChart);
}

// 详情页刚打开时尺码图表预览也是后渲染的：任务要求尺码图时等预览地址出现。
async function waitForSizeChartPreviewUrl(frame, timeoutMs = DESCRIPTION_MEDIA_TIMEOUT) {
  const deadline = Date.now() + timeoutMs;
  let url = await collectSizeChartPreviewUrl(frame).catch(() => '');
  while (!url && Date.now() < deadline) {
    await sleep(250);
    url = await collectSizeChartPreviewUrl(frame).catch(() => '');
  }
  return url;
}

// 描述末尾要插的图片顺序：尺码表图在前，产品图（全部）在后。
function buildDescriptionMediaPlan({ sizeChartUrl = '', productImageUrls = [], limit = DESCRIPTION_MEDIA_MAX_IMAGES } = {}) {
  const plan = [];
  if (sizeChartUrl) plan.push({ kind: 'size_chart', url: sizeChartUrl });
  for (const url of (productImageUrls || []).filter(Boolean).slice(0, limit)) {
    plan.push({ kind: 'product_image', url });
  }
  return plan;
}

// 描述里要用哪几张产品图：把「顶在产品图区末位的尺码表」去掉。
//
// 本土开关打开时，产品图区的最后一张就是尺码表；描述这一批图的第一张本来就已经是
// 尺码表（buildDescriptionMediaPlan 把 sizeChartUrl 排在最前），末尾再插一次就出现
// 两张尺码表（操作员 2026-10-01 要求：产品图加到详情时最后一张不要是尺码表）。
// 判定顺序：
//   1) 地址同名（同一张图换了 CDN 主机名也能认出来）——无论如何都去掉；
//   2) 任务里带了 size_chart_last_product_image 标记时，再去掉收集到的最后一张
//      （产品图区里的尺码表是另存的一份 COS 对象，地址与尺码图表预览图不同名）。
// 返回 { urls, dropped }：dropped 只用于把「去掉了第几张」报进步骤结果。
function descriptionProductImageUrls(productImageUrls, { sizeChartUrl = '', sizeChartLastProductImage = false } = {}) {
  const urls = (productImageUrls || []).map((url) => cleanText(url)).filter(Boolean);
  const sizeChartKey = descriptionImageKey(sizeChartUrl);
  let kept = sizeChartKey ? urls.filter((url) => descriptionImageKey(url) !== sizeChartKey) : [...urls];
  let dropped = urls.length - kept.length;
  if (!dropped && sizeChartLastProductImage && kept.length) {
    kept = kept.slice(0, -1);
    dropped = 1;
  }
  return { urls: kept, dropped };
}

// 已在描述里的图不再重复插：重试与 --resume-from-stage 都会重跑这一阶段。
// 按文件名（descriptionImageKey）比，CDN 换了主机名或加了参数也算同一张。
function missingDescriptionMedia(plan, existingUrls) {
  const exact = new Set();
  const keys = new Set();
  for (const url of existingUrls || []) {
    exact.add(String(url));
    const key = descriptionImageKey(url);
    if (key) keys.add(key);
  }
  return (plan || []).filter((entry) => {
    if (exact.has(String(entry.url))) return false;
    const key = descriptionImageKey(entry.url);
    return !key || !keys.has(key);
  });
}

// Element UI 的按钮文案是「确 定」（字间带空格），用 TEXT.confirm 拼出 \s* 版本。
function spacedTextPattern(text) {
  return new RegExp(String(text).split('').join('\\s*'));
}

// 描述图之间、以及最后一张图之后，只允许空段落/换行这类填充；出现正文就说明插到了中间。
function isDescriptionMediaFiller(segment) {
  const stripped = String(segment || '')
    .replace(/<\/?p[^>]*>/g, '')
    .replace(/<br[^>]*>/g, '')
    .replace(/&nbsp;/g, '')
    .replace(/<[^>]*>/g, '')
    .replace(/\s+/g, '');
  return stripped === '';
}

// 在描述 HTML 里找一张图：先整串比，找不到再按文件名比（CDN 换地址）。
// 返回 { index, length }，length 用来把游标推过这次命中——按文件名命中时它比整串短，
// 用整串长度推进会连带跳过紧跟其后的那张图。
function descriptionImageMatch(text, url, from = 0) {
  const haystack = String(text || '');
  const needle = String(url);
  const exact = haystack.indexOf(needle, from);
  if (exact >= 0) return { index: exact, length: needle.length };
  const key = descriptionImageKey(url);
  if (!key) return null;
  const found = haystack.toLowerCase().indexOf(key, from);
  return found < 0 ? null : { index: found, length: key.length };
}

// 在描述 HTML 里找一张图的下标（找不到 -1）。
function descriptionImageIndexOf(text, url, from = 0) {
  const match = descriptionImageMatch(text, url, from);
  return match ? match.index : -1;
}

// 新插的图必须压在描述正文最下面：本批地址按顺序出现，图与图之间及最后一张之后
// 不能再有正文，描述里原有的图也必须仍在本批之前。
function descriptionMediaAppendedAtTail(html, inserted, existing = []) {
  const text = String(html || '');
  const batch = (inserted || []).filter(Boolean);
  if (!batch.length) return true;
  let cursor = 0;
  let firstIndex = -1;
  let lastEnd = -1;
  for (const url of batch) {
    const match = descriptionImageMatch(text, url, cursor);
    if (!match) return false;
    const index = match.index;
    if (firstIndex < 0) firstIndex = index;
    const tagStart = text.lastIndexOf('<img', index);
    if (lastEnd >= 0 && !isDescriptionMediaFiller(text.slice(lastEnd, tagStart < 0 ? index : tagStart))) return false;
    const tagEnd = text.indexOf('>', index);
    lastEnd = tagEnd < 0 ? index + match.length : tagEnd + 1;
    cursor = index + match.length;
  }
  for (const url of existing || []) {
    const index = descriptionImageIndexOf(text, url);
    if (index >= 0 && index > firstIndex) return false;
  }
  return isDescriptionMediaFiller(text.slice(lastEnd));
}

// 位置校验失败时留一份可读证据（第一张新图的下标 + 尾部残留）。
function descriptionMediaPlacementEvidence(html, inserted, existing = []) {
  const text = String(html || '');
  const batch = (inserted || []).filter(Boolean);
  const firstIndex = batch.length ? descriptionImageIndexOf(text, batch[0]) : -1;
  let lastEnd = -1;
  if (batch.length) {
    const lastIndex = descriptionImageIndexOf(text, batch[batch.length - 1]);
    const tagEnd = lastIndex < 0 ? -1 : text.indexOf('>', lastIndex);
    lastEnd = tagEnd < 0 ? -1 : tagEnd + 1;
  }
  return {
    html_length: text.length,
    first_marker_index: firstIndex,
    tail_after_last: lastEnd < 0 ? text.slice(-120) : text.slice(lastEnd, lastEnd + 160),
    existing_after_first: (existing || []).some((url) => descriptionImageIndexOf(text, url) > firstIndex && firstIndex >= 0),
  };
}

async function descriptionEditorContext(frame) {
  const item = await waitForFormItemForLabels(frame, [TEXT.description]);
  // 详情页刚打开时 wangEditor 是后渲染的：表单项在、正文容器还没挂，得等。
  const deadline = Date.now() + DESCRIPTION_MEDIA_TIMEOUT;
  let editor = item.locator('.wangEditor-txt[contenteditable="true"]').first();
  while (Date.now() < deadline && !(await editor.count())) {
    await sleep(200);
    editor = item.locator('.wangEditor-txt[contenteditable="true"]').first();
  }
  if (!(await editor.count())) throw new BlockedError('description_editor_not_found');
  // id 是 Vue 绑定上去的：编辑器刚挂上时偶尔还读不到（现场 2026-10-27 19:57 有一次
  // description_editor_id_missing，同批任务重试就正常）。给它两秒把 id 写上来。
  let editorId = cleanText(await editor.getAttribute('id').catch(() => ''));
  const idDeadline = Date.now() + 2000;
  while (!editorId && Date.now() < idDeadline) {
    await sleep(200);
    editorId = cleanText(await editor.getAttribute('id').catch(() => ''));
  }
  if (!editorId) throw new BlockedError('description_editor_id_missing');
  return { item, editor, editorId };
}

// 详情页刚打开时描述正文也是后填的：内容为空时落光标等于落在位置 0，插完就跑到了
// 正文最前面。等正文出现并且连续两次读数一致（渲染稳定）再往下走。
async function waitForDescriptionContentReady(frame, editorId, timeoutMs = DESCRIPTION_CONTENT_TIMEOUT) {
  const deadline = Date.now() + timeoutMs;
  let previous = '';
  let stableReads = 0;
  while (Date.now() < deadline) {
    const text = await frame.evaluate((id) => {
      const editor = document.getElementById(id);
      return editor ? String(editor.innerText || '') : '';
    }, editorId).catch(() => '');
    if (text.trim() && text === previous) {
      stableReads += 1;
      if (stableReads >= 2) return true;
    } else {
      stableReads = 0;
    }
    previous = text;
    await sleep(300);
  }
  return false;
}

// 光标是不是已经贴在正文末尾（插图落在光标处，插错位置会把图塞进正文中间）。
async function descriptionCaretAtEnd(frame, editorId) {
  return frame.evaluate((id) => {
    const editor = document.getElementById(id);
    const selection = window.getSelection();
    if (!editor || !selection || !selection.anchorNode) return false;
    if (!editor.contains(selection.anchorNode)) return false;
    if (selection.anchorNode === editor) return selection.anchorOffset === editor.childNodes.length;
    const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
    let last = null;
    while (walker.nextNode()) {
      if (String(walker.currentNode.nodeValue || '').trim()) last = walker.currentNode;
    }
    if (!last || selection.anchorNode !== last) return false;
    return selection.anchorOffset >= String(last.nodeValue || '').length;
  }, editorId);
}

// 把描述正文滚到最底下，并把编辑器滚进视野：插图落在光标处，正文比可视区高时
// 尾巴要露出来（正文自己的滚动盒子就是 .wangEditor-txt，自带 overflow:auto）。
//
// 只滚编辑器自己的盒子 + 把它滚进视野，不再把外层容器一路滚到底：描述表单项下面还有
// 变体、SKU 网格、物流信息等一大堆表单项，页面主滚动容器（main.el-main）永远到不了底。
// 2026-09-23 现场埋点：main.el-main 内容 4051px、可视 785px，每一次尝试都被判「没滚到底」，
// 本土作业全部卡在 description_caret_not_at_end，描述里一张图都插不进去。
async function scrollDescriptionToBottom(frame, editorId) {
  return frame.evaluate((id) => {
    const editor = document.getElementById(id);
    if (!editor) return false;
    if (editor.scrollHeight - editor.clientHeight > 4) editor.scrollTop = editor.scrollHeight;
    if (typeof editor.scrollIntoView === 'function') editor.scrollIntoView({ block: 'nearest' });
    return true;
  }, editorId).catch(() => false);
}

// 落完光标后浏览器会把光标滚进视图，可能把编辑器自己的盒子滚回顶部（现场：第一次尝试
// scrollTop=0，后两次才是 277）。落光标之后再钉一次盒子贴底——滚动不会移动 DOM 光标。
async function scrollDescriptionEditorBoxToBottom(frame, editorId) {
  return frame.evaluate((id) => {
    const editor = document.getElementById(id);
    if (!editor) return false;
    if (editor.scrollHeight - editor.clientHeight > 4) editor.scrollTop = editor.scrollHeight;
    return true;
  }, editorId).catch(() => false);
}

// 编辑器自己的滚动盒子是不是贴在底下（留 4px 容差）。只看编辑器自己：祖先容器
// （main.el-main 这类页面滚动区）到不到底与描述无关，见上。
async function descriptionScrolledToBottom(frame, editorId) {
  return frame.evaluate((id) => {
    const editor = document.getElementById(id);
    if (!editor) return false;
    return !(editor.scrollHeight - editor.clientHeight > 4
      && editor.scrollTop + editor.clientHeight < editor.scrollHeight - 4);
  }, editorId).catch(() => false);
}

// 把光标挪到描述末尾：编辑器刚渲染完时 press() 只会 focus 到开头，所以每次都要验，
// 键盘不生效就退回 Range 直接落光标。落完光标还要确认编辑器自己的盒子贴到底——两样都
// 成立才认为「下一步插图会落在正文末尾」。
async function focusDescriptionEnd(frame, editor, editorId) {
  const caretReady = async () => (
    (await descriptionCaretAtEnd(frame, editorId)) && (await descriptionScrolledToBottom(frame, editorId))
  );
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await scrollDescriptionToBottom(frame, editorId);
    await frame.evaluate((id) => {
      const editor = document.getElementById(id);
      if (!editor) return;
      editor.focus();
      const range = document.createRange();
      range.selectNodeContents(editor);
      range.collapse(false);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }, editorId).catch(() => {});
    await scrollDescriptionEditorBoxToBottom(frame, editorId);
    if (await caretReady()) return 'range';
    await sleep(150);
  }
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await scrollDescriptionToBottom(frame, editorId);
    await editor.click({ timeout: 5000 }).catch(() => {});
    await editor.press('Control+End').catch(() => {});
    await scrollDescriptionEditorBoxToBottom(frame, editorId);
    if (await caretReady()) return 'keyboard';
    await sleep(150);
  }
  return '';
}

// 描述里那张尺码图后面还有正文 → 它不在末尾（历史坏数据：插到了正文最前面）。
function descriptionImageInsideBody(html, url) {
  const text = String(html || '');
  const index = descriptionImageIndexOf(text, url);
  if (index < 0) return false;
  const tagEnd = text.indexOf('>', index);
  return tagEnd >= 0 && !isDescriptionMediaFiller(text.slice(tagEnd + 1));
}

// 本地尺码图的文件名（去掉扩展名），用来认描述里那张图是不是同一张。
function descriptionFileStem(filePath) {
  const name = String(filePath || '').split(/[\\/]/).pop() || '';
  return name.replace(/\.[^.]+$/, '').trim().toLowerCase();
}

// 描述里是不是已经有这张尺码图。wangEditor 的「上传图片」会把本地文件名写进 img 的
// alt（现场 2026-10-27：alt="511692cc-70fd-4b85-9054-318c9168a7cf"），按它能认出来；
// 每次上传马帮都会给新地址，只按 src 认不出来。按地址插进来的那张由
// markDescriptionSizeChart 补上同样的标记，所以两条路都认得出。有了它，
// --resume-from-stage 复用页面时不会把同一张尺码表再传一遍（描述里出现两张尺码表）。
async function findInsertedDescriptionSizeChart(frame, editorId, filePath) {
  const stem = descriptionFileStem(filePath);
  if (!stem) return '';
  return frame.evaluate(({ id, needle }) => {
    const editor = document.getElementById(id);
    if (!editor) return '';
    for (const image of editor.querySelectorAll('img')) {
      const marker = String(image.getAttribute('data-mabang-size-chart') || '').toLowerCase();
      const alt = String(image.getAttribute('alt') || '').toLowerCase();
      const src = String(image.getAttribute('src') || '');
      if (marker === needle || alt.includes(needle) || src.toLowerCase().includes(needle)) return src;
    }
    return '';
  }, { id: editorId, needle: stem }).catch(() => '');
}

// 给描述里这张尺码图打标记：alt 用文件名（子上传时 wangEditor 自己写的就是它），
// 另加 data-mabang-size-chart。描述正文就是 wangEditor 的 DOM，改属性能跟着保存；
// 不这样做的话，按地址插入的图没有 alt，复用页面时会认不出来又传一张。
async function markDescriptionSizeChart(frame, editorId, url, stem) {
  if (!url) return false;
  return frame.evaluate(({ id, target, marker }) => {
    const editor = document.getElementById(id);
    if (!editor) return false;
    let marked = false;
    for (const image of editor.querySelectorAll('img')) {
      if (String(image.getAttribute('src') || '') !== target) continue;
      image.setAttribute('data-mabang-size-chart', marker || '1');
      if (!image.getAttribute('alt') && marker) image.setAttribute('alt', marker);
      marked = true;
    }
    return marked;
  }, { id: editorId, target: url, marker: stem }).catch(() => false);
}

async function readDescriptionImageUrls(frame, editorId) {
  return frame.evaluate((id) => {
    const editor = document.getElementById(id);
    if (!editor) return [];
    return [...editor.querySelectorAll('img')].map((image) => image.src).filter(Boolean);
  }, editorId);
}

async function readDescriptionHtml(frame, editorId) {
  return frame.evaluate((id) => {
    const editor = document.getElementById(id);
    return editor ? editor.innerHTML : '';
  }, editorId);
}

async function waitForDescriptionImages(frame, editorId, expectedCount, timeoutMs = DESCRIPTION_MEDIA_TIMEOUT) {
  const deadline = Date.now() + timeoutMs;
  let urls = await readDescriptionImageUrls(frame, editorId);
  while (Date.now() < deadline && urls.length < expectedCount) {
    await sleep(200);
    urls = await readDescriptionImageUrls(frame, editorId);
  }
  return urls;
}

async function openDescriptionImageMenu(item) {
  const container = item.locator('.wangEditor-container').first();
  const menu = container.locator('.menu-item a:has(i.wangeditor-menu-img-picture)').first();
  if (!(await menu.count())) throw new BlockedError('description_image_menu_not_found');
  await menu.click();
  const panel = container.locator('.wangEditor-drop-panel').first();
  const networkTab = panel.locator('.tab-container a').filter({ hasText: TEXT.networkImage }).first();
  if (!(await networkTab.count())) throw new BlockedError('description_network_image_tab_not_found');
  await networkTab.click();
  return panel;
}

// 「图片 → 网络图片」：按 URL 插一张图（尺码表图没有引用入口，只能走这条）。
// ensureCaret 在填完地址、点「确定」之前再钉一次光标：插图最终落在 wangEditor
// 内部保存的 range 上，填地址这一步就可能让它漂回正文中间。
async function insertDescriptionImageByUrl(item, url, ensureCaret) {
  const panel = await openDescriptionImageMenu(item);
  const input = panel.locator('.content-container input').first();
  await input.fill(url);
  if (typeof ensureCaret === 'function') await ensureCaret();
  const submit = panel.locator('.content-container button').filter({ hasText: TEXT.submitImage }).first();
  if (!(await submit.count())) throw new BlockedError('description_network_image_submit_not_found');
  await submit.click();
}

// 「图片 → 上传图片」：传本地文件给 wangEditor 自己的上传入口，由它 POST 到马帮再把
// 返回的 COS 地址插到光标处（本土描述要引用本地尺码图时走这条，因为页面上没有可引用的
// 尺码图地址）。这一页签点开的是系统文件对话框——面板里没有 <input type=file>，只有
// .upload-icon-container 图标，所以用 Playwright 的文件选择器拦截。
// ensureCaret 在选文件之前钉光标：wangEditor 按它内部保存的 range 插图。
async function insertDescriptionImageByFile(item, filePath, ensureCaret) {
  const page = item.page();
  const container = item.locator('.wangEditor-container').first();
  const menu = container.locator('.menu-item a:has(i.wangeditor-menu-img-picture)').first();
  if (!(await menu.count())) throw new BlockedError('description_image_menu_not_found');
  await menu.click();
  const panel = container.locator('.wangEditor-drop-panel').first();
  const uploadTab = panel.locator('.tab-container a').filter({ hasText: TEXT.uploadImage }).first();
  if (!(await uploadTab.count())) throw new BlockedError('description_upload_image_tab_not_found');
  await uploadTab.click();
  const chooserPromise = page.waitForEvent('filechooser', { timeout: DESCRIPTION_MEDIA_TIMEOUT });
  await panel.locator('.upload-icon-container').first().click();
  const chooser = await chooserPromise;
  if (typeof ensureCaret === 'function') await ensureCaret();
  await chooser.setFiles(filePath);
}

// 把店铺模板里操作员上传的本地尺码图传进描述，返回马帮给的地址（正文里最新那张图）。
// 上传+落图比引用地址慢，单独给更长的等待；没落到正文就当失败，调用方按警告处理。
async function uploadDescriptionSizeChartFile(frame, context, filePath, existingCount) {
  const { item, editor, editorId } = context;
  const ensureCaret = async () => {
    if (!(await focusDescriptionEnd(frame, editor, editorId))) {
      throw new BlockedError('description_caret_not_at_end');
    }
  };
  await ensureCaret();
  await insertDescriptionImageByFile(item, filePath, ensureCaret);
  const urls = await waitForDescriptionImages(
    frame, editorId, existingCount + 1, DESCRIPTION_SIZE_CHART_UPLOAD_TIMEOUT,
  );
  if (urls.length < existingCount + 1) throw new BlockedError('description_size_chart_not_inserted');
  return String(urls[urls.length - 1] || '');
}

// 一张都没勾上时先把弹窗关掉再抛错：调用方会退回「按 URL 逐张插入」，
// 留一层模态遮罩会把后面的工具栏点击挡掉。
async function closeReferenceProductImageDialog(dialog) {
  const cancel = dialog.locator('button').filter({ hasText: spacedTextPattern(TEXT.cancel) }).first();
  if (await cancel.count()) await cancel.click({ timeout: 5000 }).catch(() => {});
  if (await dialog.first().isVisible().catch(() => false)) {
    await dialog.locator('.el-dialog__headerbtn').first().click({ timeout: 5000 }).catch(() => {});
  }
}

// 「产品图片」→「引用产品图」弹窗：勾选待插的那些再确定。
// 按地址匹配而不是按前 N 项——重跑（重试、--resume-from-stage）时描述里已经有的图
// 不在待插集合里，按位置勾会把它们再插一遍并漏掉后面的图。
async function insertDescriptionProductImages(frame, editorId, wantedUrls) {
  const wanted = new Set((wantedUrls || []).filter(Boolean));
  if (!wanted.size) return 0;
  const entry = frame.locator(`#${editorId}_product_img`);
  if ((await entry.count()) !== 1) {
    throw new BlockedError('description_product_image_entry_not_found', { count: await entry.count() });
  }
  await entry.click();
  const dialog = frame.locator('.el-dialog__wrapper:visible').filter({ hasText: TEXT.referenceProductImage }).first();
  const items = dialog.locator('.images-item');
  await items.first().waitFor({ state: 'visible', timeout: DESCRIPTION_MEDIA_TIMEOUT });
  if (!(await items.count())) throw new BlockedError('description_product_images_empty');
  // 弹窗列表顺序 = 「产品图」区顺序，勾选后马帮按这个顺序追加到描述末尾。
  const listed = await items.evaluateAll((nodes) => nodes.map((node) => {
    const image = node.querySelector('img.img') || node.querySelector('img');
    return image ? String(image.src || '') : '';
  }));
  // 按地址勾选，整串对不上时再按文件名（CDN 主机名/参数不同也算同一张）。
  const wantedKeys = new Set([...wanted].map(descriptionImageKey).filter(Boolean));
  const indexes = listed.reduce((picked, url, index) => (
    wanted.has(url) || wantedKeys.has(descriptionImageKey(url)) ? [...picked, index] : picked
  ), []);
  if (!indexes.length) {
    await closeReferenceProductImageDialog(dialog);
    throw new BlockedError('description_product_images_not_listed', { wanted: wanted.size, listed: listed.length });
  }
  // 点 label 是切换语义，弹窗若带着上次的勾选状态重开，再点一次会把它取消掉。
  // 所以先读一遍各行的勾选状态，只点还没勾上的。
  const checked = await items.evaluateAll((nodes) => nodes.map((node) => {
    const box = node.querySelector('input[type="checkbox"]');
    return box ? Boolean(box.checked) : false;
  }));
  let picked = 0;
  for (const index of indexes) {
    if (!checked[index]) {
      // Element UI 的原生 checkbox 是 opacity:0，点不动，只能点它的 label。
      await items.nth(index).locator('label.el-checkbox').first().click();
    }
    picked += 1;
  }
  const confirm = dialog.locator('button').filter({ hasText: spacedTextPattern(TEXT.confirm) }).first();
  await confirm.click();
  return picked;
}

// 插图落到正文中间时撤掉这批（wangEditor 的撤销栈按每次插图一步）。
// 撤销次数按这批最多插进去的图数封顶：多按会把正文的文字一起撤掉。
async function undoDescriptionInserts(frame, editor, editorId, expectedCount, insertedCount = 0) {
  const attempts = Math.max(3, Math.min(20, Number(insertedCount) + 2));
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const urls = await readDescriptionImageUrls(frame, editorId);
    if (urls.length <= expectedCount) return true;
    await editor.press('Control+z').catch(() => {});
    await sleep(200);
  }
  return (await readDescriptionImageUrls(frame, editorId)).length <= expectedCount;
}

async function appendDescriptionMediaOnce(frame, task) {
  const { item, editor, editorId } = await descriptionEditorContext(frame);
  await waitForDescriptionContentReady(frame, editorId);
  const existing = await readDescriptionImageUrls(frame, editorId);
  const wantsSizeChart = sizeChartRequestedByTask(task);
  const sizeChartSource = descriptionSizeChartSource(task);
  const productImageUrlCandidates = await collectProductImageUrls(frame).catch(() => []);
  const warnings = [];
  // 尺码表图先落地：上传图片那条路要先把本地文件插进正文，才能拿到马帮给的地址，
  // 之后按地址走的计划/去重/顺序/位置校验与「网络图片」那条路完全一致。
  let sizeChartUrl = '';
  let sizeChartExisting = existing;
  let sizeChartMethod = '';
  if (wantsSizeChart && sizeChartSource.kind === 'file') {
    try {
      // 复用页面（--resume-from-stage）时描述里可能已经有这张尺码图了：再传一次就会
      // 出现两张。认出了就直接用它，不再上传。
      const alreadyInserted = await findInsertedDescriptionSizeChart(frame, editorId, sizeChartSource.path);
      if (alreadyInserted) {
        sizeChartUrl = alreadyInserted;
        sizeChartExisting = [...existing, alreadyInserted].filter(Boolean);
        sizeChartMethod = 'size_chart:already_in_description';
        const existingHtml = await readDescriptionHtml(frame, editorId);
        if (descriptionImageInsideBody(existingHtml, alreadyInserted)) {
          sizeChartMethod = 'size_chart:already_in_description-position-not-at-tail';
          warnings.push('description_size_chart_position_not_at_tail');
        }
      } else {
        sizeChartUrl = await uploadDescriptionSizeChartFile(frame, { item, editor, editorId }, sizeChartSource.path, existing.length);
        // 「上传图片」这条路由 wangEditor 自己按内部保存的 range 落图，落点不受我们的
        // Range 控制：现场 2026-10-27（任务 6f4ff5af / 78fce435）它把尺码表插到了正文
        // 最前面。这里必须验位置——不在末尾就撤掉这张，交给下面按地址的批量插图重插
        // （那条路有 ensureCaret 与末尾校验，能插回正文底部）。
        const uploadedHtml = await readDescriptionHtml(frame, editorId);
        if (descriptionMediaAppendedAtTail(uploadedHtml, [sizeChartUrl], existing)) {
          sizeChartExisting = [...existing, sizeChartUrl].filter(Boolean);
          sizeChartMethod = 'size_chart:upload';
        } else {
          const undone = await undoDescriptionInserts(frame, editor, editorId, existing.length, 1);
          if (undone) {
            sizeChartMethod = 'size_chart:upload-reinsert';
          } else {
            // 撤不回来就别再插第二张：留在原处，把位置问题作为警告交给运营。
            sizeChartExisting = [...existing, sizeChartUrl].filter(Boolean);
            sizeChartMethod = 'size_chart:upload-position-not-verified';
            warnings.push('description_size_chart_position_not_at_tail');
          }
        }
      }
    } catch (error) {
      // 本地尺码图没传上去不阻断：产品图照插，尺码图让运营人工补。
      warnings.push('description_size_chart_upload_failed');
    }
  } else if (wantsSizeChart) {
    sizeChartUrl = await waitForSizeChartPreviewUrl(frame);
  } else {
    sizeChartUrl = await collectSizeChartPreviewUrl(frame).catch(() => '');
  }
  // 产品图区末位那张可能是尺码表（本土开关）：这一批图的第一张已经是尺码表，
  // 描述里不重复插，去掉末位那张（操作员 2026-10-01 的要求）。
  const productImageSelection = descriptionProductImageUrls(productImageUrlCandidates, {
    sizeChartUrl,
    sizeChartLastProductImage: Boolean((task.images || {}).size_chart_last_product_image),
  });
  const productImageUrls = productImageSelection.urls;
  const sizeChartExcluded = productImageSelection.dropped;
  const plan = buildDescriptionMediaPlan({ sizeChartUrl, productImageUrls });
  if (!plan.length) throw new BlockedError('description_media_source_missing');
  const sizeCharts = plan.filter((entry) => entry.kind === 'size_chart').length;
  const productImages = plan.filter((entry) => entry.kind === 'product_image').length;
  const pending = missingDescriptionMedia(plan, sizeChartExisting);
  if (wantsSizeChart && !sizeChartUrl) {
    warnings.push('description_size_chart_source_missing');
  }
  // 任务里带着产品图，页面上却一张地址都没收集到：这是收集失败，不是「描述里已经有了」。
  // 不报出来的话计划里只剩尺码表，插完尺码表就 already:true 假装成功（2026-10-27 现场）。
  const declaredProductImages = [
    ...((task.images || {}).product_image_paths || []),
    ...((task.images || {}).product_image_form_paths || []),
  ].filter(Boolean).length;
  if (!productImageUrlCandidates.length && declaredProductImages) {
    warnings.push('description_product_images_not_collected');
  }
  // 描述里已经有这张尺码图（上一次运行插过的）时补个标记，下次复用页面才认得出。
  await markDescriptionSizeChart(frame, editorId, sizeChartUrl, descriptionFileStem(sizeChartSource.path));
  if (!pending.length && !warnings.length) {
    return {
      already: true,
      size_chart: Boolean(sizeCharts),
      product_images: productImages,
      // 末位尺码表被跳掉的张数（0 = 没跳过；>0 说明产品图区末位那只是尺码表）。
      size_chart_excluded: sizeChartExcluded,
      method: 'already_present',
    };
  }
  if (!pending.length) {
    // 尺码表已经插进去了、产品图也都在，只剩尺码表那类警告：按警告返回，别把警告吞掉。
    return {
      status: 'warning',
      code: warnings[0],
      size_chart: Boolean(sizeCharts),
      product_images: productImages,
      size_chart_excluded: sizeChartExcluded,
      inserted: 0,
      method: 'already_present',
    };
  }

  // 描述正文是富文本，插图落在光标处。页面刚打开时 wangEditor 内部光标可能还停在
  // 开头，所以插完必须验位置：不在最下面就把这批撤掉、重落光标再来一次。
  const expectedOrder = pending.map((entry) => entry.url);
  let attempt = null;
  let evidence = null;
  let failure = '';
  for (let round = 0; round < DESCRIPTION_MEDIA_ATTEMPTS; round += 1) {
    let outcome = null;
    try {
      outcome = await insertDescriptionMediaBatch(frame, { item, editor, editorId }, pending, sizeChartUrl, sizeChartExisting);
    } catch (error) {
      // 一批插到一半失败（尺码表插进去了、产品图没插全）时，正文里已经留下的图
      // 没人管会把描述切成两半，所以先撤干净再决定重试还是报错。
      failure = cleanText(error && error.message) || 'description_media_failed';
      evidence = { ...((error && error.details) || {}), failed_round: round + 1 };
      const rolled = await undoDescriptionInserts(frame, editor, editorId, sizeChartExisting.length, pending.length);
      evidence.rolled_back = rolled;
      if (!rolled) break;
      continue;
    }
    const html = await readDescriptionHtml(frame, editorId);
    if (descriptionMediaAppendedAtTail(html, expectedOrder, sizeChartExisting)) {
      attempt = { ...outcome, round: round + 1 };
      break;
    }
    evidence = descriptionMediaPlacementEvidence(html, expectedOrder, sizeChartExisting);
    const undone = await undoDescriptionInserts(frame, editor, editorId, sizeChartExisting.length, pending.length);
    evidence.rolled_back = undone;
    if (!undone) break;
  }
  if (!attempt) {
    throw new BlockedError(failure || 'description_media_position_not_at_tail', {
      expected: expectedOrder.length,
      evidence,
    });
  }
  const { caret, methods, round } = attempt;
  // 这批插完给尺码图打标记：下次复用同一个详情页时（--resume-from-stage）认得出来，
  // 不会再传一张。
  await markDescriptionSizeChart(frame, editorId, sizeChartUrl, descriptionFileStem(sizeChartSource.path));

  return {
    size_chart: Boolean(sizeCharts),
    product_images: productImages,
    size_chart_excluded: sizeChartExcluded,
    inserted: pending.length,
    caret,
    round,
    // 本地尺码图是批次之前单独传进去的，它的方式也要报出来（尺码表插哪儿了一条日志能看出来）。
    method: [sizeChartMethod, methods.join('+')].filter(Boolean).join('+'),
    ...(warnings.length ? { status: 'warning', code: warnings[0] } : {}),
  };
}

// 插一批图：尺码表先、产品图后，每步等图真的落到正文里。
async function insertDescriptionMediaBatch(frame, context, pending, sizeChartUrl, existing) {
  const { item, editor, editorId } = context;
  const caret = await focusDescriptionEnd(frame, editor, editorId);
  if (!caret) throw new BlockedError('description_caret_not_at_end');
  // 每次按 URL 插图前重新钉一次光标（正文滚到底 + 光标在末尾）。钉不住就整批不插：
  // 插到正文中间会把描述切成两半，宁可留警告让运营人工补图。
  const ensureCaret = async () => {
    if (!(await focusDescriptionEnd(frame, editor, editorId))) {
      throw new BlockedError('description_caret_not_at_end');
    }
  };
  const methods = [];
  let expectedCount = existing.length;

  if (pending.some((entry) => entry.kind === 'size_chart')) {
    await insertDescriptionImageByUrl(item, sizeChartUrl, ensureCaret);
    methods.push('size_chart:url');
    expectedCount += 1;
    const urls = await waitForDescriptionImages(frame, editorId, expectedCount);
    if (urls.length < expectedCount) throw new BlockedError('description_size_chart_not_inserted');
  }

  const pendingProducts = pending.filter((entry) => entry.kind === 'product_image');
  if (pendingProducts.length) {
    let added = 0;
    try {
      added = await insertDescriptionProductImages(frame, editorId, pendingProducts.map((entry) => entry.url));
      methods.push('product_images:reference');
    } catch (error) {
      // 引用弹窗不可用（页面结构变动）时退化为按已上传图片的 URL 逐张插入。
      for (const entry of pendingProducts) {
        await insertDescriptionImageByUrl(item, entry.url, ensureCaret);
        added += 1;
      }
      methods.push('product_images:url');
    }
    expectedCount += added;
    const urls = await waitForDescriptionImages(frame, editorId, expectedCount);
    if (urls.length < expectedCount) {
      throw new BlockedError('description_product_images_not_inserted', { expected: expectedCount, actual: urls.length });
    }
  }

  const after = await readDescriptionImageUrls(frame, editorId);
  const missing = missingDescriptionMedia(pending, after);
  if (missing.length) {
    // 带完整地址：只留文件名时看不出是页面收集到的地址不对，还是插入后地址变了。
    throw new BlockedError('description_media_not_verified', {
      missing: missing.map((entry) => `${entry.kind}:${descriptionImageKey(entry.url) || entry.url}`).slice(0, 8),
      missing_urls: missing.map((entry) => entry.url).slice(0, 8),
      present_urls: after.slice(-8),
    });
  }
  const appended = after.slice(existing.length);
  const expected = pending.map((entry) => entry.url);
  const ordered = appended.length === expected.length
    && appended.every((url, index) => url === expected[index] || descriptionImageKey(url) === descriptionImageKey(expected[index]));
  if (!ordered) {
    throw new BlockedError('description_media_order_unexpected', {
      appended: appended.map((url) => url.split('/').pop()).slice(0, 8),
      expected: expected.map((url) => url.split('/').pop()).slice(0, 8),
    });
  }
  return { caret, methods };
}

// 描述插图失败不阻断草稿（与媒体阶段一致）：降级成警告，人工补图。
async function appendDescriptionMedia(frame, task) {
  // 跨境 CNSC 的描述页没有这套插图入口，保持原样。
  if (String((task.store || {}).listing_mode || '') === 'cnsc_global') return { skipped: true };
  try {
    const result = await appendDescriptionMediaOnce(frame, task);
    // 步骤追踪只留 stage/ok，具体是哪条路（upload / upload-reinsert / already）与
    // 收集到几张图看不出来；写进调试日志，下次「描述里没图」能直接读原因。
    attrPickDebug({ phase: 'description_media', ...result });
    emit('stage', { stage: 'append_description_media', ok: true, ...result });
    return result;
  } catch (error) {
    const warning = {
      status: 'warning',
      code: cleanText(error && error.message) || 'description_media_failed',
      details: (error && error.details) || {},
    };
    attrPickDebug({ phase: 'description_media', ...warning });
    emit('stage', { stage: 'append_description_media', ...warning });
    return warning;
  }
}

// ---------------------------------------------------------------------------
// 描述图片宽度自动修复（马帮保存闸：商品描述里的图片宽度不能小于 700）
//
// 马帮保存时校验描述正文里每张图片的**实际像素宽度**（img.naturalWidth），
// 小于 700 就拒绝保存，只在页面顶部弹一句「商品描述中的图片宽度不能小于700，
// 请点击上方编辑图片按钮进行修改」，草稿不会落库。运营的标准处理是：
// 点描述上方的「编辑图片」→ 全选 → 修改尺寸「变化至」1000px → 更改。
//
// 2026-10-27 真机验证（HN0110 的详情页）：9 张 620–679px 的图点「更改」后约 2 秒
// 全部重编码成 1000px，描述正文的 img 地址原地换新（新的 COS 对象），弹窗保持打开，
// 关掉即可。所以这条路可以在保存前做完，不需要再点一次保存（保存只点一次是硬约束）。
// ---------------------------------------------------------------------------
const DESCRIPTION_IMAGE_MIN_WIDTH = 700;
const DESCRIPTION_IMAGE_TARGET_WIDTH = 1000;
const DESCRIPTION_IMAGE_LOAD_TIMEOUT = 10000;
const DESCRIPTION_IMAGE_FIX_TIMEOUT = 90000;

// 描述正文里每张图的地址与实际像素尺寸。没加载完的图 width=0（loaded=false）。
async function readDescriptionImageSizes(frame) {
  return frame.evaluate((label) => {
    const compact = (value) => String(value ?? '').replace(/\s+/g, '').trim();
    const item = [...document.querySelectorAll('.el-form-item')].find((candidate) => (
      compact(candidate.querySelector(':scope > .el-form-item__label')?.textContent).includes(label)
    ));
    const editor = item && item.querySelector('.wangEditor-txt[contenteditable="true"]');
    if (!editor) return [];
    return [...editor.querySelectorAll('img')].map((image) => {
      const width = Number(image.naturalWidth) || 0;
      return {
        src: String(image.getAttribute('src') || ''),
        width,
        height: Number(image.naturalHeight) || 0,
        loaded: Boolean(image.complete) && width > 0,
      };
    });
  }, TEXT.description).catch(() => []);
}

async function waitForDescriptionImageSizes(frame, timeoutMs = DESCRIPTION_IMAGE_LOAD_TIMEOUT) {
  const deadline = Date.now() + timeoutMs;
  let sizes = await readDescriptionImageSizes(frame);
  while (Date.now() < deadline && sizes.length && sizes.some((entry) => !entry.loaded)) {
    await sleep(200);
    sizes = await readDescriptionImageSizes(frame);
  }
  return sizes;
}

async function openDescriptionImageEditor(frame) {
  const item = await waitForFormItemForLabels(frame, [TEXT.description]);
  const trigger = item.locator('button.el-button').filter({ hasText: TEXT.editDescriptionImages }).first();
  if (!(await trigger.count())) {
    throw new BlockedError('description_image_editor_not_found', { reason: 'button_missing' });
  }
  await trigger.click();
  const dialog = frame.locator('.el-dialog__wrapper:visible').filter({ hasText: TEXT.resizeSection }).first();
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (await isVisible(dialog)) return dialog;
    await sleep(200);
  }
  throw new BlockedError('description_image_editor_not_found', { reason: 'dialog_not_visible' });
}

async function closeDescriptionImageEditor(frame) {
  const dialog = frame.locator('.el-dialog__wrapper:visible').filter({ hasText: TEXT.resizeSection }).first();
  if (!(await isVisible(dialog))) return true;
  await dialog.locator('.el-dialog__headerbtn').first().click({ timeout: 5000 }).catch(() => {});
  if (await isVisible(dialog)) {
    // Frame 上没有 keyboard（见水印收尾）：要发给 page。
    await frame.page?.()?.keyboard?.press('Escape').catch(() => {});
  }
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline && await isVisible(dialog)) await sleep(200);
  return !(await isVisible(dialog));
}

async function readSelectedDescriptionImageCount(dialog) {
  const text = await dialog.innerText().catch(() => '');
  const match = String(text || '').match(/\u5df2\u9009\s*(\d+)\s*\u5f20/);
  return match ? Number(match[1]) : -1;
}

// 只在下拉值漂了的时候才动：默认就是「等比例调整 / 图片小边」。
async function ensureDescriptionResizeSelect(frame, selectRoot, wanted) {
  const input = selectRoot.locator('input.el-input__inner').first();
  const current = cleanText(await input.inputValue().catch(() => ''));
  if (current === wanted) return current;
  await selectRoot.click().catch(() => {});
  await sleep(300);
  let option = selectRoot.locator('.el-select-dropdown__item').filter({ hasText: wanted }).first();
  if (!(await option.count())) {
    option = frame.locator('.el-select-dropdown:visible .el-select-dropdown__item').filter({ hasText: wanted }).first();
  }
  if (await option.count()) {
    await option.click().catch(() => {});
  } else {
    // 没找到目标选项时别把下拉留在打开状态挡住「更改」按钮。
    await frame.page?.()?.keyboard?.press('Escape').catch(() => {});
  }
  return cleanText(await input.inputValue().catch(() => ''));
}

// 保存前调用：描述里有宽 < 700 的图就走马帮自己的「编辑图片 → 全选 → 1000px → 更改」。
// 返回 {status:'skipped'|'fixed'}；修不好就抛 BlockedError（保存必被拒，别浪费一次 save）。
async function ensureDescriptionImageWidths(frame, options = {}) {
  const minWidth = Number(options.minWidth || DESCRIPTION_IMAGE_MIN_WIDTH);
  const targetWidth = Number(options.targetWidth || DESCRIPTION_IMAGE_TARGET_WIDTH);
  const before = await waitForDescriptionImageSizes(frame);
  const widths = before.map((entry) => entry.width);
  if (!before.length) {
    emit('stage', { stage: 'fix_description_image_widths', ok: true, skipped: 'no_description_images' });
    return { status: 'skipped', reason: 'no_description_images' };
  }
  const tooSmall = before.filter((entry) => entry.width > 0 && entry.width < minWidth);
  if (!tooSmall.length) {
    emit('stage', { stage: 'fix_description_image_widths', ok: true, skipped: 'widths_ok', widths });
    return { status: 'skipped', reason: 'widths_ok', widths };
  }

  const dialog = await openDescriptionImageEditor(frame);
  let after = [];
  try {
    const selectAllLabel = dialog.locator('.el-checkbox').filter({ hasText: TEXT.selectAllImages }).first();
    if (!(await selectAllLabel.count())) {
      throw new BlockedError('description_image_fix_failed', { step: 'select_all_missing' });
    }
    // 弹窗里是「全选 + 每张图一行」，图片数 = 复选框数 - 1。
    const checkboxes = dialog.locator('.el-checkbox');
    const total = Math.max(0, (await checkboxes.count()) - 1);
    if ((await readSelectedDescriptionImageCount(dialog)) !== total) {
      const checked = await selectAllLabel.locator('input.el-checkbox__original').first()
        .isChecked().catch(() => false);
      if (checked) await selectAllLabel.click();  // 重跑时可能已全选，再点一次会取消
      // label 的点击会被浏览器转发给内部 input（可能被组件按"切换"语义处理），
      // 所以点完必须回读「已选N张」，没到全选就逐张补勾，别信一次点击。
      await selectAllLabel.click();
      await sleep(300);
      if ((await readSelectedDescriptionImageCount(dialog)) !== total) {
        for (let index = 1; index <= total; index += 1) {
          const rowInput = checkboxes.nth(index).locator('input.el-checkbox__original').first();
          if (await rowInput.isChecked().catch(() => false)) continue;
          await checkboxes.nth(index).click().catch(() => {});
        }
        await sleep(300);
      }
    }
    const selected = await readSelectedDescriptionImageCount(dialog);
    if (selected <= 0 || (total > 0 && selected !== total)) {
      throw new BlockedError('description_image_fix_failed', { step: 'select_all_not_applied', selected, total });
    }

    const sizeBlock = dialog.locator('.background-block').filter({ hasText: TEXT.resizeSection }).first();
    const sizeInput = sizeBlock.locator('.el-autocomplete input.el-input__inner').first();
    if (!(await sizeInput.count())) {
      throw new BlockedError('description_image_fix_failed', { step: 'size_input_missing' });
    }
    const selects = sizeBlock.locator('.el-select');
    if ((await selects.count()) >= 2) {
      await ensureDescriptionResizeSelect(frame, selects.nth(0), TEXT.proportionalResize);
      await ensureDescriptionResizeSelect(frame, selects.nth(1), TEXT.smallEdgeResize);
    }
    await sizeInput.fill(String(targetWidth));
    if (cleanText(await sizeInput.inputValue().catch(() => '')) !== String(targetWidth)) {
      throw new BlockedError('description_image_fix_failed', { step: 'size_input_not_applied' });
    }

    const changeButton = dialog.locator('button').filter({ hasText: TEXT.changeImageSize }).first();
    if (!(await changeButton.count())) {
      throw new BlockedError('description_image_fix_failed', { step: 'change_button_missing' });
    }
    await changeButton.click();

    // 真机约 2 秒重编码完：正文 img 换新地址并重新加载，轮询到全部 >= 700 为止。
    const deadline = Date.now() + DESCRIPTION_IMAGE_FIX_TIMEOUT;
    while (Date.now() < deadline) {
      await sleep(500);
      after = await readDescriptionImageSizes(frame);
      if (after.length && after.every((entry) => entry.loaded && entry.width >= minWidth)) break;
    }
  } finally {
    await closeDescriptionImageEditor(frame);
  }

  after = after.length ? after : await readDescriptionImageSizes(frame);
  const stillSmall = after.filter((entry) => entry.width > 0 && entry.width < minWidth);
  const unloaded = after.filter((entry) => !entry.loaded);
  // 张数变了说明不是"改尺寸"而是被删/被替换了——绝不能当成功放过去。
  if (after.length !== before.length) {
    throw new BlockedError('description_image_width_fix_failed', {
      step: 'image_count_changed',
      before_count: before.length,
      after_count: after.length,
      before: widths,
      after: after.map((entry) => entry.width),
    });
  }
  if (stillSmall.length) {
    throw new BlockedError('description_image_width_fix_failed', {
      min_width: minWidth,
      before: widths,
      after: after.map((entry) => entry.width),
      still_small: stillSmall.map((entry) => entry.width).slice(0, 10),
      unloaded: unloaded.length,
    });
  }
  const result = {
    status: 'fixed',
    target_width: targetWidth,
    count: after.length,
    before: widths,
    after: after.map((entry) => entry.width),
    unloaded: unloaded.length,
  };
  emit('stage', { stage: 'fix_description_image_widths', ok: true, ...result });
  return result;
}

// 水印下拉里不是"模板"的那几行：管理入口和空态。匹配模板时必须排除。
// （真实 markup：末尾一行是「水印模板管理」，模板列表为空时是一行「暂无水印模板」。）
const WATERMARK_NON_TEMPLATE_ROWS = [
  /水印模板管理/,
  /暂无水印模板/,
  /Watermark template/i,
  /No watermark/i,
];

function resolveWatermarkTemplate(catalog, reference) {
  const templateId = cleanText(reference && reference.template_id);
  const templateName = cleanText(reference && reference.name);
  if (!templateId || !templateName) return null;
  const matches = (catalog || []).filter((item) => (
    cleanText(item && (item.template_id || item.templateId || item.watermark_id || item.watermarkId || item.id)) === templateId &&
    cleanText(item && (item.name || item.template_name || item.templateName || item.watermark_name)) === templateName
  ));
  return matches.length === 1 ? matches[0] : null;
}

// A visible menu container that belongs to the 「添加水印」 trigger. Element-UI
// renders the dropdown in a popper anywhere in the frame, so we accept the first
// visible menu whose rows actually look like watermark options (an id attribute or
// a thumbnail), which keeps other open pickers (类目 cascader, 尺码图 select) out.
async function watermarkMenuOpen(frame, triggerLabel = TEXT.addWatermark) {
  const candidates = [];
  for (const selector of ['.el-dropdown-menu', '[role="menu"]', '.el-select-dropdown__menu', '.el-select-dropdown']) {
    const menus = frame.locator(selector);
    for (let index = 0, count = await menus.count().catch(() => 0); index < count; index += 1) {
      const menu = menus.nth(index);
      if (!(await isVisible(menu))) continue;
      candidates.push(menu);
    }
  }
  for (const menu of candidates) {
    // 2026-10-01：真实页面的水印下拉行是 Element UI 的 .el-dropdown-menu__item
    // （publish-ui 的 main.js 里就是 el-dropdown-item），探针里必须带上它，
    // 否则「菜单明明开了」也会被判成 watermark_menu_not_visible。
    const rows = menu.locator('[data-watermark-id],[data-watermark-name],[data-template-id],[data-watermarkid],.watermark-item,.watermark-list-item,.el-dropdown-menu__item');
    if (await rows.count().catch(() => 0)) return menu;
  }
  return null;
}

// Locate the 「添加水印」 trigger. The watermark control is a single toggle inside
// the 商品素材 / 产品图 media area; roles share the trigger but are applied with
// their own image_positions (main=[1], product=[2..N]) — the page decides which
// images the chosen template covers, never a menu position.
async function findWatermarkTrigger(frame) {
  const buttons = frame.getByText(TEXT.addWatermark, { exact: true });
  const matches = [];
  for (let index = 0, count = await buttons.count().catch(() => 0); index < count; index += 1) {
    const candidate = buttons.nth(index);
    if (!(await isVisible(candidate))) continue;
    const buttonCount = await candidate.locator('xpath=ancestor::button[1]').count().catch(() => 0);
    matches.push(buttonCount ? candidate.locator('xpath=ancestor::button[1]').first() : candidate);
  }
  // 真实页面是 <button class="el-button …el-dropdown-selfdefine"><span> 添加水印 …</span></button>：
  // button 本身和它里面的 span 文案都等于「添加水印」，直接数 matches 会得到 2 →
  // 明明只有一颗按钮却报 watermark_trigger_not_unique。给元素打标记去重后再判定。
  const unique = [];
  for (const match of matches) {
    const token = await match
      .evaluate((node) => {
        if (!node.dataset.dshWatermarkTrigger) {
          node.dataset.dshWatermarkTrigger = `wm-trigger-${Math.random().toString(36).slice(2)}`;
        }
        return node.dataset.dshWatermarkTrigger;
      })
      .catch(() => '');
    if (!token || unique.includes(token)) continue;
    unique.push(token);
  }
  if (unique.length !== 1) {
    throw new BlockedError('watermark_trigger_not_unique', { count: unique.length, raw_matches: matches.length });
  }
  return matches[0];
}

// 打开「添加水印」下拉。真实页面上的 trigger 是 **click**（`el-dropdown trigger:"click"`），
// hover 只是兼容兜底（有些皮肤的 dropdown 会挂 mouseenter）。
async function openWatermarkMenu(frame, trigger, options = {}) {
  if (await watermarkMenuOpen(frame)) return { method: 'already_open' };
  const deadline = Date.now() + (options.timeoutMs ?? 6000);
  let method = 'click';
  await trigger.click({ timeout: 2000 }).catch(() => { method = ''; });
  if (!(await watermarkMenuOpen(frame))) {
    try {
      await trigger.hover({ timeout: 1500 });
      method = 'hover';
    } catch (_error) {
      method = method || '';
    }
  }
  while (Date.now() < deadline) {
    const menu = await watermarkMenuOpen(frame);
    if (menu) return { method, menu };
    await sleep(100);
  }
  throw new BlockedError('watermark_menu_not_visible', { method });
}

// Read the open watermark menu's rows as catalog entries. Each row maps to a
// watermark template: id from data-watermark-id / data-template-id / data-id /
// data-value, name from data-watermark-name / title / row text, and a thumbnail
// from the first <img>. Never uses the row's visual position as an identity.
async function readWatermarkMenuItems(menu) {
  const rowSelector = '[data-watermark-id],[data-template-id],[data-templateid],[data-watermarkid],.watermark-item,.watermark-list-item,.el-dropdown-menu__item';
  const rows = menu.locator(rowSelector);
  const items = [];
  const seen = new Set();
  const rowId = async (row) => cleanText(
    (await row.getAttribute('data-watermark-id').catch(() => null)) ||
    (await row.getAttribute('data-template-id').catch(() => null)) ||
    (await row.getAttribute('data-templateid').catch(() => null)) ||
    (await row.getAttribute('data-watermarkid').catch(() => null)) ||
    (await row.getAttribute('data-id').catch(() => null)) ||
    (await row.getAttribute('data-value').catch(() => null)),
  );
  for (let index = 0, count = await rows.count().catch(() => 0); index < count; index += 1) {
    const row = rows.nth(index);
    const id = await rowId(row);
    const name = cleanText(
      (await row.getAttribute('data-watermark-name').catch(() => null)) ||
      (await row.getAttribute('data-template-name').catch(() => null)) ||
      (await row.getAttribute('title').catch(() => null)) ||
      cleanText(await row.innerText().catch(() => '')),
    );
    const thumb = await row.locator('img').first().getAttribute('src').catch(() => null);
    if (id && name && !seen.has(id)) {
      seen.add(id);
      items.push({ template_id: id, name, preview_url: thumb || '' });
    }
  }
  return items;
}

// Click the exact menu row whose template matches the configured watermark.
//
// 真实页面（publish-ui main.js）的行 markup 是：
//   el-dropdown-item({key: t.id}, [ t.templateName ])
// —— **DOM 里没有 id**（Vue 的 key 不落 DOM），行文本就是模板名。所以匹配规则是：
//   1) 有 data 属性且 id+name 都对上的行优先（兼容夹具 / 其它皮肤）；
//   2) 否则按**模板名精确相等**匹配，且必须唯一；
//   3) 「水印模板管理」「暂无水印模板」这两行永远不算候选。
// 名字对不上/有多行同名 → 直接抛错，绝不按位置点（点错模板=给操作员的草稿加了错水印）。
async function clickWatermarkMenuOption(menu, role, templateId, templateName) {
  const rowSelector = '[data-watermark-id],[data-template-id],[data-templateid],[data-watermarkid],.watermark-item,.watermark-list-item,.el-dropdown-menu__item';
  const rows = menu.locator(rowSelector);
  const rowName = async (row) => cleanText(
    (await row.getAttribute('data-watermark-name').catch(() => null)) ||
    (await row.getAttribute('data-template-name').catch(() => null)) ||
    (await row.getAttribute('title').catch(() => null)) ||
    cleanText(await row.innerText().catch(() => '')),
  );
  const rowId = async (row) => cleanText(
    (await row.getAttribute('data-watermark-id').catch(() => null)) ||
    (await row.getAttribute('data-template-id').catch(() => null)) ||
    (await row.getAttribute('data-templateid').catch(() => null)) ||
    (await row.getAttribute('data-watermarkid').catch(() => null)) ||
    (await row.getAttribute('data-id').catch(() => null)) ||
    (await row.getAttribute('data-value').catch(() => null)),
  );
  const wantedId = cleanText(templateId);
  const wantedName = cleanText(templateName);
  const count = await rows.count().catch(() => 0);
  const byIdAndName = [];
  const byName = [];
  const availableNames = [];
  for (let index = 0; index < count; index += 1) {
    const row = rows.nth(index);
    const id = await rowId(row);
    const name = await rowName(row);
    if (!name || WATERMARK_NON_TEMPLATE_ROWS.some((pattern) => pattern.test(name))) continue;
    availableNames.push(name);
    if (id && wantedId && id === wantedId && name === wantedName) byIdAndName.push({ row, name });
    if (name === wantedName) byName.push({ row, name });
  }
  const candidates = byIdAndName.length ? byIdAndName : byName;
  if (candidates.length === 1) {
    await candidates[0].row.scrollIntoViewIfNeeded().catch(() => {});
    await candidates[0].row.click({ timeout: 3000 });
    return {
      method: 'menu_option_clicked',
      matched_by: byIdAndName.length ? 'template_id+name' : 'name',
      template_id: wantedId,
      name: wantedName,
    };
  }
  if (candidates.length > 1) {
    throw new BlockedError('watermark_template_ambiguous', {
      role, template_id: wantedId, template_name: wantedName, matches: candidates.length,
    });
  }
  throw new BlockedError('watermark_template_not_found', {
    role,
    template_id: wantedId,
    template_name: wantedName,
    available_names: [...new Set(availableNames)].slice(0, 20),
  });
}

// （旧的 verifyWatermarkSelection 只验"菜单关掉了"，2026-10-01 换成
//   waitForWatermarkApplied：等马帮处理完并回读图片地址，见下面「图片范围」那一段。）

// ---------------------------------------------------------------------------
// 水印的「图片范围」= 产品图区勾选了哪几张
//
// 2026-10-01 从 publish-ui 的 main.js 读出来的真实机制（不是猜的）：
//   1) 「添加水印」是 el-dropdown，trigger=click；菜单行是 .el-dropdown-menu__item，
//      行文本就是模板名，**DOM 里没有 id**（Vue key 不落 DOM）；
//   2) 点模板会调 applyWatermarkTemplate(t.id)，而它第一件事是
//      `if (0 !== this.checkPhoto.checkedPhotos.length)` —— **只处理产品图区勾选的那些图**，
//      一张都没勾就只弹「请先勾选您要操作的图片」，什么都不做；
//   3) 处理是异步的：先弹「正在添加水印，请等待完成」+ 进度条，完成后把
//      imageListData[n].url 换成新地址（页面上 <img src> 会变），并提示
//      「添加图片水印处理完成」/「添加水印成功」。
//
// 所以「主图水印 / 产品图水印」= 先按角色勾选第 1 张 / 第 2 张起，再选模板。
// 旧实现只点模板、从不勾图 → 页面上什么都不会发生（这功能因此从来没真正生效过）。
// ---------------------------------------------------------------------------
const WATERMARK_PROGRESS_TEXT = '\u6b63\u5728\u6dfb\u52a0\u6c34\u5370'; // 正在添加水印
const WATERMARK_DONE_TEXTS = ['\u6dfb\u52a0\u56fe\u7247\u6c34\u5370\u5904\u7406\u5b8c\u6210', '\u6dfb\u52a0\u6c34\u5370\u6210\u529f']; // 添加图片水印处理完成 / 添加水印成功
const WATERMARK_NEED_SELECTION_TEXT = '\u8bf7\u5148\u52fe\u9009\u60a8\u8981\u64cd\u4f5c\u7684\u56fe\u7247'; // 请先勾选您要操作的图片

// 产品图卡片与勾选框：2026-10-01 在真实编辑页上读到的结构是
//   .picture-box > .images-item > (.photo > img, .check > input[type="checkbox"])
// （每张图一个 .images-item，勾选状态就是里面那个原生 checkbox）。
// 旧的 .has-mainphoto / .el-checkbox 是采集夹具里的写法，真实页面上是 0 个，所以两个都试。
const WATERMARK_IMAGE_SCOPE_SELECTORS = ['.picture-box', '.has-mainphoto'];
const WATERMARK_IMAGE_ITEM_SELECTORS = ['.images-item', '.el-checkbox'];

async function watermarkImageItems(frame) {
  for (const scopeSelector of WATERMARK_IMAGE_SCOPE_SELECTORS) {
    const scope = frame.locator(scopeSelector).first();
    if (!(await scope.count().catch(() => 0))) continue;
    for (const itemSelector of WATERMARK_IMAGE_ITEM_SELECTORS) {
      const items = scope.locator(itemSelector);
      const count = await items.count().catch(() => 0);
      if (count) return { items, count, scope_selector: scopeSelector, item_selector: itemSelector };
    }
  }
  const items = frame.locator(WATERMARK_IMAGE_ITEM_SELECTORS[0]);
  return { items, count: await items.count().catch(() => 0), scope_selector: '', item_selector: WATERMARK_IMAGE_ITEM_SELECTORS[0] };
}

async function watermarkItemChecked(item) {
  const input = item.locator('input[type="checkbox"]').first();
  const checked = await input.isChecked?.().catch(() => null);
  if (typeof checked === 'boolean') return checked;
  const className = (await item.getAttribute('class').catch(() => '')) || '';
  return /\bis-checked\b|\bchecked\b/.test(className);
}

// 把一张图切到 want 状态；每一步都回读，达到目标就停（不能只看"是不是勾上了"——
// 取消勾选那一步同样算成功，旧写法会把取消勾选又点回来）。
async function toggleWatermarkItem(item, want) {
  if ((await watermarkItemChecked(item)) === want) return true;
  const input = item.locator('input[type="checkbox"]').first();
  if (await input.count().catch(() => 0)) {
    // 真实页面（2026-10-01 实测）：勾选状态就是这个原生 checkbox，而且它是**视觉隐藏**的
    // （点 .check / .photo / .images-item 都没反应，只有点 input 才切换，并给卡片加上
    // active-color）。隐藏元素过不了 Playwright 的可操作性检查，所以先用 DOM click，
    // 再退回 force click。
    await input.evaluate((node) => node.click()).catch(() => {});
    if ((await watermarkItemChecked(item)) === want) return true;
    await input.click({ force: true, timeout: 3000 }).catch(() => {});
    if ((await watermarkItemChecked(item)) === want) return true;
  }
  await item.locator('.check').first().click({ timeout: 2000 }).catch(() => {});
  if ((await watermarkItemChecked(item)) === want) return true;
  await item.click({ timeout: 2000 }).catch(() => {});
  return (await watermarkItemChecked(item)) === want;
}

async function watermarkImageSources(frame) {
  return frame
    .evaluate(({ scopes, items }) => {
      for (const scopeSelector of scopes) {
        const scope = document.querySelector(scopeSelector);
        if (!scope) continue;
        for (const itemSelector of items) {
          const cards = [...scope.querySelectorAll(itemSelector)];
          if (!cards.length) continue;
          return cards
            .map((card) => {
              const image = card.querySelector('img');
              return image ? String(image.getAttribute('src') || '').trim() : '';
            })
            .filter((src) => src && !src.startsWith('data:'));
        }
      }
      return [];
    }, { scopes: WATERMARK_IMAGE_SCOPE_SELECTORS, items: WATERMARK_IMAGE_ITEM_SELECTORS })
    .catch(() => []);
}

// 把产品图区的勾选状态调成 positions（1 基）。返回初始状态供收尾复原。
async function selectWatermarkImages(frame, positions) {
  const { items, count, scope_selector: scopeSelector, item_selector: itemSelector } = await watermarkImageItems(frame);
  if (!count) throw new BlockedError('watermark_images_not_found', { positions });
  const wanted = new Set(positions.filter((value) => value >= 1 && value <= count));
  const initial = [];
  let changed = 0;
  for (let index = 0; index < count; index += 1) {
    const item = items.nth(index);
    const was = await watermarkItemChecked(item);
    initial.push({ position: index + 1, checked: was });
    if (was !== wanted.has(index + 1)) {
      await toggleWatermarkItem(item, wanted.has(index + 1));
      changed += 1;
    }
  }
  const selected = [];
  const mismatch = [];
  for (let index = 0; index < count; index += 1) {
    const checked = await watermarkItemChecked(items.nth(index));
    if (checked) selected.push(index + 1);
    if (checked !== wanted.has(index + 1)) mismatch.push({ position: index + 1, checked });
  }
  if (mismatch.length) {
    throw new BlockedError('watermark_image_selection_not_verified', {
      requested: positions, mismatch, total_images: count, scope_selector: scopeSelector, item_selector: itemSelector,
    });
  }
  return { total: count, selected, changed, initial, scope_selector: scopeSelector, item_selector: itemSelector };
}

async function restoreWatermarkImages(frame, initial) {
  if (!Array.isArray(initial) || !initial.length) return false;
  const { items, count } = await watermarkImageItems(frame);
  if (!count) return false;
  for (const entry of initial) {
    if (entry.position > count) continue;
    const item = items.nth(entry.position - 1);
    const checked = await watermarkItemChecked(item);
    if (checked !== entry.checked) await toggleWatermarkItem(item, entry.checked);
  }
  return true;
}

// 等马帮把水印处理完：进度通知出现 → 消失/出现完成文案；再回读目标图的地址有没有变。
async function waitForWatermarkApplied(frame, { before = [], positions = [], timeoutMs = 90000 } = {}) {
  const beforeByPosition = new Map((before || []).map((src, index) => [index + 1, src]));
  const targetPositions = positions.filter((value) => beforeByPosition.has(value));
  let sawProgress = false;
  let sawDone = false;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const notices = await frame
      .evaluate(({ progressText, doneTexts }) => {
        const texts = [...document.querySelectorAll('.el-notification,.el-message')]
          .map((node) => String(node.textContent || ''))
          .join(' | ');
        return {
          progress: texts.includes(progressText),
          done: doneTexts.some((text) => texts.includes(text)),
          selectionWarning: texts.includes('\u8bf7\u5148\u52fe\u9009\u60a8\u8981\u64cd\u4f5c\u7684\u56fe\u7247'),
        };
      }, { progressText: WATERMARK_PROGRESS_TEXT, doneTexts: WATERMARK_DONE_TEXTS })
      .catch(() => ({ progress: false, done: false, selectionWarning: false }));
    if (notices.selectionWarning) {
      throw new BlockedError('watermark_images_not_selected', { positions });
    }
    if (notices.progress) sawProgress = true;
    if (notices.done) sawDone = true;
    if (sawProgress && !notices.progress) break;
    if (sawDone) break;
    const after = await watermarkImageSources(frame);
    const changedNow = targetPositions.some((position) => after[position - 1] && after[position - 1] !== beforeByPosition.get(position));
    if (changedNow && !notices.progress) break;
    await sleep(500);
  }
  const after = await watermarkImageSources(frame);
  const changedPositions = targetPositions.filter(
    (position) => after[position - 1] && after[position - 1] !== beforeByPosition.get(position),
  );
  return {
    changed: changedPositions.length > 0,
    changedPositions,
    checked_positions: targetPositions,
    saw_progress: sawProgress,
    saw_done: sawDone,
  };
}

async function applyWatermark(frame, role, reference) {
  const templateId = cleanText(reference && reference.template_id);
  const templateName = cleanText(reference && reference.name);
  const positions = [...new Set((reference && reference.image_positions || []).map(Number).filter((value) => value > 0))];
  // 计划里给的「图片范围」意图（main=first / product=from_second|explicit）。
  // 现在只是照原样回报到阶段结果里：马帮选完水印模板之后还要选图片范围这一步，
  // 具体控件结构要对着真实商品编辑页确认后再接（见 progress 里的待办）。
  const imageRange = (reference && typeof reference.image_range === 'object' && reference.image_range) || {};
  if (!templateId || !templateName || !positions.length) {
    return { status: 'skipped', code: 'watermark_unconfigured', role, image_positions: positions, image_range: imageRange };
  }

  // Main-image watermark may only touch image #1; product-image watermark only
  // #2..N. Both come pre-baked into image_positions by the plan builder — refuse
  // any position that would cross roles instead of silently applying elsewhere.
  if (role === 'main_image' && (positions.length !== 1 || positions[0] !== 1)) {
    throw new BlockedError('watermark_position_out_of_role', { role, image_positions: positions });
  }
  if (role === 'product_images' && positions.some((value) => value < 2)) {
    throw new BlockedError('watermark_position_out_of_role', { role, image_positions: positions });
  }

  // 1) 先把「图片范围」落到页面上：马帮只给**勾选**的图加水印。
  const selection = await selectWatermarkImages(frame, positions);
  const before = await watermarkImageSources(frame);

  let clicked = null;
  let applied = null;
  let trigger = null;
  try {
    // 2) 打开「添加水印」下拉（真实页面 trigger=click），点名称唯一匹配的模板行。
    trigger = await findWatermarkTrigger(frame);
    const opened = await openWatermarkMenu(frame, trigger);
    const menu = opened.menu || await watermarkMenuOpen(frame);
    if (!menu) throw new BlockedError('watermark_menu_not_visible', { role });
    clicked = await clickWatermarkMenuOption(menu, role, templateId, templateName);

    // 3) 等马帮处理完，并回读目标图片地址是否真的换了。
    applied = await waitForWatermarkApplied(frame, { before, positions });
  } finally {
    // 4) 收尾（失败也要做）：菜单收起；勾选状态复原成操作员原来的样子，
    //    不把我们为选范围临时勾的选择留在页面上。
    //    注意：Escape 要发给 page，Frame 上**没有 keyboard**（2026-10-01 真机踩到：
    //    frame.keyboard.press 直接 TypeError，把 finally 里后面的复原也一起带崩了）。
    if (await watermarkMenuOpen(frame).catch(() => null)) {
      await frame.page?.()?.keyboard?.press('Escape').catch(() => {});
      await sleep(150);
      // Element UI 的 click 型下拉对 Escape 不一定买账：还在就把触发器再点一下收起来。
      if (await watermarkMenuOpen(frame).catch(() => null)) {
        await trigger.click({ timeout: 2000 }).catch(() => {});
        await sleep(150);
      }
    }
    await restoreWatermarkImages(frame, selection.initial).catch(() => {});
  }

  return {
    status: applied.changed ? 'applied' : 'warning',
    ...(applied.changed ? {} : { code: 'watermark_applied_unverified' }),
    role,
    template_id: templateId,
    template_name: templateName,
    image_positions: positions,
    image_range: imageRange,
    selected_positions: selection.selected,
    total_images: selection.total,
    images_changed: applied.changed,
    changed_positions: applied.changedPositions,
    method: clicked.method,
    matched_by: clicked.matched_by,
    verified: {
      menu_closed: !(await watermarkMenuOpen(frame).catch(() => null)),
      checkbox_selection: selection.selected,
      saw_progress: applied.saw_progress,
      saw_done: applied.saw_done,
      images_changed: applied.changedPositions,
    },
  };
}

const VIDEO_TEXT_TITLE = '\u89c6\u9891\u7ba1\u7406'; // \u89c6\u9891\u7ba1\u7406 dialog title
const VIDEO_TEXT = Object.freeze({
  search: '\u641c\u7d22', // \u641c\u7d22 \u2014 reloads the library list (\u66f4\u65b0\u72b6\u6001 only refreshes existing rows)
  choose: '\u9009\u62e9', // \u9009\u62e9 \u2014 attaches a library row's video to the detail form
  titleSearchType: '\u89c6\u9891\u6807\u9898', // \u89c6\u9891\u6807\u9898 \u2014 default search-type in the library toolbar
  idSearchType: '\u89c6\u9891ID', // \u89c6\u9891ID \u2014 search by video id (template default-video flow)
});
const MABANG_API = 'https://api.mabangerp.com/kandeng/api/v2';
// 视频库里「搜索 → 找行 → 点选择」的重试次数：每次约 1.2~1.5s（DOM click，不等 actionability），
// 所以 4 次足够覆盖列表刷新的抖动；原来 12 次 × 30s 的默认超时是纯浪费。
const VIDEO_SEARCH_ATTEMPTS = 4;

// True when the \u89c6\u9891\u7ba1\u7406 (video library) dialog is on screen and visible.
async function videoAdminOpen(frame) {
  return frame.evaluate((title) => Array.from(document.querySelectorAll('.el-dialog__wrapper')).some((w) => {
    const t = ((w.querySelector('.el-dialog__title') || {}).textContent || '').replace(/\s+/g, '');
    return t === title && getComputedStyle(w).display !== 'none';
  }), VIDEO_TEXT_TITLE);
}

// Open \u89c6\u9891\u7ba1\u7406 from the \u4ea7\u54c1\u89c6\u9891 form-item. Handles both states: a clean item exposes an
// upload icon; an item that already carries a video swaps it for a "\u66f4\u6362\u89c6\u9891" (replace) icon.
async function openVideoAdmin(frame) {
  if (await videoAdminOpen(frame)) return true;
  await frame.evaluate(() => {
    const items = Array.from(document.querySelectorAll('.el-form-item'));
    for (const item of items) {
      const label = (item.querySelector('.el-form-item__label') || {}).textContent || '';
      if (!/\u89c6\u9891/.test(label)) continue;
      const uploadIcon = item.querySelector('.upload-icon') || item.querySelector('[class*="upload-icon"]') || item.querySelector('i[class*="upload"]');
      if (uploadIcon) { uploadIcon.click(); return; }
      // Already-attached state: click the \u66f4\u6362\u89c6\u9891 (replace) control to reopen the library.
      const replace = Array.from(item.querySelectorAll('img[title], .cursor')).find((el) => /\u66f4\u6362\u89c6\u9891/.test(el.getAttribute('title') || (el.querySelector('img[title]') || {}).getAttribute?.('title') || ''));
      const clickable = replace && (replace.closest('.cursor') || replace);
      if (clickable) { clickable.click(); return; }
    }
  });
  for (let i = 0; i < 12 && !(await videoAdminOpen(frame)); i += 1) await sleep(500);
  return videoAdminOpen(frame);
}

// Read the currently-attached video from the detail form (idempotency check).
async function attachedFormVideo(frame) {
  return frame.evaluate(() => {
    const roots = [...document.querySelectorAll('*')].map((node) => node.__vue__).filter(Boolean);
    const seen = new Set();
    let found = null;
    const visit = (value, depth) => {
      if (found || !value || typeof value !== 'object' || depth > 7 || seen.has(value)) return;
      seen.add(value);
      if (value._data && value._data.videoInfo && typeof value._data.videoInfo === 'object') {
        const info = value._data.videoInfo;
        if (info.url || info.name) found = { url: String(info.url || ''), name: String(info.name || '') };
      }
      for (const [key, child] of Object.entries(value)) {
        if (key.startsWith('$') && key !== '$refs' && key !== '$children') continue;
        if (child && typeof child === 'object') visit(child, depth + 1);
      }
    };
    roots.slice(0, 40).forEach((root) => visit(root, 0));
    return found || { url: '', name: '' };
  });
}

// Capture Mabang API auth headers (Bearer token + projectid/cluster-id/key) from a live
// request. The app's axios interceptor adds these; they are the only way to replay
// upVideo/getVideo/shop-list against api.mabangerp.com (cookies alone return 401, and a
// credentialed cross-origin fetch is blocked by CORS — an APIRequestContext replay is not).
function attachAuthCapture(page) {
  const state = { auth: null };
  const handler = async (req) => {
    if (state.auth) return;
    // Every authenticated call to api.mabangerp.com carries the same interceptor headers,
    // so capture from ANY of them (shop/list, getVideo, and the byte-upload POST all work).
    if (!/api\.mabangerp\.com/i.test(req.url())) return;
    try {
      const headers = await req.allHeaders();
      const auth = {};
      for (const key of ['authorization', 'projectid', 'cluster-id', 'key']) {
        if (headers[key] != null) auth[key] = headers[key];
      }
      if (auth.authorization) state.auth = auth;
    } catch (_e) { /* header read races are fine; the next request retries */ }
  };
  page.on('request', handler);
  return { state, detach: () => page.off('request', handler) };
}

// Resolve a store's numeric shop_id from its display name via the publish shop list.
// The list record's `name` is the human store name (e.g. "POD菲律宾") and `id` is the shop_id.
async function resolveShopId(context, auth, storeName) {
  const wanted = compactText(storeName);
  if (!wanted) return null;
  const res = await context.request.get(`${MABANG_API}/common/publish/shop/list?platformId=17&type=v2`, { headers: auth });
  let list = [];
  try { const body = await res.json(); list = Array.isArray(body.data) ? body.data : []; } catch (_e) { /* noop */ }
  const shop = list.find((s) => compactText(s.name) === wanted);
  return shop ? String(shop.id) : null;
}

// The 视频管理 wrapper is a fixed full-screen overlay: a stage failure while it is
// open makes every later click die on "subtree intercepts pointer events". Close
// it best-effort via its header X so a failed video stage can't poison the run.
async function closeVideoAdminDialog(frame) {
  return frame.evaluate((title) => {
    const norm = (s) => String(s || '').replace(/\s+/g, '');
    const wrapper = Array.from(document.querySelectorAll('.el-dialog__wrapper')).find((w) => {
      if (getComputedStyle(w).display === 'none') return false;
      const dialog = w.querySelector('.el-dialog');
      return dialog && norm((dialog.querySelector('.el-dialog__title') || {}).textContent) === norm(title);
    });
    if (!wrapper) return false;
    const close = wrapper.querySelector('.el-dialog__headerbtn');
    if (close) close.click();
    return true;
  }, VIDEO_TEXT_TITLE).catch(() => false);
}

// 点 视频管理 里的「搜索」：**用 DOM click**，不要用 Playwright click。
// 现场 2026-09-27（任务 45fff5a5，optional_media 花了 398.9s）：视频管理是整屏 overlay，
// Playwright 的 click 一直被它的子树拦下（"subtree intercepts pointer events"），于是每次
// 吃满 30s 默认超时、又被 .catch() 吞掉，12 次重试白烧 ~360s；更糟的是搜索从未真正执行，
// 列表没刷新 → 行找不到 → video_select_failed:row_not_found。
// 行内的「选择」按钮本来就是这个 DOM click 路子，这里保持一致。
async function clickVideoAdminSearch(frame) {
  return frame.evaluate((title) => {
    const norm = (value) => String(value || '').replace(/\s+/g, '');
    const dialog = Array.from(document.querySelectorAll('.el-dialog'))
      .find((node) => norm((node.querySelector('.el-dialog__title') || {}).textContent) === norm(title));
    if (!dialog) return false;
    const button = Array.from(dialog.querySelectorAll('button, .el-button'))
      .find((node) => norm(node.textContent) === '\u641c\u7d22');
    if (!button) return false;
    button.click();
    return true;
  }, VIDEO_TEXT_TITLE).catch(() => false);
}

// 视频管理里「搜索 → 找到这一行 → 点选择」的公共循环：有界、快速失败，不再空等 30s。
async function pickVideoLibraryRow(frame, wanted) {
  let clicked = { ok: false, why: 'never_ran' };
  for (let i = 0; i < VIDEO_SEARCH_ATTEMPTS && !clicked.ok; i += 1) {
    const searched = await clickVideoAdminSearch(frame);
    if (!searched) return { ok: false, why: 'search_button_not_found' };
    await sleep(i === 0 ? 1500 : 1200);
    clicked = await frame.evaluate(({ title, wantedId, wantedName, chooseLabel, requireUploaded }) => {
      const norm = (value) => String(value || '').replace(/\s+/g, '');
      const dialog = Array.from(document.querySelectorAll('.el-dialog'))
        .find((node) => norm((node.querySelector('.el-dialog__title') || {}).textContent) === norm(title));
      if (!dialog) return { ok: false, why: 'no_dialog' };
      const rows = Array.from(dialog.querySelectorAll('table.vxe-table--body tbody tr'));
      for (const row of rows) {
        const text = norm(row.textContent);
        if (wantedId && !text.includes(norm(wantedId))) continue;
        if (!wantedId && requireUploaded && !text.includes('\u4e0a\u4f20\u6210\u529f')) continue;
        if (wantedName && !wantedId && !text.includes(norm(wantedName))) continue;
        const button = Array.from(row.querySelectorAll('button,a,.el-button')).find((node) => norm(node.textContent) === norm(chooseLabel));
        if (button) { button.click(); return { ok: true }; }
      }
      return { ok: false, why: 'row_not_found' };
    }, wanted);
  }
  return clicked;
}

// 把搜索词写进视频库搜索框：**placeholder=请输入 的非只读输入框**（原生 setter + input/change
// 事件；现场实测能真正过滤：AL534 → 1 条、AL052 → 3 条、空 → 第 1 页 10 条 / 共 3462 条）。
// 现场 2026-09-27 的坑：原实现挑"第一个非只读输入框"，工具栏前面还有「上传开始日期 /
// 上传结束日期」两个日期框 → 搜索词被写进日期框，搜索从未过滤，列表永远是第 1 页那 10 行 →
// video_select_failed:row_not_found。这里显式跳过日期框与分页的「条/页」框。
async function typeVideoSearchValue(frame, value) {
  return frame.evaluate(({ title, text }) => {
    const norm = (node) => String(node || '').replace(/\s+/g, ' ').trim();
    const compact = (node) => String(node || '').replace(/\s+/g, '').trim();
    const dialog = Array.from(document.querySelectorAll('.el-dialog'))
      .find((node) => compact((node.querySelector('.el-dialog__title') || {}).textContent) === compact(title));
    if (!dialog) return 'no_dialog';
    const usable = Array.from(dialog.querySelectorAll('input')).filter((input) => !input.hasAttribute('readonly')
      && !['checkbox', 'radio', 'file', 'hidden'].includes(input.type)
      && !/\u4e0a\u4f20(\u5f00\u59cb|\u7ed3\u675f)\u65e5\u671f/.test(input.placeholder || '')
      && !/\u6761\/\u9875/.test(input.value || ''));
    const input = usable.find((node) => norm(node.placeholder) === '\u8bf7\u8f93\u5165') || usable[0];
    if (!input) return 'no_input';
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(input, text);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return 'typed:' + norm(input.placeholder);
  }, { title: VIDEO_TEXT_TITLE, text: value }).catch(() => 'evaluate_failed');
}

// 视频库「搜索类型」下拉（视频标题 / 视频ID）：DOM 点击 + 回读确认。
async function switchVideoSearchType(frame, label) {
  const clicked = await frame.evaluate(({ title, wanted }) => {
    const compact = (value) => String(value || '').replace(/\s+/g, '').trim();
    const dialog = Array.from(document.querySelectorAll('.el-dialog'))
      .find((node) => compact((node.querySelector('.el-dialog__title') || {}).textContent) === compact(title));
    if (!dialog) return 'no_dialog';
    const select = dialog.querySelector('.el-select');
    if (!select) return 'no_select';
    const input = select.querySelector('input');
    if (compact(input && input.value) === compact(wanted)) return 'already';
    const suffix = select.querySelector('.el-input__suffix');
    if (suffix) suffix.click();
    return 'clicked';
  }, { title: VIDEO_TEXT_TITLE, wanted: label }).catch(() => 'error');
  if (clicked === 'already') return true;
  if (clicked !== 'clicked') return false;
  await sleep(500);
  const optionClicked = await frame.evaluate(({ wanted }) => {
    const norm = (value) => String(value || '').replace(/\s+/g, ' ').trim();
    const visible = (node) => !!(node.offsetWidth || node.offsetHeight);
    const poppers = Array.from(document.querySelectorAll('.el-select-dropdown')).filter(visible);
    for (const popper of poppers) {
      const option = Array.from(popper.querySelectorAll('.el-select-dropdown__item'))
        .find((node) => norm(node.textContent) === norm(wanted));
      if (option) { option.click(); return true; }
    }
    return false;
  }, { wanted: label }).catch(() => false);
  await sleep(400);
  const now = await frame.evaluate(({ title }) => {
    const compact = (value) => String(value || '').replace(/\s+/g, '').trim();
    const dialog = Array.from(document.querySelectorAll('.el-dialog'))
      .find((node) => compact((node.querySelector('.el-dialog__title') || {}).textContent) === compact(title));
    const input = dialog && dialog.querySelector('.el-select input');
    return compact(input && input.value);
  }, { title: VIDEO_TEXT_TITLE }).catch(() => '');
  return optionClicked && now === compactText(label);
}

// 库里没匹配上时回报现场：搜了什么、库里共多少条 —— 操作员据此区分"库里有但没匹配上"
// 还是"库里根本没有这条视频"。
async function videoLibrarySummary(frame, videoId, videoTitle) {
  return frame.evaluate(({ title, wantedId, wantedTitle }) => {
    const norm = (value) => String(value || '').replace(/\s+/g, ' ').trim();
    const compact = (value) => String(value || '').replace(/\s+/g, '').trim();
    const dialog = Array.from(document.querySelectorAll('.el-dialog'))
      .find((node) => compact((node.querySelector('.el-dialog__title') || {}).textContent) === compact(title));
    if (!dialog) return { error: 'no_dialog' };
    const pager = Array.from(dialog.querySelectorAll('.el-pagination, .vxe-pager')).map((node) => norm(node.textContent)).join(' ');
    const total = (pager.match(/\u5171\s*(\d+)\s*\u6761/) || [])[1] || '';
    return {
      video_id: wantedId || '',
      video_title: wantedTitle || '',
      library_total: total,
      rows_on_page: dialog.querySelectorAll('table.vxe-table--body tbody tr').length,
    };
  }, { title: VIDEO_TEXT_TITLE, wantedId: videoId, wantedTitle: videoTitle }).catch(() => ({ error: 'evaluate_failed' }));
}

// 模板默认视频（本土）：在 视频管理 库里按 videoId 精确选择，不碰上传。
// 操作员手工路径即自动化路径：打开 视频管理 → 搜索类型下拉（默认 视频标题）改选
// 「视频ID」→ 搜索框键入 videoId → 搜索 → 点该行「选择」。element-ui 的选中值存在
// input.value 里（不在 textContent），搜索框的 Vue 绑定要逐字键入（见 typeIntoField）。
async function selectTemplateVideoById(frame, videoId, videoTitle = '') {
  if (!(await openVideoAdmin(frame))) throw new Error('video_admin_dialog_not_open');
  try {
    // 1) 搜索类型切到「视频ID」（DOM 点击 + 回读确认；Playwright 的 actionability 会被整屏 overlay 挡住）。
    const switched = await switchVideoSearchType(frame, VIDEO_TEXT.idSearchType);
    let clicked = { ok: false, why: switched ? 'never_ran' : 'search_type_switch_failed' };
    if (switched) {
      // 2) 搜索词写进真正的搜索框（placeholder=请输入），再搜索并点该行的「选择」。
      const typed = await typeVideoSearchValue(frame, videoId);
      attrPickDebug({ phase: 'video_search', field: 'video_id', typed, value: videoId });
      if (String(typed).startsWith('typed')) {
        clicked = await pickVideoLibraryRow(frame, {
          title: VIDEO_TEXT_TITLE, wantedId: videoId, wantedName: '', chooseLabel: VIDEO_TEXT.choose, requireUploaded: false,
        });
      }
    }
    // 3) 按 ID 找不到时退回按「视频标题」= 主 SKU 搜一次（模板默认视频的标题就是主 SKU）：
    //    库是 3400+ 条的大库，ID 可能已失效/被删，标题搜索往往还能命中。
    if (!clicked.ok && videoTitle) {
      await switchVideoSearchType(frame, VIDEO_TEXT.titleSearchType);
      const typedByName = await typeVideoSearchValue(frame, videoTitle);
      attrPickDebug({ phase: 'video_search', field: 'video_title', typed: typedByName, value: videoTitle });
      if (String(typedByName).startsWith('typed')) {
        clicked = await pickVideoLibraryRow(frame, {
          title: VIDEO_TEXT_TITLE, wantedId: '', wantedName: videoTitle, chooseLabel: VIDEO_TEXT.choose, requireUploaded: true,
        });
      }
    }
    if (!clicked.ok) {
      // 把现场一次带出去：搜了什么、库里共多少条 —— 操作员据此判断是"库里根本没有这条视频"。
      const summary = await videoLibrarySummary(frame, videoId, videoTitle);
      throw new Error(`video_select_failed:${clicked.why}:${JSON.stringify(summary)}`);
    }
    await sleep(1500);
    const attached = await attachedFormVideo(frame);
    if (!attached.url) throw new Error('video_attach_not_verified');
    return { status: 'attached', video_id: videoId, video_url: attached.url, source: 'template_default' };
  } finally {
    await closeVideoAdminDialog(frame);
  }
}

async function uploadProductVideo(frame, task, page) {
  const video = task.video || {};
  if (video.action_status !== 'ready' || video.validation_status !== 'valid') {
    return { status: 'skipped', code: (video.validation_errors || [])[0] || 'video_missing' };
  }
  // 模板默认视频（本土）：副本带 default_video_id 时直接按 ID 选择，不再上传。
  const defaultVideoId = cleanText(video.default_video_id);
  if (video.select_by_id && defaultVideoId) {
    return selectTemplateVideoById(frame, defaultVideoId, cleanText(video.video_title));
  }
  const uploadPath = cleanFilePath(video.upload_path);
  if (!uploadPath || !fs.existsSync(uploadPath)) throw new Error('video_file_missing');
  if (!page) throw new Error('video_upload_requires_page');
  // Video title = 主 SKU（task.video.video_title，跨境/本土一致）；没有主 SKU 时
  // 退回原文件名（不是 on-disk 的 product.mp4 拷贝名）。
  const videoName = cleanText(video.video_title) || cleanText(video.file_name) || require('path').basename(uploadPath);
  const storeName = ((task.store || {}).name) || '';

  // Idempotency: if a video is already attached to the form, skip re-uploading.
  const existing = await attachedFormVideo(frame);
  if (existing.url && compactText(existing.name) === compactText(videoName)) {
    return { status: 'already_attached', file_name: videoName, video_url: existing.url };
  }

  const context = page.context();
  const authCapture = attachAuthCapture(page);
  let dialogOpened = false;
  try {
    // 1) Open 视频管理. This lays out the library table (+ 选择 rows) and fires shop/list +
    //    getVideo, from which we opportunistically capture the API auth headers.
    if (!(await openVideoAdmin(frame))) throw new Error('video_admin_dialog_not_open');
    dialogOpened = true;

    // 2) Byte upload. setInputFiles on the hidden input#uploadVideo fires the page's own
    //    change-handler (the visible 上传视频/#UploadDialog path never lays out reliably —
    //    its wrapper stays display:none, and the native file chooser blocks the page). Scope
    //    to the input inside the VISIBLE 视频管理 dialog: a second input#uploadVideo lives in
    //    the hidden #UploadDialog, and a bare .first() can land on that dead (display:none) one.
    //    This POST to api.mabangerp.com also guarantees the auth headers get captured.
    let uploadedUrl = null;
    const waitUpload = page.waitForResponse((r) => /common\/image\/upload\/video/i.test(r.url()), { timeout: 90000 })
      .then(async (r) => { try { const b = await r.json(); uploadedUrl = b && b.data && b.data.videoUrl; } catch (_e) { /* noop */ } return r; })
      .catch(() => null);
    // Only the uploaded BYTES matter here — the library title is set by the video_name we
    // pass to upVideo below (the upload API returns a server-generated numeric object name).
    await frame.locator('.el-dialog__wrapper:visible', { hasText: VIDEO_TEXT_TITLE })
      .locator('input#uploadVideo').first().setInputFiles(uploadPath);
    await waitUpload;
    if (!uploadedUrl) throw new Error('video_bytes_upload_failed');

    // The upload triggers the page's own #UploadDialog (上传视频) to pop over 视频管理. We
    // register via the API below, so close it (header X) — otherwise it overlays and blocks
    // the 搜索/选择 buttons in 视频管理. Closing does not cancel the already-uploaded bytes.
    await frame.evaluate(() => {
      const ud = document.querySelector('#UploadDialog');
      if (ud && getComputedStyle(ud).display !== 'none') {
        const x = ud.querySelector('.el-dialog__headerbtn');
        if (x) x.click();
      }
    });
    await sleep(1000);

    // 3) Auth is now guaranteed captured (from the upload POST at minimum).
    for (let i = 0; i < 10 && !authCapture.state.auth; i += 1) await sleep(300);
    const auth = authCapture.state.auth;
    if (!auth) throw new Error('video_api_auth_not_captured');

    // 4) Resolve the listing store's shop_id, then register into the library (upVideo).
    const shopId = await resolveShopId(context, auth, storeName);
    if (!shopId) throw new Error('video_store_not_found');
    const register = await context.request.post(`${MABANG_API}/shopee/upVideo`, {
      headers: { ...auth, 'content-type': 'application/json' },
      data: { video_name: videoName, video_url: uploadedUrl, shop_id: shopId },
    });
    if (register.status() !== 200) throw new Error(`video_register_failed:${register.status()}`);

    // 5) Poll getVideo (server-side transcode) until our record reaches SUCCEEDED with a
    //    real videoId. Status/videoId are async — never trust the immediate table row.
    const targetName = compactText(videoName);
    const getVideoUrl = `${MABANG_API}/shopee/getVideo?status=&page=1&page_size=10&search_type=videoName&search_value=&shop_id=${shopId}`;
    const deadline = Date.now() + 180000;
    let record = null;
    while (Date.now() < deadline) {
      await sleep(5000);
      let list = [];
      try {
        const res = await context.request.get(getVideoUrl, { headers: auth });
        const body = await res.json();
        if (body && body.data && Array.isArray(body.data.list)) list = body.data.list;
      } catch (_e) { /* transient; retry on the next tick */ }
      const mine = list.filter((v) => compactText(v.videoName) === targetName);
      const succeeded = mine.find((v) => v.status === 'SUCCEEDED' && v.videoId && v.videoId !== '--');
      if (succeeded) { record = succeeded; break; }
      const pending = mine.some((v) => v.status === 'PENDING' || !v.status);
      const failed = mine.find((v) => v.status === 'FAILED');
      if (!pending && failed) throw new Error(`video_upload_failed:${cleanText(failed.message) || 'FAILED'}`);
    }
    if (!record) throw new Error('video_upload_timeout');

    // 6) Attach to the form: click 搜索 to RELOAD the library (更新状态 only refreshes the
    //    status of already-loaded rows — the freshly registered row is not among them), then
    //    click 选择 on the matching SUCCEEDED row (writes videoId/videoUrl into the Vue form).
    const clicked = await pickVideoLibraryRow(frame, {
      title: VIDEO_TEXT_TITLE, wantedId: record.videoId, wantedName: videoName,
      chooseLabel: VIDEO_TEXT.choose, requireUploaded: true,
    });
    if (!clicked.ok) throw new Error(`video_select_failed:${clicked.why}`);
    await sleep(1500);

    // 7) Verify the form now carries the video (url populated, dialog auto-closed).
    const attached = await attachedFormVideo(frame);
    if (!attached.url) throw new Error('video_attach_not_verified');
    return {
      status: 'attached',
      file_name: videoName,
      video_id: record.videoId,
      video_url: attached.url,
      upload_url: uploadedUrl,
    };
  } finally {
    authCapture.detach();
    if (dialogOpened) await closeVideoAdminDialog(frame);
  }
}

async function runOptionalMediaStages(frame, task, operations = {}) {
  const apply = operations.applyWatermark || applyWatermark;
  const upload = operations.uploadVideo || uploadProductVideo;
  const page = operations.page || null;
  const stages = {};
  const warnings = [];
  const runRecoverable = async (stage, operation, context = {}) => {
    try {
      stages[stage] = await operation();
    } catch (error) {
      stages[stage] = { status: 'warning', code: cleanText(error && error.message) || `${stage}_failed` };
    }
    // Media stages never fail the draft — a failure/skip is downgraded to a warning
    // so the draft still saves and a human is alerted to handle the media manually.
    // Attach context (e.g. the video file name) so the operator knows what to act on.
    for (const [key, value] of Object.entries(context)) {
      if (value && stages[stage][key] === undefined) stages[stage][key] = value;
    }
    if (['warning', 'skipped'].includes(stages[stage].status)) {
      warnings.push({ stage, ...stages[stage] });
    }
    emit('stage', { stage, ...stages[stage] });
  };
  await runRecoverable('apply_main_image_watermark', () => apply(frame, 'main_image', (task.watermarks || {}).main_image || {}));
  await runRecoverable('apply_product_image_watermark', () => apply(frame, 'product_images', (task.watermarks || {}).product_images || {}));
  const videoContext = (task.video || {});
  await runRecoverable('video_upload', () => upload(frame, task, page), {
    file_name: cleanText(videoContext.file_name),
  });
  return { ...stages, warnings };
}

async function skuTableAudit(frame) {
  return frame.evaluate(() => {
    const tables = [...document.querySelectorAll('.vxe-table')].map((element) => element.__vue__).filter(Boolean);
    const table = tables.find((vm) => (vm.tableColumn || []).some((column) => ['sku', 'sellerSku', 'variationSku'].includes(column.field)));
    return { rows: table && table.tableData ? table.tableData.length : 0 };
  });
}

async function variantCard(frame) {
  const cards = frame.locator('.el-card:visible').filter({ hasText: TEXT.variantInfo });
  if ((await cards.count()) !== 1) {
    throw new BlockedError('variant_card_not_unique', { count: await cards.count() });
  }
  return cards.first();
}

// The Shopee detail form starts in 单品 (single) mode; the 变种信息 (variant info)
// card that holds the axis picker and .natureBlock value editors only renders after
// the 变种 radio is selected. That radio lives in the 交易信息 card while in single
// mode, so it must be located at the frame level (not inside the variant card, which
// does not exist yet).
async function selectVariantMode(frame) {
  const radios = frame.locator('label.el-radio');
  const findVariantRadio = async () => {
    for (let index = 0, count = await radios.count().catch(() => 0); index < count; index += 1) {
      const radio = radios.nth(index);
      if (compactText(await radio.innerText().catch(() => '')) !== compactText(TEXT.variant)) continue;
      if (!(await isVisible(radio))) continue;
      return radio;
    }
    return null;
  };
  let target = await findVariantRadio();
  if (!target) throw new BlockedError('variant_radio_not_found');
  const checked = await target.evaluate((node) => node.classList.contains('is-checked')).catch(() => false);
  if (!checked) {
    // The radio list re-renders while the 交易信息 card settles; a positional
    // .nth(i) captured here can point at a hidden radio by click time.
    try {
      await clickWithRescan(async () => {
        const radio = await findVariantRadio();
        if (!radio) return null;
        const stillUnchecked = !(await radio.evaluate((node) => node.classList.contains('is-checked')).catch(() => true));
        return stillUnchecked ? radio : null;
      });
    } catch (error) {
      // Outcome-based: a reported click failure that still left the radio checked
      // (or the card rendered) achieved the goal — only rethrow on real failure.
      const confirmRadio = await findVariantRadio();
      const nowChecked = confirmRadio
        ? await confirmRadio.evaluate((node) => node.classList.contains('is-checked')).catch(() => false)
        : false;
      if (!nowChecked) throw error;
    }
  }
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const cards = frame.locator('.el-card:visible').filter({ hasText: TEXT.variantInfo });
    if ((await cards.count().catch(() => 0)) === 1) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new BlockedError('variant_info_card_not_shown');
}

async function selectedAxisBlock(card, axis) {
  const blocks = card.locator('.natureBlock');
  for (let index = 0, count = await blocks.count(); index < count; index += 1) {
    const block = blocks.nth(index);
    if (await block.getByText(String(axis), { exact: true }).count()) return block;
  }
  return null;
}

async function ensureVariantAxis(card, axis) {
  let block = await selectedAxisBlock(card, axis);
  if (block) return block;
  const collectVisibleOption = async () => {
    const options = card.getByText(String(axis), { exact: true });
    const visible = [];
    for (let index = 0, count = await options.count(); index < count; index += 1) {
      const option = options.nth(index);
      if (await isVisible(option)) visible.push(option);
    }
    if (visible.length !== 1) {
      throw new BlockedError('variant_axis_option_not_unique', { axis, count: visible.length });
    }
    return visible[0];
  };
  await clickWithRescan(collectVisibleOption);
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    block = await selectedAxisBlock(card, axis);
    if (block) return block;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new BlockedError('variant_axis_not_selected', { axis });
}

async function addAxisValue(card, axis, value) {
  const block = await ensureVariantAxis(card, axis);
  if (await block.getByText(String(value), { exact: true }).count()) return;
  const input = block.locator('input[placeholder="\u8bf7\u8f93\u5165"]').first();
  const buttons = block.locator('button').filter({ hasText: TEXT.addCustom });
  if ((await input.count()) !== 1 || (await buttons.count()) !== 1) {
    throw new BlockedError('variant_axis_value_controls_not_unique', { axis });
  }
  await input.fill(String(value));
  await buttons.first().click();
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (await block.getByText(String(value), { exact: true }).count()) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new BlockedError('variant_axis_value_not_added', { axis, value: String(value) });
}

async function waitForSkuRows(frame, expectedRows, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  let audit = { rows: 0 };
  while (Date.now() < deadline) {
    audit = await skuTableAudit(frame);
    if (audit.rows === expectedRows) return audit;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return audit;
}

async function configureVariants(frame, task) {
  const variation = task.variation || {};
  const colors = variation.colors || [];
  const sizes = variation.sizes || [];
  const expectedRows = Number((task.sku_grid || {}).expected_rows || colors.length * sizes.length);
  let audit = await skuTableAudit(frame);
  if (audit.rows === expectedRows && expectedRows > 0) {
    emit('stage', { stage: 'configure_variants', ok: true, method: 'already_configured' });
    return { ...audit, method: 'already_configured' };
  }

  await selectVariantMode(frame);
  const card = await variantCard(frame);
  await ensureVariantAxis(card, 'color');
  await ensureVariantAxis(card, 'size');
  for (const color of colors) {
    await addAxisValue(card, 'color', color);
  }
  for (const size of sizes) {
    await addAxisValue(card, 'size', size);
  }
  audit = await waitForSkuRows(frame, expectedRows);
  if (audit.rows !== expectedRows) {
    throw new BlockedError('sku_row_count_after_variant_mismatch', { expected: expectedRows, actual: audit.rows });
  }
  emit('stage', { stage: 'configure_variants', ok: true, rows: audit.rows });
  return audit;
}

// Uncheck "分sku设置件重尺" in the 交易信息 card. When checked (the fresh-draft
// default), weight/package-size are per-SKU columns in the grid; unchecking moves
// them to the product-level 物流信息 card (see fillLogisticsInfo). Idempotent: does
// nothing if already unchecked. Confirms by waiting for the weight column to leave
// the SKU grid.
const SKU_WEIGHT_LABEL = '分sku设置件重尺';

async function skuGridHasWeightColumn(frame) {
  // In the checked state the SKU grid gains weight/package-size columns whose vxe
  // field is `package_weight` (verified live — it appears once for 重量 and once for
  // 件重尺寸). Match that plus a few defensive aliases so we can confirm the columns
  // actually leave the grid after unchecking.
  return frame.evaluate(() => {
    const tables = [...document.querySelectorAll('.vxe-table')].map((el) => el.__vue__).filter(Boolean);
    const table = tables.find((vm) => (vm.tableColumn || []).some((c) => ['sku', 'sellerSku', 'variationSku'].includes(c.field)) && (vm.tableData || []).length);
    if (!table) return false;
    const weightFields = ['package_weight', 'weight', 'packageLength', 'packageWidth', 'packageHeight', 'length', 'width', 'height'];
    return (table.tableColumn || []).some((c) => weightFields.includes(c.field));
  });
}

async function disableSkuWeight(frame) {
  const state = await frame.evaluate((labelText) => {
    const compact = (el) => (el ? (el.textContent || '').replace(/\s+/g, '') : '');
    const boxes = [...document.querySelectorAll('label.el-checkbox')].filter((el) => compact(el.querySelector('.el-checkbox__label')) === labelText);
    if (boxes.length !== 1) return { found: boxes.length, checked: null };
    return { found: 1, checked: boxes[0].classList.contains('is-checked') };
  }, SKU_WEIGHT_LABEL);
  if (state.found !== 1) throw new BlockedError('sku_weight_checkbox_not_unique', { count: state.found });
  if (state.checked === false) {
    emit('stage', { stage: 'disable_sku_weight', ok: true, method: 'already_unchecked' });
    return { status: 'ok', method: 'already_unchecked' };
  }
  const target = frame.locator('label.el-checkbox').filter({ hasText: SKU_WEIGHT_LABEL });
  if ((await target.count()) !== 1) throw new BlockedError('sku_weight_checkbox_not_unique', { count: await target.count() });
  await target.first().click();
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const stillChecked = await frame.evaluate((labelText) => {
      const compact = (el) => (el ? (el.textContent || '').replace(/\s+/g, '') : '');
      const box = [...document.querySelectorAll('label.el-checkbox')].find((el) => compact(el.querySelector('.el-checkbox__label')) === labelText);
      return box ? box.classList.contains('is-checked') : true;
    }, SKU_WEIGHT_LABEL);
    if (!stillChecked && !(await skuGridHasWeightColumn(frame))) {
      emit('stage', { stage: 'disable_sku_weight', ok: true, method: 'unchecked' });
      return { status: 'ok', method: 'unchecked' };
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new BlockedError('sku_weight_uncheck_not_confirmed');
}

function siteSkuRowIdentity(color, size) {
  const normalizedColor = cleanText(color).toLocaleLowerCase();
  const normalizedSize = cleanText(size).toLocaleLowerCase();
  if (!normalizedColor || !normalizedSize) return '';
  return `${normalizedColor}\u0000${normalizedSize}`;
}

// Mabang is free to reorder color values after the variation axes are created.
// Never assume its tableData order matches task.sku_grid.skus: that turns a valid
// 77-row grid into silently swapped seller SKUs (AL0471: PureWhite ← White,
// Red ← Purple). The visible color + size pair is the sole row identity.
function matchSiteSkuRows(plannedSkus, pageRows) {
  const plannedByIdentity = new Map();
  for (const sku of plannedSkus || []) {
    const identity = siteSkuRowIdentity(sku?.color_code || sku?.color_cn, sku?.size);
    if (!identity) throw new BlockedError('site_sku_plan_row_identity_missing', { sku_code: String(sku?.sku_code || '') });
    if (plannedByIdentity.has(identity)) {
      throw new BlockedError('site_sku_plan_row_identity_not_unique', { identity });
    }
    plannedByIdentity.set(identity, sku);
  }

  const assignments = [];
  const pageIdentities = new Set();
  for (let index = 0; index < (pageRows || []).length; index += 1) {
    const row = pageRows[index] || {};
    const identity = siteSkuRowIdentity(row.color, row.size);
    if (!identity) {
      throw new BlockedError('site_sku_page_row_identity_missing', { row_index: index, color: row.color || '', size: row.size || '' });
    }
    if (pageIdentities.has(identity)) {
      throw new BlockedError('site_sku_page_row_identity_not_unique', { row_index: index, identity });
    }
    pageIdentities.add(identity);
    const sku = plannedByIdentity.get(identity);
    if (!sku) {
      throw new BlockedError('site_sku_page_row_not_in_plan', { row_index: index, color: row.color || '', size: row.size || '' });
    }
    assignments.push({ row_index: index, sku_code: String(sku.sku_code || '') });
  }
  for (const identity of plannedByIdentity.keys()) {
    if (!pageIdentities.has(identity)) {
      throw new BlockedError('site_sku_plan_row_missing_from_page', { identity });
    }
  }
  return assignments;
}

async function readSiteSkuGridRows(frame) {
  const snapshot = await frame.evaluate(() => {
    const normalize = (value) => String(value || '').replace(/\s|\*/g, '').toLowerCase();
    const tables = [...document.querySelectorAll('.vxe-table')].map((element) => element.__vue__).filter(Boolean);
    const table = tables.find((vm) => (vm.tableColumn || []).some((column) => ['sku', 'sellerSku', 'variationSku'].includes(column.field)) && (vm.tableData || []).length);
    if (!table) return { ok: false, reason: 'sku_table_not_found' };
    const rows = table.tableData || [];
    const columns = table.tableColumn || [];
    const fieldFor = (names, titles) => {
      const matches = new Set(names.filter((name) => columns.some((column) => column.field === name) || rows.some((row) => Object.prototype.hasOwnProperty.call(row, name))));
      for (const column of columns) {
        const title = normalize(column.title || column.label || column.field);
        if (titles.some((candidate) => title.includes(normalize(candidate))) && column.field) matches.add(column.field);
      }
      return matches.size === 1 ? [...matches][0] : '';
    };
    const colorField = fieldFor(['color', 'colour', 'Color', '颜色'], ['颜色', '颜色属性']);
    const sizeField = fieldFor(['size', 'Size', '尺码', '尺寸'], ['尺码', '尺寸']);
    const skuField = fieldFor(['sku', 'sellerSku', 'variationSku'], ['SKU']);
    if (!colorField || !sizeField || !skuField) {
      return { ok: false, reason: 'site_sku_identity_columns_missing', missing: [!colorField && 'color', !sizeField && 'size', !skuField && 'sku'].filter(Boolean) };
    }
    return {
      ok: true,
      rows: rows.map((row) => ({
        color: String(row[colorField] || '').trim(),
        size: String(row[sizeField] || '').trim(),
        sku: String(row[skuField] || '').trim(),
      })),
    };
  });
  if (!snapshot.ok) throw new BlockedError(snapshot.reason, snapshot);
  return snapshot.rows;
}

function verifySiteSkuReadback(assignments, pageRows) {
  const mismatches = (assignments || []).flatMap((assignment) => {
    const actual = String(pageRows?.[assignment.row_index]?.sku || '').trim();
    return actual === String(assignment.sku_code || '').trim()
      ? []
      : [{ row_index: assignment.row_index, expected_sku: assignment.sku_code, actual_sku: actual }];
  });
  if (mismatches.length) throw new BlockedError('site_sku_readback_mismatch', { mismatches: mismatches.slice(0, 20) });
  return { rows: (assignments || []).length };
}

async function fillSkuGrid(frame, task) {
  const grid = task.sku_grid || {};
  const siteMode = String(task.store?.listing_mode || '') !== 'cnsc_global';
  const siteAssignments = siteMode ? matchSiteSkuRows(grid.skus || [], await readSiteSkuGridRows(frame)) : [];
  const result = await frame.evaluate((input) => {
    const grid = input.grid || {};
    const siteAssignments = input.siteAssignments || [];
    // Helpers must live inside evaluate — this body runs in the page context.
    const normalize = (value) => String(value || '').replace(/\s|\*/g, '').toLowerCase();
    const fieldFor = (row, columns, columnDefs, names, titleFragments = []) => {
      const name = names.find((candidate) => columns.has(candidate) || Object.prototype.hasOwnProperty.call(row, candidate));
      if (name) return name;
      const match = columnDefs.find((column) => {
        const title = normalize(column.title || column.label || column.field);
        return titleFragments.some((fragment) => title.includes(normalize(fragment)));
      });
      return match && match.field ? match.field : names[0];
    };
    const setGridValue = (row, columns, columnDefs, names, value, titleFragments = []) => {
      const name = fieldFor(row, columns, columnDefs, names, titleFragments);
      row[name] = value;
    };
    const applyTransactionInformation = (row, columns, columnDefs, values) => {
      // Only `stock` is a per-row requirement. Weight/package dims are product-level
      // (物流信息 card) whenever "分sku设置件重尺" is unchecked — their columns are then
      // absent from the SKU grid, so we only write them when the column still exists
      // (checked/legacy state) and never fail the row over their absence.
      const present = (key) => values != null && values[key] !== undefined && values[key] !== null && values[key] !== '';
      if (!present('stock')) return false;
      setGridValue(row, columns, columnDefs, ['stock', 'quantity', 'availableStock'], values.stock, ['库存']);
      const setIfColumn = (names, key) => {
        if (!present(key)) return;
        if (names.some((name) => columns.has(name))) setGridValue(row, columns, columnDefs, names, values[key]);
      };
      setIfColumn(['weight'], 'weight_kg');
      setIfColumn(['packageLength', 'length'], 'package_length_cm');
      setIfColumn(['packageWidth', 'width'], 'package_width_cm');
      setIfColumn(['packageHeight', 'height'], 'package_height_cm');
      return true;
    };

    const tables = [...document.querySelectorAll('.vxe-table')].map((element) => element.__vue__).filter(Boolean);
    const table = tables.find((vm) => (vm.tableColumn || []).some((column) => ['sku', 'sellerSku', 'variationSku'].includes(column.field)) && (vm.tableData || []).length);
    if (!table) return { ok: false, reason: 'sku_table_not_found', rows: 0 };
    const rows = table.tableData || [];
    if (rows.length !== Number(grid.expected_rows || 0)) {
      return { ok: false, reason: 'sku_row_count_mismatch', rows: rows.length };
    }
    const pricesBySku = new Map((grid.prices || []).map((price) => [String(price.sku_code || ''), price]));
    const columnDefs = table.tableColumn || [];
    const columns = new Set(columnDefs.map((column) => column.field));
    const globalMode = String(grid.listing_mode || '') === 'cnsc_global';
    const strictFieldFor = (names, titleFragments) => {
      const matches = new Set(names.filter((name) => columns.has(name)));
      for (const column of columnDefs) {
        const title = normalize(column.title || column.label || column.field);
        if (titleFragments.some((fragment) => title.includes(normalize(fragment))) && column.field) matches.add(column.field);
      }
      return matches.size === 1 ? [...matches][0] : '';
    };
    const cnscFields = globalMode ? {
      global_price: strictFieldFor(['globalPrice', 'global_product_price', 'globalProductPrice'], ['全球商品价格']),
      TH: strictFieldFor(['TH', 'TH_stock', 'THStock', 'thStock'], ['泰国仓库存']),
      CN_GZ: strictFieldFor(['CN_GZ', 'guangzhou', 'guangzhouStock', 'cnGzStock'], ['广州仓库存']),
      PH: strictFieldFor(['PH', 'PH_stock', 'PHStock', 'phStock'], ['菲律宾仓库存']),
    } : null;
    if (cnscFields) {
      const missing = Object.entries(cnscFields).filter(([, field]) => !field).map(([name]) => name);
      if (missing.length) return { ok: false, reason: 'cnsc_required_columns_missing', missing };
    }
    const siteSkuByRow = new Map(siteAssignments.map((assignment) => [Number(assignment.row_index), String(assignment.sku_code || '')]));
    const siteSkuByCode = new Map((grid.skus || []).map((sku) => [String(sku.sku_code || ''), sku]));
    for (let index = 0; index < rows.length; index += 1) {
      const row = rows[index];
      const siteSkuCode = siteSkuByRow.get(index);
      const sku = globalMode ? ((grid.skus || [])[index] || {}) : (siteSkuByCode.get(siteSkuCode) || {});
      if (!globalMode && !siteSkuCode) return { ok: false, reason: 'site_sku_row_assignment_missing', row_index: index };
      const price = pricesBySku.get(String(sku.sku_code || '')) || (grid.prices || [])[index] || {};
      setGridValue(row, columns, columnDefs, ['sku', 'sellerSku', 'variationSku'], sku.sku_code || '', ['SKU']);
      if (globalMode) row[cnscFields.global_price] = sku.global_price_cny || price.global_price_cny || '';
      else setGridValue(row, columns, columnDefs, ['price', 'original_price', 'originalPrice', 'sitePrice'], price.normal_price || price.price || '', ['价格']);
      if (!globalMode) setGridValue(row, columns, columnDefs, ['discountPrice', 'discount_price', 'salePrice', 'promotionPrice'], price.sale_price || price.discount_price || '', ['售价', '折扣价']);
      const transaction = grid.transaction_information || grid.transaction_info || sku;
      if (globalMode) {
        // 马帮把 泰国/广州/菲律宾仓库存 三列当必填：留空保存/发布时报错，0 是合法值。
        // 逐仓解析：SKU 自带值 → 副本级默认 → 0。空 dict 在 JS 里是真值，不能整行
        // 吞掉默认（`{} || defaults` 不会走 defaults），所以按仓库逐个回退。
        const skuStocks = (sku.warehouse_stocks && typeof sku.warehouse_stocks === 'object') ? sku.warehouse_stocks : {};
        const defaultStocks = grid.warehouse_stock_defaults || {};
        const stockFor = (code) => {
          for (const candidate of [skuStocks[code], defaultStocks[code]]) {
            if (candidate === undefined || candidate === null || candidate === '') continue;
            const value = Number(candidate);
            if (Number.isFinite(value) && value >= 0) return value;
          }
          return 0;
        };
        for (const code of ['TH', 'CN_GZ', 'PH']) row[cnscFields[code]] = stockFor(code);
      }
      if (!applyTransactionInformation(row, columns, columnDefs, transaction)) {
        return { ok: false, reason: 'transaction_information_incomplete', rows: rows.length, row_index: index };
      }
    }
    if (typeof table.updateData === 'function') table.updateData();
    if (typeof table.recalculate === 'function') table.recalculate();
    return { ok: true, rows: rows.length };
  }, { grid, siteAssignments });
  if (!result.ok && String(task.store?.listing_mode || '') === 'cnsc_global') {
    return fillCNSCSkuGridFromDom(frame, task);
  }
  if (!result.ok) throw new BlockedError(result.reason, result);
  if (siteMode) verifySiteSkuReadback(siteAssignments, await readSiteSkuGridRows(frame));
  emit('stage', { stage: 'fill_sku_grid', ok: true, rows: result.rows });
  return result;
}

const CNSC_DOM_COLUMN_DEFINITIONS = Object.freeze([
  { key: 'sku', matches: (title) => title === 'sku' || title === 'sellersku' },
  { key: 'global_price', matches: (title) => title.includes('全球商品价格') },
  { key: 'TH', matches: (title) => title.includes('泰国仓库存') },
  { key: 'CN_GZ', matches: (title) => title.includes('广州仓库存') },
  { key: 'PH', matches: (title) => title.includes('菲律宾仓库存') },
]);

function normalizeCNSCDomText(value) {
  return String(value || '').replace(/[\s*]/g, '').toLowerCase();
}

function vxeColumnId(className) {
  const match = String(className || '').match(/(?:^|\s)col_(\d+)(?:\s|$)/);
  return match ? `col_${match[1]}` : '';
}

function nextVxeColumnId(columnId) {
  const match = String(columnId || '').match(/^col_(\d+)$/);
  return match ? `col_${Number(match[1]) + 1}` : '';
}

// CNSC 仓库库存逐仓解析：SKU 自带值 → 副本级默认(grid.warehouse_stock_defaults) → 0。
// 马帮把 泰国/广州/菲律宾仓库存 三列当必填，留空在保存/发布时报错；0 是合法值必须照填。
function resolveCNSCWarehouseStock(sku, grid, code) {
  const skuStocks = (sku && sku.warehouse_stocks) || {};
  const defaultStocks = (grid && grid.warehouse_stock_defaults) || {};
  for (const candidate of [skuStocks[code], defaultStocks[code]]) {
    if (candidate === undefined || candidate === null || candidate === '') continue;
    const value = Number(candidate);
    if (Number.isFinite(value) && value >= 0) return value;
  }
  return 0;
}

function expectedCNSCDomRows(grid) {
  return (grid?.skus || []).map((sku) => ({
    sku: String(sku.sku_code || '').trim(),
    global_price: sku.global_price_cny,
    TH: resolveCNSCWarehouseStock(sku, grid, 'TH'),
    CN_GZ: resolveCNSCWarehouseStock(sku, grid, 'CN_GZ'),
    PH: resolveCNSCWarehouseStock(sku, grid, 'PH'),
  }));
}

// VXE renders the editable leaf cell one numeric column after its grouped header.
// Example from CNSC: header col_5 "全球商品价格" -> body col_6 input.
function buildCNSCDomGridPlan(grid, headers, bodyColumnIds, rowCount) {
  const expectedRows = Number.isFinite(Number(grid?.expected_rows)) ? Number(grid.expected_rows) : (grid?.skus || []).length;
  if (Number(rowCount) !== expectedRows) return { ok: false, reason: 'sku_row_count_mismatch', rows: Number(rowCount) || 0 };

  const presentBodyColumns = new Set(bodyColumnIds || []);
  const columns = {};
  const missing = [];
  for (const definition of CNSC_DOM_COLUMN_DEFINITIONS) {
    const matches = (headers || []).filter((header) => definition.matches(normalizeCNSCDomText(header.text)));
    const bodyColumn = matches.length === 1 ? nextVxeColumnId(vxeColumnId(matches[0].className)) : '';
    if (!bodyColumn || !presentBodyColumns.has(bodyColumn)) missing.push(definition.key);
    else columns[definition.key] = bodyColumn;
  }
  if (missing.length) return { ok: false, reason: 'cnsc_required_columns_missing', missing };
  return { ok: true, rows: expectedRows, columns };
}

function validateCNSCDomGridReadback(snapshot, grid) {
  const rows = Array.isArray(snapshot?.rows) ? snapshot.rows : [];
  const plan = buildCNSCDomGridPlan(grid, snapshot?.headers || [], snapshot?.body_column_ids || [], rows.length);
  if (!plan.ok) return plan;
  const expectedRows = expectedCNSCDomRows(grid);
  const mismatches = [];
  for (let index = 0; index < expectedRows.length; index += 1) {
    const expected = expectedRows[index];
    const actual = rows[index] || {};
    const fields = [];
    if (String(actual[plan.columns.sku] || '').trim() !== expected.sku) fields.push('sku');
    for (const field of ['global_price', 'TH', 'CN_GZ', 'PH']) {
      const actualValue = Number(actual[plan.columns[field]]);
      const expectedValue = Number(expected[field]);
      if (!Number.isFinite(actualValue) || actualValue !== expectedValue) fields.push(field);
    }
    if (fields.length) mismatches.push({ row_index: index, fields });
  }
  if (mismatches.length) return { ok: false, reason: 'cnsc_value_mismatch', rows: rows.length, mismatches };
  return { ok: true, rows: rows.length, mismatches: [] };
}

async function readCNSCDomGridSnapshot(frame) {
  return frame.evaluate(() => {
    // Two CNSC trade-info grids coexist once 变种 renders: the SKU grid
    // (color/size/SKU/仓库库存 columns) and the per-site price matrix below it.
    // Both are "visible" .tradeInfoTableRef-table.vxe-table nodes — select the
    // SKU grid by its headers, never by document order or count alone.
    const isSkuGrid = (node) => {
      const headers = [...node.querySelectorAll('.vxe-header--column')]
        .map((cell) => String(cell.innerText || '').replace(/\s+/g, ''));
      return headers.some((text) => text.includes('全球商品价格')) && headers.some((text) => text.includes('SKU'));
    };
    const tables = [...document.querySelectorAll('.tradeInfoTableRef-table.vxe-table')]
      .filter((node) => node.getClientRects().length > 0 && isSkuGrid(node));
    if (tables.length !== 1) return { table_count: tables.length, headers: [], body_column_ids: [], rows: [] };
    const table = tables[0];
    const headers = [...table.querySelectorAll('.vxe-header--column')].map((cell) => ({
      className: cell.className,
      text: String(cell.innerText || '').trim(),
    }));
    const rowNodes = [...table.querySelectorAll('.vxe-body--row')];
    const rows = rowNodes.map((row) => Object.fromEntries(
      [...row.querySelectorAll('.vxe-body--column')]
        .map((cell) => [String(cell.className || '').match(/(?:^|\s)(col_\d+)(?:\s|$)/)?.[1] || '', cell.querySelector('input.el-input__inner')?.value || ''])
        .filter(([column]) => Boolean(column)),
    ));
    const bodyColumnIds = [...new Set(rows.flatMap((row) => Object.keys(row)))];
    return { table_count: 1, headers, body_column_ids: bodyColumnIds, rows };
  });
}

async function fillCNSCSkuGridFromDom(frame, task) {
  const grid = task.sku_grid || {};
  const snapshot = await readCNSCDomGridSnapshot(frame);
  if (snapshot.table_count !== 1) throw new BlockedError('sku_table_not_found', { table_count: snapshot.table_count || 0 });
  const plan = buildCNSCDomGridPlan(grid, snapshot.headers, snapshot.body_column_ids, snapshot.rows.length);
  if (!plan.ok) throw new BlockedError(plan.reason, plan);
  const rows = expectedCNSCDomRows(grid);
  const writeResult = await frame.evaluate(({ columns, values }) => {
    // Same header-based selection as readCNSCDomGridSnapshot: the SKU grid, not
    // the per-site price matrix that also lives under .tradeInfoTableRef-table.
    const isSkuGrid = (node) => {
      const headers = [...node.querySelectorAll('.vxe-header--column')]
        .map((cell) => String(cell.innerText || '').replace(/\s+/g, ''));
      return headers.some((text) => text.includes('全球商品价格')) && headers.some((text) => text.includes('SKU'));
    };
    const table = [...document.querySelectorAll('.tradeInfoTableRef-table.vxe-table')]
      .filter((node) => node.getClientRects().length > 0 && isSkuGrid(node))[0];
    if (!table) return { ok: false, reason: 'sku_table_not_found' };
    const rowNodes = [...table.querySelectorAll('.vxe-body--row')];
    if (rowNodes.length !== values.length) return { ok: false, reason: 'sku_row_count_mismatch', rows: rowNodes.length };
    const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    if (!nativeSetter) return { ok: false, reason: 'cnsc_dom_input_setter_missing' };
    for (let index = 0; index < rowNodes.length; index += 1) {
      const row = rowNodes[index];
      const valuesByField = values[index];
      for (const field of ['sku', 'global_price', 'TH', 'CN_GZ', 'PH']) {
        const inputs = [...row.querySelectorAll(`.vxe-body--column.${columns[field]} input.el-input__inner`)];
        if (inputs.length !== 1) return { ok: false, reason: 'cnsc_dom_input_not_unique', row_index: index, field, count: inputs.length };
        const input = inputs[0];
        nativeSetter.call(input, String(valuesByField[field]));
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        input.dispatchEvent(new Event('blur', { bubbles: true }));
      }
    }
    return { ok: true, rows: rowNodes.length };
  }, { columns: plan.columns, values: rows });
  if (!writeResult.ok) throw new BlockedError(writeResult.reason, writeResult);
  const readback = validateCNSCDomGridReadback(await readCNSCDomGridSnapshot(frame), grid);
  if (!readback.ok) throw new BlockedError(readback.reason, readback);
  emit('stage', { stage: 'fill_sku_grid', ok: true, rows: readback.rows, method: 'cnsc_dom' });
  return { ...readback, method: 'cnsc_dom' };
}

// Pure, unit-testable check that the product-level 物流信息 values are complete.
// Weight (KG) and all three package dims (cm) are required by Mabang (marked *).
// A missing value is surfaced as a hard block (logistics_info_incomplete) so it goes
// to a human — matching the store-template contract that these come from the template.
const LOGISTICS_NUMERIC_FIELDS = Object.freeze(['weight_kg', 'package_length_cm', 'package_width_cm', 'package_height_cm']);

function logisticsValuesComplete(values) {
  if (values == null) return false;
  return LOGISTICS_NUMERIC_FIELDS.every((key) => {
    const raw = values[key];
    if (raw === undefined || raw === null || String(raw).trim() === '') return false;
    return Number.isFinite(Number(raw)) && Number(raw) > 0;
  });
}

function formatWeightKg(value) {
  // Weight keeps its decimal precision (e.g. 0.25). Trim trailing zeros for a clean
  // read-back match (0.250 -> 0.25, 1.0 -> 1).
  const num = Number(value);
  if (!Number.isFinite(num)) return '';
  return String(num);
}

function formatPackageCm(value) {
  // 包裹尺寸 inputs are integer-only ("长(整数)"). Round to nearest integer.
  const num = Number(value);
  if (!Number.isFinite(num)) return '';
  return String(Math.round(num));
}

// Fill the product-level 物流信息 card (重量 + 包裹尺寸) from transaction_info. Only
// used together with disableSkuWeight (which moves these off the per-SKU grid). The
// values originate from the store template via the listing copy.
async function fillLogisticsInfo(frame, task) {
  const info = (task.sku_grid || {}).transaction_information || (task.sku_grid || {}).transaction_info || {};
  if (!logisticsValuesComplete(info)) {
    const missing = LOGISTICS_NUMERIC_FIELDS.filter((key) => {
      const raw = info[key];
      return raw === undefined || raw === null || String(raw).trim() === '' || !(Number(raw) > 0);
    });
    throw new BlockedError('logistics_info_incomplete', { missing });
  }
  const card = frame.locator('.el-card:visible').filter({ hasText: TEXT.logisticsInfo });
  if ((await card.count()) !== 1) throw new BlockedError('logistics_card_not_unique', { count: await card.count() });

  // 重量: the single text input in the 重量 form-item of this card.
  const weightItem = card.locator('.el-form-item').filter({ hasText: '重量' }).first();
  const weightInput = weightItem.locator('input.el-input__inner').first();
  const weightValue = formatWeightKg(info.weight_kg);
  await weightInput.fill(weightValue);
  await weightInput.dispatchEvent('input').catch(() => {});
  await weightInput.dispatchEvent('change').catch(() => {});

  // 包裹尺寸: three integer inputs matched by placeholder (长/宽/高).
  const sizeItem = card.locator('.el-form-item').filter({ hasText: '包裹尺寸' }).first();
  const dims = [
    { placeholder: '长', value: formatPackageCm(info.package_length_cm) },
    { placeholder: '宽', value: formatPackageCm(info.package_width_cm) },
    { placeholder: '高', value: formatPackageCm(info.package_height_cm) },
  ];
  for (const dim of dims) {
    const input = sizeItem.locator(`input[placeholder^="${dim.placeholder}"]`).first();
    if ((await input.count()) !== 1) throw new BlockedError('logistics_dimension_input_not_found', { placeholder: dim.placeholder });
    await input.fill(dim.value);
    await input.dispatchEvent('input').catch(() => {});
    await input.dispatchEvent('change').catch(() => {});
  }

  // Verify by reading values back — dims may be normalized by the field, so compare
  // numerically for those and exact for weight.
  const actualWeight = cleanText(await weightInput.inputValue().catch(() => ''));
  if (actualWeight !== weightValue) {
    throw new BlockedError('logistics_weight_not_verified', { expected: weightValue, actual: actualWeight });
  }
  for (const dim of dims) {
    const input = sizeItem.locator(`input[placeholder^="${dim.placeholder}"]`).first();
    const actual = cleanText(await input.inputValue().catch(() => ''));
    if (Number(actual) !== Number(dim.value)) {
      throw new BlockedError('logistics_dimension_not_verified', { placeholder: dim.placeholder, expected: dim.value, actual });
    }
  }
  emit('stage', { stage: 'fill_logistics_info', ok: true, weight_kg: weightValue });
  return { status: 'ok', weight_kg: weightValue, package_cm: dims.map((d) => d.value) };
}

// Module-level twins of the fillSkuGrid page-context helpers, kept for unit tests
// and the module export. The copies inside frame.evaluate are what run in the browser.
function setGridValue(row, columns, names, value) {
  const name = names.find((candidate) => columns.has(candidate) || Object.prototype.hasOwnProperty.call(row, candidate)) || names[0];
  row[name] = value;
}

function applyTransactionInformation(row, columns, values) {
  // Only `stock` is required per row. Weight/package dims live in the 物流信息 card
  // once "分sku设置件重尺" is unchecked (their grid columns are then gone), so we only
  // write them when the column still exists and never fail the row over their absence.
  const present = (key) => values != null && values[key] !== undefined && values[key] !== null && values[key] !== '';
  if (!present('stock')) {
    throw new Error('transaction_information_incomplete');
  }
  setGridValue(row, columns, ['stock', 'quantity', 'availableStock'], values.stock);
  const setIfColumn = (names, key) => {
    if (!present(key)) return;
    if (names.some((name) => columns.has(name))) setGridValue(row, columns, names, values[key]);
  };
  setIfColumn(['weight'], 'weight_kg');
  setIfColumn(['packageLength', 'length'], 'package_length_cm');
  setIfColumn(['packageWidth', 'width'], 'package_width_cm');
  setIfColumn(['packageHeight', 'height'], 'package_height_cm');
}

async function selectDiscountActivity(frame, task) {
  const discount = task.discount || {};
  const table = frame.locator('.tradeInfoTableRef-table.vxe-table');
  if ((await table.count()) !== 1) {
    throw new BlockedError('trade_info_table_not_unique', { count: await table.count() });
  }
  const headers = table.locator('.vxe-header--column').filter({ hasText: TEXT.discountActivity });
  if ((await headers.count()) !== 1) {
    throw new BlockedError('discount_header_not_unique', { count: await headers.count() });
  }
  const header = headers.first();
  const headerText = cleanText(await header.innerText());
  if (discount.label && headerText.includes(cleanText(discount.label))) {
    emit('stage', { stage: 'select_discount_activity', ok: true, method: 'already_selected' });
    return { method: 'already_selected' };
  }
  const targetId = String(discount.mabang_discount_id || '');
  const storeId = String((task.store || {}).mabang_store_id || '');
  if (!targetId || !storeId) throw new BlockedError('discount_identity_missing');

  let dialogs = frame.locator('.el-dialog:visible').filter({ hasText: TEXT.selectDiscountActivity });
  if (!(await dialogs.count())) {
    const actions = header.getByText(TEXT.selectActivity, { exact: true });
    if ((await actions.count()) !== 1) {
      throw new BlockedError('discount_select_action_not_unique', { count: await actions.count() });
    }
    await actions.first().click();
  }
  const dialogDeadline = Date.now() + 5000;
  while (Date.now() < dialogDeadline) {
    dialogs = frame.locator('.el-dialog:visible').filter({ hasText: TEXT.selectDiscountActivity });
    if (await dialogs.count()) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if ((await dialogs.count()) !== 1) throw new BlockedError('discount_dialog_not_visible');
  const dialog = dialogs.first();
  const discountTable = dialog.locator('.vxe-table:visible');
  if ((await discountTable.count()) !== 1) {
    throw new BlockedError('discount_table_not_unique', { count: await discountTable.count() });
  }

  const rowDeadline = Date.now() + 10000;
  let match = null;
  while (Date.now() < rowDeadline) {
    match = await discountTable.first().evaluate((element, expected) => {
      const rows = element.__vue__?.tableData || [];
      const index = rows.findIndex((row) => String(row.discount_id || '') === expected.discountId);
      if (index < 0) return null;
      const row = rows[index];
      return {
        index,
        discount_name: String(row.discount_name || ''),
        shop_id: String(row.shopId || row.shop_id || ''),
      };
    }, { discountId: targetId });
    if (match) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!match) throw new BlockedError('discount_activity_id_not_found');
  if (match.shop_id !== storeId) {
    throw new BlockedError('discount_activity_store_mismatch');
  }
  const row = discountTable.first().locator('.vxe-body--row').nth(match.index);
  const choose = row.getByText(TEXT.choose, { exact: true });
  if ((await choose.count()) !== 1) {
    throw new BlockedError('discount_choose_action_not_unique', { count: await choose.count() });
  }
  await choose.first().click();
  await dialog.waitFor({ state: 'hidden', timeout: 5000 });
  const expectedName = cleanText(match.discount_name || discount.label);
  const verifyDeadline = Date.now() + 5000;
  while (Date.now() < verifyDeadline) {
    if (cleanText(await header.innerText()).includes(expectedName)) {
      emit('stage', { stage: 'select_discount_activity', ok: true, method: 'discount_activity_selected_by_id' });
      return { method: 'discount_activity_selected_by_id', discount_name: expectedName };
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new BlockedError('discount_activity_not_verified');
}

async function uploadVariantThumbnails(frame, task) {
  const uploads = task.variant_thumbnail_uploads || {};
  const entries = Object.entries(uploads).filter(([, value]) => value && value.exists && value.upload_path);
  if (!entries.length) return { count: 0 };
  const card = await variantCard(frame);
  const block = card.locator('.custom-img .img-center').first();
  if (await block.count() !== 1) throw new BlockedError('variant_thumbnail_block_not_unique', { count: await block.count() });
  // The variant image area renders ONE .img-center block holding every color
  // group ("<color> 选择图片" + that group's upload icon) — there are no
  // per-color rows, so resolve each color's icon inside its label's group.
  const resolveGroupIndex = async (color) => {
    const index = await block.evaluate((element, wanted) => {
      const labels = [...element.querySelectorAll('*')].filter((el) => !el.childElementCount && el.textContent.trim() === wanted);
      for (const label of labels) {
        let up = label.parentElement;
        while (up && up.parentElement !== element) up = up.parentElement;
        if (up) return [...element.children].indexOf(up);
      }
      return -1;
    }, String(color));
    if (index < 0) throw new BlockedError('variant_thumbnail_row_not_unique', { color });
    return index;
  };
  const groupLoaded = async (groupIndex) => block.evaluate((element, index) => {
    const group = element.children[index];
    return !!group && [...group.querySelectorAll('img')]
      .some((image) => image.naturalWidth > 64 && image.naturalHeight > 64);
  }, groupIndex).catch(() => false);
  const uploadOne = async (color, upload) => {
    const groupIndex = await resolveGroupIndex(color);
    if (await groupLoaded(groupIndex)) return 'variant_thumbnail_already_uploaded';
    // Uploading REPLACES the group's 选择图片 icon with the preview image, so
    // popover ids and icons go stale — re-resolve from the block each attempt.
    const popoverId = await block.evaluate((element, index) => {
      const group = element.children[index];
      const iconNode = group && group.querySelector('.upload-icon.el-popover__reference[aria-describedby]');
      return iconNode ? iconNode.getAttribute('aria-describedby') : '';
    }, groupIndex);
    const inputs = frame.locator(`[id="${popoverId}"] input[type="file"]`);
    if (!popoverId || (await inputs.count()) !== 1) {
      throw new BlockedError('variant_thumbnail_input_not_found', { color });
    }
    await uploadFiles(inputs.first(), [upload.upload_path]);
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      if (await groupLoaded(groupIndex)) break;
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    if (!(await groupLoaded(groupIndex))) return null;
    // The preview renders from the local file before the COS upload finishes;
    // rapid successive uploads raced and dropped all but one preview. Let the
    // async upload settle, then confirm the preview is still there.
    await sleep(2000);
    return (await groupLoaded(groupIndex)) ? 'uploaded' : null;
  };
  const methods = {};
  let pending = entries.filter(([color]) => !methods[color]);
  for (let pass = 0; pass < 3 && pending.length; pass += 1) {
    const failed = [];
    for (const [color, upload] of pending) {
      const outcome = await uploadOne(color, upload);
      if (outcome) methods[color] = outcome;
      else failed.push([color, upload]);
    }
    pending = failed;
    if (pending.length) await sleep(1000);
  }
  if (pending.length) {
    throw new BlockedError('variant_thumbnail_not_verified', { colors: pending.map(([color]) => color) });
  }
  emit('stage', { stage: 'upload_variant_thumbnails', ok: true, count: entries.length });
  return { count: entries.length, methods };
}

async function validateBeforeSave(frame, task, stages) {
  const requiredStages = [
    'select_store',
    'select_category',
    'set_category_attributes',
    'fill_basic_info',
    'upload_product_images',
    'configure_variants',
    'disable_sku_weight',
    'fill_sku_grid',
    'fill_logistics_info',
    'upload_variant_thumbnails',
  ];
  if (String((task.store || {}).listing_mode || '') !== 'cnsc_global') requiredStages.push('select_discount_activity');
  else requiredStages.push('enable_cnsc_auto_publish', 'select_cnsc_target_sites', 'fill_cnsc_site_prices_and_discounts');
  const missingStages = requiredStages.filter((stage) => !(stage in stages));
  if (missingStages.length) throw new BlockedError('required_stage_missing', { stages: missingStages });
  const audit = await frame.evaluate(({ saveText, expectedRows, cnsc }) => {
    const normalize = (value) => String(value || '').replace(/\s+/g, '');
    const visible = (element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    const navMissing = [...document.querySelectorAll('body *')]
      .filter(visible)
      .map((element) => String(element.innerText || '').trim())
      .filter((text) => /^\d+\s*\u5fc5\u586b\u9879\u672a\u586b$/.test(text));
    const errors = [...document.querySelectorAll('.el-form-item__error,.is-error .el-form-item__label')]
      .filter(visible)
      .map((element) => String(element.innerText || '').trim())
      .filter(Boolean);
    const tables = [...document.querySelectorAll('.vxe-table')].map((element) => element.__vue__).filter(Boolean);
    const table = tables.find((vm) => (vm.tableColumn || []).some((column) => ['sku', 'sellerSku', 'variationSku'].includes(column.field)));
    const rows = table && table.tableData ? table.tableData : [];
    const cnscAudit = { missing_columns: [], value_mismatches: 0, selected_sites: 0, expected_sites: Number(cnsc.expected_sites || 0), auto_publish: false };
    if (cnsc.enabled && !table) cnscAudit.missing_columns = ['sku_table'];
    if (cnsc.enabled && table) {
      const columns = table.tableColumn || [];
      const fields = new Set(columns.map((column) => column.field));
      const strictFieldFor = (names, titleFragments) => {
        const matches = new Set(names.filter((name) => fields.has(name)));
        for (const column of columns) {
          const title = normalize(column.title || column.label || column.field);
          if (titleFragments.some((fragment) => title.includes(normalize(fragment))) && column.field) matches.add(column.field);
        }
        return matches.size === 1 ? [...matches][0] : '';
      };
      const cnscFields = {
        global_price: strictFieldFor(['globalPrice', 'global_product_price', 'globalProductPrice'], ['全球商品价格']),
        TH: strictFieldFor(['TH', 'TH_stock', 'THStock', 'thStock'], ['泰国仓库存']),
        CN_GZ: strictFieldFor(['CN_GZ', 'guangzhou', 'guangzhouStock', 'cnGzStock'], ['广州仓库存']),
        PH: strictFieldFor(['PH', 'PH_stock', 'PHStock', 'phStock'], ['菲律宾仓库存']),
      };
      cnscAudit.missing_columns = Object.entries(cnscFields).filter(([, field]) => !field).map(([name]) => name);
      const expectedBySku = new Map((cnsc.skus || []).map((sku) => [String(sku.sku_code || ''), sku]));
      const numericEqual = (left, right) => Number(left) === Number(right) && Number.isFinite(Number(left));
      if (!cnscAudit.missing_columns.length) {
        cnscAudit.value_mismatches = rows.filter((row) => {
          const sku = expectedBySku.get(String(row.sku || row.sellerSku || row.variationSku || '').trim());
          if (!sku) return true;
          const stocks = sku.warehouse_stocks || {};
          return !numericEqual(row[cnscFields.global_price], sku.global_price_cny)
            || !numericEqual(row[cnscFields.TH], stocks.TH)
            || !numericEqual(row[cnscFields.CN_GZ], stocks.CN_GZ)
            || !numericEqual(row[cnscFields.PH], stocks.PH);
        }).length;
      }
      const selectedText = String(document.querySelector('#shops-wap .shop-box')?.innerText || '');
      const selectedMatch = selectedText.match(/(\d+)/);
      cnscAudit.selected_sites = selectedMatch ? Number(selectedMatch[1]) : 0;
      cnscAudit.auto_publish = [...document.querySelectorAll('input[type="checkbox"]')].some((input) => {
        const text = String(input.closest('label')?.innerText || input.parentElement?.innerText || '');
        return text.includes('\u4e0a\u67b6\u540e\u81ea\u52a8\u53d1\u5e03\u5230\u7ad9\u70b9\u5e97\u94fa') && input.checked;
      });
    }
    const saveButtons = [...document.querySelectorAll('button,.el-button,input[type="button"],input[type="submit"]')]
      .filter(visible)
      .filter((element) => normalize(element.innerText || element.textContent || element.value) === normalize(saveText));
    // nav 报「N 必填项未填」时把缺的项名字抓出来：扫描标签带 * 且值为空的表单项
    // （下拉看选中文本，文本框看 value，多选看 tag 数），给操作员可执行的提示。
    const missingRequiredLabels = [...document.querySelectorAll('.el-form-item')]
      .filter(visible)
      .map((item) => {
        const labelEl = item.querySelector('.el-form-item__label');
        if (!labelEl) return null;
        const raw = String(labelEl.textContent || '').trim();
        if (!/^[*\uFF0A]/.test(raw)) return null;
        const selectInput = item.querySelector('.el-select .el-input__inner');
        const selectFilled = Boolean(selectInput && selectInput.value && selectInput.value !== '\u8bf7\u9009\u62e9');
        const textFilled = [...item.querySelectorAll('input:not([type="hidden"]), textarea')]
          .some((input) => !input.readOnly && String(input.value || '').trim() !== '');
        const tagCount = item.querySelectorAll('.el-select__tags .el-tag').length;
        if (selectFilled || textFilled || tagCount > 0) return null;
        // chip/自定义控件（变种属性等）不走 input/select，标成 custom 避免误报；
        // 只有 form 类（真下拉/文本框为空）才算硬缺失。
        const customControl = Boolean(item.querySelector('.vxe-table, [class*="chip"], [class*="natureBlock"], [class*="upload"]'))
          || ![...item.querySelectorAll('input, textarea, select')].length;
        return { label: raw.replace(/^[*\uFF0A]\s*/, ''), kind: customControl ? 'custom' : 'form' };
      })
      .filter(Boolean).slice(0, 20);
    const guard = window.__AI_LISTING_PUBLISH_GUARD__ || {};
    return {
      nav_missing: [...new Set(navMissing)].slice(0, 20),
      missing_required_labels: missingRequiredLabels.map((item) => item.label),
      missing_required_form_labels: missingRequiredLabels.filter((item) => item.kind === 'form').map((item) => item.label),
      form_errors: [...new Set(errors)].slice(0, 20),
      rows: rows.length,
      expected_rows: expectedRows,
      empty_skus: rows.filter((row) => !String(row.sku || row.sellerSku || row.variationSku || '').trim()).length,
      save_button_count: saveButtons.length,
      guard_installed: Boolean(window.__AI_LISTING_PUBLISH_GUARD_V2_INSTALLED__),
      blocked_publish_clicks: Number(guard.blockedClicks || 0),
      publish_clicked: Boolean(guard.publishClicked),
      cnsc: cnscAudit,
    };
  }, {
    saveText: TEXT.save,
    expectedRows: Number((task.sku_grid || {}).expected_rows || 0),
    cnsc: {
      enabled: String((task.store || {}).listing_mode || '') === 'cnsc_global',
      expected_sites: (task.store?.target_site_stores || []).filter((target) => String(target.site_code || '').trim().toUpperCase() !== 'SIP').length,
      skus: task.sku_grid?.skus || [],
    },
  });
  if (String((task.store || {}).listing_mode || '') === 'cnsc_global') {
    const snapshot = await readCNSCDomGridSnapshot(frame);
    if (snapshot.table_count !== 1) {
      audit.cnsc.missing_columns = ['sku_table'];
    } else {
      const readback = validateCNSCDomGridReadback(snapshot, task.sku_grid || {});
      audit.rows = snapshot.rows.length;
      if (readback.ok) {
        const plan = buildCNSCDomGridPlan(task.sku_grid || {}, snapshot.headers, snapshot.body_column_ids, snapshot.rows.length);
        audit.empty_skus = snapshot.rows.filter((row) => !String(row[plan.columns.sku] || '').trim()).length;
        audit.cnsc.missing_columns = [];
        audit.cnsc.value_mismatches = 0;
      } else if (readback.reason === 'cnsc_required_columns_missing') {
        audit.cnsc.missing_columns = readback.missing;
      } else if (readback.reason === 'cnsc_value_mismatch') {
        audit.cnsc.missing_columns = [];
        audit.cnsc.value_mismatches = readback.mismatches.length;
      } else {
        audit.cnsc.missing_columns = [readback.reason];
      }
    }
    const targetSites = await verifyCNSCTargetSites(frame, task);
    const selection = await frame.evaluate(() => {
      const autoPublish = [...document.querySelectorAll('input[type="checkbox"]')].some((input) => {
        const text = String(input.closest('label')?.innerText || input.parentElement?.innerText || '');
        return text.includes('\u4e0a\u67b6\u540e\u81ea\u52a8\u53d1\u5e03\u5230\u7ad9\u70b9\u5e97\u94fa') && input.checked;
      });
      return { auto_publish: autoPublish };
    });
    audit.cnsc.selected_sites = targetSites.selected.length;
    audit.cnsc.auto_publish = selection.auto_publish;
    const sitePlan = buildCNSCSitePricePlan(task);
    const siteTable = await findCNSCSitePriceTable(frame, sitePlan);
    const siteReadback = validateCNSCSitePriceReadback(
      await readCNSCSitePriceSnapshot(frame, siteTable, sitePlan),
      sitePlan,
      actionableCNSCTargets(task),
    );
    audit.cnsc.site_price_mismatches = siteReadback.mismatches || [];
  }
  if (audit.cnsc.missing_columns.length) throw new BlockedError('cnsc_required_columns_missing', audit);
  if (audit.cnsc.value_mismatches) throw new BlockedError('cnsc_value_mismatch', audit);
  if (audit.cnsc.site_price_mismatches?.length) throw new BlockedError('cnsc_site_price_mismatch', audit);
  if (audit.cnsc.expected_sites && (audit.cnsc.selected_sites !== audit.cnsc.expected_sites || !audit.cnsc.auto_publish)) {
    throw new BlockedError('cnsc_site_selection_not_verified', audit);
  }
  // nav 徽标疑似未刷新的豁免：现场 AI 已把页面必填项补齐、扫描又找不到任何
  // 真表单类的空必填项（只剩 chip 类自定义控件的误报）时，nav 的「N 必填项未填」
  // 很可能是页面加载时的旧计数。放行点保存，由马帮自己的校验做最终裁决；
  // 若被拒，form_errors / 马帮报错会带回真正的字段名（草稿安全：publish guard 仍拦发布）。
  const suspectStaleBadge = Boolean(
    audit.nav_missing.length
    && !audit.form_errors.length
    && !(audit.missing_required_form_labels || []).length
    && audit.rows === audit.expected_rows
    && !audit.empty_skus
  );
  if (suspectStaleBadge) {
    emit('stage', {
      stage: 'validate_before_save', ok: true, method: 'stale_badge_deferred_to_mabang',
      nav_missing: audit.nav_missing, missing_required_labels: audit.missing_required_labels,
    });
  }
  if (
    (audit.nav_missing.length && !suspectStaleBadge) ||
    audit.form_errors.length ||
    audit.rows !== audit.expected_rows ||
    audit.empty_skus ||
    audit.save_button_count !== 1 ||
    !audit.guard_installed ||
    audit.publish_clicked
  ) {
    throw new BlockedError('pre_save_validation_failed', audit);
  }
  emit('stage', { stage: 'validate_before_save', ok: true, rows: audit.rows });
  return audit;
}

// Payload guard mirroring storage.py's shared CNSC source of truth
// (``_normalize_cnsc_price_tiers`` + ``_cnsc_configuration_issues``). It must never
// be stricter than that validator: a draft the Python side reports ready must not
// be blocked here as ``cnsc_payload_invalid`` (a ``cnsc_payload_invalid`` on a
// ready copy is a bug in this guard, not a broken copy). Rules:
//   - sale_price is optional on tier site entries — it is derived from the target
//     site's %OFF (sale = original × (1 - off/100)) when blank, both in Python and
//     in buildCNSCSitePricePlan.
//   - 全球商品价格 (global_price_cny) is a REQUIRED per-SKU column on the real CNSC
//     page (verified 2026-09-04: blank → per-row "请输入价格" → pre_save_validation
//     fails and save never happens). Every tier — color-only ones included — must
//     therefore carry a positive global. Color-only tiers get it filled from the
//     base tier's cost_price at copy time (see storage._cnsc_copy_price_tiers), and
//     the SKU inherits that value.
function validateCNSCTaskPayload(task) {
  if (String((task.store || {}).listing_mode || '') !== 'cnsc_global') return { ok: true, errors: [] };
  const errors = [];
  const store = task.store || {};
  const cnscStore = store.cnsc_store || {};
  if (!String(cnscStore.id || cnscStore.name || '').trim()) errors.push('cnsc_store_missing');
  const targets = Array.isArray(store.target_site_stores) ? store.target_site_stores : [];
  if (!targets.length) errors.push('cnsc_target_sites_missing');
  if (targets.some((target) => !String(target.store_id || target.mabang_store_id || target.store_name || target.name || '').trim())) errors.push('cnsc_target_site_identity_missing');
  const defaultPercent = Number(store.default_site_discount_percent ?? task.cnsc?.default_site_discount_percent ?? 40);
  if (!Number.isFinite(defaultPercent) || defaultPercent <= 0 || defaultPercent >= 100) errors.push('cnsc_default_site_discount_percent_invalid');
  for (const target of targets) {
    const code = String(target.site_code || target.country || '').trim().toUpperCase();
    if (code === 'SIP') continue;
    if (!String(target.mabang_discount_id || '').trim()) errors.push(`cnsc_site_discount_activity_missing:${code}`);
    const percent = String(target.discount_percent ?? '').trim() ? Number(target.discount_percent) : defaultPercent;
    if (!Number.isFinite(percent) || percent <= 0 || percent >= 100) errors.push(`cnsc_site_discount_percent_invalid:${code}`);
  }
  const tiers = Array.isArray(task.cnsc?.price_tiers) ? task.cnsc.price_tiers : [];
  if (!tiers.length) errors.push('cnsc_price_tiers_missing');
  for (const tier of tiers) {
    // global_price_cny is required on EVERY tier: the real CNSC page's
    // *全球商品价格 SKU column is mandatory and the SKU inherits the tier value.
    const globalPrice = Number(tier.global_price_cny);
    if (!Number.isFinite(globalPrice) || globalPrice <= 0) errors.push(`cnsc_global_price_invalid:${tier.id || ''}`);
    const matrix = tier.site_prices || {};
    for (const target of targets) {
      const code = String(target.site_code || target.country || '').trim().toUpperCase();
      if (!code || code === 'SIP') continue;
      const prices = matrix[code] || {};
      const sale = String(prices.sale_price ?? '').trim() ? Number(prices.sale_price) : '';
      const original = Number(prices.original_price);
      // original is required; sale is optional (derived from the target %OFF when
      // blank) and, when present, must be positive and not exceed original.
      if (!Number.isFinite(original) || original <= 0 || (sale !== '' && (!Number.isFinite(sale) || sale <= 0 || sale > original))) {
        errors.push(`cnsc_site_price_missing:${tier.id || ''}:${code}`);
      }
    }
  }
  const skus = Array.isArray(task.sku_grid?.skus) ? task.sku_grid.skus : [];
  if (skus.some((sku) => !Number.isFinite(Number(sku.global_price_cny)) || Number(sku.global_price_cny) <= 0)) {
    errors.push('cnsc_sku_global_price_invalid');
  }
  for (const sku of skus) {
    for (const code of ['TH', 'CN_GZ', 'PH']) {
      // 缺省不拦：执行器按 副本默认→0 逐仓回退填进必填列（0 合法，马帮留空才报错）。
      // 显式给了值但不是非负数才算脏数据，保留 fail-closed。
      const raw = (sku.warehouse_stocks || {})[code];
      if (raw === undefined || raw === null || raw === '') continue;
      const value = Number(raw);
      if (!Number.isFinite(value) || value < 0) errors.push(`cnsc_warehouse_stock_invalid:${code}`);
    }
  }
  return { ok: errors.length === 0, errors };
}

// 马帮的保存前置校验只有点了「保存」才跑，被拒时不落草稿、也不写字段行内错误，
// 只在页面顶部弹一条提示。执行器原先完全没读这些提示，15 秒证据窗口耗尽后只能报
// save_result_not_proven（界面「无法确认草稿已保存」），运营看不出真正原因。
// 这里把已知提示归成稳定错误码，原始提示原文一并带进日志（details.page_message）。
const SAVE_REJECTION_SELECTORS = [
  '.el-message',
  '.el-message-box__message',
  '.el-notification',
  '.el-dialog__body',
  '.el-form-item__error',
].join(',');

// 页面正文里出现这些片段就当候选行抓回来（具体归哪个错误码由 Node 侧的规则决定）。
// 这样即使马帮把提示换成非 Element UI 的浮层，也能从 innerText 里读到。
const SAVE_REJECTION_HINTS = Object.freeze([
  '\u56fe\u7247\u5bbd\u5ea6\u4e0d\u80fd\u5c0f\u4e8e',
  '\u7f16\u8f91\u56fe\u7247\u6309\u94ae\u8fdb\u884c\u4fee\u6539',
]);

const SAVE_REJECTION_RULES = Object.freeze([
  {
    code: 'description_image_width_too_small',
    pattern: /商品描述中的图片宽度不能小于|图片宽度不能小于\s*700/,
  },
]);

function classifySaveRejection(texts) {
  const candidates = (Array.isArray(texts) ? texts : [])
    .map((text) => cleanText(text))
    .filter(Boolean);
  for (const rule of SAVE_REJECTION_RULES) {
    const hit = candidates.find((text) => rule.pattern.test(text));
    if (hit) return { code: rule.code, message: hit };
  }
  return null;
}

// 读当前 frame 里可见的提示原文：先看 toast / 消息框 / 表单行内错误节点，
// 再按已知片段扫一遍正文（正文里看不见的隐藏文案不会进 innerText）。
async function readSaveRejectionTexts(frame) {
  return frame.evaluate(([selector, hints]) => {
    const visible = (node) => {
      if (!node) return false;
      const style = window.getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
      const rect = node.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    };
    const texts = [...document.querySelectorAll(selector)]
      .filter(visible)
      .map((node) => String(node.innerText || node.textContent || '').trim())
      .filter(Boolean);
    const bodyText = String((document.body && document.body.innerText) || '');
    for (const line of bodyText.split(/\n+/)) {
      const trimmed = line.trim();
      if (trimmed && trimmed.length <= 300 && hints.some((hint) => trimmed.includes(hint))) {
        texts.push(trimmed);
      }
    }
    return [...new Set(texts)].slice(0, 20);
  }, [SAVE_REJECTION_SELECTORS, SAVE_REJECTION_HINTS]).catch(() => []);
}

// 提示也可能挂在父页面而不是详情 iframe 上：详情 frame 没读到就顺带扫一遍其它 frame。
async function readSaveRejectionTextsAcrossFrames(page, detailFrame) {
  const own = await readSaveRejectionTexts(detailFrame);
  if (classifySaveRejection(own)) return own;
  const frames = page && typeof page.frames === 'function' ? page.frames() : [];
  for (const candidate of frames) {
    if (candidate === detailFrame) continue;
    if (typeof candidate.isDetached === 'function' && candidate.isDetached()) continue;
    const extra = await readSaveRejectionTexts(candidate);
    if (classifySaveRejection(extra)) return extra;
  }
  return own;
}

function saveRejectionError(rejection, evidence, saveResult) {
  emit('stage', { stage: 'save_draft_rejected', ok: false, code: rejection.code, message: rejection.message });
  return new BlockedError(rejection.code, {
    save_clicked: true,
    publish_clicked: false,
    page_message: rejection.message,
    success_message: Boolean(evidence && evidence.success_message),
    product_id: Boolean(evidence && evidence.product_id),
    save_response: Boolean(saveResult && (saveResult.network || []).length),
  });
}

// 保存请求的响应监听。响应回来的时间不受控：早先这里点完固定等 1.8s 就把监听摘掉，
// 稍慢的一次响应就永远记不到（现场 HN0012：草稿其实存下来了、页面上也有保存成功
// 提示，但网络那条证据是空的 → 只剩一条证据 → 报 save_result_not_proven，任务停在
// 待确认）。所以监听要活到保存校验结束，由 verifySavedDraft 收尾时 stop()。
function watchSaveResponses(page) {
  const network = [];
  const onResponse = (response) => {
    const request = response.request();
    if (!['POST', 'PUT', 'PATCH'].includes(request.method()) || !response.ok()) return;
    try {
      const url = new URL(response.url());
      if (/product|listing|publish|save|update/i.test(url.pathname)) network.push({ path: url.pathname, status: response.status() });
    } catch {}
  };
  page.on('response', onResponse);
  return {
    network,
    stop: () => { try { page.off('response', onResponse); } catch {} },
  };
}

async function clickSaveDraftOnce(page, frame, task) {
  const safety = task.safety || {};
  if (!safety.save_token || task.__saveTokenUsed) throw new BlockedError('save_token_invalid_or_used');
  const watcher = watchSaveResponses(page);
  task.__saveTokenUsed = true;
  safety.save_token = '';
  try {
    const button = await exactVisibleLocator(frame, 'button,.el-button,input[type="button"],input[type="submit"]', TEXT.save);
    // A category-dialog loading mask left visible after the dialog closed has been
    // observed intercepting the save click (WinError-style pointer-events block).
    // Clear it before clicking so the draft save is not misreported as a timeout.
    await waitForNoLoadingMask(frame, 15000);
    await button.click();
    await new Promise((resolve) => setTimeout(resolve, 1800));
  } catch (error) {
    // 保存根本没点成：监听当场摘掉，页面上不留悬挂的 response 处理器。
    watcher.stop();
    throw error;
  }
  emit('stage', { stage: 'save_draft', ok: true });
  // network 是活数组：校验期间到达的响应照样算证据。
  return { save_clicked: true, publish_clicked: false, network: watcher.network, stopWatchingSave: watcher.stop };
}

async function findSavedSuccessDialog(page, detailFrame) {
  const frames = [detailFrame, ...page.frames().filter((candidate) => candidate !== detailFrame)];
  const matches = [];
  for (const candidateFrame of frames) {
    if (candidateFrame.isDetached()) continue;
    const closeActions = await exactVisibleLocators(candidateFrame, ACTION_SELECTOR, TEXT.closeThisPage);
    for (const action of closeActions) {
      const dialog = action.locator(
        `xpath=ancestor::*[not(self::html or self::body) and contains(string(.), '${TEXT.savedSuccess}')][1]`,
      );
      if ((await dialog.count().catch(() => 0)) !== 1 || !(await isVisible(dialog))) continue;
      const visibleSuccessMessage = compactText(await dialog.innerText().catch(() => ''))
        .includes(compactText(TEXT.savedSuccess));
      if (visibleSuccessMessage) matches.push(dialog);
    }
  }
  if (matches.length !== 1) {
    throw new BlockedError('saved_success_dialog_not_unique', { count: matches.length });
  }
  return matches[0];
}

async function savedDetailPageIsClosed(page) {
  if (page.isClosed()) return true;
  let detailFrame;
  try {
    detailFrame = await detailFrameFromPage(page);
  } catch {
    return false;
  }
  if (detailFrame) return false;
  for (const frame of page.frames()) {
    if (frame.isDetached()) continue;
    const matches = frame.getByText(TEXT.listingDraftBox, { exact: true });
    for (let index = 0, count = await matches.count().catch(() => 0); index < count; index += 1) {
      if (await isVisible(matches.nth(index))) return true;
    }
  }
  return false;
}

async function waitForSavedDetailPageToClose(page, options = {}) {
  const timeoutMs = Number(options.timeoutMs || 8000);
  const intervalMs = Number.isFinite(options.intervalMs) ? Number(options.intervalMs) : 200;
  const stableRequired = Number(options.stableRequired || 3);
  const closureProbe = options.closureProbe || savedDetailPageIsClosed;
  const deadline = Date.now() + timeoutMs;
  let stableClosureMatches = 0;
  while (Date.now() < deadline) {
    if (page.isClosed()) return true;
    if (await closureProbe(page)) {
      stableClosureMatches += 1;
      if (stableClosureMatches >= stableRequired) return true;
    } else {
      stableClosureMatches = 0;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  return page.isClosed();
}

async function closeSavedDetailPage(page, frame) {
  let dialog;
  try {
    dialog = await findSavedSuccessDialog(page, frame);
  } catch (error) {
    if (error instanceof BlockedError) {
      error.details = {
        ...error.details,
        save_clicked: true,
        publish_clicked: false,
        page_closed: false,
      };
      throw error;
    }
    throw new BlockedError('saved_success_dialog_not_found', {
      save_clicked: true,
      publish_clicked: false,
      page_closed: false,
    });
  }
  let dialogError = '';
  try {
    const closeButton = await exactVisibleLocator(dialog, ACTION_SELECTOR, TEXT.closeThisPage);
    await closeButton.click();
    if (await waitForSavedDetailPageToClose(page)) {
      emit('stage', { stage: 'close_saved_detail_page', ok: true, close_method: 'success_dialog' });
      return { page_closed: true, close_method: 'success_dialog' };
    }
    dialogError = 'detail_page_remained_open';
  } catch (error) {
    dialogError = cleanText(error && (error.reason || error.message));
  }

  if (!page.isClosed()) {
    await page.close({ runBeforeUnload: false }).catch(() => null);
  }
  if (!page.isClosed()) {
    throw new BlockedError('saved_detail_page_not_closed', {
      save_clicked: true,
      publish_clicked: false,
      page_closed: false,
      dialog_error: dialogError,
    });
  }
  emit('stage', { stage: 'close_saved_detail_page', ok: true, close_method: 'playwright_fallback' });
  return { page_closed: true, close_method: 'playwright_fallback' };
}

function matchCNSCDraftRows(parentSku, rowTexts) {
  const parent = cleanText(parentSku);
  const escaped = parent.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const exactToken = new RegExp(`(^|[^A-Za-z0-9_-])${escaped}($|[^A-Za-z0-9_-])`);
  const rows = (rowTexts || []).map((row) => cleanText(row)).filter((row) => row && parent && exactToken.test(row));
  return { parent_sku: parent, match_count: rows.length, rows };
}

async function openCNSCDraftInbox(browser, task) {
  if (!browser) throw new BlockedError('cnsc_draft_inbox_browser_missing');
  let page = allPages(browser).find((candidate) => /mabangerp\.com/i.test(candidate.url()));
  if (!page) page = await requireBrowserContext(browser).newPage();
  if (!/mabangerp\.com/i.test(page.url())) {
    await page.goto(task.source_url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  }
  if (await hasLoginOrVerification(page)) throw new BlockedError('needs_manual_login_or_verification');
  const pendingFrame = await findFrameWithExactText(page, TEXT.pending);
  if (!(await activateTab(pendingFrame, TEXT.pending))) await clickExact(pendingFrame, TEXT.pending);
  const cnscTab = pendingFrame.getByRole('tab', { name: TEXT.cnscPending, exact: true });
  if (await cnscTab.count() !== 1) throw new BlockedError('cnsc_draft_inbox_tab_not_found');
  if (!(await cnscTab.evaluate((node) => node.classList.contains('is-active')).catch(() => false))) {
    await cnscTab.click();
  }
  await sleep(800);
  return { page, frame: pendingFrame };
}

// Pre-save duplicate guard: the post-save inbox check fail-closes on ambiguity,
// so saving while a same-parent-SKU draft already exists would always end blocked
// AND leave yet another duplicate behind. Fail closed before clicking save.
async function assertCNSCDraftInboxCleanForParentSku(browser, task) {
  const parentSku = String((task.basic_info || {}).parent_sku || '').trim();
  if (!parentSku) throw new BlockedError('cnsc_parent_sku_missing');
  const inbox = await openCNSCDraftInbox(browser, task);
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    const rows = await inbox.frame.evaluate(() => {
      const seen = new Set();
      return [...document.querySelectorAll('.vxe-body--row, .el-table__body-wrapper tr, table tbody > tr')]
        .filter((row) => {
          if (seen.has(row)) return false;
          seen.add(row);
          const style = getComputedStyle(row);
          return style.display !== 'none' && style.visibility !== 'hidden';
        })
        .map((row) => String(row.innerText || '').trim())
        .filter(Boolean);
    }).catch(() => []);
    const matched = matchCNSCDraftRows(parentSku, rows);
    if (matched.match_count > 0) {
      emit('stage', { stage: 'pre_save_duplicate_check', ok: false, parent_sku: parentSku, match_count: matched.match_count });
      throw new BlockedError('cnsc_draft_inbox_parent_sku_exists', matched);
    }
    if (rows.length) {
      emit('stage', { stage: 'pre_save_duplicate_check', ok: true, parent_sku: parentSku });
      return { clean: true };
    }
    await sleep(500);
  }
  // Inconclusive (list never rendered rows) — do not block the save on it.
  emit('stage', { stage: 'pre_save_duplicate_check', ok: true, inconclusive: true });
  return { clean: true, inconclusive: true };
}

async function verifyCNSCDraftInbox(browser, task) {
  const parentSku = String((task.basic_info || {}).parent_sku || '').trim();
  if (!parentSku) throw new BlockedError('cnsc_parent_sku_missing');
  const inbox = await openCNSCDraftInbox(browser, task);
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    const rows = await inbox.frame.evaluate(() => {
      const seen = new Set();
      return [...document.querySelectorAll('.vxe-body--row, .el-table__body-wrapper tr, table tbody > tr')]
        .filter((row) => {
          if (seen.has(row)) return false;
          seen.add(row);
          const style = getComputedStyle(row);
          return style.display !== 'none' && style.visibility !== 'hidden';
        })
        .map((row) => String(row.innerText || '').trim())
        .filter(Boolean);
    }).catch(() => []);
    const matched = matchCNSCDraftRows(parentSku, rows);
    if (matched.match_count === 1) {
      emit('stage', { stage: 'verify_cnsc_draft_inbox', ok: true, parent_sku: parentSku });
      return { ...matched, row: matched.rows[0] };
    }
    if (matched.match_count > 1) {
      throw new BlockedError('cnsc_draft_inbox_parent_sku_not_unique', matched);
    }
    await sleep(500);
  }
  throw new BlockedError('cnsc_draft_inbox_parent_sku_not_found', { parent_sku: parentSku });
}

// 保存校验（成功或失败）结束后必须摘掉 clickSaveDraftOnce 注册的响应监听——包一层
// 而不是在函数里 try/finally 重排缩进，逻辑本体保持原样。
async function verifySavedDraft(page, frame, saveResult, task, stages = {}, browser = null) {
  try {
    return await verifySavedDraftEvidence(page, frame, saveResult, task, stages, browser);
  } finally {
    if (typeof saveResult.stopWatchingSave === 'function') saveResult.stopWatchingSave();
  }
}

async function verifySavedDraftEvidence(page, frame, saveResult, task, stages = {}, browser = null) {
  const deadline = Date.now() + 15000;
  let evidence = {};
  while (Date.now() < deadline) {
    evidence = await frame.evaluate(() => {
      const text = String(document.body && document.body.innerText || '');
      const success = ['\u4fdd\u5b58\u6210\u529f', '\u64cd\u4f5c\u6210\u529f', '\u8349\u7a3f\u4fdd\u5b58\u6210\u529f'].find((label) => text.includes(label)) || '';
      const productId = /(?:productId|product_id|id)=[0-9]+/.test(location.href) || Boolean(document.querySelector('[data-product-id]'));
      const guard = window.__AI_LISTING_PUBLISH_GUARD__ || {};
      return { success_message: Boolean(success), product_id: productId, publish_clicked: Boolean(guard.publishClicked) };
    }).catch(() => ({ success_message: false, product_id: false, publish_clicked: false }));
    if (evidence.publish_clicked) {
      throw new BlockedError('publish_guard_triggered_after_save');
    }
    // 马帮拒绝保存的提示优先于证据计数：只要页面上出现了「描述图片宽度 < 700」这类
    // 拒绝文案，这次保存就没落草稿，不该再等出 save_result_not_proven。
    const rejection = classifySaveRejection(await readSaveRejectionTexts(frame));
    if (rejection) throw saveRejectionError(rejection, evidence, saveResult);
    const networkResponse = (saveResult.network || []).length > 0;
    const evidenceCount = [evidence.success_message, evidence.product_id, networkResponse].filter(Boolean).length;
    if (evidenceCount >= 2) {
      emit('stage', { stage: 'verify_saved_draft', ok: true, evidence_count: evidenceCount });
      const closure = await closeSavedDetailPage(page, frame);
      let cnscDraft = null;
      if (String((task.store || {}).listing_mode || '') === 'cnsc_global') {
        try {
          cnscDraft = await verifyCNSCDraftInbox(browser, task);
        } catch (error) {
          if (error instanceof BlockedError) {
            error.details = {
              ...error.details,
              save_clicked: true,
              publish_clicked: false,
              page_closed: true,
            };
          }
          throw error;
        }
      }
      const result = {
        ok: true,
        status: 'saved',
        save_clicked: true,
        publish_clicked: false,
        page_closed: true,
        close_method: closure.close_method,
        evidence: {
          success_message: evidence.success_message,
          product_id: evidence.product_id,
          save_response: networkResponse,
          parent_sku: (task.basic_info || {}).parent_sku,
          cnsc_draft_inbox: cnscDraft,
        },
        stages,
      };
      return result;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  // 收尾再扫一遍（含父页面等其它 frame）：提示通常 3 秒就自动消失，轮询没撞上时
  // 这一次扫描仍可能读到还没散的那条。
  const lateTexts = await readSaveRejectionTextsAcrossFrames(page, frame);
  const lateRejection = classifySaveRejection(lateTexts);
  if (lateRejection) throw saveRejectionError(lateRejection, evidence, saveResult);
  throw new BlockedError('save_result_not_proven', {
    save_clicked: true,
    publish_clicked: false,
    success_message: Boolean(evidence.success_message),
    product_id: Boolean(evidence.product_id),
    save_response: (saveResult.network || []).length > 0,
    // 认不出来的页面提示也留在详情里：下次马帮换了文案，操作员不用再靠截图猜。
    page_messages: (Array.isArray(lateTexts) ? lateTexts : [])
      .map((text) => cleanText(text).slice(0, 200))
      .filter(Boolean)
      .slice(0, 5),
  });
}

// Ordered list of pre-save stages. Each entry is [stageKey, runner(detail, task, stages)].
// Runner returns the stage record to store under stages[stageKey]. The order matches
// the original run() body; runOptionalMediaStages returns an object that is merged in.
const PRE_SAVE_STAGES = [
  ['select_store', async (d, task) => selectStore(d.frame, task)],
  ['enable_cnsc_auto_publish', async (d, task) => enableCNSCAutoPublish(d.frame, task)],
  ['select_cnsc_target_sites', async (d, task) => selectCNSCTargetSites(d.frame, task)],
  ['select_category', async (d, task) => selectCategory(d.frame, task)],
  ['set_category_attributes', async (d, task) => setCategoryAttributes(d.frame, task, d.page)],
  ['fill_basic_info', async (d, task) => fillBasicInfo(d.frame, task)],
  ['upload_product_images', async (d, task) => uploadProductImages(d.frame, task)],
  // 描述末尾插图要等产品图上传完（引用产品图弹窗列的就是这些已上传的图），
  // 所以紧跟在上传阶段之后，而不是跟着 fill_basic_info 填描述文字时做。
  ['append_description_media', async (d, task) => appendDescriptionMedia(d.frame, task)],
  ['__optional_media__', async (d, task) => runOptionalMediaStages(d.frame, task, { page: d.page })],
  ['configure_variants', async (d, task) => configureVariants(d.frame, task)],
  // Uncheck 分sku设置件重尺 BEFORE filling the grid so the weight/package columns are
  // gone, then fill those product-level values in the 物流信息 card instead.
  ['disable_sku_weight', async (d) => disableSkuWeight(d.frame)],
  ['fill_sku_grid', async (d, task) => fillSkuGrid(d.frame, task)],
  ['fill_cnsc_site_prices_and_discounts', async (d, task) => fillCNSCSitePricesAndDiscounts(d.frame, task)],
  ['fill_logistics_info', async (d, task) => fillLogisticsInfo(d.frame, task)],
  ['select_discount_activity', async (d, task) => task.store?.listing_mode === 'cnsc_global' ? { skipped: true } : selectDiscountActivity(d.frame, task)],
  ['upload_variant_thumbnails', async (d, task) => uploadVariantThumbnails(d.frame, task)],
  // 马帮保存闸：描述图片宽度 < 700 会被拒。描述里的图都在 append_description_media
  // 之后才齐，所以这一步放在保存前、校验前，用马帮自己的「编辑图片」把宽度修成 1000，
  // 免得点了一次保存被拒（save 按设计只点一次，不会重试）。
  ['fix_description_image_widths', async (d) => ensureDescriptionImageWidths(d.frame)],
  ['validate_before_save', async (d, task, stages) => validateBeforeSave(d.frame, task, stages)],
];

function resolvePreSaveStartIndex(startStage) {
  if (!startStage) return 0;
  const index = PRE_SAVE_STAGES.findIndex(([stageKey]) => stageKey === startStage);
  if (index < 0) {
    const error = new BlockedError('resume_stage_unknown', { start_stage: String(startStage) });
    error.failedStage = 'resume_stage';
    throw error;
  }
  return index;
}

// Run one pre-save fill attempt end-to-end. Returns { detail, stages }; throws with
// { failedStage } attached on any BlockedError / Playwright error so the caller can
// decide whether to retry or bail.
async function runPreSaveAttempt(browser, task, attempt, options = {}) {
  const cnscValidation = validateCNSCTaskPayload(task);
  if (!cnscValidation.ok) throw new BlockedError('cnsc_payload_invalid', { errors: cnscValidation.errors });
  const stages = {};
  await installPublishGuard(browser);
  const detail = options.existingDetail ? await waitForDetailFrame(browser) : await openListingWorkspace(browser, task);
  await installPublishGuard(browser);
  let failedStage = 'open_workspace';
  try {
    const startIndex = resolvePreSaveStartIndex(options.startStage);
    for (const [stageKey, runner] of PRE_SAVE_STAGES.slice(startIndex)) {
      failedStage = stageKey === '__optional_media__' ? 'optional_media' : stageKey;
      // 每个阶段记一条耗时：操作员看到的"卡住"到底是哪一步在等（选店铺/选类目/属性/
      // 上传图片…），下一次卡住就能直接从 tmp_upload/auto-fill-debug.log 读出来。
      const stageStartedAt = Date.now();
      try {
        const outcome = await runner(detail, task, stages);
        attrPickDebug({
          phase: 'stage_timing', stage: failedStage, attempt,
          ms: Date.now() - stageStartedAt, ok: true,
        });
        if (stageKey === '__optional_media__') {
          Object.assign(stages, outcome);
        } else {
          stages[stageKey] = outcome;
        }
      } catch (error) {
        attrPickDebug({
          phase: 'stage_timing', stage: failedStage, attempt,
          ms: Date.now() - stageStartedAt, ok: false,
          error: cleanText(error && (error.reason || error.message)).slice(0, 120),
        });
        throw error;
      }
    }
  } catch (error) {
    error.failedStage = error.failedStage || failedStage;
    error.attempt = attempt;
    throw error;
  }
  return { detail, stages };
}

// Close every open detail form page so the next attempt has to re-open the form
// via "待发布商品 → 新增刊登". Best-effort: closing is allowed to fail (page may already
// be detached).
async function closeAllDetailPages(browser) {
  for (const page of allPages(browser)) {
    try {
      // 表单失败时详情页可能正处于重渲染状态，文案探测会暂时失败；
      // productDetailShopee URL 是稳定边界，作为关闭重试页的兜底，避免
      // 下一次 attempt 复用残留的店铺/下拉状态。
      const hasDetailUrl = page.frames().some((frame) => /productDetailShopee/i.test(frame.url()));
      const detail = hasDetailUrl ? true : await detailFrameFromPage(page);
      if (detail) await page.close({ runBeforeUnload: false });
    } catch {}
  }
}

async function handleFailedPreSaveAttempt(browser, error, attempts, willRetry, options = {}) {
  const closeDetails = options.closeDetails || closeAllDetailPages;
  const wait = options.wait || sleep;
  if (!(options.preserveDetails && !willRetry)) await closeDetails(browser);
  if (!willRetry) {
    error.attempts = attempts;
    throw error;
  }
  await wait(600);
}

function classifyError(error) {
  if (error instanceof BlockedError) return error.reason;
  const message = String((error && error.message) || '');
  if (/Timeout \d+ms exceeded/i.test(message)) return 'playwright_timeout';
  return 'unknown_error';
}

// 只做收尾：把残留的详情页关掉（页面上还留着「刊登信息保存成功」弹窗的也一并关掉）。
// 背景：worker 的 executor 超时会把 node 进程直接杀掉，那次运行的 close_saved_detail_page
// 就没跑成，浏览器里会留着成功弹窗和详情页（现场 2026-09-27 22:21，任务 45fff5a5）。
// 超时后由 runner 用 --close-detail-pages 再调一次这里补收尾。
async function closeLeftoverDetailPages(browser) {
  const outcome = { closed: 0, saved_dialog_pages: 0, kept: [] };
  for (const page of allPages(browser)) {
    let detailFrame = page.frames().find((frame) => /productDetailShopee/i.test(frame.url())) || null;
    if (!detailFrame) {
      const fallback = await detailFrameFromPage(page).catch(() => null);
      if (!fallback) continue;
      detailFrame = fallback;
    }
    const bodyText = cleanText(await detailFrame.locator('body').innerText({ timeout: 2000 }).catch(() => ''));
    if (bodyText.includes(TEXT.savedSuccess)) outcome.saved_dialog_pages += 1;
    await page.close({ runBeforeUnload: false }).catch(() => {});
    if (page.isClosed()) outcome.closed += 1;
    else outcome.kept.push(cleanText(page.url()).slice(0, 80));
  }
  emit('stage', { stage: 'close_leftover_detail_pages', ...outcome });
  return outcome;
}

async function run() {
  const args = parseArgs(process.argv.slice(2));
  const browser = await chromium.connectOverCDP(args.cdpUrl, { timeout: CDP_CONNECT_TIMEOUT_MS });

  if (args.closeDetailPages) {
    // 收尾模式：不需要任务计划（也就不读 stdin），连上浏览器清场即返回。
    const outcome = await closeLeftoverDetailPages(browser);
    return { ok: true, status: 'closed_detail_pages', ...outcome };
  }

  const task = await readTaskPlan();

  const attempts = [];
  let detail = null;
  let stages = null;
  let lastError = null;

  if (args.resumeFromStage) {
    const startedAt = Date.now();
    const attempts = [];
    try {
      const result = await runPreSaveAttempt(browser, task, 1, {
        existingDetail: true,
        startStage: args.resumeFromStage,
      });
      if (args.stopBeforeSave) {
        return {
          ok: true,
          status: 'ready_to_save',
          save_clicked: false,
          publish_clicked: false,
          validation: result.stages.validate_before_save,
          attempts: [{ attempt: 1, ok: true, failed_stage: null }],
        };
      }
      if (String((task.store || {}).listing_mode || '') === 'cnsc_global') {
        await assertCNSCDraftInboxCleanForParentSku(browser, task);
      }
      const save = await clickSaveDraftOnce(result.detail.page, result.detail.frame, task);
      const verified = await verifySavedDraft(result.detail.page, result.detail.frame, save, task, result.stages, browser);
      verified.attempts = [{ attempt: 1, ok: true, failed_stage: null }];
      return verified;
    } catch (error) {
      const entry = {
        attempt: 1,
        ok: false,
        failed_stage: error.failedStage || args.resumeFromStage || 'unknown',
        error: classifyError(error),
        details: (error && error.details) || {},
        message: cleanText(error && error.message).slice(0, 500),
        at: new Date(startedAt).toISOString(),
        took_ms: Date.now() - startedAt,
      };
      attempts.push(entry);
      error.failedStage = entry.failed_stage;
      await handleFailedPreSaveAttempt(browser, error, attempts, false, {
        preserveDetails: args.preserveDetailOnFailure,
      });
      throw error;
    }
  }

  for (let attempt = 1; attempt <= MAX_PRE_SAVE_ATTEMPTS; attempt += 1) {
    const startedAt = Date.now();
    emit('attempt', { attempt, max: MAX_PRE_SAVE_ATTEMPTS, at: new Date().toISOString() });
    try {
      const result = await runPreSaveAttempt(browser, task, attempt);
      detail = result.detail;
      stages = result.stages;
      if (attempts.length) {
        // record the successful attempt too so the audit trail is complete
        attempts.push({
          attempt,
          ok: true,
          failed_stage: null,
          error: null,
          details: {},
          at: new Date(startedAt).toISOString(),
          took_ms: Date.now() - startedAt,
        });
      }
      break;
    } catch (error) {
      const reason = classifyError(error);
      const entry = {
        attempt,
        ok: false,
        failed_stage: error.failedStage || 'unknown',
        error: reason,
        details: (error && error.details) || {},
        message: cleanText(error && error.message).slice(0, 500),
        at: new Date(startedAt).toISOString(),
        took_ms: Date.now() - startedAt,
      };
      attempts.push(entry);
      // 进度面板只认 stage / code 两个字段：不补进去，界面上这次失败就只有一行
      // 「步骤」、看不到失败在哪一步、也看不到原因（现场 2026-10-27 任务 9da6b384）。
      emit('attempt_failed', { ...entry, stage: entry.failed_stage, code: entry.error });
      lastError = error;

      const retryable = RETRYABLE_ERRORS.has(reason);
      const willRetry = retryable && attempt < MAX_PRE_SAVE_ATTEMPTS;
      if (willRetry) emit('attempt_retrying', { attempt, next_attempt: attempt + 1, reason });
      await handleFailedPreSaveAttempt(browser, error, attempts, willRetry, {
        preserveDetails: args.preserveDetailOnFailure,
      });
    }
  }

  if (!detail || !stages) {
    // Should be unreachable — the loop either returns detail/stages or throws.
    if (lastError) throw lastError;
    throw new BlockedError('pre_save_flow_did_not_complete');
  }

  if (args.stopBeforeSave) {
    return {
      ok: true,
      status: 'ready_to_save',
      save_clicked: false,
      publish_clicked: false,
      validation: stages.validate_before_save,
      attempts,
    };
  }

  // Save is intentionally NOT retried: clickSaveDraftOnce consumes the save_token
  // and a real POST /save may have reached Mabang even when Playwright reports a
  // click timeout, so a retry could silently create a duplicate draft.
  if (String((task.store || {}).listing_mode || '') === 'cnsc_global') {
    await assertCNSCDraftInboxCleanForParentSku(browser, task);
  }
  const save = await clickSaveDraftOnce(detail.page, detail.frame, task);
  const verified = await verifySavedDraft(detail.page, detail.frame, save, task, stages, browser);
  verified.attempts = attempts;
  return verified;
}

if (require.main === module) {
  run()
    .then((result) => {
      process.stdout.write(RESULT_MARKER + JSON.stringify(result) + '\n', () => process.exit(result.ok ? 0 : 2));
    })
    .catch((error) => {
      const blocked = error instanceof BlockedError;
      const result = {
        ok: false,
        status: blocked ? 'blocked' : 'failed',
        error: blocked ? error.reason : cleanText(error && error.message),
        details: blocked ? error.details : {},
        save_clicked: Boolean(blocked && error.details && error.details.save_clicked),
        publish_clicked: false,
        page_closed: Boolean(blocked && error.details && error.details.page_closed),
        attempts: Array.isArray(error && error.attempts) ? error.attempts : [],
        failed_stage: error && error.failedStage ? error.failedStage : null,
      };
      process.stdout.write(RESULT_MARKER + JSON.stringify(result) + '\n', () => process.exit(blocked ? 2 : 1));
    });
}

module.exports = {
  applyTransactionInformation,
  applyAttributeValueInModel,
  applyAttributeValuesInModel,
  attributeValuesRendered,
  normalizeContentEditableText,
  ownLabelText,
  formItemByOwnLabel,
  prefixAnchoredItems,
  expandHiddenCategoryAttributes,
  isSelectPanelOpen,
  panelCloseStrategy,
  tagBasedEvidence,
  tagTextsForItem,
  selectionEvidenceMatches,
  openSelectPanel,
  submitCustomSelectValue,
  typeIntoField,
  isSelectControl,
  resolveTemplateAttributeFields,
  snapshotAttributeOptionLabels,
  looksLikeOptionId,
  applyCustomValueThroughUi,
  clearAttributeValueInModel,
  registeredCustomOption,
  isTagBasedControl,
  customValueEchoed,
  selectedTextsForItem,
  valueInputForItem,
  CONTROL_INPUT_SELECTOR,
  isFillableControl,
  addCustomSelectOption,
  selectElementOptionOrCustom,
  logisticsValuesComplete,
  formatWeightKg,
  formatPackageCm,
  buildCNSCDomGridPlan,
  validateCNSCDomGridReadback,
  resolveCNSCWarehouseStock,
  expectedCNSCDomRows,
  validateCNSCTaskPayload,
  readCNSCSitePriceSnapshot,
  collectCNSCSitePickerCandidates,
  openCNSCSitePicker,
  resolveCNSCTargetCandidate,
  normalizeName,
  selectCNSCTargetSites,
  verifyCNSCTargetSites,
  buildCNSCSitePricePlan,
  orderCNSCSiteDiscountPlan,
  validateCNSCSitePriceReadback,
  enableCNSCAutoPublish,
  discountDisplayTolerance,
  TEXT,
  PUBLISH_TEXTS,
  disableSkuWeight,
  matchSiteSkuRows,
  verifySiteSkuReadback,
  fillLogisticsInfo,
  appendDescriptionMedia,
  buildDescriptionMediaPlan,
  descriptionProductImageUrls,
  missingDescriptionMedia,
  descriptionMediaAppendedAtTail,
  descriptionImageKey,
  descriptionImageIndexOf,
  descriptionScrolledToBottom,
  scrollDescriptionToBottom,
  scrollDescriptionEditorBoxToBottom,
  focusDescriptionEnd,
  descriptionCaretAtEnd,
  descriptionSizeChartSource,
  insertDescriptionImageByFile,
  uploadDescriptionSizeChartFile,
  undoDescriptionInserts,
      insertDescriptionProductImages,
  readDescriptionImageSizes,
  waitForDescriptionImageSizes,
  openDescriptionImageEditor,
  closeDescriptionImageEditor,
  ensureDescriptionImageWidths,
  collectProductImageUrls,
  collectSizeChartPreviewUrl,
  skuGridHasWeightColumn,
  resolveWatermarkTemplate,
  findWatermarkTrigger,
  openWatermarkMenu,
  readWatermarkMenuItems,
  clickWatermarkMenuOption,
  selectWatermarkImages,
  restoreWatermarkImages,
  watermarkImageSources,
  waitForWatermarkApplied,
  applyWatermark,
  runOptionalMediaStages,
  uploadProductVideo,
  uploadProductImages,
  countProductImageCards,
  cleanFilePath,
  closeSavedDetailPage,
  matchCNSCDraftRows,
  verifySavedDraft,
  classifySaveRejection,
  readSaveRejectionTexts,
  readSaveRejectionTextsAcrossFrames,
  watchSaveResponses,
  waitForSavedDetailPageToClose,
  handleFailedPreSaveAttempt,
  resolvePreSaveStartIndex,
  detailPageMatchesTask,
};
