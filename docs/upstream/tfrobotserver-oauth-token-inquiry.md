# TFRobotServer OAuth Token 直收确认

**发起方**：theseus-kit（#18 MCP OAuth 授权登录）  
**回复请给**：JQQ / theseus-kit 团队  
**背景文档**：`docs/auth-oauth-design.md` §10.1-new

## 背景（30 秒版）

theseus-kit 正在实现 MCP OAuth 2.0 授权登录（无 PAT 时）。用户通过 MCP Client 在
TFRSManager AS 完成 OAuth 授权后，theseus-kit（作为 RS）收到一个 OAuth access token：

- 签名算法：RS256
- issuer：TFRSManager AS
- audience：`{iss}/robots/<Account.ID>`（例如 `https://accounts.example.test/robots/42`）
- subject：`user:<userID>`
- 含 `client_id` claim
- scope：`config:read` 等

之前的设计假设是"theseus-kit 拿到 OAuth token → 去 Manager 换发成 robot JWT → 发给
TFRobotServer"。但经核实，Manager 的换发端点只接受 HS256 登录会话 JWT / PAT /
client_credentials，**不接受** OAuth AS 签发的 RS256 token 作为 `subject_token`。

因此唯一可行的路径是：**theseus-kit 把 OAuth AS token 直接发给 TFRobotServer**，
跳过换发环节。

## 需要确认的问题

### Q1（核心）：TFRobotServer 当前是否接受 OAuth AS token？

OAuth AS token 的 audience 格式是 `{iss}/robots/<Account.ID>`，而现有 robot 短 JWT 的
audience 是 `robot:{orgSlug}:{employeeNo}`。两者 audience 格式不同。

**请确认**：TFRobotServer 侧的 JWT 验签逻辑，当前是否接受 audience =
`{iss}/robots/<id>` 这种格式的 token？

- [ ] **是**，TFRobotServer 当前就接受（或可以通过配置接受）
- [ ] **否**，TFRobotServer 仅接受 `aud=robot:{public_id}` 格式

### Q2：如不接受，改造方案偏好？

如果 Q1 答案是"否"，theseus-kit 的 OAuth 路径就跑不通。可能的改造方案：

**方案 A — TFRobotServer 扩展 audience 校验**  
TFRobotServer 验签器增加对 `aud={iss}/robots/<id>` 格式的支持（同时保留现有
`robot:{public_id}`），从 audience 中解析出目标机器人身份。

**方案 B — TFRSManager AS 签发时调整 audience**  
让 AS 签发 OAuth token 时，audience 直接用 `robot:{public_id}` 格式
（需确认 OAuth AS 是否支持自定义 audience / resource indicator）。

**方案 C — TFRSManager 扩展换发端点**  
Manager 的 `principalFromUserJWT` 增加 RS256 分支，接受 OAuth AS token 作为
`subject_token` 换发成标准 robot JWT。

**请选择偏好**（可多选，按优先级排列）：

- [ ] 方案 A — TFRobotServer 扩展 audience 校验（改造量最小？）
- [ ] 方案 B — AS 调整 audience 格式
- [ ] 方案 C — Manager 扩展换发端点
- [ ] 其他（请简述）：___

### Q3：scope 校验

TFRobotServer 当前是否校验 token 的 scope claim？（例如要求 `config:read` 才能读配置）

- [ ] 是，当前就校验，要求 scope 含 ___ 
- [ ] 否，当前不校验 scope
- [ ] 计划中但尚未实现

### Q4：时间线

以上改造（如有需要）预计什么时候可以开始 / 完成？

---

**回复方式**：在下方直接填写，或口头告知 JQQ 后由 theseus-kit 团队更新本文档。

---

## 回复区（由 TFRobotServer 团队填写）

**回复人**：___  
**日期**：___

### Q1 回复

### Q2 回复

### Q3 回复

### Q4 回复

### 补充说明
