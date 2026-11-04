# author: szw
"""排程节点：生成行程之后、质检之前，把"几点到几点"重算一遍。

为什么夹在这里：
  - 放在生成之后：LLM 的语义选择（去哪几个）已经定下来，这里只改顺序和钟点；
  - 放在质检之前：review_plan 的时间顺序校验看到的是修好的版本，不会因为
    "10:30 结束、10:30 又是下一站"这种问题白白打回重排一次（重排一次要多花
    一次完整 LLM 调用的钱和时间，而且大概率还是错的）。

节点本身不阻断主流程：拿不到坐标、排程抛异常，都保留原始顺序继续往下走。
"""
from langchain_core.runnables import RunnableConfig

import time

from app.core.logging import get_logger
from app.core.nodes.plan import format_response
from app.core.state import TripState
from app.services.geo import parse_region
from app.services.map_data import resolve_coords
from app.services.scheduler import optimize_days, place_name

logger = get_logger(__name__)


def _drop_note(dropped) -> str:
    names = []
    for item in dropped[:3]:
        names.append(place_name(item.get("activity")) or str(item.get("activity") or "")[:12])
    joined = "、".join(name for name in names if name) or "部分活动"
    return (f"按实际通勤时间重排后，{joined} 当天排不进去，已移到备选；"
            f"想保留可以增加天数，或减少同一天的景点。")


async def optimize_schedule_node(state: TripState, config: RunnableConfig = None) -> TripState:
    """按地理邻近重排 + 按真实通勤重算时间轴。"""
    days = state.get("draft_plan") or []
    writer = config.get("configurable", {}).get("writer") if config else None

    if not days:
        state["current_step"] = "optimize_schedule"
        return state

    if writer:
        writer({
            "type": "node_start",
            "node": "优化排程",
            "message": "正在按实际通勤时间重排..."
        })

    started = time.perf_counter()
    try:
        names = []
        for day in days:
            for activity in day.get("activities") or []:
                name = place_name(activity.get("activity"))
                if name:
                    names.append(name)

        region = parse_region(state.get("destination") or "")
        coords = await resolve_coords(names, region)
        locate_ms = int((time.perf_counter() - started) * 1000)
        result = optimize_days(days, coords.get)

        # 一个坐标都没拿到时 optimize_days 是原样返回的，这时不要覆盖 response，
        # 否则会把 LLM 的原始正文换成一份没有任何改动的格式化文本。
        if coords:
            state["draft_plan"] = result.days
            state["schedule_dropped"] = result.dropped
            state["schedule_stats"] = result.stats
            state["response"] = format_response({"days": result.days})
            if result.dropped:
                tips = list(state.get("plan_tips") or [])
                tips.append(_drop_note(result.dropped))
                state["plan_tips"] = tips
            logger.info("排程完成", extra={
                "coords": len(coords),
                "dropped": len(result.dropped),
                "short_gap": result.stats.get("short_gap_ratio"),
                "travel_min": result.stats.get("travel_minutes"),
                "locate_ms": locate_ms,
                "total_ms": int((time.perf_counter() - started) * 1000),
            })
    except Exception as e:
        # 排程是增强项，挂了保留原始顺序，不能把整条行程带走。
        logger.warning("排程优化失败，保留原始顺序", extra={"err": str(e)})

    state["current_step"] = "optimize_schedule"
    if writer:
        writer({
            "type": "node_end",
            "node": "优化排程",
            "message": "排程优化完成"
        })
    return state
