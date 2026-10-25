"""
Browser-Use 自适应诊断器（诊断专用，不执行浏览器操作）

当 Playwright 因页面结构变化而失败时，本模块使用项目已配置的 AI 连接
分析失败原因，并给出候选 CSS 选择器建议，写入 data/learned_selectors.json
供后续人工或 Playwright 改进使用。

安全边界（非常重要）：
- 本模块【只做文字诊断】：读取任务计划 + Playwright 报错，产出建议选择器。
- 本模块【绝不】声称任务已保存（status="saved"），也【绝不】操作浏览器。
- 真正的浏览器操作与“保存成功”证明始终来自 Playwright / 真实 Browser-Use，
  由 is_proven_saved_execution() 依据真实浏览器证据判定。
- 因此 AI 文字分析永远不能冒充执行结果，避免出现“假成功”。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.api.model_gateway import GenerationRequest, ModelGateway, ModelConnection
from services.api.credential_crypto import CredentialCrypto
from services.api.anthropic_messages_adapter import AnthropicMessagesAdapter
from services.api.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter
from services.api import settings, storage


class BrowserUseAdaptiveExecutor:
    """使用项目已配置的 AI 连接，对 Playwright 失败进行诊断并给出选择器建议。

    该类【只做诊断】，不执行任何浏览器操作，也不产生“已保存”结论。
    """

    def __init__(self, ai_connection: ModelConnection | None = None):
        self.ai_connection = ai_connection or self._get_default_ai_connection()
        self.crypto = CredentialCrypto(settings.get_ai_credentials_master_key())
        self.gateway = ModelGateway(
            crypto=self.crypto,
            adapters={
                "anthropic_messages": AnthropicMessagesAdapter(),
                "openai_chat_completions": OpenAIChatCompletionsAdapter(),
                "chat_completions": OpenAIChatCompletionsAdapter(),
            },
        )
        self.learned_selectors_path = settings.DATA_DIR / "learned_selectors.json"

    def _get_default_ai_connection(self) -> ModelConnection:
        """获取项目配置的默认 AI 连接（含未脱敏的加密密钥）。"""
        connection = storage.get_default_ai_model_connection_private()
        if connection is None:
            connections = storage.list_ai_model_connections()
            if not connections:
                raise RuntimeError("未配置 AI 模型连接，请在设置中添加 AI 连接")
            connection = storage.get_ai_model_connection_private(connections[0]["id"])
        if connection is None:
            raise RuntimeError("未配置 AI 模型连接，请在设置中添加 AI 连接")
        return ModelConnection.from_dict(connection)

    def diagnose_failure(
        self,
        task_plan: dict[str, Any],
        playwright_error: str = "",
        max_cost: float = 0.25,
    ) -> dict[str, Any]:
        """对 Playwright 失败进行 AI 诊断，返回建议选择器。

        Returns:
            诊断结果。字段 ok 表示诊断本身是否成功（而非任务是否保存）。
            成功时包含 learned_selectors / suggestions；【绝不】返回 status="saved"。
        """
        print("🔎 Browser-Use 诊断开始（诊断专用，不操作浏览器）")
        print(f"   AI 模型: {self.ai_connection.name} ({self.ai_connection.model_id})")
        print(f"   Playwright 错误: {playwright_error}")

        start_time = time.time()
        try:
            request = self._build_task_prompt(task_plan, playwright_error)
            analysis = self._analyze_task_with_ai(request)
            cost = self._calculate_cost(analysis.get("usage", {}))

            if cost > max_cost:
                return {
                    "ok": False,
                    "diagnosis": "cost_limit_exceeded",
                    "message": f"AI 诊断成本 ${cost:.4f} 超过限制 ${max_cost}",
                    "cost": cost,
                    "duration": time.time() - start_time,
                }

            learned_selectors = self._extract_selectors_from_analysis(analysis)
            if learned_selectors:
                self._save_learned_selectors(learned_selectors)

            duration = time.time() - start_time
            print(f"✅ Browser-Use 诊断完成 (耗时 {duration:.1f}秒, 成本 ${cost:.4f})")
            print(f"   建议选择器条目: {len(learned_selectors)}")

            return {
                "ok": True,
                "diagnosis": "selectors_suggested",
                "learned_selectors": learned_selectors,
                "ai_analysis": analysis.get("text", ""),
                "usage": analysis.get("usage", {}),
                "cost": cost,
                "duration": duration,
                "model_id": analysis.get("model_id", ""),
            }
        except Exception as exc:  # noqa: BLE001
            duration = time.time() - start_time
            print(f"❌ Browser-Use 诊断失败: {exc}")
            return {
                "ok": False,
                "diagnosis": "diagnosis_failed",
                "message": str(exc),
                "duration": duration,
            }

    def build_takeover_plan(
        self,
        task_plan: dict[str, Any],
        playwright_error: str,
        page_snapshot: dict[str, Any],
        failed_stage: str,
        max_cost: float = 0.25,
    ) -> dict[str, Any]:
        """Generate one validated action plan for the bounded browser takeover."""
        request = self._build_takeover_prompt(task_plan, playwright_error, page_snapshot, failed_stage)
        try:
            analysis = self._analyze_task_with_ai(request)
            cost = self._calculate_cost(analysis.get("usage", {}))
            if cost > max_cost:
                return {"ok": False, "diagnosis": "cost_limit_exceeded", "error": "harness_cost_limit_exceeded", "cost": cost}
            plan = analysis.get("parsed", {})
            if not isinstance(plan, dict) or plan.get("version") != 1 or not isinstance(plan.get("actions"), list):
                return {"ok": False, "diagnosis": "invalid_takeover_plan", "error": "harness_plan_invalid", "cost": cost}
            return {"ok": True, "plan": plan, "usage": analysis.get("usage", {}), "cost": cost, "model_id": analysis.get("model_id", "")}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "diagnosis": "takeover_plan_failed", "error": str(exc)[:500]}

    def _build_takeover_prompt(self, task_plan: dict[str, Any], playwright_error: str, page_snapshot: dict[str, Any], failed_stage: str) -> GenerationRequest:
        system = """你是受限浏览器恢复规划器。只输出 JSON {\"version\":1,\"actions\":[...]}。每个动作必须使用 \"type\" 键，绝不能使用 \"action\" 键。允许的 type 仅为 wait_for/click/fill/select_shop/save_draft/verify。target 只能是以下精确语义名称之一：shop_picker、shop_options、shop_selected、save_button、title_input、description_input、category_dialog、trade_info。选择店铺必须严格写成 {\"type\":\"select_shop\",\"site\":\"<任务站点>\",\"name\":\"<任务店铺名称>\",\"id\":\"<任务店铺ID>\"}，三项必须从任务中的 CNSC 目标店铺列表原样取值，且不得合并到 target。只允许恢复当前失败步骤，不能使用 CSS/XPath、JavaScript、eval、selector 或 locator。禁止发布、刊登、提交审核；save_draft 是唯一允许的保存动作。"""
        user = f"失败阶段：{failed_stage}\nPlaywright 错误：{playwright_error}\n页面快照：{json.dumps(page_snapshot, ensure_ascii=False)[:12000]}\n任务目标：{self._format_task_for_ai(task_plan)}\n只返回动作计划 JSON。"
        # deepseek-v4-flash is a reasoning model: it emits a `thinking` block before
        # the answer, and a 1200-token budget is consumed by thinking alone
        # (stop_reason=max_tokens, empty text). Leave room for reasoning + JSON.
        return GenerationRequest(system=system, user=user, max_tokens=4096, temperature=0.0)
    def _build_task_prompt(self, task_plan: dict[str, Any], playwright_error: str) -> GenerationRequest:
        """构建 AI 诊断提示。"""
        system_prompt = """你是一个浏览器自动化诊断专家。你的任务是分析马帮 ERP 页面自动化失败的原因，并提供准确的元素选择器建议。

规则：
1. 仔细分析任务描述和 Playwright 报错，推断可能变化的页面结构
2. 为每个操作目标提供多个候选选择器（按优先级排序）
3. 选择器应该：
   - 优先使用稳定的属性（data-testid, id, name）
   - 避免依赖容易变化的 class 名称
   - 使用文本匹配作为备选方案
4. 提供 CSS 选择器格式
5. 你只负责给出选择器建议，绝不假设任务已经完成或已保存

输出格式（JSON）:
{
  "selectors": {
    "save_button": ["button[data-action='save']", ".save-btn"],
    "title_input": ["input[name='title']", "#product-title"]
  },
  "warnings": ["可能需要等待类目对话框加载"],
  "confidence": 0.85
}"""

        task_description = self._format_task_for_ai(task_plan)
        user_prompt = f"""马帮 Shopee 商品详情页自动化失败诊断：

## Playwright 遇到的问题
{playwright_error or '页面结构可能已改变'}

## 需要完成的操作
{task_description}

## 要求
1. 根据报错推断哪些元素定位失败
2. 为相关操作提供 3-5 个候选选择器
3. 选择器应该稳定、精确、易于定位
4. 只输出选择器建议，不要声称任务已保存

请输出 JSON 格式的选择器映射。"""

        return GenerationRequest(
            system=system_prompt,
            user=user_prompt,
            max_tokens=2000,
            temperature=0.1,
        )

    def _format_task_for_ai(self, task_plan: dict[str, Any]) -> str:
        """将任务计划格式化为 AI 可理解的描述。"""
        lines = []
        inputs = task_plan.get("inputs", {})
        lines.append(f"店铺: {inputs.get('store_name', '')}")
        lines.append(f"类目: {inputs.get('marketplace_category_path', '')}")
        lines.append(f"标题: {inputs.get('listing_title', '')}")
        lines.append(f"描述: {(inputs.get('description') or '')[:100]}...")

        images = task_plan.get("uploads", {}).get("product_images", [])
        if images:
            lines.append(f"产品图: {len(images)} 张")

        skus = task_plan.get("skus", [])
        if skus:
            lines.append(f"SKU 变体: {len(skus)} 个")

        transaction = inputs.get("transaction_defaults", {})
        if transaction:
            lines.append(f"库存: {transaction.get('stock', '')}")
            lines.append(f"重量: {transaction.get('weight_kg', '')} kg")

        store = task_plan.get("store")
        target_sites = store.get("target_site_stores") if isinstance(store, dict) else []
        if isinstance(target_sites, list):
            identities = [
                " / ".join(
                    (
                        str(target.get("site_code") or target.get("country") or "").strip(),
                        str(target.get("store_name") or target.get("name") or "").strip(),
                        str(target.get("store_id") or target.get("mabang_store_id") or "").strip(),
                    ),
                )
                for target in target_sites
                if isinstance(target, dict)
                and str(target.get("site_code") or target.get("country") or "").strip()
                and str(target.get("store_name") or target.get("name") or "").strip()
                and str(target.get("store_id") or target.get("mabang_store_id") or "").strip()
            ]
            if identities:
                lines.append(f"CNSC 目标店铺: {'; '.join(identities)}")

        lines.append("")
        lines.append("关键操作:")
        lines.append("1. 填写店铺和类目")
        lines.append("2. 填写商品标题和描述")
        lines.append("3. 上传产品图片")
        lines.append("4. 填写 SKU 变体信息")
        lines.append("5. 填写交易信息(价格、库存、重量等)")
        lines.append("6. 只点击保存按钮(永远不点击保存并刊登)")
        lines.append("7. 定位保存成功对话框")
        lines.append("8. 关闭详情页")
        return "\n".join(lines)

    def _analyze_task_with_ai(self, request: GenerationRequest) -> dict[str, Any]:
        """使用 AI 分析任务并生成选择器建议。"""
        try:
            result = self.gateway.generate(self.ai_connection, request)
            try:
                parsed = json.loads(result.text)
            except json.JSONDecodeError:
                parsed = self._extract_json_from_text(result.text)
            return {
                "text": result.text,
                "parsed": parsed,
                "model_id": result.model_id,
                "usage": result.usage,
            }
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"AI 诊断失败: {exc}") from exc

    def _extract_json_from_text(self, text: str) -> dict[str, Any]:
        """从 AI 响应文本中提取 JSON。"""
        start_markers = ["```json", "```"]
        end_marker = "```"
        for start_marker in start_markers:
            if start_marker in text:
                start = text.find(start_marker) + len(start_marker)
                end = text.find(end_marker, start)
                if end > start:
                    json_str = text[start:end].strip()
                    try:
                        return json.loads(json_str)
                    except json.JSONDecodeError:
                        continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"selectors": {}, "warnings": ["AI 响应格式不正确"]}

    def _extract_selectors_from_analysis(self, analysis: dict[str, Any]) -> dict[str, Any]:
        """从 AI 分析中提取选择器。"""
        parsed = analysis.get("parsed", {})
        selectors = parsed.get("selectors", {})
        confidence = parsed.get("confidence", 0.7)

        learned = {}
        for key, selector_list in selectors.items():
            if not isinstance(selector_list, list):
                selector_list = [selector_list]
            learned[key] = [
                {
                    "selector": selector,
                    "confidence": confidence,
                    "source": "browser_use_ai_diagnosis",
                    "learned_at": datetime.now(timezone.utc).isoformat(),
                }
                for selector in selector_list
            ]
        return learned

    def _save_learned_selectors(self, learned_selectors: dict[str, Any]) -> None:
        """保存学习到的选择器（去重合并）。"""
        self.learned_selectors_path.parent.mkdir(parents=True, exist_ok=True)
        if self.learned_selectors_path.exists():
            try:
                existing = json.loads(self.learned_selectors_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = {}
        else:
            existing = {}

        for key, selector_list in learned_selectors.items():
            if key not in existing:
                existing[key] = []
            existing_selectors = {item["selector"] for item in existing[key]}
            for item in selector_list:
                if item["selector"] not in existing_selectors:
                    existing[key].append(item)
                    existing_selectors.add(item["selector"])

        self.learned_selectors_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"📝 选择器建议已保存到: {self.learned_selectors_path}")

    def _calculate_cost(self, usage: dict[str, int]) -> float:
        """估算 AI 调用成本。"""
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        model_id = (self.ai_connection.model_id or "").lower()

        if "gpt-4o" in model_id or "gpt-4-turbo" in model_id:
            cost = (input_tokens * 2.50 + output_tokens * 10.0) / 1_000_000
        elif "gpt-4" in model_id:
            cost = (input_tokens * 30.0 + output_tokens * 60.0) / 1_000_000
        elif "gpt-3.5" in model_id:
            cost = (input_tokens * 0.50 + output_tokens * 1.50) / 1_000_000
        elif "claude-3-5-sonnet" in model_id:
            cost = (input_tokens * 3.0 + output_tokens * 15.0) / 1_000_000
        elif "claude-3-opus" in model_id:
            cost = (input_tokens * 15.0 + output_tokens * 75.0) / 1_000_000
        elif "claude-3-haiku" in model_id or "claude-3-sonnet" in model_id:
            cost = (input_tokens * 0.25 + output_tokens * 1.25) / 1_000_000
        elif "deepseek" in model_id:
            cost = (input_tokens * 0.14 + output_tokens * 0.28) / 1_000_000
        else:
            cost = (input_tokens * 1.0 + output_tokens * 2.0) / 1_000_000
        return cost


def main():
    """诊断器命令行测试。"""
    import argparse

    parser = argparse.ArgumentParser(description="Browser-Use 自适应诊断器测试")
    parser.add_argument("--task-plan", required=True, help="任务计划 JSON 文件路径")
    parser.add_argument("--playwright-error", default="", help="Playwright 错误信息")
    parser.add_argument("--max-cost", type=float, default=0.25, help="最大成本限制（美元）")
    args = parser.parse_args()

    task_plan_path = Path(args.task_plan)
    if not task_plan_path.exists():
        print(f"错误: 任务计划文件不存在: {task_plan_path}")
        sys.exit(1)

    task_plan = json.loads(task_plan_path.read_text(encoding="utf-8"))
    executor = BrowserUseAdaptiveExecutor()
    result = executor.diagnose_failure(
        task_plan=task_plan,
        playwright_error=args.playwright_error,
        max_cost=args.max_cost,
    )

    print("\n" + "=" * 60)
    print("诊断结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
