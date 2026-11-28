'use strict';
/**
 * 采集马帮「水印」目录（只读，走 Playwright/CDP，不依赖 browser-harness）。
 *
 * 背景：browser-harness 的 daemon 在这台机器的 Python 3.14 下起不来
 * （asyncio base_events.py:297 assert self._sockets is not None），于是
 * `/api/store-watermarks/collect` 一直失败、水印目录永远是空的。
 * 这个采集器改走自动化已经在用的那条路（playwright-core + CDP 18223），
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
 *   node scripts/mabang_watermark_collect.cjs --cdp-url http://127.0.0.1:18223
 * 结果：
 *   MABANG_WATERMARK_RESULT {"ok":true,"store":{...},"templates":[...],...}
 */

const { chromium } = require('playwright-core');

const RESULT_MARKER = 'MABANG_WATERMARK_RESULT ';
const CDP_DEFAULT = 'http://127.0.0.1:18223';
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
  // 真实页面（publish-ui）的菜单行是 Element UI 的 .el-dropdown-menu__item，
  // 行文本就是模板名、DOM 里没有 id（旧选择器漏了它 → 菜单开着也读不出行）。
  '.el-dropdown-menu__item',
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
      // 菜单里不是模板的两行（管理入口 / 空态），永远不算模板。
      // 空态有两版文案（「暂无水印」/「暂无水印模板」），都算占位、都不触发「等到有内容」。
      const NON_TEMPLATE_ROWS = [/水印模板管理/, /^暂无水印/];
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

      // ---- 1) 店铺身份（用于「采集页面的店铺 == 请求的店铺」校验）----
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

      // ---- 2) 找「添加水印」触发器（真实页面是 <button><span>添加水印</span></button>）----
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

      // ---- 3) 先打开菜单。真实页面（publish-ui）的水印模板是**第一次打开下拉时才加载**的，
      //      打开前组件 data.watermarkTemplate 是空数组 —— 旧实现先扫 Vue 再开菜单，
      //      所以永远读到 0 条（这就是「目录一直是空的」的真因）。----
      const menuVisible = () => {
        if (trigger?.getAttribute('aria-expanded') === 'true') return true;
        return [...document.querySelectorAll(menuSelectorForTrigger())].some((node) => isVisible(node));
      };
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
      }

      // ---- 4) 收集可扫的 Vue 容器（Vuex / 弹层组件 / 触发器祖先链）----
      const containers = [];
      try {
        const rootVm = document.querySelector('#box1')?.__vue__ || document.querySelector('[id^="box"]')?.__vue__;
        containers.push(
          rootVm?.$refs?.productMaterialModuleRef,
          rootVm?.$refs?.productMaterialModuleRef?.$data,
          rootVm?.$store?.state?.shopee?.productMaterialModule,
        );
        for (const element of document.querySelectorAll('.el-dialog,.el-dialog__wrapper,[role="dialog"],.el-popper,.el-dropdown-menu,.el-select-dropdown')) {
          for (let vm = element.__vue__ || element.__vueParentComponent?.proxy, depth = 0; vm && depth < 8; vm = vm.$parent, depth += 1) {
            containers.push(vm, vm?.$data);
          }
        }
        // 2026-10-01 实测：这个版本的详情页把水印模板挂在**组件实例**上（data.watermarkTemplate，
        // 从「添加水印」按钮的 Vue 祖先链往上 2 层就能拿到），Vuex 里根本没有。
        // 只扫 Vuex 会采到 0 条 —— 这正是「目录一直是空的」的另一半原因。
        let triggerVm = trigger?.__vue__ || trigger?.__vueParentComponent?.proxy || null;
        const ancestryKeys = [];
        for (let depth = 0; triggerVm && depth < 12; triggerVm = triggerVm.$parent, depth += 1) {
          containers.push(triggerVm, triggerVm?.$data);
          const data = triggerVm?.$data || {};
          ancestryKeys.push({
            depth,
            name: String(triggerVm?.$options?.name || triggerVm?.$options?._componentTag || '?'),
            arrays: Object.keys(data).filter((key) => Array.isArray(data[key])).slice(0, 12),
            hasWatermarkTemplate: Array.isArray(data.watermarkTemplate),
            watermarkTemplateLength: Array.isArray(data.watermarkTemplate) ? data.watermarkTemplate.length : null,
          });
        }
        out.range_probe.vue_ancestry = ancestryKeys;
        out.range_probe.had_trigger_node = Boolean(trigger);
        out.range_probe.container_count = containers.length;
      } catch (error) {
        out.errors.push(`vue containers: ${String((error && error.message) || error)}`);
      }

      const readVueItems = () => {
        // 关键：**不要碰到第一个数组就停**。Vue 组件上有大量叫 options 的数组
        // （Element UI 的下拉/表格都有），一停就被它们劫持，真正的水印模板反而读不到。
        // 做法：所有容器/所有候选键都扫一遍，逐条按别名归一化，坏数据自然被丢掉，再按 id 去重。
        const items = [];
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
                  items.push({
                    template_id: id,
                    name,
                    preview_url: clean(item?.preview_url ?? item?.previewUrl ?? item?.image_url ?? item?.imageUrl ?? item?.img_url ?? item?.imgUrl ?? item?.url),
                  });
                }
              }
            }
          }
        }
        return items;
      };

      const readDomRows = () => {
        const rows = [];
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
            if (!name || NON_TEMPLATE_ROWS.some((pattern) => pattern.test(name))) continue;
            const image = node.querySelector('img');
            rows.push({ template_id: id, name, preview_url: clean(image?.src) });
          }
        }
        return rows;
      };

      // ---- 5) 等懒加载：列表可能比菜单晚 1~3 秒才回来，轮询到有内容或超时为止 ----
      let vueItems = readVueItems();
      let domRows = readDomRows();
      let waitedMs = 0;
      const loadDeadlineMs = Math.max(6000, Number(menuTimeoutMs) || 6000) + 6000;
      while (!vueItems.length && !domRows.length && waitedMs < loadDeadlineMs) {
        await sleep(250);
        waitedMs += 250;
        vueItems = readVueItems();
        domRows = readDomRows();
      }
      out.range_probe.load_wait_ms = waitedMs;
      if (vueItems.length) out.catalog_source = 'vue';
      out.range_probe.vue_item_count = vueItems.length;
      out.range_probe.dom_row_count = domRows.length;

      // ---- 6) DOM 行：真实页面的 <li class="el-dropdown-menu__item"> 里**没有 id**
      //      （Vue 的 key 不落 DOM），只能按**名称精确相等**回查 Vue 列表拿 id。
      //      对不上的行只记诊断，绝不猜 id（执行器按 ID + 名称双匹配点菜单）。----
      const vueByName = new Map();
      for (const item of vueItems) if (!vueByName.has(item.name)) vueByName.set(item.name, item);
      const domItems = [];
      for (const row of domRows) {
        if (row.template_id) {
          domItems.push(row);
          continue;
        }
        const matched = vueByName.get(row.name);
        if (!matched) {
          out.range_probe.unmatched_dom_names = [...(out.range_probe.unmatched_dom_names || []), row.name].slice(0, 10);
          continue;
        }
        domItems.push({ ...matched, preview_url: matched.preview_url || row.preview_url });
      }
      if (domItems.length && out.catalog_source === 'dom') out.catalog_source = 'dom';
      if (domItems.length && out.catalog_source === 'vue') out.catalog_source = 'vue+dom';

      // ---- 7) 把菜单收回去（只收我们自己打开的；已打开的保持原样）----
      // 真实页面是 hover 型下拉：Element UI 靠 mouseleave 收菜单，Escape 对 hover 型不一定
      // 买账，click 只对 click 型皮肤有效。三步依次兜底，最后如实回报菜单有没有真的关上
      // （采集完把浮层留在页面上会挡住操作员后面的点击）。
      if (out.menu_opened_by_collector) {
        const rect = trigger ? trigger.getBoundingClientRect() : null;
        for (const eventName of ['mouseleave', 'mouseout']) {
          trigger?.dispatchEvent(new MouseEvent(eventName, {
            bubbles: true, cancelable: true, view: window,
            clientX: rect ? rect.left + 1 : 0, clientY: rect ? rect.top + 1 : 0,
          }));
        }
        await sleep(200);
        if (menuVisible()) {
          document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
          await sleep(150);
        }
        if (menuVisible() && trigger) trigger.click();
        await sleep(150);
      }
      out.menu_closed = !menuVisible();

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
