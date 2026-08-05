"""Skill guide content — statically embedded markdown guides in Chinese.

Each entry in ``_SKILLS`` is a complete markdown guide teaching LLMs how to
use theseus-kit tools for a specific workflow. The guides are embedded as
Python string constants to guarantee reliable loading regardless of
packaging environment (editable install, wheel, zip).
"""

from __future__ import annotations

_INSPECT_ROBOT_CONFIG = """---
name: inspect-robot-config
description: 浏览和查看 TFRobot 配置 —— 发现已有配置、导航配置树、安全读取有界详情。
---

# 查看机器人配置

你正在通过 `theseus-kit` 查看 TFRobot 配置。本技能覆盖**只读**探索路径：
发现已有配置、导航三态配置森林、读取有界且脱敏的详情。

## 允许使用的工具

- `get_config_summary` —— 入口：机器人身份 + 三态概览
- `list_config_nodes` —— 在配置森林的任意层级列出子节点（支持分页）
- `get_config_detail` —— 读取单个节点的有界、脱敏子树视图
- `get_template` —— 按 ID 读取模板（仅元数据或完整详情）
- `get_llms_doc` —— 读取机器人运行时 Schema 文档

**禁止**在本技能中使用变更工具（`update_draft`、`save_template`、`publish_config`）
—— 这些需要 `config:write` 或 `config:publish` 权限。

## 所需 Scope

`config:read` —— 本技能中的所有工具均为只读。

## 标准探索流程

### 第 1 步：获取全局概览

首先调用 `get_config_summary`，**不传任何参数**。返回：

- `robot_identity` —— 你正在连接的机器人
- `states.draft` —— 草稿是否存在、根定位符、状态（`clean` / `dirty` / `publishing` / `unknown`）、revision
- `states.template` —— 模板是否存在、数量
- `states.online` —— 已发布配置是否存在、根定位符、revision
- `_meta.revision` / `_meta.fetched_at` —— 此摘要的构建时间

如果只关心某个状态，传 `state="draft"`、`state="template"` 或 `state="online"` 缩小返回范围。

### 第 2 步：读取机器人 Schema 文档

**在查看任何配置内容之前**，先读取机器人运行时的 `llms.txt`，了解**当前机器人版本**
有哪些端点、场景、工厂、字段和 Scope：

1. 调用 `get_llms_doc` **不传路径** —— 返回索引（`/llms.txt`），列出所有可用文档页面。
2. 调用 `get_llms_doc` 传入具体路径（如 `"schema/brain"`）读取与任务相关的页面。

**绝对不要依赖记忆中的固定 Schema。** 每个 TFRobotServer 版本可能有不同的端点、
字段和校验规则。`llms.txt` 是唯一的事实来源。

### 第 3 步：列出某层级的节点

使用 `list_config_nodes` 探索某个状态、场景或工厂内的内容：

- 不传 `parent`（或传 `null`）查看配置森林的根。
- 传定位符作为 `parent` 列出其子节点。
- 使用 `state`、`scene`、`factory`、`name` 过滤条件缩小范围。
- `page_size` 默认 50（限制在 [1, 200]）。如果返回 `next_cursor`，将其作为 `cursor` 传入继续翻页。

每个返回的节点包含一个 `locator`，可传给 `get_config_detail`。

### 第 4 步：读取具体配置

使用 `get_config_detail` 传入第 3 步获取的 `locator`：

- `depth`（默认 3）控制包含多少层子节点。
- `max_bytes`（默认 8192，上限 32768）限制返回大小。
- 使用 `select`（RFC 6901 JSON Pointer）在大节点的某个子字段内进一步钻取。

**务必检查返回的元数据：**

- `truncated: true` → 结果在 `truncated_at` 处被截断。使用 `next_actions[]` 进一步钻取：
  缩小 `select`、降低 `depth`、请求更小的 `max_bytes`，或对 `truncated_at` 处的子节点调用 `list_config_nodes`。
- `redacted[]` → 列出敏感字段被替换为 `"<<redacted>>"` 的路径。这些字段存在但其值永远不会暴露。
- `bytes_returned` / `bytes_estimated_total` → 你收到了多少 vs 总共有多少。

### 第 5 步：读取模板

当你知道模板 ID 时（从 `list_config_nodes` 或摘要中获取），使用 `get_template`：

- `metadata_only=true` → 紧凑摘要（名称、大小、生命周期）。
- `metadata_only=false`（默认）→ 完整详情，与 `get_config_detail` 形状相同。

## 关键约束

- **每次读取都是实时的。** 没有服务端缓存。如果变更工具修改了状态，重新读取以获取最新 revision。
- **8 KiB 默认 / 32 KiB 硬上限。** 大节点需要多次缩小范围的读取。
- **敏感字段自动脱敏。** 密码、令牌、密钥永远不会出现在输出中。
- **定位符是无状态的、可重放的。** 同一个定位符始终解析到相同的逻辑节点（基于实时上游状态）。
"""

_EDIT_ROBOT_DRAFT = """---
name: edit-robot-draft
description: 安全修改草稿配置 —— 读取 Schema、检查当前节点、构造修改、处理校验错误、验证结果。
---

# 编辑机器人草稿

你正在通过 `theseus-kit` 修改 TFRobot 草稿配置。本技能覆盖**读取-检查-写入**循环：
理解机器人 Schema、读取当前草稿、构造安全修改、处理校验错误、验证结果。
模板保存也在此技能中涵盖。

## 允许使用的工具

- `get_llms_doc` —— 读取机器人运行时 Schema 文档
- `get_config_summary` —— 检查草稿是否存在、revision、状态
- `get_config_detail` —— 读取当前草稿内容（含 `content_hash`）
- `list_config_nodes` —— 探索草稿树结构
- `update_draft` —— 修改现有草稿配置项
- `save_template` —— 将草稿子树保存为可复用模板
- `get_template` —— 验证已保存的模板

**禁止**在本技能中使用 `publish_config` —— 发布需要 `config:publish` Scope，
由 `publish-robot-config` 技能覆盖。

## 所需 Scope

`config:write` —— 本技能中的所有变更工具均需要写权限。
读取工具（`get_llms_doc`、`get_config_summary`、`get_config_detail`、
`list_config_nodes`、`get_template`）同时需要 `config:read`（`config:write` 隐含此权限）。

## 标准编辑流程

### 第 1 步：读取 Schema 文档

**在触碰任何配置之前**，先读取机器人运行时的 `llms.txt`：

1. 调用 `get_llms_doc` 不传路径 → 返回文档索引。
2. 调用 `get_llms_doc` 传入与编辑相关的具体页面（如 `"schema/brain"` 读取脑图谱端点，
   `"schema/vision"` 读取视觉端点）。

`llms.txt` 告诉你：
- 有哪些字段及其类型
- 校验规则（必填字段、取值范围、枚举值）
- 可用的端点、场景和工厂
- 需要的 Scope

**绝对不要依赖记忆中的 Schema。** Schema 在不同 TFRobotServer 版本之间可能变化。
`llms.txt` 是唯一的事实来源。

### 第 2 步：检查当前草稿

编辑前，理解你要修改的内容：

1. 调用 `get_config_summary(state="draft")` 确认草稿存在，检查其 `revision` 和 `status`
   （`clean` / `dirty` / `publishing`）。
2. 调用 `get_config_detail` 传入目标定位符读取当前内容。**保存返回的 `content_hash`**
   —— 在第 4 步中将其作为 `expected_hash` 传入，以保护自己免受并发修改的影响。

如果草稿不存在，无法编辑 —— 告知用户先创建草稿。

### 第 3 步：构造修改

基于 `llms.txt` Schema（第 1 步）和当前草稿内容（第 2 步）：

- 构造更新后的 `config` 字典。只包含需要变更的字段；服务端执行的是合并（merge），
  不是全量替换。
- 对照 Schema 校验你的变更：字段类型、必填字段、取值约束。
- 如果对某个字段不确定，重新读取相关的 `llms.txt` 页面。

### 第 4 步：带冲突保护的更新

调用 `update_draft`：

| 参数 | 是否必填 | 说明 |
|-----------|----------|-------------|
| `setting_id` | **是** | 草稿的数字 ID（来自定位符或详情返回） |
| `setting_name` | **是** | 配置项名称（来自详情返回） |
| `config` | **是** | 更新后的配置字典 |
| `expected_hash` | **强烈推荐** | 第 2 步中 `get_config_detail` 返回的 `content_hash` |

**始终传入 `expected_hash`。** 这启用了乐观并发控制：

- 如果其他人在你读取之后修改了草稿，调用会失败并返回冲突错误（HTTP 409），
  而不是静默覆盖他人的修改。
- 冲突时：重新读取（`get_config_detail`），重新应用你的修改，重试。
- 仅在最后一写胜出（last-write-wins）场景下才省略 `expected_hash`（不推荐）。

### 第 5 步：处理校验错误

如果 `update_draft` 返回错误：

- **HTTP 422**：你的 `config` 未通过服务端校验。阅读错误消息 —— 它会描述哪些字段失败了以及原因。
  对照 `llms.txt` Schema 重新检查字段，修复后重试。
- **HTTP 401 / 403**：Scope 或凭证问题。确认你拥有 `config:write` Scope。不要在不更换凭证的情况下重试。
- **HTTP 409**：冲突 —— 其他人修改了草稿。重新读取后重试。
- **其他 4xx / 5xx**：将错误呈现给用户；不要静默重试。

### 第 6 步：验证结果

成功更新后：

1. 使用同一个定位符调用 `get_config_detail`，确认你的修改已生效。
2. 检查返回的 `content_hash` —— 与第 2 步的 hash 不同，说明更新已生效。
3. 如果草稿 `status` 很重要（例如为了发布），调用 `get_config_summary(state="draft")`
   查看新的 status 和 revision。

## 将草稿保存为模板

使用 `save_template` 将草稿子树保存为可复用模板：

| 参数 | 是否必填 | 说明 |
|-----------|----------|-------------|
| `setting_id` | **是** | 草稿的数字 ID |
| `template_name` | **是** | 新模板的非空名称 |
| `expected_hash` | 可选 | 之前 `get_config_detail` 调用返回的 `content_hash` |

成功时，返回包含新 `template_id` 和一个定位符（可传给 `get_template` 进行验证）。
草稿**不会**被修改 —— 这只是创建一个独立的模板副本。

## 关键约束

- **Schema 是运行时的，不是记忆中的。** 编辑前始终读取 `get_llms_doc`。
  某个机器人版本中存在的字段，在另一个版本中可能不存在。
- **使用 `expected_hash` 实现安全并发。** 防止多人在同一草稿上编辑时丢失更新。
- **本工具绝不发布。** 编辑仅停留在草稿状态。发布需要 `config:publish` Scope 和
  `publish-robot-config` 技能。
- **变更不会自动重试。** 如果更新失败，先诊断错误再重试。
"""

_PUBLISH_ROBOT_CONFIG = """---
name: publish-robot-config
description: 将草稿配置发布到生产环境 —— 执行预检、确认显式审批边界、发布、验证结果。
---

# 发布机器人配置

你正在通过 `theseus-kit` 将 TFRobot 草稿配置发布到线上（生产）状态。本技能覆盖
**预检 → 审批 → 发布 → 验证**循环。

**⚠️ 发布是全局且不可逆的。** 它发布从 ROBOT-scene 草稿开始的**整个**配置树。
没有单配置项发布 —— 所有草稿变更一起上线。

## 允许使用的工具

- `get_config_summary` —— 发布前后检查草稿/线上状态
- `get_config_detail` —— 发布前检查草稿根节点
- `list_config_nodes` —— 探索草稿树结构
- `publish_config` —— 将所有草稿配置发布到线上
- `get_llms_doc` —— 读取机器人运行时 Schema（用于理解配置结构）

## 所需 Scope

`config:publish` —— 这是一个**独立的、更高级别的** Scope，与 `config:write` 不同。
仅拥有 `config:write` **不足以**发布。如果发布调用返回 401/403，请确认凭证包含
`config:publish`。

读取工具需要 `config:read`（`config:publish` 隐含此权限）。

## 标准发布流程

### 第 1 步：发布前预检

发布前，评估当前状态：

1. 调用 `get_config_summary()` 查看全部三个状态：
   - `states.draft` —— 必须是 `present: true`。记录 `root_locator`、`revision` 和 `status`
     （`clean` / `dirty` / `publishing`）。
   - `states.online` —— 记录当前 `revision`（如有），用于发布后对比。
   - `states.template` —— 模板**不受**发布影响。

2. 如果草稿 `status` 为 `"publishing"`，说明发布正在进行中 —— 等待并重新检查。

3. （可选）使用草稿根定位符调用 `get_config_detail`，检查即将发布的内容。注意：
   - `truncated: true` → 草稿较大，你只看到了部分内容。
   - `redacted[]` → 存在但你不可见的敏感字段。

### 第 2 步：显式审批边界

**发布需要用户显式确认。** `publish_config` 工具要求 `acknowledge_publish=true`
—— 这是一个精心设计的决策，旨在防止意外发布。

调用发布前，向用户展示：
- 草稿根定位符和 revision
- 即将发布的内容摘要（场景数量、关键变更等）
- 明确警告：「这将把所有草稿发布到生产环境。此操作不可逆。」

在用户**明确确认要发布之前**，**不要**使用 `acknowledge_publish=true` 调用 `publish_config`。

### 第 3 步：发布

调用 `publish_config`：

| 参数 | 是否必填 | 说明 |
|-----------|----------|-------------|
| `acknowledge_publish` | **是** | 必须为 `true`。否则调用会被拒绝。 |
| `expected_root_hash` | 推荐 | 来自 `get_config_summary(state="draft")` 的草稿根 hash。证明你已经阅读了当前草稿结构。 |

**始终传入 `expected_root_hash`。** 这能防止：
- 其他人在你审查期间修改了草稿
- 预检和发布调用之间草稿结构发生变化

如果 hash 不匹配，调用会被拒绝 —— 重新读取并重新与用户确认后再重试。

### 第 4 步：发布后验证

发布成功后：

1. 调用 `get_config_summary()` 并验证：
   - `states.draft.status` 现在为 `"clean"`（所有修改已发布）。
   - `states.online.revision` 相比发布前已变化。
   - `states.online.root_locator` 与草稿的 root_locator 一致。
2. 返回结果包含 `onlineRobotId` —— 确认它与预期的机器人匹配。

如果线上 revision 未变化，发布可能未生效 —— 在继续之前排查原因。

### 第 5 步：处理发布失败

| 错误 | 原因 | 处理方式 |
|-------|-------|--------|
| HTTP 400 含 "acknowledge_publish" | `acknowledge_publish` 为 `false` 或缺失 | 设为 `true` 并与用户重新确认 |
| HTTP 400 含 "expected_root_hash" | 草稿根 hash 不匹配 | 重新读取摘要，与用户重新确认，使用新 hash 重试 |
| HTTP 401 / 403 | 缺少 `config:publish` Scope | 确认凭证 Scope；不要在不更换 Scope 的情况下重试 |
| HTTP 409 | 草稿状态冲突（如已在发布中） | 等待，重新检查草稿状态，重试 |
| HTTP 422 | 发布前校验失败 | 阅读错误详情，修复草稿问题，与用户重新确认 |

## 关键约束

- **发布是全局的。** 所有草稿变更一起上线 —— 没有部分发布。
- **每次发布一次审批。** 每次都需要 `acknowledge_publish=true`。没有「始终允许」模式。
- **发布后验证是必须的。** 在确认线上状态已更新之前，不要认为发布已完成。
- **本工具不修改草稿。** 发布后草稿仍然保留（状态变为 `clean`）。如需进一步修改，重新编辑草稿。
- **模板不受影响。** 发布只是将草稿 → 线上；模板有独立的生命周期状态。
"""

_SKILLS: dict[str, str] = {
    "inspect-robot-config": _INSPECT_ROBOT_CONFIG,
    "edit-robot-draft": _EDIT_ROBOT_DRAFT,
    "publish-robot-config": _PUBLISH_ROBOT_CONFIG,
}
