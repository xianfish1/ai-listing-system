'use strict';

// Standalone Mabang auto-login helper, attached to the dedicated automation Chrome
// over CDP. Credentials arrive through the MABANG_ACCOUNT / MABANG_PASSWORD
// environment variables (never argv, payload, or logs) and are used only to fill
// the login form. Captcha / slider verification is never auto-solved: it is
// reported as captcha_required for the operator, per
// docs/MABANG_SHOPEE_BROWSER_USE_WORKFLOW.md.
//
// Logged-out note: opening the workspace URL (gotoApp) without a session can land
// on the marketing homepage instead of the login form. There the helper clicks
// the top-right 「登录」 entry first, then fills the login form.

const { chromium } = require('playwright');

const CDP_DEFAULT = 'http://127.0.0.1:9223';
// Same Shopee workspace URL the Python launcher opens; kept in sync with
// services/api/mabang_browser.py MABANG_NEW_PRODUCT_URL.
const WORKSPACE_URL_DEFAULT =
  'https://www.mabangerp.com/index.php?mod=main.gotoApp&v=v3&menuKey=M0010813&platform=shopee&version=1';
const LOGIN_PAGE_URL = 'https://www.mabangerp.com/index.htm';
const RESULT_MARKER = 'MABANG_LOGIN_RESULT ';

const LOGIN_FIELDS_SELECTOR = '#account,#password,#account-btn';
const ACCOUNT_SELECTOR = '#account';
const PASSWORD_SELECTOR = '#password';
const SUBMIT_SELECTOR = '#account-btn';
// The login page is /index.htm; the workspace URL uses index.php, which does not
// contain that substring, so the URL hint alone can tell the two apart.
const LOGIN_URL_HINT = 'index.htm';
const MABANG_HOST_PATTERN = /mabangerp\.com/i;
// 判断「是不是马帮自己的框架」必须看主机名：整串 URL 子串匹配会把第三方 iframe
// 也当成马帮框架 —— 53KF 客服挂件的地址里带着 referer=…mabangerp.com…，它的
// 内联脚本配置又含 "ly_captcha":"1"，于是滑块判定被误报成 captcha_required。
const MABANG_HOSTNAME_PATTERN = /(^|\.)mabangerp\.com$/i;
const CAPTCHA_PATTERN = /\u5b89\u5168\u9a8c\u8bc1|\u6ed1\u5757|captcha|verification/i;
// Same workspace marker the save executor waits for (TEXT.pending).
const WORKSPACE_TEXT_MARKER = '\u5f85\u53d1\u5e03\u5546\u54c1'; // 待发布商品
const WORKSPACE_TITLE_MARKER = '\u9a6c\u5e2eERP::Shopee\u520a\u767b'; // 马帮ERP::Shopee刊登
const LOGIN_ENTRY_TEXT = '\u767b\u5f55'; // 登录
const STATE_TIMEOUT_MS = 15000;
const LOGIN_SUBMIT_TIMEOUT_MS = 20000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function cleanText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function parseArgs(argv) {
  const result = { cdpUrl: CDP_DEFAULT, workspaceUrl: WORKSPACE_URL_DEFAULT };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === '--cdp-url' && argv[index + 1]) {
      result.cdpUrl = argv[index + 1];
      index += 1;
    }
    if (argv[index] === '--workspace-url' && argv[index + 1]) {
      result.workspaceUrl = argv[index + 1];
      index += 1;
    }
  }
  return result;
}

function loginCredentialsFromEnv(env = process.env) {
  const account = String(env.MABANG_ACCOUNT || '').trim();
  const password = String(env.MABANG_PASSWORD || '');
  if (!account || !password) return null;
  return { account, password };
}

function captchaDetected(text) {
  return CAPTCHA_PATTERN.test(String(text || ''));
}

function looksLikeLoginUrl(url) {
  const value = String(url || '');
  return MABANG_HOST_PATTERN.test(value) && value.includes(LOGIN_URL_HINT);
}

function frameHostname(url) {
  try {
    return new URL(String(url || '')).hostname;
  } catch {
    return '';
  }
}

function isMabangUrl(url) {
  return MABANG_HOSTNAME_PATTERN.test(frameHostname(url));
}

function allPages(browser) {
  return browser.contexts().flatMap((context) => context.pages());
}

async function closeAllPages(browser) {
  const pages = allPages(browser);
  await Promise.all(pages.map((page) => page.close().catch(() => {})));
  return pages.length;
}

function findMabangPage(browser) {
  return allPages(browser).find((page) => isMabangUrl(page.url())) || null;
}

// Mabang keeps the login form in the DOM (hidden) after sign-in, so a mere
// count() > 0 falsely reports a login gate; only a VISIBLE field counts.
async function hasVisibleLoginField(page) {
  const fields = page.locator(LOGIN_FIELDS_SELECTOR);
  for (let index = 0, count = await fields.count().catch(() => 0); index < count; index += 1) {
    if (await fields.nth(index).isVisible().catch(() => false)) return true;
  }
  return false;
}

async function pageCaptchaText(page) {
  for (const frame of page.frames()) {
    // 只扫描马帮自己域名的框架：第三方 iframe（53KF 客服等）和官网营销页的
    // 文案里可能出现“验证/captcha”等字样，会造成滑块误报。
    if (!isMabangUrl(frame.url()) && frame !== page.mainFrame()) continue;
    const body = cleanText(await frame.locator('body').innerText({ timeout: 1000 }).catch(() => ''));
    if (captchaDetected(body)) return body.slice(0, 200);
  }
  return '';
}

// Workspace evidence (title or the 待发布商品 tab) is the only trusted
// "already signed in" signal: a bare mabangerp.com URL without a login form can
// equally be the logged-out marketing homepage.
async function hasWorkspaceEvidence(page) {
  const title = await page.title().catch(() => '');
  if (String(title || '').includes(WORKSPACE_TITLE_MARKER)) return true;
  for (const frame of page.frames()) {
    const count = await frame.getByText(WORKSPACE_TEXT_MARKER, { exact: true }).count().catch(() => 0);
    if (count) return true;
  }
  return false;
}

async function hasLoginEntry(page) {
  const entry = page.getByText(LOGIN_ENTRY_TEXT, { exact: true }).first();
  return entry.isVisible().catch(() => false);
}

async function clickLoginEntry(page) {
  const entry = page.getByText(LOGIN_ENTRY_TEXT, { exact: true }).first();
  try {
    await entry.click({ timeout: 8000 });
  } catch (error) {
    await entry.click({ force: true, timeout: 4000 });
  }
}

async function openWorkspacePage(browser, workspaceUrl) {
  const context = browser && typeof browser.contexts === 'function' ? browser.contexts()[0] : null;
  if (!context) {
    // 浏览器上下文没了（通常是自动化浏览器被重启）：给明确错误，别抛 newPage 的 TypeError。
    throw new Error('browser_context_missing: 自动化浏览器没有可用上下文，请确认浏览器还在运行后重试');
  }
  const page = await context.newPage();
  await page.goto(workspaceUrl, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
  return page;
}

// Wait for the workspace tab to settle into one of:
// needs_login / captcha_required / already_logged_in / needs_login_entry /
// unknown_state. A login gate requires VISIBLE #account/#password fields — the
// URL is not trusted here, because on this deployment /index.htm serves the
// marketing homepage rather than a login form. The marketing homepage (visible
// 「登录」 entry, no workspace content) means signed out.
async function waitForStableState(page, timeoutMs = STATE_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await hasVisibleLoginField(page).catch(() => false)) {
      return (await pageCaptchaText(page).catch(() => '')) ? 'captcha_required' : 'needs_login';
    }
    if (await hasWorkspaceEvidence(page).catch(() => false)) return 'already_logged_in';
    if (await hasLoginEntry(page).catch(() => false)) return 'needs_login_entry';
    await sleep(500);
  }
  return 'unknown_state';
}

// After clicking 「登录」 the form may open in the same tab or a new one, so scan
// every page for VISIBLE login fields instead of watching a single tab or URL.
async function findGatePage(browser, timeoutMs = 12000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    for (const page of allPages(browser)) {
      if (!(await hasVisibleLoginField(page).catch(() => false))) continue;
      if (await pageCaptchaText(page).catch(() => '')) return { page, state: 'captcha_required' };
      return { page, state: 'needs_login' };
    }
    await sleep(400);
  }
  return null;
}

async function performLogin(page, credentials) {
  await page.locator(ACCOUNT_SELECTOR).first().fill(credentials.account, { timeout: 8000 });
  await page.locator(PASSWORD_SELECTOR).first().fill(credentials.password, { timeout: 8000 });
  await page.locator(SUBMIT_SELECTOR).first().click({ timeout: 8000 });
}

// After submitting, the form either disappears (logged_in), shows a
// slider/verification alongside the fields (captcha_required), or stays. Only
// the visible form counts as the gate; the URL is not trusted.
async function waitForLoginOutcome(page, timeoutMs = LOGIN_SUBMIT_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await sleep(500);
    if (!(await hasVisibleLoginField(page).catch(() => false))) return 'logged_in';
    if (await pageCaptchaText(page).catch(() => '')) return 'captcha_required';
  }
  return 'failed';
}

function shortError(error) {
  return cleanText(error && error.message).slice(0, 300) || 'unknown_error';
}

async function run(options = {}) {
  const args = parseArgs(options.argv || process.argv.slice(2));
  const credentials = loginCredentialsFromEnv(options.env || process.env);
  const finish = (result) => {
    // The result payload must never carry credential material.
    process.stdout.write(RESULT_MARKER + JSON.stringify(result) + '\n');
    return result;
  };

  if (!credentials) {
    return finish({ ok: false, status: 'no_credentials', error: 'missing_login_environment' });
  }

  let browser;
  try {
    browser = await chromium.connectOverCDP(args.cdpUrl, { timeout: 10000 });
  } catch (error) {
    return finish({ ok: false, status: 'cdp_unreachable', error: shortError(error) });
  }

  try {
    // 先关闭所有旧标签页/窗口，再打开一个全新的工作台页，避免残留页面干扰登录判定。
    await closeAllPages(browser);
    let page = await openWorkspacePage(browser, args.workspaceUrl);
    let state = await waitForStableState(page).catch(() => 'unknown_state');
    if (state === 'unknown_state') {
      // 页面可能停在跳转前的空白页：强制再导航一次工作台地址后重试判定。
      await page.goto(args.workspaceUrl, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
      state = await waitForStableState(page, 8000).catch(() => 'unknown_state');
    }
    if (state === 'unknown_state') {
      // 既无登录表单、也无「登录」入口、也无工作台内容：视为已登录但内容加载慢，
      // 最终由保存执行器校验。
      return finish({ ok: true, status: 'already_logged_in' });
    }
    if (state === 'needs_login_entry') {
      // 未登录时 gotoApp 会先落到官网首页：点右上角「登录」进入登录表单。
      let gate = null;
      try {
        await clickLoginEntry(page);
        gate = await findGatePage(browser, 10000);
      } catch (error) {
        gate = null;
      }
      if (!gate) {
        // 兜底：直接打开登录页地址。
        await page.goto(LOGIN_PAGE_URL, { waitUntil: 'domcontentloaded', timeout: 20000 }).catch(() => {});
        const direct = await waitForStableState(page, 8000).catch(() => 'unknown_state');
        if (direct === 'needs_login' || direct === 'captcha_required') gate = { page, state: direct };
      }
      if (!gate) return finish({ ok: false, status: 'failed', error: 'login_entry_not_found' });
      if (gate.state === 'captcha_required') {
        return finish({ ok: false, status: 'captcha_required', error: 'mabang_captcha_or_verification' });
      }
      page = gate.page;
      state = 'needs_login';
    }
    if (state === 'already_logged_in') return finish({ ok: true, status: 'already_logged_in' });
    if (state === 'captcha_required') {
      return finish({ ok: false, status: 'captcha_required', error: 'mabang_captcha_or_verification' });
    }
    if (state !== 'needs_login') {
      return finish({ ok: false, status: 'failed', error: 'mabang_page_not_loaded' });
    }

    await performLogin(page, credentials);
    const outcome = await waitForLoginOutcome(page).catch(() => 'failed');
    if (outcome === 'logged_in') return finish({ ok: true, status: 'logged_in' });
    if (outcome === 'captcha_required') {
      return finish({ ok: false, status: 'captcha_required', error: 'mabang_captcha_after_submit' });
    }
    return finish({ ok: false, status: 'failed', error: 'login_did_not_complete_check_credentials' });
  } catch (error) {
    return finish({ ok: false, status: 'failed', error: shortError(error) });
  }
}

if (require.main === module) {
  const exitCodes = { already_logged_in: 0, logged_in: 0, captcha_required: 2, no_credentials: 3 };
  run()
    .then((result) => process.exit(exitCodes[result.status] ?? 1))
    .catch((error) => {
      process.stdout.write(
        RESULT_MARKER +
          JSON.stringify({ ok: false, status: 'failed', error: shortError(error) }) +
          '\n',
        () => process.exit(1),
      );
    });
}

module.exports = {
  parseArgs,
  loginCredentialsFromEnv,
  captchaDetected,
  looksLikeLoginUrl,
  isMabangUrl,
  findMabangPage,
  closeAllPages,
  hasVisibleLoginField,
  pageCaptchaText,
  hasWorkspaceEvidence,
  hasLoginEntry,
  clickLoginEntry,
  findGatePage,
  waitForStableState,
  performLogin,
  waitForLoginOutcome,
  RESULT_MARKER,
  CDP_DEFAULT,
  WORKSPACE_URL_DEFAULT,
  LOGIN_PAGE_URL,
  LOGIN_ENTRY_TEXT,
  WORKSPACE_TEXT_MARKER,
  LOGIN_URL_HINT,
};
