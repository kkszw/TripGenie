# author: szw
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.api import auth, chat, history, knowledge
from app.core.llm import ensure_tools
from app.core.langgraph_app import get_workflow
from app.core.checkpointer import get_checkpointer
from app.core.logging import adopt_uvicorn_loggers, get_logger, setup_logging
from app.services.database import db

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
app.include_router(knowledge.router, prefix="/api/knowledge", tags=["knowledge"])


def _build_keyword_index() -> None:
    """在后台线程里把 BM25 索引建好。

    单独抽出来是为了能被 asyncio.to_thread 调用：索引是纯 CPU 活，
    直接在协程里跑会把事件循环卡住 10 秒，所有请求一起等待。
    """
    from app.rag.keyword_index import get_index

    index = get_index("public")
    if index:
        logger.info("BM25 索引预热完成", extra={"docs": index.total})


@app.on_event("startup")
async def startup():
    """启动时初始化"""
    # 初始化 LangGraph 工作流（进程内单例，chat.py 复用同一个实例）
    app.state.workflow = await get_workflow()
    app.state.checkpointer = await get_checkpointer()

    # 上一次进程退出时没跑完的入库任务：后台协程随进程一起没了，数据库里
    # 那条记录还停在 parsing/indexing。不清理的话前端会对着一个永远不会
    # 变化的状态无限轮询。
    db.reset_stale_knowledge_docs()

    # 预热 MCP 工具清单：首次拉取要起两个 uvx 子进程（约 2s），
    # 放到后台任务里做，不拖慢启动，也不让第一条消息承担这份开销。
    async def _warmup_tools():
        try:
            await ensure_tools()
        except Exception as e:
            logger.warning("MCP 工具预热失败（不影响启动，首次用到工具时会重试）",
                           extra={"err": str(e)})

    asyncio.create_task(_warmup_tools())

    # 预热 BM25 索引。首次要分词 4074 块（约 10 秒），放在启动后的后台线程里，
    # 否则这份开销会落在第一个用户的第一次检索上——他会看到 10 秒的停顿，
    # 而不是一条"正在检索"的进度提示。
    async def _warmup_bm25():
        if not settings.hybrid_search:
            return
        try:
            # 纯 CPU 活，扔到线程里才不会卡住事件循环
            await asyncio.to_thread(_build_keyword_index)
        except Exception as e:
            logger.warning("BM25 索引预热失败（不影响启动，首次检索时会重建）",
                           extra={"err": str(e)})

    asyncio.create_task(_warmup_bm25())

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