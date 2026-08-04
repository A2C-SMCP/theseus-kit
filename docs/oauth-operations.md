# OAuth 运行手册

本文档指导用户完成 theseus-kit 的 OAuth 2.0/2.1 登录、重新登录与排错。

## 凭证选择：PAT 还是 OAuth？

theseus-kit 支持三种凭证来源，由 `THESEUS_CREDENTIAL__KIND` 显式选择：

| 凭证类型 | 适用场景 | 配置方式 |
|---------|---------|---------|
| `client_credentials` | 自动化/CI/服务间调用，机器人有机器凭证 | `THESEUS_CREDENTIAL__KIND=client_credentials` |
| `user_pat` | 用户个人令牌直接使用 | `THESEUS_CREDENTIAL__KIND=user_pat` |
| `oauth` | 交互式使用，无 PAT/机器凭证 | `THESEUS_CREDENTIAL__KIND=oauth` |

**选择逻辑**：`CredentialConfig` 是 discriminated union，由 `kind` 字段决定激活哪个
variant，同一时刻只有一种凭证生效。不存在"同时配置多种"的情况——pydantic 只实例化
匹配 `kind` 的 variant。

**PAT 配置示例**（`.env` 或环境变量）：

```bash
# 机器人自身的机器凭证（自管理：callee == caller）
THESEUS_ROBOT__ROBOT_ID=my-robot
THESEUS_ROBOT__NAMESPACE=default
THESEUS_ROBOT__ROBOT_TYPE=tfrobot
THESEUS_ROBOT__API_BASE_URL=https://api.example.com
THESEUS_ROBOT__MANAGER_BASE_URL=https://manager.example.com
THESEUS_CREDENTIAL__KIND=client_credentials
THESEUS_CREDENTIAL__MACHINE_CLIENT_ID=myorg:12345
THESEUS_CREDENTIAL__MACHINE_CLIENT_SECRET=tfp_xxxxxxxx
```

**OAuth 配置示例**（`.env` 或环境变量）：

```bash
# OAuth 模式：kind=oauth，由 MCP Client 驱动授权
# authorization_server 和 manager_base_url 可指向同一 TFRSManager 实例，但用途不同：
#  — manager_base_url：PAT 路径的 Manager 换发端点（OAuth 路径不使用，但 RobotTarget 字段必填）
#  — authorization_server：AS 元数据发现 + JWKS 公钥获取
THESEUS_ROBOT__ROBOT_ID=my-robot
THESEUS_ROBOT__NAMESPACE=default
THESEUS_ROBOT__ROBOT_TYPE=tfrobot
THESEUS_ROBOT__API_BASE_URL=https://api.example.com
THESEUS_ROBOT__MANAGER_BASE_URL=https://manager.example.com
THESEUS_CREDENTIAL__KIND=oauth
THESEUS_CREDENTIAL__AUTHORIZATION_SERVER=https://manager.example.com
THESEUS_CREDENTIAL__SCOPES=config:read config:write
THESEUS_CREDENTIAL__RESOURCE_SERVER_URL=https://theseus.example.com
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `authorization_server` | 是 | TFRSManager OAuth AS 地址，用于 PRM 发现 + JWKS 获取 |
| `scopes` | 否 | 空格分隔的 scope 列表，默认 `config:read` |
| `client_id` | 否 | 预注册的 client_id，留空走 DCR / CIMD（MCP SDK 自动处理） |
| `redirect_uri` | 否 | STDIO 外部回调 URI（Topology B，尚未实现） |
| `resource_server_url` | 否 | theseus-kit HTTP 入口地址，用于 token audience 校验；留空跳过 |

## 登录流程（Topology A — MCP Client 驱动）

这是 OAuth 的主要使用方式，适用于通过 HTTP 运行的 MCP Client（如 Claude Desktop、
VS Code MCP 扩展等）。

### 首次登录

1. **启动 theseus-kit**（配置 `kind=oauth`）：
   ```bash
   uv run theseus-kit
   ```

2. **MCP Client 发现 PRM**：Client 读取
   `/.well-known/oauth-protected-resource`，获取
   `authorization_servers=[TFRSManager AS]` 和 `scopes_supported`。

3. **MCP Client 发起授权**：Client 使用 MCP SDK 内置的 `OAuthClientProvider`：
   - RFC 8414 AS 发现（`/.well-known/oauth-authorization-server`）
   - 生成 PKCE code_challenge（S256）+ 随机 state
   - 打开浏览器跳转到 TFRSManager AS 授权页（`/authorize`）

4. **用户授权**：在浏览器中登录 TFRSManager，确认授权 scope（如 `config:read`）。

5. **回调 + 换码**：AS 回调 MCP Client → Client 用 auth_code + PKCE verifier
   换 access token（`POST /token`）+ refresh token。

6. **Bearer 校验**：MCP Client 后续请求带 `Authorization: Bearer <access_token>`。
   theseus-kit 用 `TheseusTokenVerifier`（RS256 + JWKS，`tfrs_auth.JwtVerifier`）
   本地校验：签名、`iss`、`aud`、scope、过期。校验通过后提取用户身份（`sub`）。

7. **令牌直传**：校验后的 token 经 `StaticTokenSource` 注入 `RobotAuth`，
   作为 Bearer 随 `X-TF-*` 路由头转发给 TFRobotServer。TFRobotServer 原生接受
   `aud={issuer}/robots/{id}` + `typ=at+jwt` 格式，无需 theseus-kit 侧换发。

### 架构一览

```
MCP Client                    theseus-kit (RS)              TFRobotServer
    │                              │                              │
    ├─ OAuth auth-code+PKCE ──▶ TFRSManager AS                  │
    │                              │                              │
    ├─ Bearer: access_token ──▶   │                              │
    │                        TheseusTokenVerifier                │
    │                        (RS256 + JWKS 本地校验)              │
    │                              │                              │
    │                              ├─ Bearer + X-TF-* ───────▶   │
    │                              │                        (验签+scope)
    │                              │◀── 响应 ────────────────   │
    │◀── 结果 ───────────────────  │                              │
```

**关键特性**：
- theseus-kit **不做 token 换发**，OAuth AS token 直传机器人
- token 校验在 theseus-kit 进程内完成（RS256 + JWKS），不依赖 AS 内省端点
- 缓存/刷新由 MCP Client 侧（MCP SDK）负责
- `StaticTokenSource` 持有已验 token，不做换发/缓存/刷新

## 重新登录

### Token 自动刷新

access token 过期时，MCP SDK 内置的 `OAuthClientProvider` 自动用 refresh token
换取新的 access token — 用户无感知。

### 手动重新授权

以下情况需要用户重新授权：
- refresh token 也过期（长期未使用）
- refresh token 被撤销
- scope 不足需要升级（step-up）— MCP SDK 支持 403 `insufficient_scope` step-up

重新授权流程同首次登录：MCP Client 重新发起 auth-code+PKCE 流程。

### 错误提示

当 token 无效且无法刷新时，theseus-kit 返回：
- `AuthRejectedError`（401/403）：凭证无效或 scope 不足
- 错误消息经过 `redact_secrets()` 清洗，不会泄露 token 内容

## STDIO 模式（Topology B）

> **状态：尚未实现（S4）**。依赖 tfrs-auth 的 P1 上游能力「通用 OAuth 2.1 采集客户端」。
> 详见 [`docs/upstream/tfrs-auth-oauth-feature-request.md`](upstream/tfrs-auth-oauth-feature-request.md)。

STDIO 模式下 MCP Client 无 HTTP 能力，theseus-kit 需自驱授权：

- 启动本地回调 listener（`127.0.0.1:<随机端口>`）
- 构造 `/authorize` 请求（PKCE + state + resource）
- 打开默认浏览器到 TFRSManager AS 授权页
- 收回调 → 兑 auth_code → 安全缓存 refresh token（0600 文件 / OS keychain）
- 后续请求用 refresh token 换 access token → 直传机器人

实现后，STDIO 下的过期/撤销会通过 `ReauthRequiredError`（届时新增的错误类型）
提示用户重新登录。

## 排错指南

### 常见错误及处理

| 错误 | 路径 | 可能原因 | 解决方法 |
|------|------|---------|---------|
| `ConfigError`：「OAuth credentials bypass the token exchange pipeline」 | `[OAuth]` | PAT 路径代码收到了 OAuth 配置（防御性守卫） | 不需处理，OAuth 路径走 `StaticTokenSource` |
| `ConfigError`：`authorization_server` 格式错误 | `[OAuth]` | URL 未以 `http://` 或 `https://` 开头 | 修正为完整 URL |
| `AuthRejectedError`（401） | `[通用]` | Token 签名无效或过期 | 检查 AS 与 theseus-kit 时钟同步；重新登录 |
| `AuthRejectedError`（403） | `[通用]` | Scope 不足 | 确认 `scopes` 配置包含所需权限（只读=`config:read`）|
| `SubscriptionFrozenError`（402） | `[PAT]` | Manager 换发返回 402（组织订阅冻结） | 续费后重试 |
| `ExchangeUnavailableError` | `[PAT]` | TFRSManager 换发端点不可达 | 检查网络连接 + `manager_base_url` 配置；稍后重试 |
| OAuth 授权页面打不开 | `[OAuth]` | TFRSManager AS 不可达 | 确认 `authorization_server` URL 正确 |
| JWKS 获取失败 | `[OAuth]` | AS 发现返回的 `jwks_uri` 不可达 | 检查 AS 配置；确认 theseus-kit 可访问 `jwks_uri` |
| `RoutingConfigError` | `[通用]` | `robot_id`/`namespace`/`robot_type` 值不合法 | 确认值仅含小写字母、数字、连字符 `^[a-z0-9-]+$` |

### 调试技巧

1. **查看 MCP Client 日志**：大多数 MCP Client 会记录 OAuth 流程的详细日志
2. **校验 AS 发现端点**：
   ```bash
   curl https://<manager.example.com>/.well-known/oauth-authorization-server | jq .
   ```
   确认返回 `issuer`、`jwks_uri`、`authorization_endpoint`、`token_endpoint`
3. **校验 PRM 端点**（theseus-kit 作为 RS）：
   ```bash
   curl https://<theseus.example.com>/.well-known/oauth-protected-resource | jq .
   ```
   确认返回 `authorization_servers` 和 `scopes_supported`
4. **验证 JWT 格式**：theseus-kit 期望 RS256 签名的 JWT，header 包含 `typ=at+jwt`（RFC 9068）

## 安全边界

- **Token 不出进程**：OAuth access token 不会出现在 MCP 工具输出、资源内容、日志或 SKILL 内容中
- **Redaction 兜底**：即使 token 意外进入错误消息路径，`redact_secrets()` 会将其替换为 `<<redacted>>`（覆盖 PAT、JWT、opaque refresh token、authorization code、state）
- **SecretStr 保护**：所有配置中的密钥字段使用 pydantic `SecretStr`，默认 `repr` 不暴露明文
- **PKCE S256**：auth-code 流程强制 PKCE，防止授权码截获
- **State 防重放**：每次授权请求使用一次性随机 state，防止 CSRF 和重放攻击
- **Token 不换发**：OAuth AS token 仅经 theseus-kit 校验后直传机器人，不会在 theseus-kit 侧做 token exchange — 减少了 token 在系统中的暴露面
