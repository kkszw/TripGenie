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

    # ===== 向量化：百炼 text-embedding-v4（OpenAI 兼容模式）=====
    # 未单独配 EMBEDDING_API_KEY 时复用 DASHSCOPE_API_KEY
    # 原用 qwen3.7-text-embedding-flash：免费额度（FreeTierOnly）用尽后持续 400，
    # 全量建库拿不到向量，故换成同为 1024 维的 text-embedding-v4（按量计费）。
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
    embedding_base_url: str = os.getenv(
        "EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")
    # 该模型支持 256/512/768/1024，默认 1024；改了必须重建向量库
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))
    # 单次请求的文本条数上限。text-embedding-v4 / v3 都是 10 条，
    # 老模型 qwen3.7-text-embedding-flash 是 20 条。取 10 对两者都成立——
    # 超限时服务端直接 400（"batch size is invalid, it should not be larger than 10"），
    # 不会自动降级，所以这里必须是保守值。
    embedding_batch_size: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "10"))
    embedding_timeout: int = int(os.getenv("EMBEDDING_TIMEOUT", "30"))
    # collection 名带上模型标识：换模型时新老向量不能混在同一个 collection
    embedding_collection: str = os.getenv("EMBEDDING_COLLECTION", "trip_guides_v4")

    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")

    # ===== 登录认证 =====
    # 必须固定，否则每次重启都会换密钥，所有人被强制退出登录
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    jwt_expire_days: int = int(os.getenv("JWT_EXPIRE_DAYS", "7"))

    # 高德地图
    amap_api_key: str = os.getenv("AMAP_API_KEY")
    # 折线抽稀容差（米）：删掉的点到保留折线的偏离不超过它，形状不变。
    # 设 0 关闭几何抽稀；调大会更省体积，但急弯处会被削平。
    map_polyline_tolerance_m: float = float(os.getenv("MAP_POLYLINE_TOLERANCE_M", "6"))
    # 单段路线折线点数上限（兜底，正常情况下 DP 抽稀后远低于它）
    map_polyline_max_points: int = int(os.getenv("MAP_POLYLINE_MAX_POINTS", "400"))
    # 地图数据生成的并发度（同时打给高德的请求数）。
    # 个人 Key 的 QPS 很低（实测并发 4 就会稳定触发 10021 限流，景点因此
    # 掉到估算位置），默认压到 3；撞限流时 amap_get 会退避重试，表现为慢一点
    # 而不是结果错。设 1 等于退回串行，怀疑限流时可以先这样隔离问题。
    map_concurrency: int = int(os.getenv("MAP_CONCURRENCY", "3"))

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

    # ===== 日志与度量 =====
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    # 日志文件和 runs.jsonl 的目录，留空用 backend/logs
    log_dir: str = os.getenv("LOG_DIR", "")
    # 每次规划结束是否往 logs/runs.jsonl 追加一行摘要
    run_log_enabled: bool = os.getenv("RUN_LOG_ENABLED", "true").lower() in ("1", "true", "yes")
    # LLM 单价（元 / 百万 token），用于估算成本。
    # 留 0 表示只统计 token 不折算金额 —— 单价会变，写死在代码里就是定时炸弹。
    llm_input_price: float = float(os.getenv("DEEPSEEK_INPUT_PRICE", "0"))
    llm_output_price: float = float(os.getenv("DEEPSEEK_OUTPUT_PRICE", "0"))

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


