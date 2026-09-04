# author: szw
import aiohttp
from app.config import settings


async def search_attractions(city: str, keyword: str = "", limit: int = 10):
    """搜索景点（高德地图 POI）"""
    # 这里使用高德地图 API
    # 由于需要真实 API Key，提供 Mock 数据方便测试

    # 模拟数据
    mock_attractions = [
        {"name": f"{city}市中心广场", "location": "116.397,39.908", "rating": 4.5},
        {"name": f"{city}历史文化街区", "location": "116.400,39.910", "rating": 4.3},
        {"name": f"{city}国家公园", "location": "116.395,39.905", "rating": 4.7},
        {"name": f"{city}博物馆", "location": "116.398,39.907", "rating": 4.2},
        {"name": f"{city}美食街", "location": "116.402,39.912", "rating": 4.6},
    ][:limit]

    return {
        "data": mock_attractions,
        "total": len(mock_attractions)
    }

    # 真实 API 调用（有 Key 时使用）
    # url = f"https://restapi.amap.com/v3/place/text"
    # params = {
    #     "key": settings.amap_api_key,
    #     "keywords": keyword or city,
    #     "types": "旅游景点",
    #     "city": city,
    #     "offset": limit,
    #     "page": 1
    # }
    # async with aiohttp.ClientSession() as session:
    #     async with session.get(url, params=params) as resp:
    #         data = await resp.json()
    #         return data


async def calculate_distance(origin: str, destination: str, mode: str = "driving"):
    """计算两地距离"""
    # Mock 数据
    return {
        "distance": "5.2km",
        "duration": "15分钟",
        "mode": mode
    }




# import requests
#
# """
# 126.622462,45.707831  (黑龙江大学)
# 121.675131,38.879093  (大连老虎滩)
# 121.588870,38.882379  (星海广场)
# """
# def get_driving_route(origin, destination):
#     """
#     使用高德驾车路径规划API获取路线信息
#     """
#     url = "https://restapi.amap.com/v3/direction/driving"
#
#     params = {
#         "key": settings.amap_api_key,  # 高德开放平台申请的Key
#         "origin": origin,  # 起点经纬度，如 "116.397428,39.90923"
#         "destination": destination,  # 终点经纬度
#         "extensions": "all"  # 返回详细信息
#     }
#
#     response = requests.get(url, params=params)
#     data = response.json()
#
#     if data["status"] == "1":
#         route = data["route"]["paths"][0]
#         print(f"距离: {route['distance']}米")
#         print(f"耗时: {route['duration']}秒")
#         print(f"预计花费: {data["route"].get('taxi_cost', 'N/A')}元")
#         return route
#     else:
#         print(f"路线规划失败: {data['info']}")
#         return None
#
# origin = "121.675131,38.879093"
# destination = "121.588870,38.882379"
# get_driving_route(origin, destination)