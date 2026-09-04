# author: szw
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.api import chat, history
from app.core.langgraph_app import create_workflow
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
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(history.router, prefix="/api/history", tags=["history"])


@app.on_event("startup")
async def startup():
    """启动时初始化"""
    # 初始化 LangGraph 工作流
    app.state.workflow =  await create_workflow()
    app.state.checkpointer = await get_checkpointer()
    print("✅ TripGenie API 启动成功!")


@app.get("/")
async def root():
    return {"message": "TripGenie API is running", "status": "ok"}


@app.get("/health")
async def health():
    return {"status": "healthy"}