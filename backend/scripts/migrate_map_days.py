# author: szw
"""
给历史行程的地图数据**回填路线所属天数**（routes[].day），并可选重建含幻觉景点的数据。

背景：前端按天出图时，原来是拿"这天的景点名"去反推路线属于哪天
（`routes.filter(r => dayNames.includes(r.from))`）。这有两个问题：
  1. 重名景点（两条街都叫"中山路"）或跨天复用同一个点时，路线会挂到错误的天；
  2. routes 里根本没有 day 字段。

代码侧已修（新生成的行程 routes 带 day，前端优先读 day、老数据才回退名字匹配），
但**库里已有的老数据**仍是旧结构。这个脚本做两件不同量级的事：

  【默认】只做本地推断，不请求高德、不调模型：
      - 用 content 里存的行程 JSON，把每个景点名匹配到它出现在哪一天的 activity 文本里；
      - 两端景点同天的路线 → 那天；不同天则取起点的天；
      - attractions[].day / routes[].day 规范化成整数（老数据可能是字符串）。

  【--rebuild】只对"地图里有原文根本没提过的景点"的消息重跑一次地图生成（走 LLM 提取
      + 高德地理编码 + 路线）。旧版提取器提示词里有一句"未给出明确地点则随机选择一处街道"，
      它在教模型编地点——历史数据里那些没去过的点就是这么来的。重建后只保留原文提到的景点。

用法（在 backend 目录下）：
    python scripts/migrate_map_days.py                      # 只体检，不写库
    python scripts/migrate_map_days.py --apply              # 回填 day（自动备份 db）
    python scripts/migrate_map_days.py --rebuild --apply    # 连幻觉景点一起重建地图
    python scripts/migrate_map_days.py --thread <id>        # 只处理某个会话
    python scripts/migrate_map_days.py --limit 5 --apply    # 只处理前 5 条消息
"""

import asyncio
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.database import db  # noqa: E402


# ==================== 小工具 ====================

def load_plan_json(content: str):
    """把 messages.content 解析成行程 dict。

    里面可能是：行程 JSON、```json 围栏包着的 JSON、纯聊天文本。
    这里不引入 agents.parse_llm_json —— 那个模块会连带初始化 MCP 客户端，
    一个纯本地迁移脚本不该为了解析 JSON 去连高德。
    """
    if not content or not isinstance(content, str):
        return None
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except Exception:
            return None
    return data if isinstance(data, dict) else None


def day_texts(plan: dict):
    """{天数: 该天所有活动的文本}，用于把景点名落到具体某天"""
    out = {}
    for idx, day in enumerate(plan.get("days") or []):
        try:
            day_no = int(day.get("day") or idx + 1)
        except (AttributeError, TypeError, ValueError):
            day_no = idx + 1
        parts = []
        for act in day.get("activities") or []:
            if isinstance(act, dict):
                parts.append(str(act.get("activity") or ""))
                parts.append(str(act.get("description") or ""))
            else:
                parts.append(str(act))
        out[day_no] = "\n".join(parts)
    return out


def guess_day(name: str, texts: dict):
    """景点名落在哪天的文本里；找不到就返回 None（不猜）"""
    if not name:
        return None
    for day_no in sorted(texts):
        if name in texts[day_no]:
            return day_no
    return None


def is_hallucinated(name: str, texts: dict) -> bool:
    """行程原文里到底提没提过这个景点。

    提取器有可能把"老虎滩海洋公园"截成"老虎滩"，所以名字整体匹配不上时，
    再去掉常见后缀比一次，避免把正常景点误判成幻觉。
    """
    if not name:
        return True
    for text in texts.values():
        if name in text:
            return False
    base = re.sub(r"(海洋公园|国家旅游度假区|旅游度假区|风景区|公园|广场|海滩|海滨|景区|度假区)$", "", name)
    if base and base != name:
        for text in texts.values():
            if base in text:
                return False
    return True


# ==================== 迁移（纯本地） ====================

def migrate_one(md: dict, plan: dict):
    """就地修改 md，返回 (改动段数, 缺失景点的天数列表, 幻觉景点列表)"""
    changes = 0
    texts = day_texts(plan or {})
    attractions = md.get("attractions") or []
    routes = md.get("routes") or []

    # 1. 景点：day 规范化 + 用原文文本反推所属天
    name_days = {}
    hallucinations = []
    for attr in attractions:
        name = attr.get("name") or ""
        old_day = attr.get("day")
        guessed = guess_day(name, texts)
        try:
            new_day = int(old_day) if old_day not in (None, "") else None
        except (TypeError, ValueError):
            new_day = None
        if guessed is not None:
            new_day = guessed
        if new_day is not None:
            if attr.get("day") != new_day or not isinstance(attr.get("day"), int):
                attr["day"] = new_day
                changes += 1
            name_days[name] = new_day
        if texts and is_hallucinated(name, texts):
            hallucinations.append(name)

    # 2. 路线：两端同天取那天，否则取起点的天
    for route in routes:
        from_day = name_days.get(route.get("from"))
        to_day = name_days.get(route.get("to"))
        if from_day is not None and to_day is not None and from_day == to_day:
            new_day = from_day
        else:
            new_day = from_day if from_day is not None else to_day
        if new_day is None:
            continue
        if route.get("day") != new_day or not isinstance(route.get("day"), int):
            route["day"] = new_day
            changes += 1

    # 3. 诊断：哪些天一个景点都没有
    covered = {a["day"] for a in attractions if isinstance(a.get("day"), int)}
    total_days = len(plan.get("days") or []) if plan else 0
    missing = [d for d in range(1, total_days + 1) if d not in covered]
    return changes, missing, hallucinations, texts, total_days


# ==================== 重建（会打外部接口） ====================

async def rebuild_one(plan: dict):
    """按当前提示词重跑地图生成。只在 --rebuild 时调用。"""
    from app.core.agents import generate_map_data

    days = plan.get("days") or []
    state = {
        "destination": plan.get("destination") or "",
        "days": plan.get("totalDays") or len(days),
        "budget": plan.get("budget"),
        "preferences": [],
    }
    return await generate_map_data(state, {"days": days})


# ==================== 主流程 ====================

def backup_db() -> Path:
    src = Path(db.db_path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = src.with_name(f"{src.name}.bak-{stamp}")
    shutil.copy2(src, dst)
    return dst


def _arg_value(flag: str):
    if flag not in sys.argv:
        return None
    try:
        return sys.argv[sys.argv.index(flag) + 1]
    except IndexError:
        return None


async def main():
    apply = "--apply" in sys.argv
    rebuild = "--rebuild" in sys.argv
    limit = _arg_value("--limit")
    thread_id = _arg_value("--thread")
    if limit is not None:
        try:
            limit = int(limit)
        except ValueError:
            print("--limit 后面要跟一个数字")
            return

    conn = db._get_connection()
    sql = ("SELECT id, thread_id, role, content, map_data FROM messages "
           "WHERE map_data IS NOT NULL AND map_data != ''")
    params = []
    if thread_id:
        sql += " AND thread_id = ?"
        params.append(thread_id)
    sql += " ORDER BY id"
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    if limit:
        rows = rows[:limit]

    print(f"待体检 {len(rows)} 条带地图数据的消息"
          f"{f'（会话 {thread_id}）' if thread_id else ''}\n")

    todo = []       # (msg_id, md, changes, plan, hallucinations)
    for row in rows:
        try:
            md = json.loads(row["map_data"])
        except Exception as e:
            print(f"msg {row['id']} map_data 解析失败: {e}")
            continue
        plan = load_plan_json(row["content"])
        changes, missing, hallucinations, texts, total_days = migrate_one(md, plan or {})

        print(f"--- msg {row['id']} | {row['thread_id'][:8]} ---")
        if not plan:
            print("   ⚠️ content 里没有行程 JSON，只能按原有 day 规范化")
        else:
            per_day = {}
            for a in md.get("attractions") or []:
                per_day.setdefault(a.get("day"), []).append(a.get("name"))
            for d in sorted(k for k in per_day if isinstance(k, int)):
                print(f"   Day{d}: {len(per_day[d])} 个景点 - {', '.join(per_day[d])}")
            day_routes = {}
            for r in md.get("routes") or []:
                key = r.get("day")
                day_routes[key] = day_routes.get(key, 0) + 1
            if day_routes:
                ordered = dict(sorted(day_routes.items(), key=lambda x: (x[0] is None, x[0] or 0)))
                print(f"   路线: {ordered}")

        if hallucinations:
            print(f"   ⚠️ 这些景点在行程原文里找不到（旧提取器会编地点）: {', '.join(hallucinations)}")
        if missing and plan:
            print(f"   ⚠️ Day {missing} 没有任何景点，这几天的地图会是空的。原文对应活动：")
            for d in missing:
                snippet = (texts.get(d) or "").strip().replace("\n", " / ")
                print(f"      Day{d}: {snippet[:110] or '（这一天没有活动文本）'}")

        if changes:
            print(f"   → 待补/规范化 {changes} 处 day 字段")
        else:
            print("   ✓ day 字段已是最新")
        todo.append((row["id"], md, changes, plan, hallucinations))
        print()

    updates = {}   # msg_id -> md

    # 第一轮：本地迁移
    for msg_id, md, changes, _, _ in todo:
        if changes:
            updates[msg_id] = md

    # 第二轮：按需重建含幻觉景点的消息
    if rebuild:
        targets = [(mid, plan, hall) for mid, md, _, plan, hall in todo if plan and hall]
        if not targets:
            print("没有需要重建的消息（未发现原文里不存在的景点）")
        else:
            print(f"开始重建 {len(targets)} 条消息的地图数据（会调用模型 + 高德）...\n")
            for msg_id, plan, hall in targets:
                print(f"--- 重建 msg {msg_id}，可疑景点: {', '.join(hall)} ---")
                try:
                    new_md = await rebuild_one(plan)
                except Exception as e:
                    print(f"   ❌ 重建失败，保留原数据: {e}\n")
                    continue
                if not new_md or not new_md.get("attractions"):
                    print("   ❌ 重建没有产出景点，保留原数据\n")
                    continue
                names = [a["name"] for a in new_md["attractions"]]
                new_hall = [n for n in names
                            if is_hallucinated(n, day_texts(plan))]
                print(f"   新景点 {len(names)} 个: {', '.join(names)}")
                print(f"   新路线 {len(new_md.get('routes') or [])} 段，"
                      f"全部带 day: {all(isinstance(r.get('day'), int) for r in new_md.get('routes') or [])}")
                if new_hall:
                    print(f"   ⚠️ 重建后仍有可疑景点: {', '.join(new_hall)}")
                updates[msg_id] = new_md
                print()

    if not updates:
        print("没有需要写回的消息。")
        return

    print(f"共 {len(updates)} 条消息待写回")
    if not apply:
        print("（这是体检模式，加 --apply 才会写库）")
        return

    backup = backup_db()
    print(f"已备份数据库: {backup}")

    conn = db._get_connection()
    for msg_id, md in updates.items():
        conn.execute(
            "UPDATE messages SET map_data = ? WHERE id = ?",
            (json.dumps(md, ensure_ascii=False), msg_id),
        )
    conn.commit()
    conn.close()
    print(f"✅ 已写回 {len(updates)} 条消息的 map_data")


if __name__ == "__main__":
    asyncio.run(main())
