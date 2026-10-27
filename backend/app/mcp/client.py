# author: szw
import os
import shutil
import sys
from pathlib import Path
from typing import Any, List
from langchain_core.tools import BaseTool
from app.config import settings
from app.core.tools import get_weather_tool, retrieve_guides_tool
from langchain_mcp_adapters.client import MultiServerMCPClient

DISABLED_MCP_TOOLS = [
    "maps_weather",
]


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
                ]
            }
        }
    )
    amap_mcp_tool = MultiServerMCPClient(
        {
            "amap-mcp-server": {
                "transport": "stdio",
                "command": uvx_path,
                "args": [
                    "amap-mcp-server"
                ],
                "env": {
                    "AMAP_MAPS_API_KEY": settings.amap_api_key,
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
        print(f"加载MCP工具失败: {e}")

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
