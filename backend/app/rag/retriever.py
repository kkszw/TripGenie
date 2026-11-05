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

内置语料从 1 份变成 125 份之后多了一层约束：**按目的地过滤**。
这批攻略是同一家出的同一套模板，「交通·TRANSPORTATION」「有问必答·FAQ」
「亮点·HIGH LIGHTS」这些章节标题逐字相同，向量空间里挤成一团。
不过滤的话，问大连会拿回三亚的交通段落——内容看着很专业，其实是错的。

再往上一层是**混合检索**：向量路 + BM25 关键词路，用 RRF 融合。理由是
这批语料里全是地名、店名、路名（「第一市场」「鹿回头」「胜利路410号」），
向量模型会把它们和同主题的段落算得很像，而用户问「第一市场几点开门」时
只有精确提到那一段的块才答得上来。BM25 补的正是这个短板，见 keyword_index.py。
"""
from typing import Any, Dict, List, Optional
from math import ceil

from langchain_core.documents import Document

from app.config import settings
from app.core.logging import get_logger
from app.rag.vector_store import get_generation, get_vector_store

logger = get_logger(__name__)

# 单份资料最多贡献几块。取 2 是因为 top_k 通常是 5：
# 给用户资料留 2 个名额，公共库还有 3 个，两边都能说话。
MAX_PER_DOC = 2

# RRF 的平滑常数。60 是原论文的实验取值，也是 Elasticsearch 的默认值：
# 它让「第 1 名」和「第 2 名」的差距（1/61 vs 1/62）远小于「出现过」和
# 「没出现过」的差距，所以多路都命中的块会稳定胜出，而不是被某一路的
# 绝对分数带跑。
RRF_K = 60

# 公共库里出现过的目的地。第一次检索时聚合一次，之后进程内复用 ——
# 几千条 metadata 全取一遍要几十毫秒，每次查询都做没必要。
# 缓存的失效靠 vector_store 的代际号（重建库时会 +1）。
_destinations: Optional[List[str]] = None
_destinations_gen = -1



def known_destinations() -> List[str]:
    """公共语料里出现过的目的地，进程内缓存。

    从向量库的元数据聚合，而不是去读 corpus 目录：向量库才是检索时真正
    有的东西，两者可能短暂不同步（刚 extract 还没 index），以库为准更诚实。

    缓存跟着库的代际走：重建语料后（reset_collection）代数 +1，这里自动
    重算。否则新加的城市在进程重启前永远匹配不上，表现是「检索不到」，
    不报错，很难查。
    """
    global _destinations, _destinations_gen
    generation = get_generation()
    if _destinations is None or _destinations_gen != generation:
        try:
            store = get_vector_store(scope="public")
            data = store._collection.get(include=["metadatas"])
            values = {
                str(meta.get("destination"))
                for meta in (data.get("metadatas") or [])
                if meta and meta.get("destination")
            }
            _destinations = sorted(values)
        except Exception as exc:  # 库还没建 / 读失败都不该让检索挂掉
            logger.debug("目的地列表读取失败", extra={"err": str(exc)})
            _destinations = []
        _destinations_gen = generation
    return _destinations


def match_destinations(name: Optional[str]) -> List[str]:
    """把用户说的地名映射到库里实际有的目的地。

    三种命中都要算：
      - 完全相等：「大连」→「大连」
      - 库里更具体：「香港」→「香港购物」「香港离岛」
      - 用户说得更具体：「香港离岛」→ 也带上「香港」，因为香港攻略里
        往往有一整节讲离岛，扔掉它就等于自断一条线索

    反过来不做（「云南」不会带出「昆明」）——那需要一张省市归属表，
    等真的出现召回不足再说，宁可现在少做也不要编一张错表。
    """
    if not name:
        return []
    name = name.strip()
    if not name:
        return []
    return [d for d in known_destinations() if d == name or name in d or d in name]


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


def _content_key(doc: Document) -> str:
    """跨路去重用的正文指纹。

    向量路和关键词路会命中同一块，融合时按正文前缀合并，不能按 id——
    Chroma 的块 id 是随机 uuid，两条路拿到的是同一个 id，但用户库与公共库
    之间可能真是不同块而正文相同（转载内容），那种也该只留一条。
    """
    return doc.page_content.strip()[:200]


def _matches(meta: Dict, where: Optional[Dict]) -> bool:
    """把 Chroma 风格的最小过滤子集用在本地索引上。

    只支持本模块实际用到的两种形式：等值和 $in。写成一个通用求值器反而
    会掩盖「过滤到底做了什么」这件事——目的地过滤是这个模块的核心机制，
    它应该一眼能读懂。
    """
    if not where:
        return True
    for field, cond in where.items():
        value = (meta or {}).get(field)
        if isinstance(cond, dict) and "$in" in cond:
            if value not in cond["$in"]:
                return False
        elif value != cond:
            return False
    return True


def _keyword_ranking(query: str, top_k: int, scope: str,
                     where: Optional[Dict] = None) -> List[Document]:
    """关键词路召回，按 BM25 分数降序返回。

    索引建失败（用户库空、Chroma 读不到）就返回空——混合检索退化成纯向量，
    不能因为一个增强项没准备好就让整次规划失败。
    """
    from app.rag.keyword_index import get_index

    try:
        index = get_index(scope)
        if not index or not index.docs:
            return []
        allow = (lambda pos: _matches(index.docs[pos].metadata, where)) if where else None
        return [index.docs[pos] for pos, _score in index.search(query, top_k, allow=allow)]
    except Exception as exc:
        # 关键词路是增强项：索引建不出来、读坏了，都退回纯向量。
        # 让它把整次规划带走是本末倒置——没有它检索仍然能用，只是差一些。
        logger.debug("关键词检索失败，本次只用向量路", extra={"err": str(exc)})
        return []


def _rrf(rankings: List[List[Document]]) -> List[tuple]:
    """Reciprocal Rank Fusion，返回 [(doc, fused_score)]，分数越大越相关。

    只吃排名、不吃分数：向量的 l2 距离和 BM25 分数不在一个量纲上，
    要加权就得先归一化，而归一化依赖候选集分布——换一批候选权重就失效。
    RRF 没有这个问题，也没有需要调的参数。
    """
    fused: Dict[str, list] = {}
    for ranking in rankings:
        for rank, doc in enumerate(ranking, start=1):
            key = _content_key(doc)
            entry = fused.get(key)
            if entry is None:
                fused[key] = [doc, 1.0 / (RRF_K + rank)]
            else:
                entry[1] += 1.0 / (RRF_K + rank)
    # 同一块在两路都排得靠前 → 分数累加，稳定胜出
    return sorted(((doc, score) for doc, score in fused.values()),
                  key=lambda pair: -pair[1])


def _merge(candidates: List[tuple], top_k: int, max_per_doc: int,
           higher_is_better: bool = False) -> List[Document]:
    """按分数合并、去重、限额。

    candidates 里的元素是 (doc, score)。higher_is_better=False 用于向量路
    （l2 距离，越小越相近），True 用于 RRF 融合分数（越大越相关）。
    同分时保持原序（Python 的 sort 是稳定的），公共库在前，与旧行为一致。

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

    def ordered():
        return sorted(candidates, key=lambda pair: pair[1], reverse=higher_is_better)

    for doc, _score in ordered():
        if len(picked) >= top_k:
            break
        content_key = _content_key(doc)
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
        for doc, _score in ordered():
            if len(picked) >= top_k:
                break
            content_key = _content_key(doc)
            if content_key in seen_content:
                continue
            seen_content.add(content_key)
            picked.append(doc)

    return picked


def retrieve_context(query: str, top_k: int = 5,
                     user_id: Optional[str] = None,
                     destination: Optional[str] = None) -> Dict[str, Any]:
    """检索上下文。

    Args:
        query: 检索语句
        top_k: 最终返回的块数
        user_id: 当前登录用户。给了就会额外检索他的私有资料；
                 不给则只查公共攻略库（脚本、评测走这条路径）。
        destination: 本次要去的地方。给了就先只在该目的地的语料里检索，
                     命中不足 top_k 再放开到全库。

    Returns:
        {"docs": List[Document], "context": str, "sources": List[str]}
    """
    # 每一路多要一些候选，合并后才有裁剪余地。只按 top_k 取两路，
    # 合并时一旦触发去重/限额，最终数量会少于 top_k。
    fetch_k = max(top_k * 2, 10)
    hybrid = settings.hybrid_search

    matched = match_destinations(destination)

    # vector_candidates 保留 (doc, distance)，纯向量模式要用它按距离排序；
    # rankings 只存排好序的文档列表，给 RRF 用。两者同时维护而不是二选一，
    # 是因为 hybrid 开关要能在配置里改，两条路径都必须随时可用。
    vector_candidates: List[tuple] = []
    rankings: List[List[Document]] = []

    def _take(pairs: List[tuple], where: Optional[Dict], scope: str) -> None:
        vector_candidates.extend(pairs)
        rankings.append([doc for doc, _ in pairs])
        if hybrid:
            keyword = _keyword_ranking(query, fetch_k, scope, where)
            if keyword:
                rankings.append(keyword)

    # ---- 公共库 ----
    if matched:
        where = {"destination": {"$in": matched}}
        try:
            _take(_search(query, fetch_k, "public", where=where), where, "public")
        except Exception as exc:
            # 过滤语法/字段缺失等问题不该让规划直接失败，退回全库检索
            logger.warning("目的地过滤检索失败，退回全库",
                           extra={"destination": destination, "err": str(exc)})
            matched = []

    if not matched:
        _take(_search(query, fetch_k, "public"), None, "public")

    # ---- 用户自己的资料 ----
    if user_id:
        try:
            where = {"user_id": user_id}
            _take(_search(query, fetch_k, "user", where=where), where, "user")
        except Exception as exc:
            # 用户库还没建（从没人上传过资料）时 collection 不存在，
            # 不该让整次规划因为没有资料而失败。
            logger.debug("用户资料检索跳过", extra={"user_id": user_id, "err": str(exc)})

    if hybrid and len(rankings) > 1:
        docs = _merge(_rrf(rankings), top_k, MAX_PER_DOC, higher_is_better=True)
    else:
        docs = _merge(vector_candidates, top_k, MAX_PER_DOC)

    # 该目的地的语料不够填满上下文时，放开到全库补齐。两种情况都会走到：
    #   1. 库里根本没有这个地方（125 份覆盖了主要旅游地，但不可能全覆盖）
    #   2. 用户问的是「从成都怎么去九寨沟」，需要出发地的交通章节
    # 补齐用的是同一个 top_k 之外的剩余名额，不会把已经选中的本地块挤掉。
    if matched and len(docs) < top_k:
        seen = {_content_key(doc) for doc in docs}
        wide_vec = _search(query, fetch_k, "public")
        if hybrid:
            wide = [[doc for doc, _ in wide_vec if _content_key(doc) not in seen]]
            wide_kw = [doc for doc in _keyword_ranking(query, fetch_k, "public")
                       if _content_key(doc) not in seen]
            if wide_kw:
                wide.append(wide_kw)
            extra = [(doc, score) for doc, score in _rrf(wide)
                     if _content_key(doc) not in seen]
            docs += _merge(extra, top_k - len(docs), MAX_PER_DOC, higher_is_better=True)
        else:
            extra = [(doc, score) for doc, score in wide_vec
                     if _content_key(doc) not in seen]
            docs += _merge(extra, top_k - len(docs), MAX_PER_DOC)

    sources = list({_source_label(doc) for doc in docs})

    return {
        "docs": docs,
        "context": format_retrieved_docs(docs),
        "sources": sources,
    }
