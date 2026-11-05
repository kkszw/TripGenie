# author: szw
"""BM25 关键词索引：补向量检索对专有名词不敏感的短板。

为什么需要它（不是"别人都做混合检索所以我们也做"）：

  这批攻略里全是地名、店名、路名——「第一市场」「鹿回头」「胜利路410号」。
  向量模型把这些压进语义空间之后，「三亚吃海鲜去哪」和「三亚看日落去哪」
  的向量非常接近，因为句式、主题词、语气几乎一样；而用户真的问
  「第一市场几点开门」时，只有精确提到那一段的块才答得上来。
  这是 embedding 的结构性弱点，不是换个更大的模型能解决的。

  反过来 BM25 也不够：它完全不认同义改写（"住宿" vs "酒店"）。
  两者互补，所以融合而不是替换。

融合用 RRF（Reciprocal Rank Fusion）而不是加权分数：向量的 l2 距离和
BM25 的分数不在一个量纲上，任何加权都要先做归一化，而归一化本身依赖
候选集分布，换一批候选权重就失效了。RRF 只用排名，`1/(k+rank)` 相加，
不需要调参也不会因为分数尺度变化而失真——这也正是它被广泛采用的原因。

分词用 jieba。全量 4074 块、160 万词，建一次 9 秒，所以结果落盘缓存：
进程重启只加载不重建。缓存键里带文档集合的哈希而不是库代际号——
`index --skip-existing` 是增量写入，不会走 reset_collection，代际号不变，
但它确实改了库的内容，只认代际号会读到过期索引且不报错。
"""
import hashlib
import math
import pickle
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from langchain_core.documents import Document

from app.core.logging import get_logger

logger = get_logger(__name__)

# 经典取值。k1 控制词频饱和速度，b 控制长度归一化强度。
# 旅游攻略的块长差异大（一节 200 字 vs 一整页 1200 字），b 保持 0.75
# 是必要的：不做长度归一，长块会因为词多而系统性占优。
K1 = 1.5
B = 0.75

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"

# 建索引期间不允许并发检索：两次同时建会把 10 秒的白工做两遍
_build_lock = threading.Lock()

# 进程内缓存：scope -> (文档集合指纹, 索引)。
# 分 scope 而不是只留一个槽：公共库和用户库会被交替检索，共用一个槽
# 会让每次检索都判定"缓存失效"然后重建。
_index_cache: Dict[str, Tuple[str, "KeywordIndex"]] = {}

# 上次校验指纹的时间（monotonic 秒）。
_last_check: Dict[str, float] = {}

# 校验一次指纹要 95ms（Chroma 取 4074 个 id）。每个查询都付这笔钱等于给
# 检索凭空加 0.1 秒；只在超过这个间隔后才重新校验，稳态下就是纯内存操作。
# 语料重建后最多等这么久生效，而建完库本来就要求重启服务，所以不影响正确性。
CHECK_INTERVAL_SECONDS = 15.0


def tokenize(text: str) -> List[str]:
    """切词。

    过滤规则只有两条：短于 2 个字、以及不含任何字母数字的纯符号。
    没有维护停用词表——BM25 的 idf 本来就把「的」「了」这类词压到接近 0
    （几乎每篇都有，df ≈ N），再单独列表反而要维护一份随语料变化的东西。
    """
    import jieba

    tokens = []
    for word in jieba.lcut(str(text or "")):
        word = word.strip().lower()
        if len(word) < 2:
            continue
        if not any(ch.isalnum() for ch in word):
            continue
        tokens.append(word)
    return tokens


class KeywordIndex:
    """倒排索引 + BM25 打分。

    存倒排表而不是切好的词列表：真正要落盘的只有「词 → 哪些块里有、几次」，
    词列表本身是建索引的中间产物，留着只会让 pickle 大十几倍。
    """

    def __init__(self, postings: Dict[str, List[Tuple[int, int]]],
                 lengths: List[int], ids: List[str],
                 docs: Optional[List[Document]] = None):
        self.postings = postings
        self.lengths = lengths
        self.ids = ids
        # 正文不进 pickle（会大十几倍），从磁盘加载后由 get_index 补上
        self.docs = docs
        self.total = len(lengths)
        self.avgdl = (sum(lengths) / self.total) if self.total else 0.0

    # ------------------------------------------------------------------ 构建

    @classmethod
    def build(cls, docs: List[Document], ids: List[str]) -> "KeywordIndex":
        postings: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
        lengths: List[int] = []

        for position, doc in enumerate(docs):
            tokens = tokenize(doc.page_content)
            lengths.append(len(tokens))
            for term, count in Counter(tokens).items():
                postings[term].append((position, count))

        return cls(dict(postings), lengths, ids)

    # ------------------------------------------------------------------ 检索

    def _idf(self, term: str) -> float:
        """Lucene 的 BM25 idf。+1 是为了保证非负——
        原始公式在 df > N/2 时会给负数，那种词（比如「攻略」在本语料里
        出现在每一份文档里）会让匹配到它的块被**扣分**，看似合理实则有害：
        问「三亚攻略」时最相关的块反而被压下去。
        """
        df = len(self.postings.get(term, ()))
        return math.log(1 + (self.total - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 10,
               allow: Optional[Callable[[int], bool]] = None) -> List[Tuple[int, float]]:
        """返回 [(文档下标, 分数)]，分数越大越相关。

        allow 是元数据过滤回调（目的地过滤用）。先算分再过滤，不是先过滤再算分：
        过滤前进 docs 的顺序取决于 Chroma 返回顺序，跟相关性无关，按它截断
        会漏掉真正该出现的块。
        """
        terms = list(dict.fromkeys(tokenize(query)))
        if not terms:
            return []

        scores: Dict[int, float] = defaultdict(float)
        for term in terms:
            posting = self.postings.get(term)
            if not posting:
                continue
            idf = self._idf(term)
            for position, freq in posting:
                norm = K1 * (1 - B + B * self.lengths[position] / (self.avgdl or 1.0))
                scores[position] += idf * freq * (K1 + 1) / (freq + norm)

        ranked = sorted(scores.items(), key=lambda pair: -pair[1])
        if allow is None:
            return ranked[:top_k]
        return [(pos, score) for pos, score in ranked if allow(pos)][:top_k]

    # ------------------------------------------------------------------ 持久化

    def dumps(self) -> bytes:
        return pickle.dumps(
            {"postings": self.postings, "lengths": self.lengths, "ids": self.ids},
            protocol=pickle.HIGHEST_PROTOCOL,
        )

    @classmethod
    def loads(cls, payload: bytes) -> "KeywordIndex":
        data = pickle.loads(payload)
        return cls(data["postings"], data["lengths"], data["ids"])


def _collection_key(ids: List[str]) -> str:
    """文档集合的指纹。排序后哈希，保证与 Chroma 的返回顺序无关。"""
    digest = hashlib.sha1("\n".join(sorted(ids)).encode("utf-8")).hexdigest()
    return digest[:16]


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"bm25_{key}.pkl"


def build_index(docs: List[Document], ids: List[str]) -> KeywordIndex:
    """带磁盘缓存的建索引。"""
    key = _collection_key(ids)
    path = _cache_path(key)

    if path.exists():
        try:
            index = KeywordIndex.loads(path.read_bytes())
            # 缓存文件可能是上一次写到一半留下的：条数对不上就重建，
            # 半份索引不会报错，只是会静默漏掉一部分块
            if len(index.lengths) == len(docs):
                logger.info("BM25 索引已从缓存加载", extra={"docs": len(docs), "key": key})
                return index
            logger.warning("BM25 缓存条数不符，重建",
                           extra={"cached": len(index.lengths), "docs": len(docs)})
        except Exception as exc:
            logger.warning("BM25 缓存读取失败，重建", extra={"err": str(exc)})

    index = KeywordIndex.build(docs, ids)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".pkl.tmp")
        tmp.write_bytes(index.dumps())
        tmp.replace(path)
        # 旧版本的缓存留着只会越攒越多，且永远不会再被读到
        for stale in CACHE_DIR.glob("bm25_*.pkl"):
            if stale != path:
                stale.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("BM25 缓存写入失败（不影响检索）", extra={"err": str(exc)})
    return index


def get_index(scope: str = "public") -> Optional[KeywordIndex]:
    """取索引，没有就建。建失败返回 None，调用方退回纯向量检索。

    三层缓存，缺一不可：
      1. 进程内（本函数）——稳态下零开销
      2. 磁盘 pickle（build_index）——进程重启只加载不重建
      3. Chroma 指纹校验——语料变了要能发现
    """
    from app.rag.vector_store import get_vector_store

    now = time.monotonic()
    cached = _index_cache.get(scope)
    if cached is not None and now - _last_check.get(scope, 0.0) < CHECK_INTERVAL_SECONDS:
        return cached[1]

    try:
        store = get_vector_store(scope=scope)
        # 只取 id：正文有几十 MB，校验阶段用不上。
        # include=[] 不填充 documents，实测比全量快 3 倍。
        probe = store._collection.get(include=[])
    except Exception as exc:
        logger.debug("BM25 取文档失败，跳过关键词检索", extra={"err": str(exc)})
        return cached[1] if cached else None

    ids = probe.get("ids") or []
    if not ids:
        return None
    key = _collection_key(ids)

    if cached is not None and cached[0] == key:
        _last_check[scope] = now
        return cached[1]

    with _build_lock:
        # 等锁期间别人可能已经建好了，别再白建一次
        current = _index_cache.get(scope)
        if current is not None and current[0] == key:
            _last_check[scope] = now
            return current[1]

        try:
            data = store._collection.get(include=["documents", "metadatas"])
        except Exception as exc:
            logger.debug("BM25 取正文失败，跳过关键词检索", extra={"err": str(exc)})
            return None

        docs = [Document(page_content=text or "", metadata=meta or {})
                for text, meta in zip(data.get("documents") or [],
                                      data.get("metadatas") or [])]
        index = build_index(docs, ids)
        # 正文要留一份：索引里的 position 就是这份 docs 的下标。
        # 每次检索都回查 Chroma 会白白多一次 IO（4074 条约 0.3 秒）。
        index.docs = docs
        _index_cache[scope] = (key, index)
        _last_check[scope] = now
        return index


def reset_cache() -> None:
    """清掉进程内缓存（测试、或外部改了语料后强制重读）。"""
    _index_cache.clear()
    _last_check.clear()
