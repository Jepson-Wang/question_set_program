"""
画像保存工具「先落库、后清候选」的契约测试。

候选池和落库都换成假的：这里只测工具自己的编排逻辑——什么时候清候选、
清哪些、失败时怎么汇报。候选池本身的行为归 test_profile_candidates.py，
落库本身归 tests/dao/ 和 test_long_term_memory.py。
"""
import pytest
from sqlalchemy import exc as sa_exc

from backend.agents.tools import user_profile_save_tool as tool_module
from backend.agents.tools.user_profile_save_tool import UserProfileSaveTool

USER = 7


class FakeCandidateStore:
    """按预设决定每个子键是否晋升，并记下提交和删除了什么"""
    times_to_submit = 2

    def __init__(self, promote: set[str]):
        self.promote = promote
        self.submitted: list[str] = []
        self.deleted: list[tuple[str, str, str]] = []

    async def submit_one_candidate(self, user_id, field, sub_key, value):
        self.submitted.append(sub_key)
        if sub_key in self.promote:
            return 2, True
        return 1, False

    async def delete_field_by_given(self, user_id, to_delete):
        self.deleted.extend(to_delete)


class FakeLongTermMemory:
    """记下写了什么；error 不为空时模拟落库失败"""
    error: Exception | None = None
    written: list = []

    def __init__(self, mapper, stm):
        pass

    async def add_or_update(self, request):
        if FakeLongTermMemory.error is not None:
            raise FakeLongTermMemory.error
        FakeLongTermMemory.written.append(request)


@pytest.fixture
def patched(monkeypatch):
    """换掉候选池、长期记忆和两个依赖工厂，返回一个可以调整行为的候选池"""
    async def fake_factory():
        return None

    store = FakeCandidateStore(promote=set())
    FakeLongTermMemory.error = None
    FakeLongTermMemory.written = []
    monkeypatch.setattr(tool_module, "_candidate_store", store)
    monkeypatch.setattr(tool_module, "LongTermMemory", FakeLongTermMemory)
    monkeypatch.setattr(tool_module, "get_user_profile_mapper", fake_factory)
    monkeypatch.setattr(tool_module, "get_short_term_memory", fake_factory)
    # 空词表 = 不做限制。这个文件里的编排测试和词表内容无关，不能跟着 SKILL.md 的改动变红
    monkeypatch.setattr(tool_module, "load_vocab", lambda name: {})
    return store


# 分流测试用固定词表，不读真实 SKILL.md
TEST_VOCAB = {
    "weak_points": {"因式分解": ["薄弱", "一般", "已掌握"]},
    "preferences": {"题目风格": ["偏纯计算", "不要与原题雷同"]},
}


@pytest.fixture
def with_vocab(patched, monkeypatch):
    monkeypatch.setattr(tool_module, "load_vocab", lambda name: TEST_VOCAB)
    return patched


async def _save(**kwargs) -> str:
    return await UserProfileSaveTool()._arun(user_id=USER, **kwargs)


async def test_success_deletes_only_promoted_sub_keys(patched):
    """落库成功后只清晋升的子键；还在计数的候选不在 to_write 里，删了它计数就归零了"""
    patched.promote = {"题目风格"}
    result = await _save(preferences={"题目风格": "简单", "讲解详细度": "详细"})

    assert patched.deleted == [("preferences", "题目风格", "简单")]
    assert "已写入长期画像" in result


async def test_confirm_delete_carries_the_promoted_value(patched):
    """
    确认删除要带上晋升的那个值（条件删除的依据），而且必须是各自提交的值，
    不能串：带错了值，条件删除会把该删的候选留下
    """
    patched.promote = {"题目风格", "讲解详细度"}
    await _save(preferences={"题目风格": "简单", "讲解详细度": "详细"})

    assert sorted(patched.deleted) == sorted([
        ("preferences", "题目风格", "简单"),
        ("preferences", "讲解详细度", "详细"),
    ])


async def test_transient_failure_keeps_candidates(patched):
    """落库抛了异常，一个候选都不能删：它们是下次重新晋升的唯一凭据"""
    patched.promote = {"题目风格"}
    FakeLongTermMemory.error = sa_exc.OperationalError(
        "UPDATE user_profile SET preferences=%s", {}, Exception("连接断开")
    )
    result = await _save(grade="八年级", preferences={"题目风格": "简单"})

    assert patched.deleted == []
    assert "已写入长期画像" not in result
    assert "候选已保留" in result
    assert "grade 本次没有保存" in result
    assert "UPDATE" not in result, "SQL 文本不能回显给模型"


async def test_data_error_keeps_candidates_and_discourages_retry(patched):
    """DataError 重试也会失败，消息不能引导模型原样重新调用"""
    patched.promote = {"题目风格"}
    FakeLongTermMemory.error = sa_exc.DataError("INSERT ...", {}, Exception("Data too long"))
    result = await _save(preferences={"题目风格": "简单"})

    assert patched.deleted == []
    assert "不要原样重新调用" in result


async def test_direct_fields_only_does_not_call_hdel(patched):
    """只写了直写字段时没有候选可清，不能发出一条空的 HDEL"""
    result = await _save(grade="八年级")

    assert len(FakeLongTermMemory.written) == 1
    assert patched.deleted == []
    assert "已写入长期画像：grade" in result


async def test_too_long_grade_is_rejected_before_counting(patched):
    """
    超长字段要在进候选池之前拦下：拦晚了偏好已经计过一次数，
    模型修正后重新调用又计一次，用户只说了一次的偏好就被凑够了阈值
    """
    result = await _save(grade="八" * 33, preferences={"题目风格": "简单"})

    assert patched.submitted == []
    assert FakeLongTermMemory.written == []
    assert "grade" in result and "参数不合法" in result


async def test_wrong_type_is_reported_with_its_own_reason(patched):
    """类型错了要说类型错了，不能套用「超长」的提示，否则模型会去改错地方"""
    result = await _save(weak_points="函数薄弱")

    assert patched.submitted == []
    assert "weak_points" in result
    assert "32" not in result


async def test_unexpected_error_does_not_echo_exception_text(patched):
    patched.promote = {"题目风格"}
    FakeLongTermMemory.error = RuntimeError("内部细节 secret_column")
    result = await _save(preferences={"题目风格": "简单"})

    assert patched.deleted == []
    assert "secret_column" not in result
    assert "保存失败" in result


# ---------- 按词表分流 ----------

async def test_in_vocab_pair_goes_through_candidate_pool(with_vocab):
    """键和值都在词表里，照常进候选池计数"""
    await _save(preferences={"题目风格": "偏纯计算"})

    assert with_vocab.submitted == ["题目风格"]
    assert FakeLongTermMemory.written == [], "只说了一次，不该落库"


async def test_unknown_key_goes_to_notes_without_counting(with_vocab):
    """
    词表外的键不能进候选池：进去也永远累积不到阈值，偏好静默失效。
    也不能丢掉，所以原话转存 notes
    """
    result = await _save(preferences={"做题时段": "晚上"})

    assert with_vocab.submitted == []
    [request] = FakeLongTermMemory.written
    assert request.notes == ["做题时段：晚上"]
    assert "做题时段：晚上" in result, "要告诉模型这条去了 notes，否则它会以为进了候选池"


async def test_value_outside_domain_goes_to_notes_without_counting(with_vocab):
    """
    键对、值不在值域里，同样转存 notes。放进候选池的话，「简单点」和「偏纯计算」
    会被当成两个主张，计数互相重置（Task 9.5 的 B 语义）
    """
    await _save(weak_points={"因式分解": "很差"})

    assert with_vocab.submitted == []
    [request] = FakeLongTermMemory.written
    assert request.notes == ["因式分解：很差"]


async def test_overflow_is_appended_after_model_notes(with_vocab):
    """模型自己写的 notes 和转存的内容都要保留，不能互相覆盖"""
    await _save(notes=["喜欢用画图理解题意"], preferences={"做题时段": "晚上"})

    [request] = FakeLongTermMemory.written
    assert request.notes == ["喜欢用画图理解题意", "做题时段：晚上"]


async def test_mixed_payload_routes_each_sub_key_independently(with_vocab):
    """同一个字段里合规的照常计数，不合规的转存，互不影响"""
    await _save(preferences={"题目风格": "偏纯计算", "做题时段": "晚上"})

    assert with_vocab.submitted == ["题目风格"]
    [request] = FakeLongTermMemory.written
    assert request.notes == ["做题时段：晚上"]
    assert request.preferences is None, "题目风格只计了一次数，不该写进 preferences"
