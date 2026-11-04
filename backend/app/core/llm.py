# author: szw
"""LLM 实例与 MCP 工具的进程内单例。

这里只放"模型怎么建、工具怎么加载"，不含任何业务逻辑。
节点、prompt 分别在同级 nodes/ 与 prompts/ 下。
"""
import asyncio

from langchain_openai import ChatOpenAI

from app.config import settings
from app.core.state import IntentOutput
from app.mcp.client import getAllTools

#
# DeepSeek 走 OpenAI 兼容端点，所以统一用 ChatOpenAI 指向它，换供应商只改 .env。
#
# 关于思考模式：DeepSeek 默认开启（effort=high）。这里显式关闭，三个原因：
#   1) 开启后思考阶段的 chunk.content 为空，本项目的 SSE 是按 content 转发的，
#      前端会长时间没有任何输出，看起来像卡死；
#   2) 按官方文档，请求里带 tools 时必须在后续请求回传 reasoning_content，否则 400。
#      本项目每轮对话的历史是从 SQLite 重建的纯文本，拿不回 reasoning_content；
#   3) 意图分类、JSON 行程生成不需要长链推理，开着纯亏延迟和 token（思考 token 按输出计费）。
# 确实需要更强推理时，把 .env 的 DEEPSEEK_THINKING 改成 enabled，但要先解决第 2 点。
llm = ChatOpenAI(
    model=settings.deepseek_model,
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_base_url,
    streaming=True,
    timeout=settings.llm_timeout,
    max_retries=2,
    extra_body={"thinking": {"type": settings.deepseek_thinking}},
)
# 内部结构化抽取专用的模型实例：必须关掉 streaming。
# streaming=True 的 llm 在 ainvoke 时同样会逐块生成并触发 on_chat_model_stream，
# 而景点提取是在 generate_plan 节点内部被调用的，事件 metadata.langgraph_node 就是
# generate_plan —— 于是这段"只给程序看的 JSON"被 api/chat.py 当成正文推给前端，
# 还写进了 messages.content。表现就是行程 JSON 后面又跟一段 {"attractions": [...]}，
# 前端解析到第二段渲染不出卡片，整条消息退化成原始 JSON 文本。
llm_extract = ChatOpenAI(
    model=settings.deepseek_model,
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_base_url,
    streaming=False,
    timeout=settings.llm_timeout,
    max_retries=2,
    extra_body={"thinking": {"type": settings.deepseek_thinking}},
)
# DeepSeek 不支持 response_format={"type":"json_schema"}（langchain 对 pydantic 模型的默认做法），
# 直接调用会 400: "This response_format type is unavailable now"。
# 改走 function_calling，把 schema 作为 tool 定义下发——DeepSeek 官方支持 tool calls。
# 也不要用 json_mode：它要求提示词里出现 "json" 字样，且不保证 schema 完全匹配。
llm_with_structure = llm.with_structured_output(IntentOutput, method="function_calling")

tools = None
tools_by_name = {}
model_with_tools = None

_tools_lock = asyncio.Lock()
_tools_ready = False


async def initialized_tools():
    global tools, tools_by_name, model_with_tools
    tools = await getAllTools()  # 组成工具数组
    tools_by_name = {t.name: t for t in tools}
    model_with_tools = llm.bind_tools(tools)


async def ensure_tools():
    """确保 MCP 工具已加载（进程内只做一次）

    原来 classify_input 每条消息都调 initialized_tools()，要起两个 uvx 子进程
    重新拉工具清单并重新 bind_tools，实测 2.03s / 1.77s 一条消息——而意图分类
    本身走的是 llm_with_structure，根本用不到工具。
    改成「谁真正需要工具，谁在开头 await ensure_tools()」，并加锁保证并发请求
    不会同时初始化出两份。
    """
    global _tools_ready
    if _tools_ready:
        return
    async with _tools_lock:
        if _tools_ready:
            return
        await initialized_tools()
        _tools_ready = True
        print(f"🔧 MCP 工具初始化完成: {len(tools)} 个")
