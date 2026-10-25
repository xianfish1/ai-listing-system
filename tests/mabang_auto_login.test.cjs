'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');

const autoLogin = require('../scripts/mabang_auto_login.cjs');

// 线上真实的 53KF 客服挂件地址：referer 参数里带着马帮域名，内联脚本配置里含
// "ly_captcha":"1"。旧实现按整串 URL 子串匹配 mabangerp.com，会把它当成马帮框架。
const THIRD_PARTY_WIDGET_URL =
  'https://www17.53kf.com/m.php?kf_sign=x&cid=72686291&referer=https%3A%2F%2Fwww.mabangerp.com%2Findex.htm';

function fakeLocator(visible) {
  return {
    isVisible: async () => visible,
    count: async () => (visible ? 3 : 0),
    nth: () => ({ isVisible: async () => visible }),
  };
}

function fakePage({
  url = 'https://www.mabangerp.com/index.htm',
  loginVisible = false,
  body = '',
  loginEntry = false,
  workspaceMarker = false,
  title = '',
  thirdPartyBody = null,
} = {}) {
  const getByText = (text) => ({
    count: async () => (text === autoLogin.WORKSPACE_TEXT_MARKER && workspaceMarker ? 1 : 0),
    first: () => ({
      isVisible: async () => loginEntry && text === autoLogin.LOGIN_ENTRY_TEXT,
      click: async () => {
        if (!loginEntry || text !== autoLogin.LOGIN_ENTRY_TEXT) throw new Error('not clickable');
      },
    }),
  });
  const mabangFrame = {
    url: () => url,
    locator: () => ({ innerText: async () => body }),
    getByText,
  };
  const frames = [mabangFrame];
  if (thirdPartyBody !== null) {
    frames.push({
      url: () => THIRD_PARTY_WIDGET_URL,
      locator: () => ({ innerText: async () => thirdPartyBody }),
    });
  }
  return {
    url: () => url,
    title: async () => title,
    locator: (selector) => fakeLocator(selector === '#account,#password,#account-btn' ? loginVisible : false),
    getByText,
    frames: () => frames,
    mainFrame: () => mabangFrame,
  };
}

function fakeBrowser(pages) {
  return { contexts: () => [{ pages: () => pages }] };
}

test('login credentials come from the environment and never default to something', () => {
  assert.deepEqual(autoLogin.loginCredentialsFromEnv({ MABANG_ACCOUNT: 'ops', MABANG_PASSWORD: 'pw' }), {
    account: 'ops',
    password: 'pw',
  });
  assert.equal(autoLogin.loginCredentialsFromEnv({ MABANG_ACCOUNT: 'ops' }), null);
  assert.equal(autoLogin.loginCredentialsFromEnv({ MABANG_PASSWORD: 'pw' }), null);
  assert.equal(autoLogin.loginCredentialsFromEnv({}), null);
  assert.equal(autoLogin.loginCredentialsFromEnv({ MABANG_ACCOUNT: '  ', MABANG_PASSWORD: 'pw' }), null);
});

test('captcha detection covers slider and verification wording', () => {
  assert.equal(autoLogin.captchaDetected('请完成安全验证后继续'), true);
  assert.equal(autoLogin.captchaDetected('拖动滑块完成拼图'), true);
  assert.equal(autoLogin.captchaDetected('Captcha verification failed'), true);
  assert.equal(autoLogin.captchaDetected('欢迎回来，马帮ERP'), false);
  assert.equal(autoLogin.captchaDetected(''), false);
});

test('only a mabang hostname counts as a mabang frame', () => {
  assert.equal(autoLogin.isMabangUrl('https://www.mabangerp.com/index.htm'), true);
  assert.equal(autoLogin.isMabangUrl('https://mabangerp.com/index.php?mod=main.gotoApp'), true);
  // 第三方挂件/验证码框架即使带着马帮域名参数，也不得被当成马帮框架。
  assert.equal(autoLogin.isMabangUrl(THIRD_PARTY_WIDGET_URL), false);
  assert.equal(autoLogin.isMabangUrl('https://turing.captcha.gtimg.com/1/template/drag_ele.html'), false);
  assert.equal(autoLogin.isMabangUrl('https://evil.example/?next=mabangerp.com'), false);
  assert.equal(autoLogin.isMabangUrl('not a url'), false);
  assert.equal(autoLogin.isMabangUrl(''), false);
});

test('waitForStableState only trusts visible login fields, never the URL', async () => {
  const gate = fakePage({ url: 'https://www.mabangerp.com/index.htm', loginVisible: true, body: '账号登录' });
  assert.equal(await autoLogin.waitForStableState(gate, 800), 'needs_login');

  const captcha = fakePage({ url: 'https://www.mabangerp.com/index.htm', loginVisible: true, body: '请拖动滑块' });
  assert.equal(await autoLogin.waitForStableState(captcha, 800), 'captcha_required');

  // 第三方 iframe（53KF 客服等）里的验证类文案不得触发滑块判定。
  const thirdPartyNoise = fakePage({
    loginVisible: true,
    thirdPartyBody: '{"ly_captcha":"1"} 请拖动滑块完成安全验证',
  });
  assert.equal(await autoLogin.waitForStableState(thirdPartyNoise, 800), 'needs_login');

  // 该部署上 /index.htm 就是官网首页：没有可见登录表单时 URL 不算登录拦截。
  const homepageByUrl = fakePage({ url: 'https://www.mabangerp.com/index.htm', body: '安全验证' });
  assert.equal(await autoLogin.waitForStableState(homepageByUrl, 900), 'unknown_state');

  // 官网首页：有「登录」入口、无工作台内容 → 需要先点登录入口。
  const marketing = fakePage({ url: 'https://www.mabangerp.com/', loginEntry: true });
  assert.equal(await autoLogin.waitForStableState(marketing, 800), 'needs_login_entry');

  // 已登录工作台：待发布商品内容或标题就是已登录证据，立即可判定。
  const workspace = fakePage({
    url: 'https://www.mabangerp.com/index.php?mod=main.gotoApp',
    workspaceMarker: true,
    title: '马帮ERP::Shopee刊登',
  });
  assert.equal(await autoLogin.waitForStableState(workspace, 800), 'already_logged_in');

  const blank = fakePage({ url: 'about:blank' });
  assert.equal(await autoLogin.waitForStableState(blank, 900), 'unknown_state');
});

test('closeAllPages closes every tab before a fresh workspace is opened', async () => {
  let closed = 0;
  const first = { ...fakePage(), close: async () => { closed += 1; } };
  const second = { ...fakePage({ url: 'https://example.com' }), close: async () => { closed += 1; } };
  const closedCount = await autoLogin.closeAllPages(fakeBrowser([first, second]));

  assert.equal(closedCount, 2);
  assert.equal(closed, 2);
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_auto_login.cjs'), 'utf8');
  assert.match(source, /await closeAllPages\(browser\)/);
  assert.match(source, /openWorkspacePage\(browser, args\.workspaceUrl\)/);
});

test('auto-login disconnects by process exit without closing the shared CDP browser', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_auto_login.cjs'), 'utf8');

  assert.doesNotMatch(source, /browser\.close\s*\(/);
  assert.match(source, /process\.exit\(exitCodes\[result\.status\]/);
});

test('findGatePage scans every tab and only matches visible login fields', async () => {
  // 回归：/index.htm 首页此前会被 URL 误判成登录页。
  const homepage = fakePage({ url: 'https://www.mabangerp.com/index.htm', loginEntry: true, body: '安全验证' });
  assert.equal(await autoLogin.findGatePage(fakeBrowser([homepage]), 600), null);

  const gate = fakePage({ url: 'https://www.mabangerp.com/login', loginVisible: true });
  assert.deepEqual(await autoLogin.findGatePage(fakeBrowser([homepage, gate]), 500), { page: gate, state: 'needs_login' });

  const captchaPage = fakePage({ loginVisible: true, body: '请拖动滑块' });
  assert.deepEqual(await autoLogin.findGatePage(fakeBrowser([captchaPage]), 500), {
    page: captchaPage,
    state: 'captcha_required',
  });
});

test('waitForLoginOutcome reports the post-submit result', async () => {
  const loggedIn = fakePage({ url: 'https://www.mabangerp.com/index.php?mod=main.gotoApp' });
  assert.equal(await autoLogin.waitForLoginOutcome(loggedIn, 1200), 'logged_in');

  const stillLogin = fakePage({ loginVisible: true, body: '账号登录' });
  assert.equal(await autoLogin.waitForLoginOutcome(stillLogin, 700), 'failed');

  const captchaAfterSubmit = fakePage({ loginVisible: true, body: '滑块验证' });
  assert.equal(await autoLogin.waitForLoginOutcome(captchaAfterSubmit, 3000), 'captcha_required');
});

test('parseArgs keeps the defaults and accepts overrides', () => {
  assert.deepEqual(autoLogin.parseArgs([]), {
    cdpUrl: autoLogin.CDP_DEFAULT,
    workspaceUrl: autoLogin.WORKSPACE_URL_DEFAULT,
  });
  assert.deepEqual(autoLogin.parseArgs(['--cdp-url', 'http://127.0.0.1:9333']), {
    cdpUrl: 'http://127.0.0.1:9333',
    workspaceUrl: autoLogin.WORKSPACE_URL_DEFAULT,
  });
  assert.equal(autoLogin.CDP_DEFAULT, 'http://127.0.0.1:9223');
});

test('the helper clicks the marketing login entry and has a direct login-page fallback', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_auto_login.cjs'), 'utf8');
  assert.match(source, /clickLoginEntry/);
  assert.match(source, /LOGIN_PAGE_URL = 'https:\/\/www\.mabangerp\.com\/index\.htm'/);
  assert.match(source, /findGatePage\(browser/);
  // 登录表单判定只看可见字段；验证码文案只在马帮自己的框架里计数。
  assert.match(source, /isMabangUrl\(frame\.url\(\)\) && frame !== page\.mainFrame\(\)/);
  assert.match(source, /if \(!\(await hasVisibleLoginField\(page\)\.catch\(\(\) => false\)\)\) return 'logged_in';/);
});

test('the result contract never carries credential material', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_auto_login.cjs'), 'utf8');
  assert.match(source, /RESULT_MARKER \+ JSON\.stringify\(result\)/);
  assert.doesNotMatch(source, /JSON\.stringify\(\{[^}]*password/);
  // 环境变量是唯一的凭据通道，不允许把凭据拼进命令行参数。
  assert.match(source, /env\.MABANG_ACCOUNT/);
  assert.match(source, /env\.MABANG_PASSWORD/);
  assert.doesNotMatch(source, /'--account'/);
  assert.doesNotMatch(source, /'--password'/);
});

test('the save executor treats the marketing login entry as a login gate', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  // 已登出的官网首页没有登录表单，可见的独立「登录」入口等价于登录拦截，
  // 且工作台已加载（待发布商品存在）时不得误判。
  assert.match(source, /async function hasWorkspaceMarker/);
  assert.match(source, /getByText\('\\u767b\\u5f55', \{ exact: true \}\)/);
  assert.match(source, /!\(await hasWorkspaceMarker\(page\)\)/);
  assert.match(source, /frame !== page\.mainFrame\(\)/);
});
