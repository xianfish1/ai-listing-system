from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from services.api import settings


# 一个马帮账号可以同时跑几个上架 Worker（槽位）。1 = 老行为（一个账号一只浏览器、
# 一次一单）；2 起就为同一账号再开独立的 Chrome（独立 CDP 端口 + 独立 Profile），
# 于是同一个账号也能 2~3 单并行。上限 4：每个槽位都是一只 Chromium + 一个 node
# 执行器，槽位越多内存/CPU 与「同账号多会话」的风险越高。
MAX_WORKER_SLOTS_PER_ACCOUNT = 4


GROUP_FIELDS = {
    "ai": {"default_connection_id", "color_connection_id", "request_timeout_seconds"},
    "ai_listing_generation": {
        "generate_title_on_import",
        "generate_description_on_import",
        "fill_attributes_into_description",
    },
    "product_sku": {
        "auto_assign_on_import",
        "prefix",
        "number_width",
        "next_number",
    },
    "mabang": {
        "workspace_url", "session_status", "last_tested_at", "login_account", "login_password",
        "headless",
    },
    "worker": {
        "cdp_url", "poll_seconds", "lease_seconds", "auto_start", "auto_restart",
        "max_attempts", "restart_required", "status", "last_heartbeat_at",
        "slots_per_account", "start_on_demand", "idle_shutdown_minutes",
    },
    "nas": {"asset_root", "last_tested_at", "scan_mode", "max_files"},
    "store_collection": {
        "auto_focus_after_import", "last_collected_at", "enabled",
        "interval_minutes", "max_attempts", "last_error",
    },
    "storage_cleanup": {
        "enabled", "temp_retention_days", "screenshot_retention_days",
        "export_retention_days", "log_retention_days",
        # 商品素材：保留期（清理门槛）与压缩参数（上架完成后延后多少天、压到多长边、JPEG 质量）。
        "assets_retention_days",
        "compact_enabled", "compact_delay_days", "compact_max_edge", "compact_jpeg_quality",
    },
    "cnsc": {"price_site_order"},
    "display": {"cross_border_enabled"},
    "network": {"lan_enabled"},
    "updates": {
        "repo", "api_url", "include_prerelease", "auto_check", "token",
        "last_checked_at", "last_check_error", "latest_version",
    },
    "safety": {"save_draft_only", "publish_allowed", "editable"},
}


def _env_lan_default() -> bool:
    """首次运行时：启动脚本把 API_HOST 写成非回环地址，就默认开启局域网访问。

    这样老的启动方式（run_api.ps1 -HostName 0.0.0.0）升级后不会突然只能
    本机访问；设置页保存过一次之后，以运行时配置为准。
    """

    host = str(settings.API_HOST or "").strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host not in {"", "127.0.0.1", "localhost", "::1"}


_GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GITHUB_REPO_URL_RE = re.compile(
    r"^(?:https?://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)


def normalize_github_repo(value: Any) -> str:
    """把 "owner/name"、"https://github.com/owner/name"、"git@github.com:owner/name.git" 归一成 "owner/name"。

    空值表示未配置，返回空串。格式不对时抛 ValueError —— 设置页要立即给出反馈，
    而不是把坏值存进去、等到点「检查更新」才报错。
    """

    repo = str(value or "").strip()
    if not repo:
        return ""
    match = _GITHUB_REPO_URL_RE.match(repo)
    if match:
        repo = match.group(1)
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    if not _GITHUB_REPO_RE.match(repo):
        raise ValueError("GitHub 仓库必须写成 owner/name 或 GitHub 仓库地址")
    return repo


def default_configuration() -> dict[str, dict[str, Any]]:
    return {
        "ai": {"default_connection_id": "", "color_connection_id": "", "request_timeout_seconds": 60},
        "ai_listing_generation": {
            "generate_title_on_import": False,
            "generate_description_on_import": False,
            "fill_attributes_into_description": True,
        },
        "product_sku": {
            "auto_assign_on_import": False,
            "prefix": "",
            "number_width": 4,
            "next_number": 1,
        },
        "mabang": {
            "workspace_url": settings.MABANG_WORKSPACE_URL,
            "session_status": "unknown",
            "last_tested_at": "",
            "login_account": "",
            "login_password": "",
            # 无头启动自动化/Worker 浏览器：不显示窗口，适合无人值守跑批。
            # 有头模式才能人工完成马帮滑块/安全验证。
            "headless": False,
        },
        "worker": {
            "cdp_url": settings.WORKER_CDP_URL,
            "poll_seconds": settings.WORKER_POLL_SECONDS,
            "lease_seconds": settings.WORKER_LEASE_SECONDS,
            "auto_start": True,
            "auto_restart": True,
            "max_attempts": 3,
            "restart_required": False,
            "status": "offline",
            "last_heartbeat_at": "",
            # 每个马帮账号的并行槽位数（1 = 一个账号一次一单）。
            "slots_per_account": 1,
            # 按需启动：平时 Worker（以及它那只采集浏览器）不跑，有任务才唤醒，
            # 空闲满 idle_shutdown_minutes 分钟自动关闭。关掉这个开关就回到
            # 「API 起来就把 Worker 拉起来、一直挂着」的老行为。
            "start_on_demand": True,
            # 空闲自动关闭的分钟数（0 = 不自动关闭）。计时口径：该账号没有可领任务、
            # Worker 报 idle 之后开始算。
            "idle_shutdown_minutes": 10,
        },
        "nas": {
            "asset_root": str(settings.NAS_ASSET_ROOT),
            "last_tested_at": "",
            "scan_mode": "auto",
            "max_files": 3000,
        },
        "store_collection": {
            "auto_focus_after_import": True,
            "last_collected_at": {},
            "enabled": True,
            "interval_minutes": 360,
            "max_attempts": 3,
            "last_error": "",
        },
        "storage_cleanup": {
            "enabled": True,
            "temp_retention_days": 7,
            "screenshot_retention_days": 30,
            "export_retention_days": 90,
            "log_retention_days": 30,
            # 商品素材：上架完成满 3 天压缩（PNG→同名 JPEG，长边 1000、质量 88），
            # 满 30 天可在设置页/每日自动清理里删除。
            "assets_retention_days": 30,
            "compact_enabled": True,
            "compact_delay_days": 3,
            "compact_max_edge": 1000,
            "compact_jpeg_quality": 88,
        },
        "cnsc": {
            # Site order for headerless price-matrix paste; empty = page default order.
            "price_site_order": [],
        },
        "display": {
            # 跨境模式（CNSC 全球商品）界面开关：默认开＝本土/跨境都显示；
            # 关闭后前端只显示本土版本并把跨境界面隐藏，后端功能与数据不受影响。
            "cross_border_enabled": True,
        },
        "network": {
            # 允许局域网设备访问：监听 0.0.0.0 而不是 127.0.0.1。
            # 该开关保存后立即重绑监听，不需要重启 API；重启后也会按它恢复。
            "lan_enabled": _env_lan_default(),
        },
        "updates": {
            # GitHub 版本发布 / 在线更新。repo 为空 = 未配置，设置页会提示填写。
            "repo": "",
            "api_url": "https://api.github.com",
            "include_prerelease": False,
            "auto_check": True,
            # Token 落库前 Fernet 加密；公开配置里会被剔除，只回显 token_configured。
            "token": "",
            # 最近一次检查的缓存（真正的最新 Release 存在 storage/updates/check.json）。
            "last_checked_at": "",
            "last_check_error": "",
            "latest_version": "",
        },
        "safety": {"save_draft_only": True, "publish_allowed": False, "editable": False},
    }


def merge_configuration(value: Any) -> dict[str, dict[str, Any]]:
    merged = default_configuration()
    if not isinstance(value, dict):
        return merged
    for group, fields in GROUP_FIELDS.items():
        incoming = value.get(group)
        if isinstance(incoming, dict):
            merged[group].update({key: deepcopy(incoming[key]) for key in fields if key in incoming})
    merged["worker"]["auto_start"] = True
    merged["safety"] = default_configuration()["safety"]
    return merged


def public_runtime_configuration(value: Any) -> dict[str, dict[str, Any]]:
    public = merge_configuration(value)
    for group in public.values():
        for key in list(group):
            if any(fragment in key.lower() for fragment in ("api_key", "password", "secret", "token")):
                group.pop(key, None)
    return public


def worker_slots_per_account(config: Any) -> int:
    """一个马帮账号要开几个并行槽位（读不出或是脏值时按 1 处理）。

    1 就是老行为（一个账号一只浏览器、一次一单）；≥2 时调用方要为每个槽位准备
    独立的 CDP 端口与 Profile 目录（见 storage.resolve_mabang_account_runtime）。
    """

    worker = (config or {}).get("worker") if isinstance(config, dict) else None
    raw = (worker or {}).get("slots_per_account") if isinstance(worker, dict) else None
    try:
        slots = int(raw)
    except (TypeError, ValueError):
        return 1
    return max(1, min(MAX_WORKER_SLOTS_PER_ACCOUNT, slots))


def worker_start_on_demand(config: Any) -> bool:
    """Worker 是否按需启动（默认开：平时不跑，有任务才唤醒）。

    旧库没有这个字段时按「开」处理——现场要的就是别让 Worker 一直挂着；
    要回到老行为就在设置里关掉。
    """

    worker = (config or {}).get("worker") if isinstance(config, dict) else None
    if not isinstance(worker, dict):
        return True
    return worker.get("start_on_demand", True) is not False


def worker_idle_shutdown_seconds(config: Any) -> float:
    """空闲自动关闭的秒数（0 = 不自动关闭，脏值按默认 10 分钟）。"""

    worker = (config or {}).get("worker") if isinstance(config, dict) else None
    raw = (worker or {}).get("idle_shutdown_minutes") if isinstance(worker, dict) else None
    default_minutes = float(default_configuration()["worker"]["idle_shutdown_minutes"])
    if raw is None or str(raw).strip() == "":
        minutes = default_minutes
    else:
        try:
            minutes = float(raw)
        except (TypeError, ValueError):
            minutes = default_minutes
    if minutes <= 0:
        return 0.0
    # 上限一天：填错一个巨大的数等于永不关闭（还看不出原因），不如夹住。
    return min(minutes, 24 * 60) * 60


def update_runtime_configuration(current: Any, patch: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(patch, dict):
        raise ValueError("设置内容必须是对象")
    unknown_groups = set(patch) - set(GROUP_FIELDS)
    if unknown_groups:
        raise ValueError(f"未知设置分组：{sorted(unknown_groups)[0]}")
    if "safety" in patch:
        wanted = patch.get("safety")
        if not isinstance(wanted, dict) or any(
            wanted.get(key, expected) != expected
            for key, expected in default_configuration()["safety"].items()
        ):
            raise ValueError("禁止修改发布策略")

    updated = merge_configuration(current)
    old_worker = deepcopy(updated["worker"])
    for group, raw_fields in patch.items():
        if not isinstance(raw_fields, dict):
            raise ValueError(f"设置分组 {group} 必须是对象")
        unknown_fields = set(raw_fields) - GROUP_FIELDS[group]
        if unknown_fields:
            raise ValueError(f"未知设置字段：{group}.{sorted(unknown_fields)[0]}")
        updated[group].update(deepcopy(raw_fields))

    updated["worker"]["auto_start"] = True
    _validate_configuration(updated)
    worker_patch = patch.get("worker") if isinstance(patch.get("worker"), dict) else {}
    if any(key in worker_patch for key in ("cdp_url", "poll_seconds", "lease_seconds", "max_attempts")) or any(
        updated["worker"].get(key) != old_worker.get(key)
        for key in ("cdp_url", "poll_seconds", "lease_seconds", "max_attempts")
    ):
        updated["worker"]["restart_required"] = True
    updated["safety"] = default_configuration()["safety"]
    return updated


def _validate_configuration(config: dict[str, dict[str, Any]]) -> None:
    if not str(config["worker"].get("cdp_url") or "").startswith(("http://", "https://")):
        raise ValueError("Worker CDP 地址必须是 http 或 https 地址")
    config["mabang"]["headless"] = bool(config["mabang"].get("headless"))
    for key in ("poll_seconds", "lease_seconds", "max_attempts"):
        if float(config["worker"].get(key) or 0) <= 0:
            raise ValueError(f"Worker {key} 必须大于 0")
    raw_slots = config["worker"].get("slots_per_account")
    try:
        # 注意不能用 `or 1`：手填 0 会被悄悄当成 1，操作员以为关掉了并行、其实没关。
        slots = 1 if raw_slots is None or str(raw_slots).strip() == "" else int(raw_slots)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Worker 每账号并行槽位数必须是 1 到 {MAX_WORKER_SLOTS_PER_ACCOUNT} 的整数"
        ) from exc
    if not 1 <= slots <= MAX_WORKER_SLOTS_PER_ACCOUNT:
        raise ValueError(
            f"Worker 每账号并行槽位数必须是 1 到 {MAX_WORKER_SLOTS_PER_ACCOUNT} 的整数"
        )
    config["worker"]["slots_per_account"] = slots
    raw_idle = config["worker"].get("idle_shutdown_minutes")
    if raw_idle is None or str(raw_idle).strip() == "":
        config["worker"]["idle_shutdown_minutes"] = default_configuration()["worker"]["idle_shutdown_minutes"]
    else:
        try:
            idle_minutes = float(raw_idle)
        except (TypeError, ValueError) as exc:
            raise ValueError("Worker 空闲自动关闭分钟数必须是 0（不自动关闭）或正数") from exc
        if idle_minutes < 0:
            raise ValueError("Worker 空闲自动关闭分钟数必须是 0（不自动关闭）或正数")
        config["worker"]["idle_shutdown_minutes"] = (
            int(idle_minutes) if float(idle_minutes).is_integer() else idle_minutes
        )
    if config["nas"].get("scan_mode") not in {"auto", "groups", "recursive"}:
        raise ValueError("NAS 扫描模式无效")
    if int(config["nas"].get("max_files") or 0) <= 0:
        raise ValueError("NAS 最大文件数必须大于 0")
    if int(config["store_collection"].get("interval_minutes") or 0) <= 0:
        raise ValueError("店铺采集周期必须大于 0")
    if int(config["store_collection"].get("max_attempts") or 0) <= 0:
        raise ValueError("店铺采集最大重试次数必须大于 0")
    if int(config["ai"].get("request_timeout_seconds") or 0) <= 0:
        raise ValueError("AI 请求超时必须大于 0")
    for key in (
        "temp_retention_days", "screenshot_retention_days",
        "export_retention_days", "log_retention_days",
        # 素材保留期与压缩延后期同口径（1~3650 天）。
        "assets_retention_days", "compact_delay_days",
    ):
        try:
            value = int(config["storage_cleanup"].get(key))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"存储清理 {key} 必须是 1 到 3650 天") from exc
        if not 1 <= value <= 3650:
            raise ValueError(f"存储清理 {key} 必须是 1 到 3650 天")
        config["storage_cleanup"][key] = value
    try:
        edge = int(config["storage_cleanup"].get("compact_max_edge") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("素材压缩长边必须是 200 到 4000 的整数") from exc
    if not 200 <= edge <= 4000:
        raise ValueError("素材压缩长边必须是 200 到 4000 的整数")
    config["storage_cleanup"]["compact_max_edge"] = edge
    try:
        quality = int(config["storage_cleanup"].get("compact_jpeg_quality") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("素材压缩质量必须是 50 到 95 的整数") from exc
    if not 50 <= quality <= 95:
        raise ValueError("素材压缩质量必须是 50 到 95 的整数")
    config["storage_cleanup"]["compact_jpeg_quality"] = quality
    config["storage_cleanup"]["compact_enabled"] = (
        config["storage_cleanup"].get("compact_enabled") is not False
    )
    # 跨境模式开关：任意非空值按 bool 归一化（"false" 字符串等脏值一律算开），
    # 只做前端显示控制，不参与任何后端业务判断。
    config["display"]["cross_border_enabled"] = bool(config["display"].get("cross_border_enabled"))
    if not str(config["nas"].get("asset_root") or "").strip():
        raise ValueError("NAS 素材目录不能为空")
    sku_cfg = config["product_sku"]
    prefix = str(sku_cfg.get("prefix") or "").strip().upper()
    sku_cfg["prefix"] = re.sub(r"[^A-Z0-9]", "", prefix)
    try:
        width = int(sku_cfg.get("number_width") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("主 SKU 编号位数必须是 1 到 8 的整数") from exc
    if not 1 <= width <= 8:
        raise ValueError("主 SKU 编号位数必须是 1 到 8 的整数")
    sku_cfg["number_width"] = width
    try:
        next_number = int(sku_cfg.get("next_number") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("主 SKU 起始编号必须是大于 0 的整数") from exc
    if next_number <= 0:
        raise ValueError("主 SKU 起始编号必须是大于 0 的整数")
    sku_cfg["next_number"] = next_number
    if sku_cfg.get("auto_assign_on_import") and not sku_cfg["prefix"]:
        raise ValueError("启用主 SKU 自动分配时必须填写前缀")
    network_cfg = config["network"]
    raw_lan = network_cfg.get("lan_enabled")
    if isinstance(raw_lan, str):
        normalized = raw_lan.strip().lower()
        if normalized in {"1", "true", "yes", "on", "y"}:
            network_cfg["lan_enabled"] = True
        elif normalized in {"0", "false", "no", "off", "n"}:
            network_cfg["lan_enabled"] = False
        else:
            raise ValueError("局域网访问开关必须是布尔值")
    else:
        network_cfg["lan_enabled"] = bool(raw_lan)
    updates_cfg = config["updates"]
    updates_cfg["repo"] = normalize_github_repo(updates_cfg.get("repo"))
    api_url = str(updates_cfg.get("api_url") or "").strip().rstrip("/") or "https://api.github.com"
    if not api_url.startswith(("http://", "https://")):
        raise ValueError("GitHub API 地址必须是 http 或 https 地址")
    updates_cfg["api_url"] = api_url
    updates_cfg["include_prerelease"] = bool(updates_cfg.get("include_prerelease"))
    updates_cfg["auto_check"] = updates_cfg.get("auto_check") is not False
    for key in ("last_checked_at", "last_check_error", "latest_version"):
        updates_cfg[key] = str(updates_cfg.get(key) or "")
    token = updates_cfg.get("token")
    updates_cfg["token"] = "" if token is None else str(token)
    cnsc_cfg = config["cnsc"]
    sites = ["TW", "PH", "SG", "TH", "MY", "MX", "VN", "BR"]
    order = cnsc_cfg.get("price_site_order")
    if not isinstance(order, list):
        raise ValueError("CNSC 价格表识别顺序必须是站点列表")
    cleaned: list[str] = []
    for code in order:
        code = str(code or "").strip().upper()
        if code not in sites:
            raise ValueError(f"CNSC 价格表识别顺序包含未知站点：{code}")
        if code not in cleaned:
            cleaned.append(code)
    cnsc_cfg["price_site_order"] = cleaned
