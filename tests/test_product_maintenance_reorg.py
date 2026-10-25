from __future__ import annotations

import json
from pathlib import Path
from urllib import request

import pytest

from services.api import settings, storage
from tests.test_api_boundaries import _start_api


def test_folder_picker_foregrounds_native_dialog_before_opening() -> None:
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "services" / "api" / "main.py"
    ).read_text(encoding="utf-8")
    assert 'root.attributes("-topmost", True)' in source
    assert "root.lift()" in source
    assert "root.focus_force()" in source
    assert "root.grab_set()" in source
    assert "parent=root" in source


def test_folder_picker_route_can_return_unavailable_without_breaking_api() -> None:
    server, base_url = _start_api()
    try:
        req = request.Request(
            f"{base_url}/api/system/folder-picker",
            method="POST",
            data=json.dumps({"dry_run": True}).encode("utf-8"),
        )
        req.add_header("Content-Type", "application/json")
        with request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert "ok" in payload
    assert "path" in payload


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_nas_browser_ui_leaf_rows_select_on_click() -> None:
    """NAS 选择器里没有子目录的文件夹（商品款式）直接点行加入已选，
    有子目录的保持进入下一层。"""
    script = (_repo_root() / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    html = (_repo_root() / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    styles = (_repo_root() / "apps" / "web" / "styles.css").read_text(encoding="utf-8")

    # 后端返回每个条目是否有子目录，前端据此把叶子渲染成点选行
    assert "has_subdirs" in script
    assert "nas-browser-item--leaf" in script
    assert "data-nas-select" in script
    assert "nasBrowserToggleLeaf" in script
    # 叶子行在已选时给高亮
    assert "is-selected" in styles
    # 选择列表面板与确认按钮仍存在
    assert 'id="nas-browser-selection"' in html
    assert 'id="nas-browser-confirm-btn"' in html
    # 「刷新本层」按钮强制 refresh=1 绕过缓存（NAS 新增/删除目录后用）
    assert 'id="nas-browser-refresh-btn"' in html
    assert 'params.set("refresh", "1")' in script
    assert "nasBrowserNavigate(nasBrowserState.currentPath, { refresh: true" in script
    # 打开目录默认浅加载秒回(不等逐子目录探测),叶子标记由后台补全
    assert 'params.set("shallow", "1")' in script
    assert "scheduleNasLeafProbe" in script
    # 点「确认创建」立即关闭选择器弹窗，导入转到后台（runNasBatchImport）
    # 并回到商品维护视图显示进度，不用一直等在弹窗里。
    assert 'nas-browser-dialog")?.close();' in script
    assert "runNasBatchImport" in script
    assert "dialog.showModal();" in script
    assert "/api/base-products/from-folders" in script
    assert "/api/base-products/import-tasks/" in script
    assert "requestController.abort()" in script


def test_nas_browse_reports_has_subdirs_for_leaf_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/api/nas/browse 的 entries 带 has_subdirs：产品款式的无子目录叶子为 False，
    系列目录为 True，前端据此实现「叶子直接点选」。"""
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")
    monkeypatch.setattr(settings, "NAS_ASSET_ROOT", tmp_path / "nas")

    nas_root = tmp_path / "nas"
    series = nas_root / "AL 21#童装纯棉"
    series.mkdir(parents=True)
    (series / "AL0001企鹅").mkdir()          # leaf product folder
    (series / "AL0002 我的世界").mkdir()     # leaf product folder
    (nas_root / "做图-PP").mkdir()           # leaf at root (no children)
    (nas_root / "BL 水洗童装" / "BL0001").mkdir(parents=True)  # series with child

    server, base_url = _start_api()
    try:
        def browse(path: str) -> dict:
            req = request.Request(f"{base_url}/api/nas/browse?path={request.quote(path)}")
            with request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))

        payload = browse("")
        by_name = {entry["name"]: entry for entry in payload["entries"]}
        assert by_name["AL 21#童装纯棉"]["has_subdirs"] is True
        assert by_name["BL 水洗童装"]["has_subdirs"] is True
        assert by_name["做图-PP"]["has_subdirs"] is False

        inside = browse(str(series))
        leaves = {entry["name"]: entry for entry in inside["entries"]}
        assert leaves["AL0001企鹅"]["has_subdirs"] is False
        assert leaves["AL0002 我的世界"]["has_subdirs"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_nas_browse_serves_second_visit_from_cache_and_refresh_bypasses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一目录第二次浏览直接命中进程内缓存（NAS 网络盘慢），
    ?refresh=1 强制重新扫盘。"""
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")
    monkeypatch.setattr(settings, "NAS_ASSET_ROOT", tmp_path / "nas")

    nas_root = tmp_path / "nas"
    series = nas_root / "系列A"
    series.mkdir(parents=True)
    (series / "款1").mkdir()
    (series / "款2").mkdir()

    server, base_url = _start_api()
    try:
        def browse(path: str, refresh: bool = False) -> dict:
            url = f"{base_url}/api/nas/browse?path={request.quote(path)}"
            if refresh:
                url += "&refresh=1"
            with request.urlopen(url, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))

        first = browse(str(series))
        assert first["from_cache"] is False  # cold: just scanned

        second = browse(str(series))
        assert second["from_cache"] is True  # warm: served from cache
        assert second["entries"] == first["entries"]

        # 新建一个目录后强制刷新应能看到新条目
        (series / "款3").mkdir()
        third = browse(str(series), refresh=True)
        assert third["from_cache"] is False
        assert {entry["name"] for entry in third["entries"]} == {"款1", "款2", "款3"}
    finally:
        server.shutdown()
        server.server_close()


def test_nas_browse_cache_survives_restart_via_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """缓存写到本地 data/nas_browse_cache.json，API 重启后再次浏览同一目录
    直接命中磁盘缓存，不用重新扫 NAS。"""
    import services.api.main as api_main

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")
    monkeypatch.setattr(settings, "NAS_ASSET_ROOT", tmp_path / "nas")

    nas_root = tmp_path / "nas"
    series = nas_root / "系列B"
    series.mkdir(parents=True)
    (series / "款1").mkdir()
    (series / "款2").mkdir()

    cache_file = tmp_path / "nas_browse_cache.json"

    # --- 第一次“进程”：冷扫并把结果写盘 ---
    server, base_url = _start_api()
    try:
        url = f"{base_url}/api/nas/browse?path={request.quote(str(series))}"
        with request.urlopen(url, timeout=15) as resp:
            first = json.loads(resp.read().decode("utf-8"))
        assert first["from_cache"] is False
    finally:
        server.shutdown()
        server.server_close()
    assert cache_file.exists()

    # --- 模拟重启：清空进程内缓存与 warm 标志 ---
    with api_main._NAS_BROWSE_CACHE_LOCK:
        api_main._NAS_BROWSE_CACHE.clear()
    api_main._NAS_BROWSE_CACHE_DISK_WARMED = False

    # --- 第二次“进程”：应从磁盘 warm 后直接命中 ---
    server2, base_url2 = _start_api()
    try:
        url2 = f"{base_url2}/api/nas/browse?path={request.quote(str(series))}"
        with request.urlopen(url2, timeout=15) as resp:
            second = json.loads(resp.read().decode("utf-8"))
        assert second["from_cache"] is True
        assert second["entries"] == first["entries"]
    finally:
        server2.shutdown()
        server2.server_close()


def test_nas_browse_shallow_returns_null_leaf_markers_and_probe_fills_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """shallow=1 只列一次目录、has_subdirs 为 null(未知),立即返回不探测;
    后台线程把完整(带叶子标记)结果写进缓存,随后 full 浏览秒回已补全标记。
    这保证「点第一层」不再为几百个款式文件夹做逐目录扫描而卡住。"""
    import time

    import services.api.main as api_main

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")
    monkeypatch.setattr(settings, "NAS_ASSET_ROOT", tmp_path / "nas")

    nas_root = tmp_path / "nas"
    series = nas_root / "系列X"
    series.mkdir(parents=True)
    (series / "款1").mkdir()   # leaf
    (series / "款2").mkdir()   # leaf
    (series / "子系列" / "深款").mkdir(parents=True)  # has subdir

    server, base_url = _start_api()
    try:
        def browse(path: str, **extra: str) -> dict:
            url = f"{base_url}/api/nas/browse?path={request.quote(path)}"
            for key, value in extra.items():
                url += f"&{key}={value}"
            with request.urlopen(url, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))

        # 后台探测进行中时,full 请求应快速返回 probe_running=True 的浅 payload,
        # 绝不长时间阻塞。先在 inflight 里占位:这会让随后的 shallow 打开不再
        # 另起探测线程,保证下面 full 请求命中拦截分支而非已完成缓存。
        with api_main._NAS_BROWSE_PROBE_LOCK:
            api_main._NAS_BROWSE_PROBE_INFLIGHT.add(str(series))
        try:
            shallow = browse(str(series), shallow="1")
            assert shallow["shallow"] is True
            by_name = {entry["name"]: entry for entry in shallow["entries"]}
            assert {entry["has_subdirs"] for entry in shallow["entries"]} == {None}

            started = time.time()
            mid = browse(str(series))
            assert time.time() - started < 3
            assert mid.get("probe_running") is True
            assert {entry["name"] for entry in mid["entries"]} == {"款1", "款2", "子系列"}
        finally:
            with api_main._NAS_BROWSE_PROBE_LOCK:
                api_main._NAS_BROWSE_PROBE_INFLIGHT.discard(str(series))

        # 占位清除后,一次 full 浏览直接完成探测并缓存带叶子标记的结果,
        # 再一次 full 浏览应命中该缓存(不用再扫盘)。
        with api_main._NAS_BROWSE_PROBE_LOCK:
            api_main._NAS_BROWSE_PROBE_INFLIGHT.discard(str(series))
        full = browse(str(series))
        assert {entry["name"] for entry in full["entries"]} == {"款1", "款2", "子系列"}
        by_name_full = {entry["name"]: entry for entry in full["entries"]}
        assert by_name_full["款1"]["has_subdirs"] is False
        assert by_name_full["款2"]["has_subdirs"] is False
        assert by_name_full["子系列"]["has_subdirs"] is True

        second_full = browse(str(series))
        assert second_full["from_cache"] is True
        assert second_full["entries"] == full["entries"]
    finally:
        server.shutdown()
        server.server_close()


def test_nas_batch_import_task_returns_immediately_and_isolates_item_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """批量导入只创建任务，不同步等待 NAS/AI；单项失败不影响其他项。"""
    import time

    import services.api.main as api_main

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")

    def fake_create(payload: dict) -> dict:
        if payload["root_path"].endswith("bad"):
            raise ValueError("模拟扫描失败")
        time.sleep(0.05)
        return {"id": "product-good", "asset_root": payload["root_path"]}

    monkeypatch.setattr(api_main.storage, "create_base_product_from_folder", fake_create)
    monkeypatch.setattr(api_main, "_enrich_with_vision_listing", lambda product, payload: product)

    server, base_url = _start_api()
    try:
        req = request.Request(
            f"{base_url}/api/base-products/from-folders",
            method="POST",
            data=json.dumps({"paths": [r"C:\\good", r"C:\\bad"]}).encode("utf-8"),
        )
        req.add_header("Content-Type", "application/json")
        with request.urlopen(req, timeout=5) as resp:
            assert resp.status == 202
            task = json.loads(resp.read().decode("utf-8"))
        assert task["status"] in {"queued", "running"}
        assert task["total"] == 2

        deadline = time.time() + 5
        while time.time() < deadline:
            with request.urlopen(f"{base_url}/api/base-products/import-tasks/{task['id']}", timeout=5) as resp:
                snapshot = json.loads(resp.read().decode("utf-8"))
            if snapshot["status"] == "completed":
                break
            time.sleep(0.05)
        assert snapshot["status"] == "completed"
        assert snapshot["success"] == 1
        assert snapshot["failed"] == 1
        assert {item["status"] for item in snapshot["items"]} == {"succeeded", "failed"}
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def _wait_nas_browse_background_workers() -> None:
    """每个测试后等待 NAS 浏览的后台线程(probe/重扫/预取)收尾。

    这些是守护线程,server 关闭后仍可能短暂运行并把缓存写到 settings.DATA_DIR;
    下一个测试会 monkeypatch 新的 DATA_DIR,若不等待,旧线程会把上一个测试的
    内容写进新目录(或在新测试断言缓存文件前删掉它),造成跨测试串扰。"""
    import time

    import services.api.main as api_main

    yield
    deadline = time.time() + 5
    while time.time() < deadline:
        with api_main._NAS_BROWSE_PROBE_LOCK:
            idle = not api_main._NAS_BROWSE_PROBE_INFLIGHT and not api_main._NAS_BROWSE_PREFETCH_INFLIGHT
        if idle:
            return
        time.sleep(0.05)


def _wait_until(predicate, timeout: float = 8.0, interval: float = 0.1) -> bool:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_nas_browse_stale_entry_served_instantly_and_rescanned_in_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """过期(超过 fresh 阈值)缓存条目仍立即返回 from_cache=True(绝不阻塞等 NAS),
    同时后台重新扫描刷新缓存;refresh=1 仍强制同步读实盘。"""
    import datetime as _dt
    import time

    import services.api.main as api_main

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")
    monkeypatch.setattr(settings, "NAS_ASSET_ROOT", tmp_path / "nas")

    nas_root = tmp_path / "nas"
    series = nas_root / "系列S"
    series.mkdir(parents=True)
    (series / "款1").mkdir()
    (series / "款2").mkdir()

    server, base_url = _start_api()
    try:
        def browse(path: str, refresh: bool = False, shallow: bool = False) -> dict:
            url = f"{base_url}/api/nas/browse?path={request.quote(path)}"
            if refresh:
                url += "&refresh=1"
            if shallow:
                url += "&shallow=1"
            with request.urlopen(url, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))

        # 冷扫一次并缓存
        first = browse(str(series))
        assert first["from_cache"] is False

        # 人为把 full 缓存条目改到超过 fresh 阈值(但未超过磁盘 TTL)
        old = _dt.datetime.now(_dt.timezone.utc).timestamp() - (api_main._NAS_BROWSE_CACHE_FRESH_SECONDS + 60)
        stale_key = None
        with api_main._NAS_BROWSE_CACHE_LOCK:
            for key in api_main._NAS_BROWSE_CACHE:
                if key.endswith(f"|f|{series}"):
                    stale_key = key
                    break
        assert stale_key is not None
        with api_main._NAS_BROWSE_CACHE_LOCK:
            api_main._NAS_BROWSE_CACHE[stale_key] = (old, api_main._NAS_BROWSE_CACHE[stale_key][1])

        # 过期后第二次访问:应立即命中缓存(不阻塞),带 stale 标记
        started = time.time()
        second = browse(str(series))
        assert time.time() - started < 3
        assert second["from_cache"] is True
        assert second["stale"] is True
        assert {entry["name"] for entry in second["entries"]} == {"款1", "款2"}

        # 后台重扫完成:缓存时间戳应被刷新到 fresh 窗口内
        def cache_refreshed() -> bool:
            with api_main._NAS_BROWSE_CACHE_LOCK:
                cached = api_main._NAS_BROWSE_CACHE.get(stale_key)
            if cached is None:
                return False
            return (_dt.datetime.now(_dt.timezone.utc).timestamp() - cached[0]) < api_main._NAS_BROWSE_CACHE_FRESH_SECONDS

        assert _wait_until(cache_refreshed), "后台重扫应刷新过期缓存"

        # refresh=1 永远同步走实盘
        (series / "款3").mkdir()
        third = browse(str(series), refresh=True)
        assert third["from_cache"] is False
        assert {entry["name"] for entry in third["entries"]} == {"款1", "款2", "款3"}
    finally:
        server.shutdown()
        server.server_close()


def test_nas_browse_shallow_open_prefetches_children_into_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """浅打开父目录后,其子目录的浅列表在后台预取完成;随后直接进入子目录
    命中 from_cache=True,不用再等 NAS。"""
    import time

    import services.api.main as api_main

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")
    monkeypatch.setattr(settings, "NAS_ASSET_ROOT", tmp_path / "nas")

    nas_root = tmp_path / "nas"
    series = nas_root / "系列P"
    child1 = series / "子系列1"
    child2 = series / "子系列2"
    child1.mkdir(parents=True)
    (child1 / "款A").mkdir()
    child2.mkdir(parents=True)
    (child2 / "款B").mkdir()

    server, base_url = _start_api()
    try:
        def browse(path: str, **extra: str) -> dict:
            url = f"{base_url}/api/nas/browse?path={request.quote(path)}"
            for key, value in extra.items():
                url += f"&{key}={value}"
            with request.urlopen(url, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))

        # 浅打开系列层
        parent_payload = browse(str(series), shallow="1")
        assert parent_payload["shallow"] is True

        # 后台应把两个子目录的浅列表写进缓存(轮询等待)
        def children_cached() -> bool:
            with api_main._NAS_BROWSE_CACHE_LOCK:
                keys = set(api_main._NAS_BROWSE_CACHE)
            return all(
                any(key.endswith(f"|s|{child}") for key in keys)
                for child in (child1, child2)
            )

        assert _wait_until(children_cached), "子目录预取应写入缓存"

        # 直接进入子目录:应命中缓存(后台预取结果),秒回
        for child in (child1, child2):
            payload = browse(str(child), shallow="1")
            assert payload["from_cache"] is True, f"{child} 应由预取缓存秒回"
            assert payload["entries"], f"{child} 应包含预取到的子项"
    finally:
        server.shutdown()
        server.server_close()


def test_nas_browse_warm_up_thread_registered_in_main() -> None:
    """main() 启动路径注册了 NAS 浏览预热线程。"""
    source = (
        Path(__file__).resolve().parents[1] / "services" / "api" / "main.py"
    ).read_text(encoding="utf-8")
    assert "def _start_nas_browse_warm" in source
    assert "_start_nas_browse_warm(stop_event)" in source
    assert "_nas_browse_cache_load" in source
    assert "_schedule_nas_children_prefetch" in source
    assert 'name="nas-browse-warm"' in source


def test_nas_browser_ui_status_hints_for_cache_and_stale() -> None:
    """状态栏对 from_cache / stale 有明确文案(秒开、后台刷新中)。"""
    script = (_repo_root() / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    assert "已缓存，秒开" in script
    assert "后台刷新中" in script
    assert "data.stale" in script


def test_nas_browser_unknown_count_in_scope_of_return() -> None:
    """`unknownCount` 必须在 if(status) 块外声明,否则每次浏览到 return 行都抛
    ReferenceError(const 块级作用域),状态栏显示成读取失败。"""
    script = (_repo_root() / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    fn = script[script.index("async function nasBrowserNavigate"):script.index("async function nasBrowserConfirmCreate")]
    decl = fn.index("const unknownCount")
    guard = fn.index("if (status) {")
    ret = fn.index("return !unknownCount")
    assert decl < guard, "unknownCount 声明必须提到 if(status) 块外(与 return 同作用域)"
    assert decl < ret, "return 的 unknownCount 引用必须在声明之后"
