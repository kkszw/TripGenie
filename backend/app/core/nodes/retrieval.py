# author: szw
"""三个并行的取数节点：攻略检索、景点检索、天气。

三者互相独立，由 route_after_confirm 一次性扇出。
出错时各写各的 *_error 键——不能共用一个 error，并行节点同时写会 InvalidUpdateError。
"""
import asyncio

from langchain_core.runnables import RunnableConfig

from app.core import llm as llm_runtime
from app.core.logging import get_logger
from app.core.state import TripState
from app.rag.retriever import retrieve_context

logger = get_logger(__name__)


async def retrieve_guides(state: TripState, config: RunnableConfig = None):
    """从攻略库检索相关文档（RAG）"""
    writer = config.get("configurable", {}).get("writer") if config else None
    if writer:
        writer({
            "type": "node_start",
            "node": "📚 检索攻略",
            "message": "正在搜索相关旅游攻略..."
        })
    try:
        # days 现在允许为 None（表示用户没说，会先被 clarify 拦住），这里兜一次底
        prefs = " ".join(state.get("preferences") or [])
        query = f"去{state['destination']}玩{state.get('days') or 3}天"
        # 预算和偏好都可能没有。原来无条件拼进去，会得到「预算None,喜欢」
        # 这种半截句子，末尾挂一个没有宾语的动词，对 embedding 是噪声。
        if state.get("budget"):
            query += f"，预算{state['budget']}"
        if prefs:
            query += f"，喜欢{prefs}"
        # retrieve_context 内部是同步的 embedding HTTP 调用，直接跑会阻塞事件循环
        # （SSE 心跳、其它请求一起卡住），丢到线程池里执行。
        # 只取已格式化的 context 文本：原来把 List[Document] 直接塞进 prompt，
        # 发给模型的是 `[Document(page_content='...', metadata={...})]` 的 Python repr。
        # user_id 让检索额外覆盖用户自己上传的资料（没有则只查内置攻略库）。
        # destination 让检索先在本目的地的语料里找：125 份攻略是同一套模板，
        # 「交通」「有问必答」这些章节措辞逐字相同，不过滤就会拿回外地的段落。
        ctx = await asyncio.to_thread(
            retrieve_context, query, top_k=5, user_id=state.get("user_id"),
            destination=state.get("destination"))
        if writer:
            writer({
                "type": "node_end",
                "node": "📚 检索攻略",
                "message": f"找到 {len(ctx.get('docs') or [])} 篇资料"
            })
        return {"similar_guides": ctx.get("context", "")}
    except Exception as e:
        logger.warning("攻略检索失败", extra={"err": str(e)})
        # 不能只 print 后隐式返回 None：LangGraph 会把 None 当"空更新"直接放过，
        # 用户拿到一份没有攻略依据的行程，却不知道检索失败了。写进专门的键让前端可见。
        # 用 guides_error 而不是共用的 error：三个并行节点同时写 error 会 InvalidUpdateError。
        return {"guides_error": f"攻略检索失败: {e}"}


async def search_attractions_node(state: TripState, config: RunnableConfig = None):
    """搜索景点"""
    writer = config.get("configurable", {}).get("writer") if config else None
    if writer:
        writer({
            "type": "node_start",
            "node": "📚 搜索景点",
            "message": "正在搜索相关旅游景点..."
        })
    try:
        prefs = " ".join(state.get("preferences") or [])
        query = f"{state['destination']}的景点推荐"
        if prefs:
            query += f"，喜好：{prefs}"
        ctx = await asyncio.to_thread(
            retrieve_context, query, top_k=5, user_id=state.get("user_id"),
            destination=state.get("destination"))
        if writer:
            writer({
                "type": "node_end",
                "node": "📚 搜索景点",
                "message": f"找到 {len(ctx.get('docs') or [])} 篇资料"
            })
        return {"attractions_pool": ctx.get("context", "")}
    except Exception as e:
        logger.warning("景点检索失败", extra={"err": str(e)})
        return {"attractions_error": f"景点检索失败: {e}"}


async def get_weather_node(state: TripState, config: RunnableConfig = None):
    """获取天气信息"""
    writer = config.get("configurable", {}).get("writer") if config else None
    if writer:
        writer({
            "type": "node_start",
            "node": "获取天气信息",
            "message": "正在获取天气信息..."
        })
    try:
        await llm_runtime.ensure_tools()
        query = f"{state.get('destination')}的天气如何"
        response = await llm_runtime.model_with_tools.ainvoke(query)
        # 原来直接 response.tool_calls[0]：模型没返回 tool_call 就 IndexError，
        # 被下面的 except 吞掉，天气永远是"未知"。
        if not response.tool_calls:
            logger.debug("天气节点：模型没有返回工具调用，跳过天气查询")
            return {"weather_info": None}
        tool_call = response.tool_calls[0]
        tool_name = tool_call['name']
        tool_args = tool_call['args']
        tool = llm_runtime.tools_by_name.get(tool_name)
        if not tool:
            logger.warning("天气工具不可用", extra={"tool": tool_name})
            return {"weather_info": None, "weather_error": f"天气工具 {tool_name} 不可用"}
        weather_result = await tool.ainvoke(tool_args)
        if writer:
            writer({
                "type": "node_end",
                "node": "获取天气信息",
                "message": "天气信息获取完毕..."
            })
        return {"weather_info": weather_result}
    except Exception as e:
        logger.warning("天气获取失败", extra={"err": str(e)})
        return {"weather_info": None, "weather_error": f"天气获取失败: {e}"}
