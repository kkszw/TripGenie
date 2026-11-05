# author: szw
"""语料入库前的清洗：去掉文字层里的模板残留。

这批「我是驴友」PDF 有一个共同的毛病：正文是对的，但页眉/页脚混进了
制作模板残留的其他文档标题。实测两个典型：

    上海 第13页  川藏川藏南南线线十上十日海日攻略··攻攻略略
    三亚 第22页  川藏川藏南南线线十三十日亚日攻三略··亚攻攻略略

它不是「内容错了」（正文仍是上海/三亚），而是**同一段文字被渲染了两遍
并交错**——「川藏南线十三日攻略」和「三亚攻略」叠在一起。这类行单独
成块会占用检索名额，混在正文里又会打断句子，所以要清掉。

判据只保留一条零误伤的规则：**「别的目的地·攻略」标题行**。
本文档不该出现别的城市的封面标题，命中即删。

试过的其它规则全被正常文本打回，记在这里免得以后又加回来：

    「不同字重复 >= 3 种」   → 「空空荡荡」「咿咿呀呀」中招
    「连续叠字」             → 「喝喝茶，散散心」中招
    「行首整词重复」         → 「一步一步地向石林深处走」中招

最后这条最可惜：它能抓到「川藏川藏南南线线十上十日海日攻略··攻攻略略」
这种交织行（模板把「川藏南线十三日攻略」和「上海攻略」渲染了两遍再叠在
一起），但「川藏川藏」和「一步一步」「研究研究」在正则层面长得一模一样，
区分不了。这类交织行每份文档只剩 2~4 行，夹在正文中间，对语义影响很小；
而误删一句正常描述是静默的数据损失，查都查不出来。所以留着。

清洗放在入库这一步而不是抽取那一步：抽取产物是「文件里有什么」的记录，
清洗是「怎么读这份文件」的策略。策略会变，事实不该跟着变，所以
corpus/*.json 永远保持原样，清洗只在 build_documents 时生效。
"""
import re
from typing import Dict, Iterable, List, Optional

# 「XX·攻略」标题行
_TITLE_RE = re.compile(r"^([\u4e00-\u9fa5]{2,10})\s*[·•・]\s*攻略")


def is_noise_line(line: str, destination: Optional[str] = None) -> bool:
    """这一行是不是混进来的其他文档标题。

    Args:
        line: 待判断的行（会先 strip）
        destination: 本文档的目的地。不给就不做任何判断——没有目的地
                     就无从知道什么算「别的」。
    """
    text = (line or "").strip()
    if not text or not destination:
        return False

    # 同一个目的地不删：「三亚·攻略」是本文档自己的封面标题
    match = _TITLE_RE.match(text)
    return bool(match and match.group(1) != destination)


def clean_pages(pages: Iterable[Dict],
                destination: Optional[str] = None) -> List[Dict]:
    """逐页清洗，返回新的页面列表（不修改入参）。

    页码保留：清洗掉的是行，不是页。即使某页被清空也要留一个空页占位，
    否则 page_start/page_end 会整体前移，检索命中的页码就全错了。
    """
    cleaned: List[Dict] = []
    for page in pages:
        number = page.get("page")
        text = str(page.get("text") or "")
        kept = [line for line in text.splitlines()
                if not is_noise_line(line, destination)]
        cleaned.append({"page": number, "text": "\n".join(kept)})
    return cleaned


def noise_stats(pages: Iterable[Dict],
                destination: Optional[str] = None) -> Dict:
    """清洗前后的行数对比，用来在构建日志里体现清洗掉了多少。"""
    total = kept = 0
    for page in pages:
        for line in str(page.get("text") or "").splitlines():
            if not line.strip():
                continue
            total += 1
            if not is_noise_line(line, destination):
                kept += 1
    return {"lines": total, "kept": kept, "removed": total - kept}
