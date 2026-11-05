# author: szw
"""结构感知分块：把 PDF 的页面文本切成可检索的块。纯函数，不碰 IO，可离线单测。

核心判断：**行程类文档的天然语义边界是「天」，不是字符数。**
固定窗口（比如 500 字）会把「第三天」的行程拦腰截断，用户问「第三天怎么安排」时
只能命中半段，另一半跑到相邻块里。所以这里先按 `第一天/Day 2/10月3日` 这类标记
切段，只对超长段做二次切分，并且切分点优先落在句末而不是字中间。

整篇找不到任何「天」标记时（比如一篇泛泛的城市介绍），降级为按标题/段落切，
这就退化成常规的递归切分，不会比原来更差。
"""
import re
from collections import Counter
from math import ceil
from typing import Dict, List, Optional, Sequence, Tuple

from langchain_core.documents import Document

# 单块字符上限。中文行程单里一天的安排通常 300~800 字，1200 能装下绝大多数完整一天；
# 再大就会挤掉 top_k 里的其他块（5 个块 × 1200 字 ≈ 4000 字上下文，已经不小）
MAX_CHUNK_CHARS = 1200

# 小于这个长度的段不单独成块，合并到相邻段，避免出现「第 3 天」这种只有标题没有内容的碎块
MIN_CHUNK_CHARS = 80

# 二次切分时的重叠字符数。取 120 ≈ 一两句话，够衔接上下文又不至于让重复率过高
OVERLAP_CHARS = 120

# 页眉页脚判定：只对短行做重复统计，正文长句不参与（长句天然不会重复）
EDGE_LINE_MAX_CHARS = 40
HEADER_PAGE_RATIO = 0.6

_SENT_END = "。！？；：!?;:」』”\"')）】…"

# 带装饰的页码行：「- 3 -」「第 3 页」「Page 3 of 10」「3/10」
# 故意不收「裸数字」：「1500」这种单独成行的预算数字很常见，按页码删掉就会丢数据。
# 真正的裸页码交给位置判断（见 normalize_lines 里的 _BARE_NUM_RE）。
_PAGE_NUM_RE = re.compile(
    r"^\s*(?:[-—–_]\s*\d{1,4}\s*[-—–_]"
    r"|第\s*\d{1,4}\s*页(?:\s*[/／]\s*共?\s*\d+\s*页?)?"
    r"|page\s*\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?|\d{1,4}\s*/\s*\d{1,4})\s*$",
    re.IGNORECASE,
)

# 裸数字行：只有出现在一页的最后一行时才算页码
_BARE_NUM_RE = re.compile(r"^\d{1,4}$")

# 列表项：- • 1. 1) (1) （1）
_LIST_RE = re.compile(r"^\s*(?:[-•·▪◦*]|\(\d{1,2}\)|（\d{1,2}）|\d{1,2}[.)、]\s?)")

# 标题：「一、」「1.」「## 」
_HEADING_RE = re.compile(r"^\s*(?:#{1,4}\s+|[一二三四五六七八九十]{1,3}[、.]\s*|\d{1,2}[.、]\s*\S)")

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

# 天的标记。顺序有意义：先匹配「第N天」，再匹配英文，最后才是日期
_DAY_PATTERNS: Sequence[Tuple[str, re.Pattern]] = (
    ("cn", re.compile(r"^\s*[【\[（(]?\s*第\s*([零〇一二两三四五六七八九十百\d]{1,4})\s*天")),
    ("en", re.compile(r"^\s*[【\[（(]?\s*(?:day|Day|DAY)\s*[-－:]?\s*(\d{1,2})\b")),
    ("en", re.compile(r"^\s*[【\[（(]?\s*[Dd]\s*[-－]?\s*(\d{1,2})\s*[】\]）)、.．:：]")),
    ("date", re.compile(r"^\s*[【\[（(]?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")),
    ("date", re.compile(r"^\s*[【\[（(]?\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")),
)


def cn_to_int(text: str) -> Optional[int]:
    """把「三」「十二」「二十」「3」统一成整数。不认识的返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        if left and left not in _CN_DIGITS:
            return None
        if right and right not in _CN_DIGITS:
            return None
        return tens * 10 + ones
    if len(text) == 1 and text in _CN_DIGITS:
        return _CN_DIGITS[text]
    # 「一百二十」这类不常见但存在
    if text.startswith("百"):
        return None
    return None


# 行程标题行：「D1 亚龙湾-蝴蝶谷」「D2 拉市海-束河古镇」。
# 这批「我是驴友」攻略统一用这个格式（实测 12/14 份都有），而且标题和当天
# 简介写在同一行，长度经常超过普通标题的 60 字上限 —— 所以单独处理，
# 不受下面那个长度限制。
_DAY_LINE_RE = re.compile(
    r"^[【\[（(]?\s*[Dd]\s*[-－]?\s*(\d{1,2})(?:\s|$|[】\]）)、.．:：])")


def detect_day_label(line: str) -> Optional[Dict]:
    """判断某一行是不是「一天」的起始标记。

    Returns:
        {"day": int|None, "label": str}；不是天标记则 None。
        `day` 为 None 表示只认出日期形式的边界（10月3日），推不出第几天。
    """
    if not line:
        return None
    stripped = line.strip()

    # 「D1 亚龙湾-蝴蝶谷…」先判，且不看行长度：它后面常直接接当天简介
    match = _DAY_LINE_RE.match(stripped)
    if match:
        number = cn_to_int(match.group(1))
        if number is not None and 1 <= number <= 60:
            return {"day": number, "label": stripped[:20]}

    if len(stripped) > 60:  # 天标记总是在行首且短，长句不可能是标题
        return None
    for kind, pattern in _DAY_PATTERNS:
        match = pattern.match(stripped)
        if not match:
            continue
        if kind == "date":
            # 日期型标记只认短行。「3月23日至11月15日，11月16日至3月23日4:00-6:00
            # 开放时间」这种行是以日期开头的正文，不是分段标题，一刀切会把
            # 一个完整的段落劈成两半（实测那份沈阳攻略的北陵公园一节就是这样）。
            if len(stripped) > 24:
                return None
            return {"day": None, "label": stripped[:20]}
        number = cn_to_int(match.group(1))
        if number is None or not (1 <= number <= 60):
            return None
        return {"day": number, "label": stripped[:20]}
    return None


def _join_soft_wraps(prev: str, cur: str) -> bool:
    """中文 PDF 常有硬换行：一句话被版面宽度切断。判断这两行是否应该接回去。"""
    if not prev or not cur:
        return False
    if detect_day_label(cur):          # 天标题必须另起
        return False
    if _LIST_RE.match(cur):            # 列表项各占一行，不要粘在一起
        return False
    if _HEADING_RE.match(cur):         # 小标题同理
        return False
    if prev[-1] in _SENT_END:          # 上一行已经是完整句
        return False
    if len(prev) <= 14 and prev[-1] not in _SENT_END:  # 短行更像标题，保留换行
        return False
    return True


def _merge(prev: str, cur: str) -> str:
    """拼接两行：中文直接连，英文/数字之间补一个空格，避免 "Day2" 粘成 "Day2rent"。"""
    if not prev:
        return cur
    if not cur:
        return prev
    left, right = prev[-1], cur[0]
    if left.isascii() and right.isascii() and left.isalnum() and right.isalnum():
        return f"{prev} {cur}"
    return prev + cur


def _repeated_edge_lines(pages: List[List[str]], ratio: float, edge: int) -> set:
    """统计在各页首尾重复出现的短行，作为页眉页脚候选。

    只对「≥2 页」的文档做，单页文档首尾就是正文，误删代价太大。
    """
    if len(pages) < 2:
        return set()
    counter: Counter = Counter()
    for lines in pages:
        if not lines:
            continue
        # 页面本身很短时不取首尾（会重复计数同一行）
        picked = lines if len(lines) <= edge * 2 else lines[:edge] + lines[-edge:]
        for line in picked:
            if 0 < len(line) <= EDGE_LINE_MAX_CHARS:
                counter[line] += 1
    threshold = max(2, ceil(len(pages) * ratio))
    return {line for line, count in counter.items() if count >= threshold}


def normalize_lines(pages: Sequence[Dict]) -> List[Tuple[int, str]]:
    """页面文本 → 干净的 (页码, 行) 序列。

    做三件事：丢页眉页脚、丢纯页码行、把被版面宽度切断的句子接回去。
    页码信息保留，因为块的 metadata 要能指回原文第几页（溯源是 RAG 的底线）。
    """
    page_lines: List[List[str]] = []
    for page in pages:
        raw = str(page.get("text") or "")
        lines = [line.strip() for line in raw.splitlines()]
        page_lines.append([line for line in lines if line])

    repeated = _repeated_edge_lines(page_lines, HEADER_PAGE_RATIO, edge=2)

    flat: List[Tuple[int, str]] = []
    for page, lines in zip(pages, page_lines):
        page_no = int(page.get("page") or 0)
        last_index = len(lines) - 1
        for position, line in enumerate(lines):
            if line in repeated:
                continue
            if _PAGE_NUM_RE.match(line):
                continue
            # 裸页码只认「整页最后一行」，且要求这页不止两行（否则正文首尾可能被误删）
            if position == last_index and len(lines) >= 3 and _BARE_NUM_RE.match(line):
                continue
            if flat and flat[-1][0] == page_no and _join_soft_wraps(flat[-1][1], line):
                flat[-1] = (page_no, _merge(flat[-1][1], line))
            else:
                flat.append((page_no, line))
    return flat


def _new_section(first_page: int) -> Dict:
    return {"lines": [], "page_start": first_page, "page_end": first_page,
            "day": None, "day_label": None, "chars": 0}


def _split_sections(flat: List[Tuple[int, str]], has_day: bool,
                    min_chars: int, max_chars: int) -> List[Dict]:
    """按「天」切段；没有天标记时按页 + 标题切段。"""
    sections: List[Dict] = []
    current = _new_section(flat[0][0] if flat else 0)

    for page_no, line in flat:
        day = detect_day_label(line)
        is_day_boundary = bool(day)
        is_heading_boundary = (not has_day) and bool(_HEADING_RE.match(line)) \
            and current["chars"] >= min_chars
        # 按页切段的两个条件：
        #   1. 当前段没有「天」标记 —— 匿名段跨页没有任何好处，只会让溯源信息失真；
        #   2. 或者这段已经超长 —— 有「天」标记的段允许跨页（一天的内容经常跨两页），
        #      但长到该切的时候还是要切。
        # 最初写成「只要没有天结构就按页切」，结果这篇文档里「3月23日至11月15日
        # 开放时间」被误认成天标记，has_day 变 True，按页切整个失效，前 7 页
        # 连成一个巨段、只能按字数硬切，page_start/page_end 全成了 1-7。
        is_page_boundary = bool(current["lines"]) and page_no != current["page_end"] \
            and (current["day"] is None or current["chars"] >= max_chars)

        if (is_day_boundary or is_heading_boundary or is_page_boundary) and current["lines"]:
            sections.append(current)
            current = _new_section(page_no)

        if day and current["day_label"] is None:
            current["day"] = day["day"]
            current["day_label"] = day["label"]

        current["lines"].append(line)
        current["chars"] += len(line) + 1
        current["page_end"] = page_no

    if current["lines"]:
        sections.append(current)
    return sections


def _absorb_short(sections: List[Dict], min_chars: int) -> List[Dict]:
    """把过短的段并进邻居。

    典型情况：某一天只写了「第三天：自由活动」一行。单独成块既检索不到内容，
    又占掉 top_k 的名额，所以并到相邻段里，但保留 day_label 让溯源仍指向那一天。
    """
    merged: List[Dict] = []
    for section in sections:
        if merged and section["chars"] < min_chars and section["day"] is None:
            previous = merged[-1]
            previous["lines"].extend(section["lines"])
            previous["chars"] += section["chars"]
            previous["page_end"] = section["page_end"]
            continue
        merged.append(section)
    # 开头那个碎段没有「前一个」，交给后一个
    while len(merged) > 1 and merged[0]["chars"] < min_chars and merged[0]["day"] is None:
        head = merged.pop(0)
        merged[0]["lines"] = head["lines"] + merged[0]["lines"]
        merged[0]["chars"] += head["chars"]
        merged[0]["page_start"] = head["page_start"]
    return merged


def _split_sentences(text: str) -> List[str]:
    pieces = re.split(r"(?<=[。！？；!?;])|\n+", text)
    return [p for p in pieces if p and p.strip()]


def _tail(text: str, overlap: int) -> str:
    """取上一块的尾部作为下一块的开头，尽量从句子边界开始。"""
    if overlap <= 0 or not text:
        return ""
    if len(text) <= overlap:
        return text
    tail = text[-overlap:]
    for index, char in enumerate(tail):
        if char in "。！？；\n":
            return tail[index + 1:]
    return tail


def _split_long(text: str, max_chars: int, overlap: int) -> List[str]:
    """超长段按句子打包，块间保留 overlap 字的重叠。"""
    sentences = _split_sentences(text)
    if not sentences:
        return [text] if text.strip() else []

    pieces: List[str] = []
    buffer = ""
    for sentence in sentences:
        # 单句就超上限（无标点的长串）：硬切，否则永远装不下
        while len(sentence) > max_chars:
            if buffer:
                pieces.append(buffer)
                buffer = ""
            pieces.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if buffer and len(buffer) + len(sentence) > max_chars:
            pieces.append(buffer)
            buffer = _tail(buffer, overlap) + sentence
        else:
            buffer += sentence
    if buffer.strip():
        pieces.append(buffer)

    # 硬切可能切出比上限还长的块（overlap + 长句），再兜一刀
    result: List[str] = []
    for piece in pieces:
        while len(piece) > max_chars:
            result.append(piece[:max_chars])
            piece = piece[max_chars:]
        if piece.strip():
            result.append(piece)
    return result


def chunk_pages(pages: Sequence[Dict],
                metadata: Optional[Dict] = None,
                max_chars: int = MAX_CHUNK_CHARS,
                min_chars: int = MIN_CHUNK_CHARS,
                overlap: int = OVERLAP_CHARS) -> List[Document]:
    """页面文本 → 检索块。

    Args:
        pages: [{"page": 1, "text": "..."}]，来自 pdf_extract.extract_pages
        metadata: 附加到每个块的元数据（doc_id / user_id / filename 等）

    Returns:
        List[Document]，每个块的 metadata 含 page_start / page_end / day / day_label /
        chunk_index / char_count。
    """
    base = dict(metadata or {})
    flat = normalize_lines(pages)
    if not flat:
        return []

    # 只有能推出「第几天」的标记才算文档有天数结构；纯日期（3月23日）不算，
    # 否则「开放时间：3月23日至11月15日」这种行会把整篇误判成按天组织的文档。
    has_day = any((detect_day_label(line) or {}).get("day") for _, line in flat)
    sections = _absorb_short(
        _split_sections(flat, has_day, min_chars, max_chars), min_chars)

    documents: List[Document] = []
    index = 0
    for section in sections:
        text = "\n".join(section["lines"]).strip()
        if not text:
            continue
        for piece in _split_long(text, max_chars, overlap):
            piece = piece.strip()
            if not piece:
                continue
            documents.append(Document(
                page_content=piece,
                metadata={
                    **base,
                    "page_start": section["page_start"],
                    "page_end": section["page_end"],
                    "day": section["day"],
                    "day_label": section["day_label"],
                    "chunk_index": index,
                    "char_count": len(piece),
                },
            ))
            index += 1
    return documents


def chunk_stats(documents: Sequence[Document]) -> Dict:
    """给 API 返回和自检脚本用的统计。"""
    if not documents:
        return {"chunks": 0, "chars": 0, "avg_chars": 0, "min_chars": 0,
                "max_chars": 0, "days": 0, "over_limit": 0}
    sizes = [len(doc.page_content) for doc in documents]
    days = {doc.metadata.get("day") for doc in documents if doc.metadata.get("day")}
    return {
        "chunks": len(documents),
        "chars": sum(sizes),
        "avg_chars": round(sum(sizes) / len(sizes), 1),
        "min_chars": min(sizes),
        "max_chars": max(sizes),
        "days": len(days),
        "over_limit": sum(1 for size in sizes if size > MAX_CHUNK_CHARS),
    }
