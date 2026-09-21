# author: szw
from typing import List, Dict, Optional, TypedDict, Any, Literal
from typing_extensions import NotRequired
from pydantic import Field, BaseModel


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
        用户输入 目的地 天数 预算 偏好
    """
    # 用户输入 目的地 天数 预算 偏好
    user_input: str
    destination: Optional[str]
    days: int
    budget: float
    preferences: List[str]

    # 中间状态
    weather_info: Optional[Dict]
    attractions_pool: List[str]
    hotel_options: List[Dict]
    budget_analysis: Dict

    # RAG 检索结果
    similar_guides: List[str]

    # 对话相关
    thread_id: Optional[str]
    messages: List[Dict]  # 所有的聊天记录

    # 输出
    draft_plan: Optional[List[DayPlan]]
    final_plan: Optional[List[DayPlan]]
    response: str
    map_data: Dict

    # 流程控制 目前状态 错误情况 用户反馈
    current_step: str
    error: Optional[str]
    user_feedback: Optional[str]
    _events: NotRequired[List[Dict[str, Any]]]
