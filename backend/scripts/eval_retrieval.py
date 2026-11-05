# author: szw
"""检索质量自检：量化「按目的地过滤」和「混合检索」各自的收益。

为什么需要它：
  - 125 份攻略是同一家出的同一套模板，「交通·TRANSPORTATION」「有问必答·FAQ」
    「亮点·HIGH LIGHTS」这些章节标题逐字相同，正文句式也高度雷同。向量空间里
    它们挤成一团，不过滤时问大连会拿回三亚的交通段落——内容看着很专业，其实是
    错的，而且 LLM 会照着它编行程。
  - 这批语料里全是地名、店名、路名（「第一市场」「鹿回头」「胜利路410号」）。
    向量模型把这些和同主题段落算得很像，用户问「第一市场几点开门」时可能
    拿回一堆"讲吃海鲜"但不提这个市场的块。BM25 补的正是这个短板。

三个指标：
  local@k    top-k 里属于本目的地的块占比。越高说明上下文越不串城。
  term@k     查询里带一个该目的地独有的地标词时，top-k 中含这个词的块占比。
             这个指标是专门为混合检索设的：过滤一开，local@k 就顶满 100%，
             再也区分不出"检索得准不准"，而精确地名恰恰是向量检索的短板。
  hit@k      至少命中 1 条本地块的目的地比例。低了说明语料覆盖有洞。

跑法：
    cd backend
    python scripts/eval_retrieval.py                  # 全量目的地
    python scripts/eval_retrieval.py --limit 30       # 只测前 30 个，快
    python scripts/eval_retrieval.py --no-hybrid      # 只跑向量模式
"""
import argparse
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.rag import retriever  # noqa: E402


def query_for(destination: str) -> str:
    """构造查询。刻意用和线上一致的口吻（retrieval 节点就是这么拼的），
    否则测出来的数字不代表真实使用场景。"""
    return f"去{destination}玩3天，预算3000,喜欢自然风光 当地美食"


def pick_landmarks(destinations: List[str]) -> Dict[str, str]:
    """给每个目的地挑一个「只有它这里才有」的具体地名。

    选法：在该目的地的块里出现多次、但全库里只出现在这些块里的词
    （df_global <= 该目的地的块数），且长度 >= 3。满足这三条的基本都是
    具体地名（「亚龙湾」「蜈支洲岛」），而不是「美食」「酒店」这类通用词。

    抽不出来就跳过这个目的地——宁可样本少一点，也不要拿通用词去测，
    那会把两条路都测成接近 100%，什么也说明不了。
    """
    from app.rag.keyword_index import get_index, tokenize

    index = get_index("public")
    if not index or not index.docs:
        return {}

    positions_by_dest: Dict[str, List[int]] = {}
    for pos, doc in enumerate(index.docs):
        dest = str((doc.metadata or {}).get("destination") or "")
        if dest:
            positions_by_dest.setdefault(dest, []).append(pos)

    picked: Dict[str, str] = {}
    for destination in destinations:
        positions = positions_by_dest.get(destination) or []
        if not positions:
            continue
        local = Counter()
        for pos in positions:
            local.update(tokenize(index.docs[pos].page_content))

        for term, count in local.most_common(300):
            if len(term) < 3 or count < 2:
                continue
            # 至少要含两个汉字：语料里「021」这种电话区号也会满足"只在本城出现"，
            # 但它不是地名，拿它当查询词测出来的数字没有意义
            if sum(1 for ch in term if "\u4e00" <= ch <= "\u9fa5") < 2:
                continue
            # 跟目的地名重复的词不算：问「三清山怎么去」而目的地就是三清山，
            # 两条路都会命中，测出来只是把目的地的名字又念了一遍。
            # 要看的是「亚龙湾」「第一市场」这种目的地内部的具体点。
            if term == destination or term in destination or destination in term:
                continue
            if len(index.postings.get(term, ())) <= len(positions):
                picked[destination] = term
                break
    return picked


def run(destinations: List[str], top_k: int, use_filter: bool, hybrid: bool,
        landmarks: Optional[Dict[str, str]] = None) -> Dict:
    """跑一轮检索，统计本地块占比与地标词命中率。"""
    previous = settings.hybrid_search
    settings.hybrid_search = hybrid
    landmarks = landmarks or {}

    local_hits = 0
    total_chunks = 0
    covered = 0
    term_hits = 0
    term_queries = 0
    per_dest: List[Dict] = []
    started = time.perf_counter()

    try:
        for destination in destinations:
            # 包含关系也算命中：「香港」的查询召回「香港购物」是对的
            matched = set(retriever.match_destinations(destination))
            ctx = retriever.retrieve_context(
                query_for(destination), top_k=top_k,
                destination=destination if use_filter else None,
            )
            docs = ctx["docs"]
            local = sum(1 for doc in docs
                        if str((doc.metadata or {}).get("destination")) in matched)
            local_hits += local
            total_chunks += len(docs)
            if local:
                covered += 1

            # 地标词查询：问法也贴近真实用户，不要只丢一个词进去
            term = landmarks.get(destination)
            term_local = 0
            if term:
                term_queries += 1
                term_ctx = retriever.retrieve_context(
                    f"{term}怎么去，有什么好玩的", top_k=top_k,
                    destination=destination if use_filter else None,
                )
                term_local = sum(1 for doc in term_ctx["docs"] if term in doc.page_content)
                term_hits += term_local

            per_dest.append({"destination": destination, "local": local,
                             "total": len(docs), "term": term, "term_local": term_local})
    finally:
        settings.hybrid_search = previous

    count = len(destinations) or 1
    return {
        "local_ratio": local_hits / total_chunks if total_chunks else 0.0,
        "local_per_query": local_hits / count,
        "hit_rate": covered / count,
        "term_ratio": term_hits / (term_queries * top_k) if term_queries else 0.0,
        "term_queries": term_queries,
        "total_chunks": total_chunks,
        "seconds": time.perf_counter() - started,
        "per_dest": per_dest,
    }


def report(label: str, result: Dict, top_k: int) -> None:
    print(f"\n{label}")
    print(f"  本地块占比 local@{top_k}   {result['local_ratio']:>6.1%}")
    print(f"  每问平均本地块            {result['local_per_query']:>6.2f}")
    print(f"  至少命中一条的目的地      {result['hit_rate']:>6.1%}")
    if result["term_queries"]:
        print(f"  地标词命中 term@{top_k}     {result['term_ratio']:>6.1%}"
              f"   （{result['term_queries']} 个目的地）")
    print(f"  耗时                      {result['seconds']:>6.1f}s")


def main() -> int:
    parser = argparse.ArgumentParser(description="检索质量自检")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=0, help="只测前 N 个目的地")
    parser.add_argument("--show-worst", type=int, default=10,
                        help="列出最差的 N 个目的地（按本地块数升序）")
    parser.add_argument("--no-hybrid", action="store_true",
                        help="跳过混合检索这一轮（只对比过滤开关）")
    args = parser.parse_args()

    all_destinations = retriever.known_destinations()
    if not all_destinations:
        print("公共库里还没有语料，先跑 build_corpus.py index")
        return 2

    destinations = all_destinations[:args.limit] if args.limit else all_destinations
    print(f"目的地 {len(destinations)} 个，top_k={args.top_k}")

    landmarks = pick_landmarks(destinations)
    print(f"抽到地标词 {len(landmarks)} 个，示例："
          f"{list(landmarks.items())[:3]}")

    without = run(destinations, args.top_k, use_filter=False, hybrid=False,
                  landmarks=landmarks)
    with_filter = run(destinations, args.top_k, use_filter=True, hybrid=False,
                      landmarks=landmarks)

    report("全库检索 · 纯向量", without, args.top_k)
    report("目的地过滤 · 纯向量", with_filter, args.top_k)

    delta = (with_filter["local_ratio"] - without["local_ratio"]) * 100
    print(f"\n过滤带来的本地块占比提升：{delta:+.1f} 个百分点")

    if not args.no_hybrid:
        hybrid = run(destinations, args.top_k, use_filter=True, hybrid=True,
                     landmarks=landmarks)
        report("目的地过滤 · 混合检索（向量 + BM25）", hybrid, args.top_k)

        term_delta = (hybrid["term_ratio"] - with_filter["term_ratio"]) * 100
        print(f"\n混合检索带来的地标词命中提升：{term_delta:+.1f} 个百分点")

    if args.show_worst:
        short = [row for row in with_filter["per_dest"]
                 if row["local"] < row["total"]]
        if not short:
            print("\n每个目的地的 top_k 都被本地块填满，没有召回不足的情况。")
        else:
            short.sort(key=lambda row: row["local"])
            print(f"\n过滤后本地块没填满的目的地（前 {args.show_worst} 个）：")
            for row in short[:args.show_worst]:
                print(f"  {row['destination']:<10} 本地 {row['local']}/{row['total']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
