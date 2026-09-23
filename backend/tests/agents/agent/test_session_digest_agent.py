import backend.agents.agent.session_digest_agent as sd

RECORDS = [
    {"id": 1, "user_text": "解方程 2x+9=5x-3", "model_text": "移项得 3x=12，x=4"},
    {"id": 2, "user_text": "我老是把移项的符号弄错", "model_text": "移项要变号，建议写出中间结果"},
]


class FakeLLM:
    def __init__(self, content="学生练习一元一次方程，容易在移项时弄错符号。"):
        self.content = content
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return type("Response", (), {"content": self.content})()


def _patch(monkeypatch, llm):
    monkeypatch.setattr(sd, "build_session_digest_agent", lambda: llm)
    monkeypatch.setattr(sd, "load_skill", lambda name: f"【{name} 剧本】")
    return llm


def test_records_are_formatted_in_order():
    text = sd.format_records(RECORDS)
    assert text.index("解方程") < text.index("移项的符号"), "必须按时序排列"
    assert "1. 用户：" in text and "   助手：" in text


async def test_digest_uses_the_skill_and_returns_text(monkeypatch):
    llm = _patch(monkeypatch, FakeLLM())
    result = await sd.build_session_digest(RECORDS)
    assert result == "学生练习一元一次方程，容易在移项时弄错符号。"
    assert llm.messages[0].content == "【session_digest 剧本】", "system 必须来自 SKILL.md，提示词不写在代码里"
    assert "解方程 2x+9=5x-3" in llm.messages[1].content


async def test_empty_records_make_no_call(monkeypatch):
    class Exploding:
        async def ainvoke(self, messages):
            raise AssertionError("没有原文时不应该调用模型")
    _patch(monkeypatch, Exploding())
    assert await sd.build_session_digest([]) == ""


async def test_whitespace_and_non_text_are_handled(monkeypatch):
    _patch(monkeypatch, FakeLLM(content="  要点  \n"))
    assert await sd.build_session_digest(RECORDS) == "要点"

    _patch(monkeypatch, FakeLLM(content=[{"type": "text"}]))
    assert await sd.build_session_digest(RECORDS) == "", "模型返回非文本时给空摘要，而不是让整条链路报错"


def test_long_records_are_truncated():
    text = sd.format_records([{"id": 1, "user_text": "问" * 500, "model_text": "答" * 500}])
    assert "问" * sd.RECORD_USER_MAX_CHARS + "…" in text
    assert "答" * sd.RECORD_MODEL_MAX_CHARS + "…" in text


def test_oldest_records_are_dropped_when_input_is_too_long():
    """整体超长时丢最早的，保住最近的：最近发生的事对后续对话更有用"""
    many = [{"id": i, "user_text": f"第{i}问" + "x" * 190, "model_text": "y" * 290} for i in range(60)]
    text = sd.format_records(many)
    assert len(text) <= sd.DIGEST_INPUT_MAX_CHARS
    assert "第59问" in text and "第0问" not in text


def test_timeout_grows_with_input_and_is_capped():
    assert sd.digest_timeout(0) == sd.DIGEST_TIMEOUT_BASE
    assert sd.digest_timeout(1000) == sd.DIGEST_TIMEOUT_BASE + sd.DIGEST_TIMEOUT_PER_1K
    assert sd.digest_timeout(10 ** 6) == sd.DIGEST_TIMEOUT_MAX


async def test_slow_model_times_out(monkeypatch, caplog):
    """LLM 客户端本身没有超时，卡住的请求会一直挂着；摘要必须自己限时"""
    import asyncio
    import logging

    class SlowLLM:
        async def ainvoke(self, messages):
            await asyncio.sleep(1.0)
    _patch(monkeypatch, SlowLLM())
    monkeypatch.setattr(sd, "digest_timeout", lambda chars: 0.05)

    with caplog.at_level(logging.WARNING):
        try:
            await sd.build_session_digest(RECORDS)
            raise AssertionError("应当超时")
        except TimeoutError:
            pass
    assert "会话摘要超时" in caplog.text


async def test_success_logs_input_size_and_duration(monkeypatch, caplog):
    import logging
    _patch(monkeypatch, FakeLLM())
    with caplog.at_level(logging.INFO):
        await sd.build_session_digest(RECORDS)
    assert "会话摘要完成" in caplog.text and "耗时" in caplog.text
