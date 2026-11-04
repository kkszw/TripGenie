# author: szw
"""
登录认证基础设施：密码哈希（bcrypt） + 令牌（JWT） + 当前用户依赖。

设计取舍：
- 密码不直接交给 bcrypt，而是先 sha256 再 base64。bcrypt 只使用前 72 字节，
  超过部分会被静默丢弃（bcrypt 5.x 甚至会直接抛错），先摘要一遍可以支持任意长度密码。
- 令牌放 Authorization: Bearer <token>，不放 Cookie：本项目是 Vite 前端 + FastAPI 分离部署，
  Cookie 需要 CSRF 防护与 SameSite 配置，而项目里没有 v-html、没有第三方脚本，
  用 localStorage 存 JWT 的实际风险更低。
"""
import base64
import hashlib
import secrets

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# auto_error=False：自己控制 401 的文案，不让 FastAPI 返回 "Not authenticated"
_bearer = HTTPBearer(auto_error=False)

_JWT_SECRET = settings.jwt_secret
if not _JWT_SECRET:
    # 没配 JWT_SECRET 也能跑，但重启后所有令牌失效（密钥变了）。
    # 生产环境必须在 .env 里固定一个随机串。
    _JWT_SECRET = secrets.token_urlsafe(48)
    logger.warning("未配置 JWT_SECRET，已使用临时密钥：重启后现有登录态全部失效")

UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="未登录或登录已过期",
    headers={"WWW-Authenticate": "Bearer"},
)


# ==================== 密码 ====================

def _prehash(password: str) -> bytes:
    """把任意长度密码压成 44 字节，规避 bcrypt 的 72 字节上限"""
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # 库里存了非 bcrypt 格式的脏数据时当作校验失败，不要把 500 抛给前端
        return False


# ==================== 令牌 ====================

def create_access_token(user_id: str, username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "username": username,
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_expire_days),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Optional[Dict]:
    try:
        return jwt.decode(token, _JWT_SECRET, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None


# ==================== 依赖注入 ====================

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> Dict:
    """从 Bearer 令牌解析出当前用户；失败一律 401"""
    # 延迟导入：security 被 api/* 导入，database 又要在模块级建表，
    # 放到函数内可避免 import 顺序导致的循环依赖
    from app.services.database import db

    if credentials is None or not credentials.credentials:
        raise UNAUTHORIZED

    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise UNAUTHORIZED

    user = db.get_user_by_id(payload.get("sub", ""))
    if not user:
        # 用户被删了但令牌还没过期
        raise UNAUTHORIZED
    return user
