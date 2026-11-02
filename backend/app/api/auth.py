# author: szw
import uuid
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.models.auth import LoginRequest, RegisterRequest, TokenResponse, UserInfo
from app.services.database import db

router = APIRouter()


def _token_response(user: Dict) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user["id"], user["username"]),
        user=UserInfo(
            id=user["id"],
            username=user["username"],
            created_at=user["created_at"],
        ),
    )


@router.post("/register", response_model=TokenResponse, tags=["认证"])
def register(payload: RegisterRequest):
    """注册新用户，成功后直接返回令牌（不用再登录一次）"""
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")

    # 用户名不区分大小写地占位：Alice 与 alice 视为同一个账号，
    # 避免出现长得一样、实际是两个人的情况
    if db.get_user_by_username(username):
        raise HTTPException(status_code=400, detail="用户名已被占用")

    user = db.create_user(
        user_id=str(uuid.uuid4()),
        username=username,
        password_hash=hash_password(payload.password),
    )
    if not user:
        # 并发注册同名用户时插入会撞 UNIQUE
        raise HTTPException(status_code=400, detail="用户名已被占用")

    return _token_response(user)


@router.post("/login", response_model=TokenResponse, tags=["认证"])
def login(payload: LoginRequest):
    """用户名 + 密码换令牌"""
    user = db.get_user_by_username(payload.username.strip())
    # 用户不存在与密码错误返回同一句文案，避免暴露用户名是否注册过
    if not user or not verify_password(payload.password, user.get("password_hash", "")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    return _token_response(
        {"id": user["id"], "username": user["username"], "created_at": user["created_at"]}
    )


@router.get("/me", response_model=UserInfo, tags=["认证"])
def me(current_user: Dict = Depends(get_current_user)):
    """前端刷新页面时用它校验本地令牌是否还有效"""
    return UserInfo(
        id=current_user["id"],
        username=current_user["username"],
        created_at=current_user["created_at"],
    )
