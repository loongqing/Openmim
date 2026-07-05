# 群组设置

每个群组可以独立配置自己的行为参数。群管理员通过 `/gadmin` 命令访问设置面板。

## 设置项一览

| 设置项 | 默认值 | 说明 |
|--------|--------|------|
| `persona_prompt` | `""` | 自定义人设提示词 |
| `scoring_criteria` | `""` | 聚焦评分标准（自然语言描述）|
| `morning_greeting_enabled` | `true` | 是否允许早安问候 |
| `evening_greeting_enabled` | `true` | 是否允许晚安问候 |
| `idle_topic_enabled` | `true` | 是否允许冷群话题激活 |
| `free_reply_mode` | `false` | 自由回复模式 |
| `reply_preference` | `llm_first` | 回复偏好 |
| `attention_mode` | `single_message` | 注意力模式 |
| `message_drop_probability` | `0` | 消息丢弃概率（0-1）|
| `username_anonymization_enabled` | `true` | 用户名脱敏 |
| `repeater_enabled` | `true` | 复读机 |
| `llm_model` | `default` | 自定义 LLM 模型 |
| `llm_api_key` | `default` | 自定义 LLM API Key |
| `llm_api_base` | `default` | 自定义 LLM API Base |
| `tavily_api_key` | `default` | 自定义 Tavily API Key |
| `image_gen_model` | `default` | 自定义图生模型 |
| `image_gen_api_key` | `default` | 自定义图生 API Key |
| `image_gen_api_base` | `default` | 自定义图生 API Base |
| `enabled_skills` | `[]` | 已订阅的 Skills 列表 |
| `disabled_tools` | `[]` | 禁用的工具列表 |

## 详细说明

### 自定义人设（`persona_prompt`）

为当前群组设置专属人设。这个 prompt 会注入到每次对话的 system prompt 中，使机器人在不同群组中表现出不同的性格。

示例：
```
这是一个 Python 技术交流群。在这里，你是一个严谨但不失幽默的技术达人，
说话简洁，喜欢用代码例子说话，对技术问题有自己的见解。
```

### 聚焦评分标准（`scoring_criteria`）

用自然语言描述这个群希望机器人关注什么，影响聚焦系统的评分行为。

示例：
```
这是一个 AI 讨论群，重点关注大模型、AI 工具、提示词工程相关的话题。
日常生活、美食、游戏等话题评分降低。技术问题和 AI 相关内容优先参与。
```

### 回复偏好（`reply_preference`）

| 值 | 说明 |
|----|------|
| `llm_first` | 优先遵循 LLM 的综合判断 |
| `explicit_first` | 优先响应明确提到机器人名字或需要机器人回答的消息 |

### 注意力模式（`attention_mode`）

| 值 | 说明 |
|----|------|
| `single_message` | 每次只分析当前触发消息（默认，最省 Token）|
| `all_message` | 分析窗口内所有新消息，汇总判断 |
| `mixed` | 混合模式 |

### 消息丢弃概率（`message_drop_probability`）

控制机器人随机跳过普通消息的概率：

- `0`：不丢弃（默认）
- `0.2`：约 20% 的消息被跳过
- `1`：跳过所有普通消息

> 明确 @、回复机器人、叫机器人昵称不受此影响。

### 自由回复模式（`free_reply_mode`）

开启后，Bot 会把最近消息的 ID 列表提供给 LLM，LLM 可以选择对特定消息单独回复，而不是只对最新消息回复。适合活跃的群聊环境。

## BYOK 配置

每个群组可以独立配置 LLM 和工具 API，完全覆盖全局设置：

### 配置方式

通过 `/gadmin` → 接口配置，或 Web 面板中的群组设置页面。

### 可配置项

- **对话模型/API**：独立的 LLM 配置
- **图片生成 API**：独立的图生配置
- **Tavily 搜索**：独立的搜索 API Key

设置值为"使用默认接口"时，回退到全局配置。

::: info
群组 API Key 会加密存储，管理面板中显示为脱敏格式。
:::

## Skills 管理

在群管理面板中，群管理员可以订阅和管理 Skills（技能插件）：

- 从 Skills 市场浏览可用 Skills
- 订阅/取消订阅 Skills
- 为已订阅的 Skills 配置所需的私密信息（如 API Key）

## 工具管理

可以为当前群禁用特定工具，防止 LLM 在该群使用某些能力：

常用工具名：
- `run_python` — 代码执行
- `run_shell` — Shell 命令
- `search_web` — 联网搜索
- `generate_image` — 图片生成
- `create_task` — 计划任务

## 群设置数据存储

群设置持久化在 `data/group_config.sqlite3` 数据库中，重启后保留。
