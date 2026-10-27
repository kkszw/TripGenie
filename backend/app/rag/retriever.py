# author: szw
from typing import List, Dict, Any
from langchain_core.documents import Document
from app.rag.vector_store import get_vector_store
from app.rag.document_loader import load_multiple_pdfs
from langchain_community.retrievers import BM25Retriever

def format_retrieved_docs(docs: List[Document]) -> str:
    """格式化检索结果，用于 LLM 上下文"""
    if not docs:
        return "（没有找到相关信息）"
    text = "以下是相关的旅游攻略信息：\n\n"
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", "未知来源")
        page = doc.metadata.get("page", 0)
        text += f"【来源{i}】({source} 第{page}页)\n{doc.page_content}\n\n"
    return text


def retrieve_context(query: str, top_k: int = 5) -> Dict[str, Any]:
    """
    检索上下文
    Returns:
        {
            "docs": List[Document],
            "context": str,
            "sources": List[str]
        }
    """
    vector_store = get_vector_store()
    docs = vector_store.similarity_search(query, k=top_k)

    # 提取来源
    sources = list(set([doc.metadata.get("source", "未知") for doc in docs]))

    return {
        "docs": docs,
        "context": format_retrieved_docs(docs),
        "sources": sources
    }
