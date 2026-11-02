import uuid
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import get_current_user
from app.models.history import SessionCreate, SessionResponse, SessionUpdate
from app.services.database import db

router = APIRouter()


def _owned_session(thread_id: str, user: Dict) -> Dict:
    """
    取会话并校验归属。

    不存在与"属于别人"都返回 404（而不是 403）：403 会告诉攻击者"这个 thread_id 存在"，
    等于给出了一个可枚举的信号。对调用方来说，别人的会话和不存在没有区别。
    """
    session = db.get_session(thread_id)
    if not session or session.get("user_id") != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"会话 {thread_id} 不存在",
        )
    return session


@router.put("/sessions/{thread_id}", tags=["会话"])
def update_session_title(
    thread_id: str,
    request: SessionUpdate,
    current_user: Dict = Depends(get_current_user),
):
    """更新目标会话的标题名"""
    _owned_session(thread_id, current_user)
    ok = db.update_session_name(thread_id, request.title)
    return {"success": ok, "thread_id": thread_id, "title": request.title}


@router.post("/sessions", response_model=SessionResponse, tags=["会话"])
def create_new_session(
    session: SessionCreate,
    current_user: Dict = Depends(get_current_user),
):
    """创建新会话（归属当前登录用户，忽略请求体里可能带的 user_id）"""
    thread_id = str(uuid.uuid4())
    session_data = db.create_session(thread_id, current_user["id"], session.name)
    if not session_data:
        # 理论上 uuid4 不会撞，撞了说明库里已有同 id 行
        raise HTTPException(status_code=500, detail="创建会话失败，请重试")
    return SessionResponse(**session_data)


@router.get("/", tags=["会话"])
async def get_history(current_user: Dict = Depends(get_current_user)):
    """
    获取当前用户的所有会话列表

    注意：user_id 只从令牌里取，不再接受 query 参数。
    以前 `?user_id=xxx` 是谁传谁算，等于没有任何隔离。
    """
    sessions = db.get_all_sessions(current_user["id"])

    return [
        {
            "id": s.get("thread_id"),
            "title": s.get("name", "新对话"),
            "created_at": s.get("created_at"),
            "updated_at": s.get("updated_at"),
            "message_count": s.get("message_count", 0),
        }
        for s in sessions
    ]


@router.get("/{thread_id}", tags=["会话"])
async def get_session(thread_id: str, current_user: Dict = Depends(get_current_user)):
    """获取单个会话的完整内容"""
    session = _owned_session(thread_id, current_user)
    messages = db.get_messages(thread_id)

    return {
        "id": session.get("thread_id"),
        "title": session.get("name"),
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
        "messages": messages,
    }


@router.delete("/{thread_id}", tags=["会话"])
async def delete_session(thread_id: str, current_user: Dict = Depends(get_current_user)):
    """删除会话（含消息，级联删除）"""
    _owned_session(thread_id, current_user)
    db.delete_session(thread_id)
    return {"success": True, "message": "删除成功"}
