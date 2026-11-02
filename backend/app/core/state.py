# author: szw
from typing import Annotated, List, Dict, Optional, TypedDict, Any, Literal
from typing_extensions import NotRequired
from pydantic import Field, BaseModel
from langgraph.graph.message import add_messages


class IntentOutput(BaseModel):
    intent: Literal["chat", "format_input"] = Field(description="用户意图类型")


class Attraction(TypedDict):
    name: str
    location: str
    rating: float
    description: str
    visit_duration: int
    category: str


class DayPlan(TypedDict):
    day: int
    attractions: List[Attraction]
    meals: List[str]
    hotel: str
    transport: str
    budget: float


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
    # 必须带 add_messages reducer：不带 reducer 时节点返回 {"messages": [...]}
    # 是"整体覆盖"，chat 节点一返回带 tool_calls 的 AI 消息就会把历史和本轮用户
    # 问题一起清掉，tool_node 拿到的只剩 [AI(tool_calls)]，最终回复不知道用户问了什么。
    # 带 reducer 后各节点只需返回"新增的消息"，历史自动保留。
    messages: Annotated[List[Any], add_messages]

    # 输出
    draft_plan: Optional[List[DayPlan]]
    final_plan: Optional[List[DayPlan]]
    response: str
    map_data: Dict

    # 流程控制 目前状态 错误情况 用户反馈
    current_step: str
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
