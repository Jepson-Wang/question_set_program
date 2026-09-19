"""
配置加载的三条约定：
1. .env 的位置由代码本身算出来（相对 config.py 的绝对路径），跟谁在哪个目录启动无关
2. 每个读配置的模块自己调 load_env()，不靠「希望别人先导入过」
3. 真实环境变量优先于 .env 文件 —— 容器里注入的配置必须盖过镜像里带的文件

原来这些测试直接断言真实的 backend/.env 里有值。那测的是「这台机器上有没有
配好 .env」，不是代码行为：.env 不进版本库，CI 上拉下来的代码里根本没有这个
文件，三条全挂。现在改成在子进程里用一份临时 .env，测的才是代码本身。

为什么必须开子进程见 backend/tests/isolation.py 的模块注释。
"""
import os

from backend.core.config import BACKEND_ROOT, ENV_PATH
from backend.tests.isolation import run_isolated, write_env_file

# 临时 .env 的内容。值都取成一眼能认出来源的样子，断言失败时能直接看出
# 读到的是这份文件、是真实环境变量、还是代码里的默认值
DOTENV = {
    "API_KEY": "key-from-dotenv",
    "API_URL": "http://dotenv.invalid/v1",
    "MODEL_NAME": "model-from-dotenv",
    "REDIS_HOST": "dotenv.invalid",
    "REDIS_PORT": "6380",
    "REDIS_USERNAME": "user-from-dotenv",
    "REDIS_PASSWORD": "pw-from-dotenv",
}


def _layout(tmp_path):
    """.env 和工作目录放在两个不同的目录，这样「按 cwd 找文件」的写法一定读不到"""
    env_file = write_env_file(tmp_path / "config" / ".env", DOTENV)
    cwd = tmp_path / "work"
    cwd.mkdir()
    return env_file, cwd


def test_env_path_is_absolute_and_points_at_backend():
    """ENV_PATH 由 config.py 的 __file__ 推出来，因此与当前工作目录无关"""
    assert ENV_PATH.is_absolute()
    assert ENV_PATH.name == ".env"
    assert ENV_PATH.parent == BACKEND_ROOT
    assert BACKEND_ROOT.name == "backend"


def test_load_env_reads_the_configured_path_not_the_cwd(tmp_path):
    env_file, cwd = _layout(tmp_path)
    result = run_isolated(
        "import os\n"
        "cfg.load_env()\n"
        "print(os.getenv('API_KEY'))\n",
        env_file, cwd=cwd,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "key-from-dotenv", \
        "换个工作目录就读不到 .env，说明用的是相对路径"


def test_load_env_is_idempotent(tmp_path):
    """第二次调用直接返回，不会把已经改过的环境变量冲掉"""
    env_file, cwd = _layout(tmp_path)
    result = run_isolated(
        "import os\n"
        "cfg.load_env()\n"
        "os.environ['API_KEY'] = 'changed-after-first-load'\n"
        "cfg.load_env()\n"
        "print(os.getenv('API_KEY'), cfg._loaded)\n",
        env_file, cwd=cwd,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["changed-after-first-load", "True"]


def test_real_env_overrides_dotenv(tmp_path):
    """容器里注入的环境变量优先级必定高于 .env 文件（12-Factor）"""
    env_file, cwd = _layout(tmp_path)
    result = run_isolated(
        "import os\n"
        "cfg.load_env()\n"
        "print(os.getenv('API_KEY'))\n",
        env_file, cwd=cwd,
        extra_env={"API_KEY": "sentinel-from-real-env"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "sentinel-from-real-env"


def test_get_llm_reads_config_without_prior_load(tmp_path):
    """get_llm 是子进程里第一个被导入的项目模块，此时还没人调过 load_env"""
    env_file, cwd = _layout(tmp_path)
    result = run_isolated(
        "import backend.agents.agent.get_llm as m\n"
        "print(m.api_key, m.base_url, m.model)\n",
        env_file, cwd=cwd,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [
        "key-from-dotenv", "http://dotenv.invalid/v1", "model-from-dotenv",
    ], "get_llm 必须自己调 load_env，不能依赖导入顺序"


def test_redis_url_carries_credentials_without_prior_load(tmp_path):
    """
    redis_client 若不自己 load_env，密码就读成 None，连接时报
    AuthenticationError —— 一个和根因毫无关系的错误
    """
    env_file, cwd = _layout(tmp_path)
    result = run_isolated(
        "import backend.utils.redis_client as rc\n"
        "print(rc._build_redis_url())\n",
        env_file, cwd=cwd,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == \
        "redis://user-from-dotenv:pw-from-dotenv@dotenv.invalid:6380/0"


def test_dotenv_is_not_committed():
    """回归：.env 里是真密钥，一旦进了版本库就等于公开"""
    assert os.system(f'git -C "{BACKEND_ROOT.parent}" check-ignore -q backend/.env') == 0, \
        "backend/.env 没有被 .gitignore 忽略"
