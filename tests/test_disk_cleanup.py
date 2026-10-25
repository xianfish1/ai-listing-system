from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from services.api import disk_cleanup, settings
from services.api.runtime_configuration import default_configuration, update_runtime_configuration


@pytest.fixture
def cleanup_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    project_root = tmp_path / "project"
    storage_dir = project_root / "storage"
    data_dir = project_root / "data"
    monkeypatch.setattr(settings, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(settings, "STORAGE_DIR", storage_dir)
    monkeypatch.setattr(settings, "DATA_DIR", data_dir)
    monkeypatch.setattr(settings, "EXPORT_DIR", storage_dir / "exports")
    monkeypatch.setattr(settings, "SCREENSHOT_DIR", storage_dir / "screenshots")
    monkeypatch.setattr(settings, "LOG_DIR", storage_dir / "logs")
    return {"project": project_root, "storage": storage_dir, "data": data_dir}


def _write(path: Path, size: int = 8, *, age_days: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


def test_cleanup_settings_have_safe_defaults_and_validate_retention() -> None:
    config = default_configuration()["storage_cleanup"]
    assert config == {
        "enabled": True,
        "temp_retention_days": 7,
        "screenshot_retention_days": 30,
        "export_retention_days": 90,
        "log_retention_days": 30,
        # 商品素材：保留 30 天；上架完成满 3 天后压成长边 1000 / 质量 88 的 JPEG。
        "assets_retention_days": 30,
        "compact_enabled": True,
        "compact_delay_days": 3,
        "compact_max_edge": 1000,
        "compact_jpeg_quality": 88,
    }
    with pytest.raises(ValueError, match="1 到 3650"):
        update_runtime_configuration({}, {"storage_cleanup": {"temp_retention_days": 0}})
    with pytest.raises(ValueError, match="1 到 3650"):
        update_runtime_configuration({}, {"storage_cleanup": {"assets_retention_days": 0}})
    with pytest.raises(ValueError, match="素材压缩长边"):
        update_runtime_configuration({}, {"storage_cleanup": {"compact_max_edge": 100}})
    with pytest.raises(ValueError, match="素材压缩质量"):
        update_runtime_configuration({}, {"storage_cleanup": {"compact_jpeg_quality": 20}})


def test_browser_cache_cleanup_preserves_login_data(cleanup_roots: dict[str, Path]) -> None:
    storage_dir = cleanup_roots["storage"]
    cache = _write(storage_dir / "browser-use-profile" / "Default" / "Cache" / "cache.bin", 10)
    nested_cache = _write(storage_dir / "mabang-cdp-profile" / "OptGuideOnDeviceModel" / "Cache" / "nested.bin", 5)
    model = _write(storage_dir / "mabang-cdp-profile" / "OptGuideOnDeviceModel" / "weights.bin", 20)
    cookie = _write(storage_dir / "browser-use-profile" / "Default" / "Network" / "Cookies", 30)
    local_storage = _write(storage_dir / "mabang-cdp-profile" / "Default" / "Local Storage" / "leveldb", 40)
    extension_cache = _write(storage_dir / "browser-use-profile" / "Default" / "Extensions" / "example" / "Cache" / "data", 7)

    usage = disk_cleanup.scan_usage(default_configuration()["storage_cleanup"], active_job_ids=set())
    browser = usage["categories"]["browser_cache"]
    assert browser["total_bytes"] == 112
    assert browser["reclaimable_bytes"] == 35

    result = disk_cleanup.cleanup_categories(
        ["browser_cache"],
        default_configuration()["storage_cleanup"],
        active_job_ids=set(),
        cdp_available=lambda: False,
    )
    assert result["freed_bytes"] == 35
    assert not cache.exists() and not nested_cache.exists() and not model.exists()
    assert cookie.exists() and local_storage.exists() and extension_cache.exists()


def test_manual_retry_statuses_keep_automation_uploads(cleanup_roots: dict[str, Path]) -> None:
    data = {"automation_jobs": [
        {"id": "manual", "status": "needs_manual"},
        {"id": "regenerate", "status": "needs_regeneration"},
        {"id": "done", "status": "saved"},
    ]}
    assert disk_cleanup.active_automation_job_ids(data) == {"manual", "regenerate"}


def test_locked_files_are_reported_as_skipped(
    cleanup_roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = _write(cleanup_roots["storage"] / "tmp" / "locked.bin", age_days=20)
    original_unlink = Path.unlink

    def locked_unlink(path: Path, *args, **kwargs):
        if path == stale:
            raise PermissionError("locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    result = disk_cleanup.cleanup_categories(
        ["temporary_files"], default_configuration()["storage_cleanup"],
        active_job_ids=set(), cdp_available=lambda: False,
    )
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["skipped"] == [str(stale)]


def test_retention_cleanup_skips_current_files_and_active_jobs(cleanup_roots: dict[str, Path]) -> None:
    project = cleanup_roots["project"]
    storage_dir = cleanup_roots["storage"]
    stale_tmp = _write(storage_dir / "tmp" / "stale.bin", age_days=8)
    current_tmp = _write(storage_dir / "tmp" / "current.bin", age_days=6)
    active_job = _write(project / "tmp_upload" / "automation-jobs" / "job-active" / "thumb.jpg", age_days=20)
    stale_job = _write(project / "tmp_upload" / "automation-jobs" / "job-done" / "thumb.jpg", age_days=20)
    completed_upload = _write(storage_dir / "uploads" / "base-products" / "completed" / "product.jpg", age_days=20)
    stale_session = _write(storage_dir / "uploads" / "base-products" / "stale-session" / "session.json", age_days=20)
    _write(stale_session.parent / "partial.jpg", age_days=20)

    result = disk_cleanup.cleanup_categories(
        ["temporary_files"],
        default_configuration()["storage_cleanup"],
        active_job_ids={"job-active"},
        cdp_available=lambda: False,
    )

    assert not stale_tmp.exists() and not stale_job.exists() and not stale_session.parent.exists()
    assert current_tmp.exists() and active_job.exists() and completed_upload.exists()
    assert result["deleted_files"] >= 3


def test_cleanup_rejects_unknown_categories_and_running_browser(cleanup_roots: dict[str, Path]) -> None:
    _write(cleanup_roots["storage"] / "browser-use-profile" / "Default" / "GPUCache" / "data", 10)
    config = default_configuration()["storage_cleanup"]
    with pytest.raises(ValueError, match="未知清理类别"):
        disk_cleanup.cleanup_categories(["data"], config, active_job_ids=set(), cdp_available=lambda: False)
    with pytest.raises(disk_cleanup.BrowserInUseError):
        disk_cleanup.cleanup_categories(["browser_cache"], config, active_job_ids=set(), cdp_available=lambda: True)


def test_cleanup_is_idempotent_and_does_not_follow_directory_links(cleanup_roots: dict[str, Path]) -> None:
    storage_dir = cleanup_roots["storage"]
    outside = _write(cleanup_roots["project"].parent / "protected" / "outside.bin", age_days=20)
    link = storage_dir / "tmp" / "linked-outside"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(outside.parent, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 环境不允许创建测试符号链接")
    stale = _write(storage_dir / "tmp" / "stale.bin", age_days=20)
    config = default_configuration()["storage_cleanup"]

    first = disk_cleanup.cleanup_categories(
        ["temporary_files"], config, active_job_ids=set(), cdp_available=lambda: False,
    )
    second = disk_cleanup.cleanup_categories(
        ["temporary_files"], config, active_job_ids=set(), cdp_available=lambda: False,
    )

    assert not stale.exists() and outside.exists() and link.exists()
    assert first["freed_bytes"] > 0
    assert second["freed_bytes"] == 0 and second["deleted_files"] == 0


def test_automatic_cleanup_runs_daily_and_never_includes_browser_cache(
    cleanup_roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        disk_cleanup,
        "cleanup_categories",
        lambda categories, *_args, **_kwargs: calls.append(list(categories)) or {
            "ok": True, "freed_bytes": 0, "deleted_files": 0, "errors": [],
        },
    )
    config = default_configuration()["storage_cleanup"]
    assert disk_cleanup.run_automatic_cleanup_if_due(config, active_job_ids=set(), now=1_700_000_000) is not None
    # 商品素材进自动集合；「已删商品遗留素材」只能手动（不可逆 + 涉及已删商品）。
    assert calls == [["temporary_files", "screenshots", "exports", "logs", "product_assets"]]
    assert disk_cleanup.run_automatic_cleanup_if_due(config, active_job_ids=set(), now=1_700_000_100) is None
    assert disk_cleanup.run_automatic_cleanup_if_due(config, active_job_ids=set(), now=1_700_086_401) is not None

    state = json.loads((cleanup_roots["data"] / "storage_cleanup_state.json").read_text(encoding="utf-8"))
    assert state["last_auto_cleanup_at"] == 1_700_086_401
