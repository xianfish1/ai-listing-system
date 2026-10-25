# 发布说明模板

复制本文件为 `docs/releases/v<版本>.md`（例如 `docs/releases/v1.2.0.md`），替换占位内容即可使用。

发布时发布说明留空，系统会优先读取该文件；也可以把内容直接贴进设置页「发布说明」，或用 `--notes` / `--notes-file` 传给 `scripts/publish_release.py`。

---

## vX.Y.Z · YYYY-MM-DD

- **类型**：正式版 / 预发布
- **安装包**：`dist/ai-listing-system-X.Y.Z.zip`（校验值见 Release 中同名 `.sha256` 资产）

### 新增

-

### 修复

-

### 变更与不兼容

- 无

### 升级提示

- 在线更新会先把旧文件备份到 `storage/updates/backups/`，不会改动 `data/`、`storage/` 和 `.env`；失败自动回滚。
- 新增依赖：无 / 需要执行 `pip install -r requirements.txt`。
- 更新后自动重启 API；如使用自定义端口，请确认 `.env` 里的 `API_PORT`。
- 配置或数据结构是否有变化：无。

### 已知问题

-
