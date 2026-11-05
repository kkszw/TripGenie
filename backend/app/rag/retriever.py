# author: szw
"""检索上下文：内置攻略库 + 用户自己上传的资料。

两路检索分别打两个 collection，再按距离合并。为什么不是「一个 collection
加元数据过滤」，理由写在 vector_store.SCOPES 的注释里（一句话：过滤会把
没有 user_id 字段的公共块整体滤掉）。

合并时有两个约束，都是为了不让一份文件吃掉整个上下文：
  - 同一份资料最多贡献 MAX_PER_DOC 块。用户传一本 300 页的攻略书，
    前 5 条全是它，其它来源就进不来了，答案会变成"这本书的摘要"。
  - 完全相同的正文只保留一条。两个 collection 不会有重复，但公共库
    本身是多份攻略拼起来的，转载内容重复很常见。
"""
from typing import Any, Dict, List, Optional
from math import ceil

from langchain_core.documents import Document

from app.core.logging import get_logger
from app.rag.vector_store import get_vector_store

logger = get_logger(__name__)

# 单份资料最多贡献几块。取 2 是因为 top_k 通常是 5：
# 给用户资料留 2 个名额，公共库还有 3 个，两边都能说话。
MAX_PER_DOC = 2


def _source_label(doc: Document) -> str:
    """给模型看的来源标识。

    用户资料要明确标成「你上传的资料」，否则模型分不清哪份是内置攻略、
    哪份是用户自己的行程，会出现"根据攻略建议"这种把用户自己的计划
    当第三方资料复述的怪话。
    """
    meta = doc.metadata or {}
    if meta.get("scope") == "user":
        name = meta.get("filename") or "用户资料"
        return f"你上传的资料《{name}》"
    return meta.get("source") or meta.get("filename") or "内置攻略"


def format_retrieved_docs(docs: List[Document]) -> str:
    """格式化检索结果，用于 LLM 上下文。"""
    if not docs:
        return "（没有找到相关信息）"

    text = "以下是相关的旅游资料：\n\n"
    for index, doc in enumerate(docs, 1):
        meta = doc.metadata or {}
        source = _source_label(doc)
        # 页码优先用 PDF 的 page_start；旧的 markdown 块只有 page
        page = meta.get("page_start") or meta.get("page") or 0
        day = meta.get("day_label")
        where = f" 第{page}页" if page else ""
        when = f"（{day}）" if day else ""
        text += f"【来源{index}】{source}{where}{when}\n{doc.page_content}\n\n"
    return text


def _search(query: str, top_k: int, scope: str,
            where: Optional[Dict] = None) -> List[tuple]:
    """单路检索，返回 [(Document, distance)]。

    distance 越小越相近（collection 用的是默认的 l2 空间）。两个 collection
    用的是同一个 embedding 模型和同一个距离度量，所以分数可以直接比较 ——
    这一点是合并的前提，换 embedding 模型时必须重建两个库，否则分数不可比。
    """
    store = get_vector_store(scope=scope)
    kwargs: Dict[str, Any] = {"k": top_k}
    if where:
        kwargs["filter"] = where
    return store.similarity_search_with_score(query, **kwargs)


def _doc_key(doc: Document) -> str:
    """同一份资料的标识。

    用户库用 doc_id（每次上传一个 uuid），公共库用 source（文件名）。
    两者都能把「同一份文件切出来的很多块」归到一起。
    """
    meta = doc.metadata or {}
    return str(meta.get("doc_id") or meta.get("source") or meta.get("filename") or "")


def _merge(candidates: List[tuple], top_k: int, max_per_doc: int) -> List[Document]:
    """按距离合并、去重、限额。

    candidates 里的元素是 (doc, distance)。排序用距离升序；同分时保持原序
    （Python 的 sort 是稳定的），公共库在前，与旧行为一致。

    限额是自适应的，不是写死 2：`max(MAX_PER_DOC, ceil(top_k / 份数))`。
    写死 2 踩过一次 —— 公共语料目前只有一份大连攻略，一次查询的候选全来自
    它，限额 2 把 top_k=5 的结果压成了 2 条，等于凭空砍掉一半上下文。
    自适应后：只有一份资料时限额等于 top_k（没有别的可挑，别装作有选择）；
    两份时各 3 条；五份以上时回到 2 条，保证多样性。
    """
    if not candidates:
        return []

    distinct = {_doc_key(doc) for doc, _ in candidates}
    distinct.discard("")
    factor = ceil(top_k / len(distinct)) if distinct else max_per_doc
    cap = max(max_per_doc, factor)

    picked: List[Document] = []
    seen_content: set = set()
    per_doc: Dict[str, int] = {}

    for doc, _distance in sorted(candidates, key=lambda pair: pair[1]):
        if len(picked) >= top_k:
            break
        content_key = doc.page_content.strip()[:200]
        if content_key in seen_content:
            continue

        key = _doc_key(doc)
        if key:
            if per_doc.get(key, 0) >= cap:
                continue
            per_doc[key] = per_doc.get(key, 0) + 1

        seen_content.add(content_key)
        picked.append(doc)

    # 兜底：候选分布不均时上面填不满（比如只有「一份大书 6 块 + 一份小资料 1 块」，
    # 限额 3 只能凑到 4 条）。这里放开限额再捞一轮，只做去重。
    # 这么做不会破坏多样性：第一轮已经保证每份资料都拿到了名额，这一轮只是
    # 把剩下的空位用次优候选填满，少一条上下文并不会让答案更"中立"。
    if len(picked) < top_k:
        for doc, _distance in sorted(candidates, key=lambda pair: pair[1]):
            if len(picked) >= top_k:
                break
            content_key = doc.page_content.strip()[:200]
            if content_key in seen_content:
                continue
            seen_content.add(content_key)
            picked.append(doc)

    return picked


def retrieve_context(query: str, top_k: int = 5,
                     user_id: Optional[str] = None) -> Dict[str, Any]:
    """检索上下文。

    Args:
        query: 检索语句
        top_k: 最终返回的块数
        user_id: 当前登录用户。给了就会额外检索他的私有资料；
                 不给则只查公共攻略库（脚本、评测走这条路径）。

    Returns:
        {"docs": List[Document], "context": str, "sources": List[str]}
    """
    # 每一路多要一些候选，合并后才有裁剪余地。只按 top_k 取两路，
    # 合并时一旦触发去重/限额，最终数量会少于 top_k。
    fetch_k = max(top_k * 2, 10)

    candidates = _search(query, fetch_k, "public")

    if user_id:
        try:
            candidates += _search(query, fetch_k, "user", where={"user_id": user_id})
        except Exception as exc:
            # 用户库还没建（从没人上传过资料）时 collection 不存在，
            # 不该让整次规划因为没有资料而失败。
            logger.debug("用户资料检索跳过", extra={"user_id": user_id, "err": str(exc)})

    docs = _merge(candidates, top_k, MAX_PER_DOC)
    sources = list({_source_label(doc) for doc in docs})

    return {
        "docs": docs,
        "context": format_retrieved_docs(docs),
        "sources": sources,
    }
