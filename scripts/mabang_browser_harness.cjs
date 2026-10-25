'use strict';

const { chromium } = require('playwright');
const main = require('./mabang_playwright_executor.cjs');

const RESULT_MARKER = 'MABANG_HARNESS_RESULT ';
const MAX_SNAPSHOT_CHARS = 12000;
const FORBIDDEN = ['发布', '刊登', '审核', 'publish', 'submit', 'selector', 'locator', 'javascript:', 'eval('];
const TARGETS = new Set(['shop_picker', 'shop_options', 'save_button', 'title_input', 'description_input', 'category_dialog', 'trade_info', 'shop_selected']);

function HarnessError(code, message, details = {}) {
  const error = new Error(message);
  error.code = code;
  error.details = details;
  return error;
}

function validateAction(action) {
  if (!action || typeof action !== 'object') throw HarnessError('invalid_action', 'action must be an object');
  const type = typeof action.type === 'string' ? action.type.trim() : '';
  if (!['wait_for', 'click', 'fill', 'select_shop', 'save_draft', 'verify'].includes(type)) {
    throw HarnessError('unknown_action', `unsupported action: ${type || '<missing>'}`);
  }
  const serialized = JSON.stringify(action).toLowerCase();
  if (FORBIDDEN.some((word) => serialized.includes(word))) throw HarnessError('forbidden_action', 'unsafe action target');
  if (['wait_for', 'click', 'fill', 'verify'].includes(type)) {
    const target = typeof action.target === 'string' ? action.target.trim() : '';
    if (!TARGETS.has(target)) throw HarnessError('invalid_target', `unsupported semantic target: ${target || '<missing>'}`);
  }
  if (type === 'fill' && !Object.prototype.hasOwnProperty.call(action, 'value')) {
    throw HarnessError('invalid_action', 'fill requires value');
  }
  if (type === 'select_shop' && ['site', 'name', 'id'].some((key) => !String(action[key] || '').trim())) {
    throw HarnessError('shop_identity_incomplete', 'select_shop requires site, name and id');
  }
  if (type === 'save_draft' && action.target && action.target !== 'save_button') {
    throw HarnessError('invalid_target', 'save_draft target must be save_button');
  }
  return { ...action, type, ...(action.target ? { target: String(action.target).trim() } : {}) };
}

function resolveShopTarget(candidates, target) {
  const site = String(target?.site || '').trim().toUpperCase();
  const name = String(target?.name || '').trim().toLowerCase();
  const id = String(target?.id || '').trim();
  const matches = (candidates || []).filter((candidate) => {
    const ids = (candidate.storeIds || []).map(String);
    const names = (candidate.names || []).map((value) => String(value).trim().toLowerCase());
    return String(candidate.siteCode || '').toUpperCase() === site && ids.includes(id) && names.includes(name);
  });
  if (matches.length !== 1) throw HarnessError('cnsc_target_site_not_found', 'CNSC target shop is not unique', { site, name, id, count: matches.length });
  return matches[0];
}

async function buildPageSnapshot(frame, context = {}) {
  // Snapshot the DETAIL FRAME body, not the outer goPublish page: the outer body
  // is a jQuery shell with no form state, which gave the planner useless input.
  const title = await frame.title().catch(() => '');
  const rawDom = await frame.locator('body').evaluate((body) => {
    const clone = body.cloneNode(true);
    for (const element of clone.querySelectorAll('input[type="password"], input[name*="password" i], input[name*="token" i]')) {
      element.removeAttribute('value');
      element.textContent = '';
    }
    return clone.innerText || clone.outerHTML || '';
  }).catch(() => '');
  const dom = String(rawDom || '')
    .replace(/(<input\b[^>]*(?:password|token)[^>]*value=)["'][^"']*["']/gi, '$1""')
    .replace(/(password|token)(\s*[:=]\s*)[^,;\s<}]*/gi, '$1$2[REDACTED]');
  return {
    url: frame.url(),
    title: String(title || '').slice(0, 200),
    failed_stage: context.failedStage || null,
    dom: String(dom || '').slice(0, MAX_SNAPSHOT_CHARS),
  };
}

function semanticLocator(frame, target) {
  if (target === 'shop_picker') return frame.locator('#shops-wap .shop-box');
  if (target === 'shop_options') return frame.locator('#shops-wap .site');
  if (target === 'shop_selected') return frame.locator('#shops-wap .shop-box');
  if (target === 'save_button') return frame.getByRole('button', { name: main.TEXT.save, exact: true });
  if (target === 'title_input') return frame.locator('input[placeholder*="标题"], input[name*="title" i], input[id*="title" i]');
  if (target === 'description_input') return frame.locator('textarea');
  if (target === 'category_dialog') return frame.locator('.el-dialog:visible');
  if (target === 'trade_info') return frame.getByText(main.TEXT.tradeInfo, { exact: true });
  throw HarnessError('invalid_target', `unsupported semantic target: ${target}`);
}

async function findDetail(browser) {
  for (const context of browser.contexts()) {
    for (const page of context.pages()) {
      const frame = await main.detailFrameFromPage?.(page);
      if (frame) return { page, frame };
      const candidate = page.frames().find((item) => /productDetailShopee/i.test(item.url()));
      if (candidate) return { page, frame: candidate };
    }
  }
  throw HarnessError('mabang_detail_iframe_not_found', 'current detail page not found');
}

async function executeHarnessPlan(browser, task, plan, context = {}, options = {}) {
  const detail = await findDetail(browser);
  const trace = [];
  // select_shop must run as ONE multi-target call: selectCNSCTargetSites asserts
  // the exact selected set, so invoking it per-shop would treat the previous
  // action's shop as an unexpected selection and fail the second action.
  const actions = [];
  for (const rawAction of plan.actions || []) {
    const action = validateAction(rawAction);
    const previous = actions[actions.length - 1];
    if (action.type === 'select_shop' && previous && previous.type === 'select_shop') {
      previous.targets.push({ site_code: action.site, store_name: action.name, store_id: action.id });
      continue;
    }
    if (action.type === 'select_shop') {
      actions.push({ type: 'select_shop', targets: [{ site_code: action.site, store_name: action.name, store_id: action.id }] });
      continue;
    }
    actions.push(action);
  }
  for (const action of actions) {
    const started = Date.now();
    if (action.type === 'wait_for' || action.type === 'verify') {
      await semanticLocator(detail.frame, action.target).first().waitFor({ state: 'visible', timeout: options.stepTimeoutMs || 5000 });
    } else if (action.type === 'click') {
      await semanticLocator(detail.frame, action.target).first().click({ timeout: options.stepTimeoutMs || 5000 });
    } else if (action.type === 'fill') {
      await semanticLocator(detail.frame, action.target).first().fill(String(action.value));
    } else if (action.type === 'select_shop') {
      await main.selectCNSCTargetSites(detail.frame, { ...task, store: { ...(task.store || {}), target_site_stores: action.targets } });
    } else if (action.type === 'save_draft') {
      throw HarnessError('save_requires_resume', 'save_draft is executed by the deterministic resume flow');
    }
    trace.push({ type: action.type, target: action.target || (action.targets ? action.targets.map((t) => `${t.site_code}/${t.store_name}`).join('+') : null), ok: true, took_ms: Date.now() - started });
  }
  return { ok: true, status: 'recovered', trace, failed_stage: context.failedStage || null };
}

async function readStdin() {
  let data = '';
  for await (const chunk of process.stdin) data += chunk;
  return JSON.parse(data || '{}');
}

async function run() {
  const input = await readStdin();
  const browser = await chromium.connectOverCDP(input.cdp_url || 'http://127.0.0.1:9223');
  try {
    const detail = await findDetail(browser);
    const result = input.mode === 'snapshot'
      ? { ok: true, status: 'snapshot', snapshot: await buildPageSnapshot(detail.frame, input.context || {}) }
      : await executeHarnessPlan(browser, input.task || {}, input.plan || {}, input.context || {}, input.options || {});
    process.stdout.write(RESULT_MARKER + JSON.stringify(result) + '\n');
    process.exitCode = 0;
  } catch (error) {
    process.stdout.write(RESULT_MARKER + JSON.stringify({ ok: false, status: 'harness_blocked', error: error.code || error.message, details: error.details || {}, trace: [] }) + '\n');
    process.exitCode = 2;
  } finally {
    await browser.close().catch(() => {});
  }
}

if (require.main === module) run();

module.exports = { HarnessError, validateAction, resolveShopTarget, buildPageSnapshot, executeHarnessPlan };
