# author: szw
from pydantic import BaseModel


class SessionCreate(BaseModel):
    """创建会话的请求模型"""
    user_id: str
    name: str


class SessionResponse(BaseModel):
    """会话响应模型"""
    thread_id: str
    user_id: str
    name: str
    created_at: str
    updated_at: str

class SessionUpdate(BaseModel):
    """更改会话标题模型"""
    thread_id: str
    title: str
    user_id: str
