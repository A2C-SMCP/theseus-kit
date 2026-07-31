# 上游 Feature Request:tfrs-foundation-py(tfrs-auth)— OAuth 采集与 RS 校验

> 目标仓库:[tfrs-foundation-py / tfrs-auth](https://cnb.cool/turingfocus/foundation/tfrs-foundation-py)
> (Issues:[CNB issues](https://cnb.cool/turingfocus/foundation/tfrs-foundation-py/-/issues))
> 请求方:**theseus-kit**(A2C-SMCP/theseus-kit)
> 关联功能:[#18 支持无 PAT 配置时的 MCP OAuth 2.0 授权登录](https://github.com/A2C-SMCP/theseus-kit/issues/18)
> 状态:**已提单** — tfrs-foundation-py [cnb#9](https://cnb.cool/turingfocus/foundation/tfrs-foundation-py/-/issues/9)(2026-07-23,P1)。建议方案仅供参考,**以 tfrs-auth 回复文档为准**。

## 背景

theseus-kit 正在实现 #18:当用户未配置 PAT 时,按 MCP Authorization Specification
(2025-11-25)走标准 OAuth 2.0/OAuth 2.1 动态授权。tfrs-auth 已是 theseus-kit 的凭证换发
底座(`AsyncCachingTokenSource` + `UserJwtCredential` 0.2.0 + RS256 验签),且自述使命是
**"避免每个 MCP 工具/SDK 重复实现 token 获取与生命周期管理"**。本请求把 #18 中**通用、
跨消费者复用**的 OAuth 能力下沉到 tfrs-auth,遵循「最佳实践 + 最大可复用」原则,避免
theseus-kit(以及后续其它 TFRS 桌面/CLI/SDK)各写一份。

> 设计依据见 theseus-kit [`docs/auth-oauth-design.md`](../auth-oauth-design.md) §5、§10。

---

## FR-1(P0):发现感知的 RS 令牌校验器

### 需要什么(非技术语言)

作为「受 OAuth 保护的资源服务器(RS)」,theseus-kit 会收到别人(MCP Client)拿来的、
由 TFRSManager 签发的访问令牌。我需要一个**可信、统一**的校验器,告诉我:这个令牌是不是
TFRSManager 合法签发的、是不是给我的(audience = 我这个资源)、有没有我要的权限(scope)、
有没有过期或被撤销;并告诉我令牌背后的用户是谁。这个校验每个 TFRS RS/MCP server 都要做,
不该各自实现。

### 现状与缺口

- tfrs-auth 已有 `JwtVerifier`(RS256 + JWKS 纯验签)。
- 缺:**发现感知**(依据 RFC 8414 AS metadata / RFC 9728 PRM 定位 issuer 与 JWKS)、
  **audience 校验为"资源"语义**(令牌 `aud` = PRM `resource`,不是 robot audience)、
  **scope 断言**(断言所需 scope 命中)、**撤销水位 §12.2**(`iat` 晚于水位,fail-closed)、
  返回**资源拥有者身份**(sub / userID / orgID / accountID)。

### 建议方案(供参考)

新增 `AsyncResourceTokenVerifier`(或增强 `JwtVerifier`):
- 输入:令牌字符串、期望 `resource`、所需 `scopes`、AS/PRM 发现端点(或预置 issuer + JWKS)。
- 行为:JWKS 获取(带缓存/刷新)→ 验签 → 校验 `iss`/`aud`=resource/scope/过期/水位 →
  返回结构化 `Principal`(sub + TFRS claims)或 `None`(非法)/抛类型化错误。
- 错误码对齐既有 `TokenVerificationError` 体系。

### 交付要求

- 接口/类型签名 + py.typed;RFC 9728/8414 字段对照表;
- 鉴权说明(JWKS 来源、`aud`/scope 语义、撤销水位规则);
- 错误码定义(非法签名 / 过期 / aud 不符 / scope 不足 / 已撤销);
- 变更说明 + 版本号(纳入 theseus-kit 依赖下限)。

---

## FR-2(P1):通用 OAuth 2.1 采集客户端(transport-agnostic)

### 需要什么(非技术语言)

在 STDIO 等无法让 MCP Client 做 OAuth 的场景,theseus-kit 需要自己驱动一次完整的人类
授权:打开浏览器到 TFRSManager 授权页、用户同意后从本地回调拿到授权码、换成访问令牌、
安全缓存并在过期时刷新。这是一份**标准 OAuth 2.1 授权码 + PKCE** 客户端,任意 TFRS 桌面端/
CLI/SDK 都会用,应作为共享能力,而非 each-customer 自造。

### 现状与缺口

- tfrs-auth 当前**只做换发**,不含 OAuth 采集(发现/auth-code/PKCE/state)。
- MCP Python SDK 有 `OAuthClientProvider`,但它与 MCP 协议耦合(按 MCP server PRM 发现、
  带 `MCP-Protocol-Version` 等),**不能在非 MCP-client 上下文(MCP server 的 STDIO 自驱、
  普通 TFRS CLI)直接复用**。故需 tfrs-auth 提供一份与传输无关的通用采集客户端。

### 建议方案(供参考)

新增 `OAuthUserCredentialAcquirer`(命名待定):
- 入参:TFRSManager AS base URL(或 PRM)、客户端元数据(预注册 client_id 或走 DCR/CIMD)、
  申请 scope、resource(RFC 8707);
- 可插拔:`redirect_handler`(打开浏览器/输出 URL)、`callback_handler`(本地 listener)、
  `TokenStorage`(持久化,建议 OS keychain / 0600 文件);
- 行为:RFC 8414 发现 → PKCE(S256) + 一次性 state → /authorize → 回调收码(state 校验)→
  /token 兑换 → 安全缓存 access/refresh;临期或 401 自动刷新;刷新/撤销失败 → 可恢复重授权信号;
- 产出可直接喂 `UserJwtCredential`(与 0.2.0 换发接缝闭合)。

### 交付要求

- 接口/配置文档 + 鉴权说明(PKCE/state/resource/DCR vs CIMD 策略);
- 错误码定义(发现失败 / state 不符 / 授权拒绝 / token 兑换失败 / 刷新失败 / 需重新授权);
- 变更说明 + 版本号;
- (可选)一份 theseus-kit STDIO 集成示例。

---

## FR-3(确认):`AsyncCachingTokenSource.invalidate()` 落点

theseus-kit `transport.py` 已记录:机器人 401/403 后的「强制重换发」需要上游
`AsyncCachingTokenSource.invalidate()`(theseus-kit 侧记为 **tfrs-auth cnb#3**)。#18 的
重新授权/刷新路径同样依赖它。请确认其实现计划/版本落点。

---

## 优先级与节奏

| 项 | 优先级 | 阻塞 theseus-kit |
|----|--------|------------------|
| FR-1 RS 校验 | **P0** | #18 S2(HTTP 拓扑) |
| FR-2 通用采集 | **P1** | #18 S4(STDIO 拓扑) |
| FR-3 invalidate | 确认 | #18 重新授权;#17 已记 |

> 这些us-kit 端会**先做不依赖上游的切片**(S1 凭证选择/配置、S3 接缝骨架、S5 脱敏、S7 文档),
> 并以 fakes 覆盖协议契约;真连 e2e 与上游能力对齐后补齐。
