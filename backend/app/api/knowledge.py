# author: szw
"""用户资料库接口：上传、列表、详情、删除。

上传是异步的：接口只负责把文件落盘并登记一条 pending 记录，立刻返回 doc_id。
解析（可能走 OCR，几十秒）交给后台任务，前端按 doc_id 轮询 /{doc_id}。
这个设计不是过度工程 —— 实测 14 页扫描版 PDF 抽取要 51 秒，同步返回
会先撞上网关超时，用户看到的是「上传失败」但服务端其实还在跑。
"""
from typing import Dict, List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.core.logging import get_logger
from app.core.security import get_current_user
from app.models.knowledge import KnowledgeListResponse, KnowledgeDoc, UploadResponse
from app.services import knowledge_base as kb

logger = get_logger(__name__)

router = APIRouter()


@router.post("/upload", response_model=UploadResponse, tags=["资料库"])
async def upload_pdf(
    file: UploadFile = File(..., description="PDF 格式的旅行资料"),
    current_user: Dict = Depends(get_current_user),
):
    """上传一份 PDF 资料，后台自动解析、分块、入库。

    返回时 status 通常还是 pending；前端应轮询 GET /api/knowledge/{doc_id}。
    重复上传同一份文件会直接返回已有记录（duplicated=true），不重复入库。
    """
    # 先读字节再校验。UploadFile 是流式的，但 20MB 的上限保证了内存可控：
    # 超过上限的文件在 read() 之后立刻被 validate_upload 挡掉，不会进解析器。
    content = await file.read()

    try:
        record = kb.save_upload(current_user["id"], file.filename or "未命名.pdf", content)
    except kb.KnowledgeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    duplicated = record.pop("duplicated", False)
    if not duplicated:
        kb.start_ingest(record["doc_id"])

    payload = kb.get_doc(current_user["id"], record["doc_id"]) or kb._public_view(record)
    return UploadResponse(**payload, duplicated=duplicated)


@router.get("/", response_model=KnowledgeListResponse, tags=["资料库"])
async def list_docs(current_user: Dict = Depends(get_current_user)):
    """列出当前用户上传的全部资料（最新在前）。"""
    docs = kb.list_docs(current_user["id"])
    return KnowledgeListResponse(
        docs=[KnowledgeDoc(**doc) for doc in docs],
        total_chunks=sum(doc.get("chunks") or 0 for doc in docs),
        ready_count=sum(1 for doc in docs if doc.get("status") == "ready"),
    )


@router.get("/{doc_id}", response_model=KnowledgeDoc, tags=["资料库"])
async def get_doc(doc_id: str, current_user: Dict = Depends(get_current_user)):
    """查单条资料的入库进度。前端轮询用这个。"""
    doc = kb.get_doc(current_user["id"], doc_id)
    if not doc:
        # 与 history 的会话接口保持一致：别人的资料和不存在都返回 404，
        # 403 会告诉攻击者"这个 doc_id 确实存在"。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资料不存在")
    return KnowledgeDoc(**doc)


@router.delete("/{doc_id}", tags=["资料库"])
async def delete_doc(doc_id: str, current_user: Dict = Depends(get_current_user)):
    """删除资料：同时清掉原文件和它在向量库里的所有块。"""
    if not kb.delete_doc(current_user["id"], doc_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资料不存在")
    return {"success": True, "doc_id": doc_id}
