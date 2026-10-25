'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const batch = require('../apps/web/local-folder-batch.js');

const SUPPORTED = new Set(['.jpg', '.jpeg', '.png', '.webp', '.mp4']);

function file(webkitRelativePath, size = 1) {
  const parts = webkitRelativePath.replace(/\\/g, '/').split('/');
  return {
    name: parts[parts.length - 1],
    size,
    webkitRelativePath,
  };
}

test('groups two or more first-level product folders and preserves nested paths', () => {
  const plan = batch.planLocalFolderImport([
    file('批次/AL0001/main.jpg', 3),
    file('批次/AL0001/颜色/black.jpg', 4),
    file('批次/AL0002/main.png', 5),
    file('批次/root-cover.jpg', 6),
  ], SUPPORTED);

  assert.equal(plan.mode, 'batch');
  assert.equal(plan.rootName, '批次');
  assert.deepEqual(plan.groups.map((group) => group.folderName), ['AL0001', 'AL0002']);
  assert.deepEqual(plan.groups[0].manifest, [
    { path: 'main.jpg', size_bytes: 3 },
    { path: '颜色/black.jpg', size_bytes: 4 },
  ]);
  assert.equal(plan.ignoredRootFileCount, 1);
  assert.equal(plan.filteredFileCount, 0);
});

test('keeps the selected directory as one product when fewer than two child candidates exist', () => {
  const plan = batch.planLocalFolderImport([
    file('AL0003/main.jpg', 3),
    file('AL0003/颜色/black.jpg', 4),
  ], SUPPORTED);

  assert.equal(plan.mode, 'single');
  assert.deepEqual(plan.groups.map((group) => group.folderName), ['AL0003']);
  assert.deepEqual(plan.groups[0].manifest, [
    { path: 'main.jpg', size_bytes: 3 },
    { path: '颜色/black.jpg', size_bytes: 4 },
  ]);
  assert.equal(plan.ignoredRootFileCount, 0);
});

test('filters hidden and unsupported files before deciding the import mode', () => {
  const plan = batch.planLocalFolderImport([
    file('批次/AL0001/main.jpg'),
    file('批次/AL0002/main.png'),
    file('批次/.cache/preview.jpg'),
    file('批次/AL0003/readme.txt'),
  ], SUPPORTED);

  assert.equal(plan.mode, 'batch');
  assert.deepEqual(plan.groups.map((group) => group.folderName), ['AL0001', 'AL0002']);
  assert.equal(plan.filteredFileCount, 2);
});

test('rejects selections without supported product media', () => {
  assert.throws(
    () => batch.planLocalFolderImport([file('批次/AL0001/readme.txt')], SUPPORTED),
    /没有可上传的图片、视频或源文件/,
  );
});

function tick() {
  return new Promise((resolve) => setImmediate(resolve));
}

// 手动放行的执行函数：用来观察「上一项没结束前，下一项绝不开始」。
function createGatedRunner(started) {
  let releaseCurrent = null;
  return {
    run(task) {
      started.push(task.folderName);
      return new Promise((resolve) => {
        releaseCurrent = () => resolve({ id: task.folderName });
      });
    },
    release() {
      const release = releaseCurrent;
      releaseCurrent = null;
      release();
    },
  };
}

test('queue runs one product at a time and keeps later enqueues behind the running item', async () => {
  const started = [];
  const runner = createGatedRunner(started);
  const queue = batch.createImportQueue({ run: runner.run });

  queue.enqueue([{ folderName: 'AL0001' }]);
  await tick();
  assert.deepEqual(started, ['AL0001']);

  // 第一项还在跑：新选中的目录进入等待区，不会并发开跑。
  queue.enqueue([{ folderName: 'AL0002' }, { folderName: 'AL0003' }]);
  await tick();
  assert.deepEqual(started, ['AL0001']);
  assert.deepEqual(queue.snapshot().pending.map((task) => task.folderName), ['AL0002', 'AL0003']);

  runner.release();
  await tick();
  assert.deepEqual(started, ['AL0001', 'AL0002']);

  runner.release();
  await tick();
  runner.release();
  await queue.whenIdle();

  assert.deepEqual(started, ['AL0001', 'AL0002', 'AL0003']);
  assert.deepEqual(queue.snapshot().results.map((entry) => entry.value.id), ['AL0001', 'AL0002', 'AL0003']);
  assert.equal(queue.snapshot().running, false);
  assert.equal(queue.snapshot().active, null);
});

test('queue assigns unique ids and records failures without stopping the rest', async () => {
  const queue = batch.createImportQueue({
    run: async (task) => {
      if (task.folderName === 'AL0002') throw new Error('upload failed');
      return { id: task.folderName };
    },
  });

  queue.enqueue([{ folderName: 'AL0001' }, { folderName: 'AL0002' }]);
  queue.enqueue([{ folderName: 'AL0003' }]);
  await queue.whenIdle();

  const snapshot = queue.snapshot();
  assert.deepEqual(snapshot.results.map((entry) => entry.status), ['succeeded', 'failed', 'succeeded']);
  assert.match(snapshot.results[1].error.message, /upload failed/);
  assert.equal(new Set(snapshot.results.map((entry) => entry.task.queue_id)).size, 3);
  assert.deepEqual(snapshot.results.map((entry) => entry.task.queue_id), ['q1', 'q2', 'q3']);
});

test('queue can drop and clear items that have not started yet', async () => {
  const started = [];
  const runner = createGatedRunner(started);
  const queue = batch.createImportQueue({ run: runner.run });

  queue.enqueue([{ folderName: 'AL0001' }]);
  await tick();
  const queued = queue.enqueue([{ folderName: 'AL0002' }, { folderName: 'AL0003' }, { folderName: 'AL0004' }]);

  assert.equal(queue.removePending(queued[1].queue_id), true);
  assert.equal(queue.removePending('missing'), false);
  assert.equal(queue.clearPending(), 2);
  assert.deepEqual(queue.snapshot().pending, []);

  runner.release();
  await queue.whenIdle();
  assert.deepEqual(started, ['AL0001']);
  assert.deepEqual(queue.snapshot().results.map((entry) => entry.value.id), ['AL0001']);
});

test('queue notifies listeners on enqueue, start and finish', async () => {
  const events = [];
  const queue = batch.createImportQueue({
    run: async (task) => ({ id: task.folderName }),
    onChange: (snapshot) => events.push(`${snapshot.running ? 'running' : 'idle'}:${snapshot.active?.folderName || '-'}`),
  });

  queue.enqueue([{ folderName: 'AL0001' }]);
  await queue.whenIdle();

  assert.deepEqual(events, ['running:-', 'running:AL0001', 'running:-', 'idle:-']);
});

test('sequential import records a failed product and continues with later products', async () => {
  const groups = ['AL0001', 'AL0002', 'AL0003'].map((folderName) => ({ folderName }));
  const events = [];

  const result = await batch.runSequentialImport(
    groups,
    async (group) => {
      if (group.folderName === 'AL0002') throw new Error('upload failed');
      return { id: group.folderName };
    },
    (event) => events.push(`${event.phase}:${event.group.folderName}`),
  );

  assert.deepEqual(result.successes, [{ id: 'AL0001' }, { id: 'AL0003' }]);
  assert.deepEqual(result.failures.map((item) => item.folderName), ['AL0002']);
  assert.match(result.failures[0].error.message, /upload failed/);
  assert.deepEqual(events, [
    'start:AL0001', 'finish:AL0001',
    'start:AL0002', 'finish:AL0002',
    'start:AL0003', 'finish:AL0003',
  ]);
});


test('planner accepts File System Access files carrying __relativePath', () => {
  const { planLocalFolderImport } = require('../apps/web/local-folder-batch.js');
  // planner 只读 name/__relativePath/webkitRelativePath，不碰 Blob API；
  // Node 18 还没有全局 File（20 才加），这里做个同形替身即可跨版本跑。
  const makeFileLike = typeof File === 'function'
    ? (name) => new File([new Uint8Array([1, 2, 3])], name, { type: 'image/png' })
    : (name) => ({ name, size: 3, type: 'image/png' });
  const fakeFile = (relativePath, name) => {
    const file = makeFileLike(name);
    Object.defineProperty(file, '__relativePath', { value: relativePath, enumerable: false });
    return file;
  };

  const plan = planLocalFolderImport([
    fakeFile('批次/AL0900/1.png', '1.png'),
    fakeFile('批次/AL0900/2.png', '2.png'),
    fakeFile('批次/AL0901/1.png', '1.png'),
  ], new Set(['.png']));

  assert.equal(plan.mode, 'batch');
  assert.equal(plan.rootName, '批次');
  assert.deepEqual(plan.groups.map((group) => `${group.folderName}:${group.files.length}`), ['AL0900:2', 'AL0901:1']);
});
