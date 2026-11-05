# author: szw
from typing import List, Optional

from pydantic import BaseModel, Field


class KnowledgeDoc(BaseModel):
    """一份已上传资料的状态。

    状态机：pending → parsing → chunking → indexing → ready / failed。
    前端拿 status 决定显示转圈、进度条还是错误。
    """
    doc_id: str
    filename: str
    size_bytes: int = 0
    status: str
    progress: int = 0
    pages: int = 0
    chunks: int = 0
    chars: int = 0
    days: int = 0
    extract_mode: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class UploadResponse(KnowledgeDoc):
    # 同一份文件之前已经传过时为 True，此时 status 已经是 ready，
    # 前端不该再轮询进度。
    duplicated: bool = False


class KnowledgeListResponse(BaseModel):
    docs: List[KnowledgeDoc] = Field(default_factory=list)
    # 汇总给前端做"库里有多少东西"的展示，省得前端自己 reduce
    total_chunks: int = 0
    ready_count: int = 0
