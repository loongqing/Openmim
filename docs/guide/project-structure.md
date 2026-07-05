# 项目结构

Openmim 采用分层模块化架构，各目录职责明确。

```
Openmim/
├── main.py                 # 兼容入口（转发到 app/main.py）
├── start.sh                # 启动脚本
├── requirements.txt        # Python 依赖
├── data/                   # 运行时数据（SQLite、配置、日志）
│   ├── project_config.json # 主配置文件
│   ├── group_config.sqlite3 # 群组设置 + 白名单数据库
│   ├── persona_memory.sqlite3 # 用户画像记忆数据库
│   ├── human_behavior.sqlite3 # 拟人化行为状态数据库
│   ├── skills/             # 本地 Skills 存储
│   └── customization.json  # 文本/人设定制（可选）
├── app/                    # 应用启动编排
│   ├── main.py             # 主入口，注册定时任务和生命周期钩子
│   ├── bootstrap.py        # 初始化编排，注册所有 handlers
│   ├── container.py        # AppContext 数据容器
│   ├── runtime_config.py   # 运行时配置（BYOK 解析）
│   └── build_info.py       # 构建信息
├── app_config/             # 配置加载层
│   ├── config.py           # 所有配置项（JSON + 环境变量）
│   ├── customization.py    # 文本定制系统
│   └── settings.py         # 设置加载
├── handlers/               # Telegram Update 处理器
│   ├── chat_handler.py     # 核心群聊/私聊消息处理
│   ├── admin_handler.py    # 管理员命令
│   ├── admin_panel.py      # 管理员 Telegram 内联面板
│   ├── group_admin_panel.py # 群管理员 Telegram 内联面板
│   ├── business_handler.py # Business 模式消息处理
│   ├── private_text_router.py # 私聊文本路由
│   └── ...                 # 其他命令处理器
├── services/               # 业务逻辑服务层
│   ├── chat_orchestrator.py # 聊天编排（核心流程）
│   ├── focus_service.py    # 聚焦/插话决策
│   ├── reply_service.py    # 回复生成
│   ├── persona_service.py  # 人设管理
│   ├── media_service.py    # 媒体处理（图片描述等）
│   └── ...
├── features/               # 具体功能实现
│   ├── playables.py        # 可玩性功能（运势、猜图等）
│   ├── micro_actions.py    # 微动作系统
│   ├── de_ai_text.py       # 去AI味后处理
│   ├── idle_topic_scheduler.py # 冷群话题激活
│   ├── typing_rhythm.py    # 打字节奏模拟
│   ├── business_humanization.py # Business 模式拟人化
│   └── ...
├── stores/                 # 数据持久化层（ORM + SQLite）
│   ├── orm.py              # SQLAlchemy 模型定义
│   ├── context_manager.py  # 群聊上下文管理
│   ├── persona_memory.py   # 用户画像记忆
│   ├── group_settings_store.py # 群组配置 KV 存储
│   ├── memory_store.py     # 全局记忆（笔记）
│   ├── human_behavior.py   # 拟人化行为状态
│   └── ...
├── llm/                    # LLM 接入层
│   ├── llm_client.py       # LLM 客户端（OpenAI 兼容）
│   ├── prompt.py           # Prompt 构建
│   ├── message_builder.py  # 消息历史构建
│   ├── focus_scoring.py    # 聚焦评分 Prompt
│   └── tool_plugins/       # LLM Tool 插件
├── integrations/           # 外部服务集成
│   ├── web_search.py       # Tavily 联网搜索
│   ├── e2b_tool.py         # E2B/Shipyard/Local 代码沙箱
│   ├── image_gen_tool.py   # AI 图片生成
│   ├── scheduler_tool.py   # LLM 计划任务
│   └── skill_market_client.py # Skills 市场客户端
├── plugins/                # 插件系统
│   ├── base.py             # 插件基类和 Hook 协议
│   ├── manager.py          # 插件管理器
│   └── builtin/            # 内置插件
│       ├── focus.py        # 聚焦工具插件
│       ├── memory.py       # 记忆工具插件
│       ├── sandbox.py      # 沙箱工具插件
│       ├── web.py          # 搜索工具插件
│       ├── image.py        # 图片生成工具插件
│       ├── scheduler.py    # 计划任务工具插件
│       ├── skills.py       # Skills 工具插件
│       ├── web_panel.py    # Web Panel 插件
│       └── ...
└── docs/                   # 文档（VitePress）
```

## 启动流程

```
main.py
  └─ app/main.py → main()
       ├─ validate_config()         # 校验必填配置
       ├─ bootstrap.build_application()
       │    ├─ build_runtime_context()
       │    │    ├─ 初始化数据库
       │    │    ├─ 加载白名单
       │    │    ├─ 加载插件
       │    │    └─ 压缩历史上下文
       │    └─ 注册所有 handlers
       └─ application.run_polling()
            └─ post_init()
                 ├─ 同步 Bot 命令列表
                 ├─ 加载贴纸
                 ├─ 注册定时问候任务
                 └─ 启动 idle topic 循环
```

## 数据流

```
Telegram Update
    │
    ▼
raw_update_router       # 路由原始 Update（成员变动等）
    │
    ▼
chat_handler / business_handler
    │
    ▼
chat_orchestrator       # 核心编排：判断是否回复、构建 prompt
    │
    ├─ FocusService     # 聚焦评分
    ├─ ContextManager   # 拉取上下文历史
    ├─ PersonaMemory    # 注入用户画像
    ├─ llm_client       # 调用 LLM（含 tool_call 循环）
    └─ reply_service    # 发送回复（含去AI味、微动作等）
```

## 配置加载优先级

```
环境变量  >  data/project_config.json  >  默认值
```
