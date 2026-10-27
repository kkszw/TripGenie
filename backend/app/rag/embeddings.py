# author: szw
from langchain_community.embeddings import DashScopeEmbeddings
from app.config import settings

def get_embeddings():
    """获取嵌入模型"""
    return DashScopeEmbeddings(model=settings.DASHSCOPE_EMBEDDINGS, dashscope_api_key=settings.DASHSCOPE_API_KEY)


