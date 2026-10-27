# author: szw
from langchain.tools import tool
from app.rag.retriever import retrieve_context

@tool("retrieve_guides")
async def retrieve_guides_tool(query: str) -> str:
    """从攻略库检索相关文档（RAG）"""
    return retrieve_context(query, top_k=5).get("context")



@tool("get_weather")
async def get_weather_tool(city_name: str) -> str:
    """查询指定城市的天气预报"""
    try:
        from app.services.weather import get_weather
        city_name = city_name.split("市")[0]
        weather_data = await get_weather(city_name)
 
        if not weather_data:
            return f"暂时无法获取{city_name}的天气信息"

        # 格式化返回
        if "forecast" in weather_data:
            forecast = weather_data["forecast"][:5]
            response = f"📍 {city_name}天气预报：\n\n"
            for day in forecast:
                response += f"📅 {day.get('date', '')}: {day.get('type', '')} "
                response += f"{day.get('high', '')}/{day.get('low', '')}\n"
            return response
        else:
            return str(weather_data)

    except Exception as e:
        print(f"❌ 天气查询失败: {e}")
        return f"查询天气失败：{str(e)}"