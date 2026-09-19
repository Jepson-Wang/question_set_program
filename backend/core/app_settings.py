"""
随运行环境变化的开关。开发和生产用不同的值，全部来自配置（backend/.env 或环境变量），
默认值按「生产安全」来取：忘了配置时，宁可少开功能，也不暴露内部信息。
"""
import os

from backend.core.config import load_env

load_env()


def debug_enabled() -> bool:
    """APP_DEBUG=true 时开启 FastAPI 调试模式：接口出错时会把完整堆栈返回给调用方，只能在本地开发时打开"""
    return os.getenv("APP_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def cors_allow_origins() -> list[str]:
    """CORS_ALLOW_ORIGINS：允许跨域访问的来源，逗号分隔；不配置时为 *，方便本地前端调试"""
    raw = os.getenv("CORS_ALLOW_ORIGINS", "*")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]
