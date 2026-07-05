# Skills 市场

Skills 是 Openmim 的扩展工具系统，类似于 ChatGPT 的 Actions。每个 Skill 定义一组 LLM 可调用的工具，让机器人获得特定领域的能力。

## Skills 工作原理

1. 管理员将 Skill 包上传到 `skill/` 目录
2. 群管理员在群设置中订阅需要的 Skills
3. 机器人在对话时会获得该 Skill 定义的工具描述
4. LLM 按需调用这些工具，执行对应的操作

## Skill 文件结构

每个 Skill 是 `skill/` 目录下的一个子文件夹，必须包含 `SKILL.md`：

```
skill/
└── my_skill/
    └── SKILL.md
```

### SKILL.md 格式

```markdown
---
name: 我的技能
description: 这个技能的功能描述
tags: [工具, API, 数据]
---

# 我的技能

这里是详细的技能说明和工具定义...
```

### 前置元数据（Frontmatter）

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 推荐 | Skill 显示名称 |
| `description` | 推荐 | Skill 功能描述（影响搜索）|
| `tags` | 可选 | 标签列表，逗号分隔 |

## 安装 Skills

### 方式一：通过 Web 面板上传

1. 启用 Web 面板（见 [Web 面板配置](/config/web-panel)）
2. 在面板的 Skills 管理页面上传 Skill 包（ZIP 格式）
3. 上传后自动解压到 `data/skills/` 目录

```json
{
  "WEB_PANEL_SKILL_UPLOAD_ENABLED": true,
  "WEB_PANEL_SKILL_UPLOAD_MAX_BYTES": 2097152,
  "LOCAL_SKILL_ROOT": "data/skills"
}
```

### 方式二：手动放置

直接将 Skill 文件夹放入 `skill/` 目录：

```
项目根目录/
└── skill/
    └── my_skill/
        └── SKILL.md
```

## 订阅和使用 Skills

### 群管理员订阅

1. 在群中发送 `/gadmin`
2. 进入 Skills 管理
3. 浏览可用 Skills 列表
4. 点击订阅想要的 Skills

### 配置 Skill 密钥

部分 Skills 需要配置私密信息（如外部 API Key）：

1. 订阅 Skill 后，在群设置的 Skill 配置中填写
2. 这些信息会加密存储，仅该群可用

### 取消订阅

在群管理面板的 Skills 管理中取消订阅即可。

## Skills 安全说明

::: warning
Skills 本质上是提供给 LLM 的工具描述和执行代码。只安装来自可信来源的 Skills。
:::

- Skills 由群管理员在群级别控制
- 不同群组可以订阅不同的 Skills 组合
- Skills 配置的密钥加密存储，不同群之间隔离

## Skills 目录扫描

Openmim 扫描以下位置的 Skills：

1. `skill/`（项目根目录）
2. `data/skills/`（数据目录，通过 `LOCAL_SKILL_ROOT` 配置）

每个直接子文件夹包含 `SKILL.md` 的都会被识别为一个 Skill。

## 在群设置中查看已订阅 Skills

通过 `/gadmin` → Skills 管理，可以查看：
- 当前群已订阅的 Skills 列表
- 每个 Skill 的名称和描述
- 配置状态（是否需要填写密钥）
