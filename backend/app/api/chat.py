import uuid
import json
from datetime import date, datetime
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.sse import EventSourceResponse
from langchain_core.messages import HumanMessage, AIMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langchain_core.runnables import RunnableConfig
from app.core.langgraph_app import get_workflow
from app.core.security import get_current_user
from app.core.state import TripState
from app.models.chat import ChatRequest
from app.services.database import db

router = APIRouter()

@router.post("/stream")
async def send_chat(request: ChatRequest, current_user: Dict = Depends(get_current_user)):
    """发送聊天消息，使用 SSE 流式返回 Agent 响应。"""
    if not request.thread_id:
        request.thread_id = str(uuid.uuid4())

    # messages.thread_id 上有外键约束，缺行时插入会触发 FOREIGN KEY constraint failed，
    # 所以每条消息都要确保 sessions 里有对应行。
    # 区别在于：会话已存在时必须校验归属，不能让 A 拿着 B 的 thread_id 往里写；
    # 会话不存在才创建，归属当前登录用户（不再硬编码 "default"）。
    existing = db.get_session(request.thread_id)
    if existing:
        if existing.get("user_id") != current_user["id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权访问该会话",
            )
    else:
        db.create_session(request.thread_id, current_user["id"], "新对话")

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
    workflow = await get_workflow()

    # 图里真正的节点名。条件边的路由函数（intent_router / route_after_format）
    # 在 astream_events 里同样会触发 on_chain_start/end，但它们不是节点，
    # 漏进思考链就变成一行"正在执行 route_after_format..."的英文噪音。
    node_names = set(workflow.get_graph().nodes) - {"__start__", "__end__"}

    # 正文判定：按"这一次调用的 token 是不是给用户看的"来判，而不是按节点名。
    # 节点内部的辅助调用（景点提取、结构化抽取）事件同样归属该节点名，靠节点名
    # 过滤必然漏——之前景点提取的 {"attractions": [...]} 就是这么漏进正文并落库的。
    # 现在两层：
    #   1. 生成侧给面向用户的调用打 user_visible，给内部调用打 internal；
    #   2. 保留旧的节点名白名单作兼容（还没打标记的老代码不会突然丢正文）。
    LEGACY_TEXT_NODES = ("generate_plan", "chat")

    # 返回 thread_id
    yield f"data: {json.dumps({'thread_id': thread_id})}\n\n"

    try:
        # 获取历史消息
        history_messages = db.get_messages(thread_id)
        db.add_message(thread_id, "user", message)
        print(f"💾 保存用户消息: {thread_id} - {message[:20]}...")

        # messages 通道带 add_messages reducer，图输入是"追加"而不是"覆盖"。
        # 本项目的对话历史每轮都从 SQLite 重建，而 checkpoint 里也存着上一轮的 messages，
        # 不先清空就会叠加成 [H1,A1,H1,A1,H2,...]，历史翻倍、token 翻倍。
        # RemoveMessage(REMOVE_ALL_MESSAGES) 让 reducer 丢弃 checkpoint 里的旧消息，
        # 只保留本次传入的完整历史——SQLite 仍是唯一事实来源。
        messages = [RemoveMessage(id=REMOVE_ALL_MESSAGES)]
        for msg in history_messages:
            if msg.get("role") == "user":
                messages.append(HumanMessage(content=msg.get("content")))
            elif msg.get("role") == "assistant":
                messages.append(AIMessage(content=msg.get("content")))
        messages.append(HumanMessage(content=message))

        initial_state: TripState = TripState(
            user_input=message,
            thread_id=thread_id,
            messages=messages,
            start_date=date.today().isoformat()  # YYYY-MM-DD
        )

        full_response = ""  # 用于 SSE 流式输出（保持原样）
        save_content = ""  # ✅ 用于保存到数据库的内容（只累积 generate_plan 的输出）
        map_data_to_save = None
        has_trip_plan = False
        emitted_errors = set()  # 同一条 error 只推一次（节点返回整份 state，会重复出现）
        clarify_sent = False  # clarify 只推一次（同上，整份 state 回传可能重入）

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
                # 只有这两个节点的模型输出是给用户看的正文：
                #   generate_plan —— 行程 JSON；chat —— 闲聊回复。
                # format_input 这类节点内部也在调模型做结构化抽取，不过滤的话它吐的原始
                # JSON 会被当成正文推给前端。行程场景下会被卡片盖住看不出来，但澄清场景
                # 没有卡片覆盖，那段 {"destination": "", "days": null} 会直接留在界面上。
                node = (event.get("metadata") or {}).get("langgraph_node")
                tags = set(event.get("tags") or [])
                is_visible = "internal" not in tags and (
                    "user_visible" in tags or node in LEGACY_TEXT_NODES
                )
                if is_visible and chunk and hasattr(chunk, 'content') and chunk.content:
                    content = chunk.content
                    full_response += content

                    # ✅ 只把 generate_plan 节点的输出存到 save_content
                    if node == "generate_plan":
                        save_content += content

                    yield f"data: {json.dumps({'type': 'chunk', 'content': content}, ensure_ascii=False)}\n\n"

            # ✅ 处理节点开始
            elif kind == "on_chain_start":
                node_name = event.get("name", "unknown")
                if node_name in node_names:
                    yield f"data: {json.dumps({'type': 'node_start', 'node': node_name, 'message': f'正在执行 {node_name}...'}, ensure_ascii=False)}\n\n"

            # ✅ 处理节点结束
            elif kind == "on_chain_end":
                node_name = event.get("name", "unknown")
                if node_name in node_names:
                    output = event["data"].get("output", {})

                    # 节点把失败写进 state 的错误键。之前三个并行检索节点的 except 只
                    # print 后隐式返回 None，LangGraph 把它当"空更新"直接放过，用户只会
                    # 拿到一份"少了攻略/地图"的行程，不知道哪里失败了。
                    # 这里转成 warning 事件：前端在思考链里把该节点标红，但不打断正文。
                    # 三个并行节点各用自己的键（同一步写同一个 key 会 InvalidUpdateError）。
                    if isinstance(output, dict):
                        for key in ("error", "guides_error", "attractions_error", "weather_error"):
                            err = output.get(key)
                            if err and err not in emitted_errors:
                                emitted_errors.add(err)
                                yield f"data: {json.dumps({'type': 'warning', 'node': node_name, 'message': err}, ensure_ascii=False)}\n\n"

                    # clarify 走的是模板话术，没有 LLM 调用，因此不会产生
                    # on_chat_model_stream / chunk 事件。这里补发一条 message 事件，
                    # 否则前端一个字都收不到；同时计入 full_response，
                    # 否则下面 content_to_save 为空，这条追问不会落库、刷新就消失。
                    if node_name == "clarify" and not clarify_sent and isinstance(output, dict):
                        question = output.get("clarify_question") or output.get("response")
                        if question:
                            clarify_sent = True
                            full_response += question
                            yield f"data: {json.dumps({'type': 'message', 'content': question}, ensure_ascii=False)}\n\n"

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
