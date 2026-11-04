# author: szw
"""槽位抽取与追问：format_input / clarify 两个节点。"""
from typing import List, Optional

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from app.core import llm as llm_runtime
from app.core.json_utils import as_number, parse_llm_json
from app.core.logging import get_logger
from app.core.prompts import build_format_prompt, clarify_text
from app.core.state import TripState

logger = get_logger(__name__)

# 用户明确表示"不要之前那个城市"的说法。这类话里没有地名，LLM 抽不出 destination，
# 但如果上一步正在沿用历史目的地，就会变成"用户想换、系统还按老城市排"。
_CHANGE_CITY_HINTS = ("换城市", "换个城市", "别的城市", "其他城市", "其它城市",
                      "另一个城市", "换地方", "换个地方", "不想去")


def _wants_other_city(text: str) -> bool:
    text = text or ""
    return any(hint in text for hint in _CHANGE_CITY_HINTS)


def _detect_missing(state: TripState, dest_explicit: bool = True,
                    inherit_dest: bool = True) -> List[str]:
    """判断关键槽位是否缺失（destination / days）。

    budget 不在此列：缺预算用默认值即可，追问预算会让对话变成填表。

    历史目的地能不能直接沿用，取决于"这句话是不是在接着上一份行程说"：
    - inherit_dest=True：在补全追问（"3天""对"）或修改行程（"改成5天"），沿用是对的；
    - inherit_dest=False：隔了几轮又说"帮我规划个旅行"，checkpoint 里的目的地
      早就过时了，直接沿用会变成"默默规划一个用户没提的城市"
      （实测把上一轮的大连又排了一遍），所以退化成确认式追问。
    """
    missing: List[str] = []
    dest = (state.get("destination") or "").strip()
    if not dest:
        missing.append("destination")
    elif not dest_explicit and not inherit_dest:
        missing.append("confirm_destination")
    if not state.get("days"):
        missing.append("days")
    return missing


async def format_input(state: TripState, config: RunnableConfig = None) -> TripState:
    """分析用户输入，提取目的地、天数、预算、偏好

    多轮修改场景（先说"三亚3天"、再说"改成5天"）必须带上一轮的已知槽位：
    LLM 只看本轮 user_input 时，"改成5天"里没有地名，destination 会解析成空；
    如果直接覆盖 state，上一轮的三亚就丢了。所以这里既在 prompt 里喂已知值，
    写回时也只覆盖"本轮明确提到"的字段。
    """
    known_dest = (state.get("destination") or "").strip()
    known_days = state.get("days")
    known_budget = state.get("budget")

    # 上一轮是不是 clarify 追问（clarify 节点会把问题写进 clarify_question，
    # 这里读完就清空，等价于"消费一次"）。只有处在这条补全链上，
    # 才允许把 checkpoint 里的老槽位直接拿来用。
    continuing_clarify = bool((state.get("clarify_question") or "").strip())

    prompt = build_format_prompt(
        user_input=state["user_input"],
        known_dest=known_dest,
        known_days=known_days,
        known_budget=known_budget,
    )

    writer = config.get("configurable", {}).get("writer") if config else None
    if writer:
        writer({
            "type": "node_start",
            "node": "📋 分析需求",
            "message": "正在分析您的旅行需求..."
        })
    response = await llm_runtime.llm.ainvoke([HumanMessage(content=prompt)])
    content = response.content
    if isinstance(content, list):
        # 如果是列表，拼接成字符串
        content = " ".join(str(item) for item in content)
    dest_explicit = False   # 本轮输入里是否真的出现了目的地
    modifying = False       # 本轮是在修改上一份行程 / 回答追问，而不是开新任务
    try:
        logger.debug("槽位抽取结果", extra={"raw": content})
        data = parse_llm_json(content)
        modifying = str(data.get("task_type") or "").strip().lower() == "modify"

        # 只覆盖本轮明确提到的值：用户这轮说"改成5天"时，destination 不能因为
        # LLM 返回空串就把上一轮的三亚清掉。
        new_dest = (data.get("destination") or "").strip()
        dest_explicit = bool(new_dest)
        state["destination"] = new_dest or known_dest

        new_days = as_number(data.get("days"))
        state["days"] = new_days if new_days else known_days

        new_budget = as_number(data.get("budget"))
        state["budget"] = new_budget if new_budget else (known_budget or 5000)

        state["preferences"] = data.get("preferences") or state.get("preferences") or []
        # 兼容旧字段名 start_data；缺失时保留调用方传入的 start_date（不要覆盖成 None）
        state["start_date"] = data.get("start_date") or data.get("start_data") or state.get("start_date")
    except Exception as e:
        state["error"] = "解析用户输入失败"
        logger.warning("槽位抽取解析失败", extra={"err": str(e)})

    # 用户说"换个城市"却没点名：不能顺着老目的地继续排，清掉让它变成真正的追问
    if not dest_explicit and _wants_other_city(state.get("user_input")):
        state["destination"] = ""

    # 成功或失败都要算一次：解析挂了也得让 clarify 兜住，不能拿空目的地去检索
    state["missing_fields"] = _detect_missing(
        state,
        dest_explicit=dest_explicit,
        # 两种情况才允许沿用 checkpoint 里的老目的地：
        #   1) 上一轮就是 clarify 追问，用户在补全（"3天""对"）
        #   2) LLM 判定本轮是在改上一份行程（"改成5天""换成便宜点的"）
        inherit_dest=continuing_clarify or modifying,
    )
    state["clarify_question"] = None

    # 要不要在检索之前停下来跟用户确认一次（HITL）？
    # 只在"这轮给了新目的地"或"刚补完 clarify 的追问"时才停：前者可能把城市听错，
    # 后者是用户刚说完关键信息、最该让他确认一眼。
    # "改成5天""预算加到8000"这类纯参数调整不打断，否则每次微调都要多点一下。
    state["need_confirm"] = bool(not state["missing_fields"]) and (
        dest_explicit or continuing_clarify
    )

    state["current_step"] = "format_input"
    if writer:
        writer({
            "type": "node_end",
            "node": "📋 分析需求",
            "message": "需求分析完成"
        })
    return state


async def clarify(state: TripState) -> TripState:
    """关键信息不全时不硬猜，先问清楚再规划。

    只写 response / clarify_question，不动 destination 和 days：
    用户这一轮的补充会在下一轮由 format_input 合并到已有槽位上
    （已知值靠 checkpoint 带过去，前提是 format_input 不清空未提及的字段）。
    """
    missing = state.get("missing_fields") or []
    question = clarify_text(
        missing,
        dest=(state.get("destination") or "").strip(),
        days=state.get("days"),
    )

    logger.info("信息不足，发起追问", extra={"missing": missing})
    state["clarify_question"] = question
    state["response"] = question
    state["current_step"] = "clarify"
    # messages 带 add_messages reducer，这里是"追加一条"，不是覆盖历史
    state["messages"] = [AIMessage(content=question)]
    return state


def route_after_format(state: TripState):
    """format_input 之后的路由：缺关键槽位就短路到 clarify，齐全则去人工确认。

    确认之后才真正扇出三个检索节点（见 route_after_confirm）。
    """
    if state.get("missing_fields"):
        return ["clarify"]
    return ["confirm_needs"]
