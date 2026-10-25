"""load_env_file（.env 加载器）的行为：解析规则、优先级、容错。

纯离线：全部用 tmp_path 与唯一键名，不碰真实 .env 的键。
"""

from __future__ import annotations

import os

from services.api.settings import load_env_file


def test_missing_file_is_silent(tmp_path) -> None:
    assert load_env_file(tmp_path / "nope.env") == {}


def test_parses_comments_quotes_and_export(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# 注释行",
                "TEST_ENV_LOADER_PLAIN=8788",
                "export TEST_ENV_LOADER_EXPORT=yes",
                'TEST_ENV_LOADER_QUOTED="hello world"',
                "TEST_ENV_LOADER_SINGLE='abc'",
                "没有等号的行",
                "=空键",
            ]
        ),
        encoding="utf-8",
    )
    try:
        loaded = load_env_file(env_file)
        assert os.environ.get("TEST_ENV_LOADER_PLAIN") == "8788"
        assert os.environ.get("TEST_ENV_LOADER_QUOTED") == "hello world"
    finally:
        for key in (
            "TEST_ENV_LOADER_PLAIN",
            "TEST_ENV_LOADER_EXPORT",
            "TEST_ENV_LOADER_QUOTED",
            "TEST_ENV_LOADER_SINGLE",
        ):
            os.environ.pop(key, None)
    assert loaded == {
        "TEST_ENV_LOADER_PLAIN": "8788",
        "TEST_ENV_LOADER_EXPORT": "yes",
        "TEST_ENV_LOADER_QUOTED": "hello world",
        "TEST_ENV_LOADER_SINGLE": "abc",
    }


def test_existing_environment_wins(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_ENV_LOADER_PRECEDENCE", "from-env")
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_ENV_LOADER_PRECEDENCE=from-file\n", encoding="utf-8")
    loaded = load_env_file(env_file)
    assert loaded == {}
    assert os.environ["TEST_ENV_LOADER_PRECEDENCE"] == "from-env"


def test_real_env_file_contract() -> None:
    """本机 .env 约定：绝不放 API_HOST（会锁设置页局域网开关）；代理键存在。"""

    from services.api import settings

    assert (settings.PROJECT_ROOT / ".env").is_file()
    active = {}
    for line in (settings.PROJECT_ROOT / ".env").read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#") and "=" in text:
            key, _, value = text.partition("=")
            active[key.strip()] = value.strip()
    assert "API_HOST" not in active, active.keys()
    assert active.get("API_PORT") == "8788", active
    assert "GITHUB_UPDATE_PROXY" in active, active.keys()
