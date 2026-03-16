from backend.agents.agent.agents import GraphState, build_planner_agent, extract_text_from_response, build_extract_agent, \
    build_question_set_agent, build_analyse_agent, build_common_agent
import os
import json
from typing import TypedDict, Literal

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.messages import SystemMessage, BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

from backend.agents.agent.prompt import EXTRACT_PROMPT, QUESTION_SET_PROMPT, COMMON_PROMPT, PLANNER_PROMPT


def planner_node(state: GraphState) -> GraphState:
    """
    负责任务规划和调度
    :param state:
    :return:
    """

    print('正在初始化planner_agent')
    user_input = state['input']
    planner_agent = build_planner_agent()
    planner_chain = PLANNER_PROMPT | planner_agent
    response = planner_chain.invoke({'input': user_input})

    # 提取文本内容
    response_text = extract_text_from_response(response)

    # 尝试解析 JSON 格式的响应
    try:
        # 尝试从文本中提取 JSON
        if '{' in response_text and '}' in response_text:
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            json_str = response_text[json_start:json_end]
            parsed = json.loads(json_str)
            route = parsed.get('route', '').lower()
        else:
            route = response_text.strip().lower()
    except:
        route = response_text.strip().lower()

    if route not in ['extract', 'question_set', 'analyse', 'common']:
        route = 'common'

    state['route'] = route

    print(route)

    return state


def extract_node(state: GraphState) -> GraphState:
    """
    负责提取请求中知识点和难度
    :param state:
    :return:
    """

    print('正在初始化extract_agent')
    system_input = state['input']
    # 调用用户请求进行提取操作
    extract_agent = build_extract_agent()
    extract_chain = EXTRACT_PROMPT | extract_agent
    response = extract_chain.invoke({'input': system_input})
    # 将提取到的知识点和难度返回给state
    response_text = extract_text_from_response(response)

    # 尝试解析 JSON 格式的响应
    try:
        # 尝试从文本中提取 JSON
        if '{' in response_text and '}' in response_text:
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            json_str = response_text[json_start:json_end]
            parsed = json.loads(json_str)
            state['extract'] = parsed
        elif isinstance(response, dict):
            state['extract'] = response
        else:
            state['extract'] = {}
    except:
        state['extract'] = {}
    return state


def question_set_node(state: GraphState) -> GraphState:
    """
    负责根据提用户输入的题目和根据题目的知识点和难度，生成一个新题
    :param state:
    :return:
    """

    print('正在初始化question_set_agent')
    user_input = state['input']
    extract_input = state['extract']

    # 进行大模型调用相关操作
    question_set_agent = build_question_set_agent()
    question_set_chain = QUESTION_SET_PROMPT | question_set_agent
    # 构造更明确的输入提示
    enhanced_input = f"请基于以下参考题目生成一道变式题：\n\n{user_input}\n\n知识点要求：{extract_input}"
    response = question_set_chain.invoke({'input': enhanced_input})
    # 将生成的题目返回给state
    state['result'] = extract_text_from_response(response)
    return state

#TODO 完成审核相关的模型
def analyse_node(state: GraphState) -> GraphState:
    """
    负责审核生成的题目
    :param state:
    :return:
    """

    print('正在初始化analyse_agent')
    result = state['result']

    # 进行大模型调用相关操作
    analyse_agent = build_analyse_agent()
    response = analyse_agent.invoke({'input': result})
    # 将审核结果返回给state,并追加到result
    response_text = extract_text_from_response(response)
    state['result'] = result + '\n' + response_text
    return state


def common_node(state: GraphState) -> GraphState:
    """
    负责其他一般性回答
    :param state:
    :return:
    """

    print('正在初始化common_agent')
    user_input = state['input']

    # 进行大模型调用相关操作
    common_agent = build_common_agent()
    common_chain = COMMON_PROMPT | common_agent
    response = common_chain.invoke({'input': user_input})
    # 将回答返回给state,并追加到result
    response_text = extract_text_from_response(response)
    state['result'] = state['result'] + '\n' + response_text
    return state

async def async_planner_node(state: GraphState) -> GraphState:
    user_input = state['input']
    planner_agent = build_planner_agent()
    planner_chain = PLANNER_PROMPT | planner_agent
    response = await planner_chain.ainvoke({'input': user_input})
    response_text = extract_text_from_response(response)
    try:
        if '{' in response_text and '}' in response_text:
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            parsed = json.loads(response_text[json_start:json_end])
            route = parsed.get('route', '').lower()
        else:
            route = response_text.strip().lower()
    except:
        route = response_text.strip().lower()
    if route not in ['extract', 'question_set', 'analyse', 'common']:
        route = 'common'
    state['route'] = route
    return state


async def async_extract_node(state: GraphState) -> GraphState:
    system_input = state['input']
    extract_agent = build_extract_agent()
    extract_chain = EXTRACT_PROMPT | extract_agent
    response = await extract_chain.ainvoke({'input': system_input})
    response_text = extract_text_from_response(response)
    try:
        if '{' in response_text and '}' in response_text:
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            parsed = json.loads(response_text[json_start:json_end])
            state['extract'] = parsed
        else:
            state['extract'] = {}
    except:
        state['extract'] = {}
    return state


async def async_question_set_node(state: GraphState) -> GraphState:
    user_input = state['input']
    extract_input = state['extract']
    question_set_agent = build_question_set_agent(streaming=True)
    question_set_chain = QUESTION_SET_PROMPT | question_set_agent
    enhanced_input = f"请基于以下参考题目生成一道变式题：\n\n{user_input}\n\n知识点要求：{extract_input}"
    response = await question_set_chain.ainvoke({'input': enhanced_input})
    state['result'] = extract_text_from_response(response)
    return state


async def async_common_node(state: GraphState) -> GraphState:
    user_input = state['input']
    common_agent = build_common_agent(streaming=True)
    common_chain = COMMON_PROMPT | common_agent
    response = await common_chain.ainvoke({'input': user_input})
    state['result'] = extract_text_from_response(response)
    return state


def build_graph() -> CompiledStateGraph[GraphState]:
    """
    负责构建工作流
    :return:
    """


    graph = StateGraph(GraphState) #type:ignore
    #添加节点
    graph.add_node("planner", planner_node) #type:ignore
    graph.add_node("extract", extract_node) #type:ignore
    graph.add_node("question_set", question_set_node) #type:ignore
    graph.add_node("analyse", analyse_node) #type:ignore
    graph.add_node("common", common_node) #type:ignore

    #定义逻辑
    def router_logic(state:GraphState):
        route = state['route']
        if route not in ['extract','ocr','common']:
            route = 'common'
        return route

    # def analyse_logic(state:GraphState):
    #     result = state['result']

    #TODO 还得做一个OCR的Agent
    graph.add_edge(START, "planner")
    graph.add_conditional_edges(
        "planner",
        router_logic,
        {
            "extract": "extract",
            "common": "common"
        }
    )

    #TODO 将ayalyse_node的相关逻辑做好
    graph.add_edge('extract', 'question_set')
    # graph.add_edge('question_set', 'analyse')
    graph.add_edge('question_set', END)
    graph.add_edge('common', END)
    graph.add_edge('analyse', END)
    return graph.compile()


def build_stream_graph() -> CompiledStateGraph[GraphState]:
    """
    构建支持流式输出的异步工作流
    """
    graph = StateGraph(GraphState)  # type:ignore
    graph.add_node("planner", async_planner_node)  # type:ignore
    graph.add_node("extract", async_extract_node)  # type:ignore
    graph.add_node("question_set", async_question_set_node)  # type:ignore
    graph.add_node("common", async_common_node)  # type:ignore

    def router_logic(state: GraphState):
        route = state['route']
        if route not in ['extract', 'common']:
            route = 'common'
        return route

    graph.add_edge(START, "planner")
    graph.add_conditional_edges(
        "planner",
        router_logic,
        {
            "extract": "extract",
            "common": "common"
        }
    )
    graph.add_edge('extract', 'question_set')
    graph.add_edge('question_set', END)
    graph.add_edge('common', END)
    return graph.compile()