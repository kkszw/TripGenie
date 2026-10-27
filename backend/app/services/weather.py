# author: szw
import httpx
import json
from app.utils.path_tool import get_absolute_path
from langchain.tools import tool


async def get_city_id(city_name: str) -> str:
    """
    根据省份和市名获取到目标城市的编码
    :param city_name: 市名
    :return: 城市编码
    """
    with open(get_absolute_path("data/cityid.json"), "r", encoding="utf-8") as f:
        city_data = json.load(f)
        for province_item in city_data["城市代码"]:
            for city_item in province_item.get("市"):
                if city_item.get("市名") == city_name:
                    return city_item.get("编码")
    return "0"


async def get_weather(city_name: str):
    """
    获取目标城市的天气预报
    :param city_name: 市名
    :return: 目标城市目标天数的天气状况
    """

    params = await get_city_id(city_name)
    url = "http://t.weather.itboy.net/api/weather/city/"
    try:
        with httpx.Client() as client:
            response = client.get(url + params, timeout=10.0)
            data = response.json().get('data')
            return data
    except Exception:
        return "天气服务请求失败"
