# 发布流程

`theseus-kit` 使用 uv 构建发行包，使用 GitHub Release 驱动发布，并通过 PyPI/TestPyPI Trusted Publishing（OIDC）完成无长期 Token 发布。

## 一次性平台配置

首次发布前，需要分别在 PyPI 和 TestPyPI 建立 Trusted Publisher：

- PyPI Project：`theseus-kit`
- GitHub Owner：`A2C-SMCP`
- Repository：`theseus-kit`
- Workflow：`publish.yml`
- Environment：PyPI 使用 `pypi`，TestPyPI 使用 `testpypi`

如果项目尚未在平台存在，使用平台提供的 Pending Publisher 创建首个项目。配置时可由维护者登录，Codex 通过 Playwright 完成页面操作；不得把 PyPI API Token 写入 GitHub Secret 或本地仓库。

GitHub 仓库需要存在同名 `pypi` 和 `testpypi` Environment。正式环境建议开启人工审批。

## 本地准备

```bash
uv sync --locked --all-groups
uv run poe ci
uv run poe build
uv run poe package-check
```

先用 bump-my-version 同步更新版本配置、`pyproject.toml` 与 `src/theseus_kit/__init__.py`，再刷新锁文件中的项目版本：

```bash
uv run bump-my-version bump --new-version 0.1.1 --no-commit --no-tag
uv lock
```

更新 `CHANGELOG.md` 和使用说明，重跑上述门禁并检查发行包版本，然后 Review 完整 diff。
版本准备阶段不创建 Tag；正式版先通过 PR 合入 `main`，确认该提交的 CI 通过后，
再在对应提交创建 `v0.1.1` Tag 和正式 GitHub Release。预发行版使用如 `0.1.2rc1` 的版本号和 GitHub Pre-release。

## 发布规则

- GitHub **Pre-release**：发布到 TestPyPI。
- GitHub **正式 Release**：仅当 Release Commit 位于 `main` 时发布到 PyPI。
- Tag 去掉 `v` 后必须与 `pyproject.toml` 版本完全一致。
- 构建前会重新运行锁文件、格式、Lint、类型、测试与发行包元数据检查。
- 发布使用 GitHub OIDC；不保存 PyPI/TestPyPI Token。

发布是不可逆的外部操作。创建 GitHub Release 前必须明确获得维护者批准。
