import uuid
from fastapi import APIRouter, HTTPException, status
from app.models.history import *
from app.services.database import db

router = APIRouter()


@router.put("/sessions/{thread_id}", tags=["会话"])
def update_session_title(thread_id: str, request: SessionUpdate):
    """更新目标会话的标题名"""
    return db.update_session_name(thread_id, request.title)

@router.post("/sessions", response_model=SessionResponse, tags=["会话"])
def create_new_session(session: SessionCreate):
    """创建新会话"""
    thread_id = str(uuid.uuid4())
    user_id = session.user_id
    name = session.name
    return db.create_session(thread_id, user_id, name)

@router.get("/", tags=["会话"])
async def get_history(user_id: str = "default"):
    """
    获取用户的所有会话列表
    """
    sessions = db.get_all_sessions(user_id)

    # 格式化返回数据
    result = []
    for session in sessions:
        result.append({
            "id": session.get("thread_id"),
            "title": session.get("name", "新对话"),
            "created_at": session.get("created_at"),
            "updated_at": session.get("updated_at"),
            "message_count": session.get("message_count", 0)
        })

    return result


@router.get("/{thread_id}", tags=["会话"])
async def get_session(thread_id: str):
    """
    获取单个会话的完整内容
    """
    session = db.get_session(thread_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"会话 {thread_id} 不存在"
        )

    messages = db.get_messages(thread_id)

    return {
        "id": session.get("thread_id"),
        "title": session.get("name"),
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
        "messages": messages
    }


@router.delete("/{thread_id}", tags=["会话"])
async def delete_session(thread_id: str):
    """
    删除会话
    """
    success = db.delete_session(thread_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"会话 {thread_id} 不存在"
        )

    return {"success": True, "message": "删除成功"}