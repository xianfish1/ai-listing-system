/**
 * 页面上下文函数：把 CNSC / 跨境本土(3PF) 导入要读、但默认藏在交互后面的数据展开出来
 * （只展开，不保存）。
 *
 * 两件事：
 *   1. 店铺选择器 `#shops-wap`：店铺列表只有点开 `.shop-box` 才渲染；读出**已经勾选**的
 *      站点与店铺后把选择器收回。3PF 店通常只勾部分站点，所以判定标准是「选择器已完整
 *      渲染 + 至少勾了一个站点」，不再要求 8 个站点全勾。
 *   2. 每站「选择折扣活动」：站点价格表里每个站点一列，点开表头的折扣活动弹窗才有
 *      活动清单。按「同列上方表头」配对站点（支持 TW/PH 与 台湾/菲律宾 两种写法），
 *      逐站读活动清单后关闭弹窗（不点任何活动）。
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
  // 等一个数量稳定下来（打开选择器后站点分组是异步渲染的，早读会只拿到一半）。
  const waitStable = async (read, timeoutMs = 4000, stableMs = 350) => {
    const deadline = Date.now() + timeoutMs;
    let last = -1;
    let lastChange = Date.now();
    while (Date.now() < deadline) {
      const value = Number(read() || 0);
      if (value !== last) {
        last = value;
        lastChange = Date.now();
      } else if (Date.now() - lastChange >= stableMs) {
        return value;
      }
      await sleep(100);
    }
    return last < 0 ? 0 : last;
  };
  const clickForText = (scope, text) => [...scope.querySelectorAll('button,[role="button"],a,label,.el-button,.el-link,.el-radio,span')]
    .filter(visible).find((node) => clean(node.textContent) === text || clean(node.textContent).startsWith(text));
  const promotionDialog = () => {
    const dialogs = [...document.querySelectorAll('.el-dialog,.el-dialog__wrapper,[role="dialog"]')]
      .filter(visible);
    return dialogs.find((node) => clean(node.textContent).includes('\u9009\u62e9\u6298\u6263\u6d3b\u52a8'))
      || dialogs.find((node) => clean(node.textContent).includes('\u6298\u6263\u6d3b\u52a8')
        && Boolean(node.querySelector('.el-table,.vxe-table,.el-radio,.el-checkbox,.el-select-dropdown')))
      || dialogs.find((node) => clean(node.textContent).includes('\u9009\u62e9\u6d3b\u52a8'));
  };

  const requiredManualSites = ['TW', 'PH', 'SG', 'TH', 'MY', 'MX', 'VN', 'BR'];
  // 站点既可能是两字母码，也可能是中文名（3PF/部分 CNSC 店铺的页面表头用中文）。
  const SITE_ALIASES = {
    '台湾': 'TW', '台灣': 'TW',
    '菲律宾': 'PH', '菲律賓': 'PH',
    '新加坡': 'SG',
    '泰国': 'TH', '泰國': 'TH',
    '马来西亚': 'MY', '馬來西亞': 'MY',
    '墨西哥': 'MX',
    '越南': 'VN',
    '巴西': 'BR',
    SIP: 'SIP',
  };
  const siteCodeFromText = (value) => {
    const raw = clean(value);
    const upper = raw.toUpperCase();
    const code = upper.match(/(?:^|[^A-Z])(TW|PH|SG|TH|MY|MX|VN|BR|SIP)(?:[^A-Z]|$)/);
    if (code) return code[1];
    for (const [name, alias] of Object.entries(SITE_ALIASES)) {
      if (raw.includes(name)) return alias;
    }
    return '';
  };

  const state = { target_site_stores_loaded: false, target_site_stores: [] };
  const result = {
    picker: false,
    picker_opened: false,
    picker_closed: '',
    parsed: '',
    sites_seen: 0,
    loaded: false,
    count: 0,
    activity_headers: 0,
    catalogs: [],
    errors: [],
  };
  const finish = () => {
    result.loaded = state.target_site_stores_loaded === true;
    result.count = state.target_site_stores.length;
    result.activity_headers = Number(result.activity_headers || 0);
    result.catalogs = state.target_site_stores.map((item) => ({
      site_code: clean(item.site_code),
      store_id: clean(item.store_id),
      store_name: clean(item.store_name),
      mabang_discount_id: clean(item.mabang_discount_id),
      discount_name: clean(item.discount_name),
      discount_percent: item.discount_percent || '',
      discount_catalog_loaded: item.discount_catalog_loaded === true,
      activities: Array.isArray(item.discount_catalog) ? item.discount_catalog.length : 0,
    }));
    // 采集器把这段诊断原样带进 capture_debug，失败时能看出卡在哪一步。
    state.prepare_debug = result;
    window.__AI_LISTING_CNSC_IMPORT_STATE__ = state;
    return result;
  };

  const picker = document.querySelector('#shops-wap') || document.querySelector('.shops-wap');
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
  const isShopChecked = (shop) => Boolean(
    shop && (shop.isChecked === true || shop.checked === true || shop.selected === true),
  );
  // 分组是异步渲染的：每次现读（Vue 可能整个替换 shops 数组），等数量稳定再采。
  const readVueGroups = () => {
    const vm = picker.__vue__ || (picker.__vueParentComponent && picker.__vueParentComponent.proxy);
    return Array.isArray(vm && vm.shops) ? vm.shops : [];
  };
  const readDomGroups = () => [...picker.querySelectorAll('.site')];
  const seenSites = new Set();
  let parsedVia = '';
  let parsedGroups = 0;
  if (readVueGroups().length) {
    parsedVia = 'vue';
    await waitStable(() => readVueGroups().length);
    const vueSiteGroups = readVueGroups();
    parsedGroups = vueSiteGroups.length;
    // 马帮每个店铺一个 checkbox，不是 radio；Vue 模型是选中店铺的唯一可信来源。
    for (const siteGroup of vueSiteGroups) {
      const siteCode = siteCodeFromText(siteGroup && siteGroup.site);
      if (requiredManualSites.indexOf(siteCode) < 0) continue;
      seenSites.add(siteCode);
      const shops = Array.isArray(siteGroup && siteGroup.shops) ? siteGroup.shops : [];
      const selected = shops.filter(isShopChecked);
      if (!selected.length) continue;
      if (selected.length > 1) {
        result.errors.push(`${siteCode}: 勾选了 ${selected.length} 家店铺，取第一家`);
      }
      const target = makeTargetStore(siteCode, selected[0]);
      if (target) state.target_site_stores.push(target);
    }
  } else if (readDomGroups().length) {
    parsedVia = 'dom';
    await waitStable(() => readDomGroups().length);
    const domSiteGroups = readDomGroups();
    parsedGroups = domSiteGroups.length;
    // 老版本马帮没有暴露 Vue 实例时的 DOM 兜底。
    for (const siteGroup of domSiteGroups) {
      const titleText = clean((siteGroup.querySelector('.title-wap-checkbox') || siteGroup).textContent);
      const siteCode = siteCodeFromText(siteGroup.dataset && siteGroup.dataset.siteCode)
        || siteCodeFromText(titleText);
      if (requiredManualSites.indexOf(siteCode) < 0) continue;
      seenSites.add(siteCode);
      const allChecked = [...siteGroup.querySelectorAll('input[type="checkbox"],input[type="radio"]')]
        .filter((input) => input.checked);
      // 站点勾选是 checkbox，店铺选择是 radio：有 radio 时只认 radio，别把站点头当成店铺。
      const checkedRadios = allChecked.filter((input) => (
        input.type === 'radio' || (input.getAttribute && input.getAttribute('type') === 'radio')
      ));
      const checkedInputs = checkedRadios.length ? checkedRadios : allChecked;
      if (!checkedInputs.length) continue;
      if (checkedInputs.length > 1) {
        result.errors.push(`${siteCode}: 勾选了 ${checkedInputs.length} 家店铺，取第一家`);
      }
      const input = checkedInputs[0];
      const label = input.closest('label,.el-checkbox,.el-radio,.shops-checkbox') || input.parentElement;
      const target = makeTargetStore(siteCode, {
        id: (label && label.dataset && (label.dataset.storeId || label.dataset.shopId))
          || (input.dataset && (input.dataset.storeId || input.dataset.shopId))
          || input.value,
        name: (label && label.dataset && (label.dataset.storeName || label.dataset.shopName)) || (label && label.textContent),
      });
      if (target) state.target_site_stores.push(target);
    }
  }
  result.parsed = parsedVia;
  result.sites_seen = seenSites.size;
  // 「已加载」= 选择器结构能识别 + 至少勾了一个店铺。
  // 3PF 店只勾部分站点时也必须成立（旧规则要求 8 个全勾，会把这些店铺/活动整批丢掉）。
  state.target_site_stores_loaded = parsedGroups >= 1 && state.target_site_stores.length >= 1;
  if (parsedGroups < 1) {
    result.errors.push(`店铺选择器未渲染出站点分组（picker_opened=${result.picker_opened}）`);
  } else if (!state.target_site_stores.length) {
    result.errors.push(`已渲染 ${seenSites.size} 个站点分组，但没有勾选任何店铺`);
  }

  // 收回店铺选择器：优先点「取消」，没有就再点一次触发点。
  if (pickerOpen() || result.picker_opened) {
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

  // 每站折扣活动：按「同列上方表头」配对站点（表头可能是 TW/PH，也可能是台湾/菲律宾）。
  const headerCells = [...document.querySelectorAll('.vxe-header--column')]
    .filter(visible)
    .map((node) => ({
      node,
      text: clean(node.textContent),
      code: siteCodeFromText(node.textContent),
      rect: node.getBoundingClientRect(),
    }));
  const activityHeaders = headerCells.filter((entry) => entry.text.includes('\u6298\u6263\u6d3b\u52a8')
    || entry.text.includes('\u9009\u62e9\u6d3b\u52a8')
    || Boolean(entry.node.querySelector('.change-discount')));
  result.activity_headers = activityHeaders.length;
  const findSiteForActivityHeader = (entry) => {
    // 1) 自家或祖先带 data-site-code
    for (let node = entry.node; node; node = node.parentElement) {
      const code = siteCodeFromText(node.dataset && node.dataset.siteCode);
      if (code) return code;
    }
    // 2) 同一列上方最近的站点表头（分组表头在上一层，横向范围有重叠）
    const above = headerCells
      .filter((cell) => cell.code && cell !== entry
        && cell.rect.left < entry.rect.right && cell.rect.right > entry.rect.left
        && cell.rect.top < entry.rect.top)
      .sort((left, right) => (entry.rect.top - left.rect.top) - (entry.rect.top - right.rect.top));
    if (above.length) return above[0].code;
    // 3) 文档顺序里左侧最近的站点表头
    for (let index = headerCells.indexOf(entry); index >= 0; index -= 1) {
      if (headerCells[index].code) return headerCells[index].code;
    }
    return '';
  };
  const findTriggerIn = (header) => {
    const direct = header.querySelector('.change-discount');
    if (direct) return direct;
    // 表头可能被拆成分组单元格：触发点在同列/相邻列的 .change-discount 里。
    const row = header.closest('.vxe-header--row') || header.parentElement;
    if (row) {
      const candidates = [...row.querySelectorAll('.change-discount')].filter(visible);
      if (candidates.length) {
        const headerRect = header.getBoundingClientRect();
        const overlapping = candidates.find((node) => {
          const rect = node.getBoundingClientRect();
          return rect.left < headerRect.right && rect.right > headerRect.left;
        });
        if (overlapping) return overlapping;
        if (candidates.length === 1) return candidates[0];
      }
    }
    return [...header.querySelectorAll('button,[role="button"],.el-button,.el-link,a,span')]
      .find((node) => visible(node) && (clean(node.textContent) === '\u9009\u62e9\u6d3b\u52a8'
        || clean(node.textContent).includes('\u9009\u62e9\u6298\u6263')));
  };
  for (const entry of activityHeaders) {
    const siteCode = findSiteForActivityHeader(entry);
    if (!siteCode) {
      result.errors.push(`折扣活动列未识别出站点：${entry.text.slice(0, 40)}`);
      continue;
    }
    const store = state.target_site_stores.find((item) => item.site_code === siteCode);
    if (!store) continue;
    // 点开前先收表头当前显示的信息，弹窗打不开时也不至于全丢。
    const headerText = clean(entry.node.textContent).replace(/^\u6298\u6263\u6d3b\u52a8[\s\u00b7:：]*/, '');
    // 表头显示的活动名保留百分比（PH40%），去掉百分比的 stripped 只用于兜底展示。
    const cleanedHeaderName = clean(headerText);
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
      // 活动列表是异步拉取的：弹窗先出现、表格先显示「暂无内容」，几百毫秒后行才渲染。
      // 早读会把「有 3 条活动」误判成空清单（真机实测）。
      const activityTableRows = () => {
        const vueRows = [...dialog.querySelectorAll('.vxe-table')].flatMap((table) => {
          const vm = table.__vue__ || (table.__vueParentComponent && table.__vueParentComponent.proxy);
          return Array.isArray(vm && vm.tableData) ? vm.tableData : [];
        });
        if (vueRows.length) return vueRows;
        return [...dialog.querySelectorAll('.vxe-body--row,.el-table__row')]
          .filter(visible)
          .map((node) => ({ _row_text: clean(node.textContent) }));
      };
      await waitUntil(() => activityTableRows().length > 0, 10000);
      const containers = [];
      for (let vm = dialog.__vue__ || (dialog.__vueParentComponent && dialog.__vueParentComponent.proxy), depth = 0;
        vm && depth < 8; vm = vm.$parent, depth += 1) {
        containers.push(vm, vm.$data);
      }
      const rawOptions = [...activityTableRows(), ...containers.flatMap((item) => ['tableData', 'activityList', 'promotionList', 'discountList']
        .flatMap((key) => (Array.isArray(item && item[key]) ? item[key] : [])))];
      const seen = new Set();
      const percentOf = (item, name) => {
        const explicit = Number(clean(item.discount_percent || item.discountPercent || item.discount_rate || item.discountRate).replace('%', ''));
        if (explicit) return explicit;
        const matched = String(name || '').match(/(\d+(?:\.\d+)?)\s*%/);
        return matched ? Number(matched[1]) : '';
      };
      store.discount_catalog = rawOptions.map((item) => {
        const id = clean(item.discount_id || item.discountId || item.activity_id || item.activityId || item.id);
        const name = clean(item.discount_name || item.discountName || item.activity_name || item.activityName || item.name);
        return {
          mabang_discount_id: id,
          name,
          discount_percent: percentOf(item, name),
          start_at: clean(item.start_time || item.startTime || item.start_at || item.startAt),
          end_at: clean(item.end_time || item.endTime || item.end_at || item.endAt),
          selected: item.selected === true || item.checked === true || item.is_selected === true,
        };
      }).filter((item) => item.mabang_discount_id && item.name && !seen.has(item.mabang_discount_id) && seen.add(item.mabang_discount_id));
      // Vue 模型读不到（弹窗是子组件渲染/列表在 DOM 里）时，按表格行/下拉项兜底。
      if (!store.discount_catalog.length) {
        const pushOption = (id, name, percent, selected) => {
          const optionId = clean(id);
          const optionName = clean(name);
          if (!optionId || !optionName || seen.has(optionId)) return;
          seen.add(optionId);
          store.discount_catalog.push({
            mabang_discount_id: optionId,
            name: optionName,
            discount_percent: Number(percent) || '',
            selected: selected === true,
          });
        };
        const rows = [...dialog.querySelectorAll('.el-table__row,.vxe-body--row,tr')].filter(visible);
        for (const rowNode of rows) {
          const text = clean(rowNode.textContent);
          if (!text) continue;
          const input = rowNode.querySelector('input[type="radio"],input[type="checkbox"],input');
          const id = rowNode.dataset?.discountId || rowNode.dataset?.activityId || rowNode.dataset?.id
            || input?.value || '';
          const name = clean(rowNode.querySelector('.cell,td')?.textContent) || text.slice(0, 40);
          const percent = (text.match(/(\d+(?:\.\d+)?)\s*%/) || [])[1] || '';
          pushOption(id, name, percent, Boolean(input?.checked) || /is-current|selected/i.test(String(rowNode.className)));
        }
        const options = [...document.querySelectorAll('.el-select-dropdown__item')].filter(visible);
        for (const option of options) {
          const text = clean(option.textContent);
          if (!text) continue;
          const id = option.dataset?.value || option.dataset?.id || option.value || '';
          const percent = (text.match(/(\d+(?:\.\d+)?)\s*%/) || [])[1] || '';
          pushOption(id, text, percent, /selected|is-selected/.test(String(option.className)));
        }
        if (!store.discount_catalog.length) {
          const toast = clean(dialog.textContent).slice(0, 200);
          result.errors.push(`${siteCode}: 弹窗里没有可解析的活动行${toast ? `（${toast}）` : ''}`);
        }
      }
      // 清单读到了就算 loaded（3PF 经常还没选活动，但运营需要在模板里看到可选项）。
      store.discount_catalog_loaded = store.discount_catalog.length > 0;
      // 弹窗行没有 selected 标记（真机 vxe 表只有 discount_id/名称/有效期），按表头
      // 当前显示的活动名回找：先精确匹配（PH40% 不能匹配到 40%），再唯一包含匹配。
      let selected = store.discount_catalog.filter((item) => item.selected);
      if (selected.length !== 1 && cleanedHeaderName) {
        const exact = store.discount_catalog.filter((item) => clean(item.name) === cleanedHeaderName);
        const partial = store.discount_catalog.filter((item) => cleanedHeaderName.includes(clean(item.name)));
        selected = exact.length ? exact : (partial.length === 1 ? partial : []);
      }
      if (selected.length === 1) {
        store.mabang_discount_id = selected[0].mabang_discount_id;
        store.discount_name = selected[0].name;
        store.discount_label = selected[0].name;
        store.discount_percent = selected[0].discount_percent;
        store.discount_start_at = selected[0].start_at || '';
        store.discount_end_at = selected[0].end_at || '';
      } else if (!store.discount_catalog.length) {
        result.errors.push(`${siteCode}: 折扣活动清单为空`);
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
