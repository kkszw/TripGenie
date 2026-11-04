# author: szw
"""人工确认（HITL）中断点与放弃分支。"""
from typing import Any, Dict

from langgraph.types import interrupt

from app.core.json_utils import as_number
from app.core.state import TripState
from app.services.geo import parse_region


async def confirm_needs(state: TripState) -> dict:
    """HITL 中断点：把抽好的槽位摆给用户过一眼，再决定要不要花钱花时间去规划。

    为什么单独做成一个节点、而不是接在 format_input 末尾：
    LangGraph 恢复中断时会把整个节点**从头重跑一遍**（这次 interrupt() 直接返回
    resume 值）。如果塞进 format_input，用户每点一次"确认"就得多跑一次那个 LLM
    槽位抽取，既费 token 又可能抽出跟上次不一样的结果。这个节点只读 state 和
    调 interrupt，重跑成本接近零。
    """
    if not state.get("need_confirm"):
        return {"confirmed": True, "current_step": "auto_confirmed"}

    destination = state.get("destination") or ""
    # 只给了省份时（"帮我规划山东三天"），系统不会追问，而是按省内多个城市来排。
    # 这个决定必须让用户看见：卡片上"山东省"三个字看不出"会去济南泰安曲阜"，
    # 不说清楚就成了系统擅自替他选了城市。
    region = parse_region(destination)
    hint = ""
    if region.is_province_only:
        hint = "只指定了省份，行程会覆盖省内多个城市；想固定一座城市可以点「改一改」"

    payload = interrupt({
        "type": "confirm_needs",
        "destination": destination,
        "destination_hint": hint,
        "days": state.get("days"),
        "budget": state.get("budget"),
        "preferences": state.get("preferences") or [],
    })

    # 前端约定三种回复：{"action":"confirm"} / {"action":"cancel"} /
    #                    {"action":"edit", destination?, days?, budget?, preferences?}
    action = (payload or {}).get("action") or "confirm"

    if action == "cancel":
        return {
            "confirmed": False,
            "current_step": "cancelled",
            "response": "好的，这次先不规划。想改哪个条件直接告诉我就行。",
        }

    update: Dict[str, Any] = {"confirmed": True, "current_step": "confirmed"}

    if action == "edit":
        # 只认这几个字段：payload 是前端传进来的，不能让任意键落进 state
        dest = (payload.get("destination") or "").strip()
        if dest and dest != state.get("destination"):
            update["destination"] = dest
        days = as_number(payload.get("days"))
        if days:
            update["days"] = int(days)
        budget = as_number(payload.get("budget"))
        if budget:
            update["budget"] = budget
        prefs = payload.get("preferences")
        if isinstance(prefs, list):
            cleaned = [str(p).strip() for p in prefs if str(p).strip()]
            if cleaned:
                update["preferences"] = cleaned

    return update


async def cancelled(state: TripState) -> dict:
    """用户放弃规划。

    单独一个节点只是为了把 response 变成"节点自己的输出"，让 SSE 层能像处理
    clarify 那样补发一条 message。confirm_needs 同时承担确认逻辑，把话术混在
    它的输出里不好判断该不该发。
    """
    return {"response": state.get("response") or "好的，这次先不规划。"}


def route_after_confirm(state: TripState):
    """确认之后的分流：同意就扇出三个检索节点，放弃就收尾。

    两种分支都返回 list：混着返回 END 和 list 在条件边里不好读，也不方便以后
    再加分支（比如"只调整某一天"）。
    """
    if not state.get("confirmed"):
        return ["cancelled"]
    return ["retrieve_guides", "search_attractions", "get_weather"]
