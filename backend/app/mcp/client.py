# author: szw
import asyncio
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

async def getAllClientTools():
    mcp_tools = []

    # 修正：获取 Scripts 目录
    venv_root = Path(sys.executable).parent  # D:\anaconda3\envs\AI私厨+智扫通客服
    scripts_dir = venv_root / "Scripts"  # D:\anaconda3\envs\AI私厨+智扫通客服\Scripts
    uvx_path = scripts_dir / "uvx.exe"  # D:\anaconda3\envs\AI私厨+智扫通客服\Scripts\uvx.exe

    time_mcp_tool = MultiServerMCPClient(
        {
            "time": {
                "transport": "stdio",
                "command": str(uvx_path),
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
                "command": str(uvx_path),
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
