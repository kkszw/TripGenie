# author: szw
"""PDF 文本抽取 —— 全项目唯一入口。

早期写过四个 PDF 加载实现，后来全部删除，弃用原因记在这里，
避免以后又照着写一遍：
  1. `load_and_split_pdf` 用 PyPDFLoader + 500/50 固定窗口，中文旅游攻略按 500 字切
     会把「第三天」的行程拦腰截断，检索「第三天」时命中半段；
  2. `load_pdf_advanced` 走 pypdf 的 extract_text，对多栏/表格排版会把行序打乱；
  3. `load_pdf_with_docling` 依赖 torch，装不上就整条链路起不来。

这里只做「页 → 文本」这一件事，切块交给 pdf_chunk（纯函数，可离线测）。

两条抽取通道：
  - **文字层**（pdfplumber，快，单页 30~60ms）：PDF 本身带正确文本时首选；
  - **OCR 回退**（pypdfium2 渲染 + RapidOCR，慢，单页约 2.6s）：文字层缺失（扫描件）
    或文字层是乱码时使用。

表格单独处理，不跟正文一起拉平（原因见下方「表格」一节）：文字层通道下用
pdfplumber 的线框检测把真表格转成 Markdown，从正文里剔除后作为独立内容返回；
OCR 通道拿不到线框结构，表格能力为 0。

为什么需要乱码判断：有些 PDF 的内嵌子集字体 ToUnicode 映射是错的，三种解析器
（pdfplumber / pypdf / pdfium）会返回**完全一致的错误字符**——实测一份沈阳攻略里
「的」全变成「癿」、「不」时而正确时而变成「丌」。这种文件不报错、也不缺文字，
但内容是垃圾，必须靠统计特征识别后走 OCR。判据见 is_garbled()。
"""
import re
import threading
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Union

from app.core.logging import get_logger

logger = get_logger(__name__)

# 单份 PDF 页数上限。用户的行程单通常 3~20 页，80 页已经是「一整本攻略书」的量级，
# 再大应该拆开传。上限的意义是给解析时间兜底：80 页 OCR ≈ 3.5 分钟。
MAX_PAGES = 80

# 少于这个字符数就认为没有文字层（扫描件/图片版 PDF）。
# 定 20 而不是 0：有些 PDF 只有一行水印或页码，能抽出几个字但没有任何信息量。
MIN_TEXT_CHARS = 20

# OCR 渲染倍率。2.5 ≈ 180 DPI，是这个文档集里中文小字号（8~10pt）能稳定识别的最低档；
# 降到 2.0（144 DPI）识别行数会掉一成，不值那 0.2 秒。
OCR_RENDER_SCALE = 2.5

# ---- 乱码判据 ----
# 生僻字比例：乱码文本大量使用 GB2312 之外的生僻汉字（癿、佖、夗、呾…）
GARBLED_RARE_RATIO = 0.05
# 「的」频率下限：正常中文「的」占 CJK 字符 3%~6%，乱码文本通常接近 0
GARBLED_DE_RATIO = 0.015
# 样本太小时统计不可靠，不做判断
GARBLED_MIN_CJK = 80

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

# ---- 表格 ----
# 一个区域要被当成「真表格」，必须 ≥3 行、≥2 列、列数基本稳定。
# 这两个数是按实测定的：pdfplumber 在攻略类 PDF 上报的「表格」几乎全是 1 列，
# 内容是左侧信息卡片（关键词/费用/天数/舒适程度）和标题装饰框 —— 1 列没有列关系，
# 转成 Markdown 只是把一行可读文本拆成四行竖排垃圾；2 行的小表（项目/价格）
# 信息量不足以占掉一个 top_k 名额。
TABLE_MIN_ROWS = 3
TABLE_MIN_COLS = 2

# 表名（caption）的取法：表格 bbox 上方这么高的一条窄带。30pt ≈ 一行正文，
# 攻略里的表格标题基本都紧贴在表上方，取多了会把上一段正文也带进来。
TABLE_CAPTION_MARGIN = 30


class PdfError(Exception):
    """PDF 处理失败的基类，API 层据此返回 4xx 而不是 500。"""


class PdfNoTextError(PdfError):
    """没有可提取的文字层（通常是被扫描成图片的 PDF）。"""


class PdfEncryptedError(PdfError):
    """文件被加密，需要密码才能打开。"""


class PdfTooManyPagesError(PdfError):
    """页数超过上限。"""


class PdfOcrError(PdfError):
    """文字层与 OCR 都拿不到内容。"""


# ==================== 表格 ====================
# 为什么单独抽表格：extract_text() 是按「y 从上到下、行内按 x 拼接」输出纯文本，
# 表格会被拉平成一串行，列关系在这一步就丢了 ——
#   | 景点 | 门票 | 开放时间 |
#   | 沈阳故宫 | 60元 | 8:30-17:00 |
# 拉平后是「景点 门票 开放时间」「沈阳故宫 60元 8:30-17:00」，
# 人还能读，但「沈阳故宫门票多少钱」这类 query 在向量空间里对不上，
# 而且一行超过 MAX_CHUNK_CHARS 时会被 _split_long 从表格中间硬切，
# 一块只剩景点名、一块只剩票价，单独看都没意义。
#
# 做法：先把表格单独转成 Markdown 存成独立块，再从正文里把它们剔除。
# 顺序不能反 —— 先剔除再判断的话，那些被误判成表格的信息卡片
# （实测丢过「天数 3天」「费用 500-1000」这类最有用的字段）会一起消失。


def is_real_table(rows: List[List], min_rows: int = TABLE_MIN_ROWS,
                  min_cols: int = TABLE_MIN_COLS) -> bool:
    """判断 find_tables() 的结果是不是真表格。

    pdfplumber 的线框检测会把「左侧信息卡片」「标题装饰框」也认成表格，
    这类区域全是 1 列，没有列关系。判据只有三条：

      1. 去掉空行后至少 min_rows 行；
      2. 出现最多的那个列数 ≥ min_cols；
      3. 该列数覆盖过半的行 —— 防止「左图右文」这种各行列数参差的排版
         被当成表格，否则转成 Markdown 后每行的列含义都对不上。
    """
    rows = [r for r in (rows or []) if r and any((c or "").strip() for c in r)]
    if len(rows) < min_rows:
        return False
    widths = [len(r) for r in rows]
    if max(widths) < min_cols:
        return False
    dominant_width, dominant_rows = Counter(widths).most_common(1)[0]
    return dominant_width >= min_cols and dominant_rows >= len(widths) * 0.5


def table_to_markdown(rows: List[List]) -> str:
    """二维单元格 → Markdown 表格。首行当表头，单元格内换行压成空格。

    列数不齐时按表头列数补齐/截断：pdfplumber 遇到合并单元格会返回短行，
    不补的话 Markdown 管道数不一致，模型读起来会错位。
    """
    cleaned = [[(cell or "").replace("\n", " ").strip() for cell in row]
               for row in (rows or []) if row]
    if not cleaned:
        return ""
    header = cleaned[0]
    if not any(header):
        return ""
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join([" --- "] * len(header)) + "|"]
    for row in cleaned[1:]:
        row = (row + [""] * len(header))[:len(header)]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _table_caption(page, bbox, margin: float = TABLE_CAPTION_MARGIN) -> str:
    """取表格上方窄带里的文字当表名。拿不到就返回空串。

    表名是表格块唯一的自然语言锚点：块里如果只有 `| 景点 | 门票 |`，
    「沈阳故宫门票多少」这类 query 几乎不可能召回它。
    """
    x0, top, x1, _ = bbox
    if top - margin <= 0:
        return ""
    try:
        strip = page.crop((x0, top - margin, x1, top))
        text = strip.extract_text() or ""
    except Exception:          # crop 越界、页面对象损坏等，表名可有可无
        return ""
    return " ".join(text.split())[:40]


def _drop_areas(page, bboxes: List[tuple]):
    """剔除中心点落在表格区域内的文字对象，返回剩下的页面。

    用 page.filter() 而不是链式 outside_bbox()：后者每调一次都要和上一步的
    cropped bbox 求交，多个表格时会累积坐标偏差；filter 只做一次全页扫描，
    判定用的是原始页面坐标，结果可预测。
    """
    if not bboxes:
        return page

    def keep(obj):
        if obj.get("object_type") != "char":
            return True
        cx = (obj["x0"] + obj["x1"]) / 2.0
        cy = (obj["top"] + obj["bottom"]) / 2.0
        for x0, top, x1, bottom in bboxes:
            if x0 <= cx <= x1 and top <= cy <= bottom:
                return False
        return True

    return page.filter(keep)


def extract_tables(page) -> List[Dict]:
    """抽取单页里的真表格，返回 [{markdown, caption, rows, cols, bbox}]。

    没有矢量线框的页面直接跳过：find_tables 默认只认 lines/rects 构成的线框，
    纯文字页不可能有表格，先判一下能省掉每页几十毫秒的整页扫描。
    """
    try:
        if not page.lines and not page.rects:
            return []
        found = page.find_tables()
    except Exception as exc:      # 单页表格识别失败不该让整份文档解析失败
        logger.warning("表格识别失败，该页按纯文本处理", extra={"err": str(exc)})
        return []

    tables: List[Dict] = []
    for table in found:
        try:
            rows = table.extract()
        except Exception:
            continue
        if not is_real_table(rows):
            continue
        markdown = table_to_markdown(rows)
        if not markdown:
            continue
        tables.append({
            "markdown": markdown,
            "caption": _table_caption(page, table.bbox),
            "rows": len(rows),
            "cols": max(len(r) for r in rows if r),
            "bbox": tuple(table.bbox),
        })
    return tables


def extract_pages(path: Union[str, Path],
                  max_pages: int = MAX_PAGES,
                  require_text: bool = True) -> List[Dict]:
    """把 PDF 拆成 [{page, text, tables}]，页码从 1 开始。

    `text` 是**已剔除表格区域**的正文；`tables` 是本页识别出的真表格
    （[{markdown, caption, rows, cols, bbox}]，没有就是空列表）。
    两个字段互补，合起来才是整页内容。

    Args:
        require_text: True 时，整份文档文字不足 MIN_TEXT_CHARS 就抛 PdfNoTextError。
            extract_document 会传 False——它需要先拿到「空文字层」这个事实，
            再决定是不是走 OCR。

    Raises:
        FileNotFoundError / PdfEncryptedError / PdfTooManyPagesError / PdfError
        PdfNoTextError: 仅当 require_text=True 时可能抛出
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"PDF 不存在: {path}")

    # pdfplumber 依赖很重（pdfminer.six + pillow），这里延迟导入：
    # 只要不调用上传功能，就不该为它付启动成本。
    import pdfplumber

    pages: List[Dict] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            total = len(pdf.pages)
            if total > max_pages:
                raise PdfTooManyPagesError(
                    f"PDF 共 {total} 页，超过上限 {max_pages} 页，请拆分后再上传"
                )
            for index, page in enumerate(pdf.pages, start=1):
                # extract_text 返回 None（整页纯图片）而不是空串，必须兜住
                text = page.extract_text() or ""
                tables = extract_tables(page)
                if tables:
                    # 表格区域从正文里剔掉，内容改由独立的表格块承载。
                    # 不剔的话同一份内容会出现两次，白占 top_k 名额。
                    try:
                        body = _drop_areas(page, [t["bbox"] for t in tables])
                        text = body.extract_text() or text
                    except Exception as exc:
                        logger.warning("表格区域剔除失败，保留原始正文",
                                       extra={"page": index, "err": str(exc)})
                pages.append({"page": index, "text": text, "tables": tables})
    except PdfTooManyPagesError:
        raise
    except Exception as exc:  # pdfminer 的异常类型不稳定，按消息文本分类
        message = str(exc).lower()
        if "password" in message or "encrypt" in message:
            raise PdfEncryptedError("PDF 已加密，无法解析。请先解密或另存为未加密文件") from exc
        raise PdfError(f"PDF 解析失败: {exc}") from exc

    # 表格区域已从 text 里剔除，判断「有没有文字层」时要把表格内容算回来：
    # 一份纯报价表的 PDF 正文可能不足 20 字，但内容全在表格里，
    # 只看 text 会误判成扫描件，白跑一遍 OCR（而且 OCR 拿不到表格结构）。
    text_chars = sum(len(str(p["text"]).strip()) for p in pages)
    text_chars += sum(len(t["markdown"]) for p in pages for t in p.get("tables") or [])
    if require_text and text_chars < MIN_TEXT_CHARS:
        raise PdfNoTextError(
            "这份 PDF 没有可提取的文字（可能是扫描件或图片版）"
        )

    logger.debug("PDF 文字层抽取完成", extra={"pages": len(pages), "chars": text_chars})
    return pages


@lru_cache(maxsize=8192)
def _in_gb2312(char: str) -> bool:
    """常用汉字判据：GB2312 一级+二级共 6763 字，覆盖现代中文常用字。

    乱码文本的典型特征就是大量使用 GB2312 之外的生僻字（癿、佖、夗、呾）。
    注意繁体字大多也能编进 GB2312 之外的 GBK，所以这个判据单独用会误伤繁体，
    必须和「的」字频率一起看（见 is_garbled）。
    """
    try:
        char.encode("gb2312")
        return True
    except UnicodeEncodeError:
        return False


def text_quality(pages: List[Dict]) -> Dict:
    """统计文本层的健康度，供乱码判断与日志使用。

    表格的 Markdown 也算进来：它是这份文档的真实内容，而且表格若是乱码，
    正文和表格一样乱。Markdown 的管道符是 ASCII，不进 CJK 统计，不影响判据。
    """
    blocks = []
    for page in pages:
        blocks.append(str(page.get("text") or ""))
        blocks.extend(str(t.get("markdown") or "") for t in page.get("tables") or [])
    text = "\n".join(blocks)
    cjk = _CJK_RE.findall(text)
    total = len(cjk)
    rare = sum(1 for char in cjk if not _in_gb2312(char))
    de = text.count("的")
    return {
        "chars": len(text.strip()),
        "cjk": total,
        "rare_ratio": round(rare / total, 4) if total else 0.0,
        "de_ratio": round(de / total, 4) if total else 0.0,
    }


def is_garbled(pages: List[Dict]) -> Dict:
    """判断文字层是不是乱码。

    两个信号取交集，缺一不可：
      - 生僻字比例 > 5%：乱码文本的特征；
      - 「的」频率 < 1.5%：正常中文 3%~6%，乱码里「的」几乎消失（被映射成「癿」）。
    取交集是为了放过繁体中文：繁体同样生僻字多，但「的」还是「的」，频率正常。
    实测：坏 PDF 生僻 11.8%~14.9%、「的」0%；正常攻略生僻 0%、「的」5.59%。
    """
    quality = text_quality(pages)
    if quality["cjk"] < GARBLED_MIN_CJK:
        return {**quality, "garbled": False, "reason": "样本不足，不做判断"}
    garbled = (quality["rare_ratio"] > GARBLED_RARE_RATIO
               and quality["de_ratio"] < GARBLED_DE_RATIO)
    reason = ""
    if garbled:
        reason = (f"生僻字 {quality['rare_ratio']:.1%}、「的」{quality['de_ratio']:.2%}，"
                  f"判定文字层乱码")
    return {**quality, "garbled": garbled, "reason": reason}


_ocr_engine = None
_ocr_lock = threading.Lock()


def _get_ocr_engine():
    """RapidOCR 单例。初始化要 1.6 秒（加载三个 onnx 模型），不能每页都建。"""
    global _ocr_engine
    if _ocr_engine is None:
        with _ocr_lock:
            if _ocr_engine is None:
                from rapidocr import RapidOCR
                _ocr_engine = RapidOCR()
                logger.info("OCR 引擎已初始化")
    return _ocr_engine


def extract_pages_ocr(path: Union[str, Path],
                      max_pages: int = MAX_PAGES,
                      scale: float = OCR_RENDER_SCALE) -> List[Dict]:
    """渲染每一页 → OCR → [{page, text}]。

    阅读顺序交给 ocr_layout 的 XY-cut 还原：RapidOCR 自己按全局 y 返回，
    多栏排版会被交错（实测双栏攻略两栏句子互相穿插），直接 join 出来的文本
    看着是字，读起来是乱的。
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"PDF 不存在: {path}")

    import pypdfium2 as pdfium

    from app.services.ocr_layout import boxes_from_ocr_result

    doc = pdfium.PdfDocument(str(path))
    try:
        total = len(doc)
        if total > max_pages:
            raise PdfTooManyPagesError(
                f"PDF 共 {total} 页，超过 OCR 上限 {max_pages} 页，请拆分后再上传"
            )
        engine = _get_ocr_engine()
        pages: List[Dict] = []
        for index in range(total):
            image = doc[index].render(scale=scale).to_pil()
            width, height = image.size
            try:
                result = engine(image)
                lines = boxes_from_ocr_result(result, width=width, height=height)
            except Exception as exc:  # 单页 OCR 失败不该让整份文档失败
                logger.warning("单页 OCR 失败", extra={"page": index + 1, "err": str(exc)})
                lines = []
            pages.append({"page": index + 1, "text": "\n".join(lines)})
        logger.info("OCR 完成", extra={"pages": len(pages), "scale": scale})
        return pages
    finally:
        close = getattr(doc, "close", None)
        if callable(close):
            close()


def extract_document(path: Union[str, Path],
                     max_pages: int = MAX_PAGES,
                     allow_ocr: bool = True) -> Dict:
    """抽取整份文档，自动在「文字层」和「OCR」之间选择。

    Returns:
        {
          "pages": [{"page": 1, "text": ...}],
          "mode": "text" | "ocr",
          "quality": {...},          # 最终采用那份文本的健康度
          "text_layer_quality": {...} # 文字层原始健康度（排查用）
        }
    """
    text_pages = extract_pages(path, max_pages=max_pages, require_text=False)
    text_quality_result = is_garbled(text_pages)
    need_ocr = text_quality_result["garbled"] or \
        text_quality_result["chars"] < MIN_TEXT_CHARS

    if not need_ocr or not allow_ocr:
        return {
            "pages": text_pages,
            "mode": "text",
            "quality": text_quality_result,
            "text_layer_quality": text_quality_result,
        }

    logger.info("文字层不可用，转 OCR",
                extra={"garbled": text_quality_result["garbled"],
                       "chars": text_quality_result["chars"],
                       "reason": text_quality_result["reason"]})

    try:
        ocr_pages = extract_pages_ocr(path, max_pages=max_pages)
    except Exception as exc:
        # OCR 也失败时，如果文字层至少还有点内容，宁可给用户乱码也别给空库
        logger.warning("OCR 失败", extra={"err": str(exc)})
        if text_quality_result["chars"] >= MIN_TEXT_CHARS:
            return {
                "pages": text_pages,
                "mode": "text",
                "quality": text_quality_result,
                "text_layer_quality": text_quality_result,
            }
        raise PdfOcrError(f"文字层与 OCR 都无法解析这份 PDF: {exc}") from exc

    ocr_quality = is_garbled(ocr_pages)
    if ocr_quality["cjk"] == 0:
        if text_quality_result["chars"] >= MIN_TEXT_CHARS:
            return {
                "pages": text_pages,
                "mode": "text",
                "quality": text_quality_result,
                "text_layer_quality": text_quality_result,
            }
        raise PdfOcrError("OCR 没有识别出任何文字，请确认这份 PDF 是否为空白或加密文档")

    return {
        "pages": ocr_pages,
        "mode": "ocr",
        "quality": ocr_quality,
        "text_layer_quality": text_quality_result,
    }
