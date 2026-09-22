# author: szw
import json
import re
from langchain_community.chat_models import ChatTongyi
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from typing import Literal
from langchain_core.runnables import RunnableConfig
from app.core.state import TripState, IntentOutput
from app.config import settings
from app.rag.retriever import *
from langgraph.graph import END
from app.mcp.client import getAllTools

llm = ChatTongyi(model=settings.DASHSCOPE_MODEL, api_key=settings.DASHSCOPE_API_KEY, streaming=True)
llm_with_structure = llm.with_structured_output(IntentOutput)

tools = None
tools_by_name = {}
model_with_tools = None


async def initialized_tools():
    global tools, tools_by_name, model_with_tools
    tools = await getAllTools()  # 组成工具数组
    tools_by_name = {t.name: t for t in tools}
    model_with_tools = llm.bind_tools(tools)


async def classify_input(state: TripState):
    """分析用户的输入并路由到目标节点"""
    user_input = state["user_input"]
    await initialized_tools()
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
    
        请只返回如下的JSON格式：
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
        print(f"📄 format_input 提取的 JSON: {content}")
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
}
"""
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
    runnable_config: RunnableConfig = {
        "configurable": {"thread_id": state.get("thread_id")},
        "tags": ["generate_plan"]  # ← 打上标记
    }
    # 使用 astream 流式生成
    async for chunk in llm.astream([HumanMessage(content=prompt)], config=runnable_config):
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
        print("✅ JSON 解析成功")

        # raise SystemExit("调试结束")
        state["draft_plan"] = data.get("days", [])
        state["response"] = format_response(data)

        print("🗺️ 开始生成地图数据...plan_node")
        map_data = await generate_map_data(state, data)
        if map_data:
            print(f"✅ 地图数据生成成功: {len(map_data.get('attractions', []))} 个景点")
            state["map_data"] = map_data
            data["map_data"] = map_data

        if writer:
            print(f"📤 发送行程计划，包含地图数据: {bool(data.get('map_data'))}")
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


async def extract_attractions_with_llm(state: TripState, plan_data: dict) -> List[Dict]:
    """
    使用 LLM 从行程计划中智能提取景点
    """
    # 构建行程描述
    plan_text = f"目的地：{state.get('destination', '')}\n\n"
    plan_text += "详细行程：\n"

    for day in plan_data.get("days", []):
        plan_text += f"\n第{day.get('day', 0)}天：\n"
        for activity in day.get("activities", []):
            name = activity.get("activity") or activity.get("name") or ""
            desc = activity.get("description", "")
            plan_text += f"  - {name}"
            if desc:
                plan_text += f"：{desc}"
            plan_text += "\n"

    prompt = f"""
    你是一个专业的旅行规划助手。请从以下行程中提取出所有**值得参观的景点/地标/公园/博物馆**。

    ## 行程信息
    {plan_text}

    ## 提取规则
    1. **只提取景点**：如公园、广场、博物馆、海滩、山、寺、塔、古城、步行街等
    2. **识别**：未给出明确地点则从最经地方随机选择一处街道
    3. **去重**：同一个景点只提取一次
    4. **别名**：如果景点有别名，使用标准名称

    ## 返回格式
    返回 JSON 数组，包含景点名称和它出现的天数：
    {{
        "attractions": [
            {{"name": "星海广场", "day": 1}},
            {{"name": "老虎滩海洋公园", "day": 1}},
            {{"name": "大连森林动物园", "day": 2}},
            {{"name": "棒棰岛", "day": 2}},
            {{"name": "金石滩", "day": 3}}
        ]
    }}

    **只返回 JSON，不要返回其他内容。**
    """

    try:
        print("🤖 使用 LLM 提取景点...")
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = response.content

        print(f"LLM 响应: {content[:20]}...")

        # 提取 JSON
        import re
        # 尝试匹配 ```json ... ``` 或直接的 JSON
        json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # 尝试直接匹配 JSON 对象
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                json_str = json_match.group()
            else:
                print("❌ 无法从 LLM 响应中提取 JSON")
                return []

        data = json.loads(json_str)
        attractions = data.get("attractions", [])

        # 转换为统一格式
        result = []
        for attr in attractions:
            result.append({
                "name": attr.get("name", "").strip(),
                "day": attr.get("day", 1),
                "time": "",
                "description": ""
            })

        # 过滤空名称
        result = [a for a in result if a["name"]]

        print(f"✅ LLM 提取到 {len(result)} 个景点: {[a['name'] for a in result]}")
        return result

    except Exception as e:
        print(f"❌ LLM 提取景点失败: {e}")
        import traceback
        traceback.print_exc()
        return []


async def generate_map_data(state: TripState, plan_data: dict) -> Dict:
    """生成地图数据：景点坐标和路线"""
    try:
        print("🗺️ 开始生成地图数据...map_data")
        # 1. 提取所有景点
        attractions = await extract_attractions_with_llm(state, plan_data)
        print(f"📋 共 {len(attractions)} 个景点待获取坐标")

        # 2. 获取景点坐标（使用高德 MCP）
        geo_tool = tools_by_name.get("maps_geo")
        attractions_with_coords = []

        # 地理编码失败时的兜底坐标（见 .env 的 DEFAULT_MAP_LNG/LAT）
        base_lng, base_lat = settings.default_map_lng, settings.default_map_lat
        # 提取城市名（用于限定地理搜索范围）
        destination = state.get("destination", "")
        city_name = destination
        if "省" in destination:
            parts = destination.split("省")
            if len(parts) > 1:
                city_name = parts[1]
        if "市" in city_name:
            city_name = city_name.split("市")[0]
        city_name = city_name.strip()

        for idx, attr in enumerate(attractions):
            try:
                if geo_tool:
                    # ✅ 用城市+景点名组合搜索
                    search_address = f"{city_name}{attr['name']}" if city_name else attr['name']

                    print(f"📍 [{idx + 1}/{len(attractions)}] 获取 {attr['name']} 坐标（搜索: {search_address}）...")

                    result = await geo_tool.ainvoke({
                        "address": search_address,
                        "city": city_name
                    })

                    print(f"  原始返回: {str(result)[:300]}")

                    coords = parse_coordinates(result)
                    if coords:
                        attractions_with_coords.append({
                            **attr,
                            "lng": coords["lng"],
                            "lat": coords["lat"]
                        })
                        print(f"  ✅ 坐标: {coords['lng']}, {coords['lat']}")
                        continue
                    else:
                        print(f"  ⚠️ 解析坐标失败，使用默认坐标")
                else:
                    print(f"  ⚠️ geo_tool 不存在")

                # fallback：使用默认坐标（大连市中心 + 偏移）
                attractions_with_coords.append({
                    **attr,
                    "lng": base_lng + (idx * 0.008),
                    "lat": base_lat + (idx * 0.006)
                })

            except Exception as e:
                print(f"  ❌ 获取 {attr['name']} 坐标失败: {e}")
                attractions_with_coords.append({
                    **attr,
                    "lng": base_lng + (idx * 0.008),
                    "lat": base_lat + (idx * 0.006)
                })

        print(f"🔍 坐标获取完成: {len(attractions_with_coords)} 个景点")

        # 3. 规划路线（如果有多个景点）
        routes = []
        if len(attractions_with_coords) >= 2:
            driving_tool = tools_by_name.get("maps_direction_driving_by_coordinates")
            if driving_tool:
                for i in range(len(attractions_with_coords) - 1):
                    origin = attractions_with_coords[i]
                    dest = attractions_with_coords[i + 1]

                    # ✅ 关键：只在同一天内生成路线，跳过跨天
                    origin_day = origin.get("day", 1)
                    dest_day = dest.get("day", 1)
                    if origin_day != dest_day:
                        print(f"⏭️ 跳过跨天路线: {origin['name']}(Day{origin_day}) → {dest['name']}(Day{dest_day})")
                        continue

                    try:
                        print(f"🚗 规划路线: {origin['name']} → {dest['name']}")
                        result = await driving_tool.ainvoke({
                            "origin": f"{origin['lng']},{origin['lat']}",
                            "destination": f"{dest['lng']},{dest['lat']}"
                        })
                        route_info = parse_route_result(result)
                        routes.append({
                            "from": origin["name"],
                            "to": dest["name"],
                            "from_coord": [origin["lng"], origin["lat"]],
                            "to_coord": [dest["lng"], dest["lat"]],
                            "distance": route_info.get("distance", 0),
                            "duration": route_info.get("duration", 0),
                            "polyline": route_info.get("polyline", [])
                        })
                        print(f"  ✅ 路线规划完成")
                    except Exception as e:
                        print(f"路线规划失败: {e}")
                        routes.append({
                            "from": origin["name"],
                            "to": dest["name"],
                            "from_coord": [origin["lng"], origin["lat"]],
                            "to_coord": [dest["lng"], dest["lat"]],
                            "distance": 0,
                            "duration": 0
                        })

        existing_names = {a["name"] for a in attractions_with_coords}

        for route in routes:
            for point in [route["from"], route["to"]]:
                if point not in existing_names:
                    # 用路线的端点坐标补一个 attraction
                    coord = route["from_coord"] if point == route["from"] else route["to_coord"]
                    attractions_with_coords.append({
                        "name": point,
                        "day": 1,
                        "lng": coord[0],
                        "lat": coord[1]
                    })
                    existing_names.add(point)
                    print(f"➕ 补全景点: {point} ({coord})")
        # 地图中心取所有已定位景点的均值，作为前端无有效坐标时的兜底
        if attractions_with_coords:
            center = (
                sum(a["lng"] for a in attractions_with_coords) / len(attractions_with_coords),
                sum(a["lat"] for a in attractions_with_coords) / len(attractions_with_coords),
            )
        else:
            center = (base_lng, base_lat)

        result = {
            "attractions": attractions_with_coords,
            "routes": routes,
            "destination": state.get("destination", ""),
            "center": center
        }
        print(f"✅ 地图数据生成完成: {len(result['attractions'])} 个景点, {len(result['routes'])} 段路线")
        return result

    except Exception as e:
        print(f"生成地图数据失败: {e}")
        import traceback
        traceback.print_exc()
        return {}


def parse_route_result(result: Any) -> Dict:
    """解析路线结果"""
    try:
        if isinstance(result, dict):
            route = result.get("route", {})
            paths = route.get("paths", [])
            if paths:
                path = paths[0]
                return {
                    "distance": path.get("distance", 0),
                    "duration": path.get("duration", 0),
                    "polyline": path.get("polyline", [])
                }
    except:
        pass
    return {"distance": 0, "duration": 0, "polyline": []}


def parse_coordinates(result) -> Dict:
    """解析高德地理编码结果（适配 MCP 返回格式）"""
    import json
    import re

    try:
        if not result:
            return {}

        # ✅ 情况1：MCP 返回格式 list[{'type': 'text', 'text': '{...}'}]
        if isinstance(result, list) and len(result) > 0:
            item = result[0]
            if isinstance(item, dict) and "text" in item:
                text = item["text"]
                try:
                    data = json.loads(text)
                    coords = extract_location_from_dict(data)
                    if coords:
                        return coords
                except json.JSONDecodeError:
                    pass
                # JSON 解析失败，用正则兜底
                return extract_location_from_text(text)

        # ✅ 情况2：字符串（可能是 JSON 字符串）
        if isinstance(result, str):
            try:
                data = json.loads(result)
                coords = extract_location_from_dict(data)
                if coords:
                    return coords
            except json.JSONDecodeError:
                pass
            return extract_location_from_text(result)

        # ✅ 情况3：dict（兼容旧格式）
        if isinstance(result, dict):
            coords = extract_location_from_dict(result)
            if coords:
                return coords

    except Exception as e:
        print(f"解析坐标失败: {e}")

    return {}


def extract_location_from_dict(data) -> Dict:
    """从 dict 中提取 location"""
    try:
        if isinstance(data, dict):
            # 高德 MCP 返回：{"return": [{"location": "121.63,38.92", ...}]}
            returns = data.get("return", [])
            if returns and len(returns) > 0:
                loc = returns[0].get("location", "")
                if loc and "," in loc:
                    parts = loc.split(",")
                    return {"lng": float(parts[0]), "lat": float(parts[1])}

            # 兼容旧格式：{"geocodes": [{"location": "..."}]}
            geocodes = data.get("geocodes", [])
            if geocodes and len(geocodes) > 0:
                loc = geocodes[0].get("location", "")
                if loc and "," in loc:
                    parts = loc.split(",")
                    return {"lng": float(parts[0]), "lat": float(parts[1])}

            # 直接有 location
            if "location" in data:
                loc = data["location"]
                if isinstance(loc, str) and "," in loc:
                    parts = loc.split(",")
                    return {"lng": float(parts[0]), "lat": float(parts[1])}
    except Exception as e:
        print(f"extract_location_from_dict 异常: {e}")
    return {}


def extract_location_from_text(text: str) -> Dict:
    """从文本中用正则提取坐标"""
    try:
        # 匹配 "location": "121.639890,38.920099"
        match = re.search(r'"location"\s*:\s*"([\d.]+),([\d.]+)"', text)
        if match:
            return {"lng": float(match.group(1)), "lat": float(match.group(2))}
        # 兜底
        match = re.search(r'(\d+\.\d+),(\d+\.\d+)', text)
        if match:
            return {"lng": float(match.group(1)), "lat": float(match.group(2))}
    except Exception as e:
        print(f"extract_location_from_text 异常: {e}")
    return {}


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
