# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

`theseus-kit` 是一个开源 MCP server，用于检查、编辑、模板化与发布 TFRobot 配置。它首先是一个**标准 MCP server**：工具使用 MCP 输入/输出 schema，资源使用标准 `resources/list`、`resources/read`；A2C-SMCP 的 `window://`、`skill://` URI 方案与元数据是**可选的附加扩展**，通用 MCP 客户端可以在忽略它们的情况下使用核心配置工具。

当前状态：**仅脚手架**。`server.py` 启动的是空 server（stdio 传输），配置读取/编辑/发布等工具尚未实现，0.1.0 行为通过 GitHub milestone 与 issue 追踪。新增协议面变更时必须保持标准 MCP 互操作性——A2C-SMCP 扩展不得成为通用客户端使用 server 的前置依赖。

## 开发环境

要求 Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)。所有命令通过 uv + poethepoet(`poe`) 驱动。

```bash
uv sync --locked --all-groups      # 安装锁定依赖（开发必用，含 dev/test/build 全部分组）
uv run theseus-kit                 # 运行 server（当前为空 server，stdio 传输）
```

质量门禁（合并前全跑；详见下方说明）：

```bash
uv run poe ci          # = lock-check + check + test-cov（含 80% 覆盖率硬门禁）
uv run poe build       # uv build，产物在 dist/
uv run poe package-check   # twine check dist/*.whl dist/*.tar.gz
```

### 常用任务（来自 pyproject.toml `[tool.poe.tasks]`）

- `poe check` = `format-check` + `lint` + `typecheck`（只检查，不跑测试）
- `poe format` / `format-check` — ruff format（`src tests scripts`）
- `poe lint` — ruff check（rule 集：`E,F,I,B,UP`，line-length 120）
- `poe typecheck` — mypy，**strict 模式**，覆盖 `src` + `scripts`（`mcp.*` 忽略 missing imports）
- `poe test` / `test-cov` — pytest；`test-cov` 带 `--cov=theseus_kit --cov-fail-under=80`，且 `__main__.py` 被排除覆盖率统计
- `poe lock-check` — `uv lock --check`，确保 `uv.lock` 与 `pyproject.toml` 一致

运行单个测试：

```bash
uv run pytest tests/test_release.py::test_normalize_tag
```

> pytest 配置（`pyproject.toml`）含 `pythonpath = ["."]`，因此 `scripts/` 可作为包被测试导入（`tests/test_release.py` 即 `from scripts.check_release import ...`）。

CI（`.github/workflows/tests.yml`）在 Python 3.11/3.12/3.13 矩阵上跑测试，3.11 上跑质量门禁与构建。本地 `poe ci` 是它的等价前置门禁。

## 架构

完整基线见 `docs/architecture.md`。大图景要点：

**计划中的分层**（目前仅第 1 层的空壳已落地）：

1. **MCP surface** — 工具/资源声明与稳定的公共 schema（`server.py` 用 `mcp.server.fastmcp.FastMCP` 构造）。
2. **Application services** — read / edit / save-template / publish 用例。
3. **TFRobot client** — 针对机器人的 `/v1/factory/**` 与 `/llms.txt` 端点的认证 HTTP 适配器（依赖 `httpx`）。
4. **Resource projection** — 紧凑的 `window://` 状态与可分发的 `skill://` 包。

关键设计取舍：**server 不复制机器人的 Factory schema**，而是在运行时读取目标机器人版本的 `llms.txt` / `/v1/factory/llm-docs/**` 文档，让每个部署的 TFRobotServer 版本成为自己的配置文档事实来源。

**安全不变量**（实现 mutation 工具时必须遵守）：

- 凭证只留在 server 进程内，绝不进入工具输出、资源、日志或 SKILL 内容。
- read / write / publish 三类能力各自独立，对应机器人的 `config:read` / `config:write` / `config:publish` scope。
- mutation 工具在 upstream API 提供时返回受影响对象与修订证据；错误信息须可操作且对请求认证做脱敏。
- **publish 是显式操作**，绝不作为 draft 编辑或 template 保存的副作用被触发。
- 大配置的渐进式披露（progressive-disclosure）契约**尚未冻结**，需等维护者在 0.1.0 design issue 中选定方案。

## 版本与发布

发布流程详见 `docs/releasing.md`（中文）。核心规则：

- 版本号遵循 PEP 440。当前为 `0.1.0.dev0`。
- 用 `bump-my-version` 同步 `pyproject.toml` 与 `src/theseus_kit/__init__.py`，并自动创建 commit + `v<version>` tag：
  ```bash
  uv run bump-my-version bump --new-version 0.1.0rc1   # 推送前需人工 review
  ```
- **Tag 去掉 `v` 后必须与 `pyproject.toml` 版本完全一致**；该一致性由 `scripts/check_release.py` 在 CI 发布流程中校验（同时校验 wheel METADATA 版本与 Release 类型：预发行版→TestPyPI，正式版仅当来自 `main`→PyPI）。
- 发布通过 GitHub OIDC Trusted Publishing，**不保存 PyPI/TestPyPI token**。创建 GitHub Release 属于不可逆的外部操作，必须先获得维护者明确批准。

`scripts/check_release.py` 是被 CI 直接调用的发布校验脚本（非 `poe` 任务），改动它时注意 `tests/test_release.py` 覆盖了它的全部公开函数。

## 工作流约定

- 开发分支为 **`develop`**（PR 默认指向它）。每个改动应与 active milestone 中的一个 GitHub issue 对应。
- 文档（`docs/releasing.md`、`CHANGELOG.md`）与部分脚本注释使用中文；新增面向用户的文档/注释沿用此约定。
- `CHANGELOG.md` 遵循 Keep a Changelog 格式，新变更写入 `[Unreleased]`。
