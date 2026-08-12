# Changelog

本项目的显著变更都会记录在此文件中。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [PEP 440](https://peps.python.org/pep-0440/) 与语义化版本意图。

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

