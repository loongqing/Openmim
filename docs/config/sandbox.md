# 沙箱配置

代码沙箱让 LLM 可以在隔离环境中执行 Python 和 Shell 代码。Openmim 支持三种沙箱后端。

## 选择沙箱后端

通过 `SANDBOX_PROVIDER` 配置项选择：

```json
{
  "SANDBOX_PROVIDER": "e2b"
}
```

可选值：`e2b`（默认）、`shipyard`、`local`

---

## E2B（推荐）

[E2B](https://e2b.dev) 提供云端隔离的代码执行环境，安全可靠。

### 配置

```json
{
  "SANDBOX_PROVIDER": "e2b",
  "E2B_API_KEY": "e2b_...",
  "E2B_TIMEOUT": 120
}
```

### 获取 API Key

1. 注册 [e2b.dev](https://e2b.dev)
2. 在控制台创建 API Key
3. 填入配置文件

### 特性

- 每个群组独立沙箱实例（按 chat_id 隔离）
- 10 分钟空闲自动销毁，下次使用时重新创建
- 同一群内的操作共享文件系统（可安装包、保存文件）
- 规格：1 vCPU / 512 MB（E2B 基础模板）

### 费用

E2B 有免费额度，超出后按使用量计费。日常群聊使用量通常在免费额度内。

---

## Shipyard（自托管）

[Shipyard](https://github.com/AstrBotDevs/shipyard) 是可自托管的沙箱服务，适合私有部署场景。

### 配置

```json
{
  "SANDBOX_PROVIDER": "shipyard",
  "SHIPYARD_ENDPOINT": "http://127.0.0.1:8156",
  "SHIPYARD_ACCESS_TOKEN": "your-secret-token",
  "SHIPYARD_TTL": 3600,
  "SHIPYARD_MAX_SESSIONS": 1,
  "SHIPYARD_CPUS": 1.0,
  "SHIPYARD_MEMORY": "512m",
  "SHIPYARD_EXEC_TIMEOUT": 180
}
```

### 参数说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `SHIPYARD_ENDPOINT` | `http://127.0.0.1:8156` | Shipyard 服务地址 |
| `SHIPYARD_ACCESS_TOKEN` | `secret-token` | 访问令牌 |
| `SHIPYARD_TTL` | `3600` | 会话存活时间（秒）|
| `SHIPYARD_MAX_SESSIONS` | `1` | 最大并发会话数 |
| `SHIPYARD_CPUS` | `1.0` | CPU 核心数 |
| `SHIPYARD_MEMORY` | `512m` | 内存限制 |
| `SHIPYARD_EXEC_TIMEOUT` | `180` | 单次执行超时（秒）|

---

## Local（本机执行）

在本机的隔离工作目录中直接执行代码。

::: danger 安全警告
Local 沙箱**直接在宿主机**执行代码，没有容器隔离！  
仅在**完全信任所有用户**的私有环境中使用。  
不要在公共群组中启用此选项。
:::

### 配置

```json
{
  "SANDBOX_PROVIDER": "local",
  "LOCAL_SANDBOX_WORKDIR": "data/local_sandbox",
  "LOCAL_SANDBOX_TIMEOUT": 180
}
```

### 参数说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `LOCAL_SANDBOX_WORKDIR` | `data/local_sandbox` | 代码执行工作目录 |
| `LOCAL_SANDBOX_TIMEOUT` | `180` | 执行超时（秒）|

---

## 通用设置

无论使用哪种沙箱后端，以下限制都适用：

| 参数 | 值 | 说明 |
|------|----|------|
| 单次执行最大输出 | 4000 字符 | 超出部分截断 |
| 工具返回给 LLM 的最大字符 | `TOOL_RESULT_MAX_CHARS`（默认 6000）| 二次截断 |
| 同一群内最大并发 | 1 | 串行执行，避免状态污染 |

## 沙箱对应工具

| 工具名 | 功能 |
|--------|------|
| `run_python` | 执行 Python 代码 |
| `run_shell` | 执行 Shell 命令 |

这两个工具由沙箱插件注册，可在群设置中单独禁用。

## 禁用沙箱功能

在全局配置中禁用沙箱插件：

```json
{
  "PLUGINS_DISABLED": ["sandbox"]
}
```

或在群管理面板中禁用 `run_python`、`run_shell` 工具。
