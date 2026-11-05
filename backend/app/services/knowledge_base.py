# author: szw
"""用户资料入库服务。

职责边界很清晰，分三层，每层都能单独测：
  1. 校验（纯函数）：字节流 + 文件名 → 能不能收、收下来叫什么
  2. 落盘与登记：写文件、写一张 pending 记录
  3. 入库（后台）：抽取 → 分块 → 嵌入 → 写向量库，全程推进状态

第 3 步必须异步。实测那份 14 页的沈阳攻略走 OCR 要 51 秒，同步等着
HTTP 请求会先被网关掐断。所以接口立刻返回 doc_id，前端轮询状态。

**为什么把 PDF 单独存一份而不是只留向量**：向量只能检索，不能回看原文。
用户点进资料详情要看「这份文件一共几页、识别出多少字」，删库重建时也要
能重新入库；没有原文件这两件事都做不了。代价是磁盘，一份 PDF 几 MB，
比重新让用户上传便宜。
"""
import asyncio
import hashlib
import shutil
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Set

from app.config import settings
from app.core.logging import get_logger
from app.services.database import db
from app.services.pdf_chunk import chunk_pages, chunk_stats
from app.services.pdf_extract import (
    PdfEncryptedError,
    PdfError,
    PdfNoTextError,
    PdfOcrError,
    PdfTooManyPagesError,
    extract_document,
)
from app.utils.path_tool import get_absolute_path

logger = get_logger(__name__)

# 单份文件上限。中文 PDF 一页约 100KB~1MB（扫描页更大），20MB 能装下 40 页
# 以上的扫描件；再大基本是图文杂志，OCR 也要跑好几分钟，不适合放进对话框。
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# 只收 PDF。以后要支持 docx/图片，在这里加扩展名 + 对应的 extract 分支，
# 上层的校验/落盘/状态机都不用动。
ALLOWED_EXTENSIONS = {".pdf"}

# PDF 文件头。扩展名可以随便改，魔数不会骗人 —— 有人把 .docx 改名成 .pdf
# 上传时，我们要在落盘前就拒绝，而不是等解析器抛一堆看不懂的异常。
PDF_MAGIC = b"%PDF-"

# 后台任务句柄。asyncio 只持弱引用，create_task 的返回值如果没人拿着，
# 任务跑到一半可能被垃圾回收（表现为"入库卡在 parsing 再也不动"）。
_running: Set[asyncio.Task] = set()


class KnowledgeError(Exception):
    """面向用户的资料入库错误。message 会原样进接口响应，所以要写人话。"""


# ==================== 1. 校验 ====================

def sanitize_filename(name: str) -> str:
    """清洗上传文件名，只用于展示和元数据。

    落盘用的是 `{doc_id}.pdf`，永远不拼用户给的路径，所以这里不承担
    安全职责（防的是 ``../../etc/passwd`` 这类），只负责把它变成能看的字符串。
    """
    name = (name or "").strip().replace("\\", "/")
    name = name.rsplit("/", 1)[-1]          # 丢掉任何目录部分
    name = "".join(ch for ch in name if ch.isprintable())
    return name[:120] or "未命名.pdf"


def validate_upload(content: bytes, filename: str) -> str:
    """校验上传内容，返回清洗后的文件名。

    Raises:
        KnowledgeError: 空文件 / 超限 / 非 PDF / 扩展名不对
    """
    if not content:
        raise KnowledgeError("文件是空的，请重新选择")

    if len(content) > MAX_UPLOAD_BYTES:
        raise KnowledgeError(
            f"文件 {len(content) / 1024 / 1024:.1f}MB，超过 "
            f"{MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限，请压缩或拆分后再传"
        )

    safe_name = sanitize_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise KnowledgeError(
            f"只支持 PDF 文件，收到的是 {suffix or '无扩展名'} 文件"
        )

    # 魔数检查放在扩展名之后：先给最常见的错（选错文件）一句明白话，
    # 再处理「改了扩展名」这种少见情况。
    if not content.startswith(PDF_MAGIC):
        raise KnowledgeError("这个文件的内容不是 PDF（扩展名对了但文件头不对），请确认文件是否损坏")

    return safe_name


def file_hash(content: bytes) -> str:
    """内容哈希，用于同一用户重复上传时的查重。取 32 位足够，不必存全量。"""
    return hashlib.sha256(content).hexdigest()[:32]


# ==================== 2. 落盘与登记 ====================

def upload_dir(user_id: str) -> Path:
    """每个用户一个目录。user_id 来自令牌而非请求体，可以安全拼路径。"""
    return Path(get_absolute_path("data/uploads")) / user_id


def save_upload(user_id: str, filename: str, content: bytes) -> Dict:
    """校验 → 查重 → 落盘 → 登记。

    重复上传同一份文件不会报错，也不会重复建向量：直接返回之前那条记录，
    让前端看到"已经在库里了"。这比抛 409 好——用户传错的概率远低于
    忘了自己传过的概率，报错要他再想一遍发生了什么。
    """
    safe_name = validate_upload(content, filename)
    digest = file_hash(content)

    existing = db.find_knowledge_doc_by_hash(user_id, digest)
    if existing:
        logger.info("资料已存在，跳过入库", extra={"doc_id": existing["doc_id"]})
        return {**existing, "duplicated": True}

    doc_id = str(uuid.uuid4())
    directory = upload_dir(user_id)
    directory.mkdir(parents=True, exist_ok=True)

    # 用 doc_id 命名，不碰用户提供的文件名
    path = directory / f"{doc_id}.pdf"
    path.write_bytes(content)

    record = db.create_knowledge_doc(
        doc_id=doc_id,
        user_id=user_id,
        filename=safe_name,
        file_path=str(path),
        size_bytes=len(content),
        file_hash=digest,
    )
    logger.info("资料已登记", extra={"doc_id": doc_id, "bytes": len(content),
                                     # 键名不能叫 filename：那是 LogRecord 的保留字段，
                                     # 传进 extra 会抛 KeyError: Attempt to overwrite 'filename'
                                     "doc_name": safe_name})
    return {**record, "duplicated": False}


# ==================== 3. 入库（后台） ====================

def _mark(doc_id: str, **fields) -> None:
    """推进状态。失败不该影响主流程（比如记录已被并发删除）。"""
    try:
        db.update_knowledge_doc(doc_id, **fields)
    except Exception as exc:
        logger.warning("更新资料状态失败", extra={"doc_id": doc_id, "err": str(exc)})


def ingest_sync(doc_id: str) -> Dict:
    """同步入库：抽取 → 分块 → 嵌入。跑在线程池里，调用方负责包成异步。

    失败时先把状态写成 failed 再抛。放在这里而不是交给上层，是为了让
    「状态一致」不依赖调用方：脚本里直接调它重建单个文档时，失败了库里
    也该是一条 failed，而不是永远停在 parsing。

    Returns:
        {"chunks": int, "chars": int, "pages": int, "mode": "text"|"ocr",
         "stats": {...}}

    Raises:
        KnowledgeError: 各类可预期的解析失败，消息可直接给用户看
    """
    try:
        return _ingest_impl(doc_id)
    except KnowledgeError as exc:
        _mark(doc_id, status="failed", error=str(exc))
        raise
    except Exception as exc:  # 磁盘/网络/解析器的任何意外
        _mark(doc_id, status="failed", error=f"入库失败：{exc}")
        raise


def _ingest_impl(doc_id: str) -> Dict:
    record = db.get_knowledge_doc(doc_id)
    if not record:
        raise KnowledgeError("资料记录不存在")

    path = Path(record["file_path"])
    if not path.exists():
        raise KnowledgeError("原始文件丢失，请重新上传")

    _mark(doc_id, status="parsing", progress=5, error=None)

    try:
        document = extract_document(path)
    except PdfEncryptedError as exc:
        raise KnowledgeError(str(exc)) from exc
    except PdfTooManyPagesError as exc:
        raise KnowledgeError(str(exc)) from exc
    except PdfNoTextError as exc:
        raise KnowledgeError(str(exc)) from exc
    except PdfOcrError as exc:
        raise KnowledgeError(f"文字识别失败：{exc}") from exc
    except PdfError as exc:
        raise KnowledgeError(f"PDF 解析失败：{exc}") from exc

    pages = document["pages"]
    mode = document.get("mode", "text")
    _mark(doc_id, status="parsing", progress=45, pages=len(pages), extract_mode=mode)

    _mark(doc_id, status="chunking", progress=55)
    documents = chunk_pages(pages, metadata={
        "doc_id": doc_id,
        "user_id": record["user_id"],
        "filename": record["filename"],
        # scope 字段是给 prompt 用的：模型要能区分「内置攻略」和「用户自己传的」
        "scope": "user",
    })
    if not documents:
        raise KnowledgeError("这份 PDF 里没有提取到可用的文字内容")

    stats = chunk_stats(documents)
    _mark(doc_id, status="indexing", progress=62, chars=stats["chars"])

    # 分批嵌入：DashScope 单次最多 20 条，不分批会直接 400。
    # 每批都回写进度，因为 25 块要 20 秒左右，前端干等会以为卡死。
    from app.rag.vector_store import add_documents

    batch_size = max(1, settings.embedding_batch_size)
    total = len(documents)
    indexed = 0
    for start in range(0, total, batch_size):
        batch = documents[start:start + batch_size]
        try:
            add_documents(batch, scope="user")
        except Exception as exc:
            # 已经写进去的批次要清掉，否则删了记录还留着半份向量，
            # 下次同样内容重新上传会被这些孤儿块污染检索结果。
            logger.warning("批次嵌入失败，回滚已写入的块",
                           extra={"doc_id": doc_id, "indexed": indexed,
                                  "err": str(exc)})
            if indexed:
                _purge_vectors(doc_id)
            raise KnowledgeError(f"向量化失败（已写入 {indexed} 块，已回滚）：{exc}") from exc
        indexed += len(batch)
        progress = 62 + int(33 * indexed / total)
        _mark(doc_id, progress=min(progress, 96), chunks=indexed)

    _mark(doc_id, status="ready", progress=100, chunks=total,
          chars=stats["chars"], days=stats["days"], error=None)
    logger.info("资料入库完成", extra={"doc_id": doc_id, "chunks": total,
                                       "mode": mode, "days": stats["days"]})
    return {
        "chunks": total,
        "chars": stats["chars"],
        "pages": len(pages),
        "mode": mode,
        "stats": stats,
    }


def _purge_vectors(doc_id: str) -> None:
    """清掉某份资料在向量库里的所有块。失败只告警，不让删除流程卡住。"""
    try:
        from app.rag.vector_store import delete_documents

        delete_documents({"doc_id": doc_id}, scope="user")
    except Exception as exc:
        logger.warning("清理向量失败", extra={"doc_id": doc_id, "err": str(exc)})


async def ingest(doc_id: str) -> Optional[Dict]:
    """异步入库。异常一律吞掉，不向上抛——没有调用方在等结果。

    失败状态已经由 ingest_sync 写进库里，这里只负责不让异常冒到事件循环
    （未捕获的 task 异常只会打印一条 "Task exception was never retrieved"，
    用户那边什么都看不到）。
    """
    try:
        return await asyncio.to_thread(ingest_sync, doc_id)
    except KnowledgeError as exc:
        logger.warning("资料入库失败", extra={"doc_id": doc_id, "err": str(exc)})
    except Exception:
        logger.exception("资料入库异常", extra={"doc_id": doc_id})
    return None


def start_ingest(doc_id: str) -> None:
    """把入库任务丢到后台，立刻返回。

    用 create_task 而不是 BackgroundTasks：FastAPI 的 BackgroundTasks 要等
    响应发完才开始跑，且和请求生命周期绑在一起；入库要几十秒到几分钟，
    它需要一个独立于我行的任务。返回值必须存起来，否则可能被 GC。
    """
    task = asyncio.create_task(ingest(doc_id))
    _running.add(task)
    task.add_done_callback(_running.discard)


# ==================== 4. 查询与删除 ====================

def _public_view(record: Dict) -> Dict:
    """去掉不该给前端的字段。

    file_path 是服务器绝对路径，泄露了也没用但没必要；file_hash 是内部
    去重用的。保留它们只会让响应更难读。
    """
    return {
        "doc_id": record["doc_id"],
        "filename": record["filename"],
        "size_bytes": record.get("size_bytes", 0),
        "status": record.get("status"),
        "progress": record.get("progress", 0),
        "pages": record.get("pages", 0),
        "chunks": record.get("chunks", 0),
        "chars": record.get("chars", 0),
        "days": record.get("days", 0),
        "extract_mode": record.get("extract_mode"),
        "error": record.get("error"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def list_docs(user_id: str) -> List[Dict]:
    return [_public_view(record) for record in db.list_knowledge_docs(user_id)]


def get_doc(user_id: str, doc_id: str) -> Optional[Dict]:
    """取单条资料。不属于该用户时返回 None（由上层转 404，不暴露"存在但你看不到"）。"""
    record = db.get_knowledge_doc(doc_id)
    if not record or record.get("user_id") != user_id:
        return None
    return _public_view(record)


def delete_doc(user_id: str, doc_id: str) -> bool:
    """删除资料：向量 → 文件 → 记录。

    顺序是有意的：先删向量（检索立刻不再命中它），再删文件，最后删记录。
    反过来做的话，中间任何一步失败都会留下一份「检索得到但列表里没有」的
    幽灵内容，那是最难排查的状态。
    """
    record = db.get_knowledge_doc(doc_id)
    if not record or record.get("user_id") != user_id:
        return False

    _purge_vectors(doc_id)

    path = Path(record.get("file_path") or "")
    if path.exists():
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("删除原文件失败", extra={"doc_id": doc_id, "err": str(exc)})

    db.delete_knowledge_doc(doc_id)
    logger.info("资料已删除", extra={"doc_id": doc_id})
    return True


def purge_user(user_id: str) -> int:
    """删掉某个用户的全部资料（注销账号时用）。目前没接入口，留着备用。"""
    count = 0
    for record in db.list_knowledge_docs(user_id):
        if delete_doc(user_id, record["doc_id"]):
            count += 1
    directory = upload_dir(user_id)
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
    return count
