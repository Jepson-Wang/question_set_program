"""生产与开发用不同的开关：调试模式默认关闭，CORS 来源可配置。"""
import pytest

from backend.core.app_settings import cors_allow_origins, debug_enabled
from backend.tests.isolation import run_isolated, write_env_file


@pytest.mark.parametrize("raw, expected", [
    (None, False), ("false", False), ("0", False), ("", False),
    ("true", True), ("1", True), ("ON", True),
])
def test_debug_flag(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("APP_DEBUG", raising=False)
    else:
        monkeypatch.setenv("APP_DEBUG", raw)
    assert debug_enabled() is expected


def test_cors_defaults_to_wildcard(monkeypatch):
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    assert cors_allow_origins() == ["*"]


def test_cors_parses_comma_separated_list(monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", " https://1.2.3.4 , http://localhost:5173 ,")
    assert cors_allow_origins() == ["https://1.2.3.4", "http://localhost:5173"]


APP_ENV = {
    "SQL_DATABASE_URL": "mysql+asyncmy://u:p@127.0.0.1:3306/db",
    "API_KEY": "k",
    "API_URL": "http://127.0.0.1:9/v1",
    "JWT_SECRET_KEY": "s",
}
INSPECT_APP = (
    "import backend.main as m\n"
    "cors = next(x for x in m.app.user_middleware if x.cls.__name__ == 'CORSMiddleware')\n"
    "print(m.app.debug, cors.kwargs['allow_origins'], cors.kwargs['allow_credentials'])\n"
)


def test_app_is_production_safe_by_default(tmp_path):
    """什么都不配时：不开调试模式（出错不回显堆栈）；来源是通配符时不允许携带凭据"""
    env_file = write_env_file(tmp_path / ".env", APP_ENV)
    result = run_isolated(INSPECT_APP, env_file, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "False ['*'] False"


def test_app_uses_configured_origins(tmp_path):
    env_file = write_env_file(
        tmp_path / ".env",
        {**APP_ENV, "APP_DEBUG": "true", "CORS_ALLOW_ORIGINS": "https://1.2.3.4"},
    )
    result = run_isolated(INSPECT_APP, env_file, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True ['https://1.2.3.4'] True"
