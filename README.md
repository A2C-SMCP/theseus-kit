# theseus-kit

`theseus-kit` 是一个 MCP 服务器，用于安全地检查、编辑、模板化和发布 TFRobot 配置。
通过标准 MCP 协议运行，同时暴露可选的 A2C-SMCP 兼容 `window://` 和 `skill://` 资源。

## 目录

- [快速开始](#快速开始)
- [MCP 工具](#mcp-工具)
- [Skill 资源](#skill-资源)
- [使用指南](#使用指南)
  - [配置方式](#配置方式)
  - [MCP Client 集成](#mcp-client-集成)
- [工作原理](#工作原理)
  - [架构分层](#架构分层)
  - [认证体系](#认证体系)
  - [数据流](#数据流)
  - [安全模型](#安全模型)
- [开发](#开发)

## 快速开始

**环境要求**：Python 3.11+，[uv](https://docs.astral.sh/uv/)。

```bash
# 安装
uv sync --locked --all-groups

# 启动 MCP 服务器（stdio 传输）
uv run theseus-kit
```

### 最小配置

通过环境变量或 `.env` 文件配置目标机器人和凭证：

```bash
# 机器人路由信息
export THESEUS_ROBOT__ROBOT_ID="my-robot"
export THESEUS_ROBOT__NAMESPACE="default"
export THESEUS_ROBOT__ROBOT_TYPE="tfrobot"
export THESEUS_ROBOT__API_BASE_URL="https://api.example.com"
export THESEUS_ROBOT__MANAGER_BASE_URL="https://manager.example.com"

# 凭证（二选一）
# 方式 1：用户个人令牌（推荐——theseus-kit 是人管配置的工具，非 A2A）
export THESEUS_CREDENTIAL__KIND="user_pat"
export THESEUS_CREDENTIAL__PAT="tfp_xxx"
export THESEUS_CREDENTIAL__ROBOT_PUBLIC_ID="myorg:000042"

# 方式 2：OAuth 2.0（MCP Client 驱动授权）
export THESEUS_CREDENTIAL__KIND="oauth"
export THESEUS_CREDENTIAL__AUTHORIZATION_SERVER="https://manager.example.com"
export THESEUS_CREDENTIAL__SCOPES="config:read config:write"
```

## MCP 工具

theseus-kit 提供 **9 个 MCP 工具**，覆盖配置的完整生命周期：

### 只读工具

| 工具 | 说明 | 所需 Scope |
|------|------|------------|
| `get_config_summary` | 获取机器人身份 + 三态（草稿/模板/线上）配置概览 | `config:read` |
| `list_config_nodes` | 在配置森林的任意层级列出子节点，支持游标分页和过滤 | `config:read` |
| `get_config_detail` | 读取单个配置节点的有界、脱敏详情（默认 8 KiB，上限 32 KiB） | `config:read` |
| `get_template` | 按 ID 读取模板（支持仅元数据或完整详情两种模式） | `config:read` |
| `get_llms_doc` | 读取机器人运行时 `llms.txt` Schema 文档（索引 + 具体页面） | `config:read` |

### 变更工具

| 工具 | 说明 | 所需 Scope |
|------|------|------------|
| `update_draft` | 更新草稿配置项，支持 `expected_hash` 乐观并发控制 | `config:write` |
| `validate_draft` | 验证草稿配置是否满足上线条件（全量预检或指定节点），返回逐节点校验结果 | `config:write` |
| `save_template` | 将草稿子树保存为可复用模板 | `config:write` |
| `publish_config` | 将所有草稿发布到线上，要求 `acknowledge_publish=true` 显式确认 | `config:publish` |

### 工具协作流程

```
get_config_summary        ← 入口：发现有哪些配置
    ↓
list_config_nodes         ← 导航：探索配置树
    ↓
get_config_detail         ← 读取：获取具体内容（含 content_hash）
    ↓
get_llms_doc              ← Schema：了解字段/校验规则
    ↓
update_draft              ← 修改：带冲突保护的写入
    ↓
validate_draft            ← 校验：发布前预检
    ↓
publish_config            ← 发布：显式确认 + root_hash 校验
```

## Skill 资源

theseus-kit 通过 `skill://` 资源暴露 **7 个中文技能指南**，为 LLM 提供结构化的操作流程：

| Skill | 资源 URI | 说明 |
|-------|----------|------|
| 分析配置 | `skill://com.a2c-smcp.theseus-kit/analyze-config` | 确认目标 → 全局概览 → LLMTEXT 技术选型 → 优化大纲 |
| 管理拓扑 | `skill://com.a2c-smcp.theseus-kit/manage-topology` | 创建节点 → 删除节点 → 修改引用关系，结合 LLMTEXT 进行技术选型 |
| 调优配置 | `skill://com.a2c-smcp.theseus-kit/tune-config` | 读取现状 → 理解字段约束 → 合理化修改 → 校验 → 冲突处理 |
| 保存模板 | `skill://com.a2c-smcp.theseus-kit/save-template` | 识别可复用节点 → 命名 → 保存 → 验证 |
| 发布配置 | `skill://com.a2c-smcp.theseus-kit/publish-config` | 预检 → 审批 → 发布 → 验证，全局不可逆操作 |
| 用户画像采访 | `skill://com.a2c-smcp.theseus-kit/persona-interview` | 采访需求方获取画像：职业领域 / 专业技能 / 日常工作 / 知识结构三张清单 / 能力草图候选 |
| 写 TFOnto | `skill://com.a2c-smcp.theseus-kit/write-tfonto` | 知识结构 + 能力草图 → 平台可导入的 `.tfo`（概念/属性/关系 → Def，能力 → Function/Action），附带独立校验器 `scripts/validate_tfonto.py` 与完整范例 |

每个 Skill 定义了允许使用的工具、所需 Scope、标准操作流程和关键约束，
确保 LLM 按「最佳实践」而非自由发挥来操作配置。旧版别名
（`inspect-robot-config` / `edit-robot-draft` / `publish-robot-config`）以 deprecated 标记保留。

### 实时状态窗口：`window://`

2 个 `window://` 资源提供配置状态的实时快照，在每次变更操作后自动通知更新：

| 资源 | URI | 说明 |
|------|-----|------|
| 配置摘要 | `window://com.a2c-smcp.theseus-kit/config/summary` | 机器人身份 + 三态概览，每次变更后刷新 |
| 最近详情 | `window://com.a2c-smcp.theseus-kit/config/recent` | 最近打开的配置详情，无打开时返回空状态 |

## 使用指南

### 配置方式

#### 1. 显式凭证模式（user_pat）

有明确配置的凭证时，theseus-kit 走「凭证换发」路径：用户的 PAT 作为 subject_token，Manager 通过 token-exchange（RFC 8693）换发目标机器人 scope 的短 JWT。这是**人管配置**的正确鉴权模型。

```
配置的凭证 → Manager 换发端点 → 短 JWT（aud=robot:{public_id}）
→ 注入 X-TF-* 路由头 → 调用 TFRobotServer
```

这是**确定性最强**的模式：凭证固定，无需浏览器交互，适合自动化 / CI / 后台场景。

#### 2. OAuth 2.0 模式

无显式凭证时，走 MCP 标准 OAuth 授权：

```
MCP Client → TFRSManager AS（Authorization Code + PKCE）
→ OAuth AS token（aud={issuer}/robots/<id>, typ=at+jwt）
→ theseus-kit 校验（tfrs-auth RS256 + JWKS + scope）
→ 直传 TFRobotServer（无需换发）
```

适合**交互式使用**：用户在 MCP Client 中完成授权，无需手动管理令牌。

> **凭证选择不变式**：显式凭证（user_pat）始终优先；OAuth 仅在无显式凭证时启用。
> 配置错误不会静默降级，而是抛出明确的 `ConfigError`。

### MCP Client 集成

在 Claude Desktop 或任意兼容 MCP Client 的配置中添加：

```json
{
  "mcpServers": {
    "theseus-kit": {
      "command": "uv",
      "args": ["run", "theseus-kit"],
      "env": {
        "THESEUS_ROBOT__ROBOT_ID": "my-robot",
        "THESEUS_ROBOT__NAMESPACE": "default",
        "THESEUS_ROBOT__ROBOT_TYPE": "tfrobot",
        "THESEUS_ROBOT__API_BASE_URL": "https://api.example.com",
        "THESEUS_ROBOT__MANAGER_BASE_URL": "https://manager.example.com",
        "THESEUS_CREDENTIAL__KIND": "user_pat",
        "THESEUS_CREDENTIAL__PAT": "tfp_xxx",
        "THESEUS_CREDENTIAL__ROBOT_PUBLIC_ID": "myorg:000042"
      }
    }
  }
}
```

OAuth 模式下的配置：

```json
{
  "mcpServers": {
    "theseus-kit": {
      "command": "uv",
      "args": ["run", "theseus-kit"],
      "env": {
        "THESEUS_ROBOT__ROBOT_ID": "my-robot",
        "THESEUS_ROBOT__NAMESPACE": "default",
        "THESEUS_ROBOT__ROBOT_TYPE": "tfrobot",
        "THESEUS_ROBOT__API_BASE_URL": "https://api.example.com",
        "THESEUS_ROBOT__MANAGER_BASE_URL": "https://manager.example.com",
        "THESEUS_CREDENTIAL__KIND": "oauth",
        "THESEUS_CREDENTIAL__AUTHORIZATION_SERVER": "https://manager.example.com",
        "THESEUS_CREDENTIAL__SCOPES": "config:read config:write"
      }
    }
  }
}
```

## 工作原理

### 架构分层

```
┌──────────────────────────────────────────┐
│  MCP 表面层（server.py）                  │
│  FastMCP · 9 工具 · 2 window:// 资源      │
│  3 skill:// 资源 · OAuth PRM 路由         │
├──────────────────────────────────────────┤
│  应用服务层（services/）                   │
│  ConfigReader · DraftEditor · Publisher   │
│  DraftValidator · TemplateSaver           │
│  LlmsDocReader                            │
├──────────────────────────────────────────┤
│  资源投影层（resources/ · skills/）        │
│  window:// 实时快照 · skill:// 中文指南    │
├──────────────────────────────────────────┤
│  TFRobot 客户端（transport.py）            │
│  RobotClient · RobotAuth · 令牌源双路径   │
├──────────────────────────────────────────┤
│  认证层（oauth.py · tokens.py ·           │
│         credentials.py）                  │
│  JwtVerifier 适配 · 令牌换发 · AS 发现    │
├──────────────────────────────────────────┤
│  模型层（models.py · config.py ·          │
│         routing.py · errors.py）          │
│  TFSResponse · 渐进披露模型 · 路由上下文  │
└──────────────────────────────────────────┘
```

### 认证体系

theseus-kit 支持**两条凭证路径**，在 `RobotClient` 层自然收敛：

#### 路径 1：显式凭证（user_pat）

```
UserPatConfig
  → build_credential()           # 构造 tfrs-auth PatCredential
  → AsyncCachingTokenSource      # token-exchange + 缓存 + single-flight + 临期刷新 + 退避
  → RobotAuth                    # 注入 Authorization: Bearer <jwt> + X-TF-*
  → TFRobotServer
```

#### 路径 2：OAuth 2.0

```
OAuthConfig
  → TheseusTokenVerifier         # tfrs-auth JwtVerifier → MCP SDK TokenVerifier
  → RFC 8414 AS 发现             # fetch_as_metadata() → jwks_uri + issuer
  → 校验: RS256 + scope + exp    # JwtVerifier.verify(required_scope=)
  → StaticTokenSource            # 静态持有已验 token，不换发
  → RobotAuth                    # 注入 Authorization: Bearer + X-TF-*
  → TFRobotServer                # 原生接受 aud={issuer}/robots/<id> + typ=at+jwt
```

#### 两条路径对比

| | user_pat | OAuth 2.0 |
|---|---|---|
| 令牌来源 | Manager 换发（RFC 8693 token-exchange） | MCP Client 授权后直传 |
| 换发 | 是 | 否 |
| 缓存/刷新 | AsyncCachingTokenSource 内置 | MCP Client 侧负责 |
| 适用场景 | 人管配置（自动化 / CI / 后台） | 交互式使用 |

### 数据流

以**读取配置详情**为例，一次完整的请求经过以下路径：

```
1. MCP Client 调用 get_config_detail(locator="...")

2. server.py 工具处理函数
   → ConfigReader(robot_id=...).get_detail(client, locator, depth, max_bytes)

3. RobotClient.from_settings(settings)
   → RobotAuth(token_source, context).async_auth_flow()
     → token_source.token() 获取 Bearer（换发或静态）
     → context.routing_headers() 获取 X-TF-Namespace / X-TF-RobotId / X-TF-RobotType
   → httpx.AsyncClient 发送 GET 请求到 TFRobotServer

4. TFRobotServer 响应的 JSON 被反序列化为 TFSResponse[ConfigDetail]
   → code / message / data 信封解包
   → ConfigDetail 包含 content_hash / bytes_returned / truncated / redacted[] 等元数据

5. 结果返回给 MCP Client
   → 同时更新 window:// 资源的 last_locator（用于 recent 快照）
```

### 安全模型

- **令牌不出进程**：所有凭证保留在 MCP 服务器进程中，绝不进入工具输出、资源、日志或 SKILL 内容
- **SecretStr 保护**：pydantic `SecretStr` 字段默认 `repr` 不暴露密钥
- **redaction 最后防线**：`redaction.py` 用正则清除 PAT（`tfp_*`）、JWT、OAuth token/auth-code/state 形式的令牌
- **显式发布确认**：`publish_config` 要求 `acknowledge_publish=true`，防止意外发布
- **乐观并发控制**：`update_draft` 的 `expected_hash` 和 `publish_config` 的 `expected_root_hash` 防止丢失更新
- **渐进披露**：配置读取默认 8 KiB / 硬上限 32 KiB，敏感字段自动脱敏为 `<<redacted>>`

### 关键模块

| 模块 | 职责 |
|------|------|
| `server.py` | FastMCP 组合根，工具/资源注册，OAuth PRM 路由 |
| `config.py` | `TheseusSettings`（pydantic-settings），三种凭证配置的判别联合 |
| `transport.py` | `RobotClient` + `RobotAuth`（Bearer + X-TF-* 注入）+ `StaticTokenSource` |
| `oauth.py` | `TheseusTokenVerifier`（tfrs-auth → MCP SDK 适配）+ RFC 8414 发现 |
| `tokens.py` | `build_token_source()` — `AsyncCachingTokenSource` 组装 |
| `credentials.py` | `build_credential()` — user_pat → tfrs-auth PatCredential |
| `routing.py` | `RequestContext` — X-TF-* 头部构建与校验 |
| `errors.py` | 类型化异常层级 + `map_exchange_error()` |
| `redaction.py` | 令牌脱敏最后防线（PAT / JWT / OAuth token / code / state） |
| `models.py` | `TFSResponse[T]` 信封 + 渐进披露数据模型 |
| `services/` | `ConfigReader`、`DraftEditor`、`DraftValidator`、`Publisher`、`TemplateSaver`、`LlmsDocReader` |
| `resources/` | `window://` 实时快照构建 |
| `skills/` | `skill://` 静态中文指南 |

## 开发

```bash
uv sync --locked --all-groups   # 安装全部依赖
uv run poe check                # 顺序执行 format-check → lint → typecheck
uv run poe ci                   # CI 完整流程：lock-check → check → test-cov
uv run poe test                 # 运行测试（pytest，asyncio 模式 auto）
uv run poe test-cov             # 测试覆盖率（需要 ≥80%）
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

发布流程见 [发布文档](docs/releasing.md)：版本号由 `bump-my-version` 管理，通过 GitHub Release + OIDC Trusted Publishing 发布到 PyPI。

## 关键设计文档

- `docs/architecture.md` — 架构基线、分层、安全不变量
- `docs/progressive-disclosure.md` — 大配置渐进披露规格（已冻结）
- `docs/auth-oauth-design.md` — OAuth 2.0 授权登录技术设计
- `docs/releasing.md` — 发布流程

## 协议参考

- [Model Context Protocol](https://modelcontextprotocol.io/)
- [A2C-SMCP protocol](https://github.com/A2C-SMCP/a2c-smcp-protocol)
- RFC 8414 — OAuth 2.0 Authorization Server Metadata
- RFC 8693 — OAuth 2.0 Token Exchange
- RFC 8707 — Resource Indicators for OAuth 2.0
- RFC 9068 — JSON Web Token (JWT) Profile for OAuth 2.0 Access Tokens
- RFC 9728 — OAuth 2.0 Protected Resource Metadata

## License

MIT
