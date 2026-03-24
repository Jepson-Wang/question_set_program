import json
import os

from backend.agents.agent.tools import GraphState, extract_text_from_response, get_llm
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph.state import CompiledStateGraph

from dotenv import load_dotenv
load_dotenv()

IMAGE_GENE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是一个数学图片生成助手，负责根据用户的输入，生成符合要求的图片。
            1. 生成的图片要符合用户的要求，如图片的大小，图片的格式等
            2. 生成的图片要符合用户的要求，如图片的内容，图片的主题等
            3. 生成的图片要符合用户的要求，如图片的质量，图片的清晰度等
            4. 输入示例
            帮我生成图片：请生成一张清晰的平面几何图，纯白色背景，黑色线条，字体工整，无水印，无多余装饰，高清晰度，线条笔直。
            图形内容：
            1. 画一个水平放置的矩形 ABCD。
            2. 点 A 在左下角，点 B 在右下角，点 C 在右上角，点 D 在左上角。
            3. AB 边是底边，长度为 6cm。
            4. AD 边是左边，高度为 4cm。
            5. 连接对角线 AC，从左下角 A 连到右上角 C。
            6. 在 AB 边下方标注“AB=6cm”。
            7. 在 AD 边左侧标注“AD=4cm”。
            8. 四个顶点 A、B、C、D 都标注字母。
            9. 整体居中，线条清晰，像数学试卷上的标准几何图。
            """),
    ("user", "{input}")  # 使用"user"角色而不是"human"
])

def build_image_geng_agent() -> CompiledStateGraph[GraphState] | None:
    """
    负责根据用户输入，生成图片
    :return:
    """

    model = os.getenv('IMAGE_GENE_MODEL')

    if not model:
        agent = get_llm()
    else:
        agent = get_llm(model=model)

    return agent
