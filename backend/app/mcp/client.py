# author: szw
import os
import shutil
import sys
from pathlib import Path
from typing import Any, List
from langchain_core.tools import BaseTool
from app.config import settings
from app.core.logging import get_logger
from app.core.tools import get_weather_tool, retrieve_guides_tool
from langchain_mcp_adapters.client import MultiServerMCPClient

logger = get_logger(__name__)

DISABLED_MCP_TOOLS = [
    "maps_weather",
]

# amap-mcp-server 依赖的 mcp 1.8.x 会 `from pydantic._internal._typing_extra import eval_type_backport`，
# 而该函数在 pydantic 2.14 被移除。uvx 默认解析到最新的 2.14.x，导致子进程启动即 ImportError，
# 表现为「加载MCP工具失败」且地图类工具全部不可用。
# 用 --with 把 pydantic 压回 2.13.x（该版本仍有此函数），只影响这个 uvx 隔离环境，不动主环境。
_AMAP_PYDANTIC_PIN = "pydantic>=2.9,<2.14"

# 子进程（amap-mcp-server）会把它的 stdout/stderr 直接继承到我们这一侧，
# 于是 PyCharm 控制台里会看到红色的告警。这些都是 MCP 服务端自己的输出，与本项目无关：
#   1) IncompleteFieldDefinitionWarning: pydantic 2.13 + pydantic-settings 2.16 组合下，
#      它在解析 starlette 命令行配置的 lifespan 前向引用时发出的告警，纯噪音；
#   2) "Processing request of type CallToolRequest" 是 mcp 1.8.x 的 logger.info（默认 INFO 级）。
# 只在这两个子进程的环境里压掉，不影响主进程的日志与告警。
_MCP_SUBPROCESS_ENV = {
    "PYTHONWARNINGS": "ignore",
    "FASTMCP_LOG_LEVEL": "ERROR",
}


def resolve_uvx_path() -> str:
    """定位 uvx 可执行文件：优先 .env 配置，其次 PATH，最后当前虚拟环境"""
    if settings.uvx_path:
        return settings.uvx_path

    found = shutil.which("uvx")
    if found:
        return found

    bin_dir = "Scripts" if os.name == "nt" else "bin"
    executable = "uvx.exe" if os.name == "nt" else "uvx"
    return str(Path(sys.executable).parent / bin_dir / executable)


async def getAllClientTools():
    mcp_tools = []
    uvx_path = resolve_uvx_path()

    time_mcp_tool = MultiServerMCPClient(
        {
            "time": {
                "transport": "stdio",
                "command": uvx_path,
                "args": [
                    "mcp-server-time",
                    "--local-timezone=Asia/Shanghai"
                ],
                "env": dict(_MCP_SUBPROCESS_ENV),
            }
        }
    )
    amap_mcp_tool = MultiServerMCPClient(
        {
            "amap-mcp-server": {
                "transport": "stdio",
                "command": uvx_path,
                "args": [
                    "--with",
                    _AMAP_PYDANTIC_PIN,
                    "amap-mcp-server"
                ],
                "env": {
                    "AMAP_MAPS_API_KEY": settings.amap_api_key,
                    **_MCP_SUBPROCESS_ENV,
                }
            }
        }
    )

    try:
        amap_mcp_tools = await amap_mcp_tool.get_tools()
        amap_mcp_tools = filter_tools(amap_mcp_tools)
        time_mcp_tools = await time_mcp_tool.get_tools()
        mcp_tools += time_mcp_tools
        mcp_tools += amap_mcp_tools
    except Exception as e:
        logger.warning("加载 MCP 工具失败", extra={"err": str(e)})

    return mcp_tools


def filter_tools(tools: List[BaseTool]) -> List[BaseTool]:
    """过滤工具"""
    filtered = []

    for tool in tools:
        # 1. 精确匹配禁用列表
        if tool.name in DISABLED_MCP_TOOLS:
            continue

        filtered.append(tool)

    return filtered

async def getAllTools() -> list[Any]:
    mcp_tools = await getAllClientTools()
    tools = [get_weather_tool, retrieve_guides_tool] + mcp_tools
    return tools
