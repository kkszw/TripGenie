# author: szw
"""闲聊分支：chat → tool_node 的工具调用循环。"""
from typing import Literal

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END

from app.core import llm as llm_runtime
from app.core.state import TripState


async def chat(state: TripState, config: RunnableConfig = None):
    """对话节点，如果是正常聊天就正常调用返回对话，如果需要调用工具则调用工具"""
    await llm_runtime.ensure_tools()
    messages = state.get("messages", [])
    writer = config.get("configurable", {}).get("writer") if config else None
    if writer:
        writer({
            "type": "node_start",
            "node": "💬 AI 思考中",
            "message": "正在生成回复..."
        })
    full_response = None
    # user_visible 同 generate_plan：这一次调用的 token 是给用户看的正文。
    # 注意这里是重新赋值 config，会把函数参数里可能带的 tags 丢掉，所以必须显式写。
    config: RunnableConfig = {
        "configurable": {"thread_id": state.get("thread_id")},
        "tags": ["user_visible"],
    }
    async for chunk in llm_runtime.model_with_tools.astream(messages, config=config):

        if full_response is None:
            full_response = chunk
        else:
            full_response += chunk

        if chunk.content and not chunk.tool_calls:
            if writer:
                writer({"type": "chunk", "content": chunk.content})

    if writer:
        writer({
            "type": "node_end",
            "node": "AI 思考完成",
            "message": "回复完成"
        })
    if full_response and full_response.tool_calls:
        return {"messages": [full_response]}
    else:
        return {"response": full_response.content, "messages": [AIMessage(content=full_response.content)]}


async def tool_node(state: TripState):
    """工具节点：执行工具调用，如果需要继续则返回工具调用"""
    last_message = state["messages"][-1]

    if not hasattr(last_message, 'tool_calls') or not last_message.tool_calls:
        return {"response": "没有工具调用"}

    tool_calls = last_message.tool_calls
    print(f"🔧 需要执行 {len(tool_calls)} 个工具调用")

    results = []

    for i, tool_call in enumerate(tool_calls):
        print(f"🔧 执行工具 {i + 1}/{len(tool_calls)}: {tool_call['name']}")

        tool = llm_runtime.tools_by_name.get(tool_call["name"])
        if not tool:
            error_msg = f"工具 {tool_call['name']} 不存在"
            print(f"❌ {error_msg}")
            results.append(ToolMessage(
                content=error_msg,
                tool_call_id=tool_call["id"]
            ))
            continue

        try:
            # 执行工具
            if hasattr(tool, 'ainvoke'):
                print(f"参数为：{tool_call['args']}")
                result = await tool.ainvoke(tool_call["args"])
            else:
                print(f"参数为：{tool_call['args']}")
                result = tool.invoke(tool_call["args"])

            print(f"✅ 工具 {i + 1} 执行完成")

            results.append(ToolMessage(
                content=str(result) if result else "执行完成",
                tool_call_id=tool_call["id"]
            ))

        except Exception as e:
            error_msg = f"工具执行失败: {str(e)}"
            print(f"❌ {error_msg}")
            results.append(ToolMessage(
                content=error_msg,
                tool_call_id=tool_call["id"]
            ))

    # 如果所有工具都执行完成，生成最终回复
    all_messages = state.get("messages", []) + results

    try:
        # 调用 LLM 生成最终回复（不带工具）。这条是用户真正看到的正文，
        # 同样要打 user_visible——否则走工具的那一轮，最终回复会被 chat.py 过滤掉。
        final_response = await llm_runtime.llm.ainvoke(
            all_messages, config={"tags": ["user_visible"]}
        )
        final_content = final_response.content if hasattr(final_response, 'content') else str(final_response)

        print(f"📝 最终回复: {final_content[:100]}...")

        return {
            "messages": results + [AIMessage(content=final_content)],
            "response": final_content
        }
    except Exception as e:
        print(f"❌ 生成最终回复失败: {e}")
        # 如果生成失败，合并工具结果
        tool_results = "\n".join([r.content for r in results if r.content])
        return {
            "messages": results,
            "response": tool_results
        }


def should_continue(state: TripState) -> Literal["tool_node", END]:
    """判断是否需要调用工具"""
    messages = state.get("messages", [])
    if not messages:
        return END
    last_message = messages[-1]
    # 检查是否有工具调用
    if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
        print(f"下一个节点是工具节点 (有 {len(last_message.tool_calls)} 个工具调用)")
        return "tool_node"
    return END
