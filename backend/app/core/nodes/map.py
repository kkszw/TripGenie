# author: szw
"""地图数据节点：只负责读写 state，具体的地理编码/路线在各 services 模块里。"""
from langchain_core.runnables import RunnableConfig

from app.core import llm as llm_runtime
from app.core.state import TripState
from app.services.map_data import generate_map_data


async def generate_map_node(state: TripState, config: RunnableConfig = None) -> TripState:
    """生成地图数据（景点坐标 + 每日路线）。

    单独成节点的唯一原因：反思回环会把 generate_plan 跑两遍，而地图依赖高德的
    地理编码和驾车路线接口，跑两遍就是双倍耗时 + 撞 QPS。放在质检之后，
    重排多少次都只出一次地图。
    """
    writer = config.get("configurable", {}).get("writer") if config else None
    days = state.get("draft_plan") or []

    if not days:
        # 行程生成本身失败了，地图没有意义：不报错重复打扰，交给已有的 error 提示
        state["current_step"] = "generate_map"
        return state

    if writer:
        writer({
            "type": "node_start",
            "node": "生成地图",
            "message": "正在获取景点坐标和路线..."
        })

    try:
        await llm_runtime.ensure_tools()
        plan_data = {
            "days": days,
            "destination": state.get("destination") or "",
            "tips": state.get("plan_tips") or [],
        }
        map_data = await generate_map_data(state, plan_data)
        if map_data:
            state["map_data"] = map_data
            print(f"✅ 地图数据生成成功: {len(map_data.get('attractions', []))} 个景点")
    except Exception as e:
        state["error"] = f"生成地图失败: {e}"
        print(f"❌ 生成地图失败: {e}")

    state["current_step"] = "generate_map"
    if writer:
        writer({
            "type": "node_end",
            "node": "生成地图",
            "message": "地图数据就绪"
        })
    return state
