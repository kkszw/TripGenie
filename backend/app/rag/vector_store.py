# author: szw
# app/rag/vector_store.py
from langchain_chroma import Chroma
from app.rag.embeddings import get_embeddings
from app.config import settings
import os
from typing import List
from langchain_core.documents import Document

from app.utils.path_tool import get_absolute_path


def get_vector_store(embedding_function=None):
    """获取向量库"""
    if embedding_function is None:
        embedding_function = get_embeddings()
    db_path =  get_absolute_path(settings.chroma_db_path)
    os.makedirs(db_path, exist_ok=True)

    return Chroma(
        persist_directory=db_path,
        embedding_function=embedding_function,
        collection_name="trip_guides"
    )


def add_documents(documents: List[Document]):
    """添加文档到向量库"""
    vector_store = get_vector_store()
    vector_store.add_documents(documents)
    print(f"✅ 添加 {len(documents)} 个文档块到向量库")
    print(f"📁 存储路径: {get_absolute_path(settings.chroma_db_path)}")


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
    print(f"📊 向量库统计:")
    print(f"  集合名称: {collection.name}")
    print(f"  文档数量: {count}")
    return count