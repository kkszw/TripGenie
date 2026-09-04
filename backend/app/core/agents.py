# author: szw
import json
from langchain_community.chat_models import ChatTongyi
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from typing import Literal
from langchain_core.runnables import RunnableConfig
from app.core.state import TripState, IntentOutput
from app.config import settings
from app.rag.retriever import *
from app.core.tools import get_weather_tool, retrieve_guides_tool
from langgraph.graph import END
from app.mcp.client import getAllClientTools


tools = [get_weather_tool, retrieve_guides_tool]  + getAllClientTools()   # 组成工具数组
tools_by_name = {t.name: t for t in tools}  # 组成工具名和工具的映射，方便查找工具

llm = ChatTongyi(model=settings.DASHSCOPE_MODEL, api_key=settings.DASHSCOPE_API_KEY, streaming=True)
llm_with_structure = llm.with_structured_output(IntentOutput)
model_with_tools = llm.bind_tools(tools)   # 绑定工具到模型


async def classify_input(state: TripState):
    """分析用户的输入并路由到目标节点"""
    user_input = state["user_input"]
    system_prompt = """
       你是一个意图分类器。分析用户输入，判断意图类型。

       意图类型：
       - chat: 闲聊、问候和调用单一工具解决用户问题（天气、景点、交通、美食等）
       - format_input: 复杂任务（多天旅游规划、行程安排等）
       """

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"用户输入：{user_input}")
    ]
    result = await llm_with_structure.ainvoke(messages)
    state["current_step"] = result.intent
    state["response"] = None
    return state


async def intent_router(state: TripState) -> Literal["format_input", "chat"]:
    """根据意图路由到不同处理器"""
    return state.get("current_step")


async def format_input(state: TripState, config: RunnableConfig = None) -> TripState:
    """分析用户输入，提取目   的地、天数、预算、偏好"""
    prompt = f"""
        分析用户的旅行需求，提取关键信息：
        用户输入：{state['user_input']}

        请返回 JSON 格式：
        {{
            "destination": "目的地（如果没提到则为空,如果找到了请以省名市名的格式返回，如（山东省临沂市））",
            "days": 天数（数字，默认3）,
            "budget": 预算（数字，默认5000）,
            "preferences": ["偏好1", "偏好2"]
        }}
        """
    writer = config.get("configurable", {}).get("writer") if config else None
    if writer:
        writer({
            "type": "node_start",
            "node": "📋 分析需求",
            "message": "正在分析您的旅行需求..."
        })
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    content = response.content
    if isinstance(content, list):
        # 如果是列表，拼接成字符串
        content = " ".join(str(item) for item in content)
    try:
        data = json.loads(content)
        state["destination"] = data.get("destination")
        state["days"] = data.get("days", 3)
        state["budget"] = data.get("budget", 5000)
        state["preferences"] = data.get("preferences", [])
    except Exception as e:
        state["error"] = "解析用户输入失败"
        print(f"format_input错误：{e}")

    dest = state["destination"]
    days = state["days"]
    budget = state["budget"]
    prefs = '、'.join(state["preferences"]) if state["preferences"] else "自由探索"

    state["response"] = (f"好的！我收到了您的旅行需求：\n\n📍 目的地：{dest}\n📅 天数：{days} 天\n💰 预算：{budget} 元\n🎯 "
                         f"偏好：{prefs}\n\n正在为您规划详细行程，请稍候...\n\n您也可以告诉我更多具体需求，比如：\n"
                         f"- 更想去哪些景点？\n- 住宿有什么要求？\n- 有没有特别想体验的活动？")

    state["current_step"] = "format_input"
    if writer:
        writer({
            "type": "node_end",
            "node": "📋 分析需求",
            "message": "需求分析完成"
        })
    return state


async def retrieve_guides(state: TripState, config: RunnableConfig = None):
    """从攻略库检索相关文档（RAG）"""
    try:
        writer = config.get("configurable", {}).get("writer") if config else None
        if writer:
            writer({
                "type": "node_start",
                "node": "📚 检索攻略",
                "message": "正在搜索相关旅游攻略..."
            })
        query = f"去{state['destination']}玩{state['days']}天，预算{state.get('budget')},喜欢{' '.join(state.get('preferences'))}"
        docs = retrieve_context(query, top_k=5).get("docs")
        if writer:
            writer({
                "type": "node_end",
                "node": "📚 检索攻略",
                "message": f"找到 {len(docs)} 篇攻略"
            })
        return {"similar_guides": docs}
    except Exception as e:
        print(f"错误，retrieve_guides出现错误{e}")


async def search_attractions_node(state: TripState, config: RunnableConfig = None):
    """搜索景点"""
    try:
        writer = config.get("configurable", {}).get("writer") if config else None
        if writer:
            writer({
                "type": "node_start",
                "node": "📚 搜索景点",
                "message": "正在搜索相关旅游景点..."
            })
        query = f"{state['destination']}的景点推荐,喜好：{' '.join(state.get('preferences'))}"
        docs = retrieve_context(query, top_k=5).get("docs")
        if writer:
            writer({
                "type": "node_end",
                "node": "📚 搜索景点",
                "message": f"找到 {len(docs)} 篇攻略"
            })
        return {"attractions_pool": docs}
    except Exception as e:
        print(f"错误，search_attractions_node出现错误:{e}")


async def get_weather_node(state: TripState, config: RunnableConfig = None):
    """获取天气信息"""
    try:
        writer = config.get("configurable", {}).get("writer") if config else None
        if writer:
            writer({
                "type": "node_start",
                "node": "获取天气信息",
                "message": "正在获取天气信息..."
            })
        query = f"{state.get("destination")}的天气如何"
        response = await model_with_tools.ainvoke(query)
        tool_call = response.tool_calls[0]
        tool_name = tool_call['name']
        tool_args = tool_call['args']
        tool = tools_by_name.get(tool_name)
        if tool:
            weather_result = await tool.ainvoke(tool_args)
            if writer:
                writer({
                    "type": "node_end",
                    "node": "获取天气信息",
                    "message": "天气信息获取完毕..."
                })
            return {"weather_info": weather_result}
    except Exception as e:
        print(f"错误，get_weather_node出现错误:{e}")


async def generate_plan_node(state: TripState, config: RunnableConfig = None) -> TripState:
    """生成行程计划"""
    templet = """
    ```json
{
  "title": "大连5日自由行",
  "destination": "辽宁省大连市",
  "totalDays": 5,
  "budget": 6000,
  "overview": "本次行程涵盖城市地标、海洋公园、异域风情和海滨休闲，适合喜欢自由探索的旅行者。",
  "days": [
    {
      "day": 1,
      "theme": "初识浪漫之都 · 城市地标与海滨漫步",
      "date": "8月11日",
      "weather": "晴转多云，25-30℃，海风3-4级，适合户外活动",
      "activities": [
        {
          "time": "09:00-10:30",
          "activity": "抵达大连，入住酒店（推荐青泥洼桥附近经济型酒店）",
          "description":  ,
          "cost": 180
        },
        {
          "time": "11:00-12:30",
          "activity": "游览中山广场，欣赏俄式、日式与欧式混合建筑群",
          "description":  ,
          "cost": 0
        },
        {
          "time": "12:30-13:30",
          "activity": "午餐：本地特色小吃店",
          "description": "推荐尝试焖子、海菜包子、咸鱼饼子等大连地道小吃，人均约30元。",
          "cost": 30
        },
        {
          "time": "14:00-17:00",
          "activity": "星海广场散步，欣赏跨海大桥，海边拍照",
          "description":  ,
          "cost": 0
        },
        {
          "time": "18:00-19:30",
          "activity": "晚餐：星海广场附近海鲜排档",
          "description": "推荐清蒸扇贝、辣炒蚬子、海胆蒸蛋，人均约80元。",
          "cost": 80
        },
      ],
      "notes": "实际总花费远低于6000元预算，预留充足空间用于升级餐饮、住宿或临时消费。所有价格基于普通游客标准，已考虑学生证优惠及经济型消费习惯。",
      "estimatedCost": 320
    },
  "tips": [
    "大连风大，建议携带薄外套",
    "海边紫外线强，注意防晒",
    "使用学生证可节省景点门票"
  ]
}"""
    prompt = f"""
    你是一个专业的旅行规划师。根据以下信息生成行程：

    目的地：{state.get('destination', '未知')}
    天数：{state.get('days', 3)} 天
    预算：{state.get('budget', 5000)} 元
    偏好：{', '.join(state.get('preferences', []))}
    天气：{state.get('weather_info', '未知')}

    参考攻略：
    {state.get('similar_guides', ['无'])}

    可用景点：
    {state.get('attractions_pool', [])}

    请生成详细的每日行程，包含景点、餐饮、住宿建议。
    返回 JSON 格式，包含每天的具体安排,格式如下所示：{templet}
    """
    writer = config.get("configurable", {}).get("writer") if config else None
    if writer:
        writer({
            "type": "node_start",
            "node": "生成行程计划",
            "message": "正在生成行程计划..."
        })
    full_response = ""
    config: RunnableConfig = {"configurable": {"thread_id": state.get("thread_id")}}
    # 使用 astream 流式生成
    async for chunk in llm.astream([HumanMessage(content=prompt)], config=config):
        content = chunk.content
        if content:
            full_response += content
            if writer:
                writer({
                    "type": "chunk",
                    "content": content
                })


    try:
        data = json.loads(full_response)
        print("/*" * 20)
        print(data[:20])
        state["draft_plan"] = data.get("days", [])
        state["response"] = format_response(data)
        # 发送行程计划事件
        if writer:
            writer({
                "type": "trip_plan",
                "plan": data
            })
    except Exception as e:
        state["error"] = f"生成行程失败: {str(e)}"
        state["response"] = full_response

    state["current_step"] = "generate_plan"
    if writer:
        writer({
            "type": "node_end",
            "node": "生成行程",
            "message": "行程计划生成完成"
        })
    return state


def format_response(plan_data: dict) -> str:
    """格式化响应文本"""
    text = f"根据您的需求，我为您规划了以下行程：\n\n"
    for day in plan_data.get("days", []):
        text += f"📅 **Day {day.get('day')}**: "
        attractions = day.get("attractions", [])
        if attractions:
            text += " → ".join([a.get("name", "") for a in attractions])
        text += "\n"

    return text


async def chat(state: TripState, config: RunnableConfig = None):
    """对话节点，如果是正常聊天就正常调用返回对话，如果需要调用工具则调用工具"""
    messages = state.get("messages", [])
    writer = config.get("configurable", {}).get("writer") if config else None
    if writer:
        writer({
            "type": "node_start",
            "node": "💬 AI 思考中",
            "message": "正在生成回复..."
        })
    full_response = None
    config: RunnableConfig = {"configurable": {"thread_id": state.get("thread_id")}}
    async for chunk in model_with_tools.astream(messages, config=config):

        if full_response is None:
            full_response = chunk
        else:
            full_response += chunk

        if chunk.content and not chunk.tool_calls:
            if writer:
                writer({"type": "chunk", "content": chunk.content})

    if writer:
        writer({
            "type": "node_end",
            "node": "AI 思考完成",
            "message": "回复完成"
        })
    if full_response and full_response.tool_calls:
        return {"messages": [full_response]}
    else:
        return {"response": full_response.content, "messages": [AIMessage(content=full_response.content)]}


async def tool_node(state: TripState):
    """工具节点：执行工具调用，如果需要继续则返回工具调用"""
    last_message = state["messages"][-1]

    if not hasattr(last_message, 'tool_calls') or not last_message.tool_calls:
        return {"response": "没有工具调用"}

    tool_calls = last_message.tool_calls
    print(f"🔧 需要执行 {len(tool_calls)} 个工具调用")

    results = []

    for i, tool_call in enumerate(tool_calls):
        print(f"🔧 执行工具 {i + 1}/{len(tool_calls)}: {tool_call['name']}")


        tool = tools_by_name.get(tool_call["name"])
        if not tool:
            error_msg = f"工具 {tool_call['name']} 不存在"
            print(f"❌ {error_msg}")
            results.append(ToolMessage(
                content=error_msg,
                tool_call_id=tool_call["id"]
            ))
            continue

        try:
            # 执行工具
            if hasattr(tool, 'ainvoke'):
                print(f"参数为：{tool_call['args']}")
                result = await tool.ainvoke(tool_call["args"])
            else:
                print(f"参数为：{tool_call['args']}")
                result = tool.invoke(tool_call["args"])

            print(f"✅ 工具 {i + 1} 执行完成")

            results.append(ToolMessage(
                content=str(result) if result else "执行完成",
                tool_call_id=tool_call["id"]
            ))

        except Exception as e:
            error_msg = f"工具执行失败: {str(e)}"
            print(f"❌ {error_msg}")
            results.append(ToolMessage(
                content=error_msg,
                tool_call_id=tool_call["id"]
            ))

    # 如果所有工具都执行完成，生成最终回复
    all_messages = state.get("messages", []) + results

    try:
        # 调用 LLM 生成最终回复（不带工具）
        final_response = await llm.ainvoke(all_messages)
        final_content = final_response.content if hasattr(final_response, 'content') else str(final_response)

        print(f"📝 最终回复: {final_content[:100]}...")

        return {
            "messages": results + [AIMessage(content=final_content)],
            "response": final_content
        }
    except Exception as e:
        print(f"❌ 生成最终回复失败: {e}")
        # 如果生成失败，合并工具结果
        tool_results = "\n".join([r.content for r in results if r.content])
        return {
            "messages": results,
            "response": tool_results
        }


def should_continue(state: TripState) -> Literal["tool_node", END]:
    """判断是否需要调用工具"""
    messages = state.get("messages", [])
    if not messages:
        return END
    last_message = messages[-1]
    # 检查是否有工具调用
    if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
        print(f"下一个节点是工具节点 (有 {len(last_message.tool_calls)} 个工具调用)")
        return "tool_node"
    return END
