import importlib
import os


def test_env_path_is_absolute_and_points_at_backend():
    """保证env_path是绝对路径，并且指向backend"""
    from backend.core.config import BACKEND_ROOT,ENV_PATH

    assert ENV_PATH.is_absolute()
    assert ENV_PATH.name == ".env"
    assert ENV_PATH.parent == BACKEND_ROOT
    assert BACKEND_ROOT.name == "backend"

def test_load_env_works_from_any_cwd(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("API_KEY",raising=False)

    import backend.core.config as cfg
    cfg._loaded = False
    cfg.load_env()

    assert os.getenv("API_KEY"), "换个工作目录就读不到 .env，说明用的是相对路径"

def test_load_env_is_idempotent():
    import backend.core.config as cfg
    cfg._loaded = False
    cfg.load_env()
    cfg.load_env()
    assert cfg._loaded is True

def test_real_env_overrides_dotenv(monkeypatch):
    """容器中注入的环境变量优先级必定高于.env文件"""
    monkeypatch.setenv("API_KEY","sentinel-from-real-env")

    import backend.core.config as cfg
    cfg._loaded = False
    cfg.load_env()

    assert os.getenv("API_KEY") == "sentinel-from-real-env"

def test_get_llm_reads_config_without_prior_load(tmp_path,monkeypatch):
    """get_llm 原本完全不加载.env，靠导入拿到值"""
    monkeypatch.chdir(tmp_path)
    for key in ("API_KEY", "API_URL", "MODEL_NAME", "EMBEDDING_MODEL"):
        monkeypatch.delenv(key,raising=False)

    import backend.core.config as cfg
    cfg._loaded = False
    import backend.agents.agent.get_llm as m
    importlib.reload(m)

    assert m.api_key, "get_llm 必须自己调 load_env，不能依赖导入顺序"
    assert m.base_url

def test_redis_url_carries_credentials_without_prior_load(tmp_path,monkeypatch):
    """
    redis_clent原本完全不加载.env，密码读成None
    表现为AuthenticationError -> 一个和根因毫无关系的错误
    """
    monkeypatch.chdir(tmp_path)
    for key in ("REDIS_URL", "REDIS_HOST", "REDIS_PORT",
                "REDIS_PASSWORD", "REDIS_USERNAME"):
        monkeypatch.delenv(key,raising=False)

    import backend.core.config as cfg
    cfg._loaded = False
    import backend.utils.redis_client as rc
    importlib.reload(rc)

    url = rc._build_redis_url()
    assert os.getenv("REDIS_PASSWORD"), "load_env 应当已把 .env 里的密码读进来"
    assert "@" in url, "URL 必须带认证信息，否则会以 AuthenticationError 的面目失败"