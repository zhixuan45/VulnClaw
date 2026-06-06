#!/usr/bin/env python3
"""流式输出快速测试脚本 — 直接启动 CLI 测试流式 LLM 响应.

用法:
    python scripts/test_stream.py
    python scripts/test_stream.py --prompt "扫描 127.0.0.1 的开放端口"
"""

from __future__ import annotations

import asyncio
import sys
import os

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.text import Text

from vulnclaw.config.settings import load_config
from vulnclaw.agent.core import AgentCore
from vulnclaw.agent.stream_events import StreamEvent, StreamEventType
from vulnclaw.mcp.lifecycle import MCPLifecycleManager

console = Console()


async def _simple_stream_handler(event: StreamEvent) -> None:
    """简单的流式事件处理器 — 直接打印到控制台."""
    etype = event.type

    if etype == StreamEventType.THINKING_START:
        console.print(Text("🤔 思考中...", style="dim"))
    elif etype == StreamEventType.THINKING_TOKEN:
        console.print(event.content, end="", style="dim italic")
    elif etype == StreamEventType.THINKING_END:
        console.print()
    elif etype == StreamEventType.TEXT_START:
        console.print(Text("📝 输出:", style="bold"))
    elif etype == StreamEventType.TEXT_TOKEN:
        console.print(event.content, end="")
    elif etype == StreamEventType.TEXT_END:
        console.print()
    elif etype == StreamEventType.TOOL_CALL_START:
        console.print(Text("🔧 工具调用开始", style="yellow"))
    elif etype == StreamEventType.TOOL_CALL_NAME:
        console.print(Text(f"   工具: {event.content}", style="yellow"))
    elif etype == StreamEventType.TOOL_CALL_ARGS:
        console.print(event.content, end="", style="yellow dim")
    elif etype == StreamEventType.TOOL_CALL_END:
        console.print()
    elif etype == StreamEventType.TOOL_RESULT:
        result = event.content[:500]
        console.print(Text(f"   结果: {result}...", style="green"))
    elif etype == StreamEventType.ROUND_START:
        console.print(Text(f"\n--- Round {event.round_num} ---", style="cyan"))
    elif etype == StreamEventType.ROUND_END:
        console.print(Text(f"--- Round {event.round_num} 结束 ---\n", style="cyan"))


async def _run_test(prompt: str) -> None:
    """运行流式测试."""
    console.print(Text("VulnClaw 流式输出测试", style="bold green"))
    console.print(Text(f"Prompt: {prompt}\n", style="dim"))

    config = load_config()
    console.print(
        Text(
            f"Provider: {config.llm.provider} | Model: {config.llm.model}",
            style="dim",
        )
    )

    mcp_manager = MCPLifecycleManager(config)
    mcp_manager.start_enabled_servers()
    agent = AgentCore(config, mcp_manager)

    try:
        result = await agent.chat(prompt, stream_callback=_simple_stream_handler)
        console.print(Text(f"\n✅ 完成", style="bold green"))
        if result.output:
            # 如果流式已经完整打印过，这里只显示首行摘要
            preview = result.output[:200].replace("\n", " ")
            console.print(Text(f"响应摘要: {preview}...", style="dim"))
    except Exception as e:
        console.print(Text(f"\n❌ 错误: {e}", style="bold red"))
    finally:
        mcp_manager.stop_all()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="VulnClaw 流式输出测试")
    parser.add_argument(
        "--prompt",
        "-p",
        default="你好，请简单介绍一下你自己，你有哪些能力？",
        help="测试用的 prompt 文本",
    )
    args = parser.parse_args()
    asyncio.run(_run_test(args.prompt))


if __name__ == "__main__":
    main()
