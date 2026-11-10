"""
profile_schema Skill 的加载链路：被发现 → 正文能读 → 触发词能命中。

测的是「SKILL.md 写得对、loader 读得出来」，不测 loader 自身的解析细节。
这条链路任何一环断了都不会报错：list_skills 会吞掉解析异常，Skill 只是悄悄从
ReAct 主提示词的清单里消失，模型照样自由起键名，频次确认永远累积不到阈值。
"""
import pytest

from backend.agents.skills import (
    list_skills,
    load_skill,
    load_skill_code,
    match_triggers,
    skill_runner,
)

SKILL = "profile_schema"


def _discovered() -> dict:
    return {meta["name"]: meta for meta in list_skills()}


# ---------- 1. 能被发现、元数据能读 ----------

def test_skill_is_discovered_by_directory_scan():
    """ReAct 主提示词的 Skill 清单来自 list_skills 扫目录，扫不到就等于不存在"""
    assert SKILL in _discovered()


def test_meta_is_complete():
    meta = _discovered()[SKILL]
    assert meta["description"], "描述会进 ReAct 主提示词，空的话模型不知道何时该加载它"
    assert meta["triggers"], "没有触发词，match_triggers 的字面兜底就失效了"


# ---------- 2. 正文能被正确读取 ----------

def test_body_excludes_frontmatter():
    """
    frontmatter 少了闭合的 ---，解析器会把整个文件当正文返回，
    元数据全空，frontmatter 原样进了模型上下文
    """
    body = load_skill(SKILL)
    assert not body.startswith("---")
    assert "triggers:" not in body


def test_body_names_every_target_field():
    """模型要从正文知道往哪几个字段写，以及找不到合适的键时的去处（notes）"""
    body = load_skill(SKILL)
    for field in ("weak_points", "preferences", "notes"):
        assert field in body, f"正文里没有提到 {field}"


def test_vocab_block_is_extractable():
    """load_vocab 建在 load_skill_code 之上：tag 写错或代码块没闭合，这里就拿到 None"""
    code = load_skill_code(SKILL, "vocab")
    assert code is not None, "找不到 ```skill:vocab 代码块"
    assert "VOCAB" in code


# ---------- 3. 触发词能命中 ----------

@pytest.mark.parametrize("text", [
    "以后题目不要跟原题太像",          # 长期偏好
    "每次都给我讲详细一点",            # 讲解方式
    "我总是在因式分解上出错",          # 薄弱点
])
def test_long_term_statements_trigger_skill(text):
    assert SKILL in match_triggers(text)


@pytest.mark.parametrize("text", [
    "这题再简单点",                    # 一次性诉求，SKILL.md 规则明确说不写画像
    "解方程 2x+3=7",                   # 普通做题请求
])
def test_ordinary_requests_do_not_trigger_skill(text):
    # 先确认 Skill 存在：否则 Skill 被改名或删掉时，这条断言会永远成立，什么也证明不了
    assert SKILL in _discovered()
    assert SKILL not in match_triggers(text)


# ---------- 4. 机器可读词表 ----------

def test_vocab_covers_both_candidate_fields():
    """只有走候选池的两个字段需要词表；grade / subject 是事实，notes 是自由文本"""
    assert set(skill_runner.load_vocab(SKILL)) == {"weak_points", "preferences"}


def test_every_key_has_an_enumerated_value_domain():
    """
    值也必须是枚举。Task 9.5 的 B 语义用字符串相等判断「是不是同一个主张」，
    值域一旦放开，「简单点」「简单一些」就是两个主张，计数会被反复重置
    """
    for field, keys in skill_runner.load_vocab(SKILL).items():
        assert keys, f"{field} 一个键都没有"
        for key, values in keys.items():
            assert values, f"{field}.{key} 的值域为空"
            assert all(isinstance(v, str) and v for v in values), f"{field}.{key} 的值必须是非空字符串"
            assert len(set(values)) == len(values), f"{field}.{key} 的值域有重复"


def test_skill_without_vocab_returns_empty_and_warns(caplog):
    """
    空词表会被调用方当成「不做限制」——这是有意的降级，但不能悄无声息，
    否则词表写坏了没人知道
    """
    assert skill_runner.load_vocab("question_variant") == {}
    assert "question_variant" in caplog.text
