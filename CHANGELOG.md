# Changelog

本项目的显著变更都会记录在此文件中。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [PEP 440](https://peps.python.org/pep-0440/) 与语义化版本意图。

## [0.2.0] — 2026-11-17

### Breaking

- 机器人路由切换到 Manager 权威 `accessURL`（`https://{alias}/c/tfrobot/{rid}/`）：
  `THESEUS_ROBOT__ACCESS_URL` + `THESEUS_ROBOT__ROBOT_ID` + `THESEUS_ROBOT__MANAGER_API_BASE_URL`。
  移除 `THESEUS_ROBOT__NAMESPACE`、`THESEUS_ROBOT__API_BASE_URL`、`THESEUS_ROBOT__MANAGER_BASE_URL`。
- 删除 `X-TF-*` 路由头注入；`RobotAuth` 只发送 `Authorization: Bearer`，`RequestContext` 由
  `RobotAddress` 取代。
- `RobotClient` 构造函数改为 `base_url=access_url`；`api_base_url` 参数移除。
- OAuth `OAuthConfig` 新增必填 `robot_public_id`；期望 audience 改为
  `{issuer}/robots/{public_id}`（TFRM-760），不再使用 `resource_server_url` 作为 audience。

### Added

- 网关错误类型化：409 `ROBOT_ADDRESS_CHANGED` → `RobotAddressChangedError`；404 未知 Host /
  robot-not-on-Host → `RobotRouteNotFoundError`；400 路径非法 → `RobotPathRejectedError`；
  3xx 显式按地址变更处理。
- `public_id` 按 Manager 契约按首个冒号解析，允许 `EMP010-R` 等合法 employeeNo。
- 升级调研报告 `docs/upgrade-plan-robot-address-v2.md`。

### Fixed

- 合法 public_id（含连字符/冒号）不再被配置正则拒绝。
- 旧路由字段/环境变量会给出明确的迁移错误，而不是静默请求旧入口。

### Known limitations

- OAuth Topology A 的 MCP PRM `resource` 与 Manager robot resource 的兼容性仍需真实环境验收；
  验收前 `0.2.0` 只保证 user_pat。
- staging/prod 若仍部署旧 Operator/Manager，需等环境升级或临时使用旧版本 theseus-kit。

## [0.1.1] — 2026-10-04

### Fixed

- 修复发行包的 stdio 入口未加载配置、返回空工具和资源列表的问题；缺失或错误配置现在明确报错并脱敏。
- 对齐 TFRobotServer rc5 的草稿创建、更新请求与 camelCase 响应契约，保留部分配置合并和 content-hash 冲突保护。
- PAT 换发支持显式配置 scope，默认申请 `config:read config:write config:publish`，并区分 401 凭证错误与 403 权限错误。
- 修复技能注册表对安装后 `__pycache__` 和隐藏目录的处理，移除技能中不存在的 `get_config_value` 工具引用。

### Added

- `delete_draft` MCP 工具，封装草稿删除及服务端引用清理。
- A2C-SMCP Desktop 配置拓扑窗口、资源订阅和变更通知。
- 画像、计划、落地三阶段工作流及反馈技能，共 12 类中文技能；新增可打包的 TFOnto 校验器和参考工件。
- 发行包安装入口、桌面资源、删除草稿及 SDK 三端技能分发的回归测试。

### Known limitations

- 默认命令行入口使用 stdio，推荐以用户 PAT 配置；stdio 的无 PAT OAuth 外部回调登录尚未实现。
- 自动化测试中的真实机器人与 SDK E2E 由环境变量门控；普通 CI 通过不代表真实环境读写和发布已验收。

## [0.1.0] — 2026-08-12

### Added

- MCP Server 骨架，基于 FastMCP，支持 stdio 传输
- TFRobot 认证与路由层：X-TF-* 头部强制校验、user_pat token-exchange 换发、AsyncCachingTokenSource
- 渐进披露契约（方案 A）：无状态索引 + 结构化选择器，4 个 MCP 只读工具（get_config_summary → list_config_nodes → get_config_detail → get_config_value）
- 配置变更工具：update_draft（content-hash 冲突保护）、validate_draft、create_draft、save_template、publish_config
- A2C-SMCP 兼容层：window:// 和 skill:// 资源投影
- OAuth 认证路径：TokenVerifier 适配器、RS PRM + Bearer 校验、StaticTokenSource
- GitHub Actions CI/CD：质量门禁（format/lint/typecheck/tests）+ OIDC Trusted Publishing
- Python 3.11-3.13 支持
- Redaction 安全最后防线：PAT/JWT/OAuth token 泄漏防护
