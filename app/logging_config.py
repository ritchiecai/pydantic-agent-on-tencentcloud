"""应用日志配置：可选地把日志落地到文件（带轮转），默认仅走 uvicorn 的 stdout。

设计取舍：
- 未设 ``LOG_FILE`` 时**不做任何文件配置**，保持 uvicorn 默认 stdout/journald 行为，
  本地开发零侵入；只设置应用 logger（``agent``）的级别。
- 设了 ``LOG_FILE`` 时，用 ``RotatingFileHandler`` 把 root 与 uvicorn 的访问/错误日志
  一并写入该文件（自带按大小轮转）。
- 用编程式 ``addHandler``（而非 ``dictConfig`` 整体替换），与 uvicorn 自身的 logging
  配置共存、不依赖 import 顺序。
- 幂等：重复调用不会重复挂载同一文件 handler（靠 handler 名标记去重）。

环境变量：
- ``LOG_FILE``        ：日志文件路径；未设则禁用文件日志。
- ``LOG_LEVEL``       ：日志级别（如 INFO/DEBUG），默认 ``INFO``。
- ``LOG_MAX_BYTES``   ：单文件最大字节数，默认 10MB。
- ``LOG_BACKUP_COUNT``：轮转保留份数，默认 5。
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 文件 handler 的名字标记，用于幂等判断（避免重复挂载）。
_HANDLER_TAG = "agent_file_log"

# 接入文件日志的 logger：root（应用日志）+ uvicorn 三件套（访问/错误日志）。
_TARGET_LOGGERS = ("", "uvicorn", "uvicorn.access", "uvicorn.error")


def setup_logging() -> logging.Logger:
    """按环境变量配置应用日志，返回应用 logger（``agent``）。"""
    logger = logging.getLogger("agent")

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)

    log_file = os.environ.get("LOG_FILE")
    if not log_file:
        # 未配置文件日志：保持 uvicorn 默认 stdout 行为，仅设应用 logger 级别。
        return logger

    max_bytes = int(os.environ.get("LOG_MAX_BYTES", 10 * 1024 * 1024))
    backup_count = int(os.environ.get("LOG_BACKUP_COUNT", 5))

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    handler.set_name(_HANDLER_TAG)

    for name in _TARGET_LOGGERS:
        target = logging.getLogger(name)
        target.setLevel(level)
        already = any(getattr(h, "name", None) == _HANDLER_TAG for h in target.handlers)
        if not already:
            target.addHandler(handler)

    logger.info("file logging enabled: %s (level=%s)", log_file, level_name)
    return logger
