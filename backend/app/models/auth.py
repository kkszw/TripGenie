# author: szw
from typing import Optional

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    """注册请求"""
    username: str = Field(min_length=2, max_length=32, description="用户名")
    password: str = Field(min_length=6, max_length=128, description="密码")


class LoginRequest(BaseModel):
    """登录请求"""
    username: str
    password: str


class UserInfo(BaseModel):
    """对前端暴露的用户信息（永远不含 password_hash）"""
    id: str
    username: str
    created_at: str


class TokenResponse(BaseModel):
    """登录/注册成功后的响应"""
    access_token: str
    token_type: str = "bearer"
    user: UserInfo
