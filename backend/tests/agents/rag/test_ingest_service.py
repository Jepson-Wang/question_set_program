import json

import pytest

from backend.agents.rag.ingest.judge import JudgeConfigError
from backend.agents.rag.ingest.service import IngestReport, IngestService
from backend.agents.rag.models import KnowledgeChunk, QuestionItem
from backend.agents.rag.store.lexical_index import LexicalIndex
from backend.agents.rag.store.vector_store import InMemoryVectorStore
from backend.tests.agents.rag.helpers import (
    FakeJudge,
    HashingEmbedder,
    ListQuarantine,
    ScriptedLLM,
    make_ingest_service,
    write_docx,
)


def _q(stem, kps=("一元一次方程",)):
    return QuestionItem(stem=stem, answer="略", knowledge_points=list(kps), grade="七年级")


GOOD = [_q("解方程 2x+3=7"), _q("解方程 5x-1=9"), _q("分解因式 x²-9", ["因式分解"])]


async def test_passed_questions_are_stored_and_indexed(rag_settings):
    service, parts = make_ingest_service(rag_settings, judge=FakeJudge(reject_stems={"解方程 5x-1=9"}))
    report = await service.ingest_questions(GOOD)

    assert (report.extracted, report.passed, report.rejected) == (3, 2, 1)
    assert await parts.questions.count() == 2
    assert parts.index.contains(GOOD[0].id)
    assert not parts.index.contains(GOOD[1].id)
    [(item, result)] = parts.quarantine.entries
    assert item.stem == "解方程 5x-1=9" and not result.passed


async def test_reingesting_is_idempotent_and_skips_the_judge(rag_settings):
    """同一批题第二次入库：全部判为精确重复，一次裁判都不调用。"""
    judge = FakeJudge()
    service, _ = make_ingest_service(rag_settings, judge=judge)
    await service.ingest_questions(GOOD)
    calls_after_first_run = judge.calls

    report = await service.ingest_questions(GOOD)
    assert report.exact_duplicates == 3 and report.passed == 0
    assert judge.calls == calls_after_first_run


async def test_duplicates_inside_one_batch_are_dropped_before_judging(rag_settings):
    """同一批里题干相同的题只留一道，也不额外花一次裁判调用。"""
    judge = FakeJudge()
    service, parts = make_ingest_service(rag_settings, judge=judge)
    report = await service.ingest_questions([_q("解方程 2x+3=7"), _q("解方程 2x+3=7")])

    assert (report.extracted, report.exact_duplicates, report.passed) == (2, 1, 1)
    assert judge.calls == 1
    assert await parts.questions.count() == 1


async def test_near_duplicates_are_dropped_before_judging(rag_settings):
    stem = "已知长方形的长是宽的2倍，周长是36厘米，求长和宽各是多少"
    judge = FakeJudge()
    service, _ = make_ingest_service(rag_settings, judge=judge)
    report = await service.ingest_questions([_q(stem), _q(stem + "？")])
    assert report.near_duplicates == 1 and report.passed == 1
    assert judge.calls == 1, "近似重复的题不应再花一次裁判调用"


async def test_judge_concurrency_is_bounded(rag_settings):
    judge = FakeJudge(delay=0.01)
    service, _ = make_ingest_service(rag_settings, judge=judge)
    await service.ingest_questions([_q(f"第{i}题：求 {i} 的相反数") for i in range(8)])
    assert judge.calls == 8
    assert judge.max_concurrency <= rag_settings.judge_concurrency


async def test_quarantine_failure_is_reported_not_fatal(rag_settings):
    service, _ = make_ingest_service(
        rag_settings, judge=FakeJudge(reject_stems={GOOD[1].stem}), quarantine=ListQuarantine(fail=True)
    )
    report = await service.ingest_questions(GOOD)
    assert report.passed == 2 and report.rejected == 1
    assert any("写入隔离区失败" in e for e in report.errors)


async def test_service_without_judge_refuses_to_ingest(rag_settings):
    service = IngestService(settings=rag_settings, embedder=None, question_store=None,
                            knowledge_store=None, lexical_index=None)
    with pytest.raises(JudgeConfigError):
        await service.ingest_questions(GOOD)


async def test_approve_skips_the_judge(rag_settings):
    judge = FakeJudge()
    service, parts = make_ingest_service(rag_settings, judge=judge)
    await service.approve(GOOD[0])
    assert parts.index.contains(GOOD[0].id)
    assert judge.calls == 0


async def test_knowledge_chunks_are_deduplicated(rag_settings):
    service, parts = make_ingest_service(rag_settings)
    chunk = KnowledgeChunk(text="移项要变号。", knowledge_points=["一元一次方程"])

    first = await service.ingest_knowledge([chunk, chunk])
    second = await service.ingest_knowledge([chunk])
    assert (first.knowledge_added, first.knowledge_duplicates) == (1, 1)
    assert (second.knowledge_added, second.knowledge_duplicates) == (0, 1)
    assert await parts.knowledge.count() == 1


async def test_knowledge_ingest_needs_no_judge(rag_settings):
    """教材讲解来自权威原文，不经裁判：没有裁判也能入库。"""
    service = IngestService(settings=rag_settings, embedder=HashingEmbedder(),
                            question_store=InMemoryVectorStore(), knowledge_store=InMemoryVectorStore(),
                            lexical_index=LexicalIndex())
    report = await service.ingest_knowledge([KnowledgeChunk(text="移项要变号。")])
    assert (report.knowledge_added, report.rejected) == (1, 0)


async def test_ingest_file_end_to_end(rag_settings, tmp_path):
    path = tmp_path / "练习.docx"
    write_docx(path, ["一、解方程 2x+3=7。答案：x=2", "一元一次方程：只含一个未知数且次数为 1 的方程。"])
    llm_output = json.dumps({
        "questions": [{"stem": "解方程 2x+3=7", "answer": "x=2", "knowledge_points": ["一元一次方程"]}],
        "knowledge": [{"text": "一元一次方程：只含一个未知数且次数为 1 的方程。", "knowledge_points": ["一元一次方程"]}],
    }, ensure_ascii=False)
    service, parts = make_ingest_service(rag_settings, structurer_llm=ScriptedLLM(llm_output))

    report = await service.ingest_file(path, grade_hint="七年级")
    assert (report.extracted, report.passed, report.knowledge_added) == (1, 1, 1)
    stored = parts.index.get(QuestionItem(stem="解方程 2x+3=7", answer="x").id)
    assert (stored.source_type, stored.source_ref, stored.grade) == ("docx", "练习.docx#p1", "七年级")


async def test_source_name_overrides_the_stored_file_name(rag_settings, tmp_path):
    """后台上传的文件落盘名是任务 id，来源里要记原始文件名。"""
    path = tmp_path / "3f2a9c.docx"
    write_docx(path, ["一、解方程 2x+3=7。答案：x=2"])
    llm_output = json.dumps({"questions": [{"stem": "解方程 2x+3=7", "answer": "x=2"}], "knowledge": []})
    service, parts = make_ingest_service(rag_settings, structurer_llm=ScriptedLLM(llm_output))

    await service.ingest_file(path, source_name="七年级期中卷.docx")
    stored = parts.index.get(QuestionItem(stem="解方程 2x+3=7", answer="x").id)
    assert stored.source_ref == "七年级期中卷.docx#p1"


async def test_generate_and_ingest(rag_settings):
    output = json.dumps([{"stem": "解方程 3x=12", "answer": "x=4"},
                         {"stem": "解方程 x-5=2", "answer": "x=7"}], ensure_ascii=False)
    service, parts = make_ingest_service(rag_settings, generator_llm=ScriptedLLM(output))
    report = await service.generate_and_ingest("一元一次方程", "七年级", "简单", 2)

    assert report.passed == 2
    for stem in ("解方程 3x=12", "解方程 x-5=2"):
        assert parts.index.get(QuestionItem(stem=stem, answer="x").id).source_type == "llm"


def test_report_merge():
    report = IngestReport(extracted=2, passed=1, errors=["x"])
    report.merge(IngestReport(extracted=3, rejected=1, errors=["y"]))
    assert (report.extracted, report.passed, report.rejected, report.errors) == (5, 1, 1, ["x", "y"])


def test_report_to_dict_exposes_every_counter():
    data = IngestReport(extracted=1, passed=1).to_dict()
    assert set(data) == {"extracted", "exact_duplicates", "near_duplicates", "passed", "rejected",
                         "knowledge_added", "knowledge_duplicates", "errors"}
    assert data["extracted"] == 1 and data["passed"] == 1
