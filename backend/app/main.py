# author: szw
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.api import auth, chat, history
from app.core.llm import ensure_tools
from app.core.langgraph_app import get_workflow
from app.core.checkpointer import get_checkpointer

app = FastAPI(
    title="TripGenie API",
    description="智能旅行规划助手后端",
    version="1.0.0"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(history.router, prefix="/api/history", tags=["history"])


@app.on_event("startup")
async def startup():
    """启动时初始化"""
    # 初始化 LangGraph 工作流（进程内单例，chat.py 复用同一个实例）
    app.state.workflow = await get_workflow()
    app.state.checkpointer = await get_checkpointer()

    # 预热 MCP 工具清单：首次拉取要起两个 uvx 子进程（约 2s），
    # 放到后台任务里做，不拖慢启动，也不让第一条消息承担这份开销。
    async def _warmup_tools():
        try:
            await ensure_tools()
        except Exception as e:
            print(f"⚠️ MCP 工具预热失败（不影响启动，首次用到工具时会重试）: {e}")

    asyncio.create_task(_warmup_tools())
    print("✅ TripGenie API 启动成功!")


@app.get("/")
async def root():
    return {"message": "TripGenie API is running", "status": "ok"}


@app.get("/health")
async def health():
    return {"status": "healthy"}