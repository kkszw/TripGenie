# author: szw
import asyncio
from langgraph.graph import END, START, StateGraph
from app.core.nodes import (
    cancelled,
    chat,
    clarify,
    classify_input,
    confirm_needs,
    format_input,
    generate_map_node,
    generate_plan_node,
    get_weather_node,
    intent_router,
    optimize_schedule_node,
    retrieve_guides,
    review_plan_node,
    route_after_confirm,
    route_after_format,
    route_after_review,
    search_attractions_node,
    should_continue,
    tool_node,
)
from app.core.state import TripState
from app.core.checkpointer import get_checkpointer
from app.core.logging import get_logger

logger = get_logger(__name__)

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
    workflow.add_node("clarify", clarify)
    # HITL 中断点 + 用户放弃后的收尾节点
    workflow.add_node("confirm_needs", confirm_needs)
    workflow.add_node("cancelled", cancelled)
    workflow.add_node("get_weather", get_weather_node)
    workflow.add_node("retrieve_guides", retrieve_guides)
    workflow.add_node("search_attractions", search_attractions_node)
    workflow.add_node("generate_plan", generate_plan_node)
    # 排程优化：LLM 定"去哪几个"，这里定"几点到几点"。放在质检前面，
    # 让 review_plan 校验的是修好的版本，不再为"10:30 结束紧接着 10:30 开始"
    # 这种问题白打回一次重排。
    workflow.add_node("optimize_schedule", optimize_schedule_node)
    # 地图单独成节点：质检可能把 generate_plan 打回重排，地图只该出一次
    workflow.add_node("review_plan", review_plan_node)
    workflow.add_node("generate_map", generate_map_node)

    # 定义边（并行执行）
    workflow.add_edge(START, "classify_input")
    workflow.add_conditional_edges("classify_input", intent_router)

    workflow.add_conditional_edges("chat", should_continue)
    workflow.add_edge("tool_node", END)

    # format_input 之后先判断槽位是否齐全：缺目的地/天数就短路到 clarify，
    # 齐全则先经过 confirm_needs 停下来让用户确认（HITL），确认后才并行扇出。
    # 条件边返回 list 即为动态 fan-out，与原来三条静态 add_edge 等价。
    workflow.add_conditional_edges("format_input", route_after_format)
    workflow.add_edge("clarify", END)

    # 确认之后才真正开始检索。中断点在 confirm_needs 内部，
    # 恢复依赖 compile(checkpointer=...) —— 没有 checkpointer 时 interrupt 会直接报错。
    workflow.add_conditional_edges("confirm_needs", route_after_confirm)
    workflow.add_edge("cancelled", END)

    workflow.add_edge("retrieve_guides", "generate_plan")
    workflow.add_edge("search_attractions", "generate_plan")
    workflow.add_edge("get_weather", "generate_plan")

    # 生成 → 排程 → 质检 →（不过则带着 feedback 回 generate_plan，最多一轮）→ 出地图
    # 自环用的是条件边：route_after_review 返回 ["generate_plan"] 就是回到上游重跑。
    workflow.add_edge("generate_plan", "optimize_schedule")
    workflow.add_edge("optimize_schedule", "review_plan")
    workflow.add_conditional_edges("review_plan", route_after_review)
    workflow.add_edge("generate_map", END)

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
                logger.info("工作流编译完成")
    return _workflow
