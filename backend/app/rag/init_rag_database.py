# author: szw
# app/scripts/init_rag_db.py
import sys
import os
from pathlib import Path
from app.rag.embeddings import get_embeddings
from app.rag.document_loader import *
from app.rag.vector_store import add_documents, get_vector_store, get_collection_stats
from app.config import settings
from app.utils.path_tool import get_absolute_path

import pdfplumber
from docx import Document
import pandas as pd
from pptx import Presentation
from PIL import Image


def init_rag_database():
    """初始化 RAG 向量数据库"""

    print("=" * 60)
    print("初始化 RAG 向量数据库")
    print("=" * 60)

    loader = MarkdownLoader()

    # 加载所有 Markdown 文件
    file_path = get_absolute_path("data/travel_guide/我是驴友-大连旅游攻略.md")

    # 分块
    chunks = loader.load_markdown(str(file_path))
    print(f"\n📦 共生成 {len(chunks)} 个文档块\n")

    # 显示样例
    for i, doc in enumerate(chunks[:5]):
        print(f"【块 {i + 1}】")
        print(f"标题: {doc.metadata.get('h1', '')} > {doc.metadata.get('h2', '')}")
        print(f"内容: {doc.page_content}...")
        print("-" * 40)

    # # 5. 创建向量库
    print("🔧 正在创建向量库...")
    embedding = get_embeddings()
    vector_store = get_vector_store(embedding)
    # 6. 添加文档
    print("📤 添加文档到向量库...")
    for i in range(0, len(chunks), 20):
        batch = chunks[i:i + 20]
        print(f"处理批次 {i // 20 + 1}/{len(chunks) // 20 + 1}")
        add_documents(batch)
        print(f"✅ 成功添加 {len(batch)} 个文档")
    print("\n✅ RAG 向量数据库初始化完成！")
    # 8. 查看统计
    get_collection_stats()

    print("\n🧪 测试检索:")
    test_query = "大连的美食？"
    results = vector_store.similarity_search(test_query, k=3)

    for i, doc in enumerate(results, 1):
        print(f"  [{i}] {doc.page_content[:150]}...")

    print("\n✅ RAG 向量数据库初始化完成！")


if __name__ == "__main__":
    init_rag_database()
