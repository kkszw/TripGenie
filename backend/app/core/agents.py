# author: szw
"""兼容门面（facade）。

真正的实现已经拆走：
    模型与工具  -> app/core/llm.py
    JSON 解析   -> app/core/json_utils.py
    提示词      -> app/core/prompts/
    图上的节点  -> app/core/nodes/
    高德接口    -> app/services/amap.py
    地图数据    -> app/services/map_data.py

这个文件只做再导出，不要在这里加新逻辑——需要新功能时直接改上面的模块。
保留它是因为 `from app.core.agents import xxx` 在外部脚本里出现过，
删掉会让那些调用点一起挂掉。
"""
from app.core import llm as _llm
from app.core.json_utils import as_number, parse_llm_json
from app.core.nodes import *  # noqa: F401,F403  （nodes 自带 __all__）
from app.core.nodes import __all__ as _nodes_all
from app.core.prompts import *  # noqa: F401,F403
from app.core.prompts import __all__ as _prompts_all
from app.services.amap import (
    driving_route_rest,
    parse_coordinates,
    parse_polyline,
    parse_route_result,
    plan_driving_route,
    poi_search_rest,
)
from app.services.map_data import (
    extract_attractions_with_llm,
    generate_map_data,
    geocode_attraction,
    locate_attraction,
    plan_segment,
    resolve_center_base,
    scatter_around,
)

_AMAP_ALL = [
    "driving_route_rest", "parse_coordinates", "parse_polyline",
    "parse_route_result", "plan_driving_route", "poi_search_rest",
    "extract_attractions_with_llm", "generate_map_data",
    "geocode_attraction", "locate_attraction", "plan_segment",
    "resolve_center_base", "scatter_around",
]

# 常量实例直接绑定即可
ensure_tools = _llm.ensure_tools
initialized_tools = _llm.initialized_tools
llm = _llm.llm
llm_extract = _llm.llm_extract
llm_with_structure = _llm.llm_with_structure

# 这三个是进程启动后才被重新赋值的全局，必须在访问时现取。
# 若在 import 时直接赋值，拿到的是导入那一刻的 None，而且永远不再更新。
_MUTABLE_GLOBALS = ("tools", "tools_by_name", "model_with_tools")


def __getattr__(name):
    if name in _MUTABLE_GLOBALS:
        return getattr(_llm, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "llm", "llm_extract", "llm_with_structure",
    "tools", "tools_by_name", "model_with_tools",
    "initialized_tools", "ensure_tools",
    "parse_llm_json", "as_number",
    *_nodes_all,
    *_prompts_all,
    *_AMAP_ALL,
]
