import json
import subprocess

import scripts.browser_harness as browser_harness_module
from scripts.browser_harness import BrowserHarness, HarnessPlanError, normalize_harness_plan, validate_harness_plan


def test_node_harness_hides_the_windows_console(monkeypatch) -> None:
    calls = {}

    def fake_run(*args, **kwargs):
        calls["args"] = args
        calls.update(kwargs)
        payload = {"ok": True, "snapshot": {}}
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout="MABANG_HARNESS_RESULT " + json.dumps(payload),
            stderr="",
        )

    monkeypatch.setattr(browser_harness_module.subprocess, "run", fake_run)

    result = BrowserHarness(node_executable="node.exe")._node_call(
        "snapshot", {}, {}, "http://127.0.0.1:9223"
    )

    assert result["ok"] is True
    assert calls.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", None)


def test_validate_harness_plan_accepts_cnsc_recovery_actions() -> None:
    plan = {
        "version": 1,
        "actions": [
            {"type": "wait_for", "target": "shop_options"},
            {"type": "select_shop", "site": "TW", "name": "海宁TW", "id": "59760739"},
        ],
    }
    validated = validate_harness_plan(plan)
    assert validated["actions"][1]["type"] == "select_shop"
    assert validated is not plan


def test_validate_harness_plan_rejects_publish_and_unknown_actions() -> None:
    for action in (
        {"type": "click", "target": "保存并刊登"},
        {"type": "eval", "script": "document.body.innerHTML=''"},
    ):
        try:
            validate_harness_plan({"version": 1, "actions": [action]})
        except HarnessPlanError as exc:
            assert exc.code in {"forbidden_action", "unknown_action"}
        else:
            raise AssertionError("unsafe action was accepted")


def test_validate_harness_plan_enforces_action_limit_and_required_identity() -> None:
    too_many = {"version": 1, "actions": [{"type": "wait_for", "target": "shop_options"}] * 21}
    try:
        validate_harness_plan(too_many)
    except HarnessPlanError as exc:
        assert exc.code == "action_limit_exceeded"
    else:
        raise AssertionError("action limit was not enforced")

    try:
        validate_harness_plan({"version": 1, "actions": [{"type": "select_shop", "site": "TW"}]})
    except HarnessPlanError as exc:
        assert exc.code == "shop_identity_incomplete"
    else:
        raise AssertionError("incomplete shop identity was accepted")


def test_validate_harness_plan_normalizes_action_type_and_rejects_selector_text() -> None:
    normalized = validate_harness_plan({"version": 1, "actions": [{"type": " click ", "target": "shop_picker"}]})
    assert normalized["actions"][0]["type"] == "click"

    for action in (
        {"type": "wait_for", "target": "shop_options", "hint": "use querySelector"},
        {"type": "wait_for", "target": "querySelector"},
    ):
        try:
            validate_harness_plan({"version": 1, "actions": [action]})
        except HarnessPlanError as exc:
            assert exc.code == "forbidden_action"
        else:
            raise AssertionError("selector text was accepted")

    try:
        validate_harness_plan({"version": True, "actions": []})
    except HarnessPlanError as exc:
        assert exc.code == "invalid_version"
    else:
        raise AssertionError("boolean version was accepted")


def test_normalize_harness_plan_adapts_the_observed_ai_cnsc_response() -> None:
    raw_plan = {
        "version": 1,
        "actions": [
            {"action": "wait_for", "target": "shop_picker"},
            {"action": "click", "target": "shop_picker"},
            {"action": "wait_for", "target": "site_and_shop_dropdown"},
            {"action": "select_shop", "target": "TW / 海宁TW / 59760739"},
            {"action": "verify", "target": "selected_shop"},
        ],
    }

    normalized = normalize_harness_plan(raw_plan)

    assert normalized == {
        "version": 1,
        "actions": [
            {"type": "wait_for", "target": "shop_picker"},
            {"type": "click", "target": "shop_picker"},
            {"type": "wait_for", "target": "shop_options"},
            {"type": "select_shop", "site": "TW", "name": "海宁TW", "id": "59760739"},
            {"type": "verify", "target": "shop_selected"},
        ],
    }


def test_takeover_normalizes_ai_plan_before_restricted_execution() -> None:
    raw_plan = {
        "version": 1,
        "actions": [
            {"action": "click", "target": "shop_picker"},
            {"action": "wait_for", "target": "site_and_shop_dropdown"},
            {"action": "select_shop", "target": "TW / 海宁TW / 59760739"},
        ],
    }

    class FakeAiExecutor:
        def build_takeover_plan(self, *args, **kwargs):
            return {"ok": True, "plan": raw_plan}

    class FakeHarness(BrowserHarness):
        def __init__(self):
            super().__init__(ai_executor=FakeAiExecutor())
            self.executed_plan = None

        def _node_call(self, mode, task_plan, context, cdp_url, *, plan=None):
            if mode == "snapshot":
                return {"ok": True, "snapshot": {"dom": "shop picker"}}
            self.executed_plan = plan
            return {"ok": True, "status": "recovered", "trace": []}

    harness = FakeHarness()

    result = harness.takeover(
        {"store": {"target_site_stores": [{"site_code": "TW", "store_name": "海宁TW", "store_id": "59760739"}]}},
        playwright_error="cnsc_target_site_not_found",
        failed_stage="select_cnsc_target_sites",
        cdp_url="http://127.0.0.1:9223",
        max_cost=0.25,
    )

    assert result["ok"] is True
    assert harness.executed_plan["actions"][1]["target"] == "shop_options"
    assert harness.executed_plan["actions"][2] == {"type": "select_shop", "site": "TW", "name": "海宁TW", "id": "59760739"}


def test_takeover_rejects_shop_outside_the_task_configuration() -> None:
    class FakeAiExecutor:
        def build_takeover_plan(self, *args, **kwargs):
            return {
                "ok": True,
                "plan": {"version": 1, "actions": [{"type": "select_shop", "site": "PH", "name": "其他店", "id": "999"}]},
            }

    class FakeHarness(BrowserHarness):
        def _node_call(self, mode, task_plan, context, cdp_url, *, plan=None):
            if mode == "snapshot":
                return {"ok": True, "snapshot": {"dom": "shop picker"}}
            raise AssertionError("an out-of-scope shop must never reach Node execution")

    result = FakeHarness(ai_executor=FakeAiExecutor()).takeover(
        {"store": {"target_site_stores": [{"site_code": "TW", "store_name": "海宁TW", "store_id": "59760739"}]}},
        playwright_error="cnsc_target_site_not_found",
        failed_stage="select_cnsc_target_sites",
        cdp_url="http://127.0.0.1:9223",
        max_cost=0.25,
    )

    assert result["ok"] is False
    assert result["error"] == "shop_target_not_allowed"


def test_takeover_uses_deterministic_picker_recovery_when_ai_returns_no_plan() -> None:
    class EmptyAiExecutor:
        def build_takeover_plan(self, *args, **kwargs):
            return {"ok": False, "diagnosis": "takeover_plan_failed", "error": "AI returned no text"}

    class FakeHarness(BrowserHarness):
        def __init__(self):
            super().__init__(ai_executor=EmptyAiExecutor())
            self.executed_plan = None

        def _node_call(self, mode, task_plan, context, cdp_url, *, plan=None):
            if mode == "snapshot":
                return {"ok": True, "snapshot": {"dom": "shop picker"}}
            self.executed_plan = plan
            return {"ok": True, "status": "recovered", "trace": []}

    harness = FakeHarness()
    result = harness.takeover(
        {"store": {"target_site_stores": [{"site_code": "TW", "store_name": "海宁TW", "store_id": "59760739"}]}},
        playwright_error="cnsc_target_site_not_found",
        failed_stage="select_cnsc_target_sites",
        cdp_url="http://127.0.0.1:9223",
        max_cost=0.25,
    )

    assert result["ok"] is True
    assert result["fallback"] == "deterministic_cnsc_picker"
    assert harness.executed_plan == {
        "version": 1,
        "actions": [
            {"type": "click", "target": "shop_picker"},
            {"type": "wait_for", "target": "shop_options"},
        ],
    }
