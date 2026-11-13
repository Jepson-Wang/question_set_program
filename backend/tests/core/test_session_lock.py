"""
同会话串行锁的测试。

锁要同时满足四件事，缺一不可：
1. 同一会话排队，不重叠
2. 不同会话并行，不能做成全局锁
3. 用完能回收，条目不无限堆积
4. 异常、取消都不留下死锁

另外还盯住最容易做错的一条：等待锁的人也要算进计数。
"""
import asyncio

import pytest

from backend.core.session_lock import SessionLockPool, session_locks


@pytest.fixture
def pool():
    """每个测试一个全新的池：池里的 asyncio.Lock 绑定了事件循环，不能跨测试复用"""
    return SessionLockPool()


async def _settle(times: int = 3) -> None:
    """让刚创建的任务跑到它的第一个 await 点（排队位置才能确定下来）"""
    for _ in range(times):
        await asyncio.sleep(0)


async def test_same_session_requests_do_not_overlap(pool):
    """同一会话的两个请求必须排队：进入/退出严格配对，不允许两个 enter 挨着出现。"""
    timeline = []

    async def worker(tag):
        async with pool.guard(1):
            timeline.append(f"enter-{tag}")
            await asyncio.sleep(0.01)
            timeline.append(f"exit-{tag}")

    await asyncio.gather(worker("a"), worker("b"))

    assert len(timeline) == 4
    for i in (0, 2):
        assert timeline[i].startswith("enter-")
        assert timeline[i + 1].startswith("exit-"), f"两次进入之间必须有退出：{timeline}"
    assert {timeline[0], timeline[2]} == {"enter-a", "enter-b"}


async def test_different_sessions_run_in_parallel(pool):
    """不同会话必须能并行：做成一把全局锁的话这里会等到超时。"""
    inside = 0
    both_inside = asyncio.Event()

    async def worker(session_id):
        nonlocal inside
        async with pool.guard(session_id):
            inside += 1
            if inside == 2:
                both_inside.set()
            # 只等对方进来，不等对方跑完
            await asyncio.wait_for(both_inside.wait(), timeout=2)
            inside -= 1

    await asyncio.gather(worker(1), worker(2))

    assert inside == 0


async def test_waiters_are_served_in_arrival_order(pool):
    """先到的请求先拿到锁：asyncio.Lock 的等待队列是 FIFO。"""
    order = []
    holder_ready = asyncio.Event()
    release_holder = asyncio.Event()

    async def holder():
        async with pool.guard(1):
            holder_ready.set()
            await release_holder.wait()

    async def waiter(tag):
        async with pool.guard(1):
            order.append(tag)

    h = asyncio.create_task(holder())
    await holder_ready.wait()

    first = asyncio.create_task(waiter("first"))
    await _settle()
    second = asyncio.create_task(waiter("second"))
    await _settle()

    # 持有者 + 两个等待者，计数要把等待的算上
    assert pool.users(1) == 3

    release_holder.set()
    await asyncio.gather(h, first, second)

    assert order == ["first", "second"]


async def test_lock_is_released_and_reusable(pool):
    """用完之后锁要还回去：下一个请求立刻就能拿到，不死锁。"""
    async def run():
        async with pool.guard(1):
            pass

    await asyncio.wait_for(run(), timeout=1)
    await asyncio.wait_for(run(), timeout=1)

    assert pool.size == 0


async def test_exception_inside_block_releases_lock(pool):
    """块内抛异常也要还锁：否则一次报错就把这个会话永久卡死。"""
    with pytest.raises(ValueError):
        async with pool.guard(1):
            raise ValueError("boom")

    assert pool.size == 0

    async with pool.guard(1):
        pass


async def test_cancelled_waiter_leaves_no_orphan_lock(pool):
    """等锁的人被取消（客户端断开）后，这个会话仍然要能被后来的请求进入。"""
    holder_ready = asyncio.Event()
    release_holder = asyncio.Event()

    async def holder():
        async with pool.guard(1):
            holder_ready.set()
            await release_holder.wait()

    async def waiter():
        async with pool.guard(1):
            pass

    h = asyncio.create_task(holder())
    await holder_ready.wait()
    w = asyncio.create_task(waiter())
    await _settle()
    assert pool.users(1) == 2

    w.cancel()
    with pytest.raises(asyncio.CancelledError):
        await w

    assert pool.users(1) == 1, "被取消的等待者不该继续占名额"

    release_holder.set()
    await h

    assert pool.size == 0
    # 后来者能立刻拿到
    await asyncio.wait_for(_acquire_once(pool, 1), timeout=1)


async def test_idle_entry_is_reclaimed(pool):
    """空闲后条目要摘掉：会话数涨上去时锁对象不能无限堆积。"""
    async with pool.guard(1):
        assert pool.size == 1

    assert pool.size == 0


async def test_release_is_idempotent(pool):
    """重复 release 不能破坏池状态，也不能抛「释放了没持有的锁」。"""
    handle = await pool.acquire(1)

    pool.release(handle)
    pool.release(handle)
    pool.release(handle)

    assert pool.size == 0
    # 池子还是能正常用
    async with pool.guard(1):
        pass


async def test_entry_is_not_reclaimed_while_a_waiter_is_queued(pool):
    """
    等待者也计数 → 持有者退出时条目不能被回收。

    这是最容易做错的一步：等待的人不计数的话，持有者一释放条目就被摘掉，
    第三个请求又新建一把锁，同一个会话上出现两把锁，等于没串行。
    """
    holder_ready = asyncio.Event()
    release_holder = asyncio.Event()
    waiter_got = asyncio.Event()
    release_waiter = asyncio.Event()

    async def holder():
        async with pool.guard(1):
            holder_ready.set()
            await release_holder.wait()

    async def waiter():
        async with pool.guard(1):
            waiter_got.set()
            await release_waiter.wait()

    h = asyncio.create_task(holder())
    await holder_ready.wait()
    w = asyncio.create_task(waiter())
    await _settle()

    # 持有者退出，但等待者还在：条目必须还在，人数归到 1
    release_holder.set()
    await h
    await asyncio.wait_for(waiter_got.wait(), timeout=1)

    assert pool.size == 1, "还有人在用这个会话，条目不能被回收"
    assert pool.users(1) == 1

    release_waiter.set()
    await w

    assert pool.size == 0


def test_module_level_singleton_exists():
    """所有会话接口共用同一份池，同会话的请求才能互相看见。"""
    assert isinstance(session_locks, SessionLockPool)


async def _acquire_once(pool, session_id):
    """拿一次锁就放掉"""
    async with pool.guard(session_id):
        pass
