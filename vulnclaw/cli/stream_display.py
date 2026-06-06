"""Rich 驱动的 CLI 流式输出显示器."""

from __future__ import annotations

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.layout import Layout

from vulnclaw.agent.stream_events import StreamCallback, StreamEvent, StreamEventType


class CLIStreamDisplay:
    """管理 Rich Live 显示，实时渲染流式事件."""

    def __init__(self, console: Console, show_thinking: bool = False):
        self._console = console
        self._show_thinking = show_thinking
        self._thinking_lines: list[str] = []
        self._output_lines: list[str] = []
        self._tool_lines: list[str] = []
        self._live: Live | None = None
        self._active_tool_name: str = ""
        self._active_tool_args: str = ""

    def start(self) -> None:
        self._live = Live(
            self._build_layout(),
            console=self._console,
            refresh_per_second=15,
            transient=False,
        )
        self._live.start()

    def stop(self) -> None:
        if self._live:
            self._live.stop()

    def _build_layout(self) -> Layout:
        layout = Layout()
        sections = []
        if self._show_thinking and (self._thinking_lines or self._active_tool_name):
            thinking_text = "".join(self._thinking_lines)
            if thinking_text:
                sections.append(
                    Panel(Text(thinking_text, style="dim italic"), title="🤔 思考中", border_style="dim")
                )
        if self._active_tool_name:
            tool_text = f"🔧 {self._active_tool_name}\n{self._active_tool_args}"
            for line in self._tool_lines:
                tool_text += f"\n  {line}"
            sections.append(Panel(Text(tool_text, style="yellow"), title="工具调用", border_style="yellow"))
        if self._output_lines:
            output_text = "".join(self._output_lines)
            sections.append(Panel(Text(output_text), title="📝 输出", border_style="green"))
        if sections:
            layout.split_column(*sections)
        else:
            layout.update(Text("等待响应...", style="dim"))
        return layout

    async def handle_event(self, event: StreamEvent) -> None:
        etype = event.type
        if etype == StreamEventType.THINKING_START:
            self._thinking_lines = []
        elif etype == StreamEventType.THINKING_TOKEN:
            self._thinking_lines.append(event.content)
        elif etype == StreamEventType.THINKING_END:
            pass  # keep displayed until next phase
        elif etype == StreamEventType.TEXT_START:
            self._thinking_lines = []
            self._output_lines = []
            self._active_tool_name = ""
        elif etype == StreamEventType.TEXT_TOKEN:
            self._output_lines.append(event.content)
        elif etype == StreamEventType.TEXT_END:
            pass
        elif etype == StreamEventType.TOOL_CALL_START:
            self._active_tool_name = ""
            self._active_tool_args = ""
            self._tool_lines = []
        elif etype == StreamEventType.TOOL_CALL_NAME:
            self._active_tool_name = event.content
        elif etype == StreamEventType.TOOL_CALL_ARGS:
            self._active_tool_args += event.content
        elif etype == StreamEventType.TOOL_RESULT:
            self._tool_lines.append(f"结果: {event.content[:300]}")
        elif etype == StreamEventType.ROUND_START:
            self._thinking_lines = []
            self._output_lines = []
            self._active_tool_name = ""
        if self._live:
            self._live.update(self._build_layout())


def create_cli_stream_handler(console: Console, show_thinking: bool):
    """工厂函数：创建 CLI 流式处理器，返回 (handler, stop_fn)."""
    display = CLIStreamDisplay(console, show_thinking)
    display.start()
    return display.handle_event, display.stop
