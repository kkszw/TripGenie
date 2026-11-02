# author: szw
import asyncio
from langgraph.graph import StateGraph, START
from app.core.agents import *
from app.core.checkpointer import get_checkpointer

_workflow = None
_workflow_lock = asyncio.Lock()


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


async def get_workflow():
    """获取工作流（进程内单例）

    原来 chat.py 每个请求都 await create_workflow() 重新编译一遍，而 main.py 启动时
    编译好的那个 app.state.workflow 从来没人用。
    编译后的图是无状态的，状态由 thread_id 对应的 checkpoint 承载，
    多请求共用同一张图是安全的。
    """
    global _workflow
    if _workflow is None:
        async with _workflow_lock:
            if _workflow is None:
                _workflow = await create_workflow()
                print("✅ 工作流编译完成")
    return _workflow
