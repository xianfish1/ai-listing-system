# 店铺刊登副本维护编辑器实施计划

## 任务 1：详情读取和原子保存 API

文件：

- `tests/test_product_line_flow.py`
- `tests/test_store_template_workflow_api.py`
- `services/api/storage.py`
- `services/api/main.py`

步骤：

1. 增加失败测试，覆盖副本详情包含 SKU、价格、图片顺序、尺码图和 readiness。
2. 增加失败测试，覆盖一次 PUT 同时保存副本字段、SKU 库存、SKU 价格和图片顺序。
3. 在存储层实现详情组装和原子更新，校验 SKU/价格必须属于当前副本。
4. 扩展 GET/PUT API 返回刷新后的详情和中文提示所需的稳定错误代码。
5. 运行相关 API 与存储测试。

## 任务 2：完整维护弹窗

文件：

- `tests/test_store_template_workflow_ui.py`
- `apps/web/index.html`
- `apps/web/app.js`
- `apps/web/styles.css`

步骤：

1. 增加失败的 UI 结构测试，要求基本信息、图片、尺码图、SKU 和交易信息控件存在。
2. 重建弹窗布局，保持桌面端高密度、移动端可滚动且不重叠。
3. 打开弹窗时读取详情，渲染商品图和 SKU 表格。
4. 支持 HTML5 拖拽排序，第一张同步显示为主图。
5. 支持替换尺码图，并把上传结果纳入保存请求。
6. 支持逐 SKU 库存、原价和折后价编辑，原价变更时默认计算六折。
7. 支持统一交易信息编辑和保存反馈。

## 任务 3：完整性提示与任务门禁

文件：

- `apps/web/app.js`
- `tests/test_store_template_workflow_ui.py`
- `tests/test_browser_schema_payload.py`

步骤：

1. 将 readiness 字段代码映射为可读中文。
2. 错误提示绑定编辑区域定位按钮。
3. 保存后以服务端返回的 readiness 更新门禁。
4. 验证 Browser-Use payload 使用维护后的图片、尺码图、SKU 价格和交易信息。

## 任务 4：验证与实际页面检查

命令：

```powershell
pytest -q tests/test_store_template_workflow_api.py tests/test_product_line_flow.py tests/test_store_template_workflow_ui.py tests/test_browser_schema_payload.py
pytest -q
node --check apps/web/app.js
git diff --check
```

完成后重启 `127.0.0.1:8793` 的服务，并使用 Playwright 检查：

- 桌面和移动端弹窗无内容重叠。
- 商品图能拖拽排序，首图状态正确。
- 尺码图可见并可替换。
- 不同尺码可保存不同价格。
- 库存、重量、包裹尺寸保存后重新打开仍存在。
- readiness 未通过时不能生成自动化任务。

