# author: szw
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Annotated


class ChatRequest(BaseModel):
    message: str = Field(description="对话内容")
    thread_id: Optional[str] = Field(description="会话编号")
    context: Optional[Dict[str, Any]] = Field(description="用户的长期习惯")