# author: szw
# app/rag/vector_store.py
import os
import threading
from typing import Dict, List

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
_stores: Dict[str, Chroma] = {}
# 必须用 RLock 而不是 Lock：get_vector_store 拿着锁去调 _build，_build 里去
# _get_client 又要同一把锁。Lock 不可重入，结果是**首次取库里直接死锁**，
# 不报错、不超时、进程就挂在那儿（实测表现为入库卡在"OCR 完成"之后）。
_store_lock = threading.RLock()

# 整个进程只能有一个 chromadb 客户端。
# 这不是洁癖，是踩过的坑：公共库和用户库各建一个 PersistentClient 指向同一个
# 目录时，后建的那个会把先建的 HNSW 段搞坏，先前的查询直接抛
# `InternalError: Error creating hnsw segment reader: Nothing found on disk`——
# 而且是**回头的那个查询**才炸，前面一切正常，极其难查。
# 实测复现：先建 user 库并写入，再建 public 库，然后回头查 user 必挂。
# 正确做法是一个 client 管多个 collection。
_client = None

# 库的代际。reset_collection 时 +1，用来让上层（retriever 的目的地列表）
# 知道自己缓存的东西已经过期了。
# 为什么需要：检索侧把「库里有哪些目的地」缓存在进程内存里，重建语料后
# 不重启服务，过滤条件还是旧的，表现为「新加的城市检索不到」——不报错，
# 只是查不出来，是最难排查的那种。
_generation = 0


def get_generation() -> int:
    """当前库的代际。缓存方拿它判断是否需要重算。"""
    return _generation

# 两个独立的 collection：
#   public —— 内置的城市攻略（app/data/corpus + build_corpus.py）
#   user   —— 用户自己上传的 PDF 资料
#
# 为什么不做成「同一个 collection + 元数据过滤」，理由有两个，都不是洁癖：
#   1. 公共块没有 user_id 字段。Chroma 的过滤语法里
#      {"user_id": {"$ne": "x"}} 只匹配「有这个字段且不等于」的文档，
#      无字段的公共块会被整体滤掉 —— 一加过滤，公共攻略全部消失，且不报错。
#   2. 用户资料要支持「删一份文档就删干净」，独立 collection 里按
#      where={"doc_id": ...} 批量删是原子的；混在一个库里，误删的爆炸半径更大。
# 代价是检索要查两次再合并，这个成本远低于处理上面两个问题的成本。
SCOPES = {"public": "", "user": "_user"}


def _collection_name(scope: str) -> str:
    suffix = SCOPES.get(scope)
    if suffix is None:
        raise ValueError(f"未知的知识库范围: {scope}")
    return f"{settings.embedding_collection}{suffix}"


def _get_client():
    """进程内唯一的 chromadb 客户端。"""
    global _client
    if _client is None:
        with _store_lock:
            if _client is None:
                import chromadb

                db_path = get_absolute_path(settings.chroma_db_path)
                os.makedirs(db_path, exist_ok=True)
                _client = chromadb.PersistentClient(path=db_path)
    return _client


def _build(embedding_function, scope: str) -> Chroma:
    # 传 client 而不是 persist_directory：两者同时给会被 langchain 判为冲突。
    # 复用同一个 client 才能安全地持有多个 collection。
    return Chroma(
        client=_get_client(),
        embedding_function=embedding_function,
        collection_name=_collection_name(scope),
    )


def get_vector_store(embedding_function=None, scope: str = "public") -> Chroma:
    """获取向量库（默认 embedding 走进程内单例）

    显式传入 embedding_function 时不做缓存：调用方可能用的是另一个 embedding，
    复用单例会把两种向量混进同一个集合。
    """
    db_path = get_absolute_path(settings.chroma_db_path)
    os.makedirs(db_path, exist_ok=True)

    if embedding_function is not None:
        return _build(embedding_function, scope)

    if scope not in _stores:
        with _store_lock:
            if scope not in _stores:
                _stores[scope] = _build(get_embeddings(), scope)
    return _stores[scope]


def add_documents(documents: List[Document], scope: str = "public"):
    """添加文档到向量库"""
    vector_store = get_vector_store(scope=scope)
    vector_store.add_documents(documents)
    logger.info("向量库写入完成",
                extra={"chunks": len(documents), "scope": scope,
                       "path": get_absolute_path(settings.chroma_db_path)})


def delete_documents(where: Dict, scope: str = "user") -> None:
    """按元数据条件删除块。用于「删掉这份资料」时清掉它留下的所有向量。

    单条 where 条件不命中时 Chroma 静默返回，不抛错 —— 调用方若要确认删除
    成功，应该看删除后的计数，而不是指望这里报错。
    """
    store = get_vector_store(scope=scope)
    store._collection.delete(where=where)
    logger.info("向量库删除完成", extra={"where": where, "scope": scope})


def count_documents(where: Dict, scope: str = "public") -> int:
    """数一下符合 where 的块有多少。用于「这份资料已经入库了吗」。

    include=[] 只要 id，不拉回 embedding —— 建库脚本每份语料都问一次，
    带上向量会让这一步从毫秒级变成秒级。
    """
    store = get_vector_store(scope=scope)
    result = store._collection.get(where=where, include=[])
    return len(result.get("ids") or [])


def reset_collection(scope: str = "public") -> None:
    """清空一个 collection 并重建，不动另一个库。

    为什么不照搬 init_rag_database 的 `shutil.rmtree(chroma_db_path)`：
    那个路径下同时放着 public 和 user 两个 collection，整个目录删掉会把
    用户上传的资料一起抹掉。重建公共语料不该有这种副作用。

    删除之后必须把缓存里的 store 扔掉，否则 get_vector_store 拿到的还是
    指向已删除 collection 的旧对象，下一次写入会报
    "Collection ... does not exist"。
    """
    global _generation

    client = _get_client()
    name = _collection_name(scope)
    try:
        client.delete_collection(name)
    except Exception as exc:  # 集合本来就不存在时 Chroma 抛的异常类型不稳定
        logger.info("删除 collection 未执行", extra={"collection": name, "err": str(exc)})

    with _store_lock:
        _stores.pop(scope, None)

    # 让依赖库内容的上层缓存失效
    _generation += 1

    # 立刻建回来，让后续 add_documents 有地方写
    get_vector_store(scope=scope)
    logger.info("collection 已重建", extra={"collection": name, "scope": scope})


def get_retriever(top_k: int = 5, scope: str = "public"):
    """获取检索器"""
    vector_store = get_vector_store(scope=scope)
    return vector_store.as_retriever(
        search_kwargs={"k": top_k}
    )


def search_similar(query: str, top_k: int = 5, scope: str = "public") -> List[Document]:
    """搜索相似文档"""
    vector_store = get_vector_store(scope=scope)
    results = vector_store.similarity_search(query, k=top_k)
    return results


def get_collection_stats(scope: str = "public") -> int:
    """获取集合统计信息"""
    vector_store = get_vector_store(scope=scope)
    collection = vector_store._collection
    count = collection.count()
    logger.info("向量库统计", extra={"collection": collection.name,
                                     "scope": scope, "documents": count})
    return count
