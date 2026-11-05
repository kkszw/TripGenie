# author: szw
"""检索质量自检：量化「按目的地过滤」到底解决了什么问题。

为什么需要它：125 份攻略是同一家出的同一套模板，「交通·TRANSPORTATION」
「有问必答·FAQ」「亮点·HIGH LIGHTS」这些章节标题逐字相同，正文的句式也
高度雷同。向量空间里它们挤成一团，不过滤时问大连会拿回三亚的交通段落——
内容看着很专业，其实是错的，而且 LLM 会照着它编行程。

两个指标：
  local@k    top-k 里属于本目的地的块占比。越高说明上下文越相关。
  hit@k      至少命中 1 条本地块的目的地比例。低了说明语料覆盖有洞。

对照跑两遍（开过滤 / 关过滤），差值就是过滤带来的收益。

跑法：
    cd backend
    python scripts/eval_retrieval.py                  # 全量目的地
    python scripts/eval_retrieval.py --limit 30       # 只测前 30 个，快
    python scripts/eval_retrieval.py --top-k 8
"""
import argparse
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.rag import retriever  # noqa: E402


def query_for(destination: str) -> str:
    """构造查询。刻意用和线上一致的口吻（retrieval 节点就是这么拼的），
    否则测出来的数字不代表真实使用场景。"""
    return f"去{destination}玩3天，预算3000,喜欢自然风光 当地美食"


def run(destinations: List[str], top_k: int, use_filter: bool) -> Dict:
    """跑一轮检索，统计本地块占比。"""
    local_hits = 0
    total_chunks = 0
    covered = 0
    per_dest: List[Dict] = []

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
        per_dest.append({"destination": destination, "local": local, "total": len(docs)})

    count = len(destinations) or 1
    return {
        "local_ratio": local_hits / total_chunks if total_chunks else 0.0,
        "local_per_query": local_hits / count,
        "hit_rate": covered / count,
        "total_chunks": total_chunks,
        "per_dest": per_dest,
    }


def report(label: str, result: Dict, top_k: int) -> None:
    print(f"\n{label}")
    print(f"  本地块占比 local@{top_k}   {result['local_ratio']:>6.1%}")
    print(f"  每问平均本地块            {result['local_per_query']:>6.2f}")
    print(f"  至少命中一条的目的地      {result['hit_rate']:>6.1%}")


def main() -> int:
    parser = argparse.ArgumentParser(description="检索质量自检")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=0, help="只测前 N 个目的地")
    parser.add_argument("--show-worst", type=int, default=10,
                        help="列出最差的 N 个目的地（按本地块数升序）")
    args = parser.parse_args()

    all_destinations = retriever.known_destinations()
    if not all_destinations:
        print("公共库里还没有语料，先跑 build_corpus.py index")
        return 2

    destinations = all_destinations[:args.limit] if args.limit else all_destinations
    print(f"目的地 {len(destinations)} 个，top_k={args.top_k}")

    without = run(destinations, args.top_k, use_filter=False)
    with_filter = run(destinations, args.top_k, use_filter=True)

    report("关闭目的地过滤（全库检索）", without, args.top_k)
    report("开启目的地过滤", with_filter, args.top_k)

    delta = (with_filter["local_ratio"] - without["local_ratio"]) * 100
    print(f"\n差值：本地块占比 {delta:+.1f} 个百分点")

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
