# author: szw
"""行程生成节点（Generator）。"""
from datetime import date as dt_date

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from app.core import llm as llm_runtime
from app.core.json_utils import parse_llm_json
from app.core.prompts import build_plan_prompt
from app.core.state import TripState


def format_response(plan_data: dict) -> str:
    """格式化响应文本"""
    text = "根据您的需求，我为您规划了以下行程：\n\n"
    for day in plan_data.get("days", []):
        text += f"📅 **Day {day.get('day')}**: "
        attractions = day.get("attractions", [])
        if attractions:
            text += " → ".join([a.get("name", "") for a in attractions])
        text += "\n"

    return text


async def generate_plan_node(state: TripState, config: RunnableConfig = None) -> TripState:
    """生成行程计划"""
    start_date_str = state.get('start_date') or dt_date.today().isoformat()

    # 重排时把质检的修改指令带进 prompt；第一版没有 feedback，这段为空。
    review_feedback = ""
    if (state.get("review_round") or 0) > 0 and (state.get("review_feedback") or "").strip():
        review_feedback = state["review_feedback"]

    prompt = build_plan_prompt(
        destination=state.get('destination', '未知'),
        days=state.get('days'),
        budget=state.get('budget'),
        preferences=state.get('preferences', []),
        weather=state.get('weather_info', '未知'),
        start_date=start_date_str,
        guides=state.get('similar_guides'),
        attractions=state.get('attractions_pool'),
        review_feedback=review_feedback,
    )

    writer = config.get("configurable", {}).get("writer") if config else None
    if writer:
        writer({
            "type": "node_start",
            "node": "生成行程计划",
            "message": "正在生成行程计划..."
        })

    full_response = ""
    runnable_config: RunnableConfig = {
        "configurable": {"thread_id": state.get("thread_id")},
        # user_visible：这一次调用的 token 是给用户看的正文。
        # api/chat.py 只按这个标记推流，而不再是"节点名等于 generate_plan"——
        # 节点内部任何辅助调用都归属同一节点名，靠节点名过滤必然漏（景点提取就漏过）。
        "tags": ["user_visible"],
    }
    # 使用 astream 流式生成
    async for chunk in llm_runtime.llm.astream([HumanMessage(content=prompt)], config=runnable_config):
        content = chunk.content
        if content:
            full_response += content
            if writer:
                writer({
                    "type": "chunk",
                    "content": content
                })

    try:
        data = parse_llm_json(full_response)
        print("✅ JSON 解析成功")

        state["draft_plan"] = data.get("days", [])
        state["plan_tips"] = data.get("tips") or []
        state["response"] = format_response(data)

        # 地图数据不在这里生成了（原来挂在下面）：质检可能打回重排，
        # 每重排一次就要把地理编码 + 驾车路线整套再打一遍高德，既慢又容易撞 QPS。
        # 现在拆成 generate_map 节点，等质检放行后在最终版上只跑一次。
    except Exception as e:
        state["error"] = f"生成行程失败: {str(e)}"
        state["response"] = full_response

    state["current_step"] = "generate_plan"
    if writer:
        writer({
            "type": "node_end",
            "node": "生成行程",
            "message": "行程计划生成完成"
        })
    return state
