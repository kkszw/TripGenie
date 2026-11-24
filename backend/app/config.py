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
    deepseek_thinking: str = os.getenv("DEEPSEEK_THINKING", "disabled")

    # 单次 LLM 调用的超时（秒），防止无响应时 SSE 一直挂着
    llm_timeout: int = int(os.getenv("LLM_TIMEOUT", "120"))

    # 单个工具调用的超时（秒）。MCP 调用自身没有超时，响应丢了就永久挂起；
    # 不设这层，一个工具卡住会把整条 SSE 拖到外层超时才结束。
    tool_timeout: int = int(os.getenv("TOOL_TIMEOUT", "30"))

    # 注入模型的对话历史 token 预算，超出就丢最旧的消息（0 = 不限制）。
    # 按 token 而不是条数：50 条短问答只有几百 token，50 条带行程 JSON 的
    # 对话能到几万，条数完全约束不住成本，也挡不住超窗口。
    chat_history_max_tokens: int = int(os.getenv("CHAT_HISTORY_MAX_TOKENS", "6000"))

    # 单次检索拼进 prompt 的资料 token 上限（0 = 不限制）。
    # 攻略检索和景点检索各调一次 retrieve_context，实测各 5 篇约 3900 token，
    # 两个节点合计能到 7800——它和对话历史是两份互不相干的预算，以前只有
    # 历史有上限，资料这一路完全敞开。超出按整篇从尾部截（尾部相关性最低）。
    rag_context_max_tokens: int = int(os.getenv("RAG_CONTEXT_MAX_TOKENS", "3000"))

    # ===== 请求级限流 =====
    # 三个都为 0 时关闭限流。默认值按"一个人正常聊天"给：
    # 稳态 10 条/分钟、允许一次连发 3 条、同时最多 2 条流在跑。
    # 一次规划要跑几十秒，并发上限比速率更能挡住"连点发送"。
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))
    rate_limit_burst: int = int(os.getenv("RATE_LIMIT_BURST", "3"))
    rate_limit_max_concurrent: int = int(os.getenv("RATE_LIMIT_MAX_CONCURRENT", "2"))

    # ===== 向量化：百炼 text-embedding-v4（OpenAI 兼容模式）=====
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
    embedding_base_url: str = os.getenv(
        "EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))
    embedding_batch_size: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "10"))
    embedding_timeout: int = int(os.getenv("EMBEDDING_TIMEOUT", "30"))
    embedding_collection: str = os.getenv("EMBEDDING_COLLECTION", "trip_guides_v4")

    # ===== 检索 =====
    # 混合检索（向量 + BM25，RRF 融合）。关掉就退回纯向量，
    # 留着这个开关是为了能一条命令做 A/B，而不是改代码再改回来。
    hybrid_search: bool = os.getenv("HYBRID_SEARCH", "true").lower() in ("1", "true", "yes")

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
    #
    # 分三档而不是一个数，因为 DeepSeek 的输入价按缓存命中与否差 50 倍：
    # 命中 0.02~0.04，未命中 1~2。本项目 system prompt + 攻略上下文每次都是几千字重复前缀，
    # 实测第二轮就有 3072/3322 命中，不分档会把成本高估好几倍。
    llm_input_price: float = float(os.getenv("DEEPSEEK_INPUT_PRICE", "2.0"))
    llm_output_price: float = float(os.getenv("DEEPSEEK_OUTPUT_PRICE", "8.0"))
    llm_cache_hit_price: float = float(os.getenv("DEEPSEEK_CACHE_HIT_PRICE", "0.04"))
    # 空闲时段相对高峰的折扣。DeepSeek 官方是 0.5。
    llm_offpeak_ratio: float = float(os.getenv("DEEPSEEK_OFFPEAK_RATIO", "0.5"))

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


