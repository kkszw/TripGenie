# author: szw
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.api import auth, chat, history
from app.core.llm import ensure_tools
from app.core.langgraph_app import get_workflow
from app.core.checkpointer import get_checkpointer
from app.core.logging import adopt_uvicorn_loggers, get_logger, setup_logging

# 尽量早：之后任何模块第一行日志都能落文件、带 trace_id
setup_logging()
logger = get_logger(__name__)

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
            logger.warning("MCP 工具预热失败（不影响启动，首次用到工具时会重试）",
                           extra={"err": str(e)})

    asyncio.create_task(_warmup_tools())
    # uvicorn 在启动时给自己的 logger 单独挂了 handler，摘掉才能让它的
    # 启动/报错日志也走我们的格式（带 trace_id、同时落文件）
    adopt_uvicorn_loggers()
    logger.info("TripGenie API 启动完成")


@app.get("/")
async def root():
    return {"message": "TripGenie API is running", "status": "ok"}


@app.get("/health")
async def health():
    return {"status": "healthy"}