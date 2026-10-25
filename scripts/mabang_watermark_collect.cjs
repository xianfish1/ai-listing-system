'use strict';
/**
 * 采集马帮「水印」目录（只读，走 Playwright/CDP，不依赖 browser-harness）。
 *
 * 背景：browser-harness 的 daemon 在这台机器的 Python 3.14 下起不来
 * （asyncio base_events.py:297 assert self._sockets is not None），于是
 * `/api/store-watermarks/collect` 一直失败、水印目录永远是空的。
 * 这个采集器改走自动化已经在用的那条路（playwright-core + CDP 9223），
 * 它只做三件事：
 *   1) 找到 Shopee 商品编辑页（productDetailShopee）里的店铺身份；
 *   2) 读当前店铺可用的水印模板（Vue 状态优先，可见菜单里的 DOM 行兜底）；
 *   3) 顺带把「选完模板之后要选的图片范围」控件的真实结构读出来（probe），
 *      执行器那一段要按真实结构接。
 *
 * 硬规则：**绝不点击任何水印选项**（那会改到操作员的草稿）。只负责打开菜单、
 * 读、再把菜单收回去；读不到就报原因，绝不猜。
 *
 * 用法：
 *   node scripts/mabang_watermark_collect.cjs --cdp-url http://127.0.0.1:9223
 * 结果：
 *   MABANG_WATERMARK_RESULT {"ok":true,"store":{...},"templates":[...],...}
 */

const { chromium } = require('playwright-core');

const RESULT_MARKER = 'MABANG_WATERMARK_RESULT ';
const CDP_DEFAULT = 'http://127.0.0.1:9223';
const CDP_CONNECT_TIMEOUT_MS = 90000;
const MENU_TIMEOUT_MS = 6000;
// 与 scripts/mabang_page_import_capture.js 保持一致的水印模板字段别名。
// 注意第一个 watermarkTemplate（**单数**）：2026-10-01 在真实详情页上实测，
// 组件 data 上的字段就叫这个名字；旧别名表只有复数/带 List 的写法，所以怎么扫都是 0 条。
const TEMPLATE_LIST_KEYS = [
  'watermarkTemplate',
  'options',
  'watermarkTemplates',
  'watermarkTemplateList',
  'watermarkList',
  'templateWatermarkList',
  'watermarkTemplateOptions',
  'watermarkOptions',
  'waterMarkTemplateList',
  'watermarkListData',
  'imageWatermarkList',
  'addWatermarkList',
];
const DOM_ROW_SELECTOR = [
  '[data-watermark-id]',
  '[data-template-id]',
  '[data-templateid]',
  '[data-watermarkid]',
  '.watermark-template',
  '.watermark-item',
  '.watermark-list-item',
].join(',');
const MENU_SELECTOR = '.el-dropdown-menu,.el-select-dropdown,.el-popper,[role="menu"]';
const ADD_WATERMARK_TEXT = '\u6dfb\u52a0\u6c34\u5370';

function parseArgs(argv) {
  const args = { cdpUrl: CDP_DEFAULT };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === '--cdp-url' && argv[index + 1]) {
      args.cdpUrl = argv[index + 1];
      index += 1;
    }
  }
  return args;
}

function cleanText(value) {
  return String(value === undefined || value === null ? '' : value).replace(/\s+/g, ' ').trim();
}

function firstValue(...values) {
  for (const value of values) {
    const text = cleanText(value);
    if (text) return text;
  }
  return '';
}

/** 归一化一条水印模板：id / name / 缩略图，其它字段一律不进目录。 */
function normalizeTemplate(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const templateId = firstValue(
    raw.template_id,
    raw.templateId,
    raw.watermark_id,
    raw.watermarkId,
    raw.id,
  );
  const name = firstValue(raw.name, raw.template_name, raw.templateName, raw.watermark_name, raw.watermarkName, raw.label);
  if (!templateId || !name) return null;
  const previewUrl = firstValue(raw.preview_url, raw.previewUrl, raw.image_url, raw.imageUrl, raw.img_url, raw.imgUrl, raw.url);
  return { template_id: templateId, name, preview_url: previewUrl };
}

/** 合并 Vue 列表与可见菜单里的行，按 template_id 去重（Vue 优先，顺序稳定）。 */
function mergeTemplates(vueItems, domItems) {
  const seen = new Set();
  const merged = [];
  for (const item of [...(vueItems || []), ...(domItems || [])]) {
    const normalized = normalizeTemplate(item);
    if (!normalized || seen.has(normalized.template_id)) continue;
    seen.add(normalized.template_id);
    merged.push(normalized);
  }
  return merged;
}

function isProductEditUrl(url) {
  return /productDetailShopee/i.test(String(url || ''));
}

/** 找 Shopee 商品编辑页 frame：优先当前 page，跨 page 兜底。 */
function findProductEditFrame(browser) {
  const contexts = browser.contexts();
  for (const context of contexts) {
    for (const page of context.pages()) {
      const frame = page.frames().find((candidate) => isProductEditUrl(candidate.url()));
      if (frame) return { page, frame };
    }
  }
  return null;
}

/**
 * 在编辑页里读水印目录 + 图片范围控件结构。整段都在页面里跑，返回纯数据。
 * 注意：只 hover/click 「添加水印」入口，绝不点任何水印选项。
 */
async function readWatermarkCatalog(frame) {
  return frame.evaluate(
    async ({ menuSelector, rowSelector, listKeys, addText, menuTimeoutMs }) => {
      const clean = (value) => String(value === undefined || value === null ? '' : value).replace(/\s+/g, ' ').trim();
      const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const isVisible = (node) => {
        if (!node || node.isConnected === false) return false;
        const style = getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0;
      };
      const out = {
        store: { id: '', name: '' },
        catalog_loaded: false,
        templates: [],
        catalog_source: '',
        menu_method: '',
        menu_opened_by_collector: false,
        range_probe: { candidates: [], vue_keys: [], trigger: null },
        errors: [],
      };

      // ---- 店铺身份（用于「采集页面的店铺 == 请求的店铺」校验）----
      try {
        const rootVm = document.querySelector('#box1')?.__vue__ || document.querySelector('[id^="box"]')?.__vue__;
        const storeState = rootVm?.$store?.state?.shopee?.storeCategoryModule || {};
        const shopId = clean(storeState.shopId);
        const shops = Array.isArray(storeState.shopList) ? storeState.shopList : [];
        const matched = shops.find((item) => clean(item?.id) === shopId) || null;
        out.store = { id: shopId, name: clean(matched?.name) };
        if (!out.store.id) {
          const selected = document.querySelector('.el-select .el-select__selected-item, .el-select input');
          if (selected) out.store.name = clean(selected.textContent || selected.value);
        }
      } catch (error) {
        out.errors.push(`store identity: ${String((error && error.message) || error)}`);
      }

      // ---- Vue 状态里的水印模板（capture 脚本用的同一份别名表）----
      const vueItems = [];
      try {
        const rootVm = document.querySelector('#box1')?.__vue__ || document.querySelector('[id^="box"]')?.__vue__;
        const containers = [
          rootVm?.$refs?.productMaterialModuleRef,
          rootVm?.$refs?.productMaterialModuleRef?.$data,
          rootVm?.$store?.state?.shopee?.productMaterialModule,
        ];
        for (const element of document.querySelectorAll('.el-dialog,.el-dialog__wrapper,[role="dialog"],.el-popper,.el-dropdown-menu,.el-select-dropdown')) {
          for (let vm = element.__vue__ || element.__vueParentComponent?.proxy, depth = 0; vm && depth < 8; vm = vm.$parent, depth += 1) {
            containers.push(vm, vm?.$data);
          }
        }
        // 2026-10-01 实测：这个版本的详情页把水印模板挂在**组件实例**上（data.watermarkTemplate，
        // 从「添加水印」按钮的 Vue 祖先链往上 2 层就能拿到），Vuex 里根本没有。
        // 只扫 Vuex 会采到 0 条 —— 这正是"目录一直是空的"的另一半原因。
        const buttons = [...document.querySelectorAll('button,[role="button"],a,label,.el-button,.el-link,.el-radio,span')]
          .filter((node) => isVisible(node) && clean(node.textContent) === addText)
          .map((node) => node.closest('button') || node);
        const triggerNode = buttons[0] || null;
        let triggerVm = triggerNode?.__vue__ || triggerNode?.__vueParentComponent?.proxy || null;
        const ancestryKeys = [];
        for (let depth = 0; triggerVm && depth < 12; triggerVm = triggerVm.$parent, depth += 1) {
          containers.push(triggerVm, triggerVm?.$data);
          const data = triggerVm?.$data || {};
          ancestryKeys.push({
            depth,
            name: String(triggerVm?.$options?.name || triggerVm?.$options?._componentTag || '?'),
            arrays: Object.keys(data).filter((key) => Array.isArray(data[key])).slice(0, 12),
            hasWatermarkTemplate: Array.isArray(data.watermarkTemplate),
          });
        }
        out.range_probe.vue_ancestry = ancestryKeys;
        out.range_probe.had_trigger_node = Boolean(triggerNode);
        // 关键：**不要碰到第一个数组就停**。Vue 组件上有大量叫 options 的数组
        // （Element UI 的下拉/表格都有），一停就被它们劫持，真正的水印模板反而读不到。
        // 做法：所有容器/所有候选键都扫一遍，逐条按别名归一化，坏数据自然被丢掉，再按 id 去重。
        const seen = new Set();
        for (const container of containers) {
          if (!container || typeof container !== 'object') continue;
          const sources = [container, container?.watermarkMenu, container?.watermarkConfig, container?.$data];
          for (const source of sources) {
            if (!source || typeof source !== 'object') continue;
            for (const key of listKeys) {
              if (!Array.isArray(source[key])) continue;
              for (const item of source[key]) {
                const id = clean(item?.template_id ?? item?.templateId ?? item?.watermark_id ?? item?.watermarkId ?? item?.id);
                const name = clean(item?.name ?? item?.template_name ?? item?.templateName ?? item?.watermark_name ?? item?.watermarkName ?? item?.label);
                if (id && name && !seen.has(id)) {
                  seen.add(id);
                  vueItems.push({
                    template_id: id,
                    name,
                    preview_url: clean(item?.preview_url ?? item?.previewUrl ?? item?.image_url ?? item?.imageUrl ?? item?.img_url ?? item?.imgUrl ?? item?.url),
                  });
                }
              }
            }
          }
        }
        if (vueItems.length) out.catalog_source = 'vue';
      } catch (error) {
        out.errors.push(`vue watermark list: ${String((error && error.message) || error)}`);
      }

      // ---- 打开「添加水印」菜单（hover 优先，click 兜底），只为读 DOM 行 ----
      const triggerCandidates = [...document.querySelectorAll('button,[role="button"],a,label,.el-button,.el-link,.el-radio,span')]
        .filter((node) => isVisible(node) && clean(node.textContent) === addText);
      // 真实页面是 <button><span> 添加水印 …</span></button>：同一个按钮会被数两次
      // （button 和它里面的 span 文案都相等）。解析到最近的 button 再去重。
      const triggers = [...new Set(triggerCandidates.map((node) => node.closest('button') || node))];
      const trigger = triggers.length === 1 ? triggers[0] : null;
      // Element UI 的下拉把菜单 id 写在触发器的 aria-controls 上，优先按它找。
      const menuSelectorForTrigger = () => {
        const controls = clean(trigger?.getAttribute('aria-controls'));
        return controls ? `#${controls}, ${menuSelector}` : menuSelector;
      };
      out.range_probe.trigger = {
        count: triggers.length,
        candidates: triggerCandidates.length,
        tag: trigger ? trigger.tagName.toLowerCase() : '',
        cls: trigger ? clean(trigger.className).slice(0, 80) : '',
        aria_controls: trigger ? clean(trigger.getAttribute('aria-controls')) : '',
      };
      const menuVisible = () => {
        if (trigger?.getAttribute('aria-expanded') === 'true') return true;
        return [...document.querySelectorAll(menuSelectorForTrigger())].some((node) => isVisible(node));
      };
      const domItems = [];
      if (trigger) {
        if (!menuVisible()) {
          for (const eventName of ['mouseenter', 'mouseover']) {
            const rect = trigger.getBoundingClientRect();
            trigger.dispatchEvent(new MouseEvent(eventName, {
              bubbles: true, cancelable: true, view: window,
              clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2,
            }));
          }
          out.menu_method = 'hover';
          const deadline = Date.now() + 1500;
          while (Date.now() < deadline && !menuVisible()) await sleep(100);
          if (!menuVisible()) {
            trigger.click();
            out.menu_method = 'click';
            const clickDeadline = Date.now() + 4000;
            while (Date.now() < clickDeadline && !menuVisible()) await sleep(100);
          }
          if (menuVisible()) out.menu_opened_by_collector = true;
        } else {
          out.menu_method = 'already_open';
        }
        if (menuVisible()) {
          for (const menu of [...document.querySelectorAll(menuSelectorForTrigger())].filter(isVisible)) {
            for (const node of menu.querySelectorAll(rowSelector)) {
              if (!isVisible(node)) continue;
              const id = clean(
                node.getAttribute('data-watermark-id')
                || node.getAttribute('data-template-id')
                || node.getAttribute('data-templateid')
                || node.getAttribute('data-watermarkid')
                || node.getAttribute('data-id')
                || node.getAttribute('data-value'),
              );
              const name = clean(
                node.getAttribute('data-watermark-name')
                || node.getAttribute('data-template-name')
                || node.getAttribute('title')
                || node.querySelector('[data-watermark-name],.watermark-name,.template-name')?.textContent
                || node.textContent,
              );
              const image = node.querySelector('img');
              if (id && name) domItems.push({ template_id: id, name, preview_url: clean(image?.src) });
            }
          }
          if (domItems.length) out.catalog_source = out.catalog_source ? 'vue+dom' : 'dom';

          // ---- 图片范围控件 probe：水印模板选完之后要选「第 1 张 / 第 2 张以后 / 指定」，
          //      先把候选控件连同上下文一起读出来（不点）。----
          const rangePattern = /^(第\s*\d+\s*张|首图|主图|全部图片|所有图片|指定图片|自定义范围|图片范围|应用范围|范围)/;
          for (const node of document.querySelectorAll('button,[role="button"],label,span,div,li')) {
            const text = clean(node.textContent);
            if (!text || text.length > 20 || !rangePattern.test(text)) continue;
            if (!isVisible(node)) continue;
            out.range_probe.candidates.push({
              tag: node.tagName.toLowerCase(),
              cls: clean(node.className).slice(0, 90),
              text,
              parentCls: clean(node.parentElement?.className).slice(0, 90),
              checked: Boolean(node.querySelector?.('input:checked')),
            });
            if (out.range_probe.candidates.length >= 25) break;
          }
        }
      }
      // 把菜单收回去（只收我们自己打开的；已打开的保持原样）
      if (out.menu_opened_by_collector) {
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        await sleep(150);
      }

      out.templates = (() => {
        const seen = new Set();
        const merged = [];
        for (const item of [...vueItems, ...domItems]) {
          if (!item || !item.template_id || !item.name || seen.has(item.template_id)) continue;
          seen.add(item.template_id);
          merged.push({ template_id: item.template_id, name: item.name, preview_url: item.preview_url || '' });
        }
        return merged;
      })();
      out.catalog_loaded = out.templates.length > 0;
      return out;
    },
    {
      menuSelector: MENU_SELECTOR,
      rowSelector: DOM_ROW_SELECTOR,
      listKeys: TEMPLATE_LIST_KEYS,
      addText: ADD_WATERMARK_TEXT,
      menuTimeoutMs: MENU_TIMEOUT_MS,
    },
  );
}

async function collectWatermarks({ cdpUrl = CDP_DEFAULT } = {}) {
  const browser = await chromium.connectOverCDP(cdpUrl, { timeout: CDP_CONNECT_TIMEOUT_MS });
  try {
    const target = findProductEditFrame(browser);
    if (!target) {
      return {
        ok: false,
        status: 'not_on_product_edit_page',
        error: '当前浏览器没有打开 Shopee 商品编辑页。请先在马帮里打开一个商品（编辑），再点采集水印。',
        templates: [],
        catalog_loaded: false,
        store: { id: '', name: '' },
      };
    }
    const result = await readWatermarkCatalog(target.frame);
    return {
      ok: true,
      status: result.catalog_loaded ? 'ok' : 'empty_catalog',
      error: '',
      page_url: target.frame.url(),
      ...result,
    };
  } finally {
    await browser.close().catch(() => {});
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  let payload;
  try {
    payload = await collectWatermarks({ cdpUrl: args.cdpUrl });
  } catch (error) {
    payload = {
      ok: false,
      status: 'collect_failed',
      error: cleanText((error && error.message) || error) || '水印采集失败',
      templates: [],
      catalog_loaded: false,
      store: { id: '', name: '' },
    };
  }
  process.stdout.write(RESULT_MARKER + JSON.stringify(payload) + '\n');
}

if (require.main === module) {
  main().catch((error) => {
    process.stdout.write(RESULT_MARKER + JSON.stringify({ ok: false, status: 'collect_failed', error: String(error) }) + '\n');
    process.exitCode = 1;
  });
}

module.exports = {
  RESULT_MARKER,
  parseArgs,
  normalizeTemplate,
  mergeTemplates,
  isProductEditUrl,
  collectWatermarks,
};
