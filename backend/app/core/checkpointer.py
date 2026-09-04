# author: szw
import aiosqlite
import os
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from app.config import settings


_checkpointer = None


async def get_checkpointer():
    """获取检查点实例（单例）"""
    global _checkpointer

    if _checkpointer is None:
        # 确保目录存在
        os.makedirs(os.path.dirname(settings.checkpoint_db_path), exist_ok=True)
        conn = await aiosqlite.connect(settings.checkpoint_db_path)
        _checkpointer = AsyncSqliteSaver(conn=conn)
        await _checkpointer.setup()

    return _checkpointer