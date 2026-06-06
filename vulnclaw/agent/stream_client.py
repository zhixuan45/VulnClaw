"""流式 LLM 调用核心 — 逐 chunk 解析 + 事件回调."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any

from vulnclaw.agent.llm_client import build_chat_completion_kwargs, extract_response
from vulnclaw.agent.stream_events import StreamCallback, StreamEvent, StreamEventType
from vulnclaw.agent.tool_call_manager import handle_tool_calls, handle_tool_calls_with_results

_THINK_TAG_RE = re.compile(r"</?(think|thinking|reasoning)>", re.IGNORECASE)


@dataclass
class _StreamAccumulator:
    """Chunk 解析过程中的累积状态."""

    full_text: str = ""
    reasoning_text: str = ""
    current_tool_calls: list[dict] = field(default_factory=list)
    # tool_call index -> accumulated arguments string
    tool_args_buf: dict[int, str] = field(default_factory=dict)
    phase: str = "idle"  # idle | thinking | text | tool
    # 追踪增量 thinking 标签状态
    _inside_think_tag: bool = False
    # 已发送过 TOOL_CALL_END 的 tool call index 集合
    _args_ended: set[int] = field(default_factory=set)


# ── thinking 标签增量检测 ──────────────────────────────────────────

def _detect_think_open(content: str, acc: _StreamAccumulator) -> tuple[str, bool]:
    """检测 content 中是否以 <think> 标签开始（可能跨 chunk）."""
    lower = content.lower()
    for tag in ("<think>", "<thinking>", "<reasoning>"):
        if tag in lower and not acc._inside_think_tag:
            idx = lower.index(tag)
            after = content[idx + len(tag) :]
            acc._inside_think_tag = True
            return after, True
    return content, False


def _detect_think_close(content: str, acc: _StreamAccumulator) -> tuple[str, bool]:
    """检测 content 中是否包含 </think> 闭合标签."""
    lower = content.lower()
    for tag in ("</think>", "</thinking>", "</reasoning>"):
        if tag in lower and acc._inside_think_tag:
            idx = lower.index(tag)
            before = content[:idx]
            after = content[idx + len(tag) :]
            acc._inside_think_tag = False
            return before, True, after
    return content, False, ""


# ── 工具调用 delta 处理 ────────────────────────────────────────────

async def _handle_tool_delta(
    tool_calls_delta: list,
    acc: _StreamAccumulator,
    on_event: StreamCallback,
    round_num: int,
    cycle_num: int,
) -> None:
    """处理增量 tool_calls delta."""
    for tc_delta in tool_calls_delta:
        idx = getattr(tc_delta, "index", 0)

        if idx not in acc.tool_args_buf:
            acc.tool_args_buf[idx] = ""
            acc.current_tool_calls.append(
                {"id": "", "function": {"name": "", "arguments": ""}}
            )
            await on_event(
                StreamEvent(
                    StreamEventType.TOOL_CALL_START,
                    round_num=round_num,
                    cycle_num=cycle_num,
                )
            )

        tc = acc.current_tool_calls[idx]

        # 累积 id
        if getattr(tc_delta, "id", "") and not tc["id"]:
            tc["id"] = tc_delta.id

        # 工具名
        if tc_delta.function and getattr(tc_delta.function, "name", ""):
            tc["function"]["name"] = tc_delta.function.name
            await on_event(
                StreamEvent(
                    StreamEventType.TOOL_CALL_NAME,
                    content=tc_delta.function.name,
                    round_num=round_num,
                    cycle_num=cycle_num,
                    metadata={"tool_call_id": tc["id"]},
                )
            )

        # 参数累积
        if tc_delta.function and getattr(tc_delta.function, "arguments", ""):
            fragment = tc_delta.function.arguments
            acc.tool_args_buf[idx] += fragment
            tc["function"]["arguments"] = acc.tool_args_buf[idx]
            await on_event(
                StreamEvent(
                    StreamEventType.TOOL_CALL_ARGS,
                    content=fragment,
                    round_num=round_num,
                    cycle_num=cycle_num,
                    metadata={"tool_call_id": tc["id"]},
                )
            )


def _mark_tool_calls_complete(acc: _StreamAccumulator) -> None:
    """标记所有尚未完成的 tool call 参数累积完成."""
    for idx in acc.tool_args_buf:
        tc = acc.current_tool_calls[idx]
        tc["function"]["arguments"] = acc.tool_args_buf[idx]


# ── 核心流式响应函数 ───────────────────────────────────────────────

async def _stream_response(
    client,
    kwargs: dict,
    on_event: StreamCallback,
    round_num: int = 0,
    cycle_num: int = 0,
    provider: str = "",
    model: str = "",
) -> tuple[str, list]:
    """流式调用 API，逐 chunk 解析并回调 on_event。

    Returns:
        (累积完整文本, 原始 tool_calls 列表)
    """
    acc = _StreamAccumulator()

    kwargs = dict(kwargs)  # 浅拷贝避免污染调用方
    kwargs["stream"] = True
    kwargs["stream_options"] = {"include_usage": True}

    stream = client.chat.completions.create(**kwargs)

    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        # 1. DeepSeek reasoning_content（delta 顶层字段）
        reasoning = getattr(delta, "reasoning_content", None) or ""
        if reasoning:
            if acc.phase == "text":
                await on_event(
                    StreamEvent(
                        StreamEventType.TEXT_END,
                        round_num=round_num,
                        cycle_num=cycle_num,
                    )
                )
            if acc.phase != "thinking":
                acc.phase = "thinking"
                await on_event(
                    StreamEvent(
                        StreamEventType.THINKING_START,
                        round_num=round_num,
                        cycle_num=cycle_num,
                    )
                )
            await on_event(
                StreamEvent(
                    StreamEventType.THINKING_TOKEN,
                    content=reasoning,
                    round_num=round_num,
                    cycle_num=cycle_num,
                    metadata={"is_reasoning": True},
                )
            )
            acc.reasoning_text += reasoning
            continue

        # 2. 标准 content
        content = delta.content or ""
        if content:
            # 检测 </thinking> 闭合标签（如果之前在 think 内部）
            if acc._inside_think_tag:
                before, closed, after = _detect_think_close(content, acc)
                if before:
                    await on_event(
                        StreamEvent(
                            StreamEventType.THINKING_TOKEN,
                            content=before,
                            round_num=round_num,
                            cycle_num=cycle_num,
                        )
                    )
                    acc.reasoning_text += before
                if closed:
                    acc.phase = "idle"
                    acc._inside_think_tag = False
                    await on_event(
                        StreamEvent(
                            StreamEventType.THINKING_END,
                            round_num=round_num,
                            cycle_num=cycle_num,
                        )
                    )
                    # 闭合标签后的内容归为正文
                    content = after
                    if not content:
                        acc.full_text += before
                        continue
                else:
                    acc.full_text += content
                    continue

            # 检测 <thinking> 开始标签
            cleaned, opened = _detect_think_open(content, acc)
            if opened:
                if acc.phase == "text":
                    await on_event(
                        StreamEvent(
                            StreamEventType.TEXT_END,
                            round_num=round_num,
                            cycle_num=cycle_num,
                        )
                    )
                acc.phase = "thinking"
                await on_event(
                    StreamEvent(
                        StreamEventType.THINKING_START,
                        round_num=round_num,
                        cycle_num=cycle_num,
                    )
                )
                if cleaned:
                    await on_event(
                        StreamEvent(
                            StreamEventType.THINKING_TOKEN,
                            content=cleaned,
                            round_num=round_num,
                            cycle_num=cycle_num,
                        )
                    )
                    acc.reasoning_text += cleaned
                acc.full_text += content
                continue

            # 普通 content token
            if acc.phase != "text":
                if acc.phase == "thinking":
                    await on_event(
                        StreamEvent(
                            StreamEventType.THINKING_END,
                            round_num=round_num,
                            cycle_num=cycle_num,
                        )
                    )
                acc.phase = "text"
                await on_event(
                    StreamEvent(
                        StreamEventType.TEXT_START,
                        round_num=round_num,
                        cycle_num=cycle_num,
                    )
                )
            await on_event(
                StreamEvent(
                    StreamEventType.TEXT_TOKEN,
                    content=content,
                    round_num=round_num,
                    cycle_num=cycle_num,
                )
            )
            acc.full_text += content
            continue

        # 3. tool_calls（增量 delta）
        if delta.tool_calls:
            if acc.phase == "thinking":
                await on_event(
                    StreamEvent(
                        StreamEventType.THINKING_END,
                        round_num=round_num,
                        cycle_num=cycle_num,
                    )
                )
            elif acc.phase == "text":
                await on_event(
                    StreamEvent(
                        StreamEventType.TEXT_END,
                        round_num=round_num,
                        cycle_num=cycle_num,
                    )
                )
            acc.phase = "tool"
            await _handle_tool_delta(
                delta.tool_calls, acc, on_event, round_num, cycle_num
            )

        # 4. 检查 finish_reason 标记 tool_calls 完成
        finish_reason = getattr(chunk.choices[0], "finish_reason", "")
        if finish_reason == "tool_calls":
            _mark_tool_calls_complete(acc)
            for idx in acc.tool_args_buf:
                if idx not in acc._args_ended:
                    tc = acc.current_tool_calls[idx]
                    await on_event(
                        StreamEvent(
                            StreamEventType.TOOL_CALL_END,
                            round_num=round_num,
                            cycle_num=cycle_num,
                            metadata={
                                "tool_call_id": tc["id"],
                                "tool_name": tc["function"]["name"],
                            },
                        )
                    )
                    acc._args_ended.add(idx)

    # 收尾：确保各阶段正确关闭
    if acc.phase == "thinking":
        await on_event(
            StreamEvent(
                StreamEventType.THINKING_END,
                round_num=round_num,
                cycle_num=cycle_num,
            )
        )
    elif acc.phase == "text":
        await on_event(
            StreamEvent(
                StreamEventType.TEXT_END,
                round_num=round_num,
                cycle_num=cycle_num,
            )
        )

    return acc.full_text, acc.current_tool_calls


# ── 流式重试包装器 ──────────────────────────────────────────────────

async def _stream_with_retries(
    client,
    kwargs: dict,
    on_event: StreamCallback,
    round_num: int,
    cycle_num: int,
    stage_label: str,
    max_retries: int = 3,
) -> tuple[str, list]:
    """带重试的流式调用包装器."""
    for attempt in range(max_retries + 1):
        try:
            return await _stream_response(
                client, kwargs, on_event, round_num, cycle_num
            )
        except Exception as exc:
            from vulnclaw.agent.llm_client import _is_non_retriable_llm_error

            error_text = str(exc).lower()
            if _is_non_retriable_llm_error(error_text) or attempt >= max_retries:
                raise
            wait_s = 5 * (attempt + 1)
            msg = f"\n[!] {stage_label} 流式连接中断，第 {attempt + 1} 次重试... ({wait_s}s)\n"
            await on_event(
                StreamEvent(
                    StreamEventType.TEXT_TOKEN,
                    content=msg,
                    round_num=round_num,
                    cycle_num=cycle_num,
                )
            )
            await asyncio.sleep(wait_s)
    # 不应到达，但让类型检查满意
    raise RuntimeError("unreachable")


# ── 公开的流式调用入口 ──────────────────────────────────────────────

async def call_llm_stream(
    agent,
    system_prompt: str,
    on_event: StreamCallback,
) -> str:
    """单轮流式调用。"""
    client = agent._get_client()
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(agent.context.get_messages())
    tools = agent._build_openai_tools()

    kwargs = build_chat_completion_kwargs(agent, messages, tools)

    full_text, tool_calls_data = await _stream_with_retries(
        client,
        kwargs,
        on_event,
        round_num=0,
        cycle_num=0,
        stage_label="单轮流式",
    )

    if tool_calls_data:
        # 需要构建一个虚拟的 message 对象来复用 handle_tool_calls
        mock_msg = _build_mock_message(tool_calls_data)
        tool_result_text = await handle_tool_calls(agent, mock_msg)
        full_text = tool_result_text if not full_text else full_text

    return full_text


async def call_llm_auto_stream(
    agent,
    system_prompt: str,
    round_context: str,
    on_event: StreamCallback,
    round_num: int = 0,
    cycle_num: int = 0,
) -> str:
    """自主渗透流式调用，含工具执行和二次 LLM 调用。"""
    client = agent._get_client()

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages.extend(agent.context.get_messages())
    messages.append({"role": "user", "content": round_context})
    tools = agent._build_openai_tools()

    kwargs = build_chat_completion_kwargs(agent, messages, tools)

    # 第一次流式调用
    full_text, tool_calls_data = await _stream_with_retries(
        client,
        kwargs,
        on_event,
        round_num,
        cycle_num,
        stage_label="自主循环流式",
    )

    if not tool_calls_data:
        return full_text

    # 执行工具并流式推送结果
    mock_msg = _build_mock_message(tool_calls_data)
    tool_results, skipped_info = await handle_tool_calls_with_results(agent, mock_msg)

    for tr in tool_results:
        content = tr.get("content", "") if isinstance(tr, dict) else str(tr)
        tool_name_val = ""
        tc_id = ""
        if isinstance(tr, dict):
            tc_id = tr.get("tool_call_id", "")
            tc_obj = tr.get("tool_call", None)
            if tc_obj and hasattr(tc_obj, "function"):
                tool_name_val = getattr(tc_obj.function, "name", "")
        await on_event(
            StreamEvent(
                StreamEventType.TOOL_RESULT,
                content=content[:2000],
                round_num=round_num,
                cycle_num=cycle_num,
                metadata={
                    "tool_name": tool_name_val,
                    "tool_call_id": tc_id,
                },
            )
        )

    # 构建 assistant 消息和 tool 结果消息
    executed_tcs = []
    for tr in tool_results:
        if isinstance(tr, dict) and "tool_call" in tr:
            executed_tcs.append(tr["tool_call"])

    assistant_msg: dict = {
        "role": "assistant",
        "content": full_text or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in executed_tcs
        ],
    }
    messages.append(assistant_msg)

    for tr in tool_results:
        if isinstance(tr, dict) and "tool_call_id" in tr:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": tr.get("content", ""),
                }
            )

    # 第二次流式调用（工具结果总结）
    kwargs["messages"] = messages
    full_text2, _ = await _stream_with_retries(
        client,
        kwargs,
        on_event,
        round_num,
        cycle_num,
        stage_label="工具总结流式",
    )

    return full_text2


# ── 辅助 ────────────────────────────────────────────────────────────


def _build_mock_message(tool_calls_data: list[dict]) -> Any:
    """从流式累积的 tool_calls 数据构建一个类似 OpenAI message 的对象."""

    class _MockFunc:
        def __init__(self, d: dict):
            self.name = d["function"]["name"]
            self.arguments = d["function"]["arguments"]

    class _MockTC:
        def __init__(self, d: dict):
            self.id = d["id"]
            self.function = _MockFunc(d)

    class _MockMsg:
        def __init__(self, tcs: list[dict]):
            self.tool_calls = [_MockTC(tc) for tc in tcs if tc["function"]["name"]]
            self.content = ""

    return _MockMsg(tool_calls_data)
