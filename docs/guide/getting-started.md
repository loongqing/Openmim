# 快速开始

本指南将带你在 5 分钟内完成 Openmim 的基础部署。

## 前置要求

- Python 3.11 或更高版本
- 一个 Telegram Bot Token（通过 [@BotFather](https://t.me/BotFather) 创建）
- 一个 OpenAI 兼容的 LLM API（OpenAI、DeepSeek、本地 Ollama 等均可）

## 第一步：克隆仓库

```bash
git clone https://github.com/loongqing/Openmim.git
cd Openmim
```

## 第二步：安装依赖

```bash
pip install -r requirements.txt
```

## 第三步：创建配置文件

在 `data/` 目录下创建 `project_config.json`（若 `data/` 目录不存在会自动创建）：

```json
{
  "BOT_TOKEN": "123456789:你的TelegramBotToken",
  "ADMIN_IDS": ["你的TelegramUserID"],
  "LLM_API_BASE": "https://api.openai.com/v1",
  "LLM_API_KEY": "sk-...",
  "LLM_MODEL": "gpt-4o-mini"
}
```

::: tip 如何获取 Telegram User ID？
给 [@userinfobot](https://t.me/userinfobot) 发送任意消息，它会返回你的用户 ID。
:::

::: info 配置文件位置
配置文件优先从 `data/project_config.json` 读取，若不存在则尝试根目录的 `project_config.json`（兼容旧版本）。  
所有配置项也可通过**环境变量**设置，环境变量优先级高于配置文件。
:::

## 第四步：启动机器人

```bash
./start.sh
```

或直接运行：

```bash
python main.py
```

启动成功后，机器人会向所有 `ADMIN_IDS` 发送一条启动通知，内容包含贴纸加载状态、白名单群组数量等信息。

## 第五步：将群组加入白名单

机器人默认不会响应任何群组的消息，需要将群组加入白名单。

1. 在你希望机器人工作的群组中，发送 `/admin` 命令打开管理面板
2. 或者直接私聊机器人发送 `/admin`，然后在面板中添加群组 ID

::: warning 注意
机器人必须先被添加到群组中，且拥有发送消息的权限，才能正常工作。
:::

## 可选：启用更多功能

### 联网搜索（Tavily）

```json
{
  "TAVILY_API_KEY": "tvly-..."
}
```

注册地址：[tavily.com](https://tavily.com)

### 图片生成

```json
{
  "IMAGE_GEN_API_BASE": "https://api.openai.com/v1",
  "IMAGE_GEN_API_KEY": "sk-...",
  "IMAGE_GEN_MODEL": "dall-e-3"
}
```

### 代码沙箱（E2B）

```json
{
  "E2B_API_KEY": "e2b_..."
}
```

注册地址：[e2b.dev](https://e2b.dev)

### Web 管理面板

```json
{
  "WEB_PANEL_ENABLED": true,
  "WEB_PANEL_PORT": 7860,
  "WEB_PANEL_ACCESS_TOKEN": "your-secret-token"
}
```

启动后访问 `http://127.0.0.1:7860`。

## 下一步

- [配置详解](/config/overview) — 完整的配置参数说明
- [记忆系统](/features/memory) — 深入了解三层记忆体系
- [聚焦插话](/features/focus) — 配置机器人的主动参与行为
