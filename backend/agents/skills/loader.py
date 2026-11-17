"""
Skill 加载器：读取 `backend/agents/skills/<name>/SKILL.md`，返回 frontmatter 元数据和正文。

与 `backend/agents/tools/` 完全不同的概念：
- tools 是 function calling 的可执行函数
- skills 是可加载到上下文的程序化知识（SKILL.md 剧本）

本模块只负责文件读取和 frontmatter 解析，不做缓存，方便教研热编辑 SKILL.md 后立即生效。
"""
import re
from pathlib import Path
from typing import TypedDict

_SKILLS_ROOT = Path(__file__).parent

# mtime 缓存：key=skill name, value=(mtime, meta, body)
_cache: dict[str, tuple[float, dict, str]] = {}


class SkillMeta(TypedDict):
    name: str
    description: str
    triggers: list[str]
    version: str
    # public（默认）：出现在 ReAct 主提示词的 Skill 清单里，参与触发词匹配
    # internal：只供代码通过 load_skill 使用（如 RAG 入库的 prompt），对 Agent 不可见
    visibility: str


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    极简 YAML frontmatter 解析：仅支持 `key: value` 和 `key: [a, b, c]` 两种形式。
    避免引入 PyYAML 依赖；SKILL.md 的 frontmatter 字段固定且简单。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, text

    meta: dict = {}
    for raw in lines[1:end_idx]:
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip("'\"") for v in value[1:-1].split(",")]
            meta[key] = [v for v in items if v]
        else:
            meta[key] = value.strip("'\"")

    body = "\n".join(lines[end_idx + 1:]).lstrip("\n")
    return meta, body


def _read_skill_file(name: str) -> tuple[dict, str]:
    path = _SKILLS_ROOT / name / "SKILL.md"
    if not path.exists():
        raise FileNotFoundError(f"Skill '{name}' not found at {path}")
    mtime = path.stat().st_mtime
    if name in _cache and _cache[name][0] == mtime:
        _, meta, body = _cache[name]
        return meta, body
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    _cache[name] = (mtime, meta, body)
    return meta, body


def load_skill(name: str) -> str:
    """加载指定 Skill 的正文内容（不含 frontmatter），用于注入到 LLM 上下文。"""
    _, body = _read_skill_file(name)
    return body


def get_skill_meta(name: str) -> SkillMeta:
    """获取单个 Skill 的元数据。"""
    meta, _ = _read_skill_file(name)
    return SkillMeta(
        name=meta.get("name", name),
        description=meta.get("description", ""),
        triggers=meta.get("triggers", []),
        version=meta.get("version", ""),
        visibility=meta.get("visibility", "public"),
    )


def list_skills(include_internal: bool = False) -> list[SkillMeta]:
    """
    遍历 skills 目录，返回 Skill 的元数据列表。
    默认不含 internal skill：这份清单会进 ReAct 主提示词，internal skill 对 Agent 没有用，
    列出来只会诱导它在处理用户请求时去加载。
    """
    result: list[SkillMeta] = []
    for child in sorted(_SKILLS_ROOT.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "SKILL.md").exists():
            continue
        try:
            meta = get_skill_meta(child.name)
        except Exception:
            continue
        if meta["visibility"] == "internal" and not include_internal:
            continue
        result.append(meta)
    return result


def get_skill_list_prompt() -> str:
    """格式化成 ReAct 主 prompt 里展示的 Skill 清单文本。"""
    skills = list_skills()
    if not skills:
        return "（当前没有可加载的 Skill）"
    lines = []
    for idx, meta in enumerate(skills, 1):
        triggers = "、".join(meta["triggers"]) if meta["triggers"] else "无"
        lines.append(f"{idx}. Skill 名：{meta['name']}")
        lines.append(f"   描述：{meta['description']}")
        lines.append(f"   触发词：{triggers}\n")
    return "\n".join(lines)


def match_triggers(user_input: str) -> list[str]:
    """
    字面子串匹配：扫描 user_input，返回所有触发词命中的 Skill 名列表。
    用于 ReAct 主循环在 LLM 推理前做客观兜底，防止语义漏触发。
    """
    if not user_input:
        return []
    hit: list[str] = []
    for meta in list_skills():
        for trig in meta["triggers"]:
            if trig and trig in user_input:
                hit.append(meta["name"])
                break
    return hit


def load_skill_code(name: str, tag: str = "validator") -> str | None:
    """提取 SKILL.md 里指定 tag 的 fenced code block 内容。"""
    _, body = _read_skill_file(name)
    pattern = rf"```skill:{re.escape(tag)}\n(.*?)```"
    m = re.search(pattern, body, re.DOTALL)
    return m.group(1) if m else None
