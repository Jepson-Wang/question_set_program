"""从 LLM 输出中截取 JSON。模型常在 JSON 外面包一层 Markdown 围栏，或加一两句说明。"""
import json


def parse_json_object(text) -> dict | None:
    return _parse(text, "{", "}", dict)


def parse_json_array(text) -> list | None:
    return _parse(text, "[", "]", list)


def _parse(text, open_ch: str, close_ch: str, kind: type):
    if not isinstance(text, str):
        return None
    start, end = text.find(open_ch), text.rfind(close_ch)
    if start == -1 or end < start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, kind) else None
