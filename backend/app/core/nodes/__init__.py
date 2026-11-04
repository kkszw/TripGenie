# author: szw
"""LangGraph 各节点的实现。

一个节点一个模块，按职责命名：
  routing   — 意图分类与路由
  slots     — 槽位抽取与追问
  confirm   — 人工确认（HITL）与放弃
  retrieval — 攻略/景点/天气三个并行取数节点
  plan      — 行程生成
  schedule  — 排程优化（重排顺序 + 重算时间轴）
  review    — 行程质检（规则 + 模型）
  map       — 地图数据
  chat      — 闲聊与工具调用

提示词全部在 app/core/prompts/ 下，不写在这里。
"""
from app.core.nodes.chat import chat, should_continue, tool_node
from app.core.nodes.confirm import cancelled, confirm_needs, route_after_confirm
from app.core.nodes.map import generate_map_node
from app.core.nodes.plan import format_response, generate_plan_node
from app.core.nodes.retrieval import (
    get_weather_node,
    retrieve_guides,
    search_attractions_node,
)
from app.core.nodes.review import (
    REVIEW_MAX_ROUNDS,
    city_name,
    code_review,
    merge_plan_tips,
    parse_violations,
    review_plan_node,
    route_after_review,
)
from app.core.nodes.routing import classify_input, intent_router
from app.core.nodes.schedule import optimize_schedule_node
from app.core.nodes.slots import clarify, format_input, route_after_format

__all__ = [
    # 图上的节点与路由函数
    "classify_input", "intent_router",
    "chat", "tool_node", "should_continue",
    "format_input", "clarify", "route_after_format",
    "confirm_needs", "cancelled", "route_after_confirm",
    "retrieve_guides", "search_attractions_node", "get_weather_node",
    "generate_plan_node", "format_response",
    "optimize_schedule_node",
    "review_plan_node", "route_after_review",
    "generate_map_node",
    # 质检的可单独测试的部件
    "REVIEW_MAX_ROUNDS", "city_name", "code_review",
    "parse_violations", "merge_plan_tips",
]
