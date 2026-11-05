# author: szw
"""一次运行的度量：节点耗时、LLM token、外部工具调用次数。

回答三个问题：
  1. 这 30 秒花在哪个节点？（改之前只能靠临时脚本才测得出来）
  2. 调了几次模型、多少 token、折算多少钱？
  3. 调了多少次高德/天气接口？（外部依赖多，超时和限流都藏在这里）

为什么不用 OpenTelemetry / Prometheus：单进程、关心的只是"这一次跑得怎么样"，
攒在内存里、结束时写一行 JSON 就够。引一套监控栈要额外起 collector、配 exporter，
对一个本地跑的项目是纯负担。真需要链路追踪时，这里的 run_id 就是天然的锚点。
"""
from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.logging import get_logger, log_dir_path

logger = get_logger(__name__)


@dataclass
class LlmCall:
    node: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    duration_ms: float = 0.0
    # 这次调用发生在高峰还是空闲时段。逐条记而不是在 summary 里判一次：
    # 一次规划可能跨 18:00，前半段高峰价、后半段空闲价。
    peak: bool = True


# DeepSeek 按时段计价：工作日 9:00-12:00、14:00-18:00 为高峰，其余（含周末）
# 为高峰价的一半。
PEAK_WINDOWS = ((9, 12), (14, 18))


def is_peak_hour(when: datetime) -> bool:
    """是否处于高峰时段。

    法定节假日官方也算空闲，但哪几天放假每年都变，离线判断不了；
    这里按「周末 + 工作日时段」算，最多把节假日当天的成本算高一倍。
    """
    if when.weekday() >= 5:
        return False
    return any(start <= when.hour < end for start, end in PEAK_WINDOWS)


class RunMetrics:
    """一次请求（含中断后恢复的那半段）的度量容器。

    节点耗时用"每个节点只保留最后一次结束时间"的朴素做法：同一个节点在一次
    运行里最多执行两次（review 打回重排会让 generate_plan 跑第二遍），
    这里按节点名累计，重排的两次会被加在一起——这正是我们想看的"这个节点总共花了多久"。
    """

    def __init__(self, trace_id: str, thread_id: str = "", user_id: Any = ""):
        self.trace_id = trace_id
        self.thread_id = thread_id
        self.user_id = user_id
        self.started = time.perf_counter()
        self._node_started: Dict[str, float] = {}
        self.node_ms: Dict[str, float] = {}
        self.llm_calls: List[LlmCall] = []
        self._llm_started: Dict[str, float] = {}
        self.tools: Counter = Counter()
        self.status = "running"
        self.extra: Dict[str, Any] = {}

    # ------------------------------------------------------------------ 采集

    def node_start(self, name: str) -> None:
        self._node_started[name] = time.perf_counter()

    def node_end(self, name: str) -> None:
        start = self._node_started.pop(name, None)
        if start is None:
            return
        cost = (time.perf_counter() - start) * 1000
        self.node_ms[name] = round(self.node_ms.get(name, 0.0) + cost, 1)

    def llm_start(self, run_id: str) -> None:
        self._llm_started[run_id] = time.perf_counter()

    def add_llm(self, node: str, model: str, input_tokens: int, output_tokens: int,
                run_id: Optional[str] = None, cache_hit_tokens: int = 0) -> None:
        duration = 0.0
        if run_id:
            start = self._llm_started.pop(run_id, None)
            if start is not None:
                duration = (time.perf_counter() - start) * 1000
        # 命中数不可能超过输入总数，多算会算成负数价格
        hit = max(0, min(cache_hit_tokens or 0, input_tokens or 0))
        self.llm_calls.append(LlmCall(
            node=node or "-",
            model=model or "-",
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
            cache_hit_tokens=hit,
            duration_ms=round(duration, 1),
            peak=is_peak_hour(datetime.now()),
        ))

    def add_tool(self, name: str) -> None:
        if name:
            self.tools[name] += 1

    def note(self, key: str, value: Any) -> None:
        """挂业务字段（目的地、天数等），最后一起写进摘要。"""
        if value not in (None, "", [], {}):
            self.extra[key] = value

    def finish(self, status: str = "ok") -> None:
        self.status = status

    # ------------------------------------------------------------------ 汇总

    @property
    def total_ms(self) -> float:
        return round((time.perf_counter() - self.started) * 1000, 1)

    def _llm_by_node(self) -> Dict[str, Dict[str, int]]:
        grouped: Dict[str, Dict[str, int]] = {}
        for call in self.llm_calls:
            row = grouped.setdefault(call.node, {"calls": 0, "in": 0, "out": 0})
            row["calls"] += 1
            row["in"] += call.input_tokens
            row["out"] += call.output_tokens
        return grouped

    def _cost_cny(self) -> float:
        """按三档单价 + 时段折扣算成本。

        输入价必须拆成「缓存命中」和「未命中」：两者差 50 倍。只看总输入量
        按未命中价算，会把一次几千字上下文的规划算成几分钱，实际只要几厘。
        """
        total = 0.0
        for call in self.llm_calls:
            ratio = 1.0 if call.peak else settings.llm_offpeak_ratio
            miss = max(0, call.input_tokens - call.cache_hit_tokens)
            total += (
                call.cache_hit_tokens * settings.llm_cache_hit_price
                + miss * settings.llm_input_price
                + call.output_tokens * settings.llm_output_price
            ) * ratio
        return total / 1_000_000

    def summary(self) -> Dict[str, Any]:
        total = self.total_ms
        nodes = {
            name: {"ms": ms, "pct": round(ms / total * 100, 1) if total else 0.0}
            for name, ms in sorted(self.node_ms.items(), key=lambda kv: -kv[1])
        }
        input_tokens = sum(c.input_tokens for c in self.llm_calls)
        output_tokens = sum(c.output_tokens for c in self.llm_calls)
        cache_hit = sum(c.cache_hit_tokens for c in self.llm_calls)
        cost = self._cost_cny()

        return {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "trace": self.trace_id,
            "thread_id": self.thread_id,
            "user": self.user_id,
            "status": self.status,
            "total_ms": total,
            "nodes": nodes,
            "llm": {
                "calls": len(self.llm_calls),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                # 命中率是判断"缓存到底有没有生效"的唯一依据。前缀稳定才有命中，
                # 一旦有人往 prompt 开头塞时间戳/随机 id，这个数会掉到 0。
                "cache_hit_tokens": cache_hit,
                "cache_hit_ratio": round(cache_hit / input_tokens, 3) if input_tokens else 0.0,
                "cost_cny": round(cost, 6),
                "by_node": self._llm_by_node(),
            },
            "tools": dict(self.tools),
            # 固定输出这个键（哪怕为空）：runs.jsonl 是给程序读的，
            # 有的行有、有的行没有会让下游每次都得判空
            "extra": dict(self.extra),
        }

    def log_summary(self) -> None:
        """打一行人能直接读的汇总，出问题时先看这一行。"""
        data = self.summary()
        nodes = " ".join(f"{n}={v['ms'] / 1000:.1f}s" for n, v in data["nodes"].items())
        llm = data["llm"]
        extra = {"status": data["status"], "total_ms": data["total_ms"]}
        if llm["calls"]:
            extra["llm_calls"] = llm["calls"]
            extra["tokens"] = f"{llm['input_tokens']}+{llm['output_tokens']}"
            if llm["cache_hit_tokens"]:
                extra["cache"] = f"{llm['cache_hit_ratio']:.0%}"
        if llm["cost_cny"]:
            extra["cost_cny"] = llm["cost_cny"]
        if self.tools:
            extra["tools"] = sum(self.tools.values())
        if self.extra:
            for key in ("destination", "days"):
                if key in self.extra:
                    extra[key] = self.extra[key]

        logger.info("运行结束 %s", nodes or "-", extra=extra)

    def write(self) -> None:
        """追加一行到 logs/runs.jsonl。写盘失败只告警，不影响用户拿到行程。"""
        if not settings.run_log_enabled:
            return
        try:
            path = log_dir_path() / "runs.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(self.summary(), ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("运行摘要写盘失败", extra={"err": str(exc)})

    def close(self, status: str = "ok") -> None:
        """收尾三连：定状态、打汇总、落盘。

        整体包一层 try：度量是旁路，它自己出问题不能把主流程弄崩——
        这行代码跑在 finally 里，抛异常会把原始异常盖掉，SSE 直接断流。
        """
        self.finish(status)
        try:
            self.log_summary()
            self.write()
        except Exception:  # noqa: BLE001  度量失败不影响用户拿到行程
            pass
