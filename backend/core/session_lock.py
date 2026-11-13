"""
同一会话的请求串行化。

为什么要它：同一个 session 的两个请求并发跑时，两边会交叉着写库——
user1 的提问可能配上 user2 的回答，问答直接串味；两次请求各自读到的
上下文快照也会互相踩（先落库的那个成了后一个请求读到的「历史」）。

锁保证的是「同一会话的请求不并发执行、问答配对不串味」。
它不保证「落库顺序 = 提问顺序」：两条并发提问谁先拿到锁，
取决于谁先走到加锁那一步，这个先后本来就无从定义。

锁的粒度是 session，不是进程：不同会话之间必须还能并行，
做成一把全局锁等于把整个后端变成单线程。
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass


@dataclass
class SessionLockHandle:
    """
    一次加锁的凭证。

    为什么不直接把 asyncio.Lock 返回给调用方：release 得能分清
    「这次 acquire 对应的那一次 release」。流式路径有两条释放路径
    （生成器 finally + BackgroundTask 兜底），谁先谁后都不确定，
    重复释放必须没有副作用。
    """

    session_id: int
    entry: "_LockEntry"
    released: bool = False


@dataclass
class _LockEntry:
    """池里一个会话的条目。"""

    session_id: int
    lock: asyncio.Lock
    # 占用这个会话锁的人数 = 已持有 + 正在等待。
    #
    # 等待的人必须算数：否则持有者一退出，条目就被回收，第三个请求又新建一把锁，
    # 同一个会话出现两把锁，等于没串行。
    users: int = 0


class SessionLockPool:
    """按 session_id 串行化请求的锁池。"""

    def __init__(self) -> None:
        self._entries: dict[int, _LockEntry] = {}

    async def acquire(self, session_id: int) -> SessionLockHandle:
        """
        等到拿到这个会话的锁为止。

        排队而不是拒绝：同一会话的并发提问是正常用法，用户的第二个问题不该被丢掉。
        先到先得靠 asyncio.Lock 的等待队列保证——它内部是 FIFO。
        """
        entry = self._entries.get(session_id)
        if entry is None:
            entry = _LockEntry(session_id=session_id, lock=asyncio.Lock())
            self._entries[session_id] = entry

        # 计数必须在 await 之前加。这段代码里没有 await，所以「拿到条目」和
        # 「登记自己是使用者」之间不会被别的协程插进来，计数不会漏。
        entry.users += 1
        try:
            await entry.lock.acquire()
        except BaseException:
            # 等锁期间被取消（客户端断开）：自己没拿到锁，就不该继续占着池子里的名额，
            # 否则这个会话的条目永远回收不掉，后来的请求全被堵死。
            # asyncio.Lock 自己在取消时会把等待者从队列里摘掉，这里只管我们的计数。
            self._drop_user(entry)
            raise
        return SessionLockHandle(session_id=session_id, entry=entry)

    def release(self, handle: SessionLockHandle) -> None:
        """
        归还锁。幂等：重复调用没有副作用。

        为什么要幂等：流式路径有两条释放路径（生成器 finally、响应 __call__ 的 finally），
        重复 release 一次 asyncio.Lock 会抛 "release unlocked lock"，
        把一个本来正常的请求变成 500。

        必须在事件循环线程里调用：这里既要改 users 计数，又要唤醒等锁的协程。
        在工作线程里做，唤醒送不到阻塞中的事件循环（debug 模式下还会直接抛
        "Non-thread-safe operation invoked on an event loop other than the current one"），
        计数也会和事件循环线程的 users += 1 打架，丢更新后同一个会话会出现两把锁。
        因此下面 released 的「读—改」之间没有 await，在事件循环里是原子的。
        """
        if handle.released:
            return
        handle.released = True
        entry = handle.entry
        self._drop_user(entry)
        # 计数在归还锁之前减：这时如果还有别人在等，条目不会被回收；
        # 真的没有别人了，条目摘掉也不影响——锁马上就空了。
        entry.lock.release()

    def _drop_user(self, entry: _LockEntry) -> None:
        """少一个使用者；最后一个走了就把条目从池子里摘掉。"""
        if entry.users > 0:
            entry.users -= 1
        if entry.users > 0:
            return
        # 空闲即回收：会话数涨上去时锁对象不能无限堆积。
        #
        # 只摘「池子里还是这一个」的：别人可能已经因为人数归零又建了新条目，
        # 摘错了就等于把新会话的锁弄丢，同一个会话上会同时存在两把锁。
        if self._entries.get(entry.session_id) is entry:
            del self._entries[entry.session_id]

    @asynccontextmanager
    async def guard(self, session_id: int):
        """把一段代码包在这个会话的锁里。块内抛异常也会归还锁。"""
        handle = await self.acquire(session_id)
        try:
            yield handle
        finally:
            self.release(handle)

    @property
    def size(self) -> int:
        """池里当前有多少个会话条目（排障与测试用）。"""
        return len(self._entries)

    def users(self, session_id: int) -> int:
        """这个会话上占用锁的人数 = 已持有 + 正在等待（排障与测试用）。"""
        entry = self._entries.get(session_id)
        return entry.users if entry else 0


# 进程内单例：所有会话接口共用这一份池，同会话的请求才能互相看见。
session_locks = SessionLockPool()
