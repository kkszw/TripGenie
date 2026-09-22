# author: szw
from pydantic_settings import BaseSettings
from typing import List
import os
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseSettings):
    # 阿里云百炼
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY")
    DASHSCOPE_BASE_URL: str = os.getenv("DASHSCOPE_BASE_URL")
    DASHSCOPE_MODEL: str = os.getenv("DASHSCOPE_MODEL")
    DASHSCOPE_EMBEDDINGS: str = os.getenv("DASHSCOPE_EMBEDDINGS")

    # 高德地图
    amap_api_key: str = os.getenv("AMAP_API_KEY")

    # OpenWeather
    weather_api_key: str = os.getenv("WEATHER_API_KEY")

    # MCP 工具运行时（uvx）。留空则依次尝试 PATH、当前虚拟环境
    uvx_path: str = os.getenv("UVX_PATH", "")

    # 景点地理编码失败时的坐标兜底中心
    default_map_lng: float = float(os.getenv("DEFAULT_MAP_LNG", "121.6186"))
    default_map_lat: float = float(os.getenv("DEFAULT_MAP_LAT", "38.9146"))

    # 路径
    chroma_db_path: str = os.getenv("CHROMA_DB_PATH")
    checkpoint_db_path: str = os.getenv("CHECKPOINT_DB_PATH")

    # CORS
    allowed_origins: List[str] = os.getenv("ALLOWED_ORIGINS")

    # 日志
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


