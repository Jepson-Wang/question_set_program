"""
启动时的 Redis 版本检查。

画像候选池用 HSETEX（Redis 8.0+）。版本不够时命令报 unknown command，
被业务层的 except 吞掉，表现为「偏好永远不晋升」——没有任何报错让人发现。
所以版本不够必须在启动时就失败，而不是留到第一次写入。
"""
import pytest

import backend.core.hooks as hooks
from backend.utils.redis_client import ensure_redis_version


class _FakeRedis:
    """只实现 info("server")：低版本 Redis 没法在测试里真起一个，只能模拟这一个返回值"""

    def __init__(self, version: str):
        self._version = version

    async def info(self, section=None):
        return {"redis_version": self._version}


async def test_real_redis_meets_minimum(redis_test_client):
    """CI 和本地跑的都是 8.x，真实连接必须通过——顺带验证解析的是真实的 INFO 格式"""
    version = await ensure_redis_version(redis_test_client)
    assert int(version.split(".")[0]) >= 8


async def test_old_redis_is_rejected():
    with pytest.raises(RuntimeError, match=r"7\.4\.2.*8\.0"):
        await ensure_redis_version(_FakeRedis("7.4.2"))


@pytest.mark.parametrize("version", ["8.0.0", "10.0.1"])
async def test_minimum_and_double_digit_major_are_accepted(version):
    """
    10.0.1 防的是按字符串比较：'10.0.1' < '8.0' 在字符串意义下成立，
    那样写会把更新的 Redis 当成旧版拒掉。
    """
    assert await ensure_redis_version(_FakeRedis(version)) == version


async def test_startup_fails_before_touching_database(monkeypatch):
    """
    版本检查放在建表之前：它不依赖 MySQL，又是确定性的配置错误，越早失败越好。
    engine 换成会炸的对象——如果启动流程先去建表，这里会抛 AssertionError 而不是 RuntimeError。
    """
    class _FakeClientWrapper:
        client = _FakeRedis("7.4.2")

    class _ExplodingEngine:
        def begin(self):
            raise AssertionError("版本不够时不应该走到建表")

    monkeypatch.setattr(hooks, "get_redis_client", lambda: _FakeClientWrapper())
    monkeypatch.setattr(hooks, "engine", _ExplodingEngine())

    with pytest.raises(RuntimeError, match="7.4.2"):
        await hooks.startup_event()
