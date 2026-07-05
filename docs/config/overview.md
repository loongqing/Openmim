# 配置总览

Openmim 通过 `data/project_config.json` 或**环境变量**进行配置。

## 配置加载顺序

1. 优先读取**环境变量**
2. 其次读取 `data/project_config.json`
3. 最后回退到**默认值**

## 必填配置

| 配置项 | 说明 |
|--------|------|
| `BOT_TOKEN` | Telegram Bot Token（从 @BotFather 获取）|
| `ADMIN_IDS` | 管理员 Telegram User ID 列表 |
| `LLM_API_KEY` | LLM API Key |

## 完整配置文件模板

下面是一份完整的 `data/project_config.json` 模板。你可以直接复制并根据需要进行修改：

```json
{
  "BOT_TOKEN": "123456789:your-bot-token",
  "ADMIN_IDS": ["your-user-id"],
  "LOG_LEVEL": "INFO",

  "LLM_API_BASE": "https://api.openai.com/v1",
  "LLM_API_KEY": "sk-...",
  "LLM_MODEL": "gpt-4o-mini",
  "LLM_TIMEOUT": 120,
  "LLM_TEMPERATURE": 0.9,
  "LLM_MAX_TOKENS": 1024,
  "STREAM_ENABLED": false,

  "IMAGE_GEN_API_BASE": "https://api.openai.com/v1",
  "IMAGE_GEN_API_KEY": "sk-...",
  "IMAGE_GEN_MODEL": "dall-e-3",
  "IMAGE_GEN_TIMEOUT": 120,
  "CAPTION_API_BASE": "",
  "CAPTION_API_KEY": "",
  "CAPTION_MODEL": "",

  "TAVILY_API_KEY": "tvly-...",

  "SANDBOX_PROVIDER": "e2b",
  "E2B_API_KEY": "e2b_...",
  "E2B_TIMEOUT": 120,
  "SHIPYARD_ENDPOINT": "http://127.0.0.1:8156",
  "SHIPYARD_ACCESS_TOKEN": "secret-token",
  "LOCAL_SANDBOX_WORKDIR": "data/local_sandbox",
  "LOCAL_SANDBOX_TIMEOUT": 180,

  "CONTEXT_MESSAGE_COUNT": 100,
  "CONTEXT_MAX_TEXT_CHARS": 700,
  "BOT_CONTEXT_MAX_CHARS": 1400,
  "RECENT_CONTEXT_MESSAGES": 25,
  "AGENT_MAX_ROUNDS": 10,
  "TOOL_RESULT_MAX_CHARS": 6000,
  "MAX_IMAGE_DOWNLOAD_BYTES": 900000,
  "BOT_CALL_ALIASES": ["猫猫", "小猫", "咪姆", "咪姆酱"],

  "FOCUS_LIGHT_HINT_ENABLED": true,
  "FOCUS_LIGHT_HINT_PROBABILITY": 0.25,
  "FOCUS_LIGHT_HINT_COOLDOWN_SECONDS": 60,
  "FOCUS_LIGHT_HINT_MIN_CHARS": 8,
  "FOCUS_JOIN_SCORE_THRESHOLD": 6,
  "FOCUS_JOIN_MAX_PER_SESSION": 5,
  "FOCUS_STAGE1_THRESHOLD": 4,

  "IDLE_TOPIC_IDLE_HOURS": 6,
  "IDLE_TOPIC_SCAN_INTERVAL_SECONDS": 3600,
  "IDLE_TOPIC_MAX_PER_RUN": 3,

  "PERSONALITY_ENABLED": true,
  "TYPO_RHYTHM_ENABLED": true,
  "CONVERSATION_MEMORY_ENABLED": true,
  "MICRO_ACTIONS_ENABLED": true,
  "DE_AI_ENABLED": true,
  "HUMANIZATION_ENABLED": true,
  "HUMANIZATION_INTENSITY": 0.7,
  "HUMANIZATION_STYLE": "balanced",

  "PERSONA_MEMORY_ENABLED": true,
  "PERSONA_MEMORY_UPDATE_ENABLED": true,
  "PERSONA_MEMORY_MAX_USERS_PER_TURN": 8,
  "PERSONA_MEMORY_PROMPT_MAX_CHARS": 1800,
  "PERSONA_MEMORY_TOTAL_MAX_CHARS": 700,
  "PERSONA_MEMORY_UPDATE_MIN_CONFIDENCE": 0.65,
  "PERSONA_MEMORY_UPDATE_INTERVAL_SECONDS": 30,

  "GUEST_MODE_ENABLED": true,
  "GUEST_MODE_MAX_REPLY_CHARS": 800,
  "GUEST_ALLOWED_USER_IDS": [],
  "GUEST_TOOL_ENABLED": true,

  "PRIVATE_CHAT_ENABLED": true,
  "PRIVATE_ALLOWED_USER_IDS": [],

  "BUSINESS_ENABLED": true,
  "BUSINESS_ALLOWED_USER_IDS": [],
  "BUSINESS_MAX_REPLY_CHARS": 800,
  "BUSINESS_TYPING_DELAY_MIN": 0.8,
  "BUSINESS_TYPING_DELAY_MAX": 4.5,
  "BUSINESS_TYPING_DELAY_PER_CHAR": 0.04,

  "TELEGRAM_CONCURRENT_UPDATES": 8,
  "BACKPRESSURE_GLOBAL_MAX": 5,
  "BACKPRESSURE_PER_CHAT_MAX": 4,

  "STICKER_SETS": ["RinCat_SD_AC33D506", "PROs_Misc_Collection"],
  "PLUGINS_DISABLED": [],
  "TEXT_TOOL_ENABLED": true,

  "WEB_PANEL_ENABLED": false,
  "WEB_PANEL_HOST": "127.0.0.1",
  "WEB_PANEL_PORT": 7860,
  "WEB_PANEL_ACCESS_TOKEN": "",
  "WEB_PANEL_ALLOW_REMOTE_WITHOUT_TOKEN": false,
  "WEB_PANEL_PUBLIC_BASE_URL": "",
  "WEB_PANEL_RESTART_ENABLED": false,
  "WEB_PANEL_RESTART_COMMAND": "",
  "WEB_PANEL_SKILL_UPLOAD_ENABLED": true,
  "WEB_PANEL_SKILL_UPLOAD_MAX_BYTES": 2097152,
  "LOCAL_SKILL_ROOT": "data/skills"
}
```

## 完整配置参考

### 基础配置

```json
{
  "BOT_TOKEN": "123456789:your-bot-token",
  "ADMIN_IDS": ["your-user-id"],
  "LOG_LEVEL": "INFO"
}
```

### LLM 配置

```json
{
  "LLM_API_BASE": "https://api.openai.com/v1",
  "LLM_API_KEY": "sk-...",
  "LLM_MODEL": "gpt-4o-mini",
  "LLM_TIMEOUT": 120,
  "LLM_TEMPERATURE": 0.9,
  "LLM_MAX_TOKENS": 1024,
  "STREAM_ENABLED": false
}
```

### 图片处理配置

```json
{
  "IMAGE_GEN_API_BASE": "https://api.openai.com/v1",
  "IMAGE_GEN_API_KEY": "sk-...",
  "IMAGE_GEN_MODEL": "dall-e-3",
  "IMAGE_GEN_TIMEOUT": 120,
  "CAPTION_API_BASE": "",
  "CAPTION_API_KEY": "",
  "CAPTION_MODEL": ""
}
```

> `CAPTION_*` 用于图片转文字（图片描述）功能，可配置独立的 Vision 模型。留空时图片输入自动使用主 LLM。

### 搜索配置

```json
{
  "TAVILY_API_KEY": "tvly-..."
}
```

### 沙箱配置

```json
{
  "SANDBOX_PROVIDER": "e2b",
  "E2B_API_KEY": "e2b_...",
  "E2B_TIMEOUT": 120,
  "SHIPYARD_ENDPOINT": "http://127.0.0.1:8156",
  "SHIPYARD_ACCESS_TOKEN": "secret-token",
  "LOCAL_SANDBOX_WORKDIR": "data/local_sandbox",
  "LOCAL_SANDBOX_TIMEOUT": 180
}
```

### 行为参数

```json
{
  "CONTEXT_MESSAGE_COUNT": 100,
  "CONTEXT_MAX_TEXT_CHARS": 700,
  "BOT_CONTEXT_MAX_CHARS": 1400,
  "RECENT_CONTEXT_MESSAGES": 25,
  "AGENT_MAX_ROUNDS": 10,
  "TOOL_RESULT_MAX_CHARS": 6000,
  "MAX_IMAGE_DOWNLOAD_BYTES": 900000,
  "BOT_CALL_ALIASES": ["猫猫", "小猫", "咪姆", "咪姆酱"]
}
```

### 聚焦系统配置

```json
{
  "FOCUS_LIGHT_HINT_ENABLED": true,
  "FOCUS_LIGHT_HINT_PROBABILITY": 0.25,
  "FOCUS_LIGHT_HINT_COOLDOWN_SECONDS": 60,
  "FOCUS_LIGHT_HINT_MIN_CHARS": 8,
  "FOCUS_JOIN_SCORE_THRESHOLD": 6,
  "FOCUS_JOIN_MAX_PER_SESSION": 5,
  "FOCUS_STAGE1_THRESHOLD": 4
}
```

### 冷群话题激活配置

```json
{
  "IDLE_TOPIC_IDLE_HOURS": 6,
  "IDLE_TOPIC_SCAN_INTERVAL_SECONDS": 3600,
  "IDLE_TOPIC_MAX_PER_RUN": 3
}
```

### 拟人化配置

```json
{
  "PERSONALITY_ENABLED": true,
  "TYPO_RHYTHM_ENABLED": true,
  "CONVERSATION_MEMORY_ENABLED": true,
  "MICRO_ACTIONS_ENABLED": true,
  "DE_AI_ENABLED": true,
  "HUMANIZATION_ENABLED": true,
  "HUMANIZATION_INTENSITY": 0.7,
  "HUMANIZATION_STYLE": "balanced"
}
```

### 用户画像记忆配置

```json
{
  "PERSONA_MEMORY_ENABLED": true,
  "PERSONA_MEMORY_UPDATE_ENABLED": true,
  "PERSONA_MEMORY_MAX_USERS_PER_TURN": 8,
  "PERSONA_MEMORY_PROMPT_MAX_CHARS": 1800,
  "PERSONA_MEMORY_TOTAL_MAX_CHARS": 700,
  "PERSONA_MEMORY_UPDATE_MIN_CONFIDENCE": 0.65,
  "PERSONA_MEMORY_UPDATE_INTERVAL_SECONDS": 30
}
```

### Guest 模式配置

```json
{
  "GUEST_MODE_ENABLED": true,
  "GUEST_MODE_MAX_REPLY_CHARS": 800,
  "GUEST_ALLOWED_USER_IDS": [],
  "GUEST_TOOL_ENABLED": true
}
```

### 私聊配置

```json
{
  "PRIVATE_CHAT_ENABLED": true,
  "PRIVATE_ALLOWED_USER_IDS": []
}
```

### Business 模式配置

```json
{
  "BUSINESS_ENABLED": true,
  "BUSINESS_ALLOWED_USER_IDS": [],
  "BUSINESS_MAX_REPLY_CHARS": 800,
  "BUSINESS_TYPING_DELAY_MIN": 0.8,
  "BUSINESS_TYPING_DELAY_MAX": 4.5,
  "BUSINESS_TYPING_DELAY_PER_CHAR": 0.04
}
```

### 并发配置

```json
{
  "TELEGRAM_CONCURRENT_UPDATES": 8,
  "BACKPRESSURE_GLOBAL_MAX": 5,
  "BACKPRESSURE_PER_CHAT_MAX": 4
}
```

### 贴纸配置

```json
{
  "STICKER_SETS": ["RinCat_SD_AC33D506", "PROs_Misc_Collection"]
}
```

### 插件配置

```json
{
  "PLUGINS_DISABLED": []
}
```

### 工具相关

```json
{
  "TEXT_TOOL_ENABLED": true,
  "AGENT_MAX_ROUNDS": 10
}
```

### Web 面板配置

```json
{
  "WEB_PANEL_ENABLED": false,
  "WEB_PANEL_HOST": "127.0.0.1",
  "WEB_PANEL_PORT": 7860,
  "WEB_PANEL_ACCESS_TOKEN": "",
  "WEB_PANEL_ALLOW_REMOTE_WITHOUT_TOKEN": false,
  "WEB_PANEL_PUBLIC_BASE_URL": "",
  "WEB_PANEL_RESTART_ENABLED": false,
  "WEB_PANEL_RESTART_COMMAND": "",
  "WEB_PANEL_SKILL_UPLOAD_ENABLED": true,
  "WEB_PANEL_SKILL_UPLOAD_MAX_BYTES": 2097152,
  "LOCAL_SKILL_ROOT": "data/skills"
}
```

## 数据文件位置

| 文件 | 说明 |
|------|------|
| `data/project_config.json` | 主配置文件 |
| `data/group_config.sqlite3` | 群组设置 + 白名单 |
| `data/persona_memory.sqlite3` | 用户画像记忆 |
| `data/human_behavior.sqlite3` | 拟人化行为状态 |
| `data/customization.json` | 文本/人设定制 |
| `data/skills/` | 本地 Skills 存储目录 |

## 环境变量

所有配置项均可通过环境变量设置，列表型配置用逗号分隔：

```bash
export BOT_TOKEN="123456:your-token"
export ADMIN_IDS="123456789,987654321"
export LLM_API_KEY="sk-..."
export STICKER_SETS="StickerSet1,StickerSet2"
```
