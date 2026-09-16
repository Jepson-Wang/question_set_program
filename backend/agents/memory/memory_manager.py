"""
三层记忆的统一入口。

归档流程为什么不需要锁：被挤出窗口的条目在 Lua 脚本里已经原子地搬进了 pending 队列，
读路径看不到它，后台归档和窗口读写不会互相干扰。

原文与摘要的分工：
- memory_record 存原文，只增不改。一条几百字节，存得起；出问题能追溯，mysqldump 天然备份
- memory_digest 存会话要点，是从原文重算出来的派生数据。摘坏了、换了提示词，重跑一遍就行，
  不会像「在摘要上继续摘要」那样一轮轮失真
"""
import asyncio
import hashlib
import json
from typing import Any

from backend.agents.agent.session_digest_agent import build_session_digest
from backend.agents.memory.long_term_memory import LongTermMemory
from backend.agents.memory.short_term_memory import ShortTermMemory, MemoryUnit
from backend.core.single_tool import singleMeta
from backend.dao.memory_mapper import MemoryMapper
from backend.middleware.logging import get_logger

logger = get_logger(__name__)

# 攒够这么多条还没进摘要的记录，就重算一次会话摘要。太小则频繁调用大模型，太大则上下文跟不上进度
DIGEST_EVERY = 5
# 重算摘要时最多回看多少条原文：会话再长，单次摘要的输入也有上限
DIGEST_SOURCE_LIMIT = 40


class MemoryManager(metaclass=singleMeta):
    def __init__(self,
                 long_term_memory: LongTermMemory,
                 short_term_memory: ShortTermMemory,
                 memory_mapper: MemoryMapper):
        self.long_term_memory = long_term_memory
        self.short_term_memory = short_term_memory
        self.memory_mapper = memory_mapper
        self._tasks: set[asyncio.Task] = set()

    async def get_memory_for_planner(self, user_id: int, session_id: int) -> dict[str, Any]:
        """规划器需要的三样：最近几轮原文、本会话要点、长期画像"""
        short_memory = await self.short_term_memory.get_latest_memories(user_id, session_id)
        long_memory = await self.long_term_memory.get_by_user_id(user_id)
        digest = await self.memory_mapper.get_digest(user_id, session_id)
        return {
            "short_memory": short_memory,                       # list[dict]
            "long_memory": long_memory,                         # UserProfileResponse | None
            "session_digest": digest.summary if digest else "",  # str
        }

    async def add_memory(self, user_id: int, session_id: int, memory: MemoryUnit) -> None:
        """写入短期记忆；窗口满了就把挤出来的条目交给后台归档，不占用请求路径"""
        evicted = await self.short_term_memory.add_memory(user_id, session_id, memory)
        if evicted:
            self._spawn(self._archive(user_id, session_id, evicted))

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        # 事件循环对任务只持弱引用，自己不存一份的话，任务可能跑到一半被垃圾回收
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task: asyncio.Task) -> None:
        """任务结束（成功、失败或被取消）时调用：释放引用，并把异常记进日志"""
        self._tasks.discard(task)
        # 被取消的任务调用 exception() 会抛 CancelledError，要先排除
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            # 回调里没有「当前异常」，exc_info=True 取不到堆栈，要把异常对象直接传进去
            logger.error("归档任务异常退出: %s", exc, exc_info=exc)

    async def _archive(self, user_id, session_id, raw_items: list[str]) -> int:
        """逐条归档，返回成功的条数；真的写进新记录时，顺带看看要不要重算摘要"""
        success = 0
        for raw in raw_items:
            if await self._archive_one(user_id, session_id, raw):
                success += 1
        if success:
            await self._refresh_digest(user_id, session_id)
        return success

    async def _archive_one(self, user_id, session_id, raw: str) -> bool:
        """把一条原文写进 memory_record，写成功才 ack；失败就留在 pending 等下次重试"""
        try:
            unit = json.loads(raw)
        except json.JSONDecodeError as e:
            # 坏数据重试多少次都不会好，直接从 pending 删掉，否则每次启动都会卡在它上面
            logger.error("待归档记忆无法反序列化，已丢弃: %s | %s", e, raw[:100])
            await self.short_term_memory.ack_archived(user_id, session_id, raw)
            return False

        memory = unit.get("memory", {}) if isinstance(unit, dict) else {}
        # 用原始条目的哈希做唯一键：重试写入同一条时被数据库挡下，不会出现两行
        fingerprint = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        try:
            await self.memory_mapper.add_record(
                user_id, session_id,
                memory.get("user_memory", ""), memory.get("model_memory", ""), fingerprint,
            )
        except Exception as e:
            logger.error("写入记忆表失败，留在 pending 待重试: %s", e, exc_info=True)
            return False

        await self.short_term_memory.ack_archived(user_id, session_id, raw)
        return True

    async def _refresh_digest(self, user_id: int, session_id: int) -> bool:
        """
        攒够 DIGEST_EVERY 条新记录就重算一次会话摘要。
        摘要失败不影响已经落库的原文：下一批归档还会再试一次。
        """
        try:
            digest = await self.memory_mapper.get_digest(user_id, session_id)
            covered = digest.covered_until_id if digest else 0
            pending, _ = await self.memory_mapper.count_since(user_id, session_id, covered)
            if pending < DIGEST_EVERY:
                return False

            records = await self.memory_mapper.list_records(user_id, session_id, DIGEST_SOURCE_LIMIT)
            if not records:
                return False
            summary = await build_session_digest(records)
            if not summary:
                logger.warning("会话摘要为空，本次不更新: user=%s session=%s", user_id, session_id)
                return False

            await self.memory_mapper.save_digest(user_id, session_id, summary, records[-1]["id"])
            logger.info("会话摘要已更新: user=%s session=%s 覆盖到记录 %s", user_id, session_id, records[-1]["id"])
            return True
        except Exception as e:
            logger.error("会话摘要更新失败: %s", e, exc_info=True)
            return False

    async def drain_pending(self) -> int:
        """重启恢复：把已弹出但没归档成功的条目补做掉"""
        keys = await self.short_term_memory.scan_pending_keys()
        total = 0
        for key in keys:
            try:
                user_id, session_id = self.short_term_memory.parse_pending_key(key)
            except (IndexError, ValueError):
                logger.error("无法解析pending key，已跳过: %s", key)
                continue

            texts = await self.short_term_memory.get_pending(user_id, session_id)
            if not texts:
                continue
            logger.info("启动恢复：session %s:%s 有 %s 条待归档", user_id, session_id, len(texts))
            total += await self._archive(user_id, session_id, texts)

        if total:
            logger.info("drain_pending 成功归档: %s 条消息", total)
        return total

    async def shutdown(self, timeout: float = 10.0) -> None:
        tasks = list(self._tasks)
        if not tasks:
            return
        logger.info("shutdown 等待 %s 个归档任务", len(tasks))
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            logger.warning("shutdown 时有 %s 个归档任务超时，已取消，下次启动由 drain_pending 补做", len(pending))
            for task in pending:
                task.cancel()
            # cancel() 只是发出取消请求，要等任务真正退出，否则事件循环关闭时会报 Task was destroyed but it is pending
            await asyncio.gather(*pending, return_exceptions=True)
