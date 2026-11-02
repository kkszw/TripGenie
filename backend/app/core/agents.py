# author: szw
import asyncio
import json
from datetime import date as dt_date, timedelta
import re
import httpx
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from typing import Literal, Dict, List, Any, Optional, Tuple
from langchain_core.runnables import RunnableConfig
from app.core.state import TripState, IntentOutput
from app.config import settings
from app.rag.retriever import *
from langgraph.graph import END
from app.mcp.client import getAllTools

#
# DeepSeek 走 OpenAI 兼容端点，所以统一用 ChatOpenAI 指向它，换供应商只改 .env。
#
# 关于思考模式：DeepSeek 默认开启（effort=high）。这里显式关闭，三个原因：
#   1) 开启后思考阶段的 chunk.content 为空，本项目的 SSE 是按 content 转发的，
#      前端会长时间没有任何输出，看起来像卡死；
#   2) 按官方文档，请求里带 tools 时必须在后续请求回传 reasoning_content，否则 400。
#      本项目每轮对话的历史是从 SQLite 重建的纯文本，拿不回 reasoning_content；
#   3) 意图分类、JSON 行程生成不需要长链推理，开着纯亏延迟和 token（思考 token 按输出计费）。
# 确实需要更强推理时，把 .env 的 DEEPSEEK_THINKING 改成 enabled，但要先解决第 2 点。
llm = ChatOpenAI(
    model=settings.deepseek_model,
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_base_url,
    streaming=True,
    timeout=settings.llm_timeout,
    max_retries=2,
    extra_body={"thinking": {"type": settings.deepseek_thinking}},
)
# 内部结构化抽取专用的模型实例：必须关掉 streaming。
# streaming=True 的 llm 在 ainvoke 时同样会逐块生成并触发 on_chat_model_stream，
# 而景点提取是在 generate_plan 节点内部被调用的，事件 metadata.langgraph_node 就是
# generate_plan —— 于是这段"只给程序看的 JSON"被 api/chat.py 当成正文推给前端，
# 还写进了 messages.content。表现就是行程 JSON 后面又跟一段 {"attractions": [...]}，
# 前端解析到第二段渲染不出卡片，整条消息退化成原始 JSON 文本。
llm_extract = ChatOpenAI(
    model=settings.deepseek_model,
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_base_url,
    streaming=False,
    timeout=settings.llm_timeout,
    max_retries=2,
    extra_body={"thinking": {"type": settings.deepseek_thinking}},
)
# DeepSeek 不支持 response_format={"type":"json_schema"}（langchain 对 pydantic 模型的默认做法），
# 直接调用会 400: "This response_format type is unavailable now"。
# 改走 function_calling，把 schema 作为 tool 定义下发——DeepSeek 官方支持 tool calls。
# 也不要用 json_mode：它要求提示词里出现 "json" 字样，且不保证 schema 完全匹配。
llm_with_structure = llm.with_structured_output(IntentOutput, method="function_calling")

tools = None
tools_by_name = {}
model_with_tools = None

_tools_lock = asyncio.Lock()
_tools_ready = False


async def initialized_tools():
    global tools, tools_by_name, model_with_tools
    tools = await getAllTools()  # 组成工具数组
    tools_by_name = {t.name: t for t in tools}
    model_with_tools = llm.bind_tools(tools)


async def ensure_tools():
    """确保 MCP 工具已加载（进程内只做一次）

    原来 classify_input 每条消息都调 initialized_tools()，要起两个 uvx 子进程
    重新拉工具清单并重新 bind_tools，实测 2.03s / 1.77s 一条消息——而意图分类
    本身走的是 llm_with_structure，根本用不到工具。
    改成「谁真正需要工具，谁在开头 await ensure_tools()」，并加锁保证并发请求
    不会同时初始化出两份。
    """
    global _tools_ready
    if _tools_ready:
        return
    async with _tools_lock:
        if _tools_ready:
            return
        await initialized_tools()
        _tools_ready = True
        print(f"🔧 MCP 工具初始化完成: {len(tools)} 个")


def parse_llm_json(text: str) -> dict:
    """解析 LLM 返回的 JSON，容忍 markdown 代码围栏和前后多余文本。

    DeepSeek 经常把 JSON 包进 ```json ... ```（实测必现），裸 json.loads 会抛
    JSONDecodeError，导致整条行程生成被 except 吞掉——表现为前端只看到原始文本、
    没有行程卡片和地图。这里按「原文 → 去围栏 → 截取最外层大括号」依次尝试。
    """
    if not isinstance(text, str):
        text = str(text)

    candidates = [text]
    stripped = text.strip()
    fence = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", stripped, re.S)
    if fence:
        candidates.append(fence.group(1))
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start:end + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue

    raise ValueError(f"无法解析为 JSON，原始内容前 200 字符：{text[:200]}")


async def classify_input(state: TripState):
    """分析用户的输入并路由到目标节点"""
    user_input = state["user_input"]
    # 注意：这里不需要 ensure_tools()。意图分类走 llm_with_structure，
    # 不 bind 任何工具；放在这里只会让每条消息白等 2 秒去拉 MCP 工具清单。
    system_prompt = """
       你是一个意图分类器。分析用户输入，判断意图类型。

       意图类型：
       - chat: 闲聊、问候和调用单一工具解决用户问题（天气、景点、交通、美食等）
       - format_input: 复杂任务（多天旅游规划、行程安排等）
       """

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"用户输入：{user_input}")
    ]
    result = await llm_with_structure.ainvoke(messages)
    state["current_step"] = result.intent
    state["response"] = None
    return state


async def intent_router(state: TripState) -> Literal["format_input", "chat"]:
    """根据意图路由到不同处理器"""
    return state.get("current_step")


def _as_number(value):
    """把 LLM 返回的天数/预算宽松地转成数字，转不了就当没提供。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(str(value).strip().rstrip("天元块人民币")))
        except (TypeError, ValueError):
            return None


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

    prompt = f"""
        分析用户的旅行需求，提取关键信息。

        已知信息（前几轮已确认，本轮没提到就沿用，不要自行编造）：
        - 目的地：{known_dest or "未提供"}
        - 天数：{known_days or "未提供"}
        - 预算：{known_budget or "未提供"}

        用户本轮输入：{state['user_input']}

        规则：
        1. 只输出用户本轮明确提到的字段；没提到的，destination 填空字符串、days 填 null、budget 填 null。
        2. days 必须是用户明确说过的天数，没提就填 null，不要默认成 3。
        3. destination 用 省名+市名，例如 辽宁省大连市。
        4. task_type 判断本轮和上一份行程的关系：
           - "modify"：在改/补充刚才的行程，包括"改成5天""换成便宜点的""对""3天"这类回答；
           - "new"：重新提出一次规划请求，例如"帮我规划个旅行""再给我排一版别的"。
           如果输入里没有任何指向上文行程的线索，一律填 "new"。

        请只返回如下的JSON格式（不要加代码围栏）：
        {{
            "task_type": "new",
            "destination": "",
            "days": null,
            "budget": null,
            "preferences": ["偏好1", "偏好2"],
            "start_date": "{dt_date.today().isoformat()}"
        }}
        """
    writer = config.get("configurable", {}).get("writer") if config else None
    if writer:
        writer({
            "type": "node_start",
            "node": "📋 分析需求",
            "message": "正在分析您的旅行需求..."
        })
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    content = response.content
    if isinstance(content, list):
        # 如果是列表，拼接成字符串
        content = " ".join(str(item) for item in content)
    dest_explicit = False   # 本轮输入里是否真的出现了目的地
    modifying = False       # 本轮是在修改上一份行程 / 回答追问，而不是开新任务
    try:
        print(f"📄 format_input 提取的 JSON: {content}")
        data = parse_llm_json(content)
        modifying = str(data.get("task_type") or "").strip().lower() == "modify"

        # 只覆盖本轮明确提到的值：用户这轮说"改成5天"时，destination 不能因为
        # LLM 返回空串就把上一轮的三亚清掉。
        new_dest = (data.get("destination") or "").strip()
        dest_explicit = bool(new_dest)
        state["destination"] = new_dest or known_dest

        new_days = _as_number(data.get("days"))
        state["days"] = new_days if new_days else known_days

        new_budget = _as_number(data.get("budget"))
        state["budget"] = new_budget if new_budget else (known_budget or 5000)

        state["preferences"] = data.get("preferences") or state.get("preferences") or []
        # 兼容旧字段名 start_data；缺失时保留调用方传入的 start_date（不要覆盖成 None）
        state["start_date"] = data.get("start_date") or data.get("start_data") or state.get("start_date")
    except Exception as e:
        state["error"] = "解析用户输入失败"
        print(f"format_input错误：{e}")

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

    # dest = state["destination"]
    # days = state["days"]
    # budget = state["budget"]
    # prefs = '、'.join(state["preferences"]) if state["preferences"] else "自由探索"
    #
    # state["response"] = (f"好的！我收到了您的旅行需求：\n\n📍 目的地：{dest}\n📅 天数：{days} 天\n💰 预算：{budget} 元\n🎯 "
    #                      f"偏好：{prefs}\n\n正在为您规划详细行程，请稍候...\n\n您也可以告诉我更多具体需求，比如：\n"
    #                      f"- 更想去哪些景点？\n- 住宿有什么要求？\n- 有没有特别想体验的活动？")

    state["current_step"] = "format_input"
    if writer:
        writer({
            "type": "node_end",
            "node": "📋 分析需求",
            "message": "需求分析完成"
        })
    return state


def _clarify_text(missing: List[str], state: Optional[TripState] = None) -> str:
    """按缺失的槽位挑一句追问。

    用模板而不是让 LLM 生成：追问场景只有这几种，模板零延迟、零成本、措辞可控；
    让 LLM 生成要多一次调用，还可能把待确认的信息说得更含糊。
    """
    state = state or {}

    # 情况 0：历史里有目的地，但本轮没提、也不是在补全追问
    # -> 不是"缺信息"，而是"要不要沿用"，措辞要把老答案摆出来让用户确认
    if "confirm_destination" in missing:
        dest = (state.get("destination") or "").strip()
        days = state.get("days")
        suffix = f"（{days} 天）" if days else ""
        return (f"上次规划的是 {dest}{suffix}，这次还是按它来吗？"
                f"想换城市或改天数，直接告诉我。")

    has_dest = "destination" not in missing
    has_days = "days" not in missing

    if not has_dest and not has_days:
        return "想去哪个城市？大概玩几天？还没定天数的话，我先按 3 天帮你排。"
    if not has_dest:
        return "想去哪个城市？"
    return "打算玩几天？没说我就先按 3 天排，后面随时能改。"


async def clarify(state: TripState) -> TripState:
    """关键信息不全时不硬猜，先问清楚再规划。

    只写 response / clarify_question，不动 destination 和 days：
    用户这一轮的补充会在下一轮由 format_input 合并到已有槽位上
    （已知值靠 checkpoint 带过去，前提是 format_input 不清空未提及的字段）。
    """
    missing = state.get("missing_fields") or []
    question = _clarify_text(missing, state)

    print(f"❓ clarify 追问：missing={missing} -> {question}")
    state["clarify_question"] = question
    state["response"] = question
    state["current_step"] = "clarify"
    # messages 带 add_messages reducer，这里是"追加一条"，不是覆盖历史
    state["messages"] = [AIMessage(content=question)]
    return state


def route_after_format(state: TripState):
    """format_input 之后的路由：缺关键槽位就短路到 clarify，否则并行扇出。

    条件边返回 list 就是动态 fan-out，和原来三条静态 add_edge 等价，
    只是多了一个"不满足条件时改走 clarify"的分支。
    """
    if state.get("missing_fields"):
        return ["clarify"]
    return ["retrieve_guides", "search_attractions", "get_weather"]


async def retrieve_guides(state: TripState, config: RunnableConfig = None):
    """从攻略库检索相关文档（RAG）"""
    writer = config.get("configurable", {}).get("writer") if config else None
    if writer:
        writer({
            "type": "node_start",
            "node": "📚 检索攻略",
            "message": "正在搜索相关旅游攻略..."
        })
    try:
        # days 现在允许为 None（表示用户没说，会先被 clarify 拦住），这里兜一次底
        query = f"去{state['destination']}玩{state.get('days') or 3}天，预算{state.get('budget')},喜欢{' '.join(state.get('preferences'))}"
        # retrieve_context 内部是同步的 embedding HTTP 调用，直接跑会阻塞事件循环
        # （SSE 心跳、其它请求一起卡住），丢到线程池里执行。
        # 只取已格式化的 context 文本：原来把 List[Document] 直接塞进 prompt，
        # 发给模型的是 `[Document(page_content='...', metadata={...})]` 的 Python repr。
        ctx = await asyncio.to_thread(retrieve_context, query, top_k=5)
        if writer:
            writer({
                "type": "node_end",
                "node": "📚 检索攻略",
                "message": f"找到 {len(ctx.get('docs') or [])} 篇攻略"
            })
        return {"similar_guides": ctx.get("context", "")}
    except Exception as e:
        print(f"错误，retrieve_guides出现错误{e}")
        # 不能只 print 后隐式返回 None：LangGraph 会把 None 当"空更新"直接放过，
        # 用户拿到一份没有攻略依据的行程，却不知道检索失败了。写进专门的键让前端可见。
        # 用 guides_error 而不是共用的 error：三个并行节点同时写 error 会 InvalidUpdateError。
        return {"guides_error": f"攻略检索失败: {e}"}


async def search_attractions_node(state: TripState, config: RunnableConfig = None):
    """搜索景点"""
    writer = config.get("configurable", {}).get("writer") if config else None
    if writer:
        writer({
            "type": "node_start",
            "node": "📚 搜索景点",
            "message": "正在搜索相关旅游景点..."
        })
    try:
        query = f"{state['destination']}的景点推荐,喜好：{' '.join(state.get('preferences'))}"
        ctx = await asyncio.to_thread(retrieve_context, query, top_k=5)
        if writer:
            writer({
                "type": "node_end",
                "node": "📚 搜索景点",
                "message": f"找到 {len(ctx.get('docs') or [])} 篇攻略"
            })
        return {"attractions_pool": ctx.get("context", "")}
    except Exception as e:
        print(f"错误，search_attractions_node出现错误:{e}")
        return {"attractions_error": f"景点检索失败: {e}"}


async def get_weather_node(state: TripState, config: RunnableConfig = None):
    """获取天气信息"""
    writer = config.get("configurable", {}).get("writer") if config else None
    if writer:
        writer({
            "type": "node_start",
            "node": "获取天气信息",
            "message": "正在获取天气信息..."
        })
    try:
        await ensure_tools()
        query = f"{state.get('destination')}的天气如何"
        response = await model_with_tools.ainvoke(query)
        # 原来直接 response.tool_calls[0]：模型没返回 tool_call 就 IndexError，
        # 被下面的 except 吞掉，天气永远是"未知"。
        if not response.tool_calls:
            print("⚠️ 天气节点：模型没有返回工具调用，跳过天气查询")
            return {"weather_info": None}
        tool_call = response.tool_calls[0]
        tool_name = tool_call['name']
        tool_args = tool_call['args']
        tool = tools_by_name.get(tool_name)
        if not tool:
            print(f"⚠️ 天气节点：工具 {tool_name} 不存在")
            return {"weather_info": None, "weather_error": f"天气工具 {tool_name} 不可用"}
        weather_result = await tool.ainvoke(tool_args)
        if writer:
            writer({
                "type": "node_end",
                "node": "获取天气信息",
                "message": "天气信息获取完毕..."
            })
        return {"weather_info": weather_result}
    except Exception as e:
        print(f"错误，get_weather_node出现错误:{e}")
        return {"weather_info": None, "weather_error": f"天气获取失败: {e}"}


async def generate_plan_node(state: TripState, config: RunnableConfig = None) -> TripState:
    """生成行程计划"""
    templet = """
{
  "title": "大连5日自由行",
  "destination": "辽宁省大连市",
  "totalDays": 5,
  "budget": 6000,
  "overview": "本次行程涵盖城市地标、海洋公园、异域风情和海滨休闲，适合喜欢自由探索的旅行者。",
  "days": [
    {
      "day": 1,
      "theme": "初识浪漫之都 · 城市地标与海滨漫步",
      "date": "9月1日",
      "weather": "晴转多云，25-30℃，海风3-4级，适合户外活动",
      "activities": [
        {
          "time": "09:00-10:30",
          "activity": "抵达大连，入住酒店（推荐青泥洼桥附近经济型酒店）",
          "description": "该活动的具体说明与实用提示",
          "cost": 180
        },
        {
          "time": "11:00-12:30",
          "activity": "游览中山广场，欣赏俄式、日式与欧式混合建筑群",
          "description": "该活动的具体说明与实用提示",
          "cost": 0
        },
        {
          "time": "12:30-13:30",
          "activity": "午餐：本地特色小吃店",
          "description": "推荐尝试焖子、海菜包子、咸鱼饼子等大连地道小吃，人均约30元。",
          "cost": 30
        },
        {
          "time": "14:00-17:00",
          "activity": "星海广场散步，欣赏跨海大桥，海边拍照",
          "description": "该活动的具体说明与实用提示",
          "cost": 0
        },
        {
          "time": "18:00-19:30",
          "activity": "晚餐：星海广场附近海鲜排档",
          "description": "推荐清蒸扇贝、辣炒蚬子、海胆蒸蛋，人均约80元。",
          "cost": 80
        },
      ],
      "notes": "实际总花费远低于6000元预算，预留充足空间用于升级餐饮、住宿或临时消费。所有价格基于普通游客标准，已考虑学生证优惠及经济型消费习惯。",
      "estimatedCost": 320
    },
  "tips": [
    "大连风大，建议携带薄外套",
    "海边紫外线强，注意防晒",
    "使用学生证可节省景点门票"
  ]
}
"""
    start_date_str = state.get('start_date') or dt_date.today().isoformat()

    prompt = f"""
    你是一个专业的旅行规划师。根据以下信息生成行程：

    目的地：{state.get('destination', '未知')}
    天数：{state.get('days') or 3} 天
    预算：{state.get('budget', 5000)} 元
    偏好：{', '.join(state.get('preferences', []))}
    天气：{state.get('weather_info', '未知')}
    开始日期：{start_date_str}

    参考攻略：
    {state.get('similar_guides') or '无'}

    可用景点：
    {state.get('attractions_pool') or '无'}

    请生成详细的每日行程，包含景点、餐饮、住宿建议。
    只输出一个 JSON 对象，不要加 ```json 代码围栏，不要输出任何解释文字。
    返回 JSON 格式，包含每天的具体安排,格式如下所示：{templet}
    """
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
    async for chunk in llm.astream([HumanMessage(content=prompt)], config=runnable_config):
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

        # raise SystemExit("调试结束")
        state["draft_plan"] = data.get("days", [])
        state["response"] = format_response(data)

        print("🗺️ 开始生成地图数据...plan_node")
        map_data = await generate_map_data(state, data)
        if map_data:
            print(f"✅ 地图数据生成成功: {len(map_data.get('attractions', []))} 个景点")
            state["map_data"] = map_data
            data["map_data"] = map_data

        if writer:
            print(f"📤 发送行程计划，包含地图数据: {bool(data.get('map_data'))}")
            writer({
                "type": "trip_plan",
                "plan": data
            })
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


async def extract_attractions_with_llm(state: TripState, plan_data: dict) -> List[Dict]:
    """
    使用 LLM 从行程计划中智能提取景点
    """
    # 构建行程描述
    plan_text = f"目的地：{state.get('destination', '')}\n\n"
    plan_text += "详细行程：\n"

    for day in plan_data.get("days", []):
        plan_text += f"\n第{day.get('day', 0)}天：\n"
        for activity in day.get("activities", []):
            name = activity.get("activity") or activity.get("name") or ""
            desc = activity.get("description", "")
            plan_text += f"  - {name}"
            if desc:
                plan_text += f"：{desc}"
            plan_text += "\n"

    prompt = f"""
    你是一个专业的旅行规划助手。请从以下行程中提取出所有**值得参观的景点/地标/公园/博物馆**。

    ## 行程信息
    {plan_text}

    ## 提取规则
    1. **只提取景点**：如公园、广场、博物馆、海滩、山、寺、塔、古城、步行街等
    2. **识别**：未给出明确地点则从最经地方随机选择一处街道
    3. **去重**：同一个景点只提取一次
    4. **别名**：如果景点有别名，使用标准名称

    ## 返回格式
    返回 JSON 数组，包含景点名称和它出现的天数：
    {{
        "attractions": [
            {{"name": "星海广场", "day": 1}},
            {{"name": "老虎滩海洋公园", "day": 1}},
            {{"name": "大连森林动物园", "day": 2}},
            {{"name": "棒棰岛", "day": 2}},
            {{"name": "金石滩", "day": 3}}
        ]
    }}

    **只返回 JSON，不要返回其他内容。**
    """

    try:
        print("🤖 使用 LLM 提取景点...")
        # 用 llm_extract（streaming=False）：这里的输出是给程序看的中转数据，
        # 不能进 SSE 正文，否则会跟在行程 JSON 后面一起推给前端。
        # internal 是第二道保险：万一以后有人图省事又改回 llm，api/chat.py 看到
        # 这个标记也会跳过，不会再漏进正文。
        response = await llm_extract.ainvoke(
            [HumanMessage(content=prompt)], config={"tags": ["internal"]}
        )
        content = response.content

        print(f"LLM 响应: {content[:20]}...")

        # 提取 JSON
        import re
        # 尝试匹配 ```json ... ``` 或直接的 JSON
        json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # 尝试直接匹配 JSON 对象
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                json_str = json_match.group()
            else:
                print("❌ 无法从 LLM 响应中提取 JSON")
                return []

        data = json.loads(json_str)
        attractions = data.get("attractions", [])

        # 转换为统一格式
        result = []
        for attr in attractions:
            result.append({
                "name": attr.get("name", "").strip(),
                "day": attr.get("day", 1),
                "time": "",
                "description": ""
            })

        # 过滤空名称
        result = [a for a in result if a["name"]]

        print(f"✅ LLM 提取到 {len(result)} 个景点: {[a['name'] for a in result]}")
        return result

    except Exception as e:
        print(f"❌ LLM 提取景点失败: {e}")
        import traceback
        traceback.print_exc()
        return []


AMAP_PLACE_TEXT_URL = "https://restapi.amap.com/v3/place/text"

# POI 名字里带这些词的通常是停车场、出入口之类的配套设施，不是景点本体
_POI_NOISE = ("停车场", "停车楼", "售票处", "出入口", "公厕", "卫生间",
              "地铁站", "公交站", "充电站", "管理中心")


def _pick_best_poi(name: str, pois: List[Dict]) -> Optional[Dict]:
    """从 POI 搜索结果里挑和目标名字最贴近的一条

    高德 REST 返回是按相关性排序的，所以给首位一点权重 —— 像"椰梦长廊"这种
    横跨 20km 的线性景点，首位（椰林海滩，中段）比"椰梦长廊灯塔"（最西端）更贴合行程。
    """
    best: Optional[Dict] = None
    best_score = -1
    for i, p in enumerate(pois):
        loc = p.get("location") or ""
        if not isinstance(loc, str) or "," not in loc:
            continue
        pname = p.get("name") or ""
        score = 0
        if pname == name:
            score += 100
        elif name and name in pname:
            # 包含但更长（"椰梦长廊" vs "椰梦长廊-椰林海滩"），长度差越大越不像本体
            score += 60 - max(0, len(pname) - len(name)) * 2
        elif pname and pname in name:
            score += 40
        else:
            score += 5
        if any(w in pname for w in _POI_NOISE):
            score -= 50
        if i == 0:
            score += 12
        if score > best_score:
            best_score, best = score, p
    return best if best_score > 0 else None


async def _poi_search_rest(name: str, city_name: str) -> Optional[Dict]:
    """高德 REST POI 关键词搜索，作为 maps_geo 的降级方案

    为什么要多这一步：maps_geo 是"地址→坐标"解析，遇到非规范名称（LLM 常自己编，
    比如把「三亚国际免税城」写成「三亚免税城」）时不报错，而是返回市/区县中心点。
    换成 POI 搜索就能命中「中免集团·三亚国际免税城」这类真实条目。
    走 REST 而不是 MCP 的原因：一次 HTTP 约 50ms，而每次 MCP 工具调用都要拉起子进程（约 1s）。
    """
    key = getattr(settings, "amap_api_key", None)
    if not key or not name:
        return None

    params = {"key": key, "keywords": name, "offset": 10, "page": 1, "extensions": "base"}
    if city_name:
        params["city"] = city_name
        params["citylimit"] = "true"

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(AMAP_PLACE_TEXT_URL, params=params)
            data = resp.json()
    except Exception as e:
        print(f"  ⚠️ POI 搜索请求失败: {e}")
        return None

    if str(data.get("status")) != "1":
        print(f"  ⚠️ POI 搜索返回异常: {data.get('info')} ({data.get('infocode')})")
        return None

    pois = data.get("pois") or []
    if not pois:
        return None

    best = _pick_best_poi(name, pois)
    if not best:
        return None
    lng_str, lat_str = best["location"].split(",")[:2]
    print(f"  🔎 POI 搜索命中: {best.get('name')!r} "
          f"({best.get('adname') or ''}) -> {lng_str},{lat_str}")
    return {"lng": float(lng_str), "lat": float(lat_str)}


async def geocode_attraction(geo_tool, name: str, city_name: str,
                             idx: int = 0, total: int = 0) -> Optional[Dict]:
    """查单个景点的精确坐标，查不到返回 None（不编造坐标）

    高德对不规范的名字会静默返回"城市中心点"而不是报错，所以这里用
    parse_coordinates 的行政区级校验把那种结果挡掉，再逐级降级：
      1. 地理编码：城市名 + 景点名（景点名已含城市名时不重复拼）
      2. REST POI 关键词搜索（能命中非规范名称，比如"椰梦长廊-椰林海滩"）
      3. 裸景点名再走一次地理编码（"椰梦长廊"这种本地名带城市反而查不到）
    三级都失败才交给上层做估算排布。
    """
    candidates = []
    base = name
    if city_name and not base.startswith(city_name):
        base = f"{city_name}{name}"
    candidates.append(base)

    # 1) 组合名地理编码
    if geo_tool:
        try:
            print(f"📍 [{idx}/{total}] 地理编码 {name}（搜索: {base}）...")
            result = await geo_tool.ainvoke({"address": base, "city": city_name or None})
            coords = parse_coordinates(result)
            if coords:
                print(f"  ✅ 坐标: {coords['lng']}, {coords['lat']}")
                return coords
            print(f"  ⚠️ 「{base}」未定位到具体地点")
        except Exception as e:
            print(f"  ❌ 地理编码「{base}」异常: {e}")

    # 2) POI 关键词搜索兜底
    coords = await _poi_search_rest(name, city_name)
    if coords:
        return coords

    # 3) 裸名地理编码
    if geo_tool and base != name:
        try:
            print(f"📍 [{idx}/{total}] 地理编码 {name}（裸名: {name}）...")
            result = await geo_tool.ainvoke({"address": name, "city": city_name or None})
            coords = parse_coordinates(result)
            if coords:
                print(f"  ✅ 坐标: {coords['lng']}, {coords['lat']}")
                return coords
            print(f"  ⚠️ 「{name}」未定位到具体地点")
        except Exception as e:
            print(f"  ❌ 地理编码「{name}」异常: {e}")

    print(f"  ⚠️ {name} 三级查询均失败，将使用估算坐标")
    return None


def _scatter_around(center: Tuple[float, float], index: int, total: int,
                    radius: float = 0.012) -> Tuple[float, float]:
    """在中心点周围按环形均匀撒点，用于给定位失败的景点一个"看得见但不精确"的位置

    半径 0.012 度约 1.3km，视觉上能分开又不至于跑到城外。
    经度方向按 cos(lat) 修正，否则高纬度地区环会被压扁。
    """
    import math

    angle = 2 * math.pi * index / max(total, 1)
    lng = center[0] + radius * math.cos(angle) / max(math.cos(math.radians(center[1])), 0.2)
    lat = center[1] + radius * math.sin(angle)
    return round(lng, 6), round(lat, 6)


async def _resolve_center_base(located: List[Dict], geo_tool, city_name: str,
                               fallback: Tuple[float, float]) -> Tuple[float, float]:
    """给"定位失败的景点"找一个合理的估算基准

    优先级：本次已成功定位的景点均值 > 高德对城市本身的地理编码 > .env 兜底。
    以前直接用 .env 的 DEFAULT_MAP_LNG/LAT，那些值是大连的，三亚的景点会被画到大连去。
    """
    if located:
        return (
            round(sum(a["lng"] for a in located) / len(located), 6),
            round(sum(a["lat"] for a in located) / len(located), 6),
        )
    if geo_tool and city_name:
        try:
            # allow_admin=True：这里要的就是城市中心点
            result = await geo_tool.ainvoke({"address": city_name, "city": city_name})
            coords = parse_coordinates(result, allow_admin=True)
            if coords:
                print(f"📌 城市中心（{city_name}）: {coords['lng']}, {coords['lat']}")
                return coords["lng"], coords["lat"]
        except Exception as e:
            print(f"⚠️ 获取城市中心失败: {e}")
    print(f"⚠️ 拿不到城市中心，回退到 .env 兜底坐标 {fallback}")
    return fallback


async def generate_map_data(state: TripState, plan_data: dict) -> Dict:
    """生成地图数据：景点坐标和路线"""
    try:
        print("🗺️ 开始生成地图数据...map_data")
        # 地图数据依赖 maps_geo / 驾车路线的工具对象，这里保证工具已加载
        await ensure_tools()
        # 1. 提取所有景点
        attractions = await extract_attractions_with_llm(state, plan_data)
        print(f"📋 共 {len(attractions)} 个景点待获取坐标")

        # 2. 获取景点坐标（使用高德 MCP）
        geo_tool = tools_by_name.get("maps_geo")
        attractions_with_coords = []

        # 地理编码失败时的兜底坐标（见 .env 的 DEFAULT_MAP_LNG/LAT）
        base_lng, base_lat = settings.default_map_lng, settings.default_map_lat
        # 提取城市名（用于限定地理搜索范围）
        destination = state.get("destination", "")
        city_name = destination
        if "省" in destination:
            parts = destination.split("省")
            if len(parts) > 1:
                city_name = parts[1]
        if "市" in city_name:
            city_name = city_name.split("市")[0]
        city_name = city_name.strip()

        # 精确定位成功的和失败的先分开，失败的最后统一做估算排布
        located: List[Dict] = []
        failed: List[Dict] = []

        for idx, attr in enumerate(attractions):
            coords = None
            if geo_tool:
                coords = await geocode_attraction(
                    geo_tool, attr["name"], city_name, idx + 1, len(attractions)
                )
            else:
                print("  ⚠️ geo_tool 不存在，所有景点都将使用估算坐标")

            if coords:
                located.append({**attr, "lng": coords["lng"], "lat": coords["lat"]})
            else:
                failed.append(attr)

        # 定位失败的景点：围绕城市中心环形散开，并打 approx 标记
        # （前端据此用虚线/灰色样式提示"位置是估算的"，不再让它顶替掉真实景点）
        if failed:
            center_base = await _resolve_center_base(
                located, geo_tool, city_name, (base_lng, base_lat)
            )
            print(f"⚠️ {len(failed)} 个景点未精确定位，围绕 {center_base} 估算排布: "
                  f"{[a['name'] for a in failed]}")
            for i, attr in enumerate(failed):
                lng, lat = _scatter_around(center_base, i, len(failed))
                located.append({**attr, "lng": lng, "lat": lat, "approx": True})

        attractions_with_coords = located
        print(f"🔍 坐标获取完成: {len(attractions_with_coords)} 个景点"
              f"（精确定位 {len(located) - len(failed)} 个，估算 {len(failed)} 个）")

        # 3. 规划路线（如果有多个景点）
        routes = []
        if len(attractions_with_coords) >= 2:
            for i in range(len(attractions_with_coords) - 1):
                origin = attractions_with_coords[i]
                dest = attractions_with_coords[i + 1]

                # ✅ 关键：只在同一天内生成路线，跳过跨天
                origin_day = origin.get("day", 1)
                dest_day = dest.get("day", 1)
                if origin_day != dest_day:
                    print(f"⏭️ 跳过跨天路线: {origin['name']}(Day{origin_day}) → {dest['name']}(Day{dest_day})")
                    continue

                try:
                    print(f"🚗 规划路线: {origin['name']} → {dest['name']}")
                    route_info = await plan_driving_route(origin, dest)
                    routes.append({
                        "from": origin["name"],
                        "to": dest["name"],
                        "from_coord": [origin["lng"], origin["lat"]],
                        "to_coord": [dest["lng"], dest["lat"]],
                        "distance": route_info.get("distance", 0),
                        "duration": route_info.get("duration", 0),
                        "polyline": route_info.get("polyline", [])
                    })
                    print(f"  ✅ 路线规划完成: {route_info.get('distance', 0)}m / "
                          f"{route_info.get('duration', 0)}s / "
                          f"折线 {len(route_info.get('polyline') or [])} 点")
                except Exception as e:
                    print(f"路线规划失败: {e}")
                    routes.append({
                        "from": origin["name"],
                        "to": dest["name"],
                        "from_coord": [origin["lng"], origin["lat"]],
                        "to_coord": [dest["lng"], dest["lat"]],
                        "distance": 0,
                        "duration": 0,
                        "polyline": []
                    })

        existing_names = {a["name"] for a in attractions_with_coords}

        for route in routes:
            for point in [route["from"], route["to"]]:
                if point not in existing_names:
                    # 用路线的端点坐标补一个 attraction
                    coord = route["from_coord"] if point == route["from"] else route["to_coord"]
                    attractions_with_coords.append({
                        "name": point,
                        "day": 1,
                        "lng": coord[0],
                        "lat": coord[1]
                    })
                    existing_names.add(point)
                    print(f"➕ 补全景点: {point} ({coord})")
        # 地图中心取精确坐标的均值（估算点不参与，否则会把中心拉偏）
        precise = [a for a in attractions_with_coords if not a.get("approx")]
        center_source = precise or attractions_with_coords
        if center_source:
            center = (
                round(sum(a["lng"] for a in center_source) / len(center_source), 6),
                round(sum(a["lat"] for a in center_source) / len(center_source), 6),
            )
        else:
            center = (base_lng, base_lat)

        result = {
            "attractions": attractions_with_coords,
            "routes": routes,
            "destination": state.get("destination", ""),
            "center": center
        }
        print(f"✅ 地图数据生成完成: {len(result['attractions'])} 个景点, {len(result['routes'])} 段路线")
        return result

    except Exception as e:
        print(f"生成地图数据失败: {e}")
        import traceback
        traceback.print_exc()
        return {}


def _unwrap_mcp_result(result: Any) -> Optional[Dict]:
    """把 MCP 工具的返回值还原成 dict。

    langchain-mcp-adapters 不会把工具返回的 dict 原样交出来，而是序列化成
    ``[{'type': 'text', 'text': '{"route": ...}'}]``（多个内容块就是多个元素）；
    这里同时兼容：直接 dict、JSON 字符串、ToolMessage（带 .content）。
    """
    if result is None:
        return None
    if isinstance(result, dict):
        return result
    # ToolMessage 之类的消息对象：取 content 继续往下走
    if not isinstance(result, (list, tuple, str, bytes)) and hasattr(result, "content"):
        result = result.content
    if isinstance(result, bytes):
        result = result.decode("utf-8", "ignore")
    if isinstance(result, (list, tuple)):
        texts: List[str] = []
        structured: List[Dict] = []
        for item in result:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict):
                if "text" in item and item.get("type") in (None, "text"):
                    texts.append(str(item.get("text") or ""))
                else:
                    structured.append(item)   # 本身就已经是结构化 payload
        if texts:
            result = "".join(texts)
        elif len(structured) == 1:
            return structured[0]
        else:
            return None
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _parse_polyline(raw: Any) -> List[List[float]]:
    """把高德折线转成前端要的 [[lng, lat], ...]。

    高德 v3 驾车接口的折线在 ``paths[0]["path"]`` 里，形如 "116.1,39.1;116.2,39.2"，
    **没有** ``polyline`` 字段。老代码取的是 path.get("polyline")，永远拿不到，
    于是静默变成 []，前端只能退化成起终点之间的一条虚线（看起来"只有点没有路线"）。
    这里同时兼容字符串、已解析的数组、以及 step 级别的 polyline 片段。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        points: List[List[float]] = []
        for pair in raw.split(";"):
            parts = pair.strip().split(",")
            if len(parts) < 2:
                continue
            try:
                lng, lat = float(parts[0]), float(parts[1])
            except (TypeError, ValueError):
                continue
            if -180.0 <= lng <= 180.0 and -90.0 <= lat <= 90.0:
                points.append([round(lng, 6), round(lat, 6)])
        return points
    if isinstance(raw, (list, tuple)):
        points = []
        for item in raw:
            if isinstance(item, str):
                points.extend(_parse_polyline(item))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    lng, lat = float(item[0]), float(item[1])
                except (TypeError, ValueError):
                    continue
                if -180.0 <= lng <= 180.0 and -90.0 <= lat <= 90.0:
                    points.append([round(lng, 6), round(lat, 6)])
        return points
    return []


AMAP_DRIVING_URL = "https://restapi.amap.com/v3/direction/driving"


async def _driving_route_rest(origin: Dict, dest: Dict) -> Optional[Dict]:
    """高德 Web 服务 REST 驾车路径规划（**带完整折线**）

    为什么不直接用 MCP 的 maps_direction_driving_by_coordinates：
    那个包把 steps 精简成 instruction/road/distance 几个字段，把折线 `polyline` 丢了，
    path 级又只有 distance/duration —— 于是前端永远拿不到 polyline，
    只能退化成起终点之间的一条虚线（看起来"地图上只有点、没有路线"）。
    REST 直连一次 HTTP 约 50~150ms，比拉 MCP 子进程还快。
    """
    key = getattr(settings, "amap_api_key", None)
    if not key:
        return None

    try:
        params = {
            "key": key,
            "origin": f"{float(origin['lng']):.6f},{float(origin['lat']):.6f}",
            "destination": f"{float(dest['lng']):.6f},{float(dest['lat']):.6f}",
            "extensions": "base",
        }
    except (KeyError, TypeError, ValueError):
        return None

    # 高德对并发有 QPS 限制（个人 Key 很低），连续请求会回 10021，
    # 所以这里遇到 QPS 超限退避重试几次，别直接把这条路线丢掉。
    data: Dict = {}
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(AMAP_DRIVING_URL, params=params)
                data = resp.json()
        except Exception as e:
            print(f"  ⚠️ 驾车路线 REST 请求失败: {e}")
            data = {}
        if str(data.get("status")) == "1":
            break
        if str(data.get("infocode")) == "10021" and attempt < 2:
            await asyncio.sleep(0.5 * (attempt + 1))
            continue
        print(f"  ⚠️ 驾车路线 REST 返回异常: {data.get('info')} ({data.get('infocode')})")
        return None

    if str(data.get("status")) != "1":
        return None

    # extensions=base 时 steps 里也带 polyline；parse_route_result 会按顺序拼起来
    return parse_route_result(data)


async def plan_driving_route(origin: Dict, dest: Dict) -> Dict:
    """一段驾车路线：REST 优先（有折线），失败再退回 MCP（只有距离/时长）。"""
    info = await _driving_route_rest(origin, dest)
    if info and info.get("polyline"):
        return info

    driving_tool = tools_by_name.get("maps_direction_driving_by_coordinates")
    if driving_tool:
        try:
            result = await driving_tool.ainvoke({
                "origin": f"{origin['lng']},{origin['lat']}",
                "destination": f"{dest['lng']},{dest['lat']}"
            })
            mcp_info = parse_route_result(result)
            # MCP 只补距离/时长；折线还是以 REST 的为准（MCP 那份是空的）
            if mcp_info.get("distance") or mcp_info.get("duration"):
                if not info:
                    return mcp_info
                info["distance"] = info.get("distance") or mcp_info["distance"]
                info["duration"] = info.get("duration") or mcp_info["duration"]
        except Exception as e:
            print(f"  ⚠️ MCP 备用路线也失败: {e}")

    return info or {"distance": 0, "duration": 0, "polyline": []}


def parse_route_result(result: Any) -> Dict:
    """解析高德驾车路径规划结果。

    统一输出 {"distance": 米, "duration": 秒, "polyline": [[lng, lat], ...]}。
    解析不出来时**打印原因**，不再静默返回 0 / 空折线 —— 之前就是这样
    既看不到路线，日志还写着"路线规划完成"。
    """
    empty = {"distance": 0, "duration": 0, "polyline": []}
    payload = _unwrap_mcp_result(result)
    if not isinstance(payload, dict):
        print(f"  ⚠️ 路线结果无法解析（{type(result).__name__}）: {str(result)[:200]}")
        return empty
    if payload.get("error"):
        print(f"  ⚠️ 路线接口报错: {payload['error']}")
        return empty

    route = payload.get("route") if isinstance(payload.get("route"), dict) else payload
    paths = route.get("paths") or []
    if not paths and ("path" in route or "distance" in route):
        paths = [route]
    if not paths:
        print(f"  ⚠️ 路线结果里没有 paths，字段: {list(payload)[:8]}")
        return empty

    path = paths[0]
    distance = _as_number(path.get("distance")) or 0
    duration = _as_number(path.get("duration")) or 0
    polyline = _parse_polyline(path.get("path") or path.get("polyline"))

    # 折线主要来源：steps[].polyline（高德 v3 驾车实测就只有这里），首尾相接拼起来
    if not polyline:
        chunks = [
            step.get("polyline") or step.get("path") or ""
            for step in (path.get("steps") or [])
            if isinstance(step, dict)
        ]
        joined = _parse_polyline(";".join(c for c in chunks if isinstance(c, str)))
        # 相邻路段首尾点会重复，去一下重（否则前端折线有几段会多出一个重复点）
        for point in joined:
            if not polyline or polyline[-1] != point:
                polyline.append(point)

    if not polyline:
        print(f"  ⚠️ 拿到距离 {distance}m 但没有折线，可用字段: {list(path)[:8]}")
    return {"distance": distance, "duration": duration, "polyline": polyline}


# 高德地理编码结果里的"行政区级" level 取值。这类结果的 location 是城市/区县中心点，
# 不是具体景点坐标。以前不校验它，导致查不到的景点全部落到同一个城市中心点上
# （三亚"椰梦长廊"和"三亚免税城"就是这样重合的，地图上只看得见后画的那个点）。
# 注意高德的取值是"省"/"市"/"区县"，不是"省份"/"城市"，实测得到的。
_ADMIN_LEVELS = {"国家", "省", "省份", "市", "城市", "区县", "开发区"}


def _is_admin_level(item: Dict) -> bool:
    """判断一条 geocode 结果是否只定位到行政区，而非具体地点"""
    if not isinstance(item, dict):
        return False
    level = (item.get("level") or "").strip()
    if level in _ADMIN_LEVELS:
        return True
    if not level:
        # 老接口没有 level 字段，用 district 兜底：真实 POI 一定有区县归属
        return not item.get("district")
    return False


def parse_coordinates(result, allow_admin: bool = False) -> Dict:
    """解析高德地理编码结果（适配 MCP 返回格式）

    Args:
        result: MCP 返回结果
        allow_admin: 是否接受"只定位到行政区"的结果。
            False（默认）：景点用，行政区级结果直接丢弃，交给上层做估算兜底；
            True：需要城市中心点时用（例如给定位失败的景点找估算基准）。
    """
    import json
    import re

    try:
        if not result:
            return {}

        # ✅ 情况1：MCP 返回格式 list[{'type': 'text', 'text': '{...}'}]
        if isinstance(result, list) and len(result) > 0:
            item = result[0]
            if isinstance(item, dict) and "text" in item:
                text = item["text"]
                parsed = False
                try:
                    data = json.loads(text)
                    parsed = True
                    coords = extract_location_from_dict(data, allow_admin=allow_admin)
                    if coords:
                        return coords
                except json.JSONDecodeError:
                    pass
                # 注意：JSON 解析成功但被校验拒绝时，绝不能再用正则兜底 ——
                # 正则会从同一段文本里把那个"行政区中心点"又抓出来，校验就白做了。
                if parsed:
                    return {}
                return extract_location_from_text(text)

        # ✅ 情况2：字符串（可能是 JSON 字符串）
        if isinstance(result, str):
            parsed = False
            try:
                data = json.loads(result)
                parsed = True
                coords = extract_location_from_dict(data, allow_admin=allow_admin)
                if coords:
                    return coords
            except json.JSONDecodeError:
                pass
            if parsed:
                return {}
            return extract_location_from_text(result)

        # ✅ 情况3：dict（兼容旧格式）
        if isinstance(result, dict):
            coords = extract_location_from_dict(result, allow_admin=allow_admin)
            if coords:
                return coords

    except Exception as e:
        print(f"解析坐标失败: {e}")

    return {}


def extract_location_from_dict(data, allow_admin: bool = False) -> Dict:
    """从 dict 中提取 location

    allow_admin=False 时，只定位到行政区（level=城市/区县）的结果会被丢弃，
    避免把城市中心当成景点坐标。
    """
    try:
        if isinstance(data, dict):
            # 高德 MCP 返回：{"return": [{"location": "121.63,38.92", ...}]}
            returns = data.get("return", [])
            if returns and len(returns) > 0:
                item = returns[0]
                if not allow_admin and _is_admin_level(item):
                    print(f"   ⚠️ 只定位到行政区（level={item.get('level')!r}, "
                          f"district={item.get('district')}），这个坐标不是景点，丢弃")
                    return {}
                loc = item.get("location", "")
                if loc and "," in loc:
                    parts = loc.split(",")
                    return {"lng": float(parts[0]), "lat": float(parts[1])}

            # 兼容旧格式：{"geocodes": [{"location": "..."}]}
            geocodes = data.get("geocodes", [])
            if geocodes and len(geocodes) > 0:
                item = geocodes[0]
                if not allow_admin and _is_admin_level(item):
                    return {}
                loc = item.get("location", "")
                if loc and "," in loc:
                    parts = loc.split(",")
                    return {"lng": float(parts[0]), "lat": float(parts[1])}

            # 直接有 location
            if "location" in data:
                loc = data["location"]
                if isinstance(loc, str) and "," in loc:
                    parts = loc.split(",")
                    return {"lng": float(parts[0]), "lat": float(parts[1])}
    except Exception as e:
        print(f"extract_location_from_dict 异常: {e}")
    return {}


def extract_location_from_text(text: str) -> Dict:
    """从文本中用正则提取坐标"""
    try:
        # 匹配 "location": "121.639890,38.920099"
        match = re.search(r'"location"\s*:\s*"([\d.]+),([\d.]+)"', text)
        if match:
            return {"lng": float(match.group(1)), "lat": float(match.group(2))}
        # 兜底
        match = re.search(r'(\d+\.\d+),(\d+\.\d+)', text)
        if match:
            return {"lng": float(match.group(1)), "lat": float(match.group(2))}
    except Exception as e:
        print(f"extract_location_from_text 异常: {e}")
    return {}


def format_response(plan_data: dict) -> str:
    """格式化响应文本"""
    text = f"根据您的需求，我为您规划了以下行程：\n\n"
    for day in plan_data.get("days", []):
        text += f"📅 **Day {day.get('day')}**: "
        attractions = day.get("attractions", [])
        if attractions:
            text += " → ".join([a.get("name", "") for a in attractions])
        text += "\n"

    return text


async def chat(state: TripState, config: RunnableConfig = None):
    """对话节点，如果是正常聊天就正常调用返回对话，如果需要调用工具则调用工具"""
    await ensure_tools()
    messages = state.get("messages", [])
    writer = config.get("configurable", {}).get("writer") if config else None
    if writer:
        writer({
            "type": "node_start",
            "node": "💬 AI 思考中",
            "message": "正在生成回复..."
        })
    full_response = None
    # user_visible 同 generate_plan：这一次调用的 token 是给用户看的正文。
    # 注意这里是重新赋值 config，会把函数参数里可能带的 tags 丢掉，所以必须显式写。
    config: RunnableConfig = {
        "configurable": {"thread_id": state.get("thread_id")},
        "tags": ["user_visible"],
    }
    async for chunk in model_with_tools.astream(messages, config=config):

        if full_response is None:
            full_response = chunk
        else:
            full_response += chunk

        if chunk.content and not chunk.tool_calls:
            if writer:
                writer({"type": "chunk", "content": chunk.content})

    if writer:
        writer({
            "type": "node_end",
            "node": "AI 思考完成",
            "message": "回复完成"
        })
    if full_response and full_response.tool_calls:
        return {"messages": [full_response]}
    else:
        return {"response": full_response.content, "messages": [AIMessage(content=full_response.content)]}


async def tool_node(state: TripState):
    """工具节点：执行工具调用，如果需要继续则返回工具调用"""
    last_message = state["messages"][-1]

    if not hasattr(last_message, 'tool_calls') or not last_message.tool_calls:
        return {"response": "没有工具调用"}

    tool_calls = last_message.tool_calls
    print(f"🔧 需要执行 {len(tool_calls)} 个工具调用")

    results = []

    for i, tool_call in enumerate(tool_calls):
        print(f"🔧 执行工具 {i + 1}/{len(tool_calls)}: {tool_call['name']}")

        tool = tools_by_name.get(tool_call["name"])
        if not tool:
            error_msg = f"工具 {tool_call['name']} 不存在"
            print(f"❌ {error_msg}")
            results.append(ToolMessage(
                content=error_msg,
                tool_call_id=tool_call["id"]
            ))
            continue

        try:
            # 执行工具
            if hasattr(tool, 'ainvoke'):
                print(f"参数为：{tool_call['args']}")
                result = await tool.ainvoke(tool_call["args"])
            else:
                print(f"参数为：{tool_call['args']}")
                result = tool.invoke(tool_call["args"])

            print(f"✅ 工具 {i + 1} 执行完成")

            results.append(ToolMessage(
                content=str(result) if result else "执行完成",
                tool_call_id=tool_call["id"]
            ))

        except Exception as e:
            error_msg = f"工具执行失败: {str(e)}"
            print(f"❌ {error_msg}")
            results.append(ToolMessage(
                content=error_msg,
                tool_call_id=tool_call["id"]
            ))

    # 如果所有工具都执行完成，生成最终回复
    all_messages = state.get("messages", []) + results

    try:
        # 调用 LLM 生成最终回复（不带工具）。这条是用户真正看到的正文，
        # 同样要打 user_visible——否则走工具的那一轮，最终回复会被 chat.py 过滤掉。
        final_response = await llm.ainvoke(
            all_messages, config={"tags": ["user_visible"]}
        )
        final_content = final_response.content if hasattr(final_response, 'content') else str(final_response)

        print(f"📝 最终回复: {final_content[:100]}...")

        return {
            "messages": results + [AIMessage(content=final_content)],
            "response": final_content
        }
    except Exception as e:
        print(f"❌ 生成最终回复失败: {e}")
        # 如果生成失败，合并工具结果
        tool_results = "\n".join([r.content for r in results if r.content])
        return {
            "messages": results,
            "response": tool_results
        }


def should_continue(state: TripState) -> Literal["tool_node", END]:
    """判断是否需要调用工具"""
    messages = state.get("messages", [])
    if not messages:
        return END
    last_message = messages[-1]
    # 检查是否有工具调用
    if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
        print(f"下一个节点是工具节点 (有 {len(last_message.tool_calls)} 个工具调用)")
        return "tool_node"
    return END
