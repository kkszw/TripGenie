# author: szw
"""语料文件名 → 目的地 / 标识。

这些规则放在 service 层而不是留在脚本里，是因为它们要能被单测直接调用。
建库脚本、检索侧的目的地匹配、将来可能的后台重导入，用的是同一套判断，
散在脚本里迟早会漂移。

这批语料的命名很规整，全是 `我是驴友-<目的地>[天数]旅游攻略.pdf`，
所以用正则就够，不需要模型。真正麻烦的是两个重名：
「厦门八日旅游攻略」和「厦门旅游攻略」，「香格里拉」和「香格里拉9日」。
"""
import re
from pathlib import Path

# 文件名里要去掉的噪声。顺序有讲究：先去掉「旅游攻略」这种整词，再去掉
# 尾部天数，否则「厦门八日旅游攻略」会先被砍成「厦门八日旅游」。
_PREFIX_RE = re.compile(r"^我是驴友\s*[-—–_]\s*")
_SUFFIX_RE = re.compile(r"(旅游攻略|自由行攻略|自助游攻略|深度游攻略|攻略|游记|手册)$")
_DAYS_RE = re.compile(
    r"(\d{1,2}\s*[日天]|[一二三四五六七八九十]{1,2}\s*[日天]|"
    r"\d{1,2}\s*日游|[一二三四五六七八九十]{1,2}日游)$"
)
# 文件名里不能出现的字符（按 Windows 的规则，比 POSIX 严格）
_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\s]+')

# 单份资料的标识长度上限。太长的文件名在 Windows 上会撞 260 字符路径限制。
MAX_SLUG_LEN = 40


def parse_destination(filename: str) -> str:
    """从文件名提取目的地。

    「我是驴友-厦门八日旅游攻略.pdf」→「厦门」

    提取不出来的（比如纯数字文件名）返回去掉扩展名的原文，让人一眼能看出
    是哪份，而不是变成空串——空串会让检索过滤静默失效。
    """
    stem = Path(filename).stem
    if _PREFIX_RE.match(stem):
        stem = _PREFIX_RE.sub("", stem)
    for _ in range(3):  # 「八日旅游攻略」要连剥两次：天数 → 攻略 → 天数
        before = stem
        stem = _SUFFIX_RE.sub("", stem).strip()
        stem = _DAYS_RE.sub("", stem).strip()
        if stem == before:
            break
    return stem or Path(filename).stem


def corpus_stem(filename: str) -> str:
    """文件名 → 语料标识。比 destination 多保留区分度。

    目的地会重名：「厦门八日旅游攻略」和「厦门旅游攻略」都是厦门。
    如果只用 destination 当文件名，后抽的会覆盖先抽的；用 destination
    当幂等删除的键，两份会互相删块，最后只剩一份。

    所以 destination 归 destination（用于检索过滤，重名是对的：问厦门
    确实应该同时召回两份），落盘和 doc_id 用这个更细的标识。
    """
    stem = Path(filename).stem
    if _PREFIX_RE.match(stem):
        stem = _PREFIX_RE.sub("", stem)
    stem = _SUFFIX_RE.sub("", stem).strip()
    return stem or Path(filename).stem


def slugify(name: str) -> str:
    """名字 → 文件名安全的 slug。

    保留中文（可读，出问题时一眼知道是哪份），只替换路径分隔符和空白。
    """
    slug = _UNSAFE_RE.sub("_", name).strip("_")
    return slug[:MAX_SLUG_LEN] or "unnamed"
