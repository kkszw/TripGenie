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

        full_response = ""  # 用于 SSE 流式输出（保持原样）
        save_content = ""  # ✅ 用于保存到数据库的内容（只累积 generate_plan 的输出）
        map_data_to_save = None
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

                    # ✅ 只把 generate_plan 节点的输出存到 save_content
                    tags = event.get("tags", [])
                    if "generate_plan" in tags:
                        save_content += content

                    yield f"data: {json.dumps({'type': 'chunk', 'content': content}, ensure_ascii=False)}\n\n"

            # ✅ 处理节点开始
            elif kind == "on_chain_start":
                node_name = event.get("name", "unknown")
                if node_name and node_name != "RunnableSequence":
                    yield f"data: {json.dumps({'type': 'node_start', 'node': node_name, 'message': f'正在执行 {node_name}...'}, ensure_ascii=False)}\n\n"

            # ✅ 处理节点结束
            elif kind == "on_chain_end":
                node_name = event.get("name", "unknown")
                if node_name and node_name != "RunnableSequence":
                    output = event["data"].get("output", {})

                    # ✅ 只处理 generate_plan 节点
                    if node_name == "generate_plan" and not has_trip_plan:
                        if isinstance(output, dict):
                            draft_plan = output.get("draft_plan")
                            map_data = output.get("map_data")

                            print(f"🔍 generate_plan 完成: draft_plan={bool(draft_plan)}, map_data={bool(map_data)}")
                            if map_data:
                                print(f"🔍 map_data.attractions 数量: {len(map_data.get('attractions', []))}")

                            if draft_plan:
                                has_trip_plan = True
                                if map_data:
                                    map_data_to_save = map_data
                                plan = {
                                    "days": draft_plan,
                                    "map_data": map_data,
                                    "destination": output.get("destination", ""),
                                    "totalDays": len(draft_plan),
                                    "budget": output.get("budget", 0),
                                    "tips": output.get("tips", [])
                                }
                                print(f"📤 发送 trip_plan: map_data={bool(plan.get('map_data'))}")
                                yield f"data: {json.dumps({'type': 'trip_plan', 'plan': plan}, ensure_ascii=False)}\n\n"

                    yield f"data: {json.dumps({'type': 'node_end', 'node': node_name, 'message': f'{node_name} 完成'}, ensure_ascii=False)}\n\n"

        # ✅ 保存 AI 回复：只保存 generate_plan 的输出（纯净的行程 JSON）
        content_to_save = save_content.strip() if save_content.strip() else full_response

        if content_to_save:
            db.add_message(thread_id, "assistant", content_to_save, map_data=map_data_to_save)
            print(f"💾 保存 AI 回复（{len(content_to_save)} 字符）: {content_to_save[:80]}...")

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
        yield f"data: {json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"
