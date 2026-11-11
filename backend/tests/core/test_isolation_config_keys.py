"""
CONFIG_KEYS 是手工维护的清单，它腐烂过一次：漏了 14 个键
（模型覆盖与 RAG 配置那一批，见 docs/reviews/2026-09-26-project-issues.md 第 2 条）。

漏掉一个键的后果不是报错，而是子进程读到开发机上的真实值——
测试结果取决于谁在哪台机器上跑。不报错的东西最容易烂下去，
所以这里把「清单和代码是否对得上」变成一次 CI 失败。

为什么用 AST 扫描而不是正则：
正则会被注释、文档字符串里的 `os.getenv("...")` 例子骗到，
也会漏掉 rag/config.py 那种把键名传给 _env_bool / _env_float 的写法。
"""
import ast
import os
from pathlib import Path

from backend.core.config import BACKEND_ROOT
from backend.tests.isolation import CONFIG_KEYS

# 测试自己用的旋钮，故意不在清单里：
# REQUIRE_REDIS / REQUIRE_MYSQL 由 CI 通过环境变量显式设置，
# 目的正是让「依赖连不上就红」这件事传进子进程；剥掉它们会改变测试语义。
# TEST_SQL_DATABASE_URL 是真库测试的入口，同理。
TEST_ONLY_KEYS = {"REQUIRE_REDIS", "REQUIRE_MYSQL", "TEST_SQL_DATABASE_URL"}

# 读环境变量不止 os.getenv 一种写法：rag/config.py 用自己的
# _env_bool / _env_float 包了一层，键名以字符串字面量传给它们。
READERS = {"getenv", "_env_bool", "_env_float"}

# 不扫描的目录
SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for root, dirs, names in os.walk(BACKEND_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if name.endswith(".py"):
                files.append(Path(root) / name)
    return sorted(files)


def _keys_in_file(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            continue
        if name not in READERS:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.add(first.value)
    return found


def _all_keys_read_by_code() -> set[str]:
    keys: set[str] = set()
    for path in _iter_source_files():
        keys |= _keys_in_file(path)
    return keys


def test_scanner_actually_finds_keys():
    """
    守住扫描器本身。
    扫描器坏掉时如果只是「返回空集合」，下面那条测试会空跑通过，
    所以这里先断言它至少能找到几个已知的键。
    """
    keys = _all_keys_read_by_code()
    assert "API_KEY" in keys
    assert "MODEL_NAME" in keys
    assert "RAG_DB_DIR" in keys
    assert "RAG_ENABLED" in keys, "RAG_ENABLED 走的是 _env_bool，字面量也在扫描范围内"


def test_every_env_key_read_by_code_is_in_config_keys():
    """代码读到的每个环境变量，都必须出现在 CONFIG_KEYS 里"""
    missing = _all_keys_read_by_code() - set(CONFIG_KEYS) - TEST_ONLY_KEYS
    assert not missing, (
        f"这些环境变量被代码读取，但不在 CONFIG_KEYS 里：{sorted(missing)}\n"
        f"子进程测试会读到开发机上的值，结果取决于谁在跑。\n"
        f"请把它们加进 backend/tests/isolation.py 的 CONFIG_KEYS。"
    )


def test_config_keys_has_no_stale_entries():
    """反向检查：清单里的每个键都必须真的被代码用到，避免清单只涨不消"""
    stale = set(CONFIG_KEYS) - _all_keys_read_by_code()
    assert not stale, f"CONFIG_KEYS 里的这些键代码里读不到：{sorted(stale)}"


def test_test_only_keys_are_not_in_config_keys():
    """
    测试旋钮不能进清单：它们要由 CI 的环境变量原样传进子进程，
    剥掉会改变「连不上就红」的语义。
    """
    overlap = set(CONFIG_KEYS) & TEST_ONLY_KEYS
    assert not overlap, f"这些测试旋钮不该出现在 CONFIG_KEYS 里：{sorted(overlap)}"
