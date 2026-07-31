# #18 — MCP OAuth 2.0 授权登录(无 PAT 时):技术设计

> 状态:**设计草案,待评审**(本文档即 #18 的实施前置)。关联 issue
> [#18](https://github.com/A2C-SMCP/theseus-kit/issues/18)。
> 范围声明:本文为「准备」产物,不含实现代码;经评审与上游契约确认后再进入实施。

## 1. 目标与边界

当用户**未显式配置 PAT** 时,theseus-kit 依据 MCP Authorization Specification
(2025-11-25)走标准 OAuth 2.0/OAuth 2.1,让用户在兼容的 MCP Client 中交互式授权、
动态取得访问凭证,进而访问目标机器人;**显式 PAT 模式保持可用且优先**。两条路径
最终汇入同一套短 JWT / Scope / 刷新 / Header 注入抽象。

**非目标**:不私有化标准发现协议;不把 theseus-kit 做成授权服务器(TFRSManager 才是
AS);不把 MCP 访问令牌直接透传给目标机器人。

## 2. 现状基础(#17/#19,已在 develop)

#18 不是从零起步——#17/#19 已把"凭证 → 换发 → 路由 → Robot"管线打通,并为 OAuth
留好复用点(代码注释多处点名 #18):

```
TheseusSettings(robot, credential: client_credentials | user_pat)
  → build_credential()           # config.CredentialConfig → tfrs_auth.Credential
  → AsyncCachingTokenSource      # 缓存 / single-flight / 临期刷新 / 退避(tfrs-auth)
  → RobotClient (RobotAuth)      # 注入 Authorization: Bearer <jwt> + X-TF-*
  → RequestContext               # 唯一产出 X-TF-Namespace/RobotId/RobotType
```

可复用资产:`RequestContext`(路由)、`RobotClient`/`RobotAuth`(传输)、`errors.py`
(类型化错误 + `map_exchange_error`)、`redaction.py`(PAT/JWT 脱敏)、
`tests/_fakeserver.py`(真 socket FakeManager+FakeRobot)、`tests/test_e2e_robot.py`
(`THESEUS_E2E=1` 门控的真连 e2e)。

**关键**:OAuth 用户凭证的下游汇入点 = `tfrs_auth.UserJwtCredential`
(User JWT → `aud=robot:<callee>` 短 JWT,RFC 8693 换发),驱动它的 `AsyncCachingTokenSource`
与现有 PAT/client_credentials 路径**完全同一**。#18 本质上是"在管线上游多接一个
OAuth 用户凭证来源"。

## 3. 关键事实(已核实,决定实现方式)

| 事实 | 来源 | 含义 |
|------|------|------|
| **tfrs-auth 只做"换发 + 缓存 + 验签",不做 OAuth 采集** | 已装 0.1.1 + 已发布的 0.2.0 源码 | 发现/auth-code/PKCE 不在 tfrs-auth;0.1.1 的 `UserJwtCredential`/`PatCredential` 是骨架 |
| **tfrs-auth 0.2.0 已实现 `UserJwtCredential.request_form()`** | 0.2.0 `credentials/user_jwt.py` | OAuth 用户凭证 → robot JWT 的换发点**就绪**(应升 `tfrs-auth>=0.2.0`) |
| **MCP SDK 自带完整 OAuth 客户端** `OAuthClientProvider` | `mcp/client/auth/oauth2.py` | PRM 发现(RFC 9728)、AS 发现(RFC 8414)、PKCE、state、Resource Indicator(RFC 8707)、DCR/CIMD、auth-code+刷新、403 step-up、可插拔 `TokenStorage` + `redirect/callback` 钩子 |
| **MCP SDK 自带 RS 侧 PRM 路由** `create_protected_resource_routes` | `mcp/server/auth/routes.py` | theseus-kit 作为 RS 暴露 `/.well-known/oauth-protected-resource` = **配置即可**,无需自写 |
| **MCP SDK 提供 RS 令牌校验协议** `TokenVerifier` + bearer 中间件 | `mcp/server/auth/provider.py`、`middleware/bearer_auth.py` | theseus-kit 收 MCP Client 的 Bearer 后,用 tfrs-auth `JwtVerifier`(RS256+JWKS)本地校验,或对 AS 做内省 |
| **TFRSManager OAuth AS 已就绪可联调** | 用户确认 | 验收#1(端到端真跑)**不阻塞** |

**核心判断**:OAuth 的"标准协议机械件"已由 MCP SDK 提供,theseus-kit **不应重造**;
按"最佳实践 + 最大可复用"原则,通用、跨消费者复用的能力下沉到 **tfrs-auth**,
theseus-kit 只做 **MCP-server 集成**(暴露 PRM、校验 Bearer、把用户身份汇入现有换发管线)。

## 4. 凭证选择顺序(不变式)

```
配置了 PAT / client_credentials?  ──是──▶  走显式凭证路径(#17,#18 不参与)
        │否
        ▼
  走 OAuth 授权流程(#18)
```

- **PAT 优先、确定性**:显式凭证存在时**绝不**静默降级到 OAuth,反之亦然。
- **非鉴权配置错误不得静默降级**:配置缺失/矛盾(如同时声明 PAT 与 OAuth、或 OAuth
  配置不完整)→ 抛 `ConfigError`,给出可操作提示,不擅自选一条路。
- 这条不变式在 `TheseusSettings` 校验 + 启动期断言两层守住(与现有 `CredentialConfig`
  判别联合的扩展点一致)。

## 5. 能力切分(tfrs-auth / theseus-kit / MCP SDK)

| 能力 | 归属 | 理由 |
|------|------|------|
| OAuth **客户端**发现+auth-code+PKCE+刷新(HTTP 标准,MCP Client 侧) | **MCP SDK**(`OAuthClientProvider`,由 Claude Desktop 等 MCP Client 驱动) | theseus-kit 是 RS 不是 client;这是 MCP Client 的职责 |
| **RS 侧** Protected Resource Metadata(RFC 9728) | **MCP SDK**(`create_protected_resource_routes`) | 配置 `authorization_servers=[TFRSManager]` 即可 |
| **RS 侧** Bearer 校验(签名/iss/aud=resource/scope/撤销水位) | **tfrs-auth**(`JwtVerifier` 增强为发现感知)— *见 §10 上游请求* | 每个 TFRS RS/MCP server 都要,可复用 |
| User JWT → robot JWT 换发(RFC 8693) | **tfrs-auth**(`UserJwtCredential` 0.2.0 ✓) | 已就绪 |
| 缓存/single-flight/临期刷新/退避 | **tfrs-auth**(`AsyncCachingTokenSource`) | 已就绪,两条路径共用 |
| 凭证选择、OAuth 配置、STDIO 外部回调采集、令牌边界、脱敏 | **theseus-kit** | MCP-server 特有集成 |
| STDIO/CLI 下"拿一个用户凭证"的通用 OAuth 2.1 采集(auth-code+PKCE+外部回调) | **tfrs-auth**(新建,可选)— *见 §10 上游请求* | 每个 TFRS 桌面/CLI/SDK 都要,**不应各写一份**;不与 MCP SDK 重复(后者 MCP 协议耦合) |

## 6. Topology A — HTTP 标准(MCP Client 驱动授权)

```
+--------+   OAuth(Authorization Code+PKCE)   +-----------------+
| MCP    | ─────────────────────────────────▶ │ TFRSManager AS  │
| Client | ◀──── access_token (aud=theseus) ─ │ (issuer)        |
|        │                                    +-----------------+
|        │  Bearer: access_token (aud=theseus-kit PRM resource)
|        | ─────────────────────────────────▶ +-----------------+
+--------+                                    │ theseus-kit RS  │
                                              │ ① 校验 Bearer   │
                                              │   (tfrs-auth)   │
                                              │ ② 用户身份 →    │
                                              │   UserJwtCred.  │
                                              │   → Async…Token │
                                              │   → robot JWT   │
                                              │   (aud=robot:…) │
                                              | ③ RobotClient   |
                                              |   + X-TF-* ───▶ robot
                                              +-----------------+
```

**theseus-kit 做的事**(RS 角色,~配置 + 三段连线):
1. 启动时挂 `create_protected_resource_routes(resource_url=<theseus-kit HTTP 入口>,
   authorization_servers=[<TFRSManager AS URL>], scopes_supported=[config:read,...])`。
2. 实现 `TokenVerifier`:用 tfrs-auth 校验 MCP Client 的 Bearer(签名/JWKS、`iss`、
   `aud`=theseus-kit PRM resource、所需 scope、过期、撤销水位 §12.2),提取用户身份(`sub`)。
3. 把验证后的用户身份,经 `UserJwtCredential` → 现有 `AsyncCachingTokenSource` → `RobotClient`,
   得到 `aud=robot:<Account.ID>` 的短 JWT 调用机器人。

**不变式**:MCP access token(aud=theseus-kit)**绝不**转发给机器人;机器人只收到
audience 重新绑定为 `robot:<id>` 的换发短 JWT(与现有 `redaction` / 架构安全不变式一致)。

## 7. Topology B — STDIO 外部回调(theseus-kit 自驱授权)

STDIO 下 MCP Client 无 HTTP,无法做 OAuth。theseus-kit 自己驱动授权(「外部登录回调」):

```
theseus-kit(STDIO)
  ├─ 启动本地回调 listener(127.0.0.1:ephemeral)
  ├─ 经 tfrs-auth 通用 OAuth 客户端(见 §10)构造 /authorize 请求(PKCE+state+resource)
  ├─ 打开默认浏览器到 TFRSManager AS 授权页
  ├─ 用户授权 → AS 回调 → theseus-kit 收 auth_code(state 校验防重放/CSRF)
  ├─ 兑换 user token(刷新 token 安全缓存,文件 0600 / OS keychain)
  └─ user token → UserJwtCredential → AsyncCachingTokenSource → RobotClient
```

**过期/撤销/刷新失败** → 抛可恢复的 `ReauthRequiredError`,给出「请重新登录」的可操作提示
(不泄密),由下次工具调用或显式命令重新触发流程。

## 8. 下游收敛(两拓扑同汇一点)

无论 A/B,终态都是:

```
(user identity) ─▶ UserJwtCredential(audience=robot:<Account.ID>, scope=config:read…)
                 ─▶ AsyncCachingTokenSource  # 与 #17 完全同一,零新增换发机械件
                 ─▶ RobotClient(RobotAuth: Bearer + X-TF-*)
```

`RobotTarget`、`RequestContext`、`RobotClient`、`redaction`、错误映射**全部复用**;
#18 新增的仅是"用户身份从哪来"(OAuth)与"凭证选择"。

## 9. 安全模型(逐条对应验收标准)

- **令牌边界**:MCP access token 不出 theseus-kit 进程、不转发给机器人;机器人 token
  绑定 `aud=robot:<Account.ID>`,只发往预期资源服务器(§6 不变式)。
- **脱敏扩展**:`redaction.py` 当前覆盖 `tfp_…` 与 JWT 形;OAuth access/refresh token、
  authorization code、state 的形态需纳入脱敏正则,确保**不出现在日志/异常/MCP 结果/
  持久化明文配置**。
- **State / 重放防护**:auth-code 流程用一次性 state(CSRF + 重放);PKCE S256。
- **Resource Indicator(RFC 8707)**:授权与 token 请求带 `resource`(Topology A 由 MCP SDK
  按 PRM 处理;Topology B 由 theseus-kit 带 robot/theseus-kit resource)。
- **最小 Scope**:按工具实际操作申请(只读=`config:read`),不足时走重新授权/step-up
  (MCP SDK 已实现 403 `insufficient_scope` step-up;Topology B 由 theseus-kit 处理)。
- **缓存安全**:refresh token / user token 持久化用 OS keychain 或 0600 文件,带绑定与过期清理。

## 10. 上游契约(Phase 3)

### 10.1 [已定夺:否] OAuth AS 令牌能否作 RFC 8693 `subject_token`?

**结论(2026-07-23,TFRSManager 源码核实):否。** OAuth AS 签发的 access token(#3,
RS256,aud=`{iss}/robots/<id>`、sub=`user:<id>`)不能作为 `subject_token` 被
`POST /api/v1/oauth/token` 接受。命门:换发唯一入口 `principalFromUserJWT` 强制 **HS256 +
对称密钥 `JWT_SECRET`**,并要求自定义 `user_id`/`account_id`/`organization_id` claim(任一为 0
即拒)。#3 是 RS256 且无 `user_id` claim,两道关都挂。"User JWT" 指的是 user-service 登录会话
JWT(`auth.GenerateToken` 签、HS256),**不是** OAuth AS access token。换发机制本就只服务
**登录会话 JWT / PAT / client_credentials**,不服务 OAuth 用户令牌。

⇒ `UserJwtCredential(user_jwt=<#3>, audience=robot:<id>)` 这条接缝**作废**。

### 10.1-new [新命门,第一优先] OAuth AS 令牌能否在机器人侧**直接使用**(跳过换发)?

取代 §10.1 成为第一命门。#3 本身 robot-scoped(aud=`{iss}/robots/<Account.ID>`),直觉上无需
再换发。**待确认的一处**:TFRobotServer 验签器(tfrs-auth RS256+JWKS,强制执行层在
TFRobotServer)目前按 `robot:<Account.ID>` 校验 audience;#3 的 audience 是
`{iss}/robots/<id>`、claims 是 C1(sub=`user:<id>`、`client_id`)。**机器人侧是否也接受 #3
这套 audience/claims 形态**——是"直收"成立的前提,需 TFRobotServer 侧确认(或翻其验签代码)。

- **若直收**:theseus-kit OAuth 路径**去掉换发**;`UserJwtCredential`/`AsyncCachingTokenSource`
  只服务 PAT/client_credentials;原 AC#4 重写为"送机器人的令牌须 robot-audience 绑定"(#3 满足);
  `Account.ID` 角色从"设 audience"降为"校验 audience 目标";设计大幅简化。
- **若不直收**:Topology A 在不改上游下**跑不通**(theseus-kit 只持 #3,且 #3 不可换发、无登录
  JWT/PAT 可换)→ 需 TFRSManager **选项 C**(扩 `principalFromUserJWT` 增 RS256 分支 + 先改
  Contract Registry C1,遵循"契约先行"),theseus-kit 等待。

> 注:§6 step ②、§8、§11 中"OAuth 用户凭证 → 换发 → robot JWT"的描述,以本节定夺为准——
> 换发接缝已作废;是否仍有任何"转换"步骤取决于 §10.1-new。

### 10.2 对 tfrs-foundation-py(tfrs-auth)的 Feature Request

见 [`docs/upstream/tfrs-auth-oauth-feature-request.md`](upstream/tfrs-auth-oauth-feature-request.md)。
要点(可复用、遵循最佳实践,均"建议方案、以对方回复为准"):
- **(P0)** 发现感知的 RS 令牌校验:基于 JWKS 校验 TFRSManager 签发 token 的签名/`iss`/
  `aud`=`resource`/所需 scope/过期/撤销水位,返回资源拥有者身份。每个 TFRS RS 复用。
- **(P1)** 通用 OAuth 2.1 采集客户端(transport-agnostic):RFC 8414 发现、auth-code+PKCE、
  state、Resource Indicator、DCR/预注册/CIMD、token+刷新、可插拔 redirect/callback + storage。
  供 STDIO theseus-kit 及任意 TFRS 桌面/CLI/SDK 复用(避免各写一份)。
- **(确认)** `AsyncCachingTokenSource.invalidate()`(theseus-kit 已记为 tfrs-auth cnb#3):
  机器人 401/403 后强制重换发所需;确认其落点。

## 11. 验收标准映射(逐条 → 实现位置)

| 验收标准 | 落点 |
|----------|------|
| 无 PAT 时兼容 MCP Client 据标准发现启动登录,授权后能读机器人文档/只读配置 | Topology A(§6)+ e2e(§12 S6,真连 TFRSManager AS) |
| PAT 优先确定;两路同入短 JWT/Scope/刷新/Header 抽象 | §4 选择顺序 + §8 收敛(S1) |
| 覆盖 PRM、AS/OIDC 发现、PKCE、Resource Indicator、state/重放、刷新、重新授权 | S6 测试矩阵 |
| MCP 访问令牌不透传机器人;robot token 绑定正确 audience | §6 不变式 + S3 |
| token/auth-code/PAT 不入日志/异常/MCP 结果/明文配置 | `redaction` 扩展 + S5 |
| 不支持浏览器/OAuth 的 Client/STDIO 返回明确可操作不泄密提示 | Topology B(S4)+ S5 |

## 12. 实施切片(Epic #18 → Stories,设计评审后再正式建子 Issue)

| Story | 内容 | 依赖 |
|-------|------|------|
| **S1** 凭证选择 + OAuth 配置 | 扩展 `CredentialConfig` 判别联合(加 `oauth` kind);PAT/OAuth 互斥、不完整配置→`ConfigError`;`TheseusSettings` 校验 | — |
| **S2** RS PRM + Bearer 校验(HTTP) | 挂 `create_protected_resource_routes`;实现 `TokenVerifier`(tfrs-auth);提取用户身份 | tfrs-auth P0;§10.1 |
| **S3** 用户身份 → robot JWT 接缝 | `UserJwtCredential` → 现有 `AsyncCachingTokenSource` → `RobotClient`;audience/scope 绑定;令牌边界不变式 | tfrs-auth 0.2.0 |
| **S4** STDIO 外部回调采集 | 浏览器 + 本地回调 + 安全缓存 + 可恢复重登录(`ReauthRequiredError`) | tfrs-auth P1 |
| **S5** 安全加固 | `redaction` 扩展(OAuth token/code/state);配置/日志/MCP 结果全链路无泄密审计 | — |
| **S6** 测试矩阵 | hermetic(PRM/发现/PKCE/Resource Indicator/state-重放/刷新/重新授权/step-up,对 fakes)+ e2e(真连 TFRSManager AS,`THESEUS_E2E=1`) | S2–S4 |
| **S7** 文档 | 更新 `architecture.md` §Auth;运行手册(登录/重登/排错) | — |

> 按本仓 Phase 4 测试触发器:S2/S3「打通鉴权执行路径」+「触及真实契约」→ 新增测试须
> 真跑真实设施(TFRSManager AS + robot),当次至 PASS;全量 e2e 仍属 user-trigger。

## 13. 关联与风险

- **阻塞已解除**:#17(PAT/短 token + 路由上下文)已合并;#18 的前置就绪。
- **阻塞 #3**:HTTP Adapter(#3)须同时容纳显式 PAT 与无 PAT 的 OAuth;本设计 S2 的
  RS 集成即 #3 鉴权侧的公共抽象,#18 定稿后 #3 不返工。
- **风险**:① §10.1-new 机器人侧是否直收 OAuth AS 令牌(取代已否决的 §10.1;决定 OAuth 路径是否还需换发 / 是否需上游选项 C);② STDIO 拓扑
  依赖 tfrs-auth P1 新能力(否则 theseus-kit 需临时自实现一份,违背复用原则——应等上游);
  ③ MCP SDK 版本能力边界(FastMCP 挂载 PRM 路由 + bearer 中间件的具体 API)需实现期核验。
