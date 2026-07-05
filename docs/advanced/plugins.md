# 插件系统

Openmim 拥有强大的插件系统，几乎所有工具功能都通过插件实现。你也可以开发自定义插件来扩展机器人能力。

## 内置插件

| 插件名 | 功能 |
|--------|------|
| `focus` | 聚焦系统工具 |
| `memory` | 记忆管理工具 |
| `sandbox` | 代码沙箱（run_python、run_shell）|
| `web` | 联网搜索（search_web）|
| `image` | 图片生成（generate_image）|
| `scheduler` | 计划任务（create_task 等）|
| `skills` | Skills 市场工具 |
| `web_panel` | Web 管理面板 HTTP 服务 |
| `topic` | 话题追踪工具 |
| `file` | 文件处理 |
| `monomodal` | 单模态处理 |
| `custom_emoji` | 自定义 Emoji |
| `history_guess` | 看图猜时代插件 |

## 禁用插件

在配置文件中按插件名精确禁用：

```json
{
  "PLUGINS_DISABLED": ["sandbox", "web_panel"]
}
```

## 插件 Hook 体系

插件通过继承 `BotPlugin` 基类并实现对应的 Hook 方法来工作。

### 可用 Hook

| Hook 方法 | 触发时机 | 说明 |
|-----------|----------|------|
| `on_startup` | Bot 启动时 | 初始化资源、注册 HTTP 服务等 |
| `on_shutdown` | Bot 停止时 | 释放资源、关闭连接等 |
| `on_message` | 收到消息时 | 可拦截或修改消息处理流程 |
| `before_build_messages` | 构建 LLM 上下文前 | 向 prompt 注入额外信息 |
| `after_build_messages` | 构建 LLM 上下文后 | 修改最终的 prompt |
| `before_reply` | 发送回复前 | 处理回复分段、贴纸等 |
| `enrich_outgoing_text` | 处理输出文本 | 替换/丰富输出内容（如自定义 Emoji）|

## 开发自定义插件

### 基本结构

在 `plugins/builtin/` 下创建新文件，或在其他位置创建并通过导入注册。

```python
from plugins.base import (
    BotPlugin, ToolSpec, ToolContext,
    MessageHookContext, MessageHookResult,
    StartupContext
)

class MyPlugin(BotPlugin):
    name = "my_plugin"
    priority = 100  # 数字越小优先级越高

    # 定义工具
    tools = (
        ToolSpec(
            name="my_tool",
            definition={
                "type": "function",
                "function": {
                    "name": "my_tool",
                    "description": "我的工具描述",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "input": {
                                "type": "string",
                                "description": "输入参数"
                            }
                        },
                        "required": ["input"]
                    }
                }
            },
            executor=_my_tool_executor,
        ),
    )

    async def on_startup(self, ctx: StartupContext) -> None:
        ctx.logger.info("我的插件已启动")

    async def on_message(self, ctx: MessageHookContext) -> MessageHookResult | None:
        # 如果不需要拦截，返回 None 或 continue
        return None


async def _my_tool_executor(args: dict, ctx: ToolContext) -> str:
    input_text = args.get("input", "")
    # 处理工具调用
    return f"工具处理结果: {input_text}"


# 导出插件实例
PLUGIN = MyPlugin()
```

### MessageHookResult 动作类型

| 动作 | 说明 |
|------|------|
| `continue` | 继续正常处理（默认）|
| `handled` | 消息已被插件处理，停止后续插件处理 |
| `drop` | 丢弃消息，不进入 LLM |
| `force_llm` | 强制进入 LLM 处理 |

### ToolContext 字段

```python
@dataclass
class ToolContext:
    chat_id: int | None       # 当前群/私聊 ID
    llm_client: Any | None    # LLM 客户端实例
    telegram_context: Any     # Telegram 上下文
    runtime_config: Any       # 运行时配置
    plugin_manager: Any       # 插件管理器
```

### MessageBuildHookContext 字段

```python
@dataclass
class MessageBuildHookContext:
    chat_id: int                          # 群组 ID
    messages: list[dict]                  # 当前消息历史
    current_message: str                  # 当前消息文本
    trigger_type: str                     # 触发类型
    stable_profile_blocks: list[str]      # 稳定 prompt 块（人设等）
    dynamic_blocks: list[str]             # 动态 prompt 块（记忆、上下文等）
    extra_user_messages: list[dict]       # 额外注入的用户消息
```

## 插件优先级

插件按 `priority` 属性排序，数字越小优先级越高（越早被调用）。

系统内置插件通常优先级为 10-50，自定义插件默认 100。

## 禁用特定工具

工具可以被禁用（不出现在 LLM 的 tool_definitions 中）：

1. **全局禁用**：通过 `PLUGINS_DISABLED` 禁用整个插件
2. **群级禁用**：在群管理面板中禁用特定工具
3. **默认禁用**：`ToolSpec(enabled_by_default=False)` — 工具注册但默认不暴露给 LLM
