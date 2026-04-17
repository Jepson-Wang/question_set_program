import os

from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph

from backend.agents.agent.tools import GraphState
from backend.agents.agent.get_llm import get_llm
from backend.agents.skills import load_skill
from backend.agents.skills.skill_runner import run_validator
from backend.core.single_tool import singleton_method

from dotenv import load_dotenv

load_dotenv()


@singleton_method
def build_question_set_agent(streaming: bool = False) -> CompiledStateGraph[GraphState] | None:
    """
    负责根据提取到的知识点和难度，生成题目
    """
    model = os.getenv('QUESTION_SET_MODEL')
    if not model:
        agent = get_llm(streaming=streaming)
    else:
        agent = get_llm(model=model, streaming=streaming)
    return agent


def question_set_tool(text: dict) -> dict:
    """
    负责根据用户输入的题目和根据题目的知识点和难度，生成一个新题
    """
    try:
        user_input = text['input']
        difficulty = text['extract']['difficulty']
        knowledge_points = text['extract']['knowledge_points']

        enhanced_input = (
            f"请基于以下参考题目生成一道变式题：{user_input}\n"
            f"知识点要求：{knowledge_points}\n"
            f"难度要求：{difficulty}"
        )

        system_body = load_skill("question_variant")
        llm = build_question_set_agent()
        response = llm.invoke([
            SystemMessage(content=system_body),
            HumanMessage(content=enhanced_input),
        ])

        result_text = response.content
        is_valid, reason = run_validator("question_variant", result_text)
        if not is_valid:
            return {'error': f"生成题目未通过校验：{reason}"}

        return {
            'result': result_text,
            'difficulty': difficulty,
            'knowledge_points': knowledge_points,
        }
    except Exception as e:
        return {'error': str(e)}


async def async_question_set_tool(text: dict) -> dict:
    try:
        user_input = text['input']
        difficulty = text['extract']['difficulty']
        knowledge_points = text['extract']['knowledge_points']

        enhanced_input = (
            f"请基于以下参考题目生成一道变式题：{user_input}\n"
            f"知识点要求：{knowledge_points}\n"
            f"难度要求：{difficulty}"
        )

        system_body = load_skill("question_variant")
        llm = build_question_set_agent(streaming=False)
        response = await llm.ainvoke([
            SystemMessage(content=system_body),
            HumanMessage(content=enhanced_input),
        ])

        result_text = response.content
        is_valid, reason = run_validator("question_variant", result_text)
        if not is_valid:
            return {'error': f"生成题目未通过校验：{reason}"}

        return {
            'result': result_text,
            'difficulty': difficulty,
            'knowledge_points': knowledge_points,
        }
    except Exception as e:
        return {'error': str(e)}
