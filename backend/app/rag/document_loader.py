# author: szw
# app/rag/document_loader.py
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter
from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter
from pypdf import PdfReader
from pathlib import Path
from typing import List, Optional
from langchain_core.documents import Document
import re
import os


def load_and_split_pdf(
        pdf_path: str,
        chunk_size: int = 500,
        chunk_overlap: int = 50
) -> List[Document]:
    """
    加载 PDF 并分块

    Args:
        pdf_path: PDF 文件路径
        chunk_size: 每个块的大小（字符数）
        chunk_overlap: 块之间的重叠

    Returns:
        文档块列表
    """
    # 1. 加载 PDF
    loader = PyPDFLoader(pdf_path)
    documents = loader.load()

    print(f"📄 加载 PDF: {pdf_path}, 共 {len(documents)} 页")

    # 2. 分块
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "，", " ", ""],
        keep_separator=False
    )

    chunks = text_splitter.split_documents(documents)

    # 3. 添加元数据（来源）
    for chunk in chunks:
        chunk.metadata["source"] = Path(pdf_path).name
        chunk.metadata["page"] = chunk.metadata.get("page", 0)

    print(f"📦 分块完成: 共 {len(chunks)} 个块")
    return chunks


def load_multiple_pdfs(pdf_dir: str) -> List[Document]:
    """加载目录下的所有 PDF"""
    all_chunks = []
    pdf_dir_path = Path(pdf_dir)

    for pdf_file in pdf_dir_path.glob("*.pdf"):
        chunks = load_and_split_pdf(str(pdf_file))
        all_chunks.extend(chunks)

    return all_chunks


def load_pdf_with_docling(pdf_path: str) -> List[Document]:
    """
    使用 Docling 加载 PDF，保留格式

    优点：
    - 保留表格、列表、标题层级
    - 识别图片中的文字 (OCR)
    - 输出 Markdown 格式
    """
    converter = DocumentConverter()
    result = converter.convert(pdf_path)

    # 输出为 Markdown（保留格式）
    markdown_text = result.document.export_to_markdown()

    # 分块：按标题或段落分割
    chunks = split_by_headers(markdown_text)

    documents = []
    for i, chunk in enumerate(chunks):
        doc = Document(
            page_content=chunk,
            metadata={
                "source": pdf_path.split("/")[-1],
                "chunk_id": i,
                "format": "markdown"
            }
        )
        documents.append(doc)

    return documents


def split_by_headers(text: str) -> List[str]:
    """按标题层级分块，保持语义完整"""

    # 按 Markdown 标题分割
    pattern = r'(?=^#{1,3}\s+)'
    sections = re.split(pattern, text, flags=re.MULTILINE)

    # 过滤空块，合并太小的块
    chunks = []
    for section in sections:
        section = section.strip()
        if len(section) > 50:  # 至少50个字符
            chunks.append(section)
        elif chunks:
            chunks[-1] += "\n" + section

    return chunks if chunks else [text]


def load_pdf_advanced(pdf_path: str) -> List[Document]:
    """
    使用 PyPDF 加载 PDF，并智能分块
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

    reader = PdfReader(pdf_path)
    full_text = []

    for page_num, page in enumerate(reader.pages):
        text = page.extract_text()
        if text:
            # 清理文本
            text = clean_text(text)
            full_text.append({
                "page": page_num + 1,
                "text": text
            })

    # 智能分块
    documents = smart_chunking(full_text, os.path.basename(pdf_path))

    print(f"📄 成功加载 {len(documents)} 个文档块")
    return documents


def clean_text(text: str) -> str:
    """清理文本"""
    # 去除多余空白
    lines = text.split('\n')
    lines = [l.strip() for l in lines if l.strip()]

    # 合并被错误分割的句子
    merged = []
    for line in lines:
        if merged and not merged[-1].endswith(('。', '！', '？', '」', '：')):
            merged[-1] += line
        else:
            merged.append(line)

    return '\n'.join(merged)


def smart_chunking(page_data: List[dict], source: str) -> List[Document]:
    """
    智能分块：按语义分割
    """
    documents = []
    current_chunk = ""
    current_page = 1

    for page_info in page_data:
        page_num = page_info["page"]
        text = page_info["text"]

        lines = text.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 检测章节标题
            is_title = re.match(r'^#{1,3}\s+|^第[一二三四五六七八九十]+章|^[0-9一二三四五六七八九十]+[、.．]\s*', line)

            if is_title and len(current_chunk) > 100:
                # 保存当前块
                if current_chunk:
                    doc = Document(
                        page_content=current_chunk,
                        metadata={
                            "source": source,
                            "page": current_page,
                            "type": "section"
                        }
                    )
                    documents.append(doc)
                    current_chunk = ""
                current_page = page_num

            current_chunk += line + "\n"

            # 控制块大小
            if len(current_chunk) > 1500:
                doc = Document(
                    page_content=current_chunk,
                    metadata={
                        "source": source,
                        "page": current_page,
                        "type": "chunk"
                    }
                )
                documents.append(doc)
                current_chunk = ""

    # 保存最后一块
    if current_chunk:
        doc = Document(
            page_content=current_chunk,
            metadata={
                "source": source,
                "page": current_page,
                "type": "chunk"
            }
        )
        documents.append(doc)

    return documents





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
                    docs = self._create_documents(sub, chunk.metadata, i, len(sub_chunks))
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
                print(f"✅ 加载: {file_path.name} → {len(docs)} 个块")
            except Exception as e:
                print(f"❌ 加载失败: {file_path.name}, {e}")

        return all_docs


def load_markdown_simple(file_path: str, chunk_size: int = 1000) -> List[Document]:
    """
    简单版：直接按标题分块，不保留元数据
    """


    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 按标题分块
    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[
            ("#", "h1"),
            ("##", "h2"),
            ("###", "h3"),
        ]
    )
    chunks = splitter.split_text(content)

    return [
        Document(
            page_content=chunk.page_content,
            metadata={
                "source": Path(file_path).name,
                **chunk.metadata
            }
        )
        for chunk in chunks
    ]


