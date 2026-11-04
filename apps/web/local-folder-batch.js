(function localFolderBatchModule(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.LocalFolderBatch = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function createLocalFolderBatchApi() {
  'use strict';

  function normalizedPath(file) {
    // File System Access 选出来的文件没有 webkitRelativePath，用 __relativePath 带上目录层级
    return String(file?.__relativePath || file?.webkitRelativePath || file?.name || '').replace(/\\/g, '/');
  }

  function extension(name) {
    const value = String(name || '');
    const dot = value.lastIndexOf('.');
    return dot >= 0 ? value.slice(dot).toLowerCase() : '';
  }

  function planLocalFolderImport(files, supportedExtensions) {
    const sourceFiles = Array.from(files || []);
    const supported = supportedExtensions instanceof Set
      ? supportedExtensions
      : new Set(Array.from(supportedExtensions || []));
    const accepted = sourceFiles.flatMap((file) => {
      const path = normalizedPath(file);
      const parts = path.split('/').filter(Boolean);
      if (!parts.length || parts.some((part) => part.startsWith('.'))) return [];
      if (!supported.has(extension(file?.name || parts[parts.length - 1]))) return [];
      return [{ file, parts }];
    });

    if (!accepted.length) throw new Error('所选文件夹没有可上传的图片、视频或源文件');

    const firstParts = accepted[0].parts;
    const rootName = firstParts.length > 1 ? firstParts[0] : '本地商品';
    const candidates = new Map();
    let rootFileCount = 0;

    for (const item of accepted) {
      if (item.parts.length < 3 || item.parts[0] !== rootName) {
        rootFileCount += 1;
        continue;
      }
      const folderName = item.parts[1];
      if (!candidates.has(folderName)) candidates.set(folderName, []);
      candidates.get(folderName).push(item);
    }

    if (candidates.size >= 2) {
      const groups = Array.from(candidates, ([folderName, items]) => ({
        folderName,
        // 根目录名 + 模式跟着 group 走：重复导入的指纹与「整目录商品」判断都要用。
        rootName,
        mode: 'batch',
        files: items.map((item) => item.file),
        manifest: items.map((item) => ({
          path: item.parts.slice(2).join('/'),
          size_bytes: item.file.size,
        })),
      }));
      return {
        mode: 'batch',
        rootName,
        groups,
        ignoredRootFileCount: rootFileCount,
        filteredFileCount: sourceFiles.length - accepted.length,
      };
    }

    return {
      mode: 'single',
      rootName,
      groups: [{
        folderName: rootName,
        rootName,
        mode: 'single',
        files: accepted.map((item) => item.file),
        manifest: accepted.map((item) => ({
          path: item.parts.length > 1 ? item.parts.slice(1).join('/') : item.parts[0],
          size_bytes: item.file.size,
        })),
      }],
      ignoredRootFileCount: 0,
      filteredFileCount: sourceFiles.length - accepted.length,
    };
  }

  // 目录名 → 主 SKU，与后端 _derive_main_sku_from_folder_name 同一条规则：
  // 以字母开头、连续字母数字、至少 4 位且带数字；不像 SKU 时返回 ''。
  function folderSkuToken(name) {
    const value = String(name || '').trim().toUpperCase();
    let token = '';
    for (const ch of value) {
      if (!token) {
        if (ch >= 'A' && ch <= 'Z') {
          token += ch;
          continue;
        }
        break;
      }
      if ((ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9')) {
        token += ch;
        continue;
      }
      break;
    }
    return token.length >= 4 && /\d/.test(token) ? token : '';
  }

  // 同一个商品文件夹的「指纹」：目录名 + 该目录内的文件清单（相对路径 + 大小）。
  // 重复导入（同一目录拖了两次、导入完再拖一次、同一份目录的两个副本）算同一个指纹；
  // 目录内容变过（加了图 / 换了图）则指纹不同，仍然允许导入。
  // SKU 样式的目录名全库唯一，指纹里不再带根目录；「1」「1 (2)」这类临时目录名只在
  // 父目录内唯一，指纹要带上根目录名，免得把两批里同名但不同的商品误判成重复。
  function importGroupSignature(group, rootName) {
    const folder = String(group?.folderName || '').trim().toLowerCase();
    const root = group?.rootName !== undefined ? group.rootName : rootName;
    const scope = folderSkuToken(group?.folderName) ? '' : String(root || '').trim().toLowerCase();
    const manifest = Array.isArray(group?.manifest) ? group.manifest : [];
    const entries = manifest.length
      ? manifest.map((item) => `${String(item?.path || '').toLowerCase()}|${Number(item?.size_bytes || 0) || 0}`)
      : Array.from(group?.files || [])
        .map((file) => `${normalizedPath(file).toLowerCase()}|${Number(file?.size || 0) || 0}`);
    entries.sort();
    return `${scope}#${folder}#${entries.join('\u0001')}`;
  }

  // 文件路径去掉拖入时加的顶层根目录名，得到「相对根目录」的路径集合。
  function pathsUnderRoot(group) {
    return Array.from(group?.files || [])
      .map((file) => normalizedPath(file).split('/').filter(Boolean).slice(1).join('/').toLowerCase())
      .filter(Boolean);
  }

  // outer 是否完整包含 inner：inner 的每个文件都以「inner 目录名/相对路径」出现在
  // outer 里。用于识别「同时拖了父目录和它的子目录」——子目录是重复的那一份。
  function groupContainsGroup(outer, inner) {
    const folder = String(inner?.folderName || '').trim().toLowerCase();
    if (!folder || outer === inner) return false;
    const innerPaths = pathsUnderRoot(inner);
    if (!innerPaths.length) return false;
    const outerPaths = new Set(pathsUnderRoot(outer));
    if (!outerPaths.size) return false;
    return innerPaths.every((path) => outerPaths.has(`${folder}/${path}`));
  }

  // 这个 group 是不是「整个目录就是一个商品」（单目录导入）：只有这种项才可能把
  // 后来拖进来的子目录完整包含进去。批量导入里的每个 group 只是父目录的一小块，
  // 拿它做包含判断会把同父目录下的不同商品误判成重复。
  function groupIsWholeFolder(group) {
    const root = String(group?.rootName || '').trim();
    const folder = String(group?.folderName || '').trim();
    return !root || root === folder;
  }

  // 一次性拖入 / 连续选择多个本地目录时，同一个商品文件夹只入队一次：
  //   existing —— 队列里或本次会话已经导入过同一个文件夹（指纹相同 / 已被整目录包含）
  //   repeat   —— 本次入队里出现了两份
  // `rootName` 是本次规划出来的根目录名（planLocalFolderImport 的 plan.rootName）。
  // 返回保留的 groups 和被跳过的重复项，调用方据此提示操作员。
  function splitDuplicateLocalImportGroups(groups, existingGroups = [], options = {}) {
    const rootName = options.rootName !== undefined ? options.rootName : '';
    const existing = Array.from(existingGroups || []).filter(Boolean);
    const accepted = [];
    const duplicates = [];
    const existingSignatures = new Set(existing.map((group) => importGroupSignature(group, rootName)));
    const acceptedSignatures = new Set();
    // 只有「整目录商品」的已导入项参与包含判断（拖了父目录、又单独拖了它的子目录）。
    const wholeExisting = existing.filter(groupIsWholeFolder);
    for (const group of Array.from(groups || [])) {
      const signature = importGroupSignature(group, rootName);
      const folderName = String(group?.folderName || '');
      if (existingSignatures.has(signature)) {
        duplicates.push({ folderName, reason: 'existing' });
        continue;
      }
      if (acceptedSignatures.has(signature)) {
        duplicates.push({ folderName, reason: 'repeat' });
        continue;
      }
      if (wholeExisting.some((other) => groupContainsGroup(other, group))) {
        duplicates.push({ folderName, reason: 'nested' });
        continue;
      }
      acceptedSignatures.add(signature);
      accepted.push(group);
    }
    return { groups: accepted, duplicates };
  }

  // 一次拖入多个顶层文件夹时，若 A 完整包含 B（父目录和它的子目录一起拖进来），
  // 只保留 A：B 的文件已经在上传清单里，再按独立商品导一次就是重复商品。
  function dropContainedLocalImportBatches(batches) {
    const kept = [];
    const dropped = [];
    for (const batch of Array.from(batches || [])) {
      const group = { folderName: batch?.name, files: batch?.files };
      if (kept.some((other) => groupContainsGroup(other, group))) {
        dropped.push(batch);
        continue;
      }
      const swallowed = kept.filter((other) => groupContainsGroup(group, other));
      for (const other of swallowed) {
        kept.splice(kept.indexOf(other), 1);
        dropped.push({ name: other.folderName, files: other.files });
      }
      kept.push(group);
    }
    return {
      batches: kept.map((group) => ({ name: group.folderName, files: group.files })),
      dropped,
    };
  }

  // 页面内共享的串行导入队列。操作员可以连续选择多个本地目录，每个商品目录
  // 作为一项排队；前一项跑完自动开始下一项，因此不必等前一次导入结束再选目录。
  // 队列只活在当前页面：File 句柄无法持久化，刷新页面会丢弃等待中的项。
  function createImportQueue({ run, onChange = () => {}, idPrefix = 'q' } = {}) {
    if (typeof run !== 'function') throw new Error('导入队列缺少执行函数');
    const pending = [];
    const results = [];
    let active = null;
    let running = false;
    let sequence = 0;
    let idleWaiters = [];

    function snapshot() {
      return {
        running,
        active,
        pending: pending.map((task) => Object.assign({}, task)),
        results: results.map((entry) => Object.assign({}, entry)),
      };
    }

    function notify() {
      try {
        onChange(snapshot());
      } catch (_error) {
        // 渲染失败不应该中断已经在跑的导入。
      }
    }

    function settleIdleWaiters() {
      if (running || pending.length) return;
      const waiters = idleWaiters;
      idleWaiters = [];
      for (const resolve of waiters) resolve(snapshot());
    }

    async function drain() {
      if (running) {
        // 已经在跑：本次入队只是让等待区变长，通知一次让界面重画队列。
        notify();
        return;
      }
      running = true;
      notify();
      try {
        while (pending.length) {
          const task = pending.shift();
          active = task;
          notify();
          try {
            results.push({ task, status: 'succeeded', value: await run(task) });
          } catch (error) {
            results.push({ task, status: 'failed', error });
          }
          active = null;
          notify();
        }
      } finally {
        running = false;
        notify();
        settleIdleWaiters();
      }
    }

    function enqueue(tasks) {
      const added = [];
      for (const task of Array.from(tasks || [])) {
        sequence += 1;
        const queued = Object.assign({}, task, { queue_id: `${idPrefix}${sequence}` });
        pending.push(queued);
        added.push(queued);
      }
      if (added.length) drain();
      return added;
    }

    function removePending(queueId) {
      const index = pending.findIndex((task) => task.queue_id === queueId);
      if (index < 0) return false;
      pending.splice(index, 1);
      notify();
      return true;
    }

    function clearPending() {
      const removed = pending.length;
      pending.splice(0, pending.length);
      if (removed) notify();
      return removed;
    }

    function removeResult(queueId) {
      // 重试前清掉旧结果行：同一个目录不会在队列里留下一条失败 + 一条成功。
      const index = results.findIndex((entry) => entry?.task?.queue_id === queueId);
      if (index < 0) return false;
      results.splice(index, 1);
      notify();
      return true;
    }

    function clearResults() {
      const removed = results.length;
      results.splice(0, results.length);
      if (removed) notify();
      return removed;
    }

    function whenIdle() {
      if (!running && !pending.length) return Promise.resolve(snapshot());
      return new Promise((resolve) => idleWaiters.push(resolve));
    }

    return { enqueue, removePending, clearPending, removeResult, clearResults, snapshot, whenIdle };
  }

  async function runSequentialImport(groups, worker, onProgress = () => {}) {
    const items = Array.from(groups || []);
    const successes = [];
    const failures = [];
    for (let index = 0; index < items.length; index += 1) {
      const group = items[index];
      onProgress({ phase: 'start', index, total: items.length, group, successes, failures });
      try {
        successes.push(await worker(group, index));
      } catch (error) {
        failures.push({ folderName: group.folderName, error });
      }
      onProgress({ phase: 'finish', index, total: items.length, group, successes, failures });
    }
    return { successes, failures };
  }

  return {
    planLocalFolderImport,
    splitDuplicateLocalImportGroups,
    dropContainedLocalImportBatches,
    folderSkuToken,
    runSequentialImport,
    createImportQueue,
  };
}));
