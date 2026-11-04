# author: szw
"""模型输出的容错解析工具。

纯函数，不依赖任何业务模块——节点里解析失败想单测时可以直接 import 这里。
"""
import json
import re


def parse_llm_json(text: str) -> dict:
    """解析 LLM 返回的 JSON，容忍 markdown 代码围栏和前后多余文本。

    DeepSeek 经常把 JSON 包进 ```json ... ```（实测必现），裸 json.loads 会抛
    JSONDecodeError，导致整条行程生成被 except 吞掉——表现为前端只看到原始文本、
    没有行程卡片和地图。这里按「原文 → 去围栏 → 截取最外层大括号」依次尝试。
    """
    if not isinstance(text, str):
        text = str(text)

    candidates = [text]
    stripped = text.strip()
    fence = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", stripped, re.S)
    if fence:
        candidates.append(fence.group(1))
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start:end + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue

    raise ValueError(f"无法解析为 JSON，原始内容前 200 字符：{text[:200]}")


def as_number(value):
    """把 LLM 返回的天数/预算宽松地转成数字，转不了就当没提供。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(str(value).strip().rstrip("天元块人民币")))
        except (TypeError, ValueError):
            return None
