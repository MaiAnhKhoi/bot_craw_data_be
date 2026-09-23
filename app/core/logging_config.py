"""Loguru: stdout cho người đọc, file xoay vòng cho việc truy vết về sau."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from loguru import logger

from app.core.config import get_settings

LOG_DIR = Path("var/logs")


class _InterceptHandler(logging.Handler):
    """Đẩy log của thư viện bên thứ ba (uvicorn, sqlalchemy) qua loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(process: str = "api") -> None:
    settings = get_settings()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        sys.stdout,
        level="DEBUG" if settings.debug else "INFO",
        format="<green>{time:HH:mm:ss}</green> <level>{level: <7}</level> {message}",
        backtrace=False,
        enqueue=True,
    )
    logger.add(
        LOG_DIR / f"{process}.log",
        level="DEBUG",
        rotation="20 MB",
        retention="14 days",
        encoding="utf-8",
        enqueue=True,
    )
    logging.basicConfig(handlers=[_InterceptHandler()], level=logging.INFO, force=True)
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "asyncio", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
