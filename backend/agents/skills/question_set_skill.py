from typing import Type

from langchain_core.tools import BaseTool

from backend.agents.agent.extract_agent import async_extract_tool, extract_tool
from backend.agents.agent.question_set_agent import async_question_set_tool, question_set_tool
from pydantic import BaseModel,Field


class QuestionSetInput(BaseModel):
    query: str = Field(description="用户的题目请求")

class QuestionSetSkill(BaseTool):
    name :str = "question_set_skill"
    description :str = "根据已有的题目生成变式题"
    args_schema : Type[BaseModel] = QuestionSetInput

    def _run(self, query: str) -> str:
        try:
            extract = extract_tool(query)
            new_input = {
                'input': query,
                'extract': extract
            }
            result = question_set_tool(new_input)
            if 'error' in result:
                return f"【题目生成】生成变式题失败：{result['error']}"
            return f"【题目生成】已生成变式题：\n{result['result']}"
        except Exception as e:
            return f"【题目生成】生成变式题失败：{str(e)}"

    async def _arun(self, query: str) -> str:
        """执行题目生成技能"""
        try:
            extract = await async_extract_tool(query)
            new_input = {
                'input': query,
                'extract': extract
            }
            result = await async_question_set_tool(new_input)
            if 'error' in result:
                return f"【题目生成】生成变式题失败：{result['error']}"
            return f"【题目生成】已生成变式题：\n{result['result']}"
        except Exception as e:
            return f"【题目生成】生成变式题失败：{str(e)}"
