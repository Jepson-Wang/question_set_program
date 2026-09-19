"""注入 prompt 的上下文怎么拼：会话要点在前，最近几轮原文在后。"""
from backend.api.user_api.agent_api import _format_memory_context


def _unit(user_text: str, model_text: str) -> dict:
    return {"memory": {"user_memory": user_text, "model_memory": model_text}}


RECENT = [_unit("第三问", "第三答"), _unit("第二问", "第二答"), _unit("第一问", "第一答")]  # 新的在前


def test_nothing_to_inject():
    assert _format_memory_context([], "") == ""


def test_only_recent_dialogue():
    out = _format_memory_context(RECENT)
    assert "【本次会话要点】" not in out
    assert out.index("第一问") < out.index("第二问") < out.index("第三问"), "原文按从旧到新展示"


def test_digest_comes_before_recent_dialogue():
    out = _format_memory_context(RECENT, "学生在一元一次方程上容易弄错符号。")
    assert out.index("【本次会话要点】") < out.index("【近期对话记录】")
    assert "学生在一元一次方程上容易弄错符号。" in out


def test_only_digest():
    out = _format_memory_context([], "学生偏好不要与原题雷同。")
    assert out == "【本次会话要点】\n学生偏好不要与原题雷同。"


def test_at_most_three_rounds():
    many = [_unit(f"问{i}", f"答{i}") for i in range(10)]
    out = _format_memory_context(many)
    assert "问3" not in out and "问2" in out, "只取最近 3 轮，避免每次请求都把整段历史塞进去"
