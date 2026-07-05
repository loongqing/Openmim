# 定制化（Customization）

Openmim 支持通过 `data/customization.json` 文件对文本内容、人设、交互词汇等进行深度定制，无需修改代码。

## 配置文件位置

默认路径：`data/customization.json`

可通过环境变量指定自定义路径：
```bash
export CUSTOMIZATION_FILE="/path/to/your/customization.json"
```

## 文件结构

`customization.json` 是一个 JSON 对象，支持嵌套路径覆盖：

```json
{
  "messages": {
    "startup_log": "🤖 Bot 启动完成！"
  },
  "playables": {
    "guess": {
      "reveal_keywords": ["公布答案", "揭晓", "show answer"]
    }
  },
  "personality": {
    "micro_actions": {
      "lunch_break": "去吃饭啦！"
    }
  },
  "bot_commands": {
    "fortune": "查看今日运势",
    "guesshistory": "看图猜时代"
  }
}
```

## 可定制内容

### 消息文本（`messages`）

系统级别的日志/通知消息：

```json
{
  "messages": {
    "startup_log": "🤖 Bot 启动完成！",
    "startup_admin": "🤖 **Bot 上线了！**\n• 贴纸: {stickers_loaded}\n• 白名单: {whitelist_count}\n• Business: {business_enabled}",
    "commands_synced_log": "✅ 命令列表已同步",
    "commands_sync_failed_log": "同步命令失败: {error}",
    "launching_log": "🚀 启动中... (concurrent={concurrent_updates}, business={business})"
  }
}
```

### 可玩性功能（`playables`）

#### 猜图游戏

```json
{
  "playables": {
    "guess": {
      "reveal_keywords": ["公布答案", "显示答案", "揭晓", "看答案", "show answer", "reveal", "答案"],
      "text_starts": ["猜时代", "猜历史", "开始猜图", "历史猜图"]
    }
  }
}
```

### 微动作文本（`personality.micro_actions`）

```json
{
  "personality": {
    "micro_actions": {
      "lunch_break": "去吃饭了，待会见～",
      "late_night": "（打哈欠）这么晚还没睡呢...",
      "feel_ignored": "唔，你们聊得好热闹，感觉插不上话...",
      "goodbye": "晚安～记得早点休息哦",
      "good_morning": "早安～新的一天开始啦！",
      "random_stretch": "（伸个懒腰）好久没人理我了..."
    }
  }
}
```

### Bot 命令描述（`bot_commands`）

自定义 Telegram 命令菜单中显示的命令描述：

```json
{
  "bot_commands": {
    "start": "开始使用机器人",
    "admin": "管理员面板",
    "settings": "Business 设置",
    "gadmin": "群组管理面板",
    "muteme": "让机器人忽略我",
    "unmuteme": "恢复机器人回复",
    "quiet": "安静模式",
    "unquiet": "恢复主动参与",
    "topic": "话题追踪模式",
    "notopic": "退出话题追踪",
    "fortune": "今日运势",
    "guesshistory": "看图猜时代",
    "about": "关于机器人"
  }
}
```

## 深度合并

定制文件中只需包含你想要覆盖的部分，其他内容保持代码中的默认值。系统会将你的配置与默认值进行深度合并。

例如，只修改一个微动作文本：

```json
{
  "personality": {
    "micro_actions": {
      "goodbye": "拜拜～"
    }
  }
}
```

其他微动作文本保持默认，只有 `goodbye` 被替换。

## 热重载

修改 `customization.json` 后，可以通过以下方式让配置生效：

1. **重启机器人**：最简单的方式
2. **调用 reload API**（如果 Web 面板已启用）

::: tip
定制文件使用 `@lru_cache` 缓存，热重载需要主动清除缓存或重启进程。
:::
