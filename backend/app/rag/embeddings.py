# author: szw
from langchain_openai import OpenAIEmbeddings

from app.config import settings


def get_embeddings() -> OpenAIEmbeddings:
    """获取向量模型客户端（百炼 OpenAI 兼容模式）。

    为什么不用 langchain_community 的 DashScopeEmbeddings：
      - 新模型支持 256/512/768/1024 四档维度，只有兼容模式的 dimensions 参数能控制；
      - 能显式设超时、重试与单批条数（官方限制 20 条/次）。

    check_embedding_ctx_length=False 是关键：该参数默认 True 时 LangChain 会用
    tiktoken（OpenAI 的分词器）先切分文本再发请求，对第三方模型会把中文切碎。
    接第三方 embedding 服务一律关掉它，直接发原始字符串。
    """
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        dimensions=settings.embedding_dimensions or None,
        chunk_size=settings.embedding_batch_size,
        check_embedding_ctx_length=False,
        timeout=settings.embedding_timeout,
        max_retries=2,
    )
