import logging
import time
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# ─── 日志目录 ───
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

# ─── 日志格式 ───
_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_initialized = False


def setup_logging(level: str = "INFO"):
    """
    全局调用一次（在 main.py 启动阶段）。
    - console handler：输出到 stderr
    - file handler：RotatingFileHandler，单文件 5MB，保留 3 个备份
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    _LOG_DIR.mkdir(exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    # console
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)

    # file
    file_handler = RotatingFileHandler(
        _LOG_DIR / "app.log",
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 降低第三方库噪音
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """各模块统一入口：get_logger(__name__)"""
    return logging.getLogger(name)


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    HTTP 请求日志中间件：
    → 2024-01-01 12:00:00 | INFO    | middleware.logging | POST /agent/analyse 200 153ms
    """
    def __init__(self, app):
        super().__init__(app)
        self._logger = get_logger("middleware.logging")

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        self._logger.info(
            "%s %s %s %.0fms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response
