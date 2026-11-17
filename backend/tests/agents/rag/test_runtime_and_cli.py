import json

import pytest

from backend.agents.rag.ingest.__main__ import build_parser, expand_paths, run
from backend.agents.rag.ingest.judge import JudgeConfigError
from backend.agents.rag.models import QuestionItem
from backend.agents.rag.runtime import RagRuntime, get_rag_runtime, set_rag_runtime
from backend.tests.agents.rag.helpers import (
    FakeJudge,
    ListQuarantine,
    ScriptedLLM,
    make_test_runtime,
    write_docx,
)

STRUCTURED = json.dumps({
    "questions": [{"stem": "解方程 2x+3=7", "answer": "x=2", "knowledge_points": ["一元一次方程"]}],
    "knowledge": [],
}, ensure_ascii=False)


# ---------- 运行时 ----------

def test_judge_is_built_once_and_cached(rag_settings):
    built = []

    def factory():
        built.append(1)
        return FakeJudge()

    runtime = make_test_runtime(rag_settings)
    runtime._judge_factory = factory
    assert runtime.ensure_judge() is runtime.ensure_judge()
    assert len(built) == 1


def test_misconfigured_judge_blocks_ingest_service(rag_settings):
    runtime = make_test_runtime(rag_settings, judge_error=JudgeConfigError("同一家族"))
    with pytest.raises(JudgeConfigError):
        runtime.ingest_service(ListQuarantine())
    # 人工复核通过不需要裁判，不受影响
    assert runtime.ingest_service(None, require_judge=False) is not None


async def test_warm_up_rebuilds_index_from_store(rag_settings):
    runtime = make_test_runtime(rag_settings)
    item = QuestionItem(stem="解方程 2x+3=7", answer="x=2")
    await runtime.question_store.upsert([item.id], [[1.0]], [item.stem], [item.to_metadata()])
    assert await runtime.warm_up() == 1
    assert runtime.lexical_index.contains(item.id)


def test_runtime_singleton_can_be_injected(rag_settings):
    fake = make_test_runtime(rag_settings)
    set_rag_runtime(fake)
    try:
        assert get_rag_runtime() is fake
    finally:
        set_rag_runtime(None)


def test_production_wiring_uses_llamaindex_chroma(rag_settings):
    pytest.importorskip("chromadb", exc_type=ImportError)
    from backend.agents.rag.store.embedder import LlamaIndexEmbedder
    from backend.agents.rag.store.llama_store import LlamaIndexVectorStore

    runtime = RagRuntime.from_settings(rag_settings)
    assert isinstance(runtime.embedder, LlamaIndexEmbedder)
    assert isinstance(runtime.question_store, LlamaIndexVectorStore)
    assert isinstance(runtime.knowledge_store, LlamaIndexVectorStore)
    assert (rag_settings.db_dir / "chroma.sqlite3").exists()


# ---------- 命令行 ----------

def test_parser():
    args = build_parser().parse_args(["file", "a.pdf", "b.docx", "--grade", "七年级"])
    assert (args.command, [p.name for p in args.paths], args.grade) == ("file", ["a.pdf", "b.docx"], "七年级")
    args = build_parser().parse_args(["generate", "--kp", "一元一次方程", "--grade", "七年级", "-n", "5"])
    assert (args.kp, args.difficulty, args.n) == ("一元一次方程", "中等", 5)


def test_expand_paths_filters_and_sorts(tmp_path):
    for name in ("b.docx", "a.pdf", "notes.txt", "sub/d.docx"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    names = [p.relative_to(tmp_path).as_posix() for p in expand_paths([tmp_path])]
    assert names == ["a.pdf", "b.docx", "sub/d.docx"]


async def test_file_command_ingests_and_prints_summary(rag_settings, tmp_path):
    path = tmp_path / "练习.docx"
    write_docx(path, ["一、解方程 2x+3=7。答案：x=2"])
    runtime = make_test_runtime(rag_settings, llm=ScriptedLLM(STRUCTURED))
    lines = []

    code = await run(build_parser().parse_args(["file", str(path)]), runtime=runtime,
                     quarantine=ListQuarantine(), out=lines.append)
    assert code == 0
    assert "通过 1" in lines[-1] and lines[-1].startswith("合计")


async def test_one_bad_file_does_not_abort_the_batch(rag_settings, tmp_path):
    good, broken = tmp_path / "a.docx", tmp_path / "b.docx"
    write_docx(good, ["一、解方程 2x+3=7。答案：x=2"])
    broken.write_bytes(b"this is not a zip file")
    runtime = make_test_runtime(rag_settings, llm=ScriptedLLM(STRUCTURED))
    lines = []

    await run(build_parser().parse_args(["file", str(tmp_path)]), runtime=runtime,
              quarantine=ListQuarantine(), out=lines.append)
    assert any(line.strip().startswith("失败") for line in lines)
    assert "通过 1" in lines[-1]


async def test_misconfigured_judge_exits_with_clear_message(rag_settings):
    runtime = make_test_runtime(rag_settings, judge_error=JudgeConfigError("裁判与生成模型属于同一家族"))
    lines = []
    code = await run(build_parser().parse_args(["generate", "--kp", "一元一次方程", "--grade", "七年级"]),
                     runtime=runtime, quarantine=ListQuarantine(), out=lines.append)
    assert code == 2
    assert "同一家族" in lines[0]
