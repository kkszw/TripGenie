# author: szw
# app/rag/vector_store.py
import os
import threading
from typing import List

from langchain_chroma import Chroma
from langchain_core.documents import Document

from app.config import settings
from app.core.logging import get_logger
from app.rag.embeddings import get_embeddings
from app.utils.path_tool import get_absolute_path

logger = get_logger(__name__)

# 进程内单例。原来每次 get_vector_store() 都 new 一个 Chroma：重新打开 sqlite、
# 重新加载 collection，实测单次 0.98s；缓存后 0.27s。而一个规划请求会调两次
# （retrieve_guides + search_attractions），这些开销全是重复的。
# 用同步锁即可——调用方都是同步函数（异步节点里通过 asyncio.to_thread 进来）。
_store: Chroma | None = None
_store_lock = threading.Lock()


def _build(db_path: str, embedding_function):
    return Chroma(
        persist_directory=db_path,
        embedding_function=embedding_function,
        collection_name=settings.embedding_collection,
    )


def get_vector_store(embedding_function=None) -> Chroma:
    """获取向量库（默认 embedding 走进程内单例）

    显式传入 embedding_function 时不做缓存：调用方可能用的是另一个 embedding，
    复用单例会把两种向量混进同一个集合。
    """
    db_path = get_absolute_path(settings.chroma_db_path)
    os.makedirs(db_path, exist_ok=True)

    if embedding_function is not None:
        return _build(db_path, embedding_function)

    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = _build(db_path, get_embeddings())
    return _store


def add_documents(documents: List[Document]):
    """添加文档到向量库"""
    vector_store = get_vector_store()
    vector_store.add_documents(documents)
    logger.info("向量库写入完成",
                extra={"chunks": len(documents),
                       "path": get_absolute_path(settings.chroma_db_path)})


def get_retriever(top_k: int = 5):
    """获取检索器"""
    vector_store = get_vector_store()
    return vector_store.as_retriever(
        search_kwargs={"k": top_k}
    )


def search_similar(query: str, top_k: int = 5) -> List[Document]:
    """搜索相似文档"""
    vector_store = get_vector_store()
    results = vector_store.similarity_search(query, k=top_k)
    return results


def get_collection_stats():
    """获取集合统计信息"""
    vector_store = get_vector_store()
    collection = vector_store._collection
    count = collection.count()
    logger.info("向量库统计", extra={"collection": collection.name, "documents": count})
    return count
