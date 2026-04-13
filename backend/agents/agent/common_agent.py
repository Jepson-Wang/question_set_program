import os

from backend.agents.agent.get_llm import get_llm
from langchain_core.prompts import ChatPromptTemplate

from dotenv import load_dotenv

from backend.core.single_tool import singleton_method

load_dotenv()

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

def common_tool(text: str) -> str:
    """
    负责其他一般性回答
    :param text: 用户的查询内容
    :return:
    """

    print('正在初始化common_agent')

    # 进行大模型调用相关操作
    common_agent = build_common_agent()
    common_chain = COMMON_PROMPT | common_agent
    # 注意：这里必须调用 prompt-chain，而不是直接调用 llm
    # 否则会把 {'input': ...} 作为无效输入类型传给 ChatOpenAI
    response = common_chain.invoke({'input': text})
    # 将回答返回给用户
    return response

async def async_common_tool(text: str) -> str:
    common_agent = build_common_agent(streaming=True)
    common_chain = COMMON_PROMPT | common_agent
    response = await common_chain.ainvoke({'input': text})
    return response
