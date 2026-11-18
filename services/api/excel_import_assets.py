"""Download product images from source-site URLs into an import folder.

The Excel export points at a public CDN; the executor can only upload local
files, so every image is downloaded once into the batch product folder. Any
partial run reuses already-downloaded files, which makes "retry failed items"
cheap.
"""

from __future__ import annotations

import time
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from services.api.url_guard import is_private_or_local_host

MAX_IMAGE_BYTES = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")

_CONTENT_TYPE_EXTENSION = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_EXTENSION_FORMAT = {".jpg": "jpeg", ".png": "png", ".webp": "webp"}
_USER_AGENT = "ai-listing-system/1.0"


class ImageDownloadError(ValueError):
    """A source image could not be fetched or verified."""


def validate_image_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or is_private_or_local_host(parsed.hostname)
    ):
        raise ImageDownloadError(f"图片地址无效：{url}")
    return parsed.geturl()


def fetch_image(
    url: str,
    *,
    opener=urlopen,
    timeout: float = 30,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> tuple[bytes, str]:
    """Fetch one image and return ``(content, extension)``.

    ``opener`` is injectable so tests never touch the network.
    """
    target = validate_image_url(url)
    request = Request(target, headers={"User-Agent": _USER_AGENT})
    with opener(request, timeout=timeout) as response:
        content_type = str(response.headers.get_content_type() or "").lower()
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ImageDownloadError(f"图片超过 {max_bytes // (1024 * 1024)} MiB：{url}")
    extension = _CONTENT_TYPE_EXTENSION.get(content_type)
    if extension is None:
        raise ImageDownloadError(f"不支持的图片类型 {content_type or '(空)'}：{url}")
    _verify_image(data, extension, url)
    return data, extension


def _verify_image(data: bytes, extension: str, url: str) -> None:
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is optional at runtime
        return
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
            actual = str(image.format or "").lower()
    except Exception as exc:  # noqa: BLE001 - PIL raises many types
        raise ImageDownloadError(f"图片内容无法识别：{url}") from exc
    expected = _EXTENSION_FORMAT.get(extension)
    if expected and actual and actual != expected:
        raise ImageDownloadError(f"图片格式不符（声明 {extension}，实际 {actual}）：{url}")


def existing_image(directory: Path, stem: str) -> Path | None:
    for extension in ALLOWED_EXTENSIONS:
        candidate = directory / f"{stem}{extension}"
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return None


def download_image(
    url: str,
    directory: Path,
    stem: str,
    *,
    fetcher=fetch_image,
    attempts: int = 3,
    sleep=time.sleep,
) -> Path:
    """Download ``url`` to ``<directory>/<stem>.<ext>``, reusing a local copy."""
    safe_stem = Path(str(stem or "")).name.strip() or "image"
    directory.mkdir(parents=True, exist_ok=True)
    cached = existing_image(directory, safe_stem)
    if cached is not None:
        return cached

    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            content, extension = fetcher(url)
            target = directory / f"{safe_stem}{extension}"
            temporary = directory / f"{safe_stem}.part"
            temporary.write_bytes(content)
            temporary.replace(target)
            return target
        except Exception as exc:  # noqa: BLE001 - retry every transport failure
            last_error = exc
            if attempt < attempts:
                sleep(0.5 * attempt)
    raise ImageDownloadError(f"{url}：{last_error}") from last_error
