from pathlib import Path

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = BACKEND_ROOT / ".env"

_loaded = False

def load_env() -> None:
    """
    幂等加载
    用绝对路径，不依赖当前工作目录，不覆盖已存在的真实环境变量
    """

    global _loaded
    if _loaded:
        return
    load_dotenv(ENV_PATH)
    _loaded = True
