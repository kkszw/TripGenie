# author: szw
from langgraph.graph import StateGraph, START
from app.core.agents import *
from app.core.checkpointer import get_checkpointer


async def create_workflow():
    """创建 LangGraph 工作流"""

    # 创建图
    workflow = StateGraph(TripState)

    # 添加节点
    workflow.add_node("classify_input", classify_input)
    workflow.add_node("chat", chat)
    workflow.add_node("tool_node", tool_node)
    workflow.add_node("format_input", format_input)
    workflow.add_node("get_weather", get_weather_node)
    workflow.add_node("retrieve_guides", retrieve_guides)
    workflow.add_node("search_attractions", search_attractions_node)
    workflow.add_node("generate_plan", generate_plan_node)

    # 定义边（并行执行）
    workflow.add_edge(START, "classify_input")
    workflow.add_conditional_edges("classify_input", intent_router)

    workflow.add_conditional_edges("chat", should_continue)
    workflow.add_edge("tool_node", END)

    workflow.add_edge("format_input", "retrieve_guides")
    workflow.add_edge("format_input", "search_attractions")
    workflow.add_edge("format_input", "get_weather")

    workflow.add_edge("retrieve_guides", "generate_plan")
    workflow.add_edge("search_attractions", "generate_plan")
    workflow.add_edge("get_weather", "generate_plan")
    workflow.add_edge("generate_plan", END)

    # 编译（带检查点）
    check = await get_checkpointer()
    return workflow.compile(checkpointer=check)
