# Agentic 工具能力

Openmim 支持完整的 Agentic 模式——当用户请求超出纯聊天范围时，LLM 可以自动调用一系列工具来完成任务，包括联网搜索、代码执行、图片生成、计划任务等。

## 工具列表

### 🔍 联网搜索（Tavily）

让机器人实时搜索互联网，回答需要最新信息的问题。

**配置要求**：
```json
{
  "TAVILY_API_KEY": "tvly-..."
}
```

**示例对话**：
```
用户：今天有什么科技新闻？
机器人：（调用 search_web 工具）让我搜索一下...
       今天的主要科技新闻有...
```

**工具名**：`search_web`  
**支持**：每个群可配置独立的 Tavily API Key（BYOK）

---

### 💻 代码沙箱

让机器人在隔离环境中执行 Python 或 Shell 代码。支持三种沙箱后端：

#### E2B（推荐）
云端隔离沙箱，安全可靠，每个群独立沙箱实例，5分钟内自动复用。

```json
{
  "SANDBOX_PROVIDER": "e2b",
  "E2B_API_KEY": "e2b_...",
  "E2B_TIMEOUT": 120
}
```

#### Shipyard（自托管）
自托管沙箱服务，适合私有部署。

```json
{
  "SANDBOX_PROVIDER": "shipyard",
  "SHIPYARD_ENDPOINT": "http://127.0.0.1:8156",
  "SHIPYARD_ACCESS_TOKEN": "your-token"
}
```

#### Local（本机执行，仅可信部署）
在本机隔离目录直接执行，无需额外服务，但有安全风险。

```json
{
  "SANDBOX_PROVIDER": "local",
  "LOCAL_SANDBOX_WORKDIR": "data/local_sandbox",
  "LOCAL_SANDBOX_TIMEOUT": 180
}
```

::: warning 安全提示
Local 沙箱直接在宿主机执行代码，**仅在你完全信任所有用户的私有环境中使用**。
:::

**工具名**：`run_python`、`run_shell`

**沙箱特性**：
- 按 chat_id 复用：同一群 10 分钟内的操作共享同一沙箱（文件系统可持久）
- 串行执行：同一群的代码请求排队执行，不并发
- 输出限制：单次最多返回 4000 字符给 LLM

**示例对话**：
```
用户：帮我计算 1000 以内所有质数的和
机器人：（调用 run_python 工具）
       primes = [x for x in range(2, 1001) if all(x%i!=0 for i in range(2,x))]
       print(sum(primes))
       
       结果：76127
```

---

### 🎨 图片生成

让机器人生成 AI 图片，支持文生图和图生图。

**配置要求**：
```json
{
  "IMAGE_GEN_API_BASE": "https://api.openai.com/v1",
  "IMAGE_GEN_API_KEY": "sk-...",
  "IMAGE_GEN_MODEL": "dall-e-3"
}
```

**功能**：
- **文生图**：根据文字描述生成图片
- **图生图**：以聊天中的图片为参考，生成变体或修改版本

**示例对话**：
```
用户：画一只在宇宙中冲浪的猫咪
机器人：（调用 generate_image 工具）好的，让我画一下...
       [发送生成的图片]
```

**工具名**：`generate_image`  
**支持**：每个群可配置独立的图生 API Key 和模型（BYOK）

---

### ⏰ 计划任务

让 LLM 创建定时任务，到期后触发一次完整的 LLM 对话。

**功能**：
- **延迟触发**：N 分钟后提醒
- **指定时间**：在某个具体时间点触发
- **循环任务**：按 cron 表达式循环执行

::: info 注意
计划任务**不持久化**，进程重启后全部丢失。
:::

**示例对话**：
```
用户：30分钟后提醒我喝水
机器人：好的！（创建任务）我会在30分钟后提醒你。
       [30分钟后]
机器人：提醒一下，该喝水了～
```

**工具名**：`create_task`、`list_tasks`、`cancel_task`  
**最大任务数**：同时最多 20 个活跃任务

---

### 🧠 记忆工具

让 LLM 主动管理记忆，记住重要信息。

| 工具名 | 功能 |
|--------|------|
| `add_memory` | 添加一条记忆 |
| `list_memories` | 列出所有记忆 |
| `delete_memory` | 删除指定记忆 |
| `update_memory` | 更新记忆内容 |
| `find_memory` | 搜索相关记忆 |

---

### 📋 Skills（技能）

Skills 是可扩展的工具插件，类似于 ChatGPT 的 Actions。每个 Skill 可以定义一组工具，由 LLM 按需调用。

详见 [Skills 市场](/advanced/skills)。

---

### ⏰ 时间查询

```
工具名：get_current_time
功能：获取当前 CST 时间（避免把时间写死进 prompt）
```

当用户询问当前时间、日期、星期几时，LLM 会调用此工具获取准确的当前时间。

## Agentic 配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `AGENT_MAX_ROUNDS` | `10` | LLM 工具调用的最大轮数 |
| `TEXT_TOOL_ENABLED` | `true` | 是否为普通文本消息启用工具 |
| `GUEST_TOOL_ENABLED` | `true` | Guest 模式是否允许工具调用 |
| `TOOL_RESULT_MAX_CHARS` | `6000` | 工具返回结果的最大字符数 |

## 禁用特定工具

在群管理面板（`/gadmin` → 工具设置）中，群管理员可以为当前群禁用特定工具，防止 LLM 在该群使用某些能力。

也可在全局配置中通过 `PLUGINS_DISABLED` 完全禁用某个插件：

```json
{
  "PLUGINS_DISABLED": ["sandbox", "image"]
}
```
