# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

`theseus-kit` 是一个 MCP 服务器，用于安全地检查、编辑、模板化和发布 TFRobot 配置。它通过标准 MCP 协议运行，同时暴露可选的 A2C-SMCP 兼容的 `window://` 和 `skill://` 资源。

当前状态：0.1.0 项目骨架阶段，配置变更工具尚未实现。详情见 [0.1.0 里程碑](https://github.com/A2C-SMCP/theseus-kit/milestone/1)。

## 开发命令

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync --locked --all-groups   # 安装全部依赖
uv run theseus-kit              # 启动 MCP 服务器（stdio 传输）
```

所有质量检查通过 Poe 任务运行：

```bash
uv run poe check       # 顺序执行 format-check → lint → typecheck
uv run poe ci          # CI 完整流程：lock-check → check → test-cov
uv run poe format      # ruff 格式化
uv run poe lint        # ruff 代码检查
uv run poe typecheck   # mypy 严格模式类型检查
uv run poe test        # 运行测试（pytest，asyncio 模式 auto）
uv run poe test-cov    # 测试覆盖率（需要 ≥80%）
uv run poe build       # 构建 wheel + sdist
uv run poe package-check  # twine 检查构建产物
```

任务列表可通过 `uv run poe --help` 查看。

### 运行单个测试

```bash
uv run pytest tests/test_auth_routing.py -v
uv run pytest tests/test_auth_routing.py::test_exchange_wire_user_pat -v
```

### E2E 测试（需要真实机器人）

```bash
THESEUS_E2E=1 \
THESEUS_ROBOT__ROBOT_ID=<rid> \
THESEUS_ROBOT__NAMESPACE=<ns> \
THESEUS_ROBOT__ROBOT_TYPE=tfrobot \
THESEUS_ROBOT__API_BASE_URL=https://api.<clusterDomain> \
THESEUS_ROBOT__MANAGER_BASE_URL=https://<manager-host> \
THESEUS_CREDENTIAL__KIND=user_pat \
THESEUS_CREDENTIAL__PAT=<tfp_...> \
THESEUS_CREDENTIAL__ROBOT_PUBLIC_ID=<orgSlug>:<employeeNo> \
uv run pytest tests/test_e2e_robot.py -v -m e2e
```

### 发布前准备

```bash
uv run poe ci && uv run poe build && uv run poe package-check
```

发布流程见 `docs/releasing.md`：版本号由 `bump-my-version` 管理，通过 GitHub Release + OIDC Trusted Publishing 发布到 PyPI/TestPyPI。

## 架构

### 分层设计

1. **MCP 表面层**（`server.py`）— FastMCP 实例，工具/资源声明
2. **应用服务层**（尚未实现）— 读、编辑、保存模板、发布用例
3. **TFRobot 客户端**（`transport.py`、`routing.py`、`tokens.py`、`credentials.py`）— 认证 HTTP 适配器，对接 `/v1/factory/**` 和 `/llms.txt`
4. **资源投影层** — `window://` 实时快照（`resources.py`）与 `skill://` 技能指南（`skills/`，12 个类别，三层结构）：`theseus` 总纲调度 → 阶段技能 `persona-interview` / `persona-optimize` / `plan-config` / `apply-config-plan` → 原子技能 `analyze-config` / `manage-topology` / `tune-config` / `save-template` / `publish-config` / `write-tfonto`，外加反馈闭环技能 `enhance`。阶段交接物在机器人工作区 `~/.theseus/workspaces/<slug>/`（画像 `<slug>.md` + `configs/` + `plans/`）

### 认证与路由（第 3 层，#17 已交付）

路由契约由 tfrs-operator 确认：

- **入口**：`https://api.<clusterDomain>`（Istio 网关），不从集群外访问集群内服务
- **X-TF-* 头部**（强制三项）：`X-TF-Namespace`、`X-TF-RobotId`、`X-TF-RobotType`，缺任何一项返回 400。值必须匹配 `^[a-z0-9-]+$`
- **身份标识分离**：令牌 `audience` 是 `robot:<Account.ID>`（数字），与 `X-TF-RobotId`（rid）是**不同标识符**，不可混用
- **凭证源**：`user_pat`（用户个人令牌）通过 `tfrs_auth.PatCredential` 抽象，使用 `AsyncCachingTokenSource`（缓存/单飞/近过期刷新/退避）进行 token-exchange 换发
- **不做 401 自动刷新重试**：机器人返回 401/403 时抛出 `AuthRejectedError`，不静默重试

### 关键模块

| 模块 | 职责 |
|------|------|
| `server.py` | FastMCP 组合根，`main()` 启动 stdio 传输 |
| `config.py` | `TheseusSettings`（pydantic-settings），从 `THESEUS_*` 环境变量或 `.env` 加载。包含 `RobotTarget`（路由目标）和 `CredentialConfig`（凭证，discriminated union） |
| `routing.py` | `RequestContext` — frozen dataclass，构建时校验 X-TF-* 值，`routing_headers()` 生成三项头部 |
| `transport.py` | `RobotAuth`（httpx.Auth 子类，注入 Bearer + X-TF-*）和 `RobotClient`（异步 HTTP 客户端，`from_settings()` 工厂方法，只读 helper：`get_llms_txt()`、`get_factory_doc()`） |
| `tokens.py` | `build_token_source()` — 组装 `AsyncCachingTokenSource`，Manager 换发端点固定 `/api/v1/oauth/token` |
| `credentials.py` | `build_credential()` — 从 theseus-kit 配置构造 `tfrs_auth` 的 `PatCredential`，通过 token-exchange 换发 robot-scoped JWT |
| `errors.py` | `TheseusError` 异常层级：`ConfigError` → `RoutingConfigError`、`CredentialError`、`ScopeOrAudienceError`、`ExchangeUnavailableError`（含 `retryable`）、`AuthRejectedError`、`SubscriptionFrozenError`（含 `renew_url`）、`RobotApiError`。`map_exchange_error()` 将 `tfrs_auth` 的异常映射为 theseus-kit 类型化错误 |
| `redaction.py` | 安全最后防线：用正则清除 PAT（`tfp_*`）和 JWT 形式的令牌，防止泄露到日志/错误消息 |
| `__init__.py` | 公共 API 导出，`__version__` 由 bump-my-version 管理 |

### 数据流（读路径）

```
TheseusSettings (env/.env)
  → build_credential() → tfrs_auth Credential
  → build_token_source() → AsyncCachingTokenSource (cache + refresh)
  → RequestContext (validate X-TF-*)
  → RobotAuth (httpx.Auth: Bearer + headers)
  → RobotClient.get*(path)
  → robot HTTP response
```

## 安全不变量

- 凭证保留在 MCP 服务器进程中，绝不进入工具输出、资源、日志或 SKILL 内容
- 所有 SecretStr 字段的默认 `repr` 不会暴露密钥；`redact_secrets()` 对所有错误消息做二次清洗
- `config:read`、`config:write`、`config:publish` 三个 scope 分别对应只读、写入、发布能力
- 发布是显式操作，不会作为草稿编辑或模板保存的副作用触发

## 三段式开发思路（SKILL 系统方法论）

机器人配置工作分三段：**画像 → 计划 → 落地**（技能侧：persona-interview / persona-optimize → plan-config → apply-config-plan，总纲 `theseus`）。开发与迭代遵循三条思路：

1. **一切按步骤可落地**：每阶段产出可物化的交接物（画像 `<slug>.md`、`configs/` 工件、`plans/` 计划），谁接手都能从文件续上、不依赖会话记忆。若某步骤步长过长导致上下文不稳定（如整树读入真实配置），**增加中间物化步骤**——物化数据按工作区结构策略落盘：`~/.theseus/workspaces/<slug>/`（画像根 + `configs/` + `plans/`，脚本与临时产物另定子目录）。
2. **自然语言 → Markdown → 结构化数据 → 工具调用**：数据形态从前到后逐步转换，落入真实系统的只有工具调用；过程中任何格式与数据结构都可灵活调整，按用户真实使用中暴露的问题**针对性强化的当前系统**（画像规范格式、Plan 文件格式等允许随实践演进）。
3. **上下游都可以提意见**：TFRobotServer / tfrs 相关库同为 @JQQ 维护——只要合理就提修改建议（如 llms.txt 的调整可商议后直接决策），不因「不是本仓库」而憋着。用户体验不佳时，调用 **enhance** 技能向本仓库（A2C-SMCP/theseus-kit）提交 Issue（问题 + 建议成对、上下文脱敏），由维护者审核处理。

## 测试约定

- `tests/_fakeserver.py`：真实 TCP socket 的假服务器（stdlib `ThreadingHTTPServer`），合并 Manager 令牌端点 + 机器人 API。记录所有请求，强制校验 X-TF-* 头部契约（缺头返回 400）
- `tests/test_auth_routing.py`：真实 TCP 往返测试，不做传输层 mock。覆盖交换有线格式、头部注入、令牌缓存/刷新/单飞、端点故障映射、redaction
- `tests/test_e2e_robot.py`：E2E 烟雾测试，通过 `THESEUS_E2E=1` 门控
- 所有异步测试通过 `pytest-asyncio` + `asyncio_mode = "auto"` 运行
- FakeClock / FakeSleep 用于确定性令牌过期测试

## 渐进披露契约（已冻结）

大配置的渐进披露采用方案 A：无状态索引 + 结构化选择器，覆盖 draft/template/online 三态。默认 8 KiB、硬上限 32 KiB。四种工具：`get_config_summary` → `list_config_nodes` → `get_config_detail` → `get_config_value`。规范见 `docs/progressive-disclosure.md`（#2 决定，不可更改除非重新开启该 Issue）。

## 关键设计文档

- `docs/architecture.md` — 架构基线、分层、安全不变量
- `docs/progressive-disclosure.md` — 大配置渐进披露规格（已冻结）
- `docs/auth-oauth-design.md` — OAuth（无 PAT）路径设计方案（#18，进行中）
- `docs/releasing.md` — 发布流程（中文）

## A2C-SMCP 兼容性

A2C-SMCP 的 URI scheme 和元数据是**附加扩展**，不能被标准 MCP 客户端所必需。协议变更必须保持标准 MCP 互操作性。
