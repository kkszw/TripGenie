# author: szw
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Annotated


class ChatRequest(BaseModel):
    message: str = Field(description="对话内容")
    # thread_id / context 给默认值：pydantic v2 里 Optional 不加默认值仍是必填，
    # 少传一个字段就直接 422，而这两个字段缺了都能自己兜住。
    thread_id: Optional[str] = Field(default=None, description="会话编号")
    context: Optional[Dict[str, Any]] = Field(default=None, description="用户的长期习惯")