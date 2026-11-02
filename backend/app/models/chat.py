# author: szw
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Annotated


class ChatRequest(BaseModel):
    message: str = Field(description="对话内容")
    # thread_id / context 给默认值：pydantic v2 里 Optional 不加默认值仍是必填，
    # 少传一个字段就直接 422，而这两个字段缺了都能自己兜住。
    thread_id: Optional[str] = Field(default=None, description="会话编号")
    context: Optional[Dict[str, Any]] = Field(default=None, description="用户的长期习惯")


class ResumeRequest(BaseModel):
    """HITL：确认卡上的决定。

    payload 的形状由 confirm_needs 约定：
        {"action": "confirm"}                     就这样，开始规划
        {"action": "cancel"}                      算了
        {"action": "edit", "destination": ..., "days": 5, ...}   改一改再规划
    这里刻意用宽松的 Dict 而不是带字段的模型：不同中断点的载荷不一样，
    收紧成固定字段以后加第二个确认点就得改接口。
    """
    thread_id: str = Field(description="会话编号")
    payload: Dict[str, Any] = Field(default_factory=dict, description="确认结果")