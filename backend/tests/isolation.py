"""
在全新的 Python 子进程里运行一段代码的测试辅助工具。

用于测试「模块导入时读取配置」这类行为：
1. 子进程里没有被本进程导入过的模块，也没有已经加载过的 .env，测到的就是真实的首次导入
2. 子进程写进环境变量的值、导入的模块都随进程结束而消失，不会污染后面的测试

为什么不用 importlib.reload + monkeypatch：
- load_dotenv 会把 .env 里的值写进 os.environ。monkeypatch.delenv 只恢复它删掉的键，
  测试前本来就不存在、由 .env 新写进去的键不会被清理，后面的测试会读到假值
- reload 会把模块里的全局变量改成测试用的值，测试结束后也不恢复
代价是每个测试多花一两秒启动解释器，这样用的测试不多，可以接受。
"""
import os
import subprocess
import sys
from pathlib import Path

from backend.core.config import BACKEND_ROOT

# 启动子进程前从环境里去掉的键：load_env 不覆盖已存在的变量，
# 不去掉的话读到的是外面（开发机或 CI）的值，测试结果就取决于谁在跑它
CONFIG_KEYS = (
    "API_KEY", "API_URL", "MODEL_NAME", "EMBEDDING_MODEL", "SQL_DATABASE_URL", "JWT_SECRET_KEY",
    "REDIS_URL", "REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "REDIS_USERNAME", "REDIS_DB",
    "APP_DEBUG", "CORS_ALLOW_ORIGINS",
)


def write_env_file(path: Path, values: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path


def run_isolated(code: str, env_file: Path, cwd: Path,
                 extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """以 cwd 为工作目录、把 ENV_PATH 指向 env_file，在新进程里执行 code。"""
    env = {k: v for k, v in os.environ.items() if k not in CONFIG_KEYS}
    env["PYTHONPATH"] = str(BACKEND_ROOT.parent)
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra_env or {})
    prelude = (
        "import backend.core.config as cfg\n"
        "from pathlib import Path\n"
        f"cfg.ENV_PATH = Path({str(env_file)!r})\n"
    )
    return subprocess.run(
        [sys.executable, "-c", prelude + code],
        cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
