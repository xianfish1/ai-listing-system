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

  return { planLocalFolderImport, runSequentialImport, createImportQueue };
}));
