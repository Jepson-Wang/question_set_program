import json

from backend.agents.rag.ingest.generator import BATCH_SIZE, SeedGenerator
from backend.tests.agents.rag.helpers import ScriptedLLM


def _batch(count, kps=("一元一次方程",)):
    return json.dumps([
        {"stem": f"第{i}题：解方程 {i}x+1={i + 1}", "answer": "x=1", "knowledge_points": list(kps)}
        for i in range(count)
    ], ensure_ascii=False)


async def test_generated_items_are_marked_as_llm_source():
    items, errors = await SeedGenerator(ScriptedLLM(_batch(3))).generate("一元一次方程", "七年级", "简单", 3)
    assert errors == []
    assert len(items) == 3
    assert {i.source_type for i in items} == {"llm"}
    assert all(i.source_ref.startswith("llm:") for i in items)
    assert len({i.source_ref for i in items}) == 1, "同一次生成的题共享一个批次号"
    assert {(i.grade, i.difficulty) for i in items} == {("七年级", "简单")}


async def test_requested_knowledge_point_is_always_attached():
    items, _ = await SeedGenerator(ScriptedLLM(_batch(1, kps=["移项"]))).generate("一元一次方程", "七年级", "中等", 1)
    assert items[0].knowledge_points == ["一元一次方程", "移项"]


async def test_large_requests_are_split_into_batches():
    llm = ScriptedLLM(_batch(BATCH_SIZE), _batch(5))
    items, _ = await SeedGenerator(llm).generate("一元一次方程", "七年级", "中等", BATCH_SIZE + 5)
    assert len(llm.calls) == 2
    assert len(items) == BATCH_SIZE + 5
    assert "数量：5" in llm.calls[1][1].content


async def test_extra_items_beyond_request_are_dropped():
    items, _ = await SeedGenerator(ScriptedLLM(_batch(5))).generate("一元一次方程", "七年级", "中等", 2)
    assert len(items) == 2


async def test_invalid_output_is_reported():
    llm = ScriptedLLM("我来出几道题：……", json.dumps([{"stem": "缺答案"}], ensure_ascii=False))
    gen = SeedGenerator(llm)
    items, errors = await gen.generate("一元一次方程", "七年级", "中等", 1)
    assert items == [] and "不是合法的 JSON 数组" in errors[0]
    items, errors = await gen.generate("一元一次方程", "七年级", "中等", 1)
    assert items == [] and "字段不完整" in errors[0]


async def test_system_prompt_comes_from_the_skill():
    llm = ScriptedLLM(_batch(1))
    await SeedGenerator(llm).generate("一元一次方程", "七年级", "中等", 1)
    assert "原创题目" in llm.calls[0][0].content


async def test_difficulty_outside_the_enum_falls_back_to_the_requested_one():
    """模型回「较难」这类枚举外写法时，题目难度应保持为请求的难度，而不是被改成「中等」。"""
    raw = json.dumps(
        [{"stem": "第0题：解方程 0x+1=1", "answer": "x=1", "difficulty": "较难",
          "knowledge_points": ["一元一次方程"]}],
        ensure_ascii=False,
    )
    items, _ = await SeedGenerator(ScriptedLLM(raw)).generate("一元一次方程", "七年级", "困难", 1)
    assert items[0].difficulty == "困难"
