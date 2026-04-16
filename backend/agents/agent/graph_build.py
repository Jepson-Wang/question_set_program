# from backend.agents.agent.tools import GraphState
# from langgraph.graph import StateGraph, START, END
# from langgraph.graph.state import CompiledStateGraph
# from backend.agents.agent.planner_agent import planner_node, async_planner_node
# from backend.agents.agent.extract_agent import extract_node, async_extract_tool
# from backend.agents.agent.analyse_agent import analyse_node
# from backend.agents.agent.common_agent import common_node, async_common_node
# from backend.agents.agent.question_set_agent import async_question_set_tool






# def build_graph(user_id: int, session_id :int) -> CompiledStateGraph[GraphState]:
#     """
#     负责构建工作流
#     :return:
#     """
#     graph = StateGraph(GraphState) #type:ignore
#     # 注意：这里的 `graph` 是 LangGraph 的构建器（StateGraph），
#     # 不是可写的状态 dict；真实的初始 state 应当在 `agent.invoke(state)` 时传入（见 api 层）。

#     #添加节点
#     graph.add_node("planner", planner_node) #type:ignore
#     graph.add_node("extract", extract_node) #type:ignore
#     graph.add_node("question_set", async_question_set_tool) #type:ignore
#     graph.add_node("analyse", analyse_node) #type:ignore
#     graph.add_node("common", common_node) #type:ignore

#     #定义逻辑
#     def router_logic(state:GraphState):
#         route = state['route']
#         if route not in ['extract','ocr','common']:
#             route = 'common'
#         return route

#     # def analyse_logic(state:GraphState):
#     #     result = state['result']

#     #TODO 还得做一个OCR的Agent
#     graph.add_edge(START, "planner")
#     graph.add_conditional_edges(
#         "planner",
#         router_logic,
#         {
#             "extract": "extract",
#             "common": "common"
#         }
#     )

#     #TODO 将ayalyse_node的相关逻辑做好
#     graph.add_edge('extract', 'question_set')
#     # graph.add_edge('question_set', 'analyse')
#     graph.add_edge('question_set', END)
#     graph.add_edge('common', END)
#     graph.add_edge('analyse', END)
#     return graph.compile()


# def build_stream_graph() -> CompiledStateGraph[GraphState]:
#     """
#     构建支持流式输出的异步工作流
#     """
#     graph = StateGraph(GraphState)  # type:ignore
#     graph.add_node("planner", async_planner_node)  # type:ignore
#     graph.add_node("extract", async_extract_tool)  # type:ignore
#     graph.add_node("question_set", async_question_set_node)  # type:ignore
#     graph.add_node("common", async_common_node)  # type:ignore

#     def router_logic(state: GraphState):
#         route = state['route']
#         if route not in ['extract', 'common']:
#             route = 'common'
#         return route

#     graph.add_edge(START, "planner")
#     graph.add_conditional_edges(
#         "planner",
#         router_logic,
#         {
#             "extract": "extract",
#             "common": "common"
#         }
#     )
#     graph.add_edge('extract', 'question_set')
#     graph.add_edge('question_set', END)
#     graph.add_edge('common', END)
#     return graph.compile()