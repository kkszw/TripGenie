# author: szw
"""高德 Web 服务（REST）封装：POI 搜索、地理编码解析、驾车路线。

为什么不直接走 MCP：MCP 每次调用都要拉起子进程（约 1s），REST 直连一次 HTTP
只要 50~150ms；而且 MCP 的驾车包把 steps 里的折线丢了，前端画不出路线。
这里保留 MCP 只作为降级方案。
"""
import asyncio
import json
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings
from app.core.json_utils import as_number
from app.core.logging import get_logger
from app.services.geo import Region
from app.services.polyline import simplify_polyline

logger = get_logger(__name__)

AMAP_PLACE_TEXT_URL = "https://restapi.amap.com/v3/place/text"
AMAP_DRIVING_URL = "https://restapi.amap.com/v3/direction/driving"

# POI 名字里带这些词的通常是停车场、出入口之类的配套设施，不是景点本体
_POI_NOISE = ("停车场", "停车楼", "售票处", "出入口", "公厕", "卫生间",
              "地铁站", "公交站", "充电站", "管理中心")

# MCP 工具底下都挂在同一条 stdio 会话上，适配器对并发请求的行为没有文档保证。
# 而 MCP 兜底只在 REST 失败时才走——那时候本来就已经慢了，串行换确定性更划算。
# REST 路径不受这把锁影响，并发照常。
_MCP_LOCK = asyncio.Lock()


def _pick_best_poi(name: str, pois: List[Dict]) -> Optional[Dict]:
    """从 POI 搜索结果里挑和目标名字最贴近的一条

    高德 REST 返回是按相关性排序的，所以给首位一点权重 —— 像"椰梦长廊"这种
    横跨 20km 的线性景点，首位（椰林海滩，中段）比"椰梦长廊灯塔"（最西端）更贴合行程。
    """
    best: Optional[Dict] = None
    best_score = -1
    for i, p in enumerate(pois):
        loc = p.get("location") or ""
        if not isinstance(loc, str) or "," not in loc:
            continue
        pname = p.get("name") or ""
        score = 0
        if pname == name:
            score += 100
        elif name and name in pname:
            # 包含但更长（"椰梦长廊" vs "椰梦长廊-椰林海滩"），长度差越大越不像本体
            score += 60 - max(0, len(pname) - len(name)) * 2
        elif pname and pname in name:
            score += 40
        else:
            score += 5
        if any(w in pname for w in _POI_NOISE):
            score -= 50
        if i == 0:
            score += 12
        if score > best_score:
            best_score, best = score, p
    return best if best_score > 0 else None


async def poi_search_rest(name: str, region: "Region") -> Optional[Dict]:
    """高德 REST POI 关键词搜索，作为 maps_geo 的降级方案

    为什么要多这一步：maps_geo 是"地址→坐标"解析，遇到非规范名称（LLM 常自己编，
    比如把「三亚国际免税城」写成「三亚免税城」）时不报错，而是返回市/区县中心点。
    换成 POI 搜索就能命中「中免集团·三亚国际免税城」这类真实条目。

    地域约束走 region.query_value：只给了省份时传的是省级 adcode，
    否则"芙蓉街"这种全国都有同名的关键词会命中云南那条。
    citylimit 只在限定到城市时才加——高德对省级 adcode 配 citylimit 的行为没有文档保障。
    """
    key = getattr(settings, "amap_api_key", None)
    if not key or not name:
        return None

    params = {"key": key, "keywords": name, "offset": 10, "page": 1, "extensions": "base"}
    city_value = region.query_value if region else None
    if city_value:
        params["city"] = city_value
        if region.city:
            params["citylimit"] = "true"

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(AMAP_PLACE_TEXT_URL, params=params)
            data = resp.json()
    except Exception as e:
        logger.warning("POI 搜索请求失败", extra={"poi": name, "err": str(e)})
        return None

    if str(data.get("status")) != "1":
        logger.warning("POI 搜索返回异常",
                       extra={"poi": name, "info": data.get("info"),
                              "infocode": data.get("infocode")})
        return None

    pois = data.get("pois") or []
    if not pois:
        return None

    best = _pick_best_poi(name, pois)
    if not best:
        return None
    lng_str, lat_str = best["location"].split(",")[:2]
    logger.debug("POI 搜索命中",
                 extra={"query": name, "hit": best.get("name"),
                        "district": best.get("adname"), "coord": f"{lng_str},{lat_str}"})
    return {"lng": float(lng_str), "lat": float(lat_str)}


def unwrap_mcp_result(result: Any) -> Optional[Dict]:
    """把 MCP 工具的返回值还原成 dict。

    langchain-mcp-adapters 不会把工具返回的 dict 原样交出来，而是序列化成
    ``[{'type': 'text', 'text': '{"route": ...}'}]``（多个内容块就是多个元素）；
    这里同时兼容：直接 dict、JSON 字符串、ToolMessage（带 .content）。
    """
    if result is None:
        return None
    if isinstance(result, dict):
        return result
    # ToolMessage 之类的消息对象：取 content 继续往下走
    if not isinstance(result, (list, tuple, str, bytes)) and hasattr(result, "content"):
        result = result.content
    if isinstance(result, bytes):
        result = result.decode("utf-8", "ignore")
    if isinstance(result, (list, tuple)):
        texts: List[str] = []
        structured: List[Dict] = []
        for item in result:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict):
                if "text" in item and item.get("type") in (None, "text"):
                    texts.append(str(item.get("text") or ""))
                else:
                    structured.append(item)   # 本身就已经是结构化 payload
        if texts:
            result = "".join(texts)
        elif len(structured) == 1:
            return structured[0]
        else:
            return None
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def parse_polyline(raw: Any) -> List[List[float]]:
    """把高德折线转成前端要的 [[lng, lat], ...]。

    高德 v3 驾车接口的折线在 ``paths[0]["path"]`` 里，形如 "116.1,39.1;116.2,39.2"，
    **没有** ``polyline`` 字段。老代码取的是 path.get("polyline")，永远拿不到，
    于是静默变成 []，前端只能退化成起终点之间的一条虚线（看起来"只有点没有路线"）。
    这里同时兼容字符串、已解析的数组、以及 step 级别的 polyline 片段。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        points: List[List[float]] = []
        for pair in raw.split(";"):
            parts = pair.strip().split(",")
            if len(parts) < 2:
                continue
            try:
                lng, lat = float(parts[0]), float(parts[1])
            except (TypeError, ValueError):
                continue
            if -180.0 <= lng <= 180.0 and -90.0 <= lat <= 90.0:
                points.append([round(lng, 6), round(lat, 6)])
        return points
    if isinstance(raw, (list, tuple)):
        points = []
        for item in raw:
            if isinstance(item, str):
                points.extend(parse_polyline(item))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    lng, lat = float(item[0]), float(item[1])
                except (TypeError, ValueError):
                    continue
                if -180.0 <= lng <= 180.0 and -90.0 <= lat <= 90.0:
                    points.append([round(lng, 6), round(lat, 6)])
        return points
    return []


def parse_route_result(result: Any) -> Dict:
    """解析高德驾车路径规划结果。

    统一输出 {"distance": 米, "duration": 秒, "polyline": [[lng, lat], ...]}。
    解析不出来时**打印原因**，不再静默返回 0 / 空折线 —— 之前就是这样
    既看不到路线，日志还写着"路线规划完成"。
    """
    empty = {"distance": 0, "duration": 0, "polyline": []}
    payload = unwrap_mcp_result(result)
    if not isinstance(payload, dict):
        logger.warning("路线结果无法解析",
                       extra={"type": type(result).__name__, "head": str(result)[:200]})
        return empty
    if payload.get("error"):
        logger.warning("路线接口报错", extra={"error": payload["error"]})
        return empty

    route = payload.get("route") if isinstance(payload.get("route"), dict) else payload
    paths = route.get("paths") or []
    if not paths and ("path" in route or "distance" in route):
        paths = [route]
    if not paths:
        logger.warning("路线结果里没有 paths", extra={"keys": list(payload)[:8]})
        return empty

    path = paths[0]
    distance = as_number(path.get("distance")) or 0
    duration = as_number(path.get("duration")) or 0
    polyline = parse_polyline(path.get("path") or path.get("polyline"))

    # 折线主要来源：steps[].polyline（高德 v3 驾车实测就只有这里），首尾相接拼起来
    if not polyline:
        chunks = [
            step.get("polyline") or step.get("path") or ""
            for step in (path.get("steps") or [])
            if isinstance(step, dict)
        ]
        joined = parse_polyline(";".join(c for c in chunks if isinstance(c, str)))
        # 相邻路段首尾点会重复，去一下重（否则前端折线有几段会多出一个重复点）
        for point in joined:
            if not polyline or polyline[-1] != point:
                polyline.append(point)

    if not polyline:
        logger.warning("拿到距离但没有折线，前端画不出这段路线",
                       extra={"distance_m": distance, "path_keys": list(path)[:8]})

    # 抽稀：这段折线接着会进 SSE、进 SQLite 历史，还要供下次回放。
    # 原始 200~400 点在屏幕上看不出区别，按 6 米容差压到几十点，
    # 形状不变（DP 保证被删点偏离不超过容差），消息体积能降一个量级。
    raw_count = len(polyline)
    if raw_count > 2:
        polyline = simplify_polyline(
            polyline,
            tolerance_m=settings.map_polyline_tolerance_m,
            max_points=settings.map_polyline_max_points,
        )
        if raw_count >= 100:
            logger.debug("折线抽稀", extra={"before": raw_count, "after": len(polyline),
                                         "ratio": f"{len(polyline) / raw_count:.0%}"})

    return {"distance": distance, "duration": duration, "polyline": polyline}


async def driving_route_rest(origin: Dict, dest: Dict) -> Optional[Dict]:
    """高德 Web 服务 REST 驾车路径规划（**带完整折线**）

    为什么不直接用 MCP 的 maps_direction_driving_by_coordinates：
    那个包把 steps 精简成 instruction/road/distance 几个字段，把折线 `polyline` 丢了，
    path 级又只有 distance/duration —— 于是前端永远拿不到 polyline，
    只能退化成起终点之间的一条虚线（看起来"地图上只有点、没有路线"）。
    REST 直连一次 HTTP 约 50~150ms，比拉 MCP 子进程还快。
    """
    key = getattr(settings, "amap_api_key", None)
    if not key:
        return None

    try:
        params = {
            "key": key,
            "origin": f"{float(origin['lng']):.6f},{float(origin['lat']):.6f}",
            "destination": f"{float(dest['lng']):.6f},{float(dest['lat']):.6f}",
            "extensions": "base",
        }
    except (KeyError, TypeError, ValueError):
        return None

    # 高德对并发有 QPS 限制（个人 Key 很低），连续请求会回 10021，
    # 所以这里遇到 QPS 超限退避重试几次，别直接把这条路线丢掉。
    data: Dict = {}
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(AMAP_DRIVING_URL, params=params)
                data = resp.json()
        except Exception as e:
            logger.warning("驾车路线 REST 请求失败", extra={"err": str(e)})
            data = {}
        if str(data.get("status")) == "1":
            break
        if str(data.get("infocode")) == "10021" and attempt < 2:
            logger.debug("高德 QPS 超限，退避重试", extra={"attempt": attempt + 1})
            await asyncio.sleep(0.5 * (attempt + 1))
            continue
        logger.warning("驾车路线 REST 返回异常",
                       extra={"info": data.get("info"), "infocode": data.get("infocode")})
        return None

    if str(data.get("status")) != "1":
        return None

    # extensions=base 时 steps 里也带 polyline；parse_route_result 会按顺序拼起来
    return parse_route_result(data)


async def plan_driving_route(origin: Dict, dest: Dict) -> Dict:
    """一段驾车路线：REST 优先（有折线），失败再退回 MCP（只有距离/时长）。"""
    # 注意：tools_by_name 是进程启动后才装好的可变全局，必须走模块属性读取，
    # 直接 from ... import tools_by_name 会永远拿到导入那一刻的 None。
    from app.core import llm as llm_runtime

    info = await driving_route_rest(origin, dest)
    if info and info.get("polyline"):
        return info

    driving_tool = llm_runtime.tools_by_name.get("maps_direction_driving_by_coordinates")
    if driving_tool:
        try:
            async with _MCP_LOCK:
                result = await driving_tool.ainvoke({
                    "origin": f"{origin['lng']},{origin['lat']}",
                    "destination": f"{dest['lng']},{dest['lat']}"
                })
            mcp_info = parse_route_result(result)
            # MCP 只补距离/时长；折线还是以 REST 的为准（MCP 那份是空的）
            if mcp_info.get("distance") or mcp_info.get("duration"):
                if not info:
                    return mcp_info
                info["distance"] = info.get("distance") or mcp_info["distance"]
                info["duration"] = info.get("duration") or mcp_info["duration"]
        except Exception as e:
            logger.warning("MCP 备用路线也失败", extra={"err": str(e)})

    return info or {"distance": 0, "duration": 0, "polyline": []}


# 高德地理编码结果里的"行政区级" level 取值。这类结果的 location 是城市/区县中心点，
# 不是具体景点坐标。以前不校验它，导致查不到的景点全部落到同一个城市中心点上
# （三亚"椰梦长廊"和"三亚免税城"就是这样重合的，地图上只看得见后画的那个点）。
# 注意高德的取值是"省"/"市"/"区县"，不是"省份"/"城市"，实测得到的。
_ADMIN_LEVELS = {"国家", "省", "省份", "市", "城市", "区县", "开发区"}


def _is_admin_level(item: Dict) -> bool:
    """判断一条 geocode 结果是否只定位到行政区，而非具体地点"""
    if not isinstance(item, dict):
        return False
    level = (item.get("level") or "").strip()
    if level in _ADMIN_LEVELS:
        return True
    if not level:
        # 老接口没有 level 字段，用 district 兜底：真实 POI 一定有区县归属
        return not item.get("district")
    return False


def parse_coordinates(result, allow_admin: bool = False) -> Dict:
    """解析高德地理编码结果（适配 MCP 返回格式）

    Args:
        result: MCP 返回结果
        allow_admin: 是否接受"只定位到行政区"的结果。
            False（默认）：景点用，行政区级结果直接丢弃，交给上层做估算兜底；
            True：需要城市中心点时用（例如给定位失败的景点找估算基准）。
    """
    try:
        if not result:
            return {}

        # ✅ 情况1：MCP 返回格式 list[{'type': 'text', 'text': '{...}'}]
        if isinstance(result, list) and len(result) > 0:
            item = result[0]
            if isinstance(item, dict) and "text" in item:
                text = item["text"]
                parsed = False
                try:
                    data = json.loads(text)
                    parsed = True
                    coords = extract_location_from_dict(data, allow_admin=allow_admin)
                    if coords:
                        return coords
                except json.JSONDecodeError:
                    pass
                # 注意：JSON 解析成功但被校验拒绝时，绝不能再用正则兜底 ——
                # 正则会从同一段文本里把那个"行政区中心点"又抓出来，校验就白做了。
                if parsed:
                    return {}
                return extract_location_from_text(text)

        # ✅ 情况2：字符串（可能是 JSON 字符串）
        if isinstance(result, str):
            parsed = False
            try:
                data = json.loads(result)
                parsed = True
                coords = extract_location_from_dict(data, allow_admin=allow_admin)
                if coords:
                    return coords
            except json.JSONDecodeError:
                pass
            if parsed:
                return {}
            return extract_location_from_text(result)

        # ✅ 情况3：dict（兼容旧格式）
        if isinstance(result, dict):
            coords = extract_location_from_dict(result, allow_admin=allow_admin)
            if coords:
                return coords

    except Exception as e:
        logger.warning("解析坐标失败", extra={"err": str(e)})

    return {}


def extract_location_from_dict(data, allow_admin: bool = False) -> Dict:
    """从 dict 中提取 location

    allow_admin=False 时，只定位到行政区（level=城市/区县）的结果会被丢弃，
    避免把城市中心当成景点坐标。
    """
    try:
        if isinstance(data, dict):
            # 高德 MCP 返回：{"return": [{"location": "121.63,38.92", ...}]}
            returns = data.get("return", [])
            if returns and len(returns) > 0:
                item = returns[0]
                if not allow_admin and _is_admin_level(item):
                    logger.debug("只定位到行政区，坐标不是景点，丢弃",
                                 extra={"level": item.get("level"),
                                        "district": item.get("district")})
                    return {}
                loc = item.get("location", "")
                if loc and "," in loc:
                    parts = loc.split(",")
                    return {"lng": float(parts[0]), "lat": float(parts[1])}

            # 兼容旧格式：{"geocodes": [{"location": "..."}]}
            geocodes = data.get("geocodes", [])
            if geocodes and len(geocodes) > 0:
                item = geocodes[0]
                if not allow_admin and _is_admin_level(item):
                    return {}
                loc = item.get("location", "")
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
        logger.warning("extract_location_from_dict 异常", extra={"err": str(e)})
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
        logger.warning("extract_location_from_text 异常", extra={"err": str(e)})
    return {}
