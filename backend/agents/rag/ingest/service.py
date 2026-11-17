"""
入库服务：命令行与后台接口共用的唯一入口。

题目流水线（顺序有讲究）：
    精确去重 → 向量化 → 近似去重 → 第三方复核 → 写入向量库 → 重建词法索引
- 两级去重都在复核之前：重复的题不值得花一次裁判调用
- 复核不通过的题进隔离区，等人工处理，不直接丢弃
教材讲解不经复核：来自权威原文，只做精确去重。
"""
import asyncio
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from backend.agents.rag.config import RagSettings
from backend.agents.rag.ingest.dedup import drop_exact_duplicates, drop_near_duplicates
from backend.agents.rag.ingest.judge import JudgeConfigError
from backend.agents.rag.ingest.loaders import load_document
from backend.agents.rag.models import JudgeResult, KnowledgeChunk, QuestionItem
from backend.core.executors import get_vector_executor
from backend.middleware.logging import get_logger

logger = get_logger(__name__)

QuarantineFn = Callable[[QuestionItem, JudgeResult], Awaitable[object]]


@dataclass
class IngestReport:
    extracted: int = 0
    exact_duplicates: int = 0
    near_duplicates: int = 0
    passed: int = 0
    rejected: int = 0
    knowledge_added: int = 0
    knowledge_duplicates: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "IngestReport") -> None:
        for f in fields(self):
            if f.name == "errors":
                self.errors.extend(other.errors)
            else:
                setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))

    def to_dict(self) -> dict:
        return asdict(self)


class IngestService:
    def __init__(self, *, settings: RagSettings, embedder, question_store, knowledge_store,
                 lexical_index, structurer=None, generator=None, judge=None,
                 quarantine: QuarantineFn | None = None):
        self._settings = settings
        self._embedder = embedder
        self._questions = question_store
        self._knowledge = knowledge_store
        self._index = lexical_index
        self._structurer = structurer
        self._generator = generator
        self._judge = judge
        self._quarantine = quarantine

    # ---------- 入口 ----------

    async def ingest_file(self, path: Path, grade_hint: str | None = None,
                          source_name: str | None = None) -> IngestReport:
        """
        source_name 是记录在题目来源里的文件名。后台上传的文件落盘时改用「任务 id + 后缀」命名，
        这里要传入用户上传时的原始文件名，否则来源就成了一串 id，无法追溯。
        """
        loop = asyncio.get_running_loop()
        # PDF 解析是 CPU 密集的同步调用，放进专属线程池
        pages = await loop.run_in_executor(get_vector_executor(), load_document, path)
        structured = await self._structurer.structure(
            pages,
            source_ref=source_name or path.name,
            source_type=path.suffix.lower().lstrip("."),
            grade_hint=grade_hint,
        )
        report = IngestReport(errors=list(structured.errors))
        report.merge(await self.ingest_questions(structured.questions))
        report.merge(await self.ingest_knowledge(structured.knowledge))
        return report

    async def generate_and_ingest(self, knowledge_point: str, grade: str, difficulty: str,
                                  n: int) -> IngestReport:
        items, errors = await self._generator.generate(knowledge_point, grade, difficulty, n)
        report = IngestReport(errors=list(errors))
        report.merge(await self.ingest_questions(items))
        return report

    async def approve(self, item: QuestionItem) -> None:
        """人工复核通过：跳过裁判，直接入库。"""
        [vector] = await self._embedder.embed([item.stem])
        await self._questions.upsert([item.id], [vector], [item.stem], [item.to_metadata()])
        await self._index.rebuild_from_store(self._questions)

    # ---------- 流水线 ----------

    async def ingest_questions(self, items: Sequence[QuestionItem]) -> IngestReport:
        # 没有闸门就放行等于没有闸门：裁判与隔离区缺一不可
        if self._judge is None or self._quarantine is None:
            raise JudgeConfigError("入库服务未配置裁判或隔离区，不能写入未经复核的题目")

        report = IngestReport(extracted=len(items))
        unique, report.exact_duplicates = drop_exact_duplicates(items, self._index.contains)
        if not unique:
            return report

        vectors = await self._embedder.embed([i.stem for i in unique])
        unique, vectors, report.near_duplicates = await drop_near_duplicates(
            unique, vectors, self._questions, self._settings.near_dup_threshold
        )
        results = await self._judge_all(unique)

        accepted: list[QuestionItem] = []
        accepted_vectors: list[list[float]] = []
        for item, vector, result in zip(unique, vectors, results):
            if result.passed:
                accepted.append(item)
                accepted_vectors.append(vector)
                continue
            report.rejected += 1
            try:
                await self._quarantine(item, result)
            except Exception as e:  # 隔离区写失败不应中断整批入库
                logger.error("写入隔离区失败 %s：%s", item.id, e, exc_info=True)
                report.errors.append(f"写入隔离区失败：{item.id}（{e}）")

        if accepted:
            await self._questions.upsert(
                [i.id for i in accepted], accepted_vectors,
                [i.stem for i in accepted], [i.to_metadata() for i in accepted],
            )
            report.passed = len(accepted)
            await self._index.rebuild_from_store(self._questions)
        return report

    async def ingest_knowledge(self, chunks: Sequence[KnowledgeChunk]) -> IngestReport:
        # 教材讲解来自权威原文，不经裁判：只按 id 去掉重复的（含库中已有的）
        report = IngestReport()
        existing = {row_id for row_id, _, _ in await self._knowledge.get_all()}
        seen: set[str] = set()
        fresh: list[KnowledgeChunk] = []
        for chunk in chunks:
            if chunk.id in existing or chunk.id in seen:
                report.knowledge_duplicates += 1
                continue
            seen.add(chunk.id)
            fresh.append(chunk)
        if fresh:
            vectors = await self._embedder.embed([c.text for c in fresh])
            await self._knowledge.upsert(
                [c.id for c in fresh], vectors, [c.text for c in fresh], [c.to_metadata() for c in fresh]
            )
            report.knowledge_added = len(fresh)
        return report

    async def _judge_all(self, items: Sequence[QuestionItem]) -> list[JudgeResult]:
        # 裁判调用按配置并发限流：模型接口有速率限制，打满并发只会换来限流报错
        semaphore = asyncio.Semaphore(self._settings.judge_concurrency)

        async def judge_one(item: QuestionItem) -> JudgeResult:
            async with semaphore:
                return await self._judge.judge(item)

        # judge() 内部失败即拒、不抛异常，所以这里不需要 return_exceptions
        return list(await asyncio.gather(*(judge_one(i) for i in items)))
