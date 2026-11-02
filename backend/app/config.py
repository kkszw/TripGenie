# author: szw
from pydantic_settings import BaseSettings
from typing import List
import os
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseSettings):
    # ===== 对话模型：DeepSeek（OpenAI 兼容端点）=====
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    # DeepSeek 思考模式默认开启（effort=high），本项目默认关闭，原因见 agents.py 注释
    deepseek_thinking: str = os.getenv("DEEPSEEK_THINKING", "disabled")

    # 单次 LLM 调用的超时（秒），防止无响应时 SSE 一直挂着
    llm_timeout: int = int(os.getenv("LLM_TIMEOUT", "120"))

    # ===== 向量化：百炼 qwen3.7-text-embedding-flash（OpenAI 兼容模式）=====
    # 未单独配 EMBEDDING_API_KEY 时复用 DASHSCOPE_API_KEY
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
    embedding_base_url: str = os.getenv(
        "EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "qwen3.7-text-embedding-flash")
    # 该模型支持 256/512/768/1024，默认 1024；改了必须重建向量库
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))
    # 官方限制单次请求最多 20 条文本
    embedding_batch_size: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "20"))
    embedding_timeout: int = int(os.getenv("EMBEDDING_TIMEOUT", "30"))
    # collection 名带上模型标识：换模型时新老向量不能混在同一个 collection
    embedding_collection: str = os.getenv("EMBEDDING_COLLECTION", "trip_guides_qwen37_flash")

    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")

    # ===== 登录认证 =====
    # 必须固定，否则每次重启都会换密钥，所有人被强制退出登录
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    jwt_expire_days: int = int(os.getenv("JWT_EXPIRE_DAYS", "7"))

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

    # CORS（.env 里没配也不会崩，给个本地开发默认值；配了则按 .env 的 JSON 数组解析）
    allowed_origins: List[str] = ["http://localhost:5173"]

    # 日志
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


