from typing import Type

from backend.agents.agent.extract_agent import async_extract_tool, extract_tool
from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool


class ExtractKnowledgeInput(BaseModel):
    text: str = Field(description="用户输入的题目或文本内容")

class ExtractKnowledgeSkill(BaseTool):
    name : str = "extract_skill"
    description : str = "用户需要生成题目、变式题、提取知识点时调用"
    args_schema : Type[BaseModel] = ExtractKnowledgeInput

    def _run(self, text: str) -> str:
        try:
            extract_result = extract_tool(text)

            if extract_result:
                return f"""【知识点提取】已提取知识点：
                难度：{extract_result.get('difficulty', '未知')}
                知识点：{', '.join(extract_result.get('knowledge_points', []))}"""
            else:
                return "【知识点提取】未能提取到知识点"

        except Exception as e:
            return f"【知识点提取】提取知识点失败：{str(e)}"


    async def _arun(self, text: str) -> str:
        """执行知识点提取技能"""
        try:
            extract_result = await async_extract_tool(text)
            
            if extract_result:
                return f"""【知识点提取】已提取知识点：
                难度：{extract_result.get('difficulty', '未知')}
                知识点：{', '.join(extract_result.get('knowledge_points', []))}"""
            else:
                return "【知识点提取】未能提取到知识点"
        except Exception as e:
            return f"【知识点提取】提取知识点失败：{str(e)}"
        
