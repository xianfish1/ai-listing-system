(inputPlan) => {
  const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
  const firstDefined = (...values) => values.find((value) => value !== undefined && value !== null);
  const firstValue = (...values) => values.find((value) => {
    if (value === undefined || value === null) return false;
    if (Array.isArray(value)) return value.some((item) => clean(item));
    return typeof value !== 'object' && Boolean(clean(value));
  });
  const firstArray = (...values) => values.find((value) => Array.isArray(value) && value.length) || [];
  const firstObject = (...values) => values.find((value) => (
    value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length
  )) || {};
  const optionalBoolean = (value) => {
    if (value === true || value === 1 || clean(value).toLowerCase() === 'true' || clean(value) === '1') return true;
    if (value === false || value === 0 || clean(value).toLowerCase() === 'false' || clean(value) === '0') return false;
    return null;
  };
  const asList = (value) => {
    const values = Array.isArray(value) ? value : [value];
    return [...new Set(values.map(clean).filter(Boolean))];
  };
  const pick = (object, keys) => {
    for (const key of keys) {
      if (own(object, key) && object[key] !== undefined && object[key] !== null && clean(object[key])) return object[key];
    }
    return '';
  };
  const findDetailVm = () => {
    for (const selector of ['#box1', '#box3', '#box6']) {
      const element = document.querySelector(selector);
      for (let vm = element?.__vue__, depth = 0; vm && depth < 12; vm = vm.$parent, depth += 1) {
        if (vm.$refs?.storeCategory) return vm;
      }
    }
    for (const element of document.querySelectorAll('*')) {
      for (let vm = element.__vue__, depth = 0; vm && depth < 12; vm = vm.$parent, depth += 1) {
        if (vm.$refs?.storeCategory) return vm;
      }
    }
    return null;
  };
  const normalizeOptions = (attribute) => {
    const groups = Array.isArray(attribute?.values) ? attribute.values : [];
    const candidates = groups.flatMap((group) => Array.isArray(group?.options) ? group.options : [group]);
    const legacyOptions = Array.isArray(attribute?.attribute_value_list) ? attribute.attribute_value_list : [];
    const seen = new Set();
    return [...candidates, ...legacyOptions].map((option) => ({
      value: clean(option?.value ?? option?.value_id ?? option?.id),
      label: clean(option?.label ?? option?.name_cn ?? option?.name ?? option?.value),
    })).filter((option) => option.value && !seen.has(option.value) && seen.add(option.value));
  };
  const attributeInputType = (attribute) => {
    // Mabang marks each attribute's control kind in attribute_info.input_type:
    // 1/2 = 单选枚举, 3 = 纯文本, 5 = 多值。0/空 = 未标注。
    let raw = attribute?.attribute_info;
    if (typeof raw === 'string') {
      try { raw = JSON.parse(raw); } catch (_e) { raw = {}; }
    }
    const value = clean((raw && (raw.input_type ?? raw.inputType)) ?? '');
    if (/^\d+$/.test(value)) return value;
    return '';
  };
  const selectedValues = (attribute) => asList(attribute?.value);
  const attributeType = (attribute) => {
    const inputType = attributeInputType(attribute);
    const multiValue = Array.isArray(attribute?.value);
    const options = normalizeOptions(attribute);
    // input_type 3 = 纯文本（如“尺寸 100-150cm”）→ 必须按 text 保存，
    // 否则维护时变成带空选项的下拉框、值回显不出来。
    if (inputType === '3') return 'text';
    // 多值：带枚举或已有多个自填值 → multi_select；单选带枚举 → single_select。
    // 其余（无任何枚举选项的自由文本，如“条件 Brand New”“衣领类型 Round Neck”
    // 的自填、或未选值的“型号”）一律按 text 保存，绝不建成空下拉框。
    if (multiValue) return (options.length || selectedValues(attribute).length > 1) ? 'multi_select' : 'text';
    if (options.length) return 'single_select';
    return 'text';
  };
  const normalizeCapturedOptions = (attribute) => {
    const type = attributeType(attribute);
    if (!type.endsWith('select')) return [];
    const options = normalizeOptions(attribute);
    // 枚举里不含已选“自填/自定义”值时，把该值并入选项，保证维护下拉框能回显。
    const selected = selectedValues(attribute);
    for (const value of selected) {
      const match = options.some((option) => option.value === value || option.label === value);
      if (!match && value) options.push({ value, label: value });
    }
    return options;
  };
  const readCategoryAttributes = (storeCategory) => {
    const attributes = Array.isArray(storeCategory?.ProductAttributeInfo?.attributesNew)
      ? storeCategory.ProductAttributeInfo.attributesNew : [];
    return attributes.map((attribute) => ({
      field_id: clean(attribute?.attribute_id ?? attribute?.id),
      label: clean(attribute?.name_cn ?? attribute?.name ?? attribute?.attribute_name),
      type: attributeType(attribute),
      required: String(attribute?.mandatory ?? attribute?.required ?? '') === '1' || attribute?.required === true,
      options: normalizeCapturedOptions(attribute),
      selected_value: Array.isArray(attribute?.value) ? asList(attribute.value) : clean(attribute?.value),
    })).filter((attribute) => attribute.field_id);
  };
  const columnLabel = (column) => clean(column?.title ?? column?.label ?? column?.text)
    .replace(/^[*＊\s]+/, '');
  const columnField = (column) => clean(column?.field ?? column?.property ?? column?.key);
  const flattenColumns = (columns) => (Array.isArray(columns) ? columns : []).flatMap((column) => [
    column,
    ...flattenColumns(column?.children),
  ]);
  const siteCodeFromText = (value) => clean(value).toUpperCase().match(/(?:^|\s)(TW|PH|SG|TH|MY|MX|VN|BR|SIP)(?:\s|$)/)?.[1] || '';
  const siteFromColumnField = (field) => {
    const number = Number(String(field).match(/^col_(\d+)$/)?.[1]);
    if (!Number.isFinite(number) || number < 17) return '';
    const index = Math.floor((number - 17) / 5);
    return ['TW', 'PH', 'SG', 'TH', 'MY', 'MX', 'VN', 'BR'][index] || '';
  };
  const resolveSitePriceFields = (table) => {
    const rows = Array.isArray(table?.tableData) ? table.tableData : [];
    const result = {};
    const visit = (columns, inheritedSite = '') => {
      for (const column of Array.isArray(columns) ? columns : []) {
        const label = columnLabel(column);
        const siteCode = siteCodeFromText(label) || inheritedSite || siteFromColumnField(columnField(column));
        if (Array.isArray(column?.children) && column.children.length) {
          visit(column.children, siteCode);
          continue;
        }
        const field = columnField(column);
        if (!siteCode || siteCode === 'SIP' || !field) continue;
        const compact = label.replace(/\s+/g, '');
        const role = /折扣价|折扣價|折扣活动|折扣活動|售价|售價|sale|selling/i.test(compact)
          ? 'sale_price'
          : /^(?:价格|價格|原价|原價|划线价|劃線價)$|original|list/i.test(compact)
            ? 'original_price'
            : '';
        if (role) (result[siteCode] ||= {})[role] = field;
      }
    };
    const vueColumns = typeof table?.getColumns === 'function' ? table.getColumns() : null;
    visit((Array.isArray(vueColumns) && vueColumns.length ? vueColumns : null) || table?.columns || tableColumns(table));
    return result;
  };
  const tableColumns = (table) => {
    const vueColumns = flattenColumns(
      (typeof table?.getColumns === 'function' ? table.getColumns() : null)
      || table?.visibleColumn
      || table?.tableColumn
      || table?.columns
      || [],
    );
    const domTable = [...document.querySelectorAll('.vxe-table')]
      .find((element) => (element.__vue__ || element.__vueParentComponent?.proxy) === table);
    const domColumns = [...(domTable?.querySelectorAll?.('.vxe-header--column') || [])].map((element) => {
      const className = clean(element.className);
      return {
        title: clean(element.textContent),
        field: clean(element.getAttribute?.('colid')) || className.split(' ').find((name) => /^col_\d+$/.test(name)) || '',
      };
    });
    return [...vueColumns, ...domColumns];
  };
  const renderedTable = (table) => [...document.querySelectorAll('.vxe-table')]
    .find((element) => (element.__vue__ || element.__vueParentComponent?.proxy) === table);
  const renderedRowValues = (table, rowIndex) => {
    const row = renderedTable(table)?.querySelectorAll?.('.vxe-body--row')?.[rowIndex];
    const values = {};
    for (const cell of row?.querySelectorAll('.vxe-body--column') || []) {
      const className = clean(cell.className);
      const field = clean(cell.getAttribute?.('colid')) || className.split(' ').find((name) => /^col_\d+$/.test(name));
      if (!field) continue;
      const input = cell.querySelector?.('input');
      values[field] = clean(input?.value || cell.textContent);
    }
    return values;
  };
  const renderedValue = (values, field) => {
    if (!field) return '';
    const match = String(field).match(/^col_(\d+)$/);
    const adjacent = match ? [`col_${Number(match[1]) + 1}`, `col_${Number(match[1]) - 1}`] : [];
    return firstValue(values[field], ...adjacent.map((key) => values[key])) || '';
  };
  const numericText = (value) => clean(value).match(/-?\d+(?:\.\d+)?/)?.[0] || '';
  const resolveColumnFields = (table) => {
    const rows = Array.isArray(table?.tableData) ? table.tableData : [];
    const fields = {};
    for (const column of tableColumns(table)) {
      const label = columnLabel(column);
      let field = columnField(column);
      if (!label || !field) continue;
      if (!rows.some((row) => own(row, field))) {
        const match = field.match(/^col_(\d+)$/);
        const adjacent = match ? `col_${Number(match[1]) + 1}` : '';
        if (adjacent && rows.some((row) => own(row, adjacent))) field = adjacent;
      }
      if (!fields[label]) fields[label] = field;
    }
    return fields;
  };
  const semanticValue = (row, fields, labels, fallbackKeys = [], rendered = {}) => {
    for (const label of labels) {
      const field = fields[label];
      if (field && own(row, field)) {
        const value = row[field];
        if (clean(value)) return value;
      }
    }
    for (const label of labels) {
      const field = fields[label];
      const value = renderedValue(rendered, field);
      if (clean(value)) return value;
    }
    return pick(row, fallbackKeys);
  };
  const findTransactionTable = (detailVm) => {
    const transactionRef = detailVm?.$refs?.transactionInformationModuleRef;
    const transactionTable = transactionRef?.$refs?.tradeInfoTableRef;
    if (Array.isArray(transactionTable?.tableData)) return transactionTable;
    return [...document.querySelectorAll('.vxe-table')].map((element) => element.__vue__)
      .find((vm) => Array.isArray(vm?.tableData) && vm.tableData.length);
  };
  const readSeparateSitePriceRows = (globalTable) => {
    const tables = [...document.querySelectorAll('.vxe-table')]
      .map((element) => element.__vue__ || element.__vueParentComponent?.proxy)
      .filter((table) => table && table !== globalTable && Array.isArray(table.tableData));
    const result = [];
    for (const table of tables) {
      const priceFields = resolveSitePriceFields(table);
      if (!Object.keys(priceFields).length) continue;
      const fields = resolveColumnFields(table);
      for (const [rowIndex, row] of table.tableData.entries()) {
        const rendered = renderedRowValues(table, rowIndex);
        const sitePrices = {};
        for (const [siteCode, definitions] of Object.entries(priceFields)) {
          const originalPrice = firstValue(row[definitions.original_price], renderedValue(rendered, definitions.original_price)) || '';
          const salePrice = firstValue(row[definitions.sale_price], renderedValue(rendered, definitions.sale_price)) || '';
          const original = own(row, definitions.original_price) ? originalPrice : numericText(originalPrice);
          const sale = own(row, definitions.sale_price) ? salePrice : numericText(salePrice);
          if (clean(original) || clean(sale)) sitePrices[siteCode] = { original_price: original, sale_price: sale };
        }
        result.push({
          seller_sku: clean(semanticValue(row, fields, ['Seller SKU', '商品SKU', 'SKU'], ['seller_sku', 'sellerSku', 'sku_code', 'sku'], rendered)),
          color: clean(semanticValue(row, fields, ['颜色', '颜色属性'], ['color', 'colour', 'Color', '颜色'], rendered)),
          size: clean(semanticValue(row, fields, ['尺码', '尺寸'], ['size', 'Size', '尺码'], rendered)),
          site_prices: sitePrices,
        });
      }
    }
    const unique = new Map();
    for (const item of result) {
      const key = item.seller_sku || `${item.color}\u0000${item.size}`;
      if (!unique.has(key) || Object.keys(item.site_prices).length > Object.keys(unique.get(key).site_prices).length) unique.set(key, item);
    }
    return [...unique.values()];
  };
  const readSkuRows = (detailVm) => {
    const table = findTransactionTable(detailVm);
    const fields = resolveColumnFields(table);
    const sitePriceFields = resolveSitePriceFields(table);
    const separateSiteRows = readSeparateSitePriceRows(table);
    const cnscGrid = ['全球商品价格', '泰国仓库存', '广州仓库存', '菲律宾仓库存']
      .every((label) => Boolean(fields[label]));
    return (table?.tableData || []).filter((row) => row && typeof row === 'object').map((row, rowIndex) => {
      const rendered = renderedRowValues(table, rowIndex);
      const base = {
        color: clean(semanticValue(row, fields, ['颜色', '颜色属性'], ['color', 'colour', 'Color', '颜色'], rendered)),
        size: clean(semanticValue(row, fields, ['尺码', '尺寸'], ['size', 'Size', '尺码'], rendered)),
        stock: pick(row, ['stock', 'quantity', 'inventory', '库存']),
        weight_kg: pick(row, ['package_weight', 'weight_kg', 'weightKg', 'weight', '重量']),
        package_length_cm: pick(row, ['package_length', 'packageLength', 'length', '包裹长度']),
        package_width_cm: pick(row, ['package_width', 'packageWidth', 'width', '包裹宽度']),
        package_height_cm: pick(row, ['package_height', 'packageHeight', 'height', '包裹高度']),
        normal_price: pick(row, ['price', 'normal_price', 'original_price']),
        sale_price: pick(row, ['discount_price', 'discountPrice', 'sale_price', 'promotion_price']),
      };
      if (!cnscGrid) return base;
      const sitePrices = {};
      for (const [siteCode, priceFields] of Object.entries(sitePriceFields)) {
        const originalPrice = priceFields.original_price && own(row, priceFields.original_price) ? row[priceFields.original_price] : '';
        const salePrice = priceFields.sale_price && own(row, priceFields.sale_price) ? row[priceFields.sale_price] : '';
        if (clean(originalPrice) || clean(salePrice)) sitePrices[siteCode] = { original_price: originalPrice, sale_price: salePrice };
      }
      for (const item of Array.isArray(row.diff_info) ? row.diff_info : []) {
        const siteCode = clean(item?.site).toUpperCase();
        if (!/^(TW|PH|SG|TH|MY|MX|VN|BR)$/.test(siteCode)) continue;
        const originalPrice = firstValue(item?.price, item?.originalPrice) || '';
        const salePrice = firstValue(item?.discountPrice, item?.salePrice) || '';
        if (clean(originalPrice) || clean(salePrice)) {
          sitePrices[siteCode] = { original_price: originalPrice, sale_price: salePrice };
        }
      }
      const sellerSku = clean(semanticValue(row, fields, ['Seller SKU', '商品SKU', 'SKU'], ['seller_sku', 'sellerSku', 'sku_code', 'sku'], rendered));
      const matches = separateSiteRows.filter((candidate) => (
        sellerSku && candidate.seller_sku
          ? candidate.seller_sku === sellerSku
          : candidate.color === base.color && candidate.size === base.size
      ));
      if (matches.length === 1) Object.assign(sitePrices, matches[0].site_prices);
      return {
        ...base,
        seller_sku: sellerSku,
        global_price_cny: semanticValue(row, fields, ['全球商品价格'], ['global_price_cny', 'globalPriceCny', 'globalPrice', 'global_product_price', 'globalProductPrice'], rendered),
        warehouse_stocks: {
          TH: semanticValue(row, fields, ['泰国仓库存'], ['TH', 'TH_stock', 'THStock', 'thStock', 'warehouseTh', 'warehouseTH', 'thailandStock', 'thStockNum'], rendered),
          CN_GZ: semanticValue(row, fields, ['广州仓库存'], ['CN_GZ', 'guangzhou', 'guangzhouStock', 'cnGzStock', 'warehouseCnGz', 'warehouseCNGZ', 'CN_GZ_stock', 'gzStock'], rendered),
          PH: semanticValue(row, fields, ['菲律宾仓库存'], ['PH', 'PH_stock', 'PHStock', 'phStock', 'warehousePh', 'warehousePH', 'philippineStock', 'phStockNum'], rendered),
        },
        site_prices: sitePrices,
      };
    });
  };
  const readListingMode = (detailVm) => {
    const table = findTransactionTable(detailVm);
    const labels = new Set(tableColumns(table).map(columnLabel).filter(Boolean));
    const requiredHeaders = ['全球商品价格', '泰国仓库存', '广州仓库存', '菲律宾仓库存'];
    const matchedHeaders = requiredHeaders.filter((header) => labels.has(header));
    const fallbackHref = `${clean(location.origin)}${clean(location.pathname)}`;
    const locationUrl = new URL(clean(location.href) || fallbackHref, clean(location.origin) || undefined);
    let hashParams = new URL(location.origin).searchParams;
    try {
      const hashTarget = clean(location.hash || locationUrl.hash).replace(/^#/, '');
      if (hashTarget) hashParams = new URL(hashTarget, location.origin).searchParams;
    } catch (_error) {
      hashParams = new URL(location.origin).searchParams;
    }
    const globalParam = locationUrl.searchParams.get('global') === '4' || hashParams.get('global') === '4';
    return {
      listing_mode: globalParam && matchedHeaders.length === requiredHeaders.length ? 'cnsc_global' : 'site',
      listing_mode_evidence: { global_param: globalParam, matched_headers: matchedHeaders },
    };
  };
  const readTargetSiteStores = () => {
    const prepared = typeof window !== 'undefined' && window.__AI_LISTING_CNSC_IMPORT_STATE__;
    if (prepared && own(prepared, 'target_site_stores_loaded') && Array.isArray(prepared.target_site_stores)) {
      return {
        loaded: prepared.target_site_stores_loaded === true,
        items: prepared.target_site_stores.filter((item) => clean(item?.site_code).toUpperCase() !== 'SIP'),
      };
    }
    const picker = document.querySelector('#shops-wap');
    if (!picker) return { loaded: false, items: [] };
    const requiredManualSites = new Set(['TW', 'PH', 'SG', 'TH', 'MY', 'MX', 'VN', 'BR']);
    const checkboxes = [...picker.querySelectorAll('input[type="checkbox"]')];
    if (!checkboxes.length) return { loaded: false, items: [] };
    const seen = new Set();
    const discoveredManualSites = [];
    const items = checkboxes.filter((input) => input.checked).map((input) => {
      const siteGroup = input.closest('[data-site-code],.shop-country-item,.shop-site-item,.site-shop-row,tr,.el-checkbox-group') || input.parentElement;
      const siteText = clean(siteGroup?.textContent);
      const siteCode = clean(siteGroup?.dataset?.siteCode || input?.dataset?.siteCode || input.value || siteCodeFromText(siteText)).toUpperCase();
      if (siteCode === 'SIP') return null;
      discoveredManualSites.push(siteCode);
      if (!siteCode) return null;
      const selectedRadios = [...(siteGroup?.querySelectorAll?.('input[type="radio"]') || [])].filter((radio) => radio.checked);
      if (selectedRadios.length !== 1) return null;
      const radio = selectedRadios[0];
      const container = radio.closest('[data-store-id],[data-shop-id],label,.el-radio,tr') || radio.parentElement;
      const text = clean(container?.textContent);
      return {
        site_code: siteCode,
        store_id: clean(container?.dataset?.storeId || container?.dataset?.shopId || radio?.dataset?.storeId || radio.value),
        store_name: clean(container?.dataset?.storeName || text),
      };
    }).filter((item) => item && (item.store_id || item.store_name))
      .filter((item) => {
        const key = `${item.site_code}:${item.store_id || item.store_name}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
    const uniqueManualSites = new Set(discoveredManualSites.filter(Boolean));
    const loaded = requiredManualSites.size === uniqueManualSites.size
      && [...requiredManualSites].every((siteCode) => uniqueManualSites.has(siteCode))
      && items.length === discoveredManualSites.length;
    return { loaded, items };
  };
  const transactionModel = (detailVm) => {
    const ref = detailVm?.$refs?.transactionInformationModuleRef;
      return firstObject(
      ref?.transactionInformation,
      ref?.transactionInfo,
      ref?.formData,
      ref?.form,
      ref?.$data?.transactionInformation,
      ref?.$data?.transactionInfo,
      ref?.$data?.formData,
      ref?.$data?.form,
    ) || {};
  };
  const readTransactionInformation = (detailVm) => {
    const model = transactionModel(detailVm);
    const logistics = detailVm?.$store?.state?.shopee?.logisticsInformationModule || {};
    return {
      stock: pick(model, ['stock', 'quantity', 'inventory', '库存']),
      weight_kg: firstValue(pick(model, ['package_weight', 'weight_kg', 'weightKg', 'weight', '重量']), pick(logistics, ['weight', 'package_weight', 'weight_kg'])) || '',
      package_length_cm: firstValue(pick(model, ['package_length', 'packageLength', 'length', '包裹长度']), pick(logistics, ['length', 'package_length', 'packageLength'])) || '',
      package_width_cm: firstValue(pick(model, ['package_width', 'packageWidth', 'width', '包裹宽度']), pick(logistics, ['width', 'package_width', 'packageWidth'])) || '',
      package_height_cm: firstValue(pick(model, ['package_height', 'packageHeight', 'height', '包裹高度']), pick(logistics, ['height', 'package_height', 'packageHeight'])) || '',
    };
  };
  const readPromotionOptions = (detailVm) => {
    const ref = detailVm?.$refs?.transactionInformationModuleRef;
    const model = transactionModel(detailVm);
    const selectedPromotion = detailVm?.$store?.state?.shopee?.variantAttributeModule?.mdDiscountActivity || {};
    // mdDiscountActivity.discount_id is the authoritative selected activity on the
    // loaded page; changeDiscountInfo.discountId is a transient dialog value that
    // defaults to 0 when no activity is being changed, and the transaction model
    // fields are rarely set. A bare 0 must not be treated as a real activity id —
    // it would fabricate an "活动未配置/40%" placeholder instead of a real selection.
    const selectedRawId = String(firstValue(
      selectedPromotion?.discount_id,
      selectedPromotion?.discountId,
      pick(model, ['promotion_activity_id', 'activity_id', 'activityId', 'discount_id', 'discountId']),
      ref?.changeDiscountInfo?.discountId,
    )).trim();
    const selectedId = selectedRawId === '0' ? '' : selectedRawId;
    const optionLists = [
      ref?.promotionOptions,
      ref?.activityOptions,
      ref?.discountOptions,
      ref?.promotionList,
      ref?.$data?.promotionOptions,
      ref?.$data?.activityOptions,
      ref?.$data?.discountOptions,
    ];
    // 弹窗分页（默认 10 条/页）：列表读到了也可能只读到当前页。分页总数用来区分
    // "选中活动在别的页"（不能把它判成失效）和"列表里确实没有它"（草稿从别处带来）。
    const totalCandidates = [];
    for (const element of document.querySelectorAll('.el-dialog,.el-dialog__wrapper,[role="dialog"]')) {
      if (!clean(element.textContent).includes('选择折扣活动')) continue;
      for (let vm = element.__vue__ || element.__vueParentComponent?.proxy, depth = 0; vm && depth < 8; vm = vm.$parent, depth += 1) {
        totalCandidates.push(vm?.total, vm?.$data?.total, vm?.pagination?.total, vm?.$data?.pagination?.total);
        optionLists.push(
          vm?.tableData,
          vm?.activityList,
          vm?.promotionList,
          vm?.discountList,
          vm?.$data?.tableData,
          vm?.$data?.activityList,
          vm?.$data?.promotionList,
          vm?.$data?.discountList,
        );
      }
      // 活动列表的真正宿主是弹窗里的 vxe/el 表格组件：`tableData` 挂在表格的
      // `__vue__` 上，弹窗组件的 $parent 链通常拿不到。漏掉它就只能退化成"只写
      // 当前选中那一条"，活动会缺状态与有效期（马帮表格行字段：
      // discount_id / discount_name / start_time / end_time / flag / discount_status）。
      const tables = typeof element.querySelectorAll === 'function'
        ? [...element.querySelectorAll('.vxe-table, .el-table'), ...element.querySelectorAll('[class*="table"]')]
        : [];
      for (const table of tables) {
        const tableVm = table?.__vue__ || table?.__vueParentComponent?.proxy;
        if (!tableVm) continue;
        // vxe-table 把行放在 prop `data` / `internalData.fullData` 上，组件自己的
        // 父组件才有 `tableData`。几种形态都读一遍，避免只能读到空列表。
        let tableRows = null;
        try {
          tableRows = typeof tableVm.getTableData === 'function' ? tableVm.getTableData() : null;
        } catch (_error) {
          tableRows = null;
        }
        optionLists.push(
          tableVm.tableData,
          tableVm.$data?.tableData,
          tableVm.data,
          tableVm.$props?.data,
          tableVm.internalData?.fullData,
          tableVm.internalData?.tableData,
          tableVm.$data?.internalData?.fullData,
          tableRows,
          tableVm.list,
          tableVm.$data?.list,
        );
      }
    }
    const options = optionLists.flatMap((items) => Array.isArray(items) ? items : []);
    const listTotal = totalCandidates
      .map((value) => Number(value))
      .filter((value) => Number.isFinite(value) && value > 0)
      .reduce((max, value) => Math.max(max, value), 0) || null;
    // 列表是否真的读到：只有在弹窗/表格里拿到过活动行时才算"目录已加载"。否则
    // 这次采集只是回显了当前选中项，没有资格把别的活动判成失效。
    const listLoaded = options.length > 0;
    const optionId = (option) => clean(option?.activity_id ?? option?.activityId ?? option?.discount_id ?? option?.discountId ?? option?.id ?? option?.value);
    const hasSelected = () => options.some((option) => optionId(option) === selectedId);
    // 回显当前选中项的条件：
    //   - 没读到列表（只有选中项可回显）；
    //   - 读到了但列表分页、选中的那条不在当前页（total > 本页行数时不能判它失效，
    //     否则保存时会提示"不属于当前店铺采集目录"）。
    // 列表完整读到且就是没有它 → 不回显（草稿从别处带来的旧活动），避免绑定失效活动。
    const loadedIds = new Set(options.map(optionId).filter(Boolean));
    const paginated = Boolean(listTotal && listTotal > loadedIds.size);
    if (selectedId && !hasSelected() && (!listLoaded || paginated)) {
      // 该条只有 id+名称，所以状态留空由后端判成"未解析"，绝不冒充可用活动。
      options.push({
        activity_id: selectedId,
        name: clean(firstValue(selectedPromotion?.discount_name, selectedPromotion?.discountName, ref?.changeDiscountInfo?.discountName)),
        start_at: clean(firstValue(selectedPromotion?.start_time, selectedPromotion?.startTime)),
        end_at: clean(firstValue(selectedPromotion?.end_time, selectedPromotion?.endTime)),
      });
    }
    const seen = new Set();
    const items = options.map((option) => {
      const activityId = clean(option?.activity_id ?? option?.activityId ?? option?.discount_id ?? option?.discountId ?? option?.id ?? option?.value);
      return {
        activity_id: activityId,
        name: clean(option?.name ?? option?.activity_name ?? option?.activityName ?? option?.discount_name ?? option?.discountName ?? option?.label),
        discount_rate: clean(option?.discount_rate ?? option?.discountRate),
        start_at: clean(firstValue(
          option?.start_at,
          option?.startAt,
          option?.startTime,
          option?.start_time,
          option?.begin_time,
          option?.beginTime,
          option?.start_date,
        )),
        end_at: clean(firstValue(
          option?.end_at,
          option?.endAt,
          option?.endTime,
          option?.end_time,
          option?.finish_time,
          option?.finishTime,
          option?.end_date,
        )),
        // 马帮行自带 discount_status(ongoing/…) 与 flag(1 未开始/2 进行中/3 已结束)；
        // 两者都取不到时状态留空，由后端判成"未解析"而不是"已停用"。
        status: clean(firstValue(
          option?.status,
          option?.discount_status,
          Number(option?.flag) === 2 ? '进行中' : Number(option?.flag) === 1 ? '未开始' : Number(option?.flag) === 3 ? '已结束' : '',
        )),
        selected: Boolean(activityId && activityId === selectedId),
      };
    }).filter((option) => option.activity_id && option.name && !seen.has(option.activity_id) && seen.add(option.activity_id));
    return { items, listLoaded };
  };
  const readSizeChart = (detailVm) => {
    const sizeChart = detailVm?.$store?.state?.shopee?.productMaterialModule?.sizeChartUrl || {};
    return {
      url: clean(sizeChart?.url),
      id: clean(sizeChart?.id),
      width: sizeChart?.width ?? null,
      height: sizeChart?.height ?? null,
    };
  };
  const normalizeSizeChartTemplates = (items) => {
    const seen = new Set();
    return (Array.isArray(items) ? items : []).map((item) => {
      const templateId = clean(item?.template_id ?? item?.templateId ?? item?.size_chart_id ?? item?.sizeChartId ?? item?.id ?? item?.value);
      return {
        template_id: templateId,
        name: clean(item?.name ?? item?.template_name ?? item?.templateName ?? item?.label ?? item?.title),
        preview_url: clean(item?.preview_url ?? item?.previewUrl ?? item?.image_url ?? item?.imageUrl ?? item?.url ?? item?.image),
      };
    }).filter((item) => item.template_id && item.name && !seen.has(item.template_id) && seen.add(item.template_id));
  };
  const readSizeChartTemplatesFromSelect = () => {
    const sizeChartScope = [...document.querySelectorAll('.el-form-item,.form-item')]
      .find((node) => clean(node.textContent).includes('\u5c3a\u7801\u56fe\u8868'));
    if (!sizeChartScope) return [];
    const options = [];
    for (const element of sizeChartScope.querySelectorAll('.el-select')) {
      for (let vm = element.__vue__ || element.__vueParentComponent?.proxy, depth = 0; vm && depth < 4; vm = vm.$parent, depth += 1) {
        for (const items of [vm?.options, vm?.cachedOptions, vm?.$data?.options, vm?.$data?.cachedOptions]) {
          if (Array.isArray(items)) options.push(...items);
        }
      }
    }
    return normalizeSizeChartTemplates(options);
  };
  const readSizeChartTemplates = (detailVm) => {
    const selectItems = readSizeChartTemplatesFromSelect();
    if (selectItems.length) return { loaded: true, items: selectItems };
    const materialRef = detailVm?.$refs?.productMaterialModuleRef;
    const materialState = detailVm?.$store?.state?.shopee?.productMaterialModule || {};
    const containers = [materialRef, materialRef?.$data, materialState];
    for (const element of document.querySelectorAll('.el-dialog,.el-dialog__wrapper,[role="dialog"],.el-popper,.el-dropdown-menu,.el-select-dropdown')) {
      for (let vm = element.__vue__ || element.__vueParentComponent?.proxy, depth = 0; vm && depth < 8; vm = vm.$parent, depth += 1) {
        containers.push(vm, vm?.$data);
      }
    }
    const keys = [
      'sizeChartTemplateList',
      'sizeChartTemplates',
      'sizeChartTemplateOptions',
      'sizeChartOptions',
      'sizeChartList',
      'templateSizeChartList',
    ];
    for (const container of containers) {
      if (!container || typeof container !== 'object') continue;
      for (const key of keys) {
        if (!Array.isArray(container[key])) continue;
        const items = normalizeSizeChartTemplates(container[key]);
        if (items.length) return { loaded: true, items };
      }
    }
    return { loaded: false, items: [] };
  };
  const isElementVisible = (node) => {
    if (!node) return false;
    if (typeof getComputedStyle === 'function') {
      try {
        const style = getComputedStyle(node);
        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
      } catch (_error) {
        // getComputedStyle can throw for detached/foreign nodes; fall through to DOM signals.
      }
    }
    if (node.style && typeof node.style.display === 'string' && node.style.display === 'none') return false;
    if (typeof node.isConnected === 'boolean') return node.isConnected;
    if (typeof node.getClientRects === 'function' && node.getClientRects().length) return true;
    // Browsers always expose at least one signal above; keep plain mocks readable.
    return true;
  };
  const readWatermarkTemplatesFromDom = () => {
    const itemSelector = [
      '[data-watermark-id]',
      '[data-template-id]',
      '[data-templateid]',
      '[data-watermarkid]',
      '.watermark-template',
      '.watermark-item',
      '.watermark-list-item',
    ].join(',');
    const menuSelector = '.el-dropdown-menu,.el-select-dropdown,.el-popper,[role="menu"]';
    const seen = new Set();
    const items = [];
    const collectNode = (node) => {
      const templateId = clean(firstValue(
        node.getAttribute('data-watermark-id'),
        node.getAttribute('data-template-id'),
        node.getAttribute('data-templateid'),
        node.getAttribute('data-watermarkid'),
        node.getAttribute('data-id'),
        node.getAttribute('data-value'),
      ));
      const image = node.querySelector('img');
      const name = clean(firstValue(
        node.getAttribute('data-watermark-name'),
        node.getAttribute('data-template-name'),
        node.getAttribute('title'),
        node.querySelector('[data-watermark-name],.watermark-name,.template-name')?.textContent,
        node.textContent,
      ));
      if (templateId && name && !seen.has(templateId)) {
        seen.add(templateId);
        items.push({ template_id: templateId, name, preview_url: clean(image?.src) });
      }
    };
    // The collector opens 添加水印 before capture, so a VISIBLE menu is the
    // authoritative DOM source. Hidden menus can keep stale rows in the DOM; only
    // visible rows are merged below (Vue state is read first and dedupes by id).
    for (const menu of [...document.querySelectorAll(menuSelector)].filter(isElementVisible)) {
      for (const node of menu.querySelectorAll(itemSelector)) collectNode(node);
    }
    if (!items.length) {
      for (const node of [...document.querySelectorAll(itemSelector)].filter(isElementVisible)) collectNode(node);
    }
    return items;
  };
  const readWatermarkTemplates = (detailVm) => {
    const materialRef = detailVm?.$refs?.productMaterialModuleRef;
    const materialState = detailVm?.$store?.state?.shopee?.productMaterialModule || {};
    const containers = [materialRef, materialRef?.$data, materialState];
    for (const element of document.querySelectorAll('.el-dialog,.el-dialog__wrapper,[role="dialog"],.el-popper,.el-dropdown-menu,.el-select-dropdown')) {
      for (let vm = element.__vue__ || element.__vueParentComponent?.proxy, depth = 0; vm && depth < 8; vm = vm.$parent, depth += 1) {
        containers.push(vm, vm?.$data);
      }
    }
    const keys = [
      // 2026-10-01 实测：真实详情页把水印模板挂在组件 data 上的字段就叫 watermarkTemplate（单数）。
      // 旧别名表只有复数/带 List 的写法 → 怎么扫都是 0 条。
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
    let loadedList = null;
    for (const container of containers) {
      if (!container || typeof container !== 'object') continue;
      const sources = [
        container,
        container?.watermarkMenu,
        container?.watermarkConfig,
        container?.$data?.watermarkMenu,
        container?.$data?.watermarkConfig,
      ];
      for (const source of sources) {
        if (!source || typeof source !== 'object') continue;
        for (const key of keys) {
          if (Array.isArray(source[key])) {
            loadedList = source[key];
            break;
          }
        }
        if (loadedList !== null) break;
      }
      if (loadedList !== null) break;
    }
    if (loadedList === null) {
      const items = readWatermarkTemplatesFromDom();
      return { loaded: items.length > 0, items };
    }
    // Merge the Vue option list with any rows the VISIBLE open menu renders now.
    // The collector expanded 添加水印 right before capture, so the open menu can
    // expose options the Vue model has not flushed yet; stale HIDDEN menus never
    // contribute. Dedupe by template_id across the two sources.
    const seen = new Set();
    const vueItems = loadedList.map((item) => {
      const templateId = clean(item?.template_id ?? item?.templateId ?? item?.watermark_id ?? item?.watermarkId ?? item?.id);
      return {
        template_id: templateId,
        name: clean(item?.name ?? item?.template_name ?? item?.templateName ?? item?.watermark_name ?? item?.watermarkName ?? item?.label),
        preview_url: clean(item?.preview_url ?? item?.previewUrl ?? item?.image_url ?? item?.imageUrl ?? item?.img_url ?? item?.imgUrl ?? item?.url),
      };
    }).filter((item) => item.template_id && item.name && !seen.has(item.template_id) && seen.add(item.template_id));
    const merged = [...vueItems];
    for (const domItem of readWatermarkTemplatesFromDom()) {
      if (!seen.has(domItem.template_id)) {
        seen.add(domItem.template_id);
        merged.push(domItem);
      }
    }
    if (merged.length) return { loaded: true, items: merged };
    const domItems = readWatermarkTemplatesFromDom();
    return { loaded: domItems.length > 0, items: domItems };
  };

  const detailVm = findDetailVm();
  const storeCategory = detailVm?.$refs?.storeCategory;
  const storeInfo = storeCategory?.storeCategoryInfo || storeCategory?.storeInfo || {};
  const categoryInfo = storeCategory?.categoryInfo || storeCategory?.productCategoryInfo || storeCategory?.ProductCategoryInfo || {};
  const categorySettings = storeCategory?.categorySettings || {};
  const storeCategoryModule = detailVm?.$store?.state?.shopee?.storeCategoryModule || {};
  const categoryCandidates = Array.isArray(storeCategory?.categoryNameArr) ? storeCategory.categoryNameArr : [];
  const selectedCategory = categoryCandidates.length === 1 ? categoryCandidates[0] : {};
  const storeId = clean(firstValue(
    storeInfo?.shopId,
    storeInfo?.storeId,
    storeInfo?.id,
    storeCategory?.shopId,
    storeCategory?.storeId,
    categorySettings?.shopId,
    storeCategoryModule?.shopId,
  ));
  const shopList = firstArray(storeCategory?.shopList, storeCategoryModule?.shopList);
  const selectedStore = shopList.find((shop) => clean(shop?.id) === storeId) || {};
  const storeName = clean(firstValue(
    storeInfo?.shopName,
    storeInfo?.storeName,
    storeInfo?.name,
    storeCategory?.shopName,
    selectedStore?.name,
  ));
  const categoryId = clean(firstValue(
    categoryInfo?.categoryId,
    categoryInfo?.id,
    storeCategory?.categoryId,
    storeCategory?.ProductAttributeInfo?.category_id,
    selectedCategory?.categoryId,
  ));
  // 类目路径统一成类目目录（services/api/marketplace_category_catalog.py 的
  // `" > ".join(...)`）的写法：两边写法不一致时，模板保存的路径校验会直接失败
  // （同一个类目 id 会被判成不匹配）。这里分段清理后重拼，任何来源都归一化。
  const canonicalCategoryPath = (value) => String(value ?? '')
    .split('>')
    .map(clean)
    .filter(Boolean)
    .join(' > ');
  const categoryPath = canonicalCategoryPath(firstValue(
    categoryInfo?.path,
    categoryInfo?.categoryPath,
    categoryInfo?.fullPath,
    storeCategory?.categoryPath,
    selectedCategory?.name_en_path?.split(',').join('>'),
    selectedCategory?.name_zh_path?.split(',').join('>'),
  ));
  const categoryIdPath = asList(firstValue(
    categoryInfo?.idPath,
    categoryInfo?.categoryIdPath,
    storeCategory?.categoryIdPath,
    selectedCategory?.category_id_path?.split(','),
  ));
  const explicitLeaf = optionalBoolean(firstDefined(
    selectedCategory?.is_leaf,
    selectedCategory?.isLeaf,
    categoryInfo?.is_leaf,
    categoryInfo?.isLeaf,
  ));
  const explicitHasChildren = optionalBoolean(firstDefined(
    selectedCategory?.has_children,
    selectedCategory?.hasChildren,
    categoryInfo?.has_children,
    categoryInfo?.hasChildren,
  ));
  const selectedCategoryId = clean(selectedCategory?.categoryId ?? selectedCategory?.id);
  const moduleCategoryId = clean(storeCategoryModule?.categoryId);
  const attributesLoaded = Array.isArray(storeCategory?.ProductAttributeInfo?.attributesNew);
  const consistentSelectedLeaf = Boolean(
    categoryId
    && categoryCandidates.length === 1
    && selectedCategoryId === categoryId
    && moduleCategoryId === categoryId
    && categoryIdPath.at(-1) === categoryId
    && attributesLoaded
  );
  const isLeaf = explicitLeaf ?? (explicitHasChildren === null ? consistentSelectedLeaf : !explicitHasChildren);
  const page = {
    url: location.origin + location.pathname,
    platform: clean(inputPlan.platform || 'shopee').toLowerCase(),
    country: clean(inputPlan.country).toUpperCase(),
  };
  const promotions = readPromotionOptions(detailVm);
  const watermarks = readWatermarkTemplates(detailVm);
  const sizeChartTemplates = readSizeChartTemplates(detailVm);
  const listingMode = readListingMode(detailVm);
  const targetSiteStores = readTargetSiteStores();
  return {
    source: 'mabang_current_page',
    ...listingMode,
    page,
    store_category_state: {
      store: { id: storeId, name: storeName },
      category: { id: categoryId, path: categoryPath, id_path: categoryIdPath, is_leaf: isLeaf },
    },
    category_attributes: readCategoryAttributes(storeCategory),
    sku_rows: readSkuRows(detailVm),
    target_site_stores_loaded: targetSiteStores.loaded,
    target_site_stores: targetSiteStores.items,
    transaction_information: readTransactionInformation(detailVm),
    promotion_options: promotions.items,
    promotion_catalog_loaded: promotions.listLoaded,
    size_chart: readSizeChart(detailVm),
    size_chart_template_catalog_loaded: sizeChartTemplates.loaded,
    size_chart_templates: sizeChartTemplates.items,
    watermark_catalog_loaded: watermarks.loaded,
    watermark_templates: watermarks.items,
    provenance: {
      store_category: 'vue:$refs.storeCategory+vuex:shopee.storeCategoryModule',
      category_attributes: 'vue:ProductAttributeInfo.attributesNew',
      sku_rows: 'vue:vxe-table.tableData',
      transaction_information: 'vue:$refs.transactionInformationModuleRef',
      promotions: 'vue:$refs.transactionInformationModuleRef',
      size_chart: 'vuex:shopee.productMaterialModule.sizeChartUrl',
      size_chart_templates: sizeChartTemplates.loaded ? 'vue:mabang-size-chart-template-list' : '',
      watermarks: watermarks.loaded ? 'vue:mabang-watermark-template-list' : '',
    },
    capture_debug: {
      detail_vm: Boolean(detailVm),
      store_category_ref: Boolean(storeCategory),
    },
  };
}
