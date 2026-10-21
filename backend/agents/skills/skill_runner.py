"""
SkillRunner：执行 SKILL.md 中内嵌的可执行 Python 代码片段。

用法：
    from backend.agents.skills.skill_runner import run_validator
    is_valid, reason = run_validator("question_variant", result_text)

这里的代码片段都是用 exec 执行的：SKILL.md 和代码同仓库、同等信任，所以可以这么做，
但任何能改 SKILL.md 的人都能在服务进程里执行任意代码，这个目录不能开放给不受信任的编辑者。
"""
from backend.middleware.logging import get_logger

logger = get_logger(__name__)


def run_validator(name: str, result: str) -> tuple[bool, str]:
    """
    执行指定 Skill 的 validator 代码片段，返回 (is_valid, reason)。
    若 SKILL.md 中没有 validator 片段，视为校验通过。
    """
    from backend.agents.skills.loader import load_skill_code
    code = load_skill_code(name, "validator")
    if code is None:
        return True, "no validator"
    ns: dict = {}
    exec(compile(code, f"<skill:{name}:validator>", "exec"), ns)
    return ns["validate"](result)


def load_vocab(name: str, tag: str = "vocab") -> dict:
    """
    执行指定 Skill 的 vocab 代码片段，返回其中的 VOCAB：{字段: {键: [允许的值]}}。

    取不到词表时返回空字典，调用方会把它当成「不做限制」——词表写坏了不该让画像写入
    整个失效。但这个降级必须留下痕迹，否则词表失效了没人会发现，所以记一条 warning。
    """
    from backend.agents.skills.loader import load_skill_code
    code = load_skill_code(name, tag)
    if code is None:
        logger.warning("Skill %s 没有 %s 代码块，词表为空，将不做限制", name, tag)
        return {}
    ns: dict = {}
    exec(compile(code, f"<skill:{name}:{tag}>", "exec"), ns)
    vocab = ns.get("VOCAB", {})
    if not vocab:
        logger.warning("Skill %s 的 %s 代码块没有定义 VOCAB，词表为空，将不做限制", name, tag)
    return vocab
