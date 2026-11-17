import json

from backend.agents.rag.ingest.loaders import PageText
from backend.agents.rag.ingest.structurer import Structurer, make_windows
from backend.agents.rag.llm_json import parse_json_array, parse_json_object
from backend.agents.skills import get_skill_list_prompt, list_skills, load_skill, match_triggers
from backend.tests.agents.rag.helpers import ScriptedLLM


# ---------- llm_json ----------

# 三个反引号用拼接得到：直接写在字符串里，这段代码被嵌进 Markdown 文档时会截断代码块
FENCE = "`" * 3


def test_parse_json_object_tolerates_fences_and_chatter():
    assert parse_json_object(f'好的：\n{FENCE}json\n{{"a": 1}}\n{FENCE}') == {"a": 1}


def test_parse_json_rejects_wrong_shape_and_garbage():
    assert parse_json_object("[1, 2]") is None
    assert parse_json_array('{"a": 1}') is None
    assert parse_json_object("{不是 json}") is None
    assert parse_json_object(None) is None


# ---------- skill 可见性 ----------

def test_internal_skills_are_hidden_from_the_agent():
    """RAG 的 prompt 不能出现在 ReAct 主提示词的 Skill 清单里，也不能被触发词命中。"""
    names = [m["name"] for m in list_skills()]
    assert "rag_structuring" not in names
    assert "rag_structuring" in [m["name"] for m in list_skills(include_internal=True)]
    assert "rag_structuring" not in get_skill_list_prompt()
    assert "rag_structuring" not in match_triggers("帮我整理一下教材")


def test_internal_skills_are_still_loadable_by_code():
    assert "完整的题目" in load_skill("rag_structuring")


def test_public_skills_are_unaffected():
    assert "question_variant" in [m["name"] for m in list_skills()]


# ---------- 滑动窗口 ----------

def _pages(n):
    return [PageText(i, f"第{i}页内容") for i in range(1, n + 1)]


def test_windows_overlap_by_one_page():
    assert [[p.number for p in w] for w in make_windows(_pages(1))] == [[1]]
    assert [[p.number for p in w] for w in make_windows(_pages(3))] == [[1, 2], [2, 3]]


def test_last_page_is_always_covered():
    assert [[p.number for p in w] for w in make_windows(_pages(4), window=3)] == [[1, 2, 3], [2, 3, 4]]


# ---------- 结构化 ----------

def _output(questions=(), knowledge=()):
    return json.dumps({"questions": list(questions), "knowledge": list(knowledge)}, ensure_ascii=False)


Q1 = {"stem": "解方程 2x+3=7", "answer": "x=2", "knowledge_points": ["一元一次方程"], "difficulty": "简单"}
Q2 = {"stem": "分解因式 x²-9", "answer": "(x+3)(x-3)", "knowledge_points": ["因式分解"], "grade": "八年级"}
K1 = {"text": "含有一个未知数、未知数的次数是 1 的方程叫一元一次方程。", "knowledge_points": ["一元一次方程"]}


async def test_overlapping_windows_are_deduplicated():
    """同一道题出现在两个相邻窗口里，只保留一份。"""
    llm = ScriptedLLM(_output([Q1], [K1]), _output([Q1, Q2], [K1]))
    result = await Structurer(llm).structure(_pages(3), source_ref="练习册.pdf", source_type="pdf",
                                             grade_hint="七年级")
    assert [q.stem for q in result.questions] == [Q1["stem"], Q2["stem"]]
    assert len(result.knowledge) == 1
    assert result.errors == []


async def test_source_fields_and_grade_hint_are_applied():
    llm = ScriptedLLM(_output([Q1, Q2]))
    result = await Structurer(llm).structure(_pages(2), source_ref="练习册.pdf", source_type="pdf",
                                             grade_hint="七年级")
    q1, q2 = result.questions
    assert (q1.source_type, q1.source_ref, q1.grade) == ("pdf", "练习册.pdf#p1", "七年级")
    assert q2.grade == "八年级", "原文给出的年级优先于年级提示"


async def test_incomplete_items_are_skipped_not_fatal():
    broken = {"stem": "只有题干没有答案"}
    llm = ScriptedLLM(_output([broken, Q1], ["不是对象的讲解"]))
    result = await Structurer(llm).structure(_pages(1), source_ref="a.pdf", source_type="pdf")
    assert [q.stem for q in result.questions] == [Q1["stem"]]
    assert len(result.errors) == 2


async def test_bad_llm_output_and_failures_are_recorded_and_skipped():
    llm = ScriptedLLM("抱歉，我无法处理", RuntimeError("超时"), _output([Q2]))
    result = await Structurer(llm).structure(_pages(4), source_ref="a.pdf", source_type="pdf")
    assert [q.stem for q in result.questions] == [Q2["stem"]]
    assert any("不是合法的 JSON" in e for e in result.errors)
    assert any("LLM 调用失败" in e for e in result.errors)


async def test_prompt_uses_the_skill_and_page_markers():
    llm = ScriptedLLM(_output())
    await Structurer(llm).structure(_pages(1), source_ref="a.pdf", source_type="pdf")
    system, human = llm.calls[0]
    assert "完整的题目" in system.content
    assert "【第1页】" in human.content


async def test_no_pages_means_no_llm_call_at_all():
    """没有页面时不该白跑一趟模型：既不调用，也不报错。"""
    llm = ScriptedLLM()
    result = await Structurer(llm).structure([], source_ref="a.pdf", source_type="pdf")
    assert result.questions == [] and result.knowledge == [] and result.errors == []
    assert llm.calls == []
