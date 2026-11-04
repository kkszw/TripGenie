# author: szw
"""把一份行程变成地图数据：景点提取 → 地理编码 → 每日路线。

这里不碰 LangGraph 的 state，入参出参都是普通 dict，
只有 nodes/map.py 那一层负责读写 state。
"""
import asyncio
import json
import math
from typing import Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage

from app.config import settings
from app.core import llm as llm_runtime
from app.core.logging import get_logger
from app.core.prompts import build_attraction_prompt
from app.services.amap import parse_coordinates, plan_driving_route, poi_search_rest
from app.services.geo import Region, is_offset_ok, parse_region

logger = get_logger(__name__)


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
        logger.debug("开始用 LLM 提取景点")
        # 用 llm_extract（streaming=False）：这里的输出是给程序看的中转数据，
        # 不能进 SSE 正文，否则会跟在行程 JSON 后面一起推给前端。
        # internal 是第二道保险：万一以后有人图省事又改回 llm，api/chat.py 看到
        # 这个标记也会跳过，不会再漏进正文。
        response = await llm_runtime.llm_extract.ainvoke(
            [HumanMessage(content=prompt)], config={"tags": ["internal"]}
        )
        content = response.content

        logger.debug("景点提取响应", extra={"chars": len(content)})

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
                logger.warning("无法从 LLM 响应中提取 JSON", extra={"head": content[:120]})
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

        logger.info("景点提取完成", extra={"count": len(result)})
        return result

    except Exception as e:
        logger.exception("LLM 提取景点失败", extra={"err": str(e)})
        return []


async def geocode_attraction(geo_tool, name: str, region: Region) -> Optional[Dict]:
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
            logger.debug("地理编码", extra={"poi": name, "query": base,
                                         "region": region.label or "全国"})
            result = await geo_tool.ainvoke({"address": base, "city": city_value})
            coords = parse_coordinates(result)
            if coords:
                logger.debug("坐标命中", extra={"poi": name, **coords})
                return coords
            logger.debug("未定位到具体地点", extra={"query": base})
        except Exception as e:
            logger.warning("地理编码异常", extra={"query": base, "err": str(e)})

    # 2) POI 关键词搜索兜底
    coords = await poi_search_rest(name, region)
    if coords:
        return coords

    # 3) 裸名地理编码
    if geo_tool and base != name:
        try:
            logger.debug("地理编码（裸名）", extra={"poi": name,
                                                 "region": region.label or "全国"})
            result = await geo_tool.ainvoke({"address": name, "city": city_value})
            coords = parse_coordinates(result)
            if coords:
                logger.debug("坐标命中", extra={"poi": name, **coords})
                return coords
            logger.debug("未定位到具体地点", extra={"query": name})
        except Exception as e:
            logger.warning("地理编码异常", extra={"query": name, "err": str(e)})

    # 三级都失败用 warning 而不是 debug：这意味着地图上这个点是估算位置，
    # 用户看到的是"大概在这一带"，属于需要知道的降级，不该被日志级别过滤掉。
    logger.warning("景点三级查询均失败，改用估算坐标", extra={"poi": name})
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
            logger.debug("目的地中心", extra={"region": region.label, **coords})
            return coords["lng"], coords["lat"]
        logger.warning("未取到目的地中心，本轮跳过坐标合理性校验",
                       extra={"region": region.label})
    except Exception as e:
        logger.warning("获取目的地中心失败", extra={"err": str(e)})
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
    # 走到这里说明连目的地中心都拿不到，只能退回 .env 里的固定坐标。
    # 这是明确的地理位置错误来源（三亚的景点会被画到大连），必须是 warning。
    logger.warning("拿不到目的地中心，回退到 .env 兜底坐标", extra={"fallback": fallback})
    return fallback


async def locate_attraction(geo_tool, attr: Dict, region: Region,
                            center_ref: Optional[Tuple[float, float]],
                            sem: asyncio.Semaphore) -> Optional[Dict]:
    """定位一个景点、并校验坐标是否落在目的地合理范围内，返回它的最终条目。

    返回 None 表示"没定位到"或"定位到了但判定为错配"，两种都交给上层做估算排布。

    单独抽成函数是为了让调用方能用 gather 并发跑多个。并发只改变"什么时候发请求"，
    不改判定标准——校验逻辑与串行版逐行一致，否则并发带来的随机顺序会让
    相同输入产出不同地图，那比慢更糟。
    """
    async with sem:
        # 目的地只到省一级时（"山东"），光有省 adcode 挡不住省内同名地点：
        # 实测"玉皇顶"会被匹配到临沂那座山、"孔林"会落到菏泽一片。
        # 景点自带的 city 是行程原文写明的（"乘车前往泰安"），用它把范围收到城市级。
        # 用户已经指定了城市时一律以用户为准，不信模型的判断。
        attr_region = region
        if region.scope != "city" and attr.get("city"):
            attr_region = Region(
                city=attr["city"], province=region.province, scope="city"
            )
        try:
            coords = await geocode_attraction(geo_tool, attr["name"], attr_region)
        except Exception as e:
            # geocode_attraction 内部已经各自兜了异常，这里是最后一道：
            # 一个景点炸掉不该让 gather 把整份地图带走。
            logger.warning("景点定位异常，改用估算坐标",
                           extra={"poi": attr["name"], "err": str(e)})
            coords = None

        if not coords:
            return None

        # 坐标合理性校验：高德对同名地点是"谁相关取谁"，不校验就会把
        # 济南的芙蓉街变成云南那条、大明湖变成新疆那片。宁可判为未定位
        # （走估算 + 前端标灰），也不让一个错点把整条路线和地图中心带偏。
        ok, dist = is_offset_ok((coords["lng"], coords["lat"]), center_ref, region)
        if not ok:
            logger.warning("景点坐标超出目的地范围，判为错配改用估算",
                           extra={"poi": attr["name"], "dist_km": round(dist),
                                  "limit_km": round(region.max_offset_km),
                                  "coord": f"{coords['lng']},{coords['lat']}"})
            return None

        return {**attr, "lng": coords["lng"], "lat": coords["lat"]}


async def plan_segment(day_no: int, origin: Dict, dest: Dict,
                       sem: asyncio.Semaphore) -> Dict:
    """规划一天里相邻两个景点之间的一段路线。

    无论成功失败都返回一条 route 记录：失败时距离/时长为 0、折线为空，
    前端会退化成起终点之间的直线——这比这段路线直接消失要好，
    用户至少知道这两点之间是要走的。
    """
    async with sem:
        try:
            route_info = await plan_driving_route(origin, dest)
            logger.debug("路线规划完成",
                         extra={"from": origin["name"], "to": dest["name"],
                                "distance_m": route_info.get("distance", 0),
                                "polyline_pts": len(route_info.get("polyline") or [])})
        except Exception as e:
            logger.warning("路线规划失败",
                           extra={"from": origin["name"], "to": dest["name"],
                                  "err": str(e)})
            route_info = {}
        return {
            "from": origin["name"],
            "to": dest["name"],
            # day：前端按天取路线时直接读这个字段，不再靠"起点名字属于哪天"反推。
            # 名字反推的隐患是重名景点（如两座城市都有"中山路"）或跨天复用同一个点。
            "day": day_no,
            "from_coord": [origin["lng"], origin["lat"]],
            "to_coord": [dest["lng"], dest["lat"]],
            "distance": route_info.get("distance", 0),
            "duration": route_info.get("duration", 0),
            "polyline": route_info.get("polyline", []),
        }


async def generate_map_data(state, plan_data: dict) -> Dict:
    """生成地图数据：景点坐标和路线"""
    try:
        logger.info("开始生成地图数据")
        # 地图数据依赖 maps_geo / 驾车路线的工具对象，这里保证工具已加载
        await llm_runtime.ensure_tools()
        destination = state.get("destination", "")

        # 1. 提取所有景点
        attractions = await extract_attractions_with_llm(destination, plan_data)
        logger.debug("待定位景点数", extra={"count": len(attractions)})

        # 2. 获取景点坐标（使用高德 MCP）
        geo_tool = llm_runtime.tools_by_name.get("maps_geo")
        attractions_with_coords = []

        # 地理编码失败时的兜底坐标（见 .env 的 DEFAULT_MAP_LNG/LAT）
        base_lng, base_lat = settings.default_map_lng, settings.default_map_lat

        # 目的地解析：城市 / 只有省份 / 认不出。
        # 只给了省份时（"帮我规划山东三天"），高德必须拿到省级 adcode 才会把结果
        # 限制在省内；以前 city 传空串，全国同名地点随便匹配，"大明湖"能落到新疆。
        region = parse_region(destination)
        logger.info("目的地解析", extra={"destination": destination, "city": region.city,
                                      "province": region.province, "scope": region.scope,
                                      "query": region.query_value})

        # 目的地中心点，用于校验景点坐标是否跑偏
        center_ref = await get_region_center(geo_tool, region)

        # 精确定位成功的和失败的先分开，失败的最后统一做估算排布
        located: List[Dict] = []
        failed: List[Dict] = []

        if not geo_tool:
            logger.warning("geo_tool 不存在，所有景点都将使用估算坐标")

        # 并发定位：以前是一个接一个 await，10 个景点光地理编码就要十几秒，
        # 而它们之间没有任何依赖。并发度由 MAP_CONCURRENCY 控制——
        # 个人高德 Key 的 QPS 很低，无节制并发只会换来一串 10021 和退避重试。
        # gather 保证结果顺序与输入一致，所以下面的 located/failed 仍按原顺序排，
        # 地图和路线的呈现不会因为并发而变。
        sem = asyncio.Semaphore(max(1, settings.map_concurrency))
        results = await asyncio.gather(*(
            locate_attraction(geo_tool, attr, region, center_ref, sem)
            for attr in attractions
        ))
        for attr, item in zip(attractions, results):
            if item:
                located.append(item)
            else:
                failed.append(attr)

        # 定位失败的景点：围绕中心点环形散开，并打 approx 标记
        # （前端据此用虚线/灰色样式提示"位置是估算的"，不再让它顶替掉真实景点）
        if failed:
            center_base = await resolve_center_base(
                located, geo_tool, region, (base_lng, base_lat), center_ref
            )
            logger.warning("部分景点未精确定位，围绕中心估算排布",
                           extra={"count": len(failed), "center": center_base,
                                  "names": [a["name"] for a in failed]})
            for i, attr in enumerate(failed):
                lng, lat = scatter_around(center_base, i, len(failed))
                located.append({**attr, "lng": lng, "lat": lat, "approx": True})

        attractions_with_coords = located
        logger.info("坐标获取完成", extra={"total": len(attractions_with_coords),
                                       "precise": len(located) - len(failed),
                                       "approx": len(failed)})

        # 3. 规划路线（如果有多个景点）
        # 先按天分组再组内串联：原来是在全局序列上取相邻对、靠 day 是否相等来过滤，
        # 一旦提取顺序不是严格按天（例如 Day2 只提到一个景点），相邻对就会落在跨天上
        # 被整段丢弃，那天本该有的路线也跟着没了。按天分组后组内相邻，逻辑与"天内动线"一致。
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

        pairs: List[Tuple[int, Dict, Dict]] = []
        for day_no in day_order:
            day_attrs = grouped[day_no]
            if len(day_attrs) < 2:
                continue
            for i in range(len(day_attrs) - 1):
                pairs.append((day_no, day_attrs[i], day_attrs[i + 1]))

        # 同样并发：段与段之间互不依赖，而每段是一次 HTTP 往返。
        # gather 保序，routes 的顺序与"按天、天内按顺序"完全一致，前端不用改。
        route_sem = asyncio.Semaphore(max(1, settings.map_concurrency))
        routes = list(await asyncio.gather(*(
            plan_segment(day_no, origin, dest, route_sem)
            for day_no, origin, dest in pairs
        )))

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
                    logger.debug("补全路线端点", extra={"poi": point})
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
        logger.info("地图数据生成完成",
                    extra={"attractions": len(result["attractions"]),
                           "routes": len(result["routes"])})
        return result

    except Exception as e:
        logger.exception("生成地图数据失败", extra={"err": str(e)})
        return {}
