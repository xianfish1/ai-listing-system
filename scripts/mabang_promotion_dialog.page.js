/**
 * 页面上下文函数：打开 / 关闭马帮「选择折扣活动」弹窗（只展开，不选任何活动）。
 *
 * 真机结构（publish-ui chunk-2229 / chunk-8398）：
 *   - 本土（site）页面的「折扣活动」不是表单项，而是 SKU 表头 colgroup 里的
 *     `<span class="change-discount" @click="mdChangeDiscount">选择活动 / 活动名称</span>`；
 *   - 点击后才挂出 `el-dialog`（标题「选择折扣活动」），活动行在里面的 vxe-table；
 *   - 行字段：discount_id / discount_name / start_time / end_time / flag(1未开始 2进行中 3已结束)。
 * 只找 `.el-select`/`input` 当触发点会永远点不开弹窗，采集就只能回显当前选中项（缺有效期）。
 *
 * 这里只做弹窗的开关与行数探测；活动行本身仍由 mabang_page_import_capture.js 读取，
 * 保证导入与「单独重采」两条链路口径一致。
 *
 * 用法（在 frame.evaluate 里 eval）：
 *   (0, eval)(`(${source})`)({ phase: 'open' })
 */
(function promotionDialog(options) {
  const opts = options || {};
  const phase = String(opts.phase || 'read');
  const hint = String(opts.hint || '\u9009\u62e9\u6298\u6263\u6d3b\u52a8');
  const actionTexts = Array.isArray(opts.actionTexts) && opts.actionTexts.length
    ? opts.actionTexts
    : ['\u9009\u62e9\u6d3b\u52a8', '\u9009\u62e9\u6298\u6263'];
  const labels = Array.isArray(opts.labels) ? opts.labels : ['\u5e97\u94fa\u4fc3\u9500\u6d3b\u52a8', '\u4fc3\u9500\u6d3b\u52a8', '\u6298\u6263\u6d3b\u52a8'];
  const timeoutMs = Number(opts.timeoutMs || 8000);
  const clean = (value) => String(value === undefined || value === null ? '' : value).replace(/\s+/g, ' ').trim();
  const compact = (value) => clean(value).replace(/\s+/g, '');
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = (node) => {
    if (!node || node.isConnected === false) return false;
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  const dialogs = () => [...document.querySelectorAll('.el-dialog,.el-dialog__wrapper,[role="dialog"]')].filter(visible);
  const activityDialog = () => dialogs().find((node) => clean(node.textContent).includes(hint)) || null;
  const rowCount = (node) => (node
    ? node.querySelectorAll('.vxe-body--row,.vxe-table--body tbody tr,.el-table__body tbody tr,tbody tr').length
    : 0);
  const loading = (node) => (node
    ? [...node.querySelectorAll('.el-loading-mask')].some(visible)
    : false);
  const waitUntil = async (predicate, ms) => {
    const deadline = Date.now() + (ms || timeoutMs);
    while (Date.now() < deadline) {
      const value = predicate();
      if (value) return value;
      await sleep(100);
    }
    return null;
  };
  const fire = (node, eventName, rect) => node.dispatchEvent(new MouseEvent(eventName, {
    bubbles: true,
    cancelable: true,
    view: window,
    clientX: rect.left + rect.width / 2,
    clientY: rect.top + rect.height / 2,
  }));
  const click = (node) => {
    const rect = node.getBoundingClientRect();
    for (const eventName of ['mouseenter', 'mouseover', 'mousedown', 'mouseup', 'click']) fire(node, eventName, rect);
  };

  /** 触发点候选：真机表头文字链优先，再退到文本/旧版表单项。 */
  const triggerCandidates = () => {
    const list = [];
    const seen = new Set();
    const push = (node, method) => {
      if (!node || seen.has(node) || !visible(node)) return;
      seen.add(node);
      list.push({ node, method, text: clean(node.textContent).slice(0, 40) });
    };
    const headerCells = [...document.querySelectorAll('.vxe-header--column,.el-table th,th')].filter(visible);
    const activityHeader = headerCells.find((node) => clean(node.textContent).includes('\u6298\u6263\u6d3b\u52a8'));
    if (activityHeader) {
      const link = [...activityHeader.querySelectorAll('.change-discount,a,span,button')]
        .find((node) => visible(node) && (
          String(node.className || '').includes('change-discount')
          || actionTexts.some((text) => clean(node.textContent).includes(text))
        ));
      if (link) push(link, 'activity_header_link');
      push(activityHeader, 'activity_header');
    }
    for (const node of [...document.querySelectorAll('.change-discount')]) push(node, 'change_discount');
    const textTrigger = [...document.querySelectorAll('button,[role="button"],a,span,label,.el-button,.el-link')]
      .filter((node) => visible(node) && !node.closest('.el-dialog,.el-dialog__wrapper,[role="dialog"]'))
      .find((node) => actionTexts.some((text) => compact(node.textContent).startsWith(compact(text))));
    if (textTrigger) push(textTrigger, 'action_text');
    // 旧版页面（活动是表单项里的只读输入框/下拉）留一条退路。
    for (const item of [...document.querySelectorAll('.el-form-item,.form-item')]) {
      if (!visible(item)) continue;
      const ownLabel = clean(item.querySelector('.el-form-item__label,label')?.textContent || item.firstElementChild?.textContent);
      if (!labels.some((label) => ownLabel.includes(label))) continue;
      const control = [...item.querySelectorAll('.el-select,.el-input__inner,input[readonly],.el-input,button,a')].find(visible);
      push(control, 'form_item_control');
    }
    return list;
  };

  /** 收回弹窗：Escape → 右上角关闭 → 取消；绝不点任何活动行/确认按钮。 */
  const closeDialog = async () => {
    const dialog = activityDialog();
    if (!dialog) return { closed: 'already_closed' };
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    const escaped = await waitUntil(() => !activityDialog(), 2000);
    if (escaped) return { closed: 'escape' };
    const remaining = activityDialog();
    if (!remaining) return { closed: 'escape' };
    const headerButton = remaining.querySelector('.el-dialog__headerbtn,.el-dialog__close,[aria-label="Close"],[aria-label="\u5173\u95ed"]');
    const cancelButton = [...remaining.querySelectorAll('button,[role="button"],a,span,.el-button')]
      .filter(visible)
      .find((node) => compact(node.textContent) === '\u53d6\u6d88');
    const closeButton = headerButton || cancelButton;
    if (!closeButton) return { closed: 'still_open' };
    closeButton.click();
    const closed = await waitUntil(() => !activityDialog(), 2000);
    return { closed: closed ? 'close_button' : 'still_open' };
  };

  if (phase === 'close') return closeDialog();

  if (phase === 'read') {
    const dialog = activityDialog();
    return {
      opened: Boolean(dialog),
      rows: rowCount(dialog),
      loading: loading(dialog),
      dialog_text: dialog ? clean(dialog.textContent).slice(0, 200) : '',
    };
  }

  return (async () => {
    if (activityDialog()) {
      const existing = activityDialog();
      await waitUntil(() => !loading(existing));
      await waitUntil(() => rowCount(existing) > 0);
      return {
        opened: true,
        method: 'already_open',
        triggers: 0,
        trigger: '',
        rows: rowCount(existing),
        rows_ready: rowCount(existing) > 0,
        loading: loading(existing),
        dialog_text: clean(existing.textContent).slice(0, 200),
      };
    }
    const candidates = triggerCandidates();
    const attempts = [];
    for (const candidate of candidates) {
      if (!candidate.node || candidate.node.isConnected === false) continue;
      try {
        candidate.node.scrollIntoView({ block: 'center', inline: 'center' });
      } catch (_error) {
        /* scrollIntoView 只是尽力而为 */
      }
      click(candidate.node);
      const dialog = await waitUntil(activityDialog);
      if (!dialog) {
        attempts.push({ method: candidate.method, opened: false });
        continue;
      }
      await waitUntil(() => !loading(dialog));
      const ready = await waitUntil(() => rowCount(dialog) > 0);
      attempts.push({ method: candidate.method, opened: true, rows: rowCount(dialog) });
      return {
        opened: true,
        method: candidate.method,
        trigger: candidate.text,
        triggers: candidates.length,
        attempts,
        rows: rowCount(dialog),
        rows_ready: Boolean(ready),
        loading: loading(dialog),
        dialog_text: clean(dialog.textContent).slice(0, 200),
      };
    }
    return {
      opened: false,
      method: 'dialog_not_opened',
      triggers: candidates.length,
      attempts,
      rows: 0,
      rows_ready: false,
      dialog_text: '',
    };
  })();
})
