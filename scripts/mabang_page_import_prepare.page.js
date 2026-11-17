/**
 * 页面上下文函数：把 CNSC 导入要读、但默认藏在交互后面的数据展开出来（只展开，不保存）。
 *
 * 两件事：
 *   1. 店铺选择器 `#shops-wap`：店铺列表只有点开 `.shop-box` 才渲染；读出每个站点选中的
 *      店铺后把选择器收回（取消 / 再点触发点）。
 *   2. 每站「选择折扣活动」：CNSC 的折扣活动挂在 SKU 表头里，每个站点一列，点开弹窗
 *      才有活动行。按站点表头配对触发点，逐站读活动清单后关闭弹窗（不点任何活动）。
 *
 * 读到的结果写进 `window.__AI_LISTING_CNSC_IMPORT_STATE__`，供
 * `mabang_page_import_capture.js` 的 readTargetSiteStores 直接使用（页面导入与
 * 旧执行器共用同一份逻辑，不复制）。
 *
 * 用法（在 frame.evaluate 里 eval）：
 *   const state = await (0, eval)(`(${source})`)({});
 */

(async function prepareMabangImportState(options) {
  void options;
  const clean = (value) => String(value === undefined || value === null ? '' : value).replace(/\s+/g, ' ').trim();
  const visible = (node) => {
    if (!node || node.isConnected === false) return false;
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0'
      && rect.width > 0 && rect.height > 0;
  };
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const waitUntil = async (predicate, timeoutMs = 6000) => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const value = predicate();
      if (value) return value;
      await sleep(100);
    }
    return null;
  };
  const clickForText = (scope, text) => [...scope.querySelectorAll('button,[role="button"],a,label,.el-button,.el-link,.el-radio,span')]
    .filter(visible).find((node) => clean(node.textContent) === text || clean(node.textContent).startsWith(text));
  const promotionDialog = () => [...document.querySelectorAll('.el-dialog,.el-dialog__wrapper,[role="dialog"]')]
    .filter(visible).find((node) => clean(node.textContent).includes('\u9009\u62e9\u6298\u6263\u6d3b\u52a8'));

  const requiredManualSites = ['TW', 'PH', 'SG', 'TH', 'MY', 'MX', 'VN', 'BR'];
  const aliases = { '台湾': 'TW', '菲律宾': 'PH', '新加坡': 'SG', '泰国': 'TH', '马来西亚': 'MY', '墨西哥': 'MX', '越南': 'VN', '巴西': 'BR', SIP: 'SIP' };
  const state = { target_site_stores_loaded: false, target_site_stores: [] };
  const result = {
    picker: false,
    picker_opened: false,
    picker_closed: '',
    loaded: false,
    count: 0,
    activity_headers: 0,
    catalogs: [],
    errors: [],
  };
  const finish = () => {
    result.loaded = state.target_site_stores_loaded === true;
    result.count = state.target_site_stores.length;
    result.catalogs = state.target_site_stores.map((item) => ({
      site_code: clean(item.site_code),
      store_id: clean(item.store_id),
      store_name: clean(item.store_name),
      discount_catalog_loaded: item.discount_catalog_loaded === true,
      activities: Array.isArray(item.discount_catalog) ? item.discount_catalog.length : 0,
    }));
    // 采集器把这段诊断原样带进 capture_debug，失败时能看出卡在哪一步。
    state.prepare_debug = result;
    window.__AI_LISTING_CNSC_IMPORT_STATE__ = state;
    return result;
  };

  const picker = document.querySelector('#shops-wap');
  if (!picker) {
    result.picker = false;
    return finish();
  }
  result.picker = true;
  const pickerTrigger = picker.querySelector('.shop-box');
  const pickerOpen = () => Boolean(picker.querySelector('.feat-content'));
  if (pickerTrigger && !pickerOpen()) {
    pickerTrigger.click();
    result.picker_opened = Boolean(await waitUntil(pickerOpen, 5000));
  } else {
    result.picker_opened = pickerOpen();
  }

  const makeTargetStore = (siteCode, shop) => {
    const storeId = clean(shop && (shop.id || shop.shopId || shop.store_id || shop.storeId));
    const storeName = clean(shop && (shop.name || shop.store_name || shop.storeName));
    if (!storeId && !storeName) return null;
    return { site_code: siteCode, store_id: storeId, store_name: storeName, discount_catalog_loaded: false, discount_catalog: [] };
  };
  const shopVm = picker.__vue__ || (picker.__vueParentComponent && picker.__vueParentComponent.proxy);
  const vueSiteGroups = Array.isArray(shopVm && shopVm.shops) ? shopVm.shops : [];
  if (vueSiteGroups.length) {
    // 马帮每个店铺一个 checkbox，不是 radio；Vue 模型是选中店铺的唯一可信来源。
    for (const siteGroup of vueSiteGroups) {
      const siteCode = clean(siteGroup && siteGroup.site).toUpperCase();
      if (requiredManualSites.indexOf(siteCode) < 0) continue;
      const selected = (Array.isArray(siteGroup.shops) ? siteGroup.shops : [])
        .filter((shop) => shop && shop.isChecked === true);
      if (selected.length !== 1) continue;
      const target = makeTargetStore(siteCode, selected[0]);
      if (target) state.target_site_stores.push(target);
    }
  } else {
    // 老版本马帮没有暴露 Vue 实例时的 DOM 兜底。
    for (const siteGroup of [...picker.querySelectorAll('.site')]) {
      const titleText = clean((siteGroup.querySelector('.title-wap-checkbox') || siteGroup).textContent);
      const rawCode = clean(siteGroup.dataset && siteGroup.dataset.siteCode).toUpperCase();
      const siteCode = /^(TW|PH|SG|TH|MY|MX|VN|BR|SIP)$/.test(rawCode)
        ? rawCode
        : (Object.entries(aliases).find(([name]) => titleText.includes(name)) || [])[1] || '';
      if (requiredManualSites.indexOf(siteCode) < 0) continue;
      const selected = [...siteGroup.querySelectorAll('.shops-checkbox')]
        .filter((label) => {
          const input = label.querySelector('input[type="checkbox"]');
          return Boolean(input && input.checked);
        });
      if (selected.length !== 1) continue;
      const label = selected[0];
      const input = label.querySelector('input[type="checkbox"]');
      const target = makeTargetStore(siteCode, {
        id: (label.dataset && (label.dataset.storeId || label.dataset.shopId))
          || (input && input.dataset && (input.dataset.storeId || input.dataset.shopId))
          || (input && input.value),
        name: (label.dataset && (label.dataset.storeName || label.dataset.shopName)) || label.textContent,
      });
      if (target) state.target_site_stores.push(target);
    }
  }
  const targetCodes = state.target_site_stores.map((item) => item.site_code);
  state.target_site_stores_loaded = requiredManualSites.every(
    (code) => targetCodes.filter((value) => value === code).length === 1,
  ) && new Set(targetCodes).size === requiredManualSites.length;

  // 收回店铺选择器：优先点「取消」，没有就再点一次触发点。
  if (pickerOpenedOrOpen()) {
    const cancel = clickForText(picker, '\u53d6\u6d88');
    if (cancel) {
      cancel.click();
      result.picker_closed = 'cancel';
    } else if (pickerTrigger) {
      pickerTrigger.click();
      result.picker_closed = 'toggle';
    }
    await waitUntil(() => !pickerOpen(), 2000);
  }

  function pickerOpenedOrOpen() {
    return pickerOpen() || result.picker_opened;
  }

  // 每站折扣活动：把站点码表头与「折扣活动」子表头按列位置配对，避免依赖文档顺序。
  const visibleHeaders = [...document.querySelectorAll('.vxe-header--column')].filter(visible);
  const siteHeaderCodes = visibleHeaders.map((node) => {
    const text = clean(node.textContent).toUpperCase();
    return /^(TW|PH|SG|TH|MY|MX|VN|BR)$/.test(text) ? text : '';
  });
  const activityHeaders = visibleHeaders
    .map((node, index) => ({ node, index, text: clean(node.textContent) }))
    .filter((entry) => entry.text.includes('\u6298\u6263\u6d3b\u52a8'));
  result.activity_headers = activityHeaders.length;
  const findSiteForActivityHeader = (entry) => {
    for (let index = entry.index; index >= 0; index -= 1) {
      if (siteHeaderCodes[index]) return siteHeaderCodes[index];
    }
    for (let node = entry.node.parentElement; node; node = node.parentElement) {
      const code = clean(node.dataset && node.dataset.siteCode).toUpperCase();
      if (/^(TW|PH|SG|TH|MY|MX|VN|BR)$/.test(code)) return code;
    }
    return '';
  };
  const findTriggerIn = (header) => header.querySelector('.change-discount')
    || [...header.querySelectorAll('button,[role="button"],.el-button,.el-link,a,span')]
      .find((node) => visible(node) && (clean(node.textContent) === '\u9009\u62e9\u6d3b\u52a8'
        || clean(node.textContent).includes('\u9009\u62e9\u6298\u6263')));
  for (const entry of activityHeaders) {
    const siteCode = findSiteForActivityHeader(entry);
    if (!siteCode) continue;
    const store = state.target_site_stores.find((item) => item.site_code === siteCode);
    if (!store) continue;
    // 点开前先收表头当前显示的信息，弹窗打不开时也不至于全丢。
    const headerText = clean(entry.node.textContent).replace(/^\u6298\u6263\u6d3b\u52a8[\s\u00b7:：]*/, '');
    const headerPercent = headerText.match(/(\d+(?:\.\d+)?)\s*%/);
    if (headerPercent) store.discount_percent = Number(headerPercent[1]);
    const stripped = headerText
      .replace(/(\d+(?:\.\d+)?)\s*%(?:OFF)?/i, '')
      .replace(/\u9009\u62e9\u6d3b\u52a8|\u6279\u91cf\u4fee\u6539|\u6d3b\u52a8\u5f85\u914d\u7f6e/g, '')
      .trim();
    if (stripped) {
      store.discount_name = stripped;
      store.discount_label = stripped;
    }
    const trigger = findTriggerIn(entry.node);
    if (!trigger) {
      result.errors.push(`${siteCode}: 折扣活动触发点没找到`);
      continue;
    }
    let dialog = null;
    try {
      trigger.click();
      dialog = await waitUntil(promotionDialog, 8000);
      if (!dialog) {
        result.errors.push(`${siteCode}: 折扣活动弹窗没打开`);
        continue;
      }
      await waitUntil(() => ![...dialog.querySelectorAll('.el-loading-mask')].some(visible), 6000);
      const containers = [];
      for (let vm = dialog.__vue__ || (dialog.__vueParentComponent && dialog.__vueParentComponent.proxy), depth = 0;
        vm && depth < 8; vm = vm.$parent, depth += 1) {
        containers.push(vm, vm.$data);
      }
      const rawOptions = containers.flatMap((item) => ['tableData', 'activityList', 'promotionList', 'discountList']
        .flatMap((key) => (Array.isArray(item && item[key]) ? item[key] : [])));
      const seen = new Set();
      store.discount_catalog = rawOptions.map((item) => {
        const id = clean(item.discount_id || item.discountId || item.activity_id || item.activityId || item.id);
        const percent = Number(clean(item.discount_percent || item.discountPercent || item.discount_rate || item.discountRate).replace('%', ''));
        return {
          mabang_discount_id: id,
          name: clean(item.discount_name || item.discountName || item.activity_name || item.name),
          discount_percent: percent || '',
          selected: item.selected === true || item.checked === true,
        };
      }).filter((item) => item.mabang_discount_id && item.name && !seen.has(item.mabang_discount_id) && seen.add(item.mabang_discount_id));
      let selected = store.discount_catalog.filter((item) => item.selected);
      if (selected.length !== 1) selected = store.discount_catalog.filter((item) => entry.text.includes(item.name));
      store.discount_catalog_loaded = store.discount_catalog.length > 0 && selected.length === 1;
      if (store.discount_catalog_loaded) {
        store.mabang_discount_id = selected[0].mabang_discount_id;
        store.discount_name = selected[0].name;
        store.discount_label = selected[0].name;
        store.discount_percent = selected[0].discount_percent;
      }
    } catch (error) {
      result.errors.push(`${siteCode}: ${String((error && error.message) || error).slice(0, 120)}`);
    } finally {
      if (dialog) {
        const close = dialog.querySelector('.el-dialog__headerbtn,[aria-label="Close"],[aria-label="\u5173\u95ed"]')
          || clickForText(dialog, '\u53d6\u6d88')
          || clickForText(dialog, '\u5173\u95ed');
        if (close) close.click();
        await waitUntil(() => !promotionDialog(), 3000);
      }
    }
  }
  return finish();
})
