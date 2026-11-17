"""
入库任务与隔离区的数据访问层：rag_ingest_job、rag_quarantine。

和 chat_mapper 一样只返回普通 dict，不把 ORM 对象带出会话。
"""
import uuid
from datetime import datetime

from sqlalchemy import select, update

from backend.agents.rag.models import JudgeResult, QuestionItem
from backend.model.rag import RagIngestJob, RagQuarantine

JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"

Q_PENDING = "pending"
Q_APPROVED = "approved"
Q_DISCARDED = "discarded"

INTERRUPTED_MESSAGE = "服务重启，任务中断；重新提交即可，已入库的部分会被自动跳过"


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _job_to_dict(job: RagIngestJob) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "source": job.source,
        "params": job.params,
        "report": job.report,
        "error": job.error,
        "created_by": job.created_by,
        "create_time": _iso(job.create_time),
        "update_time": _iso(job.update_time),
    }


def _entry_to_dict(entry: RagQuarantine) -> dict:
    return {
        "id": entry.id,
        "question_id": entry.question_id,
        "item": entry.payload,
        "judge": entry.judge,
        "status": entry.status,
        "job_id": entry.job_id,
        "reviewed_by": entry.reviewed_by,
        "create_time": _iso(entry.create_time),
        "review_time": _iso(entry.review_time),
    }


class RagJobMapper:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def create(self, kind: str, source: str, params: dict | None = None,
                     created_by: int | None = None) -> str:
        job_id = uuid.uuid4().hex
        async with self.session_factory() as session:
            session.add(RagIngestJob(
                id=job_id, kind=kind, status=JOB_PENDING, source=(source or "")[:255],
                params=params, created_by=created_by,
            ))
            await session.commit()
        return job_id

    async def _set(self, job_id: str, **values) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(RagIngestJob)
                .where(RagIngestJob.id == job_id)
                .values(**values, update_time=datetime.now())
            )
            await session.commit()

    async def mark_running(self, job_id: str) -> None:
        await self._set(job_id, status=JOB_RUNNING)

    async def mark_succeeded(self, job_id: str, report: dict) -> None:
        await self._set(job_id, status=JOB_SUCCEEDED, report=report)

    async def mark_failed(self, job_id: str, error: str) -> None:
        await self._set(job_id, status=JOB_FAILED, error=(error or "")[:2000])

    async def get(self, job_id: str) -> dict | None:
        async with self.session_factory() as session:
            job = await session.get(RagIngestJob, job_id)
            return None if job is None else _job_to_dict(job)

    async def fail_interrupted(self) -> int:
        """
        服务启动时调用：上次没跑完的任务（待运行、运行中）一律标记为失败。
        不做断点续传——入库是幂等的，重新提交即可。返回被标记的任务数。
        """
        async with self.session_factory() as session:
            result = await session.execute(
                update(RagIngestJob)
                .where(RagIngestJob.status.in_([JOB_PENDING, JOB_RUNNING]))
                .values(status=JOB_FAILED, error=INTERRUPTED_MESSAGE, update_time=datetime.now())
            )
            await session.commit()
            return result.rowcount or 0


class RagQuarantineMapper:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def put(self, item: QuestionItem, result: JudgeResult, job_id: str | None = None) -> int:
        async with self.session_factory() as session:
            entry = RagQuarantine(
                question_id=item.id,
                payload=item.model_dump(),
                judge=result.to_dict(),
                status=Q_PENDING,
                job_id=job_id,
            )
            session.add(entry)
            await session.commit()
            return entry.id

    async def list(self, status: str = Q_PENDING, limit: int = 20, offset: int = 0) -> list[dict]:
        async with self.session_factory() as session:
            rows = await session.execute(
                select(RagQuarantine)
                .where(RagQuarantine.status == status)
                .order_by(RagQuarantine.id)
                .limit(limit)
                .offset(offset)
            )
            return [_entry_to_dict(e) for e in rows.scalars()]

    async def get(self, entry_id: int) -> dict | None:
        async with self.session_factory() as session:
            entry = await session.get(RagQuarantine, entry_id)
            return None if entry is None else _entry_to_dict(entry)

    async def set_status(self, entry_id: int, status: str, reviewer_id: int | None) -> bool:
        """
        只允许从 pending 变更，且只能改一次。条件更新保证并发下也只有一个人能处理成功。
        返回是否处理成功（条目不存在或已处理过时为 False）。
        """
        async with self.session_factory() as session:
            result = await session.execute(
                update(RagQuarantine)
                .where(RagQuarantine.id == entry_id, RagQuarantine.status == Q_PENDING)
                .values(status=status, reviewed_by=reviewer_id, review_time=datetime.now())
            )
            await session.commit()
            return (result.rowcount or 0) == 1
