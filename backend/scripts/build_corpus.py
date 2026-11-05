# author: szw
"""公共语料库构建：PDF → 结构化文本 → 向量库。

为什么是两段式而不是一条命令跑到底：

  抽取是唯一昂贵的一步。这批 125 份「我是驴友」攻略里 63% 的文字层是坏的
  （内嵌子集字体 ToUnicode 映射错位，三种解析器返回同一批错字），必须走
  OCR，实测 2.6 秒/页、单份约 16 页，全量 2000 页约 50 分钟。
  而分块和嵌入快得多（全量约 20 分钟，瓶颈是 embedding 接口而不是本地算力）。

  拆成 extract / index 之后，调分块参数、换 embedding 模型、重建整个库，
  都只需要重跑 index——不用再付那 50 分钟的 OCR。抽取产物落成 JSON 存进仓库，
  换台机器 clone 下来也能直接建库，不需要那 2GB 原始 PDF。

为什么 JSON 而不是 Markdown：

  分块函数 chunk_pages 要的是 [{page, text}]，页码就是溯源信息。落成 md
  会把页码丢掉，检索命中某一句话时无法告诉用户"在这份 PDF 的第 7 页"。
  代价是不可读——用 `stats` 子命令看摘要，用 --preview 看正文。

用法：
    cd backend
    python scripts/build_corpus.py extract --src "E:/旅行攻略" --limit 20
    python scripts/build_corpus.py extract --src "E:/旅行攻略"
    python scripts/build_corpus.py index --rebuild
    python scripts/build_corpus.py index --only 大连,三亚
    python scripts/build_corpus.py stats
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.services.pdf_chunk import chunk_pages, chunk_stats  # noqa: E402
from app.services.corpus_clean import clean_pages  # noqa: E402
from app.services.corpus_naming import (  # noqa: E402
    corpus_stem,
    parse_destination,
    slugify,
)
from app.services.pdf_extract import MAX_PAGES, extract_document  # noqa: E402

DEFAULT_SRC = Path(r"E:\旅行攻略")
CORPUS_DIR = ROOT / "app" / "data" / "corpus"

# 抽取产物格式版本。改了 JSON 结构就 +1，extract 会把旧版本的当成"需要重跑"，
# 否则 index 会读到缺字段的旧文件然后 KeyError。
CORPUS_VERSION = 1

# ==================== extract：PDF → JSON ====================

def file_hash(path: Path) -> str:
    """流式 sha256。45MB 的文件一次性读进内存没必要。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def corpus_path(out_dir: Path, slug: str) -> Path:
    return out_dir / f"{slugify(slug)}.json"


def extract_one(pdf: Path, out_dir: Path, force: bool = False) -> Dict:
    """抽一份 PDF 并落盘。

    Returns:
        {"status": "ok"|"skip"|"fail", "destination": str, "reason"?: str,
         "mode"?: str, "pages"?: int, "chars"?: int, "seconds"?: float}
    """
    destination = parse_destination(pdf.name)
    slug = corpus_stem(pdf.name)
    target = corpus_path(out_dir, slug)
    digest = file_hash(pdf)

    # 断点续传：已经抽过同一份文件（哈希一致）就跳过。
    # 为什么比对哈希而不是文件大小+修改时间：用户可能重新下载过同一份攻略，
    # 大小时间都变了但内容一样，重跑 50 分钟毫无意义。
    if target.exists() and not force:
        try:
            old = json.loads(target.read_text(encoding="utf-8"))
            if (old.get("version") == CORPUS_VERSION
                    and old.get("source_sha256") == digest):
                return {"status": "skip", "destination": destination,
                        "pages": old.get("pages_count"), "mode": old.get("mode")}
        except (json.JSONDecodeError, OSError):
            pass  # 文件坏了就当没抽过，重跑覆盖

    started = time.time()
    try:
        document = extract_document(pdf)
    except Exception as exc:
        return {"status": "fail", "destination": destination,
                "reason": f"{type(exc).__name__}: {exc}"}

    pages = document.get("pages") or []
    if not pages:
        return {"status": "fail", "destination": destination, "reason": "没有抽出任何页面"}

    chars = sum(len(str(p.get("text") or "")) for p in pages)
    payload = {
        "version": CORPUS_VERSION,
        "slug": slug,
        "destination": destination,
        "title": pdf.stem,
        "source_file": pdf.name,
        "source_size": pdf.stat().st_size,
        "source_sha256": digest,
        "mode": document.get("mode", "text"),
        "pages_count": len(pages),
        "chars": chars,
        "quality": document.get("quality") or {},
        "text_layer_quality": document.get("text_layer_quality") or {},
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pages": pages,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再替换：中途 Ctrl-C 不会留下半截 JSON，
    # 否则下次 extract 会因为 JSONDecodeError 重跑，而 index 会读到坏文件。
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)

    return {"status": "ok", "destination": destination,
            "mode": payload["mode"], "pages": len(pages), "chars": chars,
            "seconds": round(time.time() - started, 1),
            "quality": payload["quality"]}


def cmd_extract(args) -> int:
    src = Path(args.src)
    if not src.exists():
        print(f"目录不存在: {src}")
        return 2

    files = sorted(src.glob("*.pdf"))
    if args.only:
        wanted = {w.strip() for w in args.only.split(",") if w.strip()}
        files = [f for f in files if parse_destination(f.name) in wanted
                 or any(w in f.name for w in wanted)]
    if args.shard:
        # 分片：同一份文件只会落在一个分片里，不会两个进程同时写同一个
        # JSON（那样后写的会覆盖先写的，且 write-replace 会打架）。
        #
        # 但在这台机器上并行**没有收益**，实测过：单进程抽一份 14 页 OCR
        # 要 58 秒，开两个进程后各要 129 秒，吞吐基本没变。原因是
        # onnxruntime 默认就会占满所有核心，两个进程各自开满线程只会
        # 互相超订。所以 --shard 的真实用途是分到多台机器上跑，
        # 或者在有 GPU 的机器上避开 CPU 争抢；单机多开反而更慢。
        try:
            index, total = (int(x) for x in args.shard.split("/"))
            if not (0 <= index < total):
                raise ValueError
        except ValueError:
            print("--shard 格式应为 i/n，例如 0/3")
            return 2
        files = [f for position, f in enumerate(files) if position % total == index]
        print(f"分片 {index}/{total}：{len(files)} 份")
    if args.limit:
        files = files[:args.limit]
    if not files:
        print("没有匹配的 PDF")
        return 2

    out_dir = Path(args.out) if args.out else CORPUS_DIR
    print(f"抽取 {len(files)} 份 → {out_dir}")
    print(f"单份超过 {MAX_PAGES} 页会被拒绝\n")

    ok = skip = fail = 0
    failures: List[Dict] = []
    started = time.time()

    for index, pdf in enumerate(files, start=1):
        result = extract_one(pdf, out_dir, force=args.force)
        tag = f"[{index}/{len(files)}]"
        if result["status"] == "ok":
            ok += 1
            quality = result.get("quality") or {}
            print(f"{tag} ✓ {result['destination']:<8} {result['mode']:<4} "
                  f"{result['pages']:>3}页 {result['chars']:>6}字 "
                  f"生僻{quality.get('rare_ratio', 0):.1%} "
                  f"{result['seconds']:>5.1f}s")
        elif result["status"] == "skip":
            skip += 1
            print(f"{tag} · {result['destination']:<8} 已抽取，跳过")
        else:
            fail += 1
            failures.append(result)
            print(f"{tag} ✗ {result['destination']:<8} {result['reason'][:70]}")

    elapsed = time.time() - started
    print(f"\n完成：新增 {ok}，跳过 {skip}，失败 {fail}，耗时 {elapsed / 60:.1f} 分钟")
    if failures:
        print("\n失败清单：")
        for item in failures:
            print(f"  {item['destination']}: {item['reason'][:110]}")
    return 0 if fail == 0 else 1


# ==================== index：JSON → 向量库 ====================

def load_corpus_files(out_dir: Path, only: Optional[str] = None) -> List[Dict]:
    files = sorted(out_dir.glob("*.json"))
    payloads: List[Dict] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  ! 跳过损坏文件 {path.name}: {exc}")
            continue
        if data.get("version") != CORPUS_VERSION:
            print(f"  ! 跳过旧版本 {path.name}（请重跑 extract --force）")
            continue
        if only:
            wanted = {w.strip() for w in only.split(",") if w.strip()}
            if data.get("destination") not in wanted:
                continue
        payloads.append(data)
    return payloads


def build_documents(data: Dict) -> List:
    """一份语料 → 检索块。

    destination 是这批语料必须带的字段：125 个目的地混在一个 collection 里，
    不同攻略的「交通」「有问必答」章节措辞高度雷同，不按目的地过滤的话，
    问大连会召回三亚的交通段落。

    doc_id 用 slug 而不是 destination：重名的两份厦门要能独立删除，
    否则重建时互相删块。

    入库前先清一遍文字层里的模板残留（别的城市的封面标题混进来了），
    清洗只在这一步做，corpus/*.json 保持原样 —— 那是「文件里有什么」的
    记录，不该被读取策略改写。
    """
    slug = data.get("slug") or data["destination"]
    pages = clean_pages(data["pages"], data["destination"])
    return chunk_pages(
        pages,
        metadata={
            "doc_id": f"guide:{slugify(slug)}",
            "destination": data["destination"],
            "source": data["source_file"],
            "scope": "public",
        },
    )


def cmd_index(args) -> int:
    from app.rag.vector_store import (
        add_documents,
        count_documents,
        delete_documents,
        get_collection_stats,
        reset_collection,
    )

    out_dir = Path(args.out) if args.out else CORPUS_DIR
    payloads = load_corpus_files(out_dir, args.only)
    if not payloads:
        print(f"{out_dir} 下没有可用的语料，先跑 extract")
        return 2

    if args.rebuild and not args.only:
        reset_collection("public")
        print("已清空 public collection\n")

    # --rebuild 已经清空，跳过检查没有意义（也省掉几百次查询）
    resumable = bool(args.skip_existing) and not args.rebuild
    batch_size = max(1, settings.embedding_batch_size)
    total_chunks = 0
    indexed = 0
    skipped = 0
    started = time.time()

    for index, data in enumerate(payloads, start=1):
        destination = data["destination"]
        slug = data.get("slug") or destination
        doc_id = f"guide:{slugify(slug)}"
        documents = build_documents(data)
        if not documents:
            print(f"[{index}/{len(payloads)}] ✗ {destination}: 没有分出块")
            continue

        # 断点续传：块数完全一致才跳过。只判「有没有块」是不够的——
        # 上一轮可能写到一半被额度/欠费掐断，库里躺着半份，跳过它会让
        # 这份语料永远缺后半本，而且不报错。块数对不上就整份重写。
        existing = 0
        if resumable:
            existing = count_documents({"doc_id": doc_id}, scope="public")
            if existing == len(documents):
                skipped += 1
                print(f"[{index}/{len(payloads)}] = {slug:<10} 已入库（{existing} 块），跳过")
                continue
            if existing:
                print(f"  ! {slug} 库里只有 {existing}/{len(documents)} 块，判定为中断残留，重写")

        # 幂等：先删掉这份语料的旧块再写。重跑 index 不会让同一份语料
        # 出现两遍——那会让它在检索结果里占双倍名额且毫无好处。
        # 键用 doc_id 而不是 destination：厦门有两份，按目的地删会把
        # 刚写进去的那份也删掉，最后只剩一份。
        if not resumable or existing:
            try:
                delete_documents({"doc_id": doc_id}, scope="public")
            except Exception as exc:
                print(f"  ! 清理旧块失败（可忽略，继续写）：{exc}")

        for start in range(0, len(documents), batch_size):
            add_documents(documents[start:start + batch_size], scope="public")

        total_chunks += len(documents)
        indexed += 1
        stats = chunk_stats(documents)
        print(f"[{index}/{len(payloads)}] ✓ {slug:<10} "
              f"{len(documents):>3}块 {stats['chars']:>6}字 天数{stats['days']:>2}")

    elapsed = time.time() - started
    print(f"\n写入 {indexed} 份 / {total_chunks} 块，跳过 {skipped} 份，"
          f"耗时 {elapsed / 60:.1f} 分钟")
    print(f"public collection 当前共 {get_collection_stats(scope='public')} 块")
    # 检索侧把「库里有哪几个目的地」缓存在进程内存里（每次查询都聚合一遍
    # 几千条 metadata 不值得）。重建之后不重启，过滤条件还是旧的。
    print("提示：服务正在运行的话需要重启，检索侧的目的地缓存才会刷新")
    return 0


# ==================== stats：看语料概况 ====================

def cmd_stats(args) -> int:
    out_dir = Path(args.out) if args.out else CORPUS_DIR
    payloads = load_corpus_files(out_dir)
    if not payloads:
        print(f"{out_dir} 下还没有语料")
        return 2

    total_pages = sum(p.get("pages_count") or 0 for p in payloads)
    total_chars = sum(p.get("chars") or 0 for p in payloads)
    ocr = [p for p in payloads if p.get("mode") == "ocr"]
    print(f"语料 {len(payloads)} 份  {total_pages} 页  {total_chars:,} 字")
    print(f"其中 OCR {len(ocr)} 份（{len(ocr) / len(payloads):.0%}）\n")

    print(f"{'目的地':<10}{'模式':<6}{'页':>4}{'字':>8}{'生僻率':>8}")
    for data in sorted(payloads, key=lambda p: -(p.get("pages_count") or 0)):
        quality = data.get("quality") or {}
        print(f"{data['destination']:<10}{data.get('mode', '?'):<6}"
              f"{data.get('pages_count', 0):>4}{data.get('chars', 0):>8}"
              f"{quality.get('rare_ratio', 0):>7.1%}")
    return 0


# ==================== 入口 ====================

def main() -> int:
    parser = argparse.ArgumentParser(description="公共语料库构建")
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser("extract", help="PDF → 结构化 JSON（OCR 在这一步）")
    p_extract.add_argument("--src", default=str(DEFAULT_SRC), help="PDF 目录")
    p_extract.add_argument("--out", default=None, help="输出目录，默认 app/data/corpus")
    p_extract.add_argument("--limit", type=int, default=0, help="只处理前 N 份")
    p_extract.add_argument("--only", default="", help="只处理这些目的地，逗号分隔")
    p_extract.add_argument("--shard", default="",
                           help="分片并行，格式 i/n（如 0/2）。多开几个进程跑不同的片")
    p_extract.add_argument("--force", action="store_true", help="已抽取的也重跑")
    p_extract.set_defaults(func=cmd_extract)

    p_index = sub.add_parser("index", help="JSON → 向量库")
    p_index.add_argument("--out", default=None)
    p_index.add_argument("--only", default="", help="只重建这些目的地")
    p_index.add_argument("--rebuild", action="store_true", help="先清空 public collection")
    p_index.add_argument("--skip-existing", action="store_true",
                         help="块数已一致的语料直接跳过（中断后接着跑）")
    p_index.set_defaults(func=cmd_index)

    p_stats = sub.add_parser("stats", help="看语料概况")
    p_stats.add_argument("--out", default=None)
    p_stats.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
