# author: szw
"""PDF 文本抽取 —— 全项目唯一入口。

`document_loader` 里原先那四个 PDF 加载实现已经删除，弃用原因记在这里，
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

为什么需要乱码判断：有些 PDF 的内嵌子集字体 ToUnicode 映射是错的，三种解析器
（pdfplumber / pypdf / pdfium）会返回**完全一致的错误字符**——实测一份沈阳攻略里
「的」全变成「癿」、「不」时而正确时而变成「丌」。这种文件不报错、也不缺文字，
但内容是垃圾，必须靠统计特征识别后走 OCR。判据见 is_garbled()。
"""
import re
import threading
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


def extract_pages(path: Union[str, Path],
                  max_pages: int = MAX_PAGES,
                  require_text: bool = True) -> List[Dict]:
    """把 PDF 拆成 [{page, text}]，页码从 1 开始。

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
                pages.append({"page": index, "text": page.extract_text() or ""})
    except PdfTooManyPagesError:
        raise
    except Exception as exc:  # pdfminer 的异常类型不稳定，按消息文本分类
        message = str(exc).lower()
        if "password" in message or "encrypt" in message:
            raise PdfEncryptedError("PDF 已加密，无法解析。请先解密或另存为未加密文件") from exc
        raise PdfError(f"PDF 解析失败: {exc}") from exc

    text_chars = sum(len(str(p["text"]).strip()) for p in pages)
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
    """统计文本层的健康度，供乱码判断与日志使用。"""
    text = "\n".join(str(p.get("text") or "") for p in pages)
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
