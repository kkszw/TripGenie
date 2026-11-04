# author: szw
"""意图分类与第一段路由。"""
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage

from app.core import llm as llm_runtime
from app.core.prompts import CLASSIFY_SYSTEM_PROMPT
from app.core.state import TripState


async def classify_input(state: TripState):
    """分析用户的输入并路由到目标节点"""
    user_input = state["user_input"]
    # 注意：这里不需要 ensure_tools()。意图分类走 llm_with_structure，
    # 不 bind 任何工具；放在这里只会让每条消息白等 2 秒去拉 MCP 工具清单。
    messages = [
        SystemMessage(content=CLASSIFY_SYSTEM_PROMPT),
        HumanMessage(content=f"用户输入：{user_input}")
    ]
    result = await llm_runtime.llm_with_structure.ainvoke(messages)
    state["current_step"] = result.intent
    state["response"] = None
    return state


async def intent_router(state: TripState) -> Literal["format_input", "chat"]:
    """根据意图路由到不同处理器"""
    return state.get("current_step")
