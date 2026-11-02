# author: szw
import aiosqlite
from pathlib import Path
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from app.config import settings
from app.utils.path_tool import get_absolute_path


_checkpointer = None


async def get_checkpointer():
    """获取检查点实例（单例）"""
    global _checkpointer

    if _checkpointer is None:
        # 路径统一相对 backend/app 解析，与进程启动目录无关。
        # 原来直接把相对路径交给 aiosqlite，会按当前工作目录落库，
        # 从仓库根 / backend / IDE 启动各生成一个 sessions.db，状态互相看不见。 
        db_path = Path(get_absolute_path(settings.checkpoint_db_path))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(db_path))
        _checkpointer = AsyncSqliteSaver(conn=conn)
        await _checkpointer.setup()

    return _checkpointer
