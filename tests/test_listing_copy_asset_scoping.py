"""副本/任务的素材解析必须限定在「这次导入目录」内。

现场（HN0244）：同一个 SKU 文件夹导入过两次，两个上传目录里有同名文件
（``服装颜色替换生成图片 (1).png`` …）但图片内容不同。复制到店铺副本后，副本只存文件名，
执行器/界面按名字在**整组素材**里找路径 → 一排全落到旧导入那批图上，和商品维护里
看到的图不一样。这里锁住有 asset_root 时优先认本次导入目录的行为。
"""
from __future__ import annotations

from pathlib import Path

from services.api import storage

NEW_ROOT = str(Path("C:/uploads/new/HN0244"))
OLD_ROOT = str(Path("C:/uploads/old/HN0244"))


def _data() -> dict:
    return {
        "assets": [
            {"id": "old-1", "group_id": "HN0244", "asset_type": "variant", "file_name": "1.png",
             "file_path": str(Path(OLD_ROOT) / "1.png")},
            {"id": "new-1", "group_id": "HN0244", "asset_type": "variant", "file_name": "1.png",
             "file_path": str(Path(NEW_ROOT) / "1.png")},
            {"id": "manual", "group_id": "HN0244", "asset_type": "product", "file_name": "操作员上传.png",
             "origin": "manual_upload", "file_path": str(Path("C:/elsewhere/操作员上传.png"))},
            {"id": "other-group", "group_id": "HN9999", "asset_type": "variant", "file_name": "1.png",
             "file_path": str(Path(NEW_ROOT) / "1.png")},
        ]
    }


def test_assets_are_scoped_to_the_current_import_root() -> None:
    data = _data()

    scoped = storage._assets_for_group_in_store(data, "HN0244", NEW_ROOT)
    # 排序按 asset_type/file_name：自上传（product）排在变种图（variant）前。
    assert sorted(asset["id"] for asset in scoped) == ["manual", "new-1"]

    # 按名字解析路径时拿到的必须是本次导入目录那张，而不是同名旧图。
    paths = storage._asset_paths_for_names(scoped, ["1.png"])
    assert paths == [str(Path(NEW_ROOT) / "1.png")]


def test_missing_asset_root_keeps_previous_behaviour() -> None:
    data = _data()

    # 没有 asset_root（旧副本）：仍然整组返回，行为和改动前一致。
    assert len(storage._assets_for_group_in_store(data, "HN0244")) == 3
    # 路径写法对不上：退回整组，不能把图片全部藏掉。
    assert len(storage._assets_for_group_in_store(data, "HN0244", str(Path("C:/nowhere")))) == 3


def test_copy_asset_lookup_uses_asset_root(monkeypatch) -> None:
    """build_automation_payload 走 assets_for_base_product(copy)：副本自带 asset_root。"""

    data = _data()
    monkeypatch.setattr(
        storage,
        "assets_for_group",
        lambda group_id: [
            asset for asset in data["assets"]
            if str(asset.get("group_id") or "").upper() == str(group_id or "").upper()
        ],
    )
    copy = {"group_id": "HN0244", "asset_root": NEW_ROOT}

    assets = storage.assets_for_base_product(copy)
    assert sorted(asset["id"] for asset in assets) == ["manual", "new-1"]
    assert storage._asset_paths_for_names(assets, ["1.png"]) == [str(Path(NEW_ROOT) / "1.png")]
