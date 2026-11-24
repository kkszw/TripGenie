# author: szw
"""文本 token 估算。

放在 utils 而不是 services/database.py：检索侧（rag/retriever.py）也要用它
给上下文设预算，而 services.database 末尾有 `db = DatabaseService()` 单例，
import 它会立刻建库建表——测试里只想算个 token 数，不该有这种副作用。

为什么不引 tiktoken：它是 OpenAI 的分词器，对国内模型的中文切分不准
（rag/embeddings.py 里有同样的说明），而且只为估个数就多装一个带词表的
二进制包不划算。这里按字符类别粗估：中文 1 字 1 token（偏保守，实际约
0.6~1），其余 4 字符 1 token（英文经验值）。估多了只是少带几句旧话，
估少了才会超窗口，所以宁可高估。
"""


def estimate_tokens(text: str) -> int:
    """粗估一段文本的 token 数（偏保守）。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk + 3) // 4
