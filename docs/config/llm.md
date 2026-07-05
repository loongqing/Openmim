# LLM 配置

Openmim 支持所有 OpenAI 兼容的 LLM API。本页介绍如何配置不同服务商。

## 基础配置

```json
{
  "LLM_API_BASE": "https://api.openai.com/v1",
  "LLM_API_KEY": "sk-...",
  "LLM_MODEL": "gpt-4o-mini",
  "LLM_TIMEOUT": 120,
  "LLM_TEMPERATURE": 0.9,
  "LLM_MAX_TOKENS": 1024
}
```

## 支持的服务商

### OpenAI

```json
{
  "LLM_API_BASE": "https://api.openai.com/v1",
  "LLM_API_KEY": "sk-...",
  "LLM_MODEL": "gpt-4o-mini"
}
```

推荐模型：
- `gpt-4o-mini` — 成本低，速度快，适合日常群聊
- `gpt-4o` — 能力更强，适合需要复杂推理的场景

### DeepSeek

```json
{
  "LLM_API_BASE": "https://api.deepseek.com/v1",
  "LLM_API_KEY": "sk-...",
  "LLM_MODEL": "deepseek-chat"
}
```

### 阿里云 通义千问

```json
{
  "LLM_API_BASE": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "LLM_API_KEY": "sk-...",
  "LLM_MODEL": "qwen-turbo"
}
```

### Gemini（通过中转）

```json
{
  "LLM_API_BASE": "https://your-gemini-proxy.com/v1",
  "LLM_API_KEY": "your-api-key",
  "LLM_MODEL": "gemini-2.0-flash"
}
```

### Ollama（本地）

```json
{
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
并非所有 API 端点都支持流式输出。建议先测试确认服务商支持后再开启。
:::
