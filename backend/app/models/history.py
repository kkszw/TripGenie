# author: szw
from typing import Optional

from pydantic import BaseModel


class SessionCreate(BaseModel):
    """创建会话的请求模型

    user_id 已不再由客户端提供（归属取自登录令牌），保留可选字段只为兼容旧前端。
    """
    name: str = "新对话"
    user_id: Optional[str] = None


class SessionResponse(BaseModel):
    """会话响应模型"""
    thread_id: str
    user_id: str
    name: str
    created_at: str
    updated_at: str


class SessionUpdate(BaseModel):
    """更改会话标题模型"""
    title: str
    thread_id: Optional[str] = None
    user_id: Optional[str] = None
