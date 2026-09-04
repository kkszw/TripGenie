import uuid
import json
from fastapi import APIRouter, Request
from fastapi.sse import EventSourceResponse
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.runnables import RunnableConfig
from app.core.langgraph_app import  create_workflow
from app.core.state import TripState
from app.models.chat import ChatRequest
from app.services.database import db

router = APIRouter()

@router.post("/stream")
async def send_chat(request: ChatRequest):
    """发送聊天消息，使用 SSE 流式返回 Agent 响应。同时处理 HITL 中断决策。"""
    if not request.thread_id:
        request.thread_id = str(uuid.uuid4())
        db.create_session(request.thread_id, "default", "新对话")
    return EventSourceResponse(
        generate_sse_input(request.thread_id, request.message or ""),
        headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no"  # 如果使用 Nginx 代理
        }
    )


async def generate_sse_input(thread_id: str, message: str):
    """生成 SSE 流式响应 - 使用 astream_events 获取真正的流式输出"""
    workflow = await create_workflow()

    # 返回 thread_id
    yield f"data: {json.dumps({'thread_id': thread_id})}\n\n"

    try:
        # 获取历史消息
        history_messages = db.get_messages(thread_id)
        db.add_message(thread_id, "user", message)
        print(f"💾 保存用户消息: {thread_id} - {message[:20]}...")

        messages = []
        for msg in history_messages:
            if msg.get("role") == "user":
                messages.append(HumanMessage(content=msg.get("content")))
            elif msg.get("role") == "assistant":
                messages.append(AIMessage(content=msg.get("content")))
        messages.append(HumanMessage(content=message))

        initial_state: TripState = TripState(
            user_input=message,
            thread_id=thread_id,
            messages=messages
        )

        full_response = ""
        has_trip_plan = False

        # ✅ 使用 astream_events 获取真正的流式事件
        async for event in workflow.astream_events(
                initial_state,
                config={"configurable": {"thread_id": thread_id}},
                version="v2"
        ):
            kind = event["event"]

            # ✅ 处理 LLM 流式输出
            if kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                if chunk and hasattr(chunk, 'content') and chunk.content:
                    content = chunk.content
                    full_response += content
                    yield f"data: {json.dumps({'type': 'chunk', 'content': content})}\n\n"

            # ✅ 处理节点开始
            elif kind == "on_chain_start":
                node_name = event.get("name", "unknown")
                if node_name and node_name != "RunnableSequence":
                    yield f"data: {json.dumps({'type': 'node_start', 'node': node_name, 'message': f'正在执行 {node_name}...'})}\n\n"

            # ✅ 处理节点结束
            elif kind == "on_chain_end":
                node_name = event.get("name", "unknown")
                if node_name and node_name != "RunnableSequence":
                    output = event["data"].get("output", {})

                    # 检查是否有 trip_plan
                    if output and isinstance(output, dict):
                        if output.get("trip_plan") and not has_trip_plan:
                            has_trip_plan = True
                            yield f"data: {json.dumps({'type': 'trip_plan', 'plan': output['trip_plan']})}\n\n"
                        elif output.get("draft_plan") and not has_trip_plan:
                            has_trip_plan = True
                            yield f"data: {json.dumps({'type': 'trip_plan', 'plan': {'days': output['draft_plan']}})}\n\n"

                    yield f"data: {json.dumps({'type': 'node_end', 'node': node_name, 'message': f'{node_name} 完成'})}\n\n"

        # 保存 AI 回复
        if full_response:
            db.add_message(thread_id, "assistant", full_response)
            print(f"💾 保存 AI 回复: {thread_id} - {full_response[:20]}...")

        # 更新会话名称
        session = db.get_session(thread_id)
        if session and session.get("name") == "新对话":
            title = message[:10] + ("..." if len(message) > 20 else "")
            db.update_session_name(thread_id, title)
            print(f"📝 更新会话名称: {thread_id} - {title}")

        yield f"data: {json.dumps({'type': 'done', 'done': True})}\n\n"

    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
