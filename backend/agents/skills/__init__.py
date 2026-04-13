from backend.agents.skills.common_skill import CommonSKill
from backend.agents.skills.extract_knowledge_skill import ExtractKnowledgeSkill
from backend.agents.skills.query_memory_skill import QueryMemorySkill
from backend.agents.skills.question_set_skill import QuestionSetSkill

SKILLS = [
    CommonSKill(),
    ExtractKnowledgeSkill(),
    QueryMemorySkill(),
    QuestionSetSkill(),
]

SKILL_MAP = {skill.name: skill for skill in SKILLS}

def get_skill_prompt() -> str:
    lines = []
    for idx, skill in enumerate(SKILLS, 1):
        lines.append(f"{idx}. 技能名：{skill.name}")
        lines.append(f"   功能：{skill.description}\n")
    return "\n".join(lines)