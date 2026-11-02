import asyncio
import uuid
import json
from datetime import date
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.sse import EventSourceResponse
from langchain_core.messages import HumanMessage, AIMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import Command
from app.core.langgraph_app import get_workflow
from app.core.security import get_current_user
from app.core.state import TripState
from app.models.chat import ChatRequest, ResumeRequest
from app.services.database import db

router = APIRouter()

# 三个接口共用：Nginx 会缓冲响应，不关掉 SSE 就变成"最后一次性吐出来"
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


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
        headers=_SSE_HEADERS,
    )


@router.post("/resume")
async def resume_chat(request: ResumeRequest, current_user: Dict = Depends(get_current_user)):
    """HITL：把确认卡上的决定送回图里，从 checkpoint 原地恢复。

    和 /stream 的区别只有"喂给图的是什么"：这里喂 Command(resume=payload)，
    前面的节点一律不重跑（中断节点本身会被重放一次，见 confirm_needs 的注释）。
    """
    existing = db.get_session(request.thread_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    if existing.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该会话")

    # 没有 pending 中断时直接 409：否则用户点两次"确认"会得到一条空流，
    # 前端看起来像"点了没反应"。
    snapshot = await _get_snapshot(request.thread_id)
    if _interrupt_value(snapshot) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="没有待确认的请求（可能已经确认过了）",
        )

    return EventSourceResponse(
        generate_sse_resume(request.thread_id, request.payload or {}),
        headers=_SSE_HEADERS,
    )


@router.get("/pending/{thread_id}")
async def get_pending(thread_id: str, current_user: Dict = Depends(get_current_user)):
    """这个会话有没有待确认的中断。

    前端切换会话 / 刷新页面后靠它把确认卡找回来，否则中断状态只存在前端内存里，
    一刷新就丢，用户只能把整句话重发一遍。
    """
    existing = db.get_session(thread_id)
    if not existing or existing.get("user_id") != current_user["id"]:
        return {"pending": None}
    snapshot = await _get_snapshot(thread_id)
    return {"pending": _interrupt_value(snapshot)}


async def _get_snapshot(thread_id: str):
    workflow = await get_workflow()
    return await workflow.aget_state({"configurable": {"thread_id": thread_id}})


def _interrupt_value(snapshot) -> Optional[dict]:
    """从 aget_state 的快照里取出待确认的载荷。

    快照里每个未完成的任务都带 interrupts，值是 interrupt() 传出去的 payload。
    """
    for task in (getattr(snapshot, "tasks", None) or ()):
        for item in (getattr(task, "interrupts", None) or ()):
            value = getattr(item, "value", None)
            if isinstance(value, dict) and value:
                return value
    return None


async def _run_stream(thread_id: str,
                      *,
                      user_message: Optional[str] = None,
                      resume_payload: Optional[dict] = None):
    """把图的事件流翻译成 SSE 帧。

    首次请求（user_message）和确认恢复（resume_payload）走同一段逻辑，
    区别只在喂给图的是什么：初始 state，还是 Command(resume=...)。
    """
    workflow = await get_workflow()

    # 图里真正的节点名。条件边的路由函数（intent_router / route_after_format /
    # route_after_confirm）在 astream_events 里同样会触发 on_chain_start/end，
    # 但它们不是节点，漏进思考链就变成一行"正在执行 route_after_format..."的英文噪音。
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

    # 这些变量在 try 外面初始化：断开时 except 分支要读它们打日志，
    # 如果异常发生在 try 的前几行（比如历史消息读库失败），放在里面就是 NameError。
    full_response = ""
    save_content = ""
    interrupt_payload: Optional[dict] = None

    try:
        if resume_payload is not None:
            # 恢复：图从 checkpoint 里 pending 的那个节点继续
            graph_input = Command(resume=resume_payload)
            print(f"▶️ 恢复中断: {thread_id} - {resume_payload}")
        else:
            # 获取历史消息
            history_messages = db.get_messages(thread_id)
            db.add_message(thread_id, "user", user_message or "")
            print(f"💾 保存用户消息: {thread_id} - {(user_message or '')[:20]}...")

            # messages 通道带 add_messages reducer，图输入是"追加"而不是"覆盖"。
            # 本项目的对话历史每轮都从 SQLite 重建，而 checkpoint 里也存着上一轮的
            # messages，不先清空就会叠加成 [H1,A1,H1,A1,H2,...]，历史翻倍、token 翻倍。
            # RemoveMessage(REMOVE_ALL_MESSAGES) 让 reducer 丢弃 checkpoint 里的旧消息，
            # 只保留本次传入的完整历史——SQLite 仍是唯一事实来源。
            messages = [RemoveMessage(id=REMOVE_ALL_MESSAGES)]
            for msg in history_messages:
                if msg.get("role") == "user":
                    messages.append(HumanMessage(content=msg.get("content")))
                elif msg.get("role") == "assistant":
                    messages.append(AIMessage(content=msg.get("content")))
            messages.append(HumanMessage(content=user_message or ""))

            graph_input = TripState(
                user_input=user_message or "",
                thread_id=thread_id,
                messages=messages,
                start_date=date.today().isoformat(),  # YYYY-MM-DD
            )

        map_data_to_save = None
        has_trip_plan = False
        emitted_errors = set()  # 同一条 error 只推一次（节点返回整份 state，会重复出现）
        text_sent = False  # clarify / cancelled 的模板话术只补发一次
        config = {"configurable": {"thread_id": thread_id}}

        async for event in workflow.astream_events(graph_input, config=config, version="v2"):
            kind = event["event"]

            # ✅ 处理 LLM 流式输出
            if kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
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
                    if isinstance(output, dict):
                        for key in ("error", "guides_error", "attractions_error",
                                    "weather_error", "review_error"):
                            err = output.get(key)
                            if err and err not in emitted_errors:
                                emitted_errors.add(err)
                                yield f"data: {json.dumps({'type': 'warning', 'node': node_name, 'message': err}, ensure_ascii=False)}\n\n"

                    # clarify / cancelled 走的是模板话术，没有 LLM 调用，因此不会产生
                    # on_chat_model_stream / chunk 事件。这里补发一条 message 事件，
                    # 否则前端一个字都收不到；同时计入 full_response，
                    # 否则下面 content_to_save 为空，这条回复不会落库、刷新就消失。
                    if node_name in ("clarify", "cancelled") and not text_sent and isinstance(output, dict):
                        text = output.get("clarify_question") or output.get("response")
                        if text:
                            text_sent = True
                            full_response += text
                            yield f"data: {json.dumps({'type': 'message', 'content': text}, ensure_ascii=False)}\n\n"

                    # 质检没过、准备带着 feedback 重排：让前端把上一版正文清掉。
                    # 第二版的 token 走的是同一个流，不清就会在气泡里拼成两份行程；
                    # 服务端这两个累加器也要一起归零，否则落库内容是"坏的一版 + 好的一版"。
                    if (node_name == "review_plan" and isinstance(output, dict)
                            and not output.get("review_passed")):
                        issues = output.get("review_issues") or []
                        full_response = ""
                        save_content = ""
                        yield f"data: {json.dumps({'type': 'review', 'issues': issues, 'message': '；'.join(issues) or '行程与你的要求有出入'}, ensure_ascii=False)}\n\n"

                    # ✅ 行程卡片 + 地图：都在 generate_map 收口（地图只这一次）
                    if node_name == "generate_map" and not has_trip_plan:
                        if isinstance(output, dict):
                            draft_plan = output.get("draft_plan")
                            map_data = output.get("map_data")

                            print(f"🔍 generate_map 完成: draft_plan={bool(draft_plan)}, map_data={bool(map_data)}")
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
                                    "tips": output.get("plan_tips", [])
                                }
                                print(f"📤 发送 trip_plan: map_data={bool(plan.get('map_data'))}")
                                yield f"data: {json.dumps({'type': 'trip_plan', 'plan': plan}, ensure_ascii=False)}\n\n"

                    yield f"data: {json.dumps({'type': 'node_end', 'node': node_name, 'message': f'{node_name} 完成'}, ensure_ascii=False)}\n\n"

            # ✅ 中断：顶层图的 chunk 里带 __interrupt__（值是 Interrupt 对象元组）
            elif kind == "on_chain_stream":
                chunk = event["data"].get("chunk")
                if isinstance(chunk, dict) and "__interrupt__" in chunk:
                    for item in (chunk.get("__interrupt__") or ()):
                        value = getattr(item, "value", None)
                        if isinstance(value, dict) and value:
                            interrupt_payload = value

        # 兜底：某些版本/路径下事件里拿不到，就直接问 checkpoint 有没有 pending 中断
        if interrupt_payload is None:
            try:
                interrupt_payload = _interrupt_value(await _get_snapshot(thread_id))
            except Exception as e:
                print(f"⚠️ 读取中断状态失败: {e}")

        if interrupt_payload is not None:
            # 中断这一轮没有正文：不落库、不发 done。
            # 前端渲染确认卡，点按钮后走 /resume 从 checkpoint 原地恢复。
            print(f"⏸️ 等待用户确认: {thread_id} -> {interrupt_payload}")
            yield f"data: {json.dumps({'type': 'interrupt', 'payload': interrupt_payload}, ensure_ascii=False)}\n\n"
            return

        # ✅ 保存 AI 回复：只保存 generate_plan 的输出（纯净的行程 JSON）
        content_to_save = save_content.strip() if save_content.strip() else full_response

        if content_to_save:
            db.add_message(thread_id, "assistant", content_to_save, map_data=map_data_to_save)
            print(f"💾 保存 AI 回复（{len(content_to_save)} 字符）: {content_to_save[:80]}...")

        # 更新会话名称（只有首次请求带 user_message，resume 时不需要）
        if user_message is not None:
            session = db.get_session(thread_id)
            if session and session.get("name") == "新对话":
                title = user_message[:10] + ("..." if len(user_message) > 20 else "")
                db.update_session_name(thread_id, title)
                print(f"📝 更新会话名称: {thread_id} - {title}")

        yield f"data: {json.dumps({'type': 'done', 'done': True})}\n\n"

    except asyncio.CancelledError:
        # 客户端断开时（点「停止」、关页面、切会话）Starlette 的 StreamingResponse
        # 会取消这个生成器，CancelledError 从 yield 或 await 处抛出来。
        #
        # 这里刻意不落库：此刻 save_content 大概率是半截行程 JSON，
        # 存下来之后刷新页面就是一屏解析不了的原始文本，比没有更糟。
        # 用户消息在开头已经存了，历史里不会丢——重发一次即可。
        print(f"⏹️ 客户端断开，已停止生成: {thread_id}"
              f"（已推送 {len(full_response)} 字符，未落库）")
        raise
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        yield f"data: {json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"


async def generate_sse_input(thread_id: str, message: str):
    """首次请求：写用户消息 + 从 SQLite 重建历史，然后跑图。"""
    async for frame in _run_stream(thread_id, user_message=message):
        yield frame


async def generate_sse_resume(thread_id: str, payload: dict):
    """确认恢复：不重建历史、不动 messages，直接 Command(resume=...)。"""
    async for frame in _run_stream(thread_id, resume_payload=payload):
        yield frame
