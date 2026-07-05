![openmim](examples/Openmim.png)

一个可爱好玩的Telegram chatbot

## 官方入口

- Telegram 频道：[@mimchanOffical](https://t.me/mimchanOffical)
- 闭源版机器人：
  - [@ceciliachanbot](https://t.me/ceciliachanbot)
  - [@mimchan_bot](https://t.me/mimchan_bot)

功能：
1. 自动群聊记忆、全局记忆、人格记忆。让机器人学会自我进化！
2. 见缝插针选择性回复，根据自定义人格让LLM挑选感兴趣的消息回复，主动参与群聊对话，或者被关键词召唤。沉浸感体验远超随机回复方案！
3. Agentic功能，支持Skills，支持接入Tavily和各类沙盒(e2b、local、shipyard)，以及计划任务和绘图功能。
4. business chatbot支持，让机器人接管你的私聊！
5. guest mode支持，随时随地在任何群组召唤机器人！
6. 完全的分群组BYOK支持
7. Web UI和telegram面板支持，方便管理你的机器人。
8. 独特的微动作、去AI味等等充满心机的设计细节！

## 最小配置

复制或创建 `data/project_config.json`，至少提供：

```json
{
  "BOT_TOKEN": "123456:telegram-bot-token",
  "ADMIN_IDS": [""],
  "LLM_API_BASE": "https://api.openai.com/v1",
  "LLM_API_KEY": "sk-...",
  "LLM_MODEL": "gpt-4o-mini"
}
```

启动：

```bash
python3 -m pip install -r requirements.txt
./start.sh
```

## 文档

[https://loongqing.github.io/Openmim/](https://loongqing.github.io/Openmim/)
