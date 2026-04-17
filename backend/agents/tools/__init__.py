from backend.agents.tools.common_tool import CommonTool
from backend.agents.tools.extract_knowledge_tool import ExtractKnowledgeTool
from backend.agents.tools.query_memory_tool import QueryMemoryTool
from backend.agents.tools.question_set_tool import QuestionSetTool
from backend.agents.tools.user_profile_save_tool import UserProfileSaveTool
from backend.agents.tools.user_profile_query_tool import UserProfileQueryTool
from backend.agents.tools.user_profile_delete_tool import UserProfileDeleteTool
from backend.agents.tools.load_skill_tool import LoadSkillTool

TOOLS = [
    CommonTool(),
    ExtractKnowledgeTool(),
    QueryMemoryTool(),
    QuestionSetTool(),
    UserProfileSaveTool(),
    UserProfileQueryTool(),
    UserProfileDeleteTool(),
    LoadSkillTool(),
]

TOOL_MAP = {tool.name: tool for tool in TOOLS}

def get_tool_prompt() -> str:
    lines = []
    for idx, tool in enumerate(TOOLS, 1):
        lines.append(f"{idx}. 工具名：{tool.name}")
        lines.append(f"   功能：{tool.description}\n")
    return "\n".join(lines)
