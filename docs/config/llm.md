# LLM 配置

Openmim 支持三种 LLM API 协议：OpenAI 兼容 Chat Completions、OpenAI Responses API 和 Anthropic Messages API。三种协议均支持普通对话、流式文本、图片输入、工具调用和 token 用量记录。

## 基础配置

```json
{
  "LLM_PROVIDER": "openai_responses",
  "LLM_API_BASE": "https://api.openai.com/v1",
  "LLM_API_KEY": "sk-...",
  "LLM_MODEL": "gpt-5",
  "LLM_TIMEOUT": 120,
  "LLM_TEMPERATURE": 0.9,
  "LLM_MAX_TOKENS": 1024
}
```

## API 协议

### OpenAI Responses API

Responses API 使用 `/v1/responses`。新接入 OpenAI 时可使用：

```json
{
  "LLM_PROVIDER": "openai_responses",
  "LLM_API_BASE": "https://api.openai.com/v1",
  "LLM_API_KEY": "sk-...",
  "LLM_MODEL": "gpt-5"
}
```

实现会发送 `store: false`，并使用 Responses API 原生 function calling 与 SSE 事件。请选择明确支持 Responses API、图片输入和 function calling 的模型。

参考：[OpenAI Responses API 文档](https://developers.openai.com/api/docs/guides/text)。

### Anthropic Messages API

```json
{
  "LLM_PROVIDER": "anthropic",
  "LLM_API_BASE": "https://api.anthropic.com/v1",
  "LLM_API_KEY": "sk-ant-...",
  "LLM_MODEL": "claude-sonnet-4-5"
}
```

Openmim 会自动使用 `x-api-key` 和 `anthropic-version: 2023-06-01` 请求头，并把 system 消息、图片和工具调用转换为 Anthropic 内容块。模型 ID 请以 Anthropic Console 中实际可用的模型为准。

参考：[Anthropic Messages API 文档](https://platform.claude.com/docs/en/api/messages)。

### OpenAI 兼容 Chat Completions

现有配置保持兼容，省略 `LLM_PROVIDER` 时默认使用该协议：

```json
{
  "LLM_PROVIDER": "openai_compatible",
  "LLM_API_BASE": "https://api.openai.com/v1",
  "LLM_API_KEY": "sk-...",
  "LLM_MODEL": "gpt-4o-mini"
}
```

DeepSeek、通义千问兼容模式、Ollama 和其他 `/chat/completions` 服务均使用此值。

## OpenAI 兼容服务示例

### DeepSeek

```json
{
  "LLM_PROVIDER": "openai_compatible",
  "LLM_API_BASE": "https://api.deepseek.com/v1",
  "LLM_API_KEY": "sk-...",
  "LLM_MODEL": "deepseek-chat"
}
```

### 阿里云 通义千问

```json
{
  "LLM_PROVIDER": "openai_compatible",
  "LLM_API_BASE": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "LLM_API_KEY": "sk-...",
  "LLM_MODEL": "qwen-turbo"
}
```

### Gemini（通过中转）

```json
{
  "LLM_PROVIDER": "openai_compatible",
  "LLM_API_BASE": "https://your-gemini-proxy.com/v1",
  "LLM_API_KEY": "your-api-key",
  "LLM_MODEL": "gemini-2.0-flash"
}
```

### Ollama（本地）

```json
{
  "LLM_PROVIDER": "openai_compatible",
  "LLM_API_BASE": "http://localhost:11434/v1",
  "LLM_API_KEY": "ollama",
  "LLM_MODEL": "qwen2.5:14b"
}
```

::: tip 本地部署
使用 Ollama 可以完全本地化运行，无需任何 API 费用。推荐模型：`qwen2.5:14b`（中文能力强）、`llama3.1:8b`。
:::

## 参数说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `LLM_PROVIDER` | `openai_compatible` | API 协议：`openai_compatible` / `openai_responses` / `anthropic` |
| `LLM_API_BASE` | `https://api.openai.com/v1` | API 端点地址 |
| `LLM_API_KEY` | （必填）| API 密钥 |
| `LLM_MODEL` | `gpt-4o-mini` | 模型名称 |
| `LLM_TIMEOUT` | `120` | 请求超时（秒）|
| `LLM_TEMPERATURE` | `0.9` | 生成温度（0-2），越高越随机 |
| `LLM_MAX_TOKENS` | `1024` | 单次最大生成 Token 数 |
| `STREAM_ENABLED` | `false` | 是否启用流式输出 |

## 图片描述（Caption）模型

可以配置独立的 Vision 模型用于图片描述：

```json
{
  "CAPTION_API_BASE": "https://api.openai.com/v1",
  "CAPTION_API_KEY": "sk-...",
  "CAPTION_MODEL": "gpt-4o-mini"
}
```

若不配置，图片描述任务会直接使用主 LLM（需要主模型支持 Vision）。

## 分群 BYOK（Bring Your Own Key）

每个群组可以配置独立的 LLM API，完全覆盖全局配置：

在群管理面板（`/gadmin` → 接口配置）中设置：
- **对话 API 协议**：自定义 `openai_compatible` / `openai_responses` / `anthropic`
- **对话模型**：自定义 LLM 模型名
- **对话 API Key**：独立的 API Key
- **对话 API Base**：独立的 API 端点

设置为"使用默认接口"则回退到全局配置。

::: warning 安全提示
群组配置的 API Key 会加密存储在 SQLite 数据库中。请确保 `data/` 目录的访问权限设置正确。
:::

## 流式输出

开启流式输出后，机器人会边生成边发送消息，体验更流畅：

```json
{
  "STREAM_ENABLED": true
}
```

::: info 注意
OpenAI Responses 与 Anthropic 原生流式事件已经适配。第三方 OpenAI 兼容端点是否支持 `stream_options.include_usage` 取决于服务商；若不支持，请关闭流式输出或使用其兼容配置。
:::
