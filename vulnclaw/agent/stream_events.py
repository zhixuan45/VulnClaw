"""流式输出事件类型定义 — CLI 和 Web 共享的事件体系."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Awaitable, Callable


class StreamEventType(StrEnum):
    # 生命周期
    ROUND_START = "round_start"
    ROUND_END = "round_end"

    # Thinking / Reasoning
    THINKING_START = "thinking_start"
    THINKING_TOKEN = "thinking_token"
    THINKING_END = "thinking_end"

    # 最终文本输出
    TEXT_START = "text_start"
    TEXT_TOKEN = "text_token"
    TEXT_END = "text_end"

    # 工具调用
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_NAME = "tool_call_name"
    TOOL_CALL_ARGS = "tool_call_args"
    TOOL_CALL_END = "tool_call_end"
    TOOL_RESULT = "tool_result"


@dataclass
class StreamEvent:
    """流式事件，由 stream_client 产生，由 CLI/Web 回调消费."""

    type: StreamEventType
    content: str = ""
    round_num: int = 0
    cycle_num: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


StreamCallback = Callable[[StreamEvent], Awaitable[None]]
