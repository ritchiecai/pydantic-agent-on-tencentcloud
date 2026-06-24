"""app.logging_config.setup_logging 的单元测试。

覆盖三种行为：
- 未设 LOG_FILE 时不挂任何文件 handler（本地无侵入）。
- 设了 LOG_FILE 时挂 RotatingFileHandler、可写入、root 与 uvicorn 均接入。
- 重复调用幂等，不重复挂载同一 handler。

每个用例后清理被挂上的标记 handler，避免污染全局 logging 状态影响其他测试。
"""

import logging
from pathlib import Path

import pytest

from app.logging_config import _HANDLER_TAG, _TARGET_LOGGERS, setup_logging


def _tagged_handlers(name: str) -> list[logging.Handler]:
    return [
        h
        for h in logging.getLogger(name).handlers
        if getattr(h, "name", None) == _HANDLER_TAG
    ]


@pytest.fixture(autouse=True)
def _cleanup_handlers():
    """测试后移除并关闭本模块挂上的标记 handler，复位全局 logging。"""
    yield
    for name in _TARGET_LOGGERS:
        logger = logging.getLogger(name)
        for h in _tagged_handlers(name):
            logger.removeHandler(h)
            h.close()


def test_no_log_file_does_not_attach_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """未设 LOG_FILE 时不应挂任何文件 handler。"""
    monkeypatch.delenv("LOG_FILE", raising=False)
    setup_logging()
    for name in _TARGET_LOGGERS:
        assert _tagged_handlers(name) == []


def test_log_file_attaches_handler_and_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """设了 LOG_FILE 时挂 handler、root 与 uvicorn 均接入，且能写入文件。"""
    log_file = tmp_path / "app.log"
    monkeypatch.setenv("LOG_FILE", str(log_file))
    setup_logging()

    for name in _TARGET_LOGGERS:
        assert len(_tagged_handlers(name)) == 1

    logging.getLogger("agent").info("hello from test")
    for h in _tagged_handlers(""):
        h.flush()
    assert log_file.exists()
    assert "hello from test" in log_file.read_text(encoding="utf-8")


def test_idempotent_on_repeated_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """重复调用不应重复挂载同一文件 handler。"""
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "app.log"))
    setup_logging()
    setup_logging()
    for name in _TARGET_LOGGERS:
        assert len(_tagged_handlers(name)) == 1
