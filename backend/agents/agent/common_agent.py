import os

from backend.agents.agent.get_llm import get_llm
from langchain_core.prompts import ChatPromptTemplate

from backend.core.config import load_env

from backend.core.single_tool import singleton_method
from backend.middleware.logging import get_logger

logger = get_logger(__name__)

load_env()

COMMON_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是专业教育解题助手，负责：
        - 提供解题步骤、思路、方法、答案
        - 解释知识点、难度、考点、易错点
        - 解答题目相关疑问
        - 回答应该简练，避免使用复杂的词汇
        - 回答清晰易懂，不生成新题目。"""),
    ("user", "{input}")
])

@singleton_method
def build_common_agent(streaming: bool = False) :
    """
    负责其他一般性回答
    """
    model = os.getenv('COMMON_MODEL')
    if not model:
        agent = get_llm(streaming=streaming)
    else:
        agent = get_llm(model=model, streaming=streaming)
    return agent

async def async_common_tool(text: str) -> str:
    common_agent = build_common_agent(streaming=True)
    common_chain = COMMON_PROMPT | common_agent
    response = await common_chain.ainvoke({'input': text})
    return response.content
