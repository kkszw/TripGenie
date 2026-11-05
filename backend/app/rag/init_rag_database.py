# author: szw
"""重建 RAG 向量库。

⚠️ 内置语料已经迁到 `scripts/build_corpus.py`（PDF → 结构化 JSON → 向量库）。
本脚本只处理 `app/data/travel_guide/` 下的 Markdown，跑它会把公共库重建成
「只有那几份 md」的状态，之前入库的 125 份攻略会全部消失。日常重建请用：

    python scripts/build_corpus.py index --rebuild

留着本脚本是因为 Markdown 语料这条路仍然可用（手工整理的结构化文本，
比 PDF 抽取干净得多）。

只要换了 embedding 模型（或改了 EMBEDDING_DIMENSIONS），就必须重跑重建：
不同模型产出的向量不在同一个语义空间里，混在同一个 collection 里不会报错，
只会让检索结果静默变差——这是最难排查的一类问题，所以默认整库重建。

用法：
    python -m app.rag.init_rag_database          # 清空旧库后重建（默认）
    python -m app.rag.init_rag_database --keep   # 保留旧库，追加写入
"""
import sys

from app.rag.document_loader import MarkdownLoader
from app.rag.vector_store import (add_documents, get_collection_stats,
                                  reset_collection, search_similar)
from app.config import settings
from app.utils.path_tool import get_absolute_path

GUIDE_DIR = "data/travel_guide"


def init_rag_database(rebuild: bool = True) -> None:
    print("=" * 60)
    print(f"初始化 RAG 向量库（model={settings.embedding_model}, "
          f"dim={settings.embedding_dimensions}, collection={settings.embedding_collection}）")
    print("=" * 60)

    if rebuild:
        # 只重建 public，不碰 user。原来这里是 shutil.rmtree(整个 chroma 目录)，
        # 会把用户上传的资料一起抹掉 —— 重建公共语料不该有这种副作用。
        reset_collection("public")
        print("🗑️ 已清空旧公共向量库")

    loader = MarkdownLoader()
    chunks = loader.load_multiple_markdown(get_absolute_path(GUIDE_DIR))
    if not chunks:
        print(f"❌ {GUIDE_DIR} 下没有可用的 Markdown，已退出")
        return
    print(f"\n📦 共生成 {len(chunks)} 个文档块")

    # 官方限制单次请求最多 20 条，按配置的批大小切分
    batch_size = max(1, settings.embedding_batch_size)
    total_batches = (len(chunks) + batch_size - 1) // batch_size
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        add_documents(batch)
        print(f"✅ 批次 {i // batch_size + 1}/{total_batches}：写入 {len(batch)} 个块")

    count = get_collection_stats()
    print(f"\n✅ 向量库构建完成，共 {count} 个块")

    print("\n🧪 检索自检（query=大连的美食）:")
    for i, doc in enumerate(search_similar("大连的美食", top_k=3), 1):
        preview = doc.page_content[:80].replace("\n", " ")
        print(f"  [{i}] {preview}...")


if __name__ == "__main__":
    init_rag_database(rebuild="--keep" not in sys.argv)
