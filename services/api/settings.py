from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from cryptography.fernet import Fernet


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_env_file(path: Path | None = None) -> dict[str, str]:
    """读取项目根 .env（KEY=VALUE，支持 # 注释、export 前缀与成对引号）。

    只补缺（os.environ 里已有的键不动），所以启动脚本/系统环境变量永远优先。
    在 import 时执行一次：下面的常量都在 import 时求值，晚了读不到。
    注意：API 进程、更新器（scripts/apply_update.py）与发布 CLI 都 import
    本模块，因此三者看到同一份 .env；测试进程也 import 它，但绑定端口的
    测试一律显式传端口 0，不受 .env 影响。
    """

    file = Path(path) if path is not None else PROJECT_ROOT / ".env"
    loaded: dict[str, str] = {}
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return loaded
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        if text.startswith("export "):
            text = text[len("export ") :].strip()
            if "=" not in text:
                continue
        key, _, value = text.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not key:
            continue
        if key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


if "pytest" not in sys.modules:
    # 测试进程不加载本机 .env：否则本机配置（如 GITHUB_UPDATE_PROXY）会改变
    # 测试里 git/HTTP 的行为断言。测试要用 .env 语义时显式调 load_env_file(path)。
    load_env_file()


def _path_from_env(name: str, default: str) -> Path:
    raw = os.getenv(name, default)
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


API_HOST = (os.getenv("API_HOST", "127.0.0.1") or "127.0.0.1").strip()
API_PORT = int(os.getenv("API_PORT", "8787"))
# 局域网/多客户端：监听队列要能吸收一次首屏的十几个并发连接；
# HTTP/1.1 连接复用让多台电脑重复轮询时不用每次重新握手。
API_REQUEST_QUEUE_SIZE = max(16, int(os.getenv("API_REQUEST_QUEUE_SIZE", "256")))
API_HTTP_PROTOCOL = os.getenv("API_HTTP_PROTOCOL", "HTTP/1.1").strip() or "HTTP/1.1"
API_KEEPALIVE_TIMEOUT_SECONDS = max(5, int(os.getenv("API_KEEPALIVE_TIMEOUT_SECONDS", "65")))
# GET 响应的内存短期缓存（秒）：只用于合并同一瞬间多个客户端/多个标签页的
# 相同请求，写入时立即失效；0 = 关闭。
API_GET_CACHE_TTL_SECONDS = max(0.0, float(os.getenv("API_GET_CACHE_TTL_SECONDS", "1.0")))
TASK_STALE_MINUTES = int(os.getenv("TASK_STALE_MINUTES", "45"))
TASK_MAX_ATTEMPTS = int(os.getenv("TASK_MAX_ATTEMPTS", "3"))
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "json").strip().lower()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DATA_DIR = _path_from_env("DATA_DIR", "data")
STORAGE_DIR = _path_from_env("STORAGE_DIR", "storage")
WEB_DIR = PROJECT_ROOT / "apps" / "web"
NAS_ASSET_ROOT = _path_from_env("NAS_ASSET_ROOT", "sample_assets")
MABANG_TEMPLATE_PATH = _path_from_env("MABANG_TEMPLATE_PATH", r"\\Juns\产品编辑部\宁\产品导入示例模板 (1).xlsx")
MABANG_WORKSPACE_URL = os.getenv(
    "MABANG_WORKSPACE_URL",
    "https://www.mabangerp.com/index.php?mod=main.gotoApp&v=v3&menuKey=M0010813&platform=shopee&version=1",
).strip()
WORKER_CDP_URL = os.getenv("WORKER_CDP_URL", "http://127.0.0.1:9223").strip()
WORKER_POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "3"))
WORKER_LEASE_SECONDS = int(os.getenv("WORKER_LEASE_SECONDS", "120"))

STORE_FILE = DATA_DIR / "store.json"
SCREENSHOT_DIR = STORAGE_DIR / "screenshots"
LOG_DIR = STORAGE_DIR / "logs"
EXPORT_DIR = STORAGE_DIR / "exports"
AI_CREDENTIALS_MASTER_KEY = os.getenv("AI_CREDENTIALS_MASTER_KEY", "").strip()
_CREDENTIAL_KEY_LOCK = threading.Lock()


def get_ai_credentials_master_key() -> str:
    """Return the configured key or provision one stable local key on first use."""

    configured = str(AI_CREDENTIALS_MASTER_KEY or "").strip()
    if configured:
        _validate_fernet_key(configured)
        return configured

    key_file = DATA_DIR / "ai_credentials_master.key"
    with _CREDENTIAL_KEY_LOCK:
        if key_file.is_file():
            value = key_file.read_text(encoding="ascii").strip()
            _validate_fernet_key(value)
            return value
        key_file.parent.mkdir(parents=True, exist_ok=True)
        value = Fernet.generate_key().decode("ascii")
        key_file.write_text(value + "\n", encoding="ascii")
        return value


def _validate_fernet_key(value: str) -> None:
    try:
        Fernet(value.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise ValueError("AI_CREDENTIALS_MASTER_KEY 必须是有效的 Fernet 密钥") from exc
