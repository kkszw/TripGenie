# author: szw
"""行程排程质量的基线测量脚本。

为什么需要它：要证明"把排程从 LLM 手里拿走"这件事有收益，必须先有改造前的
真实数字，否则简历上那句"通勤时间下降 X%"就是编的。

数据来源是本地 sessions.db：LangGraph 的 checkpoint 里存着完整的 draft_plan
（activities 带 "09:00-10:30"），messages 表里存着 map_data（每段驾车
distance / duration）。两者按 thread_id 关联，**不需要重跑 LLM**，所以
改造前后可以用同一批行程做干净对比。

四个指标：
  short_gap   相邻两个活动的间隔 < 15 分钟的占比。LLM 排行程时经常
              "09:00-10:30 然后 10:30-12:00"，中间不留任何通勤时间——
              这一段在市内也要 10 分钟，属于物理上做不到。
  commute     日均驾车时长（分钟），来自 map_data.routes 的 duration。
  long_hop    单段驾车 > 20km 的段数，用来抓"一天跨半个城市"的折返。
  issues      code_review 的违规条数（规则 1/3/4/5）。

跑法：
    cd backend && python scripts/schedule_baseline.py
    cd backend && python scripts/schedule_baseline.py --json /tmp/before.json
"""
import argparse
import asyncio
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import aiosqlite  # noqa: E402
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402

from app.core.nodes.review import _time_start, code_review  # noqa: E402
from app.services.scheduler import optimize_days, place_name, schedule_stats  # noqa: E402

DEFAULT_DB = ROOT / "app" / "data" / "checkpoints" / "sessions.db"

# 相邻活动之间至少留这么多分钟，才够走到下一个点。市内短途打车/步行都在
# 10 分钟上下，低于 15 分钟基本意味着"上一个结束立刻出现在下一个地方"。
MIN_TRANSFER_MIN = 15

# 单段超过这个距离就算跨区，一天里出现多次说明行程在东西两头折返。
LONG_HOP_METERS = 20000


def span_minutes(value):
    """'09:00-10:30' -> (540, 630)。解析不出来（或跨零点）返回 None。"""
    start = _time_start(value)
    if start is None:
        return None
    parts = re.split(r"[-~—至]", str(value))
    if len(parts) < 2:
        return None
    end = _time_start(parts[-1])
    if end is None or end < start:
        return None
    return start, end


def plan_metrics(plan, map_data, days, budget):
    """对一份行程算四个指标。plan 是 days[] 结构，map_data 可为 None。"""
    short_pairs = 0
    zero_pairs = 0
    total_pairs = 0
    activity_count = 0

    for day in plan or []:
        acts = day.get("activities") or []
        activity_count += len(acts)
        spans = [span_minutes(a.get("time")) for a in acts]
        for i in range(len(spans) - 1):
            current, following = spans[i], spans[i + 1]
            if not current or not following:
                continue
            total_pairs += 1
            gap = following[0] - current[1]
            if gap <= 0:
                zero_pairs += 1
            if gap < MIN_TRANSFER_MIN:
                short_pairs += 1

    commute_min = 0.0
    long_hops = 0
    route_days = set()
    for route in (map_data or {}).get("routes") or []:
        try:
            duration = float(route.get("duration") or 0)
            distance = float(route.get("distance") or 0)
        except (TypeError, ValueError):
            continue
        commute_min += duration / 60.0
        route_days.add(route.get("day"))
        if distance > LONG_HOP_METERS:
            long_hops += 1

    issues = code_review({"draft_plan": plan, "days": days, "budget": budget})

    return {
        "days": len(plan or []),
        "activities": activity_count,
        "short_gap_pairs": short_pairs,
        "zero_gap_pairs": zero_pairs,
        "time_pairs": total_pairs,
        "short_gap_ratio": round(short_pairs / total_pairs, 3) if total_pairs else None,
        "zero_gap_ratio": round(zero_pairs / total_pairs, 3) if total_pairs else None,
        "commute_min": round(commute_min, 1),
        "commute_days": len(route_days),
        "commute_per_day": round(commute_min / len(route_days), 1) if route_days else None,
        "long_hops": long_hops,
        "issues": len(issues),
        "issue_detail": issues,
    }


async def load_samples(db_path):
    """从 checkpoint 读行程，从 messages 读对应的 map_data，按 thread 对齐。"""
    conn = await aiosqlite.connect(str(db_path))
    conn.row_factory = aiosqlite.Row
    saver = AsyncSqliteSaver(conn=conn)
    samples = []

    try:
        cursor = await conn.execute("SELECT DISTINCT thread_id FROM checkpoints")
        thread_ids = [row[0] for row in await cursor.fetchall()]
        await cursor.close()

        for thread_id in thread_ids:
            tuple_ = await saver.aget_tuple({"configurable": {"thread_id": thread_id}})
            if not tuple_:
                continue
            values = tuple_.checkpoint.get("channel_values", {})
            # 旧 checkpoint 里可能还有 final_plan（已废弃字段，从没有节点写过它），
            # 一并兼容，免得跑历史数据时全部取到 None。
            plan = values.get("draft_plan") or values.get("final_plan")
            if not plan:
                continue

            cursor = await conn.execute(
                "SELECT map_data FROM messages WHERE thread_id = ? AND map_data IS NOT NULL "
                "ORDER BY id DESC LIMIT 1",
                (thread_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            map_data = json.loads(row["map_data"]) if row and row["map_data"] else None

            samples.append({
                "thread_id": thread_id,
                "destination": values.get("destination"),
                "days": values.get("days"),
                "budget": values.get("budget"),
                "plan": plan,
                "map_data": map_data,
            })
    finally:
        await conn.close()

    return samples


def summarize(rows):
    """汇总成简历上能用的几个数字。

    驾车时长取中位数：样本里混着"山东省内多城自驾"这类长距离行程（单日 1600+
    分钟），均值会被它一个人拉高，中位数才代表"典型市内行程"。
    """
    with_gap = [r for r in rows if r["time_pairs"]]
    with_commute = [r for r in rows if r["commute_per_day"] is not None]
    commutes = [r["commute_per_day"] for r in with_commute]
    return {
        "samples": len(rows),
        "samples_with_time": len(with_gap),
        "samples_with_map": len(with_commute),
        "avg_short_gap_ratio": (
            round(sum(r["short_gap_pairs"] for r in with_gap)
                  / sum(r["time_pairs"] for r in with_gap), 3)
            if with_gap else None
        ),
        "avg_zero_gap_ratio": (
            round(sum(r["zero_gap_pairs"] for r in with_gap)
                  / sum(r["time_pairs"] for r in with_gap), 3)
            if with_gap else None
        ),
        "median_commute_per_day": round(statistics.median(commutes), 1) if commutes else None,
        "avg_long_hops": (
            round(sum(r["long_hops"] for r in with_commute) / len(with_commute), 2)
            if with_commute else None
        ),
        "samples_with_issues": sum(1 for r in rows if r["issues"] > 0),
        "total_issues": sum(r["issues"] for r in rows),
    }


def coord_lookup(map_data):
    """用历史 map_data 里的景点坐标构造查找函数——离线就能重排，不用联网。

    map_data 里的名字是 LLM 提取的景点名（"栈桥"），行程里的活动是
    "游览栈桥"，所以先精确匹配、再退化到包含匹配。
    """
    table = {}
    for item in (map_data or {}).get("attractions") or []:
        name = item.get("name")
        if name and item.get("lng") is not None and item.get("lat") is not None:
            table[name] = (float(item["lng"]), float(item["lat"]))

    def lookup(name):
        if not name:
            return None
        if name in table:
            return table[name]
        for key, coord in table.items():
            if key in name or name in key:
                return coord
        return None

    return lookup


def day_overruns(plan) -> int:
    """有多少天的最后一个活动排到了闭店（20:00）之后。"""
    overruns = 0
    for day in plan or []:
        spans = [span_minutes(a.get("time")) for a in day.get("activities") or []]
        spans = [span for span in spans if span]
        if spans and max(span[1] for span in spans) > 20 * 60:
            overruns += 1
    return overruns


async def main():
    parser = argparse.ArgumentParser(description="行程排程质量基线")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="sessions.db 路径")
    parser.add_argument("--json", help="把明细写到这个文件")
    parser.add_argument("--verbose", action="store_true", help="逐条打印指标")
    parser.add_argument("--replay", action="store_true",
                        help="用历史 map_data 的坐标重跑排程，输出改造前后对比")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}")
        return 1

    samples = await load_samples(db_path)
    rows = []
    for sample in samples:
        metrics = plan_metrics(sample["plan"], sample["map_data"], sample["days"], sample["budget"])
        rows.append({
            "thread_id": sample["thread_id"],
            "destination": sample["destination"],
            **metrics,
        })

    summary = summarize(rows)

    if args.verbose:
        header = f"{'目的地':<12}{'天':>3}{'活动':>5}{'短间隔':>8}{'日均驾车':>10}{'跨区':>6}{'质检':>5}"
        print(header)
        for row in rows:
            ratio = "-" if row["short_gap_ratio"] is None else f"{row['short_gap_ratio']:.0%}"
            commute = "-" if row["commute_per_day"] is None else f"{row['commute_per_day']:.0f}min"
            print(f"{(row['destination'] or '未知'):<12}{row['days'] or 0:>3}{row['activities']:>5}"
                  f"{ratio:>8}{commute:>10}{row['long_hops']:>6}{row['issues']:>5}")
        print()

    print(f"样本数            {summary['samples']}（有时间 {summary['samples_with_time']}，"
          f"有地图 {summary['samples_with_map']}）")
    short_ratio = summary["avg_short_gap_ratio"]
    zero_ratio = summary["avg_zero_gap_ratio"]
    print(f"短间隔占比        {short_ratio:.1%}" if short_ratio is not None else "短间隔占比        -")
    print(f"零间隔占比        {zero_ratio:.1%}" if zero_ratio is not None else "零间隔占比        -")
    print(f"日均驾车（中位）  {summary['median_commute_per_day']} 分钟")
    print(f"平均跨区段        {summary['avg_long_hops']}")
    print(f"有问题的行程      {summary['samples_with_issues']}/{summary['samples']}，"
          f"共 {summary['total_issues']} 条违规")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n明细已写入 {args.json}")

    if args.replay:
        await replay(samples)

    return 0


async def replay(samples):
    """把历史行程原样喂给排程器，对比改造前后的指标。

    坐标取自这些行程自己的 map_data（当时高德真的查过），所以整个过程不联网、
    不调 LLM——同一份输入只换排程逻辑，这是最干净的 A/B。
    """
    before = {"pairs": 0, "short": 0, "travel": 0.0, "days": 0, "overrun": 0}
    after = {"pairs": 0, "short": 0, "travel": 0.0, "days": 0, "overrun": 0}
    before_daily = []
    after_daily = []
    dropped_total = 0
    used = 0
    skipped = 0

    for sample in samples:
        lookup = coord_lookup(sample["map_data"])
        hits = sum(
            1
            for day in sample["plan"]
            for activity in day.get("activities") or []
            if lookup(place_name(activity.get("activity")))
        )
        if not hits:
            skipped += 1
            continue

        before_stats = schedule_stats(sample["plan"], lookup)
        result = optimize_days(sample["plan"], lookup)
        after_stats = schedule_stats(result.days, lookup)

        day_count = len(sample["plan"]) or 1
        used += 1
        dropped_total += len(result.dropped)
        before_daily.append(before_stats["travel_minutes"] / day_count)
        after_daily.append(after_stats["travel_minutes"] / day_count)
        before["pairs"] += before_stats["time_pairs"]
        before["short"] += before_stats["short_gap_pairs"]
        before["travel"] += before_stats["travel_minutes"]
        before["days"] += day_count
        before["overrun"] += day_overruns(sample["plan"])
        after["pairs"] += after_stats["time_pairs"]
        after["short"] += after_stats["short_gap_pairs"]
        after["travel"] += after_stats["travel_minutes"]
        after["days"] += day_count
        after["overrun"] += day_overruns(result.days)

    if not used:
        print("\n没有可用于回放的样本（历史消息里缺 map_data 坐标）")
        return

    def ratio(bucket):
        return bucket["short"] / bucket["pairs"] if bucket["pairs"] else 0.0

    # 驾车时长取中位数：样本里混着"山东省内多城自驾"，均值会被单条长途行程带偏。
    print(f"\n=== 同一批行程，只换排程逻辑（{used} 条，跳过 {skipped} 条无坐标）===")
    print(f"{'指标':<18}{'改造前':>10}{'改造后':>10}")
    print(f"{'短间隔占比':<18}{ratio(before):>9.1%}{ratio(after):>10.1%}")
    print(f"{'日均驾车(中位,估算)':<12}{statistics.median(before_daily):>8.1f}min"
          f"{statistics.median(after_daily):>10.1f}min")
    print(f"{'排到 20:00 后的天数':<13}{before['overrun']:>9}{after['overrun']:>10}")
    print(f"{'被移出的活动':<17}{'-':>10}{dropped_total:>10}")
    print(f"（统计覆盖 {before['pairs']} 个活动衔接对；只有能和历史 map_data 坐标"
          f"对上的活动才参与重排）")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
