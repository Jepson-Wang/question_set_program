"""
JWT 密钥必须来自配置。

写死在代码里的密钥进了公开仓库，等于公开：任何人都能用它签一个
{"sub": "任意用户"} 的 token 冒充任何人调接口。

两个行为测试都在独立子进程里跑：security.py 在**导入时**读密钥、读不到就抛异常，
而本进程早就把它导入过了，改环境变量对已导入的模块不起作用。
"""
from backend.core.config import BACKEND_ROOT
from backend.tests.isolation import run_isolated, write_env_file

SIGN_AND_VERIFY = (
    "import jwt\n"
    "from backend.core.security import ALGORITHM, SECRET_KEY, create_access_token\n"
    "token = create_access_token({'sub': 'alice'})\n"
    "print(SECRET_KEY, jwt.decode(token, 'secret-from-dotenv', algorithms=[ALGORITHM])['sub'])\n"
)


def test_secret_key_is_read_from_config(tmp_path):
    env_file = write_env_file(tmp_path / ".env", {"JWT_SECRET_KEY": "secret-from-dotenv"})
    result = run_isolated(SIGN_AND_VERIFY, env_file, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["secret-from-dotenv", "alice"], \
        "签名用的必须是配置里的密钥，不是代码里写死的那个"


def test_missing_secret_key_fails_fast(tmp_path):
    """没配密钥时导入就报错，而不是悄悄用一个不安全的默认值继续跑"""
    env_file = write_env_file(tmp_path / ".env", {})
    result = run_isolated("import backend.core.security", env_file, cwd=tmp_path)
    assert result.returncode != 0, "缺密钥却能正常导入，说明还有默认值兜底"
    assert "JWT_SECRET_KEY" in result.stderr, result.stderr


def test_no_secret_is_hardcoded_in_the_source():
    """回归：泄露过的那个密钥不能再出现在源码里，注释掉的旧实现里也不行"""
    source = (BACKEND_ROOT / "core" / "security.py").read_text(encoding="utf-8")
    assert "3f8a2b1c9d4e7f" not in source, "泄露的密钥仍在源码中"
