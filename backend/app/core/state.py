# author: szw
from typing import Annotated, List, Dict, Optional, TypedDict, Any, Literal
from typing_extensions import NotRequired
from pydantic import Field, BaseModel
from langgraph.graph.message import add_messages


class IntentOutput(BaseModel):
    intent: Literal["chat", "format_input"] = Field(description="用户意图类型")


class Activity(TypedDict):
    """单日活动条目。

    字段必须和 core/prompts/plan.py 里 PLAN_TEMPLATE 完全一致——模型就是照
    那个模板吐 JSON 的。曾经的版本在这里写了 name/location/rating/category，
    那是"景点"的模型，而实际上一天里除了景点还有午餐、返程、买水果，
    模型输出的是 time/activity/description/cost。两者从未对齐过，
    照着类型定义读数据只会读到空。tests/test_plan_structure.py 锁住了这一致性。
    """
    time: str           # "09:00-10:30"，排程节点会整段重算
    activity: str       # 活动描述，含地点全名，地图靠它取坐标
    description: str    # 说明与实用提示
    cost: float         # 该条花费（元）


class DayPlan(TypedDict):
    day: int
    theme: str          # 当天主题，如"初识浪漫之都 · 城市地标与海滨漫步"
    date: str
    weather: str
    activities: List[Activity]
    notes: str
    # 驼峰是为了跟模型输出对齐，改名会让每条都要做一次映射，不值得
    estimatedCost: float


class TripState(TypedDict):
    """LangGraph 状态
        用户输入 目的地 天数 预算 偏好 起始日期
    """
    # 用户输入 目的地 天数 预算 偏好
    user_input: str
    destination: Optional[str]
    days: int
    budget: float
    preferences: List[str]
    start_date: Optional[str]

    # 中间状态
    weather_info: Optional[Dict]
    attractions_pool: str
    hotel_options: List[Dict]
    budget_analysis: Dict

    # RAG 检索结果
    # 存"已格式化的纯文本"而不是 List[Document]：把 Document 列表直接 f-string 进 prompt，
    # 发给模型的是 `[Document(page_content='...', metadata={...})]` 的 Python repr，
    # 白烧 token 还干扰模型理解。
    similar_guides: str

    # 对话相关
    thread_id: Optional[str]
    # user_id：检索用户自己上传的资料时要按它过滤。只能从登录令牌注入，
    # 不能来自请求体——否则任何人都能指定别人的 user_id 去检索别人的资料。
    user_id: NotRequired[Optional[str]]
    # 必须带 add_messages reducer：不带 reducer 时节点返回 {"messages": [...]}
    # 是"整体覆盖"，chat 节点一返回带 tool_calls 的 AI 消息就会把历史和本轮用户
    # 问题一起清掉，tool_node 拿到的只剩 [AI(tool_calls)]，最终回复不知道用户问了什么。
    # 带 reducer 后各节点只需返回"新增的消息"，历史自动保留。
    messages: Annotated[List[Any], add_messages]

    # 输出
    # 只有 draft_plan，没有 final_plan。排程、质检都在原地改写 draft_plan，
    # 质检通过后它就是最终版。曾经另有 final_plan 字段但没有任何节点写过它，
    # 读的人（包括我自己）按字面去取只能拿到 None。
    draft_plan: Optional[List[DayPlan]]
    response: str
    map_data: Dict

    # 流程控制 目前状态 错误情况 用户反馈
    current_step: str
    # 关键槽位缺失时走 clarify 分支，不进入规划链路。
    # 目前只拦 destination / days：缺目的地规划出来毫无意义，缺天数也没法排逐日行程；
    # budget 缺了就用默认值，追问预算会让对话变成填表，体验更差。
    missing_fields: List[str]
    # clarify 节点要追问的话术（模板生成，不调 LLM）
    clarify_question: Optional[str]

    # ===== HITL：提交规划前的人工确认 =====
    # need_confirm：这一轮要不要在扇出检索之前停下来问用户一句。
    #   本轮明确给了目的地（可能是新需求/换城市）、或刚接完 clarify 追问 → 要停；
    #   纯粹改天数/预算（"改成5天"）→ 不打断，否则每次微调都要点一下确认。
    need_confirm: NotRequired[bool]
    # confirmed：confirm_needs 的结论。False 表示用户在确认卡上点了"算了"。
    confirmed: NotRequired[bool]

    # ===== 自我反思：生成 → 质检 →（不过则带 feedback 重排一次）=====
    # review_passed：评审是否放行。True 才会去生成地图并推给前端。
    review_passed: NotRequired[bool]
    # review_issues：具体毛病，给用户/日志看（"天数不对""预算超了"）。
    review_issues: NotRequired[List[str]]
    # review_feedback：给规划师看的具体修改指令，重排时拼进 prompt。
    review_feedback: NotRequired[str]
    # review_round：已经重排过几次。到上限就不再回环，避免无限"写-批-改"。
    review_round: NotRequired[int]
    # plan_tips：行程 JSON 里的 tips，落库/推送 trip_plan 时要带上
    plan_tips: NotRequired[List[str]]

    # ===== 排程优化：把"几点到几点"从 LLM 手里拿回来 =====
    # schedule_dropped：按真实通勤时间重排后当天塞不下的活动。有值必须告诉用户
    #   "这个点没排进去"，而不是让他到了当地才发现。
    schedule_dropped: NotRequired[List[Dict]]
    # schedule_stats：重排后的短间隔占比与估算通勤时长，写进运行摘要用于前后对比。
    schedule_stats: NotRequired[Dict]
    # review_error：质检自己挂了（LLM 超时/解析失败）。只告警，不阻断行程
    review_error: Optional[str]
    # review_conflicts：用户约束自相矛盾之处（要求一天跨两城等）。
    # 有值时直接放行——这类问题重排也解决不了——同时把矛盾写进 plan_tips 提示用户。
    review_conflicts: NotRequired[List[str]]
    # 顺序节点（format_input / generate_plan）的失败原因
    error: Optional[str]
    # 三个并行节点的失败原因必须各用一个键：它们在同一 superstep 里执行，
    # 同时写同一个 key 会抛
    # InvalidUpdateError: At key 'error': Can receive only one value per step。
    guides_error: Optional[str]
    attractions_error: Optional[str]
    weather_error: Optional[str]
    user_feedback: Optional[str]
    _events: NotRequired[List[Dict[str, Any]]]
