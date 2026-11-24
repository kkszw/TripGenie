# author: szw
"""模型输出的容错解析工具。

纯函数，不依赖任何业务模块——节点里解析失败想单测时可以直接 import 这里。
"""
import json
import re


def parse_llm_json(text: str, *, allow_list: bool = False):
    """解析 LLM 返回的 JSON，容忍 markdown 代码围栏和前后多余文本。

    DeepSeek 经常把 JSON 包进 ```json ... ```（实测必现），裸 json.loads 会抛
    JSONDecodeError，导致整条行程生成被 except 吞掉——表现为前端只看到原始文本、
    没有行程卡片和地图。这里按「原文 → 去围栏 → 按包裹符截取」依次尝试。

    allow_list：默认只接受对象，因为行程/槽位/质检的返回都是对象。只有地图的
    景点提取需要数组，由调用方显式打开。

    为什么区分包装形状而不是无脑取第一个 { 到最后一个 }：景点提取的合法输出是
    {"attractions": [...]}，而模型有时直接给 [...]。两种形状下 { 和 [ 谁先出现
    并不固定，一律先找大括号会把数组形式的内层对象当成整体，解析出一个只含单个
    景点的 dict，调用方再按名字取字段就变成"提取成功但 0 个景点"。
    """
    if not isinstance(text, str):
        text = str(text)

    stripped = text.strip()
    candidates = [stripped]
    fence = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", stripped, re.S)
    if fence:
        candidates.append(fence.group(1).strip())

    # 先出现的包裹符决定整体形状：{"attractions": [...]} 取对象，[{...}] 取数组
    inner = candidates[-1]
    start_obj, start_arr = inner.find("{"), inner.find("[")
    if start_arr != -1 and (start_obj == -1 or start_arr < start_obj):
        candidates.append(inner[start_arr:])
    elif start_obj != -1:
        candidates.append(inner[start_obj:])

    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            # raw_decode 解析出第一个完整值就停，尾部残留的"祝您旅途愉快"之类
            # 说明文字不会让整条解析失败（json.loads 会）
            data = decoder.raw_decode(candidate)[0]
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
        if allow_list and isinstance(data, list):
            return data

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
