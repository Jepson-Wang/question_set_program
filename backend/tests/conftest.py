import os

import pytest
import pytest_asyncio
import redis.asyncio as redis

from backend.core.config import load_env

# conftest 不 import 任何项目模块，没人替它加载 .env，必须自己来。
# 否则 REDIS_HOST 等一律读不到，全部回落 localhost，测试会以
# 「Redis 没开」的面目集体 skip。
load_env()

TEST_DB = 15


def _test_redis_url() -> str:
    """拼测试库（db 15）的连接串，与生产库隔离。"""
    host = os.getenv("REDIS_HOST", "localhost")
    port = os.getenv("REDIS_PORT", "6379")
    password = os.getenv("REDIS_PASSWORD")
    username = os.getenv("REDIS_USERNAME")

    # 用户名与密码之间是冒号；只有密码时要留一个空用户名位，
    # 写成 redis://password@host 会被解析成「用户名=password，密码=None」
    if password and username:
        return f"redis://{username}:{password}@{host}:{port}/{TEST_DB}"
    if password:
        return f"redis://:{password}@{host}:{port}/{TEST_DB}"
    if username:
        return f"redis://{username}@{host}:{port}/{TEST_DB}"
    return f"redis://{host}:{port}/{TEST_DB}"


@pytest_asyncio.fixture
async def redis_test_client():
    """
    指向 db 15 的独立客户端；每个测试前后各清一次库，保证测试互不污染。
    Redis 连不上时 skip 而不是报错——测试基建不该因为环境缺失而变成红色噪音。
    """
    client = redis.from_url(
        _test_redis_url(), encoding="utf-8", decode_responses=True
    )
    try:
        await client.ping()
    except Exception as e:
        await client.aclose()
        pytest.skip(f"测试需要可用的 Redis（db {TEST_DB}）：{e}")

    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()
