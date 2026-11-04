# author: szw
"""结构化日志与请求上下文。

要解决的三件事：

1. 同一条日志既能给人看（控制台带颜色和时间），也能给机器查（文件是 JSON Lines）。
   排查"昨天那次为什么失败"时，能直接 grep trace_id 把一次运行的全部行捞出来。
2. trace_id / thread_id / user_id 放在 contextvars 里，由 Formatter 自动写进每一行，
   业务代码不用到处传参。用 contextvars 而不是模块级全局变量，是因为 SSE 是异步的，
   一次请求横跨多个 await，全局变量在并发下必然串号。
3. 第三方库的噪音统一压到 WARNING。httpx 每个请求打一行、chromadb 启动打一大段，
   这些和"这次规划发生了什么"无关。

为什么不用 loguru / structlog：一个 Formatter 子类就能满足上面全部，
为一个输出格式多装一个依赖不划算，而且这两个库对 contextvars 的支持并不比自己写更好。
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# 三者都由 Formatter 自动注入日志行，业务代码只管 set 一次
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")
thread_id_var: ContextVar[str] = ContextVar("thread_id", default="-")
user_id_var: ContextVar[str] = ContextVar("user_id", default="-")

_configured = False

# LogRecord 自带的属性，序列化时跳过；剩下的都当作业务传进来的 extra 字段
#
# 注意：extra 的**键**不能和这些重名。重名会在 makeRecord 里直接抛
#   KeyError: "Attempt to overwrite 'name' in LogRecord"
# 而且是运行期才炸——写 logger.debug 时级别过滤掉不会触发，一开 DEBUG 全炸。
# 本项目踩过两个：name（景点名）、args（工具参数），已分别换成 poi / arguments。
_STD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
    "trace_id", "thread_id", "user_id",
}

# 单个字段超过这个长度就截断。日志里塞一整份 prompt 或行程 JSON，
# 会让文件按天涨到几十兆，排查时反而看不清。
_MAX_FIELD_LEN = 800


def new_trace_id() -> str:
    """8 位十六进制：短到能口头念给用户，也够一次进程内不重复。"""
    return uuid.uuid4().hex[:8]


def bind_context(*, trace_id: Optional[str] = None, thread_id: Optional[str] = None,
                 user_id: Any = None) -> None:
    if trace_id:
        trace_id_var.set(trace_id)
    if thread_id:
        thread_id_var.set(thread_id)
    if user_id is not None:
        user_id_var.set(str(user_id))


def clear_context() -> None:
    trace_id_var.set("-")
    thread_id_var.set("-")
    user_id_var.set("-")


def current_trace_id() -> str:
    return trace_id_var.get()


def log_dir_path() -> Path:
    """日志目录：优先 .env 的 LOG_DIR，否则 backend/logs。"""
    from app.config import settings

    if settings.log_dir:
        return Path(settings.log_dir)
    # 本文件在 backend/app/core/ 下，向上三级就是 backend/
    return Path(__file__).resolve().parents[2] / "logs"


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_FIELD_LEN:
        return value[:_MAX_FIELD_LEN] + f"…(+{len(value) - _MAX_FIELD_LEN})"
    if isinstance(value, (dict, list)):
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)[:_MAX_FIELD_LEN]
        if len(text) > _MAX_FIELD_LEN:
            return text[:_MAX_FIELD_LEN] + f"…(+{len(text) - _MAX_FIELD_LEN})"
        return value
    return value


class _ContextFilter(logging.Filter):
    """把 contextvars 里的值挂到每条 record 上，两种 Formatter 共用。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_id_var.get()
        record.thread_id = thread_id_var.get()
        record.user_id = user_id_var.get()
        return True


class HumanFormatter(logging.Formatter):
    """控制台格式：

        18:23:45 INFO  [a3f9c1e2] nodes.plan 生成行程完成 days=3 dur_ms=10030
    """

    _COLORS = {
        "DEBUG": "\033[38;5;244m",
        "INFO": "\033[38;5;39m",
        "WARNING": "\033[38;5;214m",
        "ERROR": "\033[38;5;203m",
        "CRITICAL": "\033[1;38;5;203m",
    }
    # 固定 5 位宽度。直接用 levelname 会在 7 个字符的 WARNING 上被截成 "WARNI"
    _SHORT = {"DEBUG": "DBG", "INFO": "INFO", "WARNING": "WARN",
              "ERROR": "ERR", "CRITICAL": "CRIT"}
    _RESET = "\033[0m"
    _DIM = "\033[38;5;244m"

    def __init__(self, color: bool = True):
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        level = self._SHORT.get(record.levelname, record.levelname[:4]).ljust(5)
        name = record.name.replace("app.", "", 1)
        trace = record.trace_id

        if self.color:
            color = self._COLORS.get(record.levelname, "")
            head = (f"{self._DIM}{ts}{self._RESET} {color}{level}{self._RESET} "
                    f"{self._DIM}[{trace}]{self._RESET} {name}")
        else:
            head = f"{ts} {level} [{trace}] {name}"

        msg = record.getMessage()
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in _STD_ATTRS and not k.startswith("_")
        }
        if extras:
            msg += " " + " ".join(f"{k}={_truncate(v)}" for k, v in extras.items())

        line = f"{head} {msg}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class JsonFormatter(logging.Formatter):
    """文件格式：一行一个 JSON，方便 grep / jq / 后续接日志系统。"""

    def format(self, record: logging.LogRecord) -> str:
        data: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created).astimezone().isoformat(
                timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name.replace("app.", "", 1),
            "msg": record.getMessage(),
            "trace": record.trace_id,
            "thread": record.thread_id,
            "user": record.user_id,
        }
        for key, value in record.__dict__.items():
            if key in _STD_ATTRS or key.startswith("_"):
                continue
            data[key] = _truncate(value)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)


def setup_logging(level: Optional[str] = None, log_dir: Optional[str] = None,
                  to_file: bool = True) -> None:
    """初始化日志，幂等。

    控制台和文件用不同的 Formatter，但共用同一个 Filter 和 level ——
    控制台方便人读，文件方便机器查，内容一致只是形态不同。
    """
    global _configured
    if _configured:
        return
    _configured = True

    # 延迟导入：config 在很下层，导入期就依赖它容易成环
    from app.config import settings

    level = (level or settings.log_level or "INFO").upper()
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    ctx_filter = _ContextFilter()
    color = sys.stdout.isatty() or os.getenv("FORCE_COLOR") == "1"

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(HumanFormatter(color=color))
    console.addFilter(ctx_filter)
    root.addHandler(console)

    if to_file:
        try:
            directory = Path(log_dir) if log_dir else log_dir_path()
            directory.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.TimedRotatingFileHandler(
                directory / "app.log", when="midnight", backupCount=7, encoding="utf-8"
            )
            file_handler.setFormatter(JsonFormatter())
            file_handler.addFilter(ctx_filter)
            root.addHandler(file_handler)
        except OSError as exc:
            # 只读文件系统 / 磁盘满：降级成只打控制台，不能让日志拖垮启动
            root.warning("日志文件不可用，仅输出到控制台", extra={"err": str(exc)})

    # 第三方库降噪：这些库的 INFO 全是"我发了个请求"，对本项目没有信息量
    for noisy in ("httpx", "httpcore", "openai", "chromadb", "urllib3",
                  "asyncio", "multipart", "python_multipart"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))


def adopt_uvicorn_loggers() -> None:
    """把 uvicorn 自己的 handler 摘掉，让它的日志流进我们的 root。

    uvicorn 在启动时用 dictConfig 给 "uvicorn" 系列 logger 单独挂了 handler，
    不清掉的话它的启动/报错日志会是另一种格式，而且不带 trace_id。
    在 startup 事件里调用（那时 uvicorn 已经配好了）。
    """
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        for handler in list(lg.handlers):
            lg.removeHandler(handler)
        lg.propagate = True


def get_logger(name: str) -> logging.Logger:
    """拿 logger。首次调用顺手完成初始化，脚本里 import 完就能直接用。"""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
