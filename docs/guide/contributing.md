# 贡献指南

感谢你对 Openmim 的关注！本指南介绍如何参与项目开发。

## 环境搭建

### 1. Fork 并克隆仓库

```bash
git clone https://github.com/你的用户名/Openmim.git
cd Openmim
```

### 2. 创建虚拟环境

```bash
python3 -m venv .venv
source .venv/bin/activate   # Linux / macOS
# .venv\Scripts\activate    # Windows
```

### 3. 安装依赖

```bash
# 运行时依赖
pip install -r requirements.txt

# 开发工具（pytest 等）
pip install -r requirements-dev.txt
```

### 4. 创建本地配置

复制最小配置到 `data/project_config.json`，参考[快速开始](/guide/getting-started)。  
开发时不必填写真实的 Bot Token，大部分单元测试不需要。

## 分支约定

| 分支 | 用途 |
|------|------|
| `main` | 稳定发布分支，不直接提交 |
| `dev` | 日常开发分支，PR 请提到这里 |
| `feat/<name>` | 新功能开发 |
| `fix/<name>` | Bug 修复 |
| `docs/<name>` | 纯文档改动 |

```bash
# 从 dev 拉新分支
git checkout dev
git pull
git checkout -b feat/my-feature
```

## 代码规范

### Python 版本

最低要求 **Python 3.11**，充分利用 `match/case`、`TypeAlias`、`tomllib` 等新特性。

### 风格

- 遵循 [PEP 8](https://pep8.org/)，行长限制 **120 字符**
- 类型注解：所有公共函数/方法必须有参数和返回值类型注解
- 导入排序：标准库 → 第三方 → 本项目，组间空一行
- 异步：I/O 相关函数一律用 `async/await`，避免在协程中使用阻塞调用

### 注释与文档字符串

- 模块、类、公共方法需有 docstring
- 复杂逻辑旁写行内注释（中文或英文均可）
- 不要保留无意义的占位注释（`# TODO` 除外，但须写清楚内容）

### 敏感数据

**绝不**将以下内容提交进仓库：

- `data/project_config.json`（已在 `.gitignore` 中排除）
- 任何 API Key、Bot Token
- `*.sqlite3` 数据库文件
- `.encryption_key`

## 运行测试

```bash
# 运行全部测试
pytest

# 只跑某个文件
pytest tests/test_focus_scoring.py

# 带输出详情
pytest -v

# 只跑带某关键字的测试
pytest -k "focus"
```

测试使用 `pytest-asyncio`，异步测试函数使用 `async def test_*`，配置 `asyncio_mode = auto`（见 `pytest.ini`）无需额外装饰器。

### 写新测试

- 测试文件放在 `tests/` 目录
- 文件名格式：`test_<模块名>.py`
- 优先对**纯函数**（无副作用）写单元测试，例如 `llm/focus_scoring.py` 中的 `parse_focus_score`
- 需要 Mock 的外部依赖：Telegram Bot、LLM API 调用等，使用 `unittest.mock`

## 项目架构速览

提交代码前，先理解各层的职责边界：

```
handlers/     ← Telegram Update 入口，尽量薄，业务逻辑下沉到 services/
services/     ← 业务编排，组合 stores/features/llm/ 的能力
features/     ← 独立功能实现（无 Telegram 依赖）
stores/       ← 数据持久化，只管读写，不做业务判断
llm/          ← LLM 调用，只管 prompt 构建和 API 通信
integrations/ ← 第三方服务（沙箱、搜索、图生等）
plugins/      ← 插件系统，通过 Hook 扩展主流程
app_config/   ← 配置加载，不做业务逻辑
```

**常见错误**：
- ❌ 在 `stores/` 中调用 `llm/` 的接口（循环依赖风险）
- ❌ 在 `handlers/` 中写超过 20 行的业务逻辑（应下沉到 `services/`）
- ❌ 在 `features/` 中直接 import `telegram`（`features/` 应与 Telegram 解耦）

## 添加新功能

### 添加新的 LLM 工具

1. 在 `integrations/` 或 `llm/tool_plugins/` 中实现工具逻辑
2. 在 `plugins/builtin/` 下创建对应插件文件，定义 `ToolSpec` 并导出 `PLUGIN`
3. 在文档 [Agentic 工具能力](/features/agentic) 中补充说明
4. 写测试：至少覆盖参数校验和主要执行路径

### 添加新的群组设置项

1. 在 `stores/group_settings_store.py` 的 `DEFAULT_GROUP_SETTINGS` 和 `SETTING_LABELS` / `SETTING_DESCRIPTIONS` 中添加
2. 在 `handlers/group_admin_panel.py` 中添加对应的管理面板入口
3. 在文档 [群组设置](/config/group-settings) 中补充该配置项

### 添加新的全局配置项

1. 在 `app_config/config.py` 中用 `_cfg_get()` 定义（自动支持 JSON + 环境变量两种读取方式）
2. 在文档 [配置总览](/config/overview) 中补充

## 提交 Pull Request

### Commit 信息格式

遵循 [Conventional Commits](https://www.conventionalcommits.org/)：

```
<type>(<scope>): <subject>

[可选的正文]
[可选的 footer]
```

常用类型：

| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 纯文档改动 |
| `refactor` | 重构（不改变功能） |
| `test` | 添加或修改测试 |
| `chore` | 构建脚本、依赖更新等杂项 |
| `perf` | 性能优化 |

示例：

```
feat(focus): 支持 all_message 注意力模式下的 mixed 回退

当混合模式下全消息注意力已回复时，跳过单消息评分，
避免同一消息触发两次完整 LLM 调用。
```

### PR 检查清单

提交 PR 前请自检：

- [ ] 代码在本地能正常运行
- [ ] 新增/修改的功能有对应测试，且 `pytest` 全部通过
- [ ] 没有提交任何密钥、配置文件、数据库文件
- [ ] 必要时更新了文档（`docs/` 目录）
- [ ] Commit 信息符合 Conventional Commits 规范

## 报告问题

提 Issue 时请包含：

1. **Openmim 版本**（`git log --oneline -1` 的输出）
2. **Python 版本**（`python3 --version`）
3. **复现步骤**：最小可复现的操作序列
4. **期望行为** vs **实际行为**
5. **相关日志**（注意脱敏，去掉 Token、API Key 等）

## 其他问题

欢迎在 Issues 中提问，或通过 Telegram 联系维护者。
