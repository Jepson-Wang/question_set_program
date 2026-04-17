"""
SkillRunner：执行 SKILL.md 中内嵌的可执行 Python 代码片段。

用法：
    from backend.agents.skills.skill_runner import run_validator
    is_valid, reason = run_validator("question_variant", result_text)
"""


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
