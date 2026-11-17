# theseus-kit 与 TFRM / TFRO / TFRS develop 互通升级报告

- 日期：2026-11-16
- theseus-kit 基线：`develop` `3c47030` / `0.1.1`（2026-10-04）
- 调研对象：
  - TFRM：`tfrsmanager-b4`，develop `0495825c`（`v0.2.0-dev18`，2026-11-11）
  - TFRO：`tfrs-operator-03`，develop `6dbef4f`（`0.9.3-dev23`，2026-11-10）
  - TFRS：`tfrobotserver-7d`，develop `80c8e42f`（`0.5.0-rc1`，2026-11-11）

## 0. 结论（TL;DR）

1. **旧路由模型已整体退役**：`https://api.<clusterDomain>` + 三个强制 `X-TF-*` 头不再是机器人入口。新模型是 **Host 权威 + `/c/{robotType}/{robotId}` 路径 + Bearer**：Host（租户权威别名）决定 namespace，路径决定 robot，token 决定权限。
2. **theseus-kit 当前实现全部按旧模型写死**：`RobotTarget` 的 `namespace`/`api_base_url`、`RequestContext.routing_headers()`、`RobotAuth` 的 X-TF 注入、fake server 的缺头 400 断言、README/架构文档/E2E 环境变量，都需要按 breaking change 处理（建议 `0.2.0`）。
3. **最小可用改造不依赖上游新功能**：把机器人入口改为权威 `accessURL`（`https://{alias}/c/tfrobot/{rid}/`），直连 `/v1/factory/**` 与 `/llms.txt`；删除 X-TF；补 409/404 的类型化错误；更新测试与文档。即可在已升级到新域名体系的 test 环境互通。
4. **OAuth（#18）跨仓 P0 已修复并关闭**：Manager 新签的 Authorization Code token 曾是 `aud=robot:{orgSlug}:{employeeNo}` + `typ=at+jwt`，TFRS 只接受 `{issuer}/robots/{public_id}` + `typ=at+jwt`，会以 `jwt_profile_mismatch` 拒绝。Owner 决定方案 A（`TFRM-760`）：Manager 恢复 `{issuer}/robots/{public_id}`（RFC 8707），TFRS 零改码；legacy PAT/token-exchange 保持 `robot:{public_id}` + `typ=JWT`。修复已随 `v0.2.0-dev19` 发布，`TFRM-760` 已按 Owner 决定关闭（开发完成即结束，resolution=完成）。TFRS 已补独立 synthetic verifier 证据（Authorization Code 形状/legacy 均 accepted，cross-profile 与缺 `client_id` 均 rejected；非 Manager-signed、非部署验收）。**PAT 路径（theseus-kit 默认）不受影响**；Manager-signed + 真实部署验收仍需等 test 窗口，若发现问题新开 Bug。`0.2.0` 先只保证 user_pat，OAuth 在真实验收通过前不启用。
5. **另有若干 P1/P2 一致性问题**：`robot_public_id` 正则过严（拒绝 `EMP010-R` 这类合法 ID）、`_parse_public_id` 用 `rsplit` 与 Manager 的“首个冒号分割”不一致、TFRM/TFRS 多份文档仍在描述旧 `api.`/X-TF/`/api` 契约（TFRO 已于 `a007c73` 修正其 network-topology 文档）；`robot_id` 字符集 Manager 宽 / gateway 严，已确认可触发 400。详见 §4。

**实现状态（2026-11-17）**：theseus-kit `0.2.0` 已按本报告完成配置（`access_url` / `manager_api_base_url`）、Bearer-only 传输、409/404/400 错误映射、`public_id` 解析和 OAuth audience（`{issuer}/robots/{public_id}`）适配；`uv run poe ci` 通过（332 passed / 5 skipped，覆盖率 88.5%）。OAuth Topology A 的 PRM `resource` 兼容性仍需真实环境验收，验收前只保证 user_pat。

---

## 1. 新契约（develop 事实）

### 1.1 机器人地址与路径

权威来源：TFRM `docs/api/robot-address-v2.md`、`docs/api/robot-address-recovery-v1.md`、`internal/shared/domainalias/robot_address.go`。

| 项 | 新契约 |
|---|---|
| Host | `https://{alias}.{DOMAIN_BASE_DOMAIN}` |
| 环境 base domain | test=`test.turingfocus.cn`；staging=`staging.turingfocus.cn`；prod=`turingfocus.cn`（region 不进域名） |
| alias 来源 | 企业自选单标签（如 `huawei`）或系统分配的个人共享分片（如 `tfrs-personal-1`） |
| Robot 访问路径 | `/c/{robotType}/{robotId}/...`；`robotType`=`tfrobot`\|`openclaw`（小写） |
| theseus-kit 直连 factory 的 base | `https://{alias}/c/tfrobot/{rid}`，再拼 `/v1/factory/**`、`/llms.txt` |
| Socket.IO | `socketBaseURL=https://{alias}` + `sioPath=/c/tfrobot/{rid}/socket.io/` |
| A2A | `/v1/a2a/{base64url(utf8("{robotType}|{robotId}"))}/`；RPC 尾斜杠必需 |
| 消息入口 | `/v1/messages`（固定 path；A2A 已从 TFRS 移除，theseus-kit 不使用） |

**关键：不要把 `connection-info.apiBaseURL` 当机器直连入口。** 它形如 `.../c/tfrobot/{rid}/api`，`/api` 是遗留描述符字段（前端 BFF 路径），不是 TFRS 直连前缀；Operator 只剥 `/c/{rtype}/{rid}`，剩下 `/api/v1/factory` 在 TFRS 不存在（只有 `/v1/factory/**`），会 404。TFRM-280 已明确修复过“初始化追加 `/api` 进入 BFF”的缺陷（`docs/tfrm-280-machine-api-plan.md`），TFRM 本轮也再次确认机器入口不能带 `/api`。机器客户端应使用 `accessURL` / `RobotURL`（不带 `/api`）。

test 环境示例（实际以 Manager 返回为准；验收域名可能已被清理）：

```
https://huawei.test.turingfocus.cn/c/tfrobot/robot-1/
https://tfrs-personal-1.test.turingfocus.cn/c/tfrobot/<rid>/
```

### 1.2 网关寻址行为

权威来源：TFRO `internal/controller/infra/cluster/tenant_host_routing.go`（develop `6dbef4f`，含 `04d06ef`）。

- **Host 是 namespace 的唯一权威**。外部传入的 `X-TF-Namespace` / `X-TF-RobotId` / `X-TF-RobotType`、Cookie（`tfNamespace` 等）、query（`namespace`/`rid`/`robotType`）都被清除/忽略，不能覆盖 Host 归属。
- 网关按精确 Host 匹配租户别名；未知 Host → **404 `unknown ingress host`**。
- `/c/{rtype}/{rid}` 路径段只接受 `^[a-z0-9-]+$` 的 robot id 与已知 type；非法 → **400**；robot 不属于该 Host 的 namespace → **404 `robot not found on this host`**。
- 网关剥掉 `/c/{rtype}/{rid}` 后，把剩余路径投给 TFRS；TFRS 北向白名单（`contracts/gateway-northbound.json`，1.6）为：
  - `/llms.txt`
  - `/v1/`（含 `/v1/factory/**`、`/v1/factory/llm-docs/**`）
  - `/utils/cos/`
  - `/.well-known/oauth-protected-resource`（精确项 + 尾斜杠前缀项）
- 集群路由显式 60s 超时（TFRO-237）；theseus-kit 当前默认 30s，正常配置读写不受影响；大配置依赖渐进披露控制体量。

**旧 Host（改名后的保留别名）行为**：

| 请求类型 | 行为 |
|---|---|
| 浏览器顶层页面导航（GET/HEAD + `Sec-Fetch-Mode: navigate` + `Sec-Fetch-Dest: document` + 已知页面） | 302，`Cache-Control: no-store`，保留 path/query |
| API（含 GET）、A2A、Socket.IO、健康检查、无法确认的程序请求 | **409**，`Cache-Control: no-store`，JSON `{"errorCode":"ROBOT_ADDRESS_CHANGED","message":"Robot address changed; rediscover through the configured control plane."}`，不转发、不重定向 |

旧别名保留窗口默认 30 天（`DOMAIN_REDIRECT_RETENTION=720h`，可配）；到期不保证重定向。**客户端不能把旧 Host 当身份，也不能盲目重试。**

### 1.3 鉴权、audience 与 scope

权威来源：TFRM `docs/deploy/tfrm-639-640-dev11.md`、`internal/user/service/oauth_token_service/oauth_token_service.go`；TFRS `tfrobotserver/middleware/jwt_verifier.py`、`contracts/oauth-scope-catalog.json`。

- TFRS **只解析 `Authorization: Bearer`**，不解析 X-TF；X-TF 由网关负责（且新网关已移除）。
- TFRS 接受的 token profile（develop）：
  - `aud=robot:{robot_id}`（实例 rid）+ `typ=JWT`；
  - `aud=robot:{machine_client_id}`（Manager public_id，`{orgSlug}:{employeeNo}`）+ `typ=JWT`；
  - `aud={issuer}/robots/{machine_client_id}` + `typ=at+jwt` + 必须带 `client_id`（Authorization Code / OAuth profile）。
- PAT token-exchange 的 audience 已从数字 Account.ID 改为 **`robot:{orgSlug}:{employeeNo}`**（TFRM-386，`9be65679`），`typ=JWT`；theseus-kit 当前的 `robot_public_id → robot:{public_id}` 逻辑方向正确。
- token 端点路径未变：`POST /api/v1/oauth/token`，form-urlencoded；PAT 的 `subject_token_type=urn:ietf:params:oauth:token-type:access_token`。theseus-kit 的 `tfrs_auth.PatCredential` 已对齐。
- scope 词表未变：`config:read` / `config:write` / `config:publish` 仍为 active；`POST /v1/factory/drafts/release` 需要 `config:publish`，GET `/llms.txt` 需要 `config:read`。

### 1.4 Manager 入口与环境矩阵

| 环境 | Manager 机器 API origin（`MANAGER_API_BASE_URL`） | JWT issuer（OAuth AS host） | 机器人新域名体系 |
|---|---|---|---|
| test | `https://rest.test.turingfocus.cn`（`api.test` 保留给数据面） | `https://user.test.turingfocus.cn`（TFRM-640 实测） | 已按 dev11 验收（Manager dev11+ / Operator dev4+）；`*.test.turingfocus.cn` |
| staging | `https://api-staging.turingfocus.cn`（GitOps overlay `apps/overlays/staging/manager-domain-runtime.yaml`） | 以 `ACCESS_TOKEN_ISSUER` 为准 | 当前 Manager rc7 + Operator rc5 + `DOMAIN_AUTOMATION_ENABLED=false`，旧线；配置态 `*.staging.turingfocus.cn` 不生效 |
| prod | `https://api.turingfocus.cn`（02-network 现行；未配置/未验收） | 以 `ACCESS_TOKEN_ISSUER` 为准 | 未配置/未验收 |

要点：

- `MANAGER_API_BASE_URL` 与 `ACCESS_TOKEN_ISSUER` **已分离**；不要从 issuer 推导机器 API，也不要用 `api.<clusterDomain>` 当 Manager API（test 用 `rest.test`）。
- `manager_base_url` 应填上表的机器 API origin；OAuth AS 的 `authorization_server` 应填 issuer。
- **环境现实**：test 已切新方案；staging 仍是旧线（Manager rc7 + Operator rc5 + 域名自动化关闭）；prod 未配置。theseus-kit 若要在 staging/prod 继续使用，可能需要一段时间的 legacy 路由兼容（见 §3.1）。
- 旧 `api.<clusterDomain>` 在新 Operator/Manager 下已退役，无兼容层；X-TF 头仅作为 deprecated 描述符元数据保留，不再参与路由，无移除日期。旧 **alias** 有 30 天保留窗口（`DOMAIN_REDIRECT_RETENTION=720h`，test/staging 配置）；`api.<clusterDomain>` 本身不在 30 天承诺内，能用到环境升级为止。没有全局截止日，不要依赖旧地址。

### 1.5 TFRS 使用细节（工厂 API）

- `/v1/factory/**`（draft/template/online 三态）、根 `GET /llms.txt`、`/v1/factory/llm-docs/**` **未破坏性变更**。
- 模板查询仍为 `page`（1-based，默认 1）+ `pageSize`（默认 10）；draft/online 查询按 scene/factory 过滤。
- 错误包络：非 422 为 `{code,message,data}`；422 为 `{"detail":[{loc,msg,type,url?}]}`，不回显 input/ctx（TFRS-770）。
- 写操作无通用 `Idempotency-Key`；失败不要盲目重放，先 query 核对。theseus-kit 现有“写不重试、GET 仅 429/5xx 重试”的策略正确。
- A2A 全栈已移除（`c95edf14`，TFRS-764），`/v1/a2a/**` 404；theseus-kit 未使用 A2A，不受影响。
- 契约版本：TFRS `CONTRACT_VERSION=1.7`（route→scope）；网关北向 `NORTHBOUND_CONTRACT_VERSION=1.6`（路径前缀未变）。theseus-kit 运行期读取 `/llms.txt`，不直接消费契约产物。

---

## 2. theseus-kit 现状与差距

### 2.1 配置模型

`src/theseus_kit/config.py`：

| 当前字段 | 问题 | 建议 |
|---|---|---|
| `RobotTarget.robot_id` | 仍可用，但描述为 `X-TF-RobotId` | 保留为业务 Robot ID（rid），用于路径校验、summary 展示 |
| `RobotTarget.namespace` | 新模型不参与路由；仍必填 | **删除**（或保留为只读元数据，默认不要求） |
| `RobotTarget.robot_type` | 仍可用于路径 `tfrobot` | 保留，枚举 `tfrobot`（配置工具只支持 tfrobot） |
| `RobotTarget.api_base_url` | 语义是旧 `https://api.<clusterDomain>` | **改名/改语义**为 `access_url`（`https://{alias}/c/tfrobot/{rid}/`） |
| `RobotTarget.manager_base_url` | 语义正确，但 docs 与 issuer 混淆 | 改名 `manager_api_base_url`，值取 §1.4 表 |
| `UserPatConfig.robot_public_id` 正则 | `^[a-z0-9-]+:[a-zA-Z0-9]+$` 拒绝 `EMP010-R`、`my-org:emp:001` 等合法值 | 按 Manager 规则改（见 §3.5） |

### 2.2 路由与传输

| 文件 | 当前行为 | 需要 |
|---|---|---|
| `src/theseus_kit/routing.py` | `RequestContext` 构造/校验三个 X-TF 头 | 删除 X-TF；保留 robot id/type 校验，或退化为 `RobotAddress` 值对象 |
| `src/theseus_kit/transport.py` | `RobotAuth` 注入 Bearer + 三个 X-TF | **只注入 Bearer**；`RobotClient` 的 `base_url` 使用 canonical `access_url` |
| `src/theseus_kit/transport.py` | `from_settings()` 用 `robot.api_base_url` + `RequestContext` | 用 `access_url` + `robot_id/type`；不再构造 header |
| `src/theseus_kit/transport.py` | `for_static_token()` 同样注入 X-TF | 同上，Bearer-only |
| `src/theseus_kit/transport.py` | `_ensure_ok()` 只处理 401/403/422/4xx | 新增 409 `ROBOT_ADDRESS_CHANGED`、404 `unknown ingress host`/`robot not found on this host`、3xx 的显式映射 |
| `src/theseus_kit/tokens.py` | `manager_base_url + /api/v1/oauth/token` | 路径不变；配置值改为机器 API origin |
| `src/theseus_kit/__init__.py` | 导出 `RequestContext` | 随 `routing.py` 调整公共导出 |

### 2.3 错误与恢复

当前 409 会被当成普通 `RobotApiError`（`status_code=409`），没有 `errorCode` 语义；302 会因 `httpx` 默认不跟随重定向而被当作成功响应，后续 JSON 解析报错，诊断价值低。

建议新增：

- `RobotAddressChangedError`：409 + `errorCode=ROBOT_ADDRESS_CHANGED`；消息明确“地址已变更，请从 Manager 重新获取 `accessURL` 并更新配置；旧地址仅保留 30 天”。
- `RobotRouteNotFoundError`：404（未知 Host / robot 不属于该 Host）；消息包含 alias、rid、type，提示检查 `access_url` 是否来自正确环境/租户。
- `RobotPathRejectedError`：400（路径/rid 非法）；通常是配置或 URL 拼接错误。
- 3xx：视为地址/入口异常，抛出 `RobotAddressChangedError` 或 `RobotApiError`，不要静默解析 HTML。

### 2.4 凭证与 public_id

- `src/theseus_kit/credentials.py:19-30` 用 `rsplit(":", 1)` 解析 public_id；Manager/TFRS 用 **首个冒号** 分割（`publicid.ParseAccountIdentity`、`parse_public_id`）。`my-org:emp:001` 会被错误解析。
- `config.py:69-72` 的 regex 拒绝 employeeNo 中的 `-`/`:`；Manager 只要求 org slug 合法、employeeNo 非空，示例 `turingfocus:EMP010-R`。
- 这两处会导致合法机器人无法配置，或 audience 构造错误。

### 2.5 测试与文档

| 位置 | 旧假设 |
|---|---|
| `tests/_fakeserver.py` | `ROUTING_HEADERS` + 缺 X-TF 返回 400；未模拟 canonical `/c/...` 路径 |
| `tests/test_auth_routing.py` | 断言 `routing_headers()` 三个头；`_CTX` 含 `namespace` |
| 12 个测试文件的 `_CTX = dict(robot_id=..., namespace=..., robot_type=...)` | 需要去掉 namespace / 改为 canonical URL |
| `tests/test_static_token.py`、`test_robot_client.py`、`test_resources.py`、`test_package.py`、`test_e2e_robot.py` 等 | 构造函数/环境变量含旧字段 |
| `tests/e2e/test_kit_e2e.py:172-177` | 断言 `x-tf-namespace == "e2e-ns"` |
| `README.md` 42/142/178/233/245/271/297/301/326 行 | `API_BASE_URL`、`X-TF-*`、`namespace`、旧入口 |
| `docs/architecture.md:24-40,60-80` | 旧 `api.<clusterDomain>` + X-TF 契约 |
| `docs/auth-oauth-design.md`、`docs/oauth-operations.md` | 多处 `Bearer + X-TF-*`、`aud={issuer}/robots/<id>` 旧描述 |
| `.env.example` | `ROBOT__NAMESPACE` / `API_BASE_URL` / 旧入口注释 |
| `CLAUDE.md` “认证与路由”与“关键模块” | 旧契约 |

### 2.6 OAuth（#18）现状

- `src/theseus_kit/oauth.py:104-132` 用 `audience=resource_server_url`（theseus-kit 自己的 PRM resource URL）校验 token；但 Manager develop 的 Authorization Code `resource` 只接受 `robot:{orgSlug}:{employeeNo}`（`parseResource` 前缀检查），不再接受 theseus-kit URL。
- 即使 token 拿到，TFRS 也会因 `aud=robot:{public_id}` + `typ=at+jwt` 的 profile 不匹配而拒绝（见 §4.1）。
- 结论：**#18 的“验证后直传”路径在当前三仓 develop 上不可用**；`0.2.0` 不应把 OAuth 作为可用能力。PAT 路径不受影响。

---

## 3. 改造方案（建议）

### 3.1 配置 schema（breaking，建议 0.2.0）

推荐新变量：

```bash
# 机器人权威入口：直接复制 Manager connection-info 的 accessURL
THESEUS_ROBOT__ACCESS_URL=https://huawei.test.turingfocus.cn/c/tfrobot/robot-1/
# 业务 Robot ID（rid），用于路径校验与 summary 展示
THESEUS_ROBOT__ROBOT_ID=robot-1
# 机器人类型；配置工具当前只支持 tfrobot
THESEUS_ROBOT__ROBOT_TYPE=tfrobot

# Manager 机器 API origin（换票），不是 issuer，也不是机器人入口
THESEUS_ROBOT__MANAGER_API_BASE_URL=https://rest.test.turingfocus.cn

THESEUS_CREDENTIAL__KIND=user_pat
THESEUS_CREDENTIAL__PAT=tfp_...
THESEUS_CREDENTIAL__ROBOT_PUBLIC_ID=turingfocus:EMP010-R
```

校验规则：

- `access_url` 必须是 HTTPS（本地测试可放宽 `http://127.0.0.1`），无 userinfo/query/fragment；path 必须匹配 `^/c/(tfrobot|openclaw)/[a-z0-9-]+/?$`。
- 若同时给 `robot_id`/`robot_type`，必须与 URL path 一致；若不给，可从 URL 派生（但建议显式，便于 summary）。
- 拒绝旧 `THESEUS_ROBOT__API_BASE_URL` + `THESEUS_ROBOT__NAMESPACE` 组合：启动时给出“旧入口已退役，请改用 `ACCESS_URL`”的明确错误，而不是静默发请求。
- `manager_api_base_url` 必须是 HTTPS origin，无 path/query/fragment。

**是否保留 legacy 模式**：

- 若 staging/prod 在 Operator 升级前仍需使用旧 `api.<clusterDomain>` + X-TF，可加 `THESEUS_ROBOT__ROUTING_MODE=canonical|legacy`（默认 canonical，legacy 打 deprecation 警告），并在环境升级完成后删除。
- 若确认 theseus-kit 只服务已升级的环境，则直接删除 legacy，减少维护面。
- 这是产品/运维决策，建议在动手前确认 §1.4 的环境升级时间表。

### 3.2 传输层

- `RobotAuth` 只注入 `Authorization: Bearer <token>`；删除 `RequestContext`/X-TF。
- `RobotClient` 直接使用 `access_url` 作为 `httpx.AsyncClient(base_url=...)`。httpx 会把 `base_url` 的 path 与请求 path 合并（已验证：`https://alias/c/tfrobot/rid` + `/v1/factory/x` → `https://alias/c/tfrobot/rid/v1/factory/x`），因此服务层现有的 `/v1/...` 路径无需逐个改。
- `RobotClient.from_settings()` / `for_static_token()` 改为从新配置构造。
- 保持“写不重试、GET 仅 429/5xx 重试”的现有策略；409/404/400 不重试。

### 3.3 错误映射

在 `_ensure_ok` 中：

```python
if response.status_code == 409:
    body = response.json() if ... else {}
    if body.get("errorCode") == "ROBOT_ADDRESS_CHANGED":
        raise RobotAddressChangedError(...)
if response.status_code == 404:
    raise RobotRouteNotFoundError(...)  # 含 alias/rid/type 与重新获取地址指引
if 300 <= response.status_code < 400:
    raise RobotAddressChangedError(...)  # 不跟随/不解析 HTML
```

错误文案建议包含：

- 旧地址：`机器人地址已变更（ROBOT_ADDRESS_CHANGED）。请从 Manager 门户重新复制 accessURL（https://{alias}/c/tfrobot/{rid}/），更新 THESEUS_ROBOT__ACCESS_URL 后重启 theseus-kit。旧地址仅保留 30 天。`
- 404：`目标机器人不在该 Host（或 Host 未知）。请确认 ACCESS_URL 来自当前环境的 Manager，且 alias/rid/type 匹配。`

### 3.4 测试改造

- `tests/_fakeserver.py`：
  - 移除 X-TF 强制校验；改为模拟 gateway：只接受 `/c/tfrobot/{rid}/...`，剥前缀后按 `/llms.txt`、`/v1/factory/**` 路由；未知 Host/路径返回 404。
  - 增加可脚本化的 409 `ROBOT_ADDRESS_CHANGED` 响应，用于错误映射测试。
  - 保留真实 TCP 往返的测试风格。
- 各测试 `_CTX` 改为 `access_url` 或统一的 fixture；建议在 `tests/conftest.py` 提供 `robot_settings` 工厂，避免 12 个文件重复。
- 新增/更新用例：
  - 配置接受 canonical URL；拒绝旧 `api_base_url`/`namespace` 组合。
  - 请求路径为 `/c/tfrobot/{rid}/v1/factory/...`，且无 X-TF 头。
  - 409/404/3xx 映射为类型化错误。
  - `robot_public_id` 接受 `turingfocus:EMP010-R`、`my-org:emp:001`；拒绝非法 org slug。
  - E2E：真实 test 环境 token exchange + `/llms.txt` + 一个 `/v1/factory` 读接口。

### 3.5 public_id 修复

- `config.py`：不要用单一 regex 硬编码 employeeNo 字符集。建议：
  - 自定义 validator：`split(":", 1)`；`org_slug` 匹配 `^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$`；`employee_no` 非空且不含空白/控制字符。
  - 保留 `robot_public_id` 原值用于日志/错误。
- `credentials.py`：`rsplit(":", 1)` 改为 `split(":", 1)`。
- 新增测试覆盖 Manager 文档中的 `turingfocus:EMP010-R` 与带冒号的 `my-org:emp:001`。

### 3.6 文档清单

- `README.md`：快速开始、配置方式、架构图、模块表、E2E 命令、安全模型中的 X-TF 描述。
- `.env.example`：按 §3.1 重写；删除 `NAMESPACE` 与旧入口注释。
- `CLAUDE.md`： “认证与路由”“关键模块”“测试约定”中的 X-TF/旧入口。
- `docs/architecture.md`：Auth and routing 整节重写（Host/path、Bearer-only、409 行为）。
- `docs/auth-oauth-design.md`：更新 §6 / §10.1-new；明确 OAuth 当前阻塞与 PAT-only 策略。
- `docs/oauth-operations.md`：配置示例、错误表、X-TF 引用。
- `CHANGELOG.md`：0.2.0 breaking change 条目。
- `docs/upstream/tfrobotserver-oauth-token-inquiry.md`：标注历史/更新结论。

### 3.7 版本与发布

- 建议 `0.2.0`（配置字段与路由契约 breaking）。
- 发布前：`uv run poe ci && uv run poe build && uv run poe package-check`。
- 在 CHANGELOG/README 给出旧 `.env` → 新 `.env` 的迁移示例。

---

## 4. 跨项目待协调（上游建议）

### 4.1 P0：OAuth Authorization Code 的 aud/typ 不匹配

事实：

- Manager `oauth_authorization_service.go:671-703`：Authorization Code 的 `resource` 统一为 `robot:{orgSlug}:{employeeNo}`；`signAccessToken` 用 `SignOAuthAccessToken`，`typ=at+jwt`（`internal/shared/signing/signer.go:41`）。
- TFRS `tfrobotserver/middleware/jwt_verifier.py:221-236`：
  - `robot:{robot_id}` → `JWT`
  - `robot:{machine_client_id}` → `JWT`
  - `{issuer}/robots/{machine_client_id}` → `at+jwt`
- 因此 `aud=robot:{public_id}` + `typ=at+jwt` 会命中前两条但 typ 不匹配，被 `jwt_profile_mismatch` 拒绝。
- 不能简单把 `robot:{public_id}` 的 profile 改成 `at+jwt`：legacy token-exchange/client_credentials 也使用同一 aud，但 `typ=JWT`，会打断 PAT 路径。

**Owner 已拍板（2026-11-16，方案 A）**：

- **TFRM 建 P0**：恢复 Authorization Code 的 resource/aud 为 `{issuer}/robots/{public_id}`（RFC 8707），`typ=at+jwt` 不变；Manager `parseResource` 同步接受该 URI。**Jira：`TFRM-760`（High，blocks TFRM-302）**。
- **legacy PAT/token-exchange 继续 `robot:{public_id}` + `typ=JWT`，不动。**
- **TFRS 零改码**：现有 `{issuer}/robots/{machine_client_id}` → `at+jwt` profile 会直接命中；TFRS 只做验收。
- **Authorization Code 在 Manager 修复发布/验收前继续按阻塞处理**；PAT/token-exchange 不受影响，theseus-kit 0.2.0 先只保证 user_pat 的结论不变。
- 进度（2026-11-16/17）：`v0.2.0-dev19` 已发布（commit `4e0d5848ec17b7425b3d239e03bc78ca5e3eac81`，tag `v0.2.0-dev19`；构建 `cnb-o1l-1k7i26ahf` 4/4 success；develop push CI `cnb-eoa-1k7i25ju9-001` 全绿）。`TFRM-760` 已包含，并已按 Owner 决定关闭（开发完成即结束，resolution=完成）；TFRS/部署验收由 Owner 另行安排，若发现问题新开 Bug。PAT/token-exchange 不受影响；theseus-kit 的 OAuth 在真实验收通过前不启用。已知并接受：四镜像 created 标签时间不同（API 触发未传共享 BUILD_TIME），digest 不可变，不影响本寻址结论。
- TFRS 独立 synthetic verifier 证据（非 Manager-signed、非部署验收）：Authorization Code 形状（`aud={issuer}/robots/{public_id}`、`typ=at+jwt`、`client_id`）→ accepted；legacy（`aud=robot:{public_id}`、`typ=JWT`）→ accepted；cross-profile（`JWT` + OAuth aud、`at+jwt` + legacy aud）→ rejected；`at+jwt` 缺 `client_id` → rejected。结论：契约层面兼容，但 Manager-signed + 真实部署验收仍需等 test 窗口。

**PRM 注意（TFRS 本轮明确）**：TFRS 的 `/.well-known/oauth-protected-resource/...` 是 **DPE protected resource**（`resource=DPE public_base_url`），不是 robot factory API 的 PRM；不要拿它当工厂 API 的 resource metadata，也不要把它的 resource 改成 robot URI。TFRM 会在 Manager 侧提供/维护正确的 robot OAuth resource；若后续需要独立的 robot API PRM，另立单。

另外，theseus-kit 的 OAuth verifier 目前以 `resource_server_url` 为 audience（`oauth.py:104-132`），与 Manager 的 robot OAuth resource 不是同一 audience；即使 P0 修复，也需要重新设计“MCP client 请求的 resource 是谁”。这是 #18 的独立设计项，不应在 0.2.0 仓促落地。

### 4.2 P1：上游文档漂移

| 仓库 | 文档/位置 | 漂移 |
|---|---|---|
| TFRO | `website/docs/architecture/network-topology.md` §约定层 | **已修正**：TFRO `a007c73`（2026-11-16）中英文同步，旧 Cookie/QueryParam、X-TF、`api.<clusterDomain>` 约定层、旧 access URL 均标 DEPRECATED 并指向 TFRO-179/176 契约。消费方不要再引用旧段落 |
| TFRO | `docs/ops/local-tls-setup.md`（TFRS 仓） | 仍有 X-TF 示例；旧联调残留 |
| TFRM | `docs/api/robot-address-v2.md:13` | 写“TFRServer 初始化 ServiceURL `.../api`”；`init_executor_v2.go:328-357` 实际返回不带 `/api` 的 `RobotURL`（TFRM-280 修复） |
| TFRM | `docs/api/oauth2-third-party-authorization.md:14` | 写 `{issuer}/robots/{robotAccountId}`；当前代码是 `robot:{orgSlug}:{employeeNo}`（TFRM-386）。**方案 A 决定恢复 `{issuer}/robots/{public_id}`，TFRM P0 落地后需同步确认文档与代码一致** |
| TFRM | `docs/design/oauth2-third-party-robot-authorization.md:100,171,312` | 同上；方案 A 落地后这些旧 URI 描述会重新成立，需按 P0 最终实现校对 |
| TFRM | `docs/deploy/02-network.md` | 只列 `api-*` Manager API；需补 test 的 `rest.test.turingfocus.cn` 与环境矩阵 |
| TFRS | `docs/ops/local-tls-setup.md` | X-TF 旧示例 |

### 4.3 P1：`robot_id` 字符集漂移（TFRO 确认为可触发缺陷）

- Manager `internal/shared/validator/robot_id_validator.go:25`：`^[A-Za-z0-9\-_.~]{6,30}$`。
- Manager `domainalias.RobotPath` **复用同一 validator**；用户显式传 `Robot-123_test.v1~final` 这类 ID 时，会生成 `/c/tfrobot/Robot-123_test.v1~final/`。
- TFRO gateway `/c/` 路径校验是 `^[a-z0-9-]+$`，实际返回 **400 `invalid robot identity`**。这不是“可能”，TFRO 已确认可触发。
- 自动生成的 `de-<uuidhex>` 是 lowercase/hyphen，安全；但用户显式指定 ID 不受保证。
- **不建议 TFRO 放宽**：`rid` 还会进入 `tf-robot-{rid}` / `tfopenclaw-{rid}` Service 名，K8s 要求 RFC1123 lowercase/hyphen；大写/下划线/点/波浪号会先让 Service 非法。
- 正确方向是 **TFRM 收紧**：建议 `^[a-z0-9]([-a-z0-9]*[a-z0-9])?$` + 保留 6–30 长度，`RobotPath` 共用；补 uppercase/underscore/dot/tilde/leading-trailing-hyphen 负例；上线前审计存量 `DigitalEmployeeInstance.RobotID`。
- 建议由 Owner/TFRM 开 Bug，标题可用「robot_id 校验允许大写/特殊字符，gateway 与 K8s Service 均拒绝」。**该问题未纳入 TFRM-760，需另立收敛任务**；theseus-kit 侧不要在配置校验里自行猜测或放宽。

### 4.4 P2：user PAT 的机器人地址发现

- `GET /api/v1/digital-employees/{id}/connection-info` 需要 User JWT（`AuthMiddleware`），不接受 user PAT。
- `GET /api/v1/a2a/peers` 需要 **robot machine PAT**（`robot_machine_auth.go`），theseus-kit 的 user PAT 不适用；且该接口返回的是 peer 描述符，不是自身地址。
- 因此 theseus-kit 目前只能“用户显式填入 canonical `accessURL`”。建议 Manager 未来提供 user PAT 可用的“我的机器人地址”发现接口，或至少在 Portal 明确展示可复制的 `accessURL` 及改域后的更新提示。

### 4.5 P2：`connection-info.apiBaseURL` 语义

- `accessURL`（无 `/api`）是机器直连入口；`apiBaseURL`（带 `/api`）是遗留描述符字段（前端 BFF 路径），不是 TFRS 直连前缀。TFRM 本轮确认机器入口不能带 `/api`。
- 建议 Manager 在 DTO/文档中明确区分，或新增 `machineBaseURL` 字段避免误用；theseus-kit 侧只消费 `accessURL`。

---

## 5. 实施顺序与验收

### Phase 0（0.5 天，先确认）

- [ ] 确认 §1.4 环境矩阵与 staging/prod 的 Operator/Manager/Server 升级时间表。
- [ ] 决定是否需要 legacy 路由模式；若不需要，直接删除旧字段。
- [ ] 在 test 环境拿一个真实 alias + rid + user PAT，跑通 §5.1 的 curl。

### Phase 1（1 天，代码）

- [ ] `config.py`：新字段、URL/public_id 校验、旧字段迁移错误。
- [ ] `routing.py`：删除 X-TF；保留 robot id/type 校验或退化为 `RobotAddress`。
- [ ] `transport.py`：Bearer-only；`base_url=access_url`；409/404/3xx 映射。
- [ ] `errors.py`：新增 `RobotAddressChangedError` / `RobotRouteNotFoundError` / `RobotPathRejectedError`。
- [ ] `credentials.py`：`split(":", 1)`。
- [ ] `__init__.py` 公共导出调整。

### Phase 2（1 天，测试）

- [ ] `tests/_fakeserver.py` 改为 canonical path + 409/404 可脚本化。
- [ ] 更新 12 个测试文件的 `_CTX`/构造参数；建议集中 fixture。
- [ ] 新增配置、路径、错误映射、public_id 用例。
- [ ] `uv run poe ci` 全绿。

### Phase 3（0.5 天，文档）

- [ ] `README.md`、`.env.example`、`CLAUDE.md`、`docs/architecture.md`、`docs/auth-oauth-design.md`、`docs/oauth-operations.md`、`CHANGELOG.md`。
- [ ] 全局 grep：`X-TF-`、`api.<clusterDomain>`、`THESEUS_ROBOT__NAMESPACE` 只应出现在迁移说明/历史文档。

### Phase 4（0.5 天，E2E）

- [ ] test 环境真实 token exchange + `/llms.txt` + `/v1/factory` 读接口。
- [ ] 旧地址/旧 Host 返回 409/404 时，theseus-kit 错误消息可操作。
- [ ] 发布 0.2.0。

### 5.1 手工验收 curl（示意，勿写入真实 token）

```bash
# 1) 换票：Manager 机器 API origin
TOKEN=$(curl -sS -X POST https://rest.test.turingfocus.cn/api/v1/oauth/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'grant_type=urn:ietf:params:oauth:grant-type:token-exchange' \
  --data-urlencode "subject_token=$PAT" \
  --data-urlencode 'subject_token_type=urn:ietf:params:oauth:token-type:access_token' \
  --data-urlencode 'audience=robot:turingfocus:EMP010-R' \
  --data-urlencode 'scope=config:read config:write config:publish' | jq -r .access_token)

# 2) 机器人 canonical 入口（无 X-TF）
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://huawei.test.turingfocus.cn/c/tfrobot/robot-1/llms.txt"

# 3) 工厂读接口（路径前缀由网关剥掉）
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://huawei.test.turingfocus.cn/c/tfrobot/robot-1/v1/factory/drafts?page=1&pageSize=10"
```

### 5.2 验收判据

- [ ] 所有机器人请求只带 `Authorization`，不再发送 X-TF。
- [ ] `RobotClient` 发出的 URL 形如 `https://{alias}/c/tfrobot/{rid}/v1/factory/...`。
- [ ] 配置错误（旧 `api.`/`namespace`、错误 path、缺 `/c/`）在启动时 fail-fast，并给出迁移指引。
- [ ] 409 `ROBOT_ADDRESS_CHANGED` 不重试、不静默；错误消息包含新地址获取方式。
- [ ] 404 未知 Host/robot 不重试；错误消息包含 alias/rid/type。
- [ ] `turingfocus:EMP010-R` 等合法 public_id 可通过配置校验并生成正确 audience。
- [ ] `uv run poe ci` 通过；E2E 在 test 环境通过。

---

## 6. 未决问题（需要 Owner 决策）

1. **是否保留 legacy 路由模式**：staging 当前仍是 Manager rc7 + Operator rc5 + 域名自动化关闭，prod 未配置；theseus-kit 是否需要在 staging/prod 升级前继续支持旧 `api.<clusterDomain>` + X-TF？若只服务 test，可直接删除 legacy。
2. **OAuth 跨仓 P0 = TFRM-760（已修复并关闭）**：Owner 方案 A 已落地并随 `v0.2.0-dev19` 发布；TFRS 本地 synthetic verifier 已通过，但 Manager-signed + 真实部署验收仍待 test 窗口，若发现问题新开 Bug。theseus-kit 的 OAuth 在真实验收通过前不启用，`0.2.0` 只保证 user_pat。
3. **user PAT 的地址发现**：TFRM 确认当前没有 user PAT 可用的地址发现接口；是否推动 Manager 新增“我的机器人地址”接口，避免用户手工复制。
4. **robot_id 字符集统一**：Manager validator（`A-Z a-z 0-9 - _ . ~`，6–30）vs gateway path regex（`^[a-z0-9-]+$`）由哪边收敛。
5. **`connection-info.apiBaseURL` 的定位**：是否新增 `machineBaseURL`（不带 `/api`）并在 DTO 中明确。

---

## 7. 证据索引

### TFRM（develop `0495825c`）

- `docs/api/robot-address-v2.md`：`https://{alias}/c/{robotType}/{robotId}/`、`accessURL`/`apiBaseURL`/`socketBaseURL`/`sioPath`。
- `docs/api/robot-address-recovery-v1.md`：`addressContractVersion`、`apiBaseURL`、旧域 302/409、`ROBOT_ADDRESS_CHANGED`、保留窗口。
- `docs/api/domain-alias.md`：`DOMAIN_BASE_DOMAIN=test.turingfocus.cn`、企业自选标签、候选/active/redirect 生命周期。
- `docs/api/personal-shard-mapping.md`：个人共享分片 alias。
- `docs/deploy/tfrm-639-640-dev11.md`：`MANAGER_API_BASE_URL` 与 issuer 分离；test=`https://rest.test.turingfocus.cn`。
- `docs/tfrm-280-machine-api-plan.md`：`/api` 是 BFF 路径，机器初始化应使用 `RobotURL` 不带 `/api`。
- `internal/shared/domainalias/robot_address.go`：`RobotPath` / `RobotURL` / `A2ABaseURL`。
- `internal/user/service/digital_employee_service/connection_info_service.go:192-203`：`accessURL` 与 `apiBaseURL`。
- `internal/user/service/digital_employee_service/init_executor_v2.go:328-357`：`getServiceURL` 不带 `/api`。
- `internal/user/service/oauth_authorization_service/oauth_authorization_service.go:609-625,671-703`：Authorization Code resource `robot:{public_id}`、`at+jwt`。
- `internal/shared/signing/signer.go:41`：`typ=at+jwt`。
- `internal/shared/publicid/publicid.go:38-54`：首个冒号分割、org slug 校验。
- `internal/shared/validator/robot_id_validator.go:24`：`^[A-Za-z0-9\-_.~]{6,30}$`。
- 关键提交：`9be65679`（TFRM-386）、`639087d1`（TFRM-609）、`af33a219`（TFRM-606）。
- Jira：**`TFRM-760`**（High，blocks `TFRM-302`）——Authorization Code resource/aud 恢复 `{issuer}/robots/{public_id}`、`typ=at+jwt`；`parseResource` 同步；legacy PAT/token-exchange 保持 `robot:{public_id}` + `typ=JWT`；TFRS verifier 零改码、只验收；DPE PRM 不改。已合入 develop（merge `ace2ff7d`），随 **`v0.2.0-dev19`** 发布（commit `4e0d5848ec17b7425b3d239e03bc78ca5e3eac81`；构建 `cnb-o1l-1k7i26ahf` 4/4 success；develop push CI `cnb-eoa-1k7i25ju9-001` 全绿），并按 Owner 决定关闭（开发完成即结束，resolution=完成）。镜像 digest：user-service `sha256:361e08dc5ea4cdeb7c7bb20a6e935728d127d95310f5fa82a473f22b2baebbb5`；admin-service `sha256:8f31f31b911e79a0ac306b42772d852d1484632edd234a0296894c5ded2c71c5`；otlp-receiver `sha256:708f290494555220ae2ed0e019d87b482fffd9469a3439c296e630a7735d1f02`；migrate `sha256:a79c2fe8fe998351aac76b0c0b7e01493d2442ee32d78cc7d16e3aaf1b15e3d3`。TFRS/部署验收由 Owner 另行安排；若发现问题新开 Bug。已知并接受：四镜像 created 标签时间不同（API 触发未传共享 BUILD_TIME），digest 不可变，不影响寻址结论。

### TFRO（develop `6dbef4f`）

- `internal/controller/infra/cluster/tenant_host_routing.go`：精确 Host 匹配、`/c/{rtype}/{rid}` 剥离、X-TF `headers:remove`、409 `ROBOT_ADDRESS_CHANGED`、302 页面导航。
- `internal/controller/infra/cluster/envoyfilter_lua_routing.go`：统一虚拟主机表、管理域精确层。
- `internal/controller/infra/cluster/virtualservice_cluster.go`：60s 路由超时（TFRO-237）。
- `docs/design/tfro-179-manager-integration-contract.md`：`publicBaseURL` / `ingressAliases`、Host 权威、A2A v2、北向契约 1.6。
- `docs/design/tfro-176-request-classification.md`：旧域 302/409 分类。
- `website/docs/architecture/network-topology.md`：**已过时**（仍描述 `api.` 约定层/X-TF），TFRO 已确认。
- 关键提交：`04d06ef`（TFRO-176/177/179）、`dfb1f1d`（TFRO-221）、`75f9d0d`（TFRO-237）、`a007c73`（network-topology 旧路由标注 DEPRECATED，2026-11-16）。

### TFRS（develop `80c8e42f`）

- `contracts/gateway-northbound.json`：`/llms.txt`、`/v1/`、`/utils/cos/`、`/.well-known/oauth-protected-resource`，版本 1.6。
- `contracts/oauth-scope-catalog.json`：`config:read` / `config:write` / `config:publish` active。
- `tfrobotserver/middleware/jwt_verifier.py:221-236`：三种 token profile；`at+jwt` 需要 `client_id`（:381）。
- `tfrobotserver/security/scope_contract.py:159`：release 需要 `config:publish`。
- `tfrobotserver/api/v1/robot_factory/**`：`/v1/factory` 三态路由未破坏性变更。
- `docs/contracts/tfrs-478-delivery.md`：北向 1.6 / 契约 1.7、A2A 移除、消息 API。
- 关键提交：`c95edf14`（TFRS-764 A2A 移除）、`79579a50`（TFRS-763 messages）、`fb50a5f6`（TFRS-770 错误脱敏）。
- 独立 synthetic verifier 证据（2026-11-17，非 Manager-signed、非部署验收）：Authorization Code 形状（`aud={issuer}/robots/{public_id}` + `typ=at+jwt` + `client_id`）accepted；legacy（`aud=robot:{public_id}` + `typ=JWT`）accepted；cross-profile（`JWT` + OAuth aud、`at+jwt` + legacy aud）rejected；`at+jwt` 缺 `client_id` rejected。契约兼容已验证，Manager-signed + 真实部署验收待 test 窗口。

### theseus-kit（`3c47030`）

- `src/theseus_kit/config.py:27-56,69-72`：`RobotTarget`、`robot_public_id` 正则。
- `src/theseus_kit/routing.py:20-55`：X-TF 构造/校验。
- `src/theseus_kit/transport.py:83-108,139-210,415-444`：`RobotAuth`、`from_settings`、`for_static_token`、`_ensure_ok`。
- `src/theseus_kit/credentials.py:19-30`：`rsplit` 解析。
- `src/theseus_kit/oauth.py:104-132`：`resource_server_url` audience。
- `tests/_fakeserver.py:25,94-110`：X-TF 强制校验。
- `tests/test_auth_routing.py:38,78-90`：X-TF 断言。
- `tests/e2e/test_kit_e2e.py:172-177`：`x-tf-namespace` 断言。
- `README.md:42,142,178,233,245,271,297,301,326`：旧入口/头/环境变量。
- `docs/architecture.md:24-40,60-80`：旧路由契约。
