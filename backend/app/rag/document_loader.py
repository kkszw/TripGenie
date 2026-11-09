# author: szw
# app/rag/document_loader.py
"""Markdown 语料加载器（旧链路，只服务于 `app.rag.init_rag_database`）。

内置攻略已改走 PDF 链路（`services/pdf_extract.py` + `services/pdf_chunk.py`），
原来那四个 PDF 加载函数已删除 —— 它们按 500 字固定窗口切、或依赖 docling/torch，
正是 pdf_extract 文档里写明弃用的原因。这里只保留按标题分块的 Markdown 加载。
"""
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter
from pathlib import Path
from typing import List
from langchain_core.documents import Document

from app.core.logging import get_logger

logger = get_logger(__name__)


class MarkdownLoader:
    """
    Markdown 文档加载器 - 按结构分块
    """

    def __init__(self):
        # 按标题层级分块
        self.headers_to_split_on = [
            ("#", "h1"),  # 一级标题
            ("##", "h2"),  # 二级标题
            ("###", "h3"),  # 三级标题
        ]

        # 后续精细分块（如果块太大）
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]
        )

    def load_markdown(self, file_path: str) -> List[Document]:
        """
        加载 Markdown 文件并分块
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 1. 按标题分块
        header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=self.headers_to_split_on
        )
        chunks = header_splitter.split_text(content)

        # 2. 精细化分块（过大的块再拆分）
        documents = []
        for chunk in chunks:
            # 如果块太大，进一步拆分
            if len(chunk.page_content) > 1500:
                sub_chunks = self.text_splitter.split_text(chunk.page_content)
                for i, sub in enumerate(sub_chunks):
                    docs = self._create_documents(
                        sub,
                        {"source": file_path.name, **chunk.metadata},
                        i,
                        len(sub_chunks),
                    )
                    documents.extend(docs)
            else:
                documents.append(Document(
                    page_content=chunk.page_content,
                    metadata={
                        "source": file_path.name,
                        **chunk.metadata
                    }
                ))

        return documents

    def _create_documents(self, text: str, metadata: dict, index: int, total: int) -> List[Document]:
        """创建文档对象"""
        return [Document(
            page_content=text,
            metadata={
                **metadata,
                "source": metadata.get("source", "unknown"),
                "chunk_index": index,
                "total_chunks": total
            }
        )]

    def load_multiple_markdown(self, folder_path: str) -> List[Document]:
        """加载文件夹下所有 Markdown 文件"""
        folder = Path(folder_path)
        all_docs = []

        for file_path in folder.glob("*.md"):
            try:
                docs = self.load_markdown(str(file_path))
                all_docs.extend(docs)
                logger.debug("文档已加载", extra={"file": file_path.name, "chunks": len(docs)})
            except Exception as e:
                logger.warning("文档加载失败", extra={"file": file_path.name, "err": str(e)})

        return all_docs
