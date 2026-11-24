import uuid
from typing import Dict, Optional

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
async def get_session(
    thread_id: str,
    limit: int = 50,
    before: Optional[int] = None,
    current_user: Dict = Depends(get_current_user),
):
    """获取单个会话的一页消息（时间正序）。

    翻页方向是从最新往前：不带 `before` 拿最新一页，响应里的 `next_before`
    用来取更早的一页。前端在列表顶部放一个「加载更早的消息」按钮。

    为什么必须分页：这里原来是 `db.get_messages(thread_id)`，默认 limit=50，
    超过 50 条的会话早期消息永远取不到，而且接口不告诉调用方被截断过——
    用户看到的是"历史丢了"。现在把 `has_more` 显式交出去。
    """
    session = _owned_session(thread_id, current_user)

    # 上限 200：一次把几千条消息塞进响应，前端挂载和滚动都要卡。
    # 下限 1：limit=0 会让 SQL 的 LIMIT 0 返回空页，看起来像"这个会话没消息"。
    limit = max(1, min(int(limit), 200))
    page = db.get_messages_page(thread_id, limit=limit, before_id=before)
    messages = page["messages"]

    return {
        "id": session.get("thread_id"),
        "title": session.get("name"),
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
        "messages": messages,
        "has_more": page["has_more"],
        # 下一页的游标 = 本页最早那条的 id。没有更早的就不给，前端少判一次。
        "next_before": messages[0]["id"] if (page["has_more"] and messages) else None,
    }


@router.delete("/{thread_id}", tags=["会话"])
async def delete_session(thread_id: str, current_user: Dict = Depends(get_current_user)):
    """删除会话（含消息，级联删除）"""
    _owned_session(thread_id, current_user)
    db.delete_session(thread_id)
    return {"success": True, "message": "删除成功"}
