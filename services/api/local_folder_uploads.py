from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from workers.asset_scanner.scanner import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS


MAX_FILES = 3000
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
STALE_SECONDS = 24 * 60 * 60
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


class LocalFolderUploadError(ValueError):
    pass


class LocalFolderUploads:
    """Persist browser-selected product folders before the normal asset scanner reads them."""

    def __init__(self, storage_dir: Path) -> None:
        self.base_dir = Path(storage_dir) / "uploads" / "base-products"
        self._lock = threading.RLock()

    def create(self, folder_name: str, files: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            self._cleanup_stale()
            normalized_folder = _folder_name(folder_name)
            manifest = _manifest(files)
            upload_id = str(uuid.uuid4())
            session_dir = self.base_dir / upload_id
            root_dir = session_dir / normalized_folder
            root_dir.mkdir(parents=True)
            metadata = {
                "upload_id": upload_id,
                "folder_name": normalized_folder,
                "files": manifest,
                "created_at": time.time(),
            }
            _write_metadata(session_dir, metadata)
            return {"upload_id": upload_id, "folder_name": normalized_folder, "file_count": len(manifest)}

    def save_file(self, upload_id: str, relative_path: str, content: bytes) -> None:
        from io import BytesIO

        self.save_stream(upload_id, relative_path, BytesIO(content), len(content))

    def save_stream(self, upload_id: str, relative_path: str, source: Any, content_length: int) -> None:
        with self._lock:
            session_dir, metadata = self._session(upload_id)
            normalized_path = _relative_path(relative_path)
            declared_size = (metadata["files"] or {}).get(normalized_path)
            if declared_size is None:
                raise LocalFolderUploadError("文件不在上传清单中")
            if content_length != declared_size:
                raise LocalFolderUploadError("文件大小与上传清单不一致")
            target = _target_path(session_dir, metadata["folder_name"], normalized_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(f"{target.name}.part")
            written = 0
            try:
                with partial.open("wb") as handle:
                    while written < declared_size:
                        chunk = source.read(min(1024 * 1024, declared_size - written))
                        if not chunk:
                            break
                        handle.write(chunk)
                        written += len(chunk)
                if written != declared_size:
                    raise LocalFolderUploadError("文件大小与上传清单不一致")
                partial.replace(target)
            except Exception:
                partial.unlink(missing_ok=True)
                raise

    def complete(
        self,
        upload_id: str,
        create_base_product: Callable[[dict[str, Any]], dict[str, Any] | None],
        *,
        category: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            session_dir, metadata = self._session(upload_id)
            root_dir = session_dir / metadata["folder_name"]
            missing = [
                path for path, size in metadata["files"].items()
                if not (target := _target_path(session_dir, metadata["folder_name"], path)).is_file()
                or target.stat().st_size != size
            ]
            if missing:
                raise LocalFolderUploadError(f"尚未上传全部文件：{missing[0]}")
            try:
                product = create_base_product({"root_path": str(root_dir), "category": str(category or "")})
            except Exception:
                _try_remove_session_dir(session_dir)
                raise
            if not product:
                _try_remove_session_dir(session_dir)
                raise LocalFolderUploadError("未从上传文件夹识别到素材")
            _try_remove_session_metadata(session_dir)
            return product

    def cancel(self, upload_id: str) -> None:
        with self._lock:
            session_dir = self.base_dir / _upload_id(upload_id)
            if session_dir.exists():
                _try_remove_session_dir(session_dir)

    def _session(self, upload_id: str) -> tuple[Path, dict[str, Any]]:
        session_dir = self.base_dir / _upload_id(upload_id)
        metadata_path = session_dir / "session.json"
        if not metadata_path.is_file():
            raise LocalFolderUploadError("上传会话不存在或已完成")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LocalFolderUploadError("上传会话无效") from exc
        if not isinstance(metadata, dict) or not isinstance(metadata.get("files"), dict):
            raise LocalFolderUploadError("上传会话无效")
        return session_dir, metadata

    def _cleanup_stale(self) -> None:
        cutoff = time.time() - STALE_SECONDS
        for session_dir in self.base_dir.iterdir():
            if not session_dir.is_dir() or not (session_dir / "session.json").is_file():
                continue
            try:
                if (session_dir / "session.json").stat().st_mtime < cutoff:
                    shutil.rmtree(session_dir)
            except OSError:
                continue


def _upload_id(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise LocalFolderUploadError("上传会话标识无效") from exc


def _folder_name(value: str) -> str:
    name = str(value or "").strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise LocalFolderUploadError("文件夹名称无效")
    return name


def _relative_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise LocalFolderUploadError("文件必须使用安全的相对路径")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise LocalFolderUploadError("文件类型不支持")
    return path.as_posix()


def _manifest(files: list[dict[str, Any]]) -> dict[str, int]:
    if not isinstance(files, list) or not files:
        raise LocalFolderUploadError("上传清单不能为空")
    if len(files) > MAX_FILES:
        raise LocalFolderUploadError(f"单次最多上传 {MAX_FILES} 个文件")
    result: dict[str, int] = {}
    total = 0
    for item in files:
        if not isinstance(item, dict):
            raise LocalFolderUploadError("上传清单格式无效")
        path = _relative_path(item.get("path") or "")
        if path in result:
            raise LocalFolderUploadError("上传清单包含重复文件")
        try:
            size = int(item.get("size_bytes"))
        except (TypeError, ValueError) as exc:
            raise LocalFolderUploadError("文件大小无效") from exc
        if size < 0 or size > MAX_FILE_BYTES:
            raise LocalFolderUploadError("单个文件大小超限")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise LocalFolderUploadError("上传总大小超限")
        result[path] = size
    return result


def _target_path(session_dir: Path, folder_name: str, relative_path: str) -> Path:
    root = session_dir / folder_name
    target = root.joinpath(*PurePosixPath(relative_path).parts)
    try:
        target.relative_to(root)
    except ValueError as exc:  # Defensive guard if callers bypass _relative_path.
        raise LocalFolderUploadError("文件路径超出上传目录") from exc
    return target


def _write_metadata(session_dir: Path, metadata: dict[str, Any]) -> None:
    target = session_dir / "session.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def _try_remove_session_metadata(session_dir: Path) -> None:
    """Mark a completed session without failing when the host blocks deletion."""
    metadata = session_dir / "session.json"
    try:
        metadata.unlink(missing_ok=True)
    except OSError:
        try:
            metadata.replace(session_dir / ".completed")
        except OSError:
            pass


def _try_remove_session_dir(session_dir: Path) -> None:
    """Best-effort cleanup; upload/product operations must not fail on cleanup."""
    try:
        shutil.rmtree(session_dir, ignore_errors=True)
    except OSError:
        pass
