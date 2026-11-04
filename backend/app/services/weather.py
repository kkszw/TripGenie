# author: szw
import json
from functools import lru_cache
from typing import Dict

import httpx

from app.core.logging import get_logger
from app.utils.path_tool import get_absolute_path

logger = get_logger(__name__)

_WEATHER_API = "http://t.weather.itboy.net/api/weather/city/"


@lru_cache(maxsize=1)
def _load_city_index() -> Dict[str, str]:
    """把 cityid.json 拍平成 {市名: 编码}，进程内只读一次

    原来每次查天气都要 open() + json.load 一个 32KB 的文件再两层循环找。
    """
    with open(get_absolute_path("data/cityid.json"), "r", encoding="utf-8") as f:
        city_data = json.load(f)

    index: Dict[str, str] = {}
    for province_item in city_data.get("城市代码", []):
        for city_item in province_item.get("市", []):
            name = city_item.get("市名")
            code = city_item.get("编码")
            if name and code:
                index[name] = code
    return index


async def get_city_id(city_name: str) -> str:
    """根据市名获取城市编码，找不到返回 "0"

    模型可能传「大连」「大连市」「辽宁省大连市」，三种都兼容。
    """
    if not city_name:
        return "0"

    index = _load_city_index()

    for candidate in (city_name, city_name.rstrip("市"), city_name + "市"):
        if candidate in index:
            return index[candidate]

    # 兜底：像"辽宁省大连市"这种带省名的输入
    for name, code in index.items():
        if name in city_name:
            return code

    return "0"


async def get_weather(city_name: str):
    """
    获取目标城市的天气预报
    :param city_name: 市名
    :return: 目标城市目标天数的天气状况
    """
    city_code = await get_city_id(city_name)
    if city_code == "0":
        return f"未找到城市「{city_name}」的天气编码"

    # 原来是同步 httpx.Client() 跑在 async 函数里：一旦这个请求慢下来（超时 10s），
    # 整个事件循环被阻塞，SSE 心跳和其它请求一起卡住。
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(_WEATHER_API + city_code)
            response.raise_for_status()
            data = response.json().get("data")
            return data or "天气服务返回为空"
    except Exception as e:
        logger.warning("天气服务请求失败", extra={"err": str(e)})
        return "天气服务请求失败"
