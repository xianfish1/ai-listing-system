# 店铺模板、AI 描述与马帮保存闭环实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将马帮真实类目、店铺活动、版本化店铺模板、Anthropic 兼容 AI 描述、可检查刊登副本和自动保存 Worker 串成可生产试运行的闭环。

**架构：** 保留现有 Python 标准库 HTTP API、JSON/Postgres JSONB 兼容存储和原生 Web 前端。新增小型领域服务处理密钥、AI 协议、描述生成、促销活动、页面导入和统一就绪检查；`storage.py` 继续充当事务门面，但不再把模板或基础商品变化同步覆盖既有副本和任务。浏览器侧复用现有 Chrome/CDP 只读采集和 Playwright 保存执行器，任务只消费不可变快照。

**技术栈：** Python 3、pytest、标准库 `Decimal`/`urllib`/`json`、`cryptography.fernet`、现有 Postgres JSONB 兼容层、原生 HTML/CSS/JavaScript、Node `node:test`、Chrome CDP、现有 Browser-Use/Playwright 执行器。

---

## 文件结构

### 新建文件

- `services/api/credential_crypto.py`：使用主密钥加密和解密 AI API Key，提供严格去敏边界。
- `services/api/model_gateway.py`：定义模型连接、统一生成请求/结果和适配器注册表。
- `services/api/anthropic_messages_adapter.py`：实现 Anthropic Messages API 兼容请求、响应解析和错误归一化。
- `services/api/description_generation.py`：处理组合生成、完全生成、受保护事实注入、HTML 清理和确定性校验。
- `services/api/store_promotions.py`：规范化店铺活动目录、活动状态和可读中文标签。
- `services/api/mabang_page_import.py`：原子完成当前页面导入、类目规则自动激活、活动目录替换和模板冲突预览。
- `services/api/template_readiness.py`：统一模板、副本和任务创建前的业务校验及中文错误码。
- `services/api/size_chart_uploads.py`：校验并保存 base64 尺码图上传。
- `scripts/migrate_store_template_workflow.py`：备份、幂等迁移和旧任务隔离。
- `tests/test_credential_crypto.py`：密钥加密、轮换和去敏测试。
- `tests/test_model_gateway.py`：模型连接、Anthropic 请求和切换测试。
- `tests/test_description_generation.py`：两种生成模式和事实保护测试。
- `tests/test_store_promotions.py`：店铺隔离、状态和中文标签测试。
- `tests/test_mabang_page_import.py`：原子只读导入、冲突和失败不覆盖测试。
- `tests/test_template_readiness.py`：模板、副本和任务三层就绪检查测试。
- `tests/test_store_template_workflow_api.py`：管理员配置、多模板复制、副本编辑和任务唯一性 API 测试。
- `tests/test_store_template_workflow_ui.py`：模板弹窗、类目弹窗、尺码、图片和副本编辑静态契约测试。
- `tests/test_store_template_workflow_migration.py`：迁移幂等、旧默认值禁用和旧任务隔离测试。
- `tests/fixtures/mabang/page_import_complete.json`：脱敏的真实页面导入结构样本。
- `tests/fixtures/mabang/page_import_second_category.json`：字段集合不同的第二类目样本。

### 修改文件

- `requirements.txt`：加入 `cryptography`。
- `services/api/db_store.py`：声明新 JSONB 集合。
- `services/api/storage.py`：新增集合门面、模板版本、副本修订、不可变任务和新状态；删除同步覆盖行为及执行器默认值。
- `services/api/category_profile_service.py`：有效采集完成后自动激活版本，移除新流程的审核状态机。
- `services/api/category_rules.py`：提供当前规则兼容性检查和稳定缺项代码。
- `services/api/main.py`：注册模型连接、描述方案、页面导入、活动、上传、副本保存和任务取消 API。
- `services/api/settings.py`：增加主密钥和上传目录配置。
- `scripts/browser_use_runner.py`：增加统一页面导入审计脚本和执行前配置过期检查。
- `scripts/collect_mabang_category_catalog.py`：复用统一页面导入结果，继续支持独立目录采集 CLI。
- `scripts/collect_mabang_category_profile.py`：将完成动作切换为自动激活语义。
- `scripts/mabang_playwright_executor.cjs`：补充交易信息字段映射和系统故障分类；保留保存成功关闭行为。
- `scripts/automation_worker.py`：支持 `needs_confirmation`、系统故障暂停和不可变快照校验。
- `apps/web/index.html`：增加 AI 连接、模板弹窗、活动、交易信息、尺码、尺码图和副本编辑结构；隐藏遗留字段。
- `apps/web/app.js`：实现新 API、自动六折计算、多选弹窗、上传和描述重试交互。
- `apps/web/styles.css`：实现紧凑弹窗、层级类目、缩略图、复选尺码和错误状态响应式布局。
- `tests/test_category_profile_service.py`：候选/审核断言改为自动激活断言。
- `tests/test_category_profile_collection_api.py`：删除 publish/reject 步骤，验证完成即生效。
- `tests/test_product_line_flow.py`：把同步覆盖旧测试替换成版本和快照不可变测试。
- `tests/test_marketplace_category_catalog_collection.py`：验证统一导入只允许真实 GET 数据。
- `tests/test_browser_use_runner.py`：验证导入禁写、配置过期阻断和保存关闭证据。
- `tests/test_automation_worker.py`：验证人工确认、系统暂停和继续下一任务。
- `tests/mabang_playwright_executor.test.cjs`：验证交易信息填写和系统故障不继续。
- `tests/test_marketplace_category_picker_ui.py`：验证弹窗层级、叶子限制和采集状态。
- `tests/test_automation_status_ui.py`：验证中文任务状态映射。
- `docs/STANDALONE_DEPLOYMENT.md`：补充 AI 主密钥、模型连接、迁移和单 Worker 试运行说明。
- `docs/AI_ASSISTANCE.md`：用模型网关和描述生成方案取代 DeepSeek 单模型说明。

## 任务 0：建立可提交的仓库基线

**文件：**
- 创建：`.gitignore`
- 检查：`data/`、`storage/`、`artifacts/`、`.venv*` 和浏览器 Profile 目录

- [ ] **步骤 1：确认当前仓库没有已提交历史**

运行：

```powershell
git rev-list --count HEAD 2>$null
git status --short
```

预期：第一条命令没有可用 `HEAD`，第二条显示全部项目文件为未跟踪。

- [ ] **步骤 2：创建忽略规则**

创建根目录 `.gitignore`：

```gitignore
__pycache__/
*.py[cod]
.pytest_cache/
.venv*/
node_modules/
data/
storage/
artifacts/
exports/
*.log
*.tmp
*.bak
.env
.env.*
*chrome-profile*/
*browser-profile*/
outputs/ai-listing-system/.env
outputs/ai-listing-system/tmp-*
outputs/ai-listing-system/mabang-*.png
outputs/ai-listing-system/smoke-input.json
outputs/ai-listing-system/.superpowers/brainstorm/*/.events
```

- [ ] **步骤 3：检查待提交文件不包含凭据或生产数据**

运行：

```powershell
git status --short
rg -n --hidden -g '!.git/**' -g '!data/**' -g '!storage/**' -g '!artifacts/**' "(api[_-]?key|password|token)\s*[:=]\s*['\"][^'\"]+" .
```

预期：只出现示例、环境变量读取或测试假值；没有真实 API Key、马帮密码、会话 token 或生产数据文件。

- [ ] **步骤 4：创建源代码基线提交**

运行：

```powershell
git add .gitignore AGENTS.md docs task_plan.md findings.md progress.md
git add outputs/ai-listing-system/.gitignore outputs/ai-listing-system/README.md outputs/ai-listing-system/CONTEXT.md outputs/ai-listing-system/requirements.txt
git add outputs/ai-listing-system/apps outputs/ai-listing-system/services outputs/ai-listing-system/workers outputs/ai-listing-system/scripts outputs/ai-listing-system/tests outputs/ai-listing-system/docs
git commit -m "chore: establish repository baseline"
```

预期：产生首个提交；根目录截图、临时 Excel/JS、`.env`、生产数据和浏览器 Profile 不在暂存区。`git status --short` 只剩被明确排除的工作目录或无输出。

## 任务 1：扩展存储集合并实现安全迁移

**文件：**
- 修改：`services/api/db_store.py`
- 修改：`services/api/storage.py:295-315`
- 创建：`scripts/migrate_store_template_workflow.py`
- 创建：`tests/test_store_template_workflow_migration.py`
- 测试：`tests/test_storage_atomic.py`

- [ ] **步骤 1：编写失败的集合与迁移测试**

在 `tests/test_store_template_workflow_migration.py` 写入：

```python
from copy import deepcopy

from scripts.migrate_store_template_workflow import migrate_store
from services.api.db_store import COLLECTIONS


NEW_COLLECTIONS = {
    "ai_model_connections",
    "description_generation_profiles",
    "store_template_versions",
    "store_promotion_activities",
    "store_promotion_catalog_runs",
    "mabang_page_import_runs",
    "listing_copy_revisions",
}


def test_new_workflow_collections_are_persisted():
    assert NEW_COLLECTIONS.issubset(set(COLLECTIONS))


def test_migration_marks_unverified_templates_and_legacy_jobs_without_inventing_defaults():
    data = {
        "category_profiles": [{"id": "cp-1", "published_version_id": "cpv-1"}],
        "category_profile_versions": [{"id": "cpv-1", "profile_id": "cp-1", "status": "published"}],
        "store_templates": [{"id": "t-1", "mabang_category_id": "100244"}],
        "store_listing_copies": [{"id": "c-1", "template_id": "t-1", "description": ""}],
        "automation_jobs": [{"id": "j-1", "listing_copy_id": "c-1", "status": "ready"}],
    }
    result = migrate_store(data, now="2026-07-15T00:00:00Z")
    template = data["store_templates"][0]
    listing_copy = data["store_listing_copies"][0]
    job = data["automation_jobs"][0]

    assert result["templates_needing_import"] == 1
    assert template["readiness_status"] == "needs_mabang_import"
    assert "stock" not in template
    assert "weight_kg" not in template
    assert listing_copy["description_status"] == "pending"
    assert job["status"] == "needs_regeneration"
    assert data["category_profiles"][0]["active_version_id"] == "cpv-1"
    assert data["category_profile_versions"][0]["status"] == "active"
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
python -m pytest tests/test_store_template_workflow_migration.py -q
```

预期：FAIL，缺少迁移模块和新集合。

- [ ] **步骤 3：实现集合和幂等迁移**

在 `db_store.py` 和 `_empty_store()` 中加入 `NEW_COLLECTIONS`。在迁移脚本中实现：

```python
WORKFLOW_COLLECTIONS = (
    "ai_model_connections",
    "description_generation_profiles",
    "store_template_versions",
    "store_promotion_activities",
    "store_promotion_catalog_runs",
    "mabang_page_import_runs",
    "listing_copy_revisions",
)


def migrate_store(data: dict, *, now: str) -> dict[str, int]:
    for collection in WORKFLOW_COLLECTIONS:
        data.setdefault(collection, [])
    result = {"templates_needing_import": 0, "jobs_needing_regeneration": 0}
    for profile in data.setdefault("category_profiles", []):
        profile.setdefault("active_version_id", profile.get("published_version_id") or "")
    for version in data.setdefault("category_profile_versions", []):
        if version.get("status") == "published":
            version["status"] = "active"
    for template in data.setdefault("store_templates", []):
        if not template.get("category_profile_version_id"):
            template["readiness_status"] = "needs_mabang_import"
            result["templates_needing_import"] += 1
        template.pop("executor_defaults", None)
    for listing_copy in data.setdefault("store_listing_copies", []):
        listing_copy.setdefault("description_status", "ready" if listing_copy.get("description") else "pending")
    for job in data.setdefault("automation_jobs", []):
        if job.get("status") in {"ready", "claimed", "running"} and not job.get("execution_snapshot"):
            job["status"] = "needs_regeneration"
            job["updated_at"] = now
            result["jobs_needing_regeneration"] += 1
    return result
```

CLI 必须支持 `--dry-run`、`--apply`、`--data-dir`，`--apply` 前复制带时间戳备份；默认只输出摘要，不写文件。

- [ ] **步骤 4：运行迁移与存储测试**

运行：

```powershell
python -m pytest tests/test_store_template_workflow_migration.py tests/test_storage_atomic.py -q
```

预期：PASS。

- [ ] **步骤 5：Commit**

```powershell
git add services/api/db_store.py services/api/storage.py scripts/migrate_store_template_workflow.py tests/test_store_template_workflow_migration.py
git commit -m "feat: add versioned store workflow storage"
```

## 任务 2：建立统一就绪检查和交易默认值规则

**文件：**
- 创建：`services/api/template_readiness.py`
- 修改：`services/api/storage.py:2273-2760`
- 创建：`tests/test_template_readiness.py`
- 修改：`tests/test_product_line_flow.py`

- [ ] **步骤 1：编写失败的就绪和六折测试**

创建 `tests/test_template_readiness.py`：

```python
from decimal import Decimal

from services.api.template_readiness import calculate_sale_price, check_store_template


def test_sale_price_is_sixty_percent_with_currency_precision():
    assert calculate_sale_price("100", decimal_places=2) == Decimal("60.00")
    assert calculate_sale_price("99.99", decimal_places=0) == Decimal("60")


def test_template_reports_chinese_missing_fields_without_executor_fallbacks():
    result = check_store_template(
        {
            "category_profile_version_id": "v-1",
            "attribute_values": {"brand": "NoBrand"},
            "promotion_activity_id": "",
            "normal_price": "100",
            "sizes": [],
            "description_profile_version_id": "",
        },
        category_version={"fields": [{"field_id": "brand", "label": "品牌", "required": True}]},
        promotion=None,
    )
    assert result.status == "promotion_unconfigured"
    assert {item.code for item in result.errors} >= {
        "promotion_missing",
        "stock_missing",
        "weight_missing",
        "package_length_missing",
        "package_width_missing",
        "package_height_missing",
        "sizes_missing",
        "description_profile_missing",
    }
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
python -m pytest tests/test_template_readiness.py -q
```

预期：FAIL，`template_readiness` 不存在。

- [ ] **步骤 3：实现纯函数就绪服务**

实现稳定类型：

```python
@dataclass(frozen=True)
class ReadinessIssue:
    code: str
    field: str
    message: str


@dataclass(frozen=True)
class ReadinessResult:
    status: str
    errors: tuple[ReadinessIssue, ...]
    warnings: tuple[ReadinessIssue, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.errors
```

使用 `Decimal(str(value)) * Decimal("0.60")` 和 `ROUND_HALF_UP`。模板检查必须调用 `validate_attribute_values()`，并要求有效活动、库存、重量、包裹长宽高、至少一个尺码、尺码图需求、描述方案。副本检查再加入商品图片、SKU、价格和描述状态；任务检查再加入当前规则版本、活动有效期和活动任务唯一性。

- [ ] **步骤 4：替换旧 `_check_automation_readiness` 并删除回退值**

`storage.py` 只负责读取相关集合并调用纯函数。删除浏览器输入中的：

```python
stock = value or "999"
weight_kg = value or "0.25"
package = value or {"length_cm": "28", "width_cm": "22", "height_cm": "2"}
```

缺值必须保留为空并阻止任务创建。

- [ ] **步骤 5：运行定向回归**

运行：

```powershell
python -m pytest tests/test_template_readiness.py tests/test_category_rules.py tests/test_product_line_flow.py -q
```

预期：PASS；原来断言硬编码默认值或模板同步覆盖的测试已改为新规则断言。

- [ ] **步骤 6：Commit**

```powershell
git add services/api/template_readiness.py services/api/storage.py tests/test_template_readiness.py tests/test_product_line_flow.py
git commit -m "feat: centralize listing readiness rules"
```

## 任务 3：类目规则自动激活和店铺活动目录

**文件：**
- 修改：`services/api/category_profile_service.py`
- 修改：`services/api/category_rules.py`
- 创建：`services/api/store_promotions.py`
- 修改：`services/api/storage.py:646-750`
- 修改：`tests/test_category_profile_service.py`
- 修改：`tests/test_category_profile_collection_api.py`
- 创建：`tests/test_store_promotions.py`

- [ ] **步骤 1：把审核流测试改成自动激活测试**

在 `tests/test_category_profile_service.py` 增加：

```python
def test_complete_valid_capture_activates_version_and_supersedes_previous():
    data = _store_with_published_version()
    run = create_collection_run(data, _run_payload(), now="2026-07-15T00:00:00Z")
    result = complete_collection_run(data, run["id"], _changed_capture(), now="2026-07-15T00:01:00Z")

    assert result["version"]["status"] == "active"
    assert result["profile"]["active_version_id"] == result["version"]["id"]
    assert not any("review" in key for key in result["version"])
    assert data["category_profile_versions"][0]["status"] == "superseded"
```

API 测试删除 `/publish` 和 `/reject` 调用，断言 `complete` 响应即返回 `active`。

- [ ] **步骤 2：编写活动目录失败测试**

创建 `tests/test_store_promotions.py`：

```python
from services.api.store_promotions import normalize_promotion_catalog, promotion_label


def test_promotions_are_store_scoped_and_use_readable_labels():
    items = normalize_promotion_catalog(
        store_id="store-1",
        raw_items=[{
            "activityId": "928232107737088",
            "activityName": "暑期六折",
            "discountRate": "0.60",
            "startTime": "2026-07-01T00:00:00Z",
            "endTime": "2026-07-31T23:59:59Z",
            "status": "enabled",
        }],
        now="2026-07-15T00:00:00Z",
    )
    assert items[0]["store_id"] == "store-1"
    assert items[0]["status"] == "available"
    assert promotion_label(items[0]) == "暑期六折｜六折（优惠 40%）｜2026-07-01 至 2026-07-31"
```

- [ ] **步骤 3：运行测试确认失败**

运行：

```powershell
python -m pytest tests/test_category_profile_service.py tests/test_category_profile_collection_api.py tests/test_store_promotions.py -q
```

预期：FAIL，旧版本仍为 `candidate` 且活动服务不存在。

- [ ] **步骤 4：实现自动激活和活动规范化**

`complete_collection_run()` 在完成结构校验和 schema hash 后，原子执行：

```python
for existing in data["category_profile_versions"]:
    if existing.get("profile_id") == profile_id and existing.get("status") == "active":
        existing["status"] = "superseded"
version["status"] = "active"
version["activated_at"] = now
profile["active_version_id"] = version["id"]
```

活动服务把折扣率统一为销售比例 `0.60`，派生 `discount_percent=40`，按当前时间计算 `not_started/available/expired/disabled`。同一 `store_id + activity_id` 更新状态但保留历史字段，不跨店复用。

- [ ] **步骤 5：运行测试**

运行：

```powershell
python -m pytest tests/test_category_profile_service.py tests/test_category_profile_collection_api.py tests/test_store_promotions.py tests/test_category_rules.py -q
```

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
git add services/api/category_profile_service.py services/api/category_rules.py services/api/store_promotions.py services/api/storage.py tests/test_category_profile_service.py tests/test_category_profile_collection_api.py tests/test_store_promotions.py
git commit -m "feat: activate collected category rules and promotions"
```

## 任务 4：安全模型连接和 Anthropic 兼容网关

**文件：**
- 修改：`requirements.txt`
- 修改：`services/api/settings.py`
- 创建：`services/api/credential_crypto.py`
- 创建：`services/api/model_gateway.py`
- 创建：`services/api/anthropic_messages_adapter.py`
- 创建：`tests/test_credential_crypto.py`
- 创建：`tests/test_model_gateway.py`

- [ ] **步骤 1：编写密钥和协议失败测试**

```python
def test_api_key_is_encrypted_and_never_serialized(fernet_key):
    crypto = CredentialCrypto(fernet_key)
    encrypted = crypto.encrypt("secret-key")
    assert encrypted != "secret-key"
    assert crypto.decrypt(encrypted) == "secret-key"
    assert redact_connection({"encrypted_api_key": encrypted})["api_key_configured"] is True
    assert "encrypted_api_key" not in redact_connection({"encrypted_api_key": encrypted})


def test_anthropic_adapter_uses_messages_contract(fake_http):
    adapter = AnthropicMessagesAdapter(http_open=fake_http.open)
    result = adapter.generate(
        connection=ModelConnection(base_url="https://ai.example/v1", model_id="model-a", api_version="2023-06-01"),
        api_key="secret-key",
        request=GenerationRequest(system="system", user="product", max_tokens=300, temperature=0.4),
    )
    assert fake_http.request.url == "https://ai.example/v1/messages"
    assert fake_http.request.headers["x-api-key"] == "secret-key"
    assert result.text == "generated description"
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
python -m pytest tests/test_credential_crypto.py tests/test_model_gateway.py -q
```

预期：FAIL，三个模块不存在。

- [ ] **步骤 3：实现密钥、类型和适配器**

`credential_crypto.py` 使用 `Fernet`，主密钥来自 `AI_CREDENTIALS_MASTER_KEY`。缺少或非法主密钥时，连接写入 API 返回配置错误，不以明文降级。

`model_gateway.py` 定义：

```python
@dataclass(frozen=True)
class GenerationRequest:
    system: str
    user: str
    max_tokens: int
    temperature: float


@dataclass(frozen=True)
class GenerationResult:
    text: str
    model_id: str
    stop_reason: str
    usage: dict[str, int]


class ModelGateway:
    def generate(self, connection: dict, request: GenerationRequest) -> GenerationResult:
        adapter = self.adapters[connection["protocol"]]
        api_key = self.crypto.decrypt(connection["encrypted_api_key"])
        return adapter.generate(connection=ModelConnection.from_dict(connection), api_key=api_key, request=request)
```

适配器只接受 `anthropic_messages`，设置 `content-type`、`x-api-key`、`anthropic-version`，解析所有 `type == "text"` 内容块。HTTP 错误只保留状态码和去敏响应摘要。

- [ ] **步骤 4：运行测试和导入检查**

运行：

```powershell
python -m pytest tests/test_credential_crypto.py tests/test_model_gateway.py -q
python -m compileall services/api -q
```

预期：两条命令退出码均为 0。

- [ ] **步骤 5：Commit**

```powershell
git add requirements.txt services/api/settings.py services/api/credential_crypto.py services/api/model_gateway.py services/api/anthropic_messages_adapter.py tests/test_credential_crypto.py tests/test_model_gateway.py
git commit -m "feat: add Anthropic-compatible model gateway"
```

## 任务 5：描述生成方案、事实保护和生成历史

**文件：**
- 创建：`services/api/description_generation.py`
- 修改：`services/api/storage.py`
- 修改：`workers/executor/deepseek_adapter.py`
- 创建：`tests/test_description_generation.py`
- 修改：`docs/AI_ASSISTANCE.md`

- [ ] **步骤 1：编写两种模式和失败状态测试**

```python
def test_combined_generation_only_replaces_product_section(fake_gateway):
    service = DescriptionGenerationService(fake_gateway)
    result = service.generate(
        profile=combined_profile(opening="Welcome", after_sales="Contact us first"),
        product={"product_name": "Hoodie"},
        connection=connection(),
    )
    assert result.status == "ready"
    assert result.description == "Welcome\n\nSoft Hoodie for daily wear.\n\nContact us first"


def test_full_generation_injects_each_protected_fact_once(fake_gateway):
    fake_gateway.text = "Intro {{SHIPPING_TIME}} Product {{PACKAGE_CONTENTS}} Service {{AFTER_SALES}}"
    result = DescriptionGenerationService(fake_gateway).generate(
        profile=full_profile(),
        product={"package_quantity": 1, "package_item": "Hoodie"},
        connection=connection(),
    )
    assert result.status == "ready"
    assert result.description.count("7-12 days") == 1
    assert result.description.count("1*Hoodie") == 1


def test_generation_failure_creates_pending_result(fake_gateway):
    fake_gateway.error = RuntimeError("HTTP 503")
    result = DescriptionGenerationService(fake_gateway).generate(
        profile=combined_profile(), product={"product_name": "Hoodie"}, connection=connection()
    )
    assert result.status == "pending"
    assert result.description == ""
    assert "secret" not in result.error
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
python -m pytest tests/test_description_generation.py -q
```

预期：FAIL，描述服务不存在。

- [ ] **步骤 3：实现描述服务**

组合模式仅把模型输出当作 `product_section`。完全模式要求三个占位符各一次，替换后再检查冲突的时间和数量。允许标签固定为：

```python
ALLOWED_TAGS = {"p", "br", "strong", "b", "em", "i", "ul", "ol", "li"}
PROTECTED_PLACEHOLDERS = ("{{SHIPPING_TIME}}", "{{PACKAGE_CONTENTS}}", "{{AFTER_SALES}}")
```

生成历史写入 `ai_outputs`，`output_type="listing_description"`，包含 `listing_copy_id`、方案版本、连接修订、模型 ID、耗时、状态和去敏错误。已有 `DeepSeekAdapter` 保留标题/属性建议兼容，但描述新流程只能通过 `ModelGateway`。

- [ ] **步骤 4：运行测试**

运行：

```powershell
python -m pytest tests/test_description_generation.py tests/test_model_gateway.py tests/test_api_boundaries.py -q
```

预期：PASS。

- [ ] **步骤 5：更新 AI 文档并提交**

`docs/AI_ASSISTANCE.md` 说明管理员连接、两种生成方案、受保护事实、`描述待生成` 和 Worker 不调用 AI。

```powershell
git add services/api/description_generation.py services/api/storage.py workers/executor/deepseek_adapter.py tests/test_description_generation.py docs/AI_ASSISTANCE.md
git commit -m "feat: generate protected listing descriptions"
```

## 任务 6：版本化模板、不可变副本和唯一任务

**文件：**
- 修改：`services/api/storage.py:756-1460`
- 修改：`tests/test_product_line_flow.py`
- 创建：`tests/test_template_readiness.py`

- [ ] **步骤 1：用不可变测试替换旧同步测试**

```python
def test_template_update_creates_version_without_mutating_existing_copy_or_job(tmp_path):
    template = _complete_template(tmp_path)
    listing_copy = _copy_from_template(template)
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    original_copy = deepcopy(storage.get_item("store_listing_copies", listing_copy["id"]))
    original_payload = storage.build_automation_payload(job["id"])

    updated = storage.update_store_template(template["id"], {"stock": 25, "normal_price": "200"})

    assert updated["version"] == template["version"] + 1
    assert storage.get_item("store_listing_copies", listing_copy["id"]) == original_copy
    assert storage.build_automation_payload(job["id"]) == original_payload


def test_one_listing_copy_allows_only_one_active_job(tmp_path):
    listing_copy = _ready_listing_copy(tmp_path)
    first = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    with pytest.raises(storage.ActiveAutomationJobExists):
        storage.create_automation_job_from_listing_copy(listing_copy["id"])
    storage.cancel_automation_job(first["id"], reason="operator_rebuild")
    second = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    assert second["execution_snapshot_hash"] != ""
```

- [ ] **步骤 2：运行测试确认旧行为失败**

运行：

```powershell
python -m pytest tests/test_product_line_flow.py -k "immutable or one_active_job" -q
```

预期：FAIL；旧代码同步改写副本/任务或允许重复任务。

- [ ] **步骤 3：实现版本和快照边界**

`create_store_template()` 创建版本 1 并把完整快照追加到 `store_template_versions`。`update_store_template()` 只更新模板当前记录并追加新版本，不遍历 `store_listing_copies` 或 `automation_jobs`。

`copy_base_product_to_store_templates()` 对每个完整模板：

```python
listing_copy = {
    "id": str(uuid.uuid4()),
    "template_id": template["id"],
    "template_version_id": template["current_version_id"],
    "category_profile_snapshot": deepcopy(category_snapshot),
    "promotion_snapshot": deepcopy(promotion),
    "transaction_info": deepcopy(template["transaction_info"]),
    "sizes": list(template["sizes"]),
    "size_chart_snapshot": deepcopy(size_chart),
    "description_status": "pending",
    "revision": 1,
}
```

每个模板独立调用描述服务；失败只让当前副本保持 `pending`。任务创建必须验证副本最新修订、当前规则/活动和唯一活动任务，然后保存 `execution_snapshot` 与 SHA-256。

- [ ] **步骤 4：增加显式副本更新和任务取消**

`update_store_listing_copy()` 允许编辑类目值、活动、价格、交易信息、尺码图和描述，成功后递增 `revision` 并追加 `listing_copy_revisions`。`cancel_automation_job()` 只允许取消 `ready/claimed`；`running` 任务必须先由 Worker 停止或租约释放。

- [ ] **步骤 5：运行核心回归**

运行：

```powershell
python -m pytest tests/test_product_line_flow.py tests/test_template_readiness.py tests/test_description_generation.py -q
```

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
git add services/api/storage.py tests/test_product_line_flow.py tests/test_template_readiness.py
git commit -m "feat: freeze listing copies and automation snapshots"
```

## 任务 7：统一马帮当前页面导入和 API

**文件：**
- 创建：`services/api/mabang_page_import.py`
- 修改：`scripts/browser_use_runner.py:880-1240`
- 修改：`scripts/collect_mabang_category_catalog.py`
- 修改：`scripts/collect_mabang_category_profile.py`
- 修改：`services/api/main.py`
- 创建：`tests/test_mabang_page_import.py`
- 修改：`tests/test_marketplace_category_catalog_collection.py`
- 修改：`tests/test_browser_use_runner.py`
- 创建：`tests/fixtures/mabang/page_import_complete.json`
- 创建：`tests/fixtures/mabang/page_import_second_category.json`

- [ ] **步骤 1：保存两个脱敏真实结构 fixture**

fixture 顶层结构固定为：

```json
{
  "source": "mabang_current_page",
  "page_url": "https://www.mabangerp.com/index.php",
  "store": {"id": "store-1", "name": "菲律宾童装店"},
  "category": {"id": "900001", "path": "童装 > 上衣 > 卫衣", "is_leaf": true},
  "category_catalog": {"discovery_source": "mabang_shopee_getCategory_api", "category_count": 2, "categories": []},
  "required_fields": [],
  "selected_values": {},
  "variation": {"required": true, "axes": ["color", "size"]},
  "promotions": []
}
```

fixture 必须来自真实响应结构脱敏，不包含 cookie、query token、账号或完整会话 URL。

- [ ] **步骤 2：编写原子导入和禁写失败测试**

```python
def test_page_import_activates_rule_and_replaces_only_selected_store_promotions(fixture):
    data = _store_with_other_store_promotion()
    result = import_current_page(data, fixture, conflict_action="update", now="2026-07-15T00:00:00Z")
    assert result["category_version"]["status"] == "active"
    assert result["template"]["store_id"] == "store-1"
    assert {item["store_id"] for item in data["store_promotion_activities"]} == {"store-1", "store-2"}


def test_invalid_partial_import_writes_nothing(fixture):
    data = _store()
    before = deepcopy(data)
    fixture["required_fields"] = []
    with pytest.raises(MabangPageImportError):
        import_current_page(data, fixture, conflict_action="update", now="2026-07-15T00:00:00Z")
    assert data == before


def test_browser_import_script_blocks_write_requests():
    script = build_mabang_page_import_audit_script({})
    assert "POST" in script and "PUT" in script and "DELETE" in script
    assert "preventDefault" in script
    assert "保存" in script and "发布" in script and "提交" in script
```

- [ ] **步骤 3：运行测试确认失败**

运行：

```powershell
python -m pytest tests/test_mabang_page_import.py tests/test_marketplace_category_catalog_collection.py tests/test_browser_use_runner.py -q
```

预期：FAIL，统一导入服务和脚本不存在。

- [ ] **步骤 4：实现只读采集脚本**

脚本先安装 `fetch`、`XMLHttpRequest`、表单提交和点击保护：只允许同源 `GET/HEAD`，阻断 `POST/PUT/PATCH/DELETE` 及保存、发布、提交、确认动作。然后读取当前店铺、叶子类目、控件定义和值；复用真实 `getCategory` 响应；从 Performance Resource 中筛选名称包含 `promotion|discount|activity` 的同源 GET 端点并读取结构化活动列表，页面 Vue 状态仅作只读回退。

输出唯一标记 `MABANG_PAGE_IMPORT_RESULT`，不得输出 cookie、Authorization 或带 query 的 URL。

- [ ] **步骤 5：实现原子服务和冲突动作**

`mabang_page_import.py` 先在深拷贝上规范化全部数据；验证完整后一次写入目录、规则版本、活动目录、导入运行和模板版本。`conflict_action` 仅允许 `update` 或 `save_as_new`，默认 `update`。失败只追加去敏失败运行，不修改当前有效业务数据。

- [ ] **步骤 6：注册 API**

新增：

```text
POST /api/mabang-page-imports
GET  /api/mabang-page-imports/{id}
POST /api/mabang-page-imports/{id}/complete
GET  /api/store-promotions?store_id=...
```

创建返回 `201`，完成返回导入摘要、模板冲突和中文缺项；旧 publish/reject 路由返回 `410 Gone`，提示类目规则已改为有效采集后自动激活。

- [ ] **步骤 7：运行采集/API 测试**

运行：

```powershell
python -m pytest tests/test_mabang_page_import.py tests/test_category_profile_collection_api.py tests/test_marketplace_category_catalog_collection.py tests/test_browser_use_runner.py -q
```

预期：PASS。

- [ ] **步骤 8：Commit**

```powershell
git add services/api/mabang_page_import.py services/api/main.py scripts/browser_use_runner.py scripts/collect_mabang_category_catalog.py scripts/collect_mabang_category_profile.py tests/test_mabang_page_import.py tests/test_marketplace_category_catalog_collection.py tests/test_browser_use_runner.py tests/fixtures/mabang
git commit -m "feat: import real Mabang template data read-only"
```

## 任务 8：模型、描述、模板和副本 API

**文件：**
- 修改：`services/api/main.py:68-630`
- 修改：`services/api/storage.py`
- 创建：`services/api/size_chart_uploads.py`
- 创建：`tests/test_store_template_workflow_api.py`

- [ ] **步骤 1：编写端到端 API 失败测试**

```python
def test_admin_can_configure_model_profile_copy_product_and_retry_description(api, fernet_key, tmp_path):
    connection = api.post("/api/ai/model-connections", {
        "name": "Anthropic 兼容主模型",
        "protocol": "anthropic_messages",
        "base_url": "https://ai.example/v1",
        "api_key": "secret-key",
        "model_id": "model-a",
        "api_version": "2023-06-01",
    })
    assert "api_key" not in connection and connection["api_key_configured"] is True

    profile = api.post("/api/description-profiles", {
        "name": "Fly_Show 组合描述",
        "mode": "combined",
        "opening": "Welcome to Fly_Show",
        "after_sales": "Contact us first.",
        "protected_facts": {"shipping_time": "7-12 days"},
    })
    result = api.post("/api/store-listing-copies/from-base", {
        "base_product_id": "base-1",
        "template_ids": ["template-1", "template-2"],
    })
    assert len(result["created"]) == 2
    assert {item["description_status"] for item in result["created"]} <= {"ready", "pending"}
```

另加测试：连接健康测试、设置默认、密钥轮换、描述重试保留历史、副本 PUT、上传非法 MIME/超限、同副本第二活动任务返回 `409`、任务取消。

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
python -m pytest tests/test_store_template_workflow_api.py -q
```

预期：FAIL，新 API 不存在。

- [ ] **步骤 3：实现上传服务**

`size_chart_uploads.py` 接收 JSON：

```json
{"file_name":"size-chart.png","content_type":"image/png","content_base64":"..."}
```

只允许 PNG/JPEG/WebP，解码后上限 5 MiB，使用魔数验证而不是信任扩展名，保存到 `settings.STORAGE_DIR / "uploads" / "size-charts" / f"{uuid_value}.{extension}"`，响应只返回资产 ID、文件名、内容类型和受控预览 URL。

- [ ] **步骤 4：注册完整 API**

新增或修改路由：

```text
GET/POST /api/ai/model-connections
PUT      /api/ai/model-connections/{id}
POST     /api/ai/model-connections/{id}/test
POST     /api/ai/model-connections/{id}/set-default
POST     /api/ai/model-connections/{id}/rotate-key
GET/POST /api/description-profiles
PUT      /api/description-profiles/{id}
POST     /api/uploads/size-chart
PUT      /api/store-listing-copies/{id}
POST     /api/store-listing-copies/{id}/description/generate
POST     /api/automation/jobs/{id}/cancel
```

所有错误通过 `ApiRequestError` 返回稳定 `code`、中文 `error` 和字段问题列表；绝不回传密钥、内部 ID 标签或堆栈。

- [ ] **步骤 5：运行 API 与安全回归**

运行：

```powershell
python -m pytest tests/test_store_template_workflow_api.py tests/test_api_boundaries.py tests/test_product_line_flow.py -q
```

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
git add services/api/main.py services/api/storage.py services/api/size_chart_uploads.py tests/test_store_template_workflow_api.py
git commit -m "feat: expose store workflow APIs"
```

## 任务 9：重构店铺模板和多模板选择界面

**文件：**
- 修改：`apps/web/index.html:52-153`
- 修改：`apps/web/app.js:1-760,1002-1260`
- 修改：`apps/web/styles.css`
- 创建：`tests/test_store_template_workflow_ui.py`
- 修改：`tests/test_marketplace_category_picker_ui.py`

- [ ] **步骤 1：编写失败的 UI 契约测试**

```python
def test_store_template_ui_hides_legacy_ids_and_has_complete_controls():
    html = INDEX.read_text(encoding="utf-8")
    for forbidden in ["马帮店铺编号", "马帮类目编号", "马帮折扣编号", "产品线", "标题后缀"]:
        assert forbidden not in html
    for required_id in [
        "mabang-page-import-btn",
        "store-template-category-dialog",
        "store-template-promotion",
        "store-template-stock",
        "store-template-weight-kg",
        "store-template-package-length-cm",
        "store-template-package-width-cm",
        "store-template-package-height-cm",
        "store-template-size-options",
        "store-template-custom-size",
        "store-template-size-chart-preview",
        "store-template-size-chart-upload",
        "store-template-description-profile",
        "copy-template-picker-dialog",
    ]:
        assert f'id="{required_id}"' in html


def test_sale_price_is_read_only_and_calculated_in_javascript():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    assert 'id="store-template-sale-price"' in html and "readonly" in html
    assert "calculateFortyPercentDiscount" in script
    assert 'Decimal' not in script
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
python -m pytest tests/test_store_template_workflow_ui.py tests/test_marketplace_category_picker_ui.py -q
```

预期：FAIL，旧字段仍可见且弹窗/控件不存在。

- [ ] **步骤 3：重建店铺模板表单**

使用 `dialog` 或现有模态模式实现平台类目选择器和模板选择器。类目结果保持层级导航、搜索、叶子限制、路径、规则字段数和采集状态；原始 ID 只在 JS state 中传 API，不进入可见 DOM 文本。

尺码使用固定复选框：

```javascript
const STANDARD_SIZES = ["100", "110", "120", "130", "140", "150", "S", "M", "L", "XL", "XXL", "XXXL"];
```

自定义尺码使用 `trim().toUpperCase()` 去重并成为当前模板复选框。原价输入变化时使用字符串十进制换算到分，再乘 60 并按币种精度显示，售价只读。

- [ ] **步骤 4：实现活动、交易信息和尺码图**

活动选项显示 `label`，无可用活动时禁用并显示 `活动未配置`。交易字段使用数字输入和单位后缀。上传读取文件为 base64，成功后更新缩略图和资产 ID；图片容器使用固定尺寸，避免加载后布局跳动。

- [ ] **步骤 5：实现多模板弹窗**

`复制到店铺模板` 打开可搜索多选弹窗，列出店铺、模板、类目、活动、价格、尺码和完整性。不完整项保留显示但 checkbox disabled，确认后一次提交选中模板 ID。

- [ ] **步骤 6：运行 UI 语法与契约测试**

运行：

```powershell
node --check apps/web/app.js
python -m pytest tests/test_store_template_workflow_ui.py tests/test_marketplace_category_picker_ui.py -q
```

预期：语法检查和测试均通过。

- [ ] **步骤 7：Commit**

```powershell
git add apps/web/index.html apps/web/app.js apps/web/styles.css tests/test_store_template_workflow_ui.py tests/test_marketplace_category_picker_ui.py
git commit -m "feat: rebuild store template workflow UI"
```

## 任务 10：店铺刊登副本检查、编辑和任务生成界面

**文件：**
- 修改：`apps/web/index.html:132-190`
- 修改：`apps/web/app.js:1045-1480`
- 修改：`apps/web/styles.css`
- 修改：`tests/test_store_template_workflow_ui.py`
- 修改：`tests/test_automation_status_ui.py`

- [ ] **步骤 1：编写副本编辑和中文状态失败测试**

```python
def test_listing_copy_editor_supports_save_retry_and_manual_task_creation():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    for control in [
        "listing-copy-editor-dialog",
        "listing-copy-description",
        "listing-copy-description-retry",
        "listing-copy-save",
        "listing-copy-create-job",
        "listing-copy-readiness-errors",
    ]:
        assert f'id="{control}"' in html
    assert "/description/generate" in script
    assert "saveListingCopy" in script
    assert "createAutomationJobForListingCopy" in script


def test_all_task_statuses_have_chinese_labels():
    script = APP.read_text(encoding="utf-8")
    for label in ["待领取", "已领取", "执行中", "已保存", "执行失败", "需要人工确认", "需要重新生成", "已取消"]:
        assert label in script
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
python -m pytest tests/test_store_template_workflow_ui.py tests/test_automation_status_ui.py -q
```

预期：FAIL，副本编辑器和完整状态映射不存在。

- [ ] **步骤 3：实现副本编辑器**

打开副本时加载快照、SKU、价格、交易信息、尺码图和描述。保存调用 `PUT /api/store-listing-copies/{id}`，成功后刷新 `revision` 和就绪检查。重试描述前使用确认弹窗，成功后更新编辑器；失败保留当前文本并显示 `描述待生成`。

“生成自动化任务”按钮只在 `readiness.ready === true` 时启用，点击后单独调用任务 API；副本保存函数不得隐式调用任务 API。

- [ ] **步骤 4：实现中文状态和错误列表**

```javascript
const TASK_STATUS_LABELS = {
  ready: "待领取",
  claimed: "已领取",
  running: "执行中",
  saved: "已保存",
  failed: "执行失败",
  needs_confirmation: "需要人工确认",
  needs_regeneration: "需要重新生成",
  canceled: "已取消",
};
```

错误列表显示服务端中文 `message`，调试详情放在折叠区域，不显示原始马帮 ID 或 API Key。

- [ ] **步骤 5：运行测试和语法检查**

运行：

```powershell
node --check apps/web/app.js
python -m pytest tests/test_store_template_workflow_ui.py tests/test_automation_status_ui.py -q
```

预期：PASS。

- [ ] **步骤 6：Commit**

```powershell
git add apps/web/index.html apps/web/app.js apps/web/styles.css tests/test_store_template_workflow_ui.py tests/test_automation_status_ui.py
git commit -m "feat: add listing copy review and task controls"
```

## 任务 11：Worker 新状态、交易信息和系统故障暂停

**文件：**
- 修改：`scripts/mabang_playwright_executor.cjs`
- 修改：`scripts/browser_use_runner.py:1358-1645`
- 修改：`scripts/automation_worker.py:25-410`
- 修改：`services/api/storage.py:1195-1386`
- 修改：`tests/mabang_playwright_executor.test.cjs`
- 修改：`tests/test_browser_use_runner.py`
- 修改：`tests/test_automation_worker.py`

- [ ] **步骤 1：编写交易信息和异常分类失败测试**

Node 测试增加：

```javascript
test('fills transaction information from immutable snapshot', async () => {
  const page = buildTransactionPage();
  await executor.fillTransactionInformation(page.frame, {
    stock: 20,
    weight_kg: '0.35',
    package_length_cm: '30',
    package_width_cm: '24',
    package_height_cm: '3',
  });
  assert.deepEqual(page.values(), { stock: '20', weight: '0.35', length: '30', width: '24', height: '3' });
});
```

Python 测试增加：

```python
def test_ambiguous_save_becomes_needs_confirmation_and_does_not_retry():
    result = worker_with_execution({"status": "blocked", "error": "save_result_ambiguous", "save_clicked": True}).run_once()
    assert result["status"] == "needs_confirmation"
    assert client.finishes[0]["status"] == "needs_confirmation"
    assert runner.call_count == 1


def test_system_failure_pauses_worker_before_claiming_next_job():
    worker = worker_with_execution({"status": "blocked", "error": "page_structure_changed", "system_failure": True})
    first = worker.run_once()
    second = worker.run_once()
    assert first["status"] == "paused"
    assert second["status"] == "paused"
    assert client.claim_count == 1
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
node --test tests/mabang_playwright_executor.test.cjs
python -m pytest tests/test_browser_use_runner.py tests/test_automation_worker.py -q
```

预期：新增用例 FAIL。

- [ ] **步骤 3：实现交易信息映射和执行前检查**

Playwright 执行器按中文 label、控件 name 和已验证选择器定位库存、重量和包裹长宽高；每次填写后读取页面值精确比较。缺控件或值回读不一致返回商品级字段错误，不使用默认值。

`browser_use_runner.py` 在启动执行器前验证 `execution_snapshot_hash`、当前规则版本和活动有效期；不一致返回 `configuration_expired`，不打开保存流程。

- [ ] **步骤 4：实现状态分类和暂停**

Worker 映射：

```python
NEEDS_CONFIRMATION_ERRORS = {"save_result_ambiguous", "saved_success_dialog_not_unique"}
SYSTEM_FAILURE_ERRORS = {"needs_manual_login_or_verification", "page_structure_changed", "save_button_not_unique"}
```

保存已点击但结果不足时完成任务为 `needs_confirmation`，不自动重试。系统错误设置进程内 `paused_reason`，轮询循环不再 claim；操作员重启 Worker 或明确恢复后才继续。商品字段错误为 `failed`，下一轮可以领取其他任务。

- [ ] **步骤 5：验证保存成功关闭与继续领取没有回归**

运行：

```powershell
node --test tests/mabang_playwright_executor.test.cjs
python -m pytest tests/test_browser_use_runner.py tests/test_automation_worker.py tests/test_product_line_flow.py -q
```

预期：PASS，并保留以下既有断言：`save_clicked=true`、`publish_clicked=false`、`page_closed=true`、合法 `close_method`；保存成功后关闭当前任务页面，下一次 `run_once()` 继续领取下一任务。

- [ ] **步骤 6：Commit**

```powershell
git add scripts/mabang_playwright_executor.cjs scripts/browser_use_runner.py scripts/automation_worker.py services/api/storage.py tests/mabang_playwright_executor.test.cjs tests/test_browser_use_runner.py tests/test_automation_worker.py tests/test_product_line_flow.py
git commit -m "feat: harden worker save loop and transaction fields"
```

## 任务 12：全量验证、迁移演练和单店生产试运行

**文件：**
- 修改：`docs/STANDALONE_DEPLOYMENT.md`
- 修改：`progress.md`
- 生成但不提交：`artifacts/store-template-workflow/`

- [ ] **步骤 1：运行完整自动测试**

运行：

```powershell
python -m pytest -q
node --test tests/mabang_playwright_executor.test.cjs
python -m compileall services workers scripts -q
node --check apps/web/app.js
```

预期：全部退出码为 0；pytest 无失败；Node 测试无失败；Python 和 JavaScript 语法检查通过。

- [ ] **步骤 2：执行迁移 dry-run 和幂等演练**

运行：

```powershell
python -m scripts.migrate_store_template_workflow --data-dir data --dry-run
New-Item -ItemType Directory -Force artifacts/store-template-workflow/migration-copy | Out-Null
Copy-Item data/store.json artifacts/store-template-workflow/store-before.json
Copy-Item data/store.json artifacts/store-template-workflow/migration-copy/store.json
python -m scripts.migrate_store_template_workflow --data-dir artifacts/store-template-workflow/migration-copy --apply
python -m scripts.migrate_store_template_workflow --data-dir artifacts/store-template-workflow/migration-copy --dry-run
```

预期：第一次输出模板/副本/任务迁移摘要；隔离副本 apply 成功；第二次 dry-run 的新增和状态变更计数均为 0。不得直接对生产 `data/store.json` 执行 `--apply`。

- [ ] **步骤 3：启动 API 和单 Worker**

确认受控环境已经注入主密钥，再启动：

```powershell
if (-not $env:AI_CREDENTIALS_MASTER_KEY) { throw "AI_CREDENTIALS_MASTER_KEY 未配置" }
.\scripts\run_api.ps1 -HostName "127.0.0.1" -Port 8787 -CdpUrl "http://127.0.0.1:9223" -NoAutomationWorker
.\scripts\run_automation_worker.ps1 -ApiUrl "http://127.0.0.1:8787" -CdpUrl "http://127.0.0.1:9223" -Once
```

预期：API 健康检查通过；首期只运行一个 Worker。主密钥不得写入仓库、PowerShell 历史归档或日志。

- [ ] **步骤 4：浏览器视觉验收**

打开 `http://127.0.0.1:8787/`，分别检查 1440x900、1024x768 和 390x844：

- 商品维护多模板弹窗可搜索、多选，禁用模板显示缺项。
- 店铺模板类目弹窗只允许叶子类目，路径和采集状态可见。
- 活动、交易信息、尺码复选框、自定义尺码和尺码图不重叠。
- 店铺刊登副本可编辑、保存、重试描述和手动生成任务。
- 所有原始马帮编号、产品线和标题后缀均不可见。
- 控制台无 error，动态内容不引起按钮或表格列跳动。

截图保存到 `artifacts/store-template-workflow/ui-desktop.png` 和 `ui-mobile.png`。

- [ ] **步骤 5：真实马帮只读采集验收**

在已登录的专用 Chrome 中打开一个真实商品编辑页，先执行页面导入，不执行保存：

```powershell
if (-not $env:MABANG_TEST_STORE_ID) { throw "MABANG_TEST_STORE_ID 未配置" }
python -m scripts.collect_mabang_category_catalog --country PH --store-id $env:MABANG_TEST_STORE_ID --output artifacts/store-template-workflow/category-catalog.json
```

预期：采集结果 `discovery_source=mabang_shopee_getCategory_api`，类目数和 ID 一致；系统页面能选择真实叶子类目；导入日志证明没有 POST/PUT/PATCH/DELETE 和保存/发布点击。

- [ ] **步骤 6：单商品保存草稿验收**

选择一个测试商品和一个完整模板：

1. 创建店铺刊登副本并生成描述。
2. 人工检查并保存副本。
3. 手动生成自动化任务。
4. 运行单 Worker 领取任务。
5. 核对马帮草稿存在、任务为 `saved`、`publish_clicked=false`、`page_closed=true`。
6. 再放入第二个已检查任务，确认 Worker 可以继续领取下一项。

保存去敏证据到 `artifacts/store-template-workflow/live-acceptance.json`，不得保存 cookie、密码、API Key 或带 query 的会话 URL。

- [ ] **步骤 7：更新部署文档和进度**

`docs/STANDALONE_DEPLOYMENT.md` 写明主密钥注入、模型连接测试、迁移、单 Worker 限制、暂停恢复和禁止发布。`progress.md` 记录所有实际命令、通过数量、迁移计数、真实采集类目数、草稿 Parent SKU 和去敏证据路径。

- [ ] **步骤 8：最终提交**

```powershell
git add docs/STANDALONE_DEPLOYMENT.md progress.md
git commit -m "docs: record store workflow production rollout"
git status --short
```

预期：提交成功；`artifacts/`、生产数据、浏览器 Profile 和凭据文件不在提交中。

## 计划执行门槛

- 不得在任务 1 的迁移测试通过前修改生产数据。
- 不得在任务 7 的禁写测试通过前连接真实马帮页面做导入。
- 不得在任务 8 的副本就绪和唯一任务测试通过前启动自动领取。
- 不得在任务 11 的发布保护、保存证据和系统暂停测试通过前进行真实保存。
- 不得把模型失败降级为自动切换其他模型。
- 不得把模板、基础商品或类目规则更新同步覆盖已有副本和在途任务。
- 不得提交 `data/`、`storage/`、`artifacts/`、浏览器 Profile、API Key、马帮 token 或带 query 的会话 URL。
