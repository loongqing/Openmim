# Web 管理面板

Openmim 内置了基于 FastAPI 的 Web 管理面板，提供可视化的机器人管理界面。

## 启用 Web 面板

```json
{
  "WEB_PANEL_ENABLED": true,
  "WEB_PANEL_PORT": 7860,
  "WEB_PANEL_ACCESS_TOKEN": "your-secret-token"
}
```

启动后访问：`http://127.0.0.1:7860`

## 配置说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `WEB_PANEL_ENABLED` | `false` | 是否启用 Web 面板 |
| `WEB_PANEL_HOST` | `127.0.0.1` | 监听地址（`0.0.0.0` 对外开放）|
| `WEB_PANEL_PORT` | `7860` | 监听端口 |
| `WEB_PANEL_ACCESS_TOKEN` | `""` | 访问令牌（为空则不鉴权）|
| `WEB_PANEL_ALLOW_REMOTE_WITHOUT_TOKEN` | `false` | 是否允许无令牌的远程访问 |
| `WEB_PANEL_PUBLIC_BASE_URL` | `""` | 公开访问的 Base URL（用于反向代理场景）|
| `WEB_PANEL_RESTART_ENABLED` | `false` | 是否允许通过面板重启机器人 |
| `WEB_PANEL_RESTART_COMMAND` | `""` | 重启命令（如 `./start.sh`）|

## 功能模块

### 白名单管理

- 查看所有白名单群组
- 添加/删除群组

### 群组设置

可视化修改每个群组的所有设置项，包括：
- LLM 接口配置（BYOK）
- 聚焦/回复参数
- 问候开关
- 自定义人设

### 记忆管理

- 查看机器人的人格记忆
- 添加/删除记忆条目
- 查看用户画像记忆

### 插件管理

- 查看所有插件状态
- 启用/禁用插件

### Skills 管理

- 查看已安装的 Skills
- 上传新的 Skill 包

## Skills 上传配置

```json
{
  "WEB_PANEL_SKILL_UPLOAD_ENABLED": true,
  "WEB_PANEL_SKILL_UPLOAD_MAX_BYTES": 2097152,
  "LOCAL_SKILL_ROOT": "data/skills"
}
```

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `WEB_PANEL_SKILL_UPLOAD_ENABLED` | `true` | 是否允许上传 Skill |
| `WEB_PANEL_SKILL_UPLOAD_MAX_BYTES` | `2097152` | 上传文件最大大小（默认 2MB）|
| `LOCAL_SKILL_ROOT` | `data/skills` | Skills 存储目录 |

## 安全配置

### 本地访问（推荐）

仅在本机访问时，保持默认的 `127.0.0.1` 监听，无需令牌：

```json
{
  "WEB_PANEL_HOST": "127.0.0.1",
  "WEB_PANEL_PORT": 7860
}
```

### 反向代理部署（推荐用于远程访问）

1. 配置令牌：
```json
{
  "WEB_PANEL_HOST": "127.0.0.1",
  "WEB_PANEL_ACCESS_TOKEN": "your-strong-random-token",
  "WEB_PANEL_PUBLIC_BASE_URL": "https://your-domain.com/panel"
}
```

2. 配置 Nginx 反向代理：
```nginx
location /panel/ {
    proxy_pass http://127.0.0.1:7860/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
}
```

### 直接对外开放（不推荐）

若必须直接对外开放，请务必设置强令牌：

```json
{
  "WEB_PANEL_HOST": "0.0.0.0",
  "WEB_PANEL_PORT": 7860,
  "WEB_PANEL_ACCESS_TOKEN": "your-very-strong-random-token"
}
```

::: danger 安全提示
Web 面板拥有修改机器人所有配置的权限，包括 API Key。请务必设置强访问令牌，不要将面板暴露在公网上。
:::

## Telegram 内联面板

除了 Web 面板，Openmim 还提供完整的 Telegram 内联管理面板，无需打开浏览器即可管理。

### 管理员面板（`/admin`）

管理员专用，功能包括：
- 白名单管理
- 全局配置查看
- 机器人状态查看
- 记忆管理

### 群管理面板（`/gadmin`）

群管理员可用（需要在 Telegram 群中是管理员），功能包括：
- 群设置配置
- 聚焦参数调整
- API Key 配置（BYOK）
- 已启用 Skills 管理
- 工具开关

### Business 设置面板（`/settings`）

私聊使用，Business 模式相关设置。
