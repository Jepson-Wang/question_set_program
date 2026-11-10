"""
画像更新路径的行锁，在真实 MySQL 上验证「锁住了」。

test_profile_mapper.py 里的 test_update_reads_with_row_lock 只能证明生成的 SQL 带了
FOR UPDATE：那批测试跑在 SQLite 上，而 SQLite 根本没有行锁。锁到底能不能把并发的两次
合并串起来，只能在 MySQL 上看。

连哪个库由 TEST_SQL_DATABASE_URL 决定，**不复用 SQL_DATABASE_URL**：测试会建表、写入
再删除数据，不能默认落到开发库上。没设置就跳过，设置 REQUIRE_MYSQL=1 时改为失败。
测试只读写 user_id = SENTINEL_USER 这一行，前后各清理一次。
"""
import asyncio
import os

import pytest_asyncio
from sqlalchemy import delete, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.dao.user_profile_mapper import UserProfileMapper
from backend.model import Base
from backend.model.user_profile import UserProfile
from backend.schemas.request.user_profile_update_request import UserProfileUpdateRequest
from backend.tests.conftest import mysql_unavailable

# 离真实用户 id 足够远；user_id 是 INT，上限 2147483647
SENTINEL_USER = 2_000_000_007


async def _delete_sentinel(maker) -> None:
    async with maker() as session:
        await session.execute(delete(UserProfile).where(UserProfile.user_id == SENTINEL_USER))
        await session.commit()


@pytest_asyncio.fixture
async def mysql_maker():
    url = os.getenv("TEST_SQL_DATABASE_URL")
    if not url:
        mysql_unavailable("未设置 TEST_SQL_DATABASE_URL，行锁的行为测试需要真实 MySQL")

    engine = create_async_engine(url, connect_args={"connect_timeout": 5})
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as e:
        await engine.dispose()
        mysql_unavailable(f"测试需要可用的 MySQL：{type(e).__name__}: {e}")

    maker = async_sessionmaker(engine, expire_on_commit=False)
    await _delete_sentinel(maker)
    yield maker
    await _delete_sentinel(maker)
    await engine.dispose()


async def test_concurrent_merges_are_serialized_by_row_lock(mysql_maker):
    """
    holder 扮演请求 A：已经用加锁读拿到这一行、正在合并。此时请求 B 走 update_user_profile。

    有行锁：B 的加锁读被挡住，A 提交后 B 读到的是 A 写完的版本，合并出三个键。
    没有行锁：B 用快照读拿到旧值，合并出 {已有, 讲解详细度}，在 A 之后提交，整列覆盖掉
    A 写入的「题目风格」——最后那条断言失败。

    注意「B 此时还没完成」这条断言单独区分不了有没有锁：没有 FOR UPDATE 时，B 的 UPDATE
    同样会被 A 持有的 X 锁挡住。真正能区分的是最终结果，也就是 B 合并时读到的是不是新值。
    """
    mapper = UserProfileMapper(mysql_maker)
    await mapper.create_memory(UserProfile(
        user_id=SENTINEL_USER, grade="七年级", subject="数学",
        weak_points={}, preferences={"已有": "保留"}, notes=[],
    ))

    async with mysql_maker() as holder:
        # A 的锁用原生 SQL 拿，不经过 with_for_update：反向验证时只摘掉被测代码里的锁，
        # A 这边的锁必须还在，否则测的就不是「B 有没有加锁」了
        await holder.execute(
            text("SELECT id FROM user_profile WHERE user_id = :uid FOR UPDATE"),
            {"uid": SENTINEL_USER},
        )

        b = asyncio.create_task(mapper.update_user_profile(
            UserProfileUpdateRequest(user_id=SENTINEL_USER, preferences={"讲解详细度": "详细"})
        ))
        try:
            await asyncio.sleep(0.5)
            assert not b.done(), "A 还持有行锁，B 不应该已经写完"

            # A 在锁内读到的是 {已有}，合并上自己的新键后写回
            await holder.execute(
                update(UserProfile)
                .where(UserProfile.user_id == SENTINEL_USER)
                .values(preferences={"已有": "保留", "题目风格": "简单"})
            )
            await holder.commit()
        finally:
            # 断言失败时也要让 B 收尾：holder 退出时回滚并释放锁，B 随之完成
            if not b.done() and holder.in_transaction():
                await holder.rollback()

    await asyncio.wait_for(b, timeout=10)

    got = await mapper.get_by_user_id(SENTINEL_USER)
    assert got.preferences == {"已有": "保留", "题目风格": "简单", "讲解详细度": "详细"}
