"""Validation protocol for the restricted AI browser takeover harness."""

from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping
import json
import subprocess
from typing import Any
from pathlib import Path

from services.api.subprocess_utils import no_console_window_kwargs


MAX_ACTIONS = 20
ALLOWED_ACTIONS = frozenset({"wait_for", "click", "fill", "select_shop", "save_draft", "verify"})
_FORBIDDEN_WORDS = ("发布", "刊登", "提交审核", "审核", "publish", "submit", "selector", "locator")
_FORBIDDEN_KEYS = frozenset({"eval", "javascript", "script", "expression", "code", "selector", "locator"})
_SEMANTIC_TARGET_ALIASES = {
    "site_and_shop_dropdown": "shop_options",
    "selected_shop": "shop_selected",
}


class HarnessPlanError(ValueError):
    """Stable, machine-readable error raised for an unsafe or invalid plan."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class BrowserHarness:
    """Call the constrained Node/CDP harness and never infer save success."""

    def __init__(self, *, ai_executor: Any = None, node_executable: str = "node") -> None:
        self.ai_executor = ai_executor
        self.node_executable = node_executable
        self.script_path = Path(__file__).with_name("mabang_browser_harness.cjs")

    def _node_call(self, mode: str, task_plan: dict[str, Any], context: dict[str, Any], cdp_url: str, *, plan: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"mode": mode, "task": task_plan, "context": context, "cdp_url": cdp_url}
        if plan is not None:
            payload["plan"] = plan
        completed = subprocess.run(
            [self.node_executable, str(self.script_path)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            **no_console_window_kwargs(),
        )
        marker = "MABANG_HARNESS_RESULT "
        for line in reversed((completed.stdout or "").splitlines()):
            if line.startswith(marker):
                result = json.loads(line[len(marker):])
                if isinstance(result, dict):
                    return result
        return {"ok": False, "status": "harness_blocked", "error": "harness_no_structured_result", "raw_output_tail": (completed.stdout or completed.stderr)[-2000:]}

    def takeover(self, task_plan: dict[str, Any], *, playwright_error: str, failed_stage: str, cdp_url: str, max_cost: float, timeout_seconds: int = 120) -> dict[str, Any]:
        snapshot = self._node_call("snapshot", task_plan, {"failed_stage": failed_stage}, cdp_url)
        if not snapshot.get("ok"):
            return {**snapshot, "status": "harness_blocked", "trace": []}
        if self.ai_executor is None:
            return {"ok": False, "status": "harness_blocked", "error": "harness_ai_unavailable", "trace": []}
        plan_result = self.ai_executor.build_takeover_plan(
            task_plan, playwright_error, snapshot.get("snapshot", {}), failed_stage, max_cost,
        )
        if not plan_result.get("ok"):
            fallback_plan = _deterministic_cnsc_picker_plan(playwright_error, failed_stage)
            if fallback_plan is not None:
                recovery = self._node_call(
                    "execute",
                    task_plan,
                    {"failed_stage": failed_stage, "timeout_seconds": timeout_seconds},
                    cdp_url,
                    plan=fallback_plan,
                )
                if recovery.get("ok") is True:
                    return {
                        "ok": True,
                        "status": "recovered",
                        "failed_stage": failed_stage,
                        "fallback": "deterministic_cnsc_picker",
                        "trace": recovery.get("trace", []),
                        "snapshot": snapshot.get("snapshot", {}),
                    }
            return {**plan_result, "status": "harness_blocked", "trace": []}
        try:
            validated = validate_harness_plan(
                normalize_harness_plan(plan_result["plan"]),
                allowed_shops=_configured_shop_identities(task_plan),
            )
        except HarnessPlanError as exc:
            return {"ok": False, "status": "harness_blocked", "error": exc.code, "message": str(exc), "trace": []}
        recovery = self._node_call("execute", task_plan, {"failed_stage": failed_stage, "timeout_seconds": timeout_seconds}, cdp_url, plan=validated)
        if recovery.get("ok") is not True:
            return {**recovery, "status": "harness_blocked"}
        return {"ok": True, "status": "recovered", "failed_stage": failed_stage, "trace": recovery.get("trace", []), "snapshot": snapshot.get("snapshot", {})}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in _FORBIDDEN_KEYS or _contains_forbidden(key) or _contains_forbidden(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(word in lowered for word in _FORBIDDEN_WORDS) or "javascript:" in lowered or "eval(" in lowered
    return False


def _validate_semantic_target(action: Mapping[str, Any]) -> None:
    target = _text(action.get("target"))
    if not target:
        raise HarnessPlanError("invalid_target", "click/fill/wait_for/verify requires a semantic target")
    # CSS/XPath and script-like selectors are deliberately outside this protocol.
    if target.startswith(("#", ".", "[", "/", "//")) or "=>" in target or "(" in target:
        raise HarnessPlanError("forbidden_action", "raw selectors or executable targets are not allowed")


def _configured_shop_identities(task_plan: Mapping[str, Any]) -> set[tuple[str, str, str]]:
    store = task_plan.get("store")
    targets = store.get("target_site_stores") if isinstance(store, Mapping) else []
    if not isinstance(targets, list):
        return set()
    return {
        (
            _text(target.get("site_code") or target.get("country")).upper(),
            _text(target.get("store_name") or target.get("name")).casefold(),
            _text(target.get("store_id") or target.get("mabang_store_id")),
        )
        for target in targets
        if isinstance(target, Mapping)
        and _text(target.get("site_code") or target.get("country"))
        and _text(target.get("store_name") or target.get("name"))
        and _text(target.get("store_id") or target.get("mabang_store_id"))
    }


def _deterministic_cnsc_picker_plan(playwright_error: str, failed_stage: str) -> dict[str, Any] | None:
    """Recover only the known collapsed CNSC picker when the AI returns no plan."""
    if playwright_error != "cnsc_target_site_not_found" or failed_stage != "select_cnsc_target_sites":
        return None
    return {
        "version": 1,
        "actions": [
            {"type": "click", "target": "shop_picker"},
            {"type": "wait_for", "target": "shop_options"},
        ],
    }


def normalize_harness_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt the narrowly-defined JSON variants returned by the configured AI model.

    The adapter only rewrites explicit protocol aliases observed in production.
    It never invents an action or turns arbitrary text into a browser selector.
    """
    if not isinstance(plan, Mapping):
        return plan  # type: ignore[return-value]
    normalized = deepcopy(dict(plan))
    actions = normalized.get("actions")
    if not isinstance(actions, list):
        return normalized

    normalized_actions: list[Any] = []
    for action in actions:
        if not isinstance(action, Mapping):
            normalized_actions.append(action)
            continue
        item = deepcopy(dict(action))
        declared_type = _text(item.get("type"))
        alias_type = _text(item.get("action"))
        if declared_type and alias_type and declared_type != alias_type:
            raise HarnessPlanError("ambiguous_action", "action and type must agree when both are supplied")
        if not declared_type and alias_type:
            item["type"] = alias_type
        item.pop("action", None)

        target = _text(item.get("target"))
        if target in _SEMANTIC_TARGET_ALIASES:
            item["target"] = _SEMANTIC_TARGET_ALIASES[target]

        if _text(item.get("type")) == "select_shop" and not all(_text(item.get(key)) for key in ("site", "name", "id")):
            parts = [part.strip() for part in target.split("/")]
            if len(parts) == 3 and all(parts):
                item.update({"site": parts[0], "name": parts[1], "id": parts[2]})
                item.pop("target", None)
        normalized_actions.append(item)
    normalized["actions"] = normalized_actions
    return normalized


def validate_harness_plan(
    plan: Mapping[str, Any],
    *,
    allowed_shops: set[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """Validate and return a normalized deep copy of a version-one action plan."""
    if not isinstance(plan, Mapping):
        raise HarnessPlanError("invalid_plan", "harness plan must be an object")
    if _contains_forbidden(plan):
        raise HarnessPlanError("forbidden_action", "publish or executable actions are not allowed")
    if type(plan.get("version")) is not int or plan.get("version") != 1:
        raise HarnessPlanError("invalid_version", "only harness plan version 1 is supported")
    actions = plan.get("actions")
    if not isinstance(actions, list):
        raise HarnessPlanError("invalid_actions", "actions must be an array")
    if len(actions) > MAX_ACTIONS:
        raise HarnessPlanError("action_limit_exceeded", f"at most {MAX_ACTIONS} actions are allowed")

    normalized = deepcopy(dict(plan))
    normalized_actions: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, Mapping):
            raise HarnessPlanError("invalid_action", "each action must be an object")
        action_type = _text(action.get("type"))
        if action_type not in ALLOWED_ACTIONS:
            raise HarnessPlanError("unknown_action", f"unsupported harness action: {action_type or '<missing>'}")
        item = deepcopy(dict(action))
        item["type"] = action_type
        if action_type in {"wait_for", "click", "verify"}:
            _validate_semantic_target(item)
            item["target"] = _text(item["target"])
        elif action_type == "fill":
            _validate_semantic_target(item)
            item["target"] = _text(item["target"])
            if "value" not in item:
                raise HarnessPlanError("invalid_action", "fill requires a value")
        elif action_type == "select_shop":
            identity = {field: _text(item.get(field)) for field in ("site", "name", "id")}
            if any(not value for value in identity.values()):
                raise HarnessPlanError("shop_identity_incomplete", "select_shop requires non-empty site, name and id")
            scoped_identity = (identity["site"].upper(), identity["name"].casefold(), identity["id"])
            if allowed_shops is not None and scoped_identity not in allowed_shops:
                raise HarnessPlanError("shop_target_not_allowed", "select_shop must match a configured target site store")
            item.update(identity)
        normalized_actions.append(item)
    normalized["version"] = 1
    normalized["actions"] = normalized_actions
    return normalized
