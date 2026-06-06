# VulnClaw 流式输出思考过程 — 实现计划

## 背景

当前项目所有 LLM 调用都是 **阻塞模式**（`stream=False`），用户需要等待完整响应返回后才能看到输出。对于涉及长时间推理（DeepSeek-R1 的 `reasoning_content`、OpenAI o-series 的 reasoning tokens）的模型，终端会长时间没有任何反应。

目标：实现类似 Claude Code CLI 的 **实时流式思考过程显示**，包括：
- 思考阶段：流式显示 LLM 推理过程（灰色/淡化样式）
- 工具调用：实时显示工具名称和参数
- 输出阶段：流式显示最终文本响应

同时支持 CLI（Rich Live）和 Web（SSE 推送 token 事件）两种模式。

---

## 事件类型定义

新增 `vulnclaw/agent/stream_events.py`，定义流式事件体系：

```python
class StreamEventType(StrEnum):
    ROUND_START     = "round_start"
    ROUND_END       = "round_end"
    THINKING_START  = "thinking_start"
    THINKING_TOKEN  = "thinking_token"
    THINKING_END    = "thinking_end"
    TEXT_START      = "text_start"
    TEXT_TOKEN      = "text_token"
    TEXT_END        = "text_end"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_NAME  = "tool_call_name"
    TOOL_CALL_ARGS  = "tool_call_args"
    TOOL_CALL_END   = "tool_call_end"
    TOOL_RESULT     = "tool_result"

@dataclass
class StreamEvent:
    type: StreamEventType
    content: str = ""
    round_num: int = 0
    cycle_num: int = 0
    metadata: dict = field(default_factory=dict)

StreamCallback = Callable[[StreamEvent], Awaitable[None]]
```

---

## 实现步骤

### 阶段 1：流式 LLM 客户端（核心引擎）

**新增文件**：`vulnclaw/agent/stream_client.py`

职责：
- 调用 `client.chat.completions.create(stream=True, stream_options={"include_usage": True})`
- 逐 chunk 解析 delta，区分 reasoning / content / tool_call
- 通过 `StreamCallback` 异步回调每种事件
- 同时累积完整文本和工具调用数据返回给调用方

关键实现：
1. **Chunk 解析状态机**：维护 `phase`（idle/thinking/text/tool），正确切换阶段
2. **DeepSeek reasoning_content**：通过 `getattr(delta, "reasoning_content", None)` 检测
3. **`<thinking>` 标签增量检测**：兼容未使用 `reasoning_content` 字段的模型，在流式 content 中实时检测 `<thinking>` 开闭标签
4. **工具调用增量累积**：OpenAI 流式 tool_calls 是增量的，需要累积 `function.arguments` 片段
5. **带重试的流式包装器**：`_stream_with_retries()` 处理 API 连接中断

**修改文件**：`vulnclaw/agent/llm_client.py`
- 新增 `call_llm_stream()` 和 `call_llm_auto_stream()` 函数
- 将原有 `call_llm()` / `call_llm_auto()` 重命名为 `_call_llm_sync()` / `_call_llm_auto_sync()`
- 在 `call_llm()` / `call_llm_auto()` 中添加 `stream_callback` 参数，按需分流到流式/非流式路径

### 阶段 2：Agent 层透传

**修改文件**：`vulnclaw/agent/core.py`
- `chat(stream_callback=None)` — 新增参数透传到 `call_llm()`
- `auto_pentest(stream_callback=None)` — 新增参数透传到 `run_auto_pentest()`
- `persistent_pentest(stream_callback=None)` — 新增参数透传到 `run_persistent_pentest()`

**修改文件**：`vulnclaw/agent/loop_controller.py`
- `auto_pentest()` 新增 `stream_callback` 参数
- 每轮开始/结束时发送 `ROUND_START` / `ROUND_END` 事件
- 透传到 `call_llm_auto()`
- `persistent_pentest()` 同样透传

### 阶段 3：CLI 流式显示

**新增文件**：`vulnclaw/cli/stream_display.py`

基于 Rich 的 `Live` + `Layout` 实现：
```
┌─ 🤔 思考中... ──────────────────────────┐
│  我需要先扫描目标端口，然后...           │
└─────────────────────────────────────────┘
┌─ 🔧 调用工具: nmap -sV 192.168.1.1 ─────┐
│  结果: PORT 80 open - nginx 1.18...     │
└─────────────────────────────────────────┘
┌─ 📝 输出 ────────────────────────────────┐
│  扫描结果显示目标开放了 80 端口...       │
└─────────────────────────────────────────┘
```

关键实现：
- `CLIStreamDisplay` 类管理 Rich Live 上下文和 layout
- `refresh_per_second=15` 限制刷新频率
- thinking 区域根据 `show_thinking` 配置控制可见性
- 使用 dim/italic 样式显示思考过程
- 返回 `(handle_event, stop)` 元组供调用方使用

**修改文件**：`vulnclaw/cli/main.py`
- `_run_repl()` 中的单轮 chat：创建 display -> 调用 agent.chat(stream_callback=...) -> 停止 display
- `_run_auto()` 中的自主渗透：同样注入 stream_callback
- 子命令（run/recon/scan/exploit）也注入流式回调

### 阶段 4：Web SSE 流式推送

**修改文件**：`vulnclaw/web/task_manager.py`
- 新增 `publish_stream(task_id, event: StreamEvent)` 方法
- 实现 token 批处理：累积到 buffer，每 50ms 或满 10 个 token 时批量 flush
- 合并的 token 事件以 `stream_tokens` 事件名通过现有 SSE 通道推送

**修改文件**：`vulnclaw/web/services/task_service.py`
- 在 `_run_task()` / `_run_single_task()` / `_run_persistent_task()` 中创建 `stream_callback`
- 将回调透传到 `agent.chat()` / `agent.auto_pentest()` / `agent.persistent_pentest()`

**修改文件**：`vulnclaw/web/schemas.py`
- 新增 `StreamTokenPayload` Pydantic 模型

**修改文件**：`vulnclaw/web/stream.py`
- `encode_sse()` 无需大改，现有逻辑兼容新事件类型

### 阶段 5：前端实时渲染

**修改文件**：`frontend/src/types/api.ts`
- 新增 `StreamTokenEventPayload` 接口

**修改文件**：`frontend/src/api/web.ts`
- `openTaskStream()` 新增 `stream_tokens` 事件监听

**修改文件**：`frontend/src/pages/TaskConsolePage.tsx`
- 新增 `LiveStreamView` 组件，管理流式状态：
  - thinking 文本折叠/展开
  - 工具调用历史列表
  - 输出文本流式追加，自动滚动
- 前端节流：用 `requestAnimationFrame` 或 100ms 定时器批量 flush React state

**修改文件**：`frontend/src/pages/HomePage.tsx`
- 在 "Technical logs" 区域渲染 `stream_tokens` 事件的精简视图

### 阶段 6：配置支持

**修改文件**：`vulnclaw/config/schema.py`
- `LLMConfig` 新增 `stream: bool = True`（控制是否启用流式）
- `LLMConfig` 新增 `stream_token_interval_ms: int = 0`（token 推送最小间隔）

---

## 修改文件汇总

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `vulnclaw/agent/stream_events.py` | 事件类型和回调定义 |
| **新增** | `vulnclaw/agent/stream_client.py` | 流式 LLM 调用核心 |
| **新增** | `vulnclaw/cli/stream_display.py` | Rich Live 流式显示 |
| **新增** | `tests/test_stream_client.py` | 流式客户端测试 |
| **修改** | `vulnclaw/agent/llm_client.py` | 添加 stream 参数分流 |
| **修改** | `vulnclaw/agent/core.py` | 透传 stream_callback |
| **修改** | `vulnclaw/agent/loop_controller.py` | 发送 ROUND_START/END + 透传 |
| **修改** | `vulnclaw/cli/main.py` | 注入 Rich 流式回调 |
| **修改** | `vulnclaw/cli/tui.py` | TUI 模式集成流式回调 |
| **修改** | `vulnclaw/web/task_manager.py` | 新增 publish_stream() |
| **修改** | `vulnclaw/web/services/task_service.py` | 注入 Web 流式回调 |
| **修改** | `vulnclaw/web/schemas.py` | 新增 StreamTokenPayload |
| **修改** | `vulnclaw/config/schema.py` | 新增 stream 配置项 |
| **修改** | `frontend/src/types/api.ts` | 新增前端事件类型 |
| **修改** | `frontend/src/api/web.ts` | 新增 stream_tokens 监听 |
| **修改** | `frontend/src/pages/TaskConsolePage.tsx` | 新增 LiveStreamView |
| **修改** | `frontend/src/pages/HomePage.tsx` | Technical logs 集成 |

---

## 关键设计决策

1. **向后兼容**：`stream_callback` 为可选参数，`None` 时回退到现有非流式路径
2. **事件驱动**：统一的 `StreamEvent` + `StreamCallback` 接口，CLI 和 Web 用不同实现
3. **状态隔离**：流式引擎（`stream_client.py`）不依赖 Rich 或 FastAPI，只产生事件并回调
4. **批处理**：Web 端 50ms 批处理避免 SSE 频繁推送；前端 100ms 节流避免 React 过度渲染

---

## 验证方案

1. **CLI 验证**：
   - `vulnclaw` 进入 REPL，输入简单问题观察流式输出
   - `think on` 后观察 thinking 内容流式显示
   - `vulnclaw run --target <目标>` 观察自主渗透中的工具调用实时显示
2. **Web 验证**：
   - `vulnclaw web` 启动 Web UI
   - 创建任务后在 Task Console 页面观察实时流式 token 渲染
   - 验证 thinking 折叠/展开功能
3. **边界测试**：
   - 网络中断时观察重试逻辑
   - DeepSeek R1（reasoning_content）和 OpenAI o-series 两种 reasoning 模式
   - `stream=False` 时确认降级到原有非流式行为
4. **单元测试**：
   - Mock OpenAI stream chunks 验证事件顺序和内容正确性
   - `_StreamAccumulator` 状态转换正确性
