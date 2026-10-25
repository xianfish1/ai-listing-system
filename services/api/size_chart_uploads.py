from __future__ import annotations

import base64
import binascii
import uuid
from pathlib import Path
from typing import Any


MAX_UPLOAD_BYTES = 5 * 1024 * 1024
SIGNATURES = {
    "image/png": (b"\x89PNG\r\n\x1a\n", "png"),
    "image/jpeg": (b"\xff\xd8\xff", "jpg"),
    "image/webp": (b"RIFF", "webp"),
}

# Upload kinds share one storage/serving implementation; only the subdirectory,
# preview route and fallback file name differ.
IMAGE_KINDS: dict[str, dict[str, Any]] = {
    "size_chart": {
        "directory": "size-charts",
        "preview_url": "/api/uploads/size-chart/{asset_id}",
        "default_stem": "size-chart",
    },
    "default_sku": {
        "directory": "default-sku",
        "preview_url": "/api/uploads/default-sku-image/{asset_id}",
        "default_stem": "default-sku",
    },
    "product_image": {
        "directory": "product-images",
        "preview_url": "/api/uploads/product-image/{asset_id}",
        "default_stem": "product-image",
        # 操作员自上传的商品图来自手机相册/微信，往往比尺码图大一圈。
        "max_bytes": 10 * 1024 * 1024,
    },
}


class SizeChartUploadError(ValueError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


# Generic alias — the same error surface serves every upload kind.
ImageUploadError = SizeChartUploadError


def _kind_config(kind: str) -> dict[str, str]:
    config = IMAGE_KINDS.get(str(kind or "").strip())
    if config is None:
        raise ImageUploadError(f"未知的图片用途: {kind}", "unsupported_image_kind")
    return config


def save_image_upload(
    payload: dict[str, Any], *, storage_dir: Path, kind: str = "size_chart"
) -> dict[str, Any]:
    info, _target = store_image_upload(payload, storage_dir=storage_dir, kind=kind)
    return info


def store_image_upload(
    payload: dict[str, Any], *, storage_dir: Path, kind: str = "size_chart"
) -> tuple[dict[str, Any], Path]:
    """校验并落盘，返回 (上传信息, 存储路径)。

    需要把上传文件登记成素材（商品图）时用这个版本拿路径，其余调用方沿用
    ``save_image_upload`` 的返回结构。"""
    config = _kind_config(kind)
    max_bytes = int(config.get("max_bytes") or MAX_UPLOAD_BYTES)
    content_type = str(payload.get("content_type") or "").strip().lower()
    signature = SIGNATURES.get(content_type)
    if signature is None:
        raise ImageUploadError("只允许上传 PNG、JPEG 或 WebP 图片", "unsupported_image_type")
    try:
        content = base64.b64decode(str(payload.get("content_base64") or ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageUploadError("图片内容不是有效的 base64", "invalid_base64") from exc
    if not content or len(content) > max_bytes:
        raise ImageUploadError(
            f"图片为空或超过 {max_bytes // (1024 * 1024)} MiB", "image_size_invalid"
        )
    prefix, extension = signature
    valid_signature = content.startswith(prefix)
    if content_type == "image/webp":
        valid_signature = valid_signature and len(content) >= 12 and content[8:12] == b"WEBP"
    if not valid_signature:
        raise ImageUploadError("图片内容与声明格式不一致", "invalid_image_signature")
    asset_id = str(uuid.uuid4())
    target_dir = storage_dir / "uploads" / config["directory"]
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{asset_id}.{extension}"
    target.write_bytes(content)
    return (
        {
            "id": asset_id,
            "file_name": str(payload.get("file_name") or f"{config['default_stem']}.{extension}"),
            "content_type": content_type,
            "preview_url": config["preview_url"].format(asset_id=asset_id),
        },
        target,
    )


def resolve_image_upload(
    asset_id: str, *, storage_dir: Path, kind: str = "size_chart"
) -> tuple[Path, str] | None:
    config = _kind_config(kind)
    normalized = str(asset_id or "").strip()
    if not normalized or any(char not in "0123456789abcdef-" for char in normalized.lower()):
        return None
    for content_type, (_, extension) in SIGNATURES.items():
        path = storage_dir / "uploads" / config["directory"] / f"{normalized}.{extension}"
        if path.is_file():
            return path, content_type
    return None


def save_size_chart(payload: dict[str, Any], *, storage_dir: Path) -> dict[str, Any]:
    return save_image_upload(payload, storage_dir=storage_dir, kind="size_chart")


def resolve_size_chart(asset_id: str, *, storage_dir: Path) -> tuple[Path, str] | None:
    return resolve_image_upload(asset_id, storage_dir=storage_dir, kind="size_chart")
