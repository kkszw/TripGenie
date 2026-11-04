# author: szw
"""把一份行程变成地图数据：景点提取 → 地理编码 → 每日路线。

这里不碰 LangGraph 的 state，入参出参都是普通 dict，
只有 nodes/map.py 那一层负责读写 state。
"""
import json
import math
import traceback
from typing import Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage

from app.config import settings
from app.core import llm as llm_runtime
from app.core.prompts import build_attraction_prompt
from app.services.amap import parse_coordinates, plan_driving_route, poi_search_rest
from app.services.geo import Region, is_offset_ok, parse_region


def build_plan_text(destination: str, plan_data: dict) -> str:
    """把行程压成"第N天：- 景点：描述"的纯文本，喂给提取提示词。"""
    plan_text = f"目的地：{destination or ''}\n\n"
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
    return plan_text


async def extract_attractions_with_llm(destination: str, plan_data: dict) -> List[Dict]:
    """使用 LLM 从行程计划中智能提取景点"""
    prompt = build_attraction_prompt(build_plan_text(destination, plan_data))

    try:
        print("🤖 使用 LLM 提取景点...")
        # 用 llm_extract（streaming=False）：这里的输出是给程序看的中转数据，
        # 不能进 SSE 正文，否则会跟在行程 JSON 后面一起推给前端。
        # internal 是第二道保险：万一以后有人图省事又改回 llm，api/chat.py 看到
        # 这个标记也会跳过，不会再漏进正文。
        response = await llm_runtime.llm_extract.ainvoke(
            [HumanMessage(content=prompt)], config={"tags": ["internal"]}
        )
        content = response.content

        print(f"LLM 响应: {content[:20]}...")

        # 提取 JSON：优先抓 ```json 围栏，否则退化成最外层大括号
        import re
        json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                json_str = json_match.group()
            else:
                print("❌ 无法从 LLM 响应中提取 JSON")
                return []

        data = json.loads(json_str)
        attractions = data.get("attractions", [])

        result = []
        for attr in attractions:
            result.append({
                "name": attr.get("name", "").strip(),
                "day": attr.get("day", 1),
                # 景点所在城市：只给了省份的目的地（"山东"）靠它把地理编码钉到具体城市，
                # 否则省内同名地点会乱匹配（玉皇顶 → 临沂那座、孔林 → 菏泽那片）
                "city": (attr.get("city") or "").strip(),
                "time": "",
                "description": ""
            })

        # 过滤空名称
        result = [a for a in result if a["name"]]

        print(f"✅ LLM 提取到 {len(result)} 个景点: {[a['name'] for a in result]}")
        return result

    except Exception as e:
        print(f"❌ LLM 提取景点失败: {e}")
        traceback.print_exc()
        return []


async def geocode_attraction(geo_tool, name: str, region: Region,
                             idx: int = 0, total: int = 0) -> Optional[Dict]:
    """查单个景点的精确坐标，查不到返回 None（不编造坐标）

    高德对不规范的名字会静默返回"城市中心点"而不是报错，所以这里用
    parse_coordinates 的行政区级校验把那种结果挡掉，再逐级降级：
      1. 地理编码：城市名 + 景点名（景点名已含城市名时不重复拼）
      2. REST POI 关键词搜索（能命中非规范名称，比如"椰梦长廊-椰林海滩"）
      3. 裸景点名再走一次地理编码（"椰梦长廊"这种本地名带城市反而查不到）
    三级都失败才交给上层做估算排布。

    地域约束一律走 region.query_value，**不要**自己拼城市字符串：
    目的地只给了省份时它给的是省级 adcode，否则"芙蓉街""大明湖"这种
    全国同名的地方会被匹配到云南、新疆去。
    """
    base = name
    if region.city and not base.startswith(region.city):
        base = f"{region.city}{name}"
    city_value = region.query_value

    # 1) 组合名地理编码
    if geo_tool:
        try:
            print(f"📍 [{idx}/{total}] 地理编码 {name}（搜索: {base}，范围: {region.label or '全国'}）...")
            result = await geo_tool.ainvoke({"address": base, "city": city_value})
            coords = parse_coordinates(result)
            if coords:
                print(f"  ✅ 坐标: {coords['lng']}, {coords['lat']}")
                return coords
            print(f"  ⚠️ 「{base}」未定位到具体地点")
        except Exception as e:
            print(f"  ❌ 地理编码「{base}」异常: {e}")

    # 2) POI 关键词搜索兜底
    coords = await poi_search_rest(name, region)
    if coords:
        return coords

    # 3) 裸名地理编码
    if geo_tool and base != name:
        try:
            print(f"📍 [{idx}/{total}] 地理编码 {name}（裸名: {name}）...")
            result = await geo_tool.ainvoke({"address": name, "city": city_value})
            coords = parse_coordinates(result)
            if coords:
                print(f"  ✅ 坐标: {coords['lng']}, {coords['lat']}")
                return coords
            print(f"  ⚠️ 「{name}」未定位到具体地点")
        except Exception as e:
            print(f"  ❌ 地理编码「{name}」异常: {e}")

    print(f"  ⚠️ {name} 三级查询均失败，将使用估算坐标")
    return None


def scatter_around(center: Tuple[float, float], index: int, total: int,
                   radius: float = 0.012) -> Tuple[float, float]:
    """在中心点周围按环形均匀撒点，用于给定位失败的景点一个"看得见但不精确"的位置

    半径 0.012 度约 1.3km，视觉上能分开又不至于跑到城外。
    经度方向按 cos(lat) 修正，否则高纬度地区环会被压扁。
    """
    angle = 2 * math.pi * index / max(total, 1)
    lng = center[0] + radius * math.cos(angle) / max(math.cos(math.radians(center[1])), 0.2)
    lat = center[1] + radius * math.sin(angle)
    return round(lng, 6), round(lat, 6)


async def get_region_center(geo_tool, region: Region) -> Optional[Tuple[float, float]]:
    """查目的地本身的中心点：城市给城市中心，只给了省份就给省中心。

    这个点有两个用途：
      1. 校验每个景点坐标是否落在目的地合理范围内（错配的挡掉，见 is_offset_ok）；
      2. 给定位失败的景点当估算基准。
    拿不到就返回 None，调用方跳过校验——校验是兜底手段，它自己失败不该连累正常景点。
    """
    if not geo_tool or not region.center_query:
        return None
    try:
        # allow_admin=True：这里要的就是行政区中心点，不是景点
        result = await geo_tool.ainvoke({
            "address": region.center_query,
            "city": region.query_value,
        })
        coords = parse_coordinates(result, allow_admin=True)
        if coords:
            print(f"📌 目的地中心（{region.label}）: {coords['lng']}, {coords['lat']}")
            return coords["lng"], coords["lat"]
        print(f"⚠️ 未取到目的地中心（{region.label}），本轮跳过坐标合理性校验")
    except Exception as e:
        print(f"⚠️ 获取目的地中心失败: {e}")
    return None


async def resolve_center_base(located: List[Dict], geo_tool, region: Region,
                              fallback: Tuple[float, float],
                              center_ref: Optional[Tuple[float, float]] = None
                              ) -> Tuple[float, float]:
    """给"定位失败的景点"找一个合理的估算基准

    优先级：本次已成功定位的景点均值 > 目的地中心 > .env 兜底。
    以前直接用 .env 的 DEFAULT_MAP_LNG/LAT，那些值是大连的，三亚的景点会被画到大连去。
    """
    if located:
        return (
            round(sum(a["lng"] for a in located) / len(located), 6),
            round(sum(a["lat"] for a in located) / len(located), 6),
        )
    if center_ref:
        return center_ref
    center = await get_region_center(geo_tool, region)
    if center:
        return center
    print(f"⚠️ 拿不到目的地中心，回退到 .env 兜底坐标 {fallback}")
    return fallback


async def generate_map_data(state, plan_data: dict) -> Dict:
    """生成地图数据：景点坐标和路线"""
    try:
        print("🗺️ 开始生成地图数据...map_data")
        # 地图数据依赖 maps_geo / 驾车路线的工具对象，这里保证工具已加载
        await llm_runtime.ensure_tools()
        destination = state.get("destination", "")

        # 1. 提取所有景点
        attractions = await extract_attractions_with_llm(destination, plan_data)
        print(f"📋 共 {len(attractions)} 个景点待获取坐标")

        # 2. 获取景点坐标（使用高德 MCP）
        geo_tool = llm_runtime.tools_by_name.get("maps_geo")
        attractions_with_coords = []

        # 地理编码失败时的兜底坐标（见 .env 的 DEFAULT_MAP_LNG/LAT）
        base_lng, base_lat = settings.default_map_lng, settings.default_map_lat

        # 目的地解析：城市 / 只有省份 / 认不出。
        # 只给了省份时（"帮我规划山东三天"），高德必须拿到省级 adcode 才会把结果
        # 限制在省内；以前 city 传空串，全国同名地点随便匹配，"大明湖"能落到新疆。
        region = parse_region(destination)
        print(f"🧭 目的地解析: {destination!r} -> 城市={region.city!r} "
              f"省份={region.province!r} 粒度={region.scope} 查询值={region.query_value!r}")

        # 目的地中心点，用于校验景点坐标是否跑偏
        center_ref = await get_region_center(geo_tool, region)

        # 精确定位成功的和失败的先分开，失败的最后统一做估算排布
        located: List[Dict] = []
        failed: List[Dict] = []

        for idx, attr in enumerate(attractions):
            coords = None
            if geo_tool:
                # 目的地只到省一级时（"山东"），光有省 adcode 挡不住省内同名地点：
                # 实测"玉皇顶"会被匹配到临沂那座山、"孔林"会落到菏泽一片。
                # 景点自带的 city 是行程原文写明的（"乘车前往泰安"），用它把范围收到城市级。
                # 用户已经指定了城市时一律以用户为准，不信模型的判断。
                attr_region = region
                if region.scope != "city" and attr.get("city"):
                    attr_region = Region(
                        city=attr["city"], province=region.province, scope="city"
                    )
                coords = await geocode_attraction(
                    geo_tool, attr["name"], attr_region, idx + 1, len(attractions)
                )
            else:
                print("  ⚠️ geo_tool 不存在，所有景点都将使用估算坐标")

            # 坐标合理性校验：高德对同名地点是"谁相关取谁"，不校验就会把
            # 济南的芙蓉街变成云南那条、大明湖变成新疆那片。宁可判为未定位
            # （走估算 + 前端标灰），也不让一个错点把整条路线和地图中心带偏。
            if coords:
                ok, dist = is_offset_ok(
                    (coords["lng"], coords["lat"]), center_ref, region
                )
                if not ok:
                    print(f"  ⚠️ {attr['name']} 坐标距目的地中心 {dist:.0f}km，"
                          f"超过上限 {region.max_offset_km:.0f}km，判为错配，改用估算")
                    coords = None

            if coords:
                located.append({**attr, "lng": coords["lng"], "lat": coords["lat"]})
            else:
                failed.append(attr)

        # 定位失败的景点：围绕中心点环形散开，并打 approx 标记
        # （前端据此用虚线/灰色样式提示"位置是估算的"，不再让它顶替掉真实景点）
        if failed:
            center_base = await resolve_center_base(
                located, geo_tool, region, (base_lng, base_lat), center_ref
            )
            print(f"⚠️ {len(failed)} 个景点未精确定位，围绕 {center_base} 估算排布: "
                  f"{[a['name'] for a in failed]}")
            for i, attr in enumerate(failed):
                lng, lat = scatter_around(center_base, i, len(failed))
                located.append({**attr, "lng": lng, "lat": lat, "approx": True})

        attractions_with_coords = located
        print(f"🔍 坐标获取完成: {len(attractions_with_coords)} 个景点"
              f"（精确定位 {len(located) - len(failed)} 个，估算 {len(failed)} 个）")

        # 3. 规划路线（如果有多个景点）
        # 先按天分组再组内串联：原来是在全局序列上取相邻对、靠 day 是否相等来过滤，
        # 一旦提取顺序不是严格按天（例如 Day2 只提到一个景点），相邻对就会落在跨天上
        # 被整段丢弃，那天本该有的路线也跟着没了。按天分组后组内相邻，逻辑与"天内动线"一致。
        routes = []
        day_order: List[int] = []
        grouped: Dict[int, List[Dict]] = {}
        for attr in attractions_with_coords:
            try:
                day_no = int(attr.get("day") or 1)
            except (TypeError, ValueError):
                day_no = 1
            attr["day"] = day_no
            if day_no not in grouped:
                grouped[day_no] = []
                day_order.append(day_no)
            grouped[day_no].append(attr)

        for day_no in day_order:
            day_attrs = grouped[day_no]
            if len(day_attrs) < 2:
                continue
            for i in range(len(day_attrs) - 1):
                origin = day_attrs[i]
                dest = day_attrs[i + 1]
                origin_day = day_no

                try:
                    print(f"🚗 规划路线: {origin['name']} → {dest['name']}")
                    route_info = await plan_driving_route(origin, dest)
                    routes.append({
                        "from": origin["name"],
                        "to": dest["name"],
                        # day：前端按天取路线时直接读这个字段，不再靠"起点名字属于哪天"反推。
                        # 名字反推的隐患是重名景点（如两座城市都有"中山路"）或跨天复用同一个点。
                        "day": origin_day,
                        "from_coord": [origin["lng"], origin["lat"]],
                        "to_coord": [dest["lng"], dest["lat"]],
                        "distance": route_info.get("distance", 0),
                        "duration": route_info.get("duration", 0),
                        "polyline": route_info.get("polyline", [])
                    })
                    print(f"  ✅ 路线规划完成: {route_info.get('distance', 0)}m / "
                          f"{route_info.get('duration', 0)}s / "
                          f"折线 {len(route_info.get('polyline') or [])} 点")
                except Exception as e:
                    print(f"路线规划失败: {e}")
                    routes.append({
                        "from": origin["name"],
                        "to": dest["name"],
                        "day": origin_day,
                        "from_coord": [origin["lng"], origin["lat"]],
                        "to_coord": [dest["lng"], dest["lat"]],
                        "distance": 0,
                        "duration": 0,
                        "polyline": []
                    })

        existing_names = {a["name"] for a in attractions_with_coords}

        for route in routes:
            for point in [route["from"], route["to"]]:
                if point not in existing_names:
                    # 用路线的端点坐标补一个 attraction
                    # day 取路线自己的天数；原来硬编码成 1，相当于把所有补出来的点
                    # 都算到第一天头上
                    coord = route["from_coord"] if point == route["from"] else route["to_coord"]
                    attractions_with_coords.append({
                        "name": point,
                        "day": route.get("day", 1),
                        "lng": coord[0],
                        "lat": coord[1]
                    })
                    existing_names.add(point)
                    print(f"➕ 补全景点: {point} ({coord})")
        # 地图中心取精确坐标的均值（估算点不参与，否则会把中心拉偏）
        precise = [a for a in attractions_with_coords if not a.get("approx")]
        center_source = precise or attractions_with_coords
        if center_source:
            center = (
                round(sum(a["lng"] for a in center_source) / len(center_source), 6),
                round(sum(a["lat"] for a in center_source) / len(center_source), 6),
            )
        else:
            center = (base_lng, base_lat)

        result = {
            "attractions": attractions_with_coords,
            "routes": routes,
            "destination": destination,
            "center": center
        }
        print(f"✅ 地图数据生成完成: {len(result['attractions'])} 个景点, {len(result['routes'])} 段路线")
        return result

    except Exception as e:
        print(f"生成地图数据失败: {e}")
        traceback.print_exc()
        return {}
