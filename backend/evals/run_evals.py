# author: szw
"""行程生成评测：一次跑完一批用例，用独立规则判分。

设计取舍（为什么这么写）：

1. 走 workflow 而不是打 HTTP。评测要的是结构化 state，不是 SSE 流；
   走 API 还得先登录换 token、再解析事件流，多一层噪声。
2. 每条用例一个独立 thread_id，跑在临时 SQLite 上。CHECKPOINT_DB_PATH 必须
   在 import app.config 之前改掉——settings 是模块级单例，读一次就不再变。
3. 判分不请 LLM 当裁判。让模型给自己打分，结果永远"看起来合理"，
   而且不可复现；规则全部落在可计算的事实上（见 checks.py）。
4. 串行跑。高德个人 key 有 QPS 限制，并发只会把失败率换成分数下降。

用法：
    python evals/run_evals.py                    # 全量
    python evals/run_evals.py --only normal      # 只跑某一类
    python evals/run_evals.py --case normal_dalian_3d
    python evals/run_evals.py --limit 3          # 冒烟
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parent
# 项目里的相对路径都以 backend/app 为基准（见 app/utils/path_tool.py 的 get_project_root），
# 不是 backend。按 backend 算会得到一个不存在的目录，自检和清理都会落空。
APP_DIR = BACKEND_DIR / "app"

# 评测写这些路径，绝不动生产库
EVAL_DB = "./data/checkpoints/evals.db"
LOG_PATH = EVAL_DIR / "run.log"
REPORT_PATH = EVAL_DIR / "last_report.json"

DEFAULT_PASS_LINE = 0.9


def emit(message: str = "") -> None:
    """runner 自己的输出。

    跑评测期间 sys.stdout 被重定向到 run.log（app 里到处是 print，不接管的话
    终端会刷屏）。这里固定写回真正的终端。
    """
    stream = sys.__stdout__ or sys.stdout
    print(message, file=stream, flush=True)


def prepare_env(db_path: str) -> None:
    # 必须在 import app.* 之前设置：settings 是模块级单例。
    # load_dotenv(override=False) 不会覆盖已存在的环境变量，所以这里优先。
    os.environ["CHECKPOINT_DB_PATH"] = db_path
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------- 环境自检


def preflight() -> List[str]:
    """跑之前先确认外部依赖在场。

    环境不全时应当明确报"环境不完整"，而不是把 20 条用例全判成失败——
    那种分数会让人误以为模型退化了。
    """
    problems: List[str] = []
    from app.config import settings

    if not (settings.deepseek_api_key or "").strip():
        problems.append("DEEPSEEK_API_KEY 未配置，无法调用对话模型")
    if not (settings.amap_api_key or "").strip():
        problems.append("AMAP_API_KEY 未配置，地图与景点检索都会失败")

    chroma = APP_DIR / str(settings.chroma_db_path or "./data/chroma_db").lstrip("./")
    if not chroma.exists():
        # 只是降级：RAG 空库会让攻略检索为空，但行程还能生成
        problems.append(f"向量库不存在：{chroma}（攻略检索会为空，评测仍可继续）")

    return problems


# ---------------------------------------------------------------- 单条用例


def _intent_of(state: Dict[str, Any]) -> str:
    step = str(state.get("current_step") or "")
    if step == "clarify" or (state.get("clarify_question") and not state.get("draft_plan")):
        return "clarify"
    if step == "cancelled":
        return "cancelled"
    if state.get("draft_plan"):
        return "plan"
    if state.get("response"):
        return "chat"
    return "failed"


def _count_interrupts(snapshot: Any) -> int:
    total = 0
    for task in getattr(snapshot, "tasks", ()) or ():
        total += len(getattr(task, "interrupts", ()) or ())
    return total


async def run_case(workflow: Any, case: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
    from langchain_core.messages import HumanMessage
    from langgraph.types import Command

    started = time.time()
    text = str(case.get("input") or "")
    repeat = int(case.get("repeat") or 1)
    if repeat > 1:
        text = "\n".join([text] * repeat)

    thread_id = f"eval-{case['id']}-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}
    init = {
        "user_input": text,
        "messages": [HumanMessage(content=text)],
        "thread_id": thread_id,
        "current_step": "init",
    }

    interrupts = 0
    error: Optional[str] = None
    snapshot = None

    try:
        await asyncio.wait_for(workflow.ainvoke(init, config), timeout=timeout_s)
        snapshot = await workflow.aget_state(config)

        # HITL：确认卡会挂住，评测里一律点"确认"继续。
        # 上限 3 次，防某个用例反复中断把整轮卡死。
        for _ in range(3):
            if not snapshot.next:
                break
            interrupts += _count_interrupts(snapshot) or 1
            await asyncio.wait_for(
                workflow.ainvoke(Command(resume={"action": "confirm"}), config),
                timeout=timeout_s,
            )
            snapshot = await workflow.aget_state(config)
    except asyncio.TimeoutError:
        error = f"超时（>{timeout_s:.0f}s）"
    except Exception as exc:  # 单个用例崩了不能带崩整轮
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()

    state: Dict[str, Any] = dict(getattr(snapshot, "values", None) or {}) if snapshot else {}
    plan = state.get("draft_plan") or []
    map_data = state.get("map_data") or {}
    routes = map_data.get("routes") or []

    return {
        "intent": _intent_of(state) if not error else "failed",
        "state": state,
        "interrupts": interrupts,
        "error": error,
        "duration": round(time.time() - started, 1),
        # 走错分支时，光看名字看不出它到底回了什么，摘要一并存进报告
        "response": str(state.get("response") or "")[:160],
        "metrics": {
            "days": len(plan),
            "acts_per_day": [len(d.get("activities") or []) for d in plan],
            "attractions": len(map_data.get("attractions") or []),
            "routes": len(routes),
            "polyline_points": [len(r.get("polyline") or []) for r in routes],
            "review_round": state.get("review_round") or 0,
            "review_passed": state.get("review_passed"),
            # 约束冲突时行程会放行，并把矛盾写进 tips 提示用户，这里留证据
            "conflicts": state.get("review_conflicts") or [],
            "tips": state.get("plan_tips") or [],
        },
    }


# ---------------------------------------------------------------- 主流程


async def run_all(cases: List[Dict[str, Any]], args: argparse.Namespace) -> Dict[str, Any]:
    from app.core.langgraph_app import get_workflow
    import checks

    emit("编译工作流…")
    workflow = await get_workflow()

    semaphore = asyncio.Semaphore(max(1, args.jobs))
    results: List[Dict[str, Any]] = []

    async def guarded(case: Dict[str, Any]) -> None:
        async with semaphore:
            emit(f"→ {case['id']} 运行中…")
            result = await run_case(workflow, case, float(case.get("timeout_s") or args.timeout))
            failures, warnings = checks.check_case(case, result)
            entry = {
                "id": case["id"],
                "category": case.get("category", ""),
                "input": str(case.get("input") or "")[:120],
                "status": "PASS" if not failures else "FAIL",
                "duration": result["duration"],
                "intent": result["intent"],
                "interrupts": result["interrupts"],
                "response": result.get("response", ""),
                "metrics": result["metrics"],
                "failures": failures,
                "warnings": warnings,
            }
            results.append(entry)
            flag = "PASS" if not failures else "FAIL"
            detail = result["metrics"]
            emit(
                f"[{flag}] {case['id']:<26} {result['duration']:>5.1f}s  "
                f"{detail['days']}天/活动{detail['acts_per_day']}/"
                f"景点{detail['attractions']}/路线{detail['routes']}"
                + (f"  ← {failures[0]}" if failures else "")
            )

    await asyncio.gather(*(guarded(c) for c in cases))

    # 按用例顺序输出，避免并发导致报告顺序随机
    order = {c["id"]: i for i, c in enumerate(cases)}
    results.sort(key=lambda r: order.get(r["id"], 0))

    passed = sum(1 for r in results if r["status"] == "PASS")
    total = len(results)
    pass_line = max(1, round(total * (args.pass_line or DEFAULT_PASS_LINE)))
    return {
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_line": pass_line,
        "result": "PASS" if passed >= pass_line else "FAIL",
        "cases": results,
    }


def load_cases(args: argparse.Namespace) -> List[Dict[str, Any]]:
    import yaml

    path = Path(args.cases)
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or []

    cases = data.get("cases") if isinstance(data, dict) else data
    cases = list(cases or [])

    if args.only:
        wanted = {c.strip() for c in args.only.split(",")}
        cases = [c for c in cases if c.get("category") in wanted]
    if args.case:
        wanted = {c.strip() for c in args.case.split(",")}
        cases = [c for c in cases if c.get("id") in wanted]
    if args.limit:
        cases = cases[: args.limit]
    return cases


def summarize(report: Dict[str, Any]) -> None:
    emit("")
    emit("=" * 78)
    failed = [c for c in report["cases"] if c["status"] == "FAIL"]
    if failed:
        emit(f"失败 {len(failed)} 条：")
        for case in failed:
            emit(f"  ✗ {case['id']}")
            for item in case["failures"][:4]:
                emit(f"      {item}")
        emit("")
    warned = [c for c in report["cases"] if c["warnings"]]
    if warned:
        emit(f"有告警 {len(warned)} 条：")
        for case in warned:
            emit(f"  ! {case['id']}: {case['warnings'][0]}")
        emit("")

    emit(f"汇总 {report['passed']}/{report['total']} "
         f"（通过线 {report['pass_line']}）→ {report['result']}")
    emit("=" * 78)


def _finish(code: int) -> None:
    """结束进程。

    MCP 的 stdio 子进程挂在 anyio 的 task group 里，asyncio.run 的收尾会卡在
    等它们退出上——实测主流程 30 秒就跑完，进程却挂了 5 分钟不退。
    报告和日志此时都已落盘，直接结束进程，不陪它等。
    """
    with contextlib.suppress(Exception):
        sys.stdout.flush()
        sys.stderr.flush()
    os._exit(code)


def main() -> int:
    parser = argparse.ArgumentParser(description="TripGenie 行程生成评测")
    parser.add_argument("--cases", default=str(EVAL_DIR / "cases.yaml"))
    parser.add_argument("--only", default="", help="按 category 过滤，逗号分隔")
    parser.add_argument("--case", default="", help="按 id 过滤，逗号分隔")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（冒烟用）")
    parser.add_argument("--jobs", type=int, default=1, help="并发数，默认 1（高德 QPS 有限）")
    parser.add_argument("--timeout", type=float, default=300.0, help="单条超时（秒）")
    parser.add_argument("--pass-line", type=float, default=DEFAULT_PASS_LINE, help="通过线比例")
    parser.add_argument("--db", default=EVAL_DB, help="评测用 checkpoint 库（相对 backend/app/）")
    parser.add_argument("--keep-db", action="store_true", help="保留评测库，便于排查")
    parser.add_argument("--verbose", action="store_true", help="不在终端屏蔽应用日志")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8")

    prepare_env(args.db)
    cases = load_cases(args)
    if not cases:
        emit("没有匹配到任何用例")
        _finish(2)

    emit(f"用例 {len(cases)} 条 | 并发 {args.jobs} | 单条超时 {args.timeout:.0f}s | 评测库 {args.db}")

    problems = preflight()
    for item in problems:
        emit(f"⚠️  {item}")

    db_file = (APP_DIR / args.db.lstrip("./")).resolve()
    if not args.keep_db and db_file.exists():
        # 每条用例都用独立 thread_id，复用旧库不影响正确性，所以删不掉也不算失败。
        # （某些环境会拦截 unlink 走回收站，这里不能让它把整轮评测带崩。）
        try:
            db_file.unlink()
            emit(f"已清空上次的评测库：{db_file}")
        except OSError:
            emit(f"⚠️  上次的评测库未能清空，将直接复用：{db_file}")

    started = time.time()
    log_file = open(LOG_PATH, "w", encoding="utf-8")
    try:
        if args.verbose:
            report = asyncio.run(run_all(cases, args))
        else:
            # 应用的 print 太多，整轮重定向到 run.log；runner 自己走 sys.__stdout__
            with contextlib.redirect_stdout(log_file):
                report = asyncio.run(run_all(cases, args))
    except Exception as exc:
        emit(f"评测中断：{type(exc).__name__}: {exc}")
        traceback.print_exc()
        _finish(3)
    finally:
        log_file.close()

    report["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started))
    report["duration_min"] = round((time.time() - started) / 60, 1)

    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summarize(report)
    emit(f"报告：{REPORT_PATH}")
    emit(f"应用日志：{LOG_PATH}")
    emit(f"总耗时：{report['duration_min']} 分钟")
    _finish(0 if report["result"] == "PASS" else 1)


if __name__ == "__main__":
    sys.exit(main())
